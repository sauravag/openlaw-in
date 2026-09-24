"""Run the GDS precomputes: PageRank authority and Louvain doctrinal clusters.

Run after scripts/build_graph.py, once per corpus release.
Run: python scripts/run_gds.py

See `openlaw.graph.algorithms` for the caveats this output should be read
with: citation-key collisions (Task 5) mean a small number of judgments are
under-ranked because their inbound citations were attributed to a colliding
case, and a large share of high-PageRank nodes may be stub `Judgment` nodes
for cases outside this corpus rather than judgments with retrievable text.
"""

import sys

from openlaw.config import get_settings
from openlaw.graph.algorithms import (
    CITATION_PROJECTION,
    drop_projection,
    most_authoritative,
    project_citation_graph,
    run_louvain,
    run_pagerank,
)
from openlaw.graph.connection import graph_driver


def main() -> int:
    with graph_driver(get_settings()) as driver:
        drop_projection(driver, CITATION_PROJECTION)

        print(f"[GDS] projected {project_citation_graph(driver):,} Judgment nodes")
        print(f"[GDS] PageRank wrote {run_pagerank(driver):,} properties")
        print(f"[GDS] Louvain wrote {run_louvain(driver):,} properties")

        print("[GDS] most authoritative judgments:")
        for row in most_authoritative(driver):
            print(f"    {row['case_id']:<24} {row['pagerank']:.4f}")

        drop_projection(driver, CITATION_PROJECTION)

    return 0


if __name__ == "__main__":
    sys.exit(main())
