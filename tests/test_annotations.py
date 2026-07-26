from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo import ASCENDING

from app.config import Settings
from app.dependencies import get_annotation_service
from app.exceptions import DataSourceUnavailableError, DomainValidationError
from app.main import app
from app.models.annotation import Annotation, AnnotationCreate, AnnotationTarget
from app.models.covid import Metric
from app.repositories.annotation_repository import AnnotationRepository
from app.services.annotation_service import AnnotationService
from app.services.cache_service import CacheService
from tests.test_api import UnavailableRedis


class BypassRedis:
    pass


class FakeSnowflake:
    def __init__(self, valid_date: bool = True) -> None:
        self.calls = 0
        self.valid_date = valid_date

    def fetch_annotation_target(self, _identifier, report_date):
        self.calls += 1
        return [
            {
                "COUNTRY": "Latvia",
                "COUNTRY_ISO2": "LV",
                "COUNTRY_ISO3": "LVA",
                "LOCATION_KEY": "LVA",
                "VALIDATED_REPORT_DATE": report_date if self.valid_date else None,
            }
        ]

    def fetch_country_identity(self, _identifier):
        self.calls += 1
        return [
            {
                "COUNTRY": "Latvia",
                "COUNTRY_ISO2": "LV",
                "COUNTRY_ISO3": "LVA",
                "LOCATION_KEY": "LVA",
            }
        ]


class FakeAnnotationRepository:
    def __init__(self) -> None:
        self.records: list[Annotation] = []

    def create(self, target, payload, created_at):
        annotation = Annotation(
            id=str(len(self.records) + 1),
            country=target.country,
            iso2=target.iso2,
            iso3=target.iso3,
            location_key=target.location_key,
            report_date=payload.report_date,
            metric=payload.metric,
            comment=payload.comment,
            created_by=payload.created_by,
            created_at=created_at,
        )
        self.records.append(annotation)
        return annotation

    def list(self, **_filters):
        return list(self.records)


class FakeAnnotationApiService:
    def __init__(self) -> None:
        self.annotation = Annotation(
            id="507f1f77bcf86cd799439011",
            country="Latvia",
            iso2="LV",
            iso3="LVA",
            location_key="LVA",
            report_date=date(2020, 3, 15),
            metric=Metric.NEW_CASES,
            comment="Reporting delay.",
            created_by="Student",
            created_at=datetime(2026, 7, 26, 9, 0, tzinfo=UTC),
        )

    def create(self, _payload):
        return self.annotation

    def list(self, **_filters):
        return [self.annotation]


