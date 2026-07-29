from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import snowflake.connector
from snowflake.connector import SnowflakeConnection

from app.config import Settings
from app.exceptions import DataSourceUnavailableError
from app.logging_config import sanitized_exception_info
from app.models.covid import Metric

logger = logging.getLogger(__name__)

SNOWFLAKE_SOURCE = "Snowflake"


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

    def _configuration(self) -> dict[str, str]:
        values = {
            "SNOWFLAKE_ACCOUNT": self.settings.snowflake_account.strip(),
            "SNOWFLAKE_USER": self.settings.snowflake_user.strip(),
            "SNOWFLAKE_PASSWORD": self.settings.snowflake_password.get_secret_value(),
            "SNOWFLAKE_API_ROLE": self.settings.snowflake_api_role.strip(),
            "SNOWFLAKE_WAREHOUSE": self.settings.snowflake_warehouse.strip(),
            "SNOWFLAKE_DATABASE": self.settings.snowflake_database.strip(),
            "SNOWFLAKE_API_SCHEMA": self.settings.snowflake_api_schema.strip(),
        }
        missing = sorted(name for name, value in values.items() if not value)
        if missing:
            logger.error(
                "snowflake_configuration_invalid",
                extra={"missing_settings": missing},
            )
            raise DataSourceUnavailableError(
                SNOWFLAKE_SOURCE,
                code="snowflake_configuration_invalid",
                message="Snowflake configuration is incomplete.",
            )

        account = values["SNOWFLAKE_ACCOUNT"].lower()
        if (
            account.startswith(("http://", "https://"))
            or "snowflakecomputing.com" in account
        ):
            logger.error("snowflake_account_identifier_invalid")
            raise DataSourceUnavailableError(
                SNOWFLAKE_SOURCE,
                code="snowflake_account_invalid",
                message="The Snowflake account identifier is invalid.",
            )
        return values

    def _connector_log_fields(
        self,
        exc: snowflake.connector.Error,
        *,
        operation: str,
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "exception_type": type(exc).__name__,
            "error_code": getattr(exc, "errno", None),
            "sqlstate": getattr(exc, "sqlstate", None),
            "role": self.settings.snowflake_api_role,
            "warehouse": self.settings.snowflake_warehouse,
        }

    @staticmethod
    def _classified_connector_error(
        exc: snowflake.connector.Error,
        *,
        operation: str,
    ) -> DataSourceUnavailableError:
        message = str(getattr(exc, "msg", "") or exc).lower()
        sqlstate = str(getattr(exc, "sqlstate", "") or "")

        if sqlstate == "28000" or any(
            marker in message
            for marker in ("incorrect username or password", "authentication failed")
        ):
            return DataSourceUnavailableError(
                SNOWFLAKE_SOURCE,
                code="snowflake_authentication_failed",
                message="Snowflake authentication failed.",
            )
        if "role" in message and any(
            marker in message
            for marker in ("not granted", "not authorized", "does not exist")
        ):
            return DataSourceUnavailableError(
                SNOWFLAKE_SOURCE,
                code="snowflake_role_unauthorized",
                message="The configured Snowflake role is unavailable to this user.",
            )
        if "warehouse" in message and any(
            marker in message
            for marker in ("not authorized", "does not exist", "insufficient privilege")
        ):
            return DataSourceUnavailableError(
                SNOWFLAKE_SOURCE,
                code="snowflake_warehouse_unavailable",
                message="The configured Snowflake warehouse is unavailable.",
            )
        if any(
            object_name in message
            for object_name in ("covid_enriched", "country_latest_metrics")
        ):
            if "insufficient privilege" in message:
                return DataSourceUnavailableError(
                    SNOWFLAKE_SOURCE,
                    code="snowflake_permission_denied",
                    message="The Snowflake application role cannot read the analytics mart.",
                )
            return DataSourceUnavailableError(
                SNOWFLAKE_SOURCE,
                code="analytics_objects_missing",
                message="Required Snowflake analytics objects are missing or inaccessible.",
            )
        if any(
            marker in message
            for marker in ("account identifier", "unknown host", "404 not found")
        ):
            return DataSourceUnavailableError(
                SNOWFLAKE_SOURCE,
                code="snowflake_account_invalid",
                message="The Snowflake account identifier is invalid.",
            )
        if sqlstate.startswith("08"):
            return DataSourceUnavailableError(
                SNOWFLAKE_SOURCE,
                code="snowflake_network_unavailable",
                message="Snowflake could not be reached from the API.",
            )
        if "insufficient privilege" in message or "not authorized" in message:
            return DataSourceUnavailableError(
                SNOWFLAKE_SOURCE,
                code="snowflake_permission_denied",
                message="The Snowflake application role lacks a required permission.",
            )
        return DataSourceUnavailableError(
            SNOWFLAKE_SOURCE,
            code="snowflake_unavailable",
            message="Snowflake is temporarily unavailable.",
        )

    def _connect(self) -> SnowflakeConnection:
        configuration = self._configuration()

        try:
            return snowflake.connector.connect(
                account=configuration["SNOWFLAKE_ACCOUNT"],
                user=configuration["SNOWFLAKE_USER"],
                password=configuration["SNOWFLAKE_PASSWORD"],
                role=configuration["SNOWFLAKE_API_ROLE"],
                warehouse=configuration["SNOWFLAKE_WAREHOUSE"],
                database=configuration["SNOWFLAKE_DATABASE"],
                schema=configuration["SNOWFLAKE_API_SCHEMA"],
                application="COVID_ANALYTICS_API",
            )
        except snowflake.connector.Error as exc:
            logger.exception(
                "snowflake_connection_failed",
                extra=self._connector_log_fields(exc, operation="connect"),
                exc_info=sanitized_exception_info(exc),
            )
            raise self._classified_connector_error(exc, operation="connect") from exc

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
            logger.exception(
                "snowflake_query_failed",
                extra=self._connector_log_fields(exc, operation=operation),
                exc_info=sanitized_exception_info(exc),
            )
            raise self._classified_connector_error(exc, operation=operation) from exc
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

    def fetch_forecast_history(
        self,
        identifier: str,
        metric: Metric,
        lookback_days: int,
    ) -> list[dict[str, Any]]:
        # Limiting in Snowflake keeps the API's modelling cost proportional to the
        # requested window instead of transferring a country's full history.
        metric_column = METRIC_COLUMNS[metric]
        return self._execute(
            "forecast_history",
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
            ),
            HISTORY AS (
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
                QUALIFY ROW_NUMBER() OVER (
                    ORDER BY data.REPORT_DATE DESC NULLS LAST
                ) <= %s
            )
            SELECT
                COUNTRY,
                COUNTRY_ISO2,
                COUNTRY_ISO3,
                LOCATION_KEY,
                REPORT_DATE,
                METRIC_VALUE
            FROM HISTORY
            ORDER BY REPORT_DATE
            """,
            (identifier, identifier, identifier, identifier, lookback_days),
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
