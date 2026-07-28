from __future__ import annotations

import hashlib
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pandas as pd

from scripts import load_population


def population_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "COUNTRY_CODE_ISO2": "LV",
                "COUNTRY_CODE_ISO3": "LVA",
                "COUNTRY_NAME": "Latvia",
                "POPULATION": 1_900_000,
                "POPULATION_YEAR": 2020,
            },
            {
                "COUNTRY_CODE_ISO2": "EE",
                "COUNTRY_CODE_ISO3": "EST",
                "COUNTRY_NAME": "Estonia",
                "POPULATION": 1_300_000,
                "POPULATION_YEAR": 2020,
            },
        ]
    )


class FakeCursor:
    def __init__(self, *, target_exists: bool, valid_load: bool = True) -> None:
        self.target_exists = target_exists
        self.valid_load = valid_load
        self.queries: list[str] = []
        self.last_query = ""
        self.closed = False

    def execute(self, query: str, _parameters=()) -> None:
        self.last_query = " ".join(query.split())
        self.queries.append(self.last_query)

    def fetchone(self):
        if self.last_query.startswith("SELECT COUNT(*) AS ROW_COUNT"):
            return (2, 2, 0, 0, 0) if self.valid_load else (2, 1, 0, 0, 0)
        if self.last_query.startswith("SHOW TABLES"):
            return ("target",) if self.target_exists else None
        raise AssertionError(f"Unexpected fetch for query: {self.last_query}")

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.cursor_instance = cursor

    def cursor(self) -> FakeCursor:
        return self.cursor_instance


