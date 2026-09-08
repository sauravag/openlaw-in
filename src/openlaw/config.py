"""Environment-driven settings for the OpenLaw corpus pipeline."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration read from the environment or a local .env file."""

    model_config = SettingsConfigDict(
        env_prefix="OPENLAW_", env_file=".env", extra="ignore"
    )

    vaquill_root: Path
    kanoongpt_root: Path
    qdrant_url: str = "http://localhost:6333"
    voyage_api_key: str | None = None
    corpus_version: str = "v2026.08"


@lru_cache
def get_settings() -> Settings:
    """Return process-wide settings, constructed once."""
    return Settings()
