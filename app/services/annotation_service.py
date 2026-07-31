from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from app.config import Settings
from app.exceptions import CountryNotFoundError, DomainValidationError
from app.models.annotation import Annotation, AnnotationCreate, AnnotationTarget
from app.models.covid import Metric
from app.repositories.annotation_repository import AnnotationRepository
from app.repositories.snowflake_repository import SnowflakeRepository
from app.services.cache_service import CacheService


class AnnotationService:
    def __init__(
        self,
        annotations: AnnotationRepository,
        snowflake: SnowflakeRepository,
        cache: CacheService,
        settings: Settings,
    ) -> None:
        self.annotations = annotations
        self.snowflake = snowflake
        self.cache = cache
        self.settings = settings

    @staticmethod
    def _identifier(value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise DomainValidationError("Country identifier cannot be empty.")
        return normalized

    @staticmethod
    def _identity(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "country": row["COUNTRY"],
            "iso2": row["COUNTRY_ISO2"],
            "iso3": row["COUNTRY_ISO3"],
            "location_key": row["LOCATION_KEY"],
        }

    @staticmethod
    def _validate_dates(
        start_date: date | None,
        end_date: date | None,
    ) -> None:
        if start_date is not None and end_date is not None and start_date > end_date:
            raise DomainValidationError("start_date must not be after end_date.")

    def _resolve_country(self, identifier: str) -> AnnotationTarget:
        normalized = self._identifier(identifier)

        def compute() -> AnnotationTarget:
            rows = self.snowflake.fetch_country_identity(normalized)
            if not rows:
                raise CountryNotFoundError(identifier)
            return AnnotationTarget(**self._identity(rows[0]))

        target, _cache_status = self.cache.get_or_compute(
            endpoint="annotation-country",
            key_payload={
                "dataset": self.settings.covid_dataset,
                "identifier": normalized,
                "version": 1,
            },
            ttl_seconds=self.settings.cache_ttl_annotation_target_seconds,
            model_type=AnnotationTarget,
            compute=compute,
        )
        return target

    def _resolve_data_point(
        self,
        identifier: str,
        report_date: date,
    ) -> AnnotationTarget:
        normalized = self._identifier(identifier)

        def compute() -> AnnotationTarget:
            rows = self.snowflake.fetch_annotation_target(normalized, report_date)
            if not rows:
                raise CountryNotFoundError(identifier)
            row = rows[0]
            if row["VALIDATED_REPORT_DATE"] is None:
                raise DomainValidationError(
                    "The selected country has no observation on that report date."
                )
            return AnnotationTarget(
                **self._identity(row),
                report_date=row["VALIDATED_REPORT_DATE"],
            )

        target, _cache_status = self.cache.get_or_compute(
            endpoint="annotation-target",
            key_payload={
                "dataset": self.settings.covid_dataset,
                "identifier": normalized,
                "report_date": report_date.isoformat(),
                "version": 1,
            },
            ttl_seconds=self.settings.cache_ttl_annotation_target_seconds,
            model_type=AnnotationTarget,
            compute=compute,
        )
        return target

    def create(self, payload: AnnotationCreate) -> Annotation:
        target = self._resolve_data_point(payload.country, payload.report_date)
        return self.annotations.create(
            target,
            payload,
            created_at=datetime.now(UTC),
        )

    def list(
        self,
        *,
        country: str,
        metric: Metric | None,
        start_date: date | None,
        end_date: date | None,
    ) -> list[Annotation]:
        self._validate_dates(start_date, end_date)
        target = self._resolve_country(country)
        return self.annotations.list(
            country=target.country,
            metric=metric.value if metric is not None else None,
            start_date=start_date,
            end_date=end_date,
        )
