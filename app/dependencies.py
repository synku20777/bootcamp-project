from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from pymongo import MongoClient
from redis import Redis

from app.config import Settings
from app.repositories.annotation_repository import AnnotationRepository
from app.repositories.snowflake_repository import SnowflakeRepository
from app.services.annotation_service import AnnotationService
from app.services.cache_service import CacheService
from app.services.covid_service import CovidService


def get_runtime_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_redis_client(request: Request) -> Redis:
    return request.app.state.redis_client


def get_mongo_client(request: Request) -> MongoClient:
    return request.app.state.mongo_client


SettingsDependency = Annotated[Settings, Depends(get_runtime_settings)]
RedisDependency = Annotated[Redis, Depends(get_redis_client)]
MongoDependency = Annotated[MongoClient, Depends(get_mongo_client)]


def get_snowflake_repository(
    settings: SettingsDependency,
) -> SnowflakeRepository:
    return SnowflakeRepository(settings)


SnowflakeRepositoryDependency = Annotated[
    SnowflakeRepository,
    Depends(get_snowflake_repository),
]


def get_cache_service(
    redis_client: RedisDependency,
    settings: SettingsDependency,
) -> CacheService:
    return CacheService(redis_client, settings)


CacheDependency = Annotated[CacheService, Depends(get_cache_service)]


def get_annotation_repository(
    mongo_client: MongoDependency,
    settings: SettingsDependency,
) -> AnnotationRepository:
    return AnnotationRepository(mongo_client, settings)


AnnotationRepositoryDependency = Annotated[
    AnnotationRepository,
    Depends(get_annotation_repository),
]


def get_annotation_service(
    annotations: AnnotationRepositoryDependency,
    repository: SnowflakeRepositoryDependency,
    cache: CacheDependency,
    settings: SettingsDependency,
) -> AnnotationService:
    return AnnotationService(annotations, repository, cache, settings)


AnnotationServiceDependency = Annotated[
    AnnotationService,
    Depends(get_annotation_service),
]


def get_covid_service(
    repository: SnowflakeRepositoryDependency,
    cache: CacheDependency,
    settings: SettingsDependency,
) -> CovidService:
    return CovidService(repository, cache, settings)


CovidServiceDependency = Annotated[CovidService, Depends(get_covid_service)]
