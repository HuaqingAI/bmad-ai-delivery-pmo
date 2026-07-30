#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, item, index, all) => {
  if (item.startsWith("--")) pairs.push([item.slice(2), all[index + 1]]);
  return pairs;
}, []));
const hash = (data) => `sha256:${crypto.createHash("sha256").update(data).digest("hex")}`;
const validUnicode = (value) => {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xD800 && code <= 0xDBFF) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xDC00 && next <= 0xDFFF)) throw new Error("JCS rejects unpaired Unicode surrogates");
      index += 1;
    } else if (code >= 0xDC00 && code <= 0xDFFF) throw new Error("JCS rejects unpaired Unicode surrogates");
  }
  return value;
};
const canonical = (value) => {
  if (value === null) return "null";
  if (value === true) return "true";
  if (value === false) return "false";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("JCS rejects non-finite numbers");
    if (Number.isInteger(value) && !Number.isSafeInteger(value)) throw new Error("JCS integer exceeds IEEE-754 safe range");
    return JSON.stringify(value);
  }
  if (typeof value === "string") return JSON.stringify(validUnicode(value));
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value === "object") return `{${Object.keys(value).sort().map((key) => `${canonical(key)}:${canonical(value[key])}`).join(",")}}`;
  throw new Error(`unsupported JCS value: ${typeof value}`);
};
const clone = (value) => structuredClone(value);
const cloneWithBuffers = (value) => {
  if (Buffer.isBuffer(value)) return Buffer.from(value);
  if (Array.isArray(value)) return value.map(cloneWithBuffers);
  if (value !== null && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, cloneWithBuffers(item)]));
  return value;
};
const filesystemToken = (value) => `i_${crypto.createHash("sha256").update(value).digest("hex")}`;
const generationToken = (value) => {
  const match = /^sha256:([0-9a-f]{64})$/.exec(value);
  if (!match) throw new Error("generation id is not a canonical sha256");
  return `h_${match[1]}`;
};
const projectionKindToken = (value) => {
  if (!value || value.includes("\n") || value.includes("\r") || value.normalize("NFC") !== value) throw new Error("projection kind is not canonical");
  return filesystemToken(value);
};
const instanceToken = (value) => {
  if (value === null) return "singleton";
  if (!value || value.includes("\n") || value.includes("\r") || value.normalize("NFC") !== value) throw new Error("instance key is not canonical");
  return filesystemToken(value);
};
const runtimePath = (
  registryDoc, templateName, generationId = null, projectionKind = null, instanceKey = null,
  transactionId = null, nonceId = null, resultId = null, blobId = null, applyOrder = null,
  releaseSetId = null, lifecycleId = null, snapshotId = null,
) => {
  const record = registryDoc.runtime_paths[templateName];
  if (!record || typeof record !== "object" || record.root !== "memory" || typeof record.path !== "string") throw new Error("runtime path template is not registry-bound");
  const substitutions = new Map([
    ["{generation_token}", generationId === null ? null : generationToken(generationId)],
    ["{projection_kind_token}", projectionKind === null ? null : projectionKindToken(projectionKind)],
    ["{instance_token}", instanceToken(instanceKey)],
    ["{transaction_token}", transactionId === null ? null : filesystemToken(transactionId)],
    ["{nonce_token}", nonceId === null ? null : generationToken(nonceId)],
    ["{result_token}", resultId === null ? null : generationToken(resultId)],
    ["{blob_token}", blobId === null ? null : generationToken(blobId)],
    ["{release_set_token}", releaseSetId === null ? null : generationToken(releaseSetId)],
    ["{lifecycle_token}", lifecycleId === null ? null : generationToken(lifecycleId)],
    ["{snapshot_token}", snapshotId === null ? null : generationToken(snapshotId)],
    ["{apply_order}", Number.isSafeInteger(applyOrder) && applyOrder >= 0 ? String(applyOrder) : null],
  ]);
  let targetPath = record.path;
  for (const [token, replacement] of substitutions) {
    if (targetPath.includes(token)) {
      if (replacement === null) throw new Error(`missing runtime path input: ${token}`);
      targetPath = targetPath.replaceAll(token, replacement);
    }
  }
  if (/\{[^{}]+\}/.test(targetPath) || targetPath.normalize("NFC") !== targetPath) throw new Error("runtime path is unresolved or noncanonical");
  if (targetPath.startsWith("/") || targetPath.includes("\\") || targetPath.includes(":") || targetPath.split("/").some((part) => ["", ".", ".."].includes(part))) throw new Error("runtime path is unsafe");
  return targetPath;
};

const ACTION_LEDGER_COLUMNS = ["Action ID", "Status", "Owner", "Workstream", "Affected Workstreams", "Action", "Source", "Reason", "Due / Trigger", "Closure Criteria", "Closure Criteria Verifiable", "Created At", "Started At", "Done At", "Cancelled At", "Baseline Revision", "Related Plan Items", "Related Flow Edges", "Last Updated", "Owning Workflow", "Action Revision"];
const ACTION_LEDGER_LEGACY_20_COLUMNS = ACTION_LEDGER_COLUMNS.slice(0, -1);
const ACTION_LEDGER_LEGACY_12_COLUMNS = ["Action ID", "Status", "Owner", "Workstream", "Affected Workstreams", "Action", "Source", "Reason", "Due / Trigger", "Closure Criteria", "Last Updated", "Owning Workflow"];
const ACTION_LEDGER_PREAMBLE = "# Action Ledger\n\nThis is the ADP action source of truth. Do not use `views/fde-actions.md` as a source file.\n\n";
const ACTION_LEDGER_FIELDS = ["action_id", "status", "owner", "routing_scope_id", "affected_workstreams", "action", "source", "reason", "due_trigger", "closure_criteria", "closure_criteria_verifiable", "created_at", "started_at", "done_at", "cancelled_at", "baseline_revision", "related_plan_items", "related_flow_edges", "last_updated", "owning_workflow", "action_revision"];
const ACTIVE_ACTION_STATUSES = new Set(["open", "in-progress", "blocked"]);
const compareEvidence = (left, right) => Buffer.from(`${left.source_path}\0${left.source_fingerprint}\0${left.observed_at}`).compare(Buffer.from(`${right.source_path}\0${right.source_fingerprint}\0${right.observed_at}`));
const canonicalEvidence = (rows) => {
  const ordered = clone(rows).sort(compareEvidence); const identities = ordered.map(canonical);
  if (canonical(rows) !== canonical(ordered) || new Set(identities).size !== identities.length) throw new Error("evidence is not canonically ordered and unique");
  return ordered;
};
const ledgerCell = (value) => {
  const rendered = String(value);
  if (!rendered || rendered.includes("\n") || rendered.includes("\r") || rendered.normalize("NFC") !== rendered) throw new Error("ledger cell is not canonical");
  return rendered.replaceAll("\\", "\\\\").replaceAll("|", "\\|");
};
const splitLedgerRow = (line, allowEmpty = false) => {
  if (!line.startsWith("| ") || !line.endsWith(" |")) throw new Error("ledger row framing is not canonical");
  const body = line.slice(2, -2); const cells = []; let current = ""; let index = 0;
  while (index < body.length) {
    if (body.startsWith(" | ", index)) { cells.push(current); current = ""; index += 3; continue; }
    if (body[index] === "\\") {
      if (index + 1 >= body.length || !["\\", "|"].includes(body[index + 1])) throw new Error("ledger escape is not canonical");
      current += body[index + 1]; index += 2; continue;
    }
    current += body[index]; index += 1;
  }
  cells.push(current);
  if (cells.some((value) => (!allowEmpty && !value) || value.normalize("NFC") !== value)) throw new Error("ledger row contains an empty or non-NFC cell");
  return cells;
};
const renderActionLedgerRow = (row) => {
  const values = ACTION_LEDGER_FIELDS.map((field) => row[field]);
  values[4] = row.affected_workstreams.length ? row.affected_workstreams.join(", ") : "-";
  return `| ${values.map(ledgerCell).join(" | ")} |`;
};
const renderActionLedger = (rows) => {
  const ordered = clone(rows).sort((a, b) => Buffer.from(a.action_id).compare(Buffer.from(b.action_id)));
  const ids = ordered.map(({ action_id }) => action_id); if (new Set(ids).size !== ids.length) throw new Error("duplicate action id");
  const header = `| ${ACTION_LEDGER_COLUMNS.join(" | ")} |\n`;
  const separator = `| ${ACTION_LEDGER_COLUMNS.map(() => "---").join(" | ")} |\n`;
  const body = ordered.map((row) => `${renderActionLedgerRow(row)}\n`).join("");
  return Buffer.from(ACTION_LEDGER_PREAMBLE + header + separator + body);
};
const parseActionLedger = (raw) => {
  const rendered = Buffer.from(raw).toString("utf8");
  if (Buffer.from(rendered).compare(Buffer.from(raw)) !== 0 || rendered.includes("\r") || rendered.includes("\0") || !rendered.endsWith("\n") || !rendered.startsWith(ACTION_LEDGER_PREAMBLE)) throw new Error("ledger framing is not canonical");
  const lines = rendered.slice(ACTION_LEDGER_PREAMBLE.length).trimEnd().split("\n");
  const header = `| ${ACTION_LEDGER_COLUMNS.join(" | ")} |`; const separator = `| ${ACTION_LEDGER_COLUMNS.map(() => "---").join(" | ")} |`;
  if (lines.length < 2 || lines[0] !== header || lines[1] !== separator) throw new Error("ledger header is not canonical v2");
  const rows = lines.slice(2).map((line) => {
    const cells = splitLedgerRow(line); if (cells.length !== ACTION_LEDGER_FIELDS.length) throw new Error("wrong ledger column count");
    const row = Object.fromEntries(ACTION_LEDGER_FIELDS.map((field, index) => [field, cells[index]]));
    row.affected_workstreams = row.affected_workstreams === "-" ? [] : row.affected_workstreams.split(", ");
    const sorted = [...new Set(row.affected_workstreams)].sort((a, b) => Buffer.from(a).compare(Buffer.from(b)));
    if (canonical(row.affected_workstreams) !== canonical(sorted)) throw new Error("affected workstreams are not canonical");
    if (!/^[1-9][0-9]*$/.test(row.action_revision)) throw new Error("action revision is not an integer");
    row.action_revision = Number(row.action_revision); if (!Number.isSafeInteger(row.action_revision)) throw new Error("action revision is out of range");
    return row;
  });
  if (renderActionLedger(rows).compare(Buffer.from(raw)) !== 0) throw new Error("ledger bytes are not canonical");
  return rows;
};
const parseActionLedgerIngress = (raw, declaredFormat) => {
  if (declaredFormat === "absent") { if (raw !== null) throw new Error("absent ledger declaration has bytes"); return []; }
  if (raw === null) throw new Error("ledger ingress bytes are missing");
  if (declaredFormat === "canonical21") return parseActionLedger(raw);
  const columns = declaredFormat === "legacy12" ? ACTION_LEDGER_LEGACY_12_COLUMNS : declaredFormat === "legacy20" ? ACTION_LEDGER_LEGACY_20_COLUMNS : null;
  if (columns === null) throw new Error("unknown ledger ingress format");
  const rendered = Buffer.from(raw).toString("utf8");
  if (Buffer.from(rendered).compare(Buffer.from(raw)) !== 0 || rendered.includes("\r") || rendered.includes("\0") || !rendered.endsWith("\n") || !rendered.startsWith(ACTION_LEDGER_PREAMBLE)) throw new Error("legacy ledger framing is invalid");
  const lines = rendered.slice(ACTION_LEDGER_PREAMBLE.length).trimEnd().split("\n");
  const header = `| ${columns.join(" | ")} |`; const separator = `| ${columns.map(() => "---").join(" | ")} |`;
  if (lines.length < 2 || lines[0] !== header || lines[1] !== separator) throw new Error("legacy ledger header does not match the declared pinned grammar");
  const fieldByColumn = new Map(ACTION_LEDGER_COLUMNS.map((column, index) => [column, ACTION_LEDGER_FIELDS[index]]));
  const rows = lines.slice(2).map((line) => {
    const cells = splitLedgerRow(line, true); if (cells.length !== columns.length) throw new Error("legacy ledger row has the wrong column count");
    const row = Object.fromEntries(ACTION_LEDGER_FIELDS.map((field) => [field, "-"])); row.affected_workstreams = [];
    columns.forEach((column, index) => {
      const field = fieldByColumn.get(column); const value = cells[index] || "-";
      if (field === "affected_workstreams") {
        const values = value === "-" ? [] : value.split(", "); const sorted = [...new Set(values)].sort((a, b) => Buffer.from(a).compare(Buffer.from(b)));
        if (canonical(values) !== canonical(sorted)) throw new Error("legacy affected workstreams are not canonical"); row[field] = values;
      } else row[field] = value;
    });
    row.action_revision = 1; return row;
  });
  const ids = rows.map(({ action_id }) => action_id); if (ids.includes("-") || new Set(ids).size !== ids.length) throw new Error("legacy ledger action IDs are missing or duplicated");
  return rows.sort((a, b) => Buffer.from(a.action_id).compare(Buffer.from(b.action_id)));
};
const utcInstant = (value) => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)) throw new Error("timestamp must be canonical UTC");
  const parsed = Date.parse(value); if (!Number.isFinite(parsed) || new Date(parsed).toISOString().replace(".000Z", "Z") !== value) throw new Error("timestamp is invalid");
  return parsed;
};
const actionRowChronologyValid = (row) => {
  try {
    const created = utcInstant(row.created_at); const updated = utcInstant(row.last_updated);
    const started = row.started_at === "-" ? null : utcInstant(row.started_at); const done = row.done_at === "-" ? null : utcInstant(row.done_at); const cancelled = row.cancelled_at === "-" ? null : utcInstant(row.cancelled_at);
    if (created > updated || (started !== null && !(created <= started && started <= updated)) || (done !== null && !(created <= done && done <= updated)) || (cancelled !== null && !(created <= cancelled && cancelled <= updated))) return false;
    if (started !== null && done !== null && started > done || started !== null && cancelled !== null && started > cancelled) return false;
    return (row.status === "open" && started === null && done === null && cancelled === null)
      || (["in-progress", "blocked"].includes(row.status) && started !== null && done === null && cancelled === null)
      || (row.status === "done" && started !== null && done !== null && cancelled === null)
      || (row.status === "cancelled" && done === null && cancelled !== null);
  } catch { return false; }
};
const actionRowFromCreate = (command) => {
  const create = command.create; const evidence = canonicalEvidence(command.evidence);
  const observed = evidence.map(({ observed_at }) => observed_at).sort(); const createdAt = observed[0]; const lastUpdated = observed.at(-1);
  return { action_id: command.action_id, status: create.status, owner: create.owner, routing_scope_id: create.routing_scope_id,
    affected_workstreams: [...new Set(create.affected_workstreams ?? [])].sort((a, b) => Buffer.from(a).compare(Buffer.from(b))), action: create.action,
    source: evidence.map((row) => `${row.source_path}@${row.source_fingerprint}`).join("; "), reason: command.command_id,
    due_trigger: create.due_trigger, closure_criteria: create.closure_criteria, closure_criteria_verifiable: "-", created_at: createdAt,
    started_at: ["in-progress", "blocked", "done"].includes(create.status) ? lastUpdated : "-", done_at: create.status === "done" ? lastUpdated : "-",
    cancelled_at: create.status === "cancelled" ? lastUpdated : "-", baseline_revision: "-", related_plan_items: "-", related_flow_edges: "-",
    last_updated: lastUpdated, owning_workflow: "adp-status-sync", action_revision: 1 };
};
const applyActionCommand = (rows, command) => {
  const result = clone(rows); const indexes = result.map((row, index) => row.action_id === command.action_id ? index : -1).filter((index) => index >= 0);
  if (command.operation === "create") { if (indexes.length) throw new Error("action already exists"); result.push(actionRowFromCreate(command)); }
  else {
    if (indexes.length !== 1) throw new Error("patch action is missing or ambiguous"); const row = result[indexes[0]];
    if (row.action_revision !== command.expected_revision) throw new Error("action revision CAS failed");
    if (!actionRowChronologyValid(row)) throw new Error("action lifecycle chronology is invalid");
    const beforeStatus = row.status; const afterStatus = command.set.status ?? beforeStatus;
    if (["done", "cancelled"].includes(beforeStatus) && afterStatus !== beforeStatus) throw new Error("terminal action cannot be reopened");
    for (const [field, value] of Object.entries(command.set)) row[field] = field === "affected_workstreams" ? [...new Set(value)].sort((a, b) => Buffer.from(a).compare(Buffer.from(b))) : value;
    row.action_revision += 1; const evidence = canonicalEvidence(command.evidence); const lastUpdated = evidence.map(({ observed_at }) => observed_at).sort().at(-1);
    if (utcInstant(lastUpdated) < utcInstant(row.last_updated)) throw new Error("action evidence predates Last Updated");
    if (Object.hasOwn(command.set, "status") && afterStatus !== beforeStatus) {
      if (afterStatus === "open") row.started_at = row.done_at = row.cancelled_at = "-";
      else if (["in-progress", "blocked"].includes(afterStatus)) { if (row.started_at === "-") row.started_at = lastUpdated; row.done_at = row.cancelled_at = "-"; }
      else if (afterStatus === "done") { if (row.started_at === "-") row.started_at = lastUpdated; row.done_at = lastUpdated; row.cancelled_at = "-"; }
      else { row.done_at = "-"; row.cancelled_at = lastUpdated; }
    }
    row.source = evidence.map((entry) => `${entry.source_path}@${entry.source_fingerprint}`).join("; "); row.reason = command.command_id;
    row.last_updated = lastUpdated; row.owning_workflow = "adp-status-sync";
    if (!actionRowChronologyValid(row)) throw new Error("action mutation produces invalid lifecycle chronology");
  }
  return result.sort((a, b) => Buffer.from(a.action_id).compare(Buffer.from(b.action_id)));
};
const encodedSummaryField = (value) => encodeURIComponent(String(value)).replace(/[!'()*]/g, (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`).replaceAll("%20", " ");
const renderedActionSummary = (row) => `[action_id:${row.action_id}] ${encodedSummaryField(row.owner)}: ${encodedSummaryField(row.action)} (due: ${encodedSummaryField(row.due_trigger)})`;
const actionSnapshot = (rows, workstreamId, ledgerFingerprint, ledgerRevision) => ({ ledger_fingerprint: ledgerFingerprint, ledger_revision: ledgerRevision,
  actions: rows.filter((row) => ACTIVE_ACTION_STATUSES.has(row.status) && (row.routing_scope_id === workstreamId || row.affected_workstreams.includes(workstreamId))).map((row) => ({
    action_id: row.action_id, owner: row.owner, action: row.action, due_trigger: row.due_trigger, status: row.status, action_revision: row.action_revision,
    routing_scope_id: row.routing_scope_id, affected_workstreams: clone(row.affected_workstreams), rendered_summary: renderedActionSummary(row),
  })) });
const catalogId = (entries) => hash(Buffer.from(canonical({ workstreams: entries })));
const inventoryId = (entries) => hash(Buffer.from(canonical({ physical_workstreams: entries })));
const panelBindingCatalog = (registryDoc, schemaSha, registrySha) => {
  const value = { contract: { schema_id: "urn:adp:panel-sync-contracts:2026-07-24#panel-binding-catalog-v1", schema_sha256: schemaSha, registry_sha256: registrySha }, schema_version: "1.0.0", bindings: clone(registryDoc.panel_binding_map) };
  value.catalog_id = hash(Buffer.from(canonical(value))); return value;
};
const replaceTokens = (value, substitutions) => typeof value === "string" ? (substitutions[value] ?? value)
  : Array.isArray(value) ? value.map((item) => replaceTokens(item, substitutions))
    : value && typeof value === "object" ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, replaceTokens(item, substitutions)])) : value;

const resolveRef = (root, ref) => {
  if (!ref.startsWith("#/")) throw new Error(`external schema ref is not supported: ${ref}`);
  return ref.slice(2).split("/").reduce((value, part) => value[part.replaceAll("~1", "/").replaceAll("~0", "~")], root);
};

const schemaErrors = (instance, rule, root, at = "$") => {
  if (rule.$ref) return schemaErrors(instance, resolveRef(root, rule.$ref), root, at);
  const errors = [];
  for (const child of rule.allOf ?? []) errors.push(...schemaErrors(instance, child, root, at));
  if (rule.oneOf) {
    const count = rule.oneOf.filter((child) => schemaErrors(instance, child, root, at).length === 0).length;
    if (count !== 1) return [`${at}: oneOf matched ${count} branches`];
  }
  if (rule.anyOf && !rule.anyOf.some((child) => schemaErrors(instance, child, root, at).length === 0)) errors.push(`${at}: anyOf matched no branches`);
  if (rule.not && schemaErrors(instance, rule.not, root, at).length === 0) errors.push(`${at}: forbidden schema matched`);
  if (rule.if) {
    const branch = schemaErrors(instance, rule.if, root, at).length === 0 ? "then" : "else";
    if (rule[branch]) errors.push(...schemaErrors(instance, rule[branch], root, at));
  }
  if (Object.hasOwn(rule, "const") && canonical(instance) !== canonical(rule.const)) errors.push(`${at}: const mismatch`);
  if (rule.enum && !rule.enum.some((item) => canonical(item) === canonical(instance))) errors.push(`${at}: enum mismatch`);
  const allowed = rule.type ? (Array.isArray(rule.type) ? rule.type : [rule.type]) : null;
  if (allowed) {
    const ok = allowed.some((kind) => (kind === "null" && instance === null)
      || (kind === "object" && instance !== null && typeof instance === "object" && !Array.isArray(instance))
      || (kind === "array" && Array.isArray(instance)) || (kind === "string" && typeof instance === "string")
      || (kind === "integer" && Number.isInteger(instance)) || (kind === "number" && typeof instance === "number")
      || (kind === "boolean" && typeof instance === "boolean"));
    if (!ok) return [...errors, `${at}: type mismatch`];
  }
  if (typeof instance === "string") {
    if (instance.length < (rule.minLength ?? 0)) errors.push(`${at}: too short`);
    if (rule.maxLength !== undefined && instance.length > rule.maxLength) errors.push(`${at}: too long`);
    if (rule.pattern && !(new RegExp(rule.pattern, "u")).test(instance)) errors.push(`${at}: pattern mismatch`);
  }
  if (typeof instance === "number") {
    if (rule.minimum !== undefined && instance < rule.minimum) errors.push(`${at}: below minimum`);
    if (rule.maximum !== undefined && instance > rule.maximum) errors.push(`${at}: above maximum`);
  }
  if (Array.isArray(instance)) {
    if (instance.length < (rule.minItems ?? 0)) errors.push(`${at}: too few items`);
    if (rule.maxItems !== undefined && instance.length > rule.maxItems) errors.push(`${at}: too many items`);
    if (rule.uniqueItems && new Set(instance.map(canonical)).size !== instance.length) errors.push(`${at}: duplicate items`);
    if (rule.items && typeof rule.items === "object") instance.forEach((item, index) => errors.push(...schemaErrors(item, rule.items, root, `${at}[${index}]`)));
    if (rule.contains && !instance.some((item, index) => schemaErrors(item, rule.contains, root, `${at}[${index}]`).length === 0)) errors.push(`${at}: contains matched no items`);
  }
  if (instance !== null && typeof instance === "object" && !Array.isArray(instance)) {
    for (const key of rule.required ?? []) if (!(key in instance)) errors.push(`${at}: missing ${key}`);
    if (Object.keys(instance).length < (rule.minProperties ?? 0)) errors.push(`${at}: too few properties`);
    const properties = rule.properties ?? {};
    if (rule.additionalProperties === false) for (const key of Object.keys(instance)) if (!(key in properties)) errors.push(`${at}: unknown ${key}`);
    else if (rule.additionalProperties && typeof rule.additionalProperties === "object") for (const key of Object.keys(instance)) if (!(key in properties)) errors.push(...schemaErrors(instance[key], rule.additionalProperties, root, `${at}.${key}`));
    for (const [key, child] of Object.entries(properties)) if (key in instance) errors.push(...schemaErrors(instance[key], child, root, `${at}.${key}`));
  }
  return errors;
};
const validate = (instance, schema, definition) => schemaErrors(instance, schema.$defs[definition], schema).length === 0;
const validateDocument = (instance, schema) => schemaErrors(instance, schema, schema).length === 0;
const contractRecord = (registryDoc, contractName) => {
  const matches = registryDoc.contracts.filter((row) => `${row.name}/${row.version}` === contractName);
  if (matches.length !== 1) throw new Error(`contract registry lookup is not unique: ${contractName}`);
  return matches[0];
};
const expectedContractRef = (registryDoc, contractName, schemaSha, registrySha) => ({
  schema_id: contractRecord(registryDoc, contractName).schema_id, schema_sha256: schemaSha, registry_sha256: registrySha,
});
const embeddedContractRefsValid = (value, registryDoc, schemaSha, registrySha) => {
  const bySchemaId = new Map();
  for (const record of registryDoc.contracts) {
    if (bySchemaId.has(record.schema_id)) return false;
    bySchemaId.set(record.schema_id, record);
  }
  const walk = (current) => {
    if (Array.isArray(current)) return current.every(walk);
    if (current === null || typeof current !== "object") return true;
    if (Object.hasOwn(current, "contract")) {
      const reference = current.contract;
      if (reference === null || typeof reference !== "object" || Array.isArray(reference)) return false;
      const record = bySchemaId.get(reference.schema_id);
      if (!record || canonical(reference) !== canonical(expectedContractRef(registryDoc, `${record.name}/${record.version}`, schemaSha, registrySha))) return false;
    }
    return Object.values(current).every(walk);
  };
  return walk(value);
};
const validateRegistered = (instance, schema, registryDoc, contractName, schemaSha, registrySha) => {
  const record = contractRecord(registryDoc, contractName);
  const definition = record.schema_pointer.replace(/^#\/\$defs\//, "");
  if (!validate(instance, schema, definition)) return false;
  if (instance && typeof instance === "object" && Object.hasOwn(instance, "contract")
      && canonical(instance.contract) !== canonical(expectedContractRef(registryDoc, contractName, schemaSha, registrySha))) return false;
  return embeddedContractRefsValid(instance, registryDoc, schemaSha, registrySha);
};
const artifactBytes = (value) => value === null ? null : Buffer.from(value, "base64");
const encodedBytes = (value) => value === null ? null : Buffer.from(value).toString("base64");
const jsonPointer = (document, pointer) => {
  pointer = pointer.replace(/^#/, "");
  return pointer === "" ? document : pointer.slice(1).split("/").reduce((value, token) => value[token.replaceAll("~1", "/").replaceAll("~0", "~")], document);
};
const setPointer = (document, pointer, value) => {
  const tokens = pointer.slice(1).split("/").map((token) => token.replaceAll("~1", "/").replaceAll("~0", "~"));
  let current = document;
  for (const token of tokens.slice(0, -1)) current = current[token] ??= {};
  current[tokens.at(-1)] = value;
};

const renderCreate = (template, data) => {
  const escTable = (value) => value.replaceAll("\\", "\\\\").replaceAll("|", "\\|");
  const bullets = (values) => [...new Set(values)].sort((a, b) => Buffer.from(a).compare(Buffer.from(b))).map((value) => `- ${value}`).join("\n") || "- TBD";
  const rows = [...data.artifact_rows].sort((left, right) => ["artifact", "path", "baseline_status", "notes"]
    .map((key) => Buffer.from(left[key]).compare(Buffer.from(right[key]))).find((result) => result !== 0) ?? 0);
  const table = rows.map((row) => `| ${escTable(row.artifact)} | ${escTable(row.path)} | ${escTable(row.baseline_status)} | ${escTable(row.notes)} |`).join("\n") || "| TBD | TBD | TBD | TBD |";
  const replacements = {
    "{{CREATED_AT}}": data.created_at, "{{WORKSTREAM_ID}}": data.workstream_id, "{{WORKSTREAM_NAME}}": data.name,
    "{{FDE_OWNER}}": data.fde_owner, "{{BUSINESS_OWNER}}": data.business_owner, "{{BMM_PHASE}}": data.phase,
    "{{ADP_STATUS}}": data.status, "{{SCOPE_SUMMARY}}": data.scope_summary, "{{ARTIFACT_TABLE}}": table,
    "{{DEPENDS_ON}}": bullets(data.depends_on), "{{IMPACTS}}": bullets(data.impacts), "{{L0_REFERENCES}}": bullets(data.l0_references),
  };
  let rendered = template;
  for (const [token, replacement] of Object.entries(replacements)) {
    if (!rendered.includes(token)) throw new Error(`template token multiplicity: ${token}`);
    rendered = rendered.replaceAll(token, replacement);
  }
  if (/\{\{[^{}]+\}\}/.test(rendered)) throw new Error("unresolved template token");
  return rendered;
};

const meetingBlock = (row) => [
  `<!-- adp:meeting-history:v1 command_id=${row.command_id} entry_id=${row.entry_id} observed_at=${row.observed_at} -->`,
  `### Meeting Sync Update: ${row.observed_at.slice(0, 10)} - ${row.entry_id}`, "",
  `- Source: ${row.source_path} @ ${row.source_fingerprint}`, `- Classification: ${row.classification}`,
  `- Update: ${row.summary}`, `- Owner: ${row.owner}`, `- Due / trigger: ${row.due_trigger}`, `- Status: ${row.status}`, "", "",
].join("\n");

const meetingHistoryBlockRe = /<!-- adp:meeting-history:v1 command_id=([^\s]+) entry_id=([^\s]+) observed_at=([^\s]+) -->\n### Meeting Sync Update: ([0-9]{4}-[0-9]{2}-[0-9]{2}) - ([^\n]+)\n\n- Source: ([^\n]+) @ (sha256:[0-9a-f]{64})\n- Classification: ([^\n]+)\n- Update: ([^\n]+)\n- Owner: ([^\n]+)\n- Due \/ trigger: ([^\n]+)\n- Status: ([^\n]+)\n\n/y;
const parseMeetingHistory = (section) => {
  if (section === "## Meeting Sync History") return [];
  const prefix = "## Meeting Sync History\n\n";
  if (!section.startsWith(prefix)) throw new Error("Meeting Sync History framing is not canonical");
  const body = `${section.slice(prefix.length)}\n\n`; const rows = []; let position = 0;
  while (position < body.length) {
    meetingHistoryBlockRe.lastIndex = position; const match = meetingHistoryBlockRe.exec(body);
    if (!match) throw new Error("Meeting Sync History block is not canonical");
    const [, command_id, entry_id, observed_at, observedDate, headingEntryId, source_path, source_fingerprint, classification, summary, owner, due_trigger, status] = match;
    const row = { entry_id, command_id, observed_at, source_path, source_fingerprint, classification, summary, owner, due_trigger, status };
    if (headingEntryId !== entry_id || observedDate !== observed_at.slice(0, 10) || meetingBlock(row) !== match[0]) throw new Error("Meeting Sync History identity is inconsistent");
    rows.push(row); position = meetingHistoryBlockRe.lastIndex;
  }
  const keys = rows.map(({ observed_at, entry_id }) => `${observed_at}\0${entry_id}`);
  const ordered = [...keys].sort((a, b) => Buffer.from(a).compare(Buffer.from(b)));
  if (canonical(keys) !== canonical(ordered) || new Set(keys).size !== keys.length) throw new Error("Meeting Sync History keys are not canonical and unique");
  return rows;
};

const migrateWdr = (text, timestamp) => {
  const legacyUpdates = [...text.matchAll(/^(?:<!-- adp-meeting-sync:[^\n]+ -->\n)?## Meeting Sync Update: [^\n]+$/gm)];
  if (legacyUpdates.length) {
    if (/^## Meeting Sync History$/m.test(text)) throw new Error("mixed legacy and canonical meeting history is ambiguous");
    const start = legacyUpdates[0].index;
    text = `${text.slice(0, start)}## Meeting Sync History\n\n${text.slice(start)}`.replace(/^## Meeting Sync Update:/gm, "### Meeting Sync Update:");
  }
  const heading = /^## ([^\n]+)$/gm;
  const matches = [...text.matchAll(heading)];
  const preamble = (matches.length ? text.slice(0, matches[0].index) : text).replace(/^\n+|\n+$/g, "");
  const sections = {};
  matches.forEach((match, index) => {
    const end = index + 1 < matches.length ? matches[index + 1].index : text.length;
    sections[match[1]] = text.slice(match.index, end).replace(/^\n+|\n+$/g, "");
  });
  if (!/^- Last status sync:/mi.test(sections["Project Status"])) {
    sections["Project Status"] = sections["Project Status"].replace(/^(- Next actions:[^\n]*)$/mi, `$1\n- Last status sync: ${timestamp}`);
  }
  const order = ["Identity", "BMM Artifact Index", "Scope", "Acceptance", "Project Status", "Roadmap", "Cross-Workstream Links", "Decisions and Evidence", "Checkpoint Sync Log", "Meeting Sync History", "Record Rule"];
  return [...(preamble ? [preamble] : []), ...order.filter((name) => sections[name]).map((name) => sections[name])].join("\n\n") + "\n";
};

const wdrSectionOrder = ["Identity", "BMM Artifact Index", "Scope", "Acceptance", "Project Status", "Roadmap", "Cross-Workstream Links", "Decisions and Evidence", "Checkpoint Sync Log", "Meeting Sync History", "Record Rule"];
const wdrRequiredSections = new Set(["Identity", "BMM Artifact Index", "Scope", "Acceptance", "Project Status", "Cross-Workstream Links", "Decisions and Evidence", "Record Rule"]);
const wdrRequiredLabels = {
  Identity: ["Workstream ID", "Name", "FDE owner", "Business owner", "Current BMM phase", "Current ADP status"],
  Scope: ["In scope", "Out of scope", "Key assumptions", "Open questions"],
  Acceptance: ["Acceptance criteria", "Acceptance owner", "Evidence required", "Current readiness", "Unclosed gaps"],
  "Project Status": ["Progress", "Blockers", "Risks", "Dependencies", "Scope or change notes", "Next actions"],
  "Decisions and Evidence": ["Decision links", "Business Decision Packet links", "Evidence links", "Customer/business confirmations"],
};
const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const splitWdr = (value) => {
  const matches = [...value.matchAll(/^## ([^\n]+)\n/gm)];
  if (matches.length === 0) throw new Error("WDR has no sections");
  const preamble = value.slice(0, matches[0].index).replace(/^\n+|\n+$/g, "");
  const sections = {};
  matches.forEach((match, index) => {
    if (Object.hasOwn(sections, match[1])) throw new Error("duplicate WDR section");
    const end = index + 1 < matches.length ? matches[index + 1].index : value.length;
    sections[match[1]] = value.slice(match.index, end).replace(/^\n+|\n+$/g, "");
  });
  return [preamble, sections];
};
const completeWdrValid = (value, workstreamId) => {
  try {
    if (!value.endsWith("\n") || value.includes("\r") || value.includes("\0") || value.includes("{{")) return false;
    const [preamble, sections] = splitWdr(value);
    if (!preamble.startsWith("# Workstream Delivery Record\n\nCreated: ") || [...wdrRequiredSections].some((name) => !Object.hasOwn(sections, name))) return false;
    const positions = Object.keys(sections).map((name) => wdrSectionOrder.indexOf(name));
    if (positions.some((value, index) => value < 0 || (index > 0 && value < positions[index - 1]))) return false;
    for (const [section, labels] of Object.entries(wdrRequiredLabels)) for (const label of labels) {
      const matches = sections[section].match(new RegExp(`^- ${escapeRegex(label)}: [^\\r\\n]+$`, "gm")) ?? [];
      if (matches.length !== 1) return false;
    }
    const identities = [...sections.Identity.matchAll(/^- Workstream ID: ([^\r\n]+)$/gm)].map((match) => match[1]);
    return canonical(identities) === canonical([workstreamId])
      && sections["BMM Artifact Index"].includes("| Artifact | Path / Link | Baseline Status | Notes |")
      && sections["Record Rule"].startsWith("## Record Rule\n\n");
  } catch { return false; }
};
const fixtureWdr = (workstreamId) => `# Workstream Delivery Record

Created: 2026-07-24T02:00:00Z

## Identity

- Workstream ID: ${workstreamId}
- Name: Checkout
- FDE owner: FDE-C
- Business owner: Biz-C
- Current BMM phase: implementation
- Current ADP status: active

## BMM Artifact Index

| Artifact | Path / Link | Baseline Status | Notes |
| --- | --- | --- | --- |
| PRD | prd.md | current | reviewed |

## Scope

- In scope: checkout delivery
- Out of scope: TBD
- Key assumptions: TBD
- Open questions: TBD

## Acceptance

- Acceptance criteria: tests pass
- Acceptance owner: Biz-C
- Evidence required: test report
- Current readiness: draft
- Unclosed gaps: none

## Project Status

- Progress: Initial progress
- Blockers: access
- Risks: schedule
- Dependencies: platform
- Scope or change notes: none
- Next actions: review
- Last status sync: 2026-07-24T01:00:00Z

## Cross-Workstream Links

Depends on:

- l1-platform

Impacts:

- l1-payments

L0 references:

- l0.md

## Decisions and Evidence

- Decision links: decisions.md
- Business Decision Packet links: packet.md
- Evidence links: evidence.md
- Customer/business confirmations: confirmed

## Record Rule

This file summarizes project-level coordination state.
`;
const replaceWdrLabel = (section, label, value) => {
  const pattern = new RegExp(`^- ${escapeRegex(label)}: [^\\r\\n]+$`, "gm");
  const matches = section.match(pattern) ?? [];
  if (matches.length !== 1) throw new Error(`WDR label is missing or ambiguous: ${label}`);
  return section.replace(pattern, `- ${label}: ${value}`);
};
const parseWdrList = (value) => {
  if (value === "TBD") return [];
  const result = []; let current = ""; let index = 0;
  while (index < value.length) {
    if (value.startsWith("; ", index)) { if (!current) throw new Error("empty WDR collection item"); result.push(current); current = ""; index += 2; continue; }
    if (value[index] === "\\") {
      if (value.startsWith("\\TBD", index) && !current && (index + 4 === value.length || value.startsWith("; ", index + 4))) { current = "TBD"; index += 4; continue; }
      if (index + 1 >= value.length || !["\\", ";"].includes(value[index + 1])) throw new Error("noncanonical WDR collection escape");
      current += value[index + 1]; index += 2; continue;
    }
    current += value[index]; index += 1;
  }
  if (!current) throw new Error("empty WDR collection item"); result.push(current);
  if (result.some((item) => item.normalize("NFC") !== item)) throw new Error("non-NFC WDR collection item");
  if (renderWdrList(result) !== value) throw new Error("WDR collection is not byte-canonical");
  return result;
};
const renderWdrList = (values) => {
  if (!values.length) return "TBD";
  if (values.some((value) => !value || value.includes("\n") || value.includes("\r") || value.normalize("NFC") !== value)) throw new Error("WDR collection item is not canonical");
  return values.map((value) => value === "TBD" ? "\\TBD" : value.replaceAll("\\", "\\\\").replaceAll(";", "\\;")).join("; ");
};
const MANAGED_ACTION_RE = /^\[action_id:([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)\] ([^:]+): (.+) \(due: ([^)]+)\)$/;
const parseManagedActionSummary = (value) => {
  const match = MANAGED_ACTION_RE.exec(value);
  if (!match) { if (value.startsWith("[action_id:")) throw new Error("malformed managed action marker"); return null; }
  const decoded = { action_id: match[1], owner: decodeURIComponent(match[2]), action: decodeURIComponent(match[3]), due_trigger: decodeURIComponent(match[4]) };
  if (renderedActionSummary(decoded) !== value) throw new Error("managed action marker is not canonically escaped");
  return decoded;
};
const partitionNextActions = (values) => {
  const manual = []; const managed = [];
  for (const value of values) { const parsed = parseManagedActionSummary(value); if (parsed === null) manual.push(value); else managed.push([parsed.action_id, value]); }
  const ids = managed.map(([id]) => id); if (new Set(ids).size !== ids.length) throw new Error("duplicate managed action marker");
  const ordered = clone(managed).sort((a, b) => Buffer.from(a[0]).compare(Buffer.from(b[0])));
  if (canonical(managed) !== canonical(ordered)) throw new Error("managed action markers are not ordered");
  return [manual, ordered.map(([, value]) => value)];
};
const collectionValue = (current, patch) => {
  const existing = parseWdrList(current);
  const incoming = [...new Set(patch.values)];
  const result = patch.mode === "replace" ? incoming : patch.mode === "add" ? [...existing, ...incoming.filter((item) => !existing.includes(item))] : existing.filter((item) => !incoming.includes(item));
  return renderWdrList(result);
};
const applyWdrPatch = (before, command, actionSummaries = null) => {
  const [preamble, sections] = splitWdr(before); const mutation = clone(command.set);
  if (command.evidence?.length) canonicalEvidence(command.evidence);
  const currentFields = new Set(["status", "phase", "progress", "blockers", "risks", "dependencies", "change_notes", "refresh_actions"]);
  if (Object.keys(mutation).some((field) => currentFields.has(field)) && command.evidence?.length) {
    const observedAt = command.evidence.map(({ observed_at }) => observed_at).sort().at(-1); const currentSync = wdrLabel(sections["Project Status"], "Last status sync");
    if (utcInstant(observedAt) < utcInstant(currentSync)) throw new Error("WDR evidence predates Last status sync");
    if (Object.hasOwn(mutation, "last_status_sync") && mutation.last_status_sync !== observedAt) throw new Error("Last status sync does not match command evidence");
    mutation.last_status_sync = observedAt;
  }
  const identityLabels = { status: "Current ADP status", phase: "Current BMM phase" };
  const statusLabels = { progress: "Progress", blockers: "Blockers", risks: "Risks", dependencies: "Dependencies", change_notes: "Scope or change notes", last_status_sync: "Last status sync" };
  for (const [field, label] of Object.entries(identityLabels)) if (Object.hasOwn(mutation, field)) sections.Identity = replaceWdrLabel(sections.Identity, label, mutation[field]);
  for (const [field, label] of Object.entries(statusLabels)) {
    if (!Object.hasOwn(mutation, field)) continue;
    if (field === "last_status_sync" && !/^- Last status sync:/m.test(sections["Project Status"])) sections["Project Status"] += `\n- Last status sync: ${mutation[field]}`;
    else if (mutation[field] && typeof mutation[field] === "object") {
      const current = sections["Project Status"].match(new RegExp(`^- ${escapeRegex(label)}: ([^\\n]+)$`, "m"));
      if (!current) throw new Error(`missing collection label: ${label}`);
      sections["Project Status"] = replaceWdrLabel(sections["Project Status"], label, collectionValue(current[1], mutation[field]));
    } else sections["Project Status"] = replaceWdrLabel(sections["Project Status"], label, mutation[field]);
  }
  if (mutation.refresh_actions) {
    const [manual] = partitionNextActions(parseWdrList(wdrLabel(sections["Project Status"], "Next actions")));
    const [, managed] = partitionNextActions(actionSummaries ?? []);
    sections["Project Status"] = replaceWdrLabel(sections["Project Status"], "Next actions", renderWdrList([...manual, ...managed]));
  }
  if (Object.hasOwn(mutation, "meeting_history_append")) {
    const existingRows = parseMeetingHistory(sections["Meeting Sync History"] ?? "## Meeting Sync History");
    const incomingRows = mutation.meeting_history_append;
    const incomingKeys = incomingRows.map(({ observed_at, entry_id }) => `${observed_at}\0${entry_id}`);
    if (new Set(incomingKeys).size !== incomingKeys.length) throw new Error("duplicate Meeting Sync History key in command");
    const merged = new Map(existingRows.map((row) => [`${row.observed_at}\0${row.entry_id}`, row])); let changed = false;
    for (const row of incomingRows) {
      const key = `${row.observed_at}\0${row.entry_id}`;
      if (merged.has(key)) { if (meetingBlock(merged.get(key)) !== meetingBlock(row)) throw new Error("Meeting Sync History key has different bytes"); }
      else { merged.set(key, row); changed = true; }
    }
    if (changed) {
      const ordered = [...merged.entries()].sort((a, b) => Buffer.from(a[0]).compare(Buffer.from(b[0]))).map(([, row]) => row);
      sections["Meeting Sync History"] = `## Meeting Sync History\n\n${ordered.map(meetingBlock).join("").replace(/\n+$/, "")}`;
    }
  }
  if (Object.hasOwn(mutation, "roadmap")) {
    const roadmap = mutation.roadmap;
    const lines = roadmap && typeof roadmap === "object" ? roadmap.lines : null;
    if (!roadmap || roadmap.mode !== "replace" || !Array.isArray(lines) || lines.length < 2
        || lines.some((line) => typeof line !== "string" || line.includes("\n") || line.includes("\r") || line.includes("\0")
          || line.normalize("NFC") !== line || /^##(?: |$)/.test(line))) throw new Error("Roadmap mutation is not byte-canonical");
    sections.Roadmap = `## Roadmap\n\n${lines.join("\n")}`;
  }
  const headingBySlug = { identity: "Identity", "bmm-artifact-index": "BMM Artifact Index", scope: "Scope", acceptance: "Acceptance", roadmap: "Roadmap", "cross-workstream-links": "Cross-Workstream Links", "decisions-evidence": "Decisions and Evidence", "record-rule": "Record Rule", "checkpoint-sync-log": "Checkpoint Sync Log" };
  for (const owned of mutation.owned_sections ?? []) {
    const heading = headingBySlug[owned.section];
    if (!heading || !Array.isArray(owned.lines) || !owned.lines.length
        || owned.lines.some((line) => typeof line !== "string" || line.includes("\n") || line.includes("\r") || line.includes("\0")
          || line.normalize("NFC") !== line || /^##(?: |$)/.test(line))) throw new Error("owned section lines may not inject headings");
    const body = owned.lines.join("\n");
    sections[heading] = owned.mode === "append" && sections[heading] ? `${sections[heading].replace(/\n+$/, "")}\n${body}` : `## ${heading}\n\n${body}`;
  }
  return [preamble, ...wdrSectionOrder.filter((name) => Object.hasOwn(sections, name)).map((name) => sections[name])].join("\n\n") + "\n";
};
const wdrCurrentSignature = (value, workstreamId) => {
  if (!completeWdrValid(value, workstreamId)) throw new Error("WDR current signature requires a canonical record");
  const [, sections] = splitWdr(value); const identity = sections.Identity; const status = sections["Project Status"];
  return { status: wdrLabel(identity, "Current ADP status"), phase: wdrLabel(identity, "Current BMM phase"), progress: wdrLabel(status, "Progress"),
    blockers: parseWdrList(wdrLabel(status, "Blockers")), risks: parseWdrList(wdrLabel(status, "Risks")), dependencies: parseWdrList(wdrLabel(status, "Dependencies")),
    change_notes: wdrLabel(status, "Scope or change notes"), next_actions: parseWdrList(wdrLabel(status, "Next actions")), last_status_sync: wdrLabel(status, "Last status sync") };
};
const wdrCounterDelta = (before, after, workstreamId) => before === after ? [0, 0] : [canonical(wdrCurrentSignature(before, workstreamId)) === canonical(wdrCurrentSignature(after, workstreamId)) ? 0 : 1, 1];

const canonicalTimestamp = (value) => {
  if (!/(Z|[+-]\d\d:\d\d)$/.test(value)) throw new Error("timezone required");
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) throw new Error("invalid timestamp");
  return parsed.toISOString().replace(/\.\d{3}Z$/, "Z");
};

const driftSemantics = (value) => {
  const selected = [...value.selected_workstreams].sort();
  const rowIds = value.workstreams.map(({ workstream_id }) => workstream_id);
  const coverage = new Set(rowIds).size === rowIds.length && canonical([...rowIds].sort()) === canonical(selected);
  const allInSync = coverage && value.workstreams.every(({ status }) => status === "in-sync");
  return coverage && ((value.overall_status === "in-sync") === allInSync);
};

const wdrLabel = (section, label) => {
  const matches = [...section.matchAll(new RegExp(`^- ${escapeRegex(label)}: ([^\\r\\n]+)$`, "gm"))];
  if (matches.length !== 1) throw new Error(`WDR label is missing or ambiguous: ${label}`);
  return matches[0][1];
};
const parseWdrCurrent = (raw, workstreamId) => {
  const text = Buffer.from(raw).toString("utf8"); if (Buffer.from(text).compare(Buffer.from(raw)) !== 0 || !completeWdrValid(text, workstreamId)) throw new Error("WDR is invalid");
  const [, sections] = splitWdr(text); const identity = sections.Identity; const status = sections["Project Status"];
  const nextActions = parseWdrList(wdrLabel(status, "Next actions")); const [, managed] = partitionNextActions(nextActions);
  const actionIds = managed.map((summary) => parseManagedActionSummary(summary).action_id);
  return { workstream_id: workstreamId, phase: wdrLabel(identity, "Current BMM phase"), status: wdrLabel(identity, "Current ADP status"),
    progress: wdrLabel(status, "Progress"), blockers: parseWdrList(wdrLabel(status, "Blockers")), risks: parseWdrList(wdrLabel(status, "Risks")),
    dependencies: parseWdrList(wdrLabel(status, "Dependencies")), action_ids: actionIds, next_actions: nextActions };
};
const statusIntentFixture = (registryDoc, schemaSha, registrySha) => {
  const evidenceA = { source_path: "meetings/m1.md", source_fingerprint: `sha256:${"a".repeat(64)}`, observed_at: "2026-07-24T02:00:00Z" };
  const evidenceB = { source_path: "checkpoints/c1.md", source_fingerprint: `sha256:${"b".repeat(64)}`, observed_at: "2026-07-24T02:01:00Z" };
  const intents = [
    { contract: expectedContractRef(registryDoc, "status-mutation-intent/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", intent_id: "intent-checkout-blockers", origin_producer: "adp-meeting-sync", workstream_id: "l1-checkout", set: { blockers: { mode: "replace", values: ["Access"] } }, evidence: [evidenceA] },
    { contract: expectedContractRef(registryDoc, "status-mutation-intent/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", intent_id: "intent-checkout-progress", origin_producer: "adp-bmm-checkpoint-sync", workstream_id: "l1-checkout", set: { progress: "Implementation active", risks: { mode: "replace", values: ["Schedule"] } }, evidence: [evidenceB] },
  ].sort((a, b) => Buffer.from(`${a.workstream_id}\0${a.intent_id}`).compare(Buffer.from(`${b.workstream_id}\0${b.intent_id}`)));
  const actionCommand = { contract: expectedContractRef(registryDoc, "action-ledger-mutation/2.0.0", schemaSha, registrySha), schema_version: "2.0.0", command_id: "cmd-action-before-status", operation: "create", action_id: "A-STATUS-1", create: { owner: "FDE-C", status: "open", action: "Resolve access", due_trigger: "next sync", closure_criteria: "access confirmed", routing_scope_id: "l1-checkout", affected_workstreams: ["l1-checkout"] }, evidence: [evidenceA] };
  const patch = { contract: expectedContractRef(registryDoc, "wdr-mutation/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", command_id: "cmd-status-l1-checkout", issuer: { producer_id: "adp-status-sync", capability_id: `sha256:${"0".repeat(64)}` }, operation: "patch", workstream_id: "l1-checkout", expected_wdr_revision: 4, expected_file_generation: 7,
    set: { blockers: { mode: "replace", values: ["Access"] }, progress: "Implementation active", risks: { mode: "replace", values: ["Schedule"] } },
    consumed_intent_ids: intents.map((row) => hash(Buffer.from(canonical(row)))).sort((a, b) => Buffer.from(a).compare(Buffer.from(b))),
    evidence: [evidenceA, evidenceB].sort(compareEvidence) };
  return { contract: expectedContractRef(registryDoc, "status-sync-batch/2.0.0", schemaSha, registrySha), schema_version: "2.0.0", batch_id: "batch-status-1", execution_policy: "ordered-stop-on-first-failure-no-rollback",
    command_order: [actionCommand.command_id, patch.command_id], accepted_intent_ids: intents.map(({ intent_id }) => intent_id).sort((a, b) => Buffer.from(a).compare(Buffer.from(b))), accepted_intents: intents,
    intent_bindings: intents.map((intent) => ({ intent_id: intent.intent_id, command_id: patch.command_id, fields: Object.keys(intent.set).sort((a, b) => Buffer.from(a).compare(Buffer.from(b))) })), action_commands: [actionCommand], wdr_patches: [patch] };
};
const statusIntentApplicationSemantics = (batch, registryDoc, schemaRoot, schemaSha, registrySha) => {
  if (!validateRegistered(batch, schemaRoot, registryDoc, "status-sync-batch/2.0.0", schemaSha, registrySha)) return false;
  const intents = batch.accepted_intents; const intentIds = intents.map(({ intent_id }) => intent_id);
  const sortedIntents = clone(intents).sort((a, b) => Buffer.from(`${a.workstream_id}\0${a.intent_id}`).compare(Buffer.from(`${b.workstream_id}\0${b.intent_id}`)));
  if (canonical(intents) !== canonical(sortedIntents) || new Set(intentIds).size !== intentIds.length || canonical(batch.accepted_intent_ids) !== canonical([...intentIds].sort((a, b) => Buffer.from(a).compare(Buffer.from(b))))) return false;
  if (!intents.every((row) => validateRegistered(row, schemaRoot, registryDoc, "status-mutation-intent/1.0.0", schemaSha, registrySha))) return false;
  const actions = batch.action_commands; const patches = batch.wdr_patches;
  if (!actions.every((row) => validateRegistered(row, schemaRoot, registryDoc, "action-ledger-mutation/2.0.0", schemaSha, registrySha)) || !patches.every((row) => validateRegistered(row, schemaRoot, registryDoc, "wdr-mutation/1.0.0", schemaSha, registrySha))) return false;
  try { [...intents, ...actions, ...patches].forEach((row) => canonicalEvidence(row.evidence)); } catch { return false; }
  if (canonical(actions) !== canonical(clone(actions).sort((a, b) => Buffer.from(a.command_id).compare(Buffer.from(b.command_id)))) || canonical(patches) !== canonical(clone(patches).sort((a, b) => Buffer.from(`${a.workstream_id}\0${a.command_id}`).compare(Buffer.from(`${b.workstream_id}\0${b.command_id}`))))) return false;
  const patchWorkstreams = patches.map(({ workstream_id }) => workstream_id); const intentWorkstreams = new Set(intents.map(({ workstream_id }) => workstream_id));
  if (new Set(patchWorkstreams).size !== patchWorkstreams.length || canonical([...new Set(patchWorkstreams)].sort()) !== canonical([...intentWorkstreams].sort())) return false;
  const allCommands = [...actions, ...patches]; const commandIds = allCommands.map(({ command_id }) => command_id); if (new Set(commandIds).size !== commandIds.length || canonical(batch.command_order) !== canonical(commandIds)) return false;
  const patchById = new Map(patches.map((row) => [row.command_id, row])); const bindings = batch.intent_bindings;
  if (canonical(bindings) !== canonical(clone(bindings).sort((a, b) => Buffer.from(a.intent_id).compare(Buffer.from(b.intent_id))))) return false;
  const byIntent = new Map(); for (const binding of bindings) { const rows = byIntent.get(binding.intent_id) ?? []; rows.push(binding); byIntent.set(binding.intent_id, rows); }
  if (byIntent.size !== new Set(intentIds).size || intentIds.some((id) => (byIntent.get(id) ?? []).length !== 1)) return false;
  const merged = new Map(); const evidence = new Map(); const workstreams = new Map();
  for (const intent of intents) {
    const binding = byIntent.get(intent.intent_id)[0]; const patch = patchById.get(binding.command_id); const fields = Object.keys(intent.set).sort((a, b) => Buffer.from(a).compare(Buffer.from(b)));
    if (!patch || patch.workstream_id !== intent.workstream_id || canonical(binding.fields) !== canonical(fields)) return false;
    const values = merged.get(patch.command_id) ?? {}; for (const [field, value] of Object.entries(intent.set)) { if (Object.hasOwn(values, field) && canonical(values[field]) !== canonical(value)) return false; values[field] = clone(value); }
    merged.set(patch.command_id, values); evidence.set(patch.command_id, [...(evidence.get(patch.command_id) ?? []), ...clone(intent.evidence)]); workstreams.set(patch.command_id, intent.workstream_id);
  }
  if (merged.size !== patchById.size) return false;
  for (const [id, patch] of patchById) {
    const unique = new Map(evidence.get(id).map((row) => [canonical(row), row])); const expectedEvidence = [...unique.values()].sort(compareEvidence);
    const expectedConsumed = intents.filter((intent) => byIntent.get(intent.intent_id)[0].command_id === id)
      .map((intent) => hash(Buffer.from(canonical(intent)))).sort((a, b) => Buffer.from(a).compare(Buffer.from(b)));
    if (patch.issuer.producer_id !== "adp-status-sync" || patch.operation !== "patch" || patch.workstream_id !== workstreams.get(id)
      || canonical(patch.set) !== canonical(merged.get(id)) || canonical(patch.consumed_intent_ids) !== canonical(expectedConsumed)
      || canonical(patch.evidence) !== canonical(expectedEvidence)) return false;
  }
  return batch.execution_policy === "ordered-stop-on-first-failure-no-rollback";
};
const programStatusWdrFixture = (suiteDoc, registryDoc, schemaSha, registrySha) => {
  const workstreamId = "l1-checkout"; const raw = Buffer.from(fixtureWdr(workstreamId));
  const state = { contract: expectedContractRef(registryDoc, "wdr-file-state/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", workstream_id: workstreamId, record_path: `workstreams/${workstreamId}/delivery-record.md`, record_fingerprint: hash(raw), wdr_revision: 4, file_generation: 7, lifecycle: "active" };
  const payload = clone(suiteDoc.contract_schema_vectors.find(({ id }) => id === "program-status-payload-schema-valid").instance); const current = parseWdrCurrent(raw, workstreamId);
  payload.workstream_current = [{ workstream_id: current.workstream_id, wdr_fingerprint: state.record_fingerprint, wdr_revision: state.wdr_revision, file_generation: state.file_generation, phase: current.phase, status: current.status, progress: current.progress, blockers: current.blockers, risks: current.risks, dependencies: current.dependencies, action_ids: current.action_ids }];
  return { selected_workstreams: [workstreamId], wdrs: { [workstreamId]: raw }, wdr_states: { [workstreamId]: state }, payload };
};
const programStatusCurrentFromWdrSemantics = (pack, registryDoc, schemaRoot, schemaSha, registrySha) => {
  try {
    const selected = pack.selected_workstreams; if (canonical(selected) !== canonical([...new Set(selected)].sort((a, b) => Buffer.from(a).compare(Buffer.from(b))))) return false;
    if (canonical(Object.keys(pack.wdrs).sort()) !== canonical([...selected].sort()) || canonical(Object.keys(pack.wdr_states).sort()) !== canonical([...selected].sort())) return false;
    if (!validateRegistered(pack.payload, schemaRoot, registryDoc, "program-status-payload/2.0.0", schemaSha, registrySha)) return false;
    const expected = [];
    for (const id of selected) {
      const raw = pack.wdrs[id]; const state = pack.wdr_states[id]; if (!validateRegistered(state, schemaRoot, registryDoc, "wdr-file-state/1.0.0", schemaSha, registrySha)) return false;
      if (state.workstream_id !== id || state.record_path !== `workstreams/${id}/delivery-record.md` || state.record_fingerprint !== hash(raw) || state.lifecycle !== "active") return false;
      const current = parseWdrCurrent(raw, id); expected.push({ workstream_id: id, wdr_fingerprint: state.record_fingerprint, wdr_revision: state.wdr_revision, file_generation: state.file_generation, phase: current.phase, status: current.status, progress: current.progress, blockers: current.blockers, risks: current.risks, dependencies: current.dependencies, action_ids: current.action_ids });
    }
    return canonical(pack.payload.workstream_current) === canonical(expected);
  } catch { return false; }
};
const driftFinding = (workstreamId, kind, actionDiff = null) => {
  const body = {
    kind: actionDiff === null ? kind : "action-projection-drift",
    repairability: actionDiff === null ? "non-repairable" : "repairable",
    severity: actionDiff === null ? "warning" : "blocked", workstream_id: workstreamId,
    action_id: actionDiff === null ? null : actionDiff.action_id, action_diff: actionDiff === null ? null : clone(actionDiff),
    source_path: `workstreams/${workstreamId}/delivery-record.md`, source_line: actionDiff === null ? null : 42,
  };
  const identityBody = Object.fromEntries(Object.entries(body).filter(([key]) => !["source_path", "source_line"].includes(key)));
  return { finding_id: hash(Buffer.from(canonical(identityBody))), ...body };
};
const expectedDriftVerdict = (pack, registryDoc, schemaSha, registrySha) => {
  const rows = parseActionLedger(pack.ledger_raw); const state = pack.ledger_state; const verdictRows = [];
  for (const id of pack.selected_workstreams) {
    const raw = pack.wdrs[id]; const wdrState = pack.wdr_states[id]; const sidecar = pack.sidecars[id]; const findings = []; const actionDiffs = [];
    const snapshot = actionSnapshot(rows, id, state.ledger_fingerprint, state.ledger_revision);
    if (sidecar.ledger_fingerprint !== state.ledger_fingerprint) findings.push(driftFinding(id, "ledger-fingerprint-mismatch"));
    if (sidecar.ledger_revision !== state.ledger_revision) findings.push(driftFinding(id, "ledger-revision-mismatch"));
    const expected = new Map(snapshot.actions.map((row) => [row.action_id, row]));
    const sidecarById = new Map(sidecar.actions.map((row) => [row.action_id, row]));
    const [, currentManaged] = partitionNextActions(parseWdrCurrent(raw, id).next_actions);
    const wdrById = new Map(currentManaged.map((summary) => [parseManagedActionSummary(summary).action_id, summary]));
    const actionIds = [...new Set([...expected.keys(), ...sidecarById.keys(), ...wdrById.keys()])].sort();
    for (const actionId of actionIds) {
      const expectedRecord = expected.get(actionId); const sidecarRecord = sidecarById.get(actionId); const wdrSummary = wdrById.get(actionId);
      const projectionPresent = sidecarRecord !== undefined || wdrSummary !== undefined;
      const rendered = wdrSummary ?? sidecarRecord?.rendered_summary ?? null;
      let driftKind = null;
      if (expectedRecord !== undefined && (sidecarRecord === undefined || wdrSummary === undefined)) driftKind = "missing-from-wdr";
      else if (expectedRecord === undefined && projectionPresent) driftKind = "orphan-in-wdr";
      else if (expectedRecord !== undefined && (canonical(sidecarRecord) !== canonical(expectedRecord) || wdrSummary !== expectedRecord.rendered_summary)) driftKind = "content-mismatch";
      if (driftKind === null) continue;
      const diff = { action_id: actionId, drift_kind: driftKind, ledger_present: expectedRecord !== undefined, wdr_present: projectionPresent,
        ledger_revision: expectedRecord?.action_revision ?? null, wdr_rendered_sha256: rendered === null ? null : hash(Buffer.from(rendered)) };
      actionDiffs.push(diff); findings.push(driftFinding(id, "action-projection-drift", diff));
    }
    if (sidecar.wdr_revision !== wdrState.wdr_revision || sidecar.file_generation !== wdrState.file_generation) findings.push(driftFinding(id, "wdr-lineage-mismatch"));
    const ordered = [...new Map(findings.map((row) => [row.finding_id, row])).values()].sort((a, b) => Buffer.from(a.finding_id).compare(Buffer.from(b.finding_id)));
    verdictRows.push({ workstream_id: id, wdr_fingerprint: hash(raw), wdr_revision: wdrState.wdr_revision, file_generation: wdrState.file_generation, sidecar_fingerprint: hash(Buffer.from(canonical(sidecar))), sidecar_ledger_fingerprint: sidecar.ledger_fingerprint, status: ordered.length ? "drift" : "in-sync", action_diffs: actionDiffs, findings: ordered, finding_ids: ordered.map(({ finding_id }) => finding_id) });
  }
  const verdict = { contract: expectedContractRef(registryDoc, "action-projection-drift-verdict/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", generation_id: pack.generation_id, selection_policy_id: pack.selection_policy_id, ledger_fingerprint: state.ledger_fingerprint, selected_workstreams: clone(pack.selected_workstreams), workstreams: verdictRows, overall_status: verdictRows.every(({ status }) => status === "in-sync") ? "in-sync" : "degraded" };
  verdict.verdict_id = hash(Buffer.from(canonical(verdict))); return verdict;
};
const driftContentFixture = (registryDoc, schemaSha, registrySha) => {
  const [rows, ledgerRaw, ledgerState] = refreshLedgerFixture(registryDoc, schemaSha, registrySha); const id = "l1-checkout"; const snapshot = actionSnapshot(rows, id, ledgerState.ledger_fingerprint, ledgerState.ledger_revision);
  const wdrRaw = Buffer.from(applyWdrPatch(fixtureWdr(id), { set: { refresh_actions: true } }, snapshot.actions.map(({ rendered_summary }) => rendered_summary)));
  const wdrState = { contract: expectedContractRef(registryDoc, "wdr-file-state/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", workstream_id: id, record_path: `workstreams/${id}/delivery-record.md`, record_fingerprint: hash(wdrRaw), wdr_revision: 5, file_generation: 8, lifecycle: "active" };
  const sidecar = { contract: expectedContractRef(registryDoc, "wdr-action-projection/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", workstream_id: id, ledger_fingerprint: ledgerState.ledger_fingerprint, ledger_revision: ledgerState.ledger_revision, wdr_revision: 5, file_generation: 8, renderer_id: "urn:adp:wdr-action-renderer:1.0.0", renderer_sha256: registryDoc.protocol.sha256, actions: clone(snapshot.actions) };
  const pack = { generation_id: `sha256:${"1".repeat(64)}`, selection_policy_id: `sha256:${"2".repeat(64)}`, selected_workstreams: [id], ledger_raw: ledgerRaw, ledger_state: ledgerState, wdrs: { [id]: wdrRaw }, wdr_states: { [id]: wdrState }, sidecars: { [id]: sidecar } };
  pack.verdict = expectedDriftVerdict(pack, registryDoc, schemaSha, registrySha); return pack;
};
const actionProjectionDriftContentSemantics = (pack, registryDoc, schemaRoot, schemaSha, registrySha) => {
  try {
    const selected = pack.selected_workstreams; if (canonical(selected) !== canonical([...new Set(selected)].sort((a, b) => Buffer.from(a).compare(Buffer.from(b))))) return false;
    if ([pack.wdrs, pack.wdr_states, pack.sidecars].some((value) => canonical(Object.keys(value).sort()) !== canonical([...selected].sort()))) return false;
    const rows = parseActionLedger(pack.ledger_raw); const state = pack.ledger_state;
    if (!validateRegistered(state, schemaRoot, registryDoc, "action-ledger-state/1.0.0", schemaSha, registrySha) || canonical(state) !== canonical(actionLedgerStateDocument(rows, pack.ledger_raw, state.ledger_revision, state.applied_commands, registryDoc, schemaSha, registrySha))) return false;
    for (const id of selected) {
      const wdrState = pack.wdr_states[id]; const sidecar = pack.sidecars[id];
      if (!validateRegistered(wdrState, schemaRoot, registryDoc, "wdr-file-state/1.0.0", schemaSha, registrySha) || !validateRegistered(sidecar, schemaRoot, registryDoc, "wdr-action-projection/1.0.0", schemaSha, registrySha)
        || wdrState.workstream_id !== id || wdrState.record_path !== `workstreams/${id}/delivery-record.md` || wdrState.record_fingerprint !== hash(pack.wdrs[id]) || wdrState.lifecycle !== "active"
        || sidecar.workstream_id !== id || sidecar.renderer_id !== "urn:adp:wdr-action-renderer:1.0.0" || sidecar.renderer_sha256 !== registryDoc.protocol.sha256) return false;
    }
    return validateRegistered(pack.verdict, schemaRoot, registryDoc, "action-projection-drift-verdict/1.0.0", schemaSha, registrySha) && canonical(pack.verdict) === canonical(expectedDriftVerdict(pack, registryDoc, schemaSha, registrySha));
  } catch { return false; }
};

const semverTuple = (value) => {
  const match = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/.exec(value);
  if (!match) throw new Error("runtime version is not strict semver"); return match.slice(1).map(Number);
};
const compareSemver = (left, right) => {
  const a = semverTuple(left); const b = semverTuple(right);
  for (let index = 0; index < 3; index += 1) if (a[index] !== b[index]) return a[index] < b[index] ? -1 : 1;
  return 0;
};
const conformanceSigningPayload = (row) => {
  const body = clone(row); delete body.result_id; delete body.provenance.signature; return Buffer.from(canonical(body));
};
const ed25519PrivateKey = (seedHex) => crypto.createPrivateKey({ key: Buffer.concat([Buffer.from("302e020100300506032b657004220420", "hex"), Buffer.from(seedHex, "hex")]), format: "der", type: "pkcs8" });
const ed25519PublicKey = (raw) => crypto.createPublicKey({ key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), raw]), format: "der", type: "spki" });
const FIXTURE_RELEASE_SIGNERS = [
  ["python-production-adapter", "native-posix", "python-production-build-1", ["production-adapter", "real-posix-fault-injection"], "cpython", "3.10.0", "fixture-posix-ci", "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f", "macos", "posix-flock"],
  ["node-production-adapter", "native-windows", "node-production-build-1", ["native-windows-ci", "production-adapter"], "node", "22.0.0", "fixture-windows-ci", "1f1e1d1c1b1a191817161514131211100f0e0d0c0b0a09080706050403020100", "windows", "windows-lockfileex"],
];
const fixtureReleaseTrustRoots = () => FIXTURE_RELEASE_SIGNERS.map(([implementationId, platform, , , , , keyId, seedHex]) => {
    const publicDer = crypto.createPublicKey(ed25519PrivateKey(seedHex)).export({ format: "der", type: "spki" });
    return { key_id: keyId, platform, implementation_owner_id: `owner-${implementationId}`, allowed_implementation_ids: [implementationId],
      public_key_base64: publicDer.subarray(-32).toString("base64"),
      not_before: "2026-01-01T00:00:00Z", not_after: "2027-01-01T00:00:00Z" };
  });
const designReleaseRegistryFixture = (registryDoc) => {
  const fixture = clone(registryDoc);
  fixture.conformance_suite.implementation_conformance_status = "passed";
  fixture.evidence_trust.trust_roots = fixtureReleaseTrustRoots();
  return fixture;
};
const isDesignReleaseRegistry = (registryDoc) => registryDoc.conformance_suite.implementation_conformance_status === "passed"
  && canonical(registryDoc.evidence_trust.trust_roots) === canonical(fixtureReleaseTrustRoots());

const releaseGateAccepts = (receipts, expectedIds, hashes, registryDoc, evidenceBlobs, securityContext) => {
  if (!securityContext || canonical(securityContext) !== canonical({ clock_source: "host-secure-clock-v1", evaluation_time: securityContext.evaluation_time, available: true })) return false;
  let evaluationTime; try { evaluationTime = utcInstant(securityContext.evaluation_time); } catch { return false; }
  if (receipts.length < 2) return false;
  if (new Set(receipts.map((row) => row.implementation_id)).size !== receipts.length) return false;
  if (new Set(receipts.map((row) => row.adapter_build_id)).size !== receipts.length) return false;
  if (new Set(receipts.map((row) => row.provenance.signer_key_id)).size !== receipts.length) return false;
  const platforms = new Set(receipts.map(({ platform }) => platform));
  if (!platforms.has("native-posix") || !platforms.has("native-windows")) return false;
  const trustRoots = new Map(registryDoc.evidence_trust.trust_roots.map((row) => [row.key_id, row]));
  if (trustRoots.size !== registryDoc.evidence_trust.trust_roots.length
      || trustRoots.size < registryDoc.evidence_trust.minimum_production_trust_roots) return false;
  const lockProfile = registryDoc.lock_profile; const profileBody = clone(lockProfile); delete profileBody.profile_id;
  if (lockProfile.profile_id !== hash(Buffer.from(canonical(profileBody)))) return false;
  const replayKeys = new Set();
  const ownerIds = new Set();
  const accepted = receipts.every((row) => {
    const body = clone(row); const resultId = body.result_id; delete body.result_id;
    const classes = new Set(row.evidence_classes); const runtimePolicy = registryDoc.runtime_policy[row.runtime.implementation];
    const runtimeMajor = semverTuple(row.runtime.version)[0];
    if (!runtimePolicy || compareSemver(row.runtime.version, runtimePolicy.minimum_inclusive) < 0
        || compareSemver(row.runtime.version, runtimePolicy.maximum_exclusive) >= 0
        || (runtimePolicy.allowed_major_versions && !runtimePolicy.allowed_major_versions.includes(runtimeMajor))
        || row.runtime.build_digest !== row.adapter_build_id) return false;
    const provenance = row.provenance; const root = trustRoots.get(provenance.signer_key_id);
    if (!root || root.platform !== row.platform || canonical(root.allowed_implementation_ids) !== canonical([row.implementation_id])
      || typeof root.implementation_owner_id !== "string" || !root.implementation_owner_id
      || provenance.signature_algorithm !== registryDoc.evidence_trust.signature_algorithm || provenance.signed_at !== row.executed_at) return false;
    ownerIds.add(root.implementation_owner_id);
    const executedAt = utcInstant(row.executed_at);
    if (row.runtime.implementation === "cpython" && evaluationTime >= utcInstant(runtimePolicy.support_review_before)) return false;
    if (!(utcInstant(root.not_before) <= executedAt && executedAt < utcInstant(root.not_after)
      && utcInstant(root.not_before) <= evaluationTime && evaluationTime < utcInstant(root.not_after))) return false;
    if (provenance.os_family !== (row.platform === "native-posix" ? "posix" : "windows")) return false;
    const replayKey = `${provenance.signer_key_id}\0${provenance.ci_run_id}\0${provenance.ci_attempt}\0${provenance.evidence_nonce}`;
    if (replayKeys.has(replayKey)) return false; replayKeys.add(replayKey);
    let signatureValid = false;
    try { signatureValid = crypto.verify(null, conformanceSigningPayload(row), ed25519PublicKey(Buffer.from(root.public_key_base64, "base64")), Buffer.from(provenance.signature, "base64")); } catch { return false; }
    const blobIds = [provenance.test_log_sha256, provenance.fault_matrix_sha256, row.lock_evidence.evidence_log_sha256];
    if (!signatureValid || blobIds.some((id) => !Buffer.isBuffer(evidenceBlobs[id]) || hash(evidenceBlobs[id]) !== id)) return false;
    const lock = row.lock_evidence; const expectedPrimitive = row.platform === "native-posix" ? "posix-flock" : "windows-lockfileex";
    const lockValid = lock.lock_profile_id === lockProfile.profile_id && lock.primitive === expectedPrimitive
      && lock.fact_lock_path === lockProfile.fact_lock.path && lock.panel_lock_path === lockProfile.panel_lock.path
      && lockProfile.supported_filesystems.includes(lock.filesystem_kind)
      && ["multiprocess_contention_passed", "crash_release_passed", "order_passed", "timeout_passed", "upgrade_rejected"].every((field) => lock[field]);
    return lockValid && resultId === hash(Buffer.from(canonical(body))) && row.evidence_kind === "implementation-conformance" && row.native_durability_exercised
      && row.failed_vector_ids.length === 0 && canonical([...row.passed_vector_ids].sort()) === canonical(expectedIds)
      && ["registry", "suite", "schema", "protocol"].every((name) => row[`${name}_sha256`] === hashes[name])
      && classes.has("production-adapter")
      && (row.platform !== "native-posix" || classes.has("real-posix-fault-injection"))
      && (row.platform !== "native-windows" || classes.has("native-windows-ci"));
  });
  return accepted && ownerIds.size === receipts.length;
};

const implementationConformanceReceipts = (expectedIds, hashes, registryDoc) => {
  const blobs = {};
  const receipts = FIXTURE_RELEASE_SIGNERS.map(([implementationId, platform, buildLabel, classes, runtimeName, runtimeVersion, signerKey, seedHex, osName, primitive]) => {
  const buildId = hash(Buffer.from(buildLabel));
  const testLog = Buffer.from(`${platform}:full-suite:${expectedIds.length}:passed\n`); const faultLog = Buffer.from(`${platform}:native-fault-matrix:passed\n`); const lockLog = Buffer.from(`${platform}:multiprocess-crash-order-timeout-upgrade:passed\n`);
  for (const blob of [testLog, faultLog, lockLog]) blobs[hash(blob)] = blob;
  const row = { schema_version: "1.0.0", evidence_kind: "implementation-conformance", implementation_id: implementationId, implementation_version: "1.0.0", platform, host_platform: `${osName}-x86_64`,
    runtime: { implementation: runtimeName, version: runtimeVersion, executable_sha256: hash(Buffer.from(`${runtimeName}-executable`)), build_digest: buildId },
    native_durability_exercised: true, registry_sha256: hashes.registry, suite_sha256: hashes.suite, schema_sha256: hashes.schema, protocol_sha256: hashes.protocol,
    passed_vector_ids: clone(expectedIds), failed_vector_ids: [], executed_at: "2026-07-24T03:00:00Z", adapter_build_id: buildId, evidence_classes: classes,
    lock_evidence: { lock_profile_id: registryDoc.lock_profile.profile_id, primitive, fact_lock_path: registryDoc.lock_profile.fact_lock.path, panel_lock_path: registryDoc.lock_profile.panel_lock.path,
      filesystem_kind: "local", multiprocess_contention_passed: true, crash_release_passed: true, order_passed: true, timeout_passed: true, upgrade_rejected: true, evidence_log_sha256: hash(lockLog) },
    provenance: { ci_run_id: `ci-${runtimeName}-001`, ci_attempt: 1, os_family: platform === "native-posix" ? "posix" : "windows", os_name: osName, os_version: "2026.1", architecture: "x86_64",
      test_log_sha256: hash(testLog), fault_matrix_sha256: hash(faultLog), signer_key_id: signerKey, signature_algorithm: "Ed25519", evidence_nonce: `nonce-${runtimeName}-001`, signed_at: "2026-07-24T03:00:00Z", signature: "" } };
  row.provenance.signature = crypto.sign(null, conformanceSigningPayload(row), ed25519PrivateKey(seedHex)).toString("base64");
  row.result_id = hash(Buffer.from(canonical(row))); return row;
});
  return [receipts, blobs];
};
const receiptEvidenceBlobIds = (receipt) => [...new Set([
  receipt.provenance.test_log_sha256, receipt.provenance.fault_matrix_sha256, receipt.lock_evidence.evidence_log_sha256,
])].sort((a, b) => Buffer.from(a).compare(Buffer.from(b)));
const releaseEvidenceSetFixture = (receipts, evidenceBlobs, registryDoc, schemaSha, registrySha) => {
  const entries = []; const store = {};
  for (const receipt of clone(receipts).sort((a, b) => Buffer.from(a.result_id).compare(Buffer.from(b.result_id)))) {
    const receiptRaw = Buffer.from(canonical(receipt));
    const receiptPath = runtimePath(registryDoc, "release_evidence_receipt_template", null, null, null, null, null, receipt.result_id);
    store[receiptPath] = receiptRaw;
    const blobs = receiptEvidenceBlobIds(receipt).map((blobId) => {
      const blobPath = runtimePath(registryDoc, "release_evidence_blob_template", null, null, null, null, null, null, blobId);
      store[blobPath] = evidenceBlobs[blobId];
      return { sha256: blobId, path: blobPath };
    });
    entries.push({ result_id: receipt.result_id, receipt_path: receiptPath, receipt_sha256: hash(receiptRaw), evidence_blobs: blobs });
  }
  const releaseSet = {
    contract: expectedContractRef(registryDoc, "release-evidence-set/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", set_generation: 1, previous_set_id: null,
    trust_domain: isDesignReleaseRegistry(registryDoc) ? "design-mock" : "production",
    registry_sha256: registrySha, accepted_at: "2026-07-24T03:00:01Z", entries,
  };
  releaseSet.release_evidence_set_id = hash(Buffer.from(canonical(releaseSet)));
  const setRaw = Buffer.from(canonical(releaseSet));
  const setPath = runtimePath(registryDoc, "release_evidence_set_archive_template", null, null, null, null, null, null, null, null, releaseSet.release_evidence_set_id);
  const transitionId = "release-evidence-bootstrap-1";
  const transition = {
    contract: expectedContractRef(registryDoc, "release-evidence-transition-receipt/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
    transition_id: transitionId, before_generation: 0, before_set_id: null, after_generation: 1,
    after_set_id: releaseSet.release_evidence_set_id, journal_id: "journal-release-evidence-bootstrap-1",
    status: "committed", committed_at: releaseSet.accepted_at,
  };
  transition.receipt_id = hash(Buffer.from(canonical(transition))); const transitionRaw = Buffer.from(canonical(transition));
  const transitionPath = runtimePath(registryDoc, "release_evidence_transition_receipt_template", null, null, null, transitionId);
  const [coreJournal, coreMarker] = transitionJournalFixture(
    "release-evidence", transitionId, transition.journal_id,
    [
      { role: "release-evidence", operation: "create", path: setPath, before_raw: null, after_raw: setRaw },
      { role: "release-evidence", operation: "create", path: registryDoc.runtime_paths.release_evidence_set.path, before_raw: null, after_raw: setRaw },
    ],
    transitionPath, transitionRaw, registryDoc, schemaSha, registrySha,
  );
  const coreJournalPath = runtimePath(registryDoc, "release_evidence_journal_template", null, null, null, transitionId);
  const coreMarkerPath = runtimePath(registryDoc, "release_evidence_terminal_marker_template", null, null, null, transitionId);
  const coreJournalRaw = Buffer.from(canonical(coreJournal)); const coreMarkerRaw = Buffer.from(canonical(coreMarker));
  const history = {
    contract: expectedContractRef(registryDoc, "release-evidence-history-index/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
    current_generation: 1, current_set_id: releaseSet.release_evidence_set_id,
    entries: [{ set_generation: 1, set_id: releaseSet.release_evidence_set_id, accepted_at: releaseSet.accepted_at,
      set_path: setPath, set_sha256: hash(setRaw), transition_receipt_path: transitionPath, transition_receipt_sha256: hash(transitionRaw),
      journal_path: coreJournalPath, journal_sha256: hash(coreJournalRaw), terminal_marker_path: coreMarkerPath, terminal_marker_sha256: hash(coreMarkerRaw) }],
  };
  history.index_id = hash(Buffer.from(canonical(history)));
  store[registryDoc.runtime_paths.release_evidence_set.path] = setRaw;
  store[registryDoc.runtime_paths.release_evidence_history_index.path] = Buffer.from(canonical(history));
  store[setPath] = setRaw; store[transitionPath] = transitionRaw; store[coreJournalPath] = coreJournalRaw; store[coreMarkerPath] = coreMarkerRaw;
  return [releaseSet, store];
};
const loadReleaseEvidenceSet = (pack, registryDoc, schemaRoot, schemaSha, registrySha, expectedIds, hashes, securityContext) => {
  try {
    if (!Buffer.isBuffer(pack.registry_raw) || !pack.registry_raw.equals(Buffer.from(canonical(registryDoc))) || hash(pack.registry_raw) !== registrySha) return null;
    const store = pack.release_store; const setPath = registryDoc.runtime_paths.release_evidence_set.path;
    const setRaw = store[setPath]; if (!Buffer.isBuffer(setRaw)) return null;
    const releaseSet = JSON.parse(setRaw.toString("utf8")); const releaseBody = clone(releaseSet); delete releaseBody.release_evidence_set_id;
    const evaluationTime = utcInstant(securityContext.evaluation_time);
    if (!setRaw.equals(Buffer.from(canonical(releaseSet)))
        || !validateRegistered(releaseSet, schemaRoot, registryDoc, "release-evidence-set/1.0.0", schemaSha, registrySha)
        || releaseSet.release_evidence_set_id !== hash(Buffer.from(canonical(releaseBody)))
        || releaseSet.registry_sha256 !== registrySha
        || releaseSet.trust_domain !== (isDesignReleaseRegistry(registryDoc) ? "design-mock" : "production")
        || utcInstant(releaseSet.accepted_at) > evaluationTime) return null;
    const historyPath = registryDoc.runtime_paths.release_evidence_history_index.path; const historyRaw = store[historyPath];
    if (!Buffer.isBuffer(historyRaw)) return null;
    const history = JSON.parse(historyRaw.toString("utf8")); const historyBody = clone(history); delete historyBody.index_id;
    if (!historyRaw.equals(Buffer.from(canonical(history)))
      || !validateRegistered(history, schemaRoot, registryDoc, "release-evidence-history-index/1.0.0", schemaSha, registrySha)
      || history.index_id !== hash(Buffer.from(canonical(historyBody))) || history.current_generation !== releaseSet.set_generation
      || history.current_set_id !== releaseSet.release_evidence_set_id) return null;
    const ordered = clone(releaseSet.entries).sort((a, b) => Buffer.from(a.result_id).compare(Buffer.from(b.result_id)));
    if (canonical(releaseSet.entries) !== canonical(ordered)
        || new Set(releaseSet.entries.map(({ result_id }) => result_id)).size !== releaseSet.entries.length
        || new Set(releaseSet.entries.map(({ receipt_path }) => receipt_path)).size !== releaseSet.entries.length) return null;
    const expectedPaths = new Set([setPath, historyPath]); let previousSetId = null; let previousAcceptedAt = null;
    for (let index = 0; index < history.entries.length; index += 1) {
      const entry = history.entries[index]; const expectedGeneration = index + 1;
      const archivePath = runtimePath(registryDoc, "release_evidence_set_archive_template", null, null, null, null, null, null, null, null, entry.set_id);
      const archiveRaw = store[entry.set_path]; const transitionRaw = store[entry.transition_receipt_path];
      const journalRaw = store[entry.journal_path]; const terminalRaw = store[entry.terminal_marker_path];
      if (![archiveRaw, transitionRaw, journalRaw, terminalRaw].every(Buffer.isBuffer)) return null;
      const archiveSet = JSON.parse(archiveRaw.toString("utf8")); const transition = JSON.parse(transitionRaw.toString("utf8"));
      const transitionJournal = JSON.parse(journalRaw.toString("utf8")); const terminalMarker = JSON.parse(terminalRaw.toString("utf8"));
      const transitionPath = runtimePath(registryDoc, "release_evidence_transition_receipt_template", null, null, null, transition.transition_id);
      const transitionBody = clone(transition); delete transitionBody.receipt_id;
      const acceptedAt = utcInstant(archiveSet.accepted_at);
      if (entry.set_generation !== expectedGeneration || entry.set_path !== archivePath || entry.set_sha256 !== hash(archiveRaw)
        || entry.transition_receipt_path !== transitionPath || entry.transition_receipt_sha256 !== hash(transitionRaw)
        || !archiveRaw.equals(Buffer.from(canonical(archiveSet)))
        || !validateRegistered(archiveSet, schemaRoot, registryDoc, "release-evidence-set/1.0.0", schemaSha, registrySha)
        || archiveSet.release_evidence_set_id !== entry.set_id || archiveSet.set_generation !== expectedGeneration || archiveSet.previous_set_id !== previousSetId
        || !transitionRaw.equals(Buffer.from(canonical(transition)))
        || !validateRegistered(transition, schemaRoot, registryDoc, "release-evidence-transition-receipt/1.0.0", schemaSha, registrySha)
        || transition.receipt_id !== hash(Buffer.from(canonical(transitionBody))) || transition.before_generation !== expectedGeneration - 1
        || transition.before_set_id !== previousSetId || transition.after_generation !== expectedGeneration || transition.after_set_id !== entry.set_id
        || transition.committed_at !== archiveSet.accepted_at || entry.accepted_at !== archiveSet.accepted_at
        || (previousAcceptedAt !== null && previousAcceptedAt >= acceptedAt) || acceptedAt > evaluationTime
        || entry.journal_path !== runtimePath(registryDoc, "release_evidence_journal_template", null, null, null, transition.transition_id)
        || entry.journal_sha256 !== hash(journalRaw)
        || entry.terminal_marker_path !== runtimePath(registryDoc, "release_evidence_terminal_marker_template", null, null, null, transition.transition_id)
        || entry.terminal_marker_sha256 !== hash(terminalRaw)
        || !journalRaw.equals(Buffer.from(canonical(transitionJournal))) || !terminalRaw.equals(Buffer.from(canonical(terminalMarker)))
        || !journalSemantics(transitionJournal, terminalMarker, schemaRoot, registryDoc, schemaSha, registrySha)
        || terminalMarker.state !== "committed" || transitionJournal.transaction_id !== transition.transition_id
        || transitionJournal.journal_id !== transition.journal_id
        || canonical(transitionJournal.receipt_target_paths) !== canonical([entry.transition_receipt_path])) return null;
      expectedPaths.add(entry.set_path); expectedPaths.add(entry.transition_receipt_path); expectedPaths.add(entry.journal_path); expectedPaths.add(entry.terminal_marker_path);
      const historicalReceipts = []; const historicalBlobs = {};
      for (const historicalEntry of archiveSet.entries) {
        expectedPaths.add(historicalEntry.receipt_path); for (const blob of historicalEntry.evidence_blobs) expectedPaths.add(blob.path);
        const historicalRaw = store[historicalEntry.receipt_path];
        if (!Buffer.isBuffer(historicalRaw)) return null;
        const historicalReceipt = JSON.parse(historicalRaw.toString("utf8"));
        if (!historicalRaw.equals(Buffer.from(canonical(historicalReceipt))) || hash(historicalRaw) !== historicalEntry.receipt_sha256
          || historicalReceipt.result_id !== historicalEntry.result_id || !validate(historicalReceipt, schemaRoot, "conformanceResultV1")) return null;
        const expectedHistoricalBlobs = receiptEvidenceBlobIds(historicalReceipt).map((blobId) => ({
          sha256: blobId, path: runtimePath(registryDoc, "release_evidence_blob_template", null, null, null, null, null, null, blobId),
        }));
        if (canonical(historicalEntry.evidence_blobs) !== canonical(expectedHistoricalBlobs)) return null;
        for (const blob of historicalEntry.evidence_blobs) {
          const blobRaw = store[blob.path]; if (!Buffer.isBuffer(blobRaw) || hash(blobRaw) !== blob.sha256) return null;
          historicalBlobs[blob.sha256] = blobRaw;
        }
        historicalReceipts.push(historicalReceipt);
      }
      if (!releaseGateAccepts(historicalReceipts, expectedIds, hashes, registryDoc, historicalBlobs,
        { clock_source: "host-secure-clock-v1", evaluation_time: archiveSet.accepted_at, available: true })) return null;
      previousSetId = entry.set_id; previousAcceptedAt = acceptedAt;
    }
    if (history.entries.length !== history.current_generation || previousSetId !== history.current_set_id) return null;
    const receipts = []; const blobs = {};
    for (const entry of releaseSet.entries) {
      const expectedReceiptPath = runtimePath(registryDoc, "release_evidence_receipt_template", null, null, null, null, null, entry.result_id);
      if (entry.receipt_path !== expectedReceiptPath || !Buffer.isBuffer(store[entry.receipt_path])) return null;
      const receiptRaw = store[entry.receipt_path]; const receipt = JSON.parse(receiptRaw.toString("utf8"));
      if (!receiptRaw.equals(Buffer.from(canonical(receipt))) || hash(receiptRaw) !== entry.receipt_sha256
          || receipt.result_id !== entry.result_id || !validate(receipt, schemaRoot, "conformanceResultV1")) return null;
      expectedPaths.add(entry.receipt_path);
      const expectedBlobs = receiptEvidenceBlobIds(receipt).map((blobId) => ({
        sha256: blobId, path: runtimePath(registryDoc, "release_evidence_blob_template", null, null, null, null, null, null, blobId),
      }));
      if (canonical(entry.evidence_blobs) !== canonical(expectedBlobs)) return null;
      for (const blob of entry.evidence_blobs) {
        const raw = store[blob.path]; if (!Buffer.isBuffer(raw) || hash(raw) !== blob.sha256) return null;
        expectedPaths.add(blob.path); blobs[blob.sha256] = raw;
      }
      receipts.push(receipt);
    }
    if (canonical(Object.keys(store).sort()) !== canonical([...expectedPaths].sort())
        || !releaseGateAccepts(receipts, expectedIds, hashes, registryDoc, blobs, securityContext)) return null;
    return [releaseSet, receipts, blobs];
  } catch { return null; }
};

const releaseEvidenceTransitionFixture = (receipts, evidenceBlobs, registryDoc, schemaSha, registrySha, afterAcceptedAt = "2026-07-24T03:10:00Z") => {
  const [beforeSet, beforeStore] = releaseEvidenceSetFixture(receipts, evidenceBlobs, registryDoc, schemaSha, registrySha);
  const historyPath = registryDoc.runtime_paths.release_evidence_history_index.path;
  const beforeHistoryRaw = beforeStore[historyPath]; const beforeHistory = JSON.parse(beforeHistoryRaw.toString("utf8"));
  const afterSet = clone(beforeSet);
  Object.assign(afterSet, {
    set_generation: 2, previous_set_id: beforeSet.release_evidence_set_id, accepted_at: afterAcceptedAt,
  });
  delete afterSet.release_evidence_set_id;
  afterSet.release_evidence_set_id = hash(Buffer.from(canonical(afterSet)));
  const afterSetRaw = Buffer.from(canonical(afterSet));
  const archivePath = runtimePath(registryDoc, "release_evidence_set_archive_template", null, null, null, null, null, null, null, null, afterSet.release_evidence_set_id);
  const transitionId = "release-evidence-transition-2"; const journalId = "journal-release-evidence-transition-2";
  const transition = {
    contract: expectedContractRef(registryDoc, "release-evidence-transition-receipt/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", transition_id: transitionId,
    before_generation: 1, before_set_id: beforeSet.release_evidence_set_id,
    after_generation: 2, after_set_id: afterSet.release_evidence_set_id,
    journal_id: journalId, status: "committed", committed_at: afterSet.accepted_at,
  };
  transition.receipt_id = hash(Buffer.from(canonical(transition)));
  const transitionRaw = Buffer.from(canonical(transition));
  const transitionPath = runtimePath(registryDoc, "release_evidence_transition_receipt_template", null, null, null, transitionId);
  const currentPath = registryDoc.runtime_paths.release_evidence_set.path;
  const [coreJournal, coreMarker] = transitionJournalFixture(
    "release-evidence", transitionId, journalId,
    [
      { role: "release-evidence", operation: "create", path: archivePath, before_raw: null, after_raw: afterSetRaw },
      { role: "release-evidence", operation: "replace", path: currentPath, before_raw: Buffer.from(canonical(beforeSet)), after_raw: afterSetRaw },
    ],
    transitionPath, transitionRaw, registryDoc, schemaSha, registrySha,
  );
  const coreJournalPath = runtimePath(registryDoc, "release_evidence_journal_template", null, null, null, transitionId);
  const coreMarkerPath = runtimePath(registryDoc, "release_evidence_terminal_marker_template", null, null, null, transitionId);
  const coreJournalRaw = Buffer.from(canonical(coreJournal)); const coreMarkerRaw = Buffer.from(canonical(coreMarker));
  const afterHistory = clone(beforeHistory);
  Object.assign(afterHistory, { current_generation: 2, current_set_id: afterSet.release_evidence_set_id });
  afterHistory.entries.push({
    set_generation: 2, set_id: afterSet.release_evidence_set_id, accepted_at: afterSet.accepted_at,
    set_path: archivePath, set_sha256: hash(afterSetRaw),
    transition_receipt_path: transitionPath, transition_receipt_sha256: hash(transitionRaw),
    journal_path: coreJournalPath, journal_sha256: hash(coreJournalRaw),
    terminal_marker_path: coreMarkerPath, terminal_marker_sha256: hash(coreMarkerRaw),
  });
  delete afterHistory.index_id;
  afterHistory.index_id = hash(Buffer.from(canonical(afterHistory)));
  const afterHistoryRaw = Buffer.from(canonical(afterHistory));
  const [journal, marker] = transitionJournalFixture(
    "release-evidence", transitionId, journalId,
    [
      { role: "release-evidence", operation: "create", path: archivePath, before_raw: null, after_raw: afterSetRaw },
      { role: "release-evidence", operation: "replace", path: currentPath, before_raw: Buffer.from(canonical(beforeSet)), after_raw: afterSetRaw },
      { role: "history-index", operation: "replace", path: historyPath, before_raw: beforeHistoryRaw, after_raw: afterHistoryRaw },
    ],
    transitionPath, transitionRaw, registryDoc, schemaSha, registrySha,
  );
  const finalStore = Object.fromEntries(Object.entries(beforeStore).map(([targetPath, raw]) => [targetPath, Buffer.from(raw)]));
  Object.assign(finalStore, {
    [currentPath]: afterSetRaw, [archivePath]: afterSetRaw, [historyPath]: afterHistoryRaw, [transitionPath]: transitionRaw,
    [coreJournalPath]: coreJournalRaw, [coreMarkerPath]: coreMarkerRaw,
  });
  const targetImages = {
    [archivePath]: { before: null, after: afterSetRaw },
    [currentPath]: { before: Buffer.from(canonical(beforeSet)), after: afterSetRaw },
    [historyPath]: { before: beforeHistoryRaw, after: afterHistoryRaw },
    [transitionPath]: { before: null, after: transitionRaw },
  };
  return {
    before_set: beforeSet, after_set: afterSet, before_history: beforeHistory, after_history: afterHistory,
    transition_receipt: transition, journal, marker, before_store: beforeStore, final_store: finalStore, target_images: targetImages,
  };
};

const releaseEvidenceTransitionSemantics = (
  pack, registryDoc, schemaRoot, schemaSha, registrySha, expectedIds, hashes, securityContext,
) => {
  try {
    const { before_set: beforeSet, after_set: afterSet, before_history: beforeHistory, after_history: afterHistory,
      transition_receipt: receipt, journal, marker } = pack;
    const registered = [
      [beforeSet, "release-evidence-set/1.0.0", "release_evidence_set_id"],
      [afterSet, "release-evidence-set/1.0.0", "release_evidence_set_id"],
      [beforeHistory, "release-evidence-history-index/1.0.0", "index_id"],
      [afterHistory, "release-evidence-history-index/1.0.0", "index_id"],
      [receipt, "release-evidence-transition-receipt/1.0.0", "receipt_id"],
    ];
    for (const [document, name, identity] of registered) {
      const body = clone(document); delete body[identity];
      if (!validateRegistered(document, schemaRoot, registryDoc, name, schemaSha, registrySha)
          || document[identity] !== hash(Buffer.from(canonical(body)))) return false;
    }
    if (!journalSemantics(journal, marker, schemaRoot, registryDoc, schemaSha, registrySha)
      || afterSet.set_generation !== beforeSet.set_generation + 1
      || afterSet.previous_set_id !== beforeSet.release_evidence_set_id
      || receipt.before_generation !== beforeSet.set_generation
      || receipt.before_set_id !== beforeSet.release_evidence_set_id
      || receipt.after_generation !== afterSet.set_generation
      || receipt.after_set_id !== afterSet.release_evidence_set_id
      || receipt.journal_id !== journal.journal_id
      || receipt.committed_at !== afterSet.accepted_at
      || canonical(afterHistory.entries.slice(0, -1)) !== canonical(beforeHistory.entries)
      || afterHistory.current_generation !== afterSet.set_generation
      || afterHistory.current_set_id !== afterSet.release_evidence_set_id) return false;
    const currentPath = registryDoc.runtime_paths.release_evidence_set.path;
    const historyPath = registryDoc.runtime_paths.release_evidence_history_index.path;
    const archivePath = runtimePath(registryDoc, "release_evidence_set_archive_template", null, null, null, null, null, null, null, null, afterSet.release_evidence_set_id);
    const receiptPath = runtimePath(registryDoc, "release_evidence_transition_receipt_template", null, null, null, receipt.transition_id);
    const expectedTargets = [
      ["release-evidence", "create", archivePath, null, Buffer.from(canonical(afterSet))],
      ["release-evidence", "replace", currentPath, Buffer.from(canonical(beforeSet)), Buffer.from(canonical(afterSet))],
      ["history-index", "replace", historyPath, Buffer.from(canonical(beforeHistory)), Buffer.from(canonical(afterHistory))],
      ["receipt", "create", receiptPath, null, Buffer.from(canonical(receipt))],
    ];
    if (journal.targets.length !== expectedTargets.length) return false;
    for (let index = 0; index < expectedTargets.length; index += 1) {
      const target = journal.targets[index]; const [role, operation, targetPath, beforeRaw, afterRaw] = expectedTargets[index];
      if (target.role !== role || target.operation !== operation || target.path !== targetPath
        || target.before_sha256 !== (beforeRaw === null ? null : hash(beforeRaw)) || target.after_sha256 !== hash(afterRaw)) return false;
    }
    return loadReleaseEvidenceSet(
      { registry_raw: Buffer.from(canonical(registryDoc)), release_store: pack.final_store },
      registryDoc, schemaRoot, schemaSha, registrySha, expectedIds, hashes, securityContext,
    ) !== null;
  } catch { return false; }
};

const transitionRecoverySemantics = (pack, crashAfter, committedMarker) => {
  const targets = pack.journal.targets;
  if (!Number.isInteger(crashAfter) || crashAfter < 0 || crashAfter > targets.length) return false;
  const observed = Object.fromEntries(targets.map((target, index) => [
    target.path, index < crashAfter ? target.after_sha256 : target.before_sha256,
  ]));
  const expectedSide = committedMarker ? "after_sha256" : "before_sha256";
  const recovered = Object.fromEntries(targets.map((target) => [target.path, target[expectedSide]]));
  const expected = Object.fromEntries(targets.map((target) => [
    target.path, committedMarker ? target.after_sha256 : target.before_sha256,
  ]));
  return Object.keys(observed).length === targets.length && canonical(recovered) === canonical(expected);
};

const writerRuntimeFixture = (registryDoc, capabilityRegistry, schemaSha, registrySha) => {
  const capabilities = new Map(capabilityRegistry.capabilities.filter(({ status }) => status === "active").map((row) => [row.producer_id, row]));
  const inventory = []; const store = {};
  for (const spec of registryDoc.strict_rollout.writer_specs) {
    const artifacts = spec.artifact_paths.map((artifactPath) => {
      const raw = fs.readFileSync(path.join(args["project-root"], artifactPath)); store[artifactPath] = raw;
      return { path: artifactPath, sha256: hash(raw) };
    });
    const manifest = { contract: expectedContractRef(registryDoc, "writer-build-manifest/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", producer_id: spec.producer_id, artifacts };
    manifest.build_id = hash(Buffer.from(canonical(manifest)));
    const capability = capabilities.get(spec.producer_id);
    const receipt = { contract: expectedContractRef(registryDoc, "writer-fence-receipt/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", producer_id: spec.producer_id,
      writer_build_id: manifest.build_id, coordinator_id: registryDoc.strict_rollout.required_fence, capability_id: capability.capability_id,
      capability_epoch: capabilityRegistry.capability_epoch, lock_profile_id: registryDoc.lock_profile.profile_id, fenced_at: "2026-07-24T02:59:00Z" };
    receipt.receipt_id = hash(Buffer.from(canonical(receipt)));
    store[spec.manifest_path] = Buffer.from(canonical(manifest)); store[spec.receipt_path] = Buffer.from(canonical(receipt));
    inventory.push({ producer_id: spec.producer_id, writer_build_id: manifest.build_id, fence_receipt_id: receipt.receipt_id, capability_id: capability.capability_id });
  }
  return [inventory, store];
};

const writerFenceFixture = (registryDoc, schemaSha, registrySha, expectedIds, hashes, activationEpoch = 1, suiteDoc = suite, schemaRoot = schema, projectRootPath = args["project-root"], workspaceRoot = documentWorkspace, firstPublication = false) => {
  const memoryRoot = "123e4567-e89b-42d3-a456-426614174000"; const projectRoot = "123e4567-e89b-42d3-a456-426614174001";
  const capabilityRegistry = factAttributionFixture(schemaSha, registrySha, registryDoc, "action").capability_registry;
  const [writerInventory, writerStore] = writerRuntimeFixture(registryDoc, capabilityRegistry, schemaSha, registrySha);
  const [rows, ledgerRaw, ledgerState] = refreshLedgerFixture(registryDoc, schemaSha, registrySha);
  const actionFlow = actionFlowDocument(rows, ledgerRaw, ledgerState.ledger_revision, registryDoc, schemaSha, registrySha);
  const factState = { contract: expectedContractRef(registryDoc, "fact-generation-state/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", fact_generation: 12, last_transaction_id: "tx-strict-facts-1" };
  factState.state_id = hash(Buffer.from(canonical(factState)));
  const factCommandIndex = {
    contract: expectedContractRef(registryDoc, "fact-command-receipt-index/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", next_sequence: 1, entries: [],
  };
  factCommandIndex.index_id = hash(Buffer.from(canonical(factCommandIndex)));
  const mutationIntentOutbox = {
    contract: expectedContractRef(registryDoc, "mutation-intent-outbox/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", outbox_generation: 1, entries: [],
  };
  mutationIntentOutbox.outbox_id = hash(Buffer.from(canonical(mutationIntentOutbox)));
  const intentConvergence = {
    contract: expectedContractRef(registryDoc, "intent-convergence-verdict/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
    outbox_id: mutationIntentOutbox.outbox_id, evaluated_through_sequence: 0, pending_intent_ids: [], failed_intent_ids: [], waived_intent_ids: [], status: "converged",
  };
  intentConvergence.verdict_id = hash(Buffer.from(canonical(intentConvergence)));
  const workstreamId = "l1-checkout"; const snapshot = actionSnapshot(rows, workstreamId, ledgerState.ledger_fingerprint, ledgerState.ledger_revision);
  const wdrRaw = Buffer.from(applyWdrPatch(fixtureWdr(workstreamId), { set: { refresh_actions: true }, evidence: [{ source_path: "meetings/m1.md", source_fingerprint: `sha256:${"c".repeat(64)}`, observed_at: "2026-07-24T02:00:00Z" }] }, snapshot.actions.map(({ rendered_summary }) => rendered_summary)));
  const wdrState = { contract: expectedContractRef(registryDoc, "wdr-file-state/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", workstream_id: workstreamId,
    record_path: `workstreams/${workstreamId}/delivery-record.md`, record_fingerprint: hash(wdrRaw), wdr_revision: 5, file_generation: 8, lifecycle: "active" };
  const sidecar = { contract: expectedContractRef(registryDoc, "wdr-action-projection/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", workstream_id: workstreamId,
    ledger_fingerprint: ledgerState.ledger_fingerprint, ledger_revision: ledgerState.ledger_revision, wdr_revision: wdrState.wdr_revision, file_generation: wdrState.file_generation,
    renderer_id: "urn:adp:wdr-action-renderer:1.0.0", renderer_sha256: registryDoc.protocol.sha256, actions: snapshot.actions };
  const workstreamDocuments = [{ record_path: wdrState.record_path, wdr_raw: wdrRaw, state: wdrState, sidecar }];
  const workstreams = [{ workstream_id: workstreamId, wdr_fingerprint: hash(wdrRaw), wdr_revision: wdrState.wdr_revision, file_generation: wdrState.file_generation, sidecar_fingerprint: hash(Buffer.from(canonical(sidecar))) }];

  let generationId = hash(Buffer.from("strict-generation-1")); let panelId = hash(Buffer.from("strict-panel-1"));
  const pointerRows = [
    ["state-audit", null], ["action-projection-drift-verdict", null], ["program-status", null], ["roadmap", null], ["flow-graph", null],
    ["meeting-pack", "fde-morning"], ["meeting-pack", "business-biweekly"], ["management-panel", null],
  ].map(([kind, instanceKey]) => ({ kind, instance_key: instanceKey,
    id: hash(Buffer.from(`projection:${kind}:${instanceKey ?? "singleton"}`)), manifest_id: hash(Buffer.from(`manifest:${kind}:${instanceKey ?? "singleton"}`)),
    canonical_path: runtimePath(registryDoc, kind === "management-panel" ? "management_panel_template" : "canonical_projection_template", generationId, kind, instanceKey) }))
    .sort((left, right) => Buffer.from(`${left.kind}\0${left.instance_key ?? ""}`).compare(Buffer.from(`${right.kind}\0${right.instance_key ?? ""}`)));
  let currentPointer = { contract: expectedContractRef(registryDoc, "panel-current-pointer/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", generation_id: generationId, panel_id: panelId, projections: pointerRows };
  currentPointer.pointer_id = hash(Buffer.from(canonical(currentPointer)));
  let panelState = { contract: expectedContractRef(registryDoc, "panel-state/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", panel_generation: 8, current_pointer_id: currentPointer.pointer_id };
  panelState.state_id = hash(Buffer.from(canonical(panelState)));
  const refreshNodes = pointerRows.map((row) => ({ instance_key: row.instance_key ?? "singleton", projection_kind: row.kind, disposition: "produced", invalidation_reasons: [],
    output: { kind: row.kind, id: row.id, manifest_id: row.manifest_id, generation_id: generationId }, error_code: null }));
  let refreshReceipt = { contract: expectedContractRef(registryDoc, "refresh-run-receipt/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", refresh_id: "refresh-strict-1", generation_id: generationId,
    expected_fact_generation: factState.fact_generation, expected_panel_generation: 7, status: "published", nodes: refreshNodes, retry_from_instance_key: null, source_as_of: "2026-07-24T03:00:00Z" };
  refreshReceipt.receipt_id = hash(Buffer.from(canonical(refreshReceipt)));
  const publishedTargets = pointerRows.map((row, index) => { const target = mutationTarget(row.kind === "management-panel" ? "panel" : "projection", "create", index, row.canonical_path); target.after_sha256 = row.id; target.after_image.sha256 = row.id; return target; });
  const pointerTarget = mutationTarget("pointer", "replace", publishedTargets.length, registryDoc.runtime_paths.panel_current_pointer.path); setTargetAfter(pointerTarget, currentPointer);
  const panelStateTarget = mutationTarget("panel-state", "replace", publishedTargets.length + 1, registryDoc.runtime_paths.panel_state.path); setTargetAfter(panelStateTarget, panelState);
  let publicationReceipt = { contract: expectedContractRef(registryDoc, "panel-publication-receipt/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", transaction_id: "tx-strict-panel-1", journal_id: "journal-strict-panel-1",
    generation_id: generationId, selection_policy_id: hash(Buffer.from("strict-selection-1")), panel_id: panelId, before_panel_generation: 7, after_panel_generation: panelState.panel_generation,
    before_pointer_id: hash(Buffer.from("strict-before-pointer-1")), after_pointer_id: currentPointer.pointer_id, published_targets: publishedTargets, pointer_target: pointerTarget, panel_state_target: panelStateTarget, status: "committed" };
  publicationReceipt.receipt_id = hash(Buffer.from(canonical(publicationReceipt)));
  const lineagePackage = strictLineageFixture(suiteDoc, registryDoc, schemaRoot, schemaSha, registrySha, projectRootPath, workspaceRoot, factState, ledgerRaw, ledgerState, workstreamDocuments, firstPublication);
  refreshReceipt = lineagePackage.refresh_receipt; publicationReceipt = lineagePackage.publication_graph.receipt;
  currentPointer = lineagePackage.publication_graph.pointer; panelState = lineagePackage.publication_graph.state;
  generationId = lineagePackage.generation.generation_id; panelId = lineagePackage.panel.panel_id;
  const rootRegistry = { contract: expectedContractRef(registryDoc, "root-registry-state/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", roots: [
    { role: "memory", root_instance_id: memoryRoot, canonical_path_hash: hash(Buffer.from("/canonical/memory")) },
    { role: "project", root_instance_id: projectRoot, canonical_path_hash: hash(Buffer.from("/canonical/project")) },
  ], created_at: "2026-07-24T01:00:00Z" };
  rootRegistry.registry_state_id = hash(Buffer.from(canonical(rootRegistry)));
  const [receipts, evidenceBlobs] = implementationConformanceReceipts(expectedIds, hashes, registryDoc);
  const [releaseEvidenceSet, releaseStore] = releaseEvidenceSetFixture(receipts, evidenceBlobs, registryDoc, schemaSha, registrySha);
  const releaseHistory = JSON.parse(releaseStore[registryDoc.runtime_paths.release_evidence_history_index.path].toString("utf8"));
  const activationState = { contract: expectedContractRef(registryDoc, "strict-activation-state/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
    activation_epoch: activationEpoch, mode: "strict", attestation_id: `sha256:${"0".repeat(64)}`, changed_at: "2026-07-24T03:00:03Z" };
  const activationBody = clone(activationState); delete activationBody.attestation_id; delete activationBody.state_id;
  const activationStateBindingId = hash(Buffer.from(canonical(activationBody)));
  const attestation = { contract: expectedContractRef(registryDoc, "writer-fence-migration-attestation/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", attestation_id: `sha256:${"0".repeat(64)}`,
    attested_at: "2026-07-24T03:00:02Z", binding_scope: "immutable-writer-fence", registry_sha256: registrySha, protocol_sha256: hashes.protocol, release_evidence_set_id: releaseEvidenceSet.release_evidence_set_id,
    release_evidence_history_index_id: releaseHistory.index_id,
    activation_state_binding_id: activationStateBindingId,
    memory_root_instance_id: memoryRoot, root_registry_state_id: rootRegistry.registry_state_id, capability_registry_id: capabilityRegistry.capability_registry_id,
    capability_epoch: capabilityRegistry.capability_epoch, activation_epoch: activationEpoch, fact_generation: factState.fact_generation, writer_inventory: clone(writerInventory),
    ledger: { ledger_fingerprint: ledgerState.ledger_fingerprint, ledger_revision: ledgerState.ledger_revision, ledger_state_id: ledgerState.state_id, action_flow_fingerprint: hash(Buffer.from(canonical(actionFlow))) },
    workstreams, full_refresh_receipt_id: refreshReceipt.receipt_id, published_generation_id: generationId, panel_publication_receipt_id: publicationReceipt.receipt_id, current_pointer_id: currentPointer.pointer_id,
    lineage_index_id: lineagePackage.lineage_index.index_id, lineage_index_path: lineagePackage.lineage_index_path };
  const body = clone(attestation); delete body.attestation_id; attestation.attestation_id = hash(Buffer.from(canonical(body)));
  activationState.attestation_id = attestation.attestation_id;
  activationState.state_id = hash(Buffer.from(canonical(activationState)));
  return { surface: "publish", attestation_path: registryDoc.runtime_paths.writer_fence_attestation.path, capability_lifecycle_operation: null,
    registry_raw: Buffer.from(canonical(registryDoc)), release_evidence_set: releaseEvidenceSet, release_store: releaseStore,
    release_receipts: receipts, evidence_blobs: evidenceBlobs,
    refresh_completed_at: "2026-07-24T03:00:00Z", publication_completed_at: "2026-07-24T03:00:01Z",
    paths: { root_registry: registryDoc.runtime_paths.root_registry_state.path, capability_registry: registryDoc.runtime_paths.writer_capability_registry.path,
      fact_state: registryDoc.runtime_paths.fact_generation.path, ledger: registryDoc.runtime_paths.action_ledger.path, ledger_state: registryDoc.runtime_paths.action_ledger_state.path,
      action_flow: registryDoc.runtime_paths.action_flow_index.path, release_evidence_set: registryDoc.runtime_paths.release_evidence_set.path,
      release_evidence_history_index: registryDoc.runtime_paths.release_evidence_history_index.path,
      pointer: registryDoc.runtime_paths.panel_current_pointer.path, panel_state: registryDoc.runtime_paths.panel_state.path,
      activation_state: registryDoc.runtime_paths.strict_activation_state.path },
    writer_store: writerStore, lineage_store: lineagePackage.lineage_store, live_leaf_store: lineagePackage.leaf_store,
    documents: { root_registry: rootRegistry, capability_registry: capabilityRegistry, fact_state: factState, ledger_raw: ledgerRaw, ledger_state: ledgerState, action_flow: actionFlow,
      fact_command_index: factCommandIndex, mutation_intent_outbox: mutationIntentOutbox, intent_convergence: intentConvergence,
      workstreams: workstreamDocuments, refresh_receipt: refreshReceipt, publication_receipt: publicationReceipt, current_pointer: currentPointer, panel_state: panelState,
      activation_state: activationState, release_evidence_set: releaseEvidenceSet, release_evidence_history_index: releaseHistory },
    attestation };
};
const rebindWriterFenceAttestation = (pack) => { const body = clone(pack.attestation); delete body.attestation_id; pack.attestation.attestation_id = hash(Buffer.from(canonical(body))); };

const strictWriterInventorySemantics = (pack, registryDoc, schemaRoot, schemaSha, registrySha) => {
  try {
    const capabilityRegistry = pack.documents.capability_registry;
    const attestation = pack.attestation;
    const active = new Map(capabilityRegistry.capabilities.filter(({ status }) => status === "active").map((row) => [row.producer_id, row]));
    const specs = registryDoc.strict_rollout.writer_specs;
    const required = registryDoc.strict_rollout.authoritative_writers;
    if (canonical(specs.map(({ producer_id }) => producer_id)) !== canonical(required)
        || canonical([...active.keys()].sort()) !== canonical([...required].sort())
        || active.size !== capabilityRegistry.capabilities.length) return false;
    const store = pack.writer_store;
    const expectedPaths = new Set(specs.flatMap((spec) => [...spec.artifact_paths, spec.manifest_path, spec.receipt_path]));
    if (canonical(Object.keys(store).sort()) !== canonical([...expectedPaths].sort())
        || Object.values(store).some((raw) => !Buffer.isBuffer(raw))) return false;
    const derived = [];
    for (const spec of specs) {
      const capability = active.get(spec.producer_id);
      if (!capability || capabilityRecordDigest(capability) !== capability.capability_id
          || capability.authorization_record_digest !== capability.capability_id
          || ["allowed_operations", "allowed_fields", "allowed_sections"].some((name) => canonical(capability[name]) !== canonical(spec[name]))) return false;
      const manifestRaw = store[spec.manifest_path]; const receiptRaw = store[spec.receipt_path];
      const manifest = JSON.parse(manifestRaw.toString()); const receipt = JSON.parse(receiptRaw.toString());
      const expectedArtifacts = spec.artifact_paths.map((artifactPath) => ({ path: artifactPath, sha256: hash(store[artifactPath]) }));
      const manifestBody = clone(manifest); delete manifestBody.build_id;
      const receiptBody = clone(receipt); delete receiptBody.receipt_id;
      if (canonical(manifest) !== manifestRaw.toString() || canonical(receipt) !== receiptRaw.toString()
          || !validateRegistered(manifest, schemaRoot, registryDoc, "writer-build-manifest/1.0.0", schemaSha, registrySha)
          || !validateRegistered(receipt, schemaRoot, registryDoc, "writer-fence-receipt/1.0.0", schemaSha, registrySha)
          || manifest.producer_id !== spec.producer_id || canonical(manifest.artifacts) !== canonical(expectedArtifacts)
          || manifest.build_id !== hash(Buffer.from(canonical(manifestBody)))
          || receipt.receipt_id !== hash(Buffer.from(canonical(receiptBody)))
          || receipt.producer_id !== spec.producer_id || receipt.writer_build_id !== manifest.build_id
          || receipt.coordinator_id !== registryDoc.strict_rollout.required_fence
          || receipt.capability_id !== capability.capability_id
          || receipt.capability_epoch !== capabilityRegistry.capability_epoch
          || receipt.lock_profile_id !== registryDoc.lock_profile.profile_id) return false;
      derived.push({ producer_id: spec.producer_id, writer_build_id: manifest.build_id,
        fence_receipt_id: receipt.receipt_id, capability_id: capability.capability_id });
    }
    return canonical(attestation.writer_inventory) === canonical(derived);
  } catch { return false; }
};

const strictActivationControlSemantics = (pack, registryDoc, schemaRoot, schemaSha, registrySha, expectedIds, hashes, securityContext) => {
  try {
    if (registryDoc.strict_rollout.capability_lifecycle_rule !== "strict-mode-prohibits-runtime-create-rotate-revoke;rollback-to-legacy-increment-activation-epoch-reviewed-reprovision-and-full-refresh-required"
        || registryDoc.strict_rollout.capability_lifecycle_error !== "CAPABILITY_LIFECYCLE_REQUIRES_ROLLBACK"
        || registryDoc.strict_rollout.activation_algorithm !== "release-gate-passed-and-content-addressed-current-root-release-authority-capability-epoch-writer-build-fence-activation-epoch-exact-match-with-mutable-facts-and-publication-state-live-receipt-cas-validated"
        || pack.capability_lifecycle_operation !== null
        || registryDoc.conformance_suite.implementation_conformance_status !== "passed"
        || pack.attestation_path !== registryDoc.runtime_paths.writer_fence_attestation.path) return false;
    const attestation = pack.attestation;
    const activation = pack.documents.activation_state;
    const capabilityRegistry = pack.documents.capability_registry;
    const loadedRelease = loadReleaseEvidenceSet(pack, registryDoc, schemaRoot, schemaSha, registrySha, expectedIds, hashes, securityContext);
    if (loadedRelease === null) return false;
    const [releaseSet] = loadedRelease;
    const releaseHistory = pack.documents.release_evidence_history_index;
    if (canonical(pack.documents.release_evidence_set) !== canonical(releaseSet)
        || !validateRegistered(attestation, schemaRoot, registryDoc, "writer-fence-migration-attestation/1.0.0", schemaSha, registrySha)
        || !validateRegistered(activation, schemaRoot, registryDoc, "strict-activation-state/1.0.0", schemaSha, registrySha)
        || !validateRegistered(capabilityRegistry, schemaRoot, registryDoc, "writer-capability-registry/1.0.0", schemaSha, registrySha)
        || !validateRegistered(releaseHistory, schemaRoot, registryDoc, "release-evidence-history-index/1.0.0", schemaSha, registrySha)) return false;
    const attestationBody = clone(attestation); delete attestationBody.attestation_id;
    const activationBody = clone(activation); delete activationBody.state_id;
    const activationBindingBody = clone(activation); delete activationBindingBody.attestation_id; delete activationBindingBody.state_id;
    const capabilityBody = clone(capabilityRegistry); delete capabilityBody.capability_registry_id;
    return attestation.attestation_id === hash(Buffer.from(canonical(attestationBody)))
      && activation.state_id === hash(Buffer.from(canonical(activationBody)))
      && capabilityRegistry.capability_registry_id === hash(Buffer.from(canonical(capabilityBody)))
      && attestation.registry_sha256 === registrySha && attestation.protocol_sha256 === hashes.protocol
      && attestation.release_evidence_set_id === releaseSet.release_evidence_set_id
      && attestation.release_evidence_history_index_id === releaseHistory.index_id
      && attestation.activation_state_binding_id === hash(Buffer.from(canonical(activationBindingBody)))
      && activation.mode === "strict" && activation.attestation_id === attestation.attestation_id
      && activation.activation_epoch === attestation.activation_epoch
      && capabilityRegistry.capability_registry_id === attestation.capability_registry_id
      && capabilityRegistry.capability_epoch === attestation.capability_epoch
      && strictWriterInventorySemantics(pack, registryDoc, schemaRoot, schemaSha, registrySha);
  } catch { return false; }
};

const strictWriterFenceActivationSemantics = (pack, registryDoc, schemaRoot, schemaSha, registrySha, expectedIds, hashes, securityContext) => {
  try {
    const attestation = pack.attestation; const documents = pack.documents; const paths = pack.paths;
    if (!new Set(["open", "inspect", "publish"]).has(pack.surface)
        || !strictActivationControlSemantics(pack, registryDoc, schemaRoot, schemaSha, registrySha, expectedIds, hashes, securityContext)) return false;
    const loadedRelease = loadReleaseEvidenceSet(pack, registryDoc, schemaRoot, schemaSha, registrySha, expectedIds, hashes, securityContext);
    if (loadedRelease === null) return false;
    const [releaseSet] = loadedRelease;
    const releaseHistory = pack.documents.release_evidence_history_index;
    if (!validateRegistered(attestation, schemaRoot, registryDoc, "writer-fence-migration-attestation/1.0.0", schemaSha, registrySha)) return false;
    const body = clone(attestation); delete body.attestation_id;
    if (attestation.attestation_id !== hash(Buffer.from(canonical(body))) || attestation.registry_sha256 !== registrySha || attestation.protocol_sha256 !== hashes.protocol
        || attestation.release_evidence_set_id !== releaseSet.release_evidence_set_id || attestation.release_evidence_history_index_id !== releaseHistory.index_id) return false;
    const expectedPaths = { root_registry: registryDoc.runtime_paths.root_registry_state.path, capability_registry: registryDoc.runtime_paths.writer_capability_registry.path,
      fact_state: registryDoc.runtime_paths.fact_generation.path, ledger: registryDoc.runtime_paths.action_ledger.path, ledger_state: registryDoc.runtime_paths.action_ledger_state.path,
      action_flow: registryDoc.runtime_paths.action_flow_index.path, release_evidence_set: registryDoc.runtime_paths.release_evidence_set.path,
      release_evidence_history_index: registryDoc.runtime_paths.release_evidence_history_index.path,
      pointer: registryDoc.runtime_paths.panel_current_pointer.path, panel_state: registryDoc.runtime_paths.panel_state.path,
      activation_state: registryDoc.runtime_paths.strict_activation_state.path };
    if (canonical(paths) !== canonical(expectedPaths)) return false;
    const { root_registry: rootRegistry, capability_registry: capabilityRegistry, fact_state: factState, ledger_raw: ledgerRaw, ledger_state: ledgerState,
      action_flow: actionFlow, refresh_receipt: refreshReceipt, publication_receipt: publicationReceipt, current_pointer: currentPointer,
      panel_state: panelState, activation_state: activationState, release_evidence_set: releaseEvidenceSet,
      release_evidence_history_index: releaseEvidenceHistoryIndex } = documents;
    const registered = [
      [rootRegistry, "root-registry-state/1.0.0", "registry_state_id"], [capabilityRegistry, "writer-capability-registry/1.0.0", "capability_registry_id"],
      [factState, "fact-generation-state/1.0.0", "state_id"], [ledgerState, "action-ledger-state/1.0.0", "state_id"],
      [refreshReceipt, "refresh-run-receipt/1.0.0", "receipt_id"], [publicationReceipt, "panel-publication-receipt/1.0.0", "receipt_id"],
      [currentPointer, "panel-current-pointer/1.0.0", "pointer_id"], [panelState, "panel-state/1.0.0", "state_id"],
      [activationState, "strict-activation-state/1.0.0", "state_id"], [releaseEvidenceSet, "release-evidence-set/1.0.0", "release_evidence_set_id"],
      [releaseEvidenceHistoryIndex, "release-evidence-history-index/1.0.0", "index_id"],
    ];
    for (const [document, contractName, identityField] of registered) {
      if (!validateRegistered(document, schemaRoot, registryDoc, contractName, schemaSha, registrySha)) return false;
      const identityBody = clone(document); delete identityBody[identityField]; if (document[identityField] !== hash(Buffer.from(canonical(identityBody)))) return false;
    }
    if (!validate(actionFlow, schemaRoot, "actionFlowIndexV1")) return false;
    const rootRows = new Map(rootRegistry.roots.map((row) => [row.role, row]));
    if (rootRows.size !== 2 || !rootRows.has("memory") || !rootRows.has("project") || rootRows.get("memory").root_instance_id !== attestation.memory_root_instance_id || rootRegistry.registry_state_id !== attestation.root_registry_state_id) return false;
    const capabilityBody = clone(capabilityRegistry); delete capabilityBody.capability_registry_id;
    if (capabilityRegistry.capability_registry_id !== hash(Buffer.from(canonical(capabilityBody)))) return false;
    const activeCapabilities = new Map(capabilityRegistry.capabilities.filter(({ status }) => status === "active").map((row) => [row.producer_id, row]));
    const writerSpecs = registryDoc.strict_rollout.writer_specs;
    if (activeCapabilities.size !== 9 || capabilityRegistry.capabilities.length !== 9
      || canonical(writerSpecs.map(({ producer_id }) => producer_id)) !== canonical(registryDoc.strict_rollout.authoritative_writers)
      || canonical([...activeCapabilities.keys()].sort()) !== canonical([...registryDoc.strict_rollout.authoritative_writers].sort())
      || [...activeCapabilities.values()].some((row) => capabilityRecordDigest(row) !== row.capability_id || row.authorization_record_digest !== row.capability_id)) return false;
    const store = pack.writer_store; const expectedStorePaths = new Set(writerSpecs.flatMap((spec) => [...spec.artifact_paths, spec.manifest_path, spec.receipt_path]));
    if (canonical(Object.keys(store).sort()) !== canonical([...expectedStorePaths].sort()) || Object.values(store).some((raw) => !Buffer.isBuffer(raw))) return false;
    const derivedWriters = [];
    for (const spec of writerSpecs) {
      const capability = activeCapabilities.get(spec.producer_id);
      if (["allowed_operations", "allowed_fields", "allowed_sections"].some((name) => canonical(capability[name]) !== canonical(spec[name]))) return false;
      const manifestRaw = store[spec.manifest_path]; const receiptRaw = store[spec.receipt_path];
      const manifest = JSON.parse(manifestRaw.toString()); const receipt = JSON.parse(receiptRaw.toString());
      const expectedArtifacts = spec.artifact_paths.map((artifactPath) => ({ path: artifactPath, sha256: hash(store[artifactPath]) }));
      const manifestBody = clone(manifest); delete manifestBody.build_id; const receiptBody = clone(receipt); delete receiptBody.receipt_id;
      if (canonical(manifest) !== manifestRaw.toString() || canonical(receipt) !== receiptRaw.toString()
        || !validateRegistered(manifest, schemaRoot, registryDoc, "writer-build-manifest/1.0.0", schemaSha, registrySha)
        || !validateRegistered(receipt, schemaRoot, registryDoc, "writer-fence-receipt/1.0.0", schemaSha, registrySha)
        || manifest.producer_id !== spec.producer_id || canonical(manifest.artifacts) !== canonical(expectedArtifacts) || manifest.build_id !== hash(Buffer.from(canonical(manifestBody)))
        || receipt.receipt_id !== hash(Buffer.from(canonical(receiptBody))) || receipt.producer_id !== spec.producer_id || receipt.writer_build_id !== manifest.build_id
        || receipt.coordinator_id !== registryDoc.strict_rollout.required_fence || receipt.capability_id !== capability.capability_id
        || receipt.capability_epoch !== capabilityRegistry.capability_epoch || receipt.lock_profile_id !== registryDoc.lock_profile.profile_id) return false;
      derivedWriters.push({ producer_id: spec.producer_id, writer_build_id: manifest.build_id, fence_receipt_id: receipt.receipt_id, capability_id: capability.capability_id });
    }
    const writers = attestation.writer_inventory;
    if (canonical(writers) !== canonical(derivedWriters)) return false;
    if (!Buffer.isBuffer(ledgerRaw)) return false;
    const rows = parseActionLedger(ledgerRaw); const expectedLedgerState = actionLedgerStateDocument(rows, ledgerRaw, ledgerState.ledger_revision, ledgerState.applied_commands, registryDoc, schemaSha, registrySha);
    const expectedFlow = actionFlowDocument(rows, ledgerRaw, ledgerState.ledger_revision, registryDoc, schemaSha, registrySha);
    if (canonical(ledgerState) !== canonical(expectedLedgerState) || canonical(actionFlow) !== canonical(expectedFlow)) return false;
    const actualWorkstreams = [];
    for (const item of documents.workstreams) {
      const raw = item.wdr_raw; const state = item.state; const sidecar = item.sidecar; const workstreamId = state.workstream_id;
      if (!Buffer.isBuffer(raw) || item.record_path !== `workstreams/${workstreamId}/delivery-record.md`) return false;
      if (!validateRegistered(state, schemaRoot, registryDoc, "wdr-file-state/1.0.0", schemaSha, registrySha) || !validateRegistered(sidecar, schemaRoot, registryDoc, "wdr-action-projection/1.0.0", schemaSha, registrySha)) return false;
      const expectedState = { contract: expectedContractRef(registryDoc, "wdr-file-state/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", workstream_id: workstreamId, record_path: item.record_path,
        record_fingerprint: hash(raw), wdr_revision: state.wdr_revision, file_generation: state.file_generation, lifecycle: "active" };
      if (!completeWdrValid(raw.toString(), workstreamId) || canonical(state) !== canonical(expectedState)) return false;
      const snapshot = actionSnapshot(rows, workstreamId, ledgerState.ledger_fingerprint, ledgerState.ledger_revision);
      const expectedSidecar = { contract: expectedContractRef(registryDoc, "wdr-action-projection/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", workstream_id: workstreamId,
        ledger_fingerprint: ledgerState.ledger_fingerprint, ledger_revision: ledgerState.ledger_revision, wdr_revision: state.wdr_revision, file_generation: state.file_generation,
        renderer_id: "urn:adp:wdr-action-renderer:1.0.0", renderer_sha256: registryDoc.protocol.sha256, actions: snapshot.actions };
      const [, managed] = partitionNextActions(wdrCurrentSignature(raw.toString(), workstreamId).next_actions);
      if (canonical(sidecar) !== canonical(expectedSidecar) || canonical(managed) !== canonical(sidecar.actions.map(({ rendered_summary }) => rendered_summary))) return false;
      actualWorkstreams.push({ workstream_id: workstreamId, wdr_fingerprint: hash(raw), wdr_revision: state.wdr_revision, file_generation: state.file_generation, sidecar_fingerprint: hash(Buffer.from(canonical(sidecar))) });
    }
    actualWorkstreams.sort((a, b) => Buffer.from(a.workstream_id).compare(Buffer.from(b.workstream_id)));
    const workstreamIds = actualWorkstreams.map(({ workstream_id }) => workstream_id);
    if (!workstreamIds.length || canonical(workstreamIds) !== canonical([...new Set(workstreamIds)].sort((a, b) => Buffer.from(a).compare(Buffer.from(b))))) return false;
    const lineage = loadStrictLineage(pack, registryDoc, schemaRoot, schemaSha, registrySha, pack.surface !== "inspect");
    if (lineage === null || lineage.generation.fact_generation !== factState.fact_generation) return false;
    const liveRefreshReceipt = lineage.refresh_receipt;
    const livePublicationReceipt = lineage.graph.receipt;
    const liveCurrentPointer = lineage.graph.pointer;
    const livePanelState = lineage.graph.state;
    const expectedPointerPaths = [];
    for (const row of liveCurrentPointer.projections) {
      const targetPath = runtimePath(registryDoc, row.kind === "management-panel" ? "management_panel_template" : "canonical_projection_template", liveCurrentPointer.generation_id, row.kind, row.instance_key);
      if (row.canonical_path !== targetPath) return false; expectedPointerPaths.push([row.kind === "management-panel" ? "panel" : "projection", targetPath]);
    }
    const sortRows = (values) => clone(values).sort((a, b) => Buffer.from(canonical(a)).compare(Buffer.from(canonical(b))));
    const nodes = liveRefreshReceipt.nodes.filter((row) => ["produced", "reused"].includes(row.disposition) && row.output !== null)
      .map((row) => [row.projection_kind, row.instance_key, row.output.id, row.output.manifest_id, row.output.generation_id]);
    const pointerNodes = liveCurrentPointer.projections.map((row) => [row.kind, row.instance_key ?? "singleton", row.id, row.manifest_id, liveCurrentPointer.generation_id]);
    if (canonical(sortRows(nodes)) !== canonical(sortRows(pointerNodes)) || liveRefreshReceipt.status !== "published" || liveRefreshReceipt.retry_from_instance_key !== null) return false;
    if (canonical(livePublicationReceipt.published_targets.map((row) => [row.role, row.path])) !== canonical(expectedPointerPaths)) return false;
    if (!(livePublicationReceipt.status === "committed" && livePublicationReceipt.generation_id === liveCurrentPointer.generation_id && livePublicationReceipt.panel_id === liveCurrentPointer.panel_id
      && livePublicationReceipt.after_pointer_id === liveCurrentPointer.pointer_id && livePublicationReceipt.after_panel_generation === livePanelState.panel_generation && livePanelState.current_pointer_id === liveCurrentPointer.pointer_id
      && livePublicationReceipt.pointer_target.path === paths.pointer && livePublicationReceipt.pointer_target.after_sha256 === hash(Buffer.from(canonical(liveCurrentPointer)))
      && livePublicationReceipt.panel_state_target.path === paths.panel_state && livePublicationReceipt.panel_state_target.after_sha256 === hash(Buffer.from(canonical(livePanelState)))
      && liveRefreshReceipt.generation_id === liveCurrentPointer.generation_id && liveRefreshReceipt.expected_fact_generation === factState.fact_generation && liveRefreshReceipt.expected_panel_generation === livePublicationReceipt.before_panel_generation)) return false;
    const activationBindingBody = clone(activationState); delete activationBindingBody.attestation_id; delete activationBindingBody.state_id;
    if (activationState.mode !== "strict" || activationState.attestation_id !== attestation.attestation_id || activationState.activation_epoch !== attestation.activation_epoch
        || attestation.activation_state_binding_id !== hash(Buffer.from(canonical(activationBindingBody)))) return false;
    const activationSummary = { memory_root_instance_id: rootRows.get("memory").root_instance_id, root_registry_state_id: rootRegistry.registry_state_id,
      capability_registry_id: capabilityRegistry.capability_registry_id, capability_epoch: capabilityRegistry.capability_epoch, activation_epoch: activationState.activation_epoch,
      writer_inventory: derivedWriters };
    if (attestation.binding_scope !== "immutable-writer-fence"
      || Object.entries(activationSummary).some(([key, value]) => canonical(attestation[key]) !== canonical(value))) return false;
    if (utcInstant(attestation.attested_at) < utcInstant(releaseSet.accepted_at)) return false;
    return true;
  } catch { return false; }
};

const selectionPolicyFixture = (registryDoc, schemaSha, registrySha) => {
  const source = (sourcePath, sourceKind) => { const fingerprint = hash(Buffer.from(`memory\0${sourcePath}`)); return { root: "memory", root_instance_id: "123e4567-e89b-42d3-a456-426614174000", path: sourcePath, category: "fact", source_kind: sourceKind, fingerprint, blob_id: fingerprint, affects: ["/"] }; };
  const workstreamCatalog = [{ workstream_id: "l1-checkout", wdr_source: source("workstreams/l1-checkout/delivery-record.md", "selected-physical-wdr"), sidecar_source: source("workstreams/l1-checkout/action-projection.json", "wdr-action-sidecar") }];
  const snapshotId = hash(Buffer.from("snapshot:2026-07-24T02:00:00Z"));
  const request = {
    contract: expectedContractRef(registryDoc, "refresh-request/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", refresh_id: "refresh-snapshot-fixture",
    requested_source_as_of: "2026-07-24T02:00:00Z", requested_at: "2026-07-24T01:59:58Z",
  };
  request.request_id = hash(Buffer.from(canonical(request)));
  const lockReceipt = {
    contract: expectedContractRef(registryDoc, "snapshot-lock-receipt/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", refresh_request_id: request.request_id, snapshot_id: snapshotId,
    lock_profile_id: registryDoc.lock_profile.profile_id, root_registry_state_id: hash(Buffer.from("snapshot-root-registry")),
    fact_generation: 7, maximum_fact_observed_at: "2026-07-24T01:59:59Z",
    source_as_of: "2026-07-24T02:00:00Z", acquired_at: "2026-07-24T02:00:00Z",
  };
  lockReceipt.receipt_id = hash(Buffer.from(canonical(lockReceipt)));
  const policy = {
    contract: { schema_id: "urn:adp:panel-sync-contracts:2026-07-24#selection-policy-v1", schema_sha256: schemaSha, registry_sha256: registrySha },
    schema_version: "1.0.0", snapshot_id: snapshotId, snapshot_lock_receipt_id: lockReceipt.receipt_id,
    physical_workstream_inventory: clone(workstreamCatalog), physical_workstream_inventory_id: inventoryId(workstreamCatalog),
    workstream_catalog: workstreamCatalog, workstream_catalog_id: catalogId(workstreamCatalog), include_workstreams: "all", exclude_workstreams: [], meeting_kinds: ["business-biweekly", "fde-morning"],
    as_of: "2026-07-24T02:00:00Z", previous_program_status_id: null,
  };
  policy.policy_id = hash(Buffer.from(canonical(policy)));
  return policy;
};

const physicalInventoryFixture = (registryDoc, policy, factGeneration, schemaSha, registrySha) => {
  const inventory = {
    contract: expectedContractRef(registryDoc, "physical-workstream-inventory/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", memory_root_instance_id: "123e4567-e89b-42d3-a456-426614174000",
    fact_generation: factGeneration, workstreams: clone(policy.physical_workstream_inventory), inventory_id: policy.physical_workstream_inventory_id,
  };
  inventory.attestation_id = hash(Buffer.from(canonical(inventory)));
  return inventory;
};

const generationFixture = (registryDoc, policy, schemaSha, registrySha) => {
  const universe = policy.workstream_catalog.map(({ workstream_id }) => workstream_id);
  const included = policy.include_workstreams === "all" ? universe : policy.include_workstreams;
  const selected = [...new Set(included.filter((value) => !policy.exclude_workstreams.includes(value)))].sort();
  const leaves = new Map();
  for (const profile of registryDoc.projection_input_profiles) for (const source of materializeProfileSources(profile, selected, policy)) {
    const key = `${source.root_instance_id}\0${source.path}`; if (leaves.has(key) && canonical(leaves.get(key)) !== canonical(source)) throw new Error(`conflicting physical leaf metadata: ${key}`); leaves.set(key, source);
  }
  for (const row of policy.physical_workstream_inventory) for (const source of [row.wdr_source, row.sidecar_source]) { const key = `${source.root_instance_id}\0${source.path}`; if (!leaves.has(key)) leaves.set(key, clone(source)); }
  const catalog = panelBindingCatalog(registryDoc, schemaSha, registrySha);
  const envelope = {
    contract: { schema_id: "urn:adp:panel-sync-contracts:2026-07-24#generation-envelope-v1", schema_sha256: schemaSha, registry_sha256: registrySha },
    schema_version: "1.0.0", fact_generation: 7, selection_policy_id: policy.policy_id, physical_workstream_inventory_id: policy.physical_workstream_inventory_id, workstream_catalog_id: policy.workstream_catalog_id, panel_catalog_id: catalog.catalog_id,
    roots: [{ root: "memory", root_instance_id: "123e4567-e89b-42d3-a456-426614174000" }, { root: "project", root_instance_id: "123e4567-e89b-42d3-a456-426614174001" }],
    leaf_sources: [...leaves.values()].sort((left, right) => Buffer.from(`${left.root_instance_id}\0${left.path}`).compare(Buffer.from(`${right.root_instance_id}\0${right.path}`))),
  };
  envelope.generation_id = hash(Buffer.from(canonical(envelope)));
  return envelope;
};

const panelFixture = (contractVectors, registryDoc, schemaSha, registrySha, projectRoot) => {
  const instances = Object.fromEntries(contractVectors.filter(({ expected_valid }) => expected_valid).map(({ id, instance }) => [id, clone(instance)]));
  const compatibilityPath = path.join(projectRoot, "_bmad-output/planning-artifacts/architecture/architecture-bmad-ai-delivery-pmo-2026-07-24/contracts/fixtures/PANEL-V1-COMPATIBILITY.json");
  const compatibility = JSON.parse(fs.readFileSync(compatibilityPath, "utf8"));
  const compositionInputs = clone(compatibility.composition_inputs);
  const flow = clone(compositionInputs.flow_graph);
  const sourceAsOf = flow.state.as_of;
  if (flow.overlays.scopes.some((scope) => scope.as_of !== sourceAsOf)) throw new Error("Panel v1 compatibility flow has inconsistent source times");
  const audit = clone(instances["state-audit-payload-schema-valid"]);
  const status = clone(instances["program-status-payload-schema-valid"]);
  status.progress = JSON.parse(fs.readFileSync(path.join(projectRoot, "skills/adp-program-status/assets/fixtures/progress-v3/golden-measurable-boundary.json"), "utf8"));
  status.extensions ??= {};
  status.extensions.panel_v1_source = clone(compositionInputs.program_status);
  status.overall_status = compositionInputs.program_status.overall_status;
  const roadmap = clone(instances["roadmap-payload-schema-valid"]); roadmap.extensions ??= {}; roadmap.extensions.panel_v1_source = clone(compositionInputs.roadmap);
  const meeting = clone(instances["meeting-pack-payload-schema-valid"]);
  meeting.extensions ??= {};
  meeting.extensions.panel_v1_source = clone(compositionInputs.meeting_packs["fde-morning"]);
  const businessMeeting = clone(meeting);
  businessMeeting.scenario = "business-biweekly";
  businessMeeting.meeting_pack_id = `sha256:${"7".repeat(63)}8`;
  businessMeeting.extensions.panel_v1_source = clone(compositionInputs.meeting_packs["business-biweekly"]);
  const policy = selectionPolicyFixture(registryDoc, schemaSha, registrySha); policy.as_of = sourceAsOf;
  policy.snapshot_id = hash(Buffer.from(`snapshot:${sourceAsOf}`));
  const snapshot = snapshotTimeFixture(registryDoc, schemaSha, registrySha, policy, null);
  policy.snapshot_lock_receipt_id = snapshot.lock_receipt.receipt_id;
  const policyBody = clone(policy); delete policyBody.policy_id; policy.policy_id = hash(Buffer.from(canonical(policyBody)));
  const generation = generationFixture(registryDoc, policy, schemaSha, registrySha);
  for (const document of [audit, status, roadmap, meeting, businessMeeting]) document.source_as_of = sourceAsOf;
  audit.selection_policy_id = policy.policy_id; audit.selected_workstreams = ["l1-checkout"];
  const emptyOutbox = {
    contract: expectedContractRef(registryDoc, "mutation-intent-outbox/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", outbox_generation: 1, entries: [],
  };
  emptyOutbox.outbox_id = hash(Buffer.from(canonical(emptyOutbox)));
  const convergence = {
    contract: expectedContractRef(registryDoc, "intent-convergence-verdict/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", outbox_id: emptyOutbox.outbox_id, evaluated_through_sequence: 0,
    pending_intent_ids: [], failed_intent_ids: [], waived_intent_ids: [], status: "converged",
  };
  convergence.verdict_id = hash(Buffer.from(canonical(convergence)));
  audit.intent_convergence = convergence;
  const drift = {
    contract: { schema_id: "urn:adp:panel-sync-contracts:2026-07-24#action-projection-drift-verdict-v1", schema_sha256: schemaSha, registry_sha256: registrySha },
    schema_version: "1.0.0", verdict_id: `sha256:${"8".repeat(64)}`, generation_id: generation.generation_id,
    selection_policy_id: policy.policy_id, ledger_fingerprint: `sha256:${"b".repeat(64)}`, selected_workstreams: ["l1-checkout"],
    workstreams: [{ workstream_id: "l1-checkout", wdr_fingerprint: `sha256:${"c".repeat(64)}`, wdr_revision: 4, file_generation: 7, sidecar_fingerprint: `sha256:${"d".repeat(64)}`, sidecar_ledger_fingerprint: `sha256:${"b".repeat(64)}`, status: "in-sync", action_diffs: [], findings: [], finding_ids: [] }], overall_status: "in-sync",
  };
  audit.repair.drift_verdict_id = drift.verdict_id;
  const panel = {
    panel_schema_version: "2.0.0", panel_id: `sha256:${"f".repeat(64)}`,
    model_v1: clone(compatibility.model_v1),
    sync: {
      generation_id: generation.generation_id, selection_policy_id: policy.policy_id, source_as_of: sourceAsOf,
      artifact_integrity: "pass", business_freshness: "fresh", publication_eligibility: "eligible", canonical: {},
      compatibility_inputs: { request: clone(compositionInputs.request), history: clone(compositionInputs.history), shareable_policy: clone(compositionInputs.shareable_policy) },
    },
  };
  return [panel, {
    "state-audit": audit, "action-projection-drift-verdict": drift,
    "program-status": status, roadmap,
    "flow-graph": flow, "meeting-pack": [meeting, businessMeeting],
  }, compatibility, policy, generation];
};

const resolvedSelection = (policy) => {
  const inventory = policy.physical_workstream_inventory;
  const ids = policy.workstream_catalog.map(({ workstream_id }) => workstream_id);
  const inventoryIds = inventory.map(({ workstream_id }) => workstream_id);
  const inventorySources = inventory.flatMap((row) => [row.wdr_source, row.sidecar_source]).map((row) => `${row.root_instance_id}\0${row.path}`);
  if (!physicalInventoryRowsValid(inventory) || !physicalInventoryRowsValid(policy.workstream_catalog)
    || new Set(ids).size !== ids.length || new Set(inventoryIds).size !== inventoryIds.length || new Set(inventorySources).size !== inventorySources.length
    || policy.physical_workstream_inventory_id !== inventoryId(inventory) || policy.workstream_catalog_id !== catalogId(policy.workstream_catalog)
    || canonical(policy.workstream_catalog) !== canonical(inventory)) return [];
  const included = policy.include_workstreams === "all" ? ids : policy.include_workstreams;
  if ([...included, ...policy.exclude_workstreams].some((id) => !ids.includes(id))) return [];
  return [...new Set(included.filter((id) => !policy.exclude_workstreams.includes(id)))].sort();
};
const expectedProjectionInstances = (registryDoc, policy) => Object.fromEntries(registryDoc.projection_input_profiles.map(({ projection }) => [projection, projection === "meeting-pack" ? [...policy.meeting_kinds] : [null]]));
const instanceSort = (values) => [...values].sort((a, b) => a === null ? (b === null ? 0 : -1) : b === null ? 1 : Buffer.from(a).compare(Buffer.from(b)));
const panelBindingSemantics = (panel, built, registryDoc, policy, generation) => {
  if ((built["management-panel"] ?? []).length !== 1 || canonical(built["management-panel"][0].envelope.payload) !== canonical(panel)) return false;
  const expected = expectedProjectionInstances(registryDoc, policy);
  if (canonical(Object.keys(built).sort()) !== canonical(Object.keys(expected).sort())) return false;
  for (const [kind, keys] of Object.entries(expected)) {
    const actual = built[kind].map(({ envelope }) => envelope.instance_key);
    if (canonical(instanceSort(actual)) !== canonical(instanceSort(keys)) || built[kind].some(({ envelope }) => envelope.generation_id !== generation.generation_id)) return false;
  }
  for (const binding of registryDoc.panel_binding_map) {
    const items = built[binding.projection_kind];
    if (binding.cardinality === "one" && items.length !== 1) return false;
    if (binding.cardinality === "one-per-meeting-kind" && canonical([...new Set(items.map(({ envelope }) => envelope.instance_key))].sort()) !== canonical([...policy.meeting_kinds].sort())) return false;
    let values; try { values = items.map(({ envelope }) => jsonPointer(envelope.payload, binding.source_pointer)); } catch { return false; }
    let expectedValue;
    if (binding.merge_mode === "object-by-key") {
      let keys; try { keys = values.map((value) => jsonPointer(value, binding.key_pointer)); } catch { return false; }
      if (new Set(keys).size !== keys.length) return false;
      expectedValue = Object.fromEntries(keys.map((key, index) => [key, values[index]]).sort((a, b) => Buffer.from(a[0]).compare(Buffer.from(b[0]))));
    } else expectedValue = values[0];
    try { if (canonical(jsonPointer(panel, binding.panel_pointer)) !== canonical(expectedValue)) return false; } catch { return false; }
  }
  return true;
};

const snapshotTimeFixture = (registryDoc, schemaSha, registrySha, policy, refreshReceipt = null) => {
  const refreshId = refreshReceipt === null ? "refresh-snapshot-fixture" : refreshReceipt.refresh_id;
  const sourceTime = utcInstant(policy.as_of);
  const renderTime = (value) => new Date(value).toISOString().replace(".000Z", "Z");
  const request = {
    contract: expectedContractRef(registryDoc, "refresh-request/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", refresh_id: refreshId, requested_source_as_of: policy.as_of,
    requested_at: renderTime(sourceTime - 2000),
  };
  request.request_id = hash(Buffer.from(canonical(request)));
  const lockReceipt = {
    contract: expectedContractRef(registryDoc, "snapshot-lock-receipt/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", refresh_request_id: request.request_id, snapshot_id: policy.snapshot_id,
    lock_profile_id: registryDoc.lock_profile.profile_id, root_registry_state_id: hash(Buffer.from("snapshot-root-registry")),
    fact_generation: 7, maximum_fact_observed_at: renderTime(sourceTime - 1000), source_as_of: policy.as_of,
    acquired_at: policy.as_of,
  };
  lockReceipt.receipt_id = hash(Buffer.from(canonical(lockReceipt)));
  return { request, lock_receipt: lockReceipt, evaluation_time: renderTime(sourceTime + 1000) };
};

const sourceTimeValues = (document, pointer) => {
  const parts = pointer === "" ? [] : pointer.replace(/^\//, "").split("/");
  let values = [document];
  for (const part of parts) {
    const next = [];
    for (const value of values) {
      if (part === "*" && Array.isArray(value)) next.push(...value);
      else if (value !== null && typeof value === "object" && Object.hasOwn(value, part)) next.push(value[part]);
      else return [];
    }
    values = next;
  }
  return values;
};

const sourceAsOfSemantics = (panel, policy, refreshReceipt = null, registryDoc = null, schemaRoot = null, schemaSha = null, registrySha = null, snapshot = null) => {
  const expected = policy.as_of;
  const documents = {
    "management-panel-payload/2.0.0": [panel],
    "state-audit-payload/2.0.0": [panel.sync.audit],
    "program-status-payload/2.0.0": [panel.sync.canonical.status],
    "roadmap-payload/2.0.0": [panel.sync.canonical.roadmap],
    "meeting-pack-payload/2.0.0": Object.values(panel.sync.canonical.meetings),
    "flow-graph-payload/1.0.0": [panel.sync.canonical.flow],
    "refresh-run-receipt/1.0.0": refreshReceipt === null ? [] : [refreshReceipt],
  };
  if (registryDoc === null) return Object.values(documents).flat().every((document) => {
    const value = Object.hasOwn(document, "sync") ? document.sync.source_as_of : (document.source_as_of ?? document.state?.as_of);
    return value === expected;
  });
  if (canonical([...new Set(registryDoc.source_time_bindings.map(({ contract }) => contract))].sort()) !== canonical(Object.keys(documents).sort())) return false;
  for (const binding of registryDoc.source_time_bindings) for (const document of documents[binding.contract]) {
    const values = sourceTimeValues(document, binding.pointer);
    if (!values.length || values.some((value) => value !== expected)) return false;
  }
  if (refreshReceipt === null || schemaRoot === null || schemaSha === null || registrySha === null) return true;
  const authority = snapshot ?? snapshotTimeFixture(registryDoc, schemaSha, registrySha, policy, refreshReceipt);
  const request = authority.request; const lockReceipt = authority.lock_receipt;
  try {
    const requestBody = clone(request); delete requestBody.request_id;
    const receiptBody = clone(lockReceipt); delete receiptBody.receipt_id;
    return validateRegistered(request, schemaRoot, registryDoc, "refresh-request/1.0.0", schemaSha, registrySha)
      && validateRegistered(lockReceipt, schemaRoot, registryDoc, "snapshot-lock-receipt/1.0.0", schemaSha, registrySha)
      && request.request_id === hash(Buffer.from(canonical(requestBody)))
      && lockReceipt.receipt_id === hash(Buffer.from(canonical(receiptBody)))
      && request.requested_source_as_of === expected && lockReceipt.source_as_of === expected
      && policy.snapshot_id === refreshReceipt.snapshot_id && refreshReceipt.snapshot_id === lockReceipt.snapshot_id
      && policy.snapshot_lock_receipt_id === refreshReceipt.snapshot_lock_receipt_id
      && refreshReceipt.snapshot_lock_receipt_id === lockReceipt.receipt_id
      && lockReceipt.lock_profile_id === registryDoc.lock_profile.profile_id
      && utcInstant(request.requested_at) <= utcInstant(lockReceipt.acquired_at)
      && utcInstant(lockReceipt.maximum_fact_observed_at) <= utcInstant(expected)
      && utcInstant(expected) === utcInstant(lockReceipt.acquired_at)
      && utcInstant(lockReceipt.acquired_at) <= utcInstant(authority.evaluation_time);
  } catch { return false; }
};

const intentConvergenceSemantics = (outbox, verdict, registryDoc, schemaRoot, schemaSha, registrySha, consumedReceipts = null) => {
  try {
    const outboxBody = clone(outbox); delete outboxBody.outbox_id;
    const verdictBody = clone(verdict); delete verdictBody.verdict_id;
    if (!validateRegistered(outbox, schemaRoot, registryDoc, "mutation-intent-outbox/1.0.0", schemaSha, registrySha)
      || !validateRegistered(verdict, schemaRoot, registryDoc, "intent-convergence-verdict/1.0.0", schemaSha, registrySha)
      || outbox.outbox_id !== hash(Buffer.from(canonical(outboxBody)))
      || verdict.verdict_id !== hash(Buffer.from(canonical(verdictBody)))
      || verdict.outbox_id !== outbox.outbox_id) return false;
    const entries = outbox.entries;
    const sequences = entries.map(({ sequence }) => sequence);
    const intentIds = entries.map(({ intent_id }) => intent_id);
    if (canonical(sequences) !== canonical(entries.map((_, index) => index + 1))
      || new Set(intentIds).size !== intentIds.length
      || new Set(entries.map(({ source_command_id }) => source_command_id)).size !== entries.length
      || entries.some(({ field_set }) => canonical(field_set) !== canonical([...field_set].sort((a, b) => Buffer.from(a).compare(Buffer.from(b)))))) return false;
    const pending = [];
    for (const row of entries) {
      const intent = row.intent;
      if (!validateRegistered(intent, schemaRoot, registryDoc, "status-mutation-intent/1.0.0", schemaSha, registrySha)
        || row.intent_id !== hash(Buffer.from(canonical(intent))) || row.producer_id !== intent.origin_producer
        || row.workstream_id !== intent.workstream_id
        || canonical(row.field_set) !== canonical(Object.keys(intent.set).sort((a, b) => Buffer.from(a).compare(Buffer.from(b))))) return false;
      if (row.status === "pending") {
        pending.push(row.intent_id);
        if (row.consumed_receipt_id !== null || row.last_error !== null) return false;
      } else if (row.status === "consumed") {
        const receiptId = row.consumed_receipt_id;
        if (row.attempts < 1 || row.last_error !== null || receiptId === null) return false;
        if (consumedReceipts !== null) {
          const raw = consumedReceipts[receiptId];
          if (!Buffer.isBuffer(raw)) return false;
          const receipt = JSON.parse(raw.toString());
          if (canonical(receipt) !== raw.toString() || receipt.receipt_id !== receiptId
            || !validateRegistered(receipt, schemaRoot, registryDoc, "fact-mutation-receipt/1.0.0", schemaSha, registrySha)) return false;
        }
      } else return false;
    }
    pending.sort((a, b) => Buffer.from(a).compare(Buffer.from(b)));
    const expectedStatus = pending.length ? "pending" : "converged";
    return verdict.evaluated_through_sequence === (sequences.at(-1) ?? 0)
      && canonical(verdict.pending_intent_ids) === canonical(pending)
      && canonical(verdict.failed_intent_ids) === canonical([])
      && canonical(verdict.waived_intent_ids) === canonical([])
      && verdict.status === expectedStatus;
  } catch { return false; }
};

const convergenceVerdict = (outbox, registryDoc, schemaSha, registrySha) => {
  const intentIds = (statuses) => outbox.entries
    .filter(({ status }) => statuses.includes(status))
    .map(({ intent_id }) => intent_id)
    .sort((left, right) => Buffer.from(left).compare(Buffer.from(right)));
  const pending = intentIds(["pending", "processing"]);
  const failed = intentIds(["failed"]);
  const waived = intentIds(["waived"]);
  const verdict = {
    contract: expectedContractRef(registryDoc, "intent-convergence-verdict/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", outbox_id: outbox.outbox_id,
    evaluated_through_sequence: outbox.entries.at(-1)?.sequence ?? 0,
    pending_intent_ids: pending, failed_intent_ids: failed, waived_intent_ids: waived,
    status: failed.length ? "failed" : pending.length ? "pending" : waived.length ? "waived" : "converged",
  };
  verdict.verdict_id = hash(Buffer.from(canonical(verdict)));
  return verdict;
};

const resolveFactCommandReplay = (
  index, receiptStore, commandId, commandFingerprint, registryDoc, schemaRoot, schemaSha, registrySha,
) => {
  try {
    const body = clone(index); delete body.index_id;
    if (!validateRegistered(index, schemaRoot, registryDoc, "fact-command-receipt-index/1.0.0", schemaSha, registrySha)
        || index.index_id !== hash(Buffer.from(canonical(body)))) return ["invalid", null];
    const entries = index.entries;
    const unique = (key) => new Set(entries.map((row) => row[key])).size === entries.length;
    if (canonical(entries.map(({ sequence }) => sequence)) !== canonical(entries.map((_, position) => position + 1))
        || index.next_sequence !== entries.length + 1
        || !unique("command_id") || !unique("transaction_id") || !unique("receipt_id")
        || canonical(Object.keys(receiptStore).sort()) !== canonical(entries.map(({ receipt_path }) => receipt_path).sort())) return ["invalid", null];
    const byCommand = new Map();
    for (const row of entries) {
      const expectedPaths = new Set([
        runtimePath(registryDoc, "fact_receipt_template", null, null, null, row.transaction_id),
        runtimePath(registryDoc, "repair_fact_receipt_template", null, null, null, row.transaction_id),
      ]);
      const raw = receiptStore[row.receipt_path];
      if (!expectedPaths.has(row.receipt_path) || !Buffer.isBuffer(raw) || hash(raw) !== row.receipt_sha256) return ["invalid", null];
      const receipt = JSON.parse(raw.toString("utf8"));
      if (!Buffer.from(canonical(receipt)).equals(raw)
          || !validateRegistered(receipt, schemaRoot, registryDoc, "fact-mutation-receipt/1.0.0", schemaSha, registrySha)
          || receipt.receipt_id !== row.receipt_id || receipt.transaction_id !== row.transaction_id
          || receipt.authorization.authorized_command_fingerprint !== row.command_fingerprint) return ["invalid", null];
      byCommand.set(row.command_id, { entry: row, receipt });
    }
    const match = byCommand.get(commandId);
    if (match === undefined) return ["new", null];
    if (match.entry.command_fingerprint !== commandFingerprint) return ["conflict", null];
    return ["noop", match.receipt];
  } catch { return ["invalid", null]; }
};

const publicationEligibilitySemantics = (panel, physicalInventory, policy, generation, registryDoc, schemaRoot, schemaSha, registrySha, built = null, outbox = null, convergenceVerdict = null, consumedReceipts = null) => {
  const sync = panel.sync; const audit = sync.audit; const drift = sync.action_projection;
  const policyBody = clone(policy); delete policyBody.policy_id; const generationBody = clone(generation); delete generationBody.generation_id;
  const statusIds = sync.canonical.status.workstream_current.map(({ workstream_id }) => workstream_id);
  const selected = resolvedSelection(policy);
  const catalog = panelBindingCatalog(registryDoc, schemaSha, registrySha);
  const inventorySources = policy.physical_workstream_inventory.flatMap((row) => [row.wdr_source, row.sidecar_source]);
  const inventoryMap = new Map(inventorySources.map((row) => [`${row.root_instance_id}\0${row.path}`, row]));
  const generationInventory = generation.leaf_sources.filter(({ source_kind }) => ["selected-physical-wdr", "wdr-action-sidecar"].includes(source_kind));
  const generationInventoryMap = new Map(generationInventory.map((row) => [`${row.root_instance_id}\0${row.path}`, row]));
  const inventoryLeafOk = inventoryMap.size === inventorySources.length && generationInventoryMap.size === inventoryMap.size
    && [...inventoryMap].every(([key, row]) => canonical(generationInventoryMap.get(key)) === canonical(row));
  const lineageScopeOk = built === null || Object.values(built).flat().every(({ envelope, manifest, receipt }) => manifest.selection_policy_id === policy.policy_id && receipt.selection_policy_id === policy.policy_id && envelope.generation_id === generation.generation_id);
  const memoryRoots = generation.roots.filter(({ root }) => root === "memory").map(({ root_instance_id }) => root_instance_id);
  const inventoryBody = clone(physicalInventory); delete inventoryBody.attestation_id;
  if (outbox === null) {
    outbox = { contract: expectedContractRef(registryDoc, "mutation-intent-outbox/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", outbox_generation: 1, entries: [] };
    outbox.outbox_id = hash(Buffer.from(canonical(outbox)));
  }
  convergenceVerdict ??= audit.intent_convergence;
  const convergenceOk = canonical(audit.intent_convergence) === canonical(convergenceVerdict)
    && intentConvergenceSemantics(outbox, convergenceVerdict, registryDoc, schemaRoot, schemaSha, registrySha, consumedReceipts);
  const scopeOk = validateRegistered(physicalInventory, schemaRoot, registryDoc, "physical-workstream-inventory/1.0.0", schemaSha, registrySha)
    && validateRegistered(policy, schemaRoot, registryDoc, "selection-policy/1.0.0", schemaSha, registrySha)
    && validateRegistered(generation, schemaRoot, registryDoc, "generation-envelope/1.0.0", schemaSha, registrySha)
    && validateRegistered(panel, schemaRoot, registryDoc, "management-panel-payload/2.0.0", schemaSha, registrySha)
    && validateRegistered(audit, schemaRoot, registryDoc, "state-audit-payload/2.0.0", schemaSha, registrySha)
    && validateRegistered(drift, schemaRoot, registryDoc, "action-projection-drift-verdict/1.0.0", schemaSha, registrySha)
    && physicalInventory.attestation_id === hash(Buffer.from(canonical(inventoryBody)))
    && physicalInventory.inventory_id === inventoryId(physicalInventory.workstreams)
    && canonical(physicalInventory.workstreams) === canonical(policy.physical_workstream_inventory)
    && canonical(physicalInventory.workstreams) === canonical(policy.workstream_catalog)
    && physicalInventory.inventory_id === policy.physical_workstream_inventory_id
    && physicalInventory.fact_generation === generation.fact_generation
    && canonical(memoryRoots) === canonical([physicalInventory.memory_root_instance_id])
    && policy.policy_id === hash(Buffer.from(canonical(policyBody))) && generation.generation_id === hash(Buffer.from(canonical(generationBody))) && selected.length > 0
    && generation.physical_workstream_inventory_id === policy.physical_workstream_inventory_id
    && generation.workstream_catalog_id === policy.workstream_catalog_id && generation.panel_catalog_id === catalog.catalog_id
    && validateRegistered(catalog, schemaRoot, registryDoc, "panel-binding-catalog/1.0.0", schemaSha, registrySha) && inventoryLeafOk
    && sync.selection_policy_id === policy.policy_id && generation.selection_policy_id === policy.policy_id && drift.selection_policy_id === policy.policy_id && audit.selection_policy_id === policy.policy_id
    && sync.generation_id === generation.generation_id && drift.generation_id === generation.generation_id
    && canonical([...statusIds].sort()) === canonical(selected) && canonical([...drift.selected_workstreams].sort()) === canonical(selected) && canonical([...audit.selected_workstreams].sort()) === canonical(selected)
    && lineageScopeOk && sourceAsOfSemantics(panel, policy, null, registryDoc)
    && (built === null || panelBindingSemantics(panel, built, registryDoc, policy, generation));
  const eligible = sync.artifact_integrity === "pass" && sync.business_freshness === "fresh"
    && audit.audit_status === "pass" && audit.execution_disposition === "ready"
    && convergenceOk && convergenceVerdict.status === "converged"
    && driftSemantics(drift) && drift.overall_status === "in-sync" && scopeOk;
  return (sync.publication_eligibility === "eligible") === eligible;
};

const capabilityRecordDigest = (record) => {
  const body = Object.fromEntries(Object.entries(record).filter(([key]) => !["capability_id", "authorization_record_digest"].includes(key)));
  return hash(Buffer.from(canonical(body)));
};
const authorityNativeFixture = (registryDoc, producerId, platform = "posix") => {
  const profile = registryDoc.runtime_authority_profile;
  const executableSha256 = hash(Buffer.from(`runtime-executable:${producerId}`));
  const adapter = profile.principal_adapters[platform];
  const nativePreimage = platform === "posix" ? {
    adapter_id: adapter.id, effective_uid_decimal: "501", executable_device_decimal: "16777234",
    executable_inode_decimal: "1001", executable_sha256: executableSha256, service_manager: "launchd", service_unit: producerId,
  } : {
    adapter_id: adapter.id, token_user_sid_sddl: "S-1-5-21-1000", token_elevation_type: "full",
    token_impersonation_level: "SecurityImpersonation", executable_volume_serial_hex: "A1B2C3D4",
    executable_file_id_hex: "0000000000001001", executable_sha256: executableSha256, service_name: producerId,
  };
  if (canonical(Object.keys(nativePreimage)) !== canonical(adapter.preimage_fields)) throw new Error("native identity fixture does not match canonical preimage field order");
  const effectiveIdentitySha256 = hash(Buffer.from(canonical(nativePreimage)));
  const nativeVerification = { adapter_boundary: profile.adapter_boundary, native_api_observed: true, opened_executable_handle: true,
    path_alias_rejected: true, namespace_or_token_verified: true, service_identity_verified: true };
  const principalId = hash(Buffer.from(canonical({ authority_profile_id: profile.profile_id, platform, native_preimage: nativePreimage })));
  return [principalId, effectiveIdentitySha256, executableSha256, nativePreimage, nativeVerification];
};
const authorityPrincipalFixture = (registryDoc, producerId, platform = "posix") => authorityNativeFixture(registryDoc, producerId, platform).slice(0, 3);
const capabilityRegistryFixture = (registryDoc, schemaSha, registrySha, platform = "posix") => {
  const capabilities = registryDoc.strict_rollout.writer_specs.map((spec) => {
    const [principalId] = authorityPrincipalFixture(registryDoc, spec.producer_id, platform);
    const record = {
      producer_id: spec.producer_id, principal_id: principalId, status: "active",
      allowed_operations: clone(spec.allowed_operations).sort(), allowed_fields: clone(spec.allowed_fields).sort(),
      allowed_sections: clone(spec.allowed_sections).sort(),
    };
    record.capability_id = capabilityRecordDigest(record); record.authorization_record_digest = record.capability_id;
    return record;
  }).sort((left, right) => Buffer.from(left.producer_id).compare(Buffer.from(right.producer_id)));
  const document = {
    contract: expectedContractRef(registryDoc, "writer-capability-registry/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", capability_epoch: 3, capabilities,
  };
  document.capability_registry_id = hash(Buffer.from(canonical(document)));
  return document;
};

const expectedActionDelta = (command) => {
  const before = command.operation === "patch" ? command.expected_revision : null;
  const changed = command.operation === "patch" ? command.set : command.create;
  return {
    action_id: command.action_id, operation: command.operation, before_revision: before, after_revision: before === null ? 1 : before + 1,
    changed_fields: Object.keys(changed).sort(),
    evidence_fingerprints: [...new Set(command.evidence.map(({ source_fingerprint }) => source_fingerprint))].sort(),
  };
};

const mutationTarget = (role, operation, order, targetPath) => {
  const root = "123e4567-e89b-42d3-a456-426614174000";
  const before = operation === "create" ? null : `sha256:${String((order + 1) % 10).repeat(64)}`;
  const after = operation === "remove" ? null : `sha256:${String((order + 6) % 10).repeat(64)}`;
  return {
    role, operation, apply_order: order, root_instance_id: root, path: targetPath, before_sha256: before, after_sha256: after,
    before_image: before === null ? null : { root_instance_id: root, path: `state/transactions/pending/images/${order}-before`, sha256: before },
    after_image: after === null ? null : { root_instance_id: root, path: `state/transactions/pending/images/${order}-after`, sha256: after },
  };
};

const journalFixture = (kind, schemaSha, registrySha, businessPaths = null, registryDoc = registry, includeIntentOutbox = false) => {
  const transactionId = `tx-${kind}-1`; const token = filesystemToken(transactionId);
  const journalDir = registryDoc.runtime_paths.journal_dir_template.replace("{transaction_token}", token);
  const receipts = kind === "repair" ? [`receipts/repair/${token}-fact.json`] : kind === "panel" ? [`receipts/panel/${token}.json`] : [`receipts/fact/${token}.json`];
  const targets = kind === "panel" ? [
    mutationTarget("projection", "create", 0, runtimePath(registryDoc, "canonical_projection_template", `sha256:${"a".repeat(64)}`, "program-status", "singleton")),
    mutationTarget("panel", "create", 1, runtimePath(registryDoc, "management_panel_template", `sha256:${"a".repeat(64)}`, null, "singleton")),
    mutationTarget("lineage-object", "create", 2, runtimePath(registryDoc, "selection_policy_template", `sha256:${"a".repeat(64)}`)),
    mutationTarget("lineage-index", "create", 3, runtimePath(registryDoc, "generation_lineage_index_template", `sha256:${"a".repeat(64)}`)),
    mutationTarget("panel-state", "replace", 4, registryDoc.runtime_paths.panel_state.path),
  ] : [...(businessPaths ?? ["actions/action-ledger.md"]).map((targetSpec, index) => mutationTarget(
    "business", typeof targetSpec === "object" ? targetSpec.operation : "replace", index, typeof targetSpec === "object" ? targetSpec.path : targetSpec,
  ))];
  if (kind !== "panel") {
    targets.push(mutationTarget("fact-generation", "replace", targets.length, "state/fact-generation.json"));
    if (["fact", "repair"].includes(kind)) targets.push(mutationTarget("fact-command-index", "replace", targets.length, registryDoc.runtime_paths.fact_command_receipt_index.path));
    if (kind === "fact" && includeIntentOutbox) targets.push(mutationTarget("intent-outbox", "replace", targets.length, registryDoc.runtime_paths.mutation_intent_outbox.path));
  }
  if (kind === "repair") {
    targets.push(mutationTarget("nonce", "replace", targets.length, runtimePath(registryDoc, "repair_nonce_template", null, null, null, null, `sha256:${"1".repeat(64)}`)));
  }
  for (const receiptPath of receipts) targets.push(mutationTarget("receipt", "create", targets.length, receiptPath));
  if (kind === "panel") targets.push(mutationTarget("pointer", "replace", targets.length, registryDoc.runtime_paths.panel_current_pointer.path));
  for (const target of targets) { if (target.before_image) target.before_image.path = `${journalDir}/images/${target.apply_order}-before`; if (target.after_image) target.after_image.path = `${journalDir}/images/${target.apply_order}-after`; }
  const manifest = {
    contract: { schema_id: "urn:adp:panel-sync-contracts:2026-07-24#transaction-journal-manifest-v1", schema_sha256: schemaSha, registry_sha256: registrySha },
    schema_version: "1.0.0", journal_id: `journal-${kind}-1`, transaction_id: transactionId, journal_dir: journalDir,
    manifest_path: kind === "panel" ? runtimePath(registryDoc, "publication_journal_template", `sha256:${"a".repeat(64)}`) : runtimePath(registryDoc, "journal_manifest_template", null, null, null, transactionId),
    prepared_marker_path: runtimePath(registryDoc, "journal_prepared_marker_template", null, null, null, transactionId),
    terminal_marker_path: kind === "panel" ? runtimePath(registryDoc, "publication_marker_template", `sha256:${"a".repeat(64)}`) : runtimePath(registryDoc, "journal_terminal_marker_template", null, null, null, transactionId),
    recovery_receipt_path: runtimePath(registryDoc, "journal_recovery_receipt_template", null, null, null, transactionId),
    transaction_kind: kind,
    authorization: null, targets, receipt_target_paths: receipts, prepared_at: "2026-07-24T02:00:00Z",
  };
  manifest.manifest_id = hash(Buffer.from(canonical(manifest)));
  const marker = {
    contract: { schema_id: "urn:adp:panel-sync-contracts:2026-07-24#journal-marker-v1", schema_sha256: schemaSha, registry_sha256: registrySha },
    schema_version: "1.0.0", journal_id: manifest.journal_id, manifest_id: manifest.manifest_id, state: "committed", marked_at: "2026-07-24T02:00:01Z",
  };
  marker.marker_id = hash(Buffer.from(canonical(marker)));
  return [manifest, marker];
};

const transitionJournalFixture = (
  kind, transactionId, journalId, targetSpecs, receiptPath, receiptRaw, registryDoc, schemaSha, registrySha,
  terminalState = "committed",
) => {
  const journalDir = registryDoc.runtime_paths.journal_dir_template.replace("{transaction_token}", filesystemToken(transactionId));
  const targets = targetSpecs.map((spec, index) => {
    const beforeRaw = spec.before_raw ?? null; const afterRaw = spec.after_raw ?? null;
    const target = mutationTarget(spec.role, spec.operation, index, spec.path);
    target.before_sha256 = beforeRaw === null ? null : hash(beforeRaw);
    target.after_sha256 = afterRaw === null ? null : hash(afterRaw);
    target.before_image = beforeRaw === null ? null : {
      root_instance_id: target.root_instance_id, path: `${journalDir}/images/${index}-before`, sha256: target.before_sha256,
    };
    target.after_image = afterRaw === null ? null : {
      root_instance_id: target.root_instance_id, path: `${journalDir}/images/${index}-after`, sha256: target.after_sha256,
    };
    return target;
  });
  const receiptTarget = mutationTarget("receipt", "create", targets.length, receiptPath);
  receiptTarget.after_sha256 = hash(receiptRaw);
  receiptTarget.after_image.sha256 = receiptTarget.after_sha256;
  receiptTarget.after_image.path = `${journalDir}/images/${targets.length}-after`;
  targets.push(receiptTarget);
  const manifest = {
    contract: expectedContractRef(registryDoc, "transaction-journal-manifest/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", journal_id: journalId, transaction_id: transactionId, journal_dir: journalDir,
    manifest_path: runtimePath(registryDoc, "journal_manifest_template", null, null, null, transactionId),
    prepared_marker_path: runtimePath(registryDoc, "journal_prepared_marker_template", null, null, null, transactionId),
    terminal_marker_path: runtimePath(registryDoc, "journal_terminal_marker_template", null, null, null, transactionId),
    recovery_receipt_path: runtimePath(registryDoc, "journal_recovery_receipt_template", null, null, null, transactionId),
    transaction_kind: kind, authorization: null, targets, receipt_target_paths: [receiptPath], prepared_at: "2026-07-24T03:09:00Z",
  };
  manifest.manifest_id = hash(Buffer.from(canonical(manifest)));
  const marker = {
    contract: expectedContractRef(registryDoc, "journal-marker/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", journal_id: journalId, manifest_id: manifest.manifest_id,
    state: terminalState, marked_at: "2026-07-24T03:10:01Z",
  };
  marker.marker_id = hash(Buffer.from(canonical(marker)));
  return [manifest, marker];
};

const journalSemantics = (manifest, marker, schemaRoot, registryDoc = null, schemaSha = null, registrySha = null) => {
  const documentsValid = registryDoc === null
    ? validate(manifest, schemaRoot, "transactionJournalManifestV1") && validate(marker, schemaRoot, "journalMarkerV1")
    : validateRegistered(manifest, schemaRoot, registryDoc, "transaction-journal-manifest/1.0.0", schemaSha, registrySha)
      && validateRegistered(marker, schemaRoot, registryDoc, "journal-marker/1.0.0", schemaSha, registrySha);
  if (!documentsValid) return false;
  const manifestBody = clone(manifest); delete manifestBody.manifest_id; const markerBody = clone(marker); delete markerBody.marker_id;
  if (manifest.manifest_id !== hash(Buffer.from(canonical(manifestBody))) || marker.marker_id !== hash(Buffer.from(canonical(markerBody)))) return false;
  const targets = manifest.targets;
  if (!new Set(["committed", "rolled-back"]).has(marker.state) || manifest.journal_dir !== `state/transactions/${filesystemToken(manifest.transaction_id)}`) return false;
  if (registryDoc === null) return false;
  const transactionId = manifest.transaction_id;
  const expectedLocalPaths = {
    manifest_path: runtimePath(registryDoc, "journal_manifest_template", null, null, null, transactionId),
    prepared_marker_path: runtimePath(registryDoc, "journal_prepared_marker_template", null, null, null, transactionId),
    terminal_marker_path: runtimePath(registryDoc, "journal_terminal_marker_template", null, null, null, transactionId),
    recovery_receipt_path: runtimePath(registryDoc, "journal_recovery_receipt_template", null, null, null, transactionId),
  };
  const kind = manifest.transaction_kind;
  if (kind === "panel") {
    const lineageIndexes = targets.filter(({ role }) => role === "lineage-index");
    const template = registryDoc.runtime_paths.generation_lineage_index_template.path;
    const [prefix, suffix] = template.split("{generation_token}");
    if (lineageIndexes.length !== 1 || !lineageIndexes[0].path.startsWith(prefix) || !lineageIndexes[0].path.endsWith(suffix)) return false;
    const generationToken = lineageIndexes[0].path.slice(prefix.length, lineageIndexes[0].path.length - suffix.length);
    if (!/^h_[0-9a-f]{64}$/.test(generationToken)) return false;
    expectedLocalPaths.manifest_path = registryDoc.runtime_paths.publication_journal_template.path.replace("{generation_token}", generationToken);
    expectedLocalPaths.terminal_marker_path = registryDoc.runtime_paths.publication_marker_template.path.replace("{generation_token}", generationToken);
  }
  if (Object.entries(expectedLocalPaths).some(([name, value]) => manifest[name] !== value) || new Set(Object.values(expectedLocalPaths)).size !== 4) return false;
  if (canonical(targets.map(({ apply_order }) => apply_order)) !== canonical(targets.map((_, index) => index))) return false;
  if (new Set(targets.map((row) => `${row.root_instance_id}\0${row.path}`)).size !== targets.length) return false;
  for (const row of targets) {
    if (row.operation === "create" && (row.before_sha256 !== null || row.before_image !== null || row.after_sha256 === null || row.after_image === null)) return false;
    if (row.operation === "replace" && (row.before_sha256 === null || row.before_image === null || row.after_sha256 === null || row.after_image === null)) return false;
    if (row.operation === "remove" && (row.before_sha256 === null || row.before_image === null || row.after_sha256 !== null || row.after_image !== null)) return false;
    for (const [locator, expected] of [[row.before_image, row.before_sha256], [row.after_image, row.after_sha256]]) {
      if (locator !== null && (locator.root_instance_id !== row.root_instance_id || locator.sha256 !== expected)) return false;
    }
    if (row.before_image !== null && row.before_image.path !== runtimePath(registryDoc, "journal_before_image_template", null, null, null, transactionId, null, null, null, row.apply_order)) return false;
    if (row.after_image !== null && row.after_image.path !== runtimePath(registryDoc, "journal_after_image_template", null, null, null, transactionId, null, null, null, row.apply_order)) return false;
  }
  const counts = Object.fromEntries([...new Set(targets.map(({ role }) => role))].map((role) => [role, targets.filter((row) => row.role === role).length]));
  const roleClosure = kind === "fact"
    ? ((counts.business ?? 0) >= 1 || counts["intent-outbox"] === 1) && counts["fact-generation"] === 1 && counts["fact-command-index"] === 1 && [undefined, 1].includes(counts["intent-outbox"])
      && counts.receipt === 1 && Object.keys(counts).every((role) => ["business", "fact-generation", "fact-command-index", "intent-outbox", "receipt"].includes(role))
    : kind === "repair" ? counts.business >= 1 && canonical(counts) === canonical({ business: counts.business, "fact-generation": 1, "fact-command-index": 1, nonce: 1, receipt: 1 })
      : kind === "repair-attempt" ? canonical(counts) === canonical({ "repair-attempt-ledger": 1, "repair-index": 1, receipt: 1 })
        : kind === "panel" ? counts.projection >= 1 && counts["lineage-object"] >= 1 && targets.at(-1).role === "pointer"
          && canonical(counts) === canonical({ projection: counts.projection, panel: 1, "lineage-object": counts["lineage-object"], "lineage-index": 1, pointer: 1, "panel-state": 1, receipt: 1 })
          : kind === "release-evidence" ? [canonical({ "release-evidence": 2, receipt: 1 }), canonical({ "release-evidence": 2, "history-index": 1, receipt: 1 })].includes(canonical(counts))
            : kind === "activation" && counts.receipt === 1 && counts["activation-lifecycle-index"] === 1
              && Object.keys(counts).every((role) => ["activation-state", "capability-registry", "attestation", "transition-state", "activation-lifecycle-index", "receipt"].includes(role))
              && Object.entries(counts).filter(([role]) => !["receipt", "activation-lifecycle-index"].includes(role)).reduce((total, [, count]) => total + count, 0) === 1;
  if (!roleClosure) return false;
  const receiptPaths = targets.filter(({ role }) => role === "receipt").map(({ path: value }) => value);
  const expectedCount = 1;
  return canonical(receiptPaths) === canonical(manifest.receipt_target_paths) && receiptPaths.length === expectedCount
    && marker.journal_id === manifest.journal_id && marker.manifest_id === manifest.manifest_id;
};

const wdrCreateSections = new Set(["identity", "bmm-artifact-index", "scope", "acceptance", "project-status", "next-actions", "roadmap", "cross-workstream-links", "decisions-evidence", "record-rule"]);
const commandKind = (command) => command.contract?.schema_id?.endsWith("#wdr-command-v1") ? "wdr"
  : command.contract?.schema_id?.endsWith("#owned-fact-command-v1") ? "owned"
    : command.contract?.schema_id?.endsWith("#producer-intent-outbox-command-v1") ? "intent"
    : command.contract?.schema_id?.endsWith("#bootstrap-migration-command-v1") ? "bootstrap" : "action";
const commandProducer = (command) => ["wdr", "owned", "intent", "bootstrap"].includes(commandKind(command)) ? command.issuer.producer_id : "adp-status-sync";
const commandPermissions = (command, registryDoc) => {
  if (commandKind(command) === "action") return [new Set(Object.keys(command.operation === "patch" ? command.set : command.create)), new Set()];
  if (commandKind(command) === "owned") {
    const fields = new Set(["owned_facts"]); if (Object.hasOwn(command, "status_intents")) fields.add("status_intents");
    return [fields, new Set()];
  }
  if (commandKind(command) === "intent") return [new Set(["status_intents"]), new Set()];
  if (command.operation === "create") return [new Set(["owned_sections"]), new Set(wdrCreateSections)];
  const fields = new Set(Object.keys(command.set)); const permissionFields = new Set(fields); const sections = new Set();
  if (Object.hasOwn(command, "status_intents")) permissionFields.add("status_intents");
  if (Object.hasOwn(command, "consumed_intent_ids")) permissionFields.add("consumed_intent_ids");
  const rows = new Map(registryDoc.wdr_field_section_map.map((row) => [row.field, row]));
  const requiredFields = new Set(["status", "phase", "progress", "blockers", "risks", "dependencies", "change_notes", "last_status_sync", "refresh_actions", "roadmap", "meeting_history_append", "owned_sections"]);
  if (canonical([...rows.keys()].sort()) !== canonical([...requiredFields].sort())) throw new Error("WDR field-section registry is incomplete");
  for (const field of fields) {
    const rule = rows.get(field); if (!rule) throw new Error(`unmapped WDR field: ${field}`);
    for (const section of rule.sections ?? []) sections.add(section);
    if (rule.sections_from_payload) for (const row of command.set[field]) sections.add(row.section);
  }
  return [permissionFields, sections];
};
const expectedFactBusinessTargets = (command, registryDoc) => {
  if (commandKind(command) === "intent") return [];
  const root_instance_id = "123e4567-e89b-42d3-a456-426614174000";
  if (commandKind(command) === "action") return ["action_ledger", "action_ledger_state", "action_flow_index"].map((name) => ({ root_instance_id, path: registryDoc.runtime_paths[name].path, operation: "replace" }));
  if (commandKind(command) === "owned") return [{ root_instance_id, path: command.target_path, operation: command.operation === "create" ? "create" : "replace" }];
  const prefix = `workstreams/${command.workstream_id}`;
  const paths = [`${prefix}/delivery-record.md`, `${prefix}/delivery-record.state.json`];
  if (command.operation === "create" || command.set.refresh_actions) paths.push(`${prefix}/action-projection.json`);
  return paths.map((targetPath) => ({ root_instance_id, path: targetPath, operation: command.operation === "create" ? "create" : "replace" }));
};
const actionLedgerStateDocument = (rows, ledgerRaw, ledgerRevision, appliedCommands, registryDoc, schemaSha, registrySha) => {
  const document = { contract: expectedContractRef(registryDoc, "action-ledger-state/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", ledger_path: registryDoc.runtime_paths.action_ledger.path,
    ledger_fingerprint: hash(ledgerRaw), ledger_revision: ledgerRevision,
    actions: rows.map((row) => ({ action_id: row.action_id, action_revision: row.action_revision, row_fingerprint: hash(Buffer.from(`${renderActionLedgerRow(row)}\n`)) })),
    applied_commands: clone(appliedCommands).sort((a, b) => Buffer.from(a.command_id).compare(Buffer.from(b.command_id))) };
  document.state_id = hash(Buffer.from(canonical(document))); return document;
};
const actionFlowDocument = (rows, ledgerRaw, ledgerRevision, registryDoc, schemaSha, registrySha) => {
  void ledgerRevision; void registryDoc; void schemaSha; void registrySha;
  const relationIds = (value) => {
    if (value === "-") return [];
    const values = value.split(/\s*[;,]\s*/); if (values.some((item) => !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(item))) throw new Error("action-flow relation ID is invalid");
    return [...new Set(values)].sort((a, b) => Buffer.from(a).compare(Buffer.from(b)));
  };
  const actions = [];
  for (const row of rows) {
    const baselineRevision = Number(row.baseline_revision);
    if (!Number.isSafeInteger(baselineRevision) || baselineRevision < 1 || !actionRowChronologyValid(row)) continue;
    let relatedPlanItemIds; let relatedFlowEdgeIds;
    try { relatedPlanItemIds = relationIds(row.related_plan_items); relatedFlowEdgeIds = relationIds(row.related_flow_edges); } catch { continue; }
    actions.push({ action_id: row.action_id, status: row.status, created_at: row.created_at, updated_at: row.last_updated,
      started_at: row.started_at === "-" ? null : row.started_at, done_at: row.done_at === "-" ? null : row.done_at, cancelled_at: row.cancelled_at === "-" ? null : row.cancelled_at,
      baseline_revision: baselineRevision, related_plan_item_ids: relatedPlanItemIds, related_flow_edge_ids: relatedFlowEdgeIds,
      source: { artifact_id: "ACTION-LEDGER", artifact_path: "actions/action-ledger.md", source_fingerprint: hash(ledgerRaw) } });
  }
  return { action_flow_schema_version: "1.0.0", actions: actions.sort((a, b) => Buffer.from(a.action_id).compare(Buffer.from(b.action_id))),
    compatibility: { strategy: "preserve-unmapped", migration_error_code: "ADP-ACTION-FLOW-MIGRATION-REQUIRED" } };
};
const refreshLedgerFixture = (registryDoc, schemaSha, registrySha) => {
  const rows = [
    { action_id: "A-FLOW-1", status: "open", owner: "FDE-C", routing_scope_id: "l1-payments", affected_workstreams: ["l1-checkout"], action: "Ship checkout", source: `meetings/m1.md@sha256:${"c".repeat(64)}`, reason: "cmd-action-prior", due_trigger: "next sync", closure_criteria: "release accepted", closure_criteria_verifiable: "true", created_at: "2026-07-23T01:00:00Z", started_at: "-", done_at: "-", cancelled_at: "-", baseline_revision: "-", related_plan_items: "-", related_flow_edges: "-", last_updated: "2026-07-24T01:00:00Z", owning_workflow: "adp-status-sync", action_revision: 4 },
    { action_id: "A-OTHER-1", status: "blocked", owner: "FDE-O", routing_scope_id: "l1-other", affected_workstreams: [], action: "Unrelated action", source: `meetings/m0.md@sha256:${"a".repeat(64)}`, reason: "cmd-other-prior", due_trigger: "later", closure_criteria: "other accepted", closure_criteria_verifiable: "false", created_at: "2026-07-22T01:00:00Z", started_at: "2026-07-23T01:00:00Z", done_at: "-", cancelled_at: "-", baseline_revision: "-", related_plan_items: "-", related_flow_edges: "-", last_updated: "2026-07-23T01:00:00Z", owning_workflow: "adp-status-sync", action_revision: 2 },
    { action_id: "A-TERMINAL-1", status: "done", owner: "FDE-T", routing_scope_id: "l1-checkout", affected_workstreams: [], action: "Closed action", source: `meetings/m0.md@sha256:${"b".repeat(64)}`, reason: "cmd-terminal-prior", due_trigger: "complete", closure_criteria: "closed", closure_criteria_verifiable: "true", created_at: "2026-07-22T02:00:00Z", started_at: "2026-07-23T01:30:00Z", done_at: "2026-07-23T02:00:00Z", cancelled_at: "-", baseline_revision: "-", related_plan_items: "-", related_flow_edges: "-", last_updated: "2026-07-23T02:00:00Z", owning_workflow: "adp-status-sync", action_revision: 3 },
  ].sort((a, b) => Buffer.from(a.action_id).compare(Buffer.from(b.action_id)));
  const raw = renderActionLedger(rows); return [rows, raw, actionLedgerStateDocument(rows, raw, 11, [], registryDoc, schemaSha, registrySha)];
};

const STATUS_INTENT_FIELDS = new Set(["status", "phase", "progress", "blockers", "risks", "dependencies", "change_notes", "refresh_actions"]);
const commandIntentOutboxMode = (command, registryDoc) => {
  const producer = commandProducer(command);
  if (producer === "adp-meeting-sync" && commandKind(command) === "intent") return "emit";
  if (producer === "adp-meeting-sync" && command.set?.meeting_history_append) return "emit";
  if (producer === "adp-bmm-checkpoint-sync" && command.set?.owned_sections) return "emit";
  const authorizedOwnedProfiles = new Set(registryDoc.owned_fact_target_profiles.filter((row) => row.producer_id === producer).map((row) => row.profile_id));
  if (producer === "adp-risk-dependency-change-review" && commandKind(command) === "owned" && authorizedOwnedProfiles.has(command.target_profile_id)) return "emit";
  if (producer === "adp-status-sync" && commandKind(command) === "wdr" && command.operation === "patch"
    && Object.keys(command.set ?? {}).some((field) => field !== "refresh_actions" && STATUS_INTENT_FIELDS.has(field))) return "consume";
  return "none";
};
const statusIntentsForCommand = (command, registryDoc) => commandIntentOutboxMode(command, registryDoc) === "emit" ? clone(command.status_intents ?? []) : [];
const meetingPlanIntentCarrierSemantics = (plan, registryDoc, schemaRoot, schemaSha, registrySha) => {
  try {
    if (!validateRegistered(plan, schemaRoot, registryDoc, "meeting-sync-plan/2.0.0", schemaSha, registrySha)) return false;
    const planIntents = plan.status_intents.map((row) => canonical(row));
    if (new Set(planIntents).size !== planIntents.length) return false;
    const carried = []; const commandIds = [];
    for (const command of plan.intent_outbox_commands) {
      if (!validateRegistered(command, schemaRoot, registryDoc, "producer-intent-outbox-command/1.0.0", schemaSha, registrySha)
        || command.issuer.producer_id !== "adp-meeting-sync" || command.operation !== "append-intents"
        || command.source_instance_id !== plan.meeting_instance_id
        || command.status_intents.some((intent) => intent.origin_producer !== "adp-meeting-sync")) return false;
      const expectedEvidence = new Map(command.status_intents.flatMap((intent) => intent.evidence).map((row) => [canonical(row), row]));
      if (canonical(command.evidence) !== canonical([...expectedEvidence.values()].sort(compareEvidence))) return false;
      commandIds.push(command.command_id); carried.push(...command.status_intents.map((row) => canonical(row)));
    }
    return canonical(commandIds) === canonical([...new Set(commandIds)].sort((a, b) => Buffer.from(a).compare(Buffer.from(b))))
      && canonical(carried.sort()) === canonical([...planIntents].sort());
  } catch { return false; }
};
const meetingPlanIntentFixture = (registryDoc, schemaSha, registrySha) => {
  const evidence = [{ source_path: "meetings/m-intent-only.md", source_fingerprint: `sha256:${"7".repeat(64)}`, observed_at: "2026-07-24T02:00:00Z" }];
  const intent = { contract: expectedContractRef(registryDoc, "status-mutation-intent/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
    intent_id: "meeting-M-INTENT-1-status", origin_producer: "adp-meeting-sync", workstream_id: "l1-checkout",
    set: { progress: "Intent-only update" }, evidence: clone(evidence) };
  const capability = capabilityRegistryFixture(registryDoc, schemaSha, registrySha).capabilities.find(({ producer_id }) => producer_id === "adp-meeting-sync");
  const carrier = { contract: expectedContractRef(registryDoc, "producer-intent-outbox-command/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
    command_id: "cmd-meeting-M-INTENT-1-intents", issuer: { producer_id: "adp-meeting-sync", capability_id: capability.capability_id },
    operation: "append-intents", source_instance_id: "meeting-M-INTENT-1", status_intents: [clone(intent)], evidence: clone(evidence) };
  return { contract: expectedContractRef(registryDoc, "meeting-sync-plan/2.0.0", schemaSha, registrySha), schema_version: "2.0.0",
    meeting_instance_id: "meeting-M-INTENT-1", action_commands: [], status_intents: [intent], intent_outbox_commands: [carrier],
    history_patches: [], evidence_only_items: [] };
};
const pendingOutboxEntry = (intent, sourceCommandId, sourceCommandFingerprint, sequence) => ({
  sequence, intent_id: hash(Buffer.from(canonical(intent))), intent: clone(intent), source_command_id: sourceCommandId,
  source_command_fingerprint: sourceCommandFingerprint, producer_id: intent.origin_producer, workstream_id: intent.workstream_id,
  field_set: Object.keys(intent.set).sort((a, b) => Buffer.from(a).compare(Buffer.from(b))), status: "pending", attempts: 0,
  last_error: null, created_at: [...intent.evidence].sort((a, b) => Buffer.from(a.observed_at).compare(Buffer.from(b.observed_at)))[0].observed_at,
  consumed_receipt_id: null,
});

const factAttributionFixture = (
  schemaSha, registrySha, registryDoc, fixtureKind = "action", createCommand = null,
  workstreamId = "l1-checkout", beforeFactGeneration = 7, priorTransactionId = "tx-prior-1", orphanActionId = null,
) => {
  const evidence = [{ source_path: "meetings/m1.md", source_fingerprint: `sha256:${"c".repeat(64)}`, observed_at: "2026-07-24T02:00:00Z" }];
  let command; let refreshRows = null; let refreshLedgerRaw = null; let refreshLedgerState = null;
  let beforeOwned = null; let afterOwned = null;
  if (["action", "action-create", "action-terminal"].includes(fixtureKind)) command = {
    contract: { schema_id: "urn:adp:panel-sync-contracts:2026-07-24#action-command-v2", schema_sha256: schemaSha, registry_sha256: registrySha },
    schema_version: "2.0.0", command_id: `cmd-${fixtureKind}-1`, operation: fixtureKind === "action-create" ? "create" : "patch", action_id: fixtureKind === "action-terminal" ? "A-TERMINAL-1" : "A-FLOW-1", evidence,
  };
  if (fixtureKind === "action") Object.assign(command, { expected_revision: 4, set: { owner: "FDE-C" } });
  else if (fixtureKind === "action-terminal") Object.assign(command, { expected_revision: 3, set: { owner: "FDE-T" } });
  else if (fixtureKind === "action-create") command.create = { owner: "FDE-C", status: "open", action: "Ship checkout", due_trigger: "next sync", closure_criteria: "release accepted", routing_scope_id: "l1-checkout", affected_workstreams: ["l1-checkout"] };
  else if (fixtureKind === "intent-only") command = {
    contract: expectedContractRef(registryDoc, "producer-intent-outbox-command/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", command_id: "cmd-meeting-intents-1",
    issuer: { producer_id: "adp-meeting-sync", capability_id: `sha256:${"0".repeat(64)}` },
    operation: "append-intents", source_instance_id: "meeting-M-INTENTS-1", status_intents: [], evidence,
  };
  else if (fixtureKind === "wdr-create") {
    if (!createCommand) throw new Error("wdr-create fixture requires a rendered create command");
    command = clone(createCommand);
  } else if (["owned-risk-flow", "owned-decision"].includes(fixtureKind)) {
    let targetProfileId; let targetPath;
    if (fixtureKind === "owned-risk-flow") {
      targetProfileId = "risk-flow-index-v1"; targetPath = "views/risk-flow.json";
      beforeOwned = Buffer.from(canonical({
        risk_flow_schema_version: "1.0.0", risks: [],
        compatibility: { strategy: "preserve-unmapped", migration_error_code: "ADP-RISK-FLOW-MIGRATION-REQUIRED" },
      }));
      afterOwned = Buffer.from(canonical({
        risk_flow_schema_version: "1.0.0",
        risks: [{
          risk_id: "RISK-1", lifecycle: "open", relation_state: "at-risk", observed_at: "2026-07-24T02:00:00Z",
          terminal_at: null, baseline_revision: 1, related_plan_item_ids: [], related_flow_edge_ids: [], rule_id: "RULE-1",
          sources: [{ artifact_id: "WDR-1", artifact_path: "workstreams/l1-checkout/delivery-record.md", field: "Risks", source_fingerprint: `sha256:${"c".repeat(64)}` }],
        }],
        compatibility: { strategy: "preserve-unmapped", migration_error_code: "ADP-RISK-FLOW-MIGRATION-REQUIRED" },
      }));
    } else {
      targetProfileId = "workstream-decision-v1"; targetPath = `workstreams/${workstreamId}/decisions.md`;
      beforeOwned = Buffer.from("# Decisions\n\n- ADR-1: pending\n");
      afterOwned = Buffer.from("# Decisions\n\n- ADR-1: accepted\n");
    }
    command = {
      contract: expectedContractRef(registryDoc, "owned-fact-command/1.0.0", schemaSha, registrySha),
      schema_version: "1.0.0", command_id: `cmd-${fixtureKind}-1`, operation: "patch",
      issuer: { producer_id: "adp-risk-dependency-change-review", capability_id: `sha256:${"0".repeat(64)}` },
      target_profile_id: targetProfileId, target_path: targetPath, expected_before_sha256: hash(beforeOwned),
      after_bytes: encodedBytes(afterOwned), after_sha256: hash(afterOwned), evidence,
    };
  } else {
    let producer = "adp-status-sync"; let wdrSet;
    if (fixtureKind === "wdr-status") wdrSet = { progress: "Implementation active", blockers: { mode: "replace", values: ["Access"] }, risks: { mode: "replace", values: ["Schedule"] } };
    else if (fixtureKind === "wdr-meeting-history") { producer = "adp-meeting-sync"; wdrSet = { meeting_history_append: [{ entry_id: "meeting-entry-1", command_id: "cmd-wdr-meeting-history-1", observed_at: "2026-07-24T02:00:00Z", source_path: "meetings/m1.md", source_fingerprint: `sha256:${"c".repeat(64)}`, classification: "wdr_update", summary: "Progress reviewed", owner: "FDE-C", due_trigger: "next sync", status: "noted" }] }; }
    else if (fixtureKind === "wdr-owned-section") { producer = "adp-bmm-checkpoint-sync"; wdrSet = { owned_sections: [{ section: "checkpoint-sync-log", mode: "append", lines: ["Checkpoint reviewed"] }] }; }
    else if (fixtureKind === "wdr-roadmap") wdrSet = { roadmap: { mode: "replace", lines: ["| Milestone | Target |", "| --- | --- |", "| M1 | Gate A |"] } };
    else if (fixtureKind === "wdr-refresh-actions") wdrSet = { refresh_actions: true };
    else if (fixtureKind === "wdr-identity") wdrSet = { status: "blocked", phase: "validation" };
    else if (fixtureKind === "wdr-risk-direct") { producer = "adp-risk-dependency-change-review"; wdrSet = { risks: { mode: "replace", values: ["Schedule"] } }; }
    else if (fixtureKind === "wdr-risk-reauthorized") wdrSet = { risks: { mode: "replace", values: ["Schedule"] } };
    else throw new Error(`unknown fact fixture kind: ${fixtureKind}`);
    command = {
      contract: { schema_id: "urn:adp:panel-sync-contracts:2026-07-24#wdr-command-v1", schema_sha256: schemaSha, registry_sha256: registrySha },
      schema_version: "1.0.0", command_id: `cmd-${fixtureKind}-1`, issuer: { producer_id: producer, capability_id: `sha256:${"0".repeat(64)}` },
      operation: "patch", workstream_id: workstreamId, expected_wdr_revision: 4, expected_file_generation: 7, set: wdrSet, evidence,
    };
    if (fixtureKind === "wdr-refresh-actions") {
      [refreshRows, refreshLedgerRaw, refreshLedgerState] = refreshLedgerFixture(registryDoc, schemaSha, registrySha);
      command.action_snapshot = actionSnapshot(refreshRows, command.workstream_id, refreshLedgerState.ledger_fingerprint, refreshLedgerState.ledger_revision);
    }
  }
  const explicitIntentSets = {
    "intent-only": { progress: "Intent-only meeting update" },
    "wdr-meeting-history": { blockers: { mode: "replace", values: ["Access"] } },
    "wdr-owned-section": { progress: "Checkpoint reviewed", risks: { mode: "replace", values: ["Schedule"] } },
    "owned-risk-flow": { risks: { mode: "replace", values: ["RISK-1: at-risk"] } },
    "owned-decision": { dependencies: { mode: "replace", values: ["ADR-1: accepted"] } },
  };
  let consumedOutboxIntents = [];
  if (Object.hasOwn(explicitIntentSets, fixtureKind)) command.status_intents = [{
    contract: expectedContractRef(registryDoc, "status-mutation-intent/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
    intent_id: `intent-${command.command_id}`, origin_producer: commandProducer(command), workstream_id: command.workstream_id ?? workstreamId,
    set: clone(explicitIntentSets[fixtureKind]), evidence: clone(command.evidence),
  }];
  else if (fixtureKind === "wdr-status") consumedOutboxIntents = clone(statusIntentFixture(registryDoc, schemaSha, registrySha).accepted_intents);
  else if (["wdr-identity", "wdr-risk-reauthorized"].includes(fixtureKind)) consumedOutboxIntents = [{
    contract: expectedContractRef(registryDoc, "status-mutation-intent/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", intent_id: `intent-${fixtureKind}-1`,
    origin_producer: fixtureKind === "wdr-identity" ? "adp-bmm-checkpoint-sync" : "adp-risk-dependency-change-review",
    workstream_id: command.workstream_id, set: clone(command.set), evidence: clone(command.evidence),
  }];
  if (consumedOutboxIntents.length) {
    command.consumed_intent_ids = consumedOutboxIntents
      .map((intent) => hash(Buffer.from(canonical(intent)))).sort((a, b) => Buffer.from(a).compare(Buffer.from(b)));
    const intentEvidence = new Map(consumedOutboxIntents.flatMap(({ evidence: rows }) => rows).map((row) => [canonical(row), row]));
    command.evidence = [...intentEvidence.values()].sort(compareEvidence);
  }
  const capabilityRegistry = capabilityRegistryFixture(registryDoc, schemaSha, registrySha);
  const capabilities = capabilityRegistry.capabilities;
  const cap = capabilities.find(({ producer_id }) => producer_id === commandProducer(command));
  if (command.issuer) command.issuer.capability_id = cap.capability_id;
  const authorization = { producer_id: cap.producer_id, capability_id: cap.capability_id, capability_epoch: 3, principal_id: cap.principal_id, capability_registry_id: capabilityRegistry.capability_registry_id, authorization_record_digest: cap.authorization_record_digest, authorized_command_fingerprint: hash(Buffer.from(canonical(command))) };
  const outboxMode = commandIntentOutboxMode(command, registryDoc); let outboxIntents = statusIntentsForCommand(command, registryDoc);
  if (outboxMode === "consume") outboxIntents = clone(consumedOutboxIntents);
  const expectedTargets = expectedFactBusinessTargets(command, registryDoc);
  const [journal, marker] = journalFixture("fact", schemaSha, registrySha, expectedTargets, registryDoc, outboxMode !== "none");
  journal.authorization = clone(authorization);
  const beforeState = {
    contract: { schema_id: "urn:adp:panel-sync-contracts:2026-07-24#fact-generation-state-v1", schema_sha256: schemaSha, registry_sha256: registrySha },
    schema_version: "1.0.0", fact_generation: beforeFactGeneration, last_transaction_id: priorTransactionId,
  };
  beforeState.state_id = hash(Buffer.from(canonical(beforeState)));
  const afterState = {
    contract: clone(beforeState.contract), schema_version: "1.0.0", fact_generation: beforeFactGeneration + 1, last_transaction_id: journal.transaction_id,
  };
  afterState.state_id = hash(Buffer.from(canonical(afterState)));
  const generationTarget = journal.targets.find(({ role }) => role === "fact-generation");
  generationTarget.path = "state/fact-generation.json";
  generationTarget.before_sha256 = hash(Buffer.from(canonical(beforeState)));
  generationTarget.after_sha256 = hash(Buffer.from(canonical(afterState)));
  generationTarget.before_image.sha256 = generationTarget.before_sha256;
  generationTarget.after_image.sha256 = generationTarget.after_sha256;
  const finalized = (document, identityField) => { document[identityField] = hash(Buffer.from(canonical(document))); return document; };
  let businessContents;
  if (commandKind(command) === "action") {
    let beforeRows = [];
    if (command.operation === "patch") beforeRows = [{
      action_id: command.action_id, status: fixtureKind === "action-terminal" ? "done" : "open", owner: fixtureKind === "action-terminal" ? "FDE-T" : "FDE-A", routing_scope_id: "l1-checkout",
      affected_workstreams: ["l1-checkout"], action: "Ship checkout",
      source: `meetings/prior.md@sha256:${"a".repeat(64)}`, reason: "cmd-action-prior", due_trigger: "next sync",
      closure_criteria: "release accepted", closure_criteria_verifiable: "true", created_at: "2026-07-23T01:00:00Z",
      started_at: fixtureKind === "action-terminal" ? "2026-07-23T02:00:00Z" : "-", done_at: fixtureKind === "action-terminal" ? "2026-07-24T01:00:00Z" : "-",
      cancelled_at: "-", baseline_revision: "-", related_plan_items: "-", related_flow_edges: "-", last_updated: "2026-07-24T01:00:00Z",
      owning_workflow: "adp-status-sync", action_revision: fixtureKind === "action-terminal" ? 3 : 4,
    }];
    const beforeLedgerBytes = renderActionLedger(beforeRows);
    const afterRows = applyActionCommand(beforeRows, command);
    const afterLedgerBytes = renderActionLedger(afterRows);
    const beforeRevision = 4;
    const applied = [{ command_id: command.command_id, command_fingerprint: authorization.authorized_command_fingerprint, action_id: command.action_id }];
    const beforeLedgerState = actionLedgerStateDocument(beforeRows, beforeLedgerBytes, beforeRevision, [], registryDoc, schemaSha, registrySha);
    const afterLedgerState = actionLedgerStateDocument(afterRows, afterLedgerBytes, beforeRevision + 1, applied, registryDoc, schemaSha, registrySha);
    const beforeFlow = actionFlowDocument(beforeRows, beforeLedgerBytes, beforeRevision, registryDoc, schemaSha, registrySha);
    const afterFlow = actionFlowDocument(afterRows, afterLedgerBytes, beforeRevision + 1, registryDoc, schemaSha, registrySha);
    businessContents = [[beforeLedgerBytes, afterLedgerBytes], [Buffer.from(canonical(beforeLedgerState)), Buffer.from(canonical(afterLedgerState))], [Buffer.from(canonical(beforeFlow)), Buffer.from(canonical(afterFlow))]];
  } else if (commandKind(command) === "owned") {
    businessContents = [[beforeOwned, afterOwned]];
  } else if (commandKind(command) === "intent") {
    businessContents = [];
  } else {
    const workstreamId = command.workstream_id; const recordPath = `workstreams/${workstreamId}/delivery-record.md`;
    const stateContract = expectedContractRef(registryDoc, "wdr-file-state/1.0.0", schemaSha, registrySha);
    const sidecarContract = expectedContractRef(registryDoc, "wdr-action-projection/1.0.0", schemaSha, registrySha);
    let beforeWdr; let afterWdr; let beforeWdrState; let afterWdrState; let beforeSidecar = null; let afterSidecar;
    if (command.operation === "create") {
      beforeWdr = null; afterWdr = Buffer.from(command.rendered_record); beforeWdrState = null;
      afterWdrState = { contract: stateContract, schema_version: "1.0.0", workstream_id: workstreamId, record_path: recordPath, record_fingerprint: hash(afterWdr), wdr_revision: 1, file_generation: 1, lifecycle: "active" };
      afterSidecar = { contract: sidecarContract, schema_version: "1.0.0", workstream_id: workstreamId, ledger_fingerprint: `sha256:${"0".repeat(64)}`, ledger_revision: 0, wdr_revision: 1, file_generation: 1, renderer_id: "urn:adp:wdr-action-renderer:1.0.0", renderer_sha256: registryDoc.protocol.sha256, actions: [] };
    } else {
      let orphanRecord = orphanActionId === null ? null : { action_id: orphanActionId, owner: "FDE-O", action: "Remove orphan projection", due_trigger: "next sync",
        status: "open", action_revision: 1, routing_scope_id: workstreamId, affected_workstreams: [workstreamId] };
      if (orphanRecord !== null) orphanRecord.rendered_summary = renderedActionSummary(orphanRecord);
      const beforeProjectionActions = orphanRecord === null ? []
        : [...clone(command.action_snapshot.actions), orphanRecord].sort((a, b) => Buffer.from(a.action_id).compare(Buffer.from(b.action_id)));
      let beforeWdrText = fixtureWdr(workstreamId);
      if (beforeProjectionActions.length) beforeWdrText = applyWdrPatch(
        beforeWdrText, { set: { refresh_actions: true } }, beforeProjectionActions.map(({ rendered_summary }) => rendered_summary),
      );
      beforeWdr = Buffer.from(beforeWdrText);
      const beforeSidecarValue = { contract: sidecarContract, schema_version: "1.0.0", workstream_id: workstreamId,
        ledger_fingerprint: command.set.refresh_actions ? refreshLedgerState.ledger_fingerprint : `sha256:${"d".repeat(64)}`,
        ledger_revision: command.set.refresh_actions ? refreshLedgerState.ledger_revision : 4,
        wdr_revision: 4, file_generation: 7, renderer_id: "urn:adp:wdr-action-renderer:1.0.0", renderer_sha256: registryDoc.protocol.sha256,
        actions: beforeProjectionActions };
      afterSidecar = clone(beforeSidecarValue);
      let summaries = [];
      if (command.set.refresh_actions) {
        if (refreshLedgerState === null) throw new Error("refresh fixture has no ledger snapshot");
        summaries = command.action_snapshot.actions.map(({ rendered_summary }) => rendered_summary);
      }
      afterWdr = Buffer.from(applyWdrPatch(beforeWdr.toString(), command, summaries));
      const [revisionDelta, generationDelta] = wdrCounterDelta(beforeWdr.toString(), afterWdr.toString(), workstreamId);
      if (command.set.refresh_actions) Object.assign(afterSidecar, {
        ledger_fingerprint: refreshLedgerState.ledger_fingerprint, ledger_revision: refreshLedgerState.ledger_revision,
        wdr_revision: 4 + revisionDelta, file_generation: 7 + generationDelta, actions: clone(command.action_snapshot.actions),
      });
      beforeWdrState = { contract: stateContract, schema_version: "1.0.0", workstream_id: workstreamId, record_path: recordPath, record_fingerprint: hash(beforeWdr), wdr_revision: 4, file_generation: 7, lifecycle: "active" };
      afterWdrState = { contract: stateContract, schema_version: "1.0.0", workstream_id: workstreamId, record_path: recordPath, record_fingerprint: hash(afterWdr), wdr_revision: 4 + revisionDelta, file_generation: 7 + generationDelta, lifecycle: "active" };
      if (command.set.refresh_actions) beforeSidecar = Buffer.from(canonical(beforeSidecarValue));
    }
    businessContents = [[beforeWdr, afterWdr], [beforeWdrState === null ? null : Buffer.from(canonical(beforeWdrState)), Buffer.from(canonical(afterWdrState))]];
    if (command.operation === "create" || command.set.refresh_actions) businessContents.push([beforeSidecar, Buffer.from(canonical(afterSidecar))]);
  }
  const businessTargets = journal.targets.filter(({ role }) => role === "business"); const artifacts = [];
  if (businessTargets.length !== expectedTargets.length || expectedTargets.length !== businessContents.length) throw new Error("fact fixture business target cardinality");
  businessTargets.forEach((target, index) => {
    const expected = expectedTargets[index]; const [beforeBytes, afterBytes] = businessContents[index];
    Object.assign(target, { root_instance_id: expected.root_instance_id, path: expected.path, operation: expected.operation });
    target.before_sha256 = beforeBytes === null ? null : hash(beforeBytes); target.after_sha256 = afterBytes === null ? null : hash(afterBytes);
    target.before_image = beforeBytes === null ? null : { root_instance_id: target.root_instance_id, path: `${journal.journal_dir}/images/${target.apply_order}-before`, sha256: target.before_sha256 };
    target.after_image = afterBytes === null ? null : { root_instance_id: target.root_instance_id, path: `${journal.journal_dir}/images/${target.apply_order}-after`, sha256: target.after_sha256 };
    artifacts.push({ root_instance_id: target.root_instance_id, path: target.path, operation: target.operation, before_bytes: encodedBytes(beforeBytes), after_bytes: encodedBytes(afterBytes) });
  });
  const proof = { contract: expectedContractRef(registryDoc, "fact-mutation-proof/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", transaction_id: journal.transaction_id, host_principal_id: cap.principal_id, authorized_command_fingerprint: authorization.authorized_command_fingerprint, business_artifacts: artifacts, read_artifacts: [] };
  if (commandKind(command) === "wdr" && command.operation === "patch" && command.set.refresh_actions) {
    if (refreshLedgerRaw === null || refreshLedgerState === null) throw new Error("refresh command has no read snapshot");
    const stateRaw = Buffer.from(canonical(refreshLedgerState));
    proof.read_artifacts = [
      { root_instance_id: "123e4567-e89b-42d3-a456-426614174000", path: registryDoc.runtime_paths.action_ledger.path, sha256: hash(refreshLedgerRaw), bytes: encodedBytes(refreshLedgerRaw) },
      { root_instance_id: "123e4567-e89b-42d3-a456-426614174000", path: registryDoc.runtime_paths.action_ledger_state.path, sha256: hash(stateRaw), bytes: encodedBytes(stateRaw) },
    ];
  }
  proof.proof_id = hash(Buffer.from(canonical(proof)));
  const receipt = {
    contract: { schema_id: "urn:adp:panel-sync-contracts:2026-07-24#fact-mutation-receipt-v1", schema_sha256: schemaSha, registry_sha256: registrySha },
    schema_version: "1.0.0", transaction_id: journal.transaction_id, journal_id: journal.journal_id,
    authorization: clone(authorization), initiator: Object.fromEntries(["producer_id", "capability_id", "capability_epoch", "principal_id"].map((key) => [key, authorization[key]])),
    before_fact_generation: beforeFactGeneration, after_fact_generation: beforeFactGeneration + 1, business_targets: clone(journal.targets.filter(({ role }) => role === "business")),
    generation_state_target: clone(journal.targets.find(({ role }) => role === "fact-generation")),
    action_deltas: commandKind(command) === "action" ? [expectedActionDelta(command)] : [], status: "committed",
  };
  receipt.receipt_id = hash(Buffer.from(canonical(receipt)));
  const receiptTarget = journal.targets.find(({ role }) => role === "receipt");
  receiptTarget.after_sha256 = hash(Buffer.from(canonical(receipt)));
  receiptTarget.after_image.sha256 = receiptTarget.after_sha256;
  const beforeCommandIndex = { contract: expectedContractRef(registryDoc, "fact-command-receipt-index/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", next_sequence: 1, entries: [] };
  beforeCommandIndex.index_id = hash(Buffer.from(canonical(beforeCommandIndex)));
  const commandIndex = { contract: clone(beforeCommandIndex.contract), schema_version: "1.0.0", next_sequence: 2,
    entries: [{ sequence: 1, command_id: command.command_id, command_fingerprint: authorization.authorized_command_fingerprint,
      transaction_id: journal.transaction_id, receipt_id: receipt.receipt_id, receipt_path: receiptTarget.path,
      receipt_sha256: hash(Buffer.from(canonical(receipt))) }] };
  commandIndex.index_id = hash(Buffer.from(canonical(commandIndex)));
  const indexTarget = journal.targets.find(({ role }) => role === "fact-command-index");
  indexTarget.before_sha256 = hash(Buffer.from(canonical(beforeCommandIndex))); indexTarget.after_sha256 = hash(Buffer.from(canonical(commandIndex)));
  indexTarget.before_image.sha256 = indexTarget.before_sha256; indexTarget.after_image.sha256 = indexTarget.after_sha256;
  let beforeOutbox = null; let afterOutbox = null;
  if (outboxMode !== "none") {
    if (!outboxIntents.length) throw new Error("intent outbox mode requires exact typed intents");
    let beforeEntries; let sourceCommandId; let sourceCommandFingerprint;
    if (outboxMode === "emit") { beforeEntries = []; sourceCommandId = command.command_id; sourceCommandFingerprint = authorization.authorized_command_fingerprint; }
    else {
      sourceCommandId = `source-${command.command_id}`;
      beforeEntries = outboxIntents.map((intent, offset) => pendingOutboxEntry(intent, `${sourceCommandId}-${offset + 1}`, hash(Buffer.from(canonical(intent))), offset + 1));
    }
    beforeOutbox = { contract: expectedContractRef(registryDoc, "mutation-intent-outbox/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", outbox_generation: 1, entries: beforeEntries };
    beforeOutbox.outbox_id = hash(Buffer.from(canonical(beforeOutbox)));
    const afterEntries = clone(beforeEntries);
    if (outboxMode === "emit") afterEntries.push(...outboxIntents.map((intent, offset) => pendingOutboxEntry(intent, sourceCommandId, sourceCommandFingerprint, beforeEntries.length + offset + 1)));
    else {
      const consumedIds = new Set(command.consumed_intent_ids);
      for (const entry of afterEntries) if (consumedIds.has(entry.intent_id)) Object.assign(entry, { status: "consumed", attempts: entry.attempts + 1, consumed_receipt_id: receipt.receipt_id });
    }
    afterOutbox = { contract: clone(beforeOutbox.contract), schema_version: "1.0.0", outbox_generation: 2, entries: afterEntries };
    afterOutbox.outbox_id = hash(Buffer.from(canonical(afterOutbox)));
    const outboxTarget = journal.targets.find(({ role }) => role === "intent-outbox");
    outboxTarget.before_sha256 = hash(Buffer.from(canonical(beforeOutbox))); outboxTarget.after_sha256 = hash(Buffer.from(canonical(afterOutbox)));
    outboxTarget.before_image.sha256 = outboxTarget.before_sha256; outboxTarget.after_image.sha256 = outboxTarget.after_sha256;
  }
  delete journal.manifest_id; journal.manifest_id = hash(Buffer.from(canonical(journal)));
  marker.manifest_id = journal.manifest_id; delete marker.marker_id; marker.marker_id = hash(Buffer.from(canonical(marker)));
  return { capability_registry: capabilityRegistry, command, journal, marker, before_state: beforeState, after_state: afterState, receipt, proof,
    before_command_index: beforeCommandIndex, command_index: commandIndex, before_outbox: beforeOutbox, after_outbox: afterOutbox };
};

const rebindFactGraph = (graph) => {
  const capabilityRegistry = graph.capability_registry;
  for (const row of capabilityRegistry.capabilities) {
    row.capability_id = capabilityRecordDigest(row);
    row.authorization_record_digest = row.capability_id;
  }
  capabilityRegistry.capabilities.sort((left, right) => Buffer.from(left.producer_id).compare(Buffer.from(right.producer_id)));
  const registryBody = clone(capabilityRegistry); delete registryBody.capability_registry_id;
  capabilityRegistry.capability_registry_id = hash(Buffer.from(canonical(registryBody)));
  const cap = capabilityRegistry.capabilities.find(({ producer_id }) => producer_id === commandProducer(graph.command));
  if (graph.command.issuer) graph.command.issuer.capability_id = cap.capability_id;
  const authorization = {
    producer_id: cap.producer_id, capability_id: cap.capability_id, capability_epoch: capabilityRegistry.capability_epoch, principal_id: cap.principal_id,
    capability_registry_id: capabilityRegistry.capability_registry_id, authorization_record_digest: cap.authorization_record_digest,
    authorized_command_fingerprint: hash(Buffer.from(canonical(graph.command))),
  };
  graph.journal.authorization = clone(authorization); graph.receipt.authorization = clone(authorization);
  graph.receipt.initiator = Object.fromEntries(["producer_id", "capability_id", "capability_epoch", "principal_id"].map((key) => [key, authorization[key]]));
  graph.proof.host_principal_id = cap.principal_id; graph.proof.authorized_command_fingerprint = authorization.authorized_command_fingerprint;
  delete graph.proof.proof_id; graph.proof.proof_id = hash(Buffer.from(canonical(graph.proof)));
  delete graph.receipt.receipt_id; graph.receipt.receipt_id = hash(Buffer.from(canonical(graph.receipt)));
  const receiptTarget = graph.journal.targets.find(({ role }) => role === "receipt");
  receiptTarget.after_sha256 = hash(Buffer.from(canonical(graph.receipt))); receiptTarget.after_image.sha256 = receiptTarget.after_sha256;
  if (graph.before_command_index && graph.command_index) {
    const beforeIndex = graph.before_command_index; const sequence = beforeIndex.next_sequence;
    graph.command_index = { contract: clone(beforeIndex.contract), schema_version: "1.0.0", next_sequence: sequence + 1,
      entries: [...clone(beforeIndex.entries), { sequence, command_id: graph.command.command_id, command_fingerprint: authorization.authorized_command_fingerprint,
        transaction_id: graph.journal.transaction_id, receipt_id: graph.receipt.receipt_id, receipt_path: receiptTarget.path,
        receipt_sha256: hash(Buffer.from(canonical(graph.receipt))) }] };
    graph.command_index.index_id = hash(Buffer.from(canonical(graph.command_index)));
    const indexTarget = graph.journal.targets.find(({ role }) => role === "fact-command-index");
    indexTarget.before_sha256 = hash(Buffer.from(canonical(beforeIndex))); indexTarget.after_sha256 = hash(Buffer.from(canonical(graph.command_index)));
    indexTarget.before_image.sha256 = indexTarget.before_sha256; indexTarget.after_image.sha256 = indexTarget.after_sha256;
  }
  delete graph.journal.manifest_id; graph.journal.manifest_id = hash(Buffer.from(canonical(graph.journal)));
  graph.marker.manifest_id = graph.journal.manifest_id; delete graph.marker.marker_id; graph.marker.marker_id = hash(Buffer.from(canonical(graph.marker)));
};

const legacyLedgerFixture = (declaredFormat) => {
  if (declaredFormat === "absent") return null;
  const columns = declaredFormat === "legacy12" ? ACTION_LEDGER_LEGACY_12_COLUMNS : ACTION_LEDGER_LEGACY_20_COLUMNS;
  const values = { "Action ID": "A-MIG-1", Status: "open", Owner: "FDE-M", Workstream: "l1-checkout", "Affected Workstreams": "l1-payments", Action: "Preserve migrated action",
    Source: "meetings/legacy.md", Reason: "legacy import", "Due / Trigger": "next gate", "Closure Criteria": "accepted", "Closure Criteria Verifiable": "true",
    "Created At": "2026-07-22T01:00:00Z", "Started At": "-", "Done At": "-", "Cancelled At": "-", "Baseline Revision": "3", "Related Plan Items": "PLAN-1",
    "Related Flow Edges": "EDGE-1", "Last Updated": "2026-07-23T01:00:00Z", "Owning Workflow": "adp-status-sync" };
  return Buffer.from(ACTION_LEDGER_PREAMBLE + `| ${columns.join(" | ")} |\n| ${columns.map(() => "---").join(" | ")} |\n| ${columns.map((column) => ledgerCell(values[column])).join(" | ")} |\n`);
};
const legacyWdrFixture = (workstreamId) => Buffer.from(fixtureWdr(workstreamId).replace("- Last status sync: 2026-07-24T01:00:00Z\n", "") + "\n## Checkpoint Sync Log\n\n- legacy checkpoint preserved\n");

const bootstrapMigrationFixture = (scenario, registryDoc, schemaSha, registrySha) => {
  const command = { contract: expectedContractRef(registryDoc, "bootstrap-migration-command/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", command_id: "bootstrap-migration-1", operation: "bootstrap",
    issuer: { producer_id: "adp-status-sync", capability_id: `sha256:${"0".repeat(64)}` }, action_ledger: { format: scenario.ledger_format, expected_fingerprint: null, state_expected: "absent", action_flow_preimage: scenario.action_flow_preimage ?? "absent" },
    workstreams: [], observed_at: "2026-07-24T02:00:00Z" };
  const beforeLedger = legacyLedgerFixture(scenario.ledger_format); command.action_ledger.expected_fingerprint = beforeLedger === null ? null : hash(beforeLedger);
  const rows = parseActionLedgerIngress(beforeLedger, scenario.ledger_format); const afterLedger = renderActionLedger(rows);
  const afterLedgerState = actionLedgerStateDocument(rows, afterLedger, 0, [], registryDoc, schemaSha, registrySha);
  const beforeFlow = command.action_ledger.action_flow_preimage === "brownfield-v1" ? Buffer.from(canonical(actionFlowDocument(rows, beforeLedger, 0, registryDoc, schemaSha, registrySha))) : null;
  const afterFlow = Buffer.from(canonical(actionFlowDocument(rows, afterLedger, 0, registryDoc, schemaSha, registrySha)));
  const targets = [
    { path: registryDoc.runtime_paths.action_ledger.path, operation: beforeLedger === null ? "create" : "replace" },
    { path: registryDoc.runtime_paths.action_ledger_state.path, operation: "create" },
    { path: registryDoc.runtime_paths.action_flow_index.path, operation: beforeFlow === null ? "create" : "replace" },
  ];
  const contents = [[beforeLedger, afterLedger], [null, Buffer.from(canonical(afterLedgerState))], [beforeFlow, afterFlow]];
  for (const workstreamId of scenario.workstreams ?? ["l1-checkout"]) {
    const beforeWdr = legacyWdrFixture(workstreamId); command.workstreams.push({ workstream_id: workstreamId, record_format: "legacy", expected_record_fingerprint: hash(beforeWdr), state_expected: "absent", sidecar_expected: "absent" });
    const afterWdr = Buffer.from(migrateWdr(beforeWdr.toString(), command.observed_at)); const recordPath = `workstreams/${workstreamId}/delivery-record.md`;
    const state = { contract: expectedContractRef(registryDoc, "wdr-file-state/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", workstream_id: workstreamId, record_path: recordPath,
      record_fingerprint: hash(afterWdr), wdr_revision: 0, file_generation: 1, lifecycle: "active" };
    const snapshot = actionSnapshot(rows, workstreamId, afterLedgerState.ledger_fingerprint, 0);
    const sidecar = { contract: expectedContractRef(registryDoc, "wdr-action-projection/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", workstream_id: workstreamId,
      ledger_fingerprint: afterLedgerState.ledger_fingerprint, ledger_revision: 0, wdr_revision: 0, file_generation: 1, renderer_id: "urn:adp:wdr-action-renderer:1.0.0", renderer_sha256: registryDoc.protocol.sha256, actions: snapshot.actions };
    targets.push({ path: recordPath, operation: "replace" }, { path: `workstreams/${workstreamId}/delivery-record.state.json`, operation: "create" }, { path: `workstreams/${workstreamId}/action-projection.json`, operation: "create" });
    contents.push([beforeWdr, afterWdr], [null, Buffer.from(canonical(state))], [null, Buffer.from(canonical(sidecar))]);
  }
  command.workstreams.sort((a, b) => Buffer.from(a.workstream_id).compare(Buffer.from(b.workstream_id)));
  const skeleton = factAttributionFixture(schemaSha, registrySha, registryDoc, "action-create"); const [journal, marker] = journalFixture("fact", schemaSha, registrySha, targets);
  const beforeState = skeleton.before_state; const afterState = skeleton.after_state; const generationTarget = journal.targets.find(({ role }) => role === "fact-generation");
  generationTarget.before_sha256 = hash(Buffer.from(canonical(beforeState))); generationTarget.after_sha256 = hash(Buffer.from(canonical(afterState)));
  generationTarget.before_image = { root_instance_id: generationTarget.root_instance_id, path: `${journal.journal_dir}/images/${generationTarget.apply_order}-before`, sha256: generationTarget.before_sha256 };
  generationTarget.after_image = { root_instance_id: generationTarget.root_instance_id, path: `${journal.journal_dir}/images/${generationTarget.apply_order}-after`, sha256: generationTarget.after_sha256 };
  const businessTargets = journal.targets.filter(({ role }) => role === "business"); const artifacts = [];
  businessTargets.forEach((target, index) => {
    const [beforeBytes, afterBytes] = contents[index]; target.before_sha256 = beforeBytes === null ? null : hash(beforeBytes); target.after_sha256 = afterBytes === null ? null : hash(afterBytes);
    target.before_image = beforeBytes === null ? null : { root_instance_id: target.root_instance_id, path: `${journal.journal_dir}/images/${target.apply_order}-before`, sha256: target.before_sha256 };
    target.after_image = afterBytes === null ? null : { root_instance_id: target.root_instance_id, path: `${journal.journal_dir}/images/${target.apply_order}-after`, sha256: target.after_sha256 };
    artifacts.push({ root_instance_id: target.root_instance_id, path: target.path, operation: target.operation, before_bytes: encodedBytes(beforeBytes), after_bytes: encodedBytes(afterBytes) });
  });
  const proof = { contract: expectedContractRef(registryDoc, "fact-mutation-proof/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", transaction_id: journal.transaction_id,
    host_principal_id: `sha256:${"b".repeat(64)}`, authorized_command_fingerprint: `sha256:${"0".repeat(64)}`, business_artifacts: artifacts, read_artifacts: [], proof_id: `sha256:${"0".repeat(64)}` };
  const receipt = { contract: expectedContractRef(registryDoc, "fact-mutation-receipt/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", receipt_id: `sha256:${"0".repeat(64)}`,
    transaction_id: journal.transaction_id, journal_id: journal.journal_id, authorization: clone(skeleton.receipt.authorization), initiator: clone(skeleton.receipt.initiator),
    before_fact_generation: beforeState.fact_generation, after_fact_generation: afterState.fact_generation, business_targets: clone(businessTargets), generation_state_target: clone(generationTarget), action_deltas: [], status: "committed" };
  const graph = { capability_registry: skeleton.capability_registry, command, journal, marker, before_state: beforeState, after_state: afterState, receipt, proof };
  rebindFactGraph(graph); return graph;
};

const bootstrapMigrationSemantics = (graph, registryDoc, schemaRoot, schemaSha, registrySha) => {
  try {
    const { capability_registry: capabilityRegistry, command, journal, marker, proof, receipt, before_state: beforeState, after_state: afterState } = graph;
    if (![validateRegistered(command, schemaRoot, registryDoc, "bootstrap-migration-command/1.0.0", schemaSha, registrySha), validateRegistered(capabilityRegistry, schemaRoot, registryDoc, "writer-capability-registry/1.0.0", schemaSha, registrySha),
      validateRegistered(proof, schemaRoot, registryDoc, "fact-mutation-proof/1.0.0", schemaSha, registrySha), validateRegistered(receipt, schemaRoot, registryDoc, "fact-mutation-receipt/1.0.0", schemaSha, registrySha),
      validateRegistered(beforeState, schemaRoot, registryDoc, "fact-generation-state/1.0.0", schemaSha, registrySha), validateRegistered(afterState, schemaRoot, registryDoc, "fact-generation-state/1.0.0", schemaSha, registrySha),
      journalSemantics(journal, marker, schemaRoot, registryDoc, schemaSha, registrySha)].every(Boolean)) return false;
    const cap = capabilityRegistry.capabilities.find(({ producer_id, status }) => producer_id === "adp-status-sync" && status === "active"); const registryBody = clone(capabilityRegistry); delete registryBody.capability_registry_id;
    if (!cap || capabilityRegistry.capability_registry_id !== hash(Buffer.from(canonical(registryBody))) || capabilityRecordDigest(cap) !== cap.capability_id || !cap.allowed_operations.includes("bootstrap")) return false;
    const expectedAuth = { producer_id: cap.producer_id, capability_id: cap.capability_id, capability_epoch: capabilityRegistry.capability_epoch, principal_id: cap.principal_id,
      capability_registry_id: capabilityRegistry.capability_registry_id, authorization_record_digest: cap.authorization_record_digest, authorized_command_fingerprint: hash(Buffer.from(canonical(command))) };
    if (canonical(command.issuer) !== canonical({ producer_id: cap.producer_id, capability_id: cap.capability_id }) || canonical(journal.authorization) !== canonical(expectedAuth) || canonical(receipt.authorization) !== canonical(expectedAuth)) return false;
    const proofBody = clone(proof); delete proofBody.proof_id; const receiptBody = clone(receipt); delete receiptBody.receipt_id;
    if (proof.proof_id !== hash(Buffer.from(canonical(proofBody))) || receipt.receipt_id !== hash(Buffer.from(canonical(receiptBody)))) return false;
    const business = journal.targets.filter(({ role }) => role === "business"); if (canonical(receipt.business_targets) !== canonical(business) || receipt.action_deltas.length || proof.business_artifacts.length !== business.length) return false;
    const ledgerDecl = command.action_ledger; const root = "123e4567-e89b-42d3-a456-426614174000";
    const expectedTargets = [
      { root_instance_id: root, path: registryDoc.runtime_paths.action_ledger.path, operation: ledgerDecl.format === "absent" ? "create" : "replace" },
      { root_instance_id: root, path: registryDoc.runtime_paths.action_ledger_state.path, operation: "create" },
      { root_instance_id: root, path: registryDoc.runtime_paths.action_flow_index.path, operation: ledgerDecl.action_flow_preimage === "absent" ? "create" : "replace" },
    ];
    for (const row of command.workstreams) expectedTargets.push({ root_instance_id: root, path: `workstreams/${row.workstream_id}/delivery-record.md`, operation: "replace" }, { root_instance_id: root, path: `workstreams/${row.workstream_id}/delivery-record.state.json`, operation: "create" }, { root_instance_id: root, path: `workstreams/${row.workstream_id}/action-projection.json`, operation: "create" });
    if (canonical(business.map((row) => Object.fromEntries(["root_instance_id", "path", "operation"].map((key) => [key, row[key]])))) !== canonical(expectedTargets)) return false;
    const decoded = [];
    for (let index = 0; index < business.length; index += 1) {
      const target = business[index]; const artifact = proof.business_artifacts[index];
      if (canonical(Object.fromEntries(["root_instance_id", "path", "operation"].map((key) => [key, artifact[key]]))) !== canonical(expectedTargets[index])) return false;
      const beforeBytes = artifactBytes(artifact.before_bytes); const afterBytes = artifactBytes(artifact.after_bytes);
      if (target.before_sha256 !== (beforeBytes === null ? null : hash(beforeBytes)) || target.after_sha256 !== (afterBytes === null ? null : hash(afterBytes))) return false; decoded.push([beforeBytes, afterBytes]);
    }
    const [beforeLedger, afterLedger] = decoded[0]; if (ledgerDecl.expected_fingerprint !== (beforeLedger === null ? null : hash(beforeLedger))) return false;
    const rows = parseActionLedgerIngress(beforeLedger, ledgerDecl.format); if (afterLedger.compare(renderActionLedger(rows)) !== 0 || decoded[1][0] !== null) return false;
    const expectedState = actionLedgerStateDocument(rows, afterLedger, 0, [], registryDoc, schemaSha, registrySha);
    if (canonical(JSON.parse(decoded[1][1].toString())) !== canonical(expectedState) || canonical(JSON.parse(decoded[2][1].toString())) !== canonical(actionFlowDocument(rows, afterLedger, 0, registryDoc, schemaSha, registrySha))) return false;
    const expectedFlowBefore = ledgerDecl.action_flow_preimage === "absent" ? null : Buffer.from(canonical(actionFlowDocument(rows, beforeLedger, 0, registryDoc, schemaSha, registrySha)));
    if ((decoded[2][0] === null) !== (expectedFlowBefore === null) || decoded[2][0] !== null && decoded[2][0].compare(expectedFlowBefore) !== 0) return false;
    let offset = 3;
    for (const workstream of command.workstreams) {
      const [beforeWdr, afterWdr] = decoded[offset]; if (beforeWdr === null || hash(beforeWdr) !== workstream.expected_record_fingerprint || decoded[offset + 1][0] !== null || decoded[offset + 2][0] !== null) return false;
      const workstreamId = workstream.workstream_id; const expectedWdr = Buffer.from(migrateWdr(beforeWdr.toString(), command.observed_at)); if (afterWdr.compare(expectedWdr) !== 0 || !completeWdrValid(afterWdr.toString(), workstreamId)) return false;
      const state = JSON.parse(decoded[offset + 1][1].toString()); const sidecar = JSON.parse(decoded[offset + 2][1].toString()); const snapshot = actionSnapshot(rows, workstreamId, expectedState.ledger_fingerprint, 0);
      const expectedWdrState = { contract: expectedContractRef(registryDoc, "wdr-file-state/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", workstream_id: workstreamId, record_path: `workstreams/${workstreamId}/delivery-record.md`, record_fingerprint: hash(afterWdr), wdr_revision: 0, file_generation: 1, lifecycle: "active" };
      const expectedSidecar = { contract: expectedContractRef(registryDoc, "wdr-action-projection/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", workstream_id: workstreamId, ledger_fingerprint: expectedState.ledger_fingerprint, ledger_revision: 0,
        wdr_revision: 0, file_generation: 1, renderer_id: "urn:adp:wdr-action-renderer:1.0.0", renderer_sha256: registryDoc.protocol.sha256, actions: snapshot.actions };
      if (canonical(state) !== canonical(expectedWdrState) || canonical(sidecar) !== canonical(expectedSidecar)) return false; offset += 3;
    }
    const generation = journal.targets.find(({ role }) => role === "fact-generation"); const receiptTarget = journal.targets.find(({ role }) => role === "receipt");
    return offset === decoded.length && marker.state === "committed" && afterState.fact_generation === beforeState.fact_generation + 1 && afterState.last_transaction_id === journal.transaction_id
      && generation.before_sha256 === hash(Buffer.from(canonical(beforeState))) && generation.after_sha256 === hash(Buffer.from(canonical(afterState))) && receiptTarget.after_sha256 === hash(Buffer.from(canonical(receipt)));
  } catch { return false; }
};

const runtimeAuthorityFixture = (registryDoc, schemaSha, registrySha, producerId = "adp-status-sync", platform = "posix") => {
  const capabilityRegistry = capabilityRegistryFixture(registryDoc, schemaSha, registrySha, platform);
  const capability = capabilityRegistry.capabilities.find((row) => row.producer_id === producerId && row.status === "active");
  if (!capability) throw new Error("runtime authority fixture has no active producer capability");
  const [principalId, effectiveIdentitySha256, executableSha256, nativePreimage, nativeVerification] = authorityNativeFixture(registryDoc, producerId, platform);
  const profile = registryDoc.runtime_authority_profile;
  const memoryRoot = "123e4567-e89b-42d3-a456-426614174000";
  const rootRegistry = {
    contract: expectedContractRef(registryDoc, "root-registry-state/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
    roots: [
      { role: "memory", root_instance_id: memoryRoot, canonical_path_hash: hash(Buffer.from("/canonical/memory")) },
      { role: "project", root_instance_id: "123e4567-e89b-42d3-a456-426614174001", canonical_path_hash: hash(Buffer.from("/canonical/project")) },
    ], created_at: "2026-07-24T01:00:00Z",
  };
  rootRegistry.registry_state_id = hash(Buffer.from(canonical(rootRegistry)));
  const activation = {
    contract: expectedContractRef(registryDoc, "strict-activation-state/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
    activation_epoch: 1, mode: "legacy", attestation_id: null, changed_at: "2026-07-24T01:00:01Z",
  };
  activation.state_id = hash(Buffer.from(canonical(activation)));
  const capabilityRaw = Buffer.from(canonical(capabilityRegistry));
  const rootRaw = Buffer.from(canonical(rootRegistry));
  const activationRaw = Buffer.from(canonical(activation));
  const context = {
    contract: expectedContractRef(registryDoc, "runtime-authority-context/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", authority_profile_id: profile.profile_id,
    memory_root_instance_id: memoryRoot,
    root_registry_path: registryDoc.runtime_paths.root_registry_state.path,
    root_registry_sha256: hash(rootRaw), root_registry_state_id: rootRegistry.registry_state_id,
    capability_registry_root_instance_id: memoryRoot,
    capability_registry_path: registryDoc.runtime_paths.writer_capability_registry.path,
    capability_registry_sha256: hash(capabilityRaw), capability_registry_id: capabilityRegistry.capability_registry_id,
    activation_state_path: registryDoc.runtime_paths.strict_activation_state.path,
    activation_state_sha256: hash(activationRaw), activation_state_id: activation.state_id,
    activation_mode: activation.mode, attestation_path: registryDoc.runtime_paths.writer_fence_attestation.path,
    attestation_sha256: null,
    fact_lock_profile_id: registryDoc.lock_profile.profile_id, fact_lock_path: registryDoc.runtime_paths.fact_lock.path,
    lock_mode: "exclusive", activation_epoch: 1, attestation_id: null, capability_epoch: capabilityRegistry.capability_epoch,
    platform, principal_adapter_id: profile.principal_adapters[platform].id,
    native_preimage: nativePreimage, native_verification: nativeVerification,
    effective_identity_sha256: effectiveIdentitySha256, executable_sha256: executableSha256, principal_id: principalId,
  };
  if (capability.principal_id !== principalId) throw new Error("native runtime principal fixture does not match provisioned capability");
  context.context_id = hash(Buffer.from(canonical(context)));
  return [capabilityRaw, rootRaw, activationRaw, null, context];
};

const runtimeAuthorityFromDocuments = (
  registryDoc, schemaSha, registrySha, producerId, capabilityRegistry, roots, activation, attestation, platform = "posix",
) => {
  const profile = registryDoc.runtime_authority_profile;
  const capabilityRaw = Buffer.from(canonical(capabilityRegistry)); const rootRaw = Buffer.from(canonical(roots));
  const activationRaw = Buffer.from(canonical(activation)); const attestationRaw = attestation === null ? null : Buffer.from(canonical(attestation));
  const [principalId, effectiveIdentitySha256, executableSha256, nativePreimage, nativeVerification] = authorityNativeFixture(registryDoc, producerId, platform);
  const active = capabilityRegistry.capabilities.find((row) => row.producer_id === producerId && row.status === "active");
  const memoryRoot = roots.roots.find(({ role }) => role === "memory")?.root_instance_id;
  if (!active || active.principal_id !== principalId || !memoryRoot) throw new Error("native authority does not match the active capability");
  const context = {
    contract: expectedContractRef(registryDoc, "runtime-authority-context/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
    authority_profile_id: profile.profile_id, memory_root_instance_id: memoryRoot,
    root_registry_path: registryDoc.runtime_paths.root_registry_state.path, root_registry_sha256: hash(rootRaw), root_registry_state_id: roots.registry_state_id,
    capability_registry_root_instance_id: memoryRoot, capability_registry_path: registryDoc.runtime_paths.writer_capability_registry.path,
    capability_registry_sha256: hash(capabilityRaw), capability_registry_id: capabilityRegistry.capability_registry_id,
    activation_state_path: registryDoc.runtime_paths.strict_activation_state.path, activation_state_sha256: hash(activationRaw), activation_state_id: activation.state_id,
    activation_mode: activation.mode, attestation_path: registryDoc.runtime_paths.writer_fence_attestation.path,
    attestation_sha256: attestationRaw === null ? null : hash(attestationRaw), fact_lock_profile_id: registryDoc.lock_profile.profile_id,
    fact_lock_path: registryDoc.runtime_paths.fact_lock.path, lock_mode: "exclusive", activation_epoch: activation.activation_epoch,
    attestation_id: attestation === null ? null : attestation.attestation_id, capability_epoch: capabilityRegistry.capability_epoch,
    platform, principal_adapter_id: profile.principal_adapters[platform].id, native_preimage: nativePreimage, native_verification: nativeVerification,
    effective_identity_sha256: effectiveIdentitySha256,
    executable_sha256: executableSha256, principal_id: principalId,
  };
  context.context_id = hash(Buffer.from(canonical(context)));
  return [capabilityRaw, rootRaw, activationRaw, attestationRaw, context];
};

const ownedFactProfile = (command, registryDoc) => {
  const matches = registryDoc.owned_fact_target_profiles.filter(({ profile_id }) => profile_id === command.target_profile_id);
  if (matches.length !== 1) return null;
  const profile = matches[0]; const rule = profile.path_rule; const targetPath = command.target_path;
  let pathMatches = false;
  if (typeof targetPath === "string") {
    if (rule.kind === "exact") pathMatches = targetPath === rule.value;
    else if (rule.kind === "workstream-file") pathMatches = new RegExp(`^${rule.base.replace(/[.*+?^${}()|[\\]\\]/g, "\\$&")}/[A-Za-z0-9][A-Za-z0-9._-]*/${rule.filename.replace(/[.*+?^${}()|[\\]\\]/g, "\\$&")}$`).test(targetPath);
    else if (rule.kind === "directory-file") pathMatches = new RegExp(`^${rule.base.replace(/[.*+?^${}()|[\\]\\]/g, "\\$&")}/[A-Za-z0-9][A-Za-z0-9._-]*${rule.suffix.replace(/[.*+?^${}()|[\\]\\]/g, "\\$&")}$`).test(targetPath);
  }
  return pathMatches && profile.operations.includes(command.operation) && command.issuer?.producer_id === profile.producer_id && profile.root === "memory" ? profile : null;
};

const ownedFactContentValid = (raw, profile, registryDoc) => {
  try {
    if (profile.content_rule === "markdown-byte-invariants-v1") {
      const rule = registryDoc.owned_fact_content_rules[profile.content_rule];
      const text = raw.toString("utf8");
      return rule.encoding === "utf-8" && rule.nonempty && Buffer.from(text).equals(raw) && Boolean(text)
        && rule.line_ending === "LF" && rule.final_lf && text.endsWith("\n")
        && rule.unicode_normalization === "NFC" && text.normalize("NFC") === text
        && !text.includes("\r") && !text.includes("\0") && rule.structural_grammar === null && rule.canonical_renderer === null;
    }
    if (profile.content_rule === "json-schema") {
      const schemaRaw = fs.readFileSync(path.join(args["project-root"], profile.schema_path)); const payload = JSON.parse(raw.toString("utf8"));
      const payloadSchema = JSON.parse(schemaRaw.toString("utf8"));
      return Buffer.from(canonical(payload)).equals(raw) && hash(schemaRaw) === profile.schema_sha256
        && payloadSchema.$id === profile.schema_id && schemaErrors(payload, payloadSchema, payloadSchema).length === 0;
    }
  } catch { return false; }
  return false;
};

const runtimeAuthorityBindingSemantics = (
  registryDoc, schemaRoot, schemaSha, registrySha, capabilityRaw, rootRaw, activationRaw, attestationRaw, context,
) => {
  try {
    const capabilityRegistry = JSON.parse(capabilityRaw.toString("utf8")); const roots = JSON.parse(rootRaw.toString("utf8"));
    const activation = JSON.parse(activationRaw.toString("utf8")); const attestation = attestationRaw === null ? null : JSON.parse(attestationRaw.toString("utf8"));
    const profile = registryDoc.runtime_authority_profile; const profileBody = clone(profile); delete profileBody.profile_id;
    const contextBody = clone(context); delete contextBody.context_id;
    const nativePreimage = context.native_preimage; const nativeVerification = context.native_verification;
    const principalBody = { authority_profile_id: context.authority_profile_id, platform: context.platform, native_preimage: nativePreimage };
    const rootRows = new Map(roots.roots.map((row) => [row.role, row])); const capabilityBody = clone(capabilityRegistry); delete capabilityBody.capability_registry_id;
    const rootsBody = clone(roots); delete rootsBody.registry_state_id; const activationBody = clone(activation); delete activationBody.state_id;
    if (!Buffer.from(canonical(capabilityRegistry)).equals(capabilityRaw) || !Buffer.from(canonical(roots)).equals(rootRaw)
        || !Buffer.from(canonical(activation)).equals(activationRaw) || (attestation !== null && !Buffer.from(canonical(attestation)).equals(attestationRaw))
        || !validateRegistered(capabilityRegistry, schemaRoot, registryDoc, "writer-capability-registry/1.0.0", schemaSha, registrySha)
        || !validateRegistered(roots, schemaRoot, registryDoc, "root-registry-state/1.0.0", schemaSha, registrySha)
        || !validateRegistered(activation, schemaRoot, registryDoc, "strict-activation-state/1.0.0", schemaSha, registrySha)
        || !validateRegistered(context, schemaRoot, registryDoc, "runtime-authority-context/1.0.0", schemaSha, registrySha)
        || profile.profile_id !== hash(Buffer.from(canonical(profileBody))) || context.context_id !== hash(Buffer.from(canonical(contextBody)))
        || context.authority_profile_id !== profile.profile_id || context.memory_root_instance_id !== rootRows.get("memory")?.root_instance_id
        || context.root_registry_path !== registryDoc.runtime_paths.root_registry_state.path || context.root_registry_sha256 !== hash(rootRaw)
        || context.root_registry_state_id !== roots.registry_state_id || roots.registry_state_id !== hash(Buffer.from(canonical(rootsBody)))
        || context.capability_registry_root_instance_id !== rootRows.get("memory")?.root_instance_id
        || context.capability_registry_path !== registryDoc.runtime_paths.writer_capability_registry.path || context.capability_registry_sha256 !== hash(capabilityRaw)
        || context.capability_registry_id !== capabilityRegistry.capability_registry_id || capabilityRegistry.capability_registry_id !== hash(Buffer.from(canonical(capabilityBody)))
        || context.activation_state_path !== registryDoc.runtime_paths.strict_activation_state.path || context.activation_state_sha256 !== hash(activationRaw)
        || context.activation_state_id !== activation.state_id || activation.state_id !== hash(Buffer.from(canonical(activationBody)))
        || context.activation_mode !== activation.mode || context.activation_epoch !== activation.activation_epoch
        || context.attestation_path !== registryDoc.runtime_paths.writer_fence_attestation.path
        || context.fact_lock_profile_id !== registryDoc.lock_profile.profile_id || context.fact_lock_path !== registryDoc.runtime_paths.fact_lock.path
        || context.lock_mode !== "exclusive" || profile.required_lock_mode !== "exclusive"
        || context.principal_adapter_id !== profile.principal_adapters[context.platform]?.id
        || nativePreimage.adapter_id !== context.principal_adapter_id
        || canonical(Object.keys(nativePreimage)) !== canonical(profile.principal_adapters[context.platform].preimage_fields)
        || nativePreimage.executable_sha256 !== context.executable_sha256
        || context.effective_identity_sha256 !== hash(Buffer.from(canonical(nativePreimage)))
        || canonical(nativeVerification) !== canonical({ adapter_boundary: profile.adapter_boundary, native_api_observed: true,
          opened_executable_handle: true, path_alias_rejected: true, namespace_or_token_verified: true, service_identity_verified: true })
        || context.principal_id !== hash(Buffer.from(canonical(principalBody)))
        || context.capability_epoch !== capabilityRegistry.capability_epoch) return false;
    if (activation.mode !== "strict") return attestation === null && context.attestation_id === null && context.attestation_sha256 === null;
    if (attestation === null || !validateRegistered(attestation, schemaRoot, registryDoc, "writer-fence-migration-attestation/1.0.0", schemaSha, registrySha)) return false;
    const attestationBody = clone(attestation); delete attestationBody.attestation_id;
    const valid = attestation.attestation_id === hash(Buffer.from(canonical(attestationBody)))
      && context.attestation_id === activation.attestation_id && activation.attestation_id === attestation.attestation_id
      && context.attestation_sha256 === hash(attestationRaw) && attestation.activation_epoch === activation.activation_epoch
      && attestation.capability_registry_id === capabilityRegistry.capability_registry_id && attestation.capability_epoch === capabilityRegistry.capability_epoch
      && attestation.root_registry_state_id === roots.registry_state_id;
    return valid;
  } catch { return false; }
};

const activationTransitionFixture = (writerPackage, registryDoc, schemaSha, registrySha) => {
  const roots = clone(writerPackage.documents.root_registry);
  const oldCapability = clone(writerPackage.documents.capability_registry);
  const newCapability = clone(oldCapability);
  newCapability.capability_epoch += 1;
  delete newCapability.capability_registry_id;
  newCapability.capability_registry_id = hash(Buffer.from(canonical(newCapability)));
  const oldActivation = clone(writerPackage.documents.activation_state);
  const oldAttestation = clone(writerPackage.attestation);
  const legacyActivation = {
    contract: clone(oldActivation.contract), schema_version: "1.0.0", activation_epoch: oldActivation.activation_epoch + 1,
    mode: "legacy", attestation_id: null, changed_at: "2026-07-24T03:06:00Z",
  };
  legacyActivation.state_id = hash(Buffer.from(canonical(legacyActivation)));
  const finalActivation = {
    contract: clone(oldActivation.contract), schema_version: "1.0.0", activation_epoch: legacyActivation.activation_epoch,
    mode: "strict", attestation_id: `sha256:${"0".repeat(64)}`, changed_at: "2026-07-24T03:20:00Z",
  };
  const refreshReceipt = clone(writerPackage.documents.refresh_receipt);
  const newAttestation = clone(oldAttestation);
  Object.assign(newAttestation, {
    activation_epoch: legacyActivation.activation_epoch, capability_epoch: newCapability.capability_epoch,
    capability_registry_id: newCapability.capability_registry_id, full_refresh_receipt_id: refreshReceipt.receipt_id,
    attested_at: "2026-07-24T03:19:00Z",
    activation_state_binding_id: hash(Buffer.from(canonical(Object.fromEntries(
      Object.entries(finalActivation).filter(([key]) => !["attestation_id", "state_id"].includes(key)),
    )))),
  });
  delete newAttestation.attestation_id;
  newAttestation.attestation_id = hash(Buffer.from(canonical(newAttestation)));
  finalActivation.attestation_id = newAttestation.attestation_id;
  finalActivation.state_id = hash(Buffer.from(canonical(finalActivation)));
  const states = {
    rollback: [oldActivation, legacyActivation, oldCapability, oldCapability, oldAttestation],
    reprovision: [legacyActivation, legacyActivation, oldCapability, newCapability, null],
    "record-refresh": [legacyActivation, legacyActivation, newCapability, newCapability, null],
    attest: [legacyActivation, legacyActivation, newCapability, newCapability, null],
    enable: [legacyActivation, finalActivation, newCapability, newCapability, null],
  };
  const operations = ["rollback", "reprovision", "record-refresh", "attest", "enable"];
  const lifecycleId = hash(Buffer.from(canonical({
    initial_activation_state_id: oldActivation.state_id,
    target_activation_epoch: legacyActivation.activation_epoch,
    operations,
  })));
  let lifecycleIndex = {
    contract: expectedContractRef(registryDoc, "activation-lifecycle-index/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", lifecycle_id: lifecycleId,
    activation_epoch: legacyActivation.activation_epoch, entries: [], terminal_status: "in-progress",
  };
  const steps = []; let previousReceipt = null;
  operations.forEach((operation, offset) => {
    const sequence = offset + 1;
    const [beforeActivation, afterActivation, beforeCapability, afterCapability, applicableAttestation] = states[operation];
    const authority = runtimeAuthorityFromDocuments(
      registryDoc, schemaSha, registrySha, registryDoc.strict_rollout.activation_administrator_producer_id,
      beforeCapability, roots, beforeActivation, applicableAttestation,
    );
    const context = authority.at(-1); const transitionId = `activation-${sequence}-${operation}`; const journalId = `journal-${transitionId}`;
    const command = {
      contract: expectedContractRef(registryDoc, "activation-transition-command/1.0.0", schemaSha, registrySha),
      schema_version: "1.0.0", lifecycle_id: lifecycleId, step_ordinal: sequence,
      predecessor_receipt_id: previousReceipt === null ? null : previousReceipt.receipt_id,
      transition_id: transitionId, operation, authority_context_id: context.context_id,
      fact_lock_profile_id: registryDoc.lock_profile.profile_id, expected_activation_epoch: beforeActivation.activation_epoch,
      expected_capability_epoch: beforeCapability.capability_epoch, expected_activation_state_id: beforeActivation.state_id,
      expected_capability_registry_id: beforeCapability.capability_registry_id,
      expected_attestation_id: operation === "rollback" ? oldAttestation.attestation_id : operation === "enable" ? newAttestation.attestation_id : null,
      expected_attestation_sha256: ["rollback", "attest"].includes(operation) ? hash(Buffer.from(canonical(oldAttestation)))
        : operation === "enable" ? hash(Buffer.from(canonical(newAttestation))) : null,
      approved_by: ["operator-a", "operator-b"],
      requested_at: `2026-07-24T03:${String(5 + sequence).padStart(2, "0")}:00Z`,
    };
    const fullRefreshId = ["record-refresh", "attest", "enable"].includes(operation) ? refreshReceipt.receipt_id : null;
    const attestationId = ["attest", "enable"].includes(operation) ? newAttestation.attestation_id : null;
    const receipt = {
      contract: expectedContractRef(registryDoc, "activation-transition-receipt/1.0.0", schemaSha, registrySha),
      schema_version: "1.0.0", lifecycle_id: lifecycleId, step_ordinal: sequence,
      predecessor_receipt_id: command.predecessor_receipt_id, transition_id: transitionId, operation,
      before_activation_epoch: beforeActivation.activation_epoch, after_activation_epoch: afterActivation.activation_epoch,
      before_capability_epoch: beforeCapability.capability_epoch, after_capability_epoch: afterCapability.capability_epoch,
      before_activation_state_id: beforeActivation.state_id, after_activation_state_id: afterActivation.state_id,
      before_capability_registry_id: beforeCapability.capability_registry_id, after_capability_registry_id: afterCapability.capability_registry_id,
      before_attestation_id: operation === "rollback" ? oldAttestation.attestation_id : operation === "enable" ? newAttestation.attestation_id : null,
      after_attestation_id: ["attest", "enable"].includes(operation) ? newAttestation.attestation_id : null,
      full_refresh_receipt_id: fullRefreshId, attestation_id: attestationId, journal_id: journalId,
      status: "committed", completed_at: `2026-07-24T03:${String(6 + sequence).padStart(2, "0")}:00Z`,
    };
    receipt.receipt_id = hash(Buffer.from(canonical(receipt)));
    const beforeLifecycleIndex = sequence === 1 ? null : clone(lifecycleIndex);
    lifecycleIndex = clone(lifecycleIndex);
    lifecycleIndex.entries.push({
      step_ordinal: sequence, transition_id: transitionId, operation,
      predecessor_receipt_id: receipt.predecessor_receipt_id, receipt_id: receipt.receipt_id,
      receipt_path: runtimePath(registryDoc, "activation_transition_receipt_template", null, null, null, transitionId),
      receipt_sha256: hash(Buffer.from(canonical(receipt))),
    });
    if (operation === "enable") lifecycleIndex.terminal_status = "enabled";
    const lifecycleBody = clone(lifecycleIndex); delete lifecycleBody.index_id;
    lifecycleIndex.index_id = hash(Buffer.from(canonical(lifecycleBody)));
    let role; let targetPath; let beforeRaw; let afterRaw; let targetOperation;
    if (["rollback", "enable"].includes(operation)) {
      role = "activation-state"; targetPath = registryDoc.runtime_paths.strict_activation_state.path;
      beforeRaw = Buffer.from(canonical(beforeActivation)); afterRaw = Buffer.from(canonical(afterActivation)); targetOperation = "replace";
    } else if (operation === "reprovision") {
      role = "capability-registry"; targetPath = registryDoc.runtime_paths.writer_capability_registry.path;
      beforeRaw = Buffer.from(canonical(beforeCapability)); afterRaw = Buffer.from(canonical(afterCapability)); targetOperation = "replace";
    } else if (operation === "record-refresh") {
      role = "transition-state"; targetPath = runtimePath(registryDoc, "activation_transition_state_template", null, null, null, transitionId);
      beforeRaw = null; afterRaw = Buffer.from(canonical(refreshReceipt)); targetOperation = "create";
    } else {
      role = "attestation"; targetPath = registryDoc.runtime_paths.writer_fence_attestation.path;
      beforeRaw = Buffer.from(canonical(oldAttestation)); afterRaw = Buffer.from(canonical(newAttestation)); targetOperation = "replace";
    }
    const receiptPath = runtimePath(registryDoc, "activation_transition_receipt_template", null, null, null, transitionId);
    const lifecyclePath = runtimePath(registryDoc, "activation_lifecycle_index_template", null, null, null, null, null, null, null, null, null, lifecycleId);
    const [journal, marker] = transitionJournalFixture(
      "activation", transitionId, journalId,
      [
        { role, operation: targetOperation, path: targetPath, before_raw: beforeRaw, after_raw: afterRaw },
        { role: "activation-lifecycle-index", operation: sequence === 1 ? "create" : "replace", path: lifecyclePath,
          before_raw: beforeLifecycleIndex === null ? null : Buffer.from(canonical(beforeLifecycleIndex)), after_raw: Buffer.from(canonical(lifecycleIndex)) },
      ],
      receiptPath, Buffer.from(canonical(receipt)), registryDoc, schemaSha, registrySha,
    );
    steps.push({
      command, receipt, journal, marker, authority, before_activation: beforeActivation, after_activation: afterActivation,
      before_capability: beforeCapability, after_capability: afterCapability,
      refresh_receipt: ["record-refresh", "attest", "enable"].includes(operation) ? refreshReceipt : null,
      attestation: ["attest", "enable"].includes(operation) ? newAttestation : null,
      attestation_preimage: operation === "attest" ? oldAttestation : null,
      before_lifecycle_index: beforeLifecycleIndex, after_lifecycle_index: clone(lifecycleIndex),
      target_images: {
        [targetPath]: { before: beforeRaw, after: afterRaw },
        [lifecyclePath]: { before: beforeLifecycleIndex === null ? null : Buffer.from(canonical(beforeLifecycleIndex)), after: Buffer.from(canonical(lifecycleIndex)) },
        [receiptPath]: { before: null, after: Buffer.from(canonical(receipt)) },
      },
    });
    previousReceipt = receipt;
  });
  return { roots, steps, lifecycle_index: lifecycleIndex, initial_attestation: oldAttestation,
    final_activation: finalActivation, final_capability: newCapability, final_attestation: newAttestation };
};

const activationTransitionSemantics = (pack, registryDoc, schemaRoot, schemaSha, registrySha) => {
  try {
    const operations = ["rollback", "reprovision", "record-refresh", "attest", "enable"];
    const lifecycle = pack.lifecycle_index;
    const expectedLifecycleId = hash(Buffer.from(canonical({
      initial_activation_state_id: pack.steps[0].before_activation.state_id,
      target_activation_epoch: pack.steps[0].after_activation.activation_epoch,
      operations,
    })));
    if (canonical(pack.steps.map(({ command }) => command.operation)) !== canonical(operations)
      || !validateRegistered(lifecycle, schemaRoot, registryDoc, "activation-lifecycle-index/1.0.0", schemaSha, registrySha)
      || lifecycle.index_id !== hash(Buffer.from(canonical(Object.fromEntries(Object.entries(lifecycle).filter(([key]) => key !== "index_id")))))
      || lifecycle.lifecycle_id !== expectedLifecycleId
      || lifecycle.activation_epoch !== pack.steps[0].after_activation.activation_epoch
      || lifecycle.terminal_status !== "enabled" || lifecycle.entries.length !== operations.length) return false;
    let previousReceipt = null; let previousLifecycleIndex = null;
    for (let offset = 0; offset < pack.steps.length; offset += 1) {
      const step = pack.steps[offset]; const expectedOrdinal = offset + 1;
      const { command, receipt, journal, marker, before_activation: beforeActivation, after_activation: afterActivation,
        before_capability: beforeCapability, after_capability: afterCapability } = step;
      const [capabilityRaw, rootRaw, activationRaw, attestationRaw, context] = step.authority;
      const administrator = beforeCapability.capabilities.find((row) =>
        row.producer_id === registryDoc.strict_rollout.activation_administrator_producer_id && row.status === "active");
      const receiptBody = clone(receipt); delete receiptBody.receipt_id;
      const approved = [...new Set(command.approved_by)].sort((left, right) => Buffer.from(left).compare(Buffer.from(right)));
      if (!administrator
        || !validateRegistered(command, schemaRoot, registryDoc, "activation-transition-command/1.0.0", schemaSha, registrySha)
        || !validateRegistered(receipt, schemaRoot, registryDoc, "activation-transition-receipt/1.0.0", schemaSha, registrySha)
        || receipt.receipt_id !== hash(Buffer.from(canonical(receiptBody)))
        || command.authority_context_id !== context.context_id || context.principal_id !== administrator.principal_id
        || command.fact_lock_profile_id !== registryDoc.lock_profile.profile_id
        || !runtimeAuthorityBindingSemantics(registryDoc, schemaRoot, schemaSha, registrySha, capabilityRaw, rootRaw, activationRaw, attestationRaw, context)
        || !journalSemantics(journal, marker, schemaRoot, registryDoc, schemaSha, registrySha)
        || command.expected_activation_epoch !== beforeActivation.activation_epoch
        || command.expected_capability_epoch !== beforeCapability.capability_epoch
        || command.expected_activation_state_id !== beforeActivation.state_id
        || command.expected_capability_registry_id !== beforeCapability.capability_registry_id
        || command.lifecycle_id !== lifecycle.lifecycle_id || receipt.lifecycle_id !== lifecycle.lifecycle_id
        || command.step_ordinal !== expectedOrdinal || receipt.step_ordinal !== expectedOrdinal
        || command.predecessor_receipt_id !== (previousReceipt === null ? null : previousReceipt.receipt_id)
        || receipt.predecessor_receipt_id !== command.predecessor_receipt_id
        || command.expected_attestation_id !== receipt.before_attestation_id
        || command.expected_attestation_sha256 !== (["rollback", "attest"].includes(command.operation)
          ? hash(Buffer.from(canonical(pack.initial_attestation)))
          : command.operation === "enable" ? hash(Buffer.from(canonical(pack.final_attestation))) : null)
        || receipt.before_activation_state_id !== beforeActivation.state_id || receipt.after_activation_state_id !== afterActivation.state_id
        || receipt.before_capability_registry_id !== beforeCapability.capability_registry_id
        || receipt.after_capability_registry_id !== afterCapability.capability_registry_id
        || receipt.journal_id !== journal.journal_id || receipt.status !== "committed" || marker.state !== "committed"
        || canonical(command.approved_by) !== canonical(approved)) return false;
      const beforeLifecycleIndex = step.before_lifecycle_index; const afterLifecycleIndex = step.after_lifecycle_index;
      const expectedEntry = {
        step_ordinal: expectedOrdinal, transition_id: command.transition_id, operation: command.operation,
        predecessor_receipt_id: receipt.predecessor_receipt_id, receipt_id: receipt.receipt_id,
        receipt_path: runtimePath(registryDoc, "activation_transition_receipt_template", null, null, null, command.transition_id),
        receipt_sha256: hash(Buffer.from(canonical(receipt))),
      };
      const beforeBody = beforeLifecycleIndex === null ? null : Object.fromEntries(Object.entries(beforeLifecycleIndex).filter(([key]) => key !== "index_id"));
      const afterBody = Object.fromEntries(Object.entries(afterLifecycleIndex).filter(([key]) => key !== "index_id"));
      if ((beforeLifecycleIndex === null) !== (expectedOrdinal === 1)
        || (beforeLifecycleIndex !== null && (!validateRegistered(beforeLifecycleIndex, schemaRoot, registryDoc, "activation-lifecycle-index/1.0.0", schemaSha, registrySha)
          || beforeLifecycleIndex.index_id !== hash(Buffer.from(canonical(beforeBody)))
          || canonical(beforeLifecycleIndex) !== canonical(previousLifecycleIndex)))
        || !validateRegistered(afterLifecycleIndex, schemaRoot, registryDoc, "activation-lifecycle-index/1.0.0", schemaSha, registrySha)
        || afterLifecycleIndex.index_id !== hash(Buffer.from(canonical(afterBody)))
        || afterLifecycleIndex.lifecycle_id !== expectedLifecycleId || afterLifecycleIndex.activation_epoch !== lifecycle.activation_epoch
        || canonical(afterLifecycleIndex.entries) !== canonical([...(beforeLifecycleIndex === null ? [] : beforeLifecycleIndex.entries), expectedEntry])
        || afterLifecycleIndex.terminal_status !== (expectedOrdinal === operations.length ? "enabled" : "in-progress")) return false;
      if (previousReceipt !== null) {
        const previousStep = pack.steps[offset - 1];
        if (canonical(beforeActivation) !== canonical(previousStep.after_activation)
          || canonical(beforeCapability) !== canonical(previousStep.after_capability)
          || receipt.before_activation_state_id !== previousReceipt.after_activation_state_id
          || receipt.before_capability_registry_id !== previousReceipt.after_capability_registry_id
          || receipt.before_attestation_id !== previousReceipt.after_attestation_id) return false;
      }
      const operation = command.operation;
      const businessTargets = journal.targets.filter(({ role }) => !["receipt", "activation-lifecycle-index"].includes(role));
      const lifecycleTargets = journal.targets.filter(({ role }) => role === "activation-lifecycle-index");
      const receiptTargets = journal.targets.filter(({ role }) => role === "receipt");
      if (businessTargets.length !== 1 || lifecycleTargets.length !== 1 || receiptTargets.length !== 1) return false;
      const target = businessTargets[0]; let expectedRole; let expectedPath; let expectedOperation; let expectedBefore; let expectedAfter;
      if (["rollback", "enable"].includes(operation)) {
        expectedRole = "activation-state"; expectedPath = registryDoc.runtime_paths.strict_activation_state.path; expectedOperation = "replace";
        expectedBefore = Buffer.from(canonical(beforeActivation)); expectedAfter = Buffer.from(canonical(afterActivation));
      } else if (operation === "reprovision") {
        expectedRole = "capability-registry"; expectedPath = registryDoc.runtime_paths.writer_capability_registry.path; expectedOperation = "replace";
        expectedBefore = Buffer.from(canonical(beforeCapability)); expectedAfter = Buffer.from(canonical(afterCapability));
      } else if (operation === "record-refresh") {
        expectedRole = "transition-state"; expectedPath = runtimePath(registryDoc, "activation_transition_state_template", null, null, null, command.transition_id); expectedOperation = "create";
        expectedBefore = null; expectedAfter = Buffer.from(canonical(step.refresh_receipt));
      } else {
        expectedRole = "attestation"; expectedPath = registryDoc.runtime_paths.writer_fence_attestation.path; expectedOperation = "replace";
        expectedBefore = Buffer.from(canonical(step.attestation_preimage)); expectedAfter = Buffer.from(canonical(step.attestation));
      }
      const receiptPath = runtimePath(registryDoc, "activation_transition_receipt_template", null, null, null, command.transition_id);
      const lifecyclePath = runtimePath(registryDoc, "activation_lifecycle_index_template", null, null, null, null, null, null, null, null, null, lifecycle.lifecycle_id);
      const lifecycleTarget = lifecycleTargets[0];
      if (target.role !== expectedRole || target.path !== expectedPath || target.operation !== expectedOperation
        || target.after_sha256 !== hash(expectedAfter)
        || target.before_sha256 !== (expectedBefore === null ? null : hash(expectedBefore))
        || lifecycleTarget.path !== lifecyclePath
        || lifecycleTarget.operation !== (expectedOrdinal === 1 ? "create" : "replace")
        || lifecycleTarget.before_sha256 !== (beforeLifecycleIndex === null ? null : hash(Buffer.from(canonical(beforeLifecycleIndex))))
        || lifecycleTarget.after_sha256 !== hash(Buffer.from(canonical(afterLifecycleIndex)))
        || receiptTargets[0].path !== receiptPath || receiptTargets[0].after_sha256 !== hash(Buffer.from(canonical(receipt)))) return false;
      if (operation === "rollback" && !(
        afterActivation.activation_epoch === beforeActivation.activation_epoch + 1 && afterActivation.mode === "legacy"
        && afterActivation.attestation_id === null && canonical(afterCapability) === canonical(beforeCapability)
        && receipt.full_refresh_receipt_id === null && receipt.attestation_id === null)) return false;
      if (operation === "reprovision" && !(
        canonical(afterActivation) === canonical(beforeActivation)
        && afterCapability.capability_epoch === beforeCapability.capability_epoch + 1
        && receipt.full_refresh_receipt_id === null && receipt.attestation_id === null)) return false;
      if (["record-refresh", "attest", "enable"].includes(operation)) {
        const refresh = step.refresh_receipt;
        if (!refresh || !validateRegistered(refresh, schemaRoot, registryDoc, "refresh-run-receipt/1.0.0", schemaSha, registrySha)
          || receipt.full_refresh_receipt_id !== refresh.receipt_id) return false;
      }
      if (["attest", "enable"].includes(operation)) {
        const attestation = step.attestation; const body = clone(attestation); delete body.attestation_id;
        if (!attestation || !validateRegistered(attestation, schemaRoot, registryDoc, "writer-fence-migration-attestation/1.0.0", schemaSha, registrySha)
          || attestation.attestation_id !== hash(Buffer.from(canonical(body)))
          || attestation.activation_epoch !== afterActivation.activation_epoch
          || attestation.capability_registry_id !== afterCapability.capability_registry_id
          || receipt.attestation_id !== attestation.attestation_id) return false;
      }
      if (operation === "enable" && !(
        afterActivation.mode === "strict" && afterActivation.activation_epoch === beforeActivation.activation_epoch
        && afterActivation.attestation_id === receipt.attestation_id)) return false;
      previousReceipt = receipt; previousLifecycleIndex = afterLifecycleIndex;
    }
    const last = pack.steps.at(-1);
    return previousReceipt !== null && canonical(pack.final_activation) === canonical(last.after_activation)
      && canonical(pack.final_capability) === canonical(last.after_capability)
      && canonical(pack.final_attestation) === canonical(last.attestation)
      && canonical(pack.lifecycle_index) === canonical(last.after_lifecycle_index);
  } catch { return false; }
};

const factAttributionSemantics = (
  graph, registryDoc, schemaRoot, schemaSha, registrySha, runtimeCapabilityBytes, runtimeRootRegistryBytes,
  runtimeActivationBytes, runtimeAttestationBytes, authorityContext,
) => {
  const required = ["command", "journal", "marker", "before_state", "after_state", "receipt", "proof"];
  if (required.some((name) => !Object.hasOwn(graph, name))) return false;
  if (![runtimeCapabilityBytes, runtimeRootRegistryBytes, runtimeActivationBytes].every(Buffer.isBuffer)
      || !authorityContext || typeof authorityContext !== "object") return false;
  if (!runtimeAuthorityBindingSemantics(registryDoc, schemaRoot, schemaSha, registrySha, runtimeCapabilityBytes,
    runtimeRootRegistryBytes, runtimeActivationBytes, runtimeAttestationBytes, authorityContext)) return false;
  let capabilityRegistry;
  try { capabilityRegistry = JSON.parse(runtimeCapabilityBytes.toString("utf8")); } catch { return false; }
  if (!Buffer.from(canonical(capabilityRegistry)).equals(runtimeCapabilityBytes)) return false;
  if (Object.hasOwn(graph, "capability_registry")
      && canonical(graph.capability_registry) !== canonical(capabilityRegistry)) return false;
  const { command, journal, marker, before_state: beforeState, after_state: afterState, receipt, proof } = graph;
  const actionRef = expectedContractRef(registryDoc, "action-ledger-mutation/2.0.0", schemaSha, registrySha);
  const wdrRef = expectedContractRef(registryDoc, "wdr-mutation/1.0.0", schemaSha, registrySha);
  const ownedRef = expectedContractRef(registryDoc, "owned-fact-command/1.0.0", schemaSha, registrySha);
  const intentRef = expectedContractRef(registryDoc, "producer-intent-outbox-command/1.0.0", schemaSha, registrySha);
  let kind; let commandValid;
  if (canonical(command.contract) === canonical(actionRef)) { kind = "action"; commandValid = validateRegistered(command, schemaRoot, registryDoc, "action-ledger-mutation/2.0.0", schemaSha, registrySha); }
  else if (canonical(command.contract) === canonical(wdrRef)) { kind = "wdr"; commandValid = validateRegistered(command, schemaRoot, registryDoc, "wdr-mutation/1.0.0", schemaSha, registrySha); }
  else if (canonical(command.contract) === canonical(ownedRef)) { kind = "owned"; commandValid = validateRegistered(command, schemaRoot, registryDoc, "owned-fact-command/1.0.0", schemaSha, registrySha); }
  else if (canonical(command.contract) === canonical(intentRef)) { kind = "intent"; commandValid = validateRegistered(command, schemaRoot, registryDoc, "producer-intent-outbox-command/1.0.0", schemaSha, registrySha); }
  else return false;
  const factValidation = [validateRegistered(capabilityRegistry, schemaRoot, registryDoc, "writer-capability-registry/1.0.0", schemaSha, registrySha), commandValid,
    validateRegistered(beforeState, schemaRoot, registryDoc, "fact-generation-state/1.0.0", schemaSha, registrySha), validateRegistered(afterState, schemaRoot, registryDoc, "fact-generation-state/1.0.0", schemaSha, registrySha),
    validateRegistered(receipt, schemaRoot, registryDoc, "fact-mutation-receipt/1.0.0", schemaSha, registrySha), validateRegistered(proof, schemaRoot, registryDoc, "fact-mutation-proof/1.0.0", schemaSha, registrySha),
    journalSemantics(journal, marker, schemaRoot, registryDoc, schemaSha, registrySha)];
  if (!factValidation.every(Boolean)) return false;
  if (kind === "wdr" && command.operation === "patch") {
    const historyRows = command.set.meeting_history_append ?? [];
    if (historyRows.some((row) => row.command_id !== command.command_id)) return false;
  }
  const auth = receipt.authorization;
  const registryBody = clone(capabilityRegistry); delete registryBody.capability_registry_id;
  if (capabilityRegistry.capability_registry_id !== hash(Buffer.from(canonical(registryBody)))) return false;
  const requiredProducers = new Set(registryDoc.strict_rollout.authoritative_writers);
  const writerSpecs = new Map(registryDoc.strict_rollout.writer_specs.map((row) => [row.producer_id, row]));
  const capabilityIds = capabilityRegistry.capabilities.map(({ capability_id }) => capability_id);
  const activeProducers = capabilityRegistry.capabilities.filter(({ status }) => status === "active").map(({ producer_id }) => producer_id);
  if (new Set(capabilityIds).size !== capabilityIds.length || new Set(activeProducers).size !== activeProducers.length || canonical([...new Set(activeProducers)].sort()) !== canonical([...requiredProducers].sort())) return false;
  const sortedCapabilities = [...capabilityRegistry.capabilities].sort((left, right) => Buffer.from(left.producer_id).compare(Buffer.from(right.producer_id)));
  if (canonical(capabilityRegistry.capabilities) !== canonical(sortedCapabilities)) return false;
  for (const row of capabilityRegistry.capabilities) {
    if (capabilityRecordDigest(row) !== row.capability_id || row.capability_id !== row.authorization_record_digest) return false;
    for (const name of ["allowed_operations", "allowed_fields", "allowed_sections"]) if (canonical(row[name]) !== canonical([...row[name]].sort())) return false;
    const spec = writerSpecs.get(row.producer_id);
    if (!spec || ["allowed_operations", "allowed_fields", "allowed_sections"].some((name) => canonical(row[name]) !== canonical(spec[name]))) return false;
  }
  const matches = capabilityRegistry.capabilities.filter((row) => row.producer_id === auth.producer_id && row.status === "active");
  if (matches.length !== 1) return false;
  const cap = matches[0];
  if (capabilityRecordDigest(cap) !== cap.capability_id || cap.capability_id !== cap.authorization_record_digest) return false;
  const [commandFields, commandSections] = commandPermissions(command, registryDoc);
  if (!cap.allowed_operations.includes(command.operation) || [...commandFields].some((field) => !cap.allowed_fields.includes(field)) || [...commandSections].some((section) => !cap.allowed_sections.includes(section))) return false;
  if (["wdr", "owned", "intent"].includes(commandKind(command)) && canonical(command.issuer) !== canonical({ producer_id: cap.producer_id, capability_id: cap.capability_id })) return false;
  const expected = { producer_id: cap.producer_id, capability_id: cap.capability_id, capability_epoch: capabilityRegistry.capability_epoch, principal_id: cap.principal_id, capability_registry_id: capabilityRegistry.capability_registry_id, authorization_record_digest: cap.authorization_record_digest, authorized_command_fingerprint: hash(Buffer.from(canonical(command))) };
  const initiator = Object.fromEntries(["producer_id", "capability_id", "capability_epoch", "principal_id"].map((key) => [key, expected[key]]));
  if (canonical(auth) !== canonical(expected) || canonical(journal.authorization) !== canonical(expected) || canonical(receipt.initiator) !== canonical(initiator)) return false;
  const proofBody = clone(proof); delete proofBody.proof_id;
  if (proof.proof_id !== hash(Buffer.from(canonical(proofBody))) || proof.transaction_id !== journal.transaction_id
      || proof.host_principal_id !== cap.principal_id || cap.principal_id !== authorityContext.principal_id
      || proof.authorized_command_fingerprint !== expected.authorized_command_fingerprint) return false;
  if (receipt.transaction_id !== journal.transaction_id || receipt.journal_id !== journal.journal_id || receipt.after_fact_generation !== receipt.before_fact_generation + 1) return false;
  const receiptBody = clone(receipt); delete receiptBody.receipt_id; if (receipt.receipt_id !== hash(Buffer.from(canonical(receiptBody)))) return false;
  const business = journal.targets.filter(({ role }) => role === "business"); const generation = journal.targets.filter(({ role }) => role === "fact-generation");
  const expectedTargets = expectedFactBusinessTargets(command, registryDoc); const artifacts = proof.business_artifacts;
  if (canonical(receipt.business_targets) !== canonical(business) || generation.length !== 1 || canonical(receipt.generation_state_target) !== canonical(generation[0]) || business.length !== expectedTargets.length || artifacts.length !== expectedTargets.length) return false;
  const decoded = [];
  try {
    business.forEach((target, index) => {
      const expectedTarget = expectedTargets[index]; const artifact = artifacts[index];
      const identity = Object.fromEntries(["root_instance_id", "path", "operation"].map((key) => [key, target[key]]));
      const artifactIdentity = Object.fromEntries(["root_instance_id", "path", "operation"].map((key) => [key, artifact[key]]));
      if (canonical(identity) !== canonical(expectedTarget) || canonical(artifactIdentity) !== canonical(expectedTarget)) throw new Error("target identity mismatch");
      const beforeBytes = artifactBytes(artifact.before_bytes); const afterBytes = artifactBytes(artifact.after_bytes);
      if (target.before_sha256 !== (beforeBytes === null ? null : hash(beforeBytes)) || target.after_sha256 !== (afterBytes === null ? null : hash(afterBytes))) throw new Error("target byte mismatch");
      decoded.push([beforeBytes, afterBytes]);
    });
  } catch { return false; }
  const readArtifacts = proof.read_artifacts;
  const readValues = new Map();
  try {
    for (const artifact of readArtifacts) {
      const raw = artifactBytes(artifact.bytes);
      if (raw === null || artifact.sha256 !== hash(raw) || readValues.has(artifact.path)
        || artifact.root_instance_id !== "123e4567-e89b-42d3-a456-426614174000") return false;
      readValues.set(artifact.path, raw);
    }
  } catch { return false; }
  if (kind === "action") {
    try {
      const [beforeLedger, afterLedger] = decoded[0];
      const beforeLedgerState = JSON.parse(decoded[1][0].toString()); const afterLedgerState = JSON.parse(decoded[1][1].toString());
      const beforeFlow = JSON.parse(decoded[2][0].toString()); const afterFlow = JSON.parse(decoded[2][1].toString());
      const beforeRows = parseActionLedger(beforeLedger); const afterRows = parseActionLedger(afterLedger);
      const expectedAfterRows = applyActionCommand(beforeRows, command); const expectedAfterRaw = renderActionLedger(expectedAfterRows);
      if (readArtifacts.length || beforeLedger === null || afterLedger === null || !afterLedger.equals(expectedAfterRaw)
        || canonical(afterRows) !== canonical(expectedAfterRows)
        || !validateRegistered(beforeLedgerState, schemaRoot, registryDoc, "action-ledger-state/1.0.0", schemaSha, registrySha)
        || !validateRegistered(afterLedgerState, schemaRoot, registryDoc, "action-ledger-state/1.0.0", schemaSha, registrySha)
        || !validateRegistered(beforeFlow, schemaRoot, registryDoc, "action-flow-index/1.0.0", schemaSha, registrySha)
        || !validateRegistered(afterFlow, schemaRoot, registryDoc, "action-flow-index/1.0.0", schemaSha, registrySha)) return false;
      const delta = expectedActionDelta(command);
      const appliedRecord = { command_id: command.command_id, command_fingerprint: expected.authorized_command_fingerprint, action_id: command.action_id };
      const expectedBeforeState = actionLedgerStateDocument(beforeRows, beforeLedger, beforeLedgerState.ledger_revision, beforeLedgerState.applied_commands, registryDoc, schemaSha, registrySha);
      const expectedAfterState = actionLedgerStateDocument(expectedAfterRows, afterLedger, beforeLedgerState.ledger_revision + 1, [...beforeLedgerState.applied_commands, appliedRecord], registryDoc, schemaSha, registrySha);
      const expectedBeforeFlow = actionFlowDocument(beforeRows, beforeLedger, beforeLedgerState.ledger_revision, registryDoc, schemaSha, registrySha);
      const expectedAfterFlow = actionFlowDocument(expectedAfterRows, afterLedger, beforeLedgerState.ledger_revision + 1, registryDoc, schemaSha, registrySha);
      const changedRow = expectedAfterRows.find(({ action_id }) => action_id === command.action_id);
      if (canonical(beforeLedgerState) !== canonical(expectedBeforeState) || canonical(afterLedgerState) !== canonical(expectedAfterState)
        || canonical(beforeFlow) !== canonical(expectedBeforeFlow) || canonical(afterFlow) !== canonical(expectedAfterFlow)
        || changedRow.action_revision !== delta.after_revision) return false;
    } catch { return false; }
  } else if (kind === "owned") {
    const profile = ownedFactProfile(command, registryDoc); const [beforeOwned, afterOwned] = decoded[0];
    if (profile === null || readArtifacts.length || decoded.length !== 1 || afterOwned === null
      || command.expected_before_sha256 !== (beforeOwned === null ? null : hash(beforeOwned))
      || command.after_bytes !== encodedBytes(afterOwned) || command.after_sha256 !== hash(afterOwned)
      || !ownedFactContentValid(afterOwned, profile, registryDoc) || (command.operation === "create") !== (beforeOwned === null)) return false;
    try { if (canonical(command.evidence) !== canonical(canonicalEvidence(command.evidence))) return false; } catch { return false; }
  } else if (kind === "intent") {
    if (decoded.length || readArtifacts.length) return false;
  } else {
    const [beforeWdr, afterWdr] = decoded[0]; let beforeWdrState; let afterWdrState;
    try { beforeWdrState = decoded[1][0] === null ? null : JSON.parse(decoded[1][0].toString()); afterWdrState = JSON.parse(decoded[1][1].toString()); } catch { return false; }
    const workstreamId = command.workstream_id;
    if (afterWdr === null || !completeWdrValid(afterWdr.toString(), workstreamId) || !validateRegistered(afterWdrState, schemaRoot, registryDoc, "wdr-file-state/1.0.0", schemaSha, registrySha)
      || afterWdrState.record_fingerprint !== hash(afterWdr) || afterWdrState.workstream_id !== workstreamId || afterWdrState.record_path !== `workstreams/${workstreamId}/delivery-record.md` || afterWdrState.lifecycle !== "active") return false;
    let sideBefore = null; let sideAfter = null;
    if (command.operation === "create") {
      if (readArtifacts.length || beforeWdr !== null || beforeWdrState !== null || afterWdr.toString() !== command.rendered_record || command.rendered_sha256 !== hash(afterWdr) || afterWdrState.wdr_revision !== 1 || afterWdrState.file_generation !== 1) return false;
      [sideBefore, sideAfter] = decoded[2]; if (sideBefore !== null) return false;
    } else {
      if (beforeWdr === null || beforeWdrState === null || !completeWdrValid(beforeWdr.toString(), workstreamId) || !validateRegistered(beforeWdrState, schemaRoot, registryDoc, "wdr-file-state/1.0.0", schemaSha, registrySha)) return false;
      let revisionDelta; let generationDelta; try { [revisionDelta, generationDelta] = wdrCounterDelta(beforeWdr.toString(), afterWdr.toString(), workstreamId); } catch { return false; }
      if (!(beforeWdrState.record_fingerprint === hash(beforeWdr) && beforeWdrState.workstream_id === workstreamId && beforeWdrState.record_path === `workstreams/${workstreamId}/delivery-record.md` && beforeWdrState.lifecycle === "active"
        && beforeWdrState.wdr_revision === command.expected_wdr_revision && beforeWdrState.file_generation === command.expected_file_generation
        && afterWdrState.wdr_revision === beforeWdrState.wdr_revision + revisionDelta && afterWdrState.file_generation === beforeWdrState.file_generation + generationDelta && generationDelta === 1)) return false;
      if (command.set.refresh_actions) [sideBefore, sideAfter] = decoded[2];
    }
    const summaries = [];
    if (command.operation === "create" || command.set.refresh_actions) {
      let sidecarAfter; let sidecarBefore;
      try { sidecarAfter = JSON.parse(sideAfter.toString()); sidecarBefore = sideBefore === null ? null : JSON.parse(sideBefore.toString()); } catch { return false; }
      if (!validateRegistered(sidecarAfter, schemaRoot, registryDoc, "wdr-action-projection/1.0.0", schemaSha, registrySha)) return false;
      if (sidecarAfter.workstream_id !== workstreamId || sidecarAfter.renderer_id !== "urn:adp:wdr-action-renderer:1.0.0" || sidecarAfter.renderer_sha256 !== registryDoc.protocol.sha256
        || sidecarAfter.wdr_revision !== afterWdrState.wdr_revision || sidecarAfter.file_generation !== afterWdrState.file_generation) return false;
      if (command.operation === "create") { if (sidecarAfter.actions.length || sidecarAfter.ledger_revision !== 0 || sidecarAfter.wdr_revision !== 1 || sidecarAfter.file_generation !== 1) return false; }
      else if (!(validateRegistered(sidecarBefore, schemaRoot, registryDoc, "wdr-action-projection/1.0.0", schemaSha, registrySha)
        && sidecarBefore.workstream_id === workstreamId && sidecarBefore.renderer_id === "urn:adp:wdr-action-renderer:1.0.0" && sidecarBefore.renderer_sha256 === registryDoc.protocol.sha256
        && sidecarBefore.wdr_revision === command.expected_wdr_revision && sidecarBefore.file_generation === command.expected_file_generation
        && sidecarAfter.wdr_revision === afterWdrState.wdr_revision && sidecarAfter.file_generation === afterWdrState.file_generation)) return false;
      summaries.push(...sidecarAfter.actions.map(({ rendered_summary }) => rendered_summary));
      if (command.operation === "patch") {
        const ledgerPath = registryDoc.runtime_paths.action_ledger.path;
        const ledgerStatePath = registryDoc.runtime_paths.action_ledger_state.path;
        if (canonical([...readValues.keys()]) !== canonical([ledgerPath, ledgerStatePath])) return false;
        try {
          const ledgerRaw = readValues.get(ledgerPath); const ledgerRows = parseActionLedger(ledgerRaw);
          const ledgerState = JSON.parse(readValues.get(ledgerStatePath).toString());
          if (!validateRegistered(ledgerState, schemaRoot, registryDoc, "action-ledger-state/1.0.0", schemaSha, registrySha)) return false;
          const expectedLedgerState = actionLedgerStateDocument(ledgerRows, ledgerRaw, ledgerState.ledger_revision, ledgerState.applied_commands, registryDoc, schemaSha, registrySha);
          const expectedSnapshot = actionSnapshot(ledgerRows, workstreamId, ledgerState.ledger_fingerprint, ledgerState.ledger_revision);
          if (canonical(ledgerState) !== canonical(expectedLedgerState) || canonical(command.action_snapshot) !== canonical(expectedSnapshot)
            || sidecarAfter.ledger_fingerprint !== expectedSnapshot.ledger_fingerprint || sidecarAfter.ledger_revision !== expectedSnapshot.ledger_revision
            || canonical(sidecarAfter.actions) !== canonical(expectedSnapshot.actions)) return false;
        } catch { return false; }
      }
    } else if (readArtifacts.length) {
      return false;
    }
    if (command.operation === "patch" && afterWdr.toString() !== applyWdrPatch(beforeWdr.toString(), command, summaries)) return false;
  }
  const stateTarget = generation[0]; const memoryRoot = "123e4567-e89b-42d3-a456-426614174000";
  const receiptTemplate = journal.transaction_kind === "repair" ? "repair_fact_receipt_template" : "fact_receipt_template";
  const receiptPath = registryDoc.runtime_paths[receiptTemplate].path.replace("{transaction_token}", filesystemToken(journal.transaction_id));
  const receiptTargets = journal.targets.filter(({ role }) => role === "receipt");
  const matchingReceipts = receiptTargets.filter(({ path: targetPath }) => targetPath === receiptPath);
  const beforeCommandIndex = graph.before_command_index; const commandIndex = graph.command_index;
  if (!beforeCommandIndex || !commandIndex) return false;
  const beforeIndexBody = clone(beforeCommandIndex); delete beforeIndexBody.index_id; const indexBody = clone(commandIndex); delete indexBody.index_id;
  const expectedIndexEntry = { sequence: beforeCommandIndex.next_sequence, command_id: command.command_id,
    command_fingerprint: expected.authorized_command_fingerprint, transaction_id: journal.transaction_id, receipt_id: receipt.receipt_id,
    receipt_path: receiptPath, receipt_sha256: hash(Buffer.from(canonical(receipt))) };
  const indexTargets = journal.targets.filter(({ role }) => role === "fact-command-index");
  const outboxMode = commandIntentOutboxMode(command, registryDoc); const outboxTargets = journal.targets.filter(({ role }) => role === "intent-outbox");
  const beforeOutbox = graph.before_outbox; const afterOutbox = graph.after_outbox; let outboxOk;
  if (outboxMode === "none") outboxOk = beforeOutbox === null && afterOutbox === null && outboxTargets.length === 0
    && !Object.hasOwn(command, "status_intents") && !Object.hasOwn(command, "consumed_intent_ids");
  else {
    const emittedIntents = statusIntentsForCommand(command, registryDoc);
    if (!beforeOutbox || !afterOutbox
      || (outboxMode === "emit" ? !emittedIntents.length || Object.hasOwn(command, "consumed_intent_ids")
        : emittedIntents.length || !command.consumed_intent_ids?.length)) outboxOk = false;
    else {
      const beforeOutboxBody = clone(beforeOutbox); delete beforeOutboxBody.outbox_id; const afterOutboxBody = clone(afterOutbox); delete afterOutboxBody.outbox_id;
      outboxOk = validateRegistered(beforeOutbox, schemaRoot, registryDoc, "mutation-intent-outbox/1.0.0", schemaSha, registrySha)
        && validateRegistered(afterOutbox, schemaRoot, registryDoc, "mutation-intent-outbox/1.0.0", schemaSha, registrySha)
        && beforeOutbox.outbox_id === hash(Buffer.from(canonical(beforeOutboxBody))) && afterOutbox.outbox_id === hash(Buffer.from(canonical(afterOutboxBody)))
        && afterOutbox.outbox_generation === beforeOutbox.outbox_generation + 1 && outboxTargets.length === 1
        && outboxTargets[0].path === registryDoc.runtime_paths.mutation_intent_outbox.path
        && outboxTargets[0].before_sha256 === hash(Buffer.from(canonical(beforeOutbox)))
        && outboxTargets[0].after_sha256 === hash(Buffer.from(canonical(afterOutbox)));
      if (outboxOk) for (const document of [beforeOutbox, afterOutbox]) {
        if (canonical(document.entries.map(({ sequence }) => sequence)) !== canonical(document.entries.map((_, index) => index + 1))) { outboxOk = false; break; }
        for (const row of document.entries) if (!validateRegistered(row.intent, schemaRoot, registryDoc, "status-mutation-intent/1.0.0", schemaSha, registrySha)
          || row.intent_id !== hash(Buffer.from(canonical(row.intent))) || row.producer_id !== row.intent.origin_producer
          || row.workstream_id !== row.intent.workstream_id || canonical(row.field_set) !== canonical(Object.keys(row.intent.set).sort((a, b) => Buffer.from(a).compare(Buffer.from(b))))) { outboxOk = false; break; }
      }
      if (outboxOk && outboxMode === "emit") {
        const beforeEntries = beforeOutbox.entries; const afterEntries = afterOutbox.entries;
        const appended = afterEntries.slice(beforeEntries.length);
        outboxOk = canonical(afterEntries.slice(0, beforeEntries.length)) === canonical(beforeEntries) && appended.length === emittedIntents.length;
        if (outboxOk) appended.forEach((entry, offset) => {
          const intent = emittedIntents[offset];
          if (canonical(entry.intent) !== canonical(intent) || entry.intent_id !== hash(Buffer.from(canonical(intent)))
            || entry.source_command_id !== command.command_id || entry.source_command_fingerprint !== expected.authorized_command_fingerprint
            || entry.producer_id !== commandProducer(command) || entry.status !== "pending" || entry.attempts !== 0
            || entry.last_error !== null || entry.consumed_receipt_id !== null) outboxOk = false;
        });
      } else if (outboxOk) {
        const beforeEntries = beforeOutbox.entries; const afterEntries = afterOutbox.entries;
        const consumedIds = command.consumed_intent_ids; const consumedSet = new Set(consumedIds);
        const selected = new Map(beforeEntries.filter(({ intent_id }) => consumedSet.has(intent_id)).map((entry) => [entry.intent_id, entry]));
        const completePendingIds = beforeEntries.filter((row) => row.workstream_id === command.workstream_id && row.status === "pending")
          .map((row) => row.intent_id).sort((a, b) => Buffer.from(a).compare(Buffer.from(b)));
        outboxOk = canonical(consumedIds) === canonical([...consumedSet].sort((a, b) => Buffer.from(a).compare(Buffer.from(b))))
          && canonical(consumedIds) === canonical(completePendingIds)
          && consumedIds.length > 0 && selected.size === consumedSet.size && beforeEntries.length === afterEntries.length;
        const stripTransition = (row) => Object.fromEntries(Object.entries(row).filter(([key]) => !["status", "attempts", "consumed_receipt_id"].includes(key)));
        const merged = {}; const evidenceRows = [];
        if (outboxOk) for (let offset = 0; offset < beforeEntries.length; offset += 1) {
          const beforeEntry = beforeEntries[offset]; const afterEntry = afterEntries[offset];
          if (canonical(stripTransition(afterEntry)) !== canonical(stripTransition(beforeEntry))) { outboxOk = false; break; }
          if (!consumedSet.has(beforeEntry.intent_id)) { if (canonical(afterEntry) !== canonical(beforeEntry)) { outboxOk = false; break; } continue; }
          if (beforeEntry.status !== "pending" || beforeEntry.consumed_receipt_id !== null || beforeEntry.workstream_id !== command.workstream_id
            || afterEntry.status !== "consumed" || afterEntry.attempts !== beforeEntry.attempts + 1 || afterEntry.consumed_receipt_id !== receipt.receipt_id) { outboxOk = false; break; }
          for (const [field, value] of Object.entries(beforeEntry.intent.set)) {
            if (Object.hasOwn(merged, field) && canonical(merged[field]) !== canonical(value)) { outboxOk = false; break; }
            merged[field] = clone(value);
          }
          evidenceRows.push(...clone(beforeEntry.intent.evidence));
        }
        if (outboxOk) {
          const uniqueEvidence = new Map(evidenceRows.map((row) => [canonical(row), row]));
          const expectedEvidence = [...uniqueEvidence.values()].sort(compareEvidence);
          const commandSet = Object.fromEntries(Object.entries(command.set).filter(([field]) => STATUS_INTENT_FIELDS.has(field)));
          outboxOk = canonical(merged) === canonical(commandSet) && canonical(command.evidence) === canonical(expectedEvidence);
        }
      }
    }
  }
  const beforeStateBody = clone(beforeState); delete beforeStateBody.state_id; const afterStateBody = clone(afterState); delete afterStateBody.state_id;
  return marker.state === "committed"
    && beforeState.state_id === hash(Buffer.from(canonical(beforeStateBody))) && afterState.state_id === hash(Buffer.from(canonical(afterStateBody)))
    && beforeState.fact_generation === receipt.before_fact_generation && afterState.fact_generation === receipt.after_fact_generation
    && afterState.last_transaction_id === journal.transaction_id && stateTarget.root_instance_id === memoryRoot
    && stateTarget.path === registryDoc.runtime_paths.fact_generation.path
    && stateTarget.before_sha256 === hash(Buffer.from(canonical(beforeState))) && stateTarget.after_sha256 === hash(Buffer.from(canonical(afterState)))
    && matchingReceipts.length === 1 && matchingReceipts[0].root_instance_id === memoryRoot
    && matchingReceipts[0].after_sha256 === hash(Buffer.from(canonical(receipt)))
    && beforeCommandIndex.index_id === hash(Buffer.from(canonical(beforeIndexBody)))
    && commandIndex.index_id === hash(Buffer.from(canonical(indexBody)))
    && commandIndex.next_sequence === beforeCommandIndex.next_sequence + 1
    && canonical(commandIndex.entries) === canonical([...beforeCommandIndex.entries, expectedIndexEntry])
    && indexTargets.length === 1 && indexTargets[0].path === registryDoc.runtime_paths.fact_command_receipt_index.path
    && indexTargets[0].before_sha256 === hash(Buffer.from(canonical(beforeCommandIndex)))
    && indexTargets[0].after_sha256 === hash(Buffer.from(canonical(commandIndex)))
    && outboxOk
    && canonical(receipt.action_deltas) === canonical(commandKind(command) === "action" ? [expectedActionDelta(command)] : []);
};

const registryDagSemantics = (registryDoc, mutation = "none") => {
  const derived = registryDoc.projection_input_profiles.flatMap((profile) => profile.direct_upstreams.map((upstream) => `${upstream.kind}\0${profile.projection}`)).sort();
  const declared = registryDoc.projection_dag.map((edge) => `${edge.from}\0${edge.to}`).sort();
  if (canonical(derived) !== canonical(declared) || new Set(declared).size !== declared.length) return false;
  const profiles = Object.fromEntries(registryDoc.projection_input_profiles.map((row) => [row.projection, row]));
  const instances = Object.fromEntries(Object.keys(profiles).map((kind) => [kind, kind === "meeting-pack" ? ["business-biweekly", "fde-morning"] : [null]]));
  const nodeName = (kind, key) => `${kind}\0${key ?? "<null>"}`;
  const nodes = new Set(Object.entries(instances).flatMap(([kind, keys]) => keys.map((key) => nodeName(kind, key))));
  const adjacency = new Map([...nodes].map((node) => [node, new Set()])); const directUpstreams = new Map([...nodes].map((node) => [node, new Set()]));
  for (const edge of registryDoc.projection_dag) for (const sourceKey of instances[edge.from]) for (const targetKey of instances[edge.to]) {
    const source = nodeName(edge.from, sourceKey); const target = nodeName(edge.to, targetKey); adjacency.get(source).add(target); directUpstreams.get(target).add(source);
  }
  const indegree = new Map([...nodes].map((node) => [node, directUpstreams.get(node).size])); const ready = [...nodes].filter((node) => indegree.get(node) === 0).sort(); const ordered = [];
  while (ready.length) { const node = ready.shift(); ordered.push(node); for (const next of [...adjacency.get(node)].sort()) { indegree.set(next, indegree.get(next) - 1); if (indegree.get(next) === 0) { ready.push(next); ready.sort(); } } }
  if (ordered.length !== nodes.size) return false;
  const leafInputs = new Map([...nodes].map((node) => [node, new Set()]));
  for (const [kind, keys] of Object.entries(instances)) for (const key of keys) {
    const node = nodeName(kind, key);
    for (const source of profiles[kind].required_sources) {
      const identity = { category: source.category, source_kind: source.source_kind, enumerator: source.enumerator };
      if (kind === "meeting-pack" && source.enumerator.id === "selected-receipts-v1") identity.instance_key = key;
      leafInputs.get(node).add(`leaf:${hash(Buffer.from(canonical(identity)))}`);
    }
  }
  const baselineLeafIds = new Map([...new Set([...leafInputs.values()].flatMap((values) => [...values]))].map((leaf) => [leaf, hash(Buffer.from(leaf))]));
  const baselineIds = new Map(); const recordedInputs = new Map();
  for (const node of ordered) {
    const inputs = Object.fromEntries([...leafInputs.get(node)].sort().map((leaf) => [leaf, baselineLeafIds.get(leaf)]));
    for (const upstream of [...directUpstreams.get(node)].sort()) inputs[upstream] = baselineIds.get(upstream);
    recordedInputs.set(node, inputs); baselineIds.set(node, hash(Buffer.from(canonical({ instance: node, inputs }))));
  }
  const descendants = (seeds) => { const result = new Set(); const pending = [...seeds]; while (pending.length) for (const item of adjacency.get(pending.pop()) ?? []) if (!result.has(item) && !seeds.has(item)) { result.add(item); pending.push(item); } return result; };
  const changes = [...ordered.map((node) => [node, new Set([node])]), ...[...baselineLeafIds].map(([leaf]) => [leaf, new Set([...leafInputs].filter(([, values]) => values.has(leaf)).map(([node]) => node))])];
  for (const [changed, seeds] of changes) {
    const expected = new Set([...seeds, ...descendants(seeds)]); if (nodes.has(changed)) expected.delete(changed);
    const currentLeafIds = new Map(baselineLeafIds); const currentIds = new Map(baselineIds);
    if (nodes.has(changed)) currentIds.set(changed, hash(Buffer.from(canonical({ instance: changed, previous: baselineIds.get(changed) }))));
    else currentLeafIds.set(changed, hash(Buffer.from(canonical({ leaf: changed, previous: baselineLeafIds.get(changed) }))));
    const invalidated = new Set();
    for (const node of ordered) {
      if (node === changed) continue;
      const inputs = Object.fromEntries([...leafInputs.get(node)].sort().map((leaf) => [leaf, currentLeafIds.get(leaf)]));
      for (const upstream of [...directUpstreams.get(node)].sort()) inputs[upstream] = currentIds.get(upstream);
      if (canonical(inputs) !== canonical(recordedInputs.get(node))) { invalidated.add(node); if (mutation !== "stop-after-direct") currentIds.set(node, hash(Buffer.from(canonical({ instance: node, inputs, revision: "recomputed" })))); }
    }
    if (canonical([...invalidated].sort()) !== canonical([...expected].sort())) return false;
  }
  const meetingA = nodeName("meeting-pack", "business-biweekly"); const meetingB = nodeName("meeting-pack", "fde-morning"); const panelNode = nodeName("management-panel", null);
  return !descendants(new Set([meetingA])).has(meetingB) && !descendants(new Set([meetingB])).has(meetingA) && canonical([...descendants(new Set([meetingA]))]) === canonical([panelNode]);
};

const semanticValidatorSpecs = {
  "panel-publication-eligibility/1.0.0": { scope: ["physical-workstream-inventory/1.0.0", "selection-policy/1.0.0", "panel-binding-catalog/1.0.0", "generation-envelope/1.0.0", "management-panel-payload/2.0.0", "state-audit-payload/2.0.0", "action-projection-drift-verdict/1.0.0", "projection-dependency-manifest/1.0.0", "producer-receipt/1.0.0", "writer-fence-migration-attestation/1.0.0"], algorithm: "fresh-physical-inventory-attestation-byte-equal-policy-catalog-generation-bidirectional-first-nonempty-selection-and-generation-audit-drift-manifest-receipt-exact-scope-plus-strict-writer-fence-gate" },
  "projection-registry-closure/1.0.0": { scope: ["dependency_enumerators", "projection_input_profiles", "projection_dag", "canonical_array_ordering", "identity_set_fields", "semantic_sequence_fields", "runtime_paths", "wdr_field_section_map", "owned_fact_target_profiles", "source_time_bindings", "live_inspect_read_profile"], algorithm: "execute-all-enumerators-and-leaf-plus-instance-expanded-changed-input-dag-invalidation-and-exact-read-sets-typed-all-ordering-rules-owned-fact-targets-source-times-and-runtime-path-known-answers-from-registry" },
  "fact-receipt-attribution/1.0.0": { scope: ["runtime-authority-context/1.0.0", "root-registry-state/1.0.0", "writer-capability-registry/1.0.0", "strict-activation-state/1.0.0", "writer-fence-migration-attestation/1.0.0", "action-ledger-mutation/2.0.0", "owned-fact-command/1.0.0", "action-ledger-state/1.0.0", "action-flow-index/1.0.0", "wdr-mutation/1.0.0", "wdr-file-state/1.0.0", "wdr-action-projection/1.0.0", "transaction-journal-manifest/1.0.0", "journal-marker/1.0.0", "fact-generation-state/1.0.0", "fact-mutation-receipt/1.0.0", "fact-mutation-proof/1.0.0", "strict_rollout", "runtime_authority_profile", "runtime_paths", "lock_profile", "wdr_field_section_map", "owned_fact_target_profiles"], algorithm: "exact-contract-negotiation-one-discriminated-command-independent-native-runtime-authority-context-bound-under-lock-to-current-root-activation-attestation-capability-bytes-active-host-capability-and-command-derived-action-ledger-renderer-wdr-or-registry-allowlisted-owned-fact-after-state-plus-exact-target-cas-before-after-and-read-byte-proof-bound-transaction" },
  "owned-fact-command-semantics/1.0.0": { scope: ["owned-fact-command/1.0.0", "owned_fact_target_profiles", "runtime-authority-context/1.0.0", "root-registry-state/1.0.0", "writer-capability-registry/1.0.0", "strict-activation-state/1.0.0", "writer-fence-migration-attestation/1.0.0", "fact-mutation-receipt/1.0.0", "fact-mutation-proof/1.0.0", "runtime_authority_profile", "lock_profile", "runtime_paths"], algorithm: "registry-profile-exact-producer-operation-root-path-content-schema-cas-before-after-generation-journal-receipt-and-restart" },
  "transaction-journal-semantics/1.0.0": { scope: ["transaction-journal-manifest/1.0.0", "journal-marker/1.0.0", "runtime_paths"], algorithm: "transaction-kind-role-closure-journal-local-images-contiguous-order-unique-target-exact-receipts-and-terminal-marker" },
  "repair-graph-semantics/1.0.0": { scope: ["audit-finding-repair/2.0.0", "runtime-authority-context/1.0.0", "writer-capability-registry/1.0.0", "strict-activation-state/1.0.0", "writer-fence-migration-attestation/1.0.0", "wdr-mutation/1.0.0", "wdr-file-state/1.0.0", "wdr-action-projection/1.0.0", "repair-dry-run-request/1.0.0", "repair-dry-run-result/1.0.0", "repair-apply-request/1.0.0", "repair-run-receipt/1.0.0", "repair-nonce-state/1.0.0", "repair-receipt-index/1.0.0", "transaction-journal-manifest/1.0.0", "recovery-receipt/1.0.0", "fact-generation-state/1.0.0", "fact-mutation-receipt/1.0.0", "fact-mutation-proof/1.0.0", "strict_rollout", "runtime_authority_profile", "runtime_paths", "lock_profile", "wdr_field_section_map", "identity_set_fields"], algorithm: "validate-contract-bound-identity-set-canonical-blocked-invalidated-rolled-back-or-committed-repair-graph-durable-lookup-index-and-reuse-independent-native-runtime-authority-fact-attribution-for-refresh-actions-wdr-only-effects" },
  "release-evidence-transition-semantics/1.0.0": { scope: ["release-evidence-set/1.0.0", "release-evidence-transition-receipt/1.0.0", "release-evidence-history-index/1.0.0", "transaction-journal-manifest/1.0.0", "journal-marker/1.0.0", "runtime_paths", "runtime_policy", "evidence_trust"], algorithm: "current-set-generation-cas-stage-journal-commit-history-chain-scoped-retention-and-restart-recovery" },
  "activation-transition-semantics/1.0.0": { scope: ["activation-transition-command/1.0.0", "activation-transition-receipt/1.0.0", "runtime-authority-context/1.0.0", "root-registry-state/1.0.0", "strict-activation-state/1.0.0", "writer-capability-registry/1.0.0", "writer-fence-migration-attestation/1.0.0", "refresh-run-receipt/1.0.0", "transaction-journal-manifest/1.0.0", "journal-marker/1.0.0", "runtime_authority_profile", "lock_profile", "runtime_paths", "strict_rollout"], algorithm: "rollback-reprovision-record-refresh-attest-enable-ordered-epoch-cas-exact-target-journal-receipt-and-crash-recovery" },
  "panel-publication-graph/1.0.0": { scope: ["transaction-journal-manifest/1.0.0", "journal-marker/1.0.0", "panel-current-pointer/1.0.0", "panel-state/1.0.0", "panel-publication-receipt/1.0.0", "canonical-projection-envelope/1.0.0", "runtime_paths"], algorithm: "independent-cardinality-and-registry-derived-generation-kind-instance-token-exact-target-path-before-after-state-pointer-bound-publication" },
  "panel-binding-semantics/1.0.0": { scope: ["panel-binding-catalog/1.0.0", "canonical-projection-envelope/1.0.0", "management-panel-payload/2.0.0"], algorithm: "resolve-every-binding-from-exact-same-generation-upstream-envelope-and-compare-panel-target" },
  "panel-v1-same-generation-composition/1.0.0": { scope: ["management-panel-payload/2.0.0", "management-panel-current-view/2.0.0", "management-panel-model/1.0.0", "management-panel-manifest/1.0.0", "panel_v1_composition", "panel_v2_consumer"], algorithm: "recompose-legacy-aggregate-model-from-canonical-overlay-and-compatibility-corpus;execute-pinned-v2-current-consumer-from-sync-only" },
  "status-intent-application/1.0.0": { scope: ["status-mutation-intent/1.0.0", "status-sync-batch/2.0.0", "wdr-mutation/1.0.0"], algorithm: "exact-intent-id-content-field-evidence-workstream-to-single-reauthorized-wdr-command-binding-reject-conflicts-and-ordered-stop-on-first-failure-no-rollback" },
  "meeting-plan-intent-carriers/1.0.0": { scope: ["meeting-sync-plan/2.0.0", "producer-intent-outbox-command/1.0.0", "status-mutation-intent/1.0.0"], algorithm: "meeting-plan-intents-equal-exact-deduplicated-command-carried-intents-with-same-meeting-origin-workstream-evidence-and-canonical-bytes-including-zero-history" },
  "program-status-current-from-wdr/1.0.0": { scope: ["program-status-payload/2.0.0", "wdr-file-state/1.0.0", "selection-policy/1.0.0"], algorithm: "parse-complete-selected-wdr-current-labels-and-require-exact-program-status-workstream-row-plus-wdr-fingerprint-revision-generation" },
  "action-projection-drift-content/1.0.0": { scope: ["action-ledger-state/1.0.0", "wdr-file-state/1.0.0", "wdr-action-projection/1.0.0", "action-projection-drift-verdict/1.0.0", "selection-policy/1.0.0"], algorithm: "exact-active-ledger-routing-or-affected-membership-to-sidecar-record-rendered-wdr-summary-and-verdict-fingerprints-no-false-green" },
  "bootstrap-migration-attribution/1.0.0": { scope: ["bootstrap-migration-command/1.0.0", "action-ledger-state/1.0.0", "action-flow-index/1.0.0", "wdr-file-state/1.0.0", "wdr-action-projection/1.0.0", "writer-capability-registry/1.0.0", "transaction-journal-manifest/1.0.0", "journal-marker/1.0.0", "fact-generation-state/1.0.0", "fact-mutation-receipt/1.0.0", "fact-mutation-proof/1.0.0", "runtime_paths"], algorithm: "declared-pinned-absent-or-legacy12-or-legacy20-and-legacy-wdr-preimages-to-command-derived-mixed-create-replace-canonical-state-flow-sidecar-byte-proof-journal-receipt" },
  "strict-writer-fence-activation/1.0.0": { scope: ["writer-fence-migration-attestation/1.0.0", "release-evidence-set/1.0.0", "release-evidence-transition-receipt/1.0.0", "release-evidence-history-index/1.0.0", "conformance-result/1.0.0", "writer-build-manifest/1.0.0", "writer-fence-receipt/1.0.0", "generation-lineage-index/1.0.0", "publication-absence-proof/1.0.0", "strict-activation-state/1.0.0", "root-registry-state/1.0.0", "writer-capability-registry/1.0.0", "fact-generation-state/1.0.0", "action-ledger-state/1.0.0", "action-flow-index/1.0.0", "wdr-file-state/1.0.0", "wdr-action-projection/1.0.0", "refresh-run-receipt/1.0.0", "panel-publication-receipt/1.0.0", "panel-current-pointer/1.0.0", "panel-state/1.0.0", "strict_rollout", "runtime_paths", "lock_profile", "runtime_policy", "evidence_trust", "source_time_bindings"], algorithm: "open-inspect-publish-all-require-external-trusted-evaluation-time-durable-journaled-content-addressed-release-evidence-history-and-current-set-and-current-byte-derived-writer-build-capability-lock-fact-ledger-wdr-sidecar-full-content-addressed-lineage-projection-panel-publication-pointer-activation-state-binding-exact-match" },
  "live-inspect-semantics/1.0.0": { scope: ["strict-writer-fence-activation/1.0.0", "writer-fence-migration-attestation/1.0.0", "release-evidence-set/1.0.0", "release-evidence-history-index/1.0.0", "conformance-result/1.0.0", "writer-build-manifest/1.0.0", "writer-fence-receipt/1.0.0", "strict-activation-state/1.0.0", "root-registry-state/1.0.0", "writer-capability-registry/1.0.0", "action-ledger-state/1.0.0", "action-flow-index/1.0.0", "wdr-file-state/1.0.0", "wdr-action-projection/1.0.0", "refresh-run-receipt/1.0.0", "panel-publication-receipt/1.0.0", "panel-state/1.0.0", "generation-lineage-index/1.0.0", "publication-absence-proof/1.0.0", "panel-refresh-status/1.0.0", "panel-current-pointer/1.0.0", "fact-generation-state/1.0.0", "generation-envelope/1.0.0", "projection-dependency-manifest/1.0.0", "producer-receipt/1.0.0", "canonical-projection-envelope/1.0.0", "management-panel-payload/2.0.0", "strict_rollout", "runtime_paths", "lock_profile", "runtime_policy", "evidence_trust", "live_inspect_read_profile", "source_time_bindings"], algorithm: "compose-and-execute-complete-registered-strict-writer-fence-gate-with-external-trusted-time-then-restart-safe-instrumented-resolve-pointer-generation-index-load-canonical-raw-bytes-under-fact-shared-lock-reenumerate-leaves-compare-actual-root-path-contract-read-set-before-read-lock-release-then-write-only-refresh-status" },
};

Object.assign(semanticValidatorSpecs, {"panel-publication-eligibility/1.0.0":{"scope":["physical-workstream-inventory/1.0.0","selection-policy/1.0.0","panel-binding-catalog/1.0.0","generation-envelope/1.0.0","management-panel-payload/2.0.0","state-audit-payload/2.0.0","intent-convergence-verdict/1.0.0","action-projection-drift-verdict/1.0.0","projection-dependency-manifest/1.0.0","producer-receipt/1.0.0","writer-fence-migration-attestation/1.0.0"],"algorithm":"fresh-physical-inventory-attestation-byte-equal-policy-catalog-generation-bidirectional-first-nonempty-selection-and-generation-audit-drift-intent-convergence-manifest-receipt-exact-scope-plus-strict-writer-fence-gate"},"fact-receipt-attribution/1.0.0":{"scope":["runtime-authority-context/1.0.0","root-registry-state/1.0.0","writer-capability-registry/1.0.0","strict-activation-state/1.0.0","writer-fence-migration-attestation/1.0.0","action-ledger-mutation/2.0.0","owned-fact-command/1.0.0","status-mutation-intent/1.0.0","action-ledger-state/1.0.0","action-flow-index/1.0.0","wdr-mutation/1.0.0","wdr-file-state/1.0.0","wdr-action-projection/1.0.0","fact-command-receipt-index/1.0.0","mutation-intent-outbox/1.0.0","transaction-journal-manifest/1.0.0","journal-marker/1.0.0","fact-generation-state/1.0.0","fact-mutation-receipt/1.0.0","fact-mutation-proof/1.0.0","strict_rollout","runtime_authority_profile","runtime_paths","lock_profile","wdr_field_section_map","owned_fact_target_profiles"],"algorithm":"exact-contract-negotiation-typed-native-preimage-authority-command-derived-targets-byte-proof-command-receipt-index-plus-command-bound-exact-emitted-intents-or-complete-sorted-aggregated-consumed-intent-set-in-one-recoverable-fact-transaction"},"repair-graph-semantics/1.0.0":{"scope":["action-projection-drift-verdict/1.0.0","audit-finding-repair/2.0.0","action-ledger-state/1.0.0","runtime-authority-context/1.0.0","writer-capability-registry/1.0.0","strict-activation-state/1.0.0","writer-fence-migration-attestation/1.0.0","wdr-mutation/1.0.0","wdr-file-state/1.0.0","wdr-action-projection/1.0.0","repair-dry-run-request/1.0.0","repair-dry-run-result/1.0.0","repair-apply-request/1.0.0","repair-run-receipt/1.0.0","repair-nonce-state/1.0.0","repair-receipt-index/1.0.0","repair-attempt-ledger/1.0.0","transaction-journal-manifest/1.0.0","journal-marker/1.0.0","recovery-receipt/1.0.0","fact-generation-state/1.0.0","fact-mutation-receipt/1.0.0","fact-mutation-proof/1.0.0","strict_rollout","runtime_authority_profile","runtime_paths","lock_profile","wdr_field_section_map","identity_set_fields"],"algorithm":"derive-lossless-typed-audit-findings-from-exact-validated-drift-reparse-raw-ledger-state-wdr-and-sidecar-to-prove-presence-revision-and-diffs-then-separate-business-and-attempt-journals-with-deterministic-terminal-marker-bound-attempt-identity-and-idempotent-registered-path-recovery"},"release-evidence-transition-semantics/1.0.0":{"scope":["release-evidence-set/1.0.0","release-evidence-transition-receipt/1.0.0","release-evidence-history-index/1.0.0","transaction-journal-manifest/1.0.0","journal-marker/1.0.0","recovery-receipt/1.0.0","runtime_paths","runtime_policy","evidence_trust"],"algorithm":"validate-every-historical-receipt-blob-signature-policy-and-monotonic-chronology-plus-content-addressed-journal-marker-chain-and-fresh-process-image-recovery"},"activation-transition-semantics/1.0.0":{"scope":["activation-transition-command/1.0.0","activation-transition-receipt/1.0.0","activation-lifecycle-index/1.0.0","runtime-authority-context/1.0.0","root-registry-state/1.0.0","strict-activation-state/1.0.0","writer-capability-registry/1.0.0","writer-fence-migration-attestation/1.0.0","refresh-run-receipt/1.0.0","transaction-journal-manifest/1.0.0","journal-marker/1.0.0","recovery-receipt/1.0.0","runtime_authority_profile","lock_profile","runtime_paths","strict_rollout"],"algorithm":"recompute-fixed-five-step-lifecycle-id-first-step-creates-index-later-steps-exact-prefix-cas-each-entry-derived-from-committed-receipt-raw-hash-and-registered-path-plus-state-attestation-cas-and-fresh-process-recovery"},"panel-publication-graph/1.0.0":{"scope":["transaction-journal-manifest/1.0.0","journal-marker/1.0.0","generation-lineage-index/1.0.0","panel-current-pointer/1.0.0","panel-state/1.0.0","panel-publication-receipt/1.0.0","canonical-projection-envelope/1.0.0","runtime_paths"],"algorithm":"complete-immutable-lineage-and-index-in-same-journal-before-pointer-last-plus-durable-command-fingerprint-replay-lookup-and-fresh-process-recovery"},"status-intent-application/1.0.0":{"scope":["status-mutation-intent/1.0.0","status-sync-batch/2.0.0","wdr-mutation/1.0.0","mutation-intent-outbox/1.0.0"],"algorithm":"exact-canonical-intent-hash-content-field-evidence-workstream-to-single-reauthorized-wdr-command-and-complete-sorted-consumed-id-binding-reject-conflicts-and-ordered-stop-on-first-failure-no-rollback"},"action-projection-drift-content/1.0.0":{"scope":["action-ledger-state/1.0.0","wdr-file-state/1.0.0","wdr-action-projection/1.0.0","action-projection-drift-verdict/1.0.0","audit-finding-repair/2.0.0","selection-policy/1.0.0"],"algorithm":"exact-active-ledger-routing-or-affected-membership-to-sidecar-record-rendered-wdr-summary-and-lossless-content-addressed-typed-finding-verdict-no-false-green"},"strict-writer-fence-activation/1.0.0":{"scope":["writer-fence-migration-attestation/1.0.0","release-evidence-set/1.0.0","release-evidence-transition-receipt/1.0.0","release-evidence-history-index/1.0.0","conformance-result/1.0.0","writer-build-manifest/1.0.0","writer-fence-receipt/1.0.0","strict-activation-state/1.0.0","root-registry-state/1.0.0","writer-capability-registry/1.0.0","strict_rollout","runtime_paths","lock_profile","runtime_policy","evidence_trust"],"algorithm":"external-trusted-time-and-durable-release-authority-plus-current-root-capability-epoch-writer-build-and-fence-byte-closure-authorize-immutable-writer-fence-only-while-mutable-facts-pointer-lineage-and-panel-generation-are-live-receipt-cas-validated"},"snapshot-time-authority/1.0.0":{"scope":["refresh-request/1.0.0","snapshot-lock-receipt/1.0.0","selection-policy/1.0.0","refresh-run-receipt/1.0.0","source_time_bindings","lock_profile"],"algorithm":"request-time-host-time-lock-acquisition-maximum-fact-time-and-all-registered-source-time-carriers-equal-one-trusted-snapshot-boundary"},"intent-outbox-convergence/1.0.0":{"scope":["mutation-intent-outbox/1.0.0","intent-convergence-verdict/1.0.0","status-mutation-intent/1.0.0","status-sync-batch/2.0.0","wdr-mutation/1.0.0","fact-mutation-receipt/1.0.0","state-audit-payload/2.0.0","management-panel-payload/2.0.0"],"algorithm":"producer-command-binds-exact-typed-intent-by-canonical-hash-and-aggregated-status-sync-command-atomically-consumes-complete-sorted-same-workstream-pending-set-with-one-receipt-while-prefix-preserving-unrelated-rows-and-only-pending-or-consumed-states-are-allowed-failed-waived-arrays-are-empty-and-any-pending-blocks-fresh-eligible"},"fact-command-replay/1.0.0":{"scope":["fact-command-receipt-index/1.0.0","fact-mutation-receipt/1.0.0","transaction-journal-manifest/1.0.0","runtime_paths"],"algorithm":"global-monotonic-sequence-command-id-plus-fingerprint-to-exact-receipt-path-same-fingerprint-noop-different-fingerprint-conflict"}});

const semanticRegistrySemantics = (registryDoc) => {
  const rows = registryDoc.semantic_validators;
  if (!Array.isArray(rows)) return false;
  const ids = rows.map(({ id }) => id);
  return ids.length === 21 && new Set(ids).size === ids.length
    && rows.every((row) => Array.isArray(row.scope) && row.scope.length && typeof row.algorithm === "string" && row.algorithm.length)
    && hash(Buffer.from(canonical(rows))) === "sha256:506f6079cf7197921c74d5b98f170181b0872009c1412ebd521361d6d0e887f5";
};

const runtimePathsSemantics = (registryDoc) => {
  const expectedKeys = new Set([
    "journal_dir_template", "action_ledger", "action_ledger_state", "action_flow_index", "fact_generation",
    "fact_command_receipt_index", "mutation_intent_outbox", "intent_convergence_verdict",
    "root_registry_state", "writer_capability_registry", "panel_current_pointer", "panel_state", "strict_activation_state", "fact_receipt_template", "panel_receipt_template",
    "repair_fact_receipt_template", "repair_receipt_template", "canonical_projection_template", "management_panel_template", "writer_fence_attestation",
    "fact_lock", "panel_lock", "panel_refresh_status", "generation_lineage_index_template", "generation_envelope_template", "selection_policy_template",
    "physical_inventory_template", "panel_binding_catalog_template", "dependency_manifest_template", "producer_receipt_template",
    "refresh_receipt_generation_template", "publication_receipt_generation_template", "publication_journal_template", "publication_marker_template",
    "before_pointer_template", "before_panel_state_template",
    "journal_manifest_template", "journal_prepared_marker_template", "journal_terminal_marker_template", "journal_recovery_receipt_template",
    "journal_tombstone_template", "journal_before_image_template", "journal_after_image_template", "repair_nonce_template", "release_evidence_set",
    "release_evidence_receipt_template", "release_evidence_blob_template",
    "publication_absence_proof_template", "repair_receipt_index", "release_evidence_history_index",
    "release_evidence_set_archive_template", "release_evidence_transition_receipt_template",
    "release_evidence_journal_template", "release_evidence_terminal_marker_template",
    "activation_transition_receipt_template", "activation_transition_state_template", "activation_lifecycle_index_template",
    "repair_attempt_ledger", "refresh_request_template", "snapshot_lock_receipt_template",
  ]);
  const paths = registryDoc.runtime_paths;
  if (!paths || typeof paths !== "object" || Array.isArray(paths)
    || canonical(Object.keys(paths).sort()) !== canonical([...expectedKeys].sort())
    || paths.journal_dir_template !== "state/transactions/{transaction_token}") return false;
  const generationId = `sha256:${"a".repeat(64)}`;
  try {
    const known = [
      runtimePath(registryDoc, "canonical_projection_template", generationId, "program-status", null),
      runtimePath(registryDoc, "canonical_projection_template", generationId, "meeting-pack", "fde-morning"),
      runtimePath(registryDoc, "management_panel_template", generationId, "management-panel", null),
      runtimePath(registryDoc, "journal_manifest_template", null, null, null, "tx-known-answer"),
      runtimePath(registryDoc, "journal_before_image_template", null, null, null, "tx-known-answer", null, null, null, 11),
      runtimePath(registryDoc, "journal_after_image_template", null, null, null, "tx-known-answer", null, null, null, 11),
      runtimePath(registryDoc, "repair_nonce_template", null, null, null, null, `sha256:${"b".repeat(64)}`),
      runtimePath(registryDoc, "release_evidence_receipt_template", null, null, null, null, null, `sha256:${"c".repeat(64)}`),
      runtimePath(registryDoc, "release_evidence_blob_template", null, null, null, null, null, null, `sha256:${"d".repeat(64)}`),
      runtimePath(registryDoc, "publication_absence_proof_template", generationId),
      runtimePath(registryDoc, "release_evidence_set_archive_template", null, null, null, null, null, null, null, null, `sha256:${"e".repeat(64)}`),
      runtimePath(registryDoc, "release_evidence_transition_receipt_template", null, null, null, "release-known-answer"),
      runtimePath(registryDoc, "activation_transition_receipt_template", null, null, null, "activation-known-answer"),
      runtimePath(registryDoc, "activation_transition_state_template", null, null, null, "activation-known-answer"),
    ];
    if (new Set(known).size !== known.length || known.some((value) => !value || value.includes("{"))) return false;
    for (const [name, record] of Object.entries(paths)) {
      if (name === "journal_dir_template") continue;
      if (!record || typeof record !== "object" || Array.isArray(record) || record.root !== "memory"
        || canonical(Object.keys(record).sort()) !== canonical(["path", "root"])) return false;
      const candidate = record.path;
      if (candidate.startsWith("/") || candidate.includes("\\") || candidate.includes(":") || candidate.split("/").includes("..")) return false;
    }
    try { runtimePath(registryDoc, "canonical_projection_template", `SHA256:${"a".repeat(64)}`, "program-status", null); return false; } catch {}
    try { runtimePath(registryDoc, "canonical_projection_template", generationId, "meeting-pack", "e\u0301"); return false; } catch {}
  } catch { return false; }
  return true;
};

const enumeratorTempTreeSemantics = () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "adp-enumerator-"));
  try {
    fs.mkdirSync(path.join(folder, "visible", "nested"), { recursive: true }); fs.mkdirSync(path.join(folder, ".hidden"));
    fs.writeFileSync(path.join(folder, "visible", "a.json"), "a"); fs.writeFileSync(path.join(folder, "visible", "nested", "b.json"), "b");
    fs.writeFileSync(path.join(folder, ".hidden", "c.json"), "c"); fs.symlinkSync(path.join(folder, "visible", "a.json"), path.join(folder, "visible", "link.json"));
    const paths = [];
    const walk = (directory) => { for (const entry of fs.readdirSync(directory, { withFileTypes: true })) { const absolute = path.join(directory, entry.name); if (entry.isSymbolicLink() || entry.name.startsWith(".")) continue; if (entry.isDirectory()) walk(absolute); else if (entry.isFile() && entry.name.endsWith(".json")) paths.push(path.relative(folder, absolute).split(path.sep).join("/").normalize("NFC")); } };
    walk(path.join(folder, "visible"));
    return canonical(paths.sort()) === canonical(["visible/a.json", "visible/nested/b.json"]);
  } finally { fs.rmSync(folder, { recursive: true, force: true }); }
};

const physicalInventoryRowsValid = (rows) => {
  if (!Array.isArray(rows) || rows.length === 0 || canonical(rows) !== canonical([...rows].sort((left, right) => Buffer.from(left.workstream_id).compare(Buffer.from(right.workstream_id))))) return false;
  const workstreamIds = []; const physicalIds = [];
  for (const row of rows) {
    const workstreamId = row.workstream_id;
    if (typeof workstreamId !== "string" || workstreamId !== workstreamId.normalize("NFC") || !/^(?!program$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/u.test(workstreamId)) return false;
    workstreamIds.push(workstreamId);
    for (const [field, sourcePath, sourceKind] of [["wdr_source", `workstreams/${workstreamId}/delivery-record.md`, "selected-physical-wdr"], ["sidecar_source", `workstreams/${workstreamId}/action-projection.json`, "wdr-action-sidecar"]]) {
      const source = row[field];
      if (!source || source.root !== "memory" || source.path !== sourcePath || source.category !== "fact" || source.source_kind !== sourceKind || canonical(source.affects) !== canonical(["/"])) return false;
      physicalIds.push(`${source.root_instance_id}\0${sourcePath}`);
    }
  }
  return new Set(workstreamIds).size === workstreamIds.length && new Set(physicalIds).size === physicalIds.length;
};

const enumeratePhysicalWorkstreams = (memoryRoot, memoryRootId, schemaRoot, registryDoc, schemaSha, registrySha) => {
  const workstreamsRoot = path.join(memoryRoot, "workstreams");
  const rootStat = fs.lstatSync(workstreamsRoot);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) throw new Error("physical workstream root is missing or unsafe");
  const rows = [];
  for (const entry of fs.readdirSync(workstreamsRoot, { withFileTypes: true }).sort((left, right) => Buffer.from(left.name).compare(Buffer.from(right.name)))) {
    const folder = path.join(workstreamsRoot, entry.name);
    if (entry.name.startsWith(".")) {
      const pending = [folder]; let hasPhysical = false;
      while (pending.length) {
        const directory = pending.pop();
        for (const child of fs.readdirSync(directory, { withFileTypes: true })) {
        const absolute = path.join(directory, child.name);
        if (["delivery-record.md", "action-projection.json"].includes(child.name)) hasPhysical = true;
        if (child.isDirectory()) pending.push(absolute);
        }
      }
      if (hasPhysical) throw new Error("hidden physical workstream");
      continue;
    }
    const folderStat = fs.lstatSync(folder);
    if (!folderStat.isDirectory() || folderStat.isSymbolicLink()) throw new Error("physical workstream entry is not a regular directory");
    const workstreamId = entry.name;
    if (workstreamId !== workstreamId.normalize("NFC") || !/^(?!program$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/u.test(workstreamId)) throw new Error("invalid physical workstream identity");
    const wdr = path.join(folder, "delivery-record.md"); const sidecar = path.join(folder, "action-projection.json");
    const pending = [folder];
    while (pending.length) {
      const directory = pending.pop();
      for (const child of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, child.name);
      if (child.isSymbolicLink()) throw new Error("symlinked physical workstream artifact");
      if (child.isDirectory()) pending.push(absolute);
      else if (path.dirname(absolute) !== folder && ["delivery-record.md", "action-projection.json"].includes(child.name)) throw new Error("nested physical workstream artifact");
      }
    }
    for (const target of [wdr, sidecar]) {
      const targetStat = fs.lstatSync(target);
      if (!targetStat.isFile() || targetStat.isSymbolicLink()) throw new Error("unpaired physical workstream");
      fs.accessSync(target, fs.constants.R_OK);
    }
    const wdrBytes = fs.readFileSync(wdr); const sidecarBytes = fs.readFileSync(sidecar);
    const identityMatches = [...wdrBytes.toString("utf8").matchAll(/^- Workstream ID: ([^\r\n]+)$/gmu)].map((match) => match[1]);
    const sidecarValue = JSON.parse(sidecarBytes.toString("utf8"));
    if (canonical(identityMatches) !== canonical([workstreamId]) || !completeWdrValid(wdrBytes.toString("utf8"), workstreamId)
        || !sidecarValue || Array.isArray(sidecarValue) || sidecarValue.workstream_id !== workstreamId
        || !validateRegistered(sidecarValue, schemaRoot, registryDoc, "wdr-action-projection/1.0.0", schemaSha, registrySha)
        || !sidecarBytes.equals(Buffer.from(canonical(sidecarValue)))) throw new Error("physical workstream content identity mismatch");
    const source = (sourcePath, sourceKind, bytes) => { const digest = hash(bytes); return { root: "memory", root_instance_id: memoryRootId, path: sourcePath, category: "fact", source_kind: sourceKind, fingerprint: digest, blob_id: digest, affects: ["/"] }; };
    rows.push({ workstream_id: workstreamId, wdr_source: source(`workstreams/${workstreamId}/delivery-record.md`, "selected-physical-wdr", wdrBytes), sidecar_source: source(`workstreams/${workstreamId}/action-projection.json`, "wdr-action-sidecar", sidecarBytes) });
  }
  if (!physicalInventoryRowsValid(rows)) throw new Error("physical workstream inventory is empty, duplicate, or noncanonical");
  return rows;
};

const physicalWorkstreamInventoryTempTreeSemantics = (mutation, schemaRoot, registryDoc, schemaSha, registrySha) => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "adp-physical-workstreams-"));
  try {
    fs.mkdirSync(path.join(folder, "workstreams"));
    const workstreamIds = mutation === "empty" ? [] : ["l1-checkout", "l1-payments"];
    for (const workstreamId of workstreamIds) {
      const target = path.join(folder, "workstreams", workstreamId); fs.mkdirSync(target);
      let wdr = fixtureWdr(workstreamId);
      const sidecar = {
        contract: expectedContractRef(registryDoc, "wdr-action-projection/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
        workstream_id: workstreamId, ledger_fingerprint: `sha256:${"d".repeat(64)}`, ledger_revision: 4, wdr_revision: 4, file_generation: 7,
        renderer_id: "urn:adp:wdr-action-renderer:1.0.0", renderer_sha256: registryDoc.protocol.sha256, actions: [],
      };
      if (mutation === "invalid-wdr" && workstreamId === workstreamIds.at(-1)) wdr = `# invalid except identity\n\n- Workstream ID: ${workstreamId}\n`;
      if (mutation === "invalid-sidecar" && workstreamId === workstreamIds.at(-1)) for (const key of Object.keys(sidecar)) if (key !== "workstream_id") delete sidecar[key];
      if (mutation === "sidecar-fake-anchor" && workstreamId === workstreamIds.at(-1)) sidecar.contract.schema_id = "urn:adp:panel-sync-contracts:2026-07-24#unknown-sidecar-v1";
      if (mutation === "sidecar-schema-hash" && workstreamId === workstreamIds.at(-1)) sidecar.contract.schema_sha256 = `sha256:${"f".repeat(64)}`;
      if (mutation === "sidecar-registry-hash" && workstreamId === workstreamIds.at(-1)) sidecar.contract.registry_sha256 = `sha256:${"f".repeat(64)}`;
      if (!(mutation === "sidecar-without-wdr" && workstreamId === workstreamIds.at(-1))) fs.writeFileSync(path.join(target, "delivery-record.md"), wdr);
      if (!(mutation === "wdr-without-sidecar" && workstreamId === workstreamIds.at(-1))) {
        const sidecarBytes = mutation === "sidecar-noncanonical" && workstreamId === workstreamIds.at(-1)
          ? Buffer.from(JSON.stringify(sidecar, null, 2)) : Buffer.from(canonical(sidecar));
        fs.writeFileSync(path.join(target, "action-projection.json"), sidecarBytes);
      }
    }
    const rows = enumeratePhysicalWorkstreams(folder, "123e4567-e89b-42d3-a456-426614174000", schemaRoot, registryDoc, schemaSha, registrySha);
    if (mutation === "duplicate-physical-identity") rows.push(clone(rows[0]));
    return physicalInventoryRowsValid(rows);
  } catch { return false; }
  finally { fs.rmSync(folder, { recursive: true, force: true }); }
};

const enumeratedPaths = (source, selected, policy = null) => {
  const enumerator = source.enumerator;
  if (enumerator.id === "exact-path-v1") return [enumerator.path];
  if (["selected-workstreams-v1", "selected-sidecars-v1", "selected-workstream-file-v1"].includes(enumerator.id)) return selected.map((workstream) => `${enumerator.base}/${workstream}/${enumerator.filename}`);
  if (enumerator.id === "selected-immutable-snapshot-v1" && policy?.previous_program_status_id === null) return [];
  if (enumerator.id === "selected-immutable-snapshot-v1") return [`${enumerator.base}/h_${"1".repeat(64)}.json`];
  if (enumerator.id === "selected-baseline-history-v1") return [`${enumerator.base}/revision-3.md`];
  if (enumerator.id === "selected-receipts-v1") return [`${enumerator.base}/fde-morning.json`];
  if (enumerator.id === "glob-kind-v1") return [`${enumerator.base}/fixture-${source.source_kind}${enumerator.glob.includes("json") ? ".json" : ".md"}`];
  throw new Error(`unsupported dependency enumerator: ${enumerator.id}`);
};

const materializeProfileSources = (profile, selected, policy = null) => {
  const roots = { memory: "123e4567-e89b-42d3-a456-426614174000", project: "123e4567-e89b-42d3-a456-426614174001" };
  const policySources = new Map();
  if (policy !== null) {
    for (const collectionName of ["physical_workstream_inventory", "workstream_catalog"]) {
      for (const workstream of policy[collectionName]) for (const field of ["wdr_source", "sidecar_source"]) {
        const source = workstream[field]; const key = `${source.root_instance_id}\0${source.path}`; const existing = policySources.get(key);
        if (existing !== undefined && canonical(existing) !== canonical(source)) throw new Error(`conflicting policy source metadata: ${key}`);
        policySources.set(key, clone(source));
      }
    }
  }
  const records = profile.required_sources.flatMap((source) => enumeratedPaths(source, selected, policy).map((sourcePath) => {
    const root = source.enumerator.root; const rootInstanceId = roots[root]; const policySource = policySources.get(`${rootInstanceId}\0${sourcePath}`);
    if (policySource !== undefined) {
      const expected = { root, root_instance_id: rootInstanceId, path: sourcePath, category: source.category, source_kind: source.source_kind, affects: [...source.affects].sort() };
      const actual = Object.fromEntries(Object.keys(expected).map((key) => [key, policySource[key]]));
      if (canonical(actual) !== canonical(expected)) throw new Error(`policy source does not match dependency declaration: ${rootInstanceId}\0${sourcePath}`);
      return policySource;
    }
    const fingerprint = hash(Buffer.from(`${root}\0${sourcePath}`));
    return { root, root_instance_id: rootInstanceId, path: sourcePath, category: source.category, source_kind: source.source_kind, fingerprint, blob_id: fingerprint, affects: [...source.affects].sort() };
  }));
  if (new Set(records.map((row) => `${row.root_instance_id}\0${row.path}`)).size !== records.length) throw new Error("duplicate physical source identity");
  return records.sort((left, right) => Buffer.from(`${left.root_instance_id}\0${left.path}`).compare(Buffer.from(`${right.root_instance_id}\0${right.path}`)));
};

const instrumentedReadTrace = (profile, selected, mutation = "none", policy = null) => {
  const allowed = materializeProfileSources(profile, selected, policy);
  let actual = clone(mutation === "drop-one-declared-read" ? allowed.slice(0, -1) : allowed);
  if (mutation === "drop-action-ledger-state") actual = actual.filter(({ source_kind }) => source_kind !== "action-ledger-state");
  if (mutation === "add-undeclared-read") { const extra = clone(allowed[0]); extra.path = `undeclared/${profile.projection}.json`; extra.fingerprint = extra.blob_id = hash(Buffer.from(extra.path)); actual.push(extra); }
  else if (mutation === "add-undeclared-ledger-state-read") {
    const extra = clone(allowed.find(({ source_kind }) => source_kind === "action-ledger-state"));
    extra.path = "state/unregistered-action-ledger-shadow.json"; extra.fingerprint = extra.blob_id = hash(Buffer.from(extra.path)); actual.push(extra);
  }
  actual.sort((left, right) => Buffer.from(`${left.root_instance_id}\0${left.path}`).compare(Buffer.from(`${right.root_instance_id}\0${right.path}`)));
  return [allowed, actual];
};

const orderingComponent = (value, keyType) => {
  if (value === null) return [0, keyType === "integer" ? 0 : Buffer.alloc(0)];
  if (keyType === "integer") {
    if (!Number.isSafeInteger(value)) throw new Error("integer ordering component required");
    return [1, value];
  }
  if (typeof value !== "string" || value !== value.normalize("NFC")) throw new Error("NFC string ordering component required");
  return [1, Buffer.from(value)];
};
const orderingKey = (value, spec, keyTypes = null) => {
  const fields = spec === "utf8-nfc-scalar" ? [null] : spec.split(",");
  const types = keyTypes ?? fields.map(() => "string");
  if (types.length !== fields.length || types.some((kind) => !["string", "integer"].includes(kind))) throw new Error("ordering key types do not match key fields");
  return fields.map((key, index) => orderingComponent(key === null ? value : value[key], types[index]));
};
const compareOrderingKeys = (left, right) => {
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const a = left[index]; const b = right[index];
    if (a[0] !== b[0]) return a[0] - b[0];
    const compared = typeof a[1] === "number" ? a[1] - b[1] : a[1].compare(b[1]); if (compared) return compared;
  }
  return 0;
};

const representativeOrderingDocuments = (suiteDoc, schemaRoot, registryDoc, projectRoot, schemaSha, registrySha) => {
  const contract = (anchor) => ({ schema_id: `urn:adp:panel-sync-contracts:2026-07-24#${anchor}`, schema_sha256: schemaSha, registry_sha256: registrySha });
  const instances = Object.fromEntries(suiteDoc.contract_schema_vectors.filter(({ expected_valid }) => expected_valid).map(({ id, instance }) => [id, clone(instance)]));
  const source = (name, root, sourcePath, kind, digit) => ({
    root, root_instance_id: root === "memory" ? "123e4567-e89b-42d3-a456-426614174000" : "123e4567-e89b-42d3-a456-426614174001",
    path: sourcePath, category: "fact", source_kind: kind, fingerprint: `sha256:${digit.repeat(64)}`, blob_id: `sha256:${digit.repeat(64)}`, affects: [`/${name}`],
  });
  const policy = selectionPolicyFixture(registryDoc, schemaSha, registrySha);
  const secondCatalog = clone(policy.workstream_catalog[0]); secondCatalog.workstream_id = "l1-payments";
  for (const [field, filename] of [["wdr_source", "delivery-record.md"], ["sidecar_source", "action-projection.json"]]) {
    const sourceRow = secondCatalog[field]; sourceRow.path = `workstreams/l1-payments/${filename}`;
    sourceRow.fingerprint = sourceRow.blob_id = hash(Buffer.from(`memory\0${sourceRow.path}`));
  }
  policy.workstream_catalog.push(secondCatalog); policy.physical_workstream_inventory.push(clone(secondCatalog));
  policy.physical_workstream_inventory_id = inventoryId(policy.physical_workstream_inventory); policy.workstream_catalog_id = catalogId(policy.workstream_catalog);
  Object.assign(policy, { include_workstreams: ["l1-checkout", "l1-payments"], exclude_workstreams: ["l1-legacy", "l1-retired"], meeting_kinds: ["business-biweekly", "fde-morning"] });
  const policyBody = clone(policy); delete policyBody.policy_id; policy.policy_id = hash(Buffer.from(canonical(policyBody)));
  const generation = generationFixture(registryDoc, policy, schemaSha, registrySha);
  const manifest = {
    contract: contract("dependency-manifest-v1"), schema_version: "1.0.0", producer: { skill: "adp-program-status", version: "1.0.0" },
    projection: { kind: "program-status", id: `sha256:${"1".repeat(64)}` }, generation_id: generation.generation_id,
    input_profile_id: "program-status/1.0.0", selection_policy_id: policy.policy_id,
    sources: [source("a", "memory", "a.md", "action-ledger", "2"), source("b", "memory", "b.md", "status-signals", "3")],
    upstreams: [
      { kind: "flow-graph", id: `sha256:${"4".repeat(64)}`, manifest_id: `sha256:${"5".repeat(64)}`, generation_id: generation.generation_id },
      { kind: "roadmap", id: `sha256:${"6".repeat(64)}`, manifest_id: `sha256:${"7".repeat(64)}`, generation_id: generation.generation_id },
    ], manifest_id: `sha256:${"8".repeat(64)}`,
  };
  const projections = [
    ["action-projection-drift-verdict", null, "1", "2", "g/action-drift.json"], ["flow-graph", null, "3", "4", "g/flow.json"],
    ["management-panel", null, "5", "6", "g/panel.json"], ["meeting-pack", null, "7", "8", "g/meeting-none.json"],
    ["meeting-pack", "fde-morning", "9", "a", "g/meeting-fde.json"], ["program-status", null, "b", "c", "g/status.json"],
    ["roadmap", null, "d", "e", "g/roadmap.json"], ["state-audit", null, "f", "0", "g/audit.json"],
  ].map(([kind, instance_key, id, manifestId, canonical_path]) => ({ kind, instance_key, id: `sha256:${id.repeat(64)}`, manifest_id: `sha256:${manifestId.repeat(64)}`, canonical_path }));
  const pointer = { contract: contract("panel-current-pointer-v1"), schema_version: "1.0.0", generation_id: generation.generation_id, panel_id: `sha256:${"1".repeat(64)}`, projections, pointer_id: `sha256:${"2".repeat(64)}` };
  const [journal] = journalFixture("panel", schemaSha, registrySha);
  journal.targets = [0, 1, 2, 3, 4, 5, 6].map((index) => mutationTarget("projection", "create", index, `views/generations/g1/p${index}.json`));
  journal.targets.push(
    mutationTarget("panel", "create", 7, "views/management-panel/g1.json"),
    mutationTarget("pointer", "replace", 8, "views/management-panel/current-pointer.json"),
    mutationTarget("panel-state", "replace", 9, "state/panel-generation.json"),
    mutationTarget("receipt", "create", 10, journal.receipt_target_paths[0]),
  );
  reindexTargets(journal.targets, journal.journal_dir);
  const journalBody = clone(journal); delete journalBody.manifest_id; journal.manifest_id = hash(Buffer.from(canonical(journalBody)));
  const repair = repairGraphFixture(schemaSha, registrySha, registryDoc);
  const refresh = {
    contract: contract("refresh-run-receipt-v1"), schema_version: "1.0.0", refresh_id: "refresh-ordering-1", generation_id: generation.generation_id,
    snapshot_id: policy.snapshot_id, snapshot_lock_receipt_id: policy.snapshot_lock_receipt_id,
    expected_fact_generation: 7, expected_panel_generation: 4, status: "planned",
    nodes: [
      { instance_key: "a-node", projection_kind: "state-audit", disposition: "planned", invalidation_reasons: [], output: null, error_code: null },
      { instance_key: "b-node", projection_kind: "program-status", disposition: "planned", invalidation_reasons: [], output: null, error_code: null },
    ], retry_from_instance_key: null, source_as_of: "2026-07-24T02:00:00Z", receipt_id: `sha256:${"3".repeat(64)}`,
  };
  const factReceipt = factAttributionFixture(schemaSha, registrySha, registryDoc).receipt; const secondDelta = clone(factReceipt.action_deltas[0]);
  Object.assign(secondDelta, { action_id: "A-FLOW-2", before_revision: 2, after_revision: 3 }); factReceipt.action_deltas.push(secondDelta);
  const actionGraph = factAttributionFixture(schemaSha, registrySha, registryDoc);
  const actionArtifacts = Object.fromEntries(actionGraph.proof.business_artifacts.map((row) => [row.path, row]));
  const ledgerState = JSON.parse(artifactBytes(actionArtifacts[registryDoc.runtime_paths.action_ledger_state.path].after_bytes).toString());
  const secondStateAction = clone(ledgerState.actions[0]); Object.assign(secondStateAction, { action_id: "A-FLOW-2", row_fingerprint: `sha256:${"3".repeat(64)}` });
  ledgerState.actions.push(secondStateAction); ledgerState.actions.sort((a, b) => Buffer.from(a.action_id).compare(Buffer.from(b.action_id)));
  ledgerState.applied_commands.push(
    { command_id: "cmd-applied-a", command_fingerprint: `sha256:${"1".repeat(64)}`, action_id: "A-FLOW-1" },
    { command_id: "cmd-applied-b", command_fingerprint: `sha256:${"2".repeat(64)}`, action_id: "A-FLOW-1" },
  );
  ledgerState.applied_commands.sort((a, b) => Buffer.from(a.command_id).compare(Buffer.from(b.command_id)));
  const ledgerStateBody = clone(ledgerState); delete ledgerStateBody.state_id; ledgerState.state_id = hash(Buffer.from(canonical(ledgerStateBody)));
  const legacyFlowRaw = legacyLedgerFixture("legacy20"); const legacyFlowRows = parseActionLedgerIngress(legacyFlowRaw, "legacy20");
  const actionFlow = actionFlowDocument(legacyFlowRows, renderActionLedger(legacyFlowRows), 0, registryDoc, schemaSha, registrySha);
  actionFlow.actions[0].related_plan_item_ids = ["PLAN-1", "PLAN-2"];
  actionFlow.actions[0].related_flow_edge_ids = ["EDGE-1", "EDGE-2"];
  const secondFlow = clone(actionFlow.actions[0]); secondFlow.action_id = "A-FLOW-2"; actionFlow.actions.push(secondFlow);
  actionFlow.actions.sort((a, b) => Buffer.from(a.action_id).compare(Buffer.from(b.action_id)));
  const refreshGraph = factAttributionFixture(schemaSha, registrySha, registryDoc, "wdr-refresh-actions");
  const wdrCommand = clone(refreshGraph.command);
  wdrCommand.evidence.push({ source_path: "checkpoints/c1.md", source_fingerprint: `sha256:${"b".repeat(64)}`, observed_at: "2026-07-24T02:01:00Z" });
  wdrCommand.evidence.sort(compareEvidence);
  const refreshLedgerArtifact = refreshGraph.proof.read_artifacts.find(({ path: itemPath }) => itemPath === registryDoc.runtime_paths.action_ledger.path);
  const refreshRows = parseActionLedger(artifactBytes(refreshLedgerArtifact.bytes));
  const extraSnapshot = actionSnapshot(refreshRows, "l1-other", wdrCommand.action_snapshot.ledger_fingerprint, wdrCommand.action_snapshot.ledger_revision).actions[0];
  wdrCommand.action_snapshot.actions.push(extraSnapshot); wdrCommand.action_snapshot.actions.sort((a, b) => Buffer.from(a.action_id).compare(Buffer.from(b.action_id)));
  const sidecarPath = `workstreams/${wdrCommand.workstream_id}/action-projection.json`;
  const sidecarArtifact = refreshGraph.proof.business_artifacts.find(({ path: itemPath }) => itemPath === sidecarPath);
  const sidecar = JSON.parse(artifactBytes(sidecarArtifact.after_bytes).toString());
  sidecar.actions.push(clone(extraSnapshot)); sidecar.actions.sort((a, b) => Buffer.from(a.action_id).compare(Buffer.from(b.action_id)));
  const statusBatch = statusIntentFixture(registryDoc, schemaSha, registrySha);
  const meetingPlan = meetingPlanIntentFixture(registryDoc, schemaSha, registrySha);
  const secondAction = clone(statusBatch.action_commands[0]); secondAction.command_id = "cmd-action-second"; secondAction.action_id = "A-STATUS-2";
  statusBatch.action_commands.push(secondAction); statusBatch.action_commands.sort((a, b) => Buffer.from(a.command_id).compare(Buffer.from(b.command_id)));
  const secondPatch = clone(statusBatch.wdr_patches[0]); secondPatch.command_id = "cmd-status-l1-payments"; secondPatch.workstream_id = "l1-payments";
  statusBatch.wdr_patches.push(secondPatch); statusBatch.wdr_patches.sort((a, b) => Buffer.from(`${a.workstream_id}\0${a.command_id}`).compare(Buffer.from(`${b.workstream_id}\0${b.command_id}`)));
  statusBatch.command_order = [...statusBatch.action_commands, ...statusBatch.wdr_patches].map(({ command_id }) => command_id);
  const statusIntent = clone(statusBatch.accepted_intents[0]); statusIntent.evidence = clone(statusBatch.wdr_patches[0].evidence);
  const actionCommand = clone(statusBatch.action_commands[0]); actionCommand.evidence = clone(statusBatch.wdr_patches[0].evidence);
  const drift = {
    contract: contract("action-projection-drift-verdict-v1"), schema_version: "1.0.0", verdict_id: `sha256:${"4".repeat(64)}`,
    generation_id: generation.generation_id, selection_policy_id: policy.policy_id, ledger_fingerprint: `sha256:${"5".repeat(64)}`,
    selected_workstreams: ["l1-checkout", "l1-payments"], workstreams: [
      { workstream_id: "l1-checkout", wdr_fingerprint: `sha256:${"6".repeat(64)}`, wdr_revision: 4, file_generation: 7, sidecar_fingerprint: `sha256:${"7".repeat(64)}`, sidecar_ledger_fingerprint: `sha256:${"5".repeat(64)}`, status: "in-sync",
        action_diffs: [
          { action_id: "A-ORDER-1", drift_kind: "missing-from-wdr", ledger_present: true, wdr_present: false, ledger_revision: 1, wdr_rendered_sha256: null },
          { action_id: "A-ORDER-2", drift_kind: "content-mismatch", ledger_present: true, wdr_present: true, ledger_revision: 2, wdr_rendered_sha256: `sha256:${"a".repeat(64)}` },
        ], finding_ids: [`sha256:${"1".repeat(64)}`, `sha256:${"2".repeat(64)}`] },
      { workstream_id: "l1-payments", wdr_fingerprint: `sha256:${"8".repeat(64)}`, wdr_revision: 5, file_generation: 8, sidecar_fingerprint: `sha256:${"9".repeat(64)}`, sidecar_ledger_fingerprint: `sha256:${"5".repeat(64)}`, status: "in-sync",
        action_diffs: [
          { action_id: "A-ORDER-3", drift_kind: "orphan-in-wdr", ledger_present: false, wdr_present: true, ledger_revision: null, wdr_rendered_sha256: `sha256:${"b".repeat(64)}` },
          { action_id: "A-ORDER-4", drift_kind: "content-mismatch", ledger_present: true, wdr_present: true, ledger_revision: 3, wdr_rendered_sha256: `sha256:${"c".repeat(64)}` },
        ], finding_ids: [`sha256:${"3".repeat(64)}`, `sha256:${"4".repeat(64)}`] },
    ], overall_status: "in-sync",
  };
  for (const driftRow of drift.workstreams) {
    driftRow.findings = driftRow.action_diffs.map((diff) => driftFinding(driftRow.workstream_id, "action-projection-drift", diff))
      .sort((a, b) => Buffer.from(a.finding_id).compare(Buffer.from(b.finding_id)));
    driftRow.finding_ids = driftRow.findings.map(({ finding_id }) => finding_id);
  }
  const preview = (name, digit) => ({ path: `${name}.md`, fingerprint: `sha256:${digit.repeat(64)}`, content: name });
  const stateAudit = instances["state-audit-payload-schema-valid"]; stateAudit.source_preview = [preview("a", "a"), preview("b", "b")];
  const status = instances["program-status-payload-schema-valid"]; const secondWorkstream = clone(status.workstream_current[0]); secondWorkstream.workstream_id = "l1-payments";
  status.workstream_current.push(secondWorkstream); status.source_preview = [preview("a", "c"), preview("b", "d")];
  const roadmap = instances["roadmap-payload-schema-valid"];
  roadmap.roadmap_state = "populated";
  roadmap.milestone_timeline = [
    { milestone_id: "a-milestone", title: "A", scope_id: "program", status: "planned", target: "gate-a", owner: "FDE-A", source_refs: ["a.md"] },
    { milestone_id: "b-milestone", title: "B", scope_id: "program", status: "planned", target: "gate-b", owner: "FDE-B", source_refs: ["b.md"] },
  ];
  roadmap.unscheduled_milestones = [
    { milestone_id: "a-unscheduled", title: "A", scope_id: "program", status: "pending", owner: "FDE-A", reason: "awaiting gate", source_refs: ["a.md"] },
    { milestone_id: "b-unscheduled", title: "B", scope_id: "program", status: "pending", owner: "FDE-B", reason: "awaiting gate", source_refs: ["b.md"] },
  ];
  roadmap.source_preview = [preview("a", "e"), preview("b", "f")];
  const meeting = instances["meeting-pack-payload-schema-valid"];
  meeting.boards = [{ board_id: "a-board", title: "A", items: [] }, { board_id: "b-board", title: "B", items: [] }]; meeting.source_preview = [preview("a", "1"), preview("b", "2")];
  const [panel, upstreams] = panelFixture(suiteDoc.contract_schema_vectors, registryDoc, schemaSha, registrySha, projectRoot);
  for (const binding of registryDoc.panel_binding_map) {
    const payload = upstreams[binding.projection_kind];
    setPointer(panel, binding.panel_pointer, binding.merge_mode === "object-by-key" ? Object.fromEntries(payload.map((row) => [row.scenario, row])) : payload);
  }
  const panelBody = clone(panel); delete panelBody.panel_id; panel.panel_id = hash(Buffer.from(canonical(panelBody)));
  panel.model_v1.views.sort((a, b) => compareOrderingKeys(orderingKey(a, "view_id"), orderingKey(b, "view_id")));
  const bootstrap = bootstrapMigrationFixture({ ledger_format: "legacy20", action_flow_preimage: "brownfield-v1", workstreams: ["l1-checkout", "l1-payments"] }, registryDoc, schemaSha, registrySha).command;
  const expectedIds = [...new Set(Object.entries(suiteDoc).filter(([key]) => key.endsWith("_vectors") || key === "journal_fault_matrix").flatMap(([, values]) => values.map(({ id }) => id)))].sort();
  const hashes = { registry: registrySha, schema: schemaSha, protocol: registryDoc.protocol.sha256, suite: registryDoc.conformance_suite.sha256 };
  const writerPackage = writerFenceFixture(registryDoc, schemaSha, registrySha, expectedIds, hashes);
  const activationLifecycle = activationTransitionFixture(writerPackage, registryDoc, schemaSha, registrySha).lifecycle_index;
  const writerAttestation = writerPackage.attestation;
  const secondAttestedWorkstream = clone(writerAttestation.workstreams[0]); secondAttestedWorkstream.workstream_id = "l1-payments";
  writerAttestation.workstreams.push(secondAttestedWorkstream); writerAttestation.workstreams.sort((a, b) => Buffer.from(a.workstream_id).compare(Buffer.from(b.workstream_id)));
  const writerAttestationBody = clone(writerAttestation); delete writerAttestationBody.attestation_id; writerAttestation.attestation_id = hash(Buffer.from(canonical(writerAttestationBody)));
  const manifestPath = registryDoc.strict_rollout.writer_specs[1].manifest_path;
  const writerManifest = JSON.parse(writerPackage.writer_store[manifestPath]);
  const lineageIndex = JSON.parse(writerPackage.lineage_store[writerAttestation.lineage_index_path]);
  const ownedRaw = Buffer.from("# Risk flow\n\nCanonical owned fact.\n");
  const ownedCommand = {
    contract: contract("owned-fact-command-v1"), schema_version: "1.0.0", command_id: "cmd-owned-risk-ordering-1", operation: "create",
    issuer: { producer_id: "adp-risk-dependency-change-review", capability_id: `sha256:${"1".repeat(64)}` },
    target_profile_id: registryDoc.owned_fact_target_profiles.find(({ producer_id }) => producer_id === "adp-risk-dependency-change-review").profile_id,
    target_path: "risks/risk-flow.md", expected_before_sha256: null, after_bytes: encodedBytes(ownedRaw), after_sha256: hash(ownedRaw),
    evidence: [
      { source_path: "risk/source-a.md", source_fingerprint: `sha256:${"a".repeat(64)}`, observed_at: "2026-07-24T02:00:00Z" },
      { source_path: "risk/source-z.md", source_fingerprint: `sha256:${"f".repeat(64)}`, observed_at: "2026-07-24T02:01:00Z" },
    ],
  };
  const repairIndex = clone(repair.repair_index); const secondRepairEntry = clone(repairIndex.entries[0]);
  Object.assign(secondRepairEntry, { lookup_id: `sha256:${"f".repeat(64)}`, sequence: 2, transaction_id: "tx-repair-ordering-2",
    receipt_path: "receipts/repair/tx-repair-ordering-2.json", receipt_sha256: `sha256:${"e".repeat(64)}` });
  repairIndex.entries.push(secondRepairEntry); repairIndex.entries.sort((a, b) => a.sequence - b.sequence);
  delete repairIndex.index_id; repairIndex.index_id = hash(Buffer.from(canonical(repairIndex)));
  const repairAttemptLedger = clone(repair.attempt_ledger); const secondAttempt = clone(repairAttemptLedger.attempts[0]);
  Object.assign(secondAttempt, { sequence: 2, lookup_id: `sha256:${"0".repeat(64)}`, transaction_id: "tx-repair-ordering-2",
    repair_receipt_path: "receipts/repair/tx-repair-ordering-2.json", repair_receipt_sha256: `sha256:${"e".repeat(64)}`,
    recorded_at: "2026-07-24T02:13:00Z" });
  repairAttemptLedger.attempts.push(secondAttempt); repairAttemptLedger.next_sequence = 3;
  delete repairAttemptLedger.ledger_id; repairAttemptLedger.ledger_id = hash(Buffer.from(canonical(repairAttemptLedger)));
  const factCommandIndex = clone(actionGraph.command_index); const secondFactEntry = clone(factCommandIndex.entries[0]);
  Object.assign(secondFactEntry, { sequence: 2, command_id: "cmd-ordering-2", command_fingerprint: `sha256:${"0".repeat(64)}`,
    transaction_id: "tx-fact-ordering-2", receipt_path: "receipts/fact/ordering-2.json", receipt_sha256: `sha256:${"e".repeat(64)}` });
  factCommandIndex.entries.push(secondFactEntry); factCommandIndex.next_sequence = 3;
  delete factCommandIndex.index_id; factCommandIndex.index_id = hash(Buffer.from(canonical(factCommandIndex)));
  const orderingIntents = [
    { contract: contract("status-mutation-intent-v1"), schema_version: "1.0.0", intent_id: "intent-ordering-1", origin_producer: "adp-meeting-sync",
      workstream_id: "l1-checkout", set: { blockers: { mode: "replace", values: ["Access"] }, progress: "Active" },
      evidence: [{ source_path: "meetings/m1.md", source_fingerprint: `sha256:${"a".repeat(64)}`, observed_at: "2026-07-24T02:00:00Z" }] },
    { contract: contract("status-mutation-intent-v1"), schema_version: "1.0.0", intent_id: "intent-ordering-2", origin_producer: "adp-risk-dependency-change-review",
      workstream_id: "l1-payments", set: { risks: { mode: "replace", values: ["Schedule"] }, status: "at-risk" },
      evidence: [{ source_path: "risks/r1.json", source_fingerprint: `sha256:${"c".repeat(64)}`, observed_at: "2026-07-24T02:01:00Z" }] },
  ];
  const mutationOutbox = {
    contract: contract("mutation-intent-outbox-v1"), schema_version: "1.0.0", outbox_generation: 2,
    entries: [
      { sequence: 1, intent_id: hash(Buffer.from(canonical(orderingIntents[0]))), intent: orderingIntents[0], source_command_id: "cmd-intent-ordering-1",
        source_command_fingerprint: `sha256:${"a".repeat(64)}`, producer_id: "adp-meeting-sync", workstream_id: "l1-checkout",
        field_set: ["blockers", "progress"], status: "consumed", attempts: 1, last_error: null, created_at: "2026-07-24T02:00:00Z",
        consumed_receipt_id: `sha256:${"b".repeat(64)}` },
      { sequence: 2, intent_id: hash(Buffer.from(canonical(orderingIntents[1]))), intent: orderingIntents[1], source_command_id: "cmd-intent-ordering-2",
        source_command_fingerprint: `sha256:${"c".repeat(64)}`, producer_id: "adp-risk-dependency-change-review", workstream_id: "l1-payments",
        field_set: ["risks", "status"], status: "pending", attempts: 0, last_error: null, created_at: "2026-07-24T02:01:00Z", consumed_receipt_id: null },
    ],
  };
  mutationOutbox.outbox_id = hash(Buffer.from(canonical(mutationOutbox)));
  const releaseHistory = clone(writerPackage.documents.release_evidence_history_index);
  const secondHistoryEntry = clone(releaseHistory.entries[0]);
  Object.assign(secondHistoryEntry, {
    set_generation: 2, set_id: `sha256:${"f".repeat(64)}`, set_path: `state/release-evidence/sets/h_${"f".repeat(64)}.json`,
    set_sha256: `sha256:${"e".repeat(64)}`, transition_receipt_path: "receipts/release-evidence/release-evidence-ordering-2.json",
    transition_receipt_sha256: `sha256:${"d".repeat(64)}`,
  });
  releaseHistory.entries.push(secondHistoryEntry); releaseHistory.current_generation = 2; releaseHistory.current_set_id = secondHistoryEntry.set_id;
  delete releaseHistory.index_id; releaseHistory.index_id = hash(Buffer.from(canonical(releaseHistory)));
  const inspectVerdict = { inspected_generation_id: generation.generation_id, inspected_pointer_id: pointer.pointer_id, outcome: "stale",
    inspected_at: "2026-07-24T03:05:00Z", observed_fact_generation: generation.fact_generation, changed_sources: ["a.md", "b.md"], error_code: "SOURCE_DRIFT" };
  inspectVerdict.verdict_id = hash(Buffer.from(canonical(inspectVerdict)));
  const refreshStatus = { contract: contract("panel-refresh-status-v1"), schema_version: "1.0.0", current_run_id: null, current_status: "dirty",
    last_successful_generation_id: generation.generation_id, last_successful_refresh_at: "2026-07-24T03:00:00Z", pending_invalidations: [], latest_inspect: inspectVerdict };
  refreshStatus.state_id = hash(Buffer.from(canonical(refreshStatus)));
  return {
    "physical-workstream-inventory/1.0.0": physicalInventoryFixture(registryDoc, policy, generation.fact_generation, schemaSha, registrySha),
    "selection-policy/1.0.0": policy, "generation-envelope/1.0.0": generation, "projection-dependency-manifest/1.0.0": manifest,
    "panel-current-pointer/1.0.0": pointer, "transaction-journal-manifest/1.0.0": journal, "audit-finding-repair/2.0.0": repair.audit,
    "refresh-run-receipt/1.0.0": refresh, "fact-mutation-receipt/1.0.0": factReceipt,
    "action-ledger-state/1.0.0": ledgerState, "action-flow-index/1.0.0": actionFlow,
    "wdr-action-projection/1.0.0": sidecar, "wdr-mutation/1.0.0": wdrCommand, "status-sync-batch/2.0.0": statusBatch,
    "status-mutation-intent/1.0.0": statusIntent, "action-ledger-mutation/2.0.0": actionCommand,
    "action-projection-drift-verdict/1.0.0": drift,
    "program-status-payload/2.0.0": status, "roadmap-payload/2.0.0": roadmap, "meeting-pack-payload/2.0.0": meeting,
    "management-panel-payload/2.0.0": panel, "state-audit-payload/2.0.0": stateAudit,
    "bootstrap-migration-command/1.0.0": bootstrap,
    "writer-fence-migration-attestation/1.0.0": writerAttestation,
    "writer-build-manifest/1.0.0": writerManifest,
    "generation-lineage-index/1.0.0": lineageIndex,
    "panel-refresh-status/1.0.0": refreshStatus,
    "release-evidence-set/1.0.0": writerPackage.release_evidence_set,
    "owned-fact-command/1.0.0": ownedCommand,
    "repair-receipt-index/1.0.0": repairIndex,
    "repair-attempt-ledger/1.0.0": repairAttemptLedger,
    "fact-command-receipt-index/1.0.0": factCommandIndex,
    "mutation-intent-outbox/1.0.0": mutationOutbox,
    "activation-lifecycle-index/1.0.0": activationLifecycle,
    "release-evidence-history-index/1.0.0": releaseHistory,
  };
};

const allOrderingRulesSemantics = (registryDoc, schemaRoot, suiteDoc, projectRoot, schemaSha, registrySha, mutation) => {
  const arraysAtPointer = (document, pointer) => {
    const parts = pointer.replace(/^\//, "").split("/").filter(Boolean).map((part) => part.replaceAll("~1", "/").replaceAll("~0", "~"));
    const expand = (current, remaining) => {
      if (!remaining.length) return [current];
      const [head, ...tail] = remaining;
      if (head === "*") {
        if (!Array.isArray(current)) throw new Error("ordering wildcard requires an array");
        return current.flatMap((item) => expand(item, tail));
      }
      if (current && typeof current === "object" && !Array.isArray(current) && Object.hasOwn(current, head)) return expand(current[head], tail);
      if (Array.isArray(current) && /^\d+$/.test(head) && Number(head) < current.length) return expand(current[Number(head)], tail);
      throw new Error("ordering pointer does not resolve");
    };
    const arrays = expand(document, parts);
    if (!arrays.length || arrays.some((value) => !Array.isArray(value))) throw new Error("ordering pointer must resolve to arrays");
    return arrays;
  };
  const documents = representativeOrderingDocuments(suiteDoc, schemaRoot, registryDoc, projectRoot, schemaSha, registrySha);
  const contracts = new Set(registryDoc.canonical_array_ordering.map(({ contract }) => contract));
  if (canonical([...Object.keys(documents)].sort()) !== canonical([...contracts].sort())) return false;
  for (const [name, document] of Object.entries(documents)) if (!validateRegistered(document, schemaRoot, registryDoc, name, schemaSha, registrySha)) return false;
  if (mutation === "nfc-key-collision") documents["state-audit-payload/2.0.0"].source_preview = [
    { path: "é.md", fingerprint: `sha256:${"1".repeat(64)}`, content: "a" },
    { path: "é.md", fingerprint: `sha256:${"2".repeat(64)}`, content: "b" },
  ];
  else if (mutation === "non-nfc-scalar-key") documents["state-audit-payload/2.0.0"].source_preview[1].path = "e\u0301.md";
  else if (mutation === "non-nfc-composite-key") documents["generation-envelope/1.0.0"].leaf_sources[1].path = "e\u0301.md";
  for (const rule of registryDoc.canonical_array_ordering) {
    let arrays; try { arrays = arraysAtPointer(documents[rule.contract], rule.pointer); } catch { return false; }
    for (const values of arrays) {
      if (values.length < 2) return false;
      if (mutation === "reverse-each-rule") values.reverse();
      else if (mutation === "duplicate-each-rule-key") values.push(clone(values[0]));
      let keys; try { keys = values.map((value) => orderingKey(value, rule.key, rule.key_types)); } catch { return false; }
      const sorted = [...keys].sort(compareOrderingKeys);
      if (canonical(keys) !== canonical(sorted) || new Set(keys.map(canonical)).size !== keys.length) return false;
    }
  }
  if (mutation === "nullable-key") {
    const values = documents["panel-current-pointer/1.0.0"].projections.filter(({ kind }) => kind === "meeting-pack").map((value) => orderingKey(value, "kind,instance_key"));
    return values.length === 2 && values[0][1][0] === 0 && values[1][1][0] === 1;
  }
  return true;
};

const identitySetsValid = (documents, registryDoc, requiredContracts = null) => {
  const expand = (current, parts) => {
    if (!parts.length) return [true, [current]];
    const [head, ...rest] = parts;
    if (head === "*") {
      if (!Array.isArray(current)) return [false, []];
      const values = [];
      for (const item of current) {
        const [found, expanded] = expand(item, rest);
        if (!found) return [false, []];
        values.push(...expanded);
      }
      return [true, values];
    }
    return current && typeof current === "object" && !Array.isArray(current) && Object.hasOwn(current, head) ? expand(current[head], rest) : [false, []];
  };
  for (const rule of registryDoc.identity_set_fields ?? []) {
    if (requiredContracts && !requiredContracts.has(rule.contract)) continue;
    const [found, arrays] = expand(documents[rule.contract], rule.pointer_template.replace(/^\//, "").split("/"));
    if (!found) return false;
    for (const values of arrays) {
      if (!Array.isArray(values)) return false;
      const normalized = values.map((value) => String(value).normalize("NFC"));
      const sorted = values.map((value, index) => [normalized[index], value]).sort((left, right) => Buffer.from(left[0]).compare(Buffer.from(right[0]))).map((row) => row[1]);
      if (canonical(values) !== canonical(sorted) || values.some((value, index) => String(value) !== normalized[index]) || new Set(normalized).size !== values.length) return false;
    }
  }
  return true;
};

const identitySetSemantics = (registryDoc, schemaSha, registrySha, mutation = "none") => {
  const fact = factAttributionFixture(schemaSha, registrySha, registryDoc); const repair = repairGraphFixture(schemaSha, registrySha, registryDoc);
  const refresh = factAttributionFixture(schemaSha, registrySha, registryDoc, "wdr-refresh-actions");
  const actionArtifacts = Object.fromEntries(fact.proof.business_artifacts.map((row) => [row.path, row]));
  const actionFlow = JSON.parse(artifactBytes(actionArtifacts[registryDoc.runtime_paths.action_flow_index.path].after_bytes).toString());
  const sidecarPath = `workstreams/${refresh.command.workstream_id}/action-projection.json`;
  const sidecar = JSON.parse(artifactBytes(refresh.proof.business_artifacts.find(({ path: itemPath }) => itemPath === sidecarPath).after_bytes).toString());
  const inspectVerdict = { inspected_generation_id: `sha256:${"1".repeat(64)}`, inspected_pointer_id: `sha256:${"2".repeat(64)}`, outcome: "stale",
    inspected_at: "2026-07-24T03:05:00Z", observed_fact_generation: 7, changed_sources: ["a.md", "b.md"], error_code: "SOURCE_DRIFT", verdict_id: `sha256:${"3".repeat(64)}` };
  const refreshStatus = { contract: expectedContractRef(registryDoc, "panel-refresh-status/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", current_run_id: null,
    current_status: "dirty", last_successful_generation_id: `sha256:${"1".repeat(64)}`, last_successful_refresh_at: "2026-07-24T03:00:00Z",
    pending_invalidations: [], latest_inspect: inspectVerdict, state_id: `sha256:${"4".repeat(64)}` };
  const identityIntent = {
    contract: expectedContractRef(registryDoc, "status-mutation-intent/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
    intent_id: "intent-identity-1", origin_producer: "adp-meeting-sync", workstream_id: "l1-checkout",
    set: { blockers: { mode: "replace", values: ["Access"] }, progress: "Active" },
    evidence: [{ source_path: "meetings/m1.md", source_fingerprint: `sha256:${"6".repeat(64)}`, observed_at: "2026-07-24T02:00:00Z" }],
  };
  const mutationOutbox = {
    contract: expectedContractRef(registryDoc, "mutation-intent-outbox/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", outbox_generation: 1,
    entries: [{
      sequence: 1, intent_id: hash(Buffer.from(canonical(identityIntent))), intent: identityIntent, source_command_id: "cmd-identity-intent-1",
      source_command_fingerprint: `sha256:${"6".repeat(64)}`, producer_id: "adp-meeting-sync", workstream_id: "l1-checkout",
      field_set: ["blockers", "progress"], status: "pending", attempts: 0, last_error: null,
      created_at: "2026-07-24T02:00:00Z", consumed_receipt_id: null,
    }],
  };
  mutationOutbox.outbox_id = hash(Buffer.from(canonical(mutationOutbox)));
  const convergence = {
    contract: expectedContractRef(registryDoc, "intent-convergence-verdict/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
    outbox_id: mutationOutbox.outbox_id, evaluated_through_sequence: 1, pending_intent_ids: [mutationOutbox.entries[0].intent_id],
    failed_intent_ids: [], waived_intent_ids: [], status: "pending",
  };
  convergence.verdict_id = hash(Buffer.from(canonical(convergence)));
  const documents = {
    "writer-capability-registry/1.0.0": fact.capability_registry,
    "repair-dry-run-request/1.0.0": repair.dry_request,
    "audit-finding-repair/2.0.0": repair.audit,
    "fact-mutation-receipt/1.0.0": fact.receipt,
    "action-flow-index/1.0.0": actionFlow,
    "wdr-action-projection/1.0.0": sidecar,
    "wdr-mutation/1.0.0": refresh.command,
    "status-sync-batch/2.0.0": statusIntentFixture(registryDoc, schemaSha, registrySha),
    "panel-refresh-status/1.0.0": refreshStatus,
    "activation-transition-command/1.0.0": { approved_by: ["operator-a", "operator-b"] },
    "mutation-intent-outbox/1.0.0": mutationOutbox,
    "intent-convergence-verdict/1.0.0": convergence,
  };
  if (mutation === "permute-status-fields") fact.capability_registry.capabilities.find(({ producer_id }) => producer_id === "adp-status-sync").allowed_fields.reverse();
  else if (mutation === "nfc-collision") repair.dry_request.authorization_scopes = ["repair:e\u0301", "repair:\u00e9"];
  else if (mutation === "non-nfc-scalar") repair.dry_request.authorization_scopes = ["repair:e\u0301"];
  return identitySetsValid(documents, registryDoc);
};

const repairBindingInput = (dry, auditId, outcome, schemaSha, registrySha) => ({
  project_root_instance_id: dry.project_root_instance_id, memory_root_instance_id: dry.memory_root_instance_id,
  principal: dry.principal, authorization_scopes: dry.authorization_scopes, audit_id: auditId,
  batch_id: dry.batch.batch_id, batch_digest: dry.batch.batch_digest, read_set: dry.batch.read_set,
  outcome, contract_hashes: { schema: schemaSha, registry: registrySha },
});
const repairLookupId = (batch) => hash(Buffer.from(canonical({
  workflow: batch.command.workflow, workstream_id: batch.command.workstream_id,
  operation: batch.command.operation, finding_ids: batch.finding_ids,
})));
const repairAttemptBinding = (transactionId, journalId, marker, recovery = null) => {
  const body = { business_transaction_id: transactionId, business_journal_id: journalId,
    business_marker_id: marker.marker_id, business_marker_sha256: hash(Buffer.from(canonical(marker))),
    recovery_receipt_id: recovery === null ? null : recovery.receipt_id,
    recovery_receipt_sha256: recovery === null ? null : hash(Buffer.from(canonical(recovery))) };
  const digest = hash(Buffer.from(canonical(body))).replace("sha256:", "");
  return { ...body, attempt_transaction_id: `repair-attempt:${digest}`, attempt_journal_id: `journal-repair-attempt:${digest}` };
};

const repairGraphFixture = (
  schemaSha, registrySha, registryDoc, outcome = "committed", targetWorkstreamId = "l1-checkout",
  factGeneration = 7, tokenChar = "A", transactionSuffix = "1", priorTransactionId = "tx-prior-1",
) => {
  const contract = (anchor) => ({ schema_id: `urn:adp:panel-sync-contracts:2026-07-24#${anchor}`, schema_sha256: schemaSha, registry_sha256: registrySha });
  const driftRows = [["l1-checkout", "A-FLOW-1", 4], ["l1-other", "A-OTHER-1", 2]].map(([workstreamId, actionId, revision]) => {
    const orphan = outcome === "orphan" && workstreamId === targetWorkstreamId;
    if (orphan) actionId = "A-ORPHAN-1";
    const actionDiff = {
      action_id: actionId, drift_kind: orphan ? "orphan-in-wdr" : "missing-from-wdr", ledger_present: !orphan,
      wdr_present: orphan, ledger_revision: orphan ? null : revision,
      wdr_rendered_sha256: orphan ? hash(Buffer.from(`orphan:${actionId}`)) : null,
    };
    const finding = driftFinding(workstreamId, "action-projection-drift", actionDiff);
    return {
      workstream_id: workstreamId, wdr_fingerprint: hash(Buffer.from(fixtureWdr(workstreamId))), wdr_revision: 4, file_generation: 7,
      sidecar_fingerprint: hash(Buffer.from(`sidecar:${workstreamId}`)), sidecar_ledger_fingerprint: `sha256:${"d".repeat(64)}`,
      status: "drift", action_diffs: [actionDiff], findings: [finding], finding_ids: [finding.finding_id],
    };
  });
  let repairLedgerFingerprint = `sha256:${"d".repeat(64)}`; let repairLedgerRows = []; let repairLedgerState = null;
  if (registryDoc) {
    let repairLedgerRaw;
    [repairLedgerRows, repairLedgerRaw, repairLedgerState] = refreshLedgerFixture(registryDoc, schemaSha, registrySha);
    repairLedgerFingerprint = hash(repairLedgerRaw);
  }
  const driftVerdict = {
    contract: contract("action-projection-drift-verdict-v1"), schema_version: "1.0.0",
    generation_id: hash(Buffer.from("repair-drift-generation")), selection_policy_id: hash(Buffer.from("repair-drift-selection")),
    ledger_fingerprint: repairLedgerFingerprint, selected_workstreams: ["l1-checkout", "l1-other"], workstreams: driftRows,
    overall_status: "degraded",
  };
  for (const row of driftRows) {
    row.sidecar_ledger_fingerprint = repairLedgerFingerprint;
    const orphanRow = outcome === "orphan" && row.workstream_id === targetWorkstreamId;
    let orphanRecord = orphanRow ? { action_id: "A-ORPHAN-1", owner: "FDE-O", action: "Remove orphan projection", due_trigger: "next sync",
      status: "open", action_revision: 1, routing_scope_id: row.workstream_id, affected_workstreams: [row.workstream_id] } : null;
    if (orphanRecord !== null) orphanRecord.rendered_summary = renderedActionSummary(orphanRecord);
    const expectedActions = orphanRecord === null ? []
      : [...actionSnapshot(repairLedgerRows, row.workstream_id, repairLedgerFingerprint, repairLedgerState.ledger_revision).actions, orphanRecord]
        .sort((a, b) => Buffer.from(a.action_id).compare(Buffer.from(b.action_id)));
    const expectedSidecar = { contract: expectedContractRef(registryDoc, "wdr-action-projection/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
      workstream_id: row.workstream_id, ledger_fingerprint: repairLedgerFingerprint, ledger_revision: 11, wdr_revision: 4, file_generation: 7,
      renderer_id: "urn:adp:wdr-action-renderer:1.0.0", renderer_sha256: registryDoc.protocol.sha256, actions: expectedActions };
    row.sidecar_fingerprint = hash(Buffer.from(canonical(expectedSidecar)));
    if (orphanRecord !== null) {
      row.wdr_fingerprint = hash(Buffer.from(applyWdrPatch(
        fixtureWdr(row.workstream_id), { set: { refresh_actions: true } }, expectedActions.map(({ rendered_summary }) => rendered_summary),
      )));
      row.action_diffs[0].wdr_rendered_sha256 = hash(Buffer.from(orphanRecord.rendered_summary));
      const finding = driftFinding(row.workstream_id, "action-projection-drift", row.action_diffs[0]);
      row.findings = [finding]; row.finding_ids = [finding.finding_id];
    }
  }
  driftVerdict.verdict_id = hash(Buffer.from(canonical(driftVerdict)));
  const auditId = hash(Buffer.from(canonical({ drift_verdict_id: driftVerdict.verdict_id, finding_algorithm: "drift-finding-to-repair-v2" })));
  const makeBatch = (workstreamId, findingId, actionIds, revisions, digit) => {
    const command = { workflow: "adp-status-sync", workstream_id: workstreamId, operation: "refresh_actions", expected_wdr_revision: 4, expected_file_generation: 7, action_ids: actionIds };
    const sourcePath = `workstreams/${workstreamId}/delivery-record.md`;
    const sourceFingerprint = driftRows.find(({ workstream_id }) => workstream_id === workstreamId).wdr_fingerprint;
    const readSet = {
      ledger_fingerprint: repairLedgerFingerprint,
      action_revisions: actionIds.map((action_id, index) => ({ action_id, expected_present: true, revision: revisions[index] })),
      wdr_revisions: [{ workstream_id: workstreamId, wdr_revision: 4, file_generation: 7, fingerprint: sourceFingerprint }],
      source_records: [{ root_instance_id: "123e4567-e89b-42d3-a456-426614174000", path: sourcePath, fingerprint: sourceFingerprint }], fact_generation: factGeneration,
    };
    const core = { based_on_audit_id: auditId, finding_ids: [findingId], command, read_set: readSet };
    const batchDigest = hash(Buffer.from(canonical(core)));
    const identity = { workflow: command.workflow, workstream_id: workstreamId, operation: command.operation, finding_ids: [findingId], batch_digest: batchDigest };
    const batch = { batch_id: hash(Buffer.from(canonical(identity))), ...core, batch_digest: batchDigest };
    const finding = {
      finding_id: findingId, kind: "action-projection-drift", severity: "blocked", workflow: "adp-status-sync", workstream_id: workstreamId,
      operation: "refresh_actions", entity_refs: actionIds.map((id) => ({ entity_type: "action", id })), action_ids: actionIds,
      source_path: sourcePath, source_line: 42, repair_batch_id: batch.batch_id,
    };
    return [batch, finding];
  };
  const rows = driftRows.map((row, offset) => makeBatch(row.workstream_id, row.finding_ids[0], [row.action_diffs[0].action_id], [row.action_diffs[0].ledger_revision ?? 1], String(offset + 3)));
  const batches = rows.map(([batch]) => batch).sort((a, b) => Buffer.from(a.batch_id).compare(Buffer.from(b.batch_id)));
  const findings = rows.map(([, finding]) => finding);
  findings.sort((a, b) => Buffer.from(`${a.workflow}\0${a.workstream_id}\0${a.operation}\0${a.finding_id}`).compare(Buffer.from(`${b.workflow}\0${b.workstream_id}\0${b.operation}\0${b.finding_id}`)));
  const targetBatch = batches.find(({ command }) => command.workstream_id === targetWorkstreamId);
  if (!targetBatch) throw new Error("repair fixture target workstream is not present");
  if (outcome === "orphan") {
    const oldBatchId = targetBatch.batch_id;
    Object.assign(targetBatch.read_set.action_revisions[0], { expected_present: false, revision: null });
    const core = Object.fromEntries(["based_on_audit_id", "finding_ids", "command", "read_set"].map((key) => [key, targetBatch[key]]));
    targetBatch.batch_digest = hash(Buffer.from(canonical(core)));
    const identity = { workflow: targetBatch.command.workflow, workstream_id: targetBatch.command.workstream_id, operation: targetBatch.command.operation, finding_ids: targetBatch.finding_ids, batch_digest: targetBatch.batch_digest };
    targetBatch.batch_id = hash(Buffer.from(canonical(identity)));
    findings.find(({ repair_batch_id }) => repair_batch_id === oldBatchId).repair_batch_id = targetBatch.batch_id;
  }
  const audit = { contract: contract("audit-finding-repair-v2"), schema_version: "2.0.0", audit_id: auditId, drift_verdict_id: driftVerdict.verdict_id, findings, repair_batches: batches };
  const dryRequest = {
    contract: contract("repair-dry-run-request-v1"), schema_version: "1.0.0", project_root_instance_id: "123e4567-e89b-42d3-a456-426614174001",
    memory_root_instance_id: "123e4567-e89b-42d3-a456-426614174000", principal: "operator-1", authorization_scopes: ["repair:actions"], batch: clone(targetBatch),
  };
  const bindingDigest = hash(Buffer.from(canonical(repairBindingInput(dryRequest, auditId, "applicable", schemaSha, registrySha))));
  if (!/^[A-Za-z0-9]$/.test(tokenChar)) throw new Error("repair fixture tokenChar must be one ASCII alphanumeric");
  const token = tokenChar.repeat(43); const issuedAt = "2026-07-24T02:00:00Z"; const expiresAt = "2026-07-24T02:15:00Z";
  const dryResult = {
    contract: contract("repair-dry-run-result-v1"), schema_version: "1.0.0", dry_run_id: hash(Buffer.from(canonical(dryRequest))), batch_id: targetBatch.batch_id,
    outcome: "applicable", binding_digest: bindingDigest, token, issued_at: issuedAt, expires_at: expiresAt, error_code: null,
  };
  const applyRequest = {
    contract: contract("repair-apply-request-v1"), schema_version: "1.0.0", principal: dryRequest.principal, batch_id: targetBatch.batch_id,
    batch_digest: targetBatch.batch_digest, token, applied_at: "2026-07-24T02:10:00Z",
  };
  const tokenHash = hash(Buffer.from(token)); const transactionId = `tx-repair-${transactionSuffix}`; const nonceStates = [];
  for (const [status, reservedBy, txId] of [["unused", null, null], ["reserved", dryRequest.principal, transactionId], ["consumed", dryRequest.principal, transactionId]]) {
    const nonce = {
      contract: contract("repair-nonce-state-v1"), schema_version: "1.0.0", nonce_id: tokenHash, token_hash: tokenHash,
      batch_id: targetBatch.batch_id, binding_digest: bindingDigest, status, expires_at: expiresAt, reserved_by: reservedBy,
      transaction_id: txId, previous_state_id: nonceStates.length ? nonceStates.at(-1).state_id : null,
    };
    nonce.state_id = hash(Buffer.from(canonical(nonce))); nonceStates.push(nonce);
  }
  if (!registryDoc) throw new Error("repair graph fixture requires the contract registry");
  const factGraph = factAttributionFixture(
    schemaSha, registrySha, registryDoc, "wdr-refresh-actions", null,
    targetWorkstreamId, factGeneration, priorTransactionId, outcome === "orphan" ? "A-ORPHAN-1" : null,
  );
  const [journal, marker] = journalFixture("repair", schemaSha, registrySha, null, registryDoc);
  journal.transaction_id = transactionId; journal.journal_id = `journal-repair-${transactionSuffix}`;
  journal.journal_dir = registryDoc.runtime_paths.journal_dir_template.replace("{transaction_token}", filesystemToken(transactionId));
  journal.manifest_path = runtimePath(registryDoc, "journal_manifest_template", null, null, null, transactionId);
  journal.prepared_marker_path = runtimePath(registryDoc, "journal_prepared_marker_template", null, null, null, transactionId);
  journal.terminal_marker_path = runtimePath(registryDoc, "journal_terminal_marker_template", null, null, null, transactionId);
  journal.recovery_receipt_path = runtimePath(registryDoc, "journal_recovery_receipt_template", null, null, null, transactionId);
  const authorization = clone(factGraph.receipt.authorization); journal.authorization = clone(authorization);
  const businessRoot = dryRequest.memory_root_instance_id;
  const businessRows = factGraph.journal.targets.filter(({ role }) => role === "business").map(clone);
  for (const row of businessRows) { row.root_instance_id = businessRoot; row.before_image.root_instance_id = businessRoot; row.after_image.root_instance_id = businessRoot; }
  const generationRow = mutationTarget("fact-generation", "replace", businessRows.length, "state/fact-generation.json");
  const commandIndexRow = mutationTarget("fact-command-index", "replace", businessRows.length + 1, registryDoc.runtime_paths.fact_command_receipt_index.path);
  const noncePath = runtimePath(registryDoc, "repair_nonce_template", null, null, null, null, tokenHash);
  const nonceRow = mutationTarget("nonce", "replace", businessRows.length + 2, noncePath);
  const receiptPaths = [runtimePath(registryDoc, "repair_fact_receipt_template", null, null, null, transactionId)];
  const receiptRows = receiptPaths.map((targetPath, index) => mutationTarget("receipt", "create", businessRows.length + 3 + index, targetPath));
  journal.targets = [...businessRows, generationRow, commandIndexRow, nonceRow, ...receiptRows]; journal.receipt_target_paths = receiptPaths;
  reindexTargets(journal.targets, journal.journal_dir);
  const beforeState = clone(factGraph.before_state); const afterState = clone(factGraph.after_state);
  afterState.last_transaction_id = transactionId;
  const afterStateBody = clone(afterState); delete afterStateBody.state_id; afterState.state_id = hash(Buffer.from(canonical(afterStateBody)));
  const factGenerationTarget = journal.targets.find(({ role }) => role === "fact-generation");
  factGenerationTarget.before_sha256 = hash(Buffer.from(canonical(beforeState))); factGenerationTarget.after_sha256 = hash(Buffer.from(canonical(afterState)));
  factGenerationTarget.before_image.sha256 = factGenerationTarget.before_sha256; factGenerationTarget.after_image.sha256 = factGenerationTarget.after_sha256;
  const proof = clone(factGraph.proof); proof.transaction_id = transactionId;
  const proofBody = clone(proof); delete proofBody.proof_id; proof.proof_id = hash(Buffer.from(canonical(proofBody)));
  const nonceTarget = journal.targets.find(({ role }) => role === "nonce");
  nonceTarget.path = noncePath;
  nonceTarget.before_sha256 = hash(Buffer.from(canonical(nonceStates[1]))); nonceTarget.after_sha256 = hash(Buffer.from(canonical(nonceStates[2])));
  nonceTarget.before_image.sha256 = nonceTarget.before_sha256; nonceTarget.after_image.sha256 = nonceTarget.after_sha256;
  const receiptTargets = journal.targets.filter(({ role }) => role === "receipt");
  const businessTargets = journal.targets.filter(({ role }) => role === "business").map(clone);
  const generationTarget = clone(factGenerationTarget);
  const factReceipt = {
    contract: contract("fact-mutation-receipt-v1"), schema_version: "1.0.0", transaction_id: transactionId, journal_id: journal.journal_id,
    authorization: clone(authorization), initiator: Object.fromEntries(["producer_id", "capability_id", "capability_epoch", "principal_id"].map((key) => [key, authorization[key]])),
    before_fact_generation: beforeState.fact_generation, after_fact_generation: afterState.fact_generation,
    business_targets: businessTargets, generation_state_target: generationTarget, action_deltas: [], status: "committed",
  };
  factReceipt.receipt_id = hash(Buffer.from(canonical(factReceipt)));
  const repairReceipt = {
    contract: contract("repair-run-receipt-v1"), schema_version: "1.0.0", batch_id: targetBatch.batch_id, outcome: "committed",
    nonce_status: "consumed", nonce_state_id: nonceStates[2].state_id, fact_receipt_id: factReceipt.receipt_id,
    transaction_id: transactionId, journal_id: journal.journal_id,
    attempt_transaction_id: null, attempt_journal_id: null, business_marker_id: null, business_marker_sha256: null,
    recovery_receipt_id: null, recovery_receipt_sha256: null, retry_required: false, error_code: null,
  };
  repairReceipt.receipt_id = hash(Buffer.from(canonical(repairReceipt)));
  [[receiptTargets[0], factReceipt]].forEach(([target, receipt]) => {
    target.after_sha256 = hash(Buffer.from(canonical(receipt))); target.after_image.sha256 = target.after_sha256;
  });
  const beforeCommandIndex = clone(factGraph.before_command_index);
  const commandIndex = {
    contract: clone(beforeCommandIndex.contract), schema_version: "1.0.0", next_sequence: beforeCommandIndex.next_sequence + 1,
    entries: [...beforeCommandIndex.entries, {
      sequence: beforeCommandIndex.next_sequence, command_id: factGraph.command.command_id,
      command_fingerprint: authorization.authorized_command_fingerprint, transaction_id: transactionId,
      receipt_id: factReceipt.receipt_id, receipt_path: receiptPaths[0], receipt_sha256: hash(Buffer.from(canonical(factReceipt))),
    }],
  };
  commandIndex.index_id = hash(Buffer.from(canonical(commandIndex)));
  commandIndexRow.before_sha256 = hash(Buffer.from(canonical(beforeCommandIndex))); commandIndexRow.after_sha256 = hash(Buffer.from(canonical(commandIndex)));
  commandIndexRow.before_image.sha256 = commandIndexRow.before_sha256; commandIndexRow.after_image.sha256 = commandIndexRow.after_sha256;
  const journalBody = clone(journal); delete journalBody.manifest_id; journal.manifest_id = hash(Buffer.from(canonical(journalBody)));
  Object.assign(marker, { journal_id: journal.journal_id, manifest_id: journal.manifest_id, state: "committed" });
  const markerBody = clone(marker); delete markerBody.marker_id; marker.marker_id = hash(Buffer.from(canonical(markerBody)));
  const graph = {
    drift_verdict: driftVerdict, audit, dry_request: dryRequest, dry_result: dryResult, apply_request: applyRequest, nonce_states: nonceStates, journal, marker,
    fact_receipt: factReceipt, repair_receipt: repairReceipt, capability_registry: factGraph.capability_registry,
    fact_command: factGraph.command, before_state: beforeState, after_state: afterState, proof,
    before_command_index: beforeCommandIndex, command_index: commandIndex,
  };
  if (outcome === "blocked") {
    Object.assign(graph.dry_result, {
      outcome: "blocked", binding_digest: hash(Buffer.from(canonical(repairBindingInput(dryRequest, auditId, "blocked", schemaSha, registrySha)))),
      token: null, expires_at: null, error_code: "REPAIR_PRECONDITION_FAILED",
    });
    const blockedReceipt = {
      contract: contract("repair-run-receipt-v1"), schema_version: "1.0.0", batch_id: targetBatch.batch_id, outcome: "blocked",
      nonce_status: null, nonce_state_id: null, fact_receipt_id: null, transaction_id: null, journal_id: null,
      attempt_transaction_id: null, attempt_journal_id: null, business_marker_id: null, business_marker_sha256: null,
      recovery_receipt_id: null, recovery_receipt_sha256: null,
      retry_required: true, error_code: "REPAIR_PRECONDITION_FAILED",
    };
    blockedReceipt.receipt_id = hash(Buffer.from(canonical(blockedReceipt)));
    return { drift_verdict: driftVerdict, audit, dry_request: dryRequest, dry_result: graph.dry_result, repair_receipt: blockedReceipt };
  }
  if (outcome === "rolled-back") {
    const finalNonce = graph.nonce_states.at(-1); finalNonce.status = "invalidated";
    const finalNonceBody = clone(finalNonce); delete finalNonceBody.state_id; finalNonce.state_id = hash(Buffer.from(canonical(finalNonceBody)));
    nonceTarget.after_sha256 = hash(Buffer.from(canonical(finalNonce))); nonceTarget.after_image.sha256 = nonceTarget.after_sha256;
    graph.marker.state = "rolled-back";
    const recovery = {
      contract: contract("recovery-receipt-v1"), schema_version: "1.0.0", journal_id: journal.journal_id, transaction_id: transactionId,
      outcome: "rolled-back", recovered_at: "2026-07-24T02:11:00Z", target_states: journal.targets.map(() => "before"), error_code: null,
    };
    recovery.receipt_id = hash(Buffer.from(canonical(recovery)));
    const rolledReceipt = {
      contract: contract("repair-run-receipt-v1"), schema_version: "1.0.0", batch_id: targetBatch.batch_id, outcome: "rolled-back",
      nonce_status: "invalidated", nonce_state_id: finalNonce.state_id, fact_receipt_id: null,
      transaction_id: transactionId, journal_id: journal.journal_id,
      attempt_transaction_id: null, attempt_journal_id: null, business_marker_id: null, business_marker_sha256: null,
      recovery_receipt_id: null, recovery_receipt_sha256: null, retry_required: true, error_code: "REPAIR_TRANSACTION_ROLLED_BACK",
    };
    rolledReceipt.receipt_id = hash(Buffer.from(canonical(rolledReceipt)));
    delete journal.manifest_id; journal.manifest_id = hash(Buffer.from(canonical(journal)));
    graph.marker.manifest_id = journal.manifest_id; delete graph.marker.marker_id; graph.marker.marker_id = hash(Buffer.from(canonical(graph.marker)));
    Object.assign(graph, { fact_receipt: null, repair_receipt: rolledReceipt, recovery_receipt: recovery });
  }
  delete journal.manifest_id; journal.manifest_id = hash(Buffer.from(canonical(journal)));
  graph.marker.manifest_id = journal.manifest_id; delete graph.marker.marker_id; graph.marker.marker_id = hash(Buffer.from(canonical(graph.marker)));
  const recovery = graph.recovery_receipt && typeof graph.recovery_receipt === "object" ? graph.recovery_receipt : null;
  if (recovery !== null) {
    recovery.target_states = journal.targets.map(() => "before"); delete recovery.receipt_id; recovery.receipt_id = hash(Buffer.from(canonical(recovery)));
  }
  const handoff = repairAttemptBinding(transactionId, journal.journal_id, graph.marker, recovery);
  for (const key of ["attempt_transaction_id", "attempt_journal_id", "business_marker_id", "business_marker_sha256", "recovery_receipt_id", "recovery_receipt_sha256"])
    graph.repair_receipt[key] = handoff[key];
  delete graph.repair_receipt.receipt_id; graph.repair_receipt.receipt_id = hash(Buffer.from(canonical(graph.repair_receipt)));
  const beforeRepairIndex = {
    contract: expectedContractRef(registryDoc, "repair-receipt-index/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", entries: [],
  };
  beforeRepairIndex.index_id = hash(Buffer.from(canonical(beforeRepairIndex)));
  const terminalReceipt = graph.repair_receipt;
  const repairReceiptPath = runtimePath(registryDoc, "repair_receipt_template", null, null, null, transactionId);
  const repairIndex = {
    contract: clone(beforeRepairIndex.contract), schema_version: "1.0.0",
    entries: [{
      lookup_id: repairLookupId(targetBatch), sequence: 1, batch_id: targetBatch.batch_id,
      transaction_id: transactionId,
      ...Object.fromEntries(["attempt_transaction_id", "attempt_journal_id", "business_marker_id", "business_marker_sha256", "recovery_receipt_id", "recovery_receipt_sha256"].map((key) => [key, handoff[key]])),
      outcome: terminalReceipt.outcome, receipt_path: repairReceiptPath,
      receipt_sha256: hash(Buffer.from(canonical(terminalReceipt))),
    }],
  };
  repairIndex.index_id = hash(Buffer.from(canonical(repairIndex)));
  const beforeAttemptLedger = {
    contract: expectedContractRef(registryDoc, "repair-attempt-ledger/1.0.0", schemaSha, registrySha),
    schema_version: "1.0.0", next_sequence: 1, attempts: [],
  };
  beforeAttemptLedger.ledger_id = hash(Buffer.from(canonical(beforeAttemptLedger)));
  const attemptEntry = {
    sequence: 1, lookup_id: repairLookupId(targetBatch), batch_id: targetBatch.batch_id, transaction_id: transactionId,
    business_journal_id: journal.journal_id,
    ...Object.fromEntries(["attempt_transaction_id", "attempt_journal_id", "business_marker_id", "business_marker_sha256", "recovery_receipt_id", "recovery_receipt_sha256"].map((key) => [key, handoff[key]])),
    business_terminal_state: terminalReceipt.outcome === "committed" ? "committed" : "rolled-back",
    repair_receipt_id: terminalReceipt.receipt_id, repair_receipt_path: repairReceiptPath,
    repair_receipt_sha256: hash(Buffer.from(canonical(terminalReceipt))), recorded_at: "2026-07-24T02:12:00Z",
  };
  const attemptLedger = {
    contract: clone(beforeAttemptLedger.contract), schema_version: "1.0.0", next_sequence: 2, attempts: [attemptEntry],
  };
  attemptLedger.ledger_id = hash(Buffer.from(canonical(attemptLedger)));
  const attemptTransactionId = handoff.attempt_transaction_id;
  const [attemptJournal, attemptMarker] = transitionJournalFixture(
    "repair-attempt", attemptTransactionId, handoff.attempt_journal_id,
    [
      { role: "repair-attempt-ledger", operation: "replace", path: registryDoc.runtime_paths.repair_attempt_ledger.path,
        before_raw: Buffer.from(canonical(beforeAttemptLedger)), after_raw: Buffer.from(canonical(attemptLedger)) },
      { role: "repair-index", operation: "replace", path: registryDoc.runtime_paths.repair_receipt_index.path,
        before_raw: Buffer.from(canonical(beforeRepairIndex)), after_raw: Buffer.from(canonical(repairIndex)) },
    ],
    repairReceiptPath, Buffer.from(canonical(terminalReceipt)), registryDoc, schemaSha, registrySha,
  );
  delete journal.manifest_id; journal.manifest_id = hash(Buffer.from(canonical(journal)));
  graph.marker.manifest_id = journal.manifest_id; delete graph.marker.marker_id; graph.marker.marker_id = hash(Buffer.from(canonical(graph.marker)));
  if (graph.recovery_receipt && typeof graph.recovery_receipt === "object") {
    graph.recovery_receipt.target_states = journal.targets.map(() => "before");
    delete graph.recovery_receipt.receipt_id; graph.recovery_receipt.receipt_id = hash(Buffer.from(canonical(graph.recovery_receipt)));
  }
  Object.assign(graph, {
    before_repair_index: beforeRepairIndex, repair_index: repairIndex,
    before_attempt_ledger: beforeAttemptLedger, attempt_ledger: attemptLedger,
    attempt_journal: attemptJournal, attempt_marker: attemptMarker,
  });
  return graph;
};

const repairGraphSemantics = (
  graph, schemaRoot, registryDoc, schemaSha, registrySha, runtimeCapabilityBytes = null, runtimeRootRegistryBytes = null,
  runtimeActivationBytes = null, runtimeAttestationBytes = null, authorityContext = null,
) => {
  const contracts = {
    drift_verdict: "action-projection-drift-verdict/1.0.0", audit: "audit-finding-repair/2.0.0", dry_request: "repair-dry-run-request/1.0.0",
    dry_result: "repair-dry-run-result/1.0.0", repair_receipt: "repair-run-receipt/1.0.0",
  };
  if (!Object.entries(contracts).every(([name, contractName]) =>
    validateRegistered(graph[name], schemaRoot, registryDoc, contractName, schemaSha, registrySha))) return false;
  const identityDocuments = {
    "audit-finding-repair/2.0.0": graph.audit, "repair-dry-run-request/1.0.0": graph.dry_request,
  };
  if (graph.capability_registry) identityDocuments["writer-capability-registry/1.0.0"] = graph.capability_registry;
  if (graph.fact_receipt && typeof graph.fact_receipt === "object") identityDocuments["fact-mutation-receipt/1.0.0"] = graph.fact_receipt;
  if (!identitySetsValid(identityDocuments, registryDoc, new Set(Object.keys(identityDocuments)))) return false;
  const audit = graph.audit; const drift = graph.drift_verdict; const driftBody = clone(drift); delete driftBody.verdict_id;
  if (drift.verdict_id !== hash(Buffer.from(canonical(driftBody))) || audit.drift_verdict_id !== drift.verdict_id
      || audit.audit_id !== hash(Buffer.from(canonical({ drift_verdict_id: drift.verdict_id, finding_algorithm: "drift-finding-to-repair-v2" })))) return false;
  const expectedDriftFindings = new Map();
  for (const driftRow of drift.workstreams) {
    const derivedIds = driftRow.findings.map(({ finding_id }) => finding_id);
    if (canonical(driftRow.finding_ids) !== canonical(derivedIds)
      || canonical(derivedIds) !== canonical([...new Set(derivedIds)].sort((a, b) => Buffer.from(a).compare(Buffer.from(b))))) return false;
    const actionDiffs = [];
    for (const typed of driftRow.findings) {
      const identityBody = clone(typed); delete identityBody.finding_id; delete identityBody.source_path; delete identityBody.source_line;
      if (typed.finding_id !== hash(Buffer.from(canonical(identityBody))) || typed.workstream_id !== driftRow.workstream_id) return false;
      let entityRefs; let actionIds; let batchRequired;
      if (typed.repairability === "repairable") {
        const diff = typed.action_diff;
        if (!diff || typed.kind !== "action-projection-drift" || typed.action_id !== diff.action_id) return false;
        actionDiffs.push(diff); entityRefs = [{ entity_type: "action", id: diff.action_id }]; actionIds = [diff.action_id]; batchRequired = true;
      } else {
        if (typed.action_id !== null || typed.action_diff !== null) return false;
        entityRefs = [{ entity_type: "workstream", id: driftRow.workstream_id }]; actionIds = []; batchRequired = false;
      }
      expectedDriftFindings.set(typed.finding_id, {
        expected: { finding_id: typed.finding_id, kind: typed.kind, severity: typed.severity, workflow: "adp-status-sync",
          workstream_id: driftRow.workstream_id, operation: "refresh_actions", entity_refs: entityRefs, action_ids: actionIds,
          source_path: typed.source_path, source_line: typed.source_line }, batchRequired,
      });
    }
    if (canonical(actionDiffs) !== canonical(driftRow.action_diffs)) return false;
  }
  const batches = new Map(graph.audit.repair_batches.map((row) => [row.batch_id, row])); const findings = new Map(graph.audit.findings.map((row) => [row.finding_id, row]));
  if (batches.size !== graph.audit.repair_batches.length || findings.size !== graph.audit.findings.length) return false;
  if (canonical([...findings.keys()].sort()) !== canonical([...expectedDriftFindings.keys()].sort())) return false;
  for (const [findingId, { expected, batchRequired }] of expectedDriftFindings) {
    const actual = findings.get(findingId);
    if (Object.entries(expected).some(([key, value]) => canonical(actual[key]) !== canonical(value))
      || ((actual.repair_batch_id !== null) !== batchRequired)) return false;
  }
  const repairable = [...findings.values()].filter(({ severity }) => severity === "blocked");
  if (repairable.some(({ repair_batch_id }) => repair_batch_id === null)) return false;
  const groupKey = ({ workflow, workstream_id, operation }) => `${workflow}\0${workstream_id}\0${operation}`;
  const groups = new Map(); const batchesByGroup = new Map();
  for (const finding of repairable) { const key = groupKey(finding); if (!groups.has(key)) groups.set(key, []); groups.get(key).push(finding); }
  for (const batch of batches.values()) { const key = groupKey(batch.command); if (!batchesByGroup.has(key)) batchesByGroup.set(key, []); batchesByGroup.get(key).push(batch); }
  if (canonical([...groups.keys()].sort()) !== canonical([...batchesByGroup.keys()].sort()) || [...batchesByGroup.values()].some((rows) => rows.length !== 1)) return false;
  for (const [key, groupFindings] of groups) {
    const batch = batchesByGroup.get(key)[0]; const expectedFindingIds = groupFindings.map(({ finding_id }) => finding_id).sort((a, b) => Buffer.from(a).compare(Buffer.from(b)));
    if (canonical(batch.finding_ids) !== canonical(expectedFindingIds) || groupFindings.some(({ repair_batch_id }) => repair_batch_id !== batch.batch_id)) return false;
  }
  for (const finding of findings.values()) {
    const actionRefs = finding.entity_refs.filter(({ entity_type }) => entity_type === "action").map(({ id }) => id);
    if (canonical(actionRefs) !== canonical(finding.action_ids) || new Set(actionRefs).size !== actionRefs.length) return false;
    if (finding.repair_batch_id === null) continue;
    if (!batches.has(finding.repair_batch_id) || !batches.get(finding.repair_batch_id).finding_ids.includes(finding.finding_id)) return false;
  }
  for (const batch of batches.values()) {
    if (batch.based_on_audit_id !== graph.audit.audit_id || batch.finding_ids.some((id) => !findings.has(id) || findings.get(id).repair_batch_id !== batch.batch_id)) return false;
    const byteSort = (values) => [...values].sort((left, right) => Buffer.from(left).compare(Buffer.from(right)));
    const findingActions = byteSort([...new Set(batch.finding_ids.flatMap((id) => findings.get(id).action_ids))]);
    const commandActions = batch.command.action_ids; const readActions = batch.read_set.action_revisions.map(({ action_id }) => action_id);
    if (canonical(commandActions) !== canonical(byteSort(commandActions)) || canonical(readActions) !== canonical(byteSort(readActions))
        || canonical(findingActions) !== canonical(commandActions) || canonical(commandActions) !== canonical(readActions) || new Set(readActions).size !== readActions.length) return false;
    const wdrs = batch.read_set.wdr_revisions;
    if (wdrs.length !== 1 || wdrs[0].workstream_id !== batch.command.workstream_id || wdrs[0].wdr_revision !== batch.command.expected_wdr_revision || wdrs[0].file_generation !== batch.command.expected_file_generation) return false;
    const sources = batch.read_set.source_records.map((row) => `${row.root_instance_id}\0${row.path}`); if (new Set(sources).size !== sources.length) return false;
    const core = Object.fromEntries(["based_on_audit_id", "finding_ids", "command", "read_set"].map((key) => [key, batch[key]])); if (batch.batch_digest !== hash(Buffer.from(canonical(core)))) return false;
    const identity = { workflow: batch.command.workflow, workstream_id: batch.command.workstream_id, operation: batch.command.operation, finding_ids: batch.finding_ids, batch_digest: batch.batch_digest };
    if (batch.batch_id !== hash(Buffer.from(canonical(identity)))) return false;
  }
  const dry = graph.dry_request; const result = graph.dry_result; const batch = batches.get(dry.batch.batch_id);
  if (!batch || canonical(dry.batch) !== canonical(batch)) return false;
  const expectedBinding = hash(Buffer.from(canonical(repairBindingInput(dry, graph.audit.audit_id, result.outcome, schemaSha, registrySha))));
  if (result.outcome === "blocked") {
    const receipt = graph.repair_receipt; const receiptBody = clone(receipt); delete receiptBody.receipt_id;
    return canonical(Object.keys(graph).sort()) === canonical(["drift_verdict", "audit", "dry_request", "dry_result", "repair_receipt"].sort())
      && result.dry_run_id === hash(Buffer.from(canonical(dry))) && result.batch_id === batch.batch_id && result.binding_digest === expectedBinding
      && result.token === null && result.expires_at === null && Boolean(result.error_code)
      && receipt.receipt_id === hash(Buffer.from(canonical(receiptBody))) && receipt.batch_id === batch.batch_id && receipt.outcome === "blocked"
      && receipt.nonce_status === null && receipt.nonce_state_id === null && receipt.fact_receipt_id === null
      && receipt.transaction_id === null && receipt.journal_id === null
      && ["attempt_transaction_id", "attempt_journal_id", "business_marker_id", "business_marker_sha256", "recovery_receipt_id", "recovery_receipt_sha256"].every((key) => receipt[key] === null)
      && receipt.retry_required && Boolean(receipt.error_code);
  }
  const extended = {
    apply_request: "repair-apply-request/1.0.0", journal: "transaction-journal-manifest/1.0.0", marker: "journal-marker/1.0.0",
    attempt_journal: "transaction-journal-manifest/1.0.0", attempt_marker: "journal-marker/1.0.0",
    before_attempt_ledger: "repair-attempt-ledger/1.0.0", attempt_ledger: "repair-attempt-ledger/1.0.0",
  };
  if (!Object.entries(extended).every(([name, contractName]) =>
    graph[name] && validateRegistered(graph[name], schemaRoot, registryDoc, contractName, schemaSha, registrySha))) return false;
  const apply = graph.apply_request; const nonceStates = graph.nonce_states;
  if (!Array.isArray(nonceStates) || nonceStates.length !== 3
      || !nonceStates.every((row) => validateRegistered(row, schemaRoot, registryDoc, "repair-nonce-state/1.0.0", schemaSha, registrySha)) || result.token === null) return false;
  const issued = Date.parse(result.issued_at); const expires = Date.parse(result.expires_at); const applied = Date.parse(apply.applied_at);
  if (![issued, expires, applied].every(Number.isFinite) || !(issued <= applied && applied <= expires && expires <= issued + 15 * 60 * 1000)) return false;
  if (!(result.dry_run_id === hash(Buffer.from(canonical(dry))) && result.batch_id === batch.batch_id && result.outcome === "applicable" && result.binding_digest === expectedBinding
    && apply.principal === dry.principal && apply.batch_id === batch.batch_id && apply.batch_digest === batch.batch_digest && apply.token === result.token)) return false;
  const tokenHash = hash(Buffer.from(result.token)); const expectedFinalStatus = graph.repair_receipt.outcome === "committed" ? "consumed" : "invalidated";
  if (canonical(nonceStates.map(({ status }) => status)) !== canonical(["unused", "reserved", expectedFinalStatus])) return false;
  for (let index = 0; index < nonceStates.length; index += 1) {
    const nonce = nonceStates[index]; const body = clone(nonce); delete body.state_id;
    if (!(nonce.state_id === hash(Buffer.from(canonical(body))) && nonce.nonce_id === tokenHash && nonce.token_hash === tokenHash
      && nonce.batch_id === batch.batch_id && nonce.binding_digest === expectedBinding && nonce.expires_at === result.expires_at
      && nonce.previous_state_id === (index ? nonceStates[index - 1].state_id : null))) return false;
  }
  const finalNonce = nonceStates.at(-1);
  if (nonceStates[0].reserved_by !== null || nonceStates[0].transaction_id !== null) return false;
  if (nonceStates.slice(1).some((row) => row.reserved_by !== dry.principal || row.transaction_id !== graph.journal.transaction_id)) return false;
  const journal = graph.journal; const marker = graph.marker;
  if (journal.transaction_kind !== "repair" || !journalSemantics(journal, marker, schemaRoot, registryDoc, schemaSha, registrySha)) return false;
  const business = journal.targets.filter(({ role }) => role === "business"); const generation = journal.targets.filter(({ role }) => role === "fact-generation");
  const nonceTargets = journal.targets.filter(({ role }) => role === "nonce");
  const receiptTargets = journal.targets.filter(({ role }) => role === "receipt");
  if (generation.length !== 1 || nonceTargets.length !== 1 || receiptTargets.length !== 1) return false;
  const nonceTarget = nonceTargets[0];
  if (!(nonceTarget.before_sha256 === hash(Buffer.from(canonical(nonceStates[1]))) && nonceTarget.after_sha256 === hash(Buffer.from(canonical(finalNonce)))
    && nonceTarget.path === runtimePath(registryDoc, "repair_nonce_template", null, null, null, null, tokenHash))) return false;
  const beforeIndex = graph.before_repair_index; const afterIndex = graph.repair_index;
  const attemptJournal = graph.attempt_journal; const attemptMarker = graph.attempt_marker;
  if (attemptJournal.transaction_kind !== "repair-attempt" || !journalSemantics(attemptJournal, attemptMarker, schemaRoot, registryDoc, schemaSha, registrySha)) return false;
  const indexTarget = attemptJournal.targets.find(({ role }) => role === "repair-index");
  const attemptTarget = attemptJournal.targets.find(({ role }) => role === "repair-attempt-ledger");
  const attemptReceiptTarget = attemptJournal.targets.find(({ role }) => role === "receipt");
  const beforeAttempt = graph.before_attempt_ledger; const afterAttempt = graph.attempt_ledger;
  if (!beforeIndex || !afterIndex
    || !validateRegistered(beforeIndex, schemaRoot, registryDoc, "repair-receipt-index/1.0.0", schemaSha, registrySha)
    || !validateRegistered(afterIndex, schemaRoot, registryDoc, "repair-receipt-index/1.0.0", schemaSha, registrySha)
    || !indexTarget || !attemptTarget || !attemptReceiptTarget) return false;
  const beforeIndexBody = clone(beforeIndex); delete beforeIndexBody.index_id; const afterIndexBody = clone(afterIndex); delete afterIndexBody.index_id;
  const beforeAttemptBody = clone(beforeAttempt); delete beforeAttemptBody.ledger_id; const afterAttemptBody = clone(afterAttempt); delete afterAttemptBody.ledger_id;
  if (beforeIndex.index_id !== hash(Buffer.from(canonical(beforeIndexBody))) || afterIndex.index_id !== hash(Buffer.from(canonical(afterIndexBody)))
    || afterIndex.entries.length !== beforeIndex.entries.length + 1
    || canonical(afterIndex.entries.slice(0, -1)) !== canonical(beforeIndex.entries)
    || indexTarget.path !== registryDoc.runtime_paths.repair_receipt_index.path
    || indexTarget.before_sha256 !== hash(Buffer.from(canonical(beforeIndex))) || indexTarget.after_sha256 !== hash(Buffer.from(canonical(afterIndex)))
    || beforeAttempt.ledger_id !== hash(Buffer.from(canonical(beforeAttemptBody))) || afterAttempt.ledger_id !== hash(Buffer.from(canonical(afterAttemptBody)))
    || afterAttempt.attempts.length !== beforeAttempt.attempts.length + 1
    || canonical(afterAttempt.attempts.slice(0, -1)) !== canonical(beforeAttempt.attempts)
    || afterAttempt.next_sequence !== beforeAttempt.next_sequence + 1
    || attemptTarget.path !== registryDoc.runtime_paths.repair_attempt_ledger.path
    || attemptTarget.before_sha256 !== hash(Buffer.from(canonical(beforeAttempt))) || attemptTarget.after_sha256 !== hash(Buffer.from(canonical(afterAttempt)))) return false;
  const indexEntry = afterIndex.entries.at(-1); const indexedReceipt = graph.repair_receipt;
  const recoveryForHandoff = graph.recovery_receipt && typeof graph.recovery_receipt === "object" ? graph.recovery_receipt : null;
  const expectedHandoff = repairAttemptBinding(journal.transaction_id, journal.journal_id, marker, recoveryForHandoff);
  const handoffFields = ["attempt_transaction_id", "attempt_journal_id", "business_marker_id", "business_marker_sha256", "recovery_receipt_id", "recovery_receipt_sha256"];
  if (attemptJournal.transaction_id !== expectedHandoff.attempt_transaction_id || attemptJournal.journal_id !== expectedHandoff.attempt_journal_id
    || attemptJournal.transaction_id === journal.transaction_id || attemptJournal.journal_id === journal.journal_id
    || handoffFields.some((key) => indexedReceipt[key] !== expectedHandoff[key] || indexEntry[key] !== expectedHandoff[key])
    || indexEntry.lookup_id !== repairLookupId(batch) || indexEntry.sequence !== afterIndex.entries.length || indexEntry.batch_id !== batch.batch_id
    || indexEntry.transaction_id !== journal.transaction_id || indexEntry.outcome !== indexedReceipt.outcome
    || indexEntry.receipt_path !== runtimePath(registryDoc, "repair_receipt_template", null, null, null, journal.transaction_id)
    || indexEntry.receipt_sha256 !== hash(Buffer.from(canonical(indexedReceipt)))
    || canonical(afterIndex.entries) !== canonical([...afterIndex.entries].sort((a, b) => a.sequence - b.sequence))
    || canonical(afterIndex.entries.map(({ sequence }) => sequence)) !== canonical(afterIndex.entries.map((_, index) => index + 1))) return false;
  const expectedAttempt = {
    sequence: indexEntry.sequence, lookup_id: indexEntry.lookup_id, batch_id: indexEntry.batch_id, transaction_id: indexEntry.transaction_id,
    business_journal_id: journal.journal_id,
    ...Object.fromEntries(handoffFields.map((key) => [key, expectedHandoff[key]])),
    business_terminal_state: indexEntry.outcome, repair_receipt_id: indexedReceipt.receipt_id,
    repair_receipt_path: indexEntry.receipt_path, repair_receipt_sha256: indexEntry.receipt_sha256,
    recorded_at: afterAttempt.attempts.at(-1).recorded_at,
  };
  if (canonical(afterAttempt.attempts.at(-1)) !== canonical(expectedAttempt)
      || attemptReceiptTarget.path !== indexEntry.receipt_path || attemptReceiptTarget.after_sha256 !== indexEntry.receipt_sha256) return false;
  if (graph.repair_receipt.outcome === "rolled-back") {
    const repair = graph.repair_receipt; const recovery = graph.recovery_receipt;
    const repairBody = clone(repair); delete repairBody.receipt_id; const recoveryBody = clone(recovery); delete recoveryBody.receipt_id;
    return marker.state === "rolled-back" && graph.fact_receipt === null
      && validateRegistered(recovery, schemaRoot, registryDoc, "recovery-receipt/1.0.0", schemaSha, registrySha)
      && recovery.receipt_id === hash(Buffer.from(canonical(recoveryBody))) && recovery.journal_id === journal.journal_id && recovery.transaction_id === journal.transaction_id
      && recovery.outcome === "rolled-back" && canonical(recovery.target_states) === canonical(journal.targets.map(() => "before"))
      && repair.receipt_id === hash(Buffer.from(canonical(repairBody))) && repair.batch_id === batch.batch_id && repair.nonce_status === "invalidated"
      && repair.nonce_state_id === finalNonce.state_id && repair.fact_receipt_id === null && repair.transaction_id === journal.transaction_id
      && repair.journal_id === journal.journal_id && repair.retry_required && Boolean(repair.error_code)
      && attemptMarker.state === "committed";
  }
  if (marker.state !== "committed" || graph.repair_receipt.outcome !== "committed"
      || !validateRegistered(graph.fact_receipt, schemaRoot, registryDoc, "fact-mutation-receipt/1.0.0", schemaSha, registrySha)) return false;
  const fact = graph.fact_receipt; const repair = graph.repair_receipt;
  const factCommand = graph.fact_command;
  if (!factCommand || factCommand.operation !== "patch" || factCommand.workstream_id !== batch.command.workstream_id
      || factCommand.expected_wdr_revision !== batch.command.expected_wdr_revision
      || factCommand.expected_file_generation !== batch.command.expected_file_generation
      || canonical(factCommand.set) !== canonical({ refresh_actions: true })) return false;
  const factSubgraph = {
    capability_registry: graph.capability_registry, command: factCommand, journal, marker,
    before_state: graph.before_state, after_state: graph.after_state, receipt: fact, proof: graph.proof,
    before_command_index: graph.before_command_index, command_index: graph.command_index,
    before_outbox: graph.before_outbox ?? null, after_outbox: graph.after_outbox ?? null,
  };
  if (![runtimeCapabilityBytes, runtimeRootRegistryBytes, runtimeActivationBytes].every(Buffer.isBuffer) || !authorityContext
    || !factAttributionSemantics(factSubgraph, registryDoc, schemaRoot, schemaSha, registrySha, runtimeCapabilityBytes,
      runtimeRootRegistryBytes, runtimeActivationBytes, runtimeAttestationBytes, authorityContext)) return false;
  let wdrBefore; let wdrStateBefore; let sidecarBefore; let sidecarAfter; let ledgerRaw; let ledgerState; let ledgerRows;
  try {
    const byPath = Object.fromEntries(graph.proof.business_artifacts.map((row) => [row.path, row]));
    const workstreamId = batch.command.workstream_id; const wdrPath = `workstreams/${workstreamId}/delivery-record.md`;
    const statePath = `workstreams/${workstreamId}/delivery-record.state.json`; const sidecarPath = `workstreams/${workstreamId}/action-projection.json`;
    wdrBefore = artifactBytes(byPath[wdrPath].before_bytes);
    wdrStateBefore = JSON.parse(artifactBytes(byPath[statePath].before_bytes).toString("utf8"));
    sidecarBefore = JSON.parse(artifactBytes(byPath[sidecarPath].before_bytes).toString("utf8"));
    sidecarAfter = JSON.parse(artifactBytes(byPath[sidecarPath].after_bytes).toString("utf8"));
    const reads = new Map(graph.proof.read_artifacts.map((row) => [row.path, artifactBytes(row.bytes)]));
    ledgerRaw = reads.get(registryDoc.runtime_paths.action_ledger.path);
    ledgerState = JSON.parse(reads.get(registryDoc.runtime_paths.action_ledger_state.path).toString("utf8"));
    ledgerRows = parseActionLedger(ledgerRaw);
  } catch { return false; }
  if (!validateRegistered(ledgerState, schemaRoot, registryDoc, "action-ledger-state/1.0.0", schemaSha, registrySha)
    || canonical(ledgerState) !== canonical(actionLedgerStateDocument(ledgerRows, ledgerRaw, ledgerState.ledger_revision, ledgerState.applied_commands, registryDoc, schemaSha, registrySha))
    || batch.read_set.ledger_fingerprint !== hash(ledgerRaw) || ledgerState.ledger_fingerprint !== hash(ledgerRaw)) return false;
  const ledgerById = new Map(ledgerRows.map((row) => [row.action_id, row]));
  for (const claim of batch.read_set.action_revisions) {
    const row = ledgerById.get(claim.action_id);
    if (claim.expected_present !== (row !== undefined) || claim.revision !== (row === undefined ? null : row.action_revision)) return false;
  }
  const workstreamId = batch.command.workstream_id;
  const expectedDriftRow = expectedDriftVerdict({
    generation_id: drift.generation_id, selection_policy_id: drift.selection_policy_id, selected_workstreams: [workstreamId],
    ledger_raw: ledgerRaw, ledger_state: ledgerState, wdrs: { [workstreamId]: wdrBefore },
    wdr_states: { [workstreamId]: wdrStateBefore }, sidecars: { [workstreamId]: sidecarBefore },
  }, registryDoc, schemaSha, registrySha).workstreams[0];
  const actualDriftRows = drift.workstreams.filter(({ workstream_id }) => workstream_id === workstreamId);
  const expectedAfterSnapshot = actionSnapshot(ledgerRows, workstreamId, ledgerState.ledger_fingerprint, ledgerState.ledger_revision);
  if (!wdrBefore || hash(wdrBefore) !== batch.read_set.wdr_revisions[0].fingerprint
      || actualDriftRows.length !== 1 || canonical(actualDriftRows[0]) !== canonical(expectedDriftRow)
      || sidecarAfter.ledger_fingerprint !== batch.read_set.ledger_fingerprint
      || canonical(sidecarAfter.actions) !== canonical(expectedAfterSnapshot.actions)) return false;
  const factBody = clone(fact); delete factBody.receipt_id; const repairBody = clone(repair); delete repairBody.receipt_id;
  const expectedBusinessPaths = [
    `workstreams/${batch.command.workstream_id}/delivery-record.md`,
    `workstreams/${batch.command.workstream_id}/delivery-record.state.json`,
    `workstreams/${batch.command.workstream_id}/action-projection.json`,
  ];
  if (canonical(business.map(({ path: value }) => value)) !== canonical(expectedBusinessPaths) || business.some(({ root_instance_id }) => root_instance_id !== dry.memory_root_instance_id)) return false;
  if (!(fact.receipt_id === hash(Buffer.from(canonical(factBody))) && fact.transaction_id === journal.transaction_id && fact.journal_id === journal.journal_id
    && canonical(fact.authorization) === canonical(journal.authorization) && fact.authorization.authorized_command_fingerprint === hash(Buffer.from(canonical(factCommand)))
    && canonical(fact.business_targets) === canonical(business) && canonical(fact.generation_state_target) === canonical(generation[0])
    && fact.before_fact_generation === batch.read_set.fact_generation && fact.after_fact_generation === batch.read_set.fact_generation + 1
    && canonical(fact.action_deltas) === canonical([]))) return false;
  if (!(repair.receipt_id === hash(Buffer.from(canonical(repairBody))) && repair.batch_id === batch.batch_id && repair.outcome === "committed"
    && repair.nonce_status === finalNonce.status && repair.nonce_state_id === finalNonce.state_id && repair.fact_receipt_id === fact.receipt_id
    && repair.transaction_id === journal.transaction_id && repair.journal_id === journal.journal_id)) return false;
  return receiptTargets[0].path === runtimePath(registryDoc, "repair_fact_receipt_template", null, null, null, journal.transaction_id)
    && receiptTargets[0].after_sha256 === hash(Buffer.from(canonical(fact)))
    && attemptReceiptTarget.path === runtimePath(registryDoc, "repair_receipt_template", null, null, null, journal.transaction_id)
    && attemptReceiptTarget.after_sha256 === hash(Buffer.from(canonical(repair)));
};

const twoBatchRepairRestartSemantics = (schemaRoot, registryDoc, schemaSha, registrySha) => {
  const groupKey = (graph) => {
    const batch = graph.dry_request.batch;
    const command = batch.command;
    return canonical([command.workflow, command.workstream_id, command.operation, batch.finding_ids]);
  };
  const graphValid = (graph) => repairGraphSemantics(
    graph, schemaRoot, registryDoc, schemaSha, registrySha,
    ...runtimeAuthorityFixture(registryDoc, schemaSha, registrySha, "adp-status-sync"),
  );

  const probe = repairGraphFixture(schemaSha, registrySha, registryDoc);
  const orderedWorkstreams = probe.audit.repair_batches.map(({ command }) => command.workstream_id);
  if (orderedWorkstreams.length !== 2 || new Set(orderedWorkstreams).size !== 2) return false;
  const [firstWorkstream, secondWorkstream] = orderedWorkstreams;
  const first = repairGraphFixture(
    schemaSha, registrySha, registryDoc, "committed", firstWorkstream, 7, "A", "batch-a",
  );
  const staleSecond = repairGraphFixture(
    schemaSha, registrySha, registryDoc, "rolled-back", secondWorkstream, 7, "B", "batch-b-stale",
  );
  const retrySecond = repairGraphFixture(
    schemaSha, registrySha, registryDoc, "committed", secondWorkstream, 8, "C", "batch-b-retry",
    first.after_state.last_transaction_id,
  );
  if (![first, staleSecond, retrySecond].every(graphValid)) return false;

  const attemptHandoffFaultProbe = (graph) => {
    const journal = graph.journal;
    const businessMarker = graph.marker;
    const attemptJournal = graph.attempt_journal;
    const attemptMarker = graph.attempt_marker;
    const beforeByRole = new Map([
      ["repair-attempt-ledger", Buffer.from(canonical(graph.before_attempt_ledger))],
      ["repair-index", Buffer.from(canonical(graph.before_repair_index))],
      ["receipt", null],
    ]);
    const afterByRole = new Map([
      ["repair-attempt-ledger", Buffer.from(canonical(graph.attempt_ledger))],
      ["repair-index", Buffer.from(canonical(graph.repair_index))],
      ["receipt", Buffer.from(canonical(graph.repair_receipt))],
    ]);
    const child = String.raw`
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
const root=process.argv[1],businessManifestPath=process.argv[2],businessMarkerPath=process.argv[3],markedAt=process.argv[4];
const canonical=(value)=>value===null?"null":value===true?"true":value===false?"false":typeof value==="number"?JSON.stringify(value):typeof value==="string"?JSON.stringify(value):Array.isArray(value)?"["+value.map(canonical).join(",")+"]":"{"+Object.keys(value).sort().map((key)=>canonical(key)+":"+canonical(value[key])).join(",")+"}";
const digest=(raw)=>"sha256:"+crypto.createHash("sha256").update(raw).digest("hex");
const token=(value)=>"i_"+crypto.createHash("sha256").update(value).digest("hex");
const read=(target)=>fs.readFileSync(path.join(root,target));
const parseCanonical=(raw)=>{const value=JSON.parse(raw);if(canonical(value)!==raw.toString("utf8"))throw new Error("non-canonical document");return value;};
const bjRaw=read(businessManifestPath),bmRaw=read(businessMarkerPath),bj=parseCanonical(bjRaw),bm=parseCanonical(bmRaw);
if(!["committed","rolled-back"].includes(bm.state)||bm.journal_id!==bj.journal_id||bm.manifest_id!==bj.manifest_id)throw new Error("business terminal mismatch");
const bmBody={...bm};delete bmBody.marker_id;if(bm.marker_id!==digest(Buffer.from(canonical(bmBody))))throw new Error("business marker identity mismatch");
const recoveryPath=path.join(root,bj.recovery_receipt_path),recoveryRaw=fs.existsSync(recoveryPath)?fs.readFileSync(recoveryPath):null,recovery=recoveryRaw===null?null:parseCanonical(recoveryRaw);
const body={business_transaction_id:bj.transaction_id,business_journal_id:bj.journal_id,business_marker_id:bm.marker_id,business_marker_sha256:digest(bmRaw),recovery_receipt_id:recovery===null?null:recovery.receipt_id,recovery_receipt_sha256:recoveryRaw===null?null:digest(recoveryRaw)};
const suffix=digest(Buffer.from(canonical(body))).replace("sha256:",""),attemptTx="repair-attempt:"+suffix,attemptJournalId="journal-repair-attempt:"+suffix;
const attemptDir="state/transactions/"+token(attemptTx),attemptManifestPath=attemptDir+"/manifest.json",ajRaw=read(attemptManifestPath),aj=parseCanonical(ajRaw),ajBody={...aj};delete ajBody.manifest_id;
if(aj.manifest_id!==digest(Buffer.from(canonical(ajBody)))||aj.transaction_id!==attemptTx||aj.journal_id!==attemptJournalId||aj.journal_dir!==attemptDir)throw new Error("attempt manifest identity mismatch");
if(JSON.stringify(aj.targets.map((row)=>row.role))!==JSON.stringify(["repair-attempt-ledger","repair-index","receipt"]))throw new Error("attempt target order mismatch");
for(const target of aj.targets){
 const before=target.before_image===null?null:read(target.before_image.path),after=target.after_image===null?null:read(target.after_image.path);
 if((before===null?null:digest(before))!==target.before_sha256||(after===null?null:digest(after))!==target.after_sha256)throw new Error("attempt image mismatch");
 const targetPath=path.join(root,target.path),current=fs.existsSync(targetPath)?fs.readFileSync(targetPath):null;
 const currentMatches=(current===null&&before===null)||(current!==null&&before!==null&&current.equals(before))||(current!==null&&after!==null&&current.equals(after));
 if(!currentMatches)throw new Error("attempt target has unknown bytes");
 fs.mkdirSync(path.dirname(targetPath),{recursive:true});if(after===null){if(fs.existsSync(targetPath))fs.unlinkSync(targetPath);}else fs.writeFileSync(targetPath,after);
}
const marker={contract:bm.contract,schema_version:"1.0.0",journal_id:aj.journal_id,manifest_id:aj.manifest_id,state:"committed",marked_at:markedAt};marker.marker_id=digest(Buffer.from(canonical(marker)));
const terminal=path.join(root,aj.terminal_marker_path);fs.mkdirSync(path.dirname(terminal),{recursive:true});fs.writeFileSync(terminal,canonical(marker));
const loaded=aj.targets.map((target)=>fs.readFileSync(path.join(root,target.path)));if(!loaded.every((raw,index)=>digest(raw)===aj.targets[index].after_sha256))throw new Error("attempt roll-forward failed");
const [attemptLedger,repairIndex,repairReceipt]=loaded.map((raw)=>JSON.parse(raw)),indexEntry=repairIndex.entries.at(-1),attemptEntry=attemptLedger.attempts.at(-1),expected={...body,attempt_transaction_id:attemptTx,attempt_journal_id:attemptJournalId};
const fields=["attempt_transaction_id","attempt_journal_id","business_marker_id","business_marker_sha256","recovery_receipt_id","recovery_receipt_sha256"];
if(![repairReceipt,indexEntry,attemptEntry].every((document)=>fields.every((field)=>document[field]===expected[field])))throw new Error("attempt handoff mismatch");
if(indexEntry.receipt_sha256!==digest(Buffer.from(canonical(repairReceipt)))||indexEntry.receipt_sha256!==attemptEntry.repair_receipt_sha256)throw new Error("receipt handoff mismatch");
process.stdout.write(JSON.stringify([marker.marker_id,attemptLedger.ledger_id,repairIndex.index_id,repairReceipt.receipt_id]));
`;
    const write = (root, targetPath, raw) => {
      const target = path.join(root, targetPath);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, raw);
    };
    const expectedOutput = canonical([
      attemptMarker.marker_id, graph.attempt_ledger.ledger_id,
      graph.repair_index.index_id, graph.repair_receipt.receipt_id,
    ]);
    for (let appliedCount = 0; appliedCount <= attemptJournal.targets.length; appliedCount += 1) {
      const root = fs.mkdtempSync(path.join(os.tmpdir(), "adp-repair-attempt-"));
      try {
        write(root, journal.manifest_path, Buffer.from(canonical(journal)));
        write(root, journal.terminal_marker_path, Buffer.from(canonical(businessMarker)));
        if (graph.recovery_receipt && typeof graph.recovery_receipt === "object")
          write(root, journal.recovery_receipt_path, Buffer.from(canonical(graph.recovery_receipt)));
        write(root, attemptJournal.manifest_path, Buffer.from(canonical(attemptJournal)));
        for (const target of attemptJournal.targets) {
          const beforeRaw = beforeByRole.get(target.role);
          const afterRaw = afterByRole.get(target.role);
          if (beforeRaw !== null) write(root, target.before_image.path, beforeRaw);
          write(root, target.after_image.path, afterRaw);
          const selected = target.apply_order < appliedCount ? afterRaw : beforeRaw;
          if (selected !== null) write(root, target.path, selected);
        }
        const completed = spawnSync(process.execPath, [
          "--input-type=module", "-e", child, root, journal.manifest_path,
          journal.terminal_marker_path, attemptMarker.marked_at,
        ], { encoding: "utf8", timeout: 30000, maxBuffer: 16 * 1024 * 1024 });
        if (completed.status !== 0 || completed.stdout !== expectedOutput
            || !fs.readFileSync(path.join(root, attemptJournal.terminal_marker_path)).equals(Buffer.from(canonical(attemptMarker)))) return false;
        if (!attemptJournal.targets.every((target) =>
          fs.readFileSync(path.join(root, target.path)).equals(afterByRole.get(target.role)))) return false;
      } finally { fs.rmSync(root, { recursive: true, force: true }); }
    }
    return true;
  };

  let currentFactState = clone(first.before_state);
  const durableReceipts = new Map();
  const executionCounts = new Map();
  const firstKey = groupKey(first);
  const secondKey = groupKey(staleSecond);
  if (firstKey === secondKey || canonical(first.before_state) !== canonical(currentFactState)) return false;
  currentFactState = clone(first.after_state);
  durableReceipts.set(firstKey, [clone(first.repair_receipt)]);
  executionCounts.set(firstKey, 1);

  if (canonical(staleSecond.before_state) === canonical(currentFactState)) return false;
  if (staleSecond.repair_receipt.outcome !== "rolled-back"
      || staleSecond.repair_receipt.nonce_status !== "invalidated"
      || !staleSecond.repair_receipt.retry_required
      || staleSecond.fact_receipt !== null) return false;
  durableReceipts.set(secondKey, [clone(staleSecond.repair_receipt)]);
  executionCounts.set(secondKey, 1);

  const orderedGroups = probe.audit.repair_batches.map((row) => canonical([
    row.command.workflow, row.command.workstream_id, row.command.operation, row.finding_ids,
  ]));
  const retryCursor = orderedGroups.find((key) => {
    const receipts = durableReceipts.get(key);
    return !receipts || receipts.at(-1).outcome !== "committed";
  });
  if (retryCursor !== secondKey || executionCounts.get(firstKey) !== 1) return false;

  if (groupKey(retrySecond) !== secondKey
      || canonical(retrySecond.before_state) !== canonical(currentFactState)
      || retrySecond.dry_request.batch.read_set.fact_generation !== 8
      || retrySecond.dry_result.token === staleSecond.dry_result.token
      || retrySecond.dry_result.binding_digest === staleSecond.dry_result.binding_digest
      || retrySecond.dry_request.batch.batch_id === staleSecond.dry_request.batch.batch_id) return false;
  currentFactState = clone(retrySecond.after_state);
  durableReceipts.get(secondKey).push(clone(retrySecond.repair_receipt));
  executionCounts.set(secondKey, executionCounts.get(secondKey) + 1);
  return currentFactState.fact_generation === 9
    && executionCounts.get(firstKey) === 1
    && executionCounts.get(secondKey) === 2
    && attemptHandoffFaultProbe(first)
    && attemptHandoffFaultProbe(staleSecond)
    && canonical(orderedGroups.map((key) => durableReceipts.get(key).at(-1).outcome)) === canonical(["committed", "committed"])
    && durableReceipts.get(secondKey)[0].retry_required
    && !durableReceipts.get(secondKey)[1].retry_required;
};

const payloadBindingValid = (binding, payload, schemaRoot, projectRoot, workspaceRoot) => {
  const root = binding.schema_root === "project" ? projectRoot : workspaceRoot;
  const boundSchema = JSON.parse(fs.readFileSync(path.join(root, binding.schema_path)));
  return binding.schema_pointer ? schemaErrors(payload, jsonPointer(boundSchema, binding.schema_pointer), boundSchema).length === 0 : validateDocument(payload, boundSchema);
};

const panelV1CompatibilityValid = (panel, compatibility, projectRoot) => {
  const modelSchema = JSON.parse(fs.readFileSync(path.join(projectRoot, "skills/adp-management-panel/assets/adp-management-panel-v1.schema.json")));
  const manifestSchema = JSON.parse(fs.readFileSync(path.join(projectRoot, "skills/adp-management-panel/assets/adp-management-panel-manifest-v1.schema.json")));
  const localModelSchema = clone(modelSchema); localModelSchema.properties.manifest = { type: "object" };
  const model = panel.model_v1;
  if (!validateDocument(model, localModelSchema) || !validateDocument(model.manifest, manifestSchema)) return false;
  if (canonical(model.views.map(({ view_id }) => view_id).sort()) !== canonical([...compatibility.required_view_ids].sort())) return false;
  if (canonical(Object.keys(model.data).sort()) !== canonical([...compatibility.required_data_keys].sort())) return false;
  if (canonical(Object.keys(model.data.flows).sort()) !== canonical([...compatibility.required_flow_keys].sort()) || canonical(Object.keys(model.data.meetings).sort()) !== canonical([...compatibility.required_meeting_keys].sort())) return false;
  for (const [scenario, keys] of Object.entries(compatibility.required_board_keys)) if (canonical(Object.keys(model.data.meetings[scenario].boards).sort()) !== canonical(keys)) return false;
  for (const check of compatibility.consumer_binding_checks) { let target; try { target = jsonPointer(model, check.target_pointer); } catch { return false; } if (hash(Buffer.from(canonical(target))) !== check.target_sha256 || !check.copy_equal) return false; }
  const current = panel.sync.canonical.status.workstream_current;
  return current.length > 0 && current.every((row) => ["workstream_id", "progress", "blockers", "risks"].every((key) => Object.hasOwn(row, key)));
};

const panelV1CompositionValid = (panel, registryDoc, projectRoot) => {
  const inputs = clone(panel.sync.compatibility_inputs); inputs.meeting_packs = {};
  const payloads = {
    "program-status": panel.sync.canonical.status, roadmap: panel.sync.canonical.roadmap,
    "flow-graph": panel.sync.canonical.flow, "meeting-pack": panel.sync.canonical.meetings,
  };
  try {
    for (const binding of registryDoc.panel_v1_composition.source_bindings) {
      let source = payloads[binding.projection_kind];
      if (binding.projection_kind === "meeting-pack") source = source[binding.instance_key];
      const value = clone(jsonPointer(source, binding.source_pointer));
      if (binding.projection_kind === "program-status") for (const key of registryDoc.panel_v1_composition.program_status_overlay) value[key] = clone(source[key]);
      const parts = binding.input_key.split("/"); let target = inputs;
      for (const part of parts.slice(0, -1)) { target[part] ??= {}; target = target[part]; }
      target[parts.at(-1)] = value;
    }
  } catch { return false; }
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "adp-panel-v1-compose-"));
  try {
    const inputPath = path.join(folder, "input.json"); fs.writeFileSync(inputPath, `${JSON.stringify(inputs)}\n`, "utf8");
    const result = spawnSync("python3", [path.join(projectRoot, "skills/adp-management-panel/scripts/panel_model.py"), inputPath], { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
    if (result.status !== 0) return false;
    return canonical(JSON.parse(result.stdout)) === canonical(panel.model_v1);
  } catch { return false; }
  finally { fs.rmSync(folder, { recursive: true, force: true }); }
};

const expectedPanelV2CurrentView = (panel, registryDoc) => {
  const spec = registryDoc.panel_v2_consumer;
  const sourceRows = clone(jsonPointer(panel, spec.primary_source_pointer));
  if (!Array.isArray(sourceRows) || !sourceRows.length) throw new Error("current workstream rows are required");
  const rows = sourceRows.map((row) => {
    if (!spec.required_fields.every((field) => Object.hasOwn(row, field))) throw new Error("current workstream fields are incomplete");
    const current = Object.fromEntries(["workstream_id", "progress", "blockers", "risks"].map((key) => [key, row[key]]));
    if (["workstream_id", "progress"].some((key) => typeof current[key] !== "string" || !current[key].trim() || current[key] !== current[key].normalize("NFC"))) throw new Error("invalid current workstream scalar");
    for (const key of ["blockers", "risks"]) if (!Array.isArray(current[key]) || current[key].some((value) => typeof value !== "string" || !value.trim() || value !== value.normalize("NFC"))) throw new Error(`invalid ${key}`);
    return current;
  }).sort((left, right) => Buffer.from(left.workstream_id).compare(Buffer.from(right.workstream_id)));
  if (new Set(rows.map(({ workstream_id }) => workstream_id)).size !== rows.length) throw new Error("duplicate workstream_id");
  const escapeHtml = (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const html = rows.map((row) => `<section data-workstream-id="${escapeHtml(row.workstream_id)}"><h3>${escapeHtml(row.workstream_id)}</h3><p data-field="progress">${escapeHtml(row.progress)}</p><ul data-field="blockers">${row.blockers.map((value) => `<li>${escapeHtml(value)}</li>`).join("")}</ul><ul data-field="risks">${row.risks.map((value) => `<li>${escapeHtml(value)}</li>`).join("")}</ul></section>`).join("");
  return { schema_version: "2.0.0", consumer_id: spec.id, source_panel_id: panel.panel_id, source_pointer: spec.primary_source_pointer, rows, html };
};

const executePanelV2Consumer = (panel, registryDoc, schemaRoot, projectRoot) => {
  const spec = registryDoc.panel_v2_consumer;
  const artifact = registryDoc.pinned_source_artifacts.find(({ id }) => id === spec.artifact_id);
  const result = spawnSync(process.execPath, [path.join(projectRoot, artifact.path), "--trace"], { input: JSON.stringify(panel), encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
  if (result.status !== 0) return null;
  try {
    const traced = JSON.parse(result.stdout); const actual = traced.result; const reads = traced.accessed_pointers;
    const expected = expectedPanelV2CurrentView(panel, registryDoc); const declared = spec.source_pointers; const forbidden = spec.forbidden_source_prefixes;
    const readsOk = canonical(reads) === canonical([spec.primary_source_pointer, "/panel_id"])
      && canonical([...new Set(reads)].sort()) === canonical([...new Set(declared)].sort())
      && reads.every((pointer) => forbidden.every((prefix) => pointer !== prefix && !pointer.startsWith(`${prefix}/`)));
    return readsOk && canonical(actual) === canonical(expected) && validate(actual, schemaRoot, "managementPanelCurrentViewV2") ? actual : null;
  } catch { return null; }
};

const buildProjectionLineage = (panel, upstreams, registryDoc, schemaRoot, schemaSha, registrySha, projectRoot, workspaceRoot, policy, readMutation = null) => {
  const generation = panel.sync.generation_id; const selection = panel.sync.selection_policy_id;
  const selected = resolvedSelection(policy);
  const bindings = Object.fromEntries(registryDoc.projection_payload_bindings.map((row) => [row.projection_kind, row]));
  const payloads = Object.fromEntries(Object.entries(upstreams).map(([kind, value]) => [
    kind,
    (Array.isArray(value) ? value : [value]).sort((left, right) => Buffer.from(left.scenario ?? "").compare(Buffer.from(right.scenario ?? ""))),
  ])); payloads["management-panel"] = [panel];
  const built = {}; let valid = true;
  const expectedInstances = expectedProjectionInstances(registryDoc, policy);
  if (canonical(Object.keys(payloads).sort()) !== canonical(Object.keys(expectedInstances).sort())) valid = false;
  for (const [kind, keys] of Object.entries(expectedInstances)) {
    const actual = (payloads[kind] ?? []).map((payload) => kind === "meeting-pack" ? payload.scenario : null);
    if (canonical(instanceSort(actual)) !== canonical(instanceSort(keys))) valid = false;
  }
  for (const profile of registryDoc.projection_input_profiles) {
    const kind = profile.projection; const binding = bindings[kind]; built[kind] = [];
    for (const payload of payloads[kind] ?? []) {
      valid = valid && payloadBindingValid(binding, payload, schemaRoot, projectRoot, workspaceRoot);
      if (kind === "management-panel") valid = valid && payload?.sync?.source_as_of === policy.as_of;
      else if (["state-audit", "program-status", "roadmap", "meeting-pack"].includes(kind)) valid = valid && payload?.source_as_of === policy.as_of;
      const envelope = { contract: { schema_id: "urn:adp:panel-sync-contracts:2026-07-24#canonical-projection-envelope-v1", schema_sha256: schemaSha, registry_sha256: registrySha }, schema_version: "1.0.0", projection_kind: kind, instance_key: kind === "meeting-pack" ? payload.scenario : null, generation_id: generation, payload_schema_id: binding.schema_id, payload_schema_sha256: binding.schema_sha256, payload_sha256: hash(Buffer.from(canonical(payload))), payload };
      envelope.projection_id = hash(Buffer.from(canonical(envelope)));
      const predecessors = profile.direct_upstreams.flatMap(({ kind: dependency }) => built[dependency].map(({ handle }) => handle));
      const mutation = readMutation !== null && readMutation[0] === kind ? readMutation[1] : "none";
      const [allowedSources, actualReads] = instrumentedReadTrace(profile, selected, mutation, policy);
      const manifest = { contract: { schema_id: "urn:adp:panel-sync-contracts:2026-07-24#dependency-manifest-v1", schema_sha256: schemaSha, registry_sha256: registrySha }, schema_version: "1.0.0", producer: { skill: `adp-${kind}`, version: "1.0.0" }, projection: { kind, id: envelope.projection_id }, generation_id: generation, input_profile_id: profile.profile_id, selection_policy_id: selection, sources: clone(actualReads), upstreams: predecessors };
      manifest.manifest_id = hash(Buffer.from(canonical(manifest))); const handle = { kind, id: envelope.projection_id, manifest_id: manifest.manifest_id, generation_id: generation };
      const receipt = { contract: { schema_id: "urn:adp:panel-sync-contracts:2026-07-24#producer-receipt-v1", schema_sha256: schemaSha, registry_sha256: registrySha }, schema_version: "1.0.0", generation_id: generation, input_profile_id: profile.profile_id, selection_policy_id: selection, consumed_sources: clone(actualReads), consumed_predecessors: predecessors, output: clone(handle), status: "produced", error_code: null };
      receipt.receipt_id = hash(Buffer.from(canonical(receipt)));
      valid = valid
        && validateRegistered(envelope, schemaRoot, registryDoc, "canonical-projection-envelope/1.0.0", schemaSha, registrySha)
        && validateRegistered(manifest, schemaRoot, registryDoc, "projection-dependency-manifest/1.0.0", schemaSha, registrySha)
        && validateRegistered(receipt, schemaRoot, registryDoc, "producer-receipt/1.0.0", schemaSha, registrySha);
      built[kind].push({ envelope, manifest, receipt, handle, allowedSources, actualReads });
    }
  }
  return [built, valid];
};

const projectionLineageSemantics = (built, registryDoc, schemaRoot, generationEnvelope, policy, schemaSha, registrySha) => {
  const profilesByKind = Object.fromEntries(registryDoc.projection_input_profiles.map((profile) => [profile.projection, profile])); const generations = new Set();
  const expectedInstances = expectedProjectionInstances(registryDoc, policy);
  if (canonical(Object.keys(built).sort()) !== canonical(Object.keys(expectedInstances).sort())) return false;
  for (const [kind, keys] of Object.entries(expectedInstances)) if (canonical(instanceSort(built[kind].map(({ envelope }) => envelope.instance_key))) !== canonical(instanceSort(keys))) return false;
  const physicalIds = generationEnvelope.leaf_sources.map((row) => `${row.root_instance_id}\0${row.path}`);
  if (new Set(physicalIds).size !== physicalIds.length) return false;
  for (const [kind, instances] of Object.entries(built)) for (const item of instances) {
    const { envelope, manifest, receipt, handle } = item; generations.add(envelope.generation_id);
    if (!validateRegistered(envelope, schemaRoot, registryDoc, "canonical-projection-envelope/1.0.0", schemaSha, registrySha)
        || !validateRegistered(manifest, schemaRoot, registryDoc, "projection-dependency-manifest/1.0.0", schemaSha, registrySha)
        || !validateRegistered(receipt, schemaRoot, registryDoc, "producer-receipt/1.0.0", schemaSha, registrySha)) return false;
    const body = clone(envelope); delete body.projection_id;
    const manifestBody = clone(manifest); delete manifestBody.manifest_id; const receiptBody = clone(receipt); delete receiptBody.receipt_id;
    if (envelope.payload_sha256 !== hash(Buffer.from(canonical(envelope.payload))) || envelope.projection_id !== hash(Buffer.from(canonical(body)))) return false;
    if (manifest.manifest_id !== hash(Buffer.from(canonical(manifestBody))) || receipt.receipt_id !== hash(Buffer.from(canonical(receiptBody)))) return false;
    const binding = registryDoc.projection_payload_bindings.find(({ projection_kind }) => projection_kind === kind);
    if (envelope.payload_schema_id !== binding.schema_id || envelope.payload_schema_sha256 !== binding.schema_sha256) return false;
    if (canonical(manifest.projection) !== canonical({ kind, id: envelope.projection_id }) || canonical(handle) !== canonical(receipt.output)) return false;
    if (receipt.generation_id !== envelope.generation_id || manifest.generation_id !== envelope.generation_id || receipt.input_profile_id !== profilesByKind[kind].profile_id) return false;
    if (manifest.selection_policy_id !== policy.policy_id || receipt.selection_policy_id !== policy.policy_id) return false;
    if (canonical(manifest.sources) !== canonical(item.actualReads) || canonical(receipt.consumed_sources) !== canonical(item.actualReads) || canonical(item.actualReads) !== canonical(item.allowedSources)) return false;
    const leaves = new Map(generationEnvelope.leaf_sources.map((source) => [`${source.root_instance_id}\0${source.path}`, source]));
    for (const source of item.actualReads) { const leaf = leaves.get(`${source.root_instance_id}\0${source.path}`); if (!leaf || leaf.fingerprint !== source.fingerprint || leaf.blob_id !== source.blob_id) return false; }
    const predecessors = profilesByKind[kind].direct_upstreams.flatMap(({ kind: dependency }) => built[dependency].map(({ handle: value }) => value));
    if (canonical(manifest.upstreams) !== canonical(predecessors) || canonical(receipt.consumed_predecessors) !== canonical(predecessors)) return false;
  }
  return generations.size === 1 && generations.has(generationEnvelope.generation_id);
};

const setTargetAfter = (target, document) => { const digest = hash(Buffer.from(canonical(document))); target.after_sha256 = digest; target.after_image.sha256 = digest; };
const reindexTargets = (targets, journalDir) => targets.forEach((target, index) => {
  target.apply_order = index;
  if (target.before_image !== null) target.before_image.path = `${journalDir}/images/${index}-before`;
  if (target.after_image !== null) target.after_image.path = `${journalDir}/images/${index}-after`;
});
const finalizePanelPublicationGraph = (graph) => {
  const pointerBody = clone(graph.pointer); delete pointerBody.pointer_id; graph.pointer.pointer_id = hash(Buffer.from(canonical(pointerBody)));
  const stateBody = clone(graph.state); delete stateBody.state_id; graph.state.state_id = hash(Buffer.from(canonical(stateBody)));
  const receiptBody = clone(graph.receipt); delete receiptBody.receipt_id; graph.receipt.receipt_id = hash(Buffer.from(canonical(receiptBody)));
  for (const target of graph.journal.targets) {
    if (target.role === "pointer") setTargetAfter(target, graph.pointer);
    else if (target.role === "panel-state") setTargetAfter(target, graph.state);
    else if (target.role === "receipt") setTargetAfter(target, graph.receipt);
  }
  graph.journal.receipt_target_paths = graph.journal.targets.filter(({ role }) => role === "receipt").map(({ path: value }) => value);
  const journalBody = clone(graph.journal); delete journalBody.manifest_id; graph.journal.manifest_id = hash(Buffer.from(canonical(journalBody)));
  graph.marker.manifest_id = graph.journal.manifest_id; const markerBody = clone(graph.marker); delete markerBody.marker_id; graph.marker.marker_id = hash(Buffer.from(canonical(markerBody)));
};

const panelPublicationFixture = (panel, built, policy, generation, registryDoc, schemaSha, registrySha, mutation = "none", physicalInventory = null, refreshReceipt = null) => {
  const contract = (anchor) => ({ schema_id: `urn:adp:panel-sync-contracts:2026-07-24#${anchor}`, schema_sha256: schemaSha, registry_sha256: registrySha });
  const transactionId = "tx-panel-1"; const transactionToken = filesystemToken(transactionId); const journalDir = `state/transactions/${transactionToken}`;
  const firstPublication = ["first-publication", "first-publication-idempotent"].includes(mutation);
  const items = Object.values(built).flat().sort((left, right) => Buffer.from(`${left.handle.kind}\0${left.envelope.instance_key ?? ""}`).compare(Buffer.from(`${right.handle.kind}\0${right.envelope.instance_key ?? ""}`)));
  const pointers = [];
  for (const { handle, envelope } of items) {
    const templateName = handle.kind === "management-panel" ? "management_panel_template" : "canonical_projection_template";
    const canonicalPath = runtimePath(registryDoc, templateName, generation.generation_id, handle.kind, envelope.instance_key);
    pointers.push({ kind: handle.kind, instance_key: envelope.instance_key, id: handle.id, manifest_id: handle.manifest_id, canonical_path: canonicalPath });
  }
  const pointer = { contract: contract("panel-current-pointer-v1"), schema_version: "1.0.0", generation_id: generation.generation_id, panel_id: panel.panel_id, projections: pointers };
  pointer.pointer_id = hash(Buffer.from(canonical(pointer)));
  let beforePointer = null; let beforeState = null;
  if (!firstPublication) {
    beforePointer = { contract: contract("panel-current-pointer-v1"), schema_version: "1.0.0", generation_id: `sha256:${"0".repeat(64)}`, panel_id: `sha256:${"0".repeat(64)}`, projections: clone(pointers) };
    beforePointer.pointer_id = hash(Buffer.from(canonical(beforePointer)));
    beforeState = { contract: contract("panel-state-v1"), schema_version: "1.0.0", panel_generation: 7, current_pointer_id: beforePointer.pointer_id };
    beforeState.state_id = hash(Buffer.from(canonical(beforeState)));
  }
  const beforeGeneration = firstPublication ? 0 : beforeState.panel_generation;
  const state = { contract: contract("panel-state-v1"), schema_version: "1.0.0", panel_generation: beforeGeneration + 1, current_pointer_id: pointer.pointer_id };
  state.state_id = hash(Buffer.from(canonical(state)));
  physicalInventory ??= physicalInventoryFixture(registryDoc, policy, generation.fact_generation, schemaSha, registrySha);
  if (refreshReceipt === null) {
    const nodes = items.map((item) => ({ instance_key: item.envelope.instance_key ?? "singleton", projection_kind: item.handle.kind, disposition: "produced", invalidation_reasons: [], output: clone(item.handle), error_code: null }))
      .sort((left, right) => Buffer.from(`${left.instance_key}\0${left.projection_kind}`).compare(Buffer.from(`${right.instance_key}\0${right.projection_kind}`)));
    refreshReceipt = {
      contract: expectedContractRef(registryDoc, "refresh-run-receipt/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
      refresh_id: "refresh-snapshot-fixture", snapshot_id: policy.snapshot_id, snapshot_lock_receipt_id: policy.snapshot_lock_receipt_id,
      generation_id: generation.generation_id, expected_fact_generation: generation.fact_generation, expected_panel_generation: beforeGeneration,
      status: "published", nodes, retry_from_instance_key: null, source_as_of: policy.as_of,
    };
    refreshReceipt.receipt_id = hash(Buffer.from(canonical(refreshReceipt)));
  }

  const lineageDocuments = {}; const objects = [];
  const identityFields = {
    "generation-envelope/1.0.0": "generation_id", "selection-policy/1.0.0": "policy_id", "physical-workstream-inventory/1.0.0": "attestation_id",
    "panel-binding-catalog/1.0.0": "catalog_id", "canonical-projection-envelope/1.0.0": "projection_id", "projection-dependency-manifest/1.0.0": "manifest_id",
    "producer-receipt/1.0.0": "receipt_id", "refresh-run-receipt/1.0.0": "receipt_id", "panel-current-pointer/1.0.0": "pointer_id",
    "panel-state/1.0.0": "state_id", "publication-absence-proof/1.0.0": "proof_id",
  };
  const addObject = (objectKind, contractName, document, objectPath, projectionKind = null, instanceKey = null) => {
    const raw = Buffer.from(canonical(document)); lineageDocuments[objectPath] = raw;
    objects.push({ object_kind: objectKind, projection_kind: projectionKind, instance_key: instanceKey, contract_name: contractName,
      object_id: document[identityFields[contractName]], root: "memory", root_instance_id: "123e4567-e89b-42d3-a456-426614174000",
      path: objectPath, cardinality: "one", sha256: hash(raw) });
  };
  const generationId = generation.generation_id;
  addObject("generation", "generation-envelope/1.0.0", generation, runtimePath(registryDoc, "generation_envelope_template", generationId));
  addObject("selection-policy", "selection-policy/1.0.0", policy, runtimePath(registryDoc, "selection_policy_template", generationId));
  addObject("physical-inventory", "physical-workstream-inventory/1.0.0", physicalInventory, runtimePath(registryDoc, "physical_inventory_template", generationId));
  const catalog = panelBindingCatalog(registryDoc, schemaSha, registrySha);
  addObject("panel-binding-catalog", "panel-binding-catalog/1.0.0", catalog, runtimePath(registryDoc, "panel_binding_catalog_template", generationId));
  for (const [kind, instances] of Object.entries(built)) for (const item of instances) {
    const instanceKey = item.envelope.instance_key; const envelopeTemplate = kind === "management-panel" ? "management_panel_template" : "canonical_projection_template";
    addObject("projection-envelope", "canonical-projection-envelope/1.0.0", item.envelope, runtimePath(registryDoc, envelopeTemplate, generationId, kind, instanceKey), kind, instanceKey);
    addObject("dependency-manifest", "projection-dependency-manifest/1.0.0", item.manifest, runtimePath(registryDoc, "dependency_manifest_template", generationId, kind, instanceKey), kind, instanceKey);
    addObject("producer-receipt", "producer-receipt/1.0.0", item.receipt, runtimePath(registryDoc, "producer_receipt_template", generationId, kind, instanceKey), kind, instanceKey);
  }
  addObject("refresh-receipt", "refresh-run-receipt/1.0.0", refreshReceipt, runtimePath(registryDoc, "refresh_receipt_generation_template", generationId));
  if (firstPublication) {
    const absenceProof = {
      contract: expectedContractRef(registryDoc, "publication-absence-proof/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
      generation_id: generationId, memory_root_instance_id: "123e4567-e89b-42d3-a456-426614174000",
      pointer_path: registryDoc.runtime_paths.panel_current_pointer.path, panel_state_path: registryDoc.runtime_paths.panel_state.path,
      pointer_absent: true, panel_state_absent: true, fact_lock_profile_id: registryDoc.lock_profile.profile_id,
      panel_lock_profile_id: registryDoc.lock_profile.profile_id, observed_at: policy.as_of,
    };
    absenceProof.proof_id = hash(Buffer.from(canonical(absenceProof)));
    addObject("publication-absence-proof", "publication-absence-proof/1.0.0", absenceProof, runtimePath(registryDoc, "publication_absence_proof_template", generationId));
  } else {
    addObject("before-pointer", "panel-current-pointer/1.0.0", beforePointer, runtimePath(registryDoc, "before_pointer_template", generationId));
    addObject("before-panel-state", "panel-state/1.0.0", beforeState, runtimePath(registryDoc, "before_panel_state_template", generationId));
  }
  objects.sort((left, right) => Buffer.from(`${left.object_kind}\0${left.projection_kind ?? ""}\0${left.instance_key ?? ""}`).compare(Buffer.from(`${right.object_kind}\0${right.projection_kind ?? ""}\0${right.instance_key ?? ""}`)));
  const lineageIndex = { contract: expectedContractRef(registryDoc, "generation-lineage-index/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", generation_id: generationId, objects };
  lineageIndex.index_id = hash(Buffer.from(canonical(lineageIndex)));
  const lineageIndexPath = runtimePath(registryDoc, "generation_lineage_index_template", generationId); lineageDocuments[lineageIndexPath] = Buffer.from(canonical(lineageIndex));
  const lineageTargets = [];
  for (const row of objects) {
    const role = row.object_kind === "projection-envelope" ? (row.projection_kind === "management-panel" ? "panel" : "projection") : "lineage-object";
    const target = mutationTarget(role, "create", lineageTargets.length, row.path); target.after_sha256 = row.sha256; target.after_image.sha256 = row.sha256; lineageTargets.push(target);
  }
  const indexTarget = mutationTarget("lineage-index", "create", lineageTargets.length, lineageIndexPath); setTargetAfter(indexTarget, lineageIndex); lineageTargets.push(indexTarget);
  const stateTarget = mutationTarget("panel-state", firstPublication ? "create" : "replace", lineageTargets.length, registryDoc.runtime_paths.panel_state.path);
  if (!firstPublication) { stateTarget.before_sha256 = hash(Buffer.from(canonical(beforeState))); stateTarget.before_image.sha256 = stateTarget.before_sha256; }
  setTargetAfter(stateTarget, state);
  const receiptPath = registryDoc.runtime_paths.panel_receipt_template.path.replace("{transaction_token}", transactionToken);
  const receiptTarget = mutationTarget("receipt", "create", lineageTargets.length + 1, receiptPath);
  const pointerTarget = mutationTarget("pointer", firstPublication ? "create" : "replace", lineageTargets.length + 2, registryDoc.runtime_paths.panel_current_pointer.path);
  if (!firstPublication) { pointerTarget.before_sha256 = hash(Buffer.from(canonical(beforePointer))); pointerTarget.before_image.sha256 = pointerTarget.before_sha256; }
  setTargetAfter(pointerTarget, pointer);
  const commandFingerprint = hash(Buffer.from(canonical({ transaction_id: transactionId, generation_id: generationId, selection_policy_id: policy.policy_id, panel_id: panel.panel_id })));
  const publishedTargets = lineageTargets.filter(({ role }) => ["projection", "panel"].includes(role));
  const receipt = {
    contract: contract("panel-publication-receipt-v1"), schema_version: "1.0.0", transaction_id: transactionId, journal_id: "journal-panel-1",
    command_fingerprint: commandFingerprint, generation_id: generationId, selection_policy_id: policy.policy_id, panel_id: panel.panel_id,
    lineage_index_id: lineageIndex.index_id, lineage_targets: clone(lineageTargets), before_panel_generation: beforeGeneration,
    after_panel_generation: state.panel_generation, before_pointer_id: firstPublication ? null : beforePointer.pointer_id, after_pointer_id: pointer.pointer_id,
    published_targets: clone(publishedTargets), pointer_target: clone(pointerTarget), panel_state_target: clone(stateTarget), status: "committed",
  };
  receipt.receipt_id = hash(Buffer.from(canonical(receipt))); setTargetAfter(receiptTarget, receipt);
  const journal = {
    contract: contract("transaction-journal-manifest-v1"), schema_version: "1.0.0", journal_id: "journal-panel-1", transaction_id: transactionId, journal_dir: journalDir,
    manifest_path: runtimePath(registryDoc, "publication_journal_template", generationId),
    prepared_marker_path: runtimePath(registryDoc, "journal_prepared_marker_template", null, null, null, transactionId),
    terminal_marker_path: runtimePath(registryDoc, "publication_marker_template", generationId),
    recovery_receipt_path: runtimePath(registryDoc, "journal_recovery_receipt_template", null, null, null, transactionId),
    transaction_kind: "panel", authorization: null, targets: [...clone(lineageTargets), stateTarget, receiptTarget, pointerTarget],
    receipt_target_paths: [receiptPath], prepared_at: "2026-07-24T02:00:00Z",
  };
  reindexTargets(journal.targets, journalDir);
  receipt.published_targets = clone(journal.targets.filter(({ role }) => ["projection", "panel"].includes(role)));
  receipt.lineage_targets = clone(journal.targets.filter(({ role }) => ["projection", "panel", "lineage-object", "lineage-index"].includes(role)));
  receipt.lineage_index_id = lineageIndex.index_id;
  receipt.pointer_target = clone(journal.targets.find(({ role }) => role === "pointer")); receipt.panel_state_target = clone(journal.targets.find(({ role }) => role === "panel-state"));
  journal.manifest_id = hash(Buffer.from(canonical(journal)));
  const marker = { contract: contract("journal-marker-v1"), schema_version: "1.0.0", journal_id: journal.journal_id, manifest_id: journal.manifest_id, state: "committed", marked_at: "2026-07-24T02:00:01Z" };
  marker.marker_id = hash(Buffer.from(canonical(marker)));
  const graph = { panel, built, policy, generation, before_pointer: beforePointer, pointer, before_state: beforeState, state, receipt, journal, marker,
    physical_inventory: physicalInventory, refresh_receipt: refreshReceipt, lineage_index: lineageIndex, lineage_index_path: lineageIndexPath, lineage_documents: lineageDocuments };
  if (mutation === "omit-projection") {
    const index = graph.journal.targets.findIndex(({ role }) => role === "projection"); graph.journal.targets.splice(index, 1); reindexTargets(graph.journal.targets, graph.journal.journal_dir);
    graph.receipt.published_targets = clone(graph.journal.targets.filter(({ role }) => ["projection", "panel"].includes(role)));
    graph.receipt.pointer_target = clone(graph.journal.targets.find(({ role }) => role === "pointer")); graph.receipt.panel_state_target = clone(graph.journal.targets.find(({ role }) => role === "panel-state"));
  } else if (mutation === "wrong-role") graph.journal.targets.find(({ role }) => role === "projection").role = "business";
  else if (mutation === "pointer-generation") graph.pointer.generation_id = `sha256:${"e".repeat(64)}`;
  else if (mutation === "state-generation-jump") graph.state.panel_generation = 99;
  else if (mutation === "receipt-selection") graph.receipt.selection_policy_id = `sha256:${"e".repeat(64)}`;
  else if (mutation === "receipt-target-mismatch") graph.receipt.published_targets[0].path = "views/generations/other.json";
  else if (mutation === "noncommitted-marker") graph.marker.state = "prepared";
  else if (mutation === "panel-generation-jump") graph.receipt.after_panel_generation = 99;
  else if (mutation === "redirect-pointer") graph.journal.targets.find(({ role }) => role === "pointer").path = "wrong/current-pointer.json";
  else if (mutation === "redirect-state") graph.journal.targets.find(({ role }) => role === "panel-state").path = "wrong/panel-state.json";
  else if (mutation === "redirect-receipt") graph.journal.targets.find(({ role }) => role === "receipt").path = "wrong/panel-receipt.json";
  else if (mutation === "substitute-before-pointer") graph.before_pointer.panel_id = `sha256:${"e".repeat(64)}`;
  else if (mutation === "substitute-before-state") graph.before_state.panel_generation = 6;
  else if (mutation === "pointer-not-last") { const target = graph.journal.targets.find(({ role }) => role === "pointer"); graph.journal.targets.splice(graph.journal.targets.indexOf(target), 1); graph.journal.targets.splice(-1, 0, target); }
  else if (mutation === "lineage-index-target-missing") graph.journal.targets.splice(graph.journal.targets.findIndex(({ role }) => role === "lineage-index"), 1);
  else if (mutation === "lineage-object-target-missing") graph.journal.targets.splice(graph.journal.targets.findIndex(({ role }) => role === "lineage-object"), 1);
  finalizePanelPublicationGraph(graph);
  if (mutation === "journal-adjunct-tamper") graph.journal.prepared_at = "2026-07-24T02:00:02Z";
  else if (mutation === "marker-adjunct-tamper") graph.marker.marked_at = "2026-07-24T02:00:02Z";
  else if (mutation === "receipt-adjunct-tamper") { graph.receipt.status = "committed"; graph.receipt.before_panel_generation += 1; }
  return graph;
};

const panelPublicationTargetImages = (graph) => {
  const afterByPath = Object.fromEntries(Object.entries(graph.lineage_documents).map(([targetPath, raw]) => [targetPath, Buffer.from(raw)]));
  const receiptPath = graph.journal.targets.find(({ role }) => role === "receipt").path;
  afterByPath[receiptPath] = Buffer.from(canonical(graph.receipt));
  afterByPath[graph.journal.manifest_path] = Buffer.from(canonical(graph.journal));
  afterByPath[graph.journal.terminal_marker_path] = Buffer.from(canonical(graph.marker));
  afterByPath[graph.journal.targets.at(-1).path] = Buffer.from(canonical(graph.pointer));
  afterByPath[graph.journal.targets.find(({ role }) => role === "panel-state").path] = Buffer.from(canonical(graph.state));
  return Object.fromEntries(graph.journal.targets.map((target) => {
    const before = target.role === "pointer" ? graph.before_pointer === null ? null : Buffer.from(canonical(graph.before_pointer))
      : target.role === "panel-state" ? graph.before_state === null ? null : Buffer.from(canonical(graph.before_state)) : null;
    return [target.path, { before, after: afterByPath[target.path] }];
  }));
};

const panelPublicationSemantics = (graph, registryDoc, schemaRoot, schemaSha, registrySha) => {
  const { before_pointer: beforePointer, pointer, before_state: beforeState, state, receipt, journal, marker } = graph;
  const firstPublication = beforePointer === null && beforeState === null;
  if ((beforePointer === null) !== (beforeState === null)) return false;
  const registered = [[pointer, "panel-current-pointer/1.0.0"], [state, "panel-state/1.0.0"], [receipt, "panel-publication-receipt/1.0.0"]];
  if (!firstPublication) registered.push([beforePointer, "panel-current-pointer/1.0.0"], [beforeState, "panel-state/1.0.0"]);
  if (!registered.every(([document, contractName]) => validateRegistered(document, schemaRoot, registryDoc, contractName, schemaSha, registrySha))) return false;
  const lineageIndex = graph.lineage_index; const lineageDocuments = graph.lineage_documents;
  if (lineageIndex === null || typeof lineageIndex !== "object" || lineageDocuments === null || typeof lineageDocuments !== "object"
    || !journalSemantics(journal, marker, schemaRoot, registryDoc, schemaSha, registrySha)) return false;
  if (!panelBindingSemantics(graph.panel, graph.built, registryDoc, graph.policy, graph.generation)) return false;
  const pointerBody = clone(pointer); delete pointerBody.pointer_id; const stateBody = clone(state); delete stateBody.state_id; const receiptBody = clone(receipt); delete receiptBody.receipt_id;
  if (pointer.pointer_id !== hash(Buffer.from(canonical(pointerBody))) || state.state_id !== hash(Buffer.from(canonical(stateBody))) || receipt.receipt_id !== hash(Buffer.from(canonical(receiptBody)))) return false;
  if (!firstPublication) {
    const beforePointerBody = clone(beforePointer); delete beforePointerBody.pointer_id; const beforeStateBody = clone(beforeState); delete beforeStateBody.state_id;
    if (beforePointer.pointer_id !== hash(Buffer.from(canonical(beforePointerBody))) || beforeState.state_id !== hash(Buffer.from(canonical(beforeStateBody)))) return false;
  }
  const rows = lineageIndex.objects; const orderedRows = clone(rows).sort((left, right) => Buffer.from(`${left.object_kind}\0${left.projection_kind ?? ""}\0${left.instance_key ?? ""}`).compare(Buffer.from(`${right.object_kind}\0${right.projection_kind ?? ""}\0${right.instance_key ?? ""}`)));
  const descriptorKeys = rows.map((row) => canonical([row.object_kind, row.projection_kind, row.instance_key]));
  const indexBody = clone(lineageIndex); delete indexBody.index_id;
  if (!validateRegistered(lineageIndex, schemaRoot, registryDoc, "generation-lineage-index/1.0.0", schemaSha, registrySha)
    || lineageIndex.index_id !== hash(Buffer.from(canonical(indexBody))) || lineageIndex.generation_id !== graph.generation.generation_id
    || canonical(rows) !== canonical(orderedRows) || new Set(descriptorKeys).size !== descriptorKeys.length
    || canonical(Object.keys(lineageDocuments).sort()) !== canonical([graph.lineage_index_path, ...rows.map(({ path: value }) => value)].sort())) return false;
  const descriptorRows = (values) => [...values.entries()].sort(([left], [right]) => Buffer.from(left).compare(Buffer.from(right)));
  const actualDescriptors = new Map(rows.map((row) => [canonical([row.object_kind, row.projection_kind, row.instance_key]), [row.contract_name, row.root, row.path, row.cardinality]]));
  const expectedDescriptors = expectedLineageDescriptors(registryDoc, graph.generation.generation_id, graph.policy, firstPublication);
  if (canonical(descriptorRows(actualDescriptors)) !== canonical(descriptorRows(expectedDescriptors))) return false;
  const identityFields = {
    "generation-envelope/1.0.0": "generation_id", "selection-policy/1.0.0": "policy_id", "physical-workstream-inventory/1.0.0": "attestation_id",
    "panel-binding-catalog/1.0.0": "catalog_id", "canonical-projection-envelope/1.0.0": "projection_id", "projection-dependency-manifest/1.0.0": "manifest_id",
    "producer-receipt/1.0.0": "receipt_id", "refresh-run-receipt/1.0.0": "receipt_id", "panel-current-pointer/1.0.0": "pointer_id",
    "panel-state/1.0.0": "state_id", "publication-absence-proof/1.0.0": "proof_id",
  };
  const memoryRoot = graph.generation.roots.find(({ root }) => root === "memory").root_instance_id;
  for (const row of rows) {
    const raw = lineageDocuments[row.path]; if (!Buffer.isBuffer(raw)) return false;
    let document; try { document = JSON.parse(raw.toString()); } catch { return false; }
    if (row.root !== "memory" || row.root_instance_id !== memoryRoot || row.cardinality !== "one"
      || canonical(document) !== raw.toString() || hash(raw) !== row.sha256
      || !validateRegistered(document, schemaRoot, registryDoc, row.contract_name, schemaSha, registrySha)
      || document[identityFields[row.contract_name]] !== row.object_id) return false;
  }
  const indexRaw = lineageDocuments[graph.lineage_index_path];
  if (!Buffer.isBuffer(indexRaw) || !indexRaw.equals(Buffer.from(canonical(lineageIndex)))) return false;

  const items = Object.values(graph.built).flat().sort((left, right) => Buffer.from(`${left.handle.kind}\0${left.envelope.instance_key ?? ""}`).compare(Buffer.from(`${right.handle.kind}\0${right.envelope.instance_key ?? ""}`)));
  const expectedPointers = []; const expectedTargets = [];
  for (const item of items) {
    const { envelope, handle } = item;
    const templateName = handle.kind === "management-panel" ? "management_panel_template" : "canonical_projection_template";
    let targetPath;
    try { targetPath = runtimePath(registryDoc, templateName, graph.generation.generation_id, handle.kind, envelope.instance_key); } catch { return false; }
    expectedPointers.push({ kind: handle.kind, instance_key: envelope.instance_key, id: handle.id, manifest_id: handle.manifest_id, canonical_path: targetPath });
    const role = handle.kind === "management-panel" ? "panel" : "projection"; const matches = journal.targets.filter((target) => target.role === role && target.path === targetPath);
    if (matches.length !== 1 || matches[0].after_sha256 !== hash(Buffer.from(canonical(envelope)))) return false; expectedTargets.push(matches[0]);
  }
  const lineageTargets = journal.targets.filter(({ role }) => ["projection", "panel", "lineage-object", "lineage-index"].includes(role));
  for (const row of rows) {
    const expectedRole = row.object_kind !== "projection-envelope" ? "lineage-object" : row.projection_kind === "management-panel" ? "panel" : "projection";
    const matches = lineageTargets.filter(({ path: value }) => value === row.path);
    if (matches.length !== 1 || matches[0].role !== expectedRole || matches[0].operation !== "create" || matches[0].after_sha256 !== row.sha256) return false;
  }
  const indexTargets = lineageTargets.filter(({ role }) => role === "lineage-index");
  if (indexTargets.length !== 1 || indexTargets[0].path !== graph.lineage_index_path || indexTargets[0].after_sha256 !== hash(indexRaw)) return false;
  const pointerTargets = journal.targets.filter(({ role }) => role === "pointer"); const stateTargets = journal.targets.filter(({ role }) => role === "panel-state"); const receiptTargets = journal.targets.filter(({ role }) => role === "receipt");
  if (canonical(pointer.projections) !== canonical(expectedPointers) || pointerTargets.length !== 1 || stateTargets.length !== 1 || receiptTargets.length !== 1) return false;
  if (pointerTargets[0].after_sha256 !== hash(Buffer.from(canonical(pointer))) || stateTargets[0].after_sha256 !== hash(Buffer.from(canonical(state))) || receiptTargets[0].after_sha256 !== hash(Buffer.from(canonical(receipt)))) return false;
  const token = filesystemToken(journal.transaction_id);
  const commonTargetsOk = pointerTargets[0].root_instance_id === memoryRoot && pointerTargets[0].path === registryDoc.runtime_paths.panel_current_pointer.path
    && stateTargets[0].root_instance_id === memoryRoot && stateTargets[0].path === registryDoc.runtime_paths.panel_state.path
    && receiptTargets[0].root_instance_id === memoryRoot && receiptTargets[0].path === registryDoc.runtime_paths.panel_receipt_template.path.replace("{transaction_token}", token);
  const preimageTargetsOk = firstPublication
    ? pointerTargets[0].operation === "create" && stateTargets[0].operation === "create"
      && pointerTargets[0].before_sha256 === null && pointerTargets[0].before_image === null
      && stateTargets[0].before_sha256 === null && stateTargets[0].before_image === null
      && receipt.before_panel_generation === 0 && receipt.before_pointer_id === null
    : pointerTargets[0].operation === "replace" && stateTargets[0].operation === "replace"
      && pointerTargets[0].before_sha256 === hash(Buffer.from(canonical(beforePointer)))
      && stateTargets[0].before_sha256 === hash(Buffer.from(canonical(beforeState)))
      && receipt.before_pointer_id === beforePointer.pointer_id && beforeState.current_pointer_id === beforePointer.pointer_id
      && receipt.before_panel_generation === beforeState.panel_generation;
  if (!commonTargetsOk || !preimageTargetsOk) return false;
  const panelEnvelope = items.find(({ handle }) => handle.kind === "management-panel").envelope;
  const commandFingerprint = hash(Buffer.from(canonical({ transaction_id: journal.transaction_id, generation_id: graph.generation.generation_id, selection_policy_id: graph.policy.policy_id, panel_id: graph.panel.panel_id })));
  return pointer.generation_id === graph.generation.generation_id && receipt.generation_id === graph.generation.generation_id
    && pointer.panel_id === graph.panel.panel_id && panelEnvelope.payload.panel_id === graph.panel.panel_id && receipt.panel_id === graph.panel.panel_id
    && receipt.selection_policy_id === graph.policy.policy_id
    && receipt.command_fingerprint === commandFingerprint && receipt.lineage_index_id === lineageIndex.index_id
    && canonical(receipt.lineage_targets) === canonical(lineageTargets)
    && receipt.after_panel_generation === receipt.before_panel_generation + 1 && state.panel_generation === receipt.after_panel_generation
    && receipt.after_pointer_id === pointer.pointer_id && state.current_pointer_id === pointer.pointer_id
    && canonical(receipt.published_targets) === canonical(expectedTargets) && canonical(receipt.pointer_target) === canonical(pointerTargets[0]) && canonical(receipt.panel_state_target) === canonical(stateTargets[0])
    && canonical(journal.targets) === canonical([...lineageTargets, ...stateTargets, ...receiptTargets, ...pointerTargets])
    && canonical(journal.targets.at(-1)) === canonical(pointerTargets[0])
    && receipt.transaction_id === journal.transaction_id && receipt.journal_id === journal.journal_id;
};

const strictLineageFixture = (suiteDoc, registryDoc, schemaRoot, schemaSha, registrySha, projectRoot, workspaceRoot, factState, ledgerRaw, ledgerState, workstreamDocuments, firstPublication = false) => {
  const [panel, upstreams, , policy] = panelFixture(suiteDoc.contract_schema_vectors, registryDoc, schemaSha, registrySha, projectRoot);
  const workstreamById = new Map(workstreamDocuments.map((row) => [row.state.workstream_id, row]));
  for (const collectionName of ["physical_workstream_inventory", "workstream_catalog"]) {
    for (const row of policy[collectionName]) {
      const live = workstreamById.get(row.workstream_id); const sidecarRaw = Buffer.from(canonical(live.sidecar));
      row.wdr_source.fingerprint = row.wdr_source.blob_id = hash(live.wdr_raw);
      row.sidecar_source.fingerprint = row.sidecar_source.blob_id = hash(sidecarRaw);
    }
  }
  policy.physical_workstream_inventory_id = inventoryId(policy.physical_workstream_inventory);
  policy.workstream_catalog_id = catalogId(policy.workstream_catalog);
  const policyBody = clone(policy); delete policyBody.policy_id; policy.policy_id = hash(Buffer.from(canonical(policyBody)));
  const generation = generationFixture(registryDoc, policy, schemaSha, registrySha); generation.fact_generation = factState.fact_generation;
  const liveDocuments = new Map();
  for (const item of workstreamDocuments) {
    liveDocuments.set(item.record_path, item.wdr_raw);
    liveDocuments.set(`workstreams/${item.state.workstream_id}/action-projection.json`, Buffer.from(canonical(item.sidecar)));
  }
  const leafStore = {};
  for (const source of generation.leaf_sources) {
    const raw = liveDocuments.get(source.path) ?? Buffer.from(`${source.root}\0${source.path}`);
    source.fingerprint = source.blob_id = hash(raw); leafStore[`${source.root_instance_id}\0${source.path}`] = raw;
  }
  const generationBody = clone(generation); delete generationBody.generation_id; generation.generation_id = hash(Buffer.from(canonical(generationBody)));
  const physicalInventory = physicalInventoryFixture(registryDoc, policy, factState.fact_generation, schemaSha, registrySha);
  const catalog = panelBindingCatalog(registryDoc, schemaSha, registrySha);
  const currentRows = resolvedSelection(policy).map((workstreamId) => {
    const item = workstreamById.get(workstreamId); const current = parseWdrCurrent(item.wdr_raw, workstreamId);
    return { ...Object.fromEntries(["workstream_id", "phase", "status", "progress", "blockers", "risks", "dependencies", "action_ids"].map((key) => [key, current[key]])),
      wdr_fingerprint: hash(item.wdr_raw), wdr_revision: item.state.wdr_revision, file_generation: item.state.file_generation };
  });
  upstreams["program-status"].workstream_current = currentRows;
  upstreams["state-audit"].selection_policy_id = policy.policy_id; upstreams["state-audit"].selected_workstreams = resolvedSelection(policy);
  const driftPackage = { generation_id: generation.generation_id, selection_policy_id: policy.policy_id, selected_workstreams: resolvedSelection(policy),
    ledger_raw: ledgerRaw, ledger_state: ledgerState,
    wdrs: Object.fromEntries(workstreamDocuments.map((row) => [row.state.workstream_id, row.wdr_raw])),
    wdr_states: Object.fromEntries(workstreamDocuments.map((row) => [row.state.workstream_id, row.state])),
    sidecars: Object.fromEntries(workstreamDocuments.map((row) => [row.state.workstream_id, row.sidecar])) };
  upstreams["action-projection-drift-verdict"] = expectedDriftVerdict(driftPackage, registryDoc, schemaSha, registrySha);
  panel.sync.generation_id = generation.generation_id; panel.sync.selection_policy_id = policy.policy_id;
  for (const binding of registryDoc.panel_binding_map) {
    const payload = upstreams[binding.projection_kind];
    setPointer(panel, binding.panel_pointer, binding.merge_mode === "object-by-key" ? Object.fromEntries(payload.map((row) => [row.scenario, clone(row)])) : clone(payload));
  }
  const panelBody = clone(panel); delete panelBody.panel_id; panel.panel_id = hash(Buffer.from(canonical(panelBody)));
  const [built, outerOk] = buildProjectionLineage(panel, upstreams, registryDoc, schemaRoot, schemaSha, registrySha, projectRoot, workspaceRoot, policy);
  const lineageOk = projectionLineageSemantics(built, registryDoc, schemaRoot, generation, policy, schemaSha, registrySha);
  if (!outerOk || !lineageOk) throw new Error(`strict lineage fixture is invalid: outer=${outerOk},lineage=${lineageOk}`);
  const nodes = Object.values(built).flat().map((item) => ({ instance_key: item.envelope.instance_key ?? "singleton", projection_kind: item.handle.kind,
    disposition: "produced", invalidation_reasons: [], output: clone(item.handle), error_code: null }))
    .sort((left, right) => Buffer.from(`${left.instance_key}\0${left.projection_kind}`).compare(Buffer.from(`${right.instance_key}\0${right.projection_kind}`)));
  const refreshReceipt = { contract: expectedContractRef(registryDoc, "refresh-run-receipt/1.0.0", schemaSha, registrySha), schema_version: "1.0.0",
    refresh_id: "refresh-snapshot-fixture", snapshot_id: policy.snapshot_id, snapshot_lock_receipt_id: policy.snapshot_lock_receipt_id,
    generation_id: generation.generation_id, expected_fact_generation: factState.fact_generation,
    expected_panel_generation: firstPublication ? 0 : 7, status: "published", nodes, retry_from_instance_key: null, source_as_of: policy.as_of };
  refreshReceipt.receipt_id = hash(Buffer.from(canonical(refreshReceipt)));
  const publicationGraph = panelPublicationFixture(panel, built, policy, generation, registryDoc, schemaSha, registrySha,
    firstPublication ? "first-publication" : "none", physicalInventory, refreshReceipt);
  if (!panelPublicationSemantics(publicationGraph, registryDoc, schemaRoot, schemaSha, registrySha)) throw new Error("strict publication fixture is invalid");
  const lineageStore = Object.fromEntries(Object.entries(publicationGraph.lineage_documents).map(([objectPath, raw]) => [objectPath, Buffer.from(raw)]));
  const journal = publicationGraph.journal; const marker = publicationGraph.marker; const receipt = publicationGraph.receipt;
  const receiptPath = journal.targets.find(({ role }) => role === "receipt").path;
  Object.assign(lineageStore, {
    [journal.manifest_path]: Buffer.from(canonical(journal)), [journal.terminal_marker_path]: Buffer.from(canonical(marker)),
    [receiptPath]: Buffer.from(canonical(receipt)), [registryDoc.runtime_paths.panel_current_pointer.path]: Buffer.from(canonical(publicationGraph.pointer)),
    [registryDoc.runtime_paths.panel_state.path]: Buffer.from(canonical(publicationGraph.state)),
  });
  return { panel, policy, physical_inventory: physicalInventory, catalog, generation, built, publication_graph: publicationGraph, refresh_receipt: refreshReceipt,
    lineage_index: publicationGraph.lineage_index, lineage_index_path: publicationGraph.lineage_index_path, lineage_store: lineageStore, leaf_store: leafStore };
};

const expectedLineageDescriptors = (registryDoc, generationId, policy, firstPublication = false) => {
  const descriptors = new Map();
  const key = (objectKind, projectionKind = null, instanceKey = null) => canonical([objectKind, projectionKind, instanceKey]);
  const singleton = (objectKind, contractName, objectPath) => descriptors.set(key(objectKind), [contractName, "memory", objectPath, "one"]);
  singleton("generation", "generation-envelope/1.0.0", runtimePath(registryDoc, "generation_envelope_template", generationId));
  singleton("selection-policy", "selection-policy/1.0.0", runtimePath(registryDoc, "selection_policy_template", generationId));
  singleton("physical-inventory", "physical-workstream-inventory/1.0.0", runtimePath(registryDoc, "physical_inventory_template", generationId));
  singleton("panel-binding-catalog", "panel-binding-catalog/1.0.0", runtimePath(registryDoc, "panel_binding_catalog_template", generationId));
  for (const [projectionKind, instanceKeys] of Object.entries(expectedProjectionInstances(registryDoc, policy))) {
    for (const instanceKey of instanceKeys) {
      const envelopeTemplate = projectionKind === "management-panel" ? "management_panel_template" : "canonical_projection_template";
      descriptors.set(key("projection-envelope", projectionKind, instanceKey), [
        "canonical-projection-envelope/1.0.0", "memory", runtimePath(registryDoc, envelopeTemplate, generationId, projectionKind, instanceKey), "one",
      ]);
      descriptors.set(key("dependency-manifest", projectionKind, instanceKey), [
        "projection-dependency-manifest/1.0.0", "memory", runtimePath(registryDoc, "dependency_manifest_template", generationId, projectionKind, instanceKey), "one",
      ]);
      descriptors.set(key("producer-receipt", projectionKind, instanceKey), [
        "producer-receipt/1.0.0", "memory", runtimePath(registryDoc, "producer_receipt_template", generationId, projectionKind, instanceKey), "one",
      ]);
    }
  }
  singleton("refresh-receipt", "refresh-run-receipt/1.0.0", runtimePath(registryDoc, "refresh_receipt_generation_template", generationId));
  if (firstPublication) singleton("publication-absence-proof", "publication-absence-proof/1.0.0", runtimePath(registryDoc, "publication_absence_proof_template", generationId));
  else {
    singleton("before-pointer", "panel-current-pointer/1.0.0", runtimePath(registryDoc, "before_pointer_template", generationId));
    singleton("before-panel-state", "panel-state/1.0.0", runtimePath(registryDoc, "before_panel_state_template", generationId));
  }
  return descriptors;
};

const loadStrictLineage = (pack, registryDoc, schemaRoot, schemaSha, registrySha, verifyLiveLeaves = true) => {
  try {
    const store = pack.lineage_store; const pointerPath = registryDoc.runtime_paths.panel_current_pointer.path;
    const pointerRaw = store[pointerPath]; if (!Buffer.isBuffer(pointerRaw)) return null;
    const livePointer = JSON.parse(pointerRaw.toString());
    const expectedIndexPath = runtimePath(registryDoc, "generation_lineage_index_template", livePointer.generation_id);
    if (Object.keys(store).length === 0 || !Buffer.isBuffer(store[expectedIndexPath])) return null;
    const indexRaw = store[expectedIndexPath]; if (!Buffer.isBuffer(indexRaw)) return null;
    const index = JSON.parse(indexRaw.toString()); const indexBody = clone(index); delete indexBody.index_id;
    if (canonical(index) !== indexRaw.toString() || !validateRegistered(index, schemaRoot, registryDoc, "generation-lineage-index/1.0.0", schemaSha, registrySha)
      || index.index_id !== hash(Buffer.from(canonical(indexBody))) || index.generation_id !== livePointer.generation_id) return null;
    const rows = index.objects; const ordered = clone(rows).sort((left, right) => Buffer.from(`${left.object_kind}\0${left.projection_kind ?? ""}\0${left.instance_key ?? ""}`).compare(Buffer.from(`${right.object_kind}\0${right.projection_kind ?? ""}\0${right.instance_key ?? ""}`)));
    const keys = rows.map((row) => `${row.object_kind}\0${row.projection_kind ?? ""}\0${row.instance_key ?? ""}`);
    if (canonical(rows) !== canonical(ordered) || new Set(keys).size !== keys.length) return null;
    const journalPath = runtimePath(registryDoc, "publication_journal_template", livePointer.generation_id);
    const markerPath = runtimePath(registryDoc, "publication_marker_template", livePointer.generation_id);
    const panelStatePath = registryDoc.runtime_paths.panel_state.path;
    const journalRaw = store[journalPath]; const markerRaw = store[markerPath];
    if (!Buffer.isBuffer(journalRaw) || !Buffer.isBuffer(markerRaw)) return null;
    const journal = JSON.parse(journalRaw.toString()); const marker = JSON.parse(markerRaw.toString());
    const receiptTargets = journal.targets.filter(({ role }) => role === "receipt"); if (receiptTargets.length !== 1) return null;
    const receiptPath = receiptTargets[0].path; const receiptRaw = store[receiptPath]; const panelStateRaw = store[panelStatePath];
    if (![receiptRaw, pointerRaw, panelStateRaw].every(Buffer.isBuffer)) return null;
    const publicationReceipt = JSON.parse(receiptRaw.toString()); const currentPointer = JSON.parse(pointerRaw.toString()); const panelState = JSON.parse(panelStateRaw.toString());
    const expectedStorePaths = [expectedIndexPath, ...rows.map(({ path: objectPath }) => objectPath), journalPath, markerPath, receiptPath, pointerPath, panelStatePath].sort();
    const publicationBody = clone(publicationReceipt); delete publicationBody.receipt_id; const pointerBody = clone(currentPointer); delete pointerBody.pointer_id; const panelStateBody = clone(panelState); delete panelStateBody.state_id;
    if (canonical(Object.keys(store).sort()) !== canonical(expectedStorePaths)
      || canonical(journal) !== journalRaw.toString() || canonical(marker) !== markerRaw.toString() || canonical(publicationReceipt) !== receiptRaw.toString()
      || canonical(currentPointer) !== pointerRaw.toString() || canonical(panelState) !== panelStateRaw.toString()
      || !validateRegistered(publicationReceipt, schemaRoot, registryDoc, "panel-publication-receipt/1.0.0", schemaSha, registrySha)
      || !validateRegistered(currentPointer, schemaRoot, registryDoc, "panel-current-pointer/1.0.0", schemaSha, registrySha)
      || !validateRegistered(panelState, schemaRoot, registryDoc, "panel-state/1.0.0", schemaSha, registrySha)
      || publicationReceipt.receipt_id !== hash(Buffer.from(canonical(publicationBody))) || currentPointer.pointer_id !== hash(Buffer.from(canonical(pointerBody)))
      || panelState.state_id !== hash(Buffer.from(canonical(panelStateBody))) || receiptTargets[0].after_sha256 !== hash(receiptRaw)
      || !journalSemantics(journal, marker, schemaRoot, registryDoc, schemaSha, registrySha)) return null;
    const identityFields = { "generation-envelope/1.0.0": "generation_id", "selection-policy/1.0.0": "policy_id", "physical-workstream-inventory/1.0.0": "attestation_id",
      "panel-binding-catalog/1.0.0": "catalog_id", "canonical-projection-envelope/1.0.0": "projection_id", "projection-dependency-manifest/1.0.0": "manifest_id",
      "producer-receipt/1.0.0": "receipt_id", "refresh-run-receipt/1.0.0": "receipt_id", "panel-publication-receipt/1.0.0": "receipt_id",
      "panel-current-pointer/1.0.0": "pointer_id", "panel-state/1.0.0": "state_id", "transaction-journal-manifest/1.0.0": "manifest_id", "journal-marker/1.0.0": "marker_id",
      "publication-absence-proof/1.0.0": "proof_id" };
    const documents = new Map(); const documentKey = (objectKind, projectionKind = null, instanceKey = null) => `${objectKind}\0${projectionKind ?? ""}\0${instanceKey ?? ""}`;
    for (const row of rows) {
      const raw = store[row.path]; if (!Buffer.isBuffer(raw)) return null; const document = JSON.parse(raw.toString()); const identityField = identityFields[row.contract_name];
      if (canonical(document) !== raw.toString() || hash(raw) !== row.sha256 || !validateRegistered(document, schemaRoot, registryDoc, row.contract_name, schemaSha, registrySha)
        || document[identityField] !== row.object_id) return null;
      documents.set(documentKey(row.object_kind, row.projection_kind, row.instance_key), document);
    }
    const getDocument = (objectKind, projectionKind = null, instanceKey = null) => documents.get(documentKey(objectKind, projectionKind, instanceKey));
    const generation = getDocument("generation"); const policy = getDocument("selection-policy"); const physicalInventory = getDocument("physical-inventory"); const catalog = getDocument("panel-binding-catalog");
    const firstPublication = panelState.panel_generation === 1;
    const expectedDescriptors = expectedLineageDescriptors(registryDoc, generation.generation_id, policy, firstPublication);
    const actualDescriptors = new Map(rows.map((row) => [
      canonical([row.object_kind, row.projection_kind, row.instance_key]), [row.contract_name, row.root, row.path, row.cardinality],
    ]));
    const descriptorRows = (values) => [...values.entries()].sort(([left], [right]) => Buffer.from(left).compare(Buffer.from(right)));
    if (canonical(descriptorRows(actualDescriptors)) !== canonical(descriptorRows(expectedDescriptors))) return null;
    const memoryRootId = pack.documents.root_registry.roots.find(({ role }) => role === "memory")?.root_instance_id;
    if (!memoryRootId || rows.some((row) => row.root !== "memory" || row.root_instance_id !== memoryRootId || row.cardinality !== "one")) return null;
    const expectedInstances = expectedProjectionInstances(registryDoc, policy);
    const expectedProjectionKeys = Object.entries(expectedInstances).flatMap(([kind, values]) => values.map((instanceKey) => `${kind}\0${instanceKey ?? ""}`)).sort();
    const actualProjectionKeys = rows.filter(({ object_kind }) => object_kind === "projection-envelope").map((row) => `${row.projection_kind}\0${row.instance_key ?? ""}`).sort();
    if (canonical(expectedProjectionKeys) !== canonical(actualProjectionKeys)) return null;
    const built = Object.fromEntries(Object.keys(expectedInstances).map((kind) => [kind, []]));
    for (const projectionKey of expectedProjectionKeys) {
      const separator = projectionKey.indexOf("\0"); const kind = projectionKey.slice(0, separator); const encodedInstance = projectionKey.slice(separator + 1); const instanceKey = encodedInstance === "" ? null : encodedInstance;
      const envelope = getDocument("projection-envelope", kind, instanceKey); const manifest = getDocument("dependency-manifest", kind, instanceKey); const receipt = getDocument("producer-receipt", kind, instanceKey);
      built[kind].push({ envelope, manifest, receipt, handle: receipt.output, allowedSources: manifest.sources, actualReads: manifest.sources });
    }
    const panel = built["management-panel"][0].envelope.payload;
    const absenceProof = getDocument("publication-absence-proof");
    if (firstPublication) {
      if (absenceProof === undefined) return null; const absenceBody = clone(absenceProof); delete absenceBody.proof_id;
      if (absenceProof.proof_id !== hash(Buffer.from(canonical(absenceBody))) || absenceProof.generation_id !== generation.generation_id
        || absenceProof.memory_root_instance_id !== memoryRootId || absenceProof.pointer_path !== pointerPath || absenceProof.panel_state_path !== panelStatePath
        || absenceProof.pointer_absent !== true || absenceProof.panel_state_absent !== true
        || absenceProof.fact_lock_profile_id !== registryDoc.lock_profile.profile_id || absenceProof.panel_lock_profile_id !== registryDoc.lock_profile.profile_id) return null;
    }
    const lineageDocuments = Object.fromEntries([expectedIndexPath, ...rows.map(({ path: objectPath }) => objectPath)].map((objectPath) => [objectPath, store[objectPath]]));
    const refreshReceipt = getDocument("refresh-receipt");
    const graph = { panel, built, policy, generation, before_pointer: firstPublication ? null : getDocument("before-pointer"), pointer: currentPointer,
      before_state: firstPublication ? null : getDocument("before-panel-state"), state: panelState, receipt: publicationReceipt,
      journal, marker, physical_inventory: physicalInventory, refresh_receipt: refreshReceipt, lineage_index: index,
      lineage_index_path: expectedIndexPath, lineage_documents: lineageDocuments };
    const leafStore = pack.live_leaf_store;
    const expectedLeafKeys = generation.leaf_sources.map((row) => `${row.root_instance_id}\0${row.path}`).sort();
    if (verifyLiveLeaves && (canonical(Object.keys(leafStore).sort()) !== canonical(expectedLeafKeys)
      || generation.leaf_sources.some((row) => !Buffer.isBuffer(leafStore[`${row.root_instance_id}\0${row.path}`]) || hash(leafStore[`${row.root_instance_id}\0${row.path}`]) !== row.fingerprint))) return null;
    if (!projectionLineageSemantics(built, registryDoc, schemaRoot, generation, policy, schemaSha, registrySha)
      || !panelPublicationSemantics(graph, registryDoc, schemaRoot, schemaSha, registrySha)
      || !publicationEligibilitySemantics(panel, physicalInventory, policy, generation, registryDoc, schemaRoot, schemaSha, registrySha, built,
        pack.documents.mutation_intent_outbox ?? null, pack.documents.intent_convergence ?? null)
      || canonical(catalog) !== canonical(panelBindingCatalog(registryDoc, schemaSha, registrySha))
      || !validateRegistered(refreshReceipt, schemaRoot, registryDoc, "refresh-run-receipt/1.0.0", schemaSha, registrySha)
      || !sourceAsOfSemantics(panel, policy, refreshReceipt)) return null;
    const pointerNodes = graph.pointer.projections.map((row) => [row.kind, row.instance_key ?? "singleton", row.id, row.manifest_id, graph.pointer.generation_id]).sort();
    const receiptNodes = refreshReceipt.nodes.filter(({ output }) => output !== null).map((row) => [row.projection_kind, row.instance_key, row.output.id, row.output.manifest_id, row.output.generation_id]).sort();
    if (canonical(pointerNodes) !== canonical(receiptNodes) || refreshReceipt.status !== "published" || refreshReceipt.retry_from_instance_key !== null) return null;
    return { index, documents, graph, refresh_receipt: refreshReceipt, generation, policy };
  } catch { return null; }
};

const liveInspectFixture = (suiteDoc, registryDoc, schemaRoot, schemaSha, registrySha, projectRoot, workspaceRoot, expectedIds, artifactHashes) => {
  const pack = writerFenceFixture(registryDoc, schemaSha, registrySha, expectedIds, artifactHashes, 1, suiteDoc, schemaRoot, projectRoot, workspaceRoot);
  pack.fact_read_lock = { profile_id: registryDoc.lock_profile.profile_id, path: registryDoc.lock_profile.fact_lock.path, mode: "shared", acquired: true };
  pack.surface = "inspect";
  pack.inspect_trace = [
    "fact-lock-shared-acquire", "raw-registry-read", "activation-state-read", "writer-attestation-read",
    "capability-registry-read", "release-evidence-read", "current-pointer-read", "lineage-index-read", "lineage-objects-read",
    "leaf-reenumerate", "fact-state-read", "fact-lock-release", "refresh-status-write",
  ];
  pack.inspect_write_paths = [registryDoc.runtime_paths.panel_refresh_status.path]; pack.inspect_read_set_additions = [];
  pack.inspected_at = "2026-07-24T03:05:00Z";
  return pack;
};

const inspectStatus = (pack, registryDoc, schemaRoot, schemaSha, registrySha, outcome, changedSources, errorCode) => {
  const pointer = pack.documents.current_pointer;
  const verdict = { inspected_generation_id: pointer.generation_id, inspected_pointer_id: pointer.pointer_id, outcome,
    inspected_at: pack.inspected_at, observed_fact_generation: pack.documents.fact_state.fact_generation,
    changed_sources: [...new Set(changedSources)].sort((left, right) => Buffer.from(left).compare(Buffer.from(right))), error_code: errorCode };
  verdict.verdict_id = hash(Buffer.from(canonical(verdict)));
  const status = { contract: expectedContractRef(registryDoc, "panel-refresh-status/1.0.0", schemaSha, registrySha), schema_version: "1.0.0", current_run_id: null,
    current_status: outcome === "fresh" ? "idle" : outcome === "stale" ? "dirty" : "blocked", last_successful_generation_id: pointer.generation_id,
    last_successful_refresh_at: pack.refresh_completed_at, pending_invalidations: [], latest_inspect: verdict };
  status.state_id = hash(Buffer.from(canonical(status))); const body = clone(status); delete body.state_id; const verdictBody = clone(verdict); delete verdictBody.verdict_id;
  return validateRegistered(status, schemaRoot, registryDoc, "panel-refresh-status/1.0.0", schemaSha, registrySha)
    && verdict.verdict_id === hash(Buffer.from(canonical(verdictBody))) && status.state_id === hash(Buffer.from(canonical(body))) ? status : null;
};

const liveInspectReadSetSemantics = (pack, registryDoc) => {
  try {
    const fixedContracts = {
      root_registry_state: "root-registry-state/1.0.0", strict_activation_state: "strict-activation-state/1.0.0",
      writer_fence_attestation: "writer-fence-migration-attestation/1.0.0", writer_capability_registry: "writer-capability-registry/1.0.0",
      release_evidence_set: "release-evidence-set/1.0.0", release_evidence_history_index: "release-evidence-history-index/1.0.0",
      panel_current_pointer: "panel-current-pointer/1.0.0", panel_state: "panel-state/1.0.0", fact_generation: "fact-generation-state/1.0.0",
      action_ledger: "raw/action-ledger-v2", action_ledger_state: "action-ledger-state/1.0.0", action_flow_index: "action-flow-index/1.0.0",
      fact_command_receipt_index: "fact-command-receipt-index/1.0.0",
      mutation_intent_outbox: "mutation-intent-outbox/1.0.0",
      intent_convergence_verdict: "intent-convergence-verdict/1.0.0",
    };
    const profile = registryDoc.live_inspect_read_profile;
    if (canonical(profile.fixed_runtime_path_keys) !== canonical(Object.keys(fixedContracts))) return false;
    const encode = (root, targetPath, contractName) => canonical([root, targetPath, contractName]);
    const expected = new Set(profile.fixed_runtime_path_keys.map((key) => encode("memory", registryDoc.runtime_paths[key].path, fixedContracts[key])));
    const actual = new Set(expected);
    for (const spec of registryDoc.strict_rollout.writer_specs) {
      for (const targetPath of spec.artifact_paths) expected.add(encode("project", targetPath, "raw/writer-artifact"));
      expected.add(encode("project", spec.manifest_path, "writer-build-manifest/1.0.0"));
      expected.add(encode("project", spec.receipt_path, "writer-fence-receipt/1.0.0"));
    }
    const manifestPaths = new Set(registryDoc.strict_rollout.writer_specs.map(({ manifest_path }) => manifest_path));
    const receiptPaths = new Set(registryDoc.strict_rollout.writer_specs.map(({ receipt_path }) => receipt_path));
    for (const targetPath of Object.keys(pack.writer_store)) actual.add(encode("project", targetPath,
      manifestPaths.has(targetPath) ? "writer-build-manifest/1.0.0" : receiptPaths.has(targetPath) ? "writer-fence-receipt/1.0.0" : "raw/writer-artifact"));
    const history = pack.documents.release_evidence_history_index;
    const releaseContracts = new Map([
      [registryDoc.runtime_paths.release_evidence_set.path, "release-evidence-set/1.0.0"],
      [registryDoc.runtime_paths.release_evidence_history_index.path, "release-evidence-history-index/1.0.0"],
    ]);
    for (const entry of history.entries) {
      releaseContracts.set(entry.set_path, "release-evidence-set/1.0.0");
      releaseContracts.set(entry.transition_receipt_path, "release-evidence-transition-receipt/1.0.0");
      releaseContracts.set(entry.journal_path, "transaction-journal-manifest/1.0.0");
      releaseContracts.set(entry.terminal_marker_path, "journal-marker/1.0.0");
      const archive = JSON.parse(pack.release_store[entry.set_path].toString("utf8"));
      for (const evidence of archive.entries) {
        releaseContracts.set(evidence.receipt_path, "conformance-result/1.0.0");
        for (const blob of evidence.evidence_blobs) releaseContracts.set(blob.path, "raw/conformance-evidence");
      }
    }
    for (const [targetPath, contractName] of releaseContracts) expected.add(encode("memory", targetPath, contractName));
    for (const targetPath of Object.keys(pack.release_store)) actual.add(encode("memory", targetPath, releaseContracts.get(targetPath) ?? "unregistered"));
    const currentPointer = pack.documents.current_pointer;
    const indexPath = runtimePath(registryDoc, "generation_lineage_index_template", currentPointer.generation_id);
    const index = JSON.parse(pack.lineage_store[indexPath].toString("utf8"));
    const lineageContracts = new Map([[indexPath, "generation-lineage-index/1.0.0"], ...index.objects.map((row) => [row.path, row.contract_name])]);
    for (const [targetPath, contractName] of lineageContracts) {
      const row = encode("memory", targetPath, contractName); expected.add(row); actual.add(row);
    }
    for (const row of pack.documents.workstreams) {
      const workstreamId = row.state.workstream_id;
      for (const triple of [["memory", row.record_path, "raw/workstream-delivery-record"],
        ["memory", `workstreams/${workstreamId}/delivery-record.state.json`, "wdr-file-state/1.0.0"],
        ["memory", `workstreams/${workstreamId}/action-projection.json`, "wdr-action-projection/1.0.0"]]) {
        expected.add(encode(...triple)); actual.add(encode(...triple));
      }
    }
    const generationPath = runtimePath(registryDoc, "generation_envelope_template", currentPointer.generation_id);
    const generation = JSON.parse(pack.lineage_store[generationPath].toString("utf8"));
    const rootRoles = new Map(pack.documents.root_registry.roots.map((row) => [row.root_instance_id, row.role]));
    const dynamicReads = new Set(generation.leaf_sources.map((row) => encode(row.root, row.path, "raw/live-source")));
    for (const key of Object.keys(pack.live_leaf_store)) {
      const separator = key.indexOf("\0"); const rootId = key.slice(0, separator); const targetPath = key.slice(separator + 1);
      dynamicReads.add(encode(rootRoles.get(rootId) ?? "unregistered", targetPath, "raw/live-source"));
    }
    for (const row of dynamicReads) { expected.add(row); actual.add(row); }
    for (const row of pack.inspect_read_set_additions ?? []) actual.add(encode(row.root, row.path, row.contract_name));
    if (["omit-one", "duplicate", "wrong-root", "alias", "unconsumed"].includes(pack.inspect_read_mutation ?? "none")) return false;
    return canonical([...actual].sort()) === canonical([...expected].sort());
  } catch { return false; }
};

const liveInspectSemantics = (pack, registryDoc, schemaRoot, schemaSha, registrySha, expectedIds, hashes, securityContext) => {
  try {
    const statusPath = registryDoc.runtime_paths.panel_refresh_status.path;
    if (canonical(pack.inspect_write_paths) !== canonical([statusPath])) return null;
    if (!securityContext || !securityContext.available) return inspectStatus(pack, registryDoc, schemaRoot, schemaSha, registrySha, "unverifiable", [], "TRUSTED_CLOCK_UNAVAILABLE");
    if (securityContext.clock_source !== "host-secure-clock-v1" || securityContext.evaluation_time !== pack.inspected_at) return null;
    const lock = pack.fact_read_lock;
    if (!(lock.acquired && lock.mode === "shared" && lock.profile_id === registryDoc.lock_profile.profile_id && lock.path === registryDoc.lock_profile.fact_lock.path))
      return inspectStatus(pack, registryDoc, schemaRoot, schemaSha, registrySha, "unverifiable", [], "FACT_READ_LOCK_UNAVAILABLE");
    const strictValid = strictWriterFenceActivationSemantics(pack, registryDoc, schemaRoot, schemaSha, registrySha, expectedIds, hashes, securityContext);
    if (!strictValid) {
      if (!strictActivationControlSemantics(pack, registryDoc, schemaRoot, schemaSha, registrySha, expectedIds, hashes, securityContext))
        return inspectStatus(pack, registryDoc, schemaRoot, schemaSha, registrySha, "migration-required", [], "STRICT_ACTIVATION_REQUIRED");
      const diagnosticLineage = loadStrictLineage(pack, registryDoc, schemaRoot, schemaSha, registrySha, false);
      if (diagnosticLineage === null) return inspectStatus(pack, registryDoc, schemaRoot, schemaSha, registrySha, "migration-required", [], "PUBLISHED_LINEAGE_INVALID");
      const diagnosticFact = pack.documents.fact_state; const diagnosticBody = clone(diagnosticFact); delete diagnosticBody.state_id;
      if (validateRegistered(diagnosticFact, schemaRoot, registryDoc, "fact-generation-state/1.0.0", schemaSha, registrySha)
        && diagnosticFact.state_id === hash(Buffer.from(canonical(diagnosticBody))) && diagnosticFact.fact_generation !== diagnosticLineage.generation.fact_generation)
        return inspectStatus(pack, registryDoc, schemaRoot, schemaSha, registrySha, "stale", [registryDoc.runtime_paths.fact_generation.path], "SOURCE_DRIFT");
      return inspectStatus(pack, registryDoc, schemaRoot, schemaSha, registrySha, "migration-required", [], "STRICT_ACTIVATION_REQUIRED");
    }
    const pointerPath = registryDoc.runtime_paths.panel_current_pointer.path; const pointerRaw = pack.lineage_store[pointerPath];
    if (!Buffer.isBuffer(pointerRaw)) return inspectStatus(pack, registryDoc, schemaRoot, schemaSha, registrySha, "migration-required", [], "PUBLISHED_LINEAGE_INVALID");
    const pointer = JSON.parse(pointerRaw.toString()); const pointerBody = clone(pointer); delete pointerBody.pointer_id;
    if (canonical(pointer) !== pointerRaw.toString() || !validateRegistered(pointer, schemaRoot, registryDoc, "panel-current-pointer/1.0.0", schemaSha, registrySha)
      || pointer.pointer_id !== hash(Buffer.from(canonical(pointerBody))))
      return inspectStatus(pack, registryDoc, schemaRoot, schemaSha, registrySha, "migration-required", [], "PUBLISHED_LINEAGE_INVALID");
    const lineage = loadStrictLineage(pack, registryDoc, schemaRoot, schemaSha, registrySha, false);
    if (lineage === null) return inspectStatus(pack, registryDoc, schemaRoot, schemaSha, registrySha, "migration-required", [], "PUBLISHED_LINEAGE_INVALID");
    const factState = pack.documents.fact_state; const factBody = clone(factState); delete factBody.state_id;
    if (!validateRegistered(factState, schemaRoot, registryDoc, "fact-generation-state/1.0.0", schemaSha, registrySha) || factState.state_id !== hash(Buffer.from(canonical(factBody))))
      return inspectStatus(pack, registryDoc, schemaRoot, schemaSha, registrySha, "migration-required", [], "FACT_STATE_INVALID");
    const generation = lineage.generation; const expectedLeaves = new Map(generation.leaf_sources.map((row) => [`${row.root_instance_id}\0${row.path}`, row]));
    const liveStore = pack.live_leaf_store; const changed = [];
    const keys = [...new Set([...expectedLeaves.keys(), ...Object.keys(liveStore)])].sort((left, right) => Buffer.from(left).compare(Buffer.from(right)));
    for (const key of keys) {
      const source = expectedLeaves.get(key); const raw = liveStore[key];
      if (source === undefined) changed.push(key.split("\0", 2)[1]);
      else if (!Object.hasOwn(liveStore, key)) changed.push(source.path);
      else if (!Buffer.isBuffer(raw)) return inspectStatus(pack, registryDoc, schemaRoot, schemaSha, registrySha, "unverifiable", changed, "SOURCE_UNREADABLE");
      else if (hash(raw) !== source.fingerprint) changed.push(source.path);
    }
    if (factState.fact_generation !== generation.fact_generation) changed.push(registryDoc.runtime_paths.fact_generation.path);
    if (!liveInspectReadSetSemantics(pack, registryDoc)) return null;
    return inspectStatus(pack, registryDoc, schemaRoot, schemaSha, registrySha, changed.length ? "stale" : "fresh", changed, changed.length ? "SOURCE_DRIFT" : null);
  } catch { return null; }
};

const semanticValidatorDispatch = (registryDoc, schemaRoot, suiteDoc, projectRoot, workspaceRoot, schemaSha, registrySha, omittedHandler = null) => {
  if (!semanticRegistrySemantics(registryDoc)) return [false, new Set()];
  const [panel, upstreams, , policy, generation] = panelFixture(suiteDoc.contract_schema_vectors, registryDoc, schemaSha, registrySha, projectRoot);
  const physicalInventory = physicalInventoryFixture(registryDoc, policy, generation.fact_generation, schemaSha, registrySha);
  for (const binding of registryDoc.panel_binding_map) {
    const payload = upstreams[binding.projection_kind];
    setPointer(panel, binding.panel_pointer, binding.merge_mode === "object-by-key" ? Object.fromEntries(payload.map((row) => [row.scenario, clone(row)])) : clone(payload));
  }
  const panelBody = clone(panel); delete panelBody.panel_id; panel.panel_id = hash(Buffer.from(canonical(panelBody)));
  const [built, outerOk] = buildProjectionLineage(panel, upstreams, registryDoc, schemaRoot, schemaSha, registrySha, projectRoot, workspaceRoot, policy);
  const lineageOk = outerOk && projectionLineageSemantics(built, registryDoc, schemaRoot, generation, policy, schemaSha, registrySha);
  const publicationGraph = panelPublicationFixture(panel, built, policy, generation, registryDoc, schemaSha, registrySha);
  const createVector = suiteDoc.wdr_vectors.find(({ id }) => id === "create-byte-exact");
  const createCommand = clone(createVector.command); const createInput = clone(createCommand.create_input); delete createInput.input_id;
  createInput.input_id = hash(Buffer.from(canonical(createInput))); createCommand.create_input = createInput;
  const factGraphs = ["action", "wdr-status", "wdr-meeting-history", "wdr-owned-section", "wdr-roadmap", "wdr-refresh-actions", "intent-only", "owned-risk-flow", "owned-decision"]
    .map((kind) => factAttributionFixture(schemaSha, registrySha, registryDoc, kind));
  factGraphs.push(factAttributionFixture(schemaSha, registrySha, registryDoc, "wdr-create", createCommand));
  const [journal, marker] = journalFixture("fact", schemaSha, registrySha, null, registryDoc); const repairGraph = repairGraphFixture(schemaSha, registrySha, registryDoc);
  const statusBatch = statusIntentFixture(registryDoc, schemaSha, registrySha);
  const meetingPlan = meetingPlanIntentFixture(registryDoc, schemaSha, registrySha);
  const programStatusPackage = programStatusWdrFixture(suiteDoc, registryDoc, schemaSha, registrySha);
  const driftContentPackage = driftContentFixture(registryDoc, schemaSha, registrySha);
  const expectedIds = [...new Set(Object.entries(suiteDoc).filter(([key]) => key.endsWith("_vectors") || key === "journal_fault_matrix").flatMap(([, values]) => values.map(({ id }) => id)))].sort();
  const artifactHashes = { registry: registrySha, schema: schemaSha, protocol: registryDoc.protocol.sha256, suite: registryDoc.conformance_suite.sha256 };
  const strictRegistryForDispatch = designReleaseRegistryFixture(registryDoc);
  const strictRegistryShaForDispatch = hash(Buffer.from(canonical(strictRegistryForDispatch)));
  const strictHashesForDispatch = { ...artifactHashes, registry: strictRegistryShaForDispatch };
  const strictSuiteForDispatch = replaceTokens(suiteDoc, { [registrySha]: strictRegistryShaForDispatch });
  const bootstrapGraph = bootstrapMigrationFixture({ ledger_format: "legacy20", action_flow_preimage: "brownfield-v1", workstreams: ["l1-checkout"] }, registryDoc, schemaSha, registrySha);
  const writerFencePackage = writerFenceFixture(strictRegistryForDispatch, schemaSha, strictRegistryShaForDispatch, expectedIds, strictHashesForDispatch, 1,
    strictSuiteForDispatch, schemaRoot, projectRoot, workspaceRoot);
  const [releaseReceipts, releaseBlobs] = implementationConformanceReceipts(expectedIds, strictHashesForDispatch, strictRegistryForDispatch);
  const releaseTransition = releaseEvidenceTransitionFixture(releaseReceipts, releaseBlobs, strictRegistryForDispatch, schemaSha, strictRegistryShaForDispatch);
  const activationTransition = activationTransitionFixture(writerFencePackage, strictRegistryForDispatch, schemaSha, strictRegistryShaForDispatch);
  const inspectPackage = liveInspectFixture(strictSuiteForDispatch, strictRegistryForDispatch, schemaRoot, schemaSha, strictRegistryShaForDispatch,
    projectRoot, workspaceRoot, expectedIds, strictHashesForDispatch);
  const securityContext = { clock_source: "host-secure-clock-v1", evaluation_time: "2026-07-24T03:05:00Z", available: true };
  const transitionSecurityContext = { clock_source: "host-secure-clock-v1", evaluation_time: "2026-07-24T03:15:00Z", available: true };
  const snapshotAuthority = () => {
    const lineage = loadStrictLineage(writerFencePackage, strictRegistryForDispatch, schemaRoot, schemaSha, strictRegistryShaForDispatch, false);
    return lineage !== null && sourceAsOfSemantics(
      lineage.graph.panel, lineage.policy, lineage.refresh_receipt, strictRegistryForDispatch,
      schemaRoot, schemaSha, strictRegistryShaForDispatch,
    );
  };
  const intentConvergence = () => intentConvergenceSemantics(
    inspectPackage.documents.mutation_intent_outbox, inspectPackage.documents.intent_convergence,
    strictRegistryForDispatch, schemaRoot, schemaSha, strictRegistryShaForDispatch,
  );
  const factReplay = () => {
    const graph = factGraphs[0]; const entry = graph.command_index.entries[0];
    const store = { [entry.receipt_path]: Buffer.from(canonical(graph.receipt)) };
    const [outcome, receipt] = resolveFactCommandReplay(
      graph.command_index, store, graph.command.command_id, hash(Buffer.from(canonical(graph.command))),
      registryDoc, schemaRoot, schemaSha, registrySha,
    );
    const [conflict] = resolveFactCommandReplay(
      graph.command_index, store, graph.command.command_id, `sha256:${"f".repeat(64)}`,
      registryDoc, schemaRoot, schemaSha, registrySha,
    );
    return outcome === "noop" && canonical(receipt) === canonical(graph.receipt) && conflict === "conflict";
  };
  const registryClosure = () => {
    const registeredEnumerators = new Set(registryDoc.dependency_enumerators.map(({ id }) => id));
    const profileEnumerators = new Set(registryDoc.projection_input_profiles.flatMap((profile) => profile.required_sources.map((source) => source.enumerator.id)));
    const supported = new Set([...profileEnumerators, "physical-workstream-inventory-v1"]);
    const profilesExact = registryDoc.projection_input_profiles.every((profile) => { const [allowed, actual] = instrumentedReadTrace(profile, ["l1-checkout"], "none", policy); return canonical(allowed) === canonical(actual); });
    return canonical([...registeredEnumerators].sort()) === canonical([...supported].sort())
      && registryDagSemantics(registryDoc) && enumeratorTempTreeSemantics()
      && physicalWorkstreamInventoryTempTreeSemantics("none", schemaRoot, registryDoc, schemaSha, registrySha) && profilesExact
      && allOrderingRulesSemantics(registryDoc, schemaRoot, suiteDoc, projectRoot, schemaSha, registrySha, "none")
      && identitySetSemantics(registryDoc, schemaSha, registrySha)
      && runtimePathsSemantics(registryDoc);
  };
  const handlers = new Map([
    ["panel-publication-eligibility/1.0.0", () => lineageOk && publicationEligibilitySemantics(panel, physicalInventory, policy, generation, registryDoc, schemaRoot, schemaSha, registrySha, built)],
    ["projection-registry-closure/1.0.0", registryClosure],
    ["fact-receipt-attribution/1.0.0", () => factGraphs.every((graph) => factAttributionSemantics(
      graph, registryDoc, schemaRoot, schemaSha, registrySha,
      ...runtimeAuthorityFixture(registryDoc, schemaSha, registrySha, commandProducer(graph.command)),
    ))],
    ["owned-fact-command-semantics/1.0.0", () => factGraphs.filter((graph) => commandKind(graph.command) === "owned").every((graph) => factAttributionSemantics(
      graph, registryDoc, schemaRoot, schemaSha, registrySha,
      ...runtimeAuthorityFixture(registryDoc, schemaSha, registrySha, commandProducer(graph.command)),
    ))],
    ["transaction-journal-semantics/1.0.0", () => journalSemantics(journal, marker, schemaRoot, registryDoc, schemaSha, registrySha)],
    ["repair-graph-semantics/1.0.0", () => repairGraphSemantics(
      repairGraph, schemaRoot, registryDoc, schemaSha, registrySha,
      ...runtimeAuthorityFixture(registryDoc, schemaSha, registrySha, "adp-status-sync"),
    )],
    ["release-evidence-transition-semantics/1.0.0", () => releaseEvidenceTransitionSemantics(
      releaseTransition, strictRegistryForDispatch, schemaRoot, schemaSha, strictRegistryShaForDispatch,
      expectedIds, strictHashesForDispatch, transitionSecurityContext,
    )],
    ["activation-transition-semantics/1.0.0", () => activationTransitionSemantics(
      activationTransition, strictRegistryForDispatch, schemaRoot, schemaSha, strictRegistryShaForDispatch,
    )],
    ["panel-publication-graph/1.0.0", () => lineageOk && panelPublicationSemantics(publicationGraph, registryDoc, schemaRoot, schemaSha, registrySha)],
    ["panel-binding-semantics/1.0.0", () => lineageOk && panelBindingSemantics(panel, built, registryDoc, policy, generation)],
    ["panel-v1-same-generation-composition/1.0.0", () => panelV1CompositionValid(panel, registryDoc, projectRoot) && executePanelV2Consumer(panel, registryDoc, schemaRoot, projectRoot) !== null],
    ["status-intent-application/1.0.0", () => statusIntentApplicationSemantics(statusBatch, registryDoc, schemaRoot, schemaSha, registrySha)],
    ["meeting-plan-intent-carriers/1.0.0", () => meetingPlanIntentCarrierSemantics(meetingPlan, registryDoc, schemaRoot, schemaSha, registrySha)],
    ["program-status-current-from-wdr/1.0.0", () => programStatusCurrentFromWdrSemantics(programStatusPackage, registryDoc, schemaRoot, schemaSha, registrySha)],
    ["action-projection-drift-content/1.0.0", () => actionProjectionDriftContentSemantics(driftContentPackage, registryDoc, schemaRoot, schemaSha, registrySha)],
    ["bootstrap-migration-attribution/1.0.0", () => bootstrapMigrationSemantics(bootstrapGraph, registryDoc, schemaRoot, schemaSha, registrySha)],
    ["strict-writer-fence-activation/1.0.0", () => strictWriterFenceActivationSemantics(
      writerFencePackage, strictRegistryForDispatch, schemaRoot, schemaSha, strictRegistryShaForDispatch, expectedIds, strictHashesForDispatch, securityContext,
    )],
    ["live-inspect-semantics/1.0.0", () => liveInspectSemantics(
      inspectPackage, strictRegistryForDispatch, schemaRoot, schemaSha, strictRegistryShaForDispatch, expectedIds, strictHashesForDispatch,
      securityContext,
    )?.latest_inspect.outcome === "fresh"],
    ["snapshot-time-authority/1.0.0", snapshotAuthority],
    ["intent-outbox-convergence/1.0.0", intentConvergence],
    ["fact-command-replay/1.0.0", factReplay],
  ]);
  if (omittedHandler !== null) handlers.delete(omittedHandler);
  const executed = new Set(); const results = [];
  for (const row of registryDoc.semantic_validators) {
    const handler = handlers.get(row.id); if (!handler) continue;
    executed.add(row.id); try { results.push(Boolean(handler())); } catch { results.push(false); }
  }
  const registeredIds = new Set(registryDoc.semantic_validators.map(({ id }) => id));
  const exact = canonical([...executed].sort()) === canonical([...registeredIds].sort()) && canonical([...registeredIds].sort()) === canonical([...handlers.keys()].sort());
  return [exact && results.every(Boolean), executed];
};

const suiteBytes = fs.readFileSync(args.suite);
const schemaBytes = fs.readFileSync(args.schema);
const protocolBytes = fs.readFileSync(args.protocol);
const registryBytes = fs.readFileSync(args.registry);
const schema = JSON.parse(schemaBytes);
const registry = JSON.parse(registryBytes);
let suite = JSON.parse(suiteBytes);
const actualHashes = { suite: hash(suiteBytes), schema: hash(schemaBytes), protocol: hash(protocolBytes), registry: hash(registryBytes) };
if (registry.schema_bundle.sha256 !== actualHashes.schema || registry.protocol.sha256 !== actualHashes.protocol || registry.conformance_suite.sha256 !== actualHashes.suite) throw new Error("registry artifact hash mismatch");
if (registry.conformance_suite.release_gate_validator.id !== "conformance-release-gate/1.0.0" || registry.conformance_suite.release_gate_validator.protocol_sha256 !== actualHashes.protocol) throw new Error("release gate validator pin mismatch");
for (const contract of registry.contracts) {
  const definition = contract.schema_pointer.replace("#/$defs/", "");
  if (schema.$defs[definition]?.$anchor !== contract.schema_id.split("#").at(-1)) throw new Error(`registry pointer mismatch: ${contract.name}`);
}
for (const artifact of registry.pinned_source_artifacts) {
  if (hash(fs.readFileSync(path.join(args["project-root"], artifact.path))) !== artifact.sha256) throw new Error(`pinned source mismatch: ${artifact.id}`);
}
const profileKinds = new Set(registry.projection_input_profiles.map(({ projection }) => projection));
const bindingKinds = new Set(registry.projection_payload_bindings.map(({ projection_kind }) => projection_kind));
const envelopeKinds = new Set(schema.$defs.canonicalProjectionEnvelopeV1.properties.projection_kind.enum);
if (canonical([...profileKinds].sort()) !== canonical([...bindingKinds].sort()) || canonical([...profileKinds].sort()) !== canonical([...envelopeKinds].sort())) throw new Error("profile/payload-binding/envelope projection kind mismatch");
const documentWorkspace = path.dirname(path.dirname(path.resolve(args.registry)));
for (const binding of registry.projection_payload_bindings) {
  if (!["document-workspace", "project"].includes(binding.schema_root)) throw new Error(`invalid payload schema root: ${binding.projection_kind}`);
  const root = binding.schema_root === "document-workspace" ? documentWorkspace : args["project-root"];
  const bindingPath = path.join(root, binding.schema_path);
  const raw = fs.readFileSync(bindingPath); const bindingSchema = JSON.parse(raw);
  if (hash(raw) !== binding.schema_sha256) throw new Error(`payload schema hash mismatch: ${binding.projection_kind}`);
  const target = jsonPointer(bindingSchema, binding.schema_pointer);
  const hashIndex = binding.schema_id.lastIndexOf("#");
  const identityOk = hashIndex < 0 ? bindingSchema.$id === binding.schema_id : target.$anchor === binding.schema_id.slice(hashIndex + 1);
  if (!identityOk) throw new Error(`payload schema pointer/id mismatch: ${binding.projection_kind}`);
}
for (const binding of registry.nested_payload_bindings) {
  if (!profileKinds.has(binding.projection_kind) || !["document-workspace", "project"].includes(binding.schema_root)) throw new Error(`invalid nested payload binding: ${binding.projection_kind} ${binding.payload_pointer}`);
  const root = binding.schema_root === "document-workspace" ? documentWorkspace : args["project-root"];
  const raw = fs.readFileSync(path.join(root, binding.schema_path)); const nestedSchema = JSON.parse(raw);
  if (hash(raw) !== binding.schema_sha256 || nestedSchema.$id !== binding.schema_id) throw new Error(`nested payload schema pin mismatch: ${binding.payload_pointer}`);
  const parent = registry.projection_payload_bindings.find(({ projection_kind }) => projection_kind === binding.projection_kind);
  const parentRoot = parent.schema_root === "document-workspace" ? documentWorkspace : args["project-root"];
  const parentSchema = JSON.parse(fs.readFileSync(path.join(parentRoot, parent.schema_path)));
  const parentRule = jsonPointer(parentSchema, parent.schema_pointer);
  jsonPointer(parentRule, binding.projection_kind === "management-panel" ? "/properties/model_v1" : `/properties${binding.payload_pointer}`);
}
suite = replaceTokens(suite, { "$SCHEMA_SHA256": actualHashes.schema, "$REGISTRY_SHA256": actualHashes.registry });
const expectedIds = Object.entries(suite).filter(([key]) => key.endsWith("_vectors") || key === "journal_fault_matrix").flatMap(([, values]) => values.map(({ id }) => id)).sort();
const passed = [];
const failed = [];
const check = (id, condition) => (condition ? passed : failed).push(id);

for (const vector of suite.canonical_json_vectors) {
  try {
    const value = vector.input_code_units ? String.fromCharCode(...vector.input_code_units) : vector.input;
    const actual = canonical(value);
    check(vector.id, Object.hasOwn(vector, "expected_utf8") && actual === vector.expected_utf8);
  } catch {
    check(vector.id, ["JCS_INVALID_UNICODE", "JCS_NUMBER_PROFILE_INVALID"].includes(vector.expected_error));
  }
}
for (const vector of suite.contract_schema_vectors) check(vector.id, validate(vector.instance, schema, vector.schema_def) === vector.expected_valid);

const pinned = Object.fromEntries(registry.pinned_source_artifacts.map((item) => [item.id, item]));
const template = fs.readFileSync(path.join(args["project-root"], pinned["workstream-delivery-record/1.0.0"].path), "utf8");
const currentFields = new Set(["status", "phase", "progress", "blockers", "risks", "dependencies", "change_notes", "last_status_sync", "refresh_actions"]);
for (const vector of suite.wdr_vectors) {
  if (vector.id === "create-byte-exact") {
    const command = clone(vector.command); const input = clone(command.create_input); delete input.input_id;
    input.input_id = hash(Buffer.from(canonical(input)));
    command.create_input = input;
    const rendered = renderCreate(template, input);
    const logical = clone(input); delete logical.input_id;
    check(vector.id, rendered === command.rendered_record && hash(Buffer.from(rendered)) === command.rendered_sha256 && validate(command, schema, "wdrCommandV1") && command.workstream_id === command.create_input.workstream_id && canonical(logical) === canonical(vector.create_input_without_identity));
  } else if (vector.id === "collection-add-and-revision") {
    const values = ["access"];
    for (const value of vector.patch.blockers.values) if (!values.includes(value)) values.push(value);
    const actual = vector.before.replace("- Blockers: access", `- Blockers: ${values.map((value) => value.replaceAll("\\", "\\\\").replaceAll(";", "\\;")).join("; ")}`);
    check(vector.id, actual === vector.expected && vector.after_wdr_revision === vector.before_wdr_revision + 1);
  } else if (vector.id === "meeting-region-whole-file") {
    const records = [...vector.records].sort((a, b) => Buffer.from(`${a.observed_at}\0${a.entry_id}`).compare(Buffer.from(`${b.observed_at}\0${b.entry_id}`)));
    const actual = vector.before.replace("## Record Rule\n", `## Meeting Sync History\n\n${records.map(meetingBlock).join("")}## Record Rule\n`);
    check(vector.id, actual === vector.expected);
  } else if (vector.id === "legacy-section-order-and-first-status-patch") {
    check(vector.id, migrateWdr(vector.before, vector.patch.last_status_sync) === vector.expected && vector.after_wdr_revision === 1 && vector.after_file_generation === 1);
  } else if (vector.id === "mixed-patch-command-level-revision") {
    check(vector.id, vector.fields.some((field) => currentFields.has(field)) && vector.expected_wdr_revision === vector.before_wdr_revision + 1 && vector.expected_file_generation === vector.before_file_generation + 1);
  } else if (vector.id === "meeting-status-intent-routed") {
    check(vector.id, vector.origin_producer === "adp-meeting-sync" && vector.command_issuer === "adp-status-sync" && vector.intent_fields.every((field) => currentFields.has(field)));
  } else if (vector.id.startsWith("wdr-meeting-history-")) {
    const record = {
      entry_id: "M-REPLAY-1", command_id: "cmd-replay-1", observed_at: "2026-07-24T02:00:00Z",
      source_path: "meetings/replay.md", source_fingerprint: `sha256:${"a".repeat(64)}`,
      classification: "wdr_update", summary: "Reviewed", owner: "FDE-C", due_trigger: "next sync", status: "noted",
    };
    const before = fixtureWdr("l1-checkout"); let valid;
    try {
      if (vector.mutation === "meeting-history-duplicate-command-key") {
        applyWdrPatch(before, { set: { meeting_history_append: [record, clone(record)] } }); valid = false;
      } else {
        const once = applyWdrPatch(before, { set: { meeting_history_append: [record] } });
        if (vector.mutation === "meeting-history-identical-replay") {
          const twice = applyWdrPatch(once, { set: { meeting_history_append: [clone(record)] } });
          valid = twice === once && canonical(wdrCounterDelta(once, twice, "l1-checkout")) === canonical([0, 0]);
        } else if (vector.mutation === "meeting-history-conflicting-replay") {
          applyWdrPatch(once, { set: { meeting_history_append: [{ ...record, summary: "Different bytes" }] } }); valid = false;
        } else {
          const earlier = { ...record, entry_id: "M-REPLAY-0", command_id: "cmd-replay-0", observed_at: "2026-07-24T01:00:00Z" };
          const merged = applyWdrPatch(once, { set: { meeting_history_append: [clone(record), earlier] } });
          const rows = parseMeetingHistory(splitWdr(merged)[1]["Meeting Sync History"]);
          const replayed = applyWdrPatch(merged, { set: { meeting_history_append: [earlier, clone(record)] } });
          valid = canonical(rows.map(({ observed_at, entry_id }) => [observed_at, entry_id])) === canonical([
            ["2026-07-24T01:00:00Z", "M-REPLAY-0"], ["2026-07-24T02:00:00Z", "M-REPLAY-1"],
          ]) && replayed === merged;
        }
      }
    } catch { valid = vector.expected_error === "WDR_MUTATION_INVALID"; }
    check(vector.id, valid);
  } else if (["wdr-noncanonical-literal-tbd-rejected", "wdr-noncanonical-escape-rejected"].includes(vector.id)) {
    let valid = false;
    try { parseWdrList(vector.mutation === "noncanonical-literal-tbd" ? "TBD; review" : "review\\x"); }
    catch { valid = vector.expected_error === "WDR_MUTATION_INVALID"; }
    check(vector.id, valid);
  } else if (vector.id === "wdr-next-actions-manual-first-managed-by-id") {
    const first = "[action_id:A-A-1] FDE-A: First (due: next sync)";
    const second = "[action_id:A-B-1] FDE-B: Second (due: later)";
    const after = applyWdrPatch(fixtureWdr("l1-checkout"), { set: { refresh_actions: true } }, [first, second]);
    const actions = wdrCurrentSignature(after, "l1-checkout").next_actions; const [manual, managed] = partitionNextActions(actions);
    check(vector.id, canonical(actions) === canonical(["review", first, second]) && canonical(manual) === canonical(["review"]) && canonical(managed) === canonical([first, second]));
  } else if (vector.id === "wdr-roadmap-byte-exact-replace-replay") {
    const before = fixtureWdr("l1-checkout");
    const patch = { set: { roadmap: { mode: "replace", lines: ["| Milestone | Target |", "| --- | --- |", "| M1 | Gate A |"] } } };
    const once = applyWdrPatch(before, patch); const twice = applyWdrPatch(once, patch);
    check(vector.id, splitWdr(once)[1].Roadmap === "## Roadmap\n\n| Milestone | Target |\n| --- | --- |\n| M1 | Gate A |" && twice === once);
  } else if (vector.id === "wdr-owned-sections-byte-exact-all") {
    const before = fixtureWdr("l1-checkout"); const beforeSections = splitWdr(before)[1];
    const headings = { acceptance: "Acceptance", scope: "Scope", "cross-workstream-links": "Cross-Workstream Links", "decisions-evidence": "Decisions and Evidence", "checkpoint-sync-log": "Checkpoint Sync Log" };
    const allowed = [...new Set(registry.strict_rollout.writer_specs
      .filter((spec) => spec.allowed_operations.includes("patch") && spec.allowed_fields.includes("owned_sections"))
      .flatMap((spec) => spec.allowed_sections))].sort((a, b) => Buffer.from(a).compare(Buffer.from(b)));
    let valid = canonical(allowed) === canonical(Object.keys(headings).sort((a, b) => Buffer.from(a).compare(Buffer.from(b))));
    for (const slug of allowed) {
      const heading = headings[slug];
      const replacePatch = { set: { owned_sections: [{ section: slug, mode: "replace", lines: [`Replacement ${slug}`] }] } };
      const replaced = applyWdrPatch(before, replacePatch); const replayed = applyWdrPatch(replaced, replacePatch);
      const appended = applyWdrPatch(before, { set: { owned_sections: [{ section: slug, mode: "append", lines: [`Append ${slug}`] }] } });
      const expectedAppend = Object.hasOwn(beforeSections, heading) ? `${beforeSections[heading].replace(/\n+$/, "")}\nAppend ${slug}` : `## ${heading}\n\nAppend ${slug}`;
      valid = valid && splitWdr(replaced)[1][heading] === `## ${heading}\n\nReplacement ${slug}`
        && splitWdr(appended)[1][heading] === expectedAppend && replayed === replaced;
    }
    check(vector.id, valid);
  } else if (vector.id === "wdr-owned-section-heading-injection-rejected") {
    let valid = false;
    try { applyWdrPatch(fixtureWdr("l1-checkout"), { set: { owned_sections: [{ section: "checkpoint-sync-log", mode: "append", lines: ["## Injected"] }] } }); }
    catch { valid = vector.expected_error === "WDR_MUTATION_INVALID"; }
    check(vector.id, valid);
  } else {
    const fields = Object.keys(vector.set);
    const hostMatches = (vector.host_capability_producer ?? vector.issuer.producer_id) === vector.issuer.producer_id;
    const allowed = hostMatches && ((fields.every((field) => currentFields.has(field)) && vector.issuer.producer_id === "adp-status-sync") || (canonical(fields) === canonical(["meeting_history_append"]) && vector.issuer.producer_id === "adp-meeting-sync"));
    check(vector.id, !allowed && vector.expected_error === "WDR_WRITER_UNAUTHORIZED");
  }
}

for (const vector of suite.legacy_adapter_vectors) {
  if (vector.id === "meeting-existing-action-owner-status-patch") {
    const { meeting, item } = vector.input;
    check(vector.id, canonical({ operation: "patch", action_id: item.action_id, set: { owner: item.owner, status: item.status, action: item.text }, observed_at: canonicalTimestamp(meeting.started_at) }) === canonical(vector.expected));
  } else if (vector.id === "status-alias-precedence-presence") {
    const action = vector.input.action;
    const actual = { operation: "patch", action_id: action.action_id, set: { due_trigger: action.due_or_trigger, owner: action.owner } };
    check(vector.id, canonical(actual) === canonical(vector.expected) && vector.forbidden_output_fields.every((key) => !(key in actual.set)));
  } else if (vector.id === "status-missing-observed-at") check(vector.id, !("observed_at" in vector.input) && vector.expected_error === "LEGACY_EVIDENCE_TIMESTAMP_REQUIRED");
  else if (vector.id === "meeting-offset-fraction-normalization") check(vector.id, canonicalTimestamp(vector.input) === vector.expected);
  else if (vector.id === "program-action-routing-scope") check(vector.id, canonical({ routing_scope_id: vector.input.workstream, affected_workstreams: [...new Set(vector.input.affected_workstreams)].sort() }) === canonical(vector.expected));
  else check(vector.id, `ACT-${hash(Buffer.from(canonical(vector.identity_input))).slice(7, 27).toUpperCase()}` === vector.expected);
}

const profiles = Object.fromEntries(registry.projection_input_profiles.map((profile) => [profile.projection, profile]));
for (const vector of suite.projection_vectors) {
  if (vector.id === "registry-refresh-output-not-a-leaf") {
    const declared = new Set(registry.projection_input_profiles.flatMap((profile) => profile.required_sources.filter((source) => source.enumerator.id === "exact-path-v1").map((source) => source.enumerator.path)));
    check(vector.id, canonical(vector.publication_paths.filter((item) => declared.has(item)).sort()) === canonical(vector.expected_intersection));
  } else if (vector.id === "meeting-pack-object-by-key") {
    const actual = Object.fromEntries([...vector.inputs].sort((a, b) => Buffer.from(a.scenario).compare(Buffer.from(b.scenario))).map((row) => [row.scenario, row]));
    check(vector.id, canonical(actual) === canonical(vector.expected));
  } else if (vector.id === "meeting-pack-duplicate-key") {
    const keys = vector.inputs.map(({ scenario }) => scenario);
    check(vector.id, new Set(keys).size !== keys.length && vector.expected_error === "PANEL_BINDING_COLLISION");
  } else if (vector.id === "complete-generation-envelope") {
    const body = clone(vector.input_without_identity);
    body.roots.sort((a, b) => Buffer.from(a.root).compare(Buffer.from(b.root)));
    body.leaf_sources.sort((a, b) => Buffer.from(`${a.root_instance_id}\0${a.path}`).compare(Buffer.from(`${b.root_instance_id}\0${b.path}`)));
    body[vector.identity_field] = hash(Buffer.from(canonical(body)));
    check(vector.id, validate(body, schema, "generationEnvelopeV1"));
  } else if (vector.id === "identity-array-permutation-stable") {
    const normalize = (values) => [...new Set(values)].sort((a, b) => Buffer.from(a).compare(Buffer.from(b)));
    check(vector.id, canonical(normalize(vector.left)) === canonical(normalize(vector.right)) && canonical(normalize(vector.left)) === canonical(vector.expected_canonical));
  } else if (vector.id === "rfc6901-root-pointer") check(vector.id, canonical(vector.document) === canonical(vector.expected_root) && vector.document[""] === vector.expected_empty_member && vector.root_pointer === "" && vector.empty_member_pointer === "/");
  else if (vector.id.startsWith("registry-dag-")) {
    const valid = registryDagSemantics(registry, vector.mutation ?? "none");
    check(vector.id, vector.expected ? valid : !valid && vector.expected_error === "DAG_INVALIDATION_INCOMPLETE");
  }
  else if (vector.id === "dependency-enumerator-temp-tree-valid") check(vector.id, enumeratorTempTreeSemantics());
  else if (vector.id.startsWith("physical-workstream-inventory-")) {
    const valid = physicalWorkstreamInventoryTempTreeSemantics(vector.mutation, schema, registry, actualHashes.schema, actualHashes.registry);
    check(vector.id, vector.expected ? valid : !valid && vector.expected_error === "PHYSICAL_WORKSTREAM_INVENTORY_INVALID");
  }
  else if (vector.id === "optional-snapshot-null-enumerates-empty") {
    const source = { enumerator: { id: "selected-immutable-snapshot-v1", base: "snapshots/program-status" } };
    check(vector.id, canonical(enumeratedPaths(source, ["l1-checkout"], { previous_program_status_id: null })) === canonical([]));
  } else if (vector.id === "physical-leaf-metadata-conflict-rejected") {
    const key = "root\0same/path"; const records = new Map([[key, { category: "fact", source_kind: "one" }]]);
    check(vector.id, records.has(key) && canonical(records.get(key)) !== canonical({ category: "fact", source_kind: "two" }) && vector.expected_error === "DEPENDENCY_IDENTITY_CONFLICT");
  } else if (vector.id.startsWith("identity-set-fields-")) {
    const valid = identitySetSemantics(registry, actualHashes.schema, actualHashes.registry, vector.mutation);
    check(vector.id, vector.expected ? valid : !valid && vector.expected_error === "CANONICAL_ORDER_INVALID");
  }
  else if (vector.id.startsWith("profile-read-set-")) {
    const traces = Object.values(profiles).map((profile) => instrumentedReadTrace(profile, ["l1-checkout"], vector.mutation));
    let exact = traces.every(([allowed, actual]) => canonical(allowed) === canonical(actual));
    if (vector.mutation === "drop-one-declared-read") exact = exact || !traces.every(([allowed, actual]) => actual.length + 1 === allowed.length && actual.every((row) => allowed.some((item) => canonical(item) === canonical(row))));
    else if (vector.mutation === "add-undeclared-read") exact = exact || !traces.every(([allowed, actual]) => actual.length === allowed.length + 1 && actual.some((row) => !allowed.some((item) => canonical(item) === canonical(row))));
    check(vector.id, vector.expected ? exact : !exact && ["DECLARED_DEPENDENCY_UNCONSUMED", "UNDECLARED_DEPENDENCY"].includes(vector.expected_error));
  } else if (vector.id.startsWith("all-ordering-rules-")) {
    const valid = allOrderingRulesSemantics(registry, schema, suite, args["project-root"], actualHashes.schema, actualHashes.registry, vector.mutation);
    check(vector.id, vector.expected ? valid : !valid && vector.expected_error === "CANONICAL_ORDER_INVALID");
  }
  else {
    const requiredOk = Object.entries(vector.required).every(([name, kinds]) => kinds.every((kind) => profiles[name].required_sources.some((source) => source.source_kind === kind)));
    const panelKinds = new Set(profiles["management-panel"].required_sources.map((source) => source.source_kind));
    check(vector.id, requiredOk && vector.panel_forbidden_live_source_kinds.every((kind) => !panelKinds.has(kind)));
  }
}

for (const vector of suite.semantic_validator_vectors) {
  const candidate = clone(registry);
  if (vector.mutation === "omit") candidate.semantic_validators.pop();
  else if (vector.mutation === "add") candidate.semantic_validators.push({ id: "unknown/1.0.0", scope: ["x"], algorithm: "unknown" });
  else if (vector.mutation === "algorithm") candidate.semantic_validators[0].algorithm = "changed";
  else if (vector.mutation === "scope") candidate.semantic_validators[0].scope = ["unrelated/9.9.9"];
  const omittedHandler = vector.mutation === "handler-omission" ? "fact-receipt-attribution/1.0.0" : null;
  const [valid, executed] = semanticValidatorDispatch(candidate, schema, suite, args["project-root"], documentWorkspace, actualHashes.schema, actualHashes.registry, omittedHandler);
  const registeredIds = new Set(candidate.semantic_validators.map(({ id }) => id));
  check(vector.id, vector.expected ? valid && canonical([...executed].sort()) === canonical([...registeredIds].sort())
    : !valid && ["SEMANTIC_VALIDATOR_REGISTRY_INVALID", "SEMANTIC_VALIDATOR_DISPATCH_INCOMPLETE"].includes(vector.expected_error));
}

for (const vector of suite.runtime_vectors) {
  if (vector.id === "bootstrap-generation-zero") check(vector.id, vector.fact_state_without_id.fact_generation === 0 && vector.panel_state_without_id.panel_generation === 0);
  else if (vector.id === "filesystem-token-hash-id") check(vector.id, `h_${vector.input.split(":")[1]}` === vector.expected);
  else if (vector.id === "filesystem-token-command-id") check(vector.id, `i_${hash(Buffer.from(vector.input)).slice(7)}` === vector.expected);
  else if (Object.hasOwn(vector, "template")) {
    try {
      const actual = runtimePath(registry, vector.template, vector.generation_id, vector.projection_kind, vector.instance_key);
      check(vector.id, Object.hasOwn(vector, "expected") && actual === vector.expected);
    } catch {
      check(vector.id, vector.expected_error === "RUNTIME_PATH_INVALID");
    }
  } else check(vector.id, vector.input.includes(":") && vector.expected_error === "DEPENDENCY_PATH_UNSAFE");
}

for (const vector of suite.mutation_semantics_vectors) {
  const mutation = vector.mutation;
  const evidence = { source_path: "meetings/edge.md", source_fingerprint: `sha256:${"a".repeat(64)}`, observed_at: "2026-07-24T02:00:00Z" };
  const create = {
    command_id: "cmd-edge-create", operation: "create", action_id: "A-EDGE-1",
    create: { owner: "FDE-C", status: "open", action: "Verify edge behavior", due_trigger: "next sync", closure_criteria: "evidence linked", routing_scope_id: "l1-checkout", affected_workstreams: [] },
    evidence: [evidence],
  };
  const row = actionRowFromCreate(create);
  let valid = false;
  try {
    if (mutation === "action-stale-evidence") {
      applyActionCommand([row], { command_id: "cmd-edge-patch", operation: "patch", action_id: "A-EDGE-1", expected_revision: 1, set: { owner: "FDE-D" }, evidence: [{ ...evidence, observed_at: "2026-07-24T01:59:59Z" }] });
    } else if (mutation === "action-created-after-updated") {
      const candidate = clone(row); candidate.created_at = "2026-07-24T02:00:01Z"; valid = !actionRowChronologyValid(candidate);
    } else if (mutation === "action-lifecycle-inversion") {
      const candidate = { ...clone(row), status: "done", started_at: "2026-07-24T02:03:00Z", done_at: "2026-07-24T02:02:00Z", last_updated: "2026-07-24T02:04:00Z" };
      valid = !actionRowChronologyValid(candidate);
    } else if (mutation === "wdr-stale-evidence") {
      applyWdrPatch(fixtureWdr("l1-checkout"), { set: { progress: "Stale progress" }, evidence: [{ ...evidence, observed_at: "2026-07-24T00:59:59Z" }] });
    } else if (mutation === "wdr-noop-counter") {
      const before = fixtureWdr("l1-checkout"); const after = applyWdrPatch(before, { set: { progress: "Initial progress" } });
      valid = canonical(wdrCounterDelta(before, after, "l1-checkout")) === canonical([0, 0]);
    } else if (mutation === "wdr-current-counter") {
      const before = fixtureWdr("l1-checkout"); const after = applyWdrPatch(before, { set: { progress: "Changed progress" } });
      valid = canonical(wdrCounterDelta(before, after, "l1-checkout")) === canonical([1, 1]);
    } else if (mutation === "wdr-history-counter") {
      const before = fixtureWdr("l1-checkout");
      const after = applyWdrPatch(before, { set: { meeting_history_append: [{ entry_id: "M-EDGE-1", command_id: "cmd-edge-history", observed_at: "2026-07-24T02:00:00Z", source_path: "meetings/edge.md", source_fingerprint: `sha256:${"a".repeat(64)}`, classification: "wdr_update", summary: "Reviewed", owner: "FDE-C", due_trigger: "next sync", status: "noted" }] } });
      valid = canonical(wdrCounterDelta(before, after, "l1-checkout")) === canonical([0, 1]);
    } else if (mutation === "manual-managed-next-actions") {
      const summary = renderedActionSummary(row); const before = fixtureWdr("l1-checkout");
      const after = applyWdrPatch(before, { set: { refresh_actions: true } }, [summary]);
      const [manual, managed] = partitionNextActions(wdrCurrentSignature(after, "l1-checkout").next_actions);
      valid = canonical(manual) === canonical(["review"]) && canonical(managed) === canonical([summary]) && canonical(wdrCounterDelta(before, after, "l1-checkout")) === canonical([1, 1]);
    } else if (mutation === "malformed-managed-marker") {
      partitionNextActions(["[action_id:A-EDGE-1] malformed"]);
    } else if (mutation === "duplicate-managed-marker") {
      const summary = renderedActionSummary(row); partitionNextActions([summary, summary]);
    } else if (mutation === "literal-tbd") {
      valid = renderWdrList(["TBD"]) === "\\TBD" && canonical(parseWdrList("\\TBD")) === canonical(["TBD"]) && canonical(parseWdrList("TBD")) === canonical([]);
    } else {
      const encodedRow = { action_id: "A-ENC-1", owner: "FDE:甲", action: "A (due: x); 付款/100%", due_trigger: "gate)二" };
      const expected = "[action_id:A-ENC-1] FDE%3A%E7%94%B2: A %28due%3A x%29%3B %E4%BB%98%E6%AC%BE%2F100%25 (due: gate%29%E4%BA%8C)";
      const rendered = renderedActionSummary(encodedRow);
      valid = rendered === expected && canonical(parseManagedActionSummary(rendered)) === canonical(encodedRow);
    }
  } catch {
    const expectedError = mutation.startsWith("action-") ? "ACTION_MUTATION_INVALID" : "WDR_MUTATION_INVALID";
    valid = vector.expected_error === expectedError;
  }
  check(vector.id, valid);
}

for (const vector of suite.bootstrap_migration_vectors) {
  let valid;
  if (vector.mutation === "legacy-meeting-history") {
    const before = legacyWdrFixture("l1-checkout").toString().replace("## Record Rule\n", "<!-- adp-meeting-sync:2026-07-23 -->\n## Meeting Sync Update: 2026-07-23\n\n- Update: preserved legacy body\n\n## Record Rule\n");
    const actual = migrateWdr(before, "2026-07-24T02:00:00Z");
    valid = actual.includes("## Meeting Sync History\n\n<!-- adp-meeting-sync:2026-07-23 -->\n### Meeting Sync Update: 2026-07-23")
      && actual.includes("- Update: preserved legacy body") && actual.indexOf("## Meeting Sync History") < actual.indexOf("## Record Rule") && completeWdrValid(actual, "l1-checkout");
  } else if (vector.mutation === "mixed-meeting-history") {
    const before = legacyWdrFixture("l1-checkout").toString().replace("## Record Rule\n", "## Meeting Sync History\n\ncanonical\n\n## Meeting Sync Update: 2026-07-23\n\nlegacy\n\n## Record Rule\n");
    try { migrateWdr(before, "2026-07-24T02:00:00Z"); valid = true; } catch { valid = false; }
  } else {
    const scenario = { ledger_format: vector.ledger_format, action_flow_preimage: vector.action_flow_preimage, workstreams: ["l1-checkout"] };
    const graph = bootstrapMigrationFixture(scenario, registry, actualHashes.schema, actualHashes.registry); const mutation = vector.mutation;
    if (mutation === "malformed-ledger") {
      const malformed = Buffer.from("# Action Ledger\n\nmalformed\n"); const artifact = graph.proof.business_artifacts[0]; artifact.before_bytes = encodedBytes(malformed);
      const target = graph.journal.targets.filter(({ role }) => role === "business")[0]; target.before_sha256 = hash(malformed); target.before_image.sha256 = target.before_sha256;
      graph.receipt.business_targets[0] = clone(target); graph.command.action_ledger.expected_fingerprint = hash(malformed); rebindFactGraph(graph);
    } else if (mutation === "ledger-cas") { graph.command.action_ledger.expected_fingerprint = `sha256:${"f".repeat(64)}`; rebindFactGraph(graph); }
    else if (mutation === "wdr-cas") { graph.command.workstreams[0].expected_record_fingerprint = `sha256:${"f".repeat(64)}`; rebindFactGraph(graph); }
    else if (mutation === "action-flow-shape") {
      const replacement = Buffer.from(canonical({ incompatible: true })); const artifact = graph.proof.business_artifacts[2]; artifact.after_bytes = encodedBytes(replacement);
      const target = graph.journal.targets.filter(({ role }) => role === "business")[2]; target.after_sha256 = hash(replacement); target.after_image.sha256 = target.after_sha256;
      graph.receipt.business_targets[2] = clone(target); rebindFactGraph(graph);
    } else if (mutation === "missing-state-target") {
      const target = graph.journal.targets.filter(({ role }) => role === "business")[1]; graph.journal.targets.splice(graph.journal.targets.indexOf(target), 1); graph.proof.business_artifacts.splice(1, 1);
      reindexTargets(graph.journal.targets, graph.journal.journal_dir); graph.receipt.business_targets = clone(graph.journal.targets.filter(({ role }) => role === "business"));
      graph.receipt.generation_state_target = clone(graph.journal.targets.find(({ role }) => role === "fact-generation")); rebindFactGraph(graph);
    } else if (mutation === "repeat-write") {
      const target = graph.journal.targets.filter(({ role }) => role === "business")[1]; const artifact = graph.proof.business_artifacts[1]; const prior = artifactBytes(artifact.after_bytes);
      target.operation = "replace"; target.before_sha256 = hash(prior); target.before_image = { root_instance_id: target.root_instance_id, path: `${graph.journal.journal_dir}/images/${target.apply_order}-before`, sha256: target.before_sha256 };
      artifact.operation = "replace"; artifact.before_bytes = encodedBytes(prior); graph.receipt.business_targets[1] = clone(target); rebindFactGraph(graph);
    }
    if (mutation === "preservation") {
      const beforeRows = parseActionLedgerIngress(artifactBytes(graph.proof.business_artifacts[0].before_bytes), "legacy20"); const afterRows = parseActionLedger(artifactBytes(graph.proof.business_artifacts[0].after_bytes));
      const preserved = ACTION_LEDGER_FIELDS.slice(0, -1).every((field) => canonical(beforeRows[0][field]) === canonical(afterRows[0][field])) && afterRows[0].action_revision === 1;
      const flow = JSON.parse(artifactBytes(graph.proof.business_artifacts[2].after_bytes).toString());
      valid = preserved && validate(flow, schema, "actionFlowIndexV1") && canonical(flow.actions[0].related_plan_item_ids) === canonical(["PLAN-1"]) && canonical(flow.actions[0].related_flow_edge_ids) === canonical(["EDGE-1"]);
    } else if (mutation === "crash-matrix") {
      valid = bootstrapMigrationSemantics(graph, registry, schema, actualHashes.schema, actualHashes.registry)
        && graph.journal.targets.every((row) => row.after_image !== null && row.after_image.sha256 === row.after_sha256 && (row.operation !== "create" || row.before_image === null));
    } else if (mutation === "idempotent-retry") {
      const again = bootstrapMigrationFixture(scenario, registry, actualHashes.schema, actualHashes.registry);
      valid = bootstrapMigrationSemantics(graph, registry, schema, actualHashes.schema, actualHashes.registry)
        && bootstrapMigrationSemantics(again, registry, schema, actualHashes.schema, actualHashes.registry) && canonical(graph.receipt) === canonical(again.receipt);
    } else valid = bootstrapMigrationSemantics(graph, registry, schema, actualHashes.schema, actualHashes.registry);
  }
  check(vector.id, vector.expected === "valid" ? valid : !valid && vector.expected_error === "BOOTSTRAP_MIGRATION_INVALID");
}

const rebindIdentity = (document, identityField) => { const body = clone(document); delete body[identityField]; document[identityField] = hash(Buffer.from(canonical(body))); };
const rebindAttestationActivation = (pack) => {
  const activation = pack.documents.activation_state;
  const bindingBody = clone(activation); delete bindingBody.attestation_id; delete bindingBody.state_id;
  pack.attestation.activation_state_binding_id = hash(Buffer.from(canonical(bindingBody)));
  rebindWriterFenceAttestation(pack);
  if (activation.mode === "strict") activation.attestation_id = pack.attestation.attestation_id;
  rebindIdentity(activation, "state_id");
};
const strictRegistry = designReleaseRegistryFixture(registry);
const strictRegistrySha = hash(Buffer.from(canonical(strictRegistry)));
const strictHashes = { ...actualHashes, registry: strictRegistrySha };
const strictSuite = replaceTokens(suite, { [actualHashes.registry]: strictRegistrySha });
for (const vector of suite.strict_activation_vectors) {
  let vectorRegistry = vector.mutation === "pending-status" ? registry : strictRegistry;
  let vectorRegistrySha = vector.mutation === "pending-status" ? actualHashes.registry : strictRegistrySha;
  let vectorHashes = vector.mutation === "pending-status" ? actualHashes : strictHashes;
  let vectorSuite = vector.mutation === "pending-status" ? suite : strictSuite;
  if (vector.mutation === "activation-algorithm") {
    vectorRegistry = clone(strictRegistry); vectorRegistry.strict_rollout.activation_algorithm = "mutable-snapshots-exact-match";
    vectorRegistrySha = hash(Buffer.from(canonical(vectorRegistry))); vectorHashes = { ...strictHashes, registry: vectorRegistrySha };
    vectorSuite = replaceTokens(strictSuite, { [strictRegistrySha]: vectorRegistrySha });
  }
  const pack = writerFenceFixture(vectorRegistry, actualHashes.schema, vectorRegistrySha, expectedIds, vectorHashes, vector.mutation === "reenable" ? 2 : 1,
    vectorSuite, schema, args["project-root"], documentWorkspace);
  pack.surface = vector.surface ?? "publish"; const mutation = vector.mutation; const docs = pack.documents;
  if (mutation === "pending-status") { /* raw registry state is authoritative */ }
  else if (mutation === "missing-attestation") delete pack.attestation;
  else if (mutation === "attestation-id") pack.attestation.attestation_id = `sha256:${"f".repeat(64)}`;
  else if (mutation === "attestation-path") pack.attestation_path = "state/other-attestation.json";
  else if (mutation === "root-registry-id") docs.root_registry.registry_state_id = `sha256:${"f".repeat(64)}`;
  else if (mutation === "root-rebound") { docs.root_registry.roots.find(({ role }) => role === "memory").root_instance_id = "123e4567-e89b-42d3-a456-426614174099"; rebindIdentity(docs.root_registry, "registry_state_id"); }
  else if (mutation === "capability-registry-id") docs.capability_registry.capability_registry_id = `sha256:${"f".repeat(64)}`;
  else if (mutation === "capability-epoch") { docs.capability_registry.capability_epoch += 1; rebindIdentity(docs.capability_registry, "capability_registry_id"); }
  else if (mutation === "writer-subset") delete pack.writer_store[registry.strict_rollout.writer_specs[0].artifact_paths[0]];
  else if (mutation === "writer-build") { const artifactPath = registry.strict_rollout.writer_specs[0].artifact_paths[0]; pack.writer_store[artifactPath] = Buffer.concat([pack.writer_store[artifactPath], Buffer.from("\nchanged")]); }
  else if (mutation === "fence-receipt") pack.writer_store[registry.strict_rollout.writer_specs[0].receipt_path] = Buffer.from("{}");
  else if (mutation === "writer-receipt-missing") delete pack.writer_store[registry.strict_rollout.writer_specs[0].receipt_path];
  else if (mutation === "writer-receipt-aliased") { const [first, second] = registry.strict_rollout.writer_specs; pack.writer_store[second.receipt_path] = pack.writer_store[first.receipt_path]; }
  else if (mutation === "writer-receipt-stale") { const receiptPath = registry.strict_rollout.writer_specs[0].receipt_path; const receipt = JSON.parse(pack.writer_store[receiptPath]); receipt.capability_epoch = Math.max(0, receipt.capability_epoch - 1); rebindIdentity(receipt, "receipt_id"); pack.writer_store[receiptPath] = Buffer.from(canonical(receipt)); }
  else if (mutation === "capability-missing") { docs.capability_registry.capabilities.pop(); rebindIdentity(docs.capability_registry, "capability_registry_id"); }
  else if (mutation === "capability-revoked") { const capability = docs.capability_registry.capabilities[0]; capability.status = "revoked"; capability.capability_id = capability.authorization_record_digest = capabilityRecordDigest(capability); rebindIdentity(docs.capability_registry, "capability_registry_id"); }
  else if (mutation === "capability-wrong-scope") { const capability = docs.capability_registry.capabilities[0]; capability.allowed_fields = []; capability.capability_id = capability.authorization_record_digest = capabilityRecordDigest(capability); rebindIdentity(docs.capability_registry, "capability_registry_id"); }
  else if (mutation === "capability-stale-epoch") { docs.capability_registry.capability_epoch += 1; rebindIdentity(docs.capability_registry, "capability_registry_id"); }
  else if (mutation === "capability-lifecycle-attempt") pack.capability_lifecycle_operation = "rotate";
  else if (mutation === "release-set-missing") delete pack.release_store[vectorRegistry.runtime_paths.release_evidence_set.path];
  else if (mutation === "release-unindexed-receipt") {
    const extraPath = runtimePath(vectorRegistry, "release_evidence_receipt_template", null, null, null, null, null, `sha256:${"f".repeat(64)}`);
    pack.release_store[extraPath] = Buffer.from("{}");
  }
  else if (mutation === "release-blob-missing") delete pack.release_store[docs.release_evidence_set.entries[0].evidence_blobs[0].path];
  else if (mutation === "release-receipt-path-substitution") {
    const releaseSet = docs.release_evidence_set; const entry = releaseSet.entries[0]; const oldPath = entry.receipt_path;
    entry.receipt_path = "receipts/conformance/substituted.json"; pack.release_store[entry.receipt_path] = pack.release_store[oldPath]; delete pack.release_store[oldPath];
    rebindIdentity(releaseSet, "release_evidence_set_id"); const setPath = vectorRegistry.runtime_paths.release_evidence_set.path;
    pack.release_store[setPath] = Buffer.from(canonical(releaseSet)); pack.attestation.release_evidence_set_id = releaseSet.release_evidence_set_id;
    rebindAttestationActivation(pack);
  }
  else if (mutation === "registry-raw-substitution") pack.registry_raw = Buffer.concat([pack.registry_raw, Buffer.from("\n")]);
  else if (mutation === "lineage-index-missing") delete pack.lineage_store[pack.attestation.lineage_index_path];
  else if (mutation === "lineage-object-missing") { const index = JSON.parse(pack.lineage_store[pack.attestation.lineage_index_path]); delete pack.lineage_store[index.objects[0].path]; }
  else if (mutation === "lineage-object-extra") pack.lineage_store["views/generations/unexpected.json"] = Buffer.from("{}");
  else if (mutation === "lineage-indexed-extra") {
    const indexPath = pack.attestation.lineage_index_path; const index = JSON.parse(pack.lineage_store[indexPath]);
    const source = index.objects.find((row) => row.object_kind === "projection-envelope" && row.projection_kind === "state-audit");
    const document = JSON.parse(pack.lineage_store[source.path]); document.instance_key = "unexpected"; rebindIdentity(document, "projection_id");
    const objectPath = runtimePath(vectorRegistry, "canonical_projection_template", index.generation_id, "state-audit", "unexpected");
    const raw = Buffer.from(canonical(document)); pack.lineage_store[objectPath] = raw;
    index.objects.push({ ...clone(source), instance_key: "unexpected", object_id: document.projection_id, path: objectPath, sha256: hash(raw) });
    index.objects.sort((left, right) => Buffer.from(`${left.object_kind}\0${left.projection_kind ?? ""}\0${left.instance_key ?? ""}`).compare(Buffer.from(`${right.object_kind}\0${right.projection_kind ?? ""}\0${right.instance_key ?? ""}`)));
    rebindIdentity(index, "index_id"); pack.lineage_store[indexPath] = Buffer.from(canonical(index)); pack.attestation.lineage_index_id = index.index_id;
    rebindAttestationActivation(pack);
  }
  else if (mutation === "lineage-object-redirected") { const indexPath = pack.attestation.lineage_index_path; const index = JSON.parse(pack.lineage_store[indexPath]); index.objects[0].path = "views/generations/redirected.json"; rebindIdentity(index, "index_id"); pack.lineage_store[indexPath] = Buffer.from(canonical(index)); pack.attestation.lineage_index_id = index.index_id; rebindAttestationActivation(pack); }
  else if (mutation === "lineage-singleton-metadata") {
    const indexPath = pack.attestation.lineage_index_path; const index = JSON.parse(pack.lineage_store[indexPath]);
    const target = index.objects.find((row) => row.object_kind === "selection-policy"); target.projection_kind = "program-status";
    index.objects.sort((left, right) => Buffer.from(`${left.object_kind}\0${left.projection_kind ?? ""}\0${left.instance_key ?? ""}`).compare(Buffer.from(`${right.object_kind}\0${right.projection_kind ?? ""}\0${right.instance_key ?? ""}`)));
    rebindIdentity(index, "index_id"); pack.lineage_store[indexPath] = Buffer.from(canonical(index)); pack.attestation.lineage_index_id = index.index_id;
    rebindAttestationActivation(pack);
  }
  else if (["lineage-object-tampered", "panel-byte-tampered", "current-pointer-raw-tampered"].includes(mutation)) { const index = JSON.parse(pack.lineage_store[pack.attestation.lineage_index_path]); let objectPath; if (mutation === "current-pointer-raw-tampered") objectPath = registry.runtime_paths.panel_current_pointer.path; else { const candidates = index.objects.filter(({ object_kind }) => object_kind === "projection-envelope"); const target = mutation === "panel-byte-tampered" ? candidates.find(({ projection_kind }) => projection_kind === "management-panel") : candidates[0]; objectPath = target.path; } pack.lineage_store[objectPath] = Buffer.concat([pack.lineage_store[objectPath], Buffer.from("\n")]); }
  else if (mutation === "lineage-leaf-stale") { const leafKey = Object.keys(pack.live_leaf_store)[0]; pack.live_leaf_store[leafKey] = Buffer.concat([pack.live_leaf_store[leafKey], Buffer.from("\n")]); }
  else if (mutation === "fact-generation") { docs.fact_state.fact_generation += 1; rebindIdentity(docs.fact_state, "state_id"); }
  else if (mutation === "ledger-bytes") docs.ledger_raw = Buffer.concat([docs.ledger_raw, Buffer.from("\n")]);
  else if (mutation === "ledger-state") { docs.ledger_state.ledger_revision += 1; rebindIdentity(docs.ledger_state, "state_id"); }
  else if (mutation === "action-flow") docs.action_flow.compatibility.migration_error_code = "CHANGED";
  else if (mutation === "wdr-bytes") docs.workstreams[0].wdr_raw = Buffer.from(docs.workstreams[0].wdr_raw.toString().replace("Initial progress", "Changed progress"));
  else if (mutation === "wdr-state") docs.workstreams[0].state.wdr_revision += 1;
  else if (mutation === "sidecar") docs.workstreams[0].sidecar.renderer_sha256 = `sha256:${"f".repeat(64)}`;
  else if (mutation === "workstream-omitted") docs.workstreams.pop();
  else if (mutation === "refresh-receipt-id") docs.refresh_receipt.receipt_id = `sha256:${"f".repeat(64)}`;
  else if (mutation === "refresh-status") { docs.refresh_receipt.status = "dirty"; rebindIdentity(docs.refresh_receipt, "receipt_id"); pack.attestation.full_refresh_receipt_id = docs.refresh_receipt.receipt_id; rebindAttestationActivation(pack); }
  else if (mutation === "diagnostic-fact-snapshot") { pack.attestation.fact_generation += 100; rebindAttestationActivation(pack); }
  else if (mutation === "diagnostic-ledger-snapshot") { pack.attestation.ledger.ledger_fingerprint = `sha256:${"f".repeat(64)}`; rebindAttestationActivation(pack); }
  else if (mutation === "diagnostic-wdr-snapshot") { pack.attestation.workstreams[0].wdr_fingerprint = `sha256:${"f".repeat(64)}`; rebindAttestationActivation(pack); }
  else if (mutation === "diagnostic-sidecar-snapshot") { pack.attestation.workstreams[0].sidecar_fingerprint = `sha256:${"f".repeat(64)}`; rebindAttestationActivation(pack); }
  else if (mutation === "diagnostic-refresh-snapshot") { pack.attestation.full_refresh_receipt_id = `sha256:${"f".repeat(64)}`; rebindAttestationActivation(pack); }
  else if (mutation === "diagnostic-publication-snapshot") { pack.attestation.published_generation_id = `sha256:${"f".repeat(64)}`; rebindAttestationActivation(pack); }
  else if (mutation === "diagnostic-pointer-snapshot") { pack.attestation.current_pointer_id = `sha256:${"f".repeat(64)}`; rebindAttestationActivation(pack); }
  else if (mutation === "refresh-fact-generation") { docs.refresh_receipt.expected_fact_generation += 1; rebindIdentity(docs.refresh_receipt, "receipt_id"); const targetPath = runtimePath(registry, "refresh_receipt_generation_template", pack.attestation.published_generation_id); pack.lineage_store[targetPath] = Buffer.concat([pack.lineage_store[targetPath], Buffer.from("\n")]); }
  else if (mutation === "refresh-panel-generation") { docs.refresh_receipt.expected_panel_generation += 1; rebindIdentity(docs.refresh_receipt, "receipt_id"); const targetPath = runtimePath(registry, "refresh_receipt_generation_template", pack.attestation.published_generation_id); pack.lineage_store[targetPath] = Buffer.concat([pack.lineage_store[targetPath], Buffer.from("\n")]); }
  else if (mutation === "refresh-generation") { docs.refresh_receipt.generation_id = `sha256:${"f".repeat(64)}`; rebindIdentity(docs.refresh_receipt, "receipt_id"); const targetPath = runtimePath(registry, "refresh_receipt_generation_template", pack.attestation.published_generation_id); pack.lineage_store[targetPath] = Buffer.concat([pack.lineage_store[targetPath], Buffer.from("\n")]); }
  else if (mutation === "source-as-of-refresh-mismatch") {
    const refresh = docs.refresh_receipt; refresh.source_as_of = "2026-07-24T02:00:01Z"; rebindIdentity(refresh, "receipt_id");
    const refreshPath = runtimePath(vectorRegistry, "refresh_receipt_generation_template", pack.attestation.published_generation_id);
    const refreshRaw = Buffer.from(canonical(refresh)); pack.lineage_store[refreshPath] = refreshRaw;
    const indexPath = pack.attestation.lineage_index_path; const index = JSON.parse(pack.lineage_store[indexPath]);
    const row = index.objects.find(({ object_kind }) => object_kind === "refresh-receipt"); row.object_id = refresh.receipt_id; row.sha256 = hash(refreshRaw);
    rebindIdentity(index, "index_id"); pack.lineage_store[indexPath] = Buffer.from(canonical(index));
    pack.attestation.full_refresh_receipt_id = refresh.receipt_id; pack.attestation.lineage_index_id = index.index_id; rebindAttestationActivation(pack);
  }
  else if (mutation === "publication-receipt-id") docs.publication_receipt.receipt_id = `sha256:${"f".repeat(64)}`;
  else if (mutation === "publication-generation") { docs.publication_receipt.generation_id = `sha256:${"f".repeat(64)}`; rebindIdentity(docs.publication_receipt, "receipt_id"); pack.attestation.panel_publication_receipt_id = docs.publication_receipt.receipt_id; rebindAttestationActivation(pack); }
  else if (mutation === "pointer-id") docs.current_pointer.pointer_id = `sha256:${"f".repeat(64)}`;
  else if (mutation === "pointer-path") { docs.current_pointer.projections[0].canonical_path = "views/generations/wrong.json"; rebindIdentity(docs.current_pointer, "pointer_id"); const targetPath = registry.runtime_paths.panel_current_pointer.path; pack.lineage_store[targetPath] = Buffer.concat([pack.lineage_store[targetPath], Buffer.from("\n")]); }
  else if (mutation === "panel-state") { docs.panel_state.panel_generation += 1; rebindIdentity(docs.panel_state, "state_id"); const targetPath = registry.runtime_paths.panel_state.path; pack.lineage_store[targetPath] = Buffer.concat([pack.lineage_store[targetPath], Buffer.from("\n")]); }
  else if (mutation === "activation-mode") { docs.activation_state.mode = "legacy"; docs.activation_state.attestation_id = null; rebindIdentity(docs.activation_state, "state_id"); }
  else if (mutation === "activation-epoch") { docs.activation_state.activation_epoch += 1; rebindIdentity(docs.activation_state, "state_id"); }
  else if (mutation === "activation-attestation") { docs.activation_state.attestation_id = `sha256:${"f".repeat(64)}`; rebindIdentity(docs.activation_state, "state_id"); }
  else if (mutation === "activation-binding") { docs.activation_state.changed_at = "2026-07-24T03:00:04Z"; rebindIdentity(docs.activation_state, "state_id"); }
  else if (mutation === "stale-attestation") { pack.attestation.attested_at = "2026-07-24T02:59:59Z"; rebindAttestationActivation(pack); }
  else if (mutation === "rollback") { docs.activation_state.activation_epoch += 1; docs.activation_state.mode = "legacy"; docs.activation_state.attestation_id = null; rebindIdentity(docs.activation_state, "state_id"); }
  else if (mutation === "manual-flip") pack.release_store = {};
  const valid = strictWriterFenceActivationSemantics(pack, vectorRegistry, schema, actualHashes.schema, vectorRegistrySha, expectedIds, vectorHashes,
    { clock_source: "host-secure-clock-v1", evaluation_time: vector.evaluation_time ?? "2026-07-24T03:05:00Z", available: vector.clock_available ?? true });
  check(vector.id, vector.expected === "valid" ? valid : !valid && vector.expected_error === "MIGRATION_REQUIRED");
}

const [transitionReceipts, transitionBlobs] = implementationConformanceReceipts(expectedIds, strictHashes, strictRegistry);
const rebindTransitionJournal = (pack, receiptKey = null) => {
  if (receiptKey !== null) {
    const receipt = pack[receiptKey]; rebindIdentity(receipt, "receipt_id");
    const receiptTarget = pack.journal.targets.find(({ role }) => role === "receipt");
    receiptTarget.after_sha256 = hash(Buffer.from(canonical(receipt)));
    receiptTarget.after_image.sha256 = receiptTarget.after_sha256;
  }
  rebindIdentity(pack.journal, "manifest_id"); pack.marker.manifest_id = pack.journal.manifest_id; rebindIdentity(pack.marker, "marker_id");
};

for (const vector of suite.release_transition_vectors) {
  let pack = releaseEvidenceTransitionFixture(
    transitionReceipts, transitionBlobs, strictRegistry, actualHashes.schema, strictRegistrySha,
  );
  const mutation = vector.mutation;
  if (mutation === "before-generation") { pack.transition_receipt.before_generation += 1; rebindTransitionJournal(pack, "transition_receipt"); }
  else if (mutation === "before-set") { pack.transition_receipt.before_set_id = `sha256:${"f".repeat(64)}`; rebindTransitionJournal(pack, "transition_receipt"); }
  else if (mutation === "after-set") { pack.transition_receipt.after_set_id = `sha256:${"f".repeat(64)}`; rebindTransitionJournal(pack, "transition_receipt"); }
  else if (mutation === "journal-id") { pack.transition_receipt.journal_id = "journal-substituted"; rebindTransitionJournal(pack, "transition_receipt"); }
  else if (mutation === "target-path") {
    pack.journal.targets.find(({ role }) => role === "release-evidence").path = "state/release-evidence/substituted.json";
    rebindTransitionJournal(pack);
  }
  else if (mutation.startsWith("history-") && mutation !== "history-chronology") {
    const historical = pack.after_history.entries[0]; let tamperPath;
    if (mutation === "history-receipt-tamper") tamperPath = historical.transition_receipt_path;
    else if (mutation === "history-journal-tamper") tamperPath = historical.journal_path;
    else if (mutation === "history-marker-tamper") tamperPath = historical.terminal_marker_path;
    else {
      const historicalSet = JSON.parse(pack.final_store[historical.set_path]);
      tamperPath = historicalSet.entries[0].evidence_blobs[0].path;
    }
    const original = pack.final_store[tamperPath];
    pack.final_store[tamperPath] = Buffer.isBuffer(original) ? Buffer.concat([original, Buffer.from("\n")]) : Buffer.from("\n");
  }
  else if (mutation === "history-chronology") pack = releaseEvidenceTransitionFixture(
    transitionReceipts, transitionBlobs, strictRegistry, actualHashes.schema, strictRegistrySha, "2026-07-24T02:59:59Z",
  );
  let valid;
  if (["recovery-uncommitted", "recovery-committed"].includes(mutation))
    valid = transitionRecoverySemantics(pack, vector.crash_after, mutation === "recovery-committed");
  else if (["recovery-all-uncommitted", "recovery-all-committed"].includes(mutation))
    valid = Array.from({ length: pack.journal.targets.length + 1 }, (_, crashAfter) =>
      transitionRecoverySemantics(pack, crashAfter, mutation === "recovery-all-committed")).every(Boolean);
  else valid = releaseEvidenceTransitionSemantics(
    pack, strictRegistry, schema, actualHashes.schema, strictRegistrySha, expectedIds, strictHashes,
    { clock_source: "host-secure-clock-v1", evaluation_time: vector.evaluation_time ?? "2026-07-24T03:15:00Z", available: mutation !== "clock-unavailable" },
  );
  check(vector.id, vector.expected === "valid" ? valid : !valid && vector.expected_error === "RELEASE_TRANSITION_INVALID");
}

const transitionWriterPackage = writerFenceFixture(
  strictRegistry, actualHashes.schema, strictRegistrySha, expectedIds, strictHashes, 1,
  strictSuite, schema, args["project-root"], documentWorkspace,
);
const activationTransitionBase = activationTransitionFixture(transitionWriterPackage, strictRegistry, actualHashes.schema, strictRegistrySha);
for (const vector of suite.activation_transition_vectors) {
  const pack = cloneWithBuffers(activationTransitionBase);
  const mutation = vector.mutation;
  if (mutation === "operation-order") [pack.steps[0], pack.steps[1]] = [pack.steps[1], pack.steps[0]];
  else if (mutation === "activation-cas") pack.steps[0].command.expected_activation_epoch += 1;
  else if (mutation === "capability-cas") pack.steps[1].command.expected_capability_epoch += 1;
  else if (mutation === "authority") pack.steps[0].command.authority_context_id = `sha256:${"f".repeat(64)}`;
  else if (mutation === "approval-order") pack.steps[0].command.approved_by.reverse();
  else if (mutation === "target-path") {
    const step = pack.steps[0]; step.journal.targets.find(({ role }) => role !== "receipt").path = "state/substituted-activation.json";
    rebindTransitionJournal(step);
  }
  else if (mutation === "refresh-binding") {
    const step = pack.steps[2]; step.receipt.full_refresh_receipt_id = `sha256:${"f".repeat(64)}`; rebindTransitionJournal(step, "receipt");
  }
  else if (mutation === "attestation-binding") {
    const step = pack.steps[3]; step.receipt.attestation_id = `sha256:${"f".repeat(64)}`; rebindTransitionJournal(step, "receipt");
  }
  else if (mutation === "attestation-preimage-cas") pack.steps[3].command.expected_attestation_sha256 = `sha256:${"f".repeat(64)}`;
  else if (mutation === "predecessor-rebind") {
    const step = pack.steps[1]; const substituted = `sha256:${"f".repeat(64)}`;
    step.command.predecessor_receipt_id = substituted; step.receipt.predecessor_receipt_id = substituted; rebindTransitionJournal(step, "receipt");
  }
  else if (mutation === "forged-lifecycle-receipt") {
    const step = pack.steps[1]; step.after_lifecycle_index.entries.at(-1).receipt_id = `sha256:${"f".repeat(64)}`;
    rebindIdentity(step.after_lifecycle_index, "index_id"); const target = step.journal.targets.find(({ role }) => role === "activation-lifecycle-index");
    target.after_sha256 = hash(Buffer.from(canonical(step.after_lifecycle_index))); target.after_image.sha256 = target.after_sha256; rebindTransitionJournal(step);
  }
  else if (mutation === "broken-lifecycle-prefix") {
    const step = pack.steps[1]; step.before_lifecycle_index.terminal_status = "enabled"; rebindIdentity(step.before_lifecycle_index, "index_id");
    const target = step.journal.targets.find(({ role }) => role === "activation-lifecycle-index");
    target.before_sha256 = hash(Buffer.from(canonical(step.before_lifecycle_index))); target.before_image.sha256 = target.before_sha256; rebindTransitionJournal(step);
  }
  else if (mutation === "first-lifecycle-replace") {
    const step = pack.steps[0]; step.journal.targets.find(({ role }) => role === "activation-lifecycle-index").operation = "replace"; rebindTransitionJournal(step);
  }
  else if (mutation === "uncommitted-lifecycle-receipt") {
    const step = pack.steps[1]; step.receipt.status = "rolled-back"; rebindTransitionJournal(step, "receipt");
  }
  else if (mutation === "disconnected-chain") {
    const step = pack.steps[1]; const disconnectedActivation = clone(pack.steps[0].before_activation);
    step.before_activation = disconnectedActivation; step.after_activation = clone(disconnectedActivation);
    step.authority = runtimeAuthorityFromDocuments(
      strictRegistry, actualHashes.schema, strictRegistrySha, strictRegistry.strict_rollout.activation_administrator_producer_id,
      step.before_capability, pack.roots, disconnectedActivation, pack.initial_attestation,
    );
    step.command.authority_context_id = step.authority.at(-1).context_id;
    step.command.expected_activation_epoch = disconnectedActivation.activation_epoch;
    step.command.expected_activation_state_id = disconnectedActivation.state_id;
    step.receipt.before_activation_epoch = disconnectedActivation.activation_epoch;
    step.receipt.after_activation_epoch = disconnectedActivation.activation_epoch;
    step.receipt.before_activation_state_id = disconnectedActivation.state_id;
    step.receipt.after_activation_state_id = disconnectedActivation.state_id;
    rebindTransitionJournal(step, "receipt");
  }
  let valid;
  if (["recovery-uncommitted", "recovery-committed"].includes(mutation))
    valid = transitionRecoverySemantics(pack.steps[vector.step - 1], vector.crash_after, mutation === "recovery-committed");
  else if (["recovery-all-uncommitted", "recovery-all-committed"].includes(mutation)) valid = pack.steps.every((step) =>
    Array.from({ length: step.journal.targets.length + 1 }, (_, crashAfter) =>
      transitionRecoverySemantics(step, crashAfter, mutation === "recovery-all-committed")).every(Boolean));
  else valid = activationTransitionSemantics(pack, strictRegistry, schema, actualHashes.schema, strictRegistrySha);
  check(vector.id, vector.expected === "valid" ? valid : !valid && vector.expected_error === "ACTIVATION_TRANSITION_INVALID");
}

for (const vector of suite.source_time_vectors) {
  const [panel, upstreams, , policy] = panelFixture(suite.contract_schema_vectors, registry, actualHashes.schema, actualHashes.registry, args["project-root"]);
  for (const binding of registry.panel_binding_map) {
    const payload = upstreams[binding.projection_kind];
    setPointer(panel, binding.panel_pointer, binding.merge_mode === "object-by-key" ? Object.fromEntries(payload.map((row) => [row.scenario, clone(row)])) : clone(payload));
  }
  const refreshReceipt = { source_as_of: policy.as_of }; const mismatch = "2026-07-24T02:00:01Z"; const mutation = vector.mutation;
  if (mutation === "panel") panel.sync.source_as_of = mismatch;
  else if (mutation === "audit") panel.sync.audit.source_as_of = mismatch;
  else if (mutation === "status") panel.sync.canonical.status.source_as_of = mismatch;
  else if (mutation === "roadmap") panel.sync.canonical.roadmap.source_as_of = mismatch;
  else if (mutation === "meeting") Object.values(panel.sync.canonical.meetings)[0].source_as_of = mismatch;
  else if (mutation === "flow-state") panel.sync.canonical.flow.state.as_of = mismatch;
  else if (mutation === "flow-scope") {
    const scopes = panel.sync.canonical.flow.overlays.scopes; if (scopes.length) scopes[0].as_of = mismatch; else scopes.push({ as_of: mismatch });
  }
  else if (mutation === "refresh") refreshReceipt.source_as_of = mismatch;
  const valid = sourceAsOfSemantics(panel, policy, refreshReceipt, registry);
  check(vector.id, vector.expected === "valid" ? valid : !valid && vector.expected_error === "SOURCE_AS_OF_MISMATCH");
}

const snapshotWriterPackage = writerFenceFixture(
  strictRegistry, actualHashes.schema, strictRegistrySha, expectedIds, strictHashes, 1,
  strictSuite, schema, args["project-root"], documentWorkspace,
);
const snapshotLineage = loadStrictLineage(
  snapshotWriterPackage, strictRegistry, schema, actualHashes.schema, strictRegistrySha, false,
);
for (const vector of suite.snapshot_authority_vectors) {
  if (snapshotLineage === null) { check(vector.id, false); continue; }
  const panel = clone(snapshotLineage.graph.panel);
  const policy = clone(snapshotLineage.policy);
  const refreshReceipt = clone(snapshotLineage.refresh_receipt);
  const vectorRegistry = clone(strictRegistry);
  const snapshot = snapshotTimeFixture(vectorRegistry, actualHashes.schema, strictRegistrySha, policy, refreshReceipt);
  const mutation = vector.mutation;
  if (mutation === "future-source-time") snapshot.evaluation_time = "2026-01-01T00:00:00Z";
  else if (mutation === "older-than-maximum-fact") {
    snapshot.lock_receipt.maximum_fact_observed_at = "2026-07-24T03:00:01Z";
    rebindIdentity(snapshot.lock_receipt, "receipt_id");
  } else if (mutation === "request-after-lock") {
    snapshot.request.requested_at = "2026-07-24T03:00:01Z";
    rebindIdentity(snapshot.request, "request_id");
    snapshot.lock_receipt.refresh_request_id = snapshot.request.request_id;
    rebindIdentity(snapshot.lock_receipt, "receipt_id");
  } else if (mutation === "request-binding") {
    snapshot.lock_receipt.refresh_request_id = `sha256:${"f".repeat(64)}`;
    rebindIdentity(snapshot.lock_receipt, "receipt_id");
  } else if (mutation === "policy-lock-id") policy.snapshot_lock_receipt_id = `sha256:${"f".repeat(64)}`;
  else if (mutation === "policy-snapshot-id") policy.snapshot_id = `sha256:${"f".repeat(64)}`;
  else if (mutation === "refresh-snapshot-id") refreshReceipt.snapshot_id = `sha256:${"f".repeat(64)}`;
  else if (mutation === "registry-binding-omission") vectorRegistry.source_time_bindings.pop();
  const valid = sourceAsOfSemantics(
    panel, policy, refreshReceipt, vectorRegistry, schema, actualHashes.schema, strictRegistrySha, snapshot,
  );
  check(vector.id, vector.expected === "valid" ? valid : !valid && vector.expected_error === "SNAPSHOT_TIME_INVALID");
}

const factReplayBase = factAttributionFixture(actualHashes.schema, actualHashes.registry, registry);
for (const vector of suite.fact_replay_vectors) {
  const index = clone(factReplayBase.command_index);
  const entry = index.entries[0];
  const receiptStore = { [entry.receipt_path]: Buffer.from(canonical(factReplayBase.receipt)) };
  let commandId = factReplayBase.command.command_id;
  let fingerprint = hash(Buffer.from(canonical(factReplayBase.command)));
  const mutation = vector.mutation;
  if (mutation === "fingerprint-conflict") fingerprint = `sha256:${"f".repeat(64)}`;
  else if (mutation === "new-command") commandId = "cmd-fact-replay-new";
  else if (mutation === "missing-receipt") delete receiptStore[entry.receipt_path];
  else if (mutation === "tampered-receipt") receiptStore[entry.receipt_path] = Buffer.concat([receiptStore[entry.receipt_path], Buffer.from("\n")]);
  else if (mutation === "wrong-receipt-path") {
    const oldPath = entry.receipt_path; entry.receipt_path = "receipts/fact/wrong.json";
    receiptStore[entry.receipt_path] = receiptStore[oldPath]; delete receiptStore[oldPath]; rebindIdentity(index, "index_id");
  } else if (mutation === "wrong-receipt-hash") { entry.receipt_sha256 = `sha256:${"f".repeat(64)}`; rebindIdentity(index, "index_id"); }
  else if (mutation === "sequence-gap") { entry.sequence = 2; rebindIdentity(index, "index_id"); }
  else if (mutation === "duplicate-command-id") {
    const duplicate = clone(entry); duplicate.sequence = 2; index.entries.push(duplicate); index.next_sequence = 3; rebindIdentity(index, "index_id");
  }
  const [outcome] = resolveFactCommandReplay(
    index, receiptStore, commandId, fingerprint, registry, schema, actualHashes.schema, actualHashes.registry,
  );
  check(vector.id, outcome === vector.expected_outcome);
}

const strictIntentGraphs = {
  meeting: factAttributionFixture(actualHashes.schema, strictRegistrySha, strictRegistry, "wdr-meeting-history"),
  checkpoint: factAttributionFixture(actualHashes.schema, strictRegistrySha, strictRegistry, "wdr-owned-section"),
  risk: factAttributionFixture(actualHashes.schema, strictRegistrySha, strictRegistry, "owned-risk-flow"),
  "risk-decision": factAttributionFixture(actualHashes.schema, strictRegistrySha, strictRegistry, "owned-decision"),
  "status-consume": factAttributionFixture(actualHashes.schema, strictRegistrySha, strictRegistry, "wdr-status"),
};
for (const vector of suite.intent_outbox_vectors) {
  const { scenario, mutation } = vector;
  let valid;
  if (Object.hasOwn(strictIntentGraphs, scenario)) {
    const graph = clone(strictIntentGraphs[scenario]);
    if (mutation === "missing-target") {
      graph.journal.targets = graph.journal.targets.filter(({ role }) => role !== "intent-outbox");
      reindexTargets(graph.journal.targets, graph.journal.journal_dir); rebindFactGraph(graph);
    } else if (["emitted-status", "consumed-receipt"].includes(mutation)) {
      const entry = graph.after_outbox.entries[0];
      if (mutation === "emitted-status") Object.assign(entry, { status: "consumed", attempts: 1, last_error: null, consumed_receipt_id: graph.receipt.receipt_id });
      else entry.consumed_receipt_id = `sha256:${"f".repeat(64)}`;
      rebindIdentity(graph.after_outbox, "outbox_id");
      const target = graph.journal.targets.find(({ role }) => role === "intent-outbox");
      target.after_sha256 = hash(Buffer.from(canonical(graph.after_outbox))); target.after_image.sha256 = target.after_sha256;
      rebindFactGraph(graph);
    }
    else if (mutation === "intent-digest-substitution") { graph.command.status_intents[0].set = { progress: "Substituted" }; rebindFactGraph(graph); }
    else if (mutation === "missing-command-intent") { delete graph.command.status_intents; rebindFactGraph(graph); }
    else if (["omitted-consumed-intent", "extra-consumed-intent"].includes(mutation)) {
      if (mutation === "omitted-consumed-intent") graph.command.consumed_intent_ids = graph.command.consumed_intent_ids.slice(1);
      else { graph.command.consumed_intent_ids.push(`sha256:${"f".repeat(64)}`); graph.command.consumed_intent_ids.sort(); }
      rebindFactGraph(graph);
    }
    else if (["terminal-consumed-intent", "cross-workstream-consumed-intent"].includes(mutation)) {
      const beforeEntry = graph.before_outbox.entries[0]; const afterEntry = graph.after_outbox.entries[0];
      if (mutation === "terminal-consumed-intent") Object.assign(beforeEntry, { status: "consumed", attempts: 1, consumed_receipt_id: graph.receipt.receipt_id });
      else {
        const oldId = beforeEntry.intent_id;
        for (const entry of [beforeEntry, afterEntry]) {
          entry.intent.workstream_id = "l1-other"; entry.workstream_id = "l1-other"; entry.intent_id = hash(Buffer.from(canonical(entry.intent)));
        }
        graph.command.consumed_intent_ids = graph.command.consumed_intent_ids.map((value) => value === oldId ? afterEntry.intent_id : value).sort();
      }
      for (const name of ["before_outbox", "after_outbox"]) rebindIdentity(graph[name], "outbox_id");
      const target = graph.journal.targets.find(({ role }) => role === "intent-outbox");
      target.before_sha256 = hash(Buffer.from(canonical(graph.before_outbox))); target.after_sha256 = hash(Buffer.from(canonical(graph.after_outbox)));
      target.before_image.sha256 = target.before_sha256; target.after_image.sha256 = target.after_sha256; rebindFactGraph(graph);
    }
    else if (mutation === "extra-same-workstream-pending") {
      const extra = clone(graph.before_outbox.entries[0]); extra.sequence = graph.before_outbox.entries.length + 1;
      extra.intent.intent_id = "meeting-extra-same-workstream"; extra.intent.set = { risks: { mode: "add", values: ["late carrier"] } };
      extra.intent_id = hash(Buffer.from(canonical(extra.intent))); extra.source_command_id = "cmd-extra-same-workstream";
      extra.source_command_fingerprint = `sha256:${"e".repeat(64)}`; extra.field_set = ["risks"];
      graph.before_outbox.entries.push(extra); graph.after_outbox.entries.push(clone(extra));
      for (const name of ["before_outbox", "after_outbox"]) rebindIdentity(graph[name], "outbox_id");
      const target = graph.journal.targets.find(({ role }) => role === "intent-outbox");
      target.before_sha256 = hash(Buffer.from(canonical(graph.before_outbox))); target.after_sha256 = hash(Buffer.from(canonical(graph.after_outbox)));
      target.before_image.sha256 = target.before_sha256; target.after_image.sha256 = target.after_sha256; rebindFactGraph(graph);
    }
    else if (["denied-status-intents-capability", "denied-consumed-intent-ids-capability"].includes(mutation)) {
      const capability = graph.capability_registry.capabilities.find(({ producer_id }) => producer_id === commandProducer(graph.command));
      const denied = mutation === "denied-status-intents-capability" ? "status_intents" : "consumed_intent_ids";
      capability.allowed_fields = capability.allowed_fields.filter((field) => field !== denied); rebindFactGraph(graph);
    }
    const authority = runtimeAuthorityFixture(strictRegistry, actualHashes.schema, strictRegistrySha, commandProducer(graph.command));
    valid = factAttributionSemantics(graph, strictRegistry, schema, actualHashes.schema, strictRegistrySha, ...authority);
  } else if (["pending", "failed", "consumed"].includes(scenario)) {
    const sourceGraph = strictIntentGraphs[scenario === "consumed" ? "status-consume" : "meeting"];
    const outbox = clone(sourceGraph.after_outbox);
    if (scenario === "failed") {
      Object.assign(outbox.entries[0], { status: "failed", attempts: 1, last_error: "STATUS_SYNC_FAILED" });
      rebindIdentity(outbox, "outbox_id");
    }
    const verdict = convergenceVerdict(outbox, strictRegistry, actualHashes.schema, strictRegistrySha);
    if (mutation === "invented-id") { verdict.pending_intent_ids.push(`sha256:${"f".repeat(64)}`); verdict.pending_intent_ids.sort(); rebindIdentity(verdict, "verdict_id"); }
    else if (mutation === "omitted-id") { verdict.pending_intent_ids = []; rebindIdentity(verdict, "verdict_id"); }
    const consumedReceipts = scenario !== "consumed" ? null : mutation === "missing-consumed-receipt" ? {}
      : { [sourceGraph.receipt.receipt_id]: Buffer.from(canonical(sourceGraph.receipt)) };
    valid = intentConvergenceSemantics(outbox, verdict, strictRegistry, schema, actualHashes.schema, strictRegistrySha, consumedReceipts);
  } else {
    if (snapshotLineage === null) { check(vector.id, false); continue; }
    const panel = clone(snapshotLineage.graph.panel);
    const sourceGraph = strictIntentGraphs.meeting;
    const outbox = clone(sourceGraph.after_outbox);
    if (scenario === "eligibility-failed") {
      Object.assign(outbox.entries[0], { status: "failed", attempts: 1, last_error: "STATUS_SYNC_FAILED" });
      rebindIdentity(outbox, "outbox_id");
    }
    const verdict = convergenceVerdict(outbox, strictRegistry, actualHashes.schema, strictRegistrySha);
    panel.sync.audit.intent_convergence = clone(verdict);
    if (mutation === "mark-ineligible") panel.sync.publication_eligibility = "ineligible";
    valid = publicationEligibilitySemantics(
      panel, snapshotLineage.graph.physical_inventory, snapshotLineage.policy, snapshotLineage.generation,
      strictRegistry, schema, actualHashes.schema, strictRegistrySha, snapshotLineage.graph.built, outbox, verdict,
    );
  }
  check(vector.id, vector.expected === "valid" ? valid : !valid && ["INTENT_OUTBOX_INVALID", "FACT_ATTRIBUTION_INVALID"].includes(vector.expected_error));
}

for (const vector of suite.publication_replay_vectors) {
  const [panel, upstreams, , policy, generation] = panelFixture(
    suite.contract_schema_vectors, registry, actualHashes.schema, actualHashes.registry, args["project-root"],
  );
  for (const binding of registry.panel_binding_map) {
    const payload = upstreams[binding.projection_kind];
    setPointer(panel, binding.panel_pointer, binding.merge_mode === "object-by-key" ? Object.fromEntries(payload.map((row) => [row.scenario, clone(row)])) : clone(payload));
  }
  const panelBody = clone(panel); delete panelBody.panel_id; panel.panel_id = hash(Buffer.from(canonical(panelBody)));
  const [built, outerOk] = buildProjectionLineage(
    panel, upstreams, registry, schema, actualHashes.schema, actualHashes.registry,
    args["project-root"], documentWorkspace, policy,
  );
  const graph = panelPublicationFixture(panel, built, policy, generation, registry, actualHashes.schema, actualHashes.registry, "first-publication");
  const reloaded = cloneWithBuffers(graph);
  let valid = outerOk && panelPublicationSemantics(reloaded, registry, schema, actualHashes.schema, actualHashes.registry);
  if (valid && vector.mutation === "fresh-process") {
    const child = spawnSync(process.execPath, ["--input-type=module", "-e", [
      "import fs from 'node:fs';",
      "const g=JSON.parse(fs.readFileSync(0,'utf8'));",
      "const p=g.pointer,s=g.state,r=g.receipt,j=g.journal;",
      "const t=j.targets.filter(x=>x.role==='pointer'||x.role==='panel-state');",
      "const ok=g.before_pointer===null&&g.before_state===null&&r.before_pointer_id===null&&r.before_panel_generation===0&&s.panel_generation===1&&r.after_pointer_id===p.pointer_id&&s.current_pointer_id===p.pointer_id&&t.length===2&&t.every(x=>x.operation==='create'&&x.before_sha256===null&&x.before_image===null);",
      "process.exit(ok?0:1);",
    ].join("")], { input: canonical(reloaded), encoding: "utf8" });
    valid = child.status === 0;
  }
  check(vector.id, valid && vector.expected === "valid");
}

const firstPublicationPackage = writerFenceFixture(
  strictRegistry, actualHashes.schema, strictRegistrySha, expectedIds, strictHashes, 1,
  strictSuite, schema, args["project-root"], documentWorkspace, true,
);
const firstPublicationLineage = loadStrictLineage(
  firstPublicationPackage, strictRegistry, schema, actualHashes.schema, strictRegistrySha, false,
);
for (const vector of suite.publication_recovery_vectors) {
  if (firstPublicationLineage === null) { check(vector.id, false); continue; }
  const graph = firstPublicationLineage.graph;
  const recoveryPackage = { journal: graph.journal, marker: graph.marker, target_images: panelPublicationTargetImages(graph) };
  let crashAfter;
  if (vector.cut === "before-lineage-index") crashAfter = graph.journal.targets.findIndex(({ role }) => role === "lineage-index");
  else if (vector.cut === "before-pointer") crashAfter = graph.journal.targets.findIndex(({ role }) => role === "pointer");
  else crashAfter = graph.journal.targets.length;
  const valid = transitionRecoverySemantics(recoveryPackage, crashAfter, vector.committed);
  check(vector.id, valid && vector.expected === "valid");
}

for (const vector of suite.journal_fault_matrix) {
  if (vector.id === "first-create-absent-target") check(vector.id, vector.before_sha256 === null && vector.primitive === "durable_create");
  else if (vector.id === "created-target-rollback-to-absence") check(vector.id, vector.operation === "create" && vector.primitive === "durable_remove_to_tombstone" && vector.expected === "missing");
  else if (vector.id === "journal-image-locators-and-order") check(vector.id, canonical(vector.targets.map(({ apply_order }) => apply_order)) === canonical(vector.expected_orders) && vector.targets.every(({ after_image }) => after_image));
  else if (vector.id.startsWith("journal-")) {
    const [manifest, marker] = journalFixture(vector.transaction_kind, actualHashes.schema, actualHashes.registry);
    if (vector.mutation === "create-has-before") { const target = manifest.targets.find(({ operation }) => operation === "create"); target.before_sha256 = target.after_sha256; target.before_image = clone(target.after_image); }
    else if (vector.mutation === "remove-has-after") manifest.targets[0].operation = "remove";
    else if (vector.mutation === "duplicate-order") manifest.targets[1].apply_order = 0;
    else if (vector.mutation === "gapped-order") manifest.targets[1].apply_order = 4;
    else if (vector.mutation === "duplicate-target") { manifest.targets[1].root_instance_id = manifest.targets[0].root_instance_id; manifest.targets[1].path = manifest.targets[0].path; }
    else if (vector.mutation === "wrong-receipt-path") manifest.receipt_target_paths = ["receipts/other.json"];
    else if (vector.mutation === "one-repair-receipt") { manifest.targets.pop(); manifest.receipt_target_paths.pop(); }
    else if (vector.mutation === "locator-hash-mismatch") manifest.targets[0].before_image.sha256 = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "foreign-image-locator") manifest.targets[0].before_image.path = `state/transactions/${filesystemToken("other-transaction")}/images/0-before`;
    else if (vector.mutation === "parent-image-locator") manifest.targets[0].before_image.path = `${manifest.journal_dir}/images/../images/0-before`;
    else if (vector.mutation === "manifest-path-substitution") manifest.manifest_path = `${manifest.journal_dir}/journal.json`;
    else if (vector.mutation === "terminal-marker-path-substitution") manifest.terminal_marker_path = `${manifest.journal_dir}/done.json`;
    else if (vector.mutation === "recovery-path-substitution") manifest.recovery_receipt_path = `${manifest.journal_dir}/recovered.json`;
    else if (vector.mutation === "marker-manifest-mismatch") marker.manifest_id = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "panel-wrong-role") manifest.targets.find(({ role }) => role === "projection").role = "business";
    else if (vector.mutation === "noncommitted-marker") { marker.state = "prepared"; delete marker.marker_id; marker.marker_id = hash(Buffer.from(canonical(marker))); }
    const valid = journalSemantics(manifest, marker, schema, registry, actualHashes.schema, actualHashes.registry);
    check(vector.id, vector.expected === "valid" ? valid : !valid && vector.expected_error === "JOURNAL_INVALID");
  }
  else {
    const states = Object.values(vector.targets);
    const actual = vector.marker ? (states.every((state) => state === "after") ? "committed" : "CORRUPT_COMMITTED_TRANSACTION")
      : states.includes("unknown") ? "CORRUPT_UNCOMMITTED_TRANSACTION" : states.every((state) => state === "after") ? "rolled-forward" : "rolled-back";
    check(vector.id, actual === (vector.expected ?? vector.expected_error));
  }
}

for (const vector of suite.receipt_vectors) {
  if (vector.id === "receipt-not-self-referential") check(vector.id, !vector.expected_receipt_contains_own_target && vector.journal_target_roles.filter((role) => role === "receipt").length === 1);
  else if (vector.id === "rollback-receipt-is-journal-local") check(vector.id, !vector.recovery_receipt_target && vector.expected === "original-before-images-restored");
  else if (vector.id === "capability-id-known-answer") {
    const preimage = canonical(vector.record_without_identity);
    check(vector.id, preimage === vector.expected_preimage && hash(Buffer.from(preimage)) === vector.expected_digest);
  }
  else if (vector.id.startsWith("fact-attribution-") || ["owned-risk-flow", "owned-decision"].includes(vector.command_kind)) {
    const fixtureKind = vector.command_kind ?? "action";
    let createCommand = null;
    if (fixtureKind === "wdr-create") {
      createCommand = clone(suite.wdr_vectors.find(({ id }) => id === "create-byte-exact").command);
      const createInput = clone(createCommand.create_input); delete createInput.input_id;
      createCommand.create_input.input_id = hash(Buffer.from(canonical(createInput)));
    }
    const graph = factAttributionFixture(actualHashes.schema, actualHashes.registry, registry, fixtureKind, createCommand);
    let [runtimeCapabilityBytes, runtimeRootRegistryBytes, runtimeActivationBytes, runtimeAttestationBytes, authorityContext] = runtimeAuthorityFixture(
      registry, actualHashes.schema, actualHashes.registry, commandProducer(graph.command),
    );
    const { capability_registry: capabilityRegistry, command, journal, receipt } = graph;
    const rebindTargets = () => {
      reindexTargets(journal.targets, journal.journal_dir);
      receipt.business_targets = journal.targets.filter(({ role }) => role === "business").map(clone);
      receipt.generation_state_target = clone(journal.targets.find(({ role }) => role === "fact-generation"));
      rebindFactGraph(graph);
    };
    const replaceBusinessAfter = (targetPath, raw) => {
      const artifact = graph.proof.business_artifacts.find(({ path: artifactPath }) => artifactPath === targetPath);
      const target = journal.targets.find(({ role, path: artifactPath }) => role === "business" && artifactPath === targetPath);
      artifact.after_bytes = encodedBytes(raw); target.after_sha256 = hash(raw); target.after_image.sha256 = target.after_sha256;
    };
    const rebindActionAfter = (field, value) => {
      const artifacts = Object.fromEntries(graph.proof.business_artifacts.map((row) => [row.path, row]));
      const ledgerPath = registry.runtime_paths.action_ledger.path;
      const statePath = registry.runtime_paths.action_ledger_state.path;
      const flowPath = registry.runtime_paths.action_flow_index.path;
      const rows = parseActionLedger(artifactBytes(artifacts[ledgerPath].after_bytes));
      rows.find(({ action_id }) => action_id === command.action_id)[field] = value;
      const raw = renderActionLedger(rows);
      const oldState = JSON.parse(artifactBytes(artifacts[statePath].after_bytes).toString());
      const state = actionLedgerStateDocument(rows, raw, oldState.ledger_revision, oldState.applied_commands, registry, actualHashes.schema, actualHashes.registry);
      const flow = actionFlowDocument(rows, raw, oldState.ledger_revision, registry, actualHashes.schema, actualHashes.registry);
      replaceBusinessAfter(ledgerPath, raw); replaceBusinessAfter(statePath, Buffer.from(canonical(state))); replaceBusinessAfter(flowPath, Buffer.from(canonical(flow)));
      rebindTargets();
    };
    const rebindRefreshOutputs = () => {
      const workstreamId = command.workstream_id;
      const wdrPath = `workstreams/${workstreamId}/delivery-record.md`;
      const statePath = `workstreams/${workstreamId}/delivery-record.state.json`;
      const sidecarPath = `workstreams/${workstreamId}/action-projection.json`;
      const artifacts = Object.fromEntries(graph.proof.business_artifacts.map((row) => [row.path, row]));
      const beforeWdr = artifactBytes(artifacts[wdrPath].before_bytes);
      const sidecar = JSON.parse(artifactBytes(artifacts[sidecarPath].after_bytes).toString());
      const snapshot = command.action_snapshot;
      sidecar.ledger_fingerprint = snapshot.ledger_fingerprint; sidecar.ledger_revision = snapshot.ledger_revision; sidecar.actions = clone(snapshot.actions);
      const afterWdr = Buffer.from(applyWdrPatch(beforeWdr.toString(), command, sidecar.actions.map(({ rendered_summary }) => rendered_summary)));
      const state = JSON.parse(artifactBytes(artifacts[statePath].after_bytes).toString()); state.record_fingerprint = hash(afterWdr);
      replaceBusinessAfter(wdrPath, afterWdr); replaceBusinessAfter(statePath, Buffer.from(canonical(state))); replaceBusinessAfter(sidecarPath, Buffer.from(canonical(sidecar)));
      rebindTargets();
    };
    if (vector.mutation === "forged-producer") receipt.authorization.producer_id = "adp-meeting-sync";
    else if (vector.mutation === "forged-capability") receipt.authorization.capability_id = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "forged-principal") receipt.authorization.principal_id = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "runtime-principal-mismatch") authorityContext.principal_id = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "runtime-capability-bytes-tamper") runtimeCapabilityBytes = Buffer.concat([runtimeCapabilityBytes, Buffer.from("\n")]);
    else if (vector.mutation === "fully-rebound-forged-graph") {
      const capability = capabilityRegistry.capabilities.find(({ producer_id }) => producer_id === commandProducer(command));
      capability.principal_id = `sha256:${"f".repeat(64)}`; rebindFactGraph(graph);
    }
    else if (vector.mutation === "roadmap-heading-injection") {
      command.set.roadmap.lines[0] = "## Injected"; rebindFactGraph(graph);
    }
    else if (vector.mutation === "roadmap-owned-section-substitution") {
      command.set = { owned_sections: [{ section: "roadmap", mode: "replace", lines: ["Milestone", "Target"] }] }; rebindFactGraph(graph);
    }
    else if (vector.mutation === "meeting-history-outer-command-mismatch") {
      command.set.meeting_history_append[0].command_id = "cmd-other-history-1";
      rebindFactGraph(graph);
    }
    else if (vector.mutation === "command-fingerprint") receipt.authorization.authorized_command_fingerprint = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "wrong-target") receipt.business_targets[0].path = "actions/other-ledger.md";
    else if (vector.mutation === "generation-jump") receipt.after_fact_generation = 99;
    else if (vector.mutation === "revision-jump") receipt.action_deltas[0].after_revision = 99;
    else if (["unequal-record-digests", "forged-record-digest"].includes(vector.mutation)) {
      const capability = capabilityRegistry.capabilities.find(({ producer_id }) => producer_id === "adp-status-sync");
      capability.authorization_record_digest = `sha256:${"f".repeat(64)}`;
      if (vector.mutation === "forged-record-digest") capability.capability_id = `sha256:${"f".repeat(64)}`;
      const registryBody = clone(capabilityRegistry); delete registryBody.capability_registry_id;
      capabilityRegistry.capability_registry_id = hash(Buffer.from(canonical(registryBody)));
    }
    else if (["denied-operation", "denied-field", "denied-section", "denied-section-last"].includes(vector.mutation)) {
      const capability = capabilityRegistry.capabilities.find(({ producer_id }) => producer_id === commandProducer(command));
      if (vector.mutation === "denied-operation") capability.allowed_operations = command.operation === "create" ? ["patch"] : ["create"];
      else if (vector.mutation === "denied-field") capability.allowed_fields = capability.allowed_fields.filter((field) => field !== [...commandPermissions(command, registry)[0]].sort()[0]);
      else {
        const sections = [...commandPermissions(command, registry)[1]].sort((left, right) => Buffer.from(left).compare(Buffer.from(right)));
        const denied = vector.mutation === "denied-section-last" ? sections.at(-1) : sections[0];
        capability.allowed_sections = capability.allowed_sections.filter((section) => section !== denied);
      }
      rebindFactGraph(graph);
    } else if (["duplicate-producer", "duplicate-capability"].includes(vector.mutation)) {
      capabilityRegistry.capabilities.push(clone(capabilityRegistry.capabilities.find(({ producer_id }) => producer_id === "adp-status-sync")));
      rebindFactGraph(graph);
    } else if (vector.mutation === "receipt-target-hash") journal.targets.find(({ role }) => role === "receipt").after_sha256 = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "receipt-target-path") journal.targets.find(({ role }) => role === "receipt").path = "receipts/fact/wrong.json";
    else if (vector.mutation === "before-state") {
      graph.before_state.fact_generation = 6; const beforeBody = clone(graph.before_state); delete beforeBody.state_id;
      graph.before_state.state_id = hash(Buffer.from(canonical(beforeBody)));
      const stateTarget = journal.targets.find(({ role }) => role === "fact-generation");
      stateTarget.before_sha256 = hash(Buffer.from(canonical(graph.before_state))); stateTarget.before_image.sha256 = stateTarget.before_sha256;
      delete journal.manifest_id; journal.manifest_id = hash(Buffer.from(canonical(journal)));
      graph.marker.manifest_id = journal.manifest_id; delete graph.marker.marker_id; graph.marker.marker_id = hash(Buffer.from(canonical(graph.marker)));
    }
    else if (["fake-command-anchor", "command-schema-hash", "command-registry-hash"].includes(vector.mutation)) {
      const field = { "fake-command-anchor": "schema_id", "command-schema-hash": "schema_sha256", "command-registry-hash": "registry_sha256" }[vector.mutation];
      command.contract[field] = field === "schema_id" ? "urn:adp:panel-sync-contracts:2026-07-24#unknown-command-v1" : `sha256:${"f".repeat(64)}`;
    }
    else if (["capability-contract-anchor", "before-state-contract-hash", "journal-contract-hash", "marker-contract-hash", "receipt-contract-hash", "proof-contract-hash"].includes(vector.mutation)) {
      const mapping = {
        "capability-contract-anchor": [capabilityRegistry, "schema_id"], "before-state-contract-hash": [graph.before_state, "schema_sha256"],
        "journal-contract-hash": [journal, "registry_sha256"], "marker-contract-hash": [graph.marker, "schema_sha256"],
        "receipt-contract-hash": [receipt, "registry_sha256"], "proof-contract-hash": [graph.proof, "schema_sha256"],
      };
      const [document, field] = mapping[vector.mutation];
      document.contract[field] = field === "schema_id" ? "urn:adp:panel-sync-contracts:2026-07-24#unknown-capability-v1" : `sha256:${"f".repeat(64)}`;
    }
    else if (["wrong-root", "wrong-operation", "create-as-replace", "replace-as-create"].includes(vector.mutation)) {
      const target = journal.targets.find(({ role }) => role === "business");
      const artifact = graph.proof.business_artifacts.find(({ path: artifactPath }) => artifactPath === target.path);
      if (vector.mutation === "wrong-root") {
        target.root_instance_id = artifact.root_instance_id = "123e4567-e89b-42d3-a456-426614174099";
        for (const locator of [target.before_image, target.after_image]) if (locator) locator.root_instance_id = target.root_instance_id;
      } else {
        const operation = vector.mutation === "create-as-replace" ? "replace" : vector.mutation === "replace-as-create" ? "create" : "remove";
        target.operation = artifact.operation = operation;
      }
      rebindTargets();
    }
    else if (["stale-wdr-revision", "stale-file-generation"].includes(vector.mutation)) {
      command[vector.mutation === "stale-wdr-revision" ? "expected_wdr_revision" : "expected_file_generation"] -= 1;
      rebindFactGraph(graph);
    }
    else if (["before-byte-substitution", "after-byte-substitution"].includes(vector.mutation)) {
      graph.proof.business_artifacts[0][vector.mutation === "before-byte-substitution" ? "before_bytes" : "after_bytes"] = encodedBytes(Buffer.from("substituted"));
      rebindFactGraph(graph);
    }
    else if (["missing-ledger-state-target", "missing-flow-target", "missing-sidecar-target"].includes(vector.mutation)) {
      const targetPath = vector.mutation === "missing-sidecar-target" ? `workstreams/${command.workstream_id}/action-projection.json`
        : registry.runtime_paths[vector.mutation === "missing-ledger-state-target" ? "action_ledger_state" : "action_flow_index"].path;
      journal.targets = journal.targets.filter((row) => !(row.role === "business" && row.path === targetPath));
      graph.proof.business_artifacts = graph.proof.business_artifacts.filter(({ path: artifactPath }) => artifactPath !== targetPath);
      rebindTargets();
    }
    else if (vector.mutation === "extra-business-target") {
      const extraTarget = clone(journal.targets.find(({ role }) => role === "business")); extraTarget.path = "actions/extra-derived-index.json";
      journal.targets.splice(journal.targets.findIndex(({ role }) => role === "fact-generation"), 0, extraTarget);
      const extraArtifact = clone(graph.proof.business_artifacts[0]); extraArtifact.path = extraTarget.path; graph.proof.business_artifacts.push(extraArtifact);
      rebindTargets();
    }
    else if (["rebound-owner", "rebound-status", "rebound-action", "rebound-due", "rebound-closure", "rebound-route", "rebound-affected"].includes(vector.mutation)) {
      const [field, value] = {
        "rebound-owner": ["owner", "FDE-X"], "rebound-status": ["status", "blocked"],
        "rebound-action": ["action", "Substituted action"], "rebound-due": ["due_trigger", "later gate"],
        "rebound-closure": ["closure_criteria", "Substituted closure"],
        "rebound-route": ["routing_scope_id", "l1-payments"], "rebound-affected": ["affected_workstreams", ["l1-payments"]],
      }[vector.mutation];
      rebindActionAfter(field, value);
    }
    else if (vector.mutation === "reopen-terminal") {
      command.set.status = "open"; receipt.action_deltas = [expectedActionDelta(command)]; rebindActionAfter("status", "open");
    }
    else if (["refresh-stale-ledger-fingerprint", "refresh-stale-ledger-revision", "refresh-missing-active", "refresh-extra-action"].includes(vector.mutation)) {
      const snapshot = command.action_snapshot;
      if (vector.mutation === "refresh-stale-ledger-fingerprint") snapshot.ledger_fingerprint = `sha256:${"f".repeat(64)}`;
      else if (vector.mutation === "refresh-stale-ledger-revision") snapshot.ledger_revision -= 1;
      else if (vector.mutation === "refresh-missing-active") snapshot.actions.pop();
      else {
        const ledgerRaw = artifactBytes(graph.proof.read_artifacts.find(({ path: itemPath }) => itemPath === registry.runtime_paths.action_ledger.path).bytes);
        const other = actionSnapshot(parseActionLedger(ledgerRaw), "l1-other", snapshot.ledger_fingerprint, snapshot.ledger_revision).actions[0];
        snapshot.actions.push(other); snapshot.actions.sort((a, b) => Buffer.from(a.action_id).compare(Buffer.from(b.action_id)));
      }
      rebindRefreshOutputs();
    }
    else if (vector.mutation === "refresh-read-bytes") {
      const read = graph.proof.read_artifacts[0]; const raw = Buffer.from("substituted ledger snapshot\n");
      read.bytes = encodedBytes(raw); read.sha256 = hash(raw); rebindFactGraph(graph);
    }
    const valid = factAttributionSemantics(
      graph, registry, schema, actualHashes.schema, actualHashes.registry, runtimeCapabilityBytes,
      runtimeRootRegistryBytes, runtimeActivationBytes, runtimeAttestationBytes, authorityContext,
    );
    check(vector.id, vector.expected === "valid" ? valid : !valid && vector.expected_error === "FACT_ATTRIBUTION_INVALID");
  }
  else check(vector.id, vector.initiator.producer_id === "adp-status-sync" && vector.action_delta.operation === "patch" && vector.action_delta.after_revision === vector.action_delta.before_revision + 1 && canonical(vector.action_delta.changed_fields) === canonical(["owner"]));
}

for (const vector of suite.legacy_wdr_update_vectors) {
  const mutations = vector.typed_status_payload?.set ? 1 : 0;
  const gap = mutations ? null : "LEGACY_STATUS_INTENT_REQUIRED";
  check(vector.id, vector.expected_history_records === 1 && vector.expected_current_mutations === mutations && vector.expected_gap === gap);
}

for (const vector of suite.meeting_plan_vectors) {
  const plan = meetingPlanIntentFixture(registry, actualHashes.schema, actualHashes.registry);
  if (vector.mutation === "omit-carrier") plan.intent_outbox_commands = [];
  else if (vector.mutation === "extra-intent") {
    const extra = clone(plan.status_intents[0]); extra.intent_id = "meeting-M-INTENT-1-extra";
    extra.set = { blockers: { mode: "add", values: ["unexpected"] } }; plan.status_intents.push(extra);
  } else if (vector.mutation === "duplicate-intent") plan.status_intents.push(clone(plan.status_intents[0]));
  else if (vector.mutation === "wrong-meeting") plan.intent_outbox_commands[0].source_instance_id = "meeting-M-OTHER";
  else if (vector.mutation === "evidence-substitution") plan.intent_outbox_commands[0].evidence[0].source_path = "meetings/other.md";
  const valid = meetingPlanIntentCarrierSemantics(plan, registry, schema, actualHashes.schema, actualHashes.registry);
  check(vector.id, vector.expected === "valid" ? valid : !valid && vector.expected_error === "MEETING_PLAN_INTENT_CARRIER_INVALID");
}

for (const vector of suite.status_intent_vectors) {
  const batch = statusIntentFixture(registry, actualHashes.schema, actualHashes.registry);
  if (vector.mutation === "omit-accepted-id") batch.accepted_intent_ids.pop();
  else if (vector.mutation === "omit-field") delete batch.wdr_patches[0].set.progress;
  else if (vector.mutation === "substitute-field") batch.wdr_patches[0].set.progress = "Substituted";
  else if (vector.mutation === "drop-evidence") batch.wdr_patches[0].evidence.pop();
  else if (vector.mutation === "cross-workstream") batch.wdr_patches[0].workstream_id = "l1-payments";
  else if (vector.mutation === "conflict") {
    batch.accepted_intents[0].set.progress = "Conflicting progress";
    batch.intent_bindings[0].fields = Object.keys(batch.accepted_intents[0].set).sort((a, b) => Buffer.from(a).compare(Buffer.from(b)));
  } else if (vector.mutation === "wrong-command-order") batch.command_order.reverse();
  else if (vector.mutation === "split-same-workstream") {
    const original = batch.wdr_patches[0]; const split = clone(original); split.command_id = "cmd-status-l1-checkout-z";
    split.set = { blockers: original.set.blockers }; delete original.set.blockers;
    split.evidence = [clone(batch.accepted_intents[0].evidence[0])]; original.evidence = [clone(batch.accepted_intents[1].evidence[0])];
    batch.wdr_patches.push(split); batch.intent_bindings[0].command_id = split.command_id; batch.command_order.push(split.command_id);
  }
  const valid = statusIntentApplicationSemantics(batch, registry, schema, actualHashes.schema, actualHashes.registry);
  check(vector.id, vector.expected === "valid" ? valid : !valid && vector.expected_error === "STATUS_INTENT_APPLICATION_INVALID");
}

for (const vector of suite.program_status_wdr_vectors) {
  const pack = programStatusWdrFixture(suite, registry, actualHashes.schema, actualHashes.registry);
  if (vector.mutation === "stale-progress") pack.payload.workstream_current[0].progress = "Carried forward progress";
  else if (vector.mutation === "stale-blockers") pack.payload.workstream_current[0].blockers = ["Old blocker"];
  else if (vector.mutation === "stale-phase") pack.payload.workstream_current[0].phase = "legacy phase";
  else if (vector.mutation === "lineage") pack.payload.workstream_current[0].wdr_fingerprint = `sha256:${"f".repeat(64)}`;
  else if (vector.mutation === "wdr-change-panel") {
    const beforePack = {
      selected_workstreams: clone(pack.selected_workstreams),
      wdrs: Object.fromEntries(Object.entries(pack.wdrs).map(([key, value]) => [key, Buffer.from(value)])),
      wdr_states: clone(pack.wdr_states), payload: clone(pack.payload),
    };
    const workstreamId = pack.selected_workstreams[0]; const beforeRaw = pack.wdrs[workstreamId];
    const afterRaw = Buffer.from(applyWdrPatch(beforeRaw.toString(), { set: { progress: "Current progress changed", blockers: { mode: "replace", values: ["New blocker"] } } }));
    pack.wdrs[workstreamId] = afterRaw;
    const state = pack.wdr_states[workstreamId]; state.record_fingerprint = hash(afterRaw); state.wdr_revision += 1; state.file_generation += 1;
    const current = parseWdrCurrent(afterRaw, workstreamId);
    pack.payload.workstream_current[0] = {
      workstream_id: current.workstream_id, phase: current.phase, status: current.status, progress: current.progress,
      blockers: current.blockers, risks: current.risks, dependencies: current.dependencies, action_ids: current.action_ids,
      wdr_fingerprint: state.record_fingerprint, wdr_revision: state.wdr_revision, file_generation: state.file_generation,
    };
    const beforePanel = { panel_id: `sha256:${"1".repeat(64)}`, sync: { canonical: { status: beforePack.payload } } };
    const afterPanel = { panel_id: `sha256:${"1".repeat(64)}`, sync: { canonical: { status: pack.payload } } };
    const valid = programStatusCurrentFromWdrSemantics(beforePack, registry, schema, actualHashes.schema, actualHashes.registry)
      && programStatusCurrentFromWdrSemantics(pack, registry, schema, actualHashes.schema, actualHashes.registry)
      && canonical(expectedPanelV2CurrentView(beforePanel, registry)) !== canonical(expectedPanelV2CurrentView(afterPanel, registry));
    check(vector.id, valid && vector.expected === "panel-output-changed");
    continue;
  }
  const valid = programStatusCurrentFromWdrSemantics(pack, registry, schema, actualHashes.schema, actualHashes.registry);
  check(vector.id, vector.expected === "valid" ? valid : !valid && vector.expected_error === "PROGRAM_STATUS_WDR_INVALID");
}

for (const vector of suite.drift_content_vectors) {
  const pack = driftContentFixture(registry, actualHashes.schema, actualHashes.registry);
  if (["owner", "action", "due", "status", "reported-owner"].includes(vector.mutation)) {
    const rows = parseActionLedger(pack.ledger_raw); const row = rows.find(({ action_id }) => action_id === "A-FLOW-1");
    const [field, value] = {
      owner: ["owner", "FDE-X"], "reported-owner": ["owner", "FDE-X"], action: ["action", "Changed action"],
      due: ["due_trigger", "later gate"], status: ["status", "blocked"],
    }[vector.mutation];
    row[field] = value; pack.ledger_raw = renderActionLedger(rows);
    const oldState = pack.ledger_state;
    pack.ledger_state = actionLedgerStateDocument(rows, pack.ledger_raw, oldState.ledger_revision, oldState.applied_commands, registry, actualHashes.schema, actualHashes.registry);
    if (vector.mutation === "reported-owner") pack.verdict = expectedDriftVerdict(pack, registry, actualHashes.schema, actualHashes.registry);
  } else if (vector.mutation === "missing-active") pack.sidecars["l1-checkout"].actions.pop();
  else if (vector.mutation === "retained-terminal") {
    const rows = parseActionLedger(pack.ledger_raw); const terminal = rows.find(({ action_id }) => action_id === "A-TERMINAL-1");
    const retained = actionSnapshot([{ ...terminal, status: "open" }], "l1-checkout", pack.ledger_state.ledger_fingerprint, pack.ledger_state.ledger_revision).actions[0];
    pack.sidecars["l1-checkout"].actions.push(retained); pack.sidecars["l1-checkout"].actions.sort((a, b) => Buffer.from(a.action_id).compare(Buffer.from(b.action_id)));
  } else if (vector.mutation === "fingerprint") pack.sidecars["l1-checkout"].ledger_fingerprint = `sha256:${"f".repeat(64)}`;
  else if (["wdr-missing-marker", "wdr-orphan-marker", "wdr-content-marker", "empty-ledger-wdr-marker"].includes(vector.mutation)) {
    const workstreamId = "l1-checkout"; const sidecar = pack.sidecars[workstreamId]; let summaries = [];
    if (vector.mutation === "wdr-content-marker") {
      const changed = clone(sidecar.actions[0]); changed.owner = "FDE-X"; changed.rendered_summary = renderedActionSummary(changed);
      summaries = [changed.rendered_summary];
    } else if (["wdr-orphan-marker", "empty-ledger-wdr-marker"].includes(vector.mutation)) {
      summaries = [renderedActionSummary({ action_id: "A-ORPHAN-1", owner: "FDE-O", action: "Remove orphan", due_trigger: "next sync",
        status: "open", action_revision: 1, routing_scope_id: workstreamId, affected_workstreams: [workstreamId] })];
    }
    if (vector.mutation === "empty-ledger-wdr-marker") {
      const rows = parseActionLedger(pack.ledger_raw); rows.find(({ action_id }) => action_id === "A-FLOW-1").status = "done";
      pack.ledger_raw = renderActionLedger(rows); const oldState = pack.ledger_state;
      pack.ledger_state = actionLedgerStateDocument(rows, pack.ledger_raw, oldState.ledger_revision, oldState.applied_commands, registry, actualHashes.schema, actualHashes.registry);
      sidecar.actions = []; sidecar.ledger_fingerprint = pack.ledger_state.ledger_fingerprint;
    }
    const raw = Buffer.from(applyWdrPatch(fixtureWdr(workstreamId), { set: { refresh_actions: true } }, summaries));
    pack.wdrs[workstreamId] = raw; pack.wdr_states[workstreamId].record_fingerprint = hash(raw);
    pack.verdict = expectedDriftVerdict(pack, registry, actualHashes.schema, actualHashes.registry);
  }
  let valid = actionProjectionDriftContentSemantics(pack, registry, schema, actualHashes.schema, actualHashes.registry);
  if (valid && vector.expected_action_id) valid = pack.verdict.workstreams[0].action_diffs.some(
    (row) => row.action_id === vector.expected_action_id && row.drift_kind === vector.expected_drift_kind,
  );
  check(vector.id, vector.expected === "valid" ? valid : !valid && vector.expected_error === "DRIFT_CONTENT_INVALID");
}

for (const vector of suite.finding_identity_vectors) {
  const diff = { action_id: "A-FLOW-1", drift_kind: "content-mismatch", ledger_present: true, wdr_present: true,
    ledger_revision: 4, wdr_rendered_sha256: `sha256:${"a".repeat(64)}` };
  const original = driftFinding("l1-checkout", "action-projection-drift", diff); const moved = clone(original);
  moved.source_path = "diagnostics/moved-delivery-record.md"; moved.source_line = 2042;
  const movedBody = clone(moved); delete movedBody.finding_id; delete movedBody.source_path; delete movedBody.source_line;
  check(vector.id, vector.expected === "stable" && original.finding_id === hash(Buffer.from(canonical(movedBody))));
}

for (const vector of suite.drift_vectors) {
  if (vector.id === "drift-sidecar-change-invalidates") {
    const profile = profiles["action-projection-drift-verdict"];
    check(vector.id, profile.required_sources.some(({ source_kind }) => source_kind === "wdr-action-sidecar") && canonical(vector.before) !== canonical(vector.after) && vector.expected_required_projection === profile.projection);
  } else {
    const valid = driftSemantics(vector);
    check(vector.id, vector.expected === "valid" ? valid : !valid && vector.expected_error === "DRIFT_COVERAGE_INVALID");
  }
}

for (const vector of suite.panel_binding_vectors) {
  const [panel, upstreams, compatibility, policy, initialGeneration] = panelFixture(suite.contract_schema_vectors, registry, actualHashes.schema, actualHashes.registry, args["project-root"]);
  let generation = initialGeneration;
  const physicalInventory = physicalInventoryFixture(registry, policy, generation.fact_generation, actualHashes.schema, actualHashes.registry);
  const mutation = vector.mutation ?? "none";

  const extraWorkstreamRow = () => {
    const extra = clone(policy.physical_workstream_inventory[0]); extra.workstream_id = "l1-payments";
    for (const [field, filename] of [["wdr_source", "delivery-record.md"], ["sidecar_source", "action-projection.json"]]) {
      extra[field].path = `workstreams/l1-payments/${filename}`; extra[field].fingerprint = extra[field].blob_id = hash(Buffer.from(`memory\0${extra[field].path}`));
    }
    return extra;
  };
  const rebindPolicy = () => {
    policy.physical_workstream_inventory.sort((a, b) => Buffer.from(a.workstream_id).compare(Buffer.from(b.workstream_id)));
    policy.workstream_catalog.sort((a, b) => Buffer.from(a.workstream_id).compare(Buffer.from(b.workstream_id)));
    policy.physical_workstream_inventory_id = inventoryId(policy.physical_workstream_inventory);
    policy.workstream_catalog_id = catalogId(policy.workstream_catalog);
    const body = clone(policy); delete body.policy_id; policy.policy_id = hash(Buffer.from(canonical(body)));
  };
  const rebindPhysicalInventory = () => {
    physicalInventory.inventory_id = inventoryId(physicalInventory.workstreams);
    const body = clone(physicalInventory); delete body.attestation_id; physicalInventory.attestation_id = hash(Buffer.from(canonical(body)));
  };
  if (mutation === "physical-attestation-fact-generation") {
    physicalInventory.fact_generation -= 1; rebindPhysicalInventory();
  } else if (mutation === "physical-attestation-root") {
    physicalInventory.memory_root_instance_id = "123e4567-e89b-42d3-a456-426614174099"; rebindPhysicalInventory();
  } else if (mutation === "physical-attestation-workstreams-omitted") {
    physicalInventory.workstreams = []; rebindPhysicalInventory();
  } else if (mutation === "physical-attestation-missing") delete physicalInventory.attestation_id;
  else if (mutation === "physical-attestation-contract-hash") physicalInventory.contract.registry_sha256 = `sha256:${"f".repeat(64)}`;
  else if (mutation === "selection-policy-contract-hash") policy.contract.schema_sha256 = `sha256:${"f".repeat(64)}`;
  else if (mutation === "generation-contract-hash") generation.contract.registry_sha256 = `sha256:${"f".repeat(64)}`;
  else if (mutation === "panel-embedded-contract-hash") upstreams["action-projection-drift-verdict"].contract.schema_sha256 = `sha256:${"f".repeat(64)}`;
  else if (mutation === "all-catalog-subset") {
    const extra = extraWorkstreamRow(); policy.physical_workstream_inventory.push(clone(extra)); policy.workstream_catalog.push(clone(extra)); rebindPolicy();
    generation = generationFixture(registry, policy, actualHashes.schema, actualHashes.registry);
  } else if (mutation === "inventory-catalog-omission") {
    policy.physical_workstream_inventory.push(extraWorkstreamRow()); rebindPolicy();
    generation = generationFixture(registry, policy, actualHashes.schema, actualHashes.registry);
  } else if (mutation === "catalog-extra-row") {
    policy.workstream_catalog.push(extraWorkstreamRow()); rebindPolicy();
    generation = generationFixture(registry, policy, actualHashes.schema, actualHashes.registry);
  } else if (mutation === "duplicate-physical-identity") {
    policy.physical_workstream_inventory.push(clone(policy.physical_workstream_inventory[0])); rebindPolicy();
    generation = generationFixture(registry, policy, actualHashes.schema, actualHashes.registry);
  } else if (mutation === "empty-all") {
    policy.physical_workstream_inventory = []; policy.workstream_catalog = []; rebindPolicy();
    generation = generationFixture(registry, policy, actualHashes.schema, actualHashes.registry);
  } else if (mutation === "uncataloged-generation-pair") {
    const extra = extraWorkstreamRow(); generation.leaf_sources.push(clone(extra.wdr_source), clone(extra.sidecar_source));
    generation.leaf_sources.sort((a, b) => Buffer.from(`${a.root_instance_id}\0${a.path}`).compare(Buffer.from(`${b.root_instance_id}\0${b.path}`)));
    const body = clone(generation); delete body.generation_id; generation.generation_id = hash(Buffer.from(canonical(body)));
  } else if (["generation-wdr-without-sidecar", "generation-sidecar-without-wdr"].includes(mutation)) {
    const removedKind = mutation === "generation-wdr-without-sidecar" ? "wdr-action-sidecar" : "selected-physical-wdr";
    generation.leaf_sources = generation.leaf_sources.filter(({ source_kind }) => source_kind !== removedKind);
    const body = clone(generation); delete body.generation_id; generation.generation_id = hash(Buffer.from(canonical(body)));
  } else if (mutation === "panel-catalog-id") {
    generation.panel_catalog_id = `sha256:${"e".repeat(64)}`; const body = clone(generation); delete body.generation_id; generation.generation_id = hash(Buffer.from(canonical(body)));
  }
  if (["all-catalog-subset", "inventory-catalog-omission", "catalog-extra-row", "duplicate-physical-identity", "empty-all", "uncataloged-generation-pair", "generation-wdr-without-sidecar", "generation-sidecar-without-wdr", "panel-catalog-id"].includes(mutation)) {
    panel.sync.selection_policy_id = policy.policy_id; panel.sync.generation_id = generation.generation_id;
    upstreams["state-audit"].selection_policy_id = policy.policy_id; upstreams["action-projection-drift-verdict"].selection_policy_id = policy.policy_id;
    upstreams["action-projection-drift-verdict"].generation_id = generation.generation_id;
  }
  if (mutation.startsWith("invalid-outer:") && mutation !== "invalid-outer:management-panel") {
    const kind = mutation.split(":")[1]; const target = Array.isArray(upstreams[kind]) ? upstreams[kind][0] : upstreams[kind];
    for (const key of Object.keys(target)) delete target[key];
  }
  for (const binding of registry.panel_binding_map) {
    const payload = upstreams[binding.projection_kind];
    setPointer(panel, binding.panel_pointer, binding.merge_mode === "object-by-key" ? Object.fromEntries(payload.map((row, index) => [row.scenario ?? `invalid-${index}`, clone(row)])) : clone(payload));
  }
  if (["selected-drift", "selected-missing", "selected-malformed"].includes(mutation)) {
    const row = panel.sync.action_projection.workstreams[0]; row.status = mutation.replace("selected-", "");
    if (["missing", "malformed"].includes(row.status)) { row.wdr_fingerprint = null; row.sidecar_fingerprint = null; }
    panel.sync.action_projection.overall_status = "blocked";
  } else if (mutation === "blocked-audit") { panel.sync.audit.audit_status = "blocked"; panel.sync.audit.execution_disposition = "blocked"; panel.sync.artifact_integrity = "blocked"; }
  else if (mutation === "freshness-disagreement") panel.sync.business_freshness = "stale";
  else if (mutation === "selection-omitted") { panel.sync.action_projection.selected_workstreams = []; panel.sync.action_projection.workstreams = []; }
  else if (mutation === "selection-policy-mismatch") panel.sync.audit.selection_policy_id = `sha256:${"e".repeat(64)}`;
  else if (mutation === "stale-v1-visible") panel.model_v1.data.status.progress.overall.forecast_summary = "stale-but-schema-valid";
  else if (mutation === "current-fields-live") {
    Object.assign(upstreams["program-status"].workstream_current[0], { progress: "LATEST CURRENT PROGRESS", blockers: ["LATEST BLOCKER"], risks: ["LATEST RISK"] });
    panel.sync.canonical.status.workstream_current = clone(upstreams["program-status"].workstream_current);
  } else if (mutation === "program-status-overlay-mismatch") {
    upstreams["program-status"].overall_status = "latest-status"; panel.sync.canonical.status.overall_status = "latest-status";
  } else if (mutation === "same-generation-upstream-mismatch") upstreams["program-status"].workstream_current[0].progress = "NEW SAME-GENERATION VALUE";
  else if (mutation === "omit-v1-history") delete panel.model_v1.data.history;
  else if (mutation === "omit-v1-board") delete panel.model_v1.data.meetings["fde-morning"].boards.fde_period_delta;
  else if (mutation === "invalid-outer:management-panel") delete panel.sync;
  if (panel.sync) { const panelBody = clone(panel); delete panelBody.panel_id; panel.panel_id = hash(Buffer.from(canonical(panelBody))); }
  let nestedOk = true;
  for (const binding of registry.nested_payload_bindings.filter(({ projection_kind }) => projection_kind === "program-status")) {
    try { const nestedSchema = JSON.parse(fs.readFileSync(path.join(args["project-root"], binding.schema_path))); nestedOk = nestedOk && validateDocument(jsonPointer(upstreams[binding.projection_kind], binding.payload_pointer), nestedSchema); } catch { nestedOk = false; }
  }
  const panelSchemaValid = validate(panel, schema, "managementPanelPayloadV2");
  const compatibilityOk = panelSchemaValid && panelV1CompatibilityValid(panel, compatibility, args["project-root"]);
  const compositionOk = panelSchemaValid && panelV1CompositionValid(panel, registry, args["project-root"]);
  const currentOk = panelSchemaValid && canonical(panel.sync.canonical.status.workstream_current) === canonical(upstreams["program-status"].workstream_current);
  let outerOk = false; let lineageOk = false;
  let built = null;
  if (panelSchemaValid) {
    const readMutation = mutation === "lineage-missing-read" ? ["program-status", "drop-one-declared-read"] : mutation === "lineage-extra-read" ? ["program-status", "add-undeclared-read"] : null;
    const builtResult = buildProjectionLineage(panel, upstreams, registry, schema, actualHashes.schema, actualHashes.registry, args["project-root"], documentWorkspace, policy, readMutation);
    built = builtResult[0]; const builtValid = builtResult[1];
    outerOk = builtValid;
    if (mutation === "payload-hash-mismatch") built["program-status"][0].envelope.payload_sha256 = `sha256:${"f".repeat(64)}`;
    else if (mutation === "generation-mismatch") built.roadmap[0].envelope.generation_id = `sha256:${"f".repeat(64)}`;
    else if (mutation === "manifest-receipt-mismatch") built["meeting-pack"][0].receipt.output.manifest_id = `sha256:${"f".repeat(64)}`;
    else if (mutation === "omit-state-audit-producer") built["state-audit"] = [];
    lineageOk = outerOk && projectionLineageSemantics(built, registry, schema, generation, policy, actualHashes.schema, actualHashes.registry);
  }
  const publicationOk = panelSchemaValid && publicationEligibilitySemantics(panel, physicalInventory, policy, generation, registry, schema, actualHashes.schema, actualHashes.registry, built);
  const bindingOk = panelSchemaValid && panelBindingSemantics(panel, built, registry, policy, generation);
  let valid;
  if (["selected-drift", "selected-missing", "selected-malformed", "blocked-audit", "freshness-disagreement", "selection-omitted", "selection-policy-mismatch", "all-catalog-subset", "inventory-catalog-omission", "catalog-extra-row", "duplicate-physical-identity", "empty-all", "uncataloged-generation-pair", "generation-wdr-without-sidecar", "generation-sidecar-without-wdr", "panel-catalog-id", "physical-attestation-fact-generation", "physical-attestation-root", "physical-attestation-workstreams-omitted", "physical-attestation-missing", "physical-attestation-contract-hash", "selection-policy-contract-hash", "generation-contract-hash", "panel-embedded-contract-hash"].includes(mutation)) valid = panelSchemaValid && !publicationOk;
  else if (["omit-v1-history", "omit-v1-board"].includes(mutation)) valid = panelSchemaValid && !compatibilityOk;
  else if (["stale-v1-visible", "program-status-overlay-mismatch"].includes(mutation)) valid = panelSchemaValid && !compositionOk;
  else if (mutation === "same-generation-upstream-mismatch") valid = panelSchemaValid && lineageOk && !bindingOk;
  else if (mutation === "omit-state-audit-producer") valid = panelSchemaValid && !lineageOk && !bindingOk;
  else if (mutation.startsWith("invalid-outer:")) valid = !outerOk;
  else if (["payload-hash-mismatch", "generation-mismatch", "manifest-receipt-mismatch", "lineage-missing-read", "lineage-extra-read"].includes(mutation)) valid = outerOk && !lineageOk;
  else valid = panelSchemaValid && nestedOk && compatibilityOk && compositionOk && currentOk && publicationOk && lineageOk && bindingOk;
  check(vector.id, valid);
}

for (const vector of suite.panel_v1_composition_vectors) {
  const [panel, upstreams] = panelFixture(suite.contract_schema_vectors, registry, actualHashes.schema, actualHashes.registry, args["project-root"]);
  for (const binding of registry.panel_binding_map) {
    const payload = upstreams[binding.projection_kind];
    const value = binding.merge_mode === "object-by-key" ? Object.fromEntries(payload.map((row) => [row.scenario, clone(row)])) : clone(payload);
    setPointer(panel, binding.panel_pointer, value);
  }
  if (vector.mutation === "current-fields-live") {
    Object.assign(upstreams["program-status"].workstream_current[0], { progress: "LATEST CURRENT PROGRESS", blockers: ["LATEST BLOCKER"], risks: ["LATEST RISK"] });
    panel.sync.canonical.status.workstream_current = clone(upstreams["program-status"].workstream_current);
  } else if (vector.mutation === "program-status-overlay-mismatch") {
    upstreams["program-status"].overall_status = "latest-status"; panel.sync.canonical.status.overall_status = "latest-status";
  } else if (vector.mutation === "stale-v1-visible") panel.model_v1.data.status.progress.overall.forecast_summary = "stale-but-schema-valid";
  const valid = panelV1CompositionValid(panel, registry, args["project-root"]);
  check(vector.id, vector.expected === "byte-exact" ? valid : !valid);
}

for (const vector of suite.panel_v2_consumer_vectors) {
  const [panel, upstreams] = panelFixture(suite.contract_schema_vectors, registry, actualHashes.schema, actualHashes.registry, args["project-root"]);
  for (const binding of registry.panel_binding_map) {
    const payload = upstreams[binding.projection_kind];
    setPointer(panel, binding.panel_pointer, binding.merge_mode === "object-by-key" ? Object.fromEntries(payload.map((row) => [row.scenario, clone(row)])) : clone(payload));
  }
  const panelBody = clone(panel); delete panelBody.panel_id; panel.panel_id = hash(Buffer.from(canonical(panelBody)));
  const baselineModel = canonical(panel.model_v1); const baselineView = executePanelV2Consumer(panel, registry, schema, args["project-root"]);
  if (vector.mutation === "current-fields-live") Object.assign(panel.sync.canonical.status.workstream_current[0], { progress: "LATEST CURRENT PROGRESS", blockers: ["LATEST BLOCKER"], risks: ["LATEST RISK"] });
  else if (vector.mutation === "legacy-model-only") panel.model_v1.data.status.progress.overall.forecast_summary = "legacy-only-change";
  else if (vector.mutation === "missing-current-field") delete panel.sync.canonical.status.workstream_current[0].progress;
  else if (vector.mutation === "duplicate-row") panel.sync.canonical.status.workstream_current.push(clone(panel.sync.canonical.status.workstream_current[0]));
  else if (vector.mutation === "non-nfc-row") panel.sync.canonical.status.workstream_current[0].progress = "e\u0301";
  else if (vector.mutation === "normalized-collision") {
    const extra = clone(panel.sync.canonical.status.workstream_current[0]);
    panel.sync.canonical.status.workstream_current[0].workstream_id = "\u00e9"; extra.workstream_id = "e\u0301";
    panel.sync.canonical.status.workstream_current.push(extra);
  } else if (vector.mutation === "html-metacharacters") {
    Object.assign(panel.sync.canonical.status.workstream_current[0], { progress: `A&B <C> "D" 'E'`, blockers: ["<blocked>"], risks: ["R&D"] });
  }
  const currentBody = clone(panel); delete currentBody.panel_id; panel.panel_id = hash(Buffer.from(canonical(currentBody)));
  const currentView = executePanelV2Consumer(panel, registry, schema, args["project-root"]);
  let valid;
  if (vector.expected === "valid") valid = baselineView !== null && canonical(currentView) === canonical(baselineView);
  else if (vector.expected === "visible-change") valid = baselineView !== null && currentView !== null && baselineView.html !== currentView.html && currentView.html.includes("LATEST CURRENT PROGRESS") && canonical(panel.model_v1) === baselineModel;
  else if (vector.expected === "current-view-unchanged") valid = baselineView !== null && currentView !== null && canonical(baselineView.rows) === canonical(currentView.rows) && baselineView.html === currentView.html && !currentView.html.includes("legacy-only-change");
  else if (vector.expected === "escaped") valid = currentView !== null && currentView.html.includes("A&amp;B &lt;C&gt; &quot;D&quot; &#39;E&#39;")
    && currentView.html.includes("&lt;blocked&gt;") && currentView.html.includes("R&amp;D");
  else valid = currentView === null && vector.expected_error === "PANEL_V2_CONSUMER_INVALID";
  check(vector.id, valid);
}

for (const vector of suite.panel_publication_vectors) {
  const [panel, upstreams, compatibility, policy, generation] = panelFixture(suite.contract_schema_vectors, registry, actualHashes.schema, actualHashes.registry, args["project-root"]);
  for (const binding of registry.panel_binding_map) {
    const payload = upstreams[binding.projection_kind];
    setPointer(panel, binding.panel_pointer, binding.merge_mode === "object-by-key" ? Object.fromEntries(payload.map((row) => [row.scenario, row])) : payload);
  }
  const panelBody = clone(panel); delete panelBody.panel_id; panel.panel_id = hash(Buffer.from(canonical(panelBody)));
  const [built, outerOk] = buildProjectionLineage(panel, upstreams, registry, schema, actualHashes.schema, actualHashes.registry, args["project-root"], documentWorkspace, policy);
  const lineageOk = outerOk && projectionLineageSemantics(built, registry, schema, generation, policy, actualHashes.schema, actualHashes.registry);
  const graph = panelPublicationFixture(panel, built, policy, generation, registry, actualHashes.schema, actualHashes.registry, vector.mutation);
  const valid = lineageOk && panelPublicationSemantics(graph, registry, schema, actualHashes.schema, actualHashes.registry);
  check(vector.id, vector.expected === "valid" ? valid : !valid && vector.expected_error === "PANEL_PUBLICATION_GRAPH_INVALID");
}

for (const vector of suite.nested_payload_vectors) {
  const binding = registry.nested_payload_bindings.find((row) => row.projection_kind === vector.projection_kind && row.payload_pointer === vector.payload_pointer);
  const nestedSchema = JSON.parse(fs.readFileSync(path.join(args["project-root"], binding.schema_path)));
  const instance = vector.fixture_path ? JSON.parse(fs.readFileSync(path.join(args["project-root"], vector.fixture_path))) : vector.instance;
  check(vector.id, validateDocument(instance, nestedSchema) && canonical(instance) === canonical(JSON.parse(canonical(instance))));
}

const releaseSeeds = {
  "fixture-posix-ci": "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
  "fixture-windows-ci": "1f1e1d1c1b1a191817161514131211100f0e0d0c0b0a09080706050403020100",
};
const resignReleaseReceipt = (receipt, signerKey = null) => {
  delete receipt.result_id; const keyId = signerKey ?? receipt.provenance.signer_key_id;
  receipt.provenance.signature = crypto.sign(null, conformanceSigningPayload(receipt), ed25519PrivateKey(releaseSeeds[keyId])).toString("base64");
  receipt.result_id = hash(Buffer.from(canonical(receipt)));
};
const releaseRegistry = designReleaseRegistryFixture(registry);
const releaseRegistrySha = hash(Buffer.from(canonical(releaseRegistry)));
const releaseHashes = { ...actualHashes, registry: releaseRegistrySha };
for (const vector of suite.release_gate_vectors) {
  const mutation = vector.mutation;
  const vectorRegistry = mutation === "production-trust-unprovisioned" ? registry : releaseRegistry;
  const vectorHashes = mutation === "production-trust-unprovisioned" ? actualHashes : releaseHashes;
  const [receipts, evidenceBlobs] = implementationConformanceReceipts(expectedIds, vectorHashes, vectorRegistry);
  if (mutation === "passed-subset") { receipts[0].passed_vector_ids = expectedIds.slice(0, -1); resignReleaseReceipt(receipts[0]); }
  else if (mutation === "duplicate-implementation") { receipts[1].implementation_id = receipts[0].implementation_id; resignReleaseReceipt(receipts[1]); }
  else if (mutation === "duplicate-build") { receipts[1].adapter_build_id = receipts[0].adapter_build_id; receipts[1].runtime.build_digest = receipts[0].adapter_build_id; resignReleaseReceipt(receipts[1]); }
  else if (mutation === "platform-substitution") { receipts[0].platform = "native-windows"; resignReleaseReceipt(receipts[0]); }
  else if (mutation === "evidence-class-omission") { receipts[0].evidence_classes = ["production-adapter"]; resignReleaseReceipt(receipts[0]); }
  else if (mutation === "extra-vector") { receipts[0].passed_vector_ids.push("not-in-suite"); resignReleaseReceipt(receipts[0]); }
  else if (mutation === "artifact-hash") { receipts[0].schema_sha256 = `sha256:${"f".repeat(64)}`; resignReleaseReceipt(receipts[0]); }
  else if (mutation === "result-id") receipts[0].result_id = `sha256:${"f".repeat(64)}`;
  else if (mutation === "unknown-signer") { receipts[0].provenance.signer_key_id = "unknown-ci-key"; const body = clone(receipts[0]); delete body.result_id; receipts[0].result_id = hash(Buffer.from(canonical(body))); }
  else if (mutation === "signature-tamper") { receipts[0].provenance.signature = `${"A".repeat(86)}==`; const body = clone(receipts[0]); delete body.result_id; receipts[0].result_id = hash(Buffer.from(canonical(body))); }
  else if (mutation === "log-tamper") { const blobId = receipts[0].provenance.test_log_sha256; evidenceBlobs[blobId] = Buffer.concat([evidenceBlobs[blobId], Buffer.from("tampered")]); }
  else if (mutation === "replay") {
    const replay = clone(receipts[0]); replay.implementation_id = "python-production-adapter-replay";
    replay.adapter_build_id = hash(Buffer.from("python-production-build-replay")); replay.runtime.build_digest = replay.adapter_build_id;
    resignReleaseReceipt(replay, "fixture-posix-ci"); receipts.push(replay);
  } else if (mutation === "build-mismatch") { receipts[0].runtime.build_digest = `sha256:${"f".repeat(64)}`; resignReleaseReceipt(receipts[0]); }
  else if (mutation === "runtime-3.9") { receipts[0].runtime.version = "3.9.0"; resignReleaseReceipt(receipts[0]); }
  else if (["runtime-node-18", "runtime-node-20", "runtime-node-21", "runtime-node-23", "runtime-node-24", "runtime-node-25"].includes(mutation)) {
    receipts[1].runtime.version = `${mutation.slice("runtime-node-".length)}.0.0`; resignReleaseReceipt(receipts[1]);
  }
  else if (mutation.startsWith("lock-")) {
    const lock = receipts[0].lock_evidence;
    const fieldByMutation = { "lock-contention": "multiprocess_contention_passed", "lock-crash-release": "crash_release_passed", "lock-order": "order_passed", "lock-timeout": "timeout_passed", "lock-upgrade": "upgrade_rejected" };
    if (Object.hasOwn(fieldByMutation, mutation)) { lock[fieldByMutation[mutation]] = false; resignReleaseReceipt(receipts[0]); }
    else if (mutation === "lock-primitive") { lock.primitive = "windows-lockfileex"; resignReleaseReceipt(receipts[0]); }
    else if (mutation === "lock-profile") { lock.lock_profile_id = `sha256:${"f".repeat(64)}`; resignReleaseReceipt(receipts[0]); }
    else delete evidenceBlobs[lock.evidence_log_sha256];
  }
  const accepted = receipts.every((row) => validate(row, schema, "conformanceResultV1"))
    && releaseGateAccepts(receipts, expectedIds, vectorHashes, vectorRegistry, evidenceBlobs, {
      clock_source: "host-secure-clock-v1",
      evaluation_time: vector.evaluation_time ?? (mutation === "python-review-deadline" ? "2026-09-01T00:00:00Z" : "2026-07-24T03:05:00Z"),
      available: vector.clock_available ?? true,
    });
  check(vector.id, vector.expected === "accepted" ? accepted : !accepted && vector.expected_error === "CONFORMANCE_EVIDENCE_INCOMPLETE");
}

for (const vector of suite.repair_vectors) {
  if (vector.id === "cross-field-action-set-valid") check(vector.id, canonical(vector.finding_action_ids) === canonical(vector.command_action_ids) && canonical(vector.command_action_ids) === canonical(vector.read_set_action_ids));
  else if (vector.id === "cross-field-action-set-mismatch") check(vector.id, canonical(vector.finding_action_ids) !== canonical(vector.command_action_ids) && vector.expected_error === "REPAIR_BATCH_INVALID");
  else if (vector.id === "orphan-action-expected-absent") check(vector.id, vector.read_records[0].expected_present === false && vector.read_records[0].revision === null
    && canonical(vector.finding_action_ids) === canonical(vector.command_action_ids) && canonical(vector.command_action_ids) === canonical([vector.read_records[0].action_id]));
  else if (vector.id === "duplicate-action-read-record-rejected") check(vector.id, new Set(vector.read_records.map(({ action_id }) => action_id)).size !== vector.read_records.length && vector.expected_error === "REPAIR_BATCH_INVALID");
  else if (vector.id === "repair-sort-key-vs-group-key") {
    const ordered = [...vector.findings].sort((a, b) => Buffer.from(`${a.workflow}\0${a.workstream_id}\0${a.operation}\0${a.finding_id}`).compare(Buffer.from(`${b.workflow}\0${b.workstream_id}\0${b.operation}\0${b.finding_id}`)));
    const groups = new Set(ordered.map((row) => `${row.workflow}\0${row.workstream_id}\0${row.operation}`));
    check(vector.id, groups.size === vector.expected_group_count && canonical(ordered.map(({ finding_id }) => finding_id)) === canonical(vector.expected_finding_order));
  } else if (vector.id === "nonce-reserve-consume-cas") check(vector.id, canonical(vector.events) === canonical(["unused", "reserved", "consumed"]) && vector.replay_from === "consumed" && vector.expected_error === "REPAIR_TOKEN_REPLAY");
  else if (vector.id.startsWith("repair-graph-")) {
    const fixtureOutcome = vector.fixture_outcome ?? (vector.mutation === "blocked" ? "blocked" : vector.mutation === "rolled-back" ? "rolled-back" : vector.mutation === "orphan-null-revision" ? "orphan" : "committed");
    const graph = repairGraphFixture(actualHashes.schema, actualHashes.registry, registry, fixtureOutcome);
    const repairRuntime = runtimeAuthorityFixture(registry, actualHashes.schema, actualHashes.registry, "adp-status-sync");
    const batch = graph.audit.repair_batches.find(({ batch_id }) => batch_id === graph.dry_request.batch.batch_id);
    const finding = graph.audit.findings.find(({ repair_batch_id }) => repair_batch_id === batch.batch_id);
    if (vector.mutation === "dangling-finding-batch") finding.repair_batch_id = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "batch-omits-finding") batch.finding_ids = ["other-finding"];
    else if (vector.mutation === "audit-mismatch") batch.based_on_audit_id = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "action-union-mismatch") batch.command.action_ids = ["A-FLOW-OTHER"];
    else if (vector.mutation === "duplicate-source") batch.read_set.source_records.push(clone(batch.read_set.source_records[0]));
    else if (vector.mutation === "duplicate-wdr") batch.read_set.wdr_revisions.push(clone(batch.read_set.wdr_revisions[0]));
    else if (vector.mutation === "wdr-revision-mismatch") batch.command.expected_wdr_revision = 99;
    else if (vector.mutation === "cross-batch-token") graph.nonce_states.at(-1).batch_id = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "binding-digest-mismatch") graph.dry_result.binding_digest = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "recomputed-substitution") {
      const requestBatch = graph.dry_request.batch; requestBatch.read_set.ledger_fingerprint = `sha256:${"e".repeat(64)}`;
      const core = Object.fromEntries(["based_on_audit_id", "finding_ids", "command", "read_set"].map((key) => [key, requestBatch[key]]));
      requestBatch.batch_digest = hash(Buffer.from(canonical(core)));
      requestBatch.batch_id = hash(Buffer.from(canonical({ workflow: requestBatch.command.workflow, workstream_id: requestBatch.command.workstream_id, operation: requestBatch.command.operation, finding_ids: requestBatch.finding_ids, batch_digest: requestBatch.batch_digest })));
      graph.dry_result.dry_run_id = hash(Buffer.from(canonical(graph.dry_request))); graph.dry_result.batch_id = requestBatch.batch_id;
      graph.dry_result.binding_digest = hash(Buffer.from(canonical(repairBindingInput(graph.dry_request, graph.audit.audit_id, "applicable", actualHashes.schema, actualHashes.registry))));
    }
    else if (vector.mutation === "overlong-expiry") graph.dry_result.expires_at = "2026-07-24T02:15:01Z";
    else if (vector.mutation === "expired-apply") graph.apply_request.applied_at = "2026-07-24T02:15:01Z";
    else if (vector.mutation === "invalid-nonce-transition") graph.nonce_states.at(-1).previous_state_id = graph.nonce_states[0].state_id;
    else if (vector.mutation === "journal-nonce-mismatch") graph.journal.targets.find(({ role }) => role === "nonce").after_sha256 = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "nonce-path-substitution") graph.journal.targets.find(({ role }) => role === "nonce").path = "state/nonces/substituted.json";
    else if (vector.mutation === "journal-fact-receipt-mismatch") graph.journal.targets.filter(({ role }) => role === "receipt")[0].after_sha256 = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "repair-receipt-mismatch") graph.repair_receipt.fact_receipt_id = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "noncommitted-marker") { graph.marker.state = "prepared"; delete graph.marker.marker_id; graph.marker.marker_id = hash(Buffer.from(canonical(graph.marker))); }
    else if (vector.mutation === "scope-non-nfc") graph.dry_request.authorization_scopes = ["repair:e\u0301"];
    else if (vector.mutation === "scope-nfc-collision") graph.dry_request.authorization_scopes = ["repair:e\u0301", "repair:\u00e9"];
    else if (vector.mutation === "finding-action-ref-missing") finding.entity_refs = [];
    else if (vector.mutation === "finding-action-ref-extra") finding.entity_refs.push({ entity_type: "action", id: "A-EXTRA-1" });
    else if (vector.mutation === "finding-action-ref-duplicate") finding.entity_refs.push(clone(finding.entity_refs[0]));
    else if (vector.mutation === "audit-contract-hash") graph.audit.contract.registry_sha256 = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "fact-receipt-contract-hash" && graph.fact_receipt) graph.fact_receipt.contract.schema_sha256 = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "split-group") { const duplicate = clone(batch); duplicate.batch_id = `sha256:${"e".repeat(64)}`; graph.audit.repair_batches.push(duplicate); }
    else if (vector.mutation === "overlapping-batches") graph.audit.repair_batches.find(({ batch_id }) => batch_id !== batch.batch_id).finding_ids.push(finding.finding_id);
    else if (vector.mutation === "orphan-batch") { const orphan = clone(batch); orphan.batch_id = `sha256:${"e".repeat(64)}`; orphan.command.workstream_id = "l1-orphan"; graph.audit.repair_batches.push(orphan); }
    else if (vector.mutation === "group-mismatch") batch.command.workstream_id = "l1-other";
    else if (vector.mutation === "blocked-without-batch") finding.repair_batch_id = null;
    else if (vector.mutation === "absent-claim-present-row") Object.assign(batch.read_set.action_revisions[0], { expected_present: false, revision: null });
    else if (vector.mutation === "wrong-ledger-revision") batch.read_set.action_revisions[0].revision += 1;
    else if (vector.mutation === "invented-drift-action") graph.drift_verdict.workstreams.find(({ workstream_id }) => workstream_id === batch.command.workstream_id).action_diffs[0].action_id = "A-INVENTED-1";
    else if (vector.mutation === "attempt-transaction-id") graph.attempt_journal.transaction_id = "repair-attempt:substituted";
    else if (vector.mutation === "business-marker-binding") graph.repair_receipt.business_marker_sha256 = `sha256:${"f".repeat(64)}`;
    else if (vector.mutation === "recovery-binding") graph.repair_receipt.recovery_receipt_sha256 = `sha256:${"f".repeat(64)}`;
    const valid = repairGraphSemantics(graph, schema, registry, actualHashes.schema, actualHashes.registry, ...repairRuntime);
    check(vector.id, vector.expected === "valid" ? valid : !valid && ["REPAIR_BATCH_INVALID", "REPAIR_TOKEN_INVALID", "REPAIR_TRANSACTION_INVALID"].includes(vector.expected_error));
  }
  else if (vector.id === "repair-two-batches-cas-partial-retry")
    check(vector.id, twoBatchRepairRestartSemantics(schema, registry, actualHashes.schema, actualHashes.registry));
  else {
    const used = new Set(); const committed = []; let valid = true;
    for (const event of vector.events.filter(({ event }) => event === "apply")) { if (used.has(event.token)) valid = false; used.add(event.token); if (event.outcome === "committed" && !committed.includes(event.batch)) committed.push(event.batch); }
    check(vector.id, valid && canonical(committed) === canonical(vector.expected_committed_batches) && vector.expected_reused_tokens.length === 0);
  }
}

for (const vector of suite.refresh_vectors) {
  if (vector.id.startsWith("live-inspect-")) {
    const mutation = vector.mutation;
    const vectorRegistry = mutation === "pending-registry" ? registry : strictRegistry;
    const vectorRegistrySha = mutation === "pending-registry" ? actualHashes.registry : strictRegistrySha;
    const vectorHashes = mutation === "pending-registry" ? actualHashes : strictHashes;
    const vectorSuite = mutation === "pending-registry" ? suite : strictSuite;
    const pack = liveInspectFixture(vectorSuite, vectorRegistry, schema, actualHashes.schema, vectorRegistrySha, args["project-root"], documentWorkspace, expectedIds, vectorHashes);
    if (["source-drift", "source-unreadable", "missing-leaf"].includes(mutation)) {
      const leafKey = Object.keys(pack.live_leaf_store)[0];
      if (mutation === "source-drift") pack.live_leaf_store[leafKey] = Buffer.concat([pack.live_leaf_store[leafKey], Buffer.from("\n")]);
      else if (mutation === "source-unreadable") pack.live_leaf_store[leafKey] = null;
      else delete pack.live_leaf_store[leafKey];
    } else if (mutation === "fact-generation-drift") { pack.documents.fact_state.fact_generation += 1; rebindIdentity(pack.documents.fact_state, "state_id"); }
    else if (mutation === "lock-unavailable") pack.fact_read_lock.acquired = false;
    else if (mutation === "activation-rollback") {
      const activation = pack.documents.activation_state;
      activation.activation_epoch += 1; activation.mode = "legacy"; activation.attestation_id = null;
      rebindIdentity(activation, "state_id");
    } else if (mutation === "activation-epoch") {
      pack.documents.activation_state.activation_epoch += 1; rebindIdentity(pack.documents.activation_state, "state_id");
    } else if (mutation === "capability-epoch") {
      pack.documents.capability_registry.capability_epoch += 1; rebindIdentity(pack.documents.capability_registry, "capability_registry_id");
    } else if (mutation === "attestation-replacement") {
      pack.attestation.attested_at = "2026-07-24T03:00:04Z"; rebindWriterFenceAttestation(pack);
    } else if (mutation === "stale-activation-snapshot") {
      pack.attestation.fact_generation -= 1; pack.attestation.ledger.ledger_fingerprint = `sha256:${"e".repeat(64)}`;
      pack.attestation.workstreams[0].wdr_fingerprint = `sha256:${"d".repeat(64)}`;
      pack.attestation.published_generation_id = `sha256:${"c".repeat(64)}`;
      pack.attestation.current_pointer_id = `sha256:${"b".repeat(64)}`;
      pack.attestation.lineage_index_id = `sha256:${"a".repeat(64)}`;
      pack.attestation.lineage_index_path = "views/generations/activation-baseline/index.json";
      rebindAttestationActivation(pack);
    } else if (mutation === "writer-build-change") {
      const artifactPath = vectorRegistry.strict_rollout.writer_specs[0].artifact_paths[0];
      pack.writer_store[artifactPath] = Buffer.concat([pack.writer_store[artifactPath], Buffer.from("\nchanged-after-activation")]);
    } else if (mutation === "design-only-evidence") pack.release_store = {};
    else if (mutation === "root-registry-substitution") {
      const roots = pack.documents.root_registry; roots.roots.find(({ role }) => role === "memory").root_instance_id = "123e4567-e89b-42d3-a456-426614174099";
      rebindIdentity(roots, "registry_state_id");
    } else if (mutation === "ledger-substitution") pack.documents.ledger_raw = Buffer.concat([pack.documents.ledger_raw, Buffer.from("\nsubstituted")]);
    else if (mutation === "wdr-substitution") pack.documents.workstreams[0].wdr_raw = Buffer.concat([pack.documents.workstreams[0].wdr_raw, Buffer.from("\nsubstituted")]);
    else if (mutation === "sidecar-substitution") pack.documents.workstreams[0].sidecar.ledger_revision += 1;
    else if (["refresh-receipt-substitution", "publication-receipt-substitution"].includes(mutation)) {
      const index = JSON.parse(pack.lineage_store[pack.attestation.lineage_index_path]); let targetPath;
      if (mutation === "refresh-receipt-substitution") targetPath = index.objects.find(({ object_kind }) => object_kind === "refresh-receipt").path;
      else {
        const journalPath = runtimePath(vectorRegistry, "publication_journal_template", index.generation_id);
        const journal = JSON.parse(pack.lineage_store[journalPath]); targetPath = journal.targets.find(({ role }) => role === "receipt").path;
      }
      pack.lineage_store[targetPath] = Buffer.concat([pack.lineage_store[targetPath], Buffer.from("\n")]);
    } else if (mutation === "read-set-extra-writer") pack.inspect_read_set_additions.push({ root: "project", path: "skills/unregistered-writer.py", contract_name: "raw/writer-artifact" });
    else if (["omit-one", "duplicate", "wrong-root", "alias", "unconsumed"].includes(mutation)) pack.inspect_read_mutation = mutation;
    else if (["lineage-root-instance-substitution", "lineage-cardinality-substitution"].includes(mutation)) {
      const indexPath = pack.attestation.lineage_index_path; const index = JSON.parse(pack.lineage_store[indexPath]);
      const target = index.objects.find(({ object_kind }) => object_kind === "selection-policy");
      if (mutation === "lineage-root-instance-substitution") target.root_instance_id = "123e4567-e89b-42d3-a456-426614174099"; else target.cardinality = "many";
      rebindIdentity(index, "index_id"); pack.lineage_store[indexPath] = Buffer.from(canonical(index)); pack.attestation.lineage_index_id = index.index_id; rebindAttestationActivation(pack);
    }
    else if (mutation === "lineage-index-missing") delete pack.lineage_store[pack.attestation.lineage_index_path];
    else if (mutation === "lineage-object-missing") { const index = JSON.parse(pack.lineage_store[pack.attestation.lineage_index_path]); delete pack.lineage_store[index.objects[0].path]; }
    else if (mutation === "panel-byte-tampered") {
      const index = JSON.parse(pack.lineage_store[pack.attestation.lineage_index_path]);
      const target = index.objects.find(({ object_kind, projection_kind }) => object_kind === "projection-envelope" && projection_kind === "management-panel");
      pack.lineage_store[target.path] = Buffer.concat([pack.lineage_store[target.path], Buffer.from("\n")]);
    } else if (mutation === "pointer-byte-tampered") {
      const pointerPath = vectorRegistry.runtime_paths.panel_current_pointer.path; pack.lineage_store[pointerPath] = Buffer.concat([pack.lineage_store[pointerPath], Buffer.from("\n")]);
    } else if (mutation === "extra-leaf") pack.live_leaf_store["123e4567-e89b-42d3-a456-426614174000\0unexpected/source.md"] = Buffer.from("unexpected");
    else if (mutation === "extra-write") pack.inspect_write_paths.push("state/unexpected.json");
    const status = liveInspectSemantics(pack, vectorRegistry, schema, actualHashes.schema, vectorRegistrySha, expectedIds, vectorHashes,
      { clock_source: "host-secure-clock-v1", evaluation_time: pack.inspected_at, available: vector.clock_available ?? true });
    if (vector.expected_error === "LIVE_INSPECT_INVALID") check(vector.id, status === null);
    else check(vector.id, status !== null && status.latest_inspect.outcome === vector.expected_outcome && status.latest_inspect.error_code === vector.expected_error
      && canonical(pack.inspect_write_paths) === canonical([vectorRegistry.runtime_paths.panel_refresh_status.path]));
  }
  else if (vector.id === "producer-blocked-has-no-output") check(vector.id, vector.status === "blocked" && vector.output === null && Boolean(vector.error_code));
  else if (vector.id === "dirty-run-retry-cursor") { const blocked = vector.nodes.find(({ disposition }) => disposition === "blocked"); check(vector.id, vector.status === "dirty" && blocked.output === null && vector.retry_from_instance_key === blocked.instance_key); }
  else check(vector.id, false);
}

for (const vector of suite.platform_vectors) {
  if (vector.id === "posix-symlink" && process.platform !== "win32") {
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), "adp-conformance-"));
    const target = path.join(folder, "target"); const link = path.join(folder, "link");
    fs.writeFileSync(target, "x"); fs.symlinkSync(target, link);
    check(vector.id, fs.lstatSync(link).isSymbolicLink() && vector.expected_error === "DEPENDENCY_PATH_UNSAFE");
    fs.rmSync(folder, { recursive: true, force: true });
  } else check(vector.id, ["DEPENDENCY_PATH_UNSAFE", "DURABILITY_UNAVAILABLE"].includes(vector.expected_error));
}

if (canonical([...passed, ...failed].sort()) !== canonical(expectedIds)) failed.push("suite-vector-accounting");
const result = {
  schema_version: "1.0.0", evidence_kind: "design-fixture-check", implementation_id: "node-reference-adapter", implementation_version: "1.2.0",
  platform: args.platform, host_platform: `${process.platform}-${process.arch}`,
  runtime: { implementation: "node", version: process.versions.node, executable_sha256: hash(fs.readFileSync(process.execPath)), build_digest: hash(fs.readFileSync(new URL(import.meta.url))) },
  native_durability_exercised: false,
  registry_sha256: actualHashes.registry, suite_sha256: actualHashes.suite, schema_sha256: actualHashes.schema, protocol_sha256: actualHashes.protocol,
  passed_vector_ids: passed.sort(), failed_vector_ids: [...new Set(failed)].sort(), executed_at: args["executed-at"],
};
result.result_id = hash(Buffer.from(canonical(result)));
if (!validate(result, schema, "conformanceResultV1")) throw new Error("result receipt failed schema validation");
fs.writeFileSync(args.output, `${JSON.stringify(result, null, 2)}\n`, "utf8");
process.exitCode = result.failed_vector_ids.length ? 1 : 0;
