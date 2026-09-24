"""Build the legal knowledge graph in Neo4j from the corpus.

Streams every legislation and regulator file and the English Supreme Court
judgments, derives nodes and edges deterministically (see `openlaw.graph.nodes`
and `openlaw.graph.edges`), and merges them in batches via
`openlaw.graph.loader`.

**The graph is derived data, dropped and rebuilt from scratch per corpus
release, never migrated.** A fresh run (no checkpoint, or `--restart`) really
does drop it first — `openlaw.graph.loader.drop_graph`, batched so a ~1.3M-node
graph does not exhaust the heap. Nothing did that before, so a section repealed
between releases survived into the next one, which is a correctness problem for
a legal product. A *resumed* run (a checkpoint exists and matches the corpus
version) does not drop, because dropping is precisely what it is resuming past.

## Phase order, and why it is what it is

1. **Provision nodes** — Act, Section, DefinedTerm, LegalSubject, plus the
   edges whose endpoints are already node keys (PART_OF, DEFINES, HAS_SUBJECT).
2. **Act index** — read back from the graph, so it names exactly the acts this
   build actually loaded, and is rebuilt identically on a resumed run.
3. **Provision REFERENCES edges** — a second pass over the same files. It is a
   separate pass because an act referenced from one file is usually defined in
   another, so resolution is only order-independent once every Act node exists.
   Re-reading the provisions costs ~9s at full corpus scale.
4. **Judgments** — Judgment/Court/Judge nodes, then CITES, INTERPRETS,
   DECIDED_BY, IN_COURT.

Judgments run **last**, and that ordering is load-bearing. INTERPRETS resolves
"section 138 of the Negotiable Instruments Act" to a real Section, which
requires every Section and Act to be in the graph already. When judgments ran
first, INTERPRETS could only ever attach to placeholders, real Sections
received none, and the statute-to-case-law bridge the product depends on was
empty. Within the judgments phase, every Judgment node is loaded (with its
`citation_key`) before any CITES edge, because CITES resolves against
`citation_key` first.

Resumable: this is a long run (~1.3M nodes, ~1.9M edges over Bolt), so progress
is checkpointed to `data/graph_checkpoint.json` (git-ignored) after each unit of
work — each legislation/regulator file per phase, and the judgments phase as one
unit. Because every write is MERGE, an accidental repeat of a batch is harmless:
the checkpoint saves time, not correctness.

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
from collections.abc import Callable
from pathlib import Path
from typing import Any

import neo4j

from openlaw.config import Settings, get_settings
from openlaw.corpus.filters import english_only
from openlaw.datasets.vaquill import read_judgment_chunks, read_provisions
from openlaw.graph.connection import graph_driver
from openlaw.graph.edges import Edge, EdgeKind, judgment_edges, provision_edges
from openlaw.graph.loader import drop_graph, load_edges, load_nodes
from openlaw.graph.nodes import (
    act_node,
    court_node,
    judge_nodes,
    judgment_node,
    section_node,
    subject_nodes,
    term_nodes,
)
from openlaw.graph.resolve import EMPTY_ACT_INDEX, ActIndex, load_act_index
from openlaw.graph.schema import apply_schema

PROGRESS_EVERY = 50_000
DEFAULT_CHECKPOINT_PATH = Path("data/graph_checkpoint.json")

# Edges derivable from a provision whose target is already a node key, so they
# need no act-title resolution and can be written in the first pass.
UNRESOLVED_PROVISION_KINDS = (EdgeKind.PART_OF, EdgeKind.DEFINES, EdgeKind.HAS_SUBJECT)

ProvisionHandler = Callable[[neo4j.Driver, Path, Settings, int | None], int]


def default_state(corpus_version: str) -> dict[str, Any]:
    """Return a fresh checkpoint state for `corpus_version`."""
    return {
        "corpus_version": corpus_version,
        "provision_node_files_done": [],
        "provision_edge_files_done": [],
        "judgments_loaded": False,
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


def provision_paths(settings: Settings) -> list[Path]:
    """Return every legislation and regulator file, in a stable order."""
    return sorted(settings.vaquill_root.glob("in_*_legislation.parquet")) + sorted(
        settings.vaquill_root.glob("in_*_regulations.parquet")
    )


def write_node_batches(
    driver: neo4j.Driver, labels_and_rows: list[tuple[str, list[dict]]]
) -> None:
    """MERGE each label's rows, printing how many distinct nodes were written."""
    for label, rows in labels_and_rows:
        written = load_nodes(driver, label, rows)
        print(f"[Graph] {label}: {written:,} nodes")


