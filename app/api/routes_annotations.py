from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, status

from app.dependencies import AnnotationServiceDependency
from app.models.annotation import Annotation, AnnotationCreate
from app.models.covid import Metric

router = APIRouter(prefix="/annotations", tags=["annotations"])


@router.post("", response_model=Annotation, status_code=status.HTTP_201_CREATED)
def create_annotation(
    payload: AnnotationCreate,
    service: AnnotationServiceDependency,
) -> Annotation:
    return service.create(payload)


@router.get("", response_model=list[Annotation])
def list_annotations(
    service: AnnotationServiceDependency,
    country: str = Query(min_length=1),
    metric: Metric | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Annotation]:
    return service.list(
        country=country,
        metric=metric,
        start_date=start_date,
        end_date=end_date,
    )
