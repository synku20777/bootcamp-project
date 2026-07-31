from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from app.spark_pipeline.clustering import (
    COVID_FEATURE_COLUMNS,
    STANDARDIZED_FEATURE_COLUMNS,
    adjusted_rand_index,
    country_key_skew,
    prepare_country_features,
    publish_clustering_artifacts,
    run_country_clustering,
    select_k_candidate,
)
from app.spark_pipeline.quality import _extended_quality_checks
from app.spark_pipeline.runtime_policy import SparkRuntimePolicy
from spark_tests.spark_test_support import (
    install_pyspark_socket_warning_filter,
    stop_test_spark_session,
)


class ClusteringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_pyspark_socket_warning_filter()
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("spark-clustering-tests")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", 4)
            .getOrCreate()
        )
        cls.addClassCleanup(stop_test_spark_session, cls.spark)
        cls.spark.sparkContext.setLogLevel("ERROR")

    def _extended(self):
        rows = []
        start = date(2020, 1, 1)
        for country_index in range(6):
            high_burden = country_index >= 3
            iso3 = f"X{country_index:02d}"
            cumulative_cases = 0
            cumulative_deaths = 0
            for offset in range(20):
                cases = (30 + country_index + offset % 3) if high_burden else 1
                deaths = (3 + offset % 2) if high_burden else 0
                cases_rate = float(cases)
                deaths_rate = float(deaths)
                if country_index == 0 and offset == 5:
                    cases = -2
                    cases_rate = -2.0
                cumulative_cases += cases
                cumulative_deaths += deaths
                rows.append(
                    (
                        f"Country {country_index}",
                        f"Q{country_index}",
                        iso3,
                        iso3,
                        start + timedelta(days=offset),
                        cases,
                        deaths,
                        cumulative_cases,
                        cumulative_deaths,
                        100_000,
                        cases_rate,
                        deaths_rate,
                        float(cumulative_cases),
                        float(cumulative_deaths),
                        cases < 0,
                        False,
                        "JHU",
                        "JHU_ONLY",
                    )
                )
        return self.spark.createDataFrame(
            rows,
            "COUNTRY string, COUNTRY_ISO2 string, COUNTRY_ISO3 string, "
            "LOCATION_KEY string, REPORT_DATE date, NEW_CASES_RAW long, "
            "NEW_DEATHS_RAW long, CASES_CUMULATIVE long, DEATHS_CUMULATIVE long, "
            "COVID_RATE_POPULATION_2020 long, NEW_CASES_PER_100K double, "
            "NEW_DEATHS_PER_100K double, CASES_PER_100K double, "
            "DEATHS_PER_100K double, HAS_NEGATIVE_CASE_CORRECTION boolean, "
            "HAS_NEGATIVE_DEATH_CORRECTION boolean, SOURCE_NAME string, "
            "SERIES_SEGMENT string",
        )

    def _baseline(self):
        return self.spark.createDataFrame(
            [
                (
                    f"X{index:02d}",
                    float(10 + index),
                    float(15 + index),
                    float(20_000 + index),
                    float(2_000 + index),
                    "snapshot",
                )
                for index in range(6)
            ],
            "context_iso3 string, population_density_2019 double, "
            "population_age_65_plus_pct_2019 double, "
            "real_gdp_per_capita_2019 double, "
            "health_expenditure_per_capita_ppp_2019 double, "
            "context_snapshot_id string",
        )

    def _policy(self) -> SparkRuntimePolicy:
        return SparkRuntimePolicy.from_manifest(
            {
                "files": {
                    "covid_extended": {"byte_count": 10_000},
                    "mapping": {"byte_count": 100},
                    "population": {"byte_count": 100},
                    "indicators": {"byte_count": 100},
                }
            },
            cluster_min_observations=14,
            cluster_k_min=2,
            cluster_k_max=2,
        )

    def test_feature_preparation_floors_only_modelling_rates(self) -> None:
        prepared = prepare_country_features(self._extended(), min_observations=14)
        try:
            first = prepared.eligible.where("country_iso3 = 'X00'").first()
            self.assertEqual(prepared.eligible_country_count, 6)
            self.assertEqual(first["latest_cases_per_100k"], 17.0)
            self.assertEqual(first["peak_14d_cases_per_100k"], 1.0)
            self.assertAlmostEqual(
                first["log1p_latest_cases_per_100k"], math.log1p(17.0)
            )
            self.assertIn("log1p_latest_cases_per_100k", prepared.eligible.columns)
            self.assertNotIn("population_density_2019", prepared.eligible.columns)
        finally:
            prepared.cleanup()

    def test_eligibility_preserves_exclusion_reason_precedence(self) -> None:
        cutoff = date(2020, 1, 10)
        extended = (
            self._extended()
            .where(
                ~((F.col("COUNTRY_ISO3") == "X02") & (F.col("REPORT_DATE") > cutoff))
            )
            .withColumn(
                "COVID_RATE_POPULATION_2020",
                F.when(F.col("COUNTRY_ISO3") == "X00", F.lit(0)).otherwise(
                    F.col("COVID_RATE_POPULATION_2020")
                ),
            )
            .withColumn(
                "NEW_CASES_PER_100K",
                F.when(
                    (F.col("COUNTRY_ISO3") == "X01")
                    & (F.col("REPORT_DATE") == date(2020, 1, 5)),
                    F.lit(None).cast("double"),
                ).otherwise(F.col("NEW_CASES_PER_100K")),
            )
        )
        prepared = prepare_country_features(extended, min_observations=14)
        try:
            reasons = {
                row["country_iso3"]: row["exclusion_reason"]
                for row in prepared.exclusions.collect()
            }
            self.assertEqual(
                reasons,
                {
                    "X00": "missing_or_invalid_population",
                    "X01": "incomplete_features",
                    "X02": "insufficient_history",
                },
            )
            self.assertEqual(prepared.eligible_country_count, 3)
        finally:
            prepared.cleanup()

    def test_extended_quality_detects_duplicate_dates_and_invalid_denominator(
        self,
    ) -> None:
        extended = self._extended()
        duplicate = extended.unionByName(extended.limit(1))
        required = (
            "COUNTRY",
            "LOCATION_KEY",
            "REPORT_DATE",
            "NEW_CASES_RAW",
            "NEW_DEATHS_RAW",
            "CASES_CUMULATIVE",
            "DEATHS_CUMULATIVE",
            "SOURCE_NAME",
            "SERIES_SEGMENT",
        )
        checks, _ = _extended_quality_checks(
            duplicate,
            {
                "covid_extended": {
                    "columns": {name: {"null_count": 0} for name in required}
                }
            },
            cluster_min_observations=14,
        )
        by_rule = {item["rule"]: item for item in checks}
        self.assertEqual(by_rule["covid_extended.duplicate_country_date"]["count"], 1)
        invalid_population = duplicate.withColumn(
            "COVID_RATE_POPULATION_2020", F.lit(0)
        )
        invalid_checks, _ = _extended_quality_checks(
            invalid_population,
            {
                "covid_extended": {
                    "columns": {name: {"null_count": 0} for name in required}
                }
            },
            cluster_min_observations=14,
        )
        invalid_by_rule = {item["rule"]: item for item in invalid_checks}
        self.assertFalse(
            invalid_by_rule["covid_extended.non_positive_population"]["passed"]
        )
        self.assertEqual(
            invalid_by_rule["covid_extended.non_positive_population"]["severity"],
            "WARN",
        )

    def test_clustering_is_stable_and_publication_keeps_profiles_local(self) -> None:
        result = run_country_clustering(
            self.spark,
            self._extended(),
            self._baseline(),
            policy=self._policy(),
            model_id="fixture-cluster",
            broadcast_baseline=True,
        )
        try:
            self.assertEqual(result.diagnostics["selection"]["selected_k"], 2)
            self.assertEqual(result.assignments.count(), 6)
            self.assertEqual(
                sorted(result.diagnostics["selection"]["cluster_sizes"].values()),
                [3, 3],
            )
            self.assertTrue(
                set(COVID_FEATURE_COLUMNS) <= set(result.assignments.columns)
            )
            self.assertTrue(
                set(STANDARDIZED_FEATURE_COLUMNS) <= set(result.assignments.columns)
            )
            standardized_means = result.assignments.agg(
                *(
                    F.avg(column).alias(column)
                    for column in STANDARDIZED_FEATURE_COLUMNS
                )
            ).first()
            for column in STANDARDIZED_FEATURE_COLUMNS:
                self.assertAlmostEqual(standardized_means[column], 0.0, places=10)
            self.assertNotIn("population_density_2019", result.assignments.columns)
            self.assertIn("median_population_density_2019", result.profiles.columns)
            with tempfile.TemporaryDirectory() as temporary_directory:
                target = Path(temporary_directory) / "model_id=fixture-cluster"
                publish_clustering_artifacts(result, target=target)
                self.assertTrue((target / "assignments").is_dir())
                self.assertTrue((target / "profiles").is_dir())
                self.assertTrue((target / "model" / "kmeans").is_dir())
                diagnostics = json.loads(
                    (target / "diagnostics.json").read_text(encoding="utf-8")
                )
                document = json.dumps(diagnostics)
                self.assertNotIn("Country 0", document)
                self.assertNotIn("X00", document)
                self.assertNotIn("median_population_density_2019", document)
        finally:
            result.cleanup()

    def test_adjusted_rand_index_and_smaller_k_tie_break(self) -> None:
        first = {"A": 0, "B": 0, "C": 1, "D": 1}
        relabelled = {"A": 5, "B": 5, "C": 2, "D": 2}
        self.assertEqual(adjusted_rand_index(first, relabelled), 1.0)
        selected = select_k_candidate(
            [
                {"k": 2, "valid": True, "median_silhouette": 0.50},
                {"k": 3, "valid": True, "median_silhouette": 0.505},
                {"k": 4, "valid": True, "median_silhouette": 0.515},
            ]
        )
        self.assertEqual(selected["k"], 3)

    def test_runtime_policy_broadcast_file_count_and_skew_thresholds(self) -> None:
        policy = self._policy()
        decisions = policy.broadcast_decisions(
            {
                "files": {
                    "mapping": {"byte_count": 10},
                    "population": {"byte_count": 20_000_000},
                    "indicators": {"byte_count": 10},
                }
            }
        )
        self.assertEqual(
            decisions,
            {"mapping": True, "population": False, "indicators": True},
        )
        self.assertEqual(policy.target_file_count(1), 1)
        self.assertEqual(policy.target_file_count(129 * 1024 * 1024), 2)
        skew = country_key_skew(
            self.spark.createDataFrame(
                [("AAA", "AAA")] * 10 + [("BBB", "BBB")],
                "COUNTRY_ISO3 string, LOCATION_KEY string",
            ),
            ratio_warn=5.0,
            share_warn=0.5,
        )
        self.assertEqual(skew["status"], "WARN")


if __name__ == "__main__":
    unittest.main()
