"""Build the legal knowledge graph in Neo4j from the corpus.

Streams the English Supreme Court judgments and every legislation and regulator
file, derives nodes and edges deterministically (see `openlaw.graph.nodes` and
`openlaw.graph.edges`), and merges them in batches via `openlaw.graph.loader`.
Every write is MERGE, so the whole run is idempotent and safe to rerun — the
graph is derived data, dropped and rebuilt from scratch per corpus release
rather than migrated.

Resumable: this is a long run (~1.3M nodes, ~1.9M edges over Bolt), so progress
is checkpointed to `data/graph_checkpoint.json` (git-ignored) after each unit
of work — the whole judgments phase (a single source file, so it checkpoints
as one unit) and each legislation/regulator file. A restart skips whatever the
checkpoint already recorded as loaded rather than re-sending batches that
already landed. Because every write is MERGE, an accidental repeat of a batch
is harmless — the checkpoint saves time, not correctness.

Judgment nodes must be loaded in full, with their `citation_key`, before any
CITES edges are merged: CITES resolves a citation to a real Judgment via
`citation_key` first, falling back to a `join_key` stub only when nothing
matches (see `openlaw.graph.loader` for why `join_key` alone would resolve
almost no citations). That ordering is why the judgments phase always loads
every Judgment node before any judgment-derived edge, and why it is
checkpointed as a single atomic unit rather than split further.

Run:      python scripts/build_graph.py
Restart:  python scripts/build_graph.py --restart
Bounded:  python scripts/build_graph.py --limit 5000   (testing only — caps
          the number of judgment chunks read, and separately caps the total
          number of provision rows read across all legislation/regulator files)
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import neo4j

from openlaw.config import Settings, get_settings
from openlaw.corpus.filters import english_only
from openlaw.corpus.schema import JudgmentChunk
from openlaw.datasets.vaquill import read_judgment_chunks, read_provisions
from openlaw.graph.connection import graph_driver
from openlaw.graph.edges import Edge, EdgeKind, judgment_edges, provision_edges
from openlaw.graph.loader import citation_key_for, load_edges, load_nodes
from openlaw.graph.nodes import (
    act_node,
    court_node,
    judge_nodes,
    judgment_node,
    section_node,
    subject_nodes,
    term_nodes,
)
from openlaw.graph.schema import apply_schema

PROGRESS_EVERY = 50_000
DEFAULT_CHECKPOINT_PATH = Path("data/graph_checkpoint.json")


def default_state(corpus_version: str) -> dict[str, Any]:
    """Return a fresh checkpoint state for `corpus_version`."""
    return {
        "corpus_version": corpus_version,
        "judgments_loaded": False,
        "provision_files_done": [],
    }


def read_checkpoint(path: Path) -> dict[str, Any] | None:
    """Return the checkpoint at `path`, or None when it does not exist."""
    if not path.exists():
        return None
    return json.loads(path.read_text())


def write_checkpoint(path: Path, state: dict[str, Any]) -> None:
    """Atomically persist `state`, so a crash mid-write never corrupts it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(state))
    tmp_path.replace(path)


def clear_checkpoint(path: Path) -> None:
    """Remove the checkpoint at `path`, if any — used by `--restart`."""
    path.unlink(missing_ok=True)


def load_state(checkpoint_path: Path, corpus_version: str) -> dict[str, Any]:
    """Return the checkpoint state to resume from, or a fresh one.

    Raises ValueError when the checkpoint was written for a different
    `corpus_version`: a different snapshot means different rows entirely, so
    the recorded progress would skip or misalign work.
    """
    checkpoint = read_checkpoint(checkpoint_path)
    if checkpoint is None:
        return default_state(corpus_version)
    if checkpoint.get("corpus_version") != corpus_version:
        raise ValueError(
            f"checkpoint corpus_version {checkpoint.get('corpus_version')!r} "
            f"does not match configured corpus_version {corpus_version!r}; "
            "refusing to resume from a different snapshot. Pass --restart to "
            "discard the checkpoint and start over."
        )
    return checkpoint


