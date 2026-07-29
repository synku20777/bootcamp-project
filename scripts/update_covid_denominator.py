from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from scripts.load_population import connect_to_snowflake

VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DENOMINATOR_COLUMNS = (
    "ISO2",
    "ISO3",
    "COUNTRY_NAME",
    "COVID_RATE_POPULATION_2020",
    "POPULATION_YEAR",
    "SOURCE_SNAPSHOT_ID",
    "DENOMINATOR_VERSION",
    "DENOMINATOR_POLICY",
    "IS_FROZEN",
    "APPROVED_AT",
)


class DenominatorUpdateError(RuntimeError):
    """A denominator update failed a review or publication invariant."""


def _version(value: str) -> str:
    if not VERSION_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "Use a short alphanumeric denominator version."
        )
    return value


def _query_one(
    cursor: Any, query: str, params: tuple[Any, ...] = ()
) -> tuple[Any, ...]:
    cursor.execute(query, params)
    row = cursor.fetchone()
    if row is None:
        raise DenominatorUpdateError("A required denominator query returned no row.")
    return tuple(row)


def _semantic_plan(connection: Any, candidate_version: str) -> dict[str, Any]:
    cursor = connection.cursor()
    try:
        active_rows = _query_one(
            cursor,
            """
            SELECT COUNT(*), MIN(SNAPSHOT_ID)
            FROM COVID_ANALYTICS.RAW.WORLD_BANK_INDICATOR_SNAPSHOTS
            WHERE IS_ACTIVE AND PUBLICATION_STATUS = 'ACTIVE'
            """,
        )
        if int(active_rows[0]) != 1:
            raise DenominatorUpdateError("Exactly one WDI snapshot must be active.")
        version_rows = _query_one(
            cursor,
            """
            SELECT COUNT(DISTINCT DENOMINATOR_VERSION),
                   MIN(DENOMINATOR_VERSION), COUNT(*)
            FROM COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR
            """,
        )
        if int(version_rows[0]) != 1 or int(version_rows[2]) < 1:
            raise DenominatorUpdateError(
                "The current denominator must contain exactly one non-empty version."
            )
        metrics = _query_one(
            cursor,
            """
            WITH CANDIDATE AS (
                SELECT ISO3, INDICATOR_VALUE::NUMBER(38, 0) AS POPULATION
                FROM COVID_ANALYTICS.STAGING.WORLD_BANK_COUNTRY_INDICATORS_CLEAN
                WHERE INDICATOR_CODE = 'SP.POP.TOTL'
                  AND OBSERVATION_YEAR = 2020
            )
            SELECT
                COUNT(*) AS CURRENT_COUNTRIES,
                COUNT(CANDIDATE.ISO3) AS MATCHED_COUNTRIES,
                COUNT_IF(CANDIDATE.ISO3 IS NULL) AS MISSING_CANDIDATES,
                COUNT_IF(CANDIDATE.POPULATION IS DISTINCT FROM
                         DENOMINATOR.COVID_RATE_POPULATION_2020) AS CHANGED_COUNTRIES,
                MAX(ABS(CANDIDATE.POPULATION
                        - DENOMINATOR.COVID_RATE_POPULATION_2020)) AS MAX_ABS_CHANGE,
                MAX(100 * ABS(CANDIDATE.POPULATION
                        / NULLIF(DENOMINATOR.COVID_RATE_POPULATION_2020, 0) - 1))
                    AS MAX_CHANGE_PCT
            FROM COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR AS DENOMINATOR
            LEFT JOIN CANDIDATE ON DENOMINATOR.ISO3 = CANDIDATE.ISO3
            """,
        )
        rate_metrics = _query_one(
            cursor,
            """
            WITH CANDIDATE AS (
                SELECT ISO3, INDICATOR_VALUE::NUMBER(38, 0) AS POPULATION
                FROM COVID_ANALYTICS.STAGING.WORLD_BANK_COUNTRY_INDICATORS_CLEAN
                WHERE INDICATOR_CODE = 'SP.POP.TOTL'
                  AND OBSERVATION_YEAR = 2020
            )
            SELECT
                COUNT(*) AS COVID_ROWS_COMPARED,
                MAX(ABS(COVID.CASES_CUMULATIVE
                    / NULLIF(DENOMINATOR.COVID_RATE_POPULATION_2020, 0) * 100000
                    - COVID.CASES_CUMULATIVE
                    / NULLIF(CANDIDATE.POPULATION, 0) * 100000))
                    AS MAX_CASES_PER_100K_CHANGE,
                MAX(ABS(COVID.DEATHS_CUMULATIVE
                    / NULLIF(DENOMINATOR.COVID_RATE_POPULATION_2020, 0) * 100000
                    - COVID.DEATHS_CUMULATIVE
                    / NULLIF(CANDIDATE.POPULATION, 0) * 100000))
                    AS MAX_DEATHS_PER_100K_CHANGE
            FROM COVID_ANALYTICS.STAGING.COVID_COUNTRY_DAILY AS COVID
            INNER JOIN COVID_ANALYTICS.MARTS.DIM_COUNTRY AS COUNTRY
                ON COVID.LOCATION_KEY = COUNTRY.LOCATION_KEY
            INNER JOIN COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR AS DENOMINATOR
                ON COUNTRY.ISO3 = DENOMINATOR.ISO3
            INNER JOIN CANDIDATE ON DENOMINATOR.ISO3 = CANDIDATE.ISO3
            """,
        )
        cursor.execute("""
            WITH CANDIDATE AS (
                SELECT ISO3, INDICATOR_VALUE::NUMBER(38, 0) AS POPULATION
                FROM COVID_ANALYTICS.STAGING.WORLD_BANK_COUNTRY_INDICATORS_CLEAN
                WHERE INDICATOR_CODE = 'SP.POP.TOTL'
                  AND OBSERVATION_YEAR = 2020
            )
            SELECT DENOMINATOR.ISO3, DENOMINATOR.COVID_RATE_POPULATION_2020,
                   CANDIDATE.POPULATION
            FROM COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR AS DENOMINATOR
            INNER JOIN CANDIDATE ON DENOMINATOR.ISO3 = CANDIDATE.ISO3
            WHERE DENOMINATOR.COVID_RATE_POPULATION_2020 IS DISTINCT FROM
                  CANDIDATE.POPULATION
            ORDER BY DENOMINATOR.ISO3
            """)
        changes = [
            {
                "iso3": row[0],
                "current_population": row[1],
                "candidate_population": row[2],
            }
            for row in cursor.fetchall()
        ]
    finally:
        cursor.close()
    return {
        "active_snapshot_id": str(active_rows[1]),
        "current_denominator_version": str(version_rows[1]),
        "candidate_denominator_version": candidate_version,
        "current_country_count": int(metrics[0]),
        "matched_country_count": int(metrics[1]),
        "missing_candidate_count": int(metrics[2]),
        "changed_country_count": int(metrics[3]),
        "max_population_absolute_change": metrics[4],
        "max_population_change_pct": metrics[5],
        "covid_rows_compared": int(rate_metrics[0]),
        "max_cases_per_100k_change": rate_metrics[1],
        "max_deaths_per_100k_change": rate_metrics[2],
        "country_changes": changes,
    }


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def plan_update(connection: Any, candidate_version: str) -> dict[str, Any]:
    semantic = _semantic_plan(connection, candidate_version)
    accepted = (
        semantic["missing_candidate_count"] == 0
        and semantic["matched_country_count"] == semantic["current_country_count"]
        and semantic["changed_country_count"] > 0
        and semantic["candidate_denominator_version"]
        != semantic["current_denominator_version"]
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "accepted": accepted,
        "plan_fingerprint": hashlib.sha256(
            _canonical_json(semantic).encode("utf-8")
        ).hexdigest(),
        "semantic_plan": semantic,
    }


