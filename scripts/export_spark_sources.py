from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import snowflake.connector
from dotenv import load_dotenv

# Running ``python scripts/export_spark_sources.py`` makes ``scripts`` the first
# import location. Add the repository root so the documented direct-file command
# can import the sibling ``app`` package without requiring PYTHONPATH changes.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.country_context_fingerprint import (  # noqa: E402
    COUNTRY_CONTEXT_FINGERPRINT_COLUMNS,
    country_context_fingerprint,
)
from app.logging_config import (  # noqa: E402
    configure_logging,
    sanitized_exception_info,
)
from app.spark_pipeline.identifiers import valid_batch_id  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_ROOT = Path("data/source")
DEFAULT_POPULATION_PATH = Path("data/external/world_bank_population_2020.csv")
DEFAULT_INDICATORS_PATH = Path("data/external/world_bank_indicators_2019_2021.csv")
DEFAULT_INDICATORS_MANIFEST_PATH = Path(
    "data/external/world_bank_indicators_2019_2021.manifest.json"
)

ECDC_QUERY = """
    SELECT
        COUNTRY_REGION,
        ISO3166_1,
        DATE AS REPORT_DATE,
        CASES,
        DEATHS
    FROM COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.ECDC_GLOBAL
    ORDER BY DATE, COUNTRY_REGION, ISO3166_1
"""

COVID_EXTENDED_QUERY = """
    SELECT
        COUNTRY,
        COUNTRY_ISO2,
        COUNTRY_ISO3,
        LOCATION_KEY,
        REPORT_DATE,
        NEW_CASES_RAW,
        NEW_DEATHS_RAW,
        CASES_CUMULATIVE,
        DEATHS_CUMULATIVE,
        COVID_RATE_POPULATION_2020,
        NEW_CASES_PER_100K,
        NEW_DEATHS_PER_100K,
        CASES_PER_100K,
        DEATHS_PER_100K,
        HAS_NEGATIVE_CASE_CORRECTION,
        HAS_NEGATIVE_DEATH_CORRECTION,
        SOURCE_NAME,
        SERIES_SEGMENT
    FROM COVID_ANALYTICS.MARTS.COVID_ENRICHED_EXTENDED
    ORDER BY REPORT_DATE, COUNTRY_ISO3, LOCATION_KEY
"""

MAPPING_QUERY = """
    SELECT
        SOURCE_COUNTRY_NAME,
        SOURCE_COUNTRY_CODE,
        NORMALIZED_COUNTRY_NAME,
        NORMALIZED_ISO2,
        NORMALIZED_ISO3,
        EXPECTED_POPULATION_MATCH
    FROM COVID_ANALYTICS.RAW.COUNTRY_CODE_MAPPING
    ORDER BY SOURCE_COUNTRY_NAME, SOURCE_COUNTRY_CODE
"""

COUNTRY_CONTEXT_QUERY = """
    SELECT
        ISO3,
        POPULATION_2020_CONTEXT,
        POPULATION_DENSITY_2019,
        POPULATION_AGE_65_PLUS_PCT_2019,
        REAL_GDP_PER_CAPITA_2019,
        HEALTH_EXPENDITURE_PER_CAPITA_PPP_2019,
        SNAPSHOT_ID
    FROM COVID_ANALYTICS.MARTS.COUNTRY_BASELINE_2019
    ORDER BY ISO3
"""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_query(cursor: Any, query: str, output_path: Path) -> int:
    cursor.execute(query)
    headers = [column[0].upper() for column in cursor.description]
    row_count = 0
    with output_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(headers)
        while rows := cursor.fetchmany(10_000):
            writer.writerows(rows)
            row_count += len(rows)
    return row_count


def fetch_context_fingerprint(cursor: Any) -> dict[str, Any]:
    cursor.execute(COUNTRY_CONTEXT_QUERY)
    headers = tuple(column[0].upper() for column in cursor.description)
    if headers != COUNTRY_CONTEXT_FINGERPRINT_COLUMNS:
        raise RuntimeError("Snowflake country-context fingerprint columns drifted.")
    rows: list[dict[str, Any]] = []
    while batch := cursor.fetchmany(10_000):
        rows.extend(dict(zip(headers, row, strict=True)) for row in batch)
    return country_context_fingerprint(rows)


def _required_environment() -> dict[str, str]:
    names = (
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_WAREHOUSE",
    )
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")
    return {name: os.environ[name] for name in names}


def connect_to_snowflake() -> snowflake.connector.SnowflakeConnection:
    environment = _required_environment()
    return snowflake.connector.connect(
        account=environment["SNOWFLAKE_ACCOUNT"],
        user=environment["SNOWFLAKE_USER"],
        password=environment["SNOWFLAKE_PASSWORD"],
        role=os.getenv("SNOWFLAKE_ROLE", "COVID_PROJECT_ADMIN"),
        warehouse=environment["SNOWFLAKE_WAREHOUSE"],
        application="COVID_SPARK_SOURCE_EXPORT",
    )


