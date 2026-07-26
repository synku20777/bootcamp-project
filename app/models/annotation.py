from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.models.covid import CountryIdentity, Metric

DisplayName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
]
Comment = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000),
]


class AnnotationCreate(BaseModel):
    country: str
    report_date: date
    metric: Metric
    comment: Comment
    created_by: DisplayName


class Annotation(BaseModel):
    id: str
    country: str
    iso2: str | None
    iso3: str | None
    location_key: str
    report_date: date
    metric: Metric
    comment: str
    created_by: str
    created_at: datetime


class AnnotationTarget(CountryIdentity):
    report_date: date | None = None
