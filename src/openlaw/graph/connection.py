"""Neo4j driver lifecycle.

The graph is reached over Bolt as a separate service. Neo4j Community Edition is
GPLv3; keeping it behind a network protocol is what lets OpenLaw's own code carry
a permissive licence.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import neo4j

from openlaw.config import Settings


@contextmanager
def graph_driver(settings: Settings) -> Iterator[neo4j.Driver]:
    """Yield a Neo4j driver built from `settings`, closing it on exit."""
    driver = neo4j.GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    try:
        yield driver
    finally:
        driver.close()
