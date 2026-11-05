/* Static replay of traces produced by the real LangGraph implementation.
 * Domain rules live exclusively in Python. No synthetic decisions are made here.
 */
(() => {
  "use strict";
  const svgNS = "http://www.w3.org/2000/svg";
  const positions = {
    load: [110, 105], validate: [325, 105], analyze: [540, 105],
    diagnose: [755, 105], verify: [970, 105],
    review: [970, 305], report: [755, 305]
  };
  const label = {
    load: "Load data", validate: "Validate", analyze: "Analyze rules",
    diagnose: "Diagnose", verify: "Verify evidence",
    review: "Human review", report: "Build report"
  };
  const $ = id => document.getElementById(id);
  let dataset = null;
  let activeCase = null;
  let replayOptions = [];
  let stepIndex = -1;
  let playTimer = null;

  function el(tag, cls, text) {
    const item = document.createElement(tag);
    if (cls) item.className = cls;
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function svgElement(tag, attrs) {
    const node = document.createElementNS(svgNS, tag);
    Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }
  function selectedTrace() {
    return activeCase?.result?.trace || [];
  }
  function drawGraph() {
    const root = $("graph-view");
    root.replaceChildren();
    const definitions = svgElement("defs");
    const marker = svgElement("marker", {
      id: "arrow-end", viewBox: "0 0 10 10", refX: 9, refY: 5,
      markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse"
    });
    marker.appendChild(svgElement("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#68889b" }));
    definitions.appendChild(marker);
    root.appendChild(definitions);
    const visited = new Set(selectedTrace().slice(0, stepIndex + 1).map(step => step.node));
    const current = stepIndex >= 0 ? selectedTrace()[stepIndex].node : null;
    const previous = stepIndex > 0 ? selectedTrace()[stepIndex - 1].node : null;

    dataset.edges.forEach(edge => {
      const a = positions[edge.from], b = positions[edge.to];
      if (!a || !b) return;
      // Highlight only the edge that the recorded trace actually traversed.
      // An earlier visited node is not proof that its edge to the current node ran.
      const isActive = current === edge.to && previous === edge.from;
      let d;
      if (edge.from === edge.to) {
        const x = a[0], y = a[1];
        d = "M " + (x - 35) + " " + (y - 33) + " C " + (x - 80) + " " + (y - 100) +
          ", " + (x + 80) + " " + (y - 100) + ", " + (x + 35) + " " + (y - 33);
      } else if (a[1] === b[1]) {
        const forward = b[0] > a[0];
        d = "M " + (a[0] + (forward ? 83 : -83)) + " " + a[1] +
          " L " + (b[0] + (forward ? -83 : 83)) + " " + b[1];
      } else if (a[0] === b[0]) {
        d = "M " + a[0] + " " + (a[1] + 38) + " L " + b[0] + " " + (b[1] - 38);
      } else {
        d = "M " + (a[0] - 15) + " " + (a[1] + 38) + " Q " + (a[0] - 50) +
          " " + (b[1] + 12) + " " + (b[0] + 65) + " " + b[1];
      }
      root.appendChild(svgElement("path", {
        d, fill: "none", stroke: isActive ? "#63e5d5" : "#547187",
        "stroke-width": isActive ? 2.5 : 1.4, "stroke-opacity": isActive ? 1 : 0.7,
        "marker-end": "url(#arrow-end)"
      }));
    });

    Object.entries(positions).forEach(([name, [x, y]]) => {
      const isActive = name === current;
      const done = visited.has(name);
      const group = svgElement("g", { role: "group", "aria-label": name + (isActive ? " active" : done ? " visited" : "") });
      group.appendChild(svgElement("rect", {
        x: x - 83, y: y - 38, width: 166, height: 76, rx: 10,
        fill: isActive ? "#1c4d56" : done ? "#173746" : "#15293b",
        stroke: isActive ? "#63e5d5" : done ? "#4c9d9e" : "#416075",
        "stroke-width": isActive ? 2.7 : 1.2
      }));
      const small = svgElement("text", {
        x, y: y - 13, "text-anchor": "middle", fill: "#a5c0cc",
        "font-size": 11, "font-family": "system-ui, sans-serif"
      });
      small.textContent = name.toUpperCase();
      group.appendChild(small);
      const title = svgElement("text", {
        x, y: y + 14, "text-anchor": "middle", fill: isActive ? "#a9fff2" : "#e6f2f7",
        "font-weight": "700", "font-size": 14, "font-family": "system-ui, sans-serif"
      });
      title.textContent = label[name];
      group.appendChild(title);
      root.appendChild(group);
    });
  }

  function drawResults() {
    const trace = selectedTrace();
    const frame = stepIndex >= 0 ? trace[stepIndex] : null;
    $("step-counter").textContent = "Step " + (stepIndex + 1) + " / " + trace.length;
    $("step-message").textContent = frame ? frame.message : "Select Next step or Play run.";
    $("state-view").textContent = frame
      ? JSON.stringify(frame.snapshot, null, 2)
      : "No nodes have completed yet.";
    $("graph-summary").textContent = frame
      ? "Active: " + label[frame.node] + " · step " + frame.step
      : "A case is loaded. Start the replay to inspect the execution.";

    const endReached = stepIndex === trace.length - 1;
    const result = activeCase.result;
    $("outcome").textContent = endReached ? result.outcome.replaceAll("_", " ") : "Not finished";
    const root = $("recommendations");
    root.replaceChildren();
    if (endReached) {
      const info = el("p", "muted", result.diagnosis.summary || "The source could not be read.");
      root.appendChild(info);
      result.findings.forEach(finding => {
        const item = el("div", "finding");
        item.appendChild(el("h4", null, finding.code.replaceAll("_", " ")));
        item.appendChild(el("small", null, "Owner: " + finding.owner));
        item.appendChild(el("p", null, finding.detail));
        item.appendChild(el("p", null, "Next: " + finding.action));
        item.appendChild(el("small", null, "Evidence: " + finding.evidence_ids.join(", ")));
        root.appendChild(item);
      });
      if (result.evidence.length) {
        const evidenceDetails = el("details", "evidence-details");
        evidenceDetails.appendChild(el("summary", null, "Inspect " + result.evidence.length + " source evidence records"));
        const evidenceList = el("div", "evidence-list");
        result.evidence.forEach(record => {
          const line = el("div", "evidence-record");
          line.appendChild(el("strong", null, record.id));
          line.appendChild(el("span", "muted", record.source));
          const value = el("pre", null, JSON.stringify(record.value, null, 2));
          line.appendChild(value);
          evidenceList.appendChild(line);
        });
        evidenceDetails.appendChild(evidenceList);
        root.appendChild(evidenceDetails);
      }
      if (result.unverified_finding_codes?.length) {
        root.appendChild(el("p", "muted", "Unverified finding codes (no advice): " +
          result.unverified_finding_codes.join(", ")));
      }
      if (!result.findings.length) {
        const message = result.outcome === "source_unavailable"
          ? "Investigation stopped after the retry budget. No root cause is asserted."
          : result.outcome === "insufficient_evidence"
            ? "Evidence references are incomplete. Findings and actions are withheld."
            : "No issues found in this limited synthetic rule set.";
        root.appendChild(el("p", "muted", message));
      }
    } else {
      root.appendChild(el("p", "muted", "The final report appears after the report node completes."));
    }
    $("previous").disabled = stepIndex < 0;
    $("next").disabled = endReached;
    $("reset").disabled = stepIndex < 0;
    $("play").disabled = !trace.length;
    $("play").textContent = playTimer ? "Pause" : "Play run";

    const timeline = $("trace-list");
    timeline.replaceChildren();
    trace.forEach((traceStep, i) => {
      const li = el("li");
      const button = el("button", i === stepIndex ? "current" : null);
      button.type = "button";
      button.disabled = i > stepIndex + 1;
      button.appendChild(el("strong", null, String(traceStep.step).padStart(2, "0") + " · " + label[traceStep.node]));
      button.appendChild(el("small", null, traceStep.message));
      button.addEventListener("click", () => { pause(); stepIndex = i; render(); });
      li.appendChild(button);
      timeline.appendChild(li);
    });
  }
  function render() { drawGraph(); drawResults(); }
  function pause() {
    if (playTimer !== null) { clearInterval(playTimer); playTimer = null; }
    $("play").textContent = "Play run";
  }
  function showCase(caseId) {
    pause();
    activeCase = replayOptions.find(item => item.id === caseId);
    if (!activeCase) return;
    stepIndex = -1;
    $("case-description").textContent = activeCase.description;
    $("replay-mode").textContent = activeCase.recorded_review_decision
      ? "Scripted CLI decision: " + activeCase.recorded_review_decision + ". This replays a real LangGraph interrupt/resume; the browser cannot approve or reject anything."
      : "Recorded run without a review decision. To trigger a live interrupt, run the Python CLI locally.";
    const input = activeCase.input;
    $("input-view").textContent = JSON.stringify({
      order: input.order, material: input.material, ship_to: input.ship_to,
      simulated_source_failures: input.source_failures
    }, null, 2);
    render();
  }
  function next() {
    const trace = selectedTrace();
    if (stepIndex < trace.length - 1) {
      stepIndex += 1;
      render();
    } else {
      pause();
    }
  }
  function play() {
    if (playTimer !== null) { pause(); render(); return; }
    if (stepIndex >= selectedTrace().length - 1) stepIndex = -1;
    playTimer = setInterval(() => {
      if (stepIndex >= selectedTrace().length - 1) { pause(); render(); return; }
      next();
    }, 1100);
    render();
  }
  $("case-picker").addEventListener("change", event => showCase(event.target.value));
  $("next").addEventListener("click", next);
  $("previous").addEventListener("click", () => { pause(); stepIndex = Math.max(-1, stepIndex - 1); render(); });
  $("reset").addEventListener("click", () => { pause(); stepIndex = -1; render(); });
  $("play").addEventListener("click", play);
  document.addEventListener("visibilitychange", () => { if (document.hidden) pause(); });

  fetch("./traces.json", { cache: "no-store" })
    .then(response => {
      if (!response.ok) throw new Error("traces.json is missing (" + response.status + ")");
      return response.json();
    })
    .then(payload => {
      if (payload.schema_version !== "1.0" || !Array.isArray(payload.cases) || !payload.nodes || !Array.isArray(payload.edges) || (payload.review_replays && !Array.isArray(payload.review_replays))) {
        throw new Error("Unsupported or invalid replay schema");
      }
      dataset = payload;
      replayOptions = [...payload.cases, ...(payload.review_replays || [])];
      const select = $("case-picker");
      select.replaceChildren();
      replayOptions.forEach(item => {
        const option = el("option", null, item.title);
        option.value = item.id;
        select.appendChild(option);
      });
      select.disabled = false;
      if (!replayOptions.length) throw new Error("No recorded executions available");
      showCase(replayOptions[0].id);
    })
    .catch(error => {
      const banner = $("load-error");
      banner.hidden = false;
      banner.textContent = "No published execution traces are available. Locally, run: cgt export-traces --output docs/traces.json, then serve the docs directory. Details: " + error.message;
    });
})();
