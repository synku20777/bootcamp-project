from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "covid-api"
    log_level: str = "INFO"

    snowflake_account: str = ""
    snowflake_user: str = ""
    snowflake_password: SecretStr = SecretStr("")
    snowflake_api_role: str = "COVID_APP_ROLE"
    snowflake_warehouse: str = "COVID_WH"
    snowflake_database: str = "COVID_ANALYTICS"
    snowflake_api_schema: str = "MARTS"

    mongodb_uri: str = "mongodb://localhost:27017/covid_app"
    mongo_database: str = "covid_app"
    redis_url: str = "redis://localhost:6379/0"

    cache_enabled: bool = True
    cache_namespace: str = "covid-api:v2"
    cache_ttl_countries_seconds: int = 86_400
    cache_ttl_overview_seconds: int = 86_400
    cache_ttl_summary_seconds: int = 86_400
    cache_ttl_timeseries_seconds: int = 86_400
    cache_ttl_compare_seconds: int = 86_400
    cache_ttl_country_page_seconds: int = 86_400
    cache_ttl_comparison_page_seconds: int = 86_400
    cache_ttl_annotation_target_seconds: int = 86_400
    cache_lock_seconds: int = 30
    cache_lock_wait_seconds: float = 10.0
    cache_lock_poll_seconds: float = 0.1


class DashboardSettings(BaseSettings):
    """Public dashboard settings; deliberately never reads the project `.env`."""

    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "covid-dashboard"
    log_level: str = "INFO"
    dashboard_api_base_url: str = "http://localhost:8000"
    dashboard_public_api_base_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    """Return one immutable settings instance per process."""

    return Settings()


@lru_cache
def get_dashboard_settings() -> DashboardSettings:
    """Return dashboard-safe settings sourced only from its environment."""

    return DashboardSettings()