class PopulationLoaderTests(unittest.TestCase):
    def _temporary_root(self) -> Path:
        root = Path("outputs/test-population-loader") / uuid4().hex
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root, True)
        return root

    def _refresh(self, *, target_exists: bool, valid_load: bool = True):
        root = self._temporary_root()
        cursor = FakeCursor(target_exists=target_exists, valid_load=valid_load)
        connection = FakeConnection(cursor)
        patches = (
            patch(
                "scripts.load_population.build_population_dataframe",
                return_value=population_dataframe(),
            ),
            patch(
                "scripts.load_population.write_pandas",
                return_value=(True, 1, 2, None),
            ),
            patch(
                "scripts.load_population._staging_table_name",
                return_value="WORLD_BANK_POPULATION_2020_STAGING_TEST",
            ),
        )
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        manifest = load_population.refresh_population(
            connection=connection,
            csv_path=root / "population.csv",
            manifest_path=root / "manifest.json",
        )
        return manifest, cursor, root

    def test_new_target_renames_validated_staging_table(self) -> None:
        manifest, cursor, root = self._refresh(target_exists=False)
        queries = "\n".join(cursor.queries)
        self.assertIn(
            "RENAME TO COVID_ANALYTICS.RAW.WORLD_BANK_POPULATION_2020", queries
        )
        self.assertNotIn("SWAP WITH", queries)
        self.assertEqual(manifest["loaded_rows"], 2)
        self.assertEqual(
            json.loads((root / "manifest.json").read_text(encoding="utf-8"))[
                "data_sha256"
            ],
            manifest["data_sha256"],
        )

    def test_existing_target_swaps_then_drops_old_table(self) -> None:
        _, cursor, _ = self._refresh(target_exists=True)
        queries = "\n".join(cursor.queries)
        self.assertIn(
            "SWAP WITH COVID_ANALYTICS.RAW.WORLD_BANK_POPULATION_2020", queries
        )
        self.assertIn(
            "DROP TABLE COVID_ANALYTICS.RAW.WORLD_BANK_POPULATION_2020_STAGING_TEST",
            queries,
        )

    def test_failed_loaded_validation_drops_only_staging(self) -> None:
        root = self._temporary_root()
        cursor = FakeCursor(target_exists=True, valid_load=False)
        connection = FakeConnection(cursor)
        with (
            patch(
                "scripts.load_population.build_population_dataframe",
                return_value=population_dataframe(),
            ),
            patch(
                "scripts.load_population.write_pandas",
                return_value=(True, 1, 2, None),
            ),
            patch(
                "scripts.load_population._staging_table_name",
                return_value="WORLD_BANK_POPULATION_2020_STAGING_TEST",
            ),
        ):
            with self.assertRaises(load_population.PopulationValidationError):
                load_population.refresh_population(
                    connection=connection,
                    csv_path=root / "population.csv",
                    manifest_path=root / "manifest.json",
                )

        queries = "\n".join(cursor.queries)
        self.assertIn("DROP TABLE IF EXISTS", queries)
        self.assertNotIn("SWAP WITH", queries)
        self.assertFalse((root / "manifest.json").exists())

    def test_invalid_download_never_opens_snowflake_cursor(self) -> None:
        invalid = population_dataframe()
        invalid.loc[1, "COUNTRY_CODE_ISO2"] = "LV"
        connection = MagicMock()
        with patch(
            "scripts.load_population.build_population_dataframe",
            return_value=invalid,
        ):
            with self.assertRaises(load_population.PopulationValidationError):
                load_population.refresh_population(connection=connection)
        connection.cursor.assert_not_called()

    def test_snapshot_mode_validates_checksum_without_calling_api(self) -> None:
        root = self._temporary_root()
        csv_path = root / "population.csv"
        source_manifest_path = root / "population.manifest.json"
        publication_manifest_path = root / "publication.json"
        dataframe = population_dataframe()
        csv_content = dataframe.to_csv(index=False, lineterminator="\n")
        csv_path.write_text(csv_content, encoding="utf-8")
        source_manifest_path.write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "checksum_canonicalization": "covid-population-csv-v1",
                    "population_year": 2020,
                    "row_count": 2,
                    "data_sha256": hashlib.sha256(
                        csv_content.encode("utf-8")
                    ).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        cursor = FakeCursor(target_exists=False)
        connection = FakeConnection(cursor)

        with (
            patch("scripts.load_population.build_population_dataframe") as api_source,
            patch(
                "scripts.load_population.write_pandas",
                return_value=(True, 1, 2, None),
            ),
            patch(
                "scripts.load_population._staging_table_name",
                return_value="WORLD_BANK_POPULATION_2020_STAGING_TEST",
            ),
        ):
            manifest = load_population.refresh_population(
                connection=connection,
                csv_path=csv_path,
                source_manifest_path=source_manifest_path,
                manifest_path=publication_manifest_path,
                source_mode="snapshot",
            )

        api_source.assert_not_called()
        self.assertEqual(manifest["source_mode"], "snapshot")
        self.assertEqual(manifest["loaded_rows"], 2)

    def test_snapshot_checksum_failure_never_opens_snowflake_cursor(self) -> None:
        root = self._temporary_root()
        csv_path = root / "population.csv"
        source_manifest_path = root / "population.manifest.json"
        csv_path.write_text(
            population_dataframe().to_csv(index=False, lineterminator="\n"),
            encoding="utf-8",
        )
        source_manifest_path.write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "checksum_canonicalization": "covid-population-csv-v1",
                    "population_year": 2020,
                    "row_count": 2,
                    "data_sha256": "incorrect",
                }
            ),
            encoding="utf-8",
        )
        connection = MagicMock()

        with self.assertRaises(load_population.PopulationValidationError):
            load_population.refresh_population(
                connection=connection,
                csv_path=csv_path,
                source_manifest_path=source_manifest_path,
                manifest_path=root / "publication.json",
                source_mode="snapshot",
            )

        connection.cursor.assert_not_called()

    def test_snapshot_preserves_namibia_iso2_code(self) -> None:
        root = self._temporary_root()
        csv_path = root / "population.csv"
        source_manifest_path = root / "population.manifest.json"
        csv_content = (
            "COUNTRY_CODE_ISO2,COUNTRY_CODE_ISO3,COUNTRY_NAME,POPULATION,POPULATION_YEAR\n"
            "NA,NAM,Namibia,2489098,2020\n"
        )
        csv_path.write_text(csv_content, encoding="utf-8")
        source_manifest_path.write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "checksum_canonicalization": "covid-population-csv-v1",
                    "population_year": 2020,
                    "row_count": 1,
                    "data_sha256": hashlib.sha256(
                        csv_content.encode("utf-8")
                    ).hexdigest(),
                }
            ),
            encoding="utf-8",
        )

        dataframe = load_population.load_population_snapshot(
            csv_path,
            source_manifest_path,
        )

        self.assertEqual(dataframe.iloc[0]["COUNTRY_CODE_ISO2"], "NA")


if __name__ == "__main__":
    unittest.main()