def write_edge_batches(
    driver: neo4j.Driver,
    edges_by_kind: dict[EdgeKind, list[Edge]],
    act_index: ActIndex = EMPTY_ACT_INDEX,
) -> None:
    """MERGE each relationship kind's edges, printing how many were written."""
    for kind, edges in edges_by_kind.items():
        written = load_edges(driver, kind, edges, act_index=act_index)
        print(f"[Graph] {kind.value}: {written:,} edges")


# --- phase 1: provision nodes -------------------------------------------------


def build_provision_nodes(
    path: Path, corpus_version: str, limit: int | None
) -> tuple[dict[str, list[dict]], dict[EdgeKind, list[Edge]], int]:
    """Return one file's node rows, its resolution-free edges, and the row count."""
    nodes: dict[str, dict[str, dict]] = {
        "Act": {}, "Section": {}, "DefinedTerm": {}, "LegalSubject": {}
    }
    edges_by_kind: dict[EdgeKind, list[Edge]] = defaultdict(list)
    seen_edges: set[Edge] = set()

    processed = 0
    for provision in read_provisions(path, corpus_version=corpus_version):
        if limit is not None and processed >= limit:
            break
        processed += 1

        nodes["Section"].setdefault(provision.chunk_id, section_node(provision))
        nodes["Act"].setdefault(provision.act_id, act_node(provision))
        for node in term_nodes(provision):
            nodes["DefinedTerm"].setdefault(node["term"], node)
        for node in subject_nodes(provision):
            nodes["LegalSubject"].setdefault(node["name"], node)
        for edge in provision_edges(provision):
            if edge.kind in UNRESOLVED_PROVISION_KINDS and edge not in seen_edges:
                seen_edges.add(edge)
                edges_by_kind[edge.kind].append(edge)

    rows = {label: list(by_key.values()) for label, by_key in nodes.items()}
    return rows, dict(edges_by_kind), processed


def load_provision_nodes(
    driver: neo4j.Driver, path: Path, settings: Settings, limit: int | None
) -> int:
    """Load one file's provision nodes and resolution-free edges."""
    rows, edges_by_kind, processed = build_provision_nodes(
        path, settings.corpus_version, limit
    )
    write_node_batches(driver, list(rows.items()))
    write_edge_batches(driver, edges_by_kind)
    return processed


# --- phase 3: provision REFERENCES edges --------------------------------------


def build_reference_edges(
    path: Path, corpus_version: str, limit: int | None
) -> tuple[list[Edge], int]:
    """Return one file's REFERENCES edges, deduplicated, and the row count."""
    edges: list[Edge] = []
    seen: set[Edge] = set()

    processed = 0
    for provision in read_provisions(path, corpus_version=corpus_version):
        if limit is not None and processed >= limit:
            break
        processed += 1
        for edge in provision_edges(provision):
            if edge.kind is EdgeKind.REFERENCES and edge not in seen:
                seen.add(edge)
                edges.append(edge)
    return edges, processed


def load_reference_edges(
    driver: neo4j.Driver,
    path: Path,
    settings: Settings,
    limit: int | None,
    act_index: ActIndex,
) -> int:
    """Load one file's REFERENCES edges, resolved against `act_index`."""
    edges, processed = build_reference_edges(path, settings.corpus_version, limit)
    write_edge_batches(driver, {EdgeKind.REFERENCES: edges}, act_index)
    return processed


# --- phase driver shared by both provision passes ------------------------------


