import fs from "node:fs";
import { pathToFileURL } from "node:url";

export const CONSUMER_ID = "management-panel-v2-current-consumer/1.0.0";
export const SOURCE_POINTER = "/sync/canonical/status/workstream_current";
export const SOURCE_POINTERS = ["/panel_id", SOURCE_POINTER];

const escapeHtml = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#39;");

const requireCanonicalText = (value, label) => {
  if (typeof value !== "string" || !value.trim() || value !== value.normalize("NFC")) {
    throw new Error(`${label} must be a non-empty NFC string`);
  }
  return value;
};

const readPointer = (document, pointer, reads) => {
  reads?.push(pointer);
  return pointer.slice(1).split("/").reduce((value, token) => value?.[token.replaceAll("~1", "/").replaceAll("~0", "~")], document);
};

export const renderCurrentWorkstreams = (panel, reads = null) => {
  const rows = readPointer(panel, SOURCE_POINTER, reads);
  if (!Array.isArray(rows) || rows.length === 0) throw new Error("current workstream rows are required");
  const normalized = rows.map((row) => ({
    workstream_id: requireCanonicalText(row?.workstream_id, "workstream_id"),
    progress: requireCanonicalText(row?.progress, "progress"),
    blockers: Array.isArray(row?.blockers) ? row.blockers.map((value) => requireCanonicalText(value, "blocker")) : null,
    risks: Array.isArray(row?.risks) ? row.risks.map((value) => requireCanonicalText(value, "risk")) : null,
  }));
  if (normalized.some((row) => row.blockers === null || row.risks === null)) throw new Error("blockers and risks must be arrays");
  normalized.sort((left, right) => Buffer.from(left.workstream_id).compare(Buffer.from(right.workstream_id)));
  if (new Set(normalized.map((row) => row.workstream_id)).size !== normalized.length) throw new Error("duplicate workstream_id");
  const html = normalized.map((row) => [
    `<section data-workstream-id="${escapeHtml(row.workstream_id)}">`,
    `<h3>${escapeHtml(row.workstream_id)}</h3>`,
    `<p data-field="progress">${escapeHtml(row.progress)}</p>`,
    `<ul data-field="blockers">${row.blockers.map((value) => `<li>${escapeHtml(value)}</li>`).join("")}</ul>`,
    `<ul data-field="risks">${row.risks.map((value) => `<li>${escapeHtml(value)}</li>`).join("")}</ul>`,
    "</section>",
  ].join("")).join("");
  return {
    schema_version: "2.0.0",
    consumer_id: CONSUMER_ID,
    source_panel_id: readPointer(panel, "/panel_id", reads),
    source_pointer: SOURCE_POINTER,
    rows: normalized,
    html,
  };
};

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const panel = JSON.parse(fs.readFileSync(0, "utf8"));
  if (process.argv.includes("--trace")) {
    const accessed_pointers = [];
    const result = renderCurrentWorkstreams(panel, accessed_pointers);
    process.stdout.write(`${JSON.stringify({ result, accessed_pointers })}\n`);
  } else {
    process.stdout.write(`${JSON.stringify(renderCurrentWorkstreams(panel))}\n`);
  }
}
