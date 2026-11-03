"""Command-line entry point. No external credentials or SAP writes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .engine import GRAPH_EDGES, GRAPH_NODES, export_traces, run_case
from .sap_shipping import SAPShippingPack


def get_pack(name: str) -> SAPShippingPack:
    if name == "sap-shipping":
        return SAPShippingPack()
    raise ValueError(f"Unknown pack: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cgt", description="Consultant Graph Tool — evidence-led graphs")
    parser.add_argument("--pack", default="sap-shipping", choices=["sap-shipping"])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List the available synthetic cases")
    run = sub.add_parser("run", help="Run an actual LangGraph execution")
    run.add_argument("case_id", help="ID from 'cgt list'")
    run.add_argument("--review", action="store_true", help="Pause for human proposal review")
    run.add_argument("--decision", choices=["approve", "reject"], help="Explicit one-shot reviewer decision")
    export = sub.add_parser("export-traces", help="Export actual Python traces for static browser replay")
    export.add_argument("--output", default="docs/traces.json")
    sub.add_parser("graph", help="Print the graph's nodes and edges as JSON")
    args = parser.parse_args(argv)
    pack = get_pack(args.pack)
    result: Any
    if args.command == "list":
        result = {"pack": pack.name, "cases": pack.list_cases()}
    elif args.command == "run":
        result = run_case(pack, args.case_id, require_review=args.review, decision=args.decision)
    elif args.command == "export-traces":
        result = export_traces(pack, Path(args.output))
        result = {"output": args.output, "pack": pack.name, "cases": len(result["cases"])}
    elif args.command == "graph":
        result = {"nodes": GRAPH_NODES, "edges": GRAPH_EDGES}
    else:
        parser.error("Unknown command")
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
