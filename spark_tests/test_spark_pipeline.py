from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import types as T

from app.spark_pipeline.benchmark import layout_decision, plan_evidence
from app.spark_pipeline.pipeline import (
    _country_context_equivalence,
    _enriched_context_metrics,
    _publish_bronze,
    _quality_summary,
)
from app.spark_pipeline.quality import inspect_header, read_bronze_source
from app.spark_pipeline.runtime_policy import SparkRuntimePolicy
from app.spark_pipeline.schemas import ECDC
from app.spark_pipeline.transformations import (
    context_eligible_country_baseline,
    country_baseline,
    duplicate_population_keys,
    enrich_with_country_context,
    enrich_with_population,
    normalized_daily,
)
from app.spark_pipeline.world_bank_checksum import spark_observation_checksum
from app.world_bank import observation_checksum
from spark_tests.spark_test_support import (
    install_pyspark_socket_warning_filter,
    stop_test_spark_session,
)


class SparkPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_pyspark_socket_warning_filter()
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("spark-pipeline-tests")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.adaptive.enabled", "true")
            .config("spark.sql.autoBroadcastJoinThreshold", -1)
            .getOrCreate()
        )
        cls.addClassCleanup(stop_test_spark_session, cls.spark)
        cls.spark.sparkContext.setLogLevel("ERROR")

    def _ecdc(self):
        schema = T.StructType(
            [
                T.StructField("COUNTRY_REGION", T.StringType()),
                T.StructField("ISO3166_1", T.StringType()),
                T.StructField("REPORT_DATE", T.DateType()),
                T.StructField("CASES", T.LongType()),
                T.StructField("DEATHS", T.LongType()),
                T.StructField("_source_file", T.StringType()),
                T.StructField("_source_batch_id", T.StringType()),
                T.StructField("_ingestion_id", T.StringType()),
                T.StructField("_ingested_at_utc", T.TimestampType()),
            ]
        )
        timestamp = datetime(2026, 1, 1, tzinfo=UTC)
        return self.spark.createDataFrame(
            [
                (
                    "Latvia",
                    "LV",
                    datetime(2020, 3, 1).date(),
                    2,
                    0,
                    "a",
                    "s",
                    "i",
                    timestamp,
                ),
                (
                    "Latvia",
                    "LV",
                    datetime(2020, 3, 1).date(),
                    3,
                    1,
                    "a",
                    "s",
                    "i",
                    timestamp,
                ),
                (
                    "Namibia",
                    None,
                    datetime(2020, 3, 1).date(),
                    4,
                    0,
                    "a",
                    "s",
                    "i",
                    timestamp,
                ),
                (
                    "Cases on an international conveyance Japan",
                    None,
                    datetime(2020, 3, 1).date(),
                    5,
                    0,
                    "a",
                    "s",
                    "i",
                    timestamp,
                ),
            ],
            schema,
        )

    def _indicators(self):
        schema = T.StructType(
            [
                T.StructField("SNAPSHOT_ID", T.StringType()),
                T.StructField("CANONICAL_ISO2", T.StringType()),
                T.StructField("CANONICAL_ISO3", T.StringType()),
                T.StructField("IDENTITY_MAPPING_STATUS", T.StringType()),
                T.StructField("IS_AGGREGATE", T.BooleanType()),
                T.StructField("INDICATOR_CODE", T.StringType()),
                T.StructField("OBSERVATION_YEAR", T.LongType()),
                T.StructField("INDICATOR_VALUE", T.DecimalType(38, 9)),
            ]
        )
        return self.spark.createDataFrame(
            [
                (
                    "snapshot",
                    "LV",
                    "LVA",
                    "matched_iso",
                    False,
                    "SP.POP.TOTL",
                    2020,
                    Decimal("1900449"),
                ),
                (
                    "snapshot",
                    "LV",
                    "LVA",
                    "matched_iso",
                    False,
                    "EN.POP.DNST",
                    2019,
                    Decimal("30.755491989"),
                ),
                (
                    "snapshot",
                    "LV",
                    "LVA",
                    "matched_iso",
                    False,
                    "SP.POP.65UP.TO.ZS",
                    2019,
                    Decimal("20.407227220"),
                ),
                (
                    "snapshot",
                    "LV",
                    "LVA",
                    "matched_iso",
                    False,
                    "NY.GDP.PCAP.KD",
                    2019,
                    Decimal("15328.385993330"),
                ),
                (
                    "snapshot",
                    "LV",
                    "LVA",
                    "matched_iso",
                    False,
                    "SH.XPD.CHEX.PP.CD",
                    2019,
                    Decimal("2202.675929217"),
                ),
            ],
            schema,
        )

    def test_country_context_is_narrow_before_broadcast(self) -> None:
        baseline = country_baseline(self._indicators())
        self.assertEqual(baseline.count(), 1)
        self.assertEqual(
            baseline.first()["context_iso3"],
            "LVA",
        )
        plan = enrich_with_country_context(
            self.spark.createDataFrame(
                [("LV", None, 1), ("LV", None, 2)],
                "country_iso2 string, country_iso3 string, observation long",
            ),
            baseline,
            broadcast_baseline=True,
        )
        self.assertEqual(plan.count(), 2)
        self.assertEqual(plan.select("context_snapshot_id").distinct().count(), 1)

    def test_context_baseline_preserves_eligible_countries_without_wdi(self) -> None:
        daily = normalized_daily(
            self._ecdc(),
            self._mapping(),
            broadcast_mapping=True,
        )
        population_enriched = enrich_with_population(
            daily,
            self._population(),
            broadcast_population=True,
        )
        baseline = context_eligible_country_baseline(
            population_enriched,
            country_baseline(self._indicators()),
            broadcast_baseline=True,
        )
        rows = {row["context_iso3"]: row.asDict() for row in baseline.collect()}

        self.assertEqual(set(rows), {"LVA", "NAM"})
        self.assertEqual(rows["LVA"]["context_snapshot_id"], "snapshot")
        self.assertIsNone(rows["NAM"]["context_snapshot_id"])

        enriched = enrich_with_country_context(
            population_enriched,
            country_baseline(self._indicators()),
            broadcast_baseline=True,
        )
        evidence = _country_context_equivalence(
            baseline,
            _enriched_context_metrics(enriched),
            None,
        )
        self.assertEqual(evidence["baseline_rows"], 2)
        self.assertEqual(evidence["joined_covid_rows"], 3)
        self.assertEqual(evidence["unmatched_location_count"], 2)

    def test_world_bank_checksum_matches_python_golden_protocol(self) -> None:
        source = {
            "CANONICAL_ISO2": "LV",
            "CANONICAL_ISO3": "LVA",
            "COUNTRY_NAME": "Latvia",
            "INDICATOR_CODE": "NY.GDP.PCAP.KD",
            "INDICATOR_NAME": "GDP per capita (constant 2015 US$)",
            "INDICATOR_UNIT": "constant 2015 US$",
            "SOURCE_ID": 2,
            "SOURCE_NAME": "World Development Indicators",
            "OBSERVATION_YEAR": 2019,
            "INDICATOR_VALUE": Decimal("123.400000000"),
            "OBSERVATION_STATUS": None,
            "SOURCE_DECIMAL_PRECISION": 1,
            "SOURCE_LAST_UPDATED": "2026-07-01",
        }
        indicators = self.spark.createDataFrame(
            [tuple(source.values())],
            T.StructType(
                [
                    T.StructField("CANONICAL_ISO2", T.StringType()),
                    T.StructField("CANONICAL_ISO3", T.StringType()),
                    T.StructField("COUNTRY_NAME", T.StringType()),
                    T.StructField("INDICATOR_CODE", T.StringType()),
                    T.StructField("INDICATOR_NAME", T.StringType()),
                    T.StructField("INDICATOR_UNIT", T.StringType()),
                    T.StructField("SOURCE_ID", T.LongType()),
                    T.StructField("SOURCE_NAME", T.StringType()),
                    T.StructField("OBSERVATION_YEAR", T.LongType()),
                    T.StructField("INDICATOR_VALUE", T.DecimalType(38, 9)),
                    T.StructField("OBSERVATION_STATUS", T.StringType()),
                    T.StructField("SOURCE_DECIMAL_PRECISION", T.LongType()),
                    T.StructField("SOURCE_LAST_UPDATED", T.StringType()),
                ]
            ),
        )

        expected = observation_checksum([source])
        self.assertEqual(spark_observation_checksum(indicators), expected)
        self.assertEqual(
            expected,
            "30ecf2520c6e69c69fe991ef1ef2f902e026810c3817aa54dc07e6a441b12e0d",
        )

    def _mapping(self):
        return self.spark.createDataFrame(
            [
                ("Namibia", None, "Namibia", "NA", "NAM", True),
                (
                    "Cases on an international conveyance Japan",
                    None,
                    "International conveyance (Japan)",
                    None,
                    None,
                    False,
                ),
            ],
            (
                "SOURCE_COUNTRY_NAME string, SOURCE_COUNTRY_CODE string, "
                "NORMALIZED_COUNTRY_NAME string, NORMALIZED_ISO2 string, "
                "NORMALIZED_ISO3 string, EXPECTED_POPULATION_MATCH boolean"
            ),
        )

    def _population(self):
        return self.spark.createDataFrame(
            [
                ("LV", "LVA", "Latvia", 1_900_000, 2020),
                ("NA", "NAM", "Namibia", 2_500_000, 2020),
            ],
            (
                "COUNTRY_CODE_ISO2 string, COUNTRY_CODE_ISO3 string, "
                "COUNTRY_NAME string, POPULATION long, POPULATION_YEAR long"
            ),
        )

    def test_duplicate_aggregation_and_null_safe_mapping(self) -> None:
        daily = normalized_daily(
            self._ecdc(),
            self._mapping(),
            broadcast_mapping=True,
        )
        rows = {row["country"]: row.asDict() for row in daily.collect()}

        self.assertEqual(rows["Latvia"]["new_cases_raw"], 5)
        self.assertEqual(rows["Latvia"]["source_row_count"], 2)
        self.assertEqual(rows["Namibia"]["country_iso2"], "NA")
        self.assertTrue(rows["Namibia"]["mapping_applied"])
        self.assertIn("International conveyance (Japan)", rows)

    def test_typed_population_join_and_broadcast_plan(self) -> None:
        daily = normalized_daily(
            self._ecdc(),
            self._mapping(),
            broadcast_mapping=True,
        )
        enriched = enrich_with_population(
            daily,
            self._population(),
            broadcast_population=True,
        )
        enriched.collect()
        latvia = enriched.where("country = 'Latvia'").first()
        evidence = plan_evidence(enriched)

        self.assertEqual(latvia["population_lookup_key"], "ISO2:LV")
        self.assertEqual(latvia["population"], 1_900_000)
        self.assertEqual(latvia["country_iso3"], "LVA")
        self.assertEqual(duplicate_population_keys(self._population()), [])
        self.assertGreaterEqual(evidence["broadcast_hash_join_count"], 2)
        self.assertGreaterEqual(evidence["build_right_count"], 2)
        self.assertTrue(
            evidence["null_safe_mapping_key_present"],
            evidence["join_operators"],
        )

    def test_size_gated_dimensions_can_use_non_broadcast_plans(self) -> None:
        daily = normalized_daily(
            self._ecdc(),
            self._mapping(),
            broadcast_mapping=False,
        )
        population_enriched = enrich_with_population(
            daily,
            self._population(),
            broadcast_population=False,
        )
        enriched = enrich_with_country_context(
            population_enriched,
            country_baseline(self._indicators()),
            broadcast_baseline=False,
        )
        enriched.collect()
        evidence = plan_evidence(enriched)

        self.assertEqual(evidence["broadcast_hash_join_count"], 0)
        self.assertTrue(evidence["null_safe_mapping_key_present"])
        self.assertTrue(evidence["typed_population_key_present"])

    def test_current_scale_layout_is_one_unpartitioned_file(self) -> None:
        decision = layout_decision(
            measured_parquet_bytes=2_000_000,
            monthly_parquet_bytes=[100_000] * 10,
        )
        self.assertEqual(decision["target_file_count"], 1)
        self.assertEqual(decision["partition_columns"], [])

    def test_explicit_schema_provenance_corruption_and_header_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / ECDC.filename
            source.write_text(
                "COUNTRY_REGION,ISO3166_1,REPORT_DATE,CASES,DEATHS\n"
                "Latvia,LV,2020-03-01,not-a-number,0.0\n",
                encoding="utf-8",
            )
            header = inspect_header(source, ECDC.headers)
            dataframe = read_bronze_source(
                self.spark,
                ECDC,
                source,
                source_batch_id="fixture-source",
                ingestion_id="fixture-ingestion",
                ingested_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            try:
                row = dataframe.first()
                self.assertTrue(header["matches"])
                self.assertIsNone(row["CASES"])
                self.assertIsNotNone(row["_corrupt_record"])
                self.assertEqual(row["_source_batch_id"], "fixture-source")
                self.assertEqual(row["_ingestion_id"], "fixture-ingestion")
                self.assertIsNotNone(row["_ingested_at_utc"])
            finally:
                dataframe.unpersist()

            source.write_text(
                "COUNTRY_REGION,ISO3166_1,DATE,CASES,DEATHS\n",
                encoding="utf-8",
            )
            self.assertFalse(inspect_header(source, ECDC.headers)["matches"])

    def test_bronze_manifest_v3_contains_quality_and_runtime_provenance(self) -> None:
        quality = {
            "ruleset_version": "bronze-quality-v2",
            "status": "WARN",
            "checks": [],
        }
        quality_summary = _quality_summary(quality)
        dataframe = self.spark.createDataFrame(
            [("value", None)],
            "value string, _corrupt_record string",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = _publish_bronze(
                {
                    "ecdc": dataframe,
                    "indicators": dataframe,
                    "population": dataframe,
                    "mapping": dataframe,
                    "covid_extended": dataframe,
                },
                bronze_root=root,
                source_batch_id="source-v1",
                source_batch_sha256="a" * 64,
                source_files={},
                ingestion_id="bronze-v1",
                spark_application_id="local-fixture",
                quality_summary=quality_summary,
                world_bank_snapshot_id="snapshot",
                snowflake_context_fingerprint=None,
                source_kind="fixture",
                runtime_policy=SparkRuntimePolicy.from_manifest({"files": {}}),
            )
            manifest = json.loads(
                (target / "manifest.json").read_text(encoding="utf-8")
            )

            self.assertEqual(manifest["manifest_version"], 3)
            self.assertEqual(manifest["source_kind"], "fixture")
            self.assertEqual(manifest["quality_summary"], quality_summary)
            self.assertEqual(manifest["world_bank_snapshot_id"], "snapshot")
            self.assertEqual(
                set(manifest["datasets"]),
                {"covid_extended", "ecdc", "indicators", "mapping", "population"},
            )


if __name__ == "__main__":
    unittest.main()
