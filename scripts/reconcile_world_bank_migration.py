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

RECONCILIATION_QUERY = """
WITH OLD_UNIVERSE AS (
    SELECT
        COALESCE(covid.COUNTRY_ISO3, legacy.COUNTRY_CODE_ISO3) AS ISO3,
        covid.REPORT_DATE,
        covid.CASES_CUMULATIVE,
        covid.DEATHS_CUMULATIVE,
        legacy.POPULATION AS POPULATION_DENOMINATOR,
        ROUND(covid.CASES_CUMULATIVE / NULLIF(legacy.POPULATION, 0) * 100000, 2)
            AS CASES_PER_100K,
        ROUND(covid.DEATHS_CUMULATIVE / NULLIF(legacy.POPULATION, 0) * 100000, 2)
            AS DEATHS_PER_100K,
        ROUND(covid.DEATHS_CUMULATIVE / NULLIF(covid.CASES_CUMULATIVE, 0) * 100, 4)
            AS MORTALITY_RATE_PERCENT
    FROM COVID_ANALYTICS.STAGING.COVID_COUNTRY_DAILY AS covid
    LEFT JOIN COVID_ANALYTICS.RAW.WORLD_BANK_POPULATION_2020 AS legacy
        ON covid.COUNTRY_ISO2 = legacy.COUNTRY_CODE_ISO2
        OR covid.COUNTRY_ISO3 = legacy.COUNTRY_CODE_ISO3
),
OLD_OUTPUT AS (
    SELECT * FROM OLD_UNIVERSE WHERE ISO3 IS NOT NULL
),
NEW_UNIVERSE AS (
    SELECT
        COUNTRY_ISO3 AS ISO3,
        REPORT_DATE,
        CASES_CUMULATIVE,
        DEATHS_CUMULATIVE,
        COVID_RATE_POPULATION_2020 AS POPULATION_DENOMINATOR,
        CASES_PER_100K,
        DEATHS_PER_100K,
        MORTALITY_RATE_PERCENT
    FROM COVID_ANALYTICS.MARTS.COVID_ENRICHED
),
NEW_OUTPUT AS (
    SELECT * FROM NEW_UNIVERSE WHERE ISO3 IS NOT NULL
),
COMPARED AS (
    SELECT
        old.ISO3 AS OLD_ISO3,
        new.ISO3 AS NEW_ISO3,
        old.REPORT_DATE AS OLD_REPORT_DATE,
        new.REPORT_DATE AS NEW_REPORT_DATE,
        old.CASES_CUMULATIVE AS OLD_CASES,
        new.CASES_CUMULATIVE AS NEW_CASES,
        old.DEATHS_CUMULATIVE AS OLD_DEATHS,
        new.DEATHS_CUMULATIVE AS NEW_DEATHS,
        old.POPULATION_DENOMINATOR AS OLD_POPULATION,
        new.POPULATION_DENOMINATOR AS NEW_POPULATION,
        ABS(old.CASES_PER_100K - new.CASES_PER_100K) AS CASE_RATE_ABS_DIFF,
        ABS(old.DEATHS_PER_100K - new.DEATHS_PER_100K) AS DEATH_RATE_ABS_DIFF,
        ABS(old.MORTALITY_RATE_PERCENT - new.MORTALITY_RATE_PERCENT)
            AS MORTALITY_ABS_DIFF,
        ABS(old.CASES_PER_100K - new.CASES_PER_100K)
            / NULLIF(ABS(old.CASES_PER_100K), 0) AS CASE_RATE_REL_DIFF,
        ABS(old.DEATHS_PER_100K - new.DEATHS_PER_100K)
            / NULLIF(ABS(old.DEATHS_PER_100K), 0) AS DEATH_RATE_REL_DIFF
    FROM OLD_OUTPUT AS old
    FULL OUTER JOIN NEW_OUTPUT AS new
        ON old.ISO3 = new.ISO3
       AND old.REPORT_DATE = new.REPORT_DATE
)
SELECT
    (SELECT COUNT(*) FROM OLD_UNIVERSE WHERE ISO3 IS NULL)
        AS EXCLUDED_OLD_ROWS_WITHOUT_ISO3,
    (SELECT COUNT(*) FROM NEW_UNIVERSE WHERE ISO3 IS NULL)
        AS EXCLUDED_NEW_ROWS_WITHOUT_ISO3,
    COUNT_IF(OLD_ISO3 IS NOT NULL AND NEW_ISO3 IS NOT NULL) AS COMPARED_ROW_COUNT,
    COUNT_IF(OLD_ISO3 IS NULL) AS MISSING_OLD_KEYS,
    COUNT_IF(NEW_ISO3 IS NULL) AS MISSING_NEW_KEYS,
    COUNT_IF(OLD_CASES = NEW_CASES AND OLD_DEATHS = NEW_DEATHS
             AND OLD_POPULATION IS NOT DISTINCT FROM NEW_POPULATION) AS EXACT_MATCHES,
    COUNT_IF(OLD_CASES IS DISTINCT FROM NEW_CASES) AS CASE_COUNT_DIFFERENCES,
    COUNT_IF(OLD_DEATHS IS DISTINCT FROM NEW_DEATHS) AS DEATH_COUNT_DIFFERENCES,
    COUNT_IF(OLD_POPULATION IS DISTINCT FROM NEW_POPULATION) AS POPULATION_DIFFERENCES,
    COUNT_IF(CASE_RATE_ABS_DIFF > 0.01) AS CASE_RATE_TOLERANCE_FAILURES,
    COUNT_IF(DEATH_RATE_ABS_DIFF > 0.01) AS DEATH_RATE_TOLERANCE_FAILURES,
    COUNT_IF(MORTALITY_ABS_DIFF > 0.0001) AS MORTALITY_TOLERANCE_FAILURES,
    MAX(CASE_RATE_ABS_DIFF) AS MAX_CASE_RATE_ABS_DIFF,
    MAX(DEATH_RATE_ABS_DIFF) AS MAX_DEATH_RATE_ABS_DIFF,
    MAX(MORTALITY_ABS_DIFF) AS MAX_MORTALITY_ABS_DIFF,
    MAX(CASE_RATE_REL_DIFF) AS MAX_CASE_RATE_REL_DIFF,
    MAX(DEATH_RATE_REL_DIFF) AS MAX_DEATH_RATE_REL_DIFF
FROM COMPARED
"""


def reconcile(connection: Any) -> dict[str, Any]:
    cursor = connection.cursor()
    try:
        cursor.execute(RECONCILIATION_QUERY)
        row = cursor.fetchone()
        columns = [description[0].lower() for description in cursor.description]
    finally:
        cursor.close()
    metrics = dict(zip(columns, row, strict=True))
    failure_fields = (
        "missing_old_keys",
        "missing_new_keys",
        "case_count_differences",
        "death_count_differences",
        "population_differences",
        "case_rate_tolerance_failures",
        "death_rate_tolerance_failures",
        "mortality_tolerance_failures",
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "tolerances": {
            "cases_and_deaths_per_100k": 0.01,
            "mortality_percentage_points": 0.0001,
        },
        "metrics": metrics,
        "accepted": all(int(metrics[field] or 0) == 0 for field in failure_fields),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile legacy and frozen-denominator COVID outputs."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/reconciliation/world_bank_denominator.json"),
    )
    args = parser.parse_args()
    load_dotenv()
    connection = connect_to_snowflake()
    try:
        report = reconcile(connection)
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
