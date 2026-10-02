"""Run the GDS precomputes: PageRank authority and Louvain doctrinal clusters.

Run after scripts/build_graph.py, once per corpus release.
Run: python scripts/run_gds.py

See `openlaw.graph.algorithms` for the caveats this output should be read
with: citation-key collisions mean a small number of judgments are
under-ranked because their inbound citations were attributed to a colliding
case, and a large share of high-PageRank nodes are stub `Judgment` nodes for
cases outside this corpus rather than judgments with retrievable text.

Those stubs are why the ranking below is rendered through
`algorithms.authority_label` rather than formatted straight from `case_id`: a
stub has no `case_id`, and `f"{None:<24}"` raises TypeError, so this script
used to die on its last line on entirely expected data. A stub row now prints
the citation string it was created from, marked `(stub)`.
"""

import sys

from openlaw.config import get_settings
from openlaw.graph.algorithms import (
    CITATION_PROJECTION,
    authority_label,
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
            print(f"    {authority_label(row):<34} {row['pagerank']:.4f}")

        drop_projection(driver, CITATION_PROJECTION)

    return 0


if __name__ == "__main__":
    sys.exit(main())
