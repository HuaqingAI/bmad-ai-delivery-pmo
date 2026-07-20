(function () {
  "use strict";

  var model = JSON.parse(document.getElementById("adp-panel-model").textContent);
  var manifest = JSON.parse(document.getElementById("adp-panel-manifest").textContent);
  var sourcePreviews = JSON.parse(document.getElementById("adp-source-previews").textContent);
  var sourcePreviewByPath = new Map(sourcePreviews.map(function (item) { return [item.path, item]; }));
  var actionLedgerPreview = sourcePreviewByPath.get("actions/action-ledger.md") || null;
  var riskRegisterPreview = sourcePreviewByPath.get("views/risk-matrix.md") || null;
  var canonicalViewIds = ["project-lead", "fde-morning", "business-biweekly"];
  var utilityViewIds = ["action-ledger", "risk-register"];
  var viewIds = canonicalViewIds.concat(manifest.distribution_profile === "internal-full" ? utilityViewIds : []);
  var modeIds = ["quantitative-progress", "flow-progress"];
  var statusValues = ["on-plan", "at-risk", "blocked", "off-plan", "indeterminate", "complete", "in-progress", "not-started", "ready", "planned", "not-applicable"];
  var state = parseHash();
  var sortState = { key: "scope_id", direction: "ascending" };
  var collapsedLanes = new Set();
  var flowTransform = { scale: 1, x: 0, y: 0 };
  var flowZoomLimits = { min: .35, max: 8 };
  var flowPanGain = 1.35;
  var flowFullscreen = false;
  var flowInteractionMode = "pan";
  var spacePan = false;
  var activeFlowNodeId = null;
  var svgNamespace = "http://www.w3.org/2000/svg";
  var markdownRenderer = typeof window.markdownit === "function"
    ? window.markdownit({ html: false, linkify: false, typographer: false })
    : null;

  if (markdownRenderer) {
    markdownRenderer.renderer.rules.link_open = function () { return "<span>"; };
    markdownRenderer.renderer.rules.link_close = function () { return "</span>"; };
    markdownRenderer.renderer.rules.image = function (tokens, index) {
      return markdownRenderer.utils.escapeHtml(tokens[index].content || "");
    };
  }

  document.documentElement.classList.add("js");
  document.documentElement.lang = model.catalog.locale;
  var app = document.getElementById("panel-app");
  app.hidden = false;

  function create(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function createSvg(tag, attributes, text) {
    var node = document.createElementNS(svgNamespace, tag);
    Object.keys(attributes || {}).forEach(function (name) { node.setAttribute(name, String(attributes[name])); });
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function message(key, fallback) {
    return model.catalog.messages[key] || fallback;
  }

  function valid(value, choices, fallback) {
    return choices.indexOf(value) >= 0 ? value : fallback;
  }

  function parseHash() {
    var raw = window.location.hash.replace(/^#/, "");
    var params = new URLSearchParams(raw);
    var defaultView = valid(document.body.dataset.defaultView, viewIds, "project-lead");
    return {
      version: params.get("v") === "1" ? "1" : "1",
      view: valid(params.get("view"), viewIds, defaultView),
      mode: valid(params.get("mode"), modeIds, "quantitative-progress"),
      workstream: params.get("workstream") || "all",
      status: params.get("status") || "all",
      owner: params.get("owner") || "all",
      period: params.get("period") || "current",
      action: params.get("action") || "",
      risk: params.get("risk") || ""
    };
  }

  function writeHash(replace) {
    var params = new URLSearchParams();
    params.set("v", "1");
    params.set("view", state.view);
    params.set("mode", state.mode);
    if (state.workstream !== "all") params.set("workstream", state.workstream);
    if (state.status !== "all") params.set("status", state.status);
    if (state.owner !== "all") params.set("owner", state.owner);
    if (state.period !== "current") params.set("period", state.period);
    if (state.view === "action-ledger" && state.action) params.set("action", state.action);
    if (state.view === "risk-register" && state.risk) params.set("risk", state.risk);
    var target = "#" + params.toString();
    if (window.location.hash === target) return;
    if (replace) history.replaceState(null, "", target); else history.pushState(null, "", target);
  }

  function setSelect(id, value) {
    var select = document.getElementById(id);
    if (Array.prototype.some.call(select.options, function (option) { return option.value === value; })) select.value = value;
    else select.value = select.options[0].value;
  }

  function addOption(select, value, label) {
    var option = document.createElement("option");
    option.value = value;
    option.textContent = label || value;
    select.appendChild(option);
  }

  function allOwners() {
    var owners = new Set();
    Object.keys(model.data.flows).forEach(function (viewId) {
      (model.data.flows[viewId].nodes || []).forEach(function (item) { if (item.owner) owners.add(String(item.owner)); });
    });
    Object.keys(model.data.meetings).forEach(function (viewId) {
      var boards = model.data.meetings[viewId].boards || {};
      Object.keys(boards).forEach(function (key) {
        (Array.isArray(boards[key]) ? boards[key] : []).forEach(function (item) { if (item.owner) owners.add(String(item.owner)); });
      });
    });
    return Array.from(owners).sort();
  }

  function initControls() {
    var workstream = document.getElementById("filter-workstream");
    (model.data.status.progress.by_scope || []).forEach(function (item) { addOption(workstream, item.scope_id); });
    var statuses = document.getElementById("filter-status");
    statusValues.forEach(function (value) {
      var label = value === "not-started" ? message("flow.state.not-started", "Not started")
        : value === "in-progress" ? message("flow.state.in-progress", "In progress")
          : value === "complete" ? message("flow.state.complete", "Complete") : value;
      addOption(statuses, value, label);
    });
    var owners = document.getElementById("filter-owner");
    allOwners().forEach(function (value) { addOption(owners, value); });
    var period = document.getElementById("filter-period");
    (model.data.history || []).forEach(function (item) { addOption(period, item.snapshot_id, item.as_of + " / " + item.snapshot_id); });
    ["workstream", "status", "owner", "period"].forEach(function (name) {
      document.getElementById("filter-" + name).addEventListener("change", function (event) {
        state[name] = event.target.value;
        writeHash(false);
        render();
      });
    });
    document.getElementById("filter-search").addEventListener("input", render);
    document.getElementById("clear-filters").addEventListener("click", function () {
      state.workstream = "all";
      state.status = "all";
      state.owner = "all";
      state.period = "current";
      document.getElementById("filter-search").value = "";
      writeHash(false);
      render();
    });
  }

  function statusToken(value) {
    var token = create("span", "status-token", value || "indeterminate");
    token.dataset.value = value || "indeterminate";
    return token;
  }

  function executionPresentation(value) {
    if (value === "complete") return { id: "complete", label: message("flow.state.complete", "Complete"), detail: value };
    if (value === "in-progress") return { id: "in-progress", label: message("flow.state.in-progress", "In progress"), detail: value };
    if (value === "ready" || value === "planned") return { id: "not-started", label: message("flow.state.not-started", "Not started"), detail: value };
    return { id: "not-applicable", label: message("flow.state.not-applicable", "Not applicable"), detail: value || "not-applicable" };
  }

  function healthPresentation(value) {
    if (value === "blocked") return { id: "blocked", label: message("flow.health.blocked", "Blocked") };
    if (value === "at-risk") return { id: "risk", label: message("flow.state.risk", "Risk") };
    return null;
  }

  function flowStatusMatches(filter, execution, health) {
    if (filter === "not-started") return execution === "planned" || execution === "ready";
    return filter === execution || filter === health;
  }

  function percent(value, suffix) {
    return value === null || value === undefined ? "Not measurable" : String(value) + (suffix === undefined ? "%" : suffix);
  }

  function metric(label, value, detail) {
    var root = create("article", "metric");
    root.appendChild(create("div", "label", label));
    root.appendChild(create("div", "value", value));
    if (detail) root.appendChild(create("div", "detail", detail));
    return root;
  }

  function section(title, sectionId) {
    var root = create("section", "band");
    if (sectionId) root.id = sectionId;
    var heading = create("div", "section-heading");
    heading.appendChild(create("h2", "", title));
    root.appendChild(heading);
    return root;
  }

  function currentProgress() {
    var current = model.data.status.progress.overall;
    if (state.workstream !== "all") {
      var selected = (model.data.status.progress.by_scope || []).find(function (item) { return item.scope_id === state.workstream; });
      if (selected) current = selected;
    }
    if (state.period === "current") return { current: current.current, forecast: current.forecast_summary, label: model.data.status.as_of };
    var historyItem = (model.data.history || []).find(function (item) { return item.snapshot_id === state.period; });
    return historyItem ? { current: historyItem.progress_current || {}, forecast: {}, label: historyItem.as_of, history: true } : { current: current.current, forecast: current.forecast_summary, label: model.data.status.as_of };
  }

  function renderHeader() {
    var status = model.data.status;
    var project = status.project || {};
    document.getElementById("project-name").textContent = project.name || status.snapshot_id;
    var strip = document.getElementById("status-strip");
    strip.replaceChildren();
    [
      ["As of", status.as_of], ["Status", status.overall_status], ["Confidence", status.report_confidence],
      ["Baseline", "r" + status.baseline_revision], ["Freshness", manifest.recovery_status], ["Generated", manifest.generated_at]
    ].forEach(function (item) {
      var group = create("div");
      group.appendChild(create("dt", "", item[0]));
      group.appendChild(create("dd", "", item[1]));
      strip.appendChild(group);
    });
    var quality = document.getElementById("quality-banner");
    quality.replaceChildren();
    var hiddenTopology = Number(manifest.redaction.hidden_nodes || 0) + Number(manifest.redaction.hidden_edges || 0);
    if (model.recovery.status !== "ready") {
      quality.dataset.level = model.recovery.status;
      var recoveryMessages = [];
      model.recovery.findings.forEach(function (item) {
        if (recoveryMessages.indexOf(item.message) < 0) recoveryMessages.push(item.message);
      });
      quality.textContent = model.recovery.status.toUpperCase() + ": " + recoveryMessages.join(" ");
      if (hiddenTopology) quality.textContent += " " + message("redaction.hidden-topology", "Part of the topology is hidden") + ": " + manifest.redaction.hidden_nodes + " nodes / " + manifest.redaction.hidden_edges + " edges; topology_reconnected=false.";
    } else if (hiddenTopology) {
      quality.dataset.level = "degraded";
      quality.textContent = message("redaction.hidden-topology", "Part of the topology is hidden") + ": " + manifest.redaction.hidden_nodes + " nodes / " + manifest.redaction.hidden_edges + " edges; topology_reconnected=false.";
    } else {
      delete quality.dataset.level;
    }
  }

  function renderNav() {
    document.getElementById("nav-action-ledger").hidden = viewIds.indexOf("action-ledger") < 0;
    document.getElementById("nav-risk-register").hidden = viewIds.indexOf("risk-register") < 0;
    viewIds.forEach(function (viewId) {
      var link = document.getElementById("nav-" + viewId);
      link.textContent = message("view." + viewId, viewId);
      link.href = "#v=1&view=" + viewId + "&mode=quantitative-progress";
      if (viewId === state.view) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    });
    var modes = document.getElementById("mode-control");
    modes.replaceChildren();
    modeIds.forEach(function (modeId) {
      var button = create("button", "", message("mode." + modeId, modeId));
      button.type = "button";
      button.setAttribute("aria-pressed", String(state.mode === modeId));
      button.addEventListener("click", function () { state.mode = modeId; writeHash(false); render(); });
      modes.appendChild(button);
    });
    setSelect("filter-workstream", state.workstream);
    setSelect("filter-status", state.status);
    setSelect("filter-owner", state.owner);
    setSelect("filter-period", state.period);
  }

  function renderViewHeading(root, title, subtitle) {
    var heading = create("div", "view-heading");
    var text = create("div");
    text.appendChild(create("h1", "", title));
    text.appendChild(create("p", "", subtitle));
    heading.appendChild(text);
    var scope = model.selection.flow_scopes[state.view];
    var utilitySource = state.view === "risk-register" ? "views/risk-matrix.md" : "actions/action-ledger.md";
    var identity = scope
      ? "panel " + manifest.panel_id + " / scope " + scope.layout_scope_id
      : "panel " + manifest.panel_id + " / source " + utilitySource;
    heading.appendChild(create("span", "identity-chip", identity));
    root.appendChild(heading);
  }

  function renderBullet(container, values) {
    var actual = Number(values.current.actual_completion_percent || 0);
    var planned = Number(values.current.planned_completion_percent || 0);
    var forecast = values.forecast.forecast_completion_percent;
    var wrap = create("div", "bullet-wrap");
    wrap.setAttribute("role", "img");
    wrap.setAttribute("aria-label", "Actual " + percent(values.current.actual_completion_percent) + ", planned " + percent(values.current.planned_completion_percent) + ", forecast " + percent(forecast));
    wrap.appendChild(create("strong", "", "Actual vs planned on a shared 0 to 100 percent axis"));
    var track = create("div", "bullet-track");
    var actualBar = create("div", "bullet-actual");
    actualBar.style.width = Math.max(0, Math.min(100, actual)) + "%";
    track.appendChild(actualBar);
    var marker = create("div", "bullet-planned");
    marker.style.left = Math.max(0, Math.min(100, planned)) + "%";
    track.appendChild(marker);
    if (forecast !== null && forecast !== undefined) {
      var extension = create("div", "bullet-forecast");
      extension.style.left = Math.max(0, Math.min(100, actual)) + "%";
      extension.style.width = Math.max(0, Math.min(100, Number(forecast)) - actual) + "%";
      track.appendChild(extension);
    }
    wrap.appendChild(track);
    var axis = create("div", "bullet-axis");
    ["0", "25", "50", "75", "100%"].forEach(function (item) { axis.appendChild(create("span", "", item)); });
    wrap.appendChild(axis);
    var legend = create("div", "legend");
    legend.appendChild(create("span", "", "Actual"));
    legend.appendChild(create("span", "", "Planned marker"));
    legend.appendChild(create("span", "", "Forecast / coverage " + percent(values.forecast.forecast_coverage_percent)));
    wrap.appendChild(legend);
    container.appendChild(wrap);
  }

  function renderTrend(container, overall) {
    var series = overall.series || {};
    var points = [];
    (series.actual_points || []).forEach(function (item) { points.push({ date: item.horizon_date, value: item.completion_percent, type: "actual" }); });
    (series.planned_points || []).forEach(function (item) { points.push({ date: item.horizon_date, value: item.completion_percent, type: "planned" }); });
    (series.forecast_points || []).forEach(function (item) { points.push({ date: item.horizon_date, value: item.forecast_completion_percent, type: "forecast" }); });
    var dates = Array.from(new Set(points.map(function (item) { return item.date; }))).sort();
    var shell = create("div", "trend-shell");
    var svg = createSvg("svg", { viewBox: "0 0 760 260", role: "img", "aria-labelledby": "trend-title trend-desc" });
    svg.appendChild(createSvg("title", { id: "trend-title" }, "Milestone step trend"));
    svg.appendChild(createSvg("desc", { id: "trend-desc" }, "Canonical actual, planned, and forecast milestone steps."));
    [0, 25, 50, 75, 100].forEach(function (value) {
      var y = 220 - value * 1.8;
      svg.appendChild(createSvg("line", { x1: 54, y1: y, x2: 735, y2: y, class: "trend-grid" }));
      svg.appendChild(createSvg("text", { x: 8, y: y + 4, fill: "#5c6773", "font-size": 11 }, value + "%"));
    });
    function stepPath(type, valueKey) {
      var selected = points.filter(function (item) { return item.type === type; }).sort(function (a, b) { return a.date.localeCompare(b.date); });
      if (!selected.length) return "";
      var path = "";
      selected.forEach(function (item, index) {
        var x = dates.length <= 1 ? 390 : 70 + (dates.indexOf(item.date) / (dates.length - 1)) * 630;
        var y = 220 - Number(item.value || 0) * 1.8;
        if (index === 0) path += "M " + x + " " + y;
        else path += " H " + x + " V " + y;
      });
      return path;
    }
    [["actual", "trend-actual"], ["planned", "trend-planned"], ["forecast", "trend-forecast"]].forEach(function (item) {
      var path = stepPath(item[0]);
      if (path) svg.appendChild(createSvg("path", { d: path, class: item[1] }));
      points.filter(function (point) { return point.type === item[0]; }).forEach(function (point) {
        var x = dates.length <= 1 ? 390 : 70 + (dates.indexOf(point.date) / (dates.length - 1)) * 630;
        var y = 220 - Number(point.value || 0) * 1.8;
        svg.appendChild(createSvg("circle", { cx: x, cy: y, r: 5, class: "trend-point-" + item[0] }));
      });
    });
    dates.forEach(function (date, index) {
      var x = dates.length <= 1 ? 390 : 70 + (index / (dates.length - 1)) * 630;
      svg.appendChild(createSvg("text", { x: x, y: 245, fill: "#5c6773", "font-size": 11, "text-anchor": "middle" }, date));
    });
    shell.appendChild(svg);
    container.appendChild(shell);
  }

  function sortableTable(rows) {
    var wrap = create("div", "data-table-wrap");
    var table = create("table", "data-table");
    table.appendChild(create("caption", "", "Canonical workstream progress; values are copied from program-status."));
    var columns = [
      ["scope_id", "Scope"], ["scope_kind", "Scope kind"], ["measurement_status", "Measurement"], ["project_weight_percent", "Project weight"],
      ["completed_contribution_pp", "Contribution"], ["actual_completion_percent", "Actual"], ["planned_completion_percent", "Planned"],
      ["completion_gap_pp", "Gap (pp)"], ["forecast_completion_percent", "Forecast / coverage"]
    ];
    var head = create("thead");
    var headerRow = create("tr");
    columns.forEach(function (column) {
      var th = create("th");
      th.scope = "col";
      th.setAttribute("aria-sort", sortState.key === column[0] ? sortState.direction : "none");
      var button = create("button", "", column[1]);
      button.type = "button";
      button.addEventListener("click", function () {
        if (sortState.key === column[0]) sortState.direction = sortState.direction === "ascending" ? "descending" : "ascending";
        else { sortState.key = column[0]; sortState.direction = "ascending"; }
        render();
      });
      th.appendChild(button);
      headerRow.appendChild(th);
    });
    head.appendChild(headerRow);
    table.appendChild(head);
    var body = create("tbody");
    rows.slice().sort(function (a, b) {
      var first = sortValue(a, sortState.key);
      var second = sortValue(b, sortState.key);
      var result = typeof first === "number" && typeof second === "number" ? first - second : String(first).localeCompare(String(second));
      return sortState.direction === "ascending" ? result : -result;
    }).forEach(function (item) {
      var row = create("tr");
      var current = item.current || {};
      var forecast = item.forecast_summary || {};
      [
        item.scope_id, item.scope_kind, item.measurement_status, percent(current.project_weight_percent), percent(current.completed_contribution_pp, " pp"),
        percent(current.actual_completion_percent), percent(current.planned_completion_percent), percent(current.completion_gap_pp, " pp"),
        percent(forecast.forecast_completion_percent) + " / " + percent(forecast.forecast_coverage_percent)
      ].forEach(function (value, index) {
        var cell = create(index === 0 ? "th" : "td", "", value);
        if (index === 0) cell.scope = "row";
        cell.dataset.label = columns[index][1];
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
    table.appendChild(body);
    wrap.appendChild(table);
    return wrap;
  }

  function sortValue(item, key) {
    if (key === "scope_id" || key === "scope_kind" || key === "measurement_status") return item[key] || "";
    if (key === "forecast_completion_percent") return Number((item.forecast_summary || {})[key] || -1);
    return Number((item.current || {})[key] || -1);
  }

  function filteredWorkstreams() {
    var query = document.getElementById("filter-search").value.trim().toLocaleLowerCase();
    return (model.data.status.progress.by_scope || []).filter(function (item) {
      if (state.workstream !== "all" && item.scope_id !== state.workstream) return false;
      if (state.status !== "all" && item.measurement_status !== state.status && (item.gate_readiness || {}).readiness_status !== state.status) return false;
      return !query || JSON.stringify(item).toLocaleLowerCase().indexOf(query) >= 0;
    });
  }

  function renderProject(root) {
    renderViewHeading(root, message("view.project-lead", "Project lead"), "Completion and plan health remain separate canonical conclusions.");
    var values = currentProgress();
    var summary = section("Completion at " + values.label, "pl-progress-summary");
    var metrics = create("div", "metrics");
    metrics.appendChild(metric("Actual completion", percent(values.current.actual_completion_percent), "Evidence-qualified milestone weight"));
    metrics.appendChild(metric("Planned completion", percent(values.current.planned_completion_percent), "Approved baseline through the selected date"));
    metrics.appendChild(metric("Completion gap", percent(values.current.completion_gap_pp, " pp"), "Actual minus planned; not date variance"));
    metrics.appendChild(metric("Next-period forecast", percent(values.forecast.forecast_completion_percent), "Coverage " + percent(values.forecast.forecast_coverage_percent) + " / " + (values.forecast.forecast_coverage_status || "not available")));
    summary.appendChild(metrics);
    var health = create("div", "health-block");
    health.dataset.status = model.data.status.overall_status;
    var healthTitle = create("div");
    healthTitle.appendChild(create("strong", "", "Plan health"));
    healthTitle.appendChild(document.createElement("br"));
    healthTitle.appendChild(statusToken(model.data.status.overall_status));
    health.appendChild(healthTitle);
    health.appendChild(create("p", "", "Canonical overall status, critical path, gates, and date variance answer whether delivery is on plan. Progress percentage does not override this judgment."));
    summary.appendChild(health);
    renderBullet(summary, values);
    if (values.history) {
      summary.appendChild(create("p", "warning", "Historical comparison is shown side by side. Forecast is intentionally absent when it is not part of the selected immutable snapshot."));
    }
    root.appendChild(summary);
    var trendScope = state.workstream === "all" ? model.data.status.progress.overall : (model.data.status.progress.by_scope || []).find(function (item) { return item.scope_id === state.workstream; }) || model.data.status.progress.overall;
    var trend = section("Milestone step trend / scope " + (trendScope.scope_id || trendScope.workstream_id || "program"), "pl-progress-trend");
    renderTrend(trend, trendScope);
    root.appendChild(trend);
    var tableSection = section("Workstream comparison", "pl-workstream-comparison");
    var rows = filteredWorkstreams();
    tableSection.appendChild(sortableTable(rows));
    root.appendChild(tableSection);
    document.getElementById("result-count").textContent = rows.length + " scopes";
  }

  function readinessBlock(meeting) {
    var root = create("div", "meeting-readiness");
    var windowValue = (meeting.meeting_window || {}).start + " to " + (meeting.meeting_window || {}).end + " (" + ((meeting.meeting_window || {}).status || "unavailable") + ")";
    [["Pack ID", meeting.meeting_pack_id], ["Meeting readiness", meeting.readiness], ["Artifact lifecycle", meeting.lifecycle], ["Meeting window", windowValue]].forEach(function (item) {
      var block = create("div");
      block.appendChild(create("strong", "", item[0]));
      block.appendChild(create("div", "", item[1] || "unavailable"));
      root.appendChild(block);
    });
    return root;
  }

  function detailText(value) {
    if (value === null || value === undefined || value === "") return "";
    if (Array.isArray(value)) return value.map(detailText).filter(Boolean).join("; ");
    if (typeof value === "object") {
      return Object.keys(value).sort().map(function (key) {
        var nested = detailText(value[key]);
        return nested ? key + ": " + nested : "";
      }).filter(Boolean).join(" · ");
    }
    return String(value);
  }

  function firstItemField(item, names) {
    for (var index = 0; index < names.length; index += 1) {
      var value = detailText((item || {})[names[index]]);
      if (value && value !== "TBD") return value;
    }
    return "";
  }

  function safeSourceReference(value) {
    if (typeof value !== "string") return null;
    var normalized = value.trim().replace(/\\/g, "/");
    if (!normalized || /^[a-z][a-z0-9+.-]*:/i.test(normalized) || normalized.charAt(0) === "/" || /^[a-z]:\//i.test(normalized)) return null;
    var hashAt = normalized.indexOf("#");
    var fragment = hashAt >= 0 ? normalized.slice(hashAt + 1) : "";
    var sourcePath = hashAt >= 0 ? normalized.slice(0, hashAt) : normalized;
    var memoryPrefix = "_bmad-output/adp/memory/";
    if (sourcePath.indexOf(memoryPrefix) === 0) sourcePath = sourcePath.slice(memoryPrefix.length);
    var segments = sourcePath.split("/");
    if (!sourcePath || segments.some(function (segment) { return !segment || segment === "." || segment === ".."; })) return null;
    return { path: sourcePath, fragment: fragment };
  }

  function legacyStructuredSourcePath(value) {
    if (typeof value !== "string") return null;
    var text = value.trim();
    if (text.length > 4096 || (text.charAt(0) !== "{" && text.charAt(0) !== "[")) return null;
    var match = text.match(/(?:^|[\[,{]\s*)['"]artifact_path['"]\s*:\s*(['"])([^'"\\\r\n]+)\1/);
    return match ? match[2] : null;
  }

  function meetingItemSource(item) {
    var structured = [item && item.source, item && item.Source].filter(function (value) { return value && typeof value === "object" && !Array.isArray(value); });
    var candidates = [
      item && item.source_path, item && item["Source path"], item && item.artifact_path, item && item["Artifact path"]
    ];
    structured.forEach(function (value) { candidates.push(value.artifact_path, value.path); });
    candidates.push(legacyStructuredSourcePath(item && item.source), legacyStructuredSourcePath(item && item.Source));
    if (item && typeof item.source === "string" && "[{".indexOf(item.source.trim().charAt(0)) < 0) candidates.push(item.source);
    if (item && typeof item.Source === "string" && "[{".indexOf(item.Source.trim().charAt(0)) < 0) candidates.push(item.Source);
    for (var index = 0; index < candidates.length; index += 1) {
      var reference = safeSourceReference(candidates[index]);
      if (reference) return reference;
    }
    return null;
  }

  function sourceHref(reference) {
    var encodedPath = reference.path.split("/").map(encodeURIComponent).join("/");
    return "../../" + encodedPath + (reference.fragment ? "#" + encodeURIComponent(reference.fragment) : "");
  }

  function markdownDocument(content, className) {
    var root = create("article", className || "markdown-document");
    if (!markdownRenderer) {
      root.appendChild(create("pre", "markdown-fallback", content));
      return root;
    }
    var parsed = new DOMParser().parseFromString(markdownRenderer.render(content), "text/html");
    var allowed = new Set([
      "P", "H1", "H2", "H3", "H4", "H5", "H6", "UL", "OL", "LI", "BLOCKQUOTE",
      "PRE", "CODE", "EM", "STRONG", "S", "TABLE", "THEAD", "TBODY", "TR", "TH", "TD",
      "HR", "BR", "SPAN"
    ]);
    Array.from(parsed.body.querySelectorAll("*")).forEach(function (node) {
      if (!allowed.has(node.tagName)) {
        node.parentNode.replaceChild(document.createTextNode(node.textContent || ""), node);
        return;
      }
      Array.from(node.attributes).forEach(function (attribute) { node.removeAttribute(attribute.name); });
    });
    while (parsed.body.firstChild) root.appendChild(parsed.body.firstChild);
    return root;
  }

  function openSourcePreview(reference, preview, trigger) {
    var current = document.getElementById("source-preview-dialog");
    if (current) current.close();
    var dialog = create("dialog", "source-preview-dialog");
    dialog.id = "source-preview-dialog";
    dialog.setAttribute("aria-labelledby", "source-preview-title");
    var header = create("header");
    var heading = create("div", "source-preview-heading");
    var title = create("h2", "", message("source.preview.title", "Source file preview"));
    title.id = "source-preview-title";
    heading.appendChild(title);
    heading.appendChild(create("code", "", reference.path));
    header.appendChild(heading);
    var close = create("button", "", message("common.close", "Close"));
    close.type = "button";
    close.addEventListener("click", function () { dialog.close(); });
    header.appendChild(close);
    dialog.appendChild(header);
    var body = create("div", "source-preview-body");
    var meta = create("div", "source-preview-meta");
    meta.appendChild(create("strong", "", message("source.preview.markdown", "Markdown preview")));
    meta.appendChild(create("span", "", String(preview.bytes) + " bytes"));
    body.appendChild(meta);
    if (preview.truncated) body.appendChild(create("p", "warning", message("source.preview.truncated", "This file is large; only the first 256 KiB is shown.")));
    var content = markdownDocument(preview.content, "source-preview-content markdown-document");
    content.tabIndex = 0;
    body.appendChild(content);
    dialog.appendChild(body);
    var footer = create("footer");
    var external = create("a", "source-external-link", message("source.preview.open-external", "Open in a new tab"));
    external.href = sourceHref(reference);
    external.target = "_blank";
    external.rel = "noopener";
    footer.appendChild(external);
    dialog.appendChild(footer);
    document.body.appendChild(dialog);
    dialog.addEventListener("click", function (event) {
      if (event.target !== dialog) return;
      var bounds = dialog.getBoundingClientRect();
      var outside = event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom;
      if (outside) dialog.close();
    });
    dialog.addEventListener("close", function () {
      dialog.remove();
      if (trigger && trigger.isConnected) trigger.focus();
    });
    dialog.showModal();
    close.focus();
  }

  function sourceReferenceLink(reference) {
    if (!reference) return null;
    var row = create("div", "source-reference");
    var preview = sourcePreviewByPath.get(reference.path);
    if (preview) {
      var button = create("button", "source-link", message("common.view-source", "View source file"));
      button.type = "button";
      button.addEventListener("click", function () { openSourcePreview(reference, preview, button); });
      row.appendChild(button);
      var external = create("a", "source-external-link", message("source.preview.open-external", "Open in a new tab"));
      external.href = sourceHref(reference);
      external.target = "_blank";
      external.rel = "noopener";
      row.appendChild(external);
    } else {
      var link = create("a", "source-link", message("common.view-source", "View source file"));
      link.href = sourceHref(reference);
      link.target = "_blank";
      link.rel = "noopener";
      row.appendChild(link);
    }
    row.appendChild(create("code", "", reference.path));
    return row;
  }

  function meetingItemId(item) {
    return firstItemField(item, ["action_id", "Action ID", "risk_id", "Risk ID", "decision_id", "Decision ID", "question_id", "Question ID", "id", "ID"]);
  }

  function structuredActionId(item) {
    return firstItemField(item, ["action_id", "Action ID"]);
  }

  function structuredRiskId(item) {
    return firstItemField(item, ["risk_id", "Risk ID"]);
  }

  function openActionLedger(actionId) {
    state.view = "action-ledger";
    state.mode = "quantitative-progress";
    state.action = actionId || "";
    writeHash(false);
    render();
  }

  function actionLedgerButton(actionId) {
    if (!actionId || !actionLedgerPreview || manifest.distribution_profile !== "internal-full") return null;
    var button = create("button", "action-ledger-link", message("action.ledger.open", "View in action ledger"));
    button.type = "button";
    button.addEventListener("click", function () { openActionLedger(actionId); });
    return button;
  }

  function openRiskRegister(riskId) {
    state.view = "risk-register";
    state.mode = "quantitative-progress";
    state.risk = riskId || "";
    writeHash(false);
    render();
  }

  function riskRegisterButton(riskId) {
    if (!riskId || !riskRegisterPreview || manifest.distribution_profile !== "internal-full") return null;
    var button = create("button", "risk-register-link", message("risk.register.open", "View in risk register"));
    button.type = "button";
    button.addEventListener("click", function () { openRiskRegister(riskId); });
    return button;
  }

  function meetingItemTitle(item, boardTitle, id) {
    var title = firstItemField(item, ["summary", "title", "Title", "Action", "Decision", "Question", "Risk", "Item", "Gap", "Dependency / Blocker", "Business Impact", "Gate"]);
    if (title && title !== id) return title;
    var node = Object.keys(model.data.flows || {}).reduce(function (found, viewId) {
      return found || ((model.data.flows[viewId].nodes || []).find(function (candidate) { return candidate.node_id === id; }));
    }, null);
    if (node) return node.name || id;
    return id ? boardTitle + " · " + id : boardTitle;
  }

  function meetingItemDetails(item) {
    var hidden = new Set(["source", "Source", "source_fingerprint", "related_plan_item_ids", "related_flow_edge_ids", "plan_item_ids"]);
    return Object.keys(item || {}).filter(function (key) {
      return !hidden.has(key) && detailText(item[key]);
    }).map(function (key) { return { label: key.replace(/_/g, " "), value: detailText(item[key]) }; });
  }

  function normalizeMeetingItem(item, boardTitle, index, itemType) {
    var record = item && typeof item === "object" ? item : { Item: item };
    var id = meetingItemId(record);
    var title = meetingItemTitle(record, boardTitle, id);
    var owner = firstItemField(record, ["owner", "Owner"]);
    var status = firstItemField(record, ["status", "Status", "readiness", "Readiness", "severity", "Severity"]);
    var workstream = firstItemField(record, ["workstream", "Workstream", "workstreams", "Workstreams"]);
    var due = firstItemField(record, ["due", "Due", "Due / Trigger", "Deadline / Trigger", "forecast", "Forecast"]);
    return {
      id: id || boardTitle.toLowerCase().replace(/[^a-z0-9]+/g, "-") + "-" + String(index + 1),
      title: title,
      owner: owner,
      status: status,
      workstream: workstream,
      due: due,
      actionId: structuredActionId(record),
      riskId: structuredRiskId(record) || (itemType === "risk" ? id : ""),
      details: meetingItemDetails(record),
      sourceReference: meetingItemSource(record),
      raw: record
    };
  }

  function appendDetailList(root, details) {
    var list = create("dl", "item-detail-list");
    (details || []).forEach(function (item) {
      list.appendChild(create("dt", "", item.label));
      list.appendChild(create("dd", "", item.value));
    });
    if (!details || !details.length) {
      list.appendChild(create("dt", "", message("common.details", "Details")));
      list.appendChild(create("dd", "muted", message("flow.item.content-unavailable", "No additional canonical content is available in this panel snapshot.")));
    }
    root.appendChild(list);
  }

  function meetingItemDisclosure(record) {
    var item = create("details", "meeting-item");
    var summary = create("summary");
    var heading = create("span", "meeting-item-heading");
    heading.appendChild(create("strong", "meeting-item-title", record.title));
    if (record.id && record.title.indexOf(record.id) < 0) heading.appendChild(create("span", "meeting-item-id", record.id));
    summary.appendChild(heading);
    var metadata = create("span", "meeting-item-metadata");
    [record.status, record.owner, record.due, record.workstream].filter(Boolean).slice(0, 3).forEach(function (value) {
      metadata.appendChild(create("span", "", value));
    });
    summary.appendChild(metadata);
    item.appendChild(summary);
    var content = create("div", "meeting-item-content");
    var ledgerButton = actionLedgerButton(record.actionId);
    if (ledgerButton) content.appendChild(ledgerButton);
    var riskButton = riskRegisterButton(record.riskId);
    if (riskButton) content.appendChild(riskButton);
    var source = sourceReferenceLink(record.sourceReference);
    if (source) content.appendChild(source);
    appendDetailList(content, record.details);
    item.appendChild(content);
    return item;
  }

  function meetingBoards(groups) {
    var root = create("div", "meeting-boards");
    var controls = create("div", "meeting-board-tabs");
    controls.setAttribute("role", "tablist");
    var panel = create("div", "meeting-board-panel");
    panel.setAttribute("role", "tabpanel");
    var active = groups.find(function (group) { return (group.items || []).length; }) || groups[0];

    function renderGroup(group) {
      active = group;
      controls.querySelectorAll("button").forEach(function (button) {
        var selected = button.dataset.boardKey === group.key;
        button.setAttribute("aria-selected", String(selected));
        button.setAttribute("tabindex", selected ? "0" : "-1");
      });
      panel.replaceChildren();
      panel.setAttribute("aria-label", group.label);
      var list = create("div", "meeting-item-list");
      if (!group.items || !group.items.length) list.appendChild(create("p", "meeting-empty muted", message("meeting.empty", "None in the canonical meeting pack.")));
      var itemType = boardItemType(group.key);
      (group.items || []).forEach(function (item, index) { list.appendChild(meetingItemDisclosure(normalizeMeetingItem(item, group.label, index, itemType))); });
      panel.appendChild(list);
    }

    groups.forEach(function (group) {
      var button = create("button", "", group.label + " " + (group.items || []).length);
      button.type = "button";
      button.dataset.boardKey = group.key;
      button.setAttribute("role", "tab");
      button.addEventListener("click", function () { renderGroup(group); });
      controls.appendChild(button);
    });
    root.appendChild(controls);
    root.appendChild(panel);
    renderGroup(active);
    return root;
  }

  function renderFde(root) {
    var meeting = model.data.meetings["fde-morning"];
    renderViewHeading(root, message("view.fde-morning", "FDE morning"), "Confirmed window changes, blockers, commitments, and one dependency context layer.");
    var readiness = section("Meeting context", "fde-meeting-readiness");
    readiness.appendChild(readinessBlock(meeting));
    root.appendChild(readiness);
    var delta = section("Since last meeting", "fde-window-delta");
    var current = model.data.status.progress.overall.current;
    var metrics = create("div", "metrics");
    metrics.appendChild(metric("Current completion gap", percent(current.completion_gap_pp, " pp"), "Canonical current status"));
    var deltaRows = meeting.boards.fde_period_delta || [];
    deltaRows.slice(0, 3).forEach(function (item) { metrics.appendChild(metric(item.workstream || "Window delta", percent(item.actual_delta_pp, " pp"), "Confirmed meeting window")); });
    delta.appendChild(metrics);
    delta.appendChild(create("p", "muted", "Long-range forecast is intentionally not resident in the FDE morning view."));
    root.appendChild(delta);
    var boards = section("Execution closure", "fde-blockers-commitments");
    boards.appendChild(meetingBoards([
      { key: "blockers", label: "Blockers", items: meeting.boards.fde_blockers || [] },
      { key: "commitments", label: "Commitments", items: meeting.boards.fde_commitments || [] },
      { key: "due", label: "Due today", items: meeting.boards.fde_due || [] },
      { key: "escalations", label: "Escalations", items: meeting.boards.fde_escalations || meeting.boards.escalations || [] }
    ]));
    root.appendChild(boards);
    document.getElementById("result-count").textContent = deltaRows.length + " window deltas";
  }

  function renderBusiness(root) {
    var meeting = model.data.meetings["business-biweekly"];
    var progress = model.data.status.progress.overall;
    renderViewHeading(root, message("view.business-biweekly", "Business biweekly"), "Plan judgment, next-period outlook, exceptions, and management decisions.");
    var readiness = section("Management meeting context", "biz-meeting-readiness");
    readiness.appendChild(readinessBlock(meeting));
    root.appendChild(readiness);
    var next = section("Next biweekly outlook", "biz-next-period-progress");
    var metrics = create("div", "metrics");
    metrics.appendChild(metric("Overall status", model.data.status.overall_status, "Independent plan-health judgment"));
    metrics.appendChild(metric("Completion gap", percent(progress.current.completion_gap_pp, " pp"), "Actual minus planned"));
    metrics.appendChild(metric("Next forecast", percent(progress.forecast_summary.forecast_completion_percent), progress.forecast_summary.horizon_date || "No horizon"));
    metrics.appendChild(metric("Forecast coverage", percent(progress.forecast_summary.forecast_coverage_percent), progress.forecast_summary.forecast_coverage_status));
    next.appendChild(metrics);
    renderBullet(next, { current: progress.current, forecast: progress.forecast_summary });
    root.appendChild(next);
    var decisions = section("Exceptions and decisions", "biz-decisions");
    decisions.appendChild(meetingBoards([
      { key: "decisions", label: "Decisions", items: meeting.boards.business_decisions || [] },
      { key: "variances", label: "Top variances", items: meeting.boards.top_variances || [] },
      { key: "readiness", label: "Readiness", items: meeting.boards.business_readiness || [] },
      { key: "impact", label: "Business impact", items: meeting.boards.cross_line_business_impact || [] }
    ]));
    root.appendChild(decisions);
    document.getElementById("result-count").textContent = (meeting.boards.business_decisions || []).length + " decisions";
  }

  function renderRegisterView(root, options) {
    renderViewHeading(
      root,
      message(options.viewMessage, options.viewFallback),
      message(options.subtitleMessage, options.subtitleFallback)
    );
    var register = create("section", "band");
    register.id = options.viewId + "-view";
    if (!options.preview) {
      register.appendChild(create("p", "meeting-empty muted", message(options.unavailableMessage, options.unavailableFallback)));
      root.appendChild(register);
      document.getElementById("result-count").textContent = "0 " + options.countLabel;
      return;
    }

    var search = create("form", "register-controls " + options.viewId + "-controls");
    search.setAttribute("role", "search");
    var label = create("label", "", message(options.searchMessage, options.searchFallback));
    var input = create("input");
    input.id = options.viewId + "-search";
    input.type = "search";
    input.autocomplete = "off";
    input.value = state[options.stateKey] || "";
    label.appendChild(input);
    search.appendChild(label);
    var locate = create("button", "", message(options.locateMessage, "Locate"));
    locate.type = "submit";
    search.appendChild(locate);
    var status = create("output", "register-search-status " + options.viewId + "-search-status");
    status.setAttribute("aria-live", "polite");
    search.appendChild(status);
    register.appendChild(search);

    var documentView = markdownDocument(options.preview.content, "register-document " + options.viewId + "-document markdown-document");
    var documentTitle = documentView.querySelector("h1");
    if (documentTitle) documentTitle.remove();
    var targets = options.targets(documentView).filter(function (target) { return target.code; });
    targets.forEach(function (target, index) {
      target.element.dataset.registerCode = target.code;
      target.element.dataset[options.datasetKey] = target.code;
      target.element.id = options.viewId + "-entry-" + String(index + 1);
      target.element.tabIndex = -1;
    });
    register.appendChild(documentView);
    root.appendChild(register);

    function findItem(value) {
      var query = value.trim().toLocaleLowerCase();
      documentView.querySelectorAll(".is-register-target").forEach(function (item) { item.classList.remove("is-register-target"); });
      if (!query) {
        status.textContent = "";
        return null;
      }
      var target = targets.find(function (item) { return item.code.toLocaleLowerCase() === query; })
        || targets.find(function (item) { return item.code.toLocaleLowerCase().indexOf(query) >= 0; });
      if (!target) {
        status.textContent = message(options.notFoundMessage, options.notFoundFallback) + ": " + value.trim();
        return null;
      }
      target.element.classList.add("is-register-target");
      status.textContent = message(options.foundMessage, "Located") + ": " + target.code;
      target.element.scrollIntoView({ block: "center" });
      target.element.focus({ preventScroll: true });
      return target.element;
    }

    search.addEventListener("submit", function (event) {
      event.preventDefault();
      state[options.stateKey] = input.value.trim();
      writeHash(false);
      findItem(state[options.stateKey]);
    });
    input.addEventListener("input", function () {
      if (!input.value) findItem("");
    });
    document.getElementById("result-count").textContent = targets.length + " " + options.countLabel;
    if (state[options.stateKey]) window.requestAnimationFrame(function () { findItem(state[options.stateKey]); });
  }

  function normalizeRegisterHeader(value) {
    return value.toLocaleLowerCase().replace(/[\s_-]+/g, "");
  }

  function registerTableTargets(documentView, headerNames) {
    var expected = new Set(headerNames.map(normalizeRegisterHeader));
    return Array.from(documentView.querySelectorAll("table")).reduce(function (targets, table) {
      var headers = Array.from(table.querySelectorAll("thead th"));
      var columnIndex = headers.findIndex(function (header) {
        return expected.has(normalizeRegisterHeader(header.textContent.trim()));
      });
      if (columnIndex < 0) return targets;
      Array.from(table.querySelectorAll("tbody tr")).forEach(function (row) {
        var cell = row.children[columnIndex];
        if (!cell || cell.tagName !== "TD") return;
        targets.push({ element: row, code: cell.textContent.replace(/\s+/g, " ").trim() });
      });
      return targets;
    }, []);
  }

  function registerHeadingTargets(documentView) {
    return Array.from(documentView.querySelectorAll("h2, h3")).map(function (heading) {
      return { element: heading, code: heading.textContent.trim() };
    }).filter(function (target) {
      return /^[A-Za-z0-9][A-Za-z0-9._:/-]{1,95}$/.test(target.code) && (/\d/.test(target.code) || /[-_:/]/.test(target.code));
    });
  }

  function renderActionLedger(root) {
    renderRegisterView(root, {
      viewId: "action-ledger",
      viewMessage: "view.action-ledger",
      viewFallback: "Action ledger",
      subtitleMessage: "action.ledger.subtitle",
      subtitleFallback: "Canonical actions from the sealed memory snapshot.",
      preview: actionLedgerPreview,
      unavailableMessage: "action.ledger.unavailable",
      unavailableFallback: "The action ledger is unavailable in this panel snapshot.",
      searchMessage: "action.ledger.search",
      searchFallback: "Action ID",
      locateMessage: "action.ledger.locate",
      foundMessage: "action.ledger.found",
      notFoundMessage: "action.ledger.not-found",
      notFoundFallback: "Action not found",
      stateKey: "action",
      datasetKey: "actionCode",
      countLabel: "actions",
      targets: function (documentView) {
        return registerHeadingTargets(documentView).concat(registerTableTargets(documentView, ["Action ID", "action_id", "行动项编号", "行动编号"]));
      }
    });
  }

  function renderRiskRegister(root) {
    renderRegisterView(root, {
      viewId: "risk-register",
      viewMessage: "view.risk-register",
      viewFallback: "Risk register",
      subtitleMessage: "risk.register.subtitle",
      subtitleFallback: "Canonical risks from the sealed memory snapshot.",
      preview: riskRegisterPreview,
      unavailableMessage: "risk.register.unavailable",
      unavailableFallback: "The risk register is unavailable in this panel snapshot.",
      searchMessage: "risk.register.search",
      searchFallback: "Risk ID",
      locateMessage: "risk.register.locate",
      foundMessage: "risk.register.found",
      notFoundMessage: "risk.register.not-found",
      notFoundFallback: "Risk not found",
      stateKey: "risk",
      datasetKey: "riskCode",
      countLabel: "risks",
      targets: function (documentView) {
        return registerTableTargets(documentView, ["Risk ID", "risk_id", "ID", "风险 ID", "风险编号"]);
      }
    });
  }

  function filteredFlow() {
    var source = model.data.flows[state.view] || {};
    var states = new Map((source.node_states || []).map(function (item) { return [item.node_id, item]; }));
    var query = document.getElementById("filter-search").value.trim().toLocaleLowerCase();
    var nodes = (source.nodes || []).filter(function (node) {
      var nodeState = states.get(node.node_id) || {};
      var execution = (nodeState.execution || {}).value;
      var health = (nodeState.health || {}).value;
      var lane = (node.lane || {}).lane_id || "PROGRAM";
      if (collapsedLanes.has(lane)) return false;
      if (state.workstream !== "all" && lane !== state.workstream) return false;
      if (state.status !== "all" && !flowStatusMatches(state.status, execution, health)) return false;
      if (state.owner !== "all" && node.owner !== state.owner) return false;
      return !query || JSON.stringify(node).toLocaleLowerCase().indexOf(query) >= 0;
    });
    var ids = new Set(nodes.map(function (node) { return node.node_id; }));
    var edges = (source.edges || []).filter(function (edge) { return ids.has(edge.predecessor) && ids.has(edge.target); });
    var edgeIds = new Set(edges.map(function (edge) { return edge.edge_id; }));
    return {
      nodes: nodes,
      edges: edges,
      node_states: (source.node_states || []).filter(function (item) { return ids.has(item.node_id); }),
      relationship_states: (source.relationship_states || []).filter(function (item) { return edgeIds.has(item.edge_id); }),
      allocations: (source.allocations || []).filter(function (item) { return item.target_type === "node" ? ids.has(item.target_id) : edgeIds.has(item.target_id); }),
      unmapped: source.unmapped || [],
      empty_state: source.empty_state || null,
      meeting_window: source.meeting_window || null,
      recovery: source.recovery || [],
      selection_id: source.selection_id,
      scope_id: source.scope_id
    };
  }

  function nodeAllocation(flow, nodeId) {
    return (flow.allocations || []).find(function (item) { return item.target_type === "node" && item.target_id === nodeId; });
  }

  function relatedItemType(sourceKind) {
    if (sourceKind === "risk") return "risk";
    if (sourceKind === "decision") return "decision";
    if (sourceKind === "question" || sourceKind === "open-question") return "open-question";
    return "todo";
  }

  function boardItemType(boardName) {
    if (boardName.indexOf("decision") >= 0) return "decision";
    if (boardName.indexOf("question") >= 0) return "open-question";
    if (boardName.indexOf("risk") >= 0 || boardName.indexOf("blocker") >= 0) return "risk";
    if (boardName.indexOf("commitment") >= 0 || boardName.indexOf("due") >= 0 || boardName.indexOf("action") >= 0) return "todo";
    return null;
  }

  function relatedTypeTitle(type) {
    return {
      decision: message("flow.action.decision", "Decision"),
      todo: message("flow.action.todo", "To-do"),
      "open-question": message("flow.action.open-question", "Open question"),
      risk: message("flow.action.risk", "Risk")
    }[type] || type;
  }

  function relatedItemCatalog() {
    var catalog = new Map();
    Object.keys(model.data.meetings || {}).forEach(function (meetingId) {
      var boards = (model.data.meetings[meetingId] || {}).boards || {};
      Object.keys(boards).forEach(function (boardName) {
        var type = boardItemType(boardName);
        if (!type || !Array.isArray(boards[boardName])) return;
        var boardTitle = boardName.replace(/_/g, " ").replace(/\b\w/g, function (letter) { return letter.toUpperCase(); });
        boards[boardName].forEach(function (item, index) {
          if (!item || typeof item !== "object") return;
          var id = meetingItemId(item);
          if (!id) return;
          var record = normalizeMeetingItem(item, boardTitle, index, type);
          record.type = type;
          var key = type + ":" + id;
          var existing = catalog.get(key);
          if (!existing || record.details.length > existing.details.length) catalog.set(key, record);
        });
      });
    });
    return catalog;
  }

  function appendRelatedItem(items, item) {
    var key = item.type + ":" + item.id;
    var existing = items.get(key);
    if (existing) {
      item.states.forEach(function (value) { if (existing.states.indexOf(value) < 0) existing.states.push(value); });
      if ((item.details || []).length > (existing.details || []).length) {
        existing.title = item.title;
        existing.details = item.details;
      }
      if (!existing.sourceReference && item.sourceReference) existing.sourceReference = item.sourceReference;
      if (!existing.actionId && item.actionId) existing.actionId = item.actionId;
      if (!existing.riskId && item.riskId) existing.riskId = item.riskId;
      return;
    }
    items.set(key, item);
  }

  function relatedNodeItems(node, flow) {
    var items = new Map();
    var catalog = relatedItemCatalog();
    var allocation = nodeAllocation(flow, node.node_id);
    Object.keys((allocation || {}).counts || {}).forEach(function (category) {
      (((allocation || {}).counts[category] || {}).source_refs || []).forEach(function (source) {
        var type = relatedItemType(source.source_kind);
        var record = catalog.get(type + ":" + source.source_id);
        appendRelatedItem(items, {
          type: type,
          id: source.source_id,
          title: record ? record.title : relatedTypeTitle(type) + " · " + source.source_id,
          states: [category],
          actionId: record ? record.actionId : (source.source_kind === "action" ? source.source_id : null),
          riskId: record ? (record.riskId || (type === "risk" ? source.source_id : null)) : (source.source_kind === "risk" ? source.source_id : null),
          sourceReference: record ? record.sourceReference : null,
          details: record ? record.details : [
            { label: "Canonical ID", value: source.source_id },
            { label: "Flow state", value: category },
            { label: "Source fingerprint", value: source.source_fingerprint || message("flow.item.content-unavailable", "Unavailable") }
          ]
        });
      });
    });

    if (manifest.distribution_profile === "internal-full") {
      Object.keys(model.data.meetings || {}).forEach(function (meetingId) {
        var boards = (model.data.meetings[meetingId] || {}).boards || {};
        Object.keys(boards).forEach(function (boardName) {
          var type = boardItemType(boardName);
          if (!type || !Array.isArray(boards[boardName])) return;
          boards[boardName].forEach(function (item) {
            if (!item || typeof item !== "object") return;
            var related = item.related_plan_item_ids || item.plan_item_ids;
            if (!Array.isArray(related) || related.indexOf(node.node_id) < 0) return;
            var id = meetingItemId(item);
            if (!id) return;
            var record = catalog.get(type + ":" + id) || normalizeMeetingItem(item, relatedTypeTitle(type), 0, type);
            appendRelatedItem(items, {
              type: type,
              id: String(id),
              title: record.title,
              states: firstItemField(item, ["status", "Status"]) ? [firstItemField(item, ["status", "Status"])] : [],
              actionId: record.actionId,
              riskId: record.riskId || (type === "risk" ? String(id) : null),
              sourceReference: record.sourceReference,
              details: record.details
            });
          });
        });
      });

      (model.data.roadmap.blocked_by_decisions || []).forEach(function (relation) {
        if (!relation || relation.target !== node.node_id) return;
        var id = relation.decision_id || relation.id;
        if (!id) return;
        var record = catalog.get("decision:" + id);
        appendRelatedItem(items, {
          type: "decision",
          id: String(id),
          title: record ? record.title : relatedTypeTitle("decision") + " · " + id,
          states: ["blocks node"],
          sourceReference: record ? record.sourceReference : null,
          details: record ? record.details : [
            { label: "Canonical ID", value: String(id) },
            { label: "Related node", value: node.node_id }
          ]
        });
      });
    }
    return Array.from(items.values()).sort(function (left, right) {
      return left.type.localeCompare(right.type) || left.id.localeCompare(right.id);
    });
  }

  function compactRelatedItemsText(node, flow) {
    var counts = { decision: 0, todo: 0, "open-question": 0, risk: 0 };
    relatedNodeItems(node, flow).forEach(function (item) { counts[item.type] += 1; });
    var values = [];
    [["decision", "D"], ["todo", "T"], ["open-question", "Q"], ["risk", "R"]].forEach(function (item) {
      if (counts[item[0]]) values.push(item[1] + (counts[item[0]] > 99 ? "99+" : counts[item[0]]));
    });
    return values.join(" ") || message("flow.actions.none-short", "No related items");
  }

  function renderFlowOverview(flow) {
    var counts = { "not-started": 0, "in-progress": 0, complete: 0, risk: 0 };
    var stateById = new Map((flow.node_states || []).map(function (item) { return [item.node_id, item]; }));
    var inProgress = [];
    flow.nodes.forEach(function (node) {
      var canonical = stateById.get(node.node_id) || {};
      var execution = (canonical.execution || {}).value;
      var health = (canonical.health || {}).value;
      var primary = executionPresentation(execution).id;
      if (counts[primary] !== undefined) counts[primary] += 1;
      if (health === "at-risk" || health === "blocked") counts.risk += 1;
      if (execution === "in-progress") inProgress.push(node);
    });
    if (!activeFlowNodeId || !inProgress.some(function (node) { return node.node_id === activeFlowNodeId; })) {
      activeFlowNodeId = inProgress.length ? inProgress[0].node_id : null;
    }

    var root = create("div", "flow-overview");
    root.setAttribute("aria-label", message("flow.overview.label", "Visible node status overview"));
    var strip = create("div", "flow-state-strip");
    [["not-started", "flow.state.not-started", "Not started"], ["in-progress", "flow.state.in-progress", "In progress"], ["complete", "flow.state.complete", "Complete"], ["risk", "flow.state.risk", "Risk"]].forEach(function (item) {
      var cell = create("div", "flow-state-cell");
      cell.dataset.state = item[0];
      cell.appendChild(create("span", "flow-state-marker"));
      cell.appendChild(create("span", "flow-state-label", message(item[1], item[2])));
      cell.appendChild(create("strong", "flow-state-count", counts[item[0]]));
      strip.appendChild(cell);
    });
    root.appendChild(strip);
    var frontier = create("div", "flow-frontier");
    frontier.appendChild(create("span", "flow-frontier-label", message("flow.current-focus", "Current focus")));
    var focusText = inProgress.length
      ? inProgress.map(function (node) { return node.name || node.node_id; }).join(" / ")
      : message("flow.current-focus.empty", "No node is currently in progress");
    frontier.appendChild(create("strong", "flow-frontier-value", focusText));
    root.appendChild(frontier);
    var announcer = create("div", "visually-hidden");
    announcer.id = "flow-keyboard-status";
    announcer.setAttribute("aria-live", "polite");
    root.appendChild(announcer);
    return root;
  }

  function updateActiveFlowNode(focus) {
    document.querySelectorAll("[data-node-id]").forEach(function (element) {
      var selected = element.dataset.nodeId === activeFlowNodeId;
      element.classList.toggle("is-current", selected);
      if (selected) element.setAttribute("aria-current", "step"); else element.removeAttribute("aria-current");
    });
    if (!focus || !activeFlowNodeId) return;
    var target = document.querySelector(".flow-node[data-node-id='" + CSS.escape(activeFlowNodeId) + "'], .stage-list [data-node-id='" + CSS.escape(activeFlowNodeId) + "']");
    if (target) target.focus({ preventScroll: true });
  }

  function setFlowFullscreen(active) {
    flowFullscreen = Boolean(active);
    document.body.classList.toggle("flow-is-fullscreen", flowFullscreen);
    var sectionRoot = document.querySelector(".flow-band");
    var button = document.getElementById("flow-fullscreen-toggle");
    if (sectionRoot) {
      sectionRoot.classList.toggle("is-fullscreen", flowFullscreen);
      sectionRoot.dataset.fullscreen = String(flowFullscreen);
    }
    if (button) {
      button.setAttribute("aria-pressed", String(flowFullscreen));
      button.textContent = flowFullscreen ? message("flow.fullscreen.exit", "Exit full screen") : message("flow.fullscreen.enter", "Full screen");
    }
    if (flowFullscreen) window.requestAnimationFrame(function () { updateActiveFlowNode(true); });
  }

  function stepInProgressNode(direction) {
    var flow = filteredFlow();
    var stateById = new Map(flow.node_states.map(function (item) { return [item.node_id, item]; }));
    var nodes = flow.nodes.filter(function (node) { return ((stateById.get(node.node_id) || {}).execution || {}).value === "in-progress"; });
    if (!nodes.length) {
      var empty = document.getElementById("flow-keyboard-status");
      if (empty) empty.textContent = message("flow.current-focus.empty", "No node is currently in progress");
      return;
    }
    var current = nodes.findIndex(function (node) { return node.node_id === activeFlowNodeId; });
    var next = current < 0 ? 0 : (current + direction + nodes.length) % nodes.length;
    var node = nodes[next];
    activeFlowNodeId = node.node_id;
    updateActiveFlowNode(!document.getElementById("source-drawer"));
    var announcer = document.getElementById("flow-keyboard-status");
    if (announcer) announcer.textContent = message("flow.current-focus", "Current focus") + ": " + (node.name || node.node_id);
    if (document.getElementById("source-drawer")) openDrawer(node, stateById.get(node.node_id) || {}, flow);
  }

  function flowFallback(flow) {
    var states = new Map(flow.node_states.map(function (item) { return [item.node_id, item]; }));
    var allocationByTarget = new Map(flow.allocations.map(function (item) { return [item.target_type + ":" + item.target_id, item.counts || {}]; }));
    var root = create("div", "flow-fallback");
    root.appendChild(create("strong", "", "Semantic dependency stage list"));
    var list = create("ol", "stage-list");
    flow.nodes.forEach(function (node) {
      var item = create("li");
      var canonical = states.get(node.node_id) || {};
      var execution = (canonical.execution || {}).value || "not-applicable";
      var health = (canonical.health || {}).value || "indeterminate";
      var primary = executionPresentation(execution);
      var auxiliary = healthPresentation(health);
      item.appendChild(create("strong", "", node.name || node.node_id));
      item.appendChild(create("div", "flow-fallback-status", primary.label + (auxiliary ? " / " + auxiliary.label : "") + " / " + ((node.lane || {}).lane_id || "PROGRAM")));
      item.appendChild(create("div", "muted", compactRelatedItemsText(node, flow)));
      item.dataset.nodeId = node.node_id;
      item.dataset.primaryState = primary.id;
      item.dataset.health = health;
      item.tabIndex = 0;
      item.addEventListener("click", function () { activeFlowNodeId = node.node_id; updateActiveFlowNode(false); openDrawer(node, canonical, flow); });
      item.addEventListener("keydown", function (event) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activeFlowNodeId = node.node_id; updateActiveFlowNode(false); openDrawer(node, canonical, flow); } });
      list.appendChild(item);
    });
    root.appendChild(list);
    if (flow.edges.length) {
      var edgeList = create("ol", "stage-list");
      flow.edges.forEach(function (edge) {
        var counts = allocationByTarget.get("edge:" + edge.edge_id) || {};
        edgeList.appendChild(create("li", "", (edge.label || edge.edge_id) + " / " + edge.relationship_type + " / " + countText(counts, "edge")));
      });
      root.appendChild(create("strong", "", "Canonical relationships"));
      root.appendChild(edgeList);
    }
    if (flow.unmapped.length) root.appendChild(create("p", "warning", flow.unmapped.length + " canonical overlays remain unmapped and are not guessed onto the graph."));
    return root;
  }

  function renderFlow(root) {
    renderViewHeading(root, message("view." + state.view, state.view) + " / " + message("mode.flow-progress", "Flow progress"), message("flow.subtitle", "Canonical topology with execution progress and independent risk signals."));
    var sectionRoot = section(message("mode.flow-progress", "Flow progress"), state.view === "project-lead" ? "pl-flow" : state.view === "fde-morning" ? "fde-flow-window" : "biz-flow-spine");
    sectionRoot.classList.add("flow-band");
    sectionRoot.tabIndex = -1;
    if (flowFullscreen) {
      sectionRoot.classList.add("is-fullscreen");
      sectionRoot.dataset.fullscreen = "true";
    }
    var flow = filteredFlow();
    var frame = create("div", "flow-frame");
    frame.id = "flow-frame";
    if (!flow.nodes.length && !flow.edges.length) {
      frame.classList.add("flow-frame-empty");
      frame.appendChild(flowEmptyState(flow));
      sectionRoot.appendChild(frame);
      sectionRoot.appendChild(create("p", "muted", "Scope " + (flow.scope_id || "none") + " / selection " + flow.selection_id + ". Counts remain canonical; zero values are not promoted to badges."));
      root.appendChild(sectionRoot);
      document.getElementById("result-count").textContent = "0 nodes / 0 edges";
      return;
    }
    sectionRoot.appendChild(renderFlowOverview(flow));
    var toolbar = create("div", "flow-toolbar");
    var interaction = create("div", "segmented flow-interaction-modes");
    interaction.setAttribute("role", "group");
    interaction.setAttribute("aria-label", message("flow.interaction.label", "Canvas interaction"));
    [["pan", "flow.interaction.pan", "Pan"], ["select", "flow.interaction.select", "Select"]].forEach(function (item) {
      var button = create("button", "", message(item[1], item[2]));
      button.type = "button";
      button.dataset.interactionMode = item[0];
      button.setAttribute("aria-pressed", String(flowInteractionMode === item[0]));
      button.addEventListener("click", function () {
        flowInteractionMode = item[0];
        interaction.querySelectorAll("button").forEach(function (candidate) {
          candidate.setAttribute("aria-pressed", String(candidate === button));
        });
        var svg = document.querySelector("#flow-frame svg");
        if (svg) svg.dataset.interactionMode = flowInteractionMode;
      });
      interaction.appendChild(button);
    });
    toolbar.appendChild(interaction);
    var lanes = Array.from(new Set((model.data.flows[state.view].nodes || []).map(function (node) { return (node.lane || {}).lane_id || "PROGRAM"; }))).sort();
    lanes.forEach(function (lane) {
      var button = create("button", "", (collapsedLanes.has(lane) ? "Expand " : "Collapse ") + lane);
      button.type = "button";
      button.setAttribute("aria-pressed", String(collapsedLanes.has(lane)));
      button.addEventListener("click", function () { if (collapsedLanes.has(lane)) collapsedLanes.delete(lane); else collapsedLanes.add(lane); render(); });
      toolbar.appendChild(button);
    });
    [["Fit", fitFlow], ["Zoom in", function () { zoomFlow(1.35); }], ["Zoom out", function () { zoomFlow(.74); }], ["Reset", resetFlow]].forEach(function (item) {
      var button = create("button", "", item[0]);
      button.type = "button";
      button.addEventListener("click", item[1]);
      toolbar.appendChild(button);
    });
    var fullscreen = create("button", "flow-fullscreen-toggle", flowFullscreen ? message("flow.fullscreen.exit", "Exit full screen") : message("flow.fullscreen.enter", "Full screen"));
    fullscreen.id = "flow-fullscreen-toggle";
    fullscreen.type = "button";
    fullscreen.setAttribute("aria-pressed", String(flowFullscreen));
    fullscreen.addEventListener("click", function () { setFlowFullscreen(!flowFullscreen); });
    toolbar.appendChild(fullscreen);
    sectionRoot.appendChild(toolbar);
    frame.appendChild(flowFallback(flow));
    sectionRoot.appendChild(frame);
    sectionRoot.appendChild(flowOverlaySummary(flow));
    sectionRoot.appendChild(create("p", "muted", "Scope " + (flow.scope_id || "none") + " / selection " + flow.selection_id + ". Counts remain canonical; zero values are not promoted to badges."));
    root.appendChild(sectionRoot);
    document.getElementById("result-count").textContent = flow.nodes.length + " nodes / " + flow.edges.length + " edges";
    if (window.matchMedia("(max-width: 520px)").matches || window.__ADP_FORCE_ELK_FAILURE__) return;
    layoutFlow(flow, frame).catch(function (error) {
      frame.dataset.layoutStatus = "fallback";
      frame.setAttribute("aria-label", "ELK layout failed; semantic stage list is active. " + error.message);
    });
  }

  function flowEmptyState(flow) {
    var empty = flow.empty_state || {};
    var windowFacts = empty.window || flow.meeting_window || {};
    var unmapped = flow.unmapped || [];
    var count = Number(empty.unmapped_count !== undefined ? empty.unmapped_count : unmapped.length);
    var root = create("div", "flow-empty-state");
    root.setAttribute("role", "status");
    root.appendChild(create("h3", "", empty.confirmed || windowFacts.status === "confirmed" ? "No explicitly related plan items in this confirmed scope" : "No explicitly related plan items in this scope"));
    root.appendChild(create("p", "", "Window " + (windowFacts.start || "TBD") + " to " + (windowFacts.end || "TBD") + " selected zero canonical nodes and zero canonical edges."));
    root.appendChild(create("p", "warning", "This empty scope is not proof of no delivery risk. Owner outputs contain no explicit related_plan_item_ids or related_flow_edge_ids for these overlays."));
    var metrics = create("dl", "flow-empty-metrics");
    [["Selected nodes", 0], ["Selected edges", 0], ["Unmapped overlays", count]].forEach(function (item) {
      var metric = create("div");
      metric.appendChild(create("dt", "", item[0]));
      metric.appendChild(create("dd", "", String(item[1])));
      metrics.appendChild(metric);
    });
    root.appendChild(metrics);
    var recovery = empty.recovery || Array.from(new Set(unmapped.map(function (item) { return item.recovery; }).filter(Boolean)));
    root.appendChild(create("p", "flow-empty-recovery", "Recovery: " + (recovery.join(" ") || "Confirm explicit owner relations in the owning action/risk workflow, then refresh the graph and meeting pack.")));
    var details = create("details", "flow-empty-sources");
    details.appendChild(create("summary", "", "Canonical unmapped source details (" + count + ")"));
    var list = create("ul");
    var sourceDetails = empty.source_details || unmapped;
    sourceDetails.forEach(function (item) {
      list.appendChild(create("li", "", (item.source_kind || "source") + " " + (item.source_id || "unknown") + " / " + (item.reason || item.finding_code || "unmapped")));
    });
    details.appendChild(list);
    root.appendChild(details);
    root.appendChild(create("p", "muted", "Scope " + (empty.scope_id || flow.scope_id || "none") + ". Selection and source identities remain available in the panel manifest."));
    return root;
  }

  function layoutFlow(flow, frame) {
    var ElkConstructor = window.ELK && (window.ELK.default || window.ELK);
    if (!ElkConstructor) return Promise.reject(new Error("ELK is unavailable"));
    var engine = new ElkConstructor();
    var graph = {
      id: "root",
      layoutOptions: {
        "elk.algorithm": "layered", "elk.direction": "RIGHT", "elk.edgeRouting": "ORTHOGONAL",
        "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES", "elk.spacing.nodeNode": "44",
        "elk.layered.spacing.nodeNodeBetweenLayers": "72"
      },
      children: flow.nodes.map(function (node) { return { id: node.node_id, width: node.node_type === "gate" ? 230 : 210, height: 76 }; }),
      edges: flow.edges.map(function (edge) { return { id: edge.edge_id, sources: [edge.predecessor], targets: [edge.target] }; })
    };
    return Promise.race([
      engine.layout(graph),
      new Promise(function (_, reject) { window.setTimeout(function () { reject(new Error("ELK layout timeout")); }, 5000); })
    ]).then(function (layout) { drawFlow(flow, frame, layout); });
  }

  function drawFlow(flow, frame, layout) {
    var fallback = frame.querySelector(".flow-fallback");
    frame.replaceChildren();
    frame.dataset.layoutStatus = "ready";
    var svg = createSvg("svg", { role: "img", "aria-labelledby": "flow-title flow-desc", viewBox: "0 0 " + Math.max(600, layout.width || 600) + " " + Math.max(360, layout.height || 360) });
    svg.dataset.interactionMode = flowInteractionMode;
    svg.appendChild(createSvg("title", { id: "flow-title" }, "ADP progress flow"));
    svg.appendChild(createSvg("desc", { id: "flow-desc" }, "Approved milestone and gate topology with canonical execution, health, relationship, and overlay states."));
    var defs = createSvg("defs");
    var marker = createSvg("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse" });
    marker.appendChild(createSvg("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#66717c" }));
    defs.appendChild(marker);
    svg.appendChild(defs);
    var viewport = createSvg("g", { id: "flow-viewport" });
    var edgeById = new Map(flow.edges.map(function (edge) { return [edge.edge_id, edge]; }));
    (layout.edges || []).forEach(function (laidEdge) {
      var edge = edgeById.get(laidEdge.id) || {};
      var pathData = "";
      (laidEdge.sections || []).forEach(function (section, index) {
        pathData += (index ? " M " : "M ") + section.startPoint.x + " " + section.startPoint.y;
        (section.bendPoints || []).forEach(function (point) { pathData += " L " + point.x + " " + point.y; });
        pathData += " L " + section.endPoint.x + " " + section.endPoint.y;
      });
      var path = createSvg("path", { d: pathData, class: "flow-edge", "data-type": edge.relationship_type || "dependency", "marker-end": "url(#arrow)" });
      var title = createSvg("title", {}, (edge.label || edge.edge_id) + " / " + (edge.relationship_type || "dependency"));
      path.appendChild(title);
      viewport.appendChild(path);
      var firstSection = (laidEdge.sections || [])[0];
      if (firstSection) {
        var midpointX = (firstSection.startPoint.x + firstSection.endPoint.x) / 2;
        var midpointY = (firstSection.startPoint.y + firstSection.endPoint.y) / 2;
        var importantLabel = ["conditional", "rework"].indexOf(edge.relationship_type) >= 0 ? truncate(edge.label || edge.edge_id, 18) : "";
        if (importantLabel) viewport.appendChild(createSvg("text", { x: midpointX, y: midpointY - 7, fill: "#49535d", "font-size": 10, "text-anchor": "middle" }, importantLabel));
      }
    });
    var nodeById = new Map(flow.nodes.map(function (node) { return [node.node_id, node]; }));
    var stateById = new Map(flow.node_states.map(function (item) { return [item.node_id, item]; }));
    (layout.children || []).forEach(function (laidNode, index) {
      var node = nodeById.get(laidNode.id);
      var canonical = stateById.get(laidNode.id) || {};
      var execution = (canonical.execution || {}).value || "indeterminate";
      var health = (canonical.health || {}).value || "indeterminate";
      var primary = executionPresentation(execution);
      var auxiliary = healthPresentation(health);
      var group = createSvg("g", { class: "flow-node", transform: "translate(" + laidNode.x + " " + laidNode.y + ")", tabindex: 0, role: "button", "aria-label": (node.name || node.node_id) + ", " + primary.label + (auxiliary ? ", " + auxiliary.label : ""), "data-node-id": node.node_id, "data-primary-state": primary.id, "data-execution": execution, "data-health": health });
      if (node.node_type === "gate") {
        group.appendChild(createSvg("rect", { width: laidNode.width, height: laidNode.height, rx: 5, ry: 5 }));
        group.appendChild(createSvg("polygon", { class: "gate-icon", points: "20,27 31,38 20,49 9,38" }));
      } else group.appendChild(createSvg("rect", { width: laidNode.width, height: laidNode.height, rx: 7, ry: 7 }));
      if (auxiliary) {
        var healthMarker = createSvg("g", { class: "health-marker", "data-health": auxiliary.id, "aria-hidden": "true" });
        healthMarker.appendChild(createSvg("circle", { cx: laidNode.width - 15, cy: 14, r: 9 }));
        healthMarker.appendChild(createSvg("text", { x: laidNode.width - 15, y: 18, "text-anchor": "middle" }, auxiliary.id === "blocked" ? "!" : "R"));
        group.appendChild(healthMarker);
      }
      var textCenter = node.node_type === "gate" ? 130 : laidNode.width / 2;
      group.appendChild(createSvg("text", { x: textCenter, y: 25, "text-anchor": "middle" }, truncate(node.name || node.node_id, 28)));
      group.appendChild(createSvg("text", { class: "flow-node-status", x: textCenter, y: 46, "text-anchor": "middle" }, primary.label + (primary.id === "not-started" ? " · " + primary.detail : "")));
      group.appendChild(createSvg("text", { class: "flow-node-meta", x: textCenter, y: 63, "text-anchor": "middle" }, ((node.lane || {}).lane_id || "PROGRAM") + " · " + compactRelatedItemsText(node, flow)));
      group.addEventListener("click", function () { activeFlowNodeId = node.node_id; updateActiveFlowNode(false); openDrawer(node, canonical, flow); });
      group.addEventListener("keydown", function (event) {
        var nodes = Array.prototype.slice.call(svg.querySelectorAll(".flow-node"));
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activeFlowNodeId = node.node_id; updateActiveFlowNode(false); openDrawer(node, canonical, flow); }
        if (event.key === "ArrowRight" || event.key === "ArrowDown") { event.preventDefault(); nodes[(index + 1) % nodes.length].focus(); }
        if (event.key === "ArrowLeft" || event.key === "ArrowUp") { event.preventDefault(); nodes[(index - 1 + nodes.length) % nodes.length].focus(); }
      });
      viewport.appendChild(group);
    });
    svg.appendChild(viewport);
    frame.appendChild(svg);
    if (fallback) frame.appendChild(fallback);
    enablePan(svg, viewport);
    applyFlowTransform();
    updateActiveFlowNode(false);
  }

  function truncate(value, size) { return value.length > size ? value.slice(0, size - 1) + "…" : value; }

  function countText(counts, execution) {
    var values = [];
    ["blocked", "risk", "pending"].forEach(function (name) {
      var count = Number((counts[name] || {}).count || 0);
      if (count) values.push(name + " " + (count > 99 ? "99+" : count));
    });
    if (execution === "ready" || execution === "in-progress" || execution === "edge") {
      var processed = Number((counts.processed || {}).count || 0);
      if (processed) values.push("processed " + (processed > 99 ? "99+" : processed));
    }
    return values.length ? values.join(" / ") : "no active overlays";
  }

  function compactCountText(counts) {
    var values = [];
    [["blocked", "B"], ["risk", "R"], ["pending", "P"], ["processed", "D"]].forEach(function (item) {
      var count = Number((counts[item[0]] || {}).count || 0);
      if (count) values.push(item[1] + (count > 99 ? "99+" : count));
    });
    return values.join(" ");
  }

  function flowOverlaySummary(flow) {
    var root = create("details", "flow-overlay-summary");
    root.appendChild(create("summary", "", "Relationship labels, state and scoped overlay counts"));
    var states = new Map(flow.relationship_states.map(function (item) { return [item.edge_id, item]; }));
    var allocations = new Map(flow.allocations.filter(function (item) { return item.target_type === "edge"; }).map(function (item) { return [item.target_id, item.counts || {}]; }));
    var list = create("ul");
    flow.edges.forEach(function (edge) {
      var canonical = states.get(edge.edge_id) || {};
      var relationshipState = (canonical.state || {}).value || "indeterminate";
      var relationshipHealth = (canonical.health || {}).value || "indeterminate";
      var countSummary = compactCountText(allocations.get(edge.edge_id) || {}) || "no active overlays";
      list.appendChild(create("li", "", (edge.label || edge.edge_id) + " · " + edge.relationship_type + " · " + relationshipState + " / " + relationshipHealth + " · " + countSummary));
    });
    root.appendChild(list);
    return root;
  }

  function applyFlowTransform() {
    var viewport = document.getElementById("flow-viewport");
    if (viewport) {
      viewport.setAttribute("transform", "matrix(" + flowTransform.scale + " 0 0 " + flowTransform.scale + " " + flowTransform.x + " " + flowTransform.y + ")");
      if (viewport.ownerSVGElement) viewport.ownerSVGElement.dataset.zoomScale = String(flowTransform.scale);
    }
  }

  function fitFlow() { flowTransform = { scale: 1, x: 0, y: 0 }; applyFlowTransform(); }
  function resetFlow() { flowTransform = { scale: 1, x: 0, y: 0 }; applyFlowTransform(); }
  function svgClientPoint(svg, clientX, clientY) {
    var point = svg.createSVGPoint();
    point.x = clientX;
    point.y = clientY;
    var matrix = svg.getScreenCTM();
    return matrix ? point.matrixTransform(matrix.inverse()) : { x: clientX, y: clientY };
  }

  function flowCenterPoint() {
    var svg = document.querySelector("#flow-frame svg");
    if (!svg) return null;
    var svgBounds = svg.getBoundingClientRect();
    var visibleNodes = Array.prototype.map.call(svg.querySelectorAll(".flow-node"), function (node) {
      var bounds = node.getBoundingClientRect();
      var left = Math.max(svgBounds.left, bounds.left);
      var top = Math.max(svgBounds.top, bounds.top);
      var right = Math.min(svgBounds.right, bounds.right);
      var bottom = Math.min(svgBounds.bottom, bounds.bottom);
      return { node: node, bounds: bounds, visibleArea: Math.max(0, right - left) * Math.max(0, bottom - top) };
    }).filter(function (item) { return item.visibleArea > 0; });
    var target = visibleNodes.find(function (item) { return item.node.dataset.nodeId === activeFlowNodeId; });
    if (!target) {
      visibleNodes.sort(function (left, right) { return right.visibleArea - left.visibleArea; });
      target = visibleNodes[0];
    }
    if (target) {
      return svgClientPoint(svg, target.bounds.left + target.bounds.width / 2, target.bounds.top + target.bounds.height / 2);
    }
    return svgClientPoint(svg, svgBounds.left + svgBounds.width / 2, svgBounds.top + svgBounds.height / 2);
  }

  function zoomFlow(factor, anchor) {
    var previous = flowTransform.scale;
    var next = Math.max(flowZoomLimits.min, Math.min(flowZoomLimits.max, previous * factor));
    if (next === previous) return;
    var point = anchor || flowCenterPoint();
    if (point) {
      var ratio = next / previous;
      flowTransform.x = point.x - (point.x - flowTransform.x) * ratio;
      flowTransform.y = point.y - (point.y - flowTransform.y) * ratio;
    }
    flowTransform.scale = next;
    applyFlowTransform();
  }

  function enablePan(svg, viewport) {
    var pointerId = null;
    var start = null;
    var origin = null;
    var moved = false;
    svg.dataset.panGain = String(flowPanGain);
    svg.dataset.zoomMin = String(flowZoomLimits.min);
    svg.dataset.zoomMax = String(flowZoomLimits.max);
    svg.addEventListener("wheel", function (event) {
      event.preventDefault();
      zoomFlow(event.deltaY < 0 ? 1.22 : .82, svgClientPoint(svg, event.clientX, event.clientY));
    }, { passive: false });
    svg.addEventListener("pointerdown", function (event) {
      if (event.button !== 0 && event.button !== 1) return;
      var overNode = event.target.closest && event.target.closest(".flow-node");
      if (flowInteractionMode !== "pan" && event.button !== 1 && !spacePan && overNode) return;
      pointerId = event.pointerId;
      start = {
        clientX: event.clientX,
        clientY: event.clientY,
        point: svgClientPoint(svg, event.clientX, event.clientY)
      };
      origin = { x: flowTransform.x, y: flowTransform.y };
      moved = false;
    });
    svg.addEventListener("pointermove", function (event) {
      if (pointerId !== event.pointerId) return;
      var clientDeltaX = event.clientX - start.clientX;
      var clientDeltaY = event.clientY - start.clientY;
      if (!moved && Math.hypot(clientDeltaX, clientDeltaY) < 4) return;
      if (!moved && !svg.hasPointerCapture(event.pointerId)) svg.setPointerCapture(event.pointerId);
      moved = true;
      svg.classList.add("is-panning");
      event.preventDefault();
      var current = svgClientPoint(svg, event.clientX, event.clientY);
      flowTransform.x = origin.x + (current.x - start.point.x) * flowPanGain;
      flowTransform.y = origin.y + (current.y - start.point.y) * flowPanGain;
      applyFlowTransform();
    });
    function finish(event) {
      if (pointerId !== event.pointerId) return;
      if (moved) {
        svg.dataset.suppressClick = "true";
        window.setTimeout(function () { delete svg.dataset.suppressClick; }, 0);
      }
      svg.classList.remove("is-panning");
      if (svg.hasPointerCapture(pointerId)) svg.releasePointerCapture(pointerId);
      pointerId = null;
      start = null;
      origin = null;
    }
    svg.addEventListener("pointerup", finish);
    svg.addEventListener("pointercancel", finish);
    svg.addEventListener("click", function (event) {
      if (svg.dataset.suppressClick !== "true") return;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
  }

  function relatedItemsPanel(node, flow) {
    var items = relatedNodeItems(node, flow);
    var labels = {
      all: message("flow.action.all", "All"),
      decision: message("flow.action.decision", "Decisions"),
      todo: message("flow.action.todo", "To-dos"),
      "open-question": message("flow.action.open-question", "Open questions"),
      risk: message("flow.action.risk", "Risks")
    };
    var root = create("section", "node-related-items");
    root.appendChild(create("h3", "", message("flow.actions.title", "Related items")));
    var controls = create("div", "related-item-filters");
    controls.setAttribute("role", "group");
    controls.setAttribute("aria-label", message("flow.actions.filter", "Filter related items"));
    var list = create("ul", "related-item-list");

    function renderItems(filter) {
      list.replaceChildren();
      var visible = filter === "all" ? items : items.filter(function (item) { return item.type === filter; });
      if (!visible.length) {
        list.appendChild(create("li", "related-item-empty", message("flow.actions.empty", "No related items of this type in the canonical flow data.")));
        return;
      }
      visible.forEach(function (item) {
        var row = create("li");
        var disclosure = create("details", "related-item");
        disclosure.dataset.itemType = item.type;
        var head = create("summary", "related-item-heading");
        var title = create("span", "related-item-title");
        title.appendChild(create("span", "related-item-kind", labels[item.type]));
        title.appendChild(create("strong", "", item.title));
        title.appendChild(create("span", "related-item-id", item.id));
        head.appendChild(title);
        if (item.states.length) {
          var states = create("div", "related-item-states");
          item.states.forEach(function (value) { states.appendChild(create("span", "", value)); });
          head.appendChild(states);
        }
        disclosure.appendChild(head);
        var content = create("div", "related-item-content");
        var ledgerButton = actionLedgerButton(item.actionId);
        if (ledgerButton) content.appendChild(ledgerButton);
        var riskButton = riskRegisterButton(item.riskId);
        if (riskButton) content.appendChild(riskButton);
        var source = sourceReferenceLink(item.sourceReference);
        if (source) content.appendChild(source);
        appendDetailList(content, item.details);
        disclosure.appendChild(content);
        row.appendChild(disclosure);
        list.appendChild(row);
      });
    }

    ["all", "decision", "todo", "open-question", "risk"].forEach(function (type, index) {
      var count = type === "all" ? items.length : items.filter(function (item) { return item.type === type; }).length;
      var button = create("button", "", labels[type] + " " + count);
      button.type = "button";
      button.setAttribute("aria-pressed", String(index === 0));
      button.addEventListener("click", function () {
        controls.querySelectorAll("button").forEach(function (item) { item.setAttribute("aria-pressed", "false"); });
        button.setAttribute("aria-pressed", "true");
        renderItems(type);
      });
      controls.appendChild(button);
    });
    root.appendChild(controls);
    root.appendChild(list);
    renderItems("all");
    return root;
  }

  function openDrawer(node, canonical, flow) {
    var existing = document.getElementById("source-drawer");
    if (existing) existing.remove();
    var dialog = create("dialog", "drawer");
    dialog.id = "source-drawer";
    var header = create("header");
    header.appendChild(create("h2", "", node.name || node.node_id));
    var close = create("button", "", message("common.close", "Close"));
    close.type = "button";
    close.addEventListener("click", function () { dialog.close(); });
    header.appendChild(close);
    dialog.appendChild(header);
    var body = create("div", "drawer-body");
    var execution = (canonical.execution || {}).value;
    var health = (canonical.health || {}).value;
    var primary = executionPresentation(execution);
    var auxiliary = healthPresentation(health);
    var nodeStatus = create("div", "drawer-node-status");
    var primaryToken = create("span", "node-state-token", primary.label);
    primaryToken.dataset.state = primary.id;
    nodeStatus.appendChild(primaryToken);
    if (auxiliary) {
      var healthToken = create("span", "node-state-token", auxiliary.label);
      healthToken.dataset.state = auxiliary.id;
      nodeStatus.appendChild(healthToken);
    }
    body.appendChild(nodeStatus);
    body.appendChild(relatedItemsPanel(node, flow));
    body.appendChild(create("h3", "drawer-source-heading", message("flow.source.title", "Source and rules")));
    var details = create("dl");
    var source = node.source || {};
    [
      ["Node ID", node.node_id], ["Lane", (node.lane || {}).lane_id], ["Execution", (canonical.execution || {}).value],
      ["Execution rule", (canonical.execution || {}).rule_id], ["Health", (canonical.health || {}).value],
      ["Health rule", (canonical.health || {}).rule_id], ["Artifact", source.artifact_id], ["Path", source.artifact_path],
      ["Field", source.field], ["Fingerprint", source.source_fingerprint], ["Flow selection", flow.selection_id], ["Panel ID", manifest.panel_id]
    ].forEach(function (item) { details.appendChild(create("dt", "", item[0])); details.appendChild(create("dd", "", item[1] || "unavailable")); });
    body.appendChild(details);
    var copy = create("button", "", "Copy source path");
    copy.type = "button";
    var manual = create("code", "", source.artifact_path || "No source path available");
    manual.tabIndex = 0;
    copy.addEventListener("click", function () {
      var value = source.artifact_path || "";
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(value).then(function () { copy.textContent = "Copied"; }).catch(function () { copy.textContent = "Select the path below"; manual.focus(); });
      else { copy.textContent = "Select the path below"; manual.focus(); }
    });
    body.appendChild(copy);
    body.appendChild(document.createElement("br"));
    body.appendChild(manual);
    dialog.appendChild(body);
    document.body.appendChild(dialog);
    dialog.addEventListener("click", function (event) {
      if (event.target !== dialog) return;
      var bounds = dialog.getBoundingClientRect();
      var outside = event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom;
      if (outside) dialog.close();
    });
    dialog.addEventListener("close", function () { dialog.remove(); });
    dialog.showModal();
    close.focus();
  }

  function render() {
    if ((utilityViewIds.indexOf(state.view) >= 0 || state.mode !== "flow-progress") && flowFullscreen) setFlowFullscreen(false);
    document.body.dataset.view = state.view;
    renderHeader();
    renderNav();
    var root = document.getElementById("dynamic-view");
    root.replaceChildren();
    if (state.view === "action-ledger") renderActionLedger(root);
    else if (state.view === "risk-register") renderRiskRegister(root);
    else if (state.mode === "flow-progress") renderFlow(root);
    else if (state.view === "project-lead") renderProject(root);
    else if (state.view === "fde-morning") renderFde(root);
    else renderBusiness(root);
  }

  initControls();
  writeHash(true);
  render();
  window.addEventListener("keydown", function (event) {
    if (event.code !== "Space" || event.repeat || ["INPUT", "SELECT", "TEXTAREA", "BUTTON", "SUMMARY"].indexOf((event.target || {}).tagName) >= 0) return;
    if (!document.querySelector("#flow-frame svg")) return;
    spacePan = true;
    document.querySelector("#flow-frame svg").classList.add("is-space-pan");
    event.preventDefault();
  });
  window.addEventListener("keyup", function (event) {
    if (event.code !== "Space") return;
    spacePan = false;
    var svg = document.querySelector("#flow-frame svg");
    if (svg) svg.classList.remove("is-space-pan");
  });
  window.addEventListener("blur", function () { spacePan = false; });
  window.addEventListener("keydown", function (event) {
    if (!flowFullscreen) return;
    if (event.key === "Escape") {
      if (document.getElementById("source-drawer")) return;
      event.preventDefault();
      setFlowFullscreen(false);
      return;
    }
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    if (["INPUT", "SELECT", "TEXTAREA"].indexOf((event.target || {}).tagName) >= 0) return;
    event.preventDefault();
    event.stopPropagation();
    stepInProgressNode(event.key === "ArrowRight" ? 1 : -1);
  }, true);
  window.addEventListener("popstate", function () { state = parseHash(); render(); });
  window.addEventListener("hashchange", function () { var next = parseHash(); if (JSON.stringify(next) !== JSON.stringify(state)) { state = next; render(); } });
}());
