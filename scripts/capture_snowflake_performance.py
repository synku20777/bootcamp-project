from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import snowflake.connector
from dotenv import load_dotenv

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from app.config import Settings  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.models.covid import Metric  # noqa: E402
from app.repositories.snowflake_repository import SnowflakeRepository  # noqa: E402

DEFAULT_OUTPUT = REPOSITORY_ROOT / "reports" / "snowflake" / "performance_evidence.json"
logger = logging.getLogger(__name__)


class ProfilingSnowflakeRepository(SnowflakeRepository):
    """Capture connector establishment time without changing runtime interfaces."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.last_connection_ms: float | None = None

    def _connect(self, operation: str) -> Any:
        started = time.perf_counter()
        connection = super()._connect(operation)
        self.last_connection_ms = round((time.perf_counter() - started) * 1000, 1)
        return connection


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture sanitized Snowflake API performance evidence."
    )
    parser.add_argument(
        "--phase",
        required=True,
        choices=("pre_materialization", "post_materialization"),
    )
    parser.add_argument("--env-file", type=Path, default=REPOSITORY_ROOT / ".env")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repetitions", type=int, default=5)
    return parser.parse_args()


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _connection(
    settings: Settings,
    *,
    role: str,
    application: str,
) -> snowflake.connector.SnowflakeConnection:
    return snowflake.connector.connect(
        account=settings.snowflake_account,
        user=settings.snowflake_user,
        password=settings.snowflake_password.get_secret_value(),
        role=role,
        warehouse=settings.snowflake_warehouse,
        database=settings.snowflake_database,
        schema=settings.snowflake_api_schema,
        application=application,
        login_timeout=settings.snowflake_login_timeout_seconds,
        network_timeout=settings.snowflake_network_timeout_seconds,
    )


def _representative_operations(
    repository: SnowflakeRepository,
) -> list[tuple[str, Any]]:
    start_date = date(2022, 1, 1)
    end_date = date(2022, 12, 31)
    return [
        ("dashboard_overview", repository.fetch_overview),
        (
            "country_timeseries",
            lambda: repository.fetch_timeseries(
                "LV", Metric.NEW_CASES, start_date, end_date
            ),
        ),
        (
            "country_dashboard",
            lambda: repository.fetch_country_dashboard(
                "LV", Metric.NEW_CASES, start_date, end_date
            ),
        ),
        (
            "dashboard_comparison",
            lambda: repository.fetch_dashboard_comparison(
                ["LV", "EE", "LT"], start_date, end_date
            ),
        ),
        (
            "case_increase_patterns",
            lambda: repository.fetch_case_increase_patterns(
                None,
                date(2019, 12, 31),
                date(2023, 3, 9),
                3,
                100,
            ),
        ),
        (
            "forecast_history",
            lambda: repository.fetch_forecast_history("LV", Metric.NEW_CASES, 90),
        ),
    ]


def _run_workload(
    repository: ProfilingSnowflakeRepository,
    *,
    warmups: int,
    repetitions: int,
) -> list[dict[str, Any]]:
    client_runs: list[dict[str, Any]] = []
    operations = _representative_operations(repository)
    for sequence in range(warmups + repetitions):
        is_warmup = sequence < warmups
        measured_repetition = None if is_warmup else sequence - warmups + 1
        for operation, execute in operations:
            started = time.perf_counter()
            rows = execute()
            client_runs.append(
                {
                    "operation": operation,
                    "warmup": is_warmup,
                    "repetition": measured_repetition,
                    "client_elapsed_ms": round(
                        (time.perf_counter() - started) * 1000,
                        1,
                    ),
                    "connection_elapsed_ms": repository.last_connection_ms,
                    "returned_rows": len(rows),
                }
            )
    return client_runs


def _recursive_sum(value: Any, keys: set[str]) -> int:
    if isinstance(value, dict):
        return sum(
            (
                int(child or 0)
                if key in keys and isinstance(child, (int, float))
                else _recursive_sum(child, keys)
            )
            for key, child in value.items()
        )
    if isinstance(value, list):
        return sum(_recursive_sum(child, keys) for child in value)
    return 0


def _operator_summary(
    cursor: snowflake.connector.cursor.SnowflakeCursor,
    query_id: str,
) -> dict[str, Any]:
    cursor.execute(
        "SELECT OPERATOR_TYPE, OPERATOR_STATISTICS, EXECUTION_TIME_BREAKDOWN "
        f"FROM TABLE(GET_QUERY_OPERATOR_STATS('{query_id}'))"
    )
    scans: list[dict[str, Any]] = []
    operators: list[dict[str, Any]] = []
    local_spill_bytes = 0
    remote_spill_bytes = 0
    for operator_type, statistics_raw, execution_raw in cursor.fetchall():
        operator_statistics = (
            json.loads(statistics_raw)
            if isinstance(statistics_raw, str)
            else statistics_raw or {}
        )
        execution = (
            json.loads(execution_raw)
            if isinstance(execution_raw, str)
            else execution_raw or {}
        )
        overall_percentage = float(execution.get("overall_percentage") or 0)
        operators.append(
            {
                "operator_type": operator_type,
                "overall_percentage": overall_percentage,
            }
        )
        local_spill_bytes += _recursive_sum(
            operator_statistics,
            {"bytes_spilled_to_local_storage", "local_storage_bytes"},
        )
        remote_spill_bytes += _recursive_sum(
            operator_statistics,
            {"bytes_spilled_to_remote_storage", "remote_storage_bytes"},
        )
        if "scan" in operator_type.lower():
            io = operator_statistics.get("io", {})
            pruning = operator_statistics.get("pruning", {})
            scans.append(
                {
                    "operator_type": operator_type,
                    "bytes_scanned": int(io.get("bytes_scanned") or 0),
                    "percentage_scanned_from_cache": io.get(
                        "percentage_scanned_from_cache"
                    ),
                    "partitions_scanned": int(pruning.get("partitions_scanned") or 0),
                    "partitions_total": int(pruning.get("partitions_total") or 0),
                    "output_rows": int(operator_statistics.get("output_rows") or 0),
                }
            )
    return {
        "scan_operators": scans,
        "local_spill_bytes": local_spill_bytes,
        "remote_spill_bytes": remote_spill_bytes,
        "top_operators": sorted(
            operators,
            key=lambda operator: operator["overall_percentage"],
            reverse=True,
        )[:5],
    }


def _query_history(
    cursor: snowflake.connector.cursor.SnowflakeCursor,
    query_tag_prefix: str,
) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT
            QUERY_ID,
            QUERY_TAG,
            START_TIME,
            TOTAL_ELAPSED_TIME,
            COMPILATION_TIME,
            EXECUTION_TIME,
            QUEUED_PROVISIONING_TIME,
            QUEUED_OVERLOAD_TIME,
            BYTES_SCANNED,
            ROWS_PRODUCED,
            BYTES_WRITTEN_TO_RESULT,
            QUERY_ACCELERATION_BYTES_SCANNED,
            QUERY_ACCELERATION_PARTITIONS_SCANNED,
            CREDITS_USED_CLOUD_SERVICES,
            QUERY_PARAMETERIZED_HASH
        FROM TABLE(
            COVID_ANALYTICS.INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER(
                USER_NAME => CURRENT_USER(),
                RESULT_LIMIT => 1000
            )
        )
        WHERE QUERY_TAG LIKE %s
          AND NOT IS_CLIENT_GENERATED_STATEMENT
        ORDER BY START_TIME
        """,
        (f"{query_tag_prefix}:%",),
    )
    columns = [column[0].lower() for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _object_evidence(
    cursor: snowflake.connector.cursor.SnowflakeCursor,
) -> dict[str, Any]:
    object_names = (
        "COVID_ENRICHED_EXTENDED",
        "COVID_ENRICHED_EXTENDED_DATA",
        "CASE_INCREASE_PATTERNS_EXTENDED",
        "CASE_INCREASE_PATTERNS_EXTENDED_DATA",
        "COUNTRY_LATEST_METRICS_EXTENDED",
    )
    placeholders = ", ".join("%s" for _ in object_names)
    cursor.execute(
        f"""
        SELECT TABLE_NAME, TABLE_TYPE, ROW_COUNT, BYTES
        FROM COVID_ANALYTICS.INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = 'MARTS'
          AND TABLE_NAME IN ({placeholders})
        ORDER BY TABLE_NAME
        """,
        object_names,
    )
    inventory = [
        {
            "object_name": row[0],
            "object_type": row[1],
            "metadata_row_count": row[2],
            "bytes": row[3],
        }
        for row in cursor.fetchall()
    ]
    existing = {item["object_name"] for item in inventory}
    results: dict[str, Any] = {"inventory": inventory}
    for object_name in object_names:
        if object_name not in existing:
            continue
        cursor.execute(
            "SELECT COUNT(*) AS ROW_COUNT, HASH_AGG(*) AS UNORDERED_HASH "
            f"FROM COVID_ANALYTICS.MARTS.{object_name}"
        )
        row_count, unordered_hash = cursor.fetchone()
        results[object_name.lower()] = {
            "row_count": row_count,
            "unordered_hash": unordered_hash,
        }
    cursor.execute("""
        SELECT
            COUNT(*) AS ROW_COUNT,
            COUNT(DISTINCT COUNTRY_ISO3) AS ISO3_COUNTRIES,
            MIN(REPORT_DATE) AS MIN_REPORT_DATE,
            MAX(REPORT_DATE) AS MAX_REPORT_DATE,
            COUNT_IF(COVID_RATE_POPULATION_2020 IS NULL)
                AS NULL_DENOMINATOR_ROWS,
            COUNT(*) - COUNT(
                DISTINCT LOCATION_KEY || '|' || TO_VARCHAR(REPORT_DATE)
            ) AS DUPLICATE_LOCATION_DATE_ROWS
        FROM COVID_ANALYTICS.MARTS.COVID_ENRICHED_EXTENDED
        """)
    columns = [column[0].lower() for column in cursor.description]
    results["extended_contract"] = dict(zip(columns, cursor.fetchone(), strict=True))
    cursor.execute("""
        SELECT
            COUNT(*) AS PATTERN_COUNT,
            COUNT(DISTINCT LOCATION_KEY) AS LOCATION_COUNT,
            COUNT_IF(DAYS_IN_PATTERN != CONSECUTIVE_INCREASES + 1)
                AS INVALID_DURATION_ROWS,
            COUNT_IF(CONSECUTIVE_INCREASES < 3) AS INVALID_INCREASE_ROWS
        FROM COVID_ANALYTICS.MARTS.CASE_INCREASE_PATTERNS_EXTENDED
        """)
    columns = [column[0].lower() for column in cursor.description]
    results["pattern_contract"] = dict(zip(columns, cursor.fetchone(), strict=True))
    return results


def _warehouse_evidence(
    cursor: snowflake.connector.cursor.SnowflakeCursor,
) -> dict[str, Any]:
    cursor.execute("SHOW WAREHOUSES LIKE 'COVID_WH'")
    warehouse_columns = [column[0] for column in cursor.description]
    warehouse_row = cursor.fetchone()
    warehouse = dict(zip(warehouse_columns, warehouse_row, strict=True))
    cursor.execute("SHOW RESOURCE MONITORS LIKE 'COVID_PROJECT_MONITOR'")
    monitor_columns = [column[0] for column in cursor.description]
    monitor_row = cursor.fetchone()
    monitor = dict(zip(monitor_columns, monitor_row, strict=True))
    cursor.execute("""
        SELECT
            COALESCE(SUM(CREDITS_USED), 0) AS QAS_CREDITS
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ACCELERATION_HISTORY
        WHERE START_TIME >= DATEADD('DAY', -7, CURRENT_TIMESTAMP())
          AND WAREHOUSE_NAME = CURRENT_WAREHOUSE()
        """)
    qas_credits = cursor.fetchone()[0]
    return {
        "warehouse": {
            key: warehouse.get(key)
            for key in (
                "name",
                "state",
                "size",
                "auto_suspend",
                "auto_resume",
                "enable_query_acceleration",
                "query_acceleration_max_scale_factor",
                "resource_monitor",
                "resource_constraint",
                "generation",
            )
        },
        "resource_monitor": {
            key: monitor.get(key)
            for key in (
                "name",
                "credit_quota",
                "used_credits",
                "remaining_credits",
                "notify_at",
                "suspend_at",
                "suspend_immediately_at",
            )
        },
        "query_acceleration_credits_last_7_days": qas_credits,
    }


def _attach_client_runs(
    query_rows: list[dict[str, Any]],
    client_runs: list[dict[str, Any]],
) -> None:
    client_by_operation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in client_runs:
        client_by_operation[run["operation"]].append(run)
    history_by_operation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in query_rows:
        operation = str(row["query_tag"]).rsplit(":", 1)[-1]
        row["operation"] = operation
        history_by_operation[operation].append(row)
    for operation, history_rows in history_by_operation.items():
        for history_row, client_run in zip(
            history_rows,
            client_by_operation[operation],
            strict=True,
        ):
            history_row.update(client_run)


def _summary(query_rows: list[dict[str, Any]]) -> dict[str, Any]:
    measured: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in query_rows:
        if not row["warmup"]:
            measured[row["operation"]].append(row)
    summary: dict[str, Any] = {}
    for operation, rows in measured.items():
        summary[operation] = {
            "measured_repetitions": len(rows),
            "median_client_elapsed_ms": statistics.median(
                row["client_elapsed_ms"] for row in rows
            ),
            "median_connection_elapsed_ms": statistics.median(
                row["connection_elapsed_ms"] for row in rows
            ),
            "median_snowflake_elapsed_ms": statistics.median(
                row["total_elapsed_time"] for row in rows
            ),
            "median_compilation_ms": statistics.median(
                row["compilation_time"] for row in rows
            ),
            "median_execution_ms": statistics.median(
                row["execution_time"] for row in rows
            ),
            "median_bytes_scanned": statistics.median(
                row["bytes_scanned"] for row in rows
            ),
            "maximum_local_spill_bytes": max(
                row["operator_summary"]["local_spill_bytes"] for row in rows
            ),
            "maximum_remote_spill_bytes": max(
                row["operator_summary"]["remote_spill_bytes"] for row in rows
            ),
            "maximum_queued_provisioning_ms": max(
                row["queued_provisioning_time"] for row in rows
            ),
            "maximum_queued_overload_ms": max(
                row["queued_overload_time"] for row in rows
            ),
            "maximum_query_acceleration_bytes_scanned": max(
                row["query_acceleration_bytes_scanned"] for row in rows
            ),
        }
    return summary


def main() -> None:
    args = parse_args()
    if args.warmups < 0 or args.repetitions < 1:
        raise SystemExit(
            "warmups must be non-negative and repetitions must be positive"
        )
    if not args.env_file.is_file():
        raise SystemExit(f"Environment file not found: {args.env_file}")
    load_dotenv(args.env_file, override=False)

    run_id = f"{args.phase}-{uuid4().hex[:12]}"
    settings = Settings(
        _env_file=None,
        snowflake_query_tag_prefix=f"covid-profile:{run_id}",
        snowflake_use_cached_result=False,
        snowflake_statement_timeout_seconds=120,
    )
    configure_logging("snowflake-performance", settings.log_level)
    logging.getLogger("app.repositories.snowflake_repository").setLevel(logging.WARNING)
    logging.getLogger("snowflake.connector").setLevel(logging.WARNING)
    repository = ProfilingSnowflakeRepository(settings)
    generated_at = datetime.now(UTC)
    client_runs = _run_workload(
        repository,
        warmups=args.warmups,
        repetitions=args.repetitions,
    )

    monitoring_role = os.getenv("SNOWFLAKE_ROLE", "COVID_PROJECT_ADMIN")
    connection = _connection(
        settings,
        role=monitoring_role,
        application="COVID_OPTIMIZATION_EVIDENCE",
    )
    cursor = connection.cursor()
    try:
        query_rows = _query_history(cursor, settings.snowflake_query_tag_prefix)
        _attach_client_runs(query_rows, client_runs)
        for row in query_rows:
            row["operator_summary"] = _operator_summary(cursor, row["query_id"])
        phase_evidence = {
            "run_id": run_id,
            "generated_at": generated_at,
            "dataset": settings.covid_dataset,
            "result_cache_enabled": settings.snowflake_use_cached_result,
            "warmups": args.warmups,
            "measured_repetitions": args.repetitions,
            "warehouse_state": _warehouse_evidence(cursor),
            "objects": _object_evidence(cursor),
            "summary": _summary(query_rows),
            "queries": query_rows,
        }
    finally:
        cursor.close()
        connection.close()

    artifact: dict[str, Any]
    if args.output.is_file():
        artifact = json.loads(args.output.read_text(encoding="utf-8"))
    else:
        artifact = {
            "schema_version": 1,
            "title": "Snowflake API performance evidence",
            "phases": {},
        }
    artifact["methodology"] = {
        "workload": (
            "One warmup followed by measured repository operations under "
            "COVID_APP_ROLE with Snowflake result-cache reuse disabled."
        ),
        "scope": (
            "Historical local project workload; not a concurrency, cluster "
            "throughput, or production service-level benchmark."
        ),
        "timing": (
            "Client duration includes connection, connector, transfer, and local "
            "deserialization time. Connection establishment is measured separately. "
            "Snowflake elapsed, compilation, and execution values come from query history."
        ),
        "query_history_latency": (
            "Information Schema query history is used for immediate run metrics; "
            "the Account Usage QUERY_HISTORY view can lag by up to 45 minutes."
        ),
        "credit_attribution": (
            "Resource-monitor and warehouse credits are aggregate account/warehouse "
            "values and cannot be assigned solely to API traffic; query tags identify "
            "the measured statements."
        ),
    }
    artifact["phases"][args.phase] = phase_evidence
    artifact["updated_at"] = datetime.now(UTC)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "snowflake_performance_evidence_written",
        extra={"output_path": str(args.output)},
    )


if __name__ == "__main__":
    main()