def apply_update(connection: Any, evidence: dict[str, Any], approve: bool) -> None:
    if not approve:
        raise DenominatorUpdateError("Applying a denominator requires --approve.")
    if not evidence.get("accepted"):
        raise DenominatorUpdateError("The reviewed denominator evidence was rejected.")
    expected = evidence["semantic_plan"]
    current = _semantic_plan(connection, expected["candidate_denominator_version"])
    fingerprint = hashlib.sha256(_canonical_json(current).encode("utf-8")).hexdigest()
    if fingerprint != evidence.get("plan_fingerprint"):
        raise DenominatorUpdateError(
            "Snowflake state changed after evidence generation; create a new plan."
        )
    cursor = connection.cursor()
    try:
        connection.autocommit(False)
        cursor.execute("""
            INSERT INTO COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR_HISTORY
            SELECT DENOMINATOR.*, CURRENT_TIMESTAMP()
            FROM COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR AS DENOMINATOR
            WHERE NOT EXISTS (
                SELECT 1
                FROM COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR_HISTORY AS HISTORY
                WHERE HISTORY.DENOMINATOR_VERSION = DENOMINATOR.DENOMINATOR_VERSION
                  AND HISTORY.ISO3 = DENOMINATOR.ISO3
            )
            """)
        cursor.execute(
            """
            UPDATE COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR AS TARGET
            SET COVID_RATE_POPULATION_2020 = SOURCE.POPULATION,
                SOURCE_SNAPSHOT_ID = %s,
                DENOMINATOR_VERSION = %s,
                DENOMINATOR_POLICY = 'reviewed_wdi_2020_population',
                IS_FROZEN = TRUE,
                APPROVED_AT = CURRENT_TIMESTAMP()
            FROM (
                SELECT ISO3, INDICATOR_VALUE::NUMBER(38, 0) AS POPULATION
                FROM COVID_ANALYTICS.STAGING.WORLD_BANK_COUNTRY_INDICATORS_CLEAN
                WHERE INDICATOR_CODE = 'SP.POP.TOTL'
                  AND OBSERVATION_YEAR = 2020
            ) AS SOURCE
            WHERE TARGET.ISO3 = SOURCE.ISO3
            """,
            (
                current["active_snapshot_id"],
                current["candidate_denominator_version"],
            ),
        )
        verified = _query_one(
            cursor,
            """
            SELECT COUNT(*), COUNT_IF(NOT IS_FROZEN)
            FROM COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR
            WHERE DENOMINATOR_VERSION = %s
            """,
            (current["candidate_denominator_version"],),
        )
        if int(verified[0]) != current["current_country_count"] or int(verified[1]):
            raise DenominatorUpdateError("The denominator update failed verification.")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.autocommit(True)
        cursor.close()


