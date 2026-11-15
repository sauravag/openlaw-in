"""A Docker build can be Mode B, and agrees with the backend by default.

`NEXT_PUBLIC_OPENLAW_AUTH_ENABLED` is inlined at `next build`, so the only way
a containerised frontend is ever Mode B is a build arg. Before Plan 7 Task 15
neither the web Dockerfile nor compose passed one: every image was Mode A
whatever the backend said. Read as text, never `docker compose config` (which
prints secrets).
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_FLAG = "NEXT_PUBLIC_OPENLAW_AUTH_ENABLED"


def test_the_web_dockerfile_declares_the_arg_before_it_builds() -> None:
    lines = (_ROOT / "web" / "Dockerfile").read_text(encoding="utf-8").splitlines()
    code = [line.strip() for line in lines if not line.lstrip().startswith("#")]

    arg = code.index(f"ARG {_FLAG}=false")
    env = code.index(f"ENV {_FLAG}=${_FLAG}")
    build = code.index("RUN npm run build")

    assert arg < env < build


def test_compose_derives_the_web_arg_from_the_variable_the_api_reads() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    web_args = compose["services"]["web"]["build"]["args"]
    api_env = compose["services"]["api"]["environment"]

    assert web_args[_FLAG] == "${OPENLAW_AUTH_ENABLED:-false}"
    assert api_env["OPENLAW_AUTH_ENABLED"] == "${OPENLAW_AUTH_ENABLED:-false}"


def test_verify_selfhost_remedy_names_the_build_arg_compose_really_passes() -> None:
    script = (_ROOT / "scripts" / "verify_selfhost.sh").read_text(encoding="utf-8")
    compose = (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert f"{_FLAG} build arg" in script
    assert f"{_FLAG}: ${{OPENLAW_AUTH_ENABLED:-false}}" in compose
    assert "docker compose build web" in script
