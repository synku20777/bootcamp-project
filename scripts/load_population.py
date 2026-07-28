from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import string
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import snowflake.connector
from dotenv import load_dotenv
from snowflake.connector.pandas_tools import write_pandas

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.logging_config import configure_logging, sanitized_exception_info  # noqa: E402

WORLD_BANK_BASE_URL = "https://api.worldbank.org/v2"
POPULATION_YEAR = 2020
TARGET_DATABASE = "COVID_ANALYTICS"
TARGET_SCHEMA = "RAW"
TARGET_TABLE = "WORLD_BANK_POPULATION_2020"
DEFAULT_CSV_PATH = Path("data/external/world_bank_population_2020.csv")
DEFAULT_SOURCE_MANIFEST_PATH = Path(
    "data/external/world_bank_population_2020.manifest.json"
)
DEFAULT_MANIFEST_PATH = Path("outputs/setup/population-manifest.json")
logger = logging.getLogger(__name__)


class PopulationValidationError(RuntimeError):
    """Downloaded or loaded population data violates the publication contract."""


def request_world_bank_data(
    endpoint: str,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    response = requests.get(
        f"{WORLD_BANK_BASE_URL}/{endpoint}",
        params=params,
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError("World Bank returned an unexpected response shape.")
    return payload[1] or []


def build_population_dataframe() -> pd.DataFrame:
    country_metadata = request_world_bank_data(
        "country",
        {"format": "json", "per_page": 400},
    )
    valid_countries = {
        country["iso2Code"]: country
        for country in country_metadata
        if country.get("iso2Code") and country.get("region", {}).get("id") != "NA"
    }
    population_records = request_world_bank_data(
        "country/all/indicator/SP.POP.TOTL",
        {
            "format": "json",
            "date": str(POPULATION_YEAR),
            "per_page": 400,
        },
    )
    rows: list[dict[str, Any]] = []
    for record in population_records:
        iso2_code = record.get("country", {}).get("id")
        population = record.get("value")
        if iso2_code not in valid_countries or population is None:
            continue
        metadata = valid_countries[iso2_code]
        rows.append(
            {
                "COUNTRY_CODE_ISO2": iso2_code,
                "COUNTRY_CODE_ISO3": metadata.get("id"),
                "COUNTRY_NAME": metadata.get("name"),
                "POPULATION": int(population),
                "POPULATION_YEAR": POPULATION_YEAR,
            }
        )
    dataframe = pd.DataFrame(rows)
    if dataframe.empty:
        raise PopulationValidationError("No population records were downloaded.")
    return dataframe.sort_values("COUNTRY_NAME").reset_index(drop=True)


def validate_population_dataframe(dataframe: pd.DataFrame) -> None:
    required = {
        "COUNTRY_CODE_ISO2",
        "COUNTRY_CODE_ISO3",
        "COUNTRY_NAME",
        "POPULATION",
        "POPULATION_YEAR",
    }
    if set(dataframe.columns) != required:
        raise PopulationValidationError("Population columns do not match the contract.")
    if dataframe.empty:
        raise PopulationValidationError("Population data is empty.")
    if dataframe[list(required)].isnull().any().any():
        raise PopulationValidationError(
            "Population data contains required null values."
        )
    if dataframe["COUNTRY_CODE_ISO2"].duplicated().any():
        raise PopulationValidationError("Population ISO-2 keys are not unique.")
    if not dataframe["COUNTRY_CODE_ISO2"].astype(str).str.len().eq(2).all():
        raise PopulationValidationError("Population ISO-2 keys have invalid lengths.")
    if not dataframe["COUNTRY_CODE_ISO3"].astype(str).str.len().eq(3).all():
        raise PopulationValidationError("Population ISO-3 keys have invalid lengths.")
    if not dataframe["POPULATION"].gt(0).all():
        raise PopulationValidationError("Population values must be positive.")
    if set(dataframe["POPULATION_YEAR"].astype(int)) != {POPULATION_YEAR}:
        raise PopulationValidationError("Population year must be 2020.")


def _source_snapshot_manifest(
    dataframe: pd.DataFrame,
    csv_content: str,
) -> dict[str, Any]:
    return {
        "manifest_version": 1,
        "checksum_canonicalization": "covid-population-csv-v1",
        "source": "World Bank API",
        "source_endpoint": "country/all/indicator/SP.POP.TOTL",
        "indicator": "SP.POP.TOTL",
        "population_year": POPULATION_YEAR,
        "row_count": len(dataframe),
        "data_sha256": hashlib.sha256(csv_content.encode("utf-8")).hexdigest(),
    }


def load_population_snapshot(
    csv_path: Path,
    source_manifest_path: Path,
) -> pd.DataFrame:
    if not csv_path.is_file() or not source_manifest_path.is_file():
        raise PopulationValidationError(
            "The committed population snapshot or its source manifest is missing."
        )
    try:
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PopulationValidationError(
            "The committed population source manifest is unreadable."
        ) from exc

    if source_manifest.get("manifest_version") != 1:
        raise PopulationValidationError(
            "The committed population source manifest version is unsupported."
        )
    if source_manifest.get("checksum_canonicalization") != "covid-population-csv-v1":
        raise PopulationValidationError(
            "The committed population checksum protocol is unsupported."
        )
    if source_manifest.get("population_year") != POPULATION_YEAR:
        raise PopulationValidationError(
            "The committed population snapshot year does not match the loader."
        )

    try:
        dataframe = pd.read_csv(
            csv_path,
            dtype={
                "COUNTRY_CODE_ISO2": "string",
                "COUNTRY_CODE_ISO3": "string",
                "COUNTRY_NAME": "string",
            },
            keep_default_na=False,
        )
    except (OSError, UnicodeError, ValueError, pd.errors.ParserError) as exc:
        raise PopulationValidationError(
            "The committed population snapshot is unreadable."
        ) from exc
    if len(dataframe) != source_manifest.get("row_count"):
        raise PopulationValidationError(
            "The committed population snapshot row count does not match its manifest."
        )
    validate_population_dataframe(dataframe)
    canonical_csv = _dataframe_csv(dataframe)
    canonical_checksum = hashlib.sha256(canonical_csv.encode("utf-8")).hexdigest()
    if source_manifest.get("data_sha256") != canonical_checksum:
        raise PopulationValidationError(
            "The committed population snapshot checksum does not match its manifest."
        )
    return dataframe


def connect_to_snowflake() -> snowflake.connector.SnowflakeConnection:
    required_variables = [
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_SCHEMA",
    ]
    missing = [variable for variable in required_variables if not os.getenv(variable)]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")
    logger.info("snowflake_connection_started")
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        role=os.getenv("SNOWFLAKE_ROLE", "COVID_PROJECT_ADMIN"),
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        schema=os.environ["SNOWFLAKE_SCHEMA"],
    )


def _staging_table_name() -> str:
    alphabet = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{TARGET_TABLE}_STAGING_{suffix}"


def _table_exists(cursor: Any, table_name: str) -> bool:
    cursor.execute(
        f"SHOW TABLES LIKE '{table_name}' IN SCHEMA {TARGET_DATABASE}.{TARGET_SCHEMA}"
    )
    return cursor.fetchone() is not None


def _validate_loaded_table(cursor: Any, table_name: str, expected_rows: int) -> None:
    cursor.execute(
        f"""
        SELECT
            COUNT(*) AS ROW_COUNT,
            COUNT(DISTINCT COUNTRY_CODE_ISO2) AS UNIQUE_ISO2,
            COUNT_IF(COUNTRY_CODE_ISO2 IS NULL
                     OR COUNTRY_CODE_ISO3 IS NULL
                     OR COUNTRY_NAME IS NULL
                     OR POPULATION IS NULL
                     OR POPULATION_YEAR IS NULL) AS REQUIRED_NULLS,
            COUNT_IF(POPULATION <= 0) AS NON_POSITIVE_POPULATION,
            COUNT_IF(POPULATION_YEAR <> %s) AS INVALID_YEAR
        FROM {TARGET_DATABASE}.{TARGET_SCHEMA}.{table_name}
        """,
        (POPULATION_YEAR,),
    )
    row_count, unique_iso2, required_nulls, non_positive, invalid_year = (
        cursor.fetchone()
    )
    if (
        int(row_count) != expected_rows
        or int(unique_iso2) != expected_rows
        or int(required_nulls) != 0
        or int(non_positive) != 0
        or int(invalid_year) != 0
    ):
        raise PopulationValidationError(
            "Snowflake staging-table validation did not pass."
        )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.{secrets.token_hex(6)}.tmp"
    try:
        staging.write_text(content, encoding="utf-8")
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def _dataframe_csv(dataframe: pd.DataFrame) -> str:
    return dataframe.to_csv(index=False, lineterminator="\n")


def refresh_population(
    *,
    connection: snowflake.connector.SnowflakeConnection | None = None,
    csv_path: Path = DEFAULT_CSV_PATH,
    source_manifest_path: Path | None = None,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    source_mode: str = "api",
) -> dict[str, Any]:
    active_source_manifest_path = source_manifest_path or csv_path.with_suffix(
        ".manifest.json"
    )
    if source_mode == "api":
        dataframe = build_population_dataframe()
    elif source_mode == "snapshot":
        dataframe = load_population_snapshot(csv_path, active_source_manifest_path)
    else:
        raise PopulationValidationError(
            "Population source mode must be either 'api' or 'snapshot'."
        )
    validate_population_dataframe(dataframe)
    csv_content = _dataframe_csv(dataframe)
    if source_mode == "api":
        _atomic_write(csv_path, csv_content)
        _atomic_write(
            active_source_manifest_path,
            json.dumps(
                _source_snapshot_manifest(dataframe, csv_content),
                indent=2,
                sort_keys=True,
            ),
        )
    owns_connection = connection is None
    active_connection = connection or connect_to_snowflake()
    staging_table = _staging_table_name()
    cursor = active_connection.cursor()
    loaded_rows = 0
    try:
        cursor.execute(f"""
            CREATE TABLE {TARGET_DATABASE}.{TARGET_SCHEMA}.{staging_table}
            (
                COUNTRY_CODE_ISO2 VARCHAR,
                COUNTRY_CODE_ISO3 VARCHAR,
                COUNTRY_NAME VARCHAR,
                POPULATION NUMBER(38, 0),
                POPULATION_YEAR NUMBER(4, 0)
            )
            """)
        success, chunks, loaded_rows, _ = write_pandas(
            active_connection,
            dataframe,
            table_name=staging_table,
            database=TARGET_DATABASE,
            schema=TARGET_SCHEMA,
            quote_identifiers=False,
        )
        if not success or int(loaded_rows) != len(dataframe):
            raise PopulationValidationError("Population staging load was incomplete.")
        _validate_loaded_table(cursor, staging_table, len(dataframe))
        if _table_exists(cursor, TARGET_TABLE):
            cursor.execute(
                f"ALTER TABLE {TARGET_DATABASE}.{TARGET_SCHEMA}.{staging_table} "
                f"SWAP WITH {TARGET_DATABASE}.{TARGET_SCHEMA}.{TARGET_TABLE}"
            )
            cursor.execute(
                f"DROP TABLE {TARGET_DATABASE}.{TARGET_SCHEMA}.{staging_table}"
            )
        else:
            cursor.execute(
                f"ALTER TABLE {TARGET_DATABASE}.{TARGET_SCHEMA}.{staging_table} "
                f"RENAME TO {TARGET_DATABASE}.{TARGET_SCHEMA}.{TARGET_TABLE}"
            )
        manifest = {
            "source": "world_bank",
            "source_mode": source_mode,
            "population_year": POPULATION_YEAR,
            "downloaded_rows": len(dataframe),
            "loaded_rows": int(loaded_rows),
            "unique_iso2_codes": int(dataframe["COUNTRY_CODE_ISO2"].nunique()),
            "generated_at": datetime.now(UTC).isoformat(),
            "data_sha256": hashlib.sha256(csv_content.encode("utf-8")).hexdigest(),
        }
        _atomic_write(
            manifest_path,
            json.dumps(manifest, indent=2, sort_keys=True),
        )
        logger.info(
            "population_refresh_completed",
            extra={
                "loaded_rows": loaded_rows,
                "chunk_count": chunks,
                "population_year": POPULATION_YEAR,
            },
        )
        return manifest
    except Exception:
        try:
            cursor.execute(
                f"DROP TABLE IF EXISTS {TARGET_DATABASE}.{TARGET_SCHEMA}.{staging_table}"
            )
        except Exception as cleanup_exc:
            logger.exception(
                "population_staging_cleanup_failed",
                exc_info=sanitized_exception_info(cleanup_exc),
            )
        raise
    finally:
        cursor.close()
        if owns_connection:
            active_connection.close()


def main() -> None:
    load_dotenv()
    configure_logging("population-loader", os.getenv("LOG_LEVEL", "INFO"))
    for external_logger in ("snowflake.connector", "urllib3"):
        logging.getLogger(external_logger).setLevel(logging.CRITICAL)
    try:
        refresh_population()
    except Exception as exc:
        logger.exception(
            "population_refresh_failed",
            extra={"error_type": type(exc).__name__},
            exc_info=sanitized_exception_info(exc),
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
