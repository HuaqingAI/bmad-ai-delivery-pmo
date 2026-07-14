#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");
const playwright = require(process.env.PLAYWRIGHT_CORE || "playwright-core");

const htmlPath = path.resolve(process.argv[2]);
const outputDir = path.resolve(process.argv[3]);
const injectionPath = process.argv[4] ? path.resolve(process.argv[4]) : null;
const shareablePath = process.argv[5] ? path.resolve(process.argv[5]) : null;
const chromePath = process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
fs.mkdirSync(outputDir, { recursive: true });

function check(condition, message) {
  if (!condition) throw new Error(message);
}

async function pageSession(browser, options = {}) {
  const context = await browser.newContext({
    viewport: options.viewport || { width: 1280, height: 720 },
    javaScriptEnabled: options.javaScriptEnabled !== false,
    colorScheme: "light",
    reducedMotion: options.reducedMotion || "no-preference"
  });
  if (options.forceElkFailure) await context.addInitScript(() => { window.__ADP_FORCE_ELK_FAILURE__ = true; });
  const page = await context.newPage();
  const errors = [];
  page.on("console", message => { if (message.type() === "error") errors.push("console: " + message.text()); });
  page.on("pageerror", error => errors.push("pageerror: " + error.message));
  return { context, page, errors };
}

async function open(page, filePath, hash = "") {
  await page.goto(pathToFileURL(filePath).href + hash, { waitUntil: "load" });
}

