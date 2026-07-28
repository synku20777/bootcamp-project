from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time
from typing import Any

from pymongo import ASCENDING, MongoClient
from pymongo.errors import CollectionInvalid, PyMongoError

from app.config import Settings
from app.exceptions import mongodb_unavailable_error
from app.logging_config import sanitized_exception_info
from app.models.annotation import Annotation, AnnotationCreate, AnnotationTarget

logger = logging.getLogger(__name__)


class AnnotationRepository:
    def __init__(self, client: MongoClient, settings: Settings) -> None:
        self.database = client[settings.mongo_database]
        self.collection = self.database["annotations"]

    def ensure_indexes(self) -> list[str]:
        try:
            if "annotations" not in self.database.list_collection_names():
                try:
                    self.database.create_collection("annotations")
                except CollectionInvalid:
                    pass
            return [
                self.collection.create_index(
                    [
                        ("country", ASCENDING),
                        ("report_date", ASCENDING),
                        ("created_at", ASCENDING),
                    ],
                    name="annotations_country_date_created_at",
                    unique=False,
                ),
                self.collection.create_index(
                    [
                        ("country", ASCENDING),
                        ("metric", ASCENDING),
                        ("report_date", ASCENDING),
                    ],
                    name="annotations_country_metric_date",
                    unique=False,
                ),
            ]
        except PyMongoError as exc:
            logger.exception(
                "annotation_index_setup_failed",
                extra={"mongo_error_type": type(exc).__name__},
                exc_info=sanitized_exception_info(exc),
            )
            raise mongodb_unavailable_error() from exc

    @staticmethod
    def _at_midnight(value: date) -> datetime:
        return datetime.combine(value, time.min, tzinfo=UTC)

    @staticmethod
    def _serialize(document: dict[str, Any]) -> Annotation:
        report_date = document["report_date"]
        if isinstance(report_date, datetime):
            report_date = report_date.date()
        created_at = document["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return Annotation(
            id=str(document["_id"]),
            country=document["country"],
            iso2=document.get("iso2"),
            iso3=document.get("iso3"),
            location_key=document["location_key"],
            report_date=report_date,
            metric=document["metric"],
            comment=document["comment"],
            created_by=document["created_by"],
            created_at=created_at,
        )

    def create(
        self,
        target: AnnotationTarget,
        payload: AnnotationCreate,
        created_at: datetime,
    ) -> Annotation:
        document = {
            "country": target.country,
            "iso2": target.iso2,
            "iso3": target.iso3,
            "location_key": target.location_key,
            "report_date": self._at_midnight(payload.report_date),
            "metric": payload.metric.value,
            "comment": payload.comment,
            "created_by": payload.created_by,
            "created_at": created_at,
        }
        try:
            result = self.collection.insert_one(document)
        except PyMongoError as exc:
            logger.exception(
                "annotation_create_failed",
                extra={"mongo_error_type": type(exc).__name__},
                exc_info=sanitized_exception_info(exc),
            )
            raise mongodb_unavailable_error() from exc
        document["_id"] = result.inserted_id
        return self._serialize(document)

    def list(
        self,
        *,
        country: str,
        metric: str | None,
        start_date: date | None,
        end_date: date | None,
    ) -> list[Annotation]:
        query: dict[str, Any] = {"country": country}
        if metric is not None:
            query["metric"] = metric
        if start_date is not None or end_date is not None:
            report_date_filter: dict[str, datetime] = {}
            if start_date is not None:
                report_date_filter["$gte"] = self._at_midnight(start_date)
            if end_date is not None:
                report_date_filter["$lte"] = self._at_midnight(end_date)
            query["report_date"] = report_date_filter

        try:
            documents = list(
                self.collection.find(query).sort(
                    [("report_date", ASCENDING), ("created_at", ASCENDING)]
                )
            )
        except PyMongoError as exc:
            logger.exception(
                "annotation_read_failed",
                extra={"mongo_error_type": type(exc).__name__},
                exc_info=sanitized_exception_info(exc),
            )
            raise mongodb_unavailable_error() from exc
        return [self._serialize(document) for document in documents]
