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
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "openlawdev"
    # Query embedding (Plan 3 T1): local voyage-4-nano is the default, per the
    # measured decision in docs/superpowers/sdd/.../task-1-brief.md -- the Voyage
    # API stays available as a configurable alternative.
    query_embedder_backend: str = "local_nano"
    nano_model_dir: Path | None = None
    # Graph expansion (Plan 3 T3, fix round 2): OFF by default. Everything
    # expansion adds becomes citable, and live measurement showed it is
    # mostly filler today -- see the full reasoning and the specific
    # re-enable condition in `openlaw.retrieval.rerank`'s module docstring.
    graph_expansion_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return process-wide settings, constructed once."""
    return Settings()
