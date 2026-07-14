#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");
const playwright = require(process.env.PLAYWRIGHT_CORE || "playwright-core");

const [currentPath, internalPath, shareablePath, englishPath, outputDir] = process.argv.slice(2).map(value => path.resolve(value));
const chromePath = process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
fs.mkdirSync(outputDir, { recursive: true });

function check(condition, message) {
  if (!condition) throw new Error(message);
}

function fileExists(filePath) {
  check(fs.existsSync(filePath), "missing acceptance input: " + filePath);
}

async function session(browser, options = {}) {
  const context = await browser.newContext({
    viewport: options.viewport || { width: 1280, height: 720 },
    javaScriptEnabled: options.javaScriptEnabled !== false,
    forcedColors: options.forcedColors || "none",
    reducedMotion: options.reducedMotion || "no-preference"
  });
  const externalRequests = [];
  await context.route("**/*", route => {
    const url = route.request().url();
    if (/^(file|data|blob):/.test(url)) return route.continue();
    externalRequests.push(url);
    return route.abort();
  });
  if (options.forceElkFailure) {
    await context.addInitScript(() => { window.__ADP_FORCE_ELK_FAILURE__ = true; });
  }
  const page = await context.newPage();
  const errors = [];
  page.on("console", message => { if (message.type() === "error") errors.push("console: " + message.text()); });
  page.on("pageerror", error => errors.push("pageerror: " + error.message));
  return { context, page, errors, externalRequests };
}

async function open(page, filePath, hash = "") {
  await page.goto(pathToFileURL(filePath).href + hash, { waitUntil: "load" });
}

async function embeddedModel(page) {
  return page.locator("#adp-panel-model").evaluate(element => JSON.parse(element.textContent));
}

async function layoutCheck(page, label) {
  const result = await page.evaluate(() => {
    const visible = element => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    const textOverflow = Array.from(document.querySelectorAll("button, a, th, td, .metric, .identity-chip"))
      .filter(visible)
      .filter(element => element.scrollWidth > element.clientWidth + 2)
      .slice(0, 12)
      .map(element => ({ tag: element.tagName, text: element.textContent.trim().slice(0, 80), overflow: element.scrollWidth - element.clientWidth }));
    return {
      bodyOverflow: document.documentElement.scrollWidth - window.innerWidth,
      textOverflow,
      textLength: document.body.innerText.trim().length,
      mainWidth: document.querySelector("#dynamic-view")?.getBoundingClientRect().width || 0
    };
  });
  check(result.textLength > 500, label + ": page is blank or nearly blank");
  check(result.mainWidth > 250 || (await page.locator("#panel-app").isHidden()), label + ": main content has no stable width");
  check(result.bodyOverflow <= 2, label + ": horizontal overflow " + result.bodyOverflow + "px");
  return result;
}

async function capture(page, name, fullPage = true) {
  const target = path.join(outputDir, name);
  await page.screenshot({ path: target, fullPage });
  check(fs.statSync(target).size > 8000, name + ": screenshot is unexpectedly small");
  return target;
}

async function clickMode(page, pattern) {
  const button = page.getByRole("button", { name: pattern });
  check(await button.count() === 1, "visualization mode control is absent or ambiguous: " + pattern);
  await button.click();
}

async function assertFlow(page, model, view, label) {
  const expected = model.data.flows[view];
  await clickMode(page, /流程图|Flow progress/i);
  await page.waitForSelector("#flow-frame");
  if (expected.nodes.length && (await page.locator("#flow-frame svg").count())) {
    await page.waitForSelector("#flow-frame[data-layout-status='ready']", { timeout: 10000 });
  }
  check(await page.locator("#flow-frame .flow-node").count() === expected.nodes.length, label + ": node count differs from selected model");
  check(await page.locator("#flow-frame .flow-edge").count() === expected.edges.length, label + ": edge count differs from selected model");
  check((await page.locator("#result-count").innerText()).includes(expected.nodes.length + " nodes / " + expected.edges.length + " edges"), label + ": result count differs from selected model");
  if (expected.nodes.length || expected.edges.length) {
    const fallbackText = await page.locator("#flow-frame .flow-fallback").innerText();
    check(fallbackText.includes("Semantic dependency stage list"), label + ": semantic fallback is absent");
    if (expected.unmapped.length) check(fallbackText.includes(String(expected.unmapped.length)), label + ": unmapped overlay disclosure is absent");
  } else {
    check(await page.locator("#flow-frame .flow-empty-state").count() === 1, label + ": scoped empty state is absent");
  }
  if (expected.nodes.length && (await page.locator("#flow-frame svg").count())) {
    const geometry = await page.evaluate(() => {
      const nodes = Array.from(document.querySelectorAll("#flow-frame svg .flow-node"));
      const rects = nodes.map(node => node.getBoundingClientRect());
      const overlaps = [];
      for (let i = 0; i < rects.length; i += 1) for (let j = i + 1; j < rects.length; j += 1) {
        const a = rects[i], b = rects[j];
        if (a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top) overlaps.push([i, j]);
      }
      return {
        overlaps,
        role: document.querySelector("#flow-frame svg")?.getAttribute("role"),
        title: document.querySelectorAll("#flow-frame svg title").length,
        desc: document.querySelectorAll("#flow-frame svg desc").length
      };
    });
    check(geometry.overlaps.length === 0, label + ": flow nodes overlap " + JSON.stringify(geometry.overlaps));
    check(geometry.role === "img" && geometry.title > 1 && geometry.desc === 1, label + ": flow lacks screen-reader title/description");
  }
  return expected;
}

