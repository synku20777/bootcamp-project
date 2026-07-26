from __future__ import annotations

import logging

from pymongo import MongoClient

from app.config import get_settings
from app.exceptions import DataSourceUnavailableError
from app.logging_config import configure_logging, sanitized_exception_info
from app.repositories.annotation_repository import AnnotationRepository

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging("mongodb-setup", settings.log_level)
    client = MongoClient(
        settings.mongodb_uri,
        tz_aware=True,
        serverSelectionTimeoutMS=3000,
    )
    try:
        index_names = AnnotationRepository(client, settings).ensure_indexes()
    except DataSourceUnavailableError as exc:
        logger.exception(
            "mongodb_setup_failed",
            extra={"source": exc.source},
            exc_info=sanitized_exception_info(exc),
        )
        raise SystemExit(1) from exc
    finally:
        client.close()
    logger.info(
        "mongodb_setup_completed",
        extra={"index_count": len(index_names)},
    )


if __name__ == "__main__":
    main()
