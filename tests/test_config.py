import re
from pathlib import Path

from openlaw.config import Settings


def test_settings_read_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")
    monkeypatch.setenv("OPENLAW_QDRANT_URL", "http://localhost:6333")

    settings = Settings()

    assert settings.vaquill_root == Path("/tmp/vaquill")
    assert settings.kanoongpt_root == Path("/tmp/kanoon")
    assert settings.qdrant_url == "http://localhost:6333"


def test_corpus_version_defaults_to_snapshot(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")

    assert Settings().corpus_version == "v2026.08"


def test_query_embedder_backend_defaults_to_local_nano(monkeypatch) -> None:
    """Binding decision (Plan 3 T1, measured): local nano, not the Voyage API."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")

    assert Settings().query_embedder_backend == "local_nano"


def test_nano_model_dir_defaults_to_none(monkeypatch) -> None:
    """Unset means fetch from the Hugging Face Hub on demand."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")

    assert Settings().nano_model_dir is None


def test_nano_model_dir_reads_a_configured_local_path(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")
    monkeypatch.setenv("OPENLAW_NANO_MODEL_DIR", "/models/voyage-4-nano")

    assert Settings().nano_model_dir == Path("/models/voyage-4-nano")


def test_graph_expansion_is_disabled_by_default(monkeypatch) -> None:
    """Binding decision (Plan 3 T3, fix round 2, measured): the statute-to-
    case-law bridge is too sparse and REFERENCES fans out through shared-Act
    supernodes, so every added chunk being citable makes ON the unsafe
    default -- see openlaw.retrieval.rerank's module docstring."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")

    assert Settings().graph_expansion_enabled is False


def test_graph_expansion_can_be_enabled_via_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")
    monkeypatch.setenv("OPENLAW_GRAPH_EXPANSION_ENABLED", "true")

    assert Settings().graph_expansion_enabled is True


def test_model_defaults_match_the_per_role_spec(monkeypatch) -> None:
    """Binding decision (Plan 3 T4): the generator needs frontier reasoning for
    grounded legal answers; translation and classification do not, and paying
    frontier prices for them is waste at the product's projected scale."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")

    settings = Settings()

    assert settings.model_generator == "claude-opus-5"
    assert settings.model_translator == "claude-haiku-4-5"
    assert settings.model_classifier == "claude-haiku-4-5"


def test_model_roles_can_be_overridden_independently(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")
    monkeypatch.setenv("OPENLAW_MODEL_GENERATOR", "claude-sonnet-5")

    settings = Settings()

    assert settings.model_generator == "claude-sonnet-5"
    assert settings.model_translator == "claude-haiku-4-5"


def test_anthropic_api_key_defaults_to_none(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")

    assert Settings().anthropic_api_key is None


def test_anthropic_api_key_reads_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")
    monkeypatch.setenv("OPENLAW_ANTHROPIC_API_KEY", "sk-ant-test")

    assert Settings().anthropic_api_key == "sk-ant-test"


def test_hallucination_log_path_has_a_default(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")

    assert Settings().hallucination_log_path == Path("data/hallucinations.jsonl")


def test_hallucination_log_path_is_configurable_via_environment(monkeypatch) -> None:
    """Plan 3 T7 fix round 1: the path must not be hard-coded at a call
    site, because the log can carry verbatim, sensitive user text and a
    deployment may need it on a restricted-access volume."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/vaquill")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/kanoon")
    monkeypatch.setenv("OPENLAW_HALLUCINATION_LOG_PATH", "/secure/hallucinations.jsonl")

    assert Settings().hallucination_log_path == Path("/secure/hallucinations.jsonl")


def test_classifier_failure_log_path_has_a_default(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")

    assert Settings().classifier_failure_log_path == Path(
        "data/classifier_failures.jsonl"
    )


def test_classifier_failure_log_path_is_configurable_via_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")
    monkeypatch.setenv(
        "OPENLAW_CLASSIFIER_FAILURE_LOG_PATH", "/secure/classifier_failures.jsonl"
    )

    assert Settings().classifier_failure_log_path == Path(
        "/secure/classifier_failures.jsonl"
    )


def test_cookie_secure_defaults_to_true(monkeypatch) -> None:
    """The safe default for any real deployment, which is reached over
    HTTPS regardless -- see `Settings.cookie_secure`'s docstring."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")

    assert Settings().cookie_secure is True


def test_cookie_secure_can_be_disabled_for_plain_http_local_development(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")
    monkeypatch.setenv("OPENLAW_COOKIE_SECURE", "false")

    assert Settings().cookie_secure is False


# --- Task 5 review, C1: the shipped .env.example must actually parse -------


def test_env_example_constructs_settings_without_error() -> None:
    """`cp .env.example .env` is the documented first step (README's Setup
    section) -- if pydantic-settings cannot parse this file, every test,
    `alembic`, and the service itself fail before a single line of this
    project's own code runs (Task 5 review, C1: a blank
    `OPENLAW_TRUSTED_PROXY_IPS=` shipped exactly that break). This
    constructs `Settings` from the real, checked-in `.env.example` -- not a
    hand-typed stand-in -- so a future setting with a broken example value
    fails this test immediately instead of shipping invisible."""
    example_path = Path(__file__).resolve().parent.parent / ".env.example"
    assert example_path.exists()

    settings = Settings(_env_file=example_path)  # type: ignore[call-arg]

    assert settings.trusted_proxy_ips == ()
    assert settings.nano_model_dir is None


# --- Task 7 review, C2: .env.example must agree with docker-compose.yml's
# own self-host defaults, not silently override them ------------------------


def test_env_example_agrees_with_compose_self_host_defaults() -> None:
    """`docker compose` auto-loads `.env` for `${VAR:-default}`
    interpolation, so a `.env.example` value that disagrees with
    `docker-compose.yml`'s own fallback silently wins the moment someone
    runs the documented `cp .env.example .env` -- exactly what shipped
    `OPENLAW_QUOTA_ENABLED=true` and `OPENLAW_COOKIE_SECURE=true` into a
    compose file whose own defaults were `false` (Task 7 review, C2): every
    self-hoster who followed the README got the hosted tier's rate limit
    turned on, and a `Secure` cookie that silently never round-trips over
    the stack's own plain-HTTP default, disabling anonymous-quota tracking
    entirely with no error. This locks the two files together so a future
    edit to either one, without the other, fails here instead of shipping
    invisible.
    """
    root = Path(__file__).resolve().parent.parent
    compose_text = (root / "docker-compose.yml").read_text()
    example_settings = Settings(_env_file=root / ".env.example")  # type: ignore[call-arg]

    for var, attr in [
        ("OPENLAW_QUOTA_ENABLED", "quota_enabled"),
        ("OPENLAW_COOKIE_SECURE", "cookie_secure"),
    ]:
        match = re.search(r"\$\{" + var + r":-(\w+)\}", compose_text)
        assert match, f"{var} has no ${{...:-default}} fallback in docker-compose.yml"
        compose_default = match.group(1) == "true"
        example_value = getattr(example_settings, attr)
        assert example_value == compose_default, (
            f"{var}: .env.example resolves to {example_value!r} but "
            f"docker-compose.yml's own fallback default is {compose_default!r} "
            "-- `cp .env.example .env` would silently override the compose "
            "default the moment someone follows the README"
        )


def test_trusted_proxy_ips_blank_value_means_no_trusted_proxies(monkeypatch) -> None:
    """A blank `OPENLAW_TRUSTED_PROXY_IPS=` (not merely absent -- present
    and empty, exactly what the shipped .env.example had before the Task 5
    review's C1 fix) must resolve to the safe empty tuple, not raise
    `SettingsError` -- see the `_decode_trusted_proxy_ips` validator."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")
    monkeypatch.setenv("OPENLAW_TRUSTED_PROXY_IPS", "")

    assert Settings().trusted_proxy_ips == ()


def test_trusted_proxy_ips_reads_the_documented_json_array_syntax(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")
    monkeypatch.setenv("OPENLAW_TRUSTED_PROXY_IPS", '["172.18.0.1","10.0.0.5"]')

    assert Settings().trusted_proxy_ips == ("172.18.0.1", "10.0.0.5")


def test_nano_model_dir_blank_value_means_unset(monkeypatch) -> None:
    """A blank `OPENLAW_NANO_MODEL_DIR=` must resolve to `None`, not
    `Path(".")` -- `pathlib` silently normalises an empty string to the
    current directory, which `prepare_local_model_dir` would then treat as
    a configured model directory and `rglob` (Task 5 review, M2)."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")
    monkeypatch.setenv("OPENLAW_NANO_MODEL_DIR", "")

    assert Settings().nano_model_dir is None


def test_no_reverse_proxy_defaults_to_false(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")

    assert Settings().no_reverse_proxy is False


def test_no_reverse_proxy_can_be_set_true(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")
    monkeypatch.setenv("OPENLAW_NO_REVERSE_PROXY", "true")

    assert Settings().no_reverse_proxy is True


# --- provider API keys and per-role provider routing -----------------------


def test_openrouter_key_is_read_from_the_unprefixed_variable(monkeypatch) -> None:
    """`OPENROUTER_API_KEY` is OpenRouter's own documented variable name, so
    it is read verbatim rather than requiring an `OPENLAW_` rename of a key
    the operator already has (see `Settings.openrouter_api_key`)."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-example")

    assert Settings().openrouter_api_key == "sk-or-example"


def test_the_documented_unprefixed_name_wins_over_the_prefixed_one(monkeypatch) -> None:
    """`populate_by_name` leaves `OPENLAW_OPENROUTER_API_KEY` working too,
    which is harmless -- but the name OpenRouter documents, and the one
    already in the operator's `.env`, must be the one that takes effect.
    If `validation_alias` were dropped this would silently invert, and a
    correctly-configured deployment would read as a missing key."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")
    monkeypatch.setenv("OPENROUTER_API_KEY", "documented-name")
    monkeypatch.setenv("OPENLAW_OPENROUTER_API_KEY", "prefixed-name")

    assert Settings(_env_file=None).openrouter_api_key == "documented-name"  # type: ignore[call-arg]


def test_openrouter_key_can_still_be_passed_by_field_name() -> None:
    """`populate_by_name` -- an aliased field must stay constructible in
    process, which is how every test and caller builds `Settings`."""
    settings = Settings(
        vaquill_root="/tmp/v", kanoongpt_root="/tmp/k", openrouter_api_key="k"
    )

    assert settings.openrouter_api_key == "k"


def test_a_role_can_be_pointed_at_openrouter_without_touching_the_others(
    monkeypatch,
) -> None:
    """The mixed configuration this feature exists for."""
    monkeypatch.setenv("OPENLAW_VAQUILL_ROOT", "/tmp/v")
    monkeypatch.setenv("OPENLAW_KANOONGPT_ROOT", "/tmp/k")
    monkeypatch.setenv("OPENLAW_MODEL_GENERATOR", "openrouter:z-ai/glm-5.3-flash")

    settings = Settings()

    assert settings.model_generator == "openrouter:z-ai/glm-5.3-flash"
    assert settings.model_translator == "claude-haiku-4-5"
    assert settings.model_classifier == "claude-haiku-4-5"


def test_env_example_ships_no_provider_keys() -> None:
    """`cp .env.example .env` must never hand anyone a credential, and must
    not look like one is configured when none is."""
    root = Path(__file__).resolve().parent.parent
    example = Settings(_env_file=root / ".env.example")  # type: ignore[call-arg]

    assert not example.anthropic_api_key
    assert not example.openrouter_api_key


def test_env_example_model_roles_stay_unprefixed_and_therefore_anthropic() -> None:
    """The backwards-compatibility guarantee, asserted on the shipped file:
    the documented example must keep resolving to the Anthropic models it
    always did, so `cp .env.example .env` changes no existing behaviour."""
    root = Path(__file__).resolve().parent.parent
    example = Settings(_env_file=root / ".env.example")  # type: ignore[call-arg]

    for model in (
        example.model_generator,
        example.model_translator,
        example.model_classifier,
    ):
        assert ":" not in model


def test_compose_passes_every_role_model_and_both_provider_keys() -> None:
    """A setting the `api` container never receives is unreachable in a
    Compose deployment however well it works locally -- the per-role model
    variables were exactly that before this change. Also pins the removal of
    compose's `${...:?}` hard-fail on the Anthropic key, which would
    otherwise block an all-OpenRouter deployment that has no Anthropic key
    (the key check now lives in `openlaw.api.deps._require_provider_keys`,
    which can see which providers are actually referenced)."""
    root = Path(__file__).resolve().parent.parent
    compose_text = (root / "docker-compose.yml").read_text()

    for var in (
        "OPENLAW_MODEL_GENERATOR",
        "OPENLAW_MODEL_TRANSLATOR",
        "OPENLAW_MODEL_CLASSIFIER",
        "OPENLAW_ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        assert f"{var}: ${{{var}" in compose_text, f"{var} is not passed to `api`"

    assert "OPENLAW_ANTHROPIC_API_KEY:?" not in compose_text