def run_provision_phase(
    driver: neo4j.Driver,
    state: dict[str, Any],
    checkpoint_path: Path,
    settings: Settings,
    limit: int | None,
    done_key: str,
    handle: ProvisionHandler,
) -> None:
    """Run `handle` over every provision file not yet recorded under `done_key`.

    Checkpointed per file: each file's rows are read, merged, and recorded as
    done before the next, so an interrupted run resumes at the next unfinished
    file instead of restarting.

    `setdefault` is what lets a checkpoint written before the provision phases
    were split (one `provision_files_done` key) resume instead of raising
    KeyError. It resumes as if no provision work were done, which is safe:
    every write is MERGE.
    """
    state.setdefault(done_key, [])
    remaining = limit
    for path in provision_paths(settings):
        if path.name in state[done_key]:
            continue
        if remaining is not None and remaining <= 0:
            print(f"[Graph] limit reached, stopping {done_key}")
            break

        processed = handle(driver, path, settings, remaining)
        print(f"[Graph] {path.name}: {processed:,} provisions read ({done_key})")

        state[done_key].append(path.name)
        write_checkpoint(checkpoint_path, state)
        if remaining is not None:
            remaining -= processed


# --- phase 4: judgments --------------------------------------------------------


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

        judgments.setdefault(chunk.case_id, judgment_node(chunk))
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


def run_judgments_phase(
    driver: neo4j.Driver,
    state: dict[str, Any],
    checkpoint_path: Path,
    settings: Settings,
    limit: int | None,
    act_index: ActIndex = EMPTY_ACT_INDEX,
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
    write_edge_batches(driver, edges_by_kind, act_index)

    state["judgments_loaded"] = True
    write_checkpoint(checkpoint_path, state)


# --- reporting ------------------------------------------------------------------


def report_cites_resolution(driver: neo4j.Driver) -> None:
    """Print how many CITES edges resolved to a real Judgment vs a stub."""
    query = (
        "MATCH ()-[:CITES]->(target:Judgment) "
        "RETURN NOT target:Stub AS resolved, count(*) AS n"
    )
    with driver.session() as session:
        counts = {row["resolved"]: row["n"] for row in session.run(query)}
    print(
        f"[Graph] CITES resolved: {counts.get(True, 0):,}, "
        f"stub: {counts.get(False, 0):,}"
    )


def report_edge_counts(driver: neo4j.Driver) -> None:
    """Print the per-type edge counts, split by real vs stub target.

    This is the release-verification number. A relationship count far above
    the plan's budget means an endpoint is being merged on a non-unique
    property and is fanning out; an INTERPRETS population that is entirely
    stubs means the statute-to-case-law bridge is empty.
    """
    query = (
        "MATCH ()-[r]->(target) "
        "RETURN type(r) AS kind, target:Stub AS is_stub, count(*) AS n "
        "ORDER BY kind, is_stub"
    )
    with driver.session() as session:
        for row in session.run(query):
            suffix = " -> stub" if row["is_stub"] else " -> real"
            print(f"[Graph] {row['kind']}{suffix}: {row['n']:,}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for this script."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--restart",
        action="store_true",
        help="discard any existing checkpoint and rebuild the graph from scratch",
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

    is_fresh_build = read_checkpoint(checkpoint_path) is None
    state = load_state(checkpoint_path, settings.corpus_version)

    with graph_driver(settings) as driver:
        if is_fresh_build:
            print(f"[Graph] fresh build: dropped {drop_graph(driver):,} nodes")
        else:
            print("[Graph] resuming from checkpoint; keeping the existing graph")
        print(f"[Graph] applied {apply_schema(driver)} schema statements")

        run_provision_phase(
            driver, state, checkpoint_path, settings, args.limit,
            "provision_node_files_done", load_provision_nodes,
        )
        act_index = load_act_index(driver)
        run_provision_phase(
            driver, state, checkpoint_path, settings, args.limit,
            "provision_edge_files_done",
            lambda d, p, s, n: load_reference_edges(d, p, s, n, act_index),
        )
        run_judgments_phase(
            driver, state, checkpoint_path, settings, args.limit, act_index
        )

        report_cites_resolution(driver)
        report_edge_counts(driver)

    print("[Graph] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