class AnnotationTests(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    @staticmethod
    def _payload() -> AnnotationCreate:
        return AnnotationCreate(
            country="LV",
            report_date=date(2020, 3, 15),
            metric=Metric.NEW_CASES,
            comment="Reporting delay.",
            created_by="Student",
        )

    def _service(
        self,
        snowflake: FakeSnowflake,
        annotations: FakeAnnotationRepository,
        *,
        cache_enabled: bool = False,
        redis=None,
    ) -> AnnotationService:
        settings = Settings(_env_file=None, cache_enabled=cache_enabled)
        cache = CacheService(redis or BypassRedis(), settings)
        return AnnotationService(annotations, snowflake, cache, settings)

    def test_create_stores_canonical_identity_and_allows_duplicates(self) -> None:
        snowflake = FakeSnowflake()
        annotations = FakeAnnotationRepository()
        service = self._service(snowflake, annotations)

        first = service.create(self._payload())
        second = service.create(self._payload())

        self.assertEqual(first.country, "Latvia")
        self.assertEqual(first.location_key, "LVA")
        self.assertEqual(len(annotations.records), 2)
        self.assertEqual(second.comment, first.comment)

    def test_invalid_annotation_date_is_not_written(self) -> None:
        snowflake = FakeSnowflake(valid_date=False)
        annotations = FakeAnnotationRepository()
        service = self._service(snowflake, annotations)

        with self.assertRaises(DomainValidationError):
            service.create(self._payload())

        self.assertEqual(annotations.records, [])

    def test_redis_failure_prevents_snowflake_validation(self) -> None:
        snowflake = FakeSnowflake()
        annotations = FakeAnnotationRepository()
        service = self._service(
            snowflake,
            annotations,
            cache_enabled=True,
            redis=UnavailableRedis(),
        )

        with self.assertRaises(DataSourceUnavailableError):
            service.create(self._payload())

        self.assertEqual(snowflake.calls, 0)
        self.assertEqual(annotations.records, [])

    def test_repository_indexes_and_object_id_serialization(self) -> None:
        client = MagicMock()
        database = MagicMock()
        collection = MagicMock()
        client.__getitem__.return_value = database
        database.__getitem__.return_value = collection
        database.list_collection_names.side_effect = [[], ["annotations"]]
        collection.create_index.side_effect = [
            "annotations_country_date_created_at",
            "annotations_country_metric_date",
            "annotations_country_date_created_at",
            "annotations_country_metric_date",
        ]
        repository = AnnotationRepository(
            client,
            Settings(_env_file=None),
        )

        repository.ensure_indexes()
        repository.ensure_indexes()

        database.create_collection.assert_called_once_with("annotations")
        first_index = collection.create_index.call_args_list[0]
        self.assertEqual(
            first_index.args[0],
            [
                ("country", ASCENDING),
                ("report_date", ASCENDING),
                ("created_at", ASCENDING),
            ],
        )
        self.assertFalse(first_index.kwargs["unique"])

        inserted_id = ObjectId()
        collection.insert_one.return_value.inserted_id = inserted_id
        created = repository.create(
            AnnotationTarget(
                country="Latvia",
                iso2="LV",
                iso3="LVA",
                location_key="LVA",
                report_date=date(2020, 3, 15),
            ),
            self._payload(),
            datetime(2026, 7, 26, 9, 0, tzinfo=UTC),
        )
        self.assertEqual(created.id, str(inserted_id))

        stored_document = {
            "_id": inserted_id,
            "country": "Latvia",
            "iso2": "LV",
            "iso3": "LVA",
            "location_key": "LVA",
            "report_date": datetime(2020, 3, 15, tzinfo=UTC),
            "metric": "new_cases",
            "comment": "Reporting delay.",
            "created_by": "Student",
            "created_at": datetime(2026, 7, 26, 9, 0, tzinfo=UTC),
        }
        cursor = MagicMock()
        collection.find.return_value = cursor
        cursor.sort.return_value = [stored_document]
        listed = repository.list(
            country="Latvia",
            metric="new_cases",
            start_date=date(2020, 3, 1),
            end_date=date(2020, 3, 31),
        )
        query = collection.find.call_args.args[0]
        self.assertEqual(query["country"], "Latvia")
        self.assertEqual(query["metric"], "new_cases")
        self.assertEqual(
            query["report_date"],
            {
                "$gte": datetime(2020, 3, 1, tzinfo=UTC),
                "$lte": datetime(2020, 3, 31, tzinfo=UTC),
            },
        )
        cursor.sort.assert_called_once_with(
            [("report_date", ASCENDING), ("created_at", ASCENDING)]
        )
        self.assertEqual(listed[0].id, str(inserted_id))

    def test_annotation_api_contract_and_validation_boundaries(self) -> None:
        service = FakeAnnotationApiService()
        app.dependency_overrides[get_annotation_service] = lambda: service
        with TestClient(app) as client:
            created = client.post(
                "/annotations",
                json={
                    "country": "LV",
                    "report_date": "2020-03-15",
                    "metric": "new_cases",
                    "comment": "Reporting delay.",
                    "created_by": "Student",
                },
            )
            listed = client.get("/annotations", params={"country": "LV"})
            invalid = client.post(
                "/annotations",
                json={
                    "country": "LV",
                    "report_date": "2020-03-15",
                    "metric": "new_cases",
                    "comment": "",
                    "created_by": "",
                },
            )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["id"], service.annotation.id)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["country"], "Latvia")
        self.assertEqual(invalid.status_code, 422)


if __name__ == "__main__":
    unittest.main()
