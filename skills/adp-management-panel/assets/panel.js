(function () {
  "use strict";

  var model = JSON.parse(document.getElementById("adp-panel-model").textContent);
  var manifest = JSON.parse(document.getElementById("adp-panel-manifest").textContent);
  var viewIds = ["project-lead", "fde-morning", "business-biweekly"];
  var modeIds = ["quantitative-progress", "flow-progress"];
  var statusValues = ["on-plan", "at-risk", "blocked", "off-plan", "indeterminate", "complete", "in-progress", "ready", "planned", "not-applicable"];
  var state = parseHash();
  var sortState = { key: "scope_id", direction: "ascending" };
  var collapsedLanes = new Set();
  var flowTransform = { scale: 1, x: 0, y: 0 };
  var svgNamespace = "http://www.w3.org/2000/svg";

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
      period: params.get("period") || "current"
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
    statusValues.forEach(function (value) { addOption(statuses, value); });
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
    } else if (hiddenTopology) {
      quality.dataset.level = "degraded";
      quality.textContent = message("redaction.hidden-topology", "Part of the topology is hidden") + ": " + manifest.redaction.hidden_nodes + " nodes / " + manifest.redaction.hidden_edges + " edges; topology_reconnected=false.";
    } else {
      delete quality.dataset.level;
    }
  }

  function renderNav() {
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
    heading.appendChild(create("span", "identity-chip", "panel " + manifest.panel_id + " / scope " + model.selection.flow_scopes[state.view].layout_scope_id));
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

  function board(title, items) {
    var root = create("section", "board");
    root.appendChild(create("h3", "", title));
    var list = create("ul");
    if (!items || !items.length) list.appendChild(create("li", "muted", "None in canonical meeting pack"));
    (items || []).forEach(function (item) {
      var value = item.summary || item.Item || item.id || item.workstream || JSON.stringify(item);
      var line = create("li", "", value);
      if (item.owner) line.appendChild(create("div", "muted", "Owner: " + item.owner));
      list.appendChild(line);
    });
    root.appendChild(list);
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
    var grid = create("div", "board-grid");
    grid.appendChild(board("Blockers", meeting.boards.fde_blockers));
    grid.appendChild(board("Commitments", meeting.boards.fde_commitments));
    grid.appendChild(board("Due today", meeting.boards.fde_due));
    grid.appendChild(board("Escalations", meeting.boards.fde_escalations || meeting.boards.escalations));
    boards.appendChild(grid);
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
    var grid = create("div", "board-grid");
    grid.appendChild(board("Decisions", meeting.boards.business_decisions));
    grid.appendChild(board("Top variances", meeting.boards.top_variances));
    grid.appendChild(board("Readiness", meeting.boards.business_readiness));
    grid.appendChild(board("Business impact", meeting.boards.cross_line_business_impact));
    decisions.appendChild(grid);
    root.appendChild(decisions);
    document.getElementById("result-count").textContent = (meeting.boards.business_decisions || []).length + " decisions";
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
      if (state.status !== "all" && state.status !== execution && state.status !== health) return false;
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

  function flowFallback(flow) {
    var states = new Map(flow.node_states.map(function (item) { return [item.node_id, item]; }));
    var allocationByTarget = new Map(flow.allocations.map(function (item) { return [item.target_type + ":" + item.target_id, item.counts || {}]; }));
    var root = create("div", "flow-fallback");
    root.appendChild(create("strong", "", "Semantic dependency stage list"));
    var list = create("ol", "stage-list");
    flow.nodes.forEach(function (node) {
      var item = create("li");
      var canonical = states.get(node.node_id) || {};
      item.appendChild(create("strong", "", node.name || node.node_id));
      item.appendChild(create("div", "muted", ((node.lane || {}).lane_id || "PROGRAM") + " / " + ((canonical.execution || {}).value || "indeterminate") + " / " + ((canonical.health || {}).value || "indeterminate")));
      var counts = allocationByTarget.get("node:" + node.node_id) || {};
      item.appendChild(create("div", "muted", countText(counts, (canonical.execution || {}).value)));
      item.tabIndex = 0;
      item.addEventListener("click", function () { openDrawer(node, canonical, flow); });
      item.addEventListener("keydown", function (event) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openDrawer(node, canonical, flow); } });
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
    renderViewHeading(root, message("view." + state.view, state.view) + " / Flow", "Canonical topology with orthogonal execution and health states.");
    var sectionRoot = section("Flow progress", state.view === "project-lead" ? "pl-flow" : state.view === "fde-morning" ? "fde-flow-window" : "biz-flow-spine");
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
    var toolbar = create("div", "flow-toolbar");
    var lanes = Array.from(new Set((model.data.flows[state.view].nodes || []).map(function (node) { return (node.lane || {}).lane_id || "PROGRAM"; }))).sort();
    lanes.forEach(function (lane) {
      var button = create("button", "", (collapsedLanes.has(lane) ? "Expand " : "Collapse ") + lane);
      button.type = "button";
      button.setAttribute("aria-pressed", String(collapsedLanes.has(lane)));
      button.addEventListener("click", function () { if (collapsedLanes.has(lane)) collapsedLanes.delete(lane); else collapsedLanes.add(lane); render(); });
      toolbar.appendChild(button);
    });
    [["Fit", fitFlow], ["Zoom in", function () { zoomFlow(1.2); }], ["Zoom out", function () { zoomFlow(.8); }], ["Reset", resetFlow]].forEach(function (item) {
      var button = create("button", "", item[0]);
      button.type = "button";
      button.addEventListener("click", item[1]);
      toolbar.appendChild(button);
    });
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
    svg.appendChild(createSvg("title", { id: "flow-title" }, "ADP progress flow"));
    svg.appendChild(createSvg("desc", { id: "flow-desc" }, "Approved milestone and gate topology with canonical execution, health, relationship, and overlay states."));
    var defs = createSvg("defs");
    var marker = createSvg("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse" });
    marker.appendChild(createSvg("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#66717c" }));
    defs.appendChild(marker);
    svg.appendChild(defs);
    var viewport = createSvg("g", { id: "flow-viewport" });
    var edgeById = new Map(flow.edges.map(function (edge) { return [edge.edge_id, edge]; }));
    var allocationByTarget = new Map(flow.allocations.map(function (item) { return [item.target_type + ":" + item.target_id, item.counts || {}]; }));
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
      var group = createSvg("g", { class: "flow-node", transform: "translate(" + laidNode.x + " " + laidNode.y + ")", tabindex: 0, role: "button", "aria-label": (node.name || node.node_id) + ", " + execution + ", " + health, "data-execution": execution, "data-health": health });
      if (node.node_type === "gate") {
        group.appendChild(createSvg("rect", { width: laidNode.width, height: laidNode.height, rx: 5, ry: 5 }));
        group.appendChild(createSvg("polygon", { class: "gate-icon", points: "20,27 31,38 20,49 9,38" }));
      } else group.appendChild(createSvg("rect", { width: laidNode.width, height: laidNode.height, rx: 7, ry: 7 }));
      var textCenter = node.node_type === "gate" ? 130 : laidNode.width / 2;
      group.appendChild(createSvg("text", { x: textCenter, y: 25, "text-anchor": "middle" }, truncate(node.name || node.node_id, 28)));
      group.appendChild(createSvg("text", { x: textCenter, y: 46, "text-anchor": "middle", fill: "#5c6773" }, execution + " / " + health));
      group.appendChild(createSvg("text", { x: textCenter, y: 63, "text-anchor": "middle", fill: "#5c6773" }, ((node.lane || {}).lane_id || "PROGRAM") + " · " + countText(allocationByTarget.get("node:" + node.node_id) || {}, execution)));
      group.addEventListener("click", function () { openDrawer(node, canonical, flow); });
      group.addEventListener("keydown", function (event) {
        var nodes = Array.prototype.slice.call(svg.querySelectorAll(".flow-node"));
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openDrawer(node, canonical, flow); }
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
    if (viewport) viewport.setAttribute("transform", "translate(" + flowTransform.x + " " + flowTransform.y + ") scale(" + flowTransform.scale + ")");
  }

  function fitFlow() { flowTransform = { scale: 1, x: 0, y: 0 }; applyFlowTransform(); }
  function resetFlow() { flowTransform = { scale: 1, x: 0, y: 0 }; applyFlowTransform(); }
  function zoomFlow(factor) { flowTransform.scale = Math.max(.5, Math.min(3, flowTransform.scale * factor)); applyFlowTransform(); }

  function enablePan(svg, viewport) {
    var dragging = false;
    var origin = null;
    svg.addEventListener("wheel", function (event) { event.preventDefault(); zoomFlow(event.deltaY < 0 ? 1.1 : .9); }, { passive: false });
    svg.addEventListener("pointerdown", function (event) { dragging = true; origin = { x: event.clientX - flowTransform.x, y: event.clientY - flowTransform.y }; svg.setPointerCapture(event.pointerId); });
    svg.addEventListener("pointermove", function (event) { if (!dragging) return; flowTransform.x = event.clientX - origin.x; flowTransform.y = event.clientY - origin.y; applyFlowTransform(); });
    svg.addEventListener("pointerup", function () { dragging = false; });
    svg.addEventListener("pointercancel", function () { dragging = false; });
  }

  function openDrawer(node, canonical, flow) {
    var existing = document.getElementById("source-drawer");
    if (existing) existing.remove();
    var dialog = create("dialog", "drawer");
    dialog.id = "source-drawer";
    var header = create("header");
    header.appendChild(create("h2", "", node.name || node.node_id));
    var close = create("button", "", "Close");
    close.type = "button";
    close.addEventListener("click", function () { dialog.close(); });
    header.appendChild(close);
    dialog.appendChild(header);
    var body = create("div", "drawer-body");
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
    dialog.addEventListener("close", function () { dialog.remove(); });
    dialog.showModal();
    close.focus();
  }

  function render() {
    document.body.dataset.view = state.view;
    renderHeader();
    renderNav();
    var root = document.getElementById("dynamic-view");
    root.replaceChildren();
    if (state.mode === "flow-progress") renderFlow(root);
    else if (state.view === "project-lead") renderProject(root);
    else if (state.view === "fde-morning") renderFde(root);
    else renderBusiness(root);
  }

  initControls();
  writeHash(true);
  render();
  window.addEventListener("popstate", function () { state = parseHash(); render(); });
  window.addEventListener("hashchange", function () { var next = parseHash(); if (JSON.stringify(next) !== JSON.stringify(state)) { state = next; render(); } });
}());
