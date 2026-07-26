from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import types as T

from app.spark_pipeline.benchmark import layout_decision, plan_evidence
from app.spark_pipeline.quality import inspect_header, read_bronze_source
from app.spark_pipeline.schemas import ECDC
from app.spark_pipeline.transformations import (
    duplicate_population_keys,
    enrich_with_population,
    normalized_daily,
)


class SparkPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .appName("spark-pipeline-tests")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.adaptive.enabled", "true")
            .config("spark.sql.autoBroadcastJoinThreshold", -1)
            .getOrCreate()
        )
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.spark.stop()

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
        self.assertEqual(duplicate_population_keys(self._population()), [])
        self.assertGreaterEqual(evidence["broadcast_hash_join_count"], 2)
        self.assertGreaterEqual(evidence["build_right_count"], 2)
        self.assertTrue(
            evidence["null_safe_mapping_key_present"],
            evidence["join_operators"],
        )

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


if __name__ == "__main__":
    unittest.main()
