"""Environment-driven settings for the OpenLaw corpus pipeline."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    # Task 5 review, M2: a bare `OPENLAW_NANO_MODEL_DIR=` (present, empty --
    # exactly what a naive .env.example entry with no value looks like) does
    # NOT stay unset: pydantic parses the empty string as `Path("")`, which
    # `pathlib` normalises to `Path(".")` -- the repository root -- not
    # `None`. Silently truthy (`Path` has no `__bool__`, so `Path(".")` is
    # truthy like every other `Path`), so `openlaw.retrieval._nano_compat
    # .prepare_local_model_dir` would `rglob` and symlink the entire repo
    # tree (`.venv`, `data/`, everything) into the model cache before
    # failing to find a model there. The validator below is what actually
    # keeps "leave unset to fetch from the Hub" true for an empty value, not
    # just for a genuinely absent one.
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
    #
    # `Annotated[..., NoDecode]` plus the validator below (Task 5 review, C1):
    # pydantic-settings normally JSON-decodes a tuple-typed env value BEFORE
    # any field validator runs, so `OPENLAW_TRUSTED_PROXY_IPS=` (empty --
    # exactly what the shipped .env.example had) raised
    # `pydantic_settings.exceptions.SettingsError` from inside the settings
    # SOURCE, unconditionally, for every process that ever constructs
    # `Settings()` -- `cp .env.example .env` broke the entire test suite and
    # `alembic`, before a single line of this project's own code ran.
    # `NoDecode` defers decoding to the validator, which treats a blank
    # value the same way `signup_url`'s default handles "nothing configured"
    # -- as the safe empty tuple -- so this can no longer be reproduced by
    # hand-editing the file, not just by fixing today's shipped value.
    trusted_proxy_ips: Annotated[tuple[str, ...], NoDecode] = ()
    # Explicit acknowledgement that no reverse proxy sits in front of this
    # service (Task 5 review, H3). `production_app` refuses to start unless
    # EITHER `trusted_proxy_ips` above is non-empty OR this is `true` --
    # `trusted_proxy_ips` left at its empty default is otherwise
    # indistinguishable between "no proxy, correctly trusting nothing" and
    # "there IS a proxy and nobody configured it", and the latter fails
    # silently: no crash, no 500, just the fourth visitor of the day getting
    # a normal-looking 429 while every caller behind the proxy shares one
    # rate-limit/anonymous-quota bucket. Forcing the choice at startup turns
    # an easy-to-miss comment into a state the service will not run in
    # unnoticed. See `openlaw.api.deps._require_proxy_acknowledgement`.
    no_reverse_proxy: bool = False
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

    @field_validator("nano_model_dir", mode="before")
    @classmethod
    def _blank_nano_model_dir_means_unset(cls, value: object) -> object:
        """Treat an empty string as `None`, not `Path("")`/`Path(".")`.

        See `nano_model_dir`'s docstring above -- an empty env value is what
        a naive `.env.example` entry with no value looks like, and `pathlib`
        silently normalises `Path("")` to the repository root rather than
        raising, so this has to be caught explicitly.
        """
        if value == "":
            return None
        return value

    @field_validator("trusted_proxy_ips", mode="before")
    @classmethod
    def _decode_trusted_proxy_ips(cls, value: object) -> object:
        """Parse `trusted_proxy_ips` from its raw (undecoded, see `NoDecode`
        above) env-var string, treating blank as the safe empty tuple.

        Still accepts the documented JSON-array syntax (`["1.2.3.4"]`) for
        anything genuinely configured; only the empty-value case is special-
        cased, so a malformed non-empty value still fails loudly with a
        clear parse error rather than being silently swallowed.
        """
        if isinstance(value, str):
            if value.strip() == "":
                return ()
            return tuple(json.loads(value))
        return value


@lru_cache
def get_settings() -> Settings:
    """Return process-wide settings, constructed once."""
    return Settings()
