from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.load_population import connect_to_snowflake  # noqa: E402

QUERIES = {
    "active_snapshot": """
        SELECT SNAPSHOT_ID, ROW_COUNT, COUNTRY_COUNT, NULL_VALUE_COUNT
        FROM COVID_ANALYTICS.RAW.WORLD_BANK_INDICATOR_SNAPSHOTS
        WHERE IS_ACTIVE AND PUBLICATION_STATUS = 'ACTIVE'
    """,
    "object_counts": """
        SELECT
            (SELECT COUNT(*) FROM COVID_ANALYTICS.RAW.WORLD_BANK_COUNTRY_INDICATORS
             WHERE SNAPSHOT_ID = (SELECT SNAPSHOT_ID
                                  FROM COVID_ANALYTICS.RAW.WORLD_BANK_INDICATOR_SNAPSHOTS
                                  WHERE IS_ACTIVE)) AS ACTIVE_OBSERVATIONS,
            (SELECT COUNT(*) FROM COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR)
                AS DENOMINATOR_ROWS,
            (SELECT COUNT(*)
             FROM COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR_HISTORY)
                AS DENOMINATOR_HISTORY_ROWS,
            (SELECT COUNT(*) FROM COVID_ANALYTICS.MARTS.COUNTRY_BASELINE_2019)
                AS BASELINE_ROWS,
            (SELECT COUNT(*) FROM COVID_ANALYTICS.MARTS.COUNTRY_INDICATOR_ANNUAL)
                AS ANNUAL_ROWS,
            (SELECT COUNT(*) FROM COVID_ANALYTICS.MARTS.COUNTRY_CONTEXT_ANALYSIS)
                AS CONTEXT_ROWS
    """,
    "duplicates": """
        SELECT COUNT(*) AS DUPLICATE_KEYS
        FROM (
            SELECT SNAPSHOT_ID, CANONICAL_ISO3, INDICATOR_CODE, OBSERVATION_YEAR
            FROM COVID_ANALYTICS.RAW.WORLD_BANK_COUNTRY_INDICATORS
            WHERE SNAPSHOT_ID = (SELECT SNAPSHOT_ID
                                 FROM COVID_ANALYTICS.RAW.WORLD_BANK_INDICATOR_SNAPSHOTS
                                 WHERE IS_ACTIVE)
            GROUP BY ALL HAVING COUNT(*) > 1
        )
    """,
    "latvia_context": """
        SELECT
            ISO3,
            POPULATION_2020_CONTEXT,
            COVID_RATE_POPULATION_2020,
            POPULATION_DENSITY_2019,
            POPULATION_AGE_65_PLUS_PCT_2019,
            REAL_GDP_PER_CAPITA_2019,
            HEALTH_EXPENDITURE_PER_CAPITA_PPP_2019,
            REAL_GDP_PER_CAPITA_CHANGE_2020_VS_2019_PCT,
            REAL_GDP_PER_CAPITA_CHANGE_2021_VS_2019_PCT,
            REAL_GDP_PER_CAPITA_CHANGE_2021_VS_2020_PCT,
            SNAPSHOT_ID
        FROM COVID_ANALYTICS.MARTS.COUNTRY_CONTEXT_ANALYSIS
        WHERE ISO3 = 'LVA'
    """,
}


def _query(connection: Any, sql: str) -> list[dict[str, Any]]:
    cursor = connection.cursor()
    try:
        cursor.execute(sql)
        columns = [description[0].lower() for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        cursor.close()


def verify(connection: Any, expected_snapshot_id: str) -> dict[str, Any]:
    results = {name: _query(connection, query) for name, query in QUERIES.items()}
    active = results["active_snapshot"]
    counts = results["object_counts"][0]
    failures: list[str] = []
    if len(active) != 1:
        failures.append("Exactly one snapshot must be active.")
    elif active[0]["snapshot_id"] != expected_snapshot_id:
        failures.append("The committed manifest and active snapshot differ.")
    if active and int(counts["active_observations"]) != int(active[0]["row_count"]):
        failures.append("The active raw observation count differs from its registry.")
    if int(results["duplicates"][0]["duplicate_keys"]) != 0:
        failures.append("The active snapshot contains duplicate keys.")
    if int(counts["annual_rows"]) != int(counts["baseline_rows"]) * 3:
        failures.append("Annual mart grain is not three rows per baseline country.")
    if int(counts["context_rows"]) != int(counts["baseline_rows"]):
        failures.append("Context view and baseline country universes differ.")
    if len(results["latvia_context"]) != 1:
        failures.append("Latvia context did not resolve exactly once.")
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "expected_snapshot_id": expected_snapshot_id,
        "results": results,
        "failures": failures,
        "accepted": not failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the deployed WDI context contract."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/external/world_bank_indicators_2019_2021.manifest.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/reconciliation/world_bank_snowflake_verification.json"),
    )
    args = parser.parse_args()
    expected = json.loads(args.manifest.read_text(encoding="utf-8"))["snapshot_id"]
    load_dotenv()
    connection = connect_to_snowflake()
    try:
        report = verify(connection, expected)
    finally:
        connection.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    sys.stdout.write(json.dumps(report, indent=2, default=str) + "\n")
    if not report["accepted"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