async function layoutCheck(page, label) {
  const result = await page.evaluate(() => {
    const visible = element => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    const selectors = [".status-shell", ".view-nav", ".tool-band", "#dynamic-view"];
    const rects = selectors.map(selector => {
      const element = document.querySelector(selector);
      if (!element || !visible(element)) return null;
      const rect = element.getBoundingClientRect();
      return { selector, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
    }).filter(Boolean);
    return {
      overflow: document.documentElement.scrollWidth - window.innerWidth,
      rects,
      textLength: document.body.innerText.trim().length,
      mainWidth: document.querySelector("#dynamic-view")?.getBoundingClientRect().width || 0
    };
  });
  check(result.textLength > 200, label + ": page is blank or nearly blank");
  check(result.mainWidth > 200, label + ": main content has no stable width");
  check(result.overflow <= 2, label + ": body horizontally overflows by " + result.overflow + "px");
  for (let index = 1; index < result.rects.length; index += 1) {
    const before = result.rects[index - 1];
    const current = result.rects[index];
    check(current.top >= before.top, label + ": major regions have incoherent order");
  }
  return result;
}

async function screenshot(page, name, fullPage = true) {
  const target = path.join(outputDir, name);
  await page.screenshot({ path: target, fullPage });
  check(fs.statSync(target).size > 5000, name + ": screenshot is unexpectedly small");
  return target;
}

(async () => {
  check(fs.existsSync(chromePath), "Google Chrome executable is unavailable: " + chromePath);
  const browser = await playwright.chromium.launch({ executablePath: chromePath, headless: true, args: ["--allow-file-access-from-files", "--disable-background-networking"] });
  const evidence = { screenshots: [], checks: [] };
  try {
    {
      const { context, page, errors } = await pageSession(browser, { viewport: { width: 1920, height: 1080 } });
      await open(page, htmlPath);
      await page.waitForSelector(".metrics .metric");
      check(await page.locator(".metrics .metric").count() === 4, "project lead first screen does not have four completion metrics");
      check(await page.locator(".bullet-track").count() === 1, "project lead bullet chart is absent");
      check(await page.locator(".health-block").count() === 1, "independent plan health block is absent");
      check(await page.locator(".trend-shell circle").count() >= 3, "milestone trend has no visible canonical point markers");
      check((await page.locator(".health-block").innerText()).includes("at-risk"), "plan health lost canonical status");
      await layoutCheck(page, "project-1920");
      evidence.screenshots.push(await screenshot(page, "project-lead-1920x1080.png"));

      await page.selectOption("#filter-workstream", "L1");
      check((await page.locator(".bullet-wrap").getAttribute("aria-label")).includes("66.67"), "workstream filter did not synchronize bullet scope");
      check((await page.locator("#pl-progress-trend h2").innerText()).includes("L1"), "workstream filter did not synchronize trend scope");
      await page.selectOption("#filter-period", "ps-history-2026-07-06");
      check((await page.locator(".metric .value").first().innerText()).includes("20"), "period comparison did not show immutable historical value");
      await page.click("#clear-filters");
      await page.fill("#filter-search", "L1");
      check((await page.locator("#result-count").innerText()).includes("1 workstreams"), "Chinese-capable free search did not filter rows");
      await page.fill("#filter-search", "");

      const firstHeader = page.locator(".data-table th button").first();
      await firstHeader.click();
      check(await page.locator(".data-table th").first().getAttribute("aria-sort") === "descending", "sortable table did not update aria-sort");

      await page.getByRole("button", { name: /流程图|Flow progress/ }).click();
      await page.waitForSelector("#flow-frame[data-layout-status='ready']", { timeout: 8000 });
      check(await page.locator("#flow-frame svg .flow-node").count() === 5, "project flow did not render the complete selected graph");
      check(await page.locator("#flow-frame svg .flow-edge").count() === 5, "project flow edge count differs from canonical selection");
      const flowGeometry = await page.evaluate(() => {
        const nodes = Array.from(document.querySelectorAll("#flow-frame svg .flow-node"));
        const rects = nodes.map(node => node.getBoundingClientRect());
        const overlaps = [];
        for (let i = 0; i < rects.length; i += 1) for (let j = i + 1; j < rects.length; j += 1) {
          const a = rects[i], b = rects[j];
          if (a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top) overlaps.push([i, j]);
        }
        const escaped = nodes.filter(node => {
          const container = node.querySelector(":scope > rect").getBoundingClientRect();
          return Array.from(node.querySelectorAll(":scope > text")).some(text => {
            const box = text.getBoundingClientRect();
            return box.left < container.left - 1 || box.right > container.right + 1 || box.top < container.top - 1 || box.bottom > container.bottom + 1;
          });
        }).length;
        return { overlaps, escaped, gateIcons: document.querySelectorAll("#flow-frame svg .gate-icon").length };
      });
      check(flowGeometry.overlaps.length === 0, "ELK flow nodes overlap: " + JSON.stringify(flowGeometry.overlaps));
      check(flowGeometry.escaped === 0, "flow label/status/count escaped its node container");
      check(flowGeometry.gateIcons === 1, "gate node lacks a stable semantic diamond icon");
      const transformBefore = await page.locator("#flow-viewport").getAttribute("transform");
      await page.getByRole("button", { name: "Zoom in" }).click();
      const transformAfter = await page.locator("#flow-viewport").getAttribute("transform");
      check(transformBefore !== transformAfter && transformAfter.includes("scale(1.2"), "flow zoom control did not update transform");
      await page.getByRole("button", { name: "Fit" }).click();
      const flowBox = await page.locator("#flow-frame svg").boundingBox();
      await page.mouse.move(flowBox.x + flowBox.width / 2, flowBox.y + flowBox.height / 2);
      await page.mouse.down();
      await page.mouse.move(flowBox.x + flowBox.width / 2 + 30, flowBox.y + flowBox.height / 2 + 20);
      await page.mouse.up();
      check((await page.locator("#flow-viewport").getAttribute("transform")).includes("translate(30 20)"), "flow pointer pan did not update transform");
      await page.getByRole("button", { name: "Reset" }).click();
      await page.getByRole("button", { name: "Collapse L1" }).click();
      await page.waitForTimeout(100);
      check((await page.locator("#result-count").innerText()).includes("3 nodes"), "lane collapse did not reduce visible canonical nodes");
      await page.getByRole("button", { name: "Expand L1" }).click();
      await page.waitForSelector("#flow-frame[data-layout-status='ready']");
      const firstNode = page.locator("#flow-frame svg .flow-node").first();
      await firstNode.focus();
      await page.keyboard.press("ArrowRight");
      await page.keyboard.press("Enter");
      await page.waitForSelector("#source-drawer[open]");
      check((await page.locator("#source-drawer").innerText()).includes("Fingerprint"), "node source drawer lacks canonical lineage");
      check(await page.locator("#flow-frame svg title").count() > 1, "flow nodes and edges lack native tooltip titles");
      await page.keyboard.press("Escape");
      await page.waitForSelector("#source-drawer", { state: "detached" });
      evidence.screenshots.push(await screenshot(page, "project-flow-1920x1080.png", false));
      check(errors.length === 0, "Chrome console errors: " + errors.join(" | "));
      evidence.checks.push("project lead, filtering, period selection, sorting, ELK flow, lane collapse, zoom, keyboard and source drawer");
      await context.close();
    }

    {
      const { context, page, errors } = await pageSession(browser, { viewport: { width: 1280, height: 720 } });
      await open(page, htmlPath, "#v=1&view=project-lead&mode=quantitative-progress");
      await layoutCheck(page, "project-1280");
      evidence.screenshots.push(await screenshot(page, "project-lead-1280x720.png", false));
      await open(page, htmlPath, "#v=1&view=fde-morning&mode=quantitative-progress");
      await page.waitForSelector("#fde-window-delta");
      const meetingContext = await page.locator("#fde-meeting-readiness").innerText();
      check(meetingContext.includes("Pack ID") && meetingContext.includes("2026-07-13-fde-morning"), "FDE view lacks meeting pack identity");
      check(meetingContext.includes("Meeting window") && meetingContext.includes("confirmed"), "FDE view lacks confirmed meeting window");
      check(await page.getByText("Next-period forecast", { exact: true }).count() === 0, "FDE view keeps a resident long-range forecast");
      const meetingControlHeight = await page.locator("#clear-filters").evaluate(element => parseFloat(getComputedStyle(element).height));
      const meetingBodySize = await page.locator("#dynamic-view").evaluate(element => parseFloat(getComputedStyle(element).fontSize));
      check(meetingControlHeight >= 44 && meetingBodySize >= 16, "meeting presentation density did not override workbench tokens");
      await layoutCheck(page, "fde-1280");
      evidence.screenshots.push(await screenshot(page, "fde-morning-1280x720.png", false));
      await page.getByRole("button", { name: /流程图|Flow progress/ }).click();
      await page.waitForSelector("#flow-frame[data-layout-status='ready']");
      check(await page.locator("#flow-frame svg .flow-node").count() === 2, "FDE flow widened beyond meeting-pack window selection");
      check(errors.length === 0, "FDE Chrome console errors: " + errors.join(" | "));
      evidence.checks.push("FDE pack identity, readiness/lifecycle, information budget and window flow");
      await context.close();
    }

    {
      const { context, page, errors } = await pageSession(browser, { viewport: { width: 1280, height: 720 }, reducedMotion: "reduce" });
      await context.close();
      const forcedContext = await browser.newContext({ viewport: { width: 1280, height: 720 }, forcedColors: "active", reducedMotion: "reduce" });
      const forcedPage = await forcedContext.newPage();
      const forcedErrors = [];
      forcedPage.on("console", message => { if (message.type() === "error") forcedErrors.push(message.text()); });
      forcedPage.on("pageerror", error => forcedErrors.push(error.message));
      await open(forcedPage, htmlPath);
      check(await forcedPage.locator(".bullet-track").count() === 1, "forced-colors mode lost quantitative progress");
      check(forcedErrors.length === 0, "forced-colors/reduced-motion errors: " + forcedErrors.join(" | "));
      evidence.checks.push("forced colors and reduced motion smoke");
      await forcedContext.close();
    }

    {
      const { context, page, errors } = await pageSession(browser, { viewport: { width: 1920, height: 1080 } });
      await open(page, htmlPath, "#v=1&view=business-biweekly&mode=quantitative-progress");
      await page.waitForSelector("#biz-next-period-progress");
      check((await page.locator("#biz-next-period-progress").innerText()).includes("60%"), "business first screen does not lead with next-period forecast");
      check((await page.locator("#biz-decisions").innerText()).includes("Approve gate exception"), "business decisions were not copied from meeting pack");
      evidence.screenshots.push(await screenshot(page, "business-biweekly-1920x1080.png", false));
      await page.getByRole("button", { name: /流程图|Flow progress/ }).click();
      await page.waitForSelector("#flow-frame[data-layout-status='ready']");
      check(await page.locator("#flow-frame svg .flow-node").count() === 3, "business flow widened beyond owner-selected spine");
      check(errors.length === 0, "business Chrome console errors: " + errors.join(" | "));
      evidence.checks.push("business next-period and program-spine information budget");
      await context.close();
    }

    {
      const { context, page, errors } = await pageSession(browser, { viewport: { width: 320, height: 800 } });
      await open(page, htmlPath, "#v=1&view=project-lead&mode=quantitative-progress");
      await page.waitForSelector(".bullet-track");
      await layoutCheck(page, "mobile-320");
      evidence.screenshots.push(await screenshot(page, "mobile-reflow-320x800.png"));
      await page.getByRole("button", { name: /流程图|Flow progress/ }).click();
      check(await page.locator("#flow-frame svg").count() === 0, "narrow reflow did not use semantic stage list");
      check(await page.locator("#flow-frame .stage-list").count() >= 1, "narrow reflow fallback is absent");
      check(errors.length === 0, "mobile Chrome console errors: " + errors.join(" | "));
      evidence.checks.push("320 CSS pixel and 400 percent-equivalent reflow");
      await context.close();
    }

    {
      const { context, page, errors } = await pageSession(browser, { viewport: { width: 1280, height: 720 } });
      await open(page, htmlPath);
      await page.setViewportSize({ width: 640, height: 720 });
      await page.waitForTimeout(100);
      await layoutCheck(page, "desktop-200-percent");
      evidence.screenshots.push(await screenshot(page, "desktop-200-percent.png", false));
      check(errors.length === 0, "zoom Chrome console errors: " + errors.join(" | "));
      evidence.checks.push("desktop 200 percent zoom");
      await context.close();
    }

    {
      const { context, page, errors } = await pageSession(browser, { viewport: { width: 1280, height: 720 }, forceElkFailure: true });
      await open(page, htmlPath, "#v=1&view=project-lead&mode=flow-progress");
      await page.waitForSelector("#flow-frame .stage-list");
      check(await page.locator("#flow-frame svg").count() === 0, "forced ELK failure left a graphical surface active");
      check((await page.locator("#flow-frame").innerText()).includes("Canonical relationships"), "ELK failure fallback lost dependency relationships");
      evidence.screenshots.push(await screenshot(page, "elk-failure-fallback.png", false));
      check(errors.length === 0, "ELK fallback console errors: " + errors.join(" | "));
      evidence.checks.push("ELK failure semantic fallback");
      await context.close();
    }

    {
      const { context, page, errors } = await pageSession(browser, { viewport: { width: 320, height: 800 }, javaScriptEnabled: false });
      await open(page, htmlPath, "#fde-morning");
      check(await page.locator("#panel-app").isHidden(), "no-JS shell became visible");
      check(await page.locator("#fde-morning").isVisible(), "no-JS direct view hash did not reveal FDE fallback");
      check(await page.locator("#project-lead").isHidden(), "no-JS direct hash did not hide default view");
      check((await page.locator("#fde-morning").innerText()).includes("Dependency order"), "no-JS fallback lost canonical flow order");
      evidence.screenshots.push(await screenshot(page, "no-js-fde-320x800.png"));
      check(errors.length === 0, "no-JS console errors: " + errors.join(" | "));
      evidence.checks.push("JavaScript-off direct hash and semantic fallback");
      await context.close();
    }

    {
      const { context, page, errors } = await pageSession(browser, { viewport: { width: 1280, height: 720 } });
      await open(page, htmlPath, "#v=99&view=untrusted&mode=other");
      await page.waitForSelector("#pl-progress-summary");
      check(page.url().includes("v=1") && page.url().includes("view=project-lead"), "malformed hash was not normalized to allowlisted state");
      await page.click("#nav-fde-morning");
      await page.waitForSelector("#fde-window-delta");
      await page.goBack();
      await page.waitForSelector("#pl-progress-summary");
      check(errors.length === 0, "hash/history console errors: " + errors.join(" | "));
      evidence.checks.push("versioned direct/malformed hash and Back navigation");
      await context.close();
    }

    {
      const { context, page, errors } = await pageSession(browser, { viewport: { width: 1280, height: 720 } });
      await open(page, htmlPath, "#v=1&view=business-biweekly&mode=flow-progress");
      await page.emulateMedia({ media: "print" });
      const navDisplay = await page.locator(".view-nav").evaluate(element => getComputedStyle(element).display);
      const fallbackDisplay = await page.locator("#flow-frame .flow-fallback").evaluate(element => getComputedStyle(element).display);
      check(navDisplay === "none", "print retained interactive navigation");
      check(fallbackDisplay !== "none", "print omitted semantic flow fallback");
      const pdfPath = path.join(outputDir, "business-flow-print.pdf");
      await page.pdf({ path: pdfPath, printBackground: true, format: "A4" });
      check(fs.statSync(pdfPath).size > 10000, "print PDF is blank or unexpectedly small");
      check(errors.length === 0, "print console errors: " + errors.join(" | "));
      evidence.checks.push("print current view/filter with semantic flow");
      evidence.print = pdfPath;
      await context.close();
    }

    if (injectionPath) {
      const { context, page, errors } = await pageSession(browser, { viewport: { width: 1280, height: 720 } });
      await open(page, injectionPath, "#v=1&view=business-biweekly&mode=quantitative-progress");
      check(await page.locator("script script, img[src='x'], foreignObject, [onerror], [onload]").count() === 0, "malicious source created executable DOM/SVG");
      check(await page.evaluate(() => window.__ADP_INJECTION_EXECUTED__ === undefined), "malicious source script executed");
      check(errors.length === 0, "injection page console errors: " + errors.join(" | "));
      evidence.checks.push("HTML/SVG injection fixture remained inert in Chrome");
      await context.close();
    }
    if (shareablePath) {
      const { context, page, errors } = await pageSession(browser, { viewport: { width: 1280, height: 720 } });
      await open(page, shareablePath, "#v=1&view=project-lead&mode=flow-progress");
      check((await page.locator("#quality-banner").innerText()).includes("部分拓扑已隐藏"), "shareable archive does not disclose hidden topology");
      const embedded = await page.locator("#adp-panel-model").evaluate(element => JSON.parse(element.textContent));
      check(embedded.manifest.distribution_profile === "shareable-summary", "shareable archive lost distribution profile");
      check(embedded.manifest.redaction.topology_reconnected === false, "shareable archive reconnected hidden topology");
      const encoded = JSON.stringify(embedded.data);
      for (const secret of ["internal-owner@example.com", "views/program-status.json", '"allocations"', '"source_fingerprints"']) {
        check(!encoded.includes(secret), "shareable archive leaked " + secret);
      }
      check(errors.length === 0, "shareable archive console errors: " + errors.join(" | "));
      evidence.screenshots.push(await screenshot(page, "shareable-archive-flow.png", false));
      evidence.checks.push("shareable offline archive redaction disclosure and no topology reconnection");
      await context.close();
    }
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify({ status: "complete", browser: "Google Chrome", file: htmlPath, ...evidence }, null, 2) + "\n");
})().catch(error => {
  process.stderr.write(error.stack + "\n");
  process.exit(1);
});
