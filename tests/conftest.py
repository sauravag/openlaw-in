from pathlib import Path

import pytest

from openlaw.config import Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True, scope="session")
def _ignore_the_developers_env_file() -> object:
    """Stop every `Settings()` in the suite reading the repo-root `.env`.

    `Settings.model_config` sets `env_file=".env"`, so on a machine with a
    populated `.env` the suite silently tests that machine's deployment
    instead of the declared defaults. It is not environment-variable
    leakage -- clearing the environment does not help, because the file is
    read directly -- so this cannot be fixed with `monkeypatch.delenv`.

    Found when 14 tests began failing after the live deployment was pointed
    at OpenRouter: `test_a_role_can_be_pointed_at_openrouter_without_touching
    _the_others` asserts the translator keeps its `claude-haiku-4-5` default,
    and got the real `.env`'s override. The tests were right and the harness
    was lying.

    A developer's `.env` also holds live credentials, so reading it during
    tests is undesirable regardless of the assertions it breaks.
    Production is unaffected: only the test session is patched.

    Nulling the file alone is not enough: `vaquill_root` and
    `kanoongpt_root` are REQUIRED and have no defaults, so the real `.env`
    was quietly satisfying them and the suite only ran at all on a machine
    with the datasets configured. Synthetic paths are supplied here instead,
    which is the same hermeticity problem seen from the other side.
    """
    patch = pytest.MonkeyPatch()
    synthetic = Path(__file__).parent / "fixtures"
    patch.setenv("OPENLAW_VAQUILL_ROOT", str(synthetic / "vaquill"))
    patch.setenv("OPENLAW_KANOONGPT_ROOT", str(synthetic / "kanoongpt"))

    original = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = None
    yield
    Settings.model_config["env_file"] = original
    patch.undo()


@pytest.fixture
def vaquill_fixture_dir() -> Path:
    """Directory holding sampled vaquill Parquet files."""
    return FIXTURE_DIR / "vaquill"


@pytest.fixture
def kanoongpt_fixture_dir() -> Path:
    """Directory holding sampled KanoonGPT Parquet files."""
    return FIXTURE_DIR / "kanoongpt"
