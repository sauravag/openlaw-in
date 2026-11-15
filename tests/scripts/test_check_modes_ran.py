"""`scripts/check_modes_ran.py`: CI's proof that every (suite, mode) ran."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_modes_ran import MODES, SUITES, check_markers, main


def _write(directory: Path, suite: str, file_mode: str, **overrides: object) -> None:
    """Write `<suite>-<file_mode>.json`; `mode=` overrides what it records."""
    payload = {"suite": suite, "mode": file_mode, "passed": True, "tests": 10, **overrides}
    (directory / f"{suite}-{file_mode}.json").write_text(json.dumps(payload))


def _write_all(directory: Path) -> None:
    for suite in SUITES:
        for mode in MODES:
            _write(directory, suite, mode)


def test_all_four_passing_markers_is_ok(tmp_path: Path) -> None:
    _write_all(tmp_path)

    assert check_markers(tmp_path) == []
    assert main(["check_modes_ran", str(tmp_path)]) == 0


@pytest.mark.parametrize("suite", SUITES)
@pytest.mark.parametrize("mode", MODES)
def test_a_missing_marker_names_the_suite_and_mode(
    tmp_path: Path, suite: str, mode: str
) -> None:
    _write_all(tmp_path)
    (tmp_path / f"{suite}-{mode}.json").unlink()

    problems = check_markers(tmp_path)

    assert len(problems) == 1
    assert f"{suite}-{mode}.json" in problems[0] and "missing" in problems[0]
    assert main(["check_modes_ran", str(tmp_path)]) == 1


def test_an_empty_directory_fails_all_four(tmp_path: Path) -> None:
    assert len(check_markers(tmp_path)) == 4


def test_a_failed_run_is_a_problem(tmp_path: Path) -> None:
    _write_all(tmp_path)
    _write(tmp_path, "python", "b", passed=False)

    assert any("did not pass" in p for p in check_markers(tmp_path))


def test_a_run_that_collected_nothing_is_a_problem(tmp_path: Path) -> None:
    _write_all(tmp_path)
    _write(tmp_path, "vitest", "a", tests=0)

    assert any("no tests" in p for p in check_markers(tmp_path))


def test_a_marker_recording_the_wrong_mode_is_a_problem(tmp_path: Path) -> None:
    """The Mode B run that quietly ran as Mode A: its marker says `a`."""
    _write_all(tmp_path)
    _write(tmp_path, "python", "b", mode="a")

    assert any("records suite/mode python/a" in p for p in check_markers(tmp_path))


def test_an_unreadable_marker_is_a_problem(tmp_path: Path) -> None:
    _write_all(tmp_path)
    (tmp_path / "python-a.json").write_text("{not json")

    assert any("unreadable" in p for p in check_markers(tmp_path))


def test_wrong_usage_exits_2() -> None:
    assert main(["check_modes_ran"]) == 2
