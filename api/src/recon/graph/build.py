"""Graph wiring and checkpointing.

Thread id is the run id, so a run has exactly one checkpoint lineage and
`make replay RUN_ID=...` has something unambiguous to point at.

Checkpointing to Postgres rather than in memory is the point of the interrupt
story: the API can be killed mid-run and the graph resumes from its last
checkpoint in a different process. An in-memory saver would make the demo work
and the claim false.

Written against langgraph 1.2 / langgraph-checkpoint-postgres 3.1, signatures
checked with `inspect`.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from recon.graph.nodes import Deps, make_nodes
from recon.graph.state import RunState


def _after_adjudication(state: RunState) -> str:
    if state.get("status") == "halted_cost":
        return "close_run"
    return "human_review" if state.get("queue") else "close_run"


def build_graph(deps: Deps, checkpointer: Any) -> Any:
    nodes = make_nodes(deps)
    graph: StateGraph[RunState, None, RunState, RunState] = StateGraph(RunState)

    for name, fn in nodes.items():
        graph.add_node(name, fn)

    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "deterministic_tiers")
    graph.add_edge("deterministic_tiers", "tier2_candidates")
    graph.add_edge("tier2_candidates", "tier3_adjudicate")
    graph.add_conditional_edges(
        "tier3_adjudicate",
        _after_adjudication,
        {"human_review": "human_review", "close_run": "close_run"},
    )
    graph.add_edge("human_review", "apply_human")
    graph.add_edge("apply_human", "close_run")
    graph.add_edge("close_run", END)

    return graph.compile(checkpointer=checkpointer)
