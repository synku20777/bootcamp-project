from __future__ import annotations

import logging

from redis import Redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging("cache-maintenance", settings.log_level)
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    cursor = 0
    deleted = 0
    pattern = f"{settings.cache_namespace}:*"

    try:
        while True:
            cursor, keys = redis_client.scan(
                cursor=cursor,
                match=pattern,
                count=100,
            )
            if keys:
                deleted += redis_client.unlink(*keys)
            if cursor == 0:
                break
    except RedisError as exc:
        logger.error(
            "cache_clear_failed",
            extra={"redis_error_type": type(exc).__name__},
        )
        raise SystemExit(1) from exc
    finally:
        redis_client.close()

    logger.info("cache_clear_completed", extra={"deleted_key_count": deleted})


if __name__ == "__main__":
    main()
