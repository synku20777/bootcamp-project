from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import snowflake.connector
from snowflake.connector import SnowflakeConnection

from app.config import Settings
from app.exceptions import DataSourceUnavailableError
from app.models.covid import Metric

logger = logging.getLogger(__name__)


METRIC_COLUMNS: dict[Metric, str] = {
    Metric.NEW_CASES: "NEW_CASES_RAW",
    Metric.NEW_DEATHS: "NEW_DEATHS_RAW",
    Metric.CASES_CUMULATIVE: "CASES_CUMULATIVE",
    Metric.DEATHS_CUMULATIVE: "DEATHS_CUMULATIVE",
    Metric.NEW_CASES_PER_100K: "NEW_CASES_PER_100K",
    Metric.NEW_DEATHS_PER_100K: "NEW_DEATHS_PER_100K",
    Metric.CASES_PER_100K: "CASES_PER_100K",
    Metric.DEATHS_PER_100K: "DEATHS_PER_100K",
    Metric.MORTALITY_RATE_PERCENT: "MORTALITY_RATE_PERCENT",
}


class SnowflakeRepository:
    """Execute one bounded Snowflake statement per public repository method."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _connect(self) -> SnowflakeConnection:
        required = {
            "account": self.settings.snowflake_account,
            "user": self.settings.snowflake_user,
            "password": self.settings.snowflake_password.get_secret_value(),
        }
        if any(not value for value in required.values()):
            raise DataSourceUnavailableError("Snowflake")

        try:
            return snowflake.connector.connect(
                account=required["account"],
                user=required["user"],
                password=required["password"],
                role=self.settings.snowflake_api_role,
                warehouse=self.settings.snowflake_warehouse,
                database=self.settings.snowflake_database,
                schema=self.settings.snowflake_api_schema,
                application="COVID_ANALYTICS_API",
            )
        except snowflake.connector.Error as exc:
            logger.error(
                "snowflake_connection_failed",
                extra={"connector_error_type": type(exc).__name__},
            )
            raise DataSourceUnavailableError("Snowflake") from exc

    def _execute(
        self,
        operation: str,
        sql: str,
        parameters: Sequence[Any] = (),
    ) -> list[dict[str, Any]]:
        connection: SnowflakeConnection | None = None
        cursor = None

        try:
            connection = self._connect()
            cursor = connection.cursor()
            cursor.execute(sql, tuple(parameters))

            if not cursor.description:
                return []

            columns = [
                getattr(column, "name", column[0]).upper()
                for column in cursor.description
            ]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        except DataSourceUnavailableError:
            raise
        except snowflake.connector.Error as exc:
            logger.error(
                "snowflake_query_failed",
                extra={
                    "operation": operation,
                    "connector_error_type": type(exc).__name__,
                },
            )
            raise DataSourceUnavailableError("Snowflake") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def check_health(self) -> list[dict[str, Any]]:
        return self._execute(
            "health_check",
            """
            SELECT
                (
                    SELECT 1
                    FROM COVID_ANALYTICS.MARTS.COVID_ENRICHED
                    LIMIT 1
                ) AS COVID_ENRICHED_ACCESSIBLE,
                (
                    SELECT 1
                    FROM COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS
                    LIMIT 1
                ) AS COUNTRY_LATEST_METRICS_ACCESSIBLE
            """,
        )

    def fetch_overview(self) -> list[dict[str, Any]]:
        return self._execute(
            "dashboard_overview",
            """
            SELECT
                COUNTRY,
                COUNTRY_ISO2,
                COUNTRY_ISO3,
                LOCATION_KEY,
                REPORT_DATE,
                POPULATION,
                CASES_CUMULATIVE,
                DEATHS_CUMULATIVE,
                CASES_PER_100K,
                DEATHS_PER_100K,
                MORTALITY_RATE_PERCENT,
                POPULATION_JOIN_STATUS,
                MAX(REPORT_DATE) OVER () AS DATASET_REPORT_DATE,
                COUNT(*) OVER () AS COUNTRY_COUNT,
                SUM(CASES_CUMULATIVE) OVER () AS TOTAL_CASES,
                SUM(DEATHS_CUMULATIVE) OVER () AS TOTAL_DEATHS,
                ROUND(
                    SUM(DEATHS_CUMULATIVE) OVER ()
                    / NULLIF(SUM(CASES_CUMULATIVE) OVER (), 0) * 100,
                    4
                ) AS GLOBAL_MORTALITY_RATE_PERCENT
            FROM COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS
            ORDER BY COUNTRY
            """,
        )

    def fetch_countries(self) -> list[dict[str, Any]]:
        return self._execute(
            "countries",
            """
            SELECT
                COUNTRY,
                COUNTRY_ISO2,
                COUNTRY_ISO3,
                LOCATION_KEY
            FROM COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS
            ORDER BY COUNTRY
            """,
        )

    def fetch_summary(self, identifier: str) -> list[dict[str, Any]]:
        return self._execute(
            "country_summary",
            """
            SELECT
                COUNTRY,
                COUNTRY_ISO2,
                COUNTRY_ISO3,
                LOCATION_KEY,
                REPORT_DATE,
                POPULATION,
                CASES_CUMULATIVE,
                DEATHS_CUMULATIVE,
                CASES_PER_100K,
                DEATHS_PER_100K,
                MORTALITY_RATE_PERCENT
            FROM COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS
            WHERE UPPER(COUNTRY) = %s
               OR UPPER(COALESCE(COUNTRY_ISO2, '')) = %s
               OR UPPER(COALESCE(COUNTRY_ISO3, '')) = %s
            QUALIFY ROW_NUMBER() OVER (
                ORDER BY
                    IFF(UPPER(COALESCE(COUNTRY_ISO2, '')) = %s, 0, 1),
                    COUNTRY
            ) = 1
            """,
            (identifier, identifier, identifier, identifier),
        )

    def fetch_country_identity(self, identifier: str) -> list[dict[str, Any]]:
        return self._execute(
            "country_identity",
            """
            SELECT
                COUNTRY,
                COUNTRY_ISO2,
                COUNTRY_ISO3,
                LOCATION_KEY
            FROM COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS
            WHERE UPPER(COUNTRY) = %s
               OR UPPER(COALESCE(COUNTRY_ISO2, '')) = %s
               OR UPPER(COALESCE(COUNTRY_ISO3, '')) = %s
            QUALIFY ROW_NUMBER() OVER (
                ORDER BY
                    IFF(UPPER(COALESCE(COUNTRY_ISO2, '')) = %s, 0, 1),
                    COUNTRY
            ) = 1
            """,
            (identifier, identifier, identifier, identifier),
        )

    def fetch_annotation_target(
        self,
        identifier: str,
        report_date: Any,
    ) -> list[dict[str, Any]]:
        return self._execute(
            "annotation_target",
            """
            WITH RESOLVED AS (
                SELECT
                    COUNTRY,
                    COUNTRY_ISO2,
                    COUNTRY_ISO3,
                    LOCATION_KEY
                FROM COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS
                WHERE UPPER(COUNTRY) = %s
                   OR UPPER(COALESCE(COUNTRY_ISO2, '')) = %s
                   OR UPPER(COALESCE(COUNTRY_ISO3, '')) = %s
                QUALIFY ROW_NUMBER() OVER (
                    ORDER BY
                        IFF(UPPER(COALESCE(COUNTRY_ISO2, '')) = %s, 0, 1),
                        COUNTRY
                ) = 1
            )
            SELECT
                resolved.COUNTRY,
                resolved.COUNTRY_ISO2,
                resolved.COUNTRY_ISO3,
                resolved.LOCATION_KEY,
                data.REPORT_DATE AS VALIDATED_REPORT_DATE
            FROM RESOLVED AS resolved
            LEFT JOIN COVID_ANALYTICS.MARTS.COVID_ENRICHED AS data
                ON data.LOCATION_KEY = resolved.LOCATION_KEY
               AND data.REPORT_DATE = %s
            """,
            (identifier, identifier, identifier, identifier, report_date),
        )

    def fetch_timeseries(
        self,
        identifier: str,
        metric: Metric,
        start_date: Any,
        end_date: Any,
    ) -> list[dict[str, Any]]:
        metric_column = METRIC_COLUMNS[metric]
        return self._execute(
            "country_timeseries",
            f"""
            WITH RESOLVED AS (
                SELECT
                    COUNTRY,
                    COUNTRY_ISO2,
                    COUNTRY_ISO3,
                    LOCATION_KEY
                FROM COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS
                WHERE UPPER(COUNTRY) = %s
                   OR UPPER(COALESCE(COUNTRY_ISO2, '')) = %s
                   OR UPPER(COALESCE(COUNTRY_ISO3, '')) = %s
                QUALIFY ROW_NUMBER() OVER (
                    ORDER BY
                        IFF(UPPER(COALESCE(COUNTRY_ISO2, '')) = %s, 0, 1),
                        COUNTRY
                ) = 1
            )
            SELECT
                resolved.COUNTRY,
                resolved.COUNTRY_ISO2,
                resolved.COUNTRY_ISO3,
                resolved.LOCATION_KEY,
                data.REPORT_DATE,
                data.{metric_column} AS METRIC_VALUE
            FROM RESOLVED AS resolved
            LEFT JOIN COVID_ANALYTICS.MARTS.COVID_ENRICHED AS data
                ON data.LOCATION_KEY = resolved.LOCATION_KEY
               AND data.REPORT_DATE BETWEEN %s AND %s
            ORDER BY data.REPORT_DATE
            """,
            (
                identifier,
                identifier,
                identifier,
                identifier,
                start_date,
                end_date,
            ),
        )

    def fetch_comparison(
        self,
        identifiers: list[str],
        metric: Metric,
        start_date: Any,
        end_date: Any,
    ) -> list[dict[str, Any]]:
        metric_column = METRIC_COLUMNS[metric]
        value_rows = ", ".join("(%s, %s)" for _ in identifiers)
        parameters: list[Any] = []
        for request_order, identifier in enumerate(identifiers):
            parameters.extend((identifier, request_order))
        parameters.extend((start_date, end_date))

        return self._execute(
            "country_comparison",
            f"""
            WITH REQUESTED AS (
                SELECT
                    column1::VARCHAR AS IDENTIFIER,
                    column2::INTEGER AS REQUEST_ORDER
                FROM VALUES {value_rows}
            ),
            RESOLVED AS (
                SELECT
                    requested.IDENTIFIER,
                    requested.REQUEST_ORDER,
                    latest.COUNTRY,
                    latest.COUNTRY_ISO2,
                    latest.COUNTRY_ISO3,
                    latest.LOCATION_KEY
                FROM REQUESTED AS requested
                LEFT JOIN COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS AS latest
                    ON UPPER(latest.COUNTRY) = requested.IDENTIFIER
                    OR UPPER(COALESCE(latest.COUNTRY_ISO2, ''))
                        = requested.IDENTIFIER
                    OR UPPER(COALESCE(latest.COUNTRY_ISO3, ''))
                        = requested.IDENTIFIER
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY requested.REQUEST_ORDER
                    ORDER BY
                        IFF(
                            UPPER(COALESCE(latest.COUNTRY_ISO2, ''))
                                = requested.IDENTIFIER,
                            0,
                            1
                        ),
                        latest.COUNTRY
                ) = 1
            )
            SELECT
                resolved.IDENTIFIER,
                resolved.REQUEST_ORDER,
                resolved.COUNTRY,
                resolved.COUNTRY_ISO2,
                resolved.COUNTRY_ISO3,
                resolved.LOCATION_KEY,
                data.REPORT_DATE,
                data.{metric_column} AS METRIC_VALUE
            FROM RESOLVED AS resolved
            LEFT JOIN COVID_ANALYTICS.MARTS.COVID_ENRICHED AS data
                ON data.LOCATION_KEY = resolved.LOCATION_KEY
               AND data.REPORT_DATE BETWEEN %s AND %s
            ORDER BY resolved.REQUEST_ORDER, data.REPORT_DATE
            """,
            parameters,
        )

    def fetch_country_dashboard(
        self,
        identifier: str,
        metric: Metric,
        start_date: Any,
        end_date: Any,
    ) -> list[dict[str, Any]]:
        metric_column = METRIC_COLUMNS[metric]
        return self._execute(
            "country_dashboard",
            f"""
            WITH RESOLVED AS (
                SELECT
                    COUNTRY,
                    COUNTRY_ISO2,
                    COUNTRY_ISO3,
                    LOCATION_KEY,
                    REPORT_DATE AS LATEST_REPORT_DATE,
                    POPULATION,
                    CASES_CUMULATIVE,
                    DEATHS_CUMULATIVE,
                    CASES_PER_100K,
                    DEATHS_PER_100K,
                    MORTALITY_RATE_PERCENT AS LATEST_MORTALITY_RATE_PERCENT
                FROM COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS
                WHERE UPPER(COUNTRY) = %s
                   OR UPPER(COALESCE(COUNTRY_ISO2, '')) = %s
                   OR UPPER(COALESCE(COUNTRY_ISO3, '')) = %s
                QUALIFY ROW_NUMBER() OVER (
                    ORDER BY
                        IFF(UPPER(COALESCE(COUNTRY_ISO2, '')) = %s, 0, 1),
                        COUNTRY
                ) = 1
            )
            SELECT
                resolved.COUNTRY,
                resolved.COUNTRY_ISO2,
                resolved.COUNTRY_ISO3,
                resolved.LOCATION_KEY,
                resolved.LATEST_REPORT_DATE,
                resolved.POPULATION,
                resolved.CASES_CUMULATIVE,
                resolved.DEATHS_CUMULATIVE,
                resolved.CASES_PER_100K,
                resolved.DEATHS_PER_100K,
                resolved.LATEST_MORTALITY_RATE_PERCENT,
                data.REPORT_DATE,
                data.{metric_column} AS SELECTED_METRIC_VALUE,
                data.NEW_CASES_RAW AS NEW_CASES_VALUE,
                data.NEW_DEATHS_RAW AS NEW_DEATHS_VALUE,
                data.MORTALITY_RATE_PERCENT AS MORTALITY_VALUE
            FROM RESOLVED AS resolved
            LEFT JOIN COVID_ANALYTICS.MARTS.COVID_ENRICHED AS data
                ON data.LOCATION_KEY = resolved.LOCATION_KEY
               AND data.REPORT_DATE BETWEEN %s AND %s
            ORDER BY data.REPORT_DATE
            """,
            (
                identifier,
                identifier,
                identifier,
                identifier,
                start_date,
                end_date,
            ),
        )

    def fetch_dashboard_comparison(
        self,
        identifiers: list[str],
        start_date: Any,
        end_date: Any,
    ) -> list[dict[str, Any]]:
        value_rows = ", ".join("(%s, %s)" for _ in identifiers)
        parameters: list[Any] = []
        for request_order, identifier in enumerate(identifiers):
            parameters.extend((identifier, request_order))
        parameters.extend((start_date, end_date))

        return self._execute(
            "dashboard_comparison",
            f"""
            WITH REQUESTED AS (
                SELECT
                    column1::VARCHAR AS IDENTIFIER,
                    column2::INTEGER AS REQUEST_ORDER
                FROM VALUES {value_rows}
            ),
            RESOLVED AS (
                SELECT
                    requested.IDENTIFIER,
                    requested.REQUEST_ORDER,
                    latest.COUNTRY,
                    latest.COUNTRY_ISO2,
                    latest.COUNTRY_ISO3,
                    latest.LOCATION_KEY
                FROM REQUESTED AS requested
                LEFT JOIN COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS AS latest
                    ON UPPER(latest.COUNTRY) = requested.IDENTIFIER
                    OR UPPER(COALESCE(latest.COUNTRY_ISO2, ''))
                        = requested.IDENTIFIER
                    OR UPPER(COALESCE(latest.COUNTRY_ISO3, ''))
                        = requested.IDENTIFIER
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY requested.REQUEST_ORDER
                    ORDER BY
                        IFF(
                            UPPER(COALESCE(latest.COUNTRY_ISO2, ''))
                                = requested.IDENTIFIER,
                            0,
                            1
                        ),
                        latest.COUNTRY
                ) = 1
            )
            SELECT
                resolved.IDENTIFIER,
                resolved.REQUEST_ORDER,
                resolved.COUNTRY,
                resolved.COUNTRY_ISO2,
                resolved.COUNTRY_ISO3,
                resolved.LOCATION_KEY,
                data.REPORT_DATE,
                data.CASES_PER_100K,
                data.DEATHS_PER_100K,
                data.MORTALITY_RATE_PERCENT
            FROM RESOLVED AS resolved
            LEFT JOIN COVID_ANALYTICS.MARTS.COVID_ENRICHED AS data
                ON data.LOCATION_KEY = resolved.LOCATION_KEY
               AND data.REPORT_DATE BETWEEN %s AND %s
            ORDER BY resolved.REQUEST_ORDER, data.REPORT_DATE
            """,
            parameters,
        )