def rollback_version(connection: Any, version: str, approve: bool) -> None:
    if not approve:
        raise DenominatorUpdateError("Rolling back a denominator requires --approve.")
    cursor = connection.cursor()
    try:
        count = _query_one(
            cursor,
            """
            SELECT COUNT(*)
            FROM COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR_HISTORY
            WHERE DENOMINATOR_VERSION = %s
            """,
            (version,),
        )
        if int(count[0]) < 1:
            raise DenominatorUpdateError("The requested denominator version is absent.")
        connection.autocommit(False)
        cursor.execute("""
            INSERT INTO COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR_HISTORY
            SELECT DENOMINATOR.*, CURRENT_TIMESTAMP()
            FROM COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR AS DENOMINATOR
            WHERE NOT EXISTS (
                SELECT 1
                FROM COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR_HISTORY AS HISTORY
                WHERE HISTORY.DENOMINATOR_VERSION = DENOMINATOR.DENOMINATOR_VERSION
                  AND HISTORY.ISO3 = DENOMINATOR.ISO3
            )
            """)
        cursor.execute("DELETE FROM COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR")
        cursor.execute(
            f"""
            INSERT INTO COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR
                ({', '.join(DENOMINATOR_COLUMNS)})
            SELECT {', '.join(DENOMINATOR_COLUMNS)}
            FROM COVID_ANALYTICS.MARTS.COUNTRY_COVID_DENOMINATOR_HISTORY
            WHERE DENOMINATOR_VERSION = %s
            """,
            (version,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.autocommit(True)
        cursor.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan, approve, or roll back a frozen COVID denominator."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--version", required=True, type=_version)
    plan.add_argument("--output", type=Path, required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("--evidence", type=Path, required=True)
    apply.add_argument("--approve", action="store_true")
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--version", required=True, type=_version)
    rollback.add_argument("--approve", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv()
    connection = connect_to_snowflake()
    try:
        if args.command == "plan":
            evidence = plan_update(connection, args.version)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(evidence, indent=2, sort_keys=True, default=str) + "\n",
                encoding="utf-8",
            )
            sys.stdout.write(json.dumps(evidence, indent=2, default=str) + "\n")
        elif args.command == "apply":
            evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
            apply_update(connection, evidence, args.approve)
        else:
            rollback_version(connection, args.version, args.approve)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
