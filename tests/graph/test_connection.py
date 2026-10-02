from unittest.mock import MagicMock

import neo4j
import pytest

from openlaw.config import Settings
from openlaw.graph.connection import graph_driver


def test_graph_driver_builds_driver_from_settings(monkeypatch) -> None:
    """The driver must be built from the URI and credentials in settings."""
    mock_driver = MagicMock(spec=neo4j.Driver)
    captured: dict[str, object] = {}

    def fake_driver(uri: str, auth: tuple[str, str]) -> neo4j.Driver:
        captured["uri"] = uri
        captured["auth"] = auth
        return mock_driver

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", fake_driver)

    settings = Settings(
        vaquill_root="/tmp/v",
        kanoongpt_root="/tmp/k",
        neo4j_uri="bolt://example:7687",
        neo4j_user="someuser",
        neo4j_password="somepassword",
    )

    with graph_driver(settings) as driver:
        assert driver is mock_driver

    assert captured["uri"] == "bolt://example:7687"
    assert captured["auth"] == ("someuser", "somepassword")
    mock_driver.close.assert_called_once()


def test_graph_driver_closes_the_driver_even_on_error(monkeypatch) -> None:
    """The driver must be closed even when the caller's block raises."""
    mock_driver = MagicMock(spec=neo4j.Driver)
    monkeypatch.setattr(
        neo4j.GraphDatabase, "driver", lambda uri, auth: mock_driver
    )
    settings = Settings(vaquill_root="/tmp/v", kanoongpt_root="/tmp/k")

    with pytest.raises(RuntimeError, match="boom"):
        with graph_driver(settings):
            raise RuntimeError("boom")

    mock_driver.close.assert_called_once()


@pytest.mark.integration
def test_graph_driver_connects_to_a_live_neo4j(live_driver: neo4j.Driver) -> None:
    """Smoke test against the running container; skips if unreachable."""
    live_driver.verify_connectivity()
