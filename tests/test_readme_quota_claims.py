"""README's quota check states the real bound (final review M2).

The anonymous allowance is keyed by the `openlaw_anon_id` cookie, which the
backend mints afresh for any request without one. A script that drops cookies
therefore never exhausts it; only the per-IP burst limiter bounds it. The
README must say so, with the limiter's actual numbers.
"""

from __future__ import annotations

from pathlib import Path

from openlaw.api import routes

_README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")


def test_readme_states_the_burst_limit_from_the_constants() -> None:
    per_window = routes._RATE_LIMIT_MAX_REQUESTS
    seconds = int(routes._RATE_LIMIT_WINDOW_SECONDS)
    assert seconds == 60, "the README states the limit per minute"
    per_day = per_window * (24 * 60 * 60 // seconds)

    assert f"{per_window} requests per minute per IP" in _README
    assert f"{per_day:,} questions a day" in _README


def test_readme_says_the_anonymous_cap_is_per_cookie_and_scripts_bypass_it() -> None:
    assert "per cookie" in _README
    assert "does not store cookies" in _README
    assert "If you never get a 429, quota is off and your API key is uncapped." not in _README
