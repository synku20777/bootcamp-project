from __future__ import annotations

import json
import math
import shutil
import statistics
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any
from uuid import uuid4

from pyspark import StorageLevel
from pyspark.ml.clustering import KMeans, KMeansModel
from pyspark.ml.evaluation import ClusteringEvaluator
from pyspark.ml.feature import StandardScaler, StandardScalerModel, VectorAssembler
from pyspark.ml.functions import vector_to_array
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T
from pyspark.sql.window import Window

from app.spark_pipeline.benchmark import dataframe_fingerprint, directory_metrics
from app.spark_pipeline.runtime_policy import SparkRuntimePolicy

CLUSTERING_DIAGNOSTICS_VERSION = 1
CLUSTERING_FEATURE_VERSION = "covid-country-features-v1"
CLUSTERING_SEEDS = (13, 29, 47, 71, 97)
SILHOUETTE_TIE_TOLERANCE = 0.01
MIN_STABILITY_ARI = 0.75

COVID_FEATURE_COLUMNS = (
    "latest_cases_per_100k",
    "latest_deaths_per_100k",
    "peak_14d_cases_per_100k",
    "peak_14d_deaths_per_100k",
    "volatility_14d_cases_per_100k",
)
LOG_FEATURE_COLUMNS = tuple(f"log1p_{name}" for name in COVID_FEATURE_COLUMNS)
STANDARDIZED_FEATURE_COLUMNS = tuple(
    f"z_score_{name}" for name in COVID_FEATURE_COLUMNS
)
WDI_PROFILE_COLUMNS = (
    "population_density_2019",
    "population_age_65_plus_pct_2019",
    "real_gdp_per_capita_2019",
    "health_expenditure_per_capita_ppp_2019",
)


class ClusteringValidationError(RuntimeError):
    """The feature population did not support a stable clustering model."""


@dataclass(slots=True)
class PreparedClusteringData:
    eligible: DataFrame
    exclusions: DataFrame
    scaler_model: StandardScalerModel
    eligible_country_count: int
    total_country_count: int

    def cleanup(self) -> None:
        self.eligible.unpersist(blocking=True)


@dataclass(slots=True)
class ClusteringResult:
    assignments: DataFrame
    profiles: DataFrame
    exclusions: DataFrame
    diagnostics: dict[str, Any]
    kmeans_model: KMeansModel
    scaler_model: StandardScalerModel
    cleanup: Any


@dataclass(slots=True)
class _ModelRun:
    seed: int
    silhouette: float
    cluster_sizes: dict[int, int]
    labels: dict[str, int]
    model: KMeansModel


def adjusted_rand_index(
    first: dict[str, int],
    second: dict[str, int],
) -> float:
    """Return label-invariant assignment agreement without sklearn."""
    keys = sorted(set(first) & set(second))
    if len(keys) < 2:
        return 1.0
    contingency = Counter((first[key], second[key]) for key in keys)
    first_sizes = Counter(first[key] for key in keys)
    second_sizes = Counter(second[key] for key in keys)

    def pairs(size: int) -> int:
        return size * (size - 1) // 2

    total_pairs = pairs(len(keys))
    same_both = sum(pairs(size) for size in contingency.values())
    same_first = sum(pairs(size) for size in first_sizes.values())
    same_second = sum(pairs(size) for size in second_sizes.values())
    expected = same_first * same_second / total_pairs
    maximum = (same_first + same_second) / 2
    denominator = maximum - expected
    if denominator == 0:
        return 1.0
    return (same_both - expected) / denominator


