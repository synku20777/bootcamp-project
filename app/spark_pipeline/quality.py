from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from pyspark import StorageLevel
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from app.spark_pipeline.schemas import (
    CORRUPT_RECORD_COLUMN,
    DatasetSchema,
)
from app.spark_pipeline.transformations import duplicate_population_keys

QUALITY_RULESET_VERSION = "bronze-quality-v1"
EXACT_DISTINCT_MAX_ROWS = 1_000_000
APPROX_DISTINCT_RSD = 0.02


def inspect_header(path: Path, expected: tuple[str, ...]) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        actual = tuple(next(csv.reader(source), []))
    return {
        "expected": list(expected),
        "actual": list(actual),
        "missing": [name for name in expected if name not in actual],
        "unexpected": [name for name in actual if name not in expected],
        "reordered": set(actual) == set(expected) and actual != expected,
        "matches": actual == expected,
    }


def read_bronze_source(
    spark: SparkSession,
    dataset: DatasetSchema,
    path: Path,
    *,
    source_batch_id: str,
    ingestion_id: str,
    ingested_at: datetime,
) -> DataFrame:
    dataframe = (
        spark.read.option("header", True)
        .option("mode", "PERMISSIVE")
        .option("columnNameOfCorruptRecord", CORRUPT_RECORD_COLUMN)
        .option("dateFormat", "yyyy-MM-dd")
        .schema(dataset.schema)
        .csv(str(path))
    )
    return (
        dataframe.withColumn("_source_file", F.input_file_name())
        .withColumn("_source_batch_id", F.lit(source_batch_id))
        .withColumn("_ingestion_id", F.lit(ingestion_id))
        .withColumn(
            "_ingested_at_utc",
            F.lit(ingested_at.isoformat()).cast("timestamp"),
        )
        .persist(StorageLevel.MEMORY_AND_DISK)
    )


def _distinct_expression(column: str, *, exact: bool) -> Any:
    if exact:
        return F.countDistinct(column).alias(f"{column}__distinct")
    return F.approx_count_distinct(column, rsd=APPROX_DISTINCT_RSD).alias(
        f"{column}__distinct"
    )


def profile_dataset(
    dataframe: DataFrame,
    dataset: DatasetSchema,
    *,
    input_bytes: int,
    exact_distinct_max_rows: int = EXACT_DISTINCT_MAX_ROWS,
) -> dict[str, Any]:
    business_columns = list(dataset.headers)
    row_count = dataframe.count()
    exact_distinct = row_count <= exact_distinct_max_rows
    aggregations = []
    for column in business_columns:
        aggregations.extend(
            [
                F.sum(F.col(column).isNull().cast("long")).alias(f"{column}__nulls"),
                _distinct_expression(column, exact=exact_distinct),
            ]
        )
        if column in dataset.numeric_columns or column in dataset.date_columns:
            aggregations.extend(
                [
                    F.min(column).alias(f"{column}__min"),
                    F.max(column).alias(f"{column}__max"),
                ]
            )
    aggregations.append(
        F.sum(F.col(CORRUPT_RECORD_COLUMN).isNotNull().cast("long")).alias(
            "corrupt_record_count"
        )
    )
    aggregate = dataframe.agg(*aggregations).first().asDict(recursive=True)

    columns: dict[str, Any] = {}
    for column in business_columns:
        null_count = int(aggregate[f"{column}__nulls"] or 0)
        details: dict[str, Any] = {
            "null_count": null_count,
            "null_percent": (
                round(null_count / row_count * 100, 6) if row_count else 0.0
            ),
            "distinct_count": int(aggregate[f"{column}__distinct"] or 0),
        }
        if column in dataset.numeric_columns or column in dataset.date_columns:
            details["minimum"] = aggregate[f"{column}__min"]
            details["maximum"] = aggregate[f"{column}__max"]
        if column in dataset.numeric_columns:
            quantiles = dataframe.approxQuantile(
                column,
                [0.01, 0.25, 0.5, 0.75, 0.99],
                0.01,
            )
            details["quantiles"] = dict(
                zip(("p01", "p25", "p50", "p75", "p99"), quantiles, strict=False)
            )
        columns[column] = details

    return {
        "row_count": row_count,
        "input_bytes": input_bytes,
        "input_partitions": dataframe.rdd.getNumPartitions(),
        "schema": dataframe.schema.simpleString(),
        "distinct_method": "exact" if exact_distinct else "approximate",
        "distinct_rsd": None if exact_distinct else APPROX_DISTINCT_RSD,
        "corrupt_record_count": int(aggregate["corrupt_record_count"] or 0),
        "columns": columns,
    }


