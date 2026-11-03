"""Contract tests for the real LangGraph workflow."""
from __future__ import annotations

import json

import pytest

from consultant_graph_tool.engine import GRAPH_EDGES, GRAPH_NODES, build_graph, export_traces, run_case
from consultant_graph_tool.sap_shipping import SAPShippingPack


@pytest.fixture(scope="module")
def pack() -> SAPShippingPack:
    return SAPShippingPack()


@pytest.mark.parametrize(
    "case_id,expected_outcome,expected_finding",
    [
        ("happy-path", "no_issue_found", None),
        ("missing-plant", "diagnosed_for_review", "missing_delivering_plant"),
        ("missing-loading-group", "diagnosed_for_review", "missing_loading_group"),
        ("missing-shipping-condition", "diagnosed_for_review", "missing_shipping_condition"),
        ("no-shipping-point-rule", "diagnosed_for_review", "shipping_point_rule_not_found"),
        ("missing-destination-zone", "diagnosed_for_review", "missing_destination_zone"),
        ("missing-transportation-group", "diagnosed_for_review", "missing_transportation_group"),
        ("no-route-rule", "diagnosed_for_review", "route_rule_not_found"),
        ("conflicting-shipping-rule", "diagnosed_for_review", "ambiguous_shipping_point_rule"),
        ("transient-source-error", "no_issue_found", None),
        ("persistent-source-error", "source_unavailable", None),
        ("manual-shipping-point", "diagnosed_for_review", "manual_shipping_point_override"),
        ("unknown-shipping-point", "diagnosed_for_review", "unknown_assigned_shipping_point"),
        ("manual-route", "diagnosed_for_review", "manual_route_override"),
    ],
)
def test_sap_scenarios(pack: SAPShippingPack, case_id: str, expected_outcome: str, expected_finding: str | None) -> None:
    report = run_case(pack, case_id)
    assert report["outcome"] == expected_outcome
    codes = {issue["code"] for issue in report["findings"]}
    if expected_finding:
        assert expected_finding in codes
    else:
        assert not codes
    assert report["trace"][-1]["node"] == "report"
    assert report["review_status"] == "not_requested"
    assert "Synthetic educational" in report["disclaimer"]


def test_evidence_is_referenced_by_every_finding(pack: SAPShippingPack) -> None:
    for case in pack.list_cases():
        report = run_case(pack, case["id"])
        proof_ids = {item["id"] for item in report["evidence"]}
        for issue in report["findings"]:
            assert issue["evidence_ids"]
            assert set(issue["evidence_ids"]).issubset(proof_ids), issue["code"]
        if report["outcome"] != "source_unavailable":
            assert report["verified"] is True


def test_route_uses_effective_shipping_point_location(pack: SAPShippingPack) -> None:
    report = run_case(pack, "manual-shipping-point")
    assert report["derived"]["proposed_shipping_point"] == "SP01"
    assert report["derived"]["effective_shipping_point"] == "SP02"
    assert report["derived"]["proposed_route"] == "R-DE-PL-02"


def test_transportation_group_is_not_loading_group(pack: SAPShippingPack) -> None:
    without_loading = run_case(pack, "missing-loading-group")
    without_transport = run_case(pack, "missing-transportation-group")
    assert without_loading["derived"]["effective_shipping_point"] is None
    assert without_transport["derived"]["effective_shipping_point"] == "SP01"
    assert without_transport["derived"]["route_status"] == "blocked_by_missing_input"


def test_retry_budget_and_loop(pack: SAPShippingPack) -> None:
    recovered = run_case(pack, "transient-source-error")
    failed = run_case(pack, "persistent-source-error")
    assert recovered["source_attempts"] == 2
    assert failed["source_attempts"] == 3
    assert [step["node"] for step in failed["trace"]] == ["load", "load", "load", "report"]
    assert failed["outcome"] == "source_unavailable"


def test_human_review_interrupt_and_resume(pack: SAPShippingPack) -> None:
    waiting = run_case(pack, "no-route-rule", require_review=True)
    assert waiting["outcome"] == "awaiting_review"
    assert waiting["interrupt"][0]["case_id"] == "no-route-rule"
    assert "No SAP writes" in waiting["interrupt"][0]["notice"]

    approved = run_case(pack, "no-route-rule", require_review=True, decision="approve")
    rejected = run_case(pack, "no-route-rule", require_review=True, decision="reject")
    assert approved["outcome"] == "proposal_approved"
    assert rejected["outcome"] == "proposal_rejected"
    assert approved["trace"][-2]["node"] == "review"


def test_review_does_not_run_for_clean_case(pack: SAPShippingPack) -> None:
    report = run_case(pack, "happy-path", require_review=True)
    assert report["outcome"] == "no_issue_found"


def test_unknown_case_and_bad_review(pack: SAPShippingPack) -> None:
    with pytest.raises(ValueError, match="Unknown case"):
        run_case(pack, "never-existed")
    with pytest.raises(ValueError, match="requires --review"):
        run_case(pack, "happy-path", decision="approve")


def test_graph_has_conditional_transitions(pack: SAPShippingPack) -> None:
    graph = build_graph(pack)
    assert graph is not None
    assert {"load", "validate", "analyze", "diagnose", "verify", "review", "report"} == set(GRAPH_NODES)
    assert any(e["from"] == "load" and e["to"] == "load" for e in GRAPH_EDGES)


def test_export_is_reproducible_and_generated_from_actual_graph(pack: SAPShippingPack, tmp_path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    a = export_traces(pack, first)
    export_traces(pack, second)
    assert first.read_bytes() == second.read_bytes()
    assert len(a["cases"]) == 14
    assert a["cases"][0]["result"]["trace"][0]["node"] == "load"
    assert all(item["result"]["trace"] for item in a["cases"])
    assert all("input" in item for item in a["cases"])
    parsed = json.loads(first.read_text(encoding="utf-8"))
    assert parsed["schema_version"] == "1.0"


def test_reference_integrity_does_not_report_false_success() -> None:
    pack = SAPShippingPack()
    pack.fixture["shipping_point_rules"][0]["shipping_point"] = "SP-ABSENT"
    report = run_case(pack, "happy-path")
    assert report["outcome"] == "diagnosed_for_review"
    assert "proposed_shipping_point_unknown" in {f["code"] for f in report["findings"]}
    assert report["derived"]["route_status"] == "blocked_by_shipping_point"
    assert report["verified"]


def test_conflicting_route_configuration_is_not_silently_selected() -> None:
    from copy import deepcopy
    pack = SAPShippingPack()
    pack.fixture["route_rules"].append(deepcopy(pack.fixture["route_rules"][0]))
    report = run_case(pack, "happy-path")
    assert report["outcome"] == "diagnosed_for_review"
    assert "ambiguous_route_rule" in {f["code"] for f in report["findings"]}
    assert report["derived"]["proposed_route"] is None
    assert report["derived"]["route_status"] == "ambiguous"
