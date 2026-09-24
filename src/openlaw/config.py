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
    # Per-role model configuration (Plan 3 T4): the generator does grounded
    # legal reasoning and warrants a frontier model; translation and
    # classification do not, and paying frontier prices for them is waste at
    # the ~10K-user scale this product targets. See openlaw.answers.models.
    model_generator: str = "claude-opus-5"
    model_translator: str = "claude-haiku-4-5"
    model_classifier: str = "claude-haiku-4-5"
    anthropic_api_key: str | None = None
    # Hallucination log (Plan 3 T7, fix round 1): the log can carry verbatim,
    # sensitive user text (abuse, immigration status, debt disclosures), so
    # its location must be a deployment decision -- e.g. a restricted-access
    # volume -- rather than a literal hard-coded at a call site. See
    # openlaw.answers.escalation.append_events / purge_stale_events.
    hallucination_log_path: Path = Path("data/hallucinations.jsonl")
    # Crisis-classifier failure log (Plan 3 T8b, fix round 2): a classifier
    # outage is silent and sustained -- every question asked during it loses
    # the second layer of crisis routing -- so the failures must be countable
    # after the fact, not just a log line nobody was watching. Same verbatim,
    # sensitive question text as the hallucination log, so same 0600 treatment
    # and same "put it on a restricted volume" deployment decision. See
    # openlaw.answers.crisis_classifier.append_classifier_failure_events.
    classifier_failure_log_path: Path = Path("data/classifier_failures.jsonl")


@lru_cache
def get_settings() -> Settings:
    """Return process-wide settings, constructed once."""
    return Settings()