def judgment_row(chunk: JudgmentChunk) -> dict[str, Any]:
    """Return the Judgment node row for `chunk`, enriched with `citation_key`.

    `judgment_node` (Task 3) has no knowledge of citation resolution — see the
    module docstring in `openlaw.graph.loader` for why CITES edges need this.
    """
    return {**judgment_node(chunk), "citation_key": citation_key_for(chunk.citation)}


def build_judgment_data(
    sc_path: Path, corpus_version: str, limit: int | None
) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict], dict[EdgeKind, list[Edge]]]:
    """Stream English Supreme Court chunks and return their derived nodes/edges."""
    judgments: dict[str, dict] = {}
    courts: dict[str, dict] = {}
    judges: dict[str, dict] = {}
    edges_by_kind: dict[EdgeKind, list[Edge]] = defaultdict(list)
    seen_edges: set[Edge] = set()

    processed = 0
    chunks = english_only(read_judgment_chunks(sc_path, corpus_version=corpus_version))
    for chunk in chunks:
        if limit is not None and processed >= limit:
            break
        processed += 1

        judgments.setdefault(chunk.case_id, judgment_row(chunk))
        courts.setdefault(chunk.court_code, court_node(chunk.court_code))
        for node in judge_nodes(chunk):
            judges.setdefault(node["name"], node)
        for edge in judgment_edges(chunk):
            if edge not in seen_edges:
                seen_edges.add(edge)
                edges_by_kind[edge.kind].append(edge)

        if processed % PROGRESS_EVERY == 0:
            print(f"[Graph] read {processed:,} English SC chunks")

    print(
        f"[Graph] {processed:,} English SC chunks read: "
        f"{len(judgments):,} judgments, {len(courts):,} courts, {len(judges):,} judges"
    )
    return judgments, courts, judges, dict(edges_by_kind)


def build_provision_data(
    path: Path, corpus_version: str, limit: int | None
) -> tuple[
    dict[str, dict], dict[str, dict], dict[str, dict], dict[str, dict],
    dict[EdgeKind, list[Edge]], int,
]:
    """Stream one legislation/regulator file and return its derived nodes/edges."""
    acts: dict[str, dict] = {}
    sections: dict[str, dict] = {}
    terms: dict[str, dict] = {}
    subjects: dict[str, dict] = {}
    edges_by_kind: dict[EdgeKind, list[Edge]] = defaultdict(list)
    seen_edges: set[Edge] = set()

    processed = 0
    for provision in read_provisions(path, corpus_version=corpus_version):
        if limit is not None and processed >= limit:
            break
        processed += 1

        sections.setdefault(provision.chunk_id, section_node(provision))
        acts.setdefault(provision.act_id, act_node(provision))
        for node in term_nodes(provision):
            terms.setdefault(node["term"], node)
        for node in subject_nodes(provision):
            subjects.setdefault(node["name"], node)
        for edge in provision_edges(provision):
            if edge not in seen_edges:
                seen_edges.add(edge)
                edges_by_kind[edge.kind].append(edge)

    return acts, sections, terms, subjects, dict(edges_by_kind), processed


def write_node_batches(driver: neo4j.Driver, labels_and_rows: list[tuple[str, list[dict]]]) -> None:
    """MERGE each label's rows, printing how many distinct nodes were written."""
    for label, rows in labels_and_rows:
        written = load_nodes(driver, label, rows)
        print(f"[Graph] {label}: {written:,} nodes")


def write_edge_batches(driver: neo4j.Driver, edges_by_kind: dict[EdgeKind, list[Edge]]) -> None:
    """MERGE each relationship kind's edges, printing how many were written."""
    for kind, edges in edges_by_kind.items():
        written = load_edges(driver, kind, edges)
        print(f"[Graph] {kind.value}: {written:,} edges")


