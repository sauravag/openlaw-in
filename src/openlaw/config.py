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
    # Per-language translator overrides, e.g.
    # OPENLAW_MODEL_TRANSLATOR_OVERRIDES='{"mr": "some-indic-specialist-model"}'.
    # ModelRegistry has supported per-language routing from the start (Indic
    # specialists often beat general models on Indian languages), but nothing
    # wired it from the environment, so the capability was unreachable without
    # editing code. Empty by default: every language uses model_translator.
    model_translator_overrides: dict[str, str] = {}
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
    # Application database (Plan 5 T1): users, quota, conversations and the
    # hallucination log. Local default matches docker-compose.yml's postgres
    # service, bound to 127.0.0.1 only. See openlaw.db.models for what is and
    # is not stored.
    database_url: str = "postgresql+psycopg://openlaw:openlaw@localhost:5432/openlaw"
    # Quota. Self-hosters set OPENLAW_QUOTA_ENABLED=false for unlimited use.
    quota_enabled: bool = True
    monthly_quota: int = 30
    anonymous_quota: int = 3
    # Supabase auth (email + Google). Left unset in self-hosted/dev use.
    supabase_url: str | None = None
    supabase_jwt_secret: str | None = None
    # Reverse-proxy trust (Task 4 review, C1). `openlaw.api.routes._client_key`
    # uses the raw TCP peer address (`request.client.host`) UNLESS that peer
    # is listed here, in which case it walks `X-Forwarded-For` from the right
    # and returns the first hop NOT in this set. Empty (the default) means
    # trust nothing -- every request's peer is used as-is.
    #
    # This matters because every self-hosted deployment of this product sits
    # behind a reverse proxy (nginx/Caddy/Traefik/Docker Compose's bridge
    # network) by design. Left at the default behind such a proxy, EVERY
    # request's peer address is the proxy itself -- one constant value -- so
    # the per-caller rate limit and (pre-fix) the anonymous quota collapse
    # into one shared bucket for the whole deployment; see the Task 4 review,
    # finding C1, for the concrete failure ("the fourth visitor ever gets
    # blocked"). The fix is NOT to trust `X-Forwarded-For` unconditionally --
    # that header is attacker-supplied, and honouring it with no configured
    # trust turns both controls into a single spoofable header, i.e.
    # unbounded model spend on what is meant to be a free, capped service.
    # An operator running behind a known proxy MUST set this to that proxy's
    # address (its Docker Compose service IP, or `127.0.0.1` for a local
    # nginx) for per-caller accounting to work at all.
    trusted_proxy_ips: tuple[str, ...] = ()
    # Where a quota/rate-limit refusal tells the caller to sign up. Relative
    # by default so it works unmodified in dev; a real deployment should
    # override it with the frontend's actual URL.
    signup_url: str = "/signup"
    # Anonymous-quota cookie `Secure` flag (Task 4 review, low finding,
    # dispatched to Task 5 -- `openlaw.api.app._ensure_anonymous_token`).
    # Cannot be unconditional: a `Secure` cookie is dropped by every browser
    # on a plain-HTTP connection, which is exactly `docker compose up`
    # local development (no TLS). Defaults to True -- the safe choice for
    # any real deployment, which must be reached over HTTPS regardless (see
    # `trusted_proxy_ips` above: the reverse proxy a self-hosted deployment
    # sits behind is also where TLS is terminated). Self-hosters running
    # plain HTTP locally must set this to false, or the cookie silently
    # never round-trips and every request looks like a first visit.
    cookie_secure: bool = True


@lru_cache
def get_settings() -> Settings:
    """Return process-wide settings, constructed once."""
    return Settings()