def country_key_skew(
    dataframe: DataFrame,
    *,
    ratio_warn: float,
    share_warn: float,
) -> dict[str, Any]:
    counts = [
        int(row["count"])
        for row in dataframe.groupBy(
            F.coalesce("COUNTRY_ISO3", "LOCATION_KEY").alias("country_key")
        )
        .count()
        .collect()
    ]
    total = sum(counts)
    if not counts:
        return {
            "status": "PASS",
            "key_count": 0,
            "total_rows": 0,
            "median_rows_per_key": 0,
            "p95_rows_per_key": 0,
            "maximum_rows_per_key": 0,
            "maximum_to_median_ratio": 0.0,
            "maximum_row_share": 0.0,
            "thresholds": {"ratio_warn": ratio_warn, "share_warn": share_warn},
        }
    ordered = sorted(counts)
    median = float(statistics.median(ordered))
    p95_index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
    maximum = ordered[-1]
    ratio = maximum / median if median else float("inf")
    share = maximum / total if total else 0.0
    return {
        "status": "WARN" if ratio > ratio_warn or share > share_warn else "PASS",
        "key_count": len(ordered),
        "total_rows": total,
        "median_rows_per_key": round(median, 3),
        "p95_rows_per_key": ordered[p95_index],
        "maximum_rows_per_key": maximum,
        "maximum_to_median_ratio": round(ratio, 6),
        "maximum_row_share": round(share, 6),
        "thresholds": {"ratio_warn": ratio_warn, "share_warn": share_warn},
    }


