from __future__ import annotations

import unittest

from fastapi.testclient import TestClient
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError
from snowflake.connector.errors import DatabaseError, ProgrammingError

from app.config import Settings
from app.dependencies import (
    get_covid_service,
    get_mongo_client,
    get_redis_client,
    get_snowflake_repository,
)
from app.exceptions import DataSourceUnavailableError
from app.main import app
from app.repositories.snowflake_repository import SnowflakeRepository


class HealthyMongo:
    @property
    def admin(self):
        return self

    def command(self, _command):
        return {"ok": 1}


class UnavailableMongo(HealthyMongo):
    def command(self, _command):
        raise PyMongoError("private MongoDB detail")


class HealthyRedis:
    def ping(self):
        return True


class UnavailableRedis(HealthyRedis):
    def ping(self):
        raise RedisError("private Redis detail")


class FailingSnowflakeRepository:
    def __init__(self, code: str) -> None:
        self.code = code

    def check_health(self):
        raise DataSourceUnavailableError(
            "Snowflake",
            code=self.code,
            message="Snowflake check failed safely.",
        )


class FailingCovidService:
    def overview(self):
        raise DataSourceUnavailableError(
            "Snowflake",
            code="analytics_objects_missing",
            message="Required Snowflake analytics objects are missing or inaccessible.",
        )


class RuntimeFailureTests(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_readiness_distinguishes_mongodb_and_redis(self) -> None:
        cases = (
            (UnavailableMongo(), HealthyRedis(), "mongodb_unavailable"),
            (HealthyMongo(), UnavailableRedis(), "cache_unavailable"),
        )
        for mongo, redis, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                app.dependency_overrides[get_mongo_client] = lambda mongo=mongo: mongo
                app.dependency_overrides[get_redis_client] = lambda redis=redis: redis
                with TestClient(app) as client:
                    response = client.get(
                        "/health/ready",
                        headers={"X-Request-ID": "qa-readiness"},
                    )

                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["error"]["code"], expected_code)
                self.assertEqual(
                    response.json()["error"]["request_id"],
                    "qa-readiness",
                )
                self.assertNotIn("private", response.text.lower())

    def test_snowflake_health_preserves_specific_safe_error_codes(self) -> None:
        codes = (
            "snowflake_authentication_failed",
            "snowflake_account_invalid",
            "snowflake_role_unauthorized",
            "snowflake_warehouse_unavailable",
            "snowflake_permission_denied",
            "analytics_objects_missing",
            "snowflake_network_unavailable",
        )
        for code in codes:
            with self.subTest(code=code):
                app.dependency_overrides[get_snowflake_repository] = lambda code=code: (
                    FailingSnowflakeRepository(code)
                )
                with TestClient(app) as client:
                    response = client.get("/health/snowflake")

                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["error"]["code"], code)
                self.assertEqual(
                    response.json()["error"]["request_id"],
                    response.headers["X-Request-ID"],
                )

    def test_analytical_route_preserves_dependency_error_code(self) -> None:
        app.dependency_overrides[get_covid_service] = lambda: FailingCovidService()
        with TestClient(app) as client:
            response = client.get("/dashboard/overview")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"]["code"],
            "analytics_objects_missing",
        )

    def test_configuration_validation_lists_no_secret_values(self) -> None:
        repository = SnowflakeRepository(
            Settings(
                _env_file=None,
                snowflake_account="",
                snowflake_user="",
                snowflake_password="do-not-expose",
            )
        )

        with self.assertRaises(DataSourceUnavailableError) as raised:
            repository._configuration()

        self.assertEqual(raised.exception.code, "snowflake_configuration_invalid")
        self.assertNotIn("do-not-expose", str(raised.exception))

    def test_url_style_account_identifier_is_rejected_before_connecting(self) -> None:
        repository = SnowflakeRepository(
            Settings(
                _env_file=None,
                snowflake_account="https://example.snowflakecomputing.com",
                snowflake_user="tester",
                snowflake_password="secret",
            )
        )

        with self.assertRaises(DataSourceUnavailableError) as raised:
            repository._configuration()

        self.assertEqual(raised.exception.code, "snowflake_account_invalid")

    def test_connector_failures_are_classified_without_approximation(self) -> None:
        cases = (
            (
                DatabaseError(
                    msg="Incorrect username or password",
                    sqlstate="28000",
                    send_telemetry=False,
                ),
                "snowflake_authentication_failed",
            ),
            (
                ProgrammingError(
                    msg="Role COVID_APP_ROLE is not granted to this user",
                    send_telemetry=False,
                ),
                "snowflake_role_unauthorized",
            ),
            (
                ProgrammingError(
                    msg="Warehouse COVID_WH does not exist or not authorized",
                    send_telemetry=False,
                ),
                "snowflake_warehouse_unavailable",
            ),
            (
                ProgrammingError(
                    msg="Object COVID_ENRICHED does not exist or not authorized",
                    send_telemetry=False,
                ),
                "analytics_objects_missing",
            ),
            (
                DatabaseError(
                    msg="Could not connect to backend",
                    sqlstate="08001",
                    send_telemetry=False,
                ),
                "snowflake_network_unavailable",
            ),
        )

        for connector_error, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                classified = SnowflakeRepository._classified_connector_error(
                    connector_error,
                    operation="health_check",
                )
                self.assertEqual(classified.code, expected_code)
                self.assertNotIn(str(connector_error), str(classified))


if __name__ == "__main__":
    unittest.main()