def _manifest_entry(path: Path, row_count: int) -> dict[str, object]:
    return {
        "filename": path.name,
        "row_count": row_count,
        "byte_count": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def _csv_row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as source:
        return max(sum(1 for _ in csv.reader(source)) - 1, 0)


def _extended_source_metadata(path: Path) -> dict[str, object]:
    minimum_report_date: str | None = None
    maximum_report_date: str | None = None
    countries: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            report_date = row.get("REPORT_DATE")
            iso3 = row.get("COUNTRY_ISO3")
            if report_date:
                minimum_report_date = min(
                    report_date,
                    minimum_report_date or report_date,
                )
                maximum_report_date = max(
                    report_date,
                    maximum_report_date or report_date,
                )
            if iso3:
                countries.add(iso3)
    return {
        "minimum_report_date": minimum_report_date,
        "maximum_report_date": maximum_report_date,
        "country_count": len(countries),
    }


def export_source_batch(
    *,
    source_batch_id: str,
    output_root: Path,
    population_path: Path,
    indicators_path: Path = DEFAULT_INDICATORS_PATH,
    indicators_manifest_path: Path = DEFAULT_INDICATORS_MANIFEST_PATH,
    connection: Any | None = None,
) -> Path:
    valid_batch_id(source_batch_id)
    target = output_root / source_batch_id
    if target.exists():
        raise FileExistsError(f"Source batch already exists: {source_batch_id}")
    if not population_path.is_file():
        raise FileNotFoundError("The population source CSV does not exist.")
    if not indicators_path.is_file() or not indicators_manifest_path.is_file():
        raise FileNotFoundError(
            "The committed WDI snapshot or manifest does not exist."
        )

    output_root.mkdir(parents=True, exist_ok=True)
    staging = output_root / f".{source_batch_id}.staging-{uuid4().hex}"
    staging.mkdir()
    owns_connection = connection is None
    active_connection = connection or connect_to_snowflake()

    try:
        cursor = active_connection.cursor()
        try:
            ecdc_path = staging / "ecdc_global.csv"
            covid_extended_path = staging / "covid_enriched_extended.csv"
            mapping_path = staging / "country_mapping.csv"
            population_copy = staging / "population.csv"
            indicators_copy = staging / "world_bank_indicators.csv"
            ecdc_rows = export_query(cursor, ECDC_QUERY, ecdc_path)
            mapping_rows = export_query(cursor, MAPPING_QUERY, mapping_path)
            covid_extended_rows = export_query(
                cursor,
                COVID_EXTENDED_QUERY,
                covid_extended_path,
            )
            snowflake_context_fingerprint = fetch_context_fingerprint(cursor)
            shutil.copy2(population_path, population_copy)
            shutil.copy2(indicators_path, indicators_copy)
        finally:
            cursor.close()

        extended_entry = _manifest_entry(covid_extended_path, covid_extended_rows)
        extended_entry.update(_extended_source_metadata(covid_extended_path))
        files = {
            "ecdc": _manifest_entry(ecdc_path, ecdc_rows),
            "covid_extended": extended_entry,
            "mapping": _manifest_entry(mapping_path, mapping_rows),
            "population": _manifest_entry(
                population_copy,
                _csv_row_count(population_copy),
            ),
            "indicators": _manifest_entry(
                indicators_copy,
                _csv_row_count(indicators_copy),
            ),
        }
        checksum_payload = "".join(str(files[name]["sha256"]) for name in sorted(files))
        manifest = {
            "manifest_version": 3,
            "source_kind": "snowflake_export",
            "source_batch_id": source_batch_id,
            "extracted_at_utc": datetime.now(UTC).isoformat(),
            "sources": {
                "ecdc": "COVID19_EPIDEMIOLOGICAL_DATA.PUBLIC.ECDC_GLOBAL",
                "covid_extended": ("COVID_ANALYTICS.MARTS.COVID_ENRICHED_EXTENDED"),
                "mapping": "COVID_ANALYTICS.RAW.COUNTRY_CODE_MAPPING",
                "population": "World Bank SP.POP.TOTL 2020 snapshot",
                "indicators": "World Development Indicators source 2, 2019-2021",
            },
            "world_bank_snapshot_id": json.loads(
                indicators_manifest_path.read_text(encoding="utf-8")
            )["snapshot_id"],
            "snowflake_context_fingerprint": snowflake_context_fingerprint,
            "files": files,
            "batch_sha256": hashlib.sha256(
                checksum_payload.encode("ascii")
            ).hexdigest(),
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        staging.replace(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        if owns_connection:
            active_connection.close()

    logger.info(
        "spark_source_batch_exported",
        extra={
            "source_batch_id": source_batch_id,
            "ecdc_row_count": ecdc_rows,
            "mapping_row_count": mapping_rows,
            "covid_extended_row_count": covid_extended_rows,
        },
    )
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export immutable Spark inputs.")
    parser.add_argument("--source-batch-id", required=True, type=valid_batch_id)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--population-input",
        type=Path,
        default=DEFAULT_POPULATION_PATH,
    )
    parser.add_argument(
        "--indicators-input",
        type=Path,
        default=DEFAULT_INDICATORS_PATH,
    )
    parser.add_argument(
        "--indicators-manifest",
        type=Path,
        default=DEFAULT_INDICATORS_MANIFEST_PATH,
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    configure_logging("spark-source-export", os.getenv("LOG_LEVEL", "INFO"))
    args = parse_args()
    try:
        export_source_batch(
            source_batch_id=args.source_batch_id,
            output_root=args.output_root,
            population_path=args.population_input,
            indicators_path=args.indicators_input,
            indicators_manifest_path=args.indicators_manifest,
        )
    except Exception as exc:
        logger.exception(
            "spark_source_export_failed",
            extra={"error_type": type(exc).__name__},
            exc_info=sanitized_exception_info(exc),
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