def run_judgments_phase(
    driver: neo4j.Driver,
    state: dict[str, Any],
    checkpoint_path: Path,
    settings: Settings,
    limit: int | None,
) -> None:
    """Load every Judgment/Court/Judge node and judgment-derived edge.

    A single atomic checkpoint unit: judgments come from one source file, and
    CITES resolution requires every Judgment node (with its citation_key)
    loaded before any CITES edge is merged, so there is no safe finer-grained
    checkpoint boundary here.
    """
    if state["judgments_loaded"]:
        print("[Graph] judgments already loaded (checkpoint)")
        return

    sc_path = settings.vaquill_root / "in_supreme-court_judgments.parquet"
    print(f"[Graph] reading English Supreme Court judgments from {sc_path.name}")
    judgments, courts, judges, edges_by_kind = build_judgment_data(
        sc_path, settings.corpus_version, limit
    )

    write_node_batches(
        driver,
        [
            ("Court", list(courts.values())),
            ("Judge", list(judges.values())),
            ("Judgment", list(judgments.values())),
        ],
    )
    write_edge_batches(driver, edges_by_kind)

    state["judgments_loaded"] = True
    write_checkpoint(checkpoint_path, state)


def run_provisions_phase(
    driver: neo4j.Driver,
    state: dict[str, Any],
    checkpoint_path: Path,
    settings: Settings,
    limit: int | None,
) -> None:
    """Load every legislation/regulator file not yet recorded as done.

    Streamed and checkpointed per file: each file's rows are read, merged, and
    recorded as done before moving to the next, so an interrupted run resumes
    at the next unfinished file instead of restarting.
    """
    paths = sorted(settings.vaquill_root.glob("in_*_legislation.parquet")) + sorted(
        settings.vaquill_root.glob("in_*_regulations.parquet")
    )
    remaining = limit
    for path in paths:
        if path.name in state["provision_files_done"]:
            continue
        if remaining is not None and remaining <= 0:
            print("[Graph] limit reached, stopping provisions phase")
            break

        acts, sections, terms, subjects, edges_by_kind, processed = build_provision_data(
            path, settings.corpus_version, remaining
        )
        print(f"[Graph] {path.name}: {processed:,} provisions read")

        write_node_batches(
            driver,
            [
                ("Act", list(acts.values())),
                ("Section", list(sections.values())),
                ("DefinedTerm", list(terms.values())),
                ("LegalSubject", list(subjects.values())),
            ],
        )
        write_edge_batches(driver, edges_by_kind)

        state["provision_files_done"].append(path.name)
        write_checkpoint(checkpoint_path, state)

        if remaining is not None:
            remaining -= processed


def report_cites_resolution(driver: neo4j.Driver) -> None:
    """Print how many CITES edges resolved to a real Judgment vs a stub.

    This is the number Task 6's PageRank quality hinges on — see the Task 5
    brief addendum on why `citation_key` resolution replaced `join_key` alone.
    """
    query = (
        "MATCH ()-[:CITES]->(target:Judgment) "
        "RETURN target.case_id IS NOT NULL AS resolved, count(*) AS n"
    )
    with driver.session() as session:
        counts = {row["resolved"]: row["n"] for row in session.run(query)}
    resolved, stub = counts.get(True, 0), counts.get(False, 0)
    print(f"[Graph] CITES resolved: {resolved:,}, stub: {stub:,}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for this script."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--restart",
        action="store_true",
        help="ignore and clear any existing checkpoint, and start over",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "process at most N judgment chunks, and separately at most N "
            "total provision rows across all files — for testing only"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()

    checkpoint_path = DEFAULT_CHECKPOINT_PATH
    if args.restart:
        print("[Graph] --restart: clearing any existing checkpoint")
        clear_checkpoint(checkpoint_path)

    state = load_state(checkpoint_path, settings.corpus_version)

    with graph_driver(settings) as driver:
        print(f"[Graph] applied {apply_schema(driver)} schema statements")

        run_judgments_phase(driver, state, checkpoint_path, settings, args.limit)
        run_provisions_phase(driver, state, checkpoint_path, settings, args.limit)
        report_cites_resolution(driver)

    print("[Graph] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