def _check(
    rule: str,
    severity: str,
    count: int,
    message: str,
) -> dict[str, Any]:
    return {
        "rule": rule,
        "severity": severity,
        "count": int(count),
        "passed": count == 0,
        "message": message,
    }


def evaluate_quality(
    profiles: dict[str, dict[str, Any]],
    headers: dict[str, dict[str, Any]],
    frames: dict[str, DataFrame],
    *,
    normalized_duplicate_count: int,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for name, header in headers.items():
        checks.append(
            _check(
                f"{name}.schema_exact",
                "FAIL",
                0 if header["matches"] else 1,
                "CSV header must exactly match the versioned contract.",
            )
        )

    for name, profile in profiles.items():
        checks.append(
            _check(
                f"{name}.corrupt_records",
                "FAIL",
                profile["corrupt_record_count"],
                "Corrupt CSV records are retained but block curated publication.",
            )
        )

    ecdc_columns = profiles["ecdc"]["columns"]
    for column in ("COUNTRY_REGION", "REPORT_DATE"):
        checks.append(
            _check(
                f"ecdc.required.{column.lower()}",
                "FAIL",
                ecdc_columns[column]["null_count"],
                "Required ECDC identity/date values cannot be null.",
            )
        )
    for column in ("CASES", "DEATHS"):
        checks.append(
            _check(
                f"ecdc.null.{column.lower()}",
                "WARN",
                ecdc_columns[column]["null_count"],
                "Null daily measures are converted to zero in curated output.",
            )
        )
    missing_iso = (
        frames["ecdc"]
        .where(F.col("ISO3166_1").isNull() | (F.trim("ISO3166_1") == ""))
        .count()
    )
    non_integral_measures = (
        frames["ecdc"]
        .where(
            (F.col("CASES").isNotNull() & (F.col("CASES") != F.floor(F.col("CASES"))))
            | (
                F.col("DEATHS").isNotNull()
                & (F.col("DEATHS") != F.floor(F.col("DEATHS")))
            )
        )
        .count()
    )
    checks.extend(
        [
            _check(
                "ecdc.missing_source_iso",
                "WARN",
                missing_iso,
                "Missing source ISO values require mapping or location fallback.",
            ),
            _check(
                "ecdc.duplicate_normalized_country_date",
                "WARN",
                normalized_duplicate_count,
                "Duplicate normalized rows are aggregated in curated output.",
            ),
            _check(
                "ecdc.non_integral_daily_measure",
                "FAIL",
                non_integral_measures,
                "Cases and deaths must be mathematically integral.",
            ),
        ]
    )

    population_columns = profiles["population"]["columns"]
    for column in ("COUNTRY_NAME", "POPULATION", "POPULATION_YEAR"):
        checks.append(
            _check(
                f"population.required.{column.lower()}",
                "FAIL",
                population_columns[column]["null_count"],
                "Required population values cannot be null.",
            )
        )
    missing_both_keys = (
        frames["population"]
        .where(
            (F.col("COUNTRY_CODE_ISO2").isNull() | (F.trim("COUNTRY_CODE_ISO2") == ""))
            & (
                F.col("COUNTRY_CODE_ISO3").isNull()
                | (F.trim("COUNTRY_CODE_ISO3") == "")
            )
        )
        .count()
    )
    non_positive_population = (
        frames["population"].where(F.col("POPULATION") <= 0).count()
    )
    invalid_population_iso = (
        frames["population"]
        .where(
            (
                F.col("COUNTRY_CODE_ISO2").isNotNull()
                & (F.length(F.trim("COUNTRY_CODE_ISO2")) != 2)
            )
            | (
                F.col("COUNTRY_CODE_ISO3").isNotNull()
                & (F.length(F.trim("COUNTRY_CODE_ISO3")) != 3)
            )
        )
        .count()
    )
    duplicate_keys = duplicate_population_keys(frames["population"])
    checks.extend(
        [
            _check(
                "population.missing_typed_key",
                "FAIL",
                missing_both_keys,
                "Every population record needs at least one typed ISO key.",
            ),
            _check(
                "population.non_positive",
                "FAIL",
                non_positive_population,
                "Population must be positive.",
            ),
            _check(
                "population.duplicate_typed_key",
                "FAIL",
                len(duplicate_keys),
                "Typed population keys must be unique.",
            ),
            _check(
                "population.invalid_iso_length",
                "FAIL",
                invalid_population_iso,
                "Non-null population ISO values must have valid lengths.",
            ),
        ]
    )

    mapping = frames["mapping"]
    mapping_columns = profiles["mapping"]["columns"]
    for column in (
        "SOURCE_COUNTRY_NAME",
        "NORMALIZED_COUNTRY_NAME",
        "EXPECTED_POPULATION_MATCH",
    ):
        checks.append(
            _check(
                f"mapping.required.{column.lower()}",
                "FAIL",
                mapping_columns[column]["null_count"],
                "Required mapping values cannot be null.",
            )
        )
    mapping_key_duplicates = (
        mapping.select(
            F.upper(F.trim("SOURCE_COUNTRY_NAME")).alias("name_key"),
            F.coalesce(
                F.when(
                    F.length(F.trim("SOURCE_COUNTRY_CODE")) > 0,
                    F.upper(F.trim("SOURCE_COUNTRY_CODE")),
                ),
                F.lit("<NULL>"),
            ).alias("code_key"),
        )
        .groupBy("name_key", "code_key")
        .count()
        .where(F.col("count") > 1)
        .count()
    )
    invalid_iso = mapping.where(
        (
            F.col("NORMALIZED_ISO2").isNotNull()
            & (F.length(F.trim("NORMALIZED_ISO2")) != 2)
        )
        | (
            F.col("NORMALIZED_ISO3").isNotNull()
            & (F.length(F.trim("NORMALIZED_ISO3")) != 3)
        )
    ).count()
    checks.extend(
        [
            _check(
                "mapping.duplicate_null_safe_key",
                "FAIL",
                mapping_key_duplicates,
                "Normalized name/code mapping keys must be unique.",
            ),
            _check(
                "mapping.invalid_iso_length",
                "FAIL",
                invalid_iso,
                "Non-null normalized ISO values must have valid lengths.",
            ),
        ]
    )

    negative_cases = frames["ecdc"].where(F.col("CASES") < 0).count()
    negative_deaths = frames["ecdc"].where(F.col("DEATHS") < 0).count()
    failures = [
        item for item in checks if item["severity"] == "FAIL" and not item["passed"]
    ]
    warnings = [
        item for item in checks if item["severity"] == "WARN" and not item["passed"]
    ]
    status = "FAIL" if failures else "WARN" if warnings else "PASS"
    return {
        "ruleset_version": QUALITY_RULESET_VERSION,
        "status": status,
        "checks": checks,
        "informational": {
            "negative_case_corrections": negative_cases,
            "negative_death_corrections": negative_deaths,
        },
    }
