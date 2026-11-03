"""Reusable LangGraph orchestration, intentionally independent of the SAP adapter.

The graph never changes source systems. Each domain pack supplies pure validation
and analysis functions. LangGraph owns transitions, retries and human review.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class ScenarioPack(Protocol):
    name: str
    def list_cases(self) -> list[dict[str, Any]]: ...
    def get_case(self, case_id: str) -> dict[str, Any]: ...
    def validate(self, case: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]: ...
    def analyze(self, case: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]: ...


class WorkflowState(TypedDict, total=False):
    pack: str
    case_id: str
    input_data: dict[str, Any]
    attempts: int
    error: str | None
    findings: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    derived: dict[str, Any]
    diagnosis: dict[str, Any]
    verified: bool
    review_requested: bool
    review_status: str
    report: dict[str, Any]
    trace: list[dict[str, Any]]


MAX_LOAD_ATTEMPTS = 3

GRAPH_NODES: dict[str, str] = {
    "load": "Read synthetic source with bounded retries",
    "validate": "Validate required master data and references",
    "analyze": "Evaluate domain rules with explicit evidence",
    "diagnose": "Classify root cause and propose actions",
    "verify": "Check evidence coverage before recommendations",
    "review": "Human approval checkpoint (no writes)",
    "report": "Produce inspectable diagnostic report",
}
GRAPH_EDGES: list[dict[str, Any]] = [
    {"from": "load", "to": "load", "condition": "transient error and retry budget"},
    {"from": "load", "to": "validate", "condition": "source available"},
    {"from": "load", "to": "report", "condition": "source unavailable after retries"},
    {"from": "validate", "to": "analyze", "condition": "valid shape"},
    {"from": "validate", "to": "diagnose", "condition": "invalid shape"},
    {"from": "analyze", "to": "diagnose"},
    {"from": "diagnose", "to": "verify"},
    {"from": "verify", "to": "review", "condition": "explicit review requested and evidence verified"},
    {"from": "verify", "to": "report", "condition": "otherwise"},
    {"from": "review", "to": "report"},
]


def _trace(state: WorkflowState, node: str, message: str, **updates: Any) -> WorkflowState:
    """Return a node update with a small, deterministic audit snapshot."""
    combined = {**state, **updates}
    current = list(state.get("trace", []))
    snapshot = {
        "attempts": combined.get("attempts", 0),
        "finding_codes": [item["code"] for item in combined.get("findings", [])],
        "derived": combined.get("derived", {}),
        "verified": combined.get("verified"),
        "review_status": combined.get("review_status", "not_requested"),
        "error": combined.get("error"),
    }
    current.append({"step": len(current) + 1, "node": node, "message": message, "snapshot": snapshot})
    return {**updates, "trace": current}


def build_graph(pack: ScenarioPack):
    """Compile a real StateGraph. Only the pack adapter is domain-specific."""

    def load(state: WorkflowState) -> WorkflowState:
        attempts = state.get("attempts", 0) + 1
        case = pack.get_case(state["case_id"])
        if attempts <= case.get("source_failures", 0):
            return _trace(state, "load", "Synthetic source unavailable; bounded retry.", attempts=attempts, error="source_unavailable")
        return _trace(state, "load", "Synthetic source loaded.", attempts=attempts, error=None, input_data=case)

    def load_route(state: WorkflowState) -> str:
        if not state.get("error"):
            return "validate"
        return "load" if state.get("attempts", 0) < MAX_LOAD_ATTEMPTS else "report"

    def validate(state: WorkflowState) -> WorkflowState:
        issues, proof = pack.validate(state["input_data"])
        return _trace(
            state, "validate", f"Checked inputs; {len(issues)} missing or invalid values.",
            findings=issues, evidence=proof,
        )

    def validation_route(state: WorkflowState) -> str:
        return "diagnose" if any(f["code"] == "invalid_case_shape" for f in state.get("findings", [])) else "analyze"

    def analyze(state: WorkflowState) -> WorkflowState:
        issues, proof, derived = pack.analyze(state["input_data"])
        return _trace(
            state, "analyze", f"Evaluated logistics rules; {len(issues)} additional findings.",
            findings=[*state.get("findings", []), *issues],
            evidence=[*state.get("evidence", []), *proof],
            derived=derived,
        )

    def diagnose(state: WorkflowState) -> WorkflowState:
        issues = state.get("findings", [])
        if not issues:
            diagnosis = {
                "classification": "no_issue_found",
                "summary": "The synthetic shipping point and route rules have matching inputs.",
                "recommendations": [],
            }
        else:
            categories = sorted({f["category"] for f in issues})
            classification = categories[0] if len(categories) == 1 else "multiple_causes"
            diagnosis = {
                "classification": classification,
                "summary": f"{len(issues)} finding(s) require attention.",
                "recommendations": [
                    {"code": f["code"], "owner": f["owner"], "action": f["action"]}
                    for f in issues
                ],
            }
        return _trace(state, "diagnose", diagnosis["summary"], diagnosis=diagnosis)

    def verify(state: WorkflowState) -> WorkflowState:
        ids = {e["id"] for e in state.get("evidence", [])}
        supported = all(set(f["evidence_ids"]).issubset(ids) and f["evidence_ids"] for f in state.get("findings", []))
        return _trace(
            state, "verify",
            "All findings have traceable evidence." if supported else "Recommendation withheld: evidence gap.",
            verified=bool(supported),
        )

    def verify_route(state: WorkflowState) -> str:
        if state.get("review_requested") and state.get("verified") and state.get("findings"):
            return "review"
        return "report"

    def review(state: WorkflowState) -> WorkflowState:
        response = interrupt({
            "case_id": state["case_id"],
            "diagnosis": state.get("diagnosis", {}),
            "notice": "Approve the diagnostic proposal only. No SAP writes will occur.",
        })
        decision = response.get("decision") if isinstance(response, dict) else None
        if decision not in ("approve", "reject"):
            decision = "reject"
        return _trace(
            state, "review", f"Reviewer decision: {decision}.",
            review_status="approved" if decision == "approve" else "rejected",
        )

    def report(state: WorkflowState) -> WorkflowState:
        findings = state.get("findings", [])
        if state.get("error"):
            outcome = "source_unavailable"
        elif not state.get("verified", False):
            outcome = "insufficient_evidence"
        elif not findings:
            outcome = "no_issue_found"
        elif state.get("review_status") == "approved":
            outcome = "proposal_approved"
        elif state.get("review_status") == "rejected":
            outcome = "proposal_rejected"
        else:
            outcome = "diagnosed_for_review"
        update = _trace(state, "report", f"Workflow finished: {outcome}.")
        trace = update["trace"]
        return {
            **update,
            "report": {
                "pack": pack.name,
                "case_id": state["case_id"],
                "outcome": outcome,
                "source_attempts": state.get("attempts", 0),
                "derived": state.get("derived", {}),
                "findings": findings,
                "evidence": state.get("evidence", []),
                "diagnosis": state.get("diagnosis", {}),
                "verified": bool(state.get("verified", False)),
                "review_status": state.get("review_status", "not_requested"),
                "trace": trace,
                "disclaimer": "Synthetic educational model; not SAP's determination engine and not a live connection.",
            },
        }

    builder = StateGraph(WorkflowState)
    for name, fn in (
        ("load", load), ("validate", validate), ("analyze", analyze),
        ("diagnose", diagnose), ("verify", verify),
        ("review", review), ("report", report),
    ):
        builder.add_node(name, fn)
    builder.add_edge(START, "load")
    builder.add_conditional_edges("load", load_route, {"load": "load", "validate": "validate", "report": "report"})
    builder.add_conditional_edges("validate", validation_route, {"analyze": "analyze", "diagnose": "diagnose"})
    builder.add_edge("analyze", "diagnose")
    builder.add_edge("diagnose", "verify")
    builder.add_conditional_edges("verify", verify_route, {"review": "review", "report": "report"})
    builder.add_edge("review", "report")
    builder.add_edge("report", END)
    return builder.compile(checkpointer=InMemorySaver())


def run_case(
    pack: ScenarioPack,
    case_id: str,
    *,
    require_review: bool = False,
    decision: str | None = None,
) -> dict[str, Any]:
    """Execute one independent run. Explicit review is about proposals, never writes."""
    if case_id not in {case["id"] for case in pack.list_cases()}:
        raise ValueError(f"Unknown case: {case_id}")
    if decision is not None and (not require_review or decision not in {"approve", "reject"}):
        raise ValueError("Decision must be approve/reject and requires --review.")
    graph = build_graph(pack)
    config = {"configurable": {"thread_id": str(uuid4())}, "recursion_limit": 25}
    initial: WorkflowState = {
        "pack": pack.name, "case_id": case_id, "attempts": 0,
        "findings": [], "evidence": [], "derived": {}, "trace": [],
        "review_requested": require_review, "review_status": "not_requested",
    }
    state = graph.invoke(initial, config=config)
    if "__interrupt__" in state:
        if decision is None:
            return {
                "pack": pack.name, "case_id": case_id,
                "outcome": "awaiting_review",
                "interrupt": [item.value for item in state["__interrupt__"]],
                "trace": state.get("trace", []),
            }
        state = graph.invoke(Command(resume={"decision": decision}), config=config)
    return state["report"]


def export_traces(pack: ScenarioPack, output: str | Path) -> dict[str, Any]:
    """Build browser replay exclusively from the actual compiled Python graph."""
    result = {
        "schema_version": "1.0",
        "pack": pack.name,
        "nodes": GRAPH_NODES,
        "edges": GRAPH_EDGES,
        "cases": [
            {
                "id": case["id"],
                "title": case["title"],
                "description": case.get("description", ""),
                "input": pack.get_case(case["id"]),
                "result": run_case(pack, case["id"]),
            }
            for case in pack.list_cases()
        ],
    }
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
