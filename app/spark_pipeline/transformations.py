from __future__ import annotations

from typing import Any

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F


def _clean_code(column: Column) -> Column:
    cleaned = F.upper(F.trim(column))
    return F.when(F.length(cleaned) > 0, cleaned)


def _source_name(column: Column) -> Column:
    return F.upper(F.trim(column))


def _typed_key(prefix: str, column: Column) -> Column:
    return F.when(column.isNotNull(), F.concat(F.lit(f"{prefix}:"), column))


def normalized_daily(
    ecdc: DataFrame,
    mapping: DataFrame,
    *,
    broadcast_mapping: bool,
) -> DataFrame:
    source = ecdc.select(
        F.trim("COUNTRY_REGION").alias("source_country_name"),
        _source_name(F.col("COUNTRY_REGION")).alias("source_name_key"),
        _clean_code(F.col("ISO3166_1")).alias("source_code_key"),
        F.col("REPORT_DATE").alias("report_date"),
        F.col("CASES").alias("cases"),
        F.col("DEATHS").alias("deaths"),
        "_source_file",
        "_source_batch_id",
        "_ingestion_id",
        "_ingested_at_utc",
    ).where(F.col("source_country_name").isNotNull() & F.col("report_date").isNotNull())

    mapping_keys = mapping.select(
        _source_name(F.col("SOURCE_COUNTRY_NAME")).alias("mapping_name_key"),
        _clean_code(F.col("SOURCE_COUNTRY_CODE")).alias("mapping_code_key"),
        F.trim("NORMALIZED_COUNTRY_NAME").alias("mapped_country"),
        _clean_code(F.col("NORMALIZED_ISO2")).alias("mapped_iso2"),
        _clean_code(F.col("NORMALIZED_ISO3")).alias("mapped_iso3"),
        F.col("EXPECTED_POPULATION_MATCH").alias("expected_population_match"),
    )
    if broadcast_mapping:
        mapping_keys = F.broadcast(mapping_keys)

    joined = source.join(
        mapping_keys,
        (F.col("source_name_key") == F.col("mapping_name_key"))
        & F.col("source_code_key").eqNullSafe(F.col("mapping_code_key")),
        "left",
    )
    source_iso2 = F.when(F.length("source_code_key") == 2, F.col("source_code_key"))
    source_iso3 = F.when(F.length("source_code_key") == 3, F.col("source_code_key"))
    normalized = joined.select(
        F.coalesce(
            "mapped_country",
            F.regexp_replace("source_country_name", "_", " "),
        ).alias("country"),
        F.coalesce("mapped_iso2", source_iso2).alias("country_iso2"),
        F.coalesce("mapped_iso3", source_iso3).alias("country_iso3"),
        "report_date",
        F.coalesce("cases", F.lit(0)).cast("long").alias("cases"),
        F.coalesce("deaths", F.lit(0)).cast("long").alias("deaths"),
        F.col("mapping_name_key").isNotNull().alias("mapping_applied"),
        F.coalesce("expected_population_match", F.lit(True)).alias(
            "expected_population_match"
        ),
        "_source_file",
        "_source_batch_id",
        "_ingestion_id",
        "_ingested_at_utc",
    ).withColumn(
        "location_key",
        F.coalesce("country_iso3", "country_iso2", F.upper("country")),
    )

    return normalized.groupBy(
        "country",
        "country_iso2",
        "country_iso3",
        "location_key",
        "report_date",
        "_source_batch_id",
        "_ingestion_id",
        "_ingested_at_utc",
    ).agg(
        F.sum("cases").cast("long").alias("new_cases_raw"),
        F.sum("deaths").cast("long").alias("new_deaths_raw"),
        F.count(F.lit(1)).alias("source_row_count"),
        F.max(F.col("mapping_applied").cast("int"))
        .cast("boolean")
        .alias("mapping_applied"),
        F.min(F.col("expected_population_match").cast("int"))
        .cast("boolean")
        .alias("expected_population_match"),
        F.concat_ws(",", F.sort_array(F.collect_set("_source_file"))).alias(
            "_source_files"
        ),
    )


def population_lookup(population: DataFrame) -> DataFrame:
    population_base = population.select(
        _clean_code(F.col("COUNTRY_CODE_ISO2")).alias("population_iso2"),
        _clean_code(F.col("COUNTRY_CODE_ISO3")).alias("population_iso3"),
        F.trim("COUNTRY_NAME").alias("population_country_name"),
        F.col("POPULATION").alias("population"),
        F.col("POPULATION_YEAR").alias("population_year"),
    )
    return population_base.select(
        F.explode(
            F.array_compact(
                F.array(
                    _typed_key("ISO2", F.col("population_iso2")),
                    _typed_key("ISO3", F.col("population_iso3")),
                )
            )
        ).alias("population_lookup_key"),
        "population_iso2",
        "population_iso3",
        "population_country_name",
        "population",
        "population_year",
    )


def enrich_with_population(
    daily: DataFrame,
    population: DataFrame,
    *,
    broadcast_population: bool,
) -> DataFrame:
    lookup = population_lookup(population)
    if broadcast_population:
        lookup = F.broadcast(lookup)

    daily_with_key = daily.withColumn(
        "population_lookup_key",
        F.coalesce(
            _typed_key("ISO2", F.col("country_iso2")),
            _typed_key("ISO3", F.col("country_iso3")),
        ),
    )
    return daily_with_key.join(lookup, "population_lookup_key", "left").select(
        "country",
        "country_iso2",
        "country_iso3",
        "location_key",
        "report_date",
        "new_cases_raw",
        "new_deaths_raw",
        "source_row_count",
        "population",
        "population_year",
        "population_lookup_key",
        F.when(F.col("population").isNotNull(), F.lit("MATCHED"))
        .when(~F.col("expected_population_match"), F.lit("NOT_EXPECTED"))
        .otherwise(F.lit("UNMATCHED"))
        .alias("population_join_status"),
        "mapping_applied",
        "expected_population_match",
        "_source_batch_id",
        "_ingestion_id",
        "_ingested_at_utc",
        "_source_files",
    )


def duplicate_count(daily: DataFrame) -> int:
    row = daily.agg(F.sum(F.col("source_row_count") - 1).alias("duplicates")).first()
    return int(row["duplicates"] or 0)


def duplicate_population_keys(population: DataFrame) -> list[dict[str, Any]]:
    rows = (
        population_lookup(population)
        .groupBy("population_lookup_key")
        .count()
        .where(F.col("count") > 1)
        .collect()
    )
    return [row.asDict(recursive=True) for row in rows]
