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

logger = logging.getLogger(__name__)
ModelT = TypeVar("ModelT", bound=BaseModel)


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

        try:
            cached = self.redis.get(key)
        except RedisError as exc:
            logger.error(
                "cache_read_failed",
                extra={"endpoint": endpoint, "cache_key_hash": key_hash},
            )
            raise DataSourceUnavailableError("Redis cache") from exc

        if cached is not None:
            try:
                value = self._decode(model_type, cached)
            except (ValidationError, ValueError):
                logger.warning(
                    "cache_value_invalid",
                    extra={"endpoint": endpoint, "cache_key_hash": key_hash},
                )
                try:
                    self.redis.delete(key)
                except RedisError as exc:
                    raise DataSourceUnavailableError("Redis cache") from exc
            else:
                logger.info(
                    "cache_hit",
                    extra={"endpoint": endpoint, "cache_key_hash": key_hash},
                )
                return value, CacheStatus.HIT

        lock_token = uuid4().hex
        try:
            lock_acquired = bool(
                self.redis.set(
                    lock_key,
                    lock_token,
                    nx=True,
                    ex=self.settings.cache_lock_seconds,
                )
            )
        except RedisError as exc:
            raise DataSourceUnavailableError("Redis cache") from exc

        if not lock_acquired:
            deadline = time.monotonic() + self.settings.cache_lock_wait_seconds
            while time.monotonic() < deadline:
                time.sleep(self.settings.cache_lock_poll_seconds)
                try:
                    cached = self.redis.get(key)
                except RedisError as exc:
                    raise DataSourceUnavailableError("Redis cache") from exc
                if cached is not None:
                    try:
                        return self._decode(model_type, cached), CacheStatus.HIT
                    except (ValidationError, ValueError):
                        break

            logger.warning(
                "cache_fill_wait_expired",
                extra={"endpoint": endpoint, "cache_key_hash": key_hash},
            )
            raise CacheFillInProgressError()

        try:
            value = compute()
            serialized = value.model_dump_json()
            try:
                self.redis.setex(key, ttl_seconds, serialized)
            except RedisError as exc:
                logger.error(
                    "cache_write_failed",
                    extra={"endpoint": endpoint, "cache_key_hash": key_hash},
                )
                raise DataSourceUnavailableError("Redis cache") from exc

            logger.info(
                "cache_miss_filled",
                extra={"endpoint": endpoint, "cache_key_hash": key_hash},
            )
            return value, CacheStatus.MISS
        finally:
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
