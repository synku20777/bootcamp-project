from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.config import Settings  # noqa: E402
from app.models.covid import Metric  # noqa: E402
from app.repositories.snowflake_repository import SnowflakeRepository  # noqa: E402
from app.services.cache_service import CacheService  # noqa: E402
from app.services.covid_service import CovidService  # noqa: E402


def main() -> None:
    load_dotenv()
    settings = Settings(cache_enabled=False)
    service = CovidService(
        SnowflakeRepository(settings),
        CacheService(cast(Any, None), settings),
        settings,
    )
    context, _ = service.context("LV")
    dashboard, _ = service.country_dashboard(
        "LV",
        Metric.CASES_PER_100K,
        date(2020, 12, 1),
        date(2020, 12, 14),
    )
    result = {
        "context_snapshot_id": context.snapshot_id,
        "country": context.country,
        "population_2020_context": context.population_2020_context.value,
        "covid_rate_population_2020": context.covid_rate_population_2020.value,
        "dashboard_context_status": dashboard.context_status,
        "dashboard_context_included": dashboard.context is not None,
        "dashboard_points": len(dashboard.selected.points),
    }
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    if (
        dashboard.context is None
        or dashboard.context.snapshot_id != context.snapshot_id
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
