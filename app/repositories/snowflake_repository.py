from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import snowflake.connector
from snowflake.connector import SnowflakeConnection

from app.config import Settings
from app.exceptions import DataSourceUnavailableError
from app.logging_config import sanitized_exception_info
from app.models.covid import Metric

logger = logging.getLogger(__name__)

SNOWFLAKE_SOURCE = "Snowflake"


@dataclass(frozen=True)
class CovidDatasetObjects:
    enriched: str
    latest: str
    patterns: str
    denominator_publication_status: str


COVID_DATASET_OBJECTS: dict[str, CovidDatasetObjects] = {
    "extended": CovidDatasetObjects(
        enriched="COVID_ANALYTICS.MARTS.COVID_ENRICHED_EXTENDED",
        latest="COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS_EXTENDED",
        patterns="COVID_ANALYTICS.MARTS.CASE_INCREASE_PATTERNS_EXTENDED",
        denominator_publication_status=(
            "COALESCE(DENOMINATOR_PUBLICATION_STATUS, 'ACTIVE')"
        ),
    ),
    "legacy": CovidDatasetObjects(
        enriched="COVID_ANALYTICS.MARTS.COVID_ENRICHED",
        latest="COVID_ANALYTICS.MARTS.COUNTRY_LATEST_METRICS",
        patterns="COVID_ANALYTICS.MARTS.CASE_INCREASE_PATTERNS",
        denominator_publication_status="'ACTIVE'",
    ),
}


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
        self.covid_objects = COVID_DATASET_OBJECTS[settings.covid_dataset]

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
            "covid_dataset": self.settings.covid_dataset,
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
            for object_name in (
                "covid_enriched",
                "country_latest_metrics",
                "case_increase_patterns",
            )
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

    def _connect(self, operation: str) -> SnowflakeConnection:
        configuration = self._configuration()
        query_tag = (
            f"{self.settings.snowflake_query_tag_prefix}:"
            f"{self.settings.covid_dataset}:{operation}"
        )

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
                login_timeout=self.settings.snowflake_login_timeout_seconds,
                network_timeout=self.settings.snowflake_network_timeout_seconds,
                session_parameters={
                    "QUERY_TAG": query_tag,
                    "STATEMENT_TIMEOUT_IN_SECONDS": (
                        self.settings.snowflake_statement_timeout_seconds
                    ),
                    "USE_CACHED_RESULT": self.settings.snowflake_use_cached_result,
                },
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
            connection_started = time.perf_counter()
            connection = self._connect(operation)
            connection_ms = round(
                (time.perf_counter() - connection_started) * 1000,
                1,
            )
            cursor = connection.cursor()
            query_started = time.perf_counter()
            cursor.execute(sql, tuple(parameters))

            if not cursor.description:
                logger.info(
                    "snowflake_query_succeeded",
                    extra={
                        "operation": operation,
                        "query_id": str(getattr(cursor, "sfqid", "") or ""),
                        "connection_ms": connection_ms,
                        "query_and_fetch_ms": round(
                            (time.perf_counter() - query_started) * 1000,
                            1,
                        ),
                        "returned_row_count": 0,
                        "covid_dataset": self.settings.covid_dataset,
                    },
                )
                return []

            columns = [
                getattr(column, "name", column[0]).upper()
                for column in cursor.description
            ]
            rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
            logger.info(
                "snowflake_query_succeeded",
                extra={
                    "operation": operation,
                    "query_id": str(getattr(cursor, "sfqid", "") or ""),
                    "connection_ms": connection_ms,
                    "query_and_fetch_ms": round(
                        (time.perf_counter() - query_started) * 1000,
                        1,
                    ),
                    "returned_row_count": len(rows),
                    "covid_dataset": self.settings.covid_dataset,
                },
            )
            return rows
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
            f"""
            SELECT
                (
                    SELECT 1
                    FROM {self.covid_objects.enriched}
                    LIMIT 1
                ) AS COVID_ENRICHED_ACCESSIBLE,
                (
                    SELECT 1
                    FROM {self.covid_objects.latest}
                    LIMIT 1
                ) AS COUNTRY_LATEST_METRICS_ACCESSIBLE,
                (
                    SELECT 1
                    FROM {self.covid_objects.patterns}
                    LIMIT 1
                ) AS CASE_INCREASE_PATTERNS_ACCESSIBLE
            """,
        )

    def fetch_overview(self) -> list[dict[str, Any]]:
        return self._execute(
            "dashboard_overview",
            f"""
            SELECT
                COUNTRY,
                COUNTRY_ISO2,
                COUNTRY_ISO3,
                LOCATION_KEY,
                REPORT_DATE,
                COVID_RATE_POPULATION_2020,
                CASES_CUMULATIVE,
                DEATHS_CUMULATIVE,
                CASES_PER_100K,
                DEATHS_PER_100K,
                MORTALITY_RATE_PERCENT,
                DENOMINATOR_JOIN_STATUS,
                {self.covid_objects.denominator_publication_status}
                    AS DENOMINATOR_PUBLICATION_STATUS,
                MAX(REPORT_DATE) OVER () AS DATASET_REPORT_DATE,
                COUNT(*) OVER () AS COUNTRY_COUNT,
                SUM(CASES_CUMULATIVE) OVER () AS TOTAL_CASES,
                SUM(DEATHS_CUMULATIVE) OVER () AS TOTAL_DEATHS,
                ROUND(
                    SUM(DEATHS_CUMULATIVE) OVER ()
                    / NULLIF(SUM(CASES_CUMULATIVE) OVER (), 0) * 100,
                    4
                ) AS GLOBAL_MORTALITY_RATE_PERCENT
            FROM {self.covid_objects.latest}
            ORDER BY COUNTRY
            """,
        )

    def fetch_countries(self) -> list[dict[str, Any]]:
        return self._execute(
            "countries",
            f"""
            SELECT
                COUNTRY,
                COUNTRY_ISO2,
                COUNTRY_ISO3,
                LOCATION_KEY
            FROM {self.covid_objects.latest}
            ORDER BY COUNTRY
            """,
        )

    def fetch_case_increase_patterns(
        self,
        country: str | None,
        start_date: Any,
        end_date: Any,
        minimum_consecutive_increases: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        country_filter = ""
        parameters: list[Any] = [
            start_date,
            end_date,
            minimum_consecutive_increases,
        ]
        if country is not None:
            country_filter = """
                AND (
                    UPPER(COUNTRY) = %s
                    OR UPPER(COALESCE(COUNTRY_ISO2, '')) = %s
                    OR UPPER(COALESCE(COUNTRY_ISO3, '')) = %s
                    OR UPPER(LOCATION_KEY) = %s
                )
            """
            parameters.extend((country, country, country, country))
        parameters.append(limit)

        return self._execute(
            "case_increase_patterns",
            f"""
            WITH FILTERED AS (
                SELECT
                    COUNTRY,
                    COUNTRY_ISO2,
                    COUNTRY_ISO3,
                    LOCATION_KEY,
                    START_DATE,
                    END_DATE,
                    DAYS_IN_PATTERN,
                    CONSECUTIVE_INCREASES,
                    START_CASES,
                    END_CASES
                FROM {self.covid_objects.patterns}
                WHERE END_DATE >= %s
                  AND START_DATE <= %s
                  AND CONSECUTIVE_INCREASES >= %s
                  {country_filter}
            ),
            SUMMARY AS (
                SELECT
                    COUNT(*) AS TOTAL_PATTERNS,
                    COUNT(DISTINCT LOCATION_KEY) AS COUNTRIES_WITH_PATTERNS,
                    MAX(CONSECUTIVE_INCREASES)
                        AS LONGEST_CONSECUTIVE_INCREASES,
                    MAX(END_DATE) AS LATEST_PATTERN_END_DATE
                FROM FILTERED
            ),
            RANKED AS (
                SELECT
                    filtered.*,
                    ROW_NUMBER() OVER (
                        ORDER BY
                            CONSECUTIVE_INCREASES DESC,
                            END_CASES DESC NULLS LAST,
                            COUNTRY,
                            START_DATE
                    ) AS RESULT_ORDER
                FROM FILTERED AS filtered
            )
            SELECT
                ranked.COUNTRY,
                ranked.COUNTRY_ISO2,
                ranked.COUNTRY_ISO3,
                ranked.LOCATION_KEY,
                ranked.START_DATE,
                ranked.END_DATE,
                ranked.DAYS_IN_PATTERN,
                ranked.CONSECUTIVE_INCREASES,
                ranked.START_CASES,
                ranked.END_CASES,
                summary.TOTAL_PATTERNS,
                summary.COUNTRIES_WITH_PATTERNS,
                summary.LONGEST_CONSECUTIVE_INCREASES,
                summary.LATEST_PATTERN_END_DATE
            FROM SUMMARY AS summary
            LEFT JOIN RANKED AS ranked ON ranked.RESULT_ORDER <= %s
            ORDER BY ranked.RESULT_ORDER NULLS LAST
            """,
            parameters,
        )

    def fetch_summary(self, identifier: str) -> list[dict[str, Any]]:
        return self._execute(
            "country_summary",
            f"""
            SELECT
                COUNTRY,
                COUNTRY_ISO2,
                COUNTRY_ISO3,
                LOCATION_KEY,
                REPORT_DATE,
                COVID_RATE_POPULATION_2020,
                {self.covid_objects.denominator_publication_status}
                    AS DENOMINATOR_PUBLICATION_STATUS,
                CASES_CUMULATIVE,
                DEATHS_CUMULATIVE,
                CASES_PER_100K,
                DEATHS_PER_100K,
                MORTALITY_RATE_PERCENT
            FROM {self.covid_objects.latest}
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
            f"""
            SELECT
                COUNTRY,
                COUNTRY_ISO2,
                COUNTRY_ISO3,
                LOCATION_KEY
            FROM {self.covid_objects.latest}
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

    def fetch_country_context(self, identifier: str) -> list[dict[str, Any]]:
        return self._execute(
            "country_context",
            f"""
            WITH RESOLVED AS (
                SELECT
                    COUNTRY,
                    COUNTRY_ISO2,
                    COUNTRY_ISO3,
                    LOCATION_KEY,
                    REPORT_DATE,
                    COVID_RATE_POPULATION_2020,
                    COVID_RATE_POPULATION_YEAR,
                    DENOMINATOR_SOURCE_SNAPSHOT_ID,
                    DENOMINATOR_VERSION,
                    DENOMINATOR_POLICY,
                    DENOMINATOR_IS_FROZEN,
                    CASES_CUMULATIVE,
                    DEATHS_CUMULATIVE,
                    CASES_PER_100K,
                    DEATHS_PER_100K,
                    MORTALITY_RATE_PERCENT
                FROM {self.covid_objects.latest}
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
                context.* EXCLUDE (
                    LOCATION_KEY,
                    ISO2,
                    ISO3,
                    COUNTRY_NAME,
                    COVID_RATE_POPULATION_2020,
                    COVID_RATE_POPULATION_YEAR,
                    DENOMINATOR_SOURCE_SNAPSHOT_ID,
                    DENOMINATOR_VERSION,
                    DENOMINATOR_POLICY,
                    DENOMINATOR_IS_FROZEN,
                    COVID_LATEST_REPORT_DATE,
                    CASES_CUMULATIVE,
                    DEATHS_CUMULATIVE,
                    CASES_PER_100K,
                    DEATHS_PER_100K,
                    MORTALITY_RATE_PERCENT
                ),
                resolved.LOCATION_KEY,
                resolved.COUNTRY_ISO2 AS ISO2,
                resolved.COUNTRY_ISO3 AS ISO3,
                resolved.COUNTRY AS COUNTRY_NAME,
                resolved.COVID_RATE_POPULATION_2020,
                resolved.COVID_RATE_POPULATION_YEAR,
                resolved.DENOMINATOR_SOURCE_SNAPSHOT_ID,
                resolved.DENOMINATOR_VERSION,
                resolved.DENOMINATOR_POLICY,
                resolved.DENOMINATOR_IS_FROZEN,
                resolved.REPORT_DATE AS COVID_LATEST_REPORT_DATE,
                resolved.CASES_CUMULATIVE,
                resolved.DEATHS_CUMULATIVE,
                resolved.CASES_PER_100K,
                resolved.DEATHS_PER_100K,
                resolved.MORTALITY_RATE_PERCENT
            FROM RESOLVED AS resolved
            LEFT JOIN COVID_ANALYTICS.MARTS.COUNTRY_CONTEXT_ANALYSIS AS context
                ON resolved.COUNTRY_ISO3 = context.ISO3
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
            f"""
            WITH RESOLVED AS (
                SELECT
                    COUNTRY,
                    COUNTRY_ISO2,
                    COUNTRY_ISO3,
                    LOCATION_KEY
                FROM {self.covid_objects.latest}
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
            LEFT JOIN {self.covid_objects.enriched} AS data
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
                FROM {self.covid_objects.latest}
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
            LEFT JOIN {self.covid_objects.enriched} AS data
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
                FROM {self.covid_objects.latest}
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
                LEFT JOIN {self.covid_objects.enriched} AS data
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
                LEFT JOIN {self.covid_objects.latest} AS latest
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
            LEFT JOIN {self.covid_objects.enriched} AS data
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
                    COVID_RATE_POPULATION_2020,
                    COVID_RATE_POPULATION_YEAR,
                    DENOMINATOR_SOURCE_SNAPSHOT_ID,
                    DENOMINATOR_VERSION,
                    DENOMINATOR_POLICY,
                    DENOMINATOR_IS_FROZEN,
                    {self.covid_objects.denominator_publication_status}
                        AS DENOMINATOR_PUBLICATION_STATUS,
                    CASES_CUMULATIVE,
                    DEATHS_CUMULATIVE,
                    CASES_PER_100K,
                    DEATHS_PER_100K,
                    MORTALITY_RATE_PERCENT AS LATEST_MORTALITY_RATE_PERCENT
                FROM {self.covid_objects.latest}
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
                resolved.COVID_RATE_POPULATION_2020,
                resolved.DENOMINATOR_PUBLICATION_STATUS,
                resolved.CASES_CUMULATIVE,
                resolved.DEATHS_CUMULATIVE,
                resolved.CASES_PER_100K,
                resolved.DEATHS_PER_100K,
                resolved.LATEST_MORTALITY_RATE_PERCENT,
                data.REPORT_DATE,
                data.{metric_column} AS SELECTED_METRIC_VALUE,
                data.NEW_CASES_RAW AS NEW_CASES_VALUE,
                data.NEW_DEATHS_RAW AS NEW_DEATHS_VALUE,
                data.MORTALITY_RATE_PERCENT AS MORTALITY_VALUE,
                context.POPULATION_2020_CONTEXT,
                context.POPULATION_2020_STATUS,
                context.POPULATION_DENSITY_2019,
                context.POPULATION_DENSITY_2019_STATUS,
                context.POPULATION_AGE_65_PLUS_PCT_2019,
                context.AGE_65_PLUS_2019_STATUS,
                context.REAL_GDP_PER_CAPITA_2019,
                context.REAL_GDP_PER_CAPITA_2019_STATUS,
                context.HEALTH_EXPENDITURE_PER_CAPITA_PPP_2019,
                context.HEALTH_EXPENDITURE_PPP_2019_STATUS,
                context.REAL_GDP_PER_CAPITA_2020,
                context.REAL_GDP_PER_CAPITA_2021,
                context.REAL_GDP_PER_CAPITA_CHANGE_2020_VS_2019_PCT,
                context.REAL_GDP_PER_CAPITA_CHANGE_2021_VS_2019_PCT,
                context.REAL_GDP_PER_CAPITA_CHANGE_2021_VS_2020_PCT,
                context.REAL_GDP_PER_CAPITA_CHANGE_2020_VS_2019_STATUS,
                context.REAL_GDP_PER_CAPITA_CHANGE_2021_VS_2019_STATUS,
                context.REAL_GDP_PER_CAPITA_CHANGE_2021_VS_2020_STATUS,
                resolved.LATEST_REPORT_DATE AS COVID_LATEST_REPORT_DATE,
                context.SNAPSHOT_ID AS CONTEXT_SNAPSHOT_ID,
                resolved.DENOMINATOR_SOURCE_SNAPSHOT_ID,
                resolved.DENOMINATOR_VERSION,
                resolved.DENOMINATOR_POLICY
            FROM RESOLVED AS resolved
            LEFT JOIN {self.covid_objects.enriched} AS data
                ON data.LOCATION_KEY = resolved.LOCATION_KEY
               AND data.REPORT_DATE BETWEEN %s AND %s
            LEFT JOIN COVID_ANALYTICS.MARTS.COUNTRY_CONTEXT_ANALYSIS AS context
                ON resolved.COUNTRY_ISO3 = context.ISO3
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

        # The baseline view is one row per ISO3. Joining it into the existing
        # comparison statement preserves the daily COVID grain while avoiding up
        # to ten additional Snowflake queries for one page render.
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
                LEFT JOIN {self.covid_objects.latest} AS latest
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
                data.MORTALITY_RATE_PERCENT,
                context.POPULATION_2020_CONTEXT,
                context.POPULATION_2020_STATUS,
                context.POPULATION_DENSITY_2019,
                context.POPULATION_DENSITY_2019_STATUS,
                context.POPULATION_AGE_65_PLUS_PCT_2019,
                context.AGE_65_PLUS_2019_STATUS,
                context.REAL_GDP_PER_CAPITA_2019,
                context.REAL_GDP_PER_CAPITA_2019_STATUS,
                context.HEALTH_EXPENDITURE_PER_CAPITA_PPP_2019,
                context.HEALTH_EXPENDITURE_PPP_2019_STATUS,
                context.SNAPSHOT_ID AS CONTEXT_SNAPSHOT_ID
            FROM RESOLVED AS resolved
            LEFT JOIN {self.covid_objects.enriched} AS data
                ON data.LOCATION_KEY = resolved.LOCATION_KEY
               AND data.REPORT_DATE BETWEEN %s AND %s
            LEFT JOIN COVID_ANALYTICS.MARTS.COUNTRY_CONTEXT_ANALYSIS AS context
                ON resolved.COUNTRY_ISO3 = context.ISO3
            ORDER BY resolved.REQUEST_ORDER, data.REPORT_DATE
            """,
            parameters,
        )
