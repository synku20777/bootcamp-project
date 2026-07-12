from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import snowflake.connector
from dotenv import load_dotenv


def connect_to_snowflake() -> snowflake.connector.SnowflakeConnection:
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        role=os.getenv("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database="COVID_ANALYTICS",
        schema="MARTS",
    )


def run_query(
    connection: snowflake.connector.SnowflakeConnection,
    sql: str,
) -> pd.DataFrame:
    cursor = connection.cursor()

    try:
        cursor.execute(sql)
        return cursor.fetch_pandas_all()
    finally:
        cursor.close()


def main() -> None:
    load_dotenv()

    output_directory = Path("outputs/eda")
    output_directory.mkdir(parents=True, exist_ok=True)

    queries = {
        "dataset_coverage": """
            SELECT
                COUNT(*) AS TOTAL_ROWS,
                COUNT(DISTINCT COUNTRY) AS COUNTRIES,
                MIN(REPORT_DATE) AS FIRST_DATE,
                MAX(REPORT_DATE) AS LAST_DATE,
                COUNT_IF(POPULATION IS NULL)
                    AS ROWS_WITHOUT_POPULATION
            FROM COVID_ANALYTICS.MARTS.COVID_ENRICHED
        """,

        "missing_population": """
            SELECT DISTINCT
                COUNTRY,
                COUNTRY_CODE
            FROM COVID_ANALYTICS.MARTS.COVID_ENRICHED
            WHERE POPULATION IS NULL
            ORDER BY COUNTRY
        """,

        "data_corrections": """
            SELECT
                COUNTRY,
                COUNT_IF(HAS_NEGATIVE_CASE_CORRECTION)
                    AS NEGATIVE_CASE_CORRECTIONS,
                COUNT_IF(HAS_NEGATIVE_DEATH_CORRECTION)
                    AS NEGATIVE_DEATH_CORRECTIONS
            FROM COVID_ANALYTICS.MARTS.COVID_ENRICHED
            GROUP BY COUNTRY
            HAVING NEGATIVE_CASE_CORRECTIONS > 0
                OR NEGATIVE_DEATH_CORRECTIONS > 0
            ORDER BY NEGATIVE_CASE_CORRECTIONS DESC
        """,

        "latest_country_metrics": """
            SELECT
                COUNTRY,
                REPORT_DATE,
                POPULATION,
                CASES_CUMULATIVE,
                DEATHS_CUMULATIVE,
                CASES_PER_100K,
                DEATHS_PER_100K,
                MORTALITY_RATE_PERCENT
            FROM COVID_ANALYTICS.MARTS.COVID_ENRICHED
            WHERE POPULATION IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY COUNTRY
                ORDER BY REPORT_DATE DESC
            ) = 1
            ORDER BY CASES_PER_100K DESC
        """,
    }

    connection = connect_to_snowflake()

    try:
        for report_name, query in queries.items():
            dataframe = run_query(connection, query)

            output_path = output_directory / f"{report_name}.csv"
            dataframe.to_csv(output_path, index=False)

            print(
                f"Created {output_path} "
                f"with {len(dataframe)} rows."
            )
    finally:
        connection.close()


if __name__ == "__main__":
    main()