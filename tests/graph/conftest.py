from collections.abc import Iterator

import neo4j
import pytest

from openlaw.config import get_settings
from openlaw.graph.connection import graph_driver


@pytest.fixture
def live_driver() -> Iterator[neo4j.Driver]:
    """A real Neo4j driver, skipping the test cleanly if unreachable."""
    with graph_driver(get_settings()) as driver:
        try:
            driver.verify_connectivity()
        except neo4j.exceptions.ServiceUnavailable:
            pytest.skip("Neo4j is not reachable at the configured URI")
        yield driver
