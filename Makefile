PYTHON ?= python3

.PHONY: install test demo export graph

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest -q

export:
	$(PYTHON) -m consultant_graph_tool.cli export-traces --output docs/traces.json

graph:
	$(PYTHON) -m consultant_graph_tool.cli graph

demo:
	$(PYTHON) -m pip install -e .
	$(PYTHON) -m consultant_graph_tool.cli export-traces --output docs/traces.json
	$(PYTHON) -m http.server 8000 --bind 127.0.0.1 --directory docs
