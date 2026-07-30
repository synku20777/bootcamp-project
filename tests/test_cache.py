from __future__ import annotations

import unittest

from redis.exceptions import RedisError

from app.config import Settings
from app.exceptions import CacheFillInProgressError, DataSourceUnavailableError
from app.models.covid import CountryIdentity
from app.services.cache_service import CacheService, CacheStatus


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.available = True
        self.allow_lock = True

    def get(self, key: str):
        if not self.available:
            raise RedisError("unavailable")
        return self.values.get(key)

    def set(self, key: str, value: str, **_kwargs):
        if not self.available:
            raise RedisError("unavailable")
        if not self.allow_lock:
            return False
        self.values[key] = value
        return True

    def setex(self, key: str, _ttl: int, value: str):
        if not self.available:
            raise RedisError("unavailable")
        self.values[key] = value

    def delete(self, key: str):
        self.values.pop(key, None)

    def eval(self, _script: str, _key_count: int, key: str, token: str):
        if self.values.get(key) == token:
            self.values.pop(key, None)
            return 1
        return 0


class CacheServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = FakeRedis()
        self.settings = Settings(
            _env_file=None,
            cache_lock_wait_seconds=0,
        )
        self.cache = CacheService(self.redis, self.settings)

    @staticmethod
    def model() -> CountryIdentity:
        return CountryIdentity(
            country="Latvia",
            iso2="LV",
            iso3="LVA",
            location_key="LVA",
        )

    def test_miss_then_hit_computes_once(self) -> None:
        compute_calls = 0

        def compute() -> CountryIdentity:
            nonlocal compute_calls
            compute_calls += 1
            return self.model()

        first, first_status = self.cache.get_or_compute(
            endpoint="summary",
            key_payload={"identifier": "LV"},
            ttl_seconds=60,
            model_type=CountryIdentity,
            compute=compute,
        )
        second, second_status = self.cache.get_or_compute(
            endpoint="summary",
            key_payload={"identifier": "LV"},
            ttl_seconds=60,
            model_type=CountryIdentity,
            compute=compute,
        )

        self.assertEqual(first, second)
        self.assertEqual(first_status, CacheStatus.MISS)
        self.assertEqual(second_status, CacheStatus.HIT)
        self.assertEqual(compute_calls, 1)

    def test_context_key_exposes_snapshot_namespace_for_safe_invalidation(self) -> None:
        snapshot_id = "wdi2-2019-2021-fixture"
        self.cache.get_or_compute(
            endpoint="country-context",
            key_payload={"iso3": "LVA", "snapshot_id": snapshot_id},
            key_override=f"{snapshot_id}:country-context:LVA",
            ttl_seconds=60,
            model_type=CountryIdentity,
            compute=self.model,
        )
        self.assertIn(
            f"covid-api:v4:{snapshot_id}:country-context:LVA",
            self.redis.values,
        )

    def test_redis_failure_does_not_compute(self) -> None:
        compute_calls = 0
        self.redis.available = False

        def compute() -> CountryIdentity:
            nonlocal compute_calls
            compute_calls += 1
            return self.model()

        with self.assertRaises(DataSourceUnavailableError):
            self.cache.get_or_compute(
                endpoint="summary",
                key_payload={"identifier": "LV"},
                ttl_seconds=60,
                model_type=CountryIdentity,
                compute=compute,
            )

        self.assertEqual(compute_calls, 0)

    def test_lock_contention_does_not_compute(self) -> None:
        compute_calls = 0
        self.redis.allow_lock = False

        def compute() -> CountryIdentity:
            nonlocal compute_calls
            compute_calls += 1
            return self.model()

        with self.assertRaises(CacheFillInProgressError):
            self.cache.get_or_compute(
                endpoint="summary",
                key_payload={"identifier": "LV"},
                ttl_seconds=60,
                model_type=CountryIdentity,
                compute=compute,
            )

        self.assertEqual(compute_calls, 0)


if __name__ == "__main__":
    unittest.main()
