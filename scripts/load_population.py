from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import snowflake.connector
from dotenv import load_dotenv
from snowflake.connector.pandas_tools import write_pandas


WORLD_BANK_BASE_URL = "https://api.worldbank.org/v2"
POPULATION_YEAR = 2020


def request_world_bank_data(
    endpoint: str,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Request a World Bank API endpoint and return its data records."""
    response = requests.get(
        f"{WORLD_BANK_BASE_URL}/{endpoint}",
        params=params,
        timeout=60,
    )
    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError(
            f"Unexpected World Bank response for endpoint: {endpoint}"
        )

    return payload[1] or []


def build_population_dataframe() -> pd.DataFrame:
    """Download country metadata and 2020 population values."""
    country_metadata = request_world_bank_data(
        "country",
        {
            "format": "json",
            "per_page": 400,
        },
    )

    valid_countries = {
        country["iso2Code"]: country
        for country in country_metadata
        if country.get("iso2Code")
        and country.get("region", {}).get("id") != "NA"
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

        if iso2_code not in valid_countries:
            continue

        if population is None:
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
        raise RuntimeError("No population records were downloaded.")

    dataframe = (
        dataframe.drop_duplicates(subset=["COUNTRY_CODE_ISO2"])
        .sort_values("COUNTRY_NAME")
        .reset_index(drop=True)
    )

    return dataframe


def connect_to_snowflake() -> snowflake.connector.SnowflakeConnection:
    """Create a Snowflake connection using environment variables."""
    required_variables = [
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_SCHEMA",
    ]

    missing = [
        variable
        for variable in required_variables
        if not os.getenv(variable)
    ]

    if missing:
        raise RuntimeError(
            f"Missing environment variables: {', '.join(missing)}"
        )

    print(
        "Connecting to Snowflake account:",
        os.environ["SNOWFLAKE_ACCOUNT"],
    )

    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        role=os.getenv("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        schema=os.environ["SNOWFLAKE_SCHEMA"],
    )


def main() -> None:
    load_dotenv()

    dataframe = build_population_dataframe()

    output_directory = Path("data/external")
    output_directory.mkdir(parents=True, exist_ok=True)

    csv_path = output_directory / "world_bank_population_2020.csv"
    dataframe.to_csv(csv_path, index=False)

    print(f"Created local file: {csv_path}")
    print(f"Downloaded rows: {len(dataframe)}")

    connection = connect_to_snowflake()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE OR REPLACE TABLE
                COVID_ANALYTICS.RAW.WORLD_BANK_POPULATION_2020
            (
                COUNTRY_CODE_ISO2 VARCHAR,
                COUNTRY_CODE_ISO3 VARCHAR,
                COUNTRY_NAME VARCHAR,
                POPULATION NUMBER(38, 0),
                POPULATION_YEAR NUMBER(4, 0)
            )
            """
        )

        success, chunks, rows, _ = write_pandas(
            connection,
            dataframe,
            table_name="WORLD_BANK_POPULATION_2020",
            database="COVID_ANALYTICS",
            schema="RAW",
            quote_identifiers=False,
        )

        if not success:
            raise RuntimeError("write_pandas reported an unsuccessful load.")

        print(
            f"Loaded {rows} rows into Snowflake "
            f"using {chunks} upload chunk(s)."
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()