def prepare_country_features(
    extended: DataFrame,
    *,
    min_observations: int,
) -> PreparedClusteringData:
    valid_identity = extended.where(
        F.col("COUNTRY_ISO3").isNotNull() & (F.length(F.trim("COUNTRY_ISO3")) == 3)
    )
    calendar_day = F.datediff("REPORT_DATE", F.lit("1970-01-01"))
    rolling_window = (
        Window.partitionBy("COUNTRY_ISO3").orderBy(calendar_day).rangeBetween(-13, 0)
    )
    working = (
        valid_identity.withColumn(
            "_null_clustering_measure",
            (
                F.col("NEW_CASES_PER_100K").isNull()
                | F.col("NEW_DEATHS_PER_100K").isNull()
                | F.col("CASES_PER_100K").isNull()
                | F.col("DEATHS_PER_100K").isNull()
            ).cast("long"),
        )
        .withColumn(
            "_invalid_population",
            (
                F.col("COVID_RATE_POPULATION_2020").isNull()
                | (F.col("COVID_RATE_POPULATION_2020") <= 0)
            ).cast("long"),
        )
        .withColumn(
            "_cases_rate_non_negative",
            F.when(
                F.col("NEW_CASES_PER_100K").isNull(), F.lit(None).cast("double")
            ).otherwise(F.greatest("NEW_CASES_PER_100K", F.lit(0.0))),
        )
        .withColumn(
            "_deaths_rate_non_negative",
            F.when(
                F.col("NEW_DEATHS_PER_100K").isNull(), F.lit(None).cast("double")
            ).otherwise(F.greatest("NEW_DEATHS_PER_100K", F.lit(0.0))),
        )
        .withColumn(
            "_rolling_observations_14d",
            F.count("REPORT_DATE").over(rolling_window),
        )
        .withColumn(
            "_rolling_cases_14d",
            F.when(
                F.col("_rolling_observations_14d") == 14,
                F.avg("_cases_rate_non_negative").over(rolling_window),
            ),
        )
        .withColumn(
            "_rolling_deaths_14d",
            F.when(
                F.col("_rolling_observations_14d") == 14,
                F.avg("_deaths_rate_non_negative").over(rolling_window),
            ),
        )
    )
    latest_window = Window.partitionBy("COUNTRY_ISO3").orderBy(
        F.col("REPORT_DATE").desc(), F.col("LOCATION_KEY").asc()
    )
    latest = (
        working.withColumn("_latest_rank", F.row_number().over(latest_window))
        .where(F.col("_latest_rank") == 1)
        .select(
            "COUNTRY_ISO3",
            F.col("CASES_PER_100K").cast("double").alias("latest_cases_per_100k"),
            F.col("DEATHS_PER_100K").cast("double").alias("latest_deaths_per_100k"),
        )
    )
    aggregated = (
        working.groupBy("COUNTRY_ISO3")
        .agg(
            F.max("COUNTRY").alias("country"),
            F.max("COUNTRY_ISO2").alias("country_iso2"),
            F.max("LOCATION_KEY").alias("location_key"),
            F.countDistinct("REPORT_DATE").alias("observation_count"),
            F.max("COVID_RATE_POPULATION_2020").alias("population_2020"),
            F.sum("_invalid_population").alias("invalid_population_rows"),
            F.sum("_null_clustering_measure").alias("null_clustering_measure_rows"),
            F.max("_rolling_cases_14d").cast("double").alias("peak_14d_cases_per_100k"),
            F.max("_rolling_deaths_14d")
            .cast("double")
            .alias("peak_14d_deaths_per_100k"),
            F.stddev_pop("_rolling_cases_14d")
            .cast("double")
            .alias("volatility_14d_cases_per_100k"),
        )
        .join(latest, "COUNTRY_ISO3", "left")
        .withColumnRenamed("COUNTRY_ISO3", "country_iso3")
    )
    incomplete = F.lit(False)
    for column in COVID_FEATURE_COLUMNS:
        incomplete = incomplete | F.col(column).isNull() | (F.col(column) < 0)
    with_reason = aggregated.withColumn(
        "exclusion_reason",
        F.when(
            F.col("invalid_population_rows") > 0,
            F.lit("missing_or_invalid_population"),
        )
        .when(
            F.col("observation_count") < min_observations,
            F.lit("insufficient_history"),
        )
        .when(
            F.col("null_clustering_measure_rows") > 0,
            F.lit("incomplete_features"),
        )
        .when(incomplete, F.lit("incomplete_features")),
    )
    missing_identity = (
        extended.where(
            F.col("COUNTRY_ISO3").isNull() | (F.length(F.trim("COUNTRY_ISO3")) != 3)
        )
        .groupBy("LOCATION_KEY")
        .agg(
            F.max("COUNTRY").alias("country"),
            F.max("COUNTRY_ISO2").alias("country_iso2"),
            F.countDistinct("REPORT_DATE").alias("observation_count"),
        )
        .select(
            "country",
            "country_iso2",
            F.lit(None).cast("string").alias("country_iso3"),
            F.col("LOCATION_KEY").alias("location_key"),
            "observation_count",
            F.lit("missing_iso3").alias("exclusion_reason"),
        )
    )
    excluded_valid_identity = with_reason.where(
        F.col("exclusion_reason").isNotNull()
    ).select(
        "country",
        "country_iso2",
        "country_iso3",
        "location_key",
        "observation_count",
        "exclusion_reason",
    )
    exclusions = excluded_valid_identity.unionByName(missing_identity)

    eligible_scalars = with_reason.where(F.col("exclusion_reason").isNull())
    eligible_country_count = eligible_scalars.count()
    excluded_country_count = exclusions.count()
    if eligible_country_count == 0:
        raise ClusteringValidationError(
            "No countries satisfy the clustering feature contract."
        )
    for source, target in zip(
        COVID_FEATURE_COLUMNS,
        LOG_FEATURE_COLUMNS,
        strict=True,
    ):
        eligible_scalars = eligible_scalars.withColumn(target, F.log1p(source))
    assembled = VectorAssembler(
        inputCols=list(LOG_FEATURE_COLUMNS),
        outputCol="unscaled_features",
        handleInvalid="error",
    ).transform(eligible_scalars)
    scaler_model = StandardScaler(
        inputCol="unscaled_features",
        outputCol="scaled_features",
        withMean=True,
        withStd=True,
    ).fit(assembled)
    eligible = scaler_model.transform(assembled).persist(StorageLevel.MEMORY_AND_DISK)
    eligible.count()
    total_country_count = eligible_country_count + excluded_country_count
    return PreparedClusteringData(
        eligible=eligible,
        exclusions=exclusions,
        scaler_model=scaler_model,
        eligible_country_count=eligible_country_count,
        total_country_count=total_country_count,
    )


def _candidate_diagnostics(
    runs: list[_ModelRun],
    *,
    k: int,
    seeds: tuple[int, ...],
    rejected_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    pairwise_ari = [
        adjusted_rand_index(first.labels, second.labels)
        for first, second in combinations(runs, 2)
    ]
    median_silhouette = (
        float(statistics.median(run.silhouette for run in runs)) if runs else None
    )
    median_ari = float(statistics.median(pairwise_ari)) if pairwise_ari else None
    valid = (
        len(runs) >= 4
        and median_silhouette is not None
        and median_silhouette > 0
        and median_ari is not None
        and median_ari >= MIN_STABILITY_ARI
    )
    return {
        "k": k,
        "requested_seeds": list(seeds),
        "valid_seed_runs": len(runs),
        "median_silhouette": median_silhouette,
        "median_pairwise_adjusted_rand_index": (
            median_ari if median_ari is not None else None
        ),
        "valid": valid,
        "rejected_runs": rejected_runs,
        "runs": [
            {
                "seed": run.seed,
                "silhouette": round(run.silhouette, 6),
                "minimum_cluster_size": min(run.cluster_sizes.values()),
                "cluster_sizes": {
                    str(key): value for key, value in sorted(run.cluster_sizes.items())
                },
            }
            for run in runs
        ],
    }


def select_k_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    valid = sorted(
        (candidate for candidate in candidates if candidate["valid"]),
        key=lambda item: item["k"],
    )
    if not valid:
        raise ClusteringValidationError(
            "No candidate satisfied silhouette, cluster-size, and stability gates."
        )
    best_silhouette = max(candidate["median_silhouette"] for candidate in valid)
    tied = [
        candidate
        for candidate in valid
        if best_silhouette - candidate["median_silhouette"]
        <= SILHOUETTE_TIE_TOLERANCE + 1e-12
    ]
    return min(tied, key=lambda candidate: candidate["k"])


def _profile_clusters(
    assignments: DataFrame,
    baseline: DataFrame,
    *,
    broadcast_baseline: bool,
) -> DataFrame:
    context = baseline.select(
        F.col("context_iso3").alias("profile_iso3"),
        *WDI_PROFILE_COLUMNS,
        "context_snapshot_id",
    )
    if broadcast_baseline:
        context = F.broadcast(context)
    profiled = assignments.join(
        context,
        F.col("country_iso3") == F.col("profile_iso3"),
        "left",
    )
    expressions = [F.count(F.lit(1)).alias("country_count")]
    expressions.append(
        F.first("context_snapshot_id", ignorenulls=True).alias("context_snapshot_id")
    )
    for column in (*COVID_FEATURE_COLUMNS, *WDI_PROFILE_COLUMNS):
        expressions.append(
            F.percentile_approx(F.col(column).cast("double"), 0.5).alias(
                f"median_{column}"
            )
        )
    return profiled.groupBy("cluster_id", "cluster_label").agg(*expressions)


def run_country_clustering(
    spark: SparkSession,
    extended: DataFrame,
    baseline: DataFrame,
    *,
    policy: SparkRuntimePolicy,
    model_id: str,
    broadcast_baseline: bool,
    seeds: tuple[int, ...] = CLUSTERING_SEEDS,
) -> ClusteringResult:
    prepared = prepare_country_features(
        extended,
        min_observations=policy.cluster_min_observations,
    )
    if prepared.eligible_country_count < policy.cluster_k_min * 3:
        prepared.cleanup()
        raise ClusteringValidationError(
            "Too few eligible countries satisfy the minimum cluster-size contract."
        )
    minimum_cluster_size = max(
        3,
        math.ceil(prepared.eligible_country_count * 0.02),
    )
    evaluator = ClusteringEvaluator(
        featuresCol="scaled_features",
        predictionCol="raw_prediction",
        metricName="silhouette",
        distanceMeasure="squaredEuclidean",
    )
    candidate_payloads: list[dict[str, Any]] = []
    runs_by_k: dict[int, list[_ModelRun]] = {}
    maximum_k = min(
        policy.cluster_k_max,
        prepared.eligible_country_count // minimum_cluster_size,
    )
    for k in range(policy.cluster_k_min, maximum_k + 1):
        valid_runs: list[_ModelRun] = []
        rejected_runs: list[dict[str, Any]] = []
        for seed in seeds:
            model = KMeans(
                k=k,
                seed=seed,
                featuresCol="scaled_features",
                predictionCol="raw_prediction",
                maxIter=100,
                tol=1e-4,
            ).fit(prepared.eligible)
            predictions = model.transform(prepared.eligible)
            cluster_sizes = {
                int(row["raw_prediction"]): int(row["count"])
                for row in predictions.groupBy("raw_prediction").count().collect()
            }
            if (
                len(cluster_sizes) != k
                or min(cluster_sizes.values()) < minimum_cluster_size
            ):
                rejected_runs.append(
                    {
                        "seed": seed,
                        "reason": "minimum_cluster_size",
                        "cluster_sizes": {
                            str(key): value
                            for key, value in sorted(cluster_sizes.items())
                        },
                    }
                )
                continue
            silhouette = float(evaluator.evaluate(predictions))
            labels = {
                str(row["country_iso3"]): int(row["raw_prediction"])
                for row in predictions.select(
                    "country_iso3", "raw_prediction"
                ).collect()
            }
            valid_runs.append(
                _ModelRun(
                    seed=seed,
                    silhouette=silhouette,
                    cluster_sizes=cluster_sizes,
                    labels=labels,
                    model=model,
                )
            )
        runs_by_k[k] = valid_runs
        candidate_payloads.append(
            _candidate_diagnostics(
                valid_runs,
                k=k,
                seeds=seeds,
                rejected_runs=rejected_runs,
            )
        )

    selected_candidate = select_k_candidate(candidate_payloads)
    selected_runs = runs_by_k[int(selected_candidate["k"])]
    median_silhouette = float(
        statistics.median(run.silhouette for run in selected_runs)
    )
    authoritative = min(
        selected_runs,
        key=lambda run: (abs(run.silhouette - median_silhouette), run.seed),
    )
    centers = authoritative.model.clusterCenters()
    ordered_raw_clusters = sorted(
        range(len(centers)),
        key=lambda raw_cluster: (float(sum(centers[raw_cluster])), raw_cluster),
    )
    stable_mapping = {
        raw_cluster: stable_cluster
        for stable_cluster, raw_cluster in enumerate(ordered_raw_clusters, start=1)
    }
    mapping_schema = T.StructType(
        [
            T.StructField("raw_prediction", T.IntegerType(), False),
            T.StructField("cluster_id", T.IntegerType(), False),
            T.StructField("centroid", T.ArrayType(T.DoubleType()), False),
        ]
    )
    mapping = spark.createDataFrame(
        [
            (
                raw_cluster,
                stable_mapping[raw_cluster],
                [float(value) for value in centers[raw_cluster]],
            )
            for raw_cluster in ordered_raw_clusters
        ],
        mapping_schema,
    )
    predictions = (
        authoritative.model.transform(prepared.eligible)
        .join(F.broadcast(mapping), "raw_prediction", "inner")
        .withColumn("_scaled_feature_array", vector_to_array("scaled_features"))
    )
    squared_differences = F.zip_with(
        vector_to_array("scaled_features"),
        F.col("centroid"),
        lambda value, center: F.pow(value - center, F.lit(2.0)),
    )
    distance = F.sqrt(
        F.aggregate(
            squared_differences,
            F.lit(0.0),
            lambda total, value: total + value,
        )
    )
    assignments = predictions.select(
        F.lit(model_id).alias("model_id"),
        "country",
        "country_iso2",
        "country_iso3",
        "location_key",
        "observation_count",
        "population_2020",
        *COVID_FEATURE_COLUMNS,
        *LOG_FEATURE_COLUMNS,
        *(
            F.col("_scaled_feature_array")[index].alias(column)
            for index, column in enumerate(STANDARDIZED_FEATURE_COLUMNS)
        ),
        "cluster_id",
        F.concat(F.lit("cluster_"), F.col("cluster_id")).alias("cluster_label"),
        distance.alias("centroid_distance"),
    )
    assignment_projection = assignments.select(
        "country_iso3",
        "cluster_id",
    )
    assignment_checksum = dataframe_fingerprint(assignment_projection)
    exclusion_counts = {
        str(row["exclusion_reason"]): int(row["count"])
        for row in prepared.exclusions.groupBy("exclusion_reason").count().collect()
    }
    stable_cluster_sizes = {
        str(stable_mapping[raw]): count
        for raw, count in authoritative.cluster_sizes.items()
    }
    diagnostics = {
        "diagnostics_version": CLUSTERING_DIAGNOSTICS_VERSION,
        "feature_version": CLUSTERING_FEATURE_VERSION,
        "model_id": model_id,
        "method": "spark_ml_kmeans_squared_euclidean",
        "feature_policy": "covid_only_with_post_fit_wdi_profiles",
        "features": [
            {
                "name": name,
                "transform": "log1p_then_standardize",
            }
            for name in COVID_FEATURE_COLUMNS
        ],
        "negative_correction_policy": (
            "source values preserved; incident rates floored at zero only for "
            "rolling clustering features"
        ),
        "eligibility": {
            "total_countries": prepared.total_country_count,
            "eligible_countries": prepared.eligible_country_count,
            "excluded_countries": sum(exclusion_counts.values()),
            "excluded_by_reason": exclusion_counts,
            "minimum_observations": policy.cluster_min_observations,
        },
        "selection": {
            "candidate_k_min": policy.cluster_k_min,
            "candidate_k_max": policy.cluster_k_max,
            "seeds": list(seeds),
            "minimum_cluster_size": minimum_cluster_size,
            "silhouette_tie_tolerance": SILHOUETTE_TIE_TOLERANCE,
            "minimum_stability_adjusted_rand_index": MIN_STABILITY_ARI,
            "candidates": candidate_payloads,
            "selected_k": selected_candidate["k"],
            "selected_seed": authoritative.seed,
            "selected_silhouette": round(authoritative.silhouette, 6),
            "selected_median_silhouette": selected_candidate["median_silhouette"],
            "selected_median_adjusted_rand_index": selected_candidate[
                "median_pairwise_adjusted_rand_index"
            ],
            "cluster_sizes": stable_cluster_sizes,
            "cluster_burden_scores": {
                str(stable_mapping[raw_cluster]): round(
                    float(sum(centers[raw_cluster])), 6
                )
                for raw_cluster in ordered_raw_clusters
            },
            "cluster_label_policy": (
                "ascending sum of standardized centroid coordinates"
            ),
        },
        "assignment_fingerprint": assignment_checksum,
        "caveats": [
            "Clusters describe historical reported outcomes, not causal regimes.",
            "World Bank fields profile clusters after fitting and do not affect membership.",
            "Cluster identifiers are ordered by standardized centroid burden for stability.",
        ],
    }
    profiles = _profile_clusters(
        assignments,
        baseline,
        broadcast_baseline=broadcast_baseline,
    )
    return ClusteringResult(
        assignments=assignments,
        profiles=profiles,
        exclusions=prepared.exclusions,
        diagnostics=diagnostics,
        kmeans_model=authoritative.model,
        scaler_model=prepared.scaler_model,
        cleanup=prepared.cleanup,
    )


def publish_clustering_artifacts(
    result: ClusteringResult,
    *,
    target: Path,
) -> dict[str, int]:
    if target.exists():
        raise FileExistsError(f"Clustering output already exists: {target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.staging-{uuid4().hex}"
    try:
        result.assignments.write.mode("error").parquet(str(staging / "assignments"))
        result.profiles.write.mode("error").parquet(str(staging / "profiles"))
        result.exclusions.write.mode("error").parquet(str(staging / "exclusions"))
        result.scaler_model.write().save(str(staging / "model" / "scaler"))
        result.kmeans_model.write().save(str(staging / "model" / "kmeans"))
        (staging / "diagnostics.json").write_text(
            json.dumps(result.diagnostics, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        metrics = directory_metrics(staging)
        staging.replace(target)
        return metrics
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