(async () => {
  [currentPath, internalPath, shareablePath, englishPath, chromePath].forEach(fileExists);
  const browser = await playwright.chromium.launch({
    executablePath: chromePath,
    headless: true,
    args: ["--allow-file-access-from-files", "--disable-background-networking", "--disable-sync"]
  });
  const evidence = { status: "complete", browser: "Google Chrome", files: {}, checks: [], screenshots: [] };
  try {
    {
      const { context, page, errors, externalRequests } = await session(browser, { viewport: { width: 1920, height: 1080 } });
      await open(page, currentPath);
      await page.waitForSelector(".metrics .metric");
      const model = await embeddedModel(page);
      evidence.files.current = { panel_id: model.panel_id, layout_id: model.manifest.layout_id, profile: model.manifest.distribution_profile };
      check(model.data.status.snapshot_id === "ps-13b37f2e63e2e977", "current panel does not bind the accepted status snapshot");
      check(model.data.status.overall_status === "off-plan" && model.data.status.report_confidence === "low", "current panel lost real status/confidence");
      check(model.data.status.progress.overall.current.actual_completion_percent === 20, "current panel lost real 20 percent actual completion");
      check(model.manifest.flow_graph_id === "sha256:5d2b888812ec1e64957289b88058c1cc59513bcd4591c0982dca565558f00686", "current panel does not bind owner-migrated flow graph");
      check(model.recovery.status === "degraded", "real degraded recovery was hidden");
      const qualityText = await page.locator("#quality-banner").innerText();
      const recoveryMessage = model.recovery.findings[0].message;
      check(qualityText.split(recoveryMessage).length - 1 === 1, "degraded recovery sentence is duplicated in the quality banner");
      check(await page.locator(".metrics .metric").count() === 4, "project lead does not show four completion metrics");
      check(await page.locator(".bullet-track").count() === 1 && await page.locator(".health-block").count() === 1, "project lead completion/health separation is absent");
      check((await page.locator("body").innerText()).match(/[\u4e00-\u9fff]/) && JSON.stringify(model).includes("MS-L"), "real mixed Chinese/English content is absent");
      await layoutCheck(page, "project-1920");
      evidence.screenshots.push(await capture(page, "project-lead-1920x1080.png", false));

      const historyOptions = await page.locator("#filter-period option").evaluateAll(options => options.map(item => item.value));
      check(historyOptions.length > 1, "immutable history selector has no predecessor");
      check(model.manifest.reporting_period.start === "2026-07-08" && model.manifest.reporting_period.end === "2026-07-14", "current reporting period is not the accepted current window");
      const previousPeriod = model.data.history.find(item => item.as_of === "2026-06-30");
      check(previousPeriod && historyOptions.includes(previousPeriod.snapshot_id), "real previous-period snapshot is absent from the history selector");
      await page.selectOption("#filter-period", previousPeriod.snapshot_id);
      check(page.url().includes("period=" + encodeURIComponent(previousPeriod.snapshot_id)), "previous-period selection was not written to versioned hash");
      await page.click("#clear-filters");
      check(await page.locator(".trend-shell").count() === 1, "milestone staircase/trend is absent");

      const projectFlow = await assertFlow(page, model, "project-lead", "project");
      check(projectFlow.nodes.length === 15 && projectFlow.edges.length === 24, "project flow is not the complete 15-node/24-edge baseline topology");
      const firstNode = page.locator("#flow-frame .flow-node").first();
      await firstNode.focus();
      await page.keyboard.press("ArrowRight");
      await page.keyboard.press("Enter");
      await page.waitForSelector("#source-drawer[open]");
      const drawer = await page.locator("#source-drawer").innerText();
      check(drawer.includes("Fingerprint") && drawer.includes("Path") && drawer.includes("Panel ID"), "source drill-down lacks canonical lineage");
      await page.keyboard.press("Escape");
      evidence.screenshots.push(await capture(page, "project-flow-1920x1080.png", false));

      const originalPanelId = model.panel_id;
      await page.reload({ waitUntil: "load" });
      check((await embeddedModel(page)).panel_id === originalPanelId, "browser refresh did not read the same regenerated static panel");
      check(errors.length === 0, "project journey console errors: " + errors.join(" | "));
      check(externalRequests.length === 0, "project journey attempted network access: " + externalRequests.join(", "));
      evidence.checks.push("project lead completion/health, history, trend, full flow, keyboard source drill-down and browser refresh");
      await context.close();
    }

    {
      const { context, page, errors, externalRequests } = await session(browser, { viewport: { width: 1280, height: 720 } });
      await open(page, currentPath, "#v=1&view=fde-morning&mode=quantitative-progress");
      await page.waitForSelector("#fde-meeting-readiness");
      const model = await embeddedModel(page);
      const meeting = model.data.meetings["fde-morning"];
      const text = await page.locator("#fde-meeting-readiness").innerText();
      check(text.includes(meeting.meeting_pack_id) && text.includes("pre-meeting-snapshot") && text.includes("degraded"), "FDE identity/readiness/lifecycle is not visible");
      check(meeting.meeting_window.start === "2026-07-13" && meeting.meeting_window.end === "2026-07-14", "FDE confirmed acceptance window changed");
      check(await page.getByText("Next-period forecast", { exact: true }).count() === 0, "FDE view leaked resident long-range forecast");
      const flow = await assertFlow(page, model, "fde-morning", "fde");
      check(flow.nodes.length === 0 && flow.edges.length === 0 && flow.unmapped.length === 20, "FDE flow did not preserve empty exact-window allocation plus unmapped risks");
      const emptyState = await page.locator("#flow-frame .flow-empty-state").innerText();
      check(emptyState.includes("confirmed scope") && emptyState.includes("Unmapped overlays\n20") && emptyState.includes("Recovery:"), "FDE scoped empty state lacks confirmed window, unmapped count or recovery");
      check(await page.locator(".flow-toolbar").count() === 0 && await page.locator("#flow-frame svg").count() === 0, "FDE scoped empty state retained empty canvas controls or SVG");
      await layoutCheck(page, "fde-1280");
      evidence.screenshots.push(await capture(page, "fde-morning-1280x720.png", false));
      check(errors.length === 0 && externalRequests.length === 0, "FDE journey emitted errors or network requests");
      evidence.checks.push("FDE confirmed window, degraded pre-meeting lifecycle, zero exact allocations and 20 unmapped risk disclosure");
      await context.close();
    }

    {
      const { context, page, errors, externalRequests } = await session(browser, { viewport: { width: 1920, height: 1080 } });
      await open(page, currentPath, "#v=1&view=business-biweekly&mode=quantitative-progress");
      await page.waitForSelector("#biz-meeting-readiness");
      const model = await embeddedModel(page);
      const meeting = model.data.meetings["business-biweekly"];
      const text = await page.locator("#biz-meeting-readiness").innerText();
      check(text.includes(meeting.meeting_pack_id) && text.includes("blocked") && text.includes("pre-meeting-snapshot"), "business blocked readiness/lifecycle is not visible");
      const flow = await assertFlow(page, model, "business-biweekly", "business");
      check(flow.nodes.length === 14 && flow.edges.length === 22, "business flow is not the selected program/critical/abnormal spine");
      evidence.screenshots.push(await capture(page, "business-biweekly-1920x1080.png", false));
      check(errors.length === 0 && externalRequests.length === 0, "business journey emitted errors or network requests");
      evidence.checks.push("business blocked pre-meeting lifecycle and 14-node/22-edge selected spine");
      await context.close();
    }

    {
      const { context, page, errors, externalRequests } = await session(browser, { viewport: { width: 320, height: 800 } });
      await open(page, currentPath, "#v=1&view=project-lead&mode=flow-progress");
      await page.waitForSelector("#flow-frame .stage-list");
      check(await page.locator("#flow-frame svg").count() === 0, "320px reflow retained an unreadable SVG canvas");
      await layoutCheck(page, "mobile-320");
      evidence.screenshots.push(await capture(page, "mobile-320x800.png"));
      check(errors.length === 0 && externalRequests.length === 0, "320px journey emitted errors or network requests");
      evidence.checks.push("320 CSS px and 400-percent-equivalent semantic reflow");
      await context.close();
    }

    {
      const { context, page, errors, externalRequests } = await session(browser, { viewport: { width: 640, height: 720 } });
      await open(page, currentPath);
      await layoutCheck(page, "desktop-200-percent");
      evidence.screenshots.push(await capture(page, "desktop-200-percent.png", false));
      check(errors.length === 0 && externalRequests.length === 0, "200 percent journey emitted errors or network requests");
      evidence.checks.push("desktop 200-percent-equivalent reflow");
      await context.close();
    }

    {
      const { context, page, errors, externalRequests } = await session(browser, { viewport: { width: 1280, height: 720 }, forceElkFailure: true });
      await open(page, currentPath, "#v=1&view=project-lead&mode=flow-progress");
      await page.waitForSelector("#flow-frame .stage-list");
      check(await page.locator("#flow-frame svg").count() === 0, "forced ELK failure retained SVG output");
      check((await page.locator("#flow-frame").innerText()).includes("Canonical relationships"), "ELK failure lost relationship fallback");
      evidence.screenshots.push(await capture(page, "elk-failure-fallback.png", false));
      check(errors.length === 0 && externalRequests.length === 0, "ELK fallback emitted errors or network requests");
      evidence.checks.push("ELK failure semantic fallback");
      await context.close();
    }

    {
      const { context, page, errors, externalRequests } = await session(browser, { viewport: { width: 320, height: 800 }, javaScriptEnabled: false });
      await open(page, currentPath, "#fde-morning");
      check(await page.locator("#panel-app").isHidden(), "no-JS enhanced shell became visible");
      check(await page.locator("#fde-morning").isVisible(), "no-JS FDE direct anchor is not visible");
      const text = await page.locator("#fde-morning").innerText();
      check(text.includes("Dependency order") && text.includes("pre-meeting-snapshot"), "no-JS fallback lost flow/lifecycle facts");
      check(text.includes("confirmed scope") && text.includes("Selected nodes\n0") && text.includes("Selected edges\n0"), "no-JS fallback lost the confirmed empty scope");
      check(text.includes("Unmapped overlays\n20") && text.includes("Recovery:") && text.includes("Canonical unmapped source details (20)"), "no-JS fallback lost unmapped or recovery evidence");
      evidence.screenshots.push(await capture(page, "no-js-fde-320x800.png"));
      check(errors.length === 0 && externalRequests.length === 0, "no-JS journey emitted errors or network requests");
      evidence.checks.push("JavaScript-off direct anchor and semantic fallback");
      await context.close();
    }

    {
      const { context, page, errors, externalRequests } = await session(browser, { viewport: { width: 1280, height: 720 }, forcedColors: "active", reducedMotion: "reduce" });
      await open(page, currentPath);
      check(await page.locator(".bullet-track").count() === 1, "forced colors lost progress chart");
      check(errors.length === 0 && externalRequests.length === 0, "forced-colors journey emitted errors or network requests");
      evidence.checks.push("forced colors and reduced motion smoke");
      await context.close();
    }

    {
      const { context, page, errors, externalRequests } = await session(browser, { viewport: { width: 1280, height: 720 } });
      await open(page, currentPath, "#v=99&view=unknown&mode=bad");
      await page.waitForSelector("#pl-progress-summary");
      check(page.url().includes("v=1") && page.url().includes("view=project-lead"), "malformed hash was not normalized");
      await page.click("#nav-business-biweekly");
      await page.waitForSelector("#biz-meeting-readiness");
      await page.goBack();
      await page.waitForSelector("#pl-progress-summary");
      check(errors.length === 0 && externalRequests.length === 0, "hash/history journey emitted errors or network requests");
      evidence.checks.push("versioned malformed hash normalization and Back navigation");
      await context.close();
    }

    {
      const { context, page, errors, externalRequests } = await session(browser, { viewport: { width: 1280, height: 720 } });
      await open(page, currentPath, "#v=1&view=business-biweekly&mode=flow-progress");
      await page.emulateMedia({ media: "print" });
      check(await page.locator(".view-nav").evaluate(element => getComputedStyle(element).display) === "none", "print retained interactive navigation");
      check(await page.locator("#flow-frame .flow-fallback").evaluate(element => getComputedStyle(element).display) !== "none", "print omitted semantic flow fallback");
      const pdf = path.join(outputDir, "business-flow-print.pdf");
      await page.pdf({ path: pdf, printBackground: true, format: "A4" });
      check(fs.statSync(pdf).size > 10000, "print PDF is blank");
      evidence.print = pdf;
      check(errors.length === 0 && externalRequests.length === 0, "print journey emitted errors or network requests");
      evidence.checks.push("print-to-PDF with semantic flow fallback");
      await context.close();
    }

    {
      const { context, page, errors, externalRequests } = await session(browser, { viewport: { width: 1280, height: 720 } });
      await open(page, internalPath);
      const model = await embeddedModel(page);
      check(model.manifest.distribution_profile === "internal-full", "internal archive lost distribution profile");
      check(model.panel_id === evidence.files.current.panel_id && model.manifest.layout_id === evidence.files.current.layout_id, "current/internal archive identity differs");
      check(model.data.meetings["fde-morning"].lifecycle === "pre-meeting-snapshot" && model.data.meetings["business-biweekly"].lifecycle === "pre-meeting-snapshot", "internal archive falsified meeting lifecycle");
      evidence.files.internal = { panel_id: model.panel_id, layout_id: model.manifest.layout_id, profile: model.manifest.distribution_profile };
      check(errors.length === 0 && externalRequests.length === 0, "internal archive emitted errors or network requests");
      evidence.checks.push("internal archive offline identity and lifecycle");
      await context.close();
    }

    {
      const { context, page, errors, externalRequests } = await session(browser, { viewport: { width: 1280, height: 720 } });
      await open(page, shareablePath, "#v=1&view=project-lead&mode=flow-progress");
      const model = await embeddedModel(page);
      check(model.manifest.distribution_profile === "shareable-summary", "shareable archive lost distribution profile");
      check(model.manifest.redaction.hidden_nodes > 0 && model.manifest.redaction.topology_reconnected === false, "shareable archive redaction/topology disclosure is invalid");
      const encoded = JSON.stringify(model.data);
      for (const secret of ["source_fingerprints", "artifact_path", "allocations", '"owner"']) check(!encoded.includes(secret), "shareable archive leaked " + secret);
      check((await page.locator("#quality-banner").innerText()).length > 0, "shareable archive does not disclose degraded/redacted state");
      evidence.files.shareable = { panel_id: model.panel_id, layout_id: model.manifest.layout_id, profile: model.manifest.distribution_profile, redaction: model.manifest.redaction };
      evidence.screenshots.push(await capture(page, "shareable-archive.png", false));
      check(errors.length === 0 && externalRequests.length === 0, "shareable archive emitted errors or network requests");
      evidence.checks.push("shareable offline redaction, no topology reconnection and no source/owner/count leakage");
      await context.close();
    }

    {
      const { context, page, errors, externalRequests } = await session(browser, { viewport: { width: 1280, height: 720 } });
      await open(page, englishPath);
      const model = await embeddedModel(page);
      const text = await page.locator("body").innerText();
      check(model.catalog.locale === "en" && model.manifest.locale.resolved === "en", "English archive did not resolve English locale");
      check(text.includes("Project Lead") || text.includes("Project lead"), "English archive lacks localized navigation");
      check(JSON.stringify(model).includes("MS-L") && text.length > 2000, "English archive lost long mixed source content");
      evidence.files.english = { panel_id: model.panel_id, layout_id: model.manifest.layout_id, locale: model.catalog.locale };
      evidence.screenshots.push(await capture(page, "english-project-lead.png", false));
      check(errors.length === 0 && externalRequests.length === 0, "English archive emitted errors or network requests");
      evidence.checks.push("English offline archive and long mixed content");
      await context.close();
    }
  } finally {
    await browser.close();
  }
  const resultText = JSON.stringify(evidence, null, 2) + "\n";
  fs.writeFileSync(path.join(outputDir, "browser-acceptance-results.json"), resultText, "utf8");
  process.stdout.write(resultText);
})().catch(error => {
  process.stderr.write(error.stack + "\n");
  process.exit(1);
});
