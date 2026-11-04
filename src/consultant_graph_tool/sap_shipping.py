"""Synthetic SAP SD logistics pack. Pure decisions, no SAP connections.

This models only a deliberately narrow, classic sales-order determination path.
SAP S/4HANA release/configuration-specific alternatives are documented separately.
"""
from __future__ import annotations

from copy import deepcopy
from importlib.resources import files
import json
from typing import Any


def evidence(ref: str, value: Any, source: str) -> dict[str, Any]:
    return {"id": ref, "value": value, "source": source}


def finding(
    code: str, category: str, owner: str, detail: str, action: str, refs: list[str],
) -> dict[str, Any]:
    return {
        "code": code,
        "category": category,
        "owner": owner,
        "detail": detail,
        "action": action,
        "evidence_ids": refs,
    }


class SAPShippingPack:
    """Example adapter for the generic consultant graph."""

    name = "sap-shipping"
    review_demo_case_id = "no-route-rule"

    def __init__(self) -> None:
        path = files("consultant_graph_tool").joinpath("data/sap_shipping.json")
        self.fixture: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        self._cases = {case["id"]: case for case in self.fixture["cases"]}

    def list_cases(self) -> list[dict[str, Any]]:
        return [
            {"id": item["id"], "title": item["title"], "description": item.get("description", "")}
            for item in self.fixture["cases"]
        ]

    def get_case(self, case_id: str) -> dict[str, Any]:
        if case_id not in self._cases:
            raise ValueError(f"Unknown case: {case_id}")
        definition = self._cases[case_id]
        case = deepcopy(self.fixture["base_case"])
        for path, value in definition.get("changes", {}).items():
            target = case
            fields = path.split(".")
            if len(fields) != 2 or fields[0] not in ("order", "material", "ship_to") or fields[1] not in target[fields[0]]:
                raise ValueError(f"Unsupported fixture field: {path}")
            target[fields[0]][fields[1]] = value
        case["case_id"] = case_id
        case["title"] = definition["title"]
        case["description"] = definition.get("description", "")
        case["source_failures"] = definition.get("source_failures", 0)
        return case

    def validate(self, case: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        issues: list[dict[str, Any]] = []
        proof: list[dict[str, Any]] = []
        for entity in ("order", "material", "ship_to"):
            if not isinstance(case.get(entity), dict):
                proof.append(evidence(f"input:{entity}", case.get(entity), "Synthetic input"))
                issues.append(finding(
                    "invalid_case_shape", "input_quality", "Data Migration",
                    f"Expected an object for {entity}.", "Correct source payload shape.",
                    [f"input:{entity}"],
                ))
                return issues, proof
        required = (
            ("order", "plant", "missing_delivering_plant", "SD / Master Data", "Check the delivering plant on the sales order item."),
            ("order", "shipping_condition", "missing_shipping_condition", "SD / Master Data", "Check the effective shipping condition in the sales order."),
            ("material", "loading_group", "missing_loading_group", "Material Master Data", "Maintain or correct the material loading group."),
            ("material", "transportation_group", "missing_transportation_group", "Material Master Data", "Maintain or correct the material transportation group."),
            ("ship_to", "country", "missing_destination_country", "Customer Master Data", "Check the ship-to country."),
            ("ship_to", "transportation_zone", "missing_destination_zone", "Customer Master Data", "Maintain or correct the ship-to transportation zone."),
        )
        for entity, field, code, owner, action in required:
            value = case[entity].get(field)
            ref = f"{entity}:{field}"
            proof.append(evidence(ref, value, f"Synthetic {entity} record"))
            if value is None or value == "":
                issues.append(finding(
                    code, "master_data", owner, f"Required field {entity}.{field} is missing.",
                    action, [ref],
                ))
        for field in ("assigned_shipping_point", "assigned_route"):
            proof.append(evidence(f"order:{field}", case["order"].get(field), "Synthetic sales order item"))
        return issues, proof

    def analyze(
        self, case: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        proof: list[dict[str, Any]] = []
        derived: dict[str, Any] = {
            "proposed_shipping_point": None,
            "effective_shipping_point": None,
            "proposed_route": None,
            "route_status": "not_evaluated",
        }
        order, material, ship_to = case["order"], case["material"], case["ship_to"]
        rules = self.fixture["shipping_point_rules"]
        points = self.fixture["shipping_points"]
        proof.append(evidence("config:shipping_point_rules", rules, "Synthetic shipping point configuration"))

        sp_keys = ("plant", "shipping_condition")
        sp_ready = all(order.get(field) for field in sp_keys) and bool(material.get("loading_group"))
        matching_sp: list[tuple[int, dict[str, Any]]] = []
        if sp_ready:
            matching_sp = [
                (idx, rule) for idx, rule in enumerate(rules)
                if rule["plant"] == order["plant"]
                and rule["shipping_condition"] == order["shipping_condition"]
                and rule["loading_group"] == material["loading_group"]
            ]
            if not matching_sp:
                issues.append(finding(
                    "shipping_point_rule_not_found", "configuration", "SAP LE / Configuration",
                    "No match in the synthetic shipping point determination rules.",
                    "Review the plant, shipping condition and loading group combination in shipping point configuration.",
                    ["order:plant", "order:shipping_condition", "material:loading_group", "config:shipping_point_rules"],
                ))
            elif len(matching_sp) > 1:
                issues.append(finding(
                    "ambiguous_shipping_point_rule", "configuration_conflict", "SAP LE / Configuration",
                    "The synthetic rule table contains more than one shipping point proposal for the same keys.",
                    "Resolve the conflicting synthetic rules; verify actual SAP customizing and manual alternatives.",
                    ["config:shipping_point_rules", "order:plant", "order:shipping_condition", "material:loading_group"],
                ))
            else:
                proposed = matching_sp[0][1]["shipping_point"]
                derived["proposed_shipping_point"] = proposed
                rule_ref = f"config:shipping_point_rule:{matching_sp[0][0]}"
                proof.append(evidence(rule_ref, matching_sp[0][1], "Matching synthetic rule"))
                if proposed not in points:
                    issues.append(finding(
                        "proposed_shipping_point_unknown", "configuration_conflict", "SAP LE / Configuration",
                        "The matched synthetic rule points to a shipping point absent from the reference dataset.",
                        "Validate the shipping point record and assignment before evaluating a route.",
                        [rule_ref, "config:shipping_point_rules"],
                    ))

        assigned = order.get("assigned_shipping_point")
        if assigned:
            if assigned not in points:
                issues.append(finding(
                    "unknown_assigned_shipping_point", "master_data", "SD / Logistics Execution",
                    "The assigned shipping point has no corresponding record in the synthetic fixture.",
                    "Validate the manually assigned shipping point and its plant assignment.",
                    ["order:assigned_shipping_point", "config:shipping_point_rules"],
                ))
            else:
                derived["effective_shipping_point"] = assigned
                if derived["proposed_shipping_point"] and assigned != derived["proposed_shipping_point"]:
                    proof.append(evidence(f"record:shipping_point:{assigned}", points[assigned], "Synthetic shipping point record"))
                    issues.append(finding(
                        "manual_shipping_point_override", "manual_review", "SD / Logistics Execution",
                        "Assigned shipping point differs from the simulated proposal; this can be a valid manual choice.",
                        "Check whether the alternative shipping point is permitted for this plant and business process.",
                        ["order:assigned_shipping_point", f"record:shipping_point:{assigned}"],
                    ))
        elif derived["proposed_shipping_point"]:
            derived["effective_shipping_point"] = derived["proposed_shipping_point"]

        sp = derived["effective_shipping_point"]
        if sp is None or sp not in points:
            derived["route_status"] = "blocked_by_shipping_point"
            return issues, proof, derived

        point = points[sp]
        proof.append(evidence(f"record:shipping_point:{sp}", point, "Synthetic shipping point record"))
        if point["plant"] != order.get("plant"):
            issues.append(finding(
                "shipping_point_plant_mismatch", "configuration_conflict", "SAP LE / Configuration",
                "Shipping point is not assigned to the selected plant in the synthetic records.",
                "Confirm allowed shipping points for the delivering plant.",
                [f"record:shipping_point:{sp}", "order:plant"],
            ))
            derived["route_status"] = "blocked_by_plant_mismatch"
            return issues, proof, derived
        if not all([
            order.get("shipping_condition"), material.get("transportation_group"),
            ship_to.get("country"), ship_to.get("transportation_zone"),
        ]):
            derived["route_status"] = "blocked_by_missing_input"
            return issues, proof, derived

        route_rules = self.fixture["route_rules"]
        proof.append(evidence("config:route_rules", route_rules, "Synthetic route determination configuration"))
        matching_routes = [
            (idx, rule) for idx, rule in enumerate(route_rules)
            if rule["departure_country"] == point["country"]
            and rule["departure_zone"] == point["transportation_zone"]
            and rule["destination_country"] == ship_to["country"]
            and rule["destination_zone"] == ship_to["transportation_zone"]
            and rule["shipping_condition"] == order["shipping_condition"]
            and rule["transportation_group"] == material["transportation_group"]
        ]
        if not matching_routes:
            derived["route_status"] = "no_matching_rule"
            issues.append(finding(
                "route_rule_not_found", "configuration", "SAP LE / Configuration",
                "No route matches these values in the synthetic configuration.",
                "Review departure country/zone, destination country/zone, shipping condition and transportation group in route determination.",
                [f"record:shipping_point:{sp}", "ship_to:country", "ship_to:transportation_zone",
                 "order:shipping_condition", "material:transportation_group", "config:route_rules"],
            ))
        elif len(matching_routes) > 1:
            derived["route_status"] = "ambiguous"
            issues.append(finding(
                "ambiguous_route_rule", "configuration_conflict", "SAP LE / Configuration",
                "Multiple synthetic routes match the same determination keys.",
                "Review duplicate or competing route records and SAP release-specific behavior.",
                ["config:route_rules", f"record:shipping_point:{sp}"],
            ))
        else:
            derived["proposed_route"] = matching_routes[0][1]["route"]
            derived["route_status"] = "matched"
            proof.append(evidence(f"config:route_rule:{matching_routes[0][0]}", matching_routes[0][1], "Matching synthetic route rule"))

        assigned_route = order.get("assigned_route")
        if assigned_route and derived["proposed_route"] and assigned_route != derived["proposed_route"]:
            issues.append(finding(
                "manual_route_override", "manual_review", "SD / Logistics Execution",
                "Order route differs from the computed proposal; manual choices may be valid.",
                "Verify route override against business requirements and configuration.",
                ["order:assigned_route", "config:route_rules"],
            ))
        return issues, proof, derived
