from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from enum import StrEnum
from typing import TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from redis import Redis
from redis.exceptions import RedisError

from app.config import Settings
from app.exceptions import CacheFillInProgressError, DataSourceUnavailableError
from app.logging_config import sanitized_exception_info

logger = logging.getLogger(__name__)
ModelT = TypeVar("ModelT", bound=BaseModel)
REDIS_CACHE_SOURCE = "Redis cache"


class CacheStatus(StrEnum):
    HIT = "HIT"
    MISS = "MISS"
    BYPASS = "BYPASS"


class CacheService:
    """Fail-closed Redis cache with per-key stampede protection."""

    _release_lock_script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
    """

    def __init__(self, redis_client: Redis, settings: Settings) -> None:
        self.redis = redis_client
        self.settings = settings

    def _key(self, endpoint: str, payload: dict[str, object]) -> tuple[str, str]:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        key = f"{self.settings.cache_namespace}:{endpoint}:{digest}"
        return key, digest

    @staticmethod
    def _decode(model_type: type[ModelT], raw_value: str) -> ModelT:
        return model_type.model_validate_json(raw_value)

    def _read_cached_value(
        self,
        *,
        key: str,
        endpoint: str,
        key_hash: str,
        model_type: type[ModelT],
    ) -> ModelT | None:
        try:
            raw_value = self.redis.get(key)
        except RedisError as exc:
            logger.exception(
                "cache_read_failed",
                extra={"endpoint": endpoint, "cache_key_hash": key_hash},
                exc_info=sanitized_exception_info(exc),
            )
            raise DataSourceUnavailableError(REDIS_CACHE_SOURCE) from exc

        if raw_value is None:
            return None

        try:
            return self._decode(model_type, raw_value)
        except (ValidationError, ValueError):
            logger.warning(
                "cache_value_invalid",
                extra={"endpoint": endpoint, "cache_key_hash": key_hash},
            )
            self._delete_invalid_value(
                key=key,
                endpoint=endpoint,
                key_hash=key_hash,
            )
            return None

    def _delete_invalid_value(
        self,
        *,
        key: str,
        endpoint: str,
        key_hash: str,
    ) -> None:
        try:
            self.redis.delete(key)
        except RedisError as exc:
            logger.exception(
                "cache_delete_failed",
                extra={"endpoint": endpoint, "cache_key_hash": key_hash},
                exc_info=sanitized_exception_info(exc),
            )
            raise DataSourceUnavailableError(REDIS_CACHE_SOURCE) from exc

    def _acquire_lock(
        self,
        *,
        lock_key: str,
        lock_token: str,
        endpoint: str,
        key_hash: str,
    ) -> bool:
        try:
            return bool(
                self.redis.set(
                    lock_key,
                    lock_token,
                    nx=True,
                    ex=self.settings.cache_lock_seconds,
                )
            )
        except RedisError as exc:
            logger.exception(
                "cache_lock_acquisition_failed",
                extra={"endpoint": endpoint, "cache_key_hash": key_hash},
                exc_info=sanitized_exception_info(exc),
            )
            raise DataSourceUnavailableError(REDIS_CACHE_SOURCE) from exc

    def _wait_for_cached_value(
        self,
        *,
        key: str,
        endpoint: str,
        key_hash: str,
        model_type: type[ModelT],
    ) -> ModelT:
        deadline = time.monotonic() + self.settings.cache_lock_wait_seconds
        while time.monotonic() < deadline:
            time.sleep(self.settings.cache_lock_poll_seconds)
            cached_value = self._read_cached_value(
                key=key,
                endpoint=endpoint,
                key_hash=key_hash,
                model_type=model_type,
            )
            if cached_value is not None:
                logger.info(
                    "cache_hit_after_wait",
                    extra={"endpoint": endpoint, "cache_key_hash": key_hash},
                )
                return cached_value

        logger.warning(
            "cache_fill_wait_expired",
            extra={"endpoint": endpoint, "cache_key_hash": key_hash},
        )
        raise CacheFillInProgressError()

    def _write_cached_value(
        self,
        *,
        key: str,
        endpoint: str,
        key_hash: str,
        ttl_seconds: int,
        value: BaseModel,
    ) -> None:
        try:
            self.redis.setex(key, ttl_seconds, value.model_dump_json())
        except RedisError as exc:
            logger.exception(
                "cache_write_failed",
                extra={"endpoint": endpoint, "cache_key_hash": key_hash},
                exc_info=sanitized_exception_info(exc),
            )
            raise DataSourceUnavailableError(REDIS_CACHE_SOURCE) from exc

    def _release_lock(
        self,
        *,
        lock_key: str,
        lock_token: str,
        endpoint: str,
        key_hash: str,
    ) -> None:
        try:
            self.redis.eval(
                self._release_lock_script,
                1,
                lock_key,
                lock_token,
            )
        except RedisError:
            logger.warning(
                "cache_lock_release_failed",
                extra={"endpoint": endpoint, "cache_key_hash": key_hash},
            )

    def _compute_and_cache(
        self,
        *,
        key: str,
        lock_key: str,
        lock_token: str,
        endpoint: str,
        key_hash: str,
        ttl_seconds: int,
        compute: Callable[[], ModelT],
    ) -> ModelT:
        try:
            value = compute()
            self._write_cached_value(
                key=key,
                endpoint=endpoint,
                key_hash=key_hash,
                ttl_seconds=ttl_seconds,
                value=value,
            )
            logger.info(
                "cache_miss_filled",
                extra={"endpoint": endpoint, "cache_key_hash": key_hash},
            )
            return value
        finally:
            self._release_lock(
                lock_key=lock_key,
                lock_token=lock_token,
                endpoint=endpoint,
                key_hash=key_hash,
            )

    def get_or_compute(
        self,
        *,
        endpoint: str,
        key_payload: dict[str, object],
        ttl_seconds: int,
        model_type: type[ModelT],
        compute: Callable[[], ModelT],
    ) -> tuple[ModelT, CacheStatus]:
        if not self.settings.cache_enabled:
            return compute(), CacheStatus.BYPASS

        key, key_hash = self._key(endpoint, key_payload)
        lock_key = f"{key}:lock"

        cached_value = self._read_cached_value(
            key=key,
            endpoint=endpoint,
            key_hash=key_hash,
            model_type=model_type,
        )
        if cached_value is not None:
            logger.info(
                "cache_hit",
                extra={"endpoint": endpoint, "cache_key_hash": key_hash},
            )
            return cached_value, CacheStatus.HIT

        lock_token = uuid4().hex
        lock_acquired = self._acquire_lock(
            lock_key=lock_key,
            lock_token=lock_token,
            endpoint=endpoint,
            key_hash=key_hash,
        )

        if not lock_acquired:
            cached_value = self._wait_for_cached_value(
                key=key,
                endpoint=endpoint,
                key_hash=key_hash,
                model_type=model_type,
            )
            return cached_value, CacheStatus.HIT

        value = self._compute_and_cache(
            key=key,
            lock_key=lock_key,
            lock_token=lock_token,
            endpoint=endpoint,
            key_hash=key_hash,
            ttl_seconds=ttl_seconds,
            compute=compute,
        )
        return value, CacheStatus.MISS
