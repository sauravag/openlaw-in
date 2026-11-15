"""The CI workflow must keep running both modes of both suites.

A flag exercised in only one state breaks quietly in the other, and a
workflow is the easiest place to lose a state: delete a matrix entry, a step,
a variable. This test fails when that happens, and `scripts/check_modes_ran.py`
(the workflow's last job) fails the CI run itself when a mode did not run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "test.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _steps(job: dict) -> list[dict]:
    return job["steps"]


def _run_text(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(job))


@pytest.mark.parametrize("job_name", ["python", "vitest", "web-build"])
def test_the_job_runs_both_modes(workflow: dict, job_name: str) -> None:
    matrix = workflow["jobs"][job_name]["strategy"]["matrix"]

    assert sorted(matrix["mode"]) == ["a", "b"]


def test_pytest_receives_the_matrix_mode(workflow: dict) -> None:
    job = workflow["jobs"]["python"]
    pytest_steps = [s for s in _steps(job) if "pytest" in str(s.get("run", ""))]

    assert len(pytest_steps) == 1
    assert pytest_steps[0]["env"]["OPENLAW_TEST_MODE"] == "${{ matrix.mode }}"


def test_vitest_receives_the_flag_from_the_matrix_mode(workflow: dict) -> None:
    job = workflow["jobs"]["vitest"]
    steps = [s for s in _steps(job) if "test:coverage" in str(s.get("run", ""))]

    assert len(steps) == 1
    flag = steps[0]["env"]["NEXT_PUBLIC_OPENLAW_AUTH_ENABLED"]
    assert "matrix.mode == 'b'" in flag and "'true'" in flag and "'false'" in flag


def test_the_mode_a_build_leaves_the_flag_unset_and_checks_the_bundle(
    workflow: dict,
) -> None:
    steps = _steps(workflow["jobs"]["web-build"])
    build_a = [s for s in steps if s.get("run") == "npm run build" and "'a'" in s["if"]]
    check = [s for s in steps if s.get("run") == "npm run check:mode-a-bundle"]

    assert len(build_a) == 1 and "env" not in build_a[0]
    assert len(check) == 1 and "'a'" in check[0]["if"]


def test_the_mode_b_build_sets_the_flag(workflow: dict) -> None:
    steps = _steps(workflow["jobs"]["web-build"])
    build_b = [s for s in steps if s.get("run") == "npm run build" and "'b'" in s["if"]]

    assert len(build_b) == 1
    assert build_b[0]["env"]["NEXT_PUBLIC_OPENLAW_AUTH_ENABLED"] == "true"


def test_a_final_job_requires_every_suite_to_have_run_in_every_mode(
    workflow: dict,
) -> None:
    job = workflow["jobs"]["both-modes-ran"]

    assert {"python", "vitest", "web-build"} <= set(job["needs"])
    assert "scripts/check_modes_ran.py" in _run_text(job)
    assert job["if"] == "always()"


@pytest.mark.parametrize("suite", ["python", "vitest"])
def test_each_suite_uploads_its_markers_even_when_it_fails(
    workflow: dict, suite: str
) -> None:
    uploads = [
        s for s in _steps(workflow["jobs"][suite])
        if str(s.get("uses", "")).startswith("actions/upload-artifact")
    ]

    assert len(uploads) == 1
    assert uploads[0]["if"] == "always()"
    assert suite in uploads[0]["with"]["name"]


def test_the_live_postgres_tests_get_the_database_they_expect(workflow: dict) -> None:
    postgres = workflow["jobs"]["python"]["services"]["postgres"]

    assert postgres["env"]["POSTGRES_DB"] == "openlaw_test"
    assert postgres["env"]["POSTGRES_USER"] == "openlaw"
    assert "5432:5432" in postgres["ports"]


def test_the_workflow_holds_no_secret() -> None:
    assert "secrets." not in _WORKFLOW.read_text(encoding="utf-8")


def test_the_default_test_database_url_matches_the_service() -> None:
    from tests.conftest import _DEFAULT_TEST_DATABASE_URL

    assert _DEFAULT_TEST_DATABASE_URL.endswith("openlaw:openlaw@localhost:5432/openlaw_test")
