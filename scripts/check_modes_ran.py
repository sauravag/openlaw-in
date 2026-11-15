"""Fail unless BOTH build modes ran and passed, for BOTH test suites.

Each suite writes one JSON marker per mode when its run ends (pytest:
`tests/conftest.py::pytest_sessionfinish`; vitest:
`web/test-utils/modeMarkerReporter.mjs`) into the directory named by
`OPENLAW_TEST_MODE_MARKER_DIR`. CI runs this as its last step, so a job that
silently stopped running a mode (a deleted step, a mode variable that no
longer takes effect, a run that collected zero tests) fails here instead of
quietly halving coverage.

    python scripts/check_modes_ran.py <marker-dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SUITES = ("python", "vitest")
MODES = ("a", "b")


def check_markers(marker_dir: Path) -> list[str]:
    """Return one problem per missing, unreadable, wrong or failing marker."""
    problems: list[str] = []
    for suite in SUITES:
        for mode in MODES:
            name = f"{suite}-{mode}.json"
            path = marker_dir / name
            if not path.is_file():
                problems.append(f"{name}: missing -- the {suite} suite never ran in Mode {mode.upper()}")
                continue
            try:
                marker = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                problems.append(f"{name}: unreadable ({error})")
                continue
            if marker.get("suite") != suite or marker.get("mode") != mode:
                problems.append(f"{name}: records suite/mode {marker.get('suite')}/{marker.get('mode')}")
            elif marker.get("passed") is not True:
                problems.append(f"{name}: the run did not pass")
            elif not isinstance(marker.get("tests"), int) or marker["tests"] < 1:
                problems.append(f"{name}: no tests were collected")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    problems = check_markers(Path(argv[1]))
    for problem in problems:
        print(f"[check_modes_ran] {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"[check_modes_ran] ok: {len(SUITES) * len(MODES)} marker(s) present and passing")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
