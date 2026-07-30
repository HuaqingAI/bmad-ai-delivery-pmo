#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _valid_unicode(value: str) -> str:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError("JCS rejects unpaired Unicode surrogates")
    return value


def _utf16_sort_key(value: str) -> bytes:
    return _valid_unicode(value).encode("utf-16-be")


def _canonical_number(value: float) -> str:
    if value != value or value in {float("inf"), float("-inf")}:
        raise ValueError("JCS rejects non-finite numbers")
    if value == 0:
        return "0"
    negative = value < 0
    raw = repr(abs(value)).lower()
    if value.is_integer() and abs(value) > 9_007_199_254_740_991:
        raise ValueError("JCS integer exceeds IEEE-754 safe range")
    mantissa, exponent_text = (raw.split("e", 1) + ["0"])[:2] if "e" in raw else (raw, "0")
    exponent = int(exponent_text)
    integer, fraction = (mantissa.split(".", 1) + [""])[:2] if "." in mantissa else (mantissa, "")
    fraction = fraction.rstrip("0")
    digits = integer + fraction
    scientific_exponent = exponent + len(integer) - 1
    if -6 <= scientific_exponent < 21:
        point = scientific_exponent + 1
        if point <= 0:
            rendered = "0." + "0" * (-point) + digits
        elif point >= len(digits):
            rendered = digits + "0" * (point - len(digits))
        else:
            rendered = digits[:point] + "." + digits[point:]
    else:
        rendered = digits[0] + (("." + digits[1:]) if len(digits) > 1 else "") + "e" + ("+" if scientific_exponent >= 0 else "") + str(scientific_exponent)
    return ("-" if negative else "") + rendered


def _canonical_text(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > 9_007_199_254_740_991:
            raise ValueError("JCS integer exceeds IEEE-754 safe range")
        return str(value)
    if isinstance(value, float):
        return _canonical_number(value)
    if isinstance(value, str):
        return json.dumps(_valid_unicode(value), ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_canonical_text(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("JCS object keys must be strings")
        return "{" + ",".join(
            _canonical_text(key) + ":" + _canonical_text(value[key])
            for key in sorted(value, key=_utf16_sort_key)
        ) + "}"
    raise ValueError(f"unsupported JCS value: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    return _canonical_text(value).encode("utf-8")


ED25519_Q = 2**255 - 19
ED25519_L = 2**252 + 27742317777372353535851937790883648493
ED25519_D = -121665 * pow(121666, ED25519_Q - 2, ED25519_Q) % ED25519_Q
ED25519_I = pow(2, (ED25519_Q - 1) // 4, ED25519_Q)


def _ed25519_xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(ED25519_D * y * y + 1, ED25519_Q - 2, ED25519_Q) % ED25519_Q
    x = pow(xx, (ED25519_Q + 3) // 8, ED25519_Q)
    if (x * x - xx) % ED25519_Q:
        x = x * ED25519_I % ED25519_Q
    if x & 1:
        x = ED25519_Q - x
    return x


ED25519_BY = 4 * pow(5, ED25519_Q - 2, ED25519_Q) % ED25519_Q
ED25519_BX = _ed25519_xrecover(ED25519_BY)
ED25519_B = (ED25519_BX, ED25519_BY, 1, ED25519_BX * ED25519_BY % ED25519_Q)
ED25519_IDENTITY = (0, 1, 1, 0)


def _ed25519_add(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x1, y1, z1, t1 = left
    x2, y2, z2, t2 = right
    a = (y1 - x1) * (y2 - x2) % ED25519_Q
    b = (y1 + x1) * (y2 + x2) % ED25519_Q
    c = 2 * ED25519_D * t1 * t2 % ED25519_Q
    d = 2 * z1 * z2 % ED25519_Q
    e, f, g, h = (b - a) % ED25519_Q, (d - c) % ED25519_Q, (d + c) % ED25519_Q, (b + a) % ED25519_Q
    return e * f % ED25519_Q, g * h % ED25519_Q, f * g % ED25519_Q, e * h % ED25519_Q


def _ed25519_scalar_mult(point: tuple[int, int, int, int], scalar: int) -> tuple[int, int, int, int]:
    result = ED25519_IDENTITY
    addend = point
    while scalar:
        if scalar & 1:
            result = _ed25519_add(result, addend)
        addend = _ed25519_add(addend, addend)
        scalar >>= 1
    return result


def _ed25519_encode(point: tuple[int, int, int, int]) -> bytes:
    x, y, z, _ = point
    inverse = pow(z, ED25519_Q - 2, ED25519_Q)
    x, y = x * inverse % ED25519_Q, y * inverse % ED25519_Q
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _ed25519_decode(encoded: bytes) -> tuple[int, int, int, int]:
    if len(encoded) != 32:
        raise ValueError("Ed25519 point length")
    value = int.from_bytes(encoded, "little")
    y, sign = value & ((1 << 255) - 1), value >> 255
    if y >= ED25519_Q:
        raise ValueError("Ed25519 noncanonical point")
    x = _ed25519_xrecover(y)
    if x & 1 != sign:
        x = ED25519_Q - x
    point = (x, y, 1, x * y % ED25519_Q)
    if (
        _ed25519_encode(point) != encoded
        or _ed25519_encode(_ed25519_scalar_mult(point, ED25519_L)) != _ed25519_encode(ED25519_IDENTITY)
    ):
        raise ValueError("Ed25519 invalid point")
    return point


def ed25519_public_key(seed: bytes) -> bytes:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(bytes([digest[0] & 248]) + digest[1:31] + bytes([(digest[31] & 63) | 64]), "little")
    return _ed25519_encode(_ed25519_scalar_mult(ED25519_B, scalar))


def ed25519_sign(seed: bytes, message: bytes) -> bytes:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(bytes([digest[0] & 248]) + digest[1:31] + bytes([(digest[31] & 63) | 64]), "little")
    public_key = ed25519_public_key(seed)
    nonce = int.from_bytes(hashlib.sha512(digest[32:] + message).digest(), "little") % ED25519_L
    encoded_r = _ed25519_encode(_ed25519_scalar_mult(ED25519_B, nonce))
    challenge = int.from_bytes(hashlib.sha512(encoded_r + public_key + message).digest(), "little") % ED25519_L
    return encoded_r + ((nonce + challenge * scalar) % ED25519_L).to_bytes(32, "little")


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    try:
        if len(signature) != 64:
            return False
        encoded_r, scalar_bytes = signature[:32], signature[32:]
        scalar = int.from_bytes(scalar_bytes, "little")
        if scalar >= ED25519_L:
            return False
        public_point = _ed25519_decode(public_key)
        r_point = _ed25519_decode(encoded_r)
        challenge = int.from_bytes(hashlib.sha512(encoded_r + public_key + message).digest(), "little") % ED25519_L
        return _ed25519_encode(_ed25519_scalar_mult(ED25519_B, scalar)) == _ed25519_encode(_ed25519_add(r_point, _ed25519_scalar_mult(public_point, challenge)))
    except (ValueError, OverflowError):
        return False


def filesystem_token(value: str) -> str:
    return "i_" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def generation_token(value: str) -> str:
    match = re.fullmatch(r"sha256:([0-9a-f]{64})", value)
    if match is None:
        raise ValueError("generation id is not a canonical sha256")
    return "h_" + match.group(1)


def projection_kind_token(value: str) -> str:
    if not value or "\n" in value or "\r" in value or unicodedata.normalize("NFC", value) != value:
        raise ValueError("projection kind is not a canonical single line")
    return filesystem_token(value)


def instance_token(value: str | None) -> str:
    if value is None:
        return "singleton"
    if not value or "\n" in value or "\r" in value or unicodedata.normalize("NFC", value) != value:
        raise ValueError("instance key is not a canonical single line")
    return filesystem_token(value)


def runtime_path(
    registry: dict[str, Any], template_name: str, *, generation_id: str | None = None,
    projection_kind: str | None = None, instance_key: str | None = None, transaction_id: str | None = None,
    nonce_id: str | None = None, result_id: str | None = None, blob_id: str | None = None,
    release_set_id: str | None = None, lifecycle_id: str | None = None, snapshot_id: str | None = None,
    apply_order: int | None = None,
) -> str:
    record = registry["runtime_paths"].get(template_name)
    if not isinstance(record, dict) or record.get("root") != "memory" or not isinstance(record.get("path"), str):
        raise ValueError("runtime path template is not registry-bound")
    substitutions = {
        "{generation_token}": generation_token(generation_id) if generation_id is not None else None,
        "{projection_kind_token}": projection_kind_token(projection_kind) if projection_kind is not None else None,
        "{instance_token}": instance_token(instance_key),
        "{transaction_token}": filesystem_token(transaction_id) if transaction_id is not None else None,
        "{nonce_token}": generation_token(nonce_id) if nonce_id is not None else None,
        "{result_token}": generation_token(result_id) if result_id is not None else None,
        "{blob_token}": generation_token(blob_id) if blob_id is not None else None,
        "{release_set_token}": generation_token(release_set_id) if release_set_id is not None else None,
        "{lifecycle_token}": generation_token(lifecycle_id) if lifecycle_id is not None else None,
        "{snapshot_token}": generation_token(snapshot_id) if snapshot_id is not None else None,
        "{apply_order}": str(apply_order) if apply_order is not None and apply_order >= 0 else None,
    }
    path = record["path"]
    for token, replacement in substitutions.items():
        if token in path:
            if replacement is None:
                raise ValueError(f"missing runtime path input: {token}")
            path = path.replace(token, replacement)
    if re.search(r"\{[^{}]+\}", path) or path != unicodedata.normalize("NFC", path):
        raise ValueError("runtime path contains an unresolved or non-canonical token")
    if path.startswith("/") or "\\" in path or ":" in path or any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError("runtime path is unsafe")
    return path


ACTION_LEDGER_COLUMNS = [
    "Action ID", "Status", "Owner", "Workstream", "Affected Workstreams", "Action", "Source", "Reason",
    "Due / Trigger", "Closure Criteria", "Closure Criteria Verifiable", "Created At", "Started At", "Done At",
    "Cancelled At", "Baseline Revision", "Related Plan Items", "Related Flow Edges", "Last Updated",
    "Owning Workflow", "Action Revision",
]
ACTION_LEDGER_LEGACY_20_COLUMNS = ACTION_LEDGER_COLUMNS[:-1]
ACTION_LEDGER_LEGACY_12_COLUMNS = [
    "Action ID", "Status", "Owner", "Workstream", "Affected Workstreams", "Action", "Source", "Reason",
    "Due / Trigger", "Closure Criteria", "Last Updated", "Owning Workflow",
]
ACTION_LEDGER_PREAMBLE = (
    "# Action Ledger\n\n"
    "This is the ADP action source of truth. Do not use `views/fde-actions.md` as a source file.\n\n"
)
ACTION_LEDGER_FIELDS = [
    "action_id", "status", "owner", "routing_scope_id", "affected_workstreams", "action", "source", "reason",
    "due_trigger", "closure_criteria", "closure_criteria_verifiable", "created_at", "started_at", "done_at",
    "cancelled_at", "baseline_revision", "related_plan_items", "related_flow_edges", "last_updated",
    "owning_workflow", "action_revision",
]
ACTIVE_ACTION_STATUSES = {"open", "in-progress", "blocked"}


def evidence_order_key(row: dict[str, Any]) -> tuple[bytes, bytes, bytes]:
    return tuple(row[field].encode("utf-8") for field in ("source_path", "source_fingerprint", "observed_at"))


def canonical_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(copy.deepcopy(rows), key=evidence_order_key)
    identities = [canonical_bytes(row) for row in ordered]
    if rows != ordered or len(identities) != len(set(identities)):
        raise ValueError("evidence is not canonically ordered and unique")
    return ordered


def _ledger_cell(value: Any) -> str:
    text = str(value)
    if not text or "\n" in text or "\r" in text or unicodedata.normalize("NFC", text) != text:
        raise ValueError("ledger cell is not a canonical single line")
    return text.replace("\\", "\\\\").replace("|", "\\|")


def _split_ledger_row(line: str, *, allow_empty: bool = False) -> list[str]:
    if not line.startswith("| ") or not line.endswith(" |"):
        raise ValueError("ledger row framing is not canonical")
    body = line[2:-2]
    cells: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(body):
        if body.startswith(" | ", index):
            cells.append("".join(current))
            current = []
            index += 3
            continue
        char = body[index]
        if char == "\\":
            if index + 1 >= len(body) or body[index + 1] not in {"\\", "|"}:
                raise ValueError("ledger cell uses a non-canonical escape")
            current.append(body[index + 1])
            index += 2
            continue
        current.append(char)
        index += 1
    cells.append("".join(current))
    if any((not allow_empty and not value) or unicodedata.normalize("NFC", value) != value for value in cells):
        raise ValueError("ledger row contains an empty or non-NFC cell")
    return cells


def render_action_ledger_row(row: dict[str, Any]) -> str:
    affected = ", ".join(row["affected_workstreams"]) if row["affected_workstreams"] else "-"
    values = [row[field] for field in ACTION_LEDGER_FIELDS]
    values[4] = affected
    return "| " + " | ".join(_ledger_cell(value) for value in values) + " |"


def render_action_ledger(rows: list[dict[str, Any]]) -> bytes:
    ordered = sorted(rows, key=lambda row: row["action_id"].encode("utf-8"))
    ids = [row["action_id"] for row in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate action id")
    header = "| " + " | ".join(ACTION_LEDGER_COLUMNS) + " |\n"
    separator = "| " + " | ".join("---" for _ in ACTION_LEDGER_COLUMNS) + " |\n"
    body = "".join(render_action_ledger_row(row) + "\n" for row in ordered)
    return (ACTION_LEDGER_PREAMBLE + header + separator + body).encode("utf-8")


def parse_action_ledger(raw: bytes) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("ledger is not UTF-8") from error
    if "\r" in text or "\0" in text or not text.endswith("\n") or not text.startswith(ACTION_LEDGER_PREAMBLE):
        raise ValueError("ledger framing is not canonical")
    lines = text[len(ACTION_LEDGER_PREAMBLE):].splitlines()
    expected_header = "| " + " | ".join(ACTION_LEDGER_COLUMNS) + " |"
    expected_separator = "| " + " | ".join("---" for _ in ACTION_LEDGER_COLUMNS) + " |"
    if len(lines) < 2 or lines[:2] != [expected_header, expected_separator]:
        raise ValueError("ledger header is not canonical v2")
    rows: list[dict[str, Any]] = []
    for line in lines[2:]:
        cells = _split_ledger_row(line)
        if len(cells) != len(ACTION_LEDGER_FIELDS):
            raise ValueError("ledger row has the wrong column count")
        row = dict(zip(ACTION_LEDGER_FIELDS, cells))
        affected = [] if row["affected_workstreams"] == "-" else row["affected_workstreams"].split(", ")
        if affected != sorted(set(affected), key=lambda value: value.encode("utf-8")):
            raise ValueError("affected workstreams are not canonical")
        row["affected_workstreams"] = affected
        try:
            row["action_revision"] = int(row["action_revision"])
        except ValueError as error:
            raise ValueError("action revision is not an integer") from error
        if row["action_revision"] < 1 or row["action_revision"] > 9_007_199_254_740_991:
            raise ValueError("action revision is out of range")
        rows.append(row)
    if render_action_ledger(rows) != raw:
        raise ValueError("ledger bytes are not canonical")
    return rows


def parse_action_ledger_ingress(raw: bytes | None, declared_format: str) -> list[dict[str, Any]]:
    if declared_format == "absent":
        if raw is not None:
            raise ValueError("absent ledger declaration has bytes")
        return []
    if raw is None:
        raise ValueError("ledger ingress bytes are missing")
    if declared_format == "canonical21":
        return parse_action_ledger(raw)
    columns = {
        "legacy12": ACTION_LEDGER_LEGACY_12_COLUMNS,
        "legacy20": ACTION_LEDGER_LEGACY_20_COLUMNS,
    }.get(declared_format)
    if columns is None:
        raise ValueError("unknown ledger ingress format")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("legacy ledger is not UTF-8") from error
    if "\r" in text or "\0" in text or not text.endswith("\n") or not text.startswith(ACTION_LEDGER_PREAMBLE):
        raise ValueError("legacy ledger framing is invalid")
    lines = text[len(ACTION_LEDGER_PREAMBLE):].splitlines()
    expected_header = "| " + " | ".join(columns) + " |"
    expected_separator = "| " + " | ".join("---" for _ in columns) + " |"
    if len(lines) < 2 or lines[:2] != [expected_header, expected_separator]:
        raise ValueError("legacy ledger header does not match the declared pinned grammar")
    rows: list[dict[str, Any]] = []
    field_by_column = dict(zip(ACTION_LEDGER_COLUMNS, ACTION_LEDGER_FIELDS))
    for line in lines[2:]:
        cells = _split_ledger_row(line, allow_empty=True)
        if len(cells) != len(columns):
            raise ValueError("legacy ledger row has the wrong column count")
        row = {field: "-" for field in ACTION_LEDGER_FIELDS}
        row["affected_workstreams"] = []
        for column, cell in zip(columns, cells):
            field = field_by_column[column]
            value = cell if cell else "-"
            if field == "affected_workstreams":
                values = [] if value == "-" else value.split(", ")
                if values != sorted(set(values), key=lambda item: item.encode("utf-8")):
                    raise ValueError("legacy affected workstreams are not canonical")
                row[field] = values
            else:
                row[field] = value
        row["action_revision"] = 1
        rows.append(row)
    ids = [row["action_id"] for row in rows]
    if any(value == "-" for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("legacy ledger action IDs are missing or duplicated")
    return sorted(rows, key=lambda row: row["action_id"].encode("utf-8"))


def _utc_instant(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be canonical UTC")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.microsecond or parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC at whole-second precision")
    return parsed


def action_row_chronology_valid(row: dict[str, Any]) -> bool:
    try:
        created = _utc_instant(row["created_at"])
        updated = _utc_instant(row["last_updated"])
        started = None if row["started_at"] == "-" else _utc_instant(row["started_at"])
        done = None if row["done_at"] == "-" else _utc_instant(row["done_at"])
        cancelled = None if row["cancelled_at"] == "-" else _utc_instant(row["cancelled_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if created > updated or (started is not None and not created <= started <= updated):
        return False
    if done is not None and not created <= done <= updated:
        return False
    if cancelled is not None and not created <= cancelled <= updated:
        return False
    if started is not None and done is not None and started > done:
        return False
    if started is not None and cancelled is not None and started > cancelled:
        return False
    status = row["status"]
    return (
        (status == "open" and started is None and done is None and cancelled is None)
        or (status in {"in-progress", "blocked"} and started is not None and done is None and cancelled is None)
        or (status == "done" and started is not None and done is not None and cancelled is None)
        or (status == "cancelled" and done is None and cancelled is not None)
    )


def action_row_from_create(command: dict[str, Any]) -> dict[str, Any]:
    create = command["create"]
    evidence = canonical_evidence(command["evidence"])
    source = "; ".join(f"{row['source_path']}@{row['source_fingerprint']}" for row in evidence)
    created_at = min(row["observed_at"] for row in evidence)
    last_updated = max(row["observed_at"] for row in evidence)
    status = create["status"]
    return {
        "action_id": command["action_id"], "status": status, "owner": create["owner"],
        "routing_scope_id": create["routing_scope_id"],
        "affected_workstreams": sorted(set(create.get("affected_workstreams", [])), key=lambda value: value.encode("utf-8")),
        "action": create["action"], "source": source, "reason": command["command_id"],
        "due_trigger": create["due_trigger"], "closure_criteria": create["closure_criteria"],
        "closure_criteria_verifiable": "-", "created_at": created_at,
        "started_at": last_updated if status in {"in-progress", "blocked", "done"} else "-",
        "done_at": last_updated if status == "done" else "-",
        "cancelled_at": last_updated if status == "cancelled" else "-",
        "baseline_revision": "-", "related_plan_items": "-", "related_flow_edges": "-",
        "last_updated": last_updated, "owning_workflow": "adp-status-sync", "action_revision": 1,
    }


def apply_action_command(rows: list[dict[str, Any]], command: dict[str, Any]) -> list[dict[str, Any]]:
    result = copy.deepcopy(rows)
    matches = [index for index, row in enumerate(result) if row["action_id"] == command["action_id"]]
    if command["operation"] == "create":
        if matches:
            raise ValueError("action already exists")
        result.append(action_row_from_create(command))
    else:
        if len(matches) != 1:
            raise ValueError("patch action is missing or ambiguous")
        row = result[matches[0]]
        if row["action_revision"] != command["expected_revision"]:
            raise ValueError("action revision CAS failed")
        if not action_row_chronology_valid(row):
            raise ValueError("action lifecycle chronology is invalid")
        before_status = row["status"]
        after_status = command["set"].get("status", before_status)
        if before_status in {"done", "cancelled"} and after_status != before_status:
            raise ValueError("terminal action cannot be reopened")
        field_map = {"routing_scope_id": "routing_scope_id", "affected_workstreams": "affected_workstreams"}
        for field, value in command["set"].items():
            target = field_map.get(field, field)
            row[target] = sorted(set(value), key=lambda item: item.encode("utf-8")) if field == "affected_workstreams" else value
        last_updated = max(entry["observed_at"] for entry in command["evidence"])
        if _utc_instant(last_updated) < _utc_instant(row["last_updated"]):
            raise ValueError("action evidence predates Last Updated")
        if "status" in command["set"] and after_status != before_status:
            if after_status == "open":
                row["started_at"] = row["done_at"] = row["cancelled_at"] = "-"
            elif after_status in {"in-progress", "blocked"}:
                if row["started_at"] == "-":
                    row["started_at"] = last_updated
                row["done_at"] = row["cancelled_at"] = "-"
            elif after_status == "done":
                if row["started_at"] == "-":
                    row["started_at"] = last_updated
                row["done_at"], row["cancelled_at"] = last_updated, "-"
            else:
                row["done_at"], row["cancelled_at"] = "-", last_updated
        row["action_revision"] += 1
        row["source"] = "; ".join(
            f"{entry['source_path']}@{entry['source_fingerprint']}"
            for entry in canonical_evidence(command["evidence"])
        )
        row["reason"] = command["command_id"]
        row["last_updated"] = last_updated
        row["owning_workflow"] = "adp-status-sync"
        if not action_row_chronology_valid(row):
            raise ValueError("action mutation produces invalid lifecycle chronology")
    return sorted(result, key=lambda row: row["action_id"].encode("utf-8"))


def rendered_action_summary(row: dict[str, Any]) -> str:
    encode = lambda value: urllib.parse.quote(str(value), safe=" -._~")
    return f"[action_id:{row['action_id']}] {encode(row['owner'])}: {encode(row['action'])} (due: {encode(row['due_trigger'])})"


def action_snapshot(rows: list[dict[str, Any]], workstream_id: str, ledger_fingerprint: str, ledger_revision: int) -> dict[str, Any]:
    actions = []
    for row in rows:
        if row["status"] not in ACTIVE_ACTION_STATUSES:
            continue
        if row["routing_scope_id"] != workstream_id and workstream_id not in row["affected_workstreams"]:
            continue
        actions.append({
            "action_id": row["action_id"], "owner": row["owner"], "action": row["action"], "due_trigger": row["due_trigger"],
            "status": row["status"], "action_revision": row["action_revision"], "routing_scope_id": row["routing_scope_id"],
            "affected_workstreams": copy.deepcopy(row["affected_workstreams"]), "rendered_summary": rendered_action_summary(row),
        })
    return {"ledger_fingerprint": ledger_fingerprint, "ledger_revision": ledger_revision, "actions": actions}


def canonical_catalog_id(entries: list[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_bytes({"workstreams": entries}))


def canonical_inventory_id(entries: list[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_bytes({"physical_workstreams": entries}))


def panel_binding_catalog(registry: dict[str, Any], schema_sha: str, registry_sha: str) -> dict[str, Any]:
    catalog = {
        "contract": {"schema_id": "urn:adp:panel-sync-contracts:2026-07-24#panel-binding-catalog-v1", "schema_sha256": schema_sha, "registry_sha256": registry_sha},
        "schema_version": "1.0.0", "bindings": copy.deepcopy(registry["panel_binding_map"]),
    }
    catalog["catalog_id"] = sha256_bytes(canonical_bytes(catalog))
    return catalog


def replace_tokens(value: Any, substitutions: dict[str, str]) -> Any:
    if isinstance(value, str):
        return substitutions.get(value, value)
    if isinstance(value, list):
        return [replace_tokens(item, substitutions) for item in value]
    if isinstance(value, dict):
        return {key: replace_tokens(item, substitutions) for key, item in value.items()}
    return value


def resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"external schema ref is not supported by design runner: {ref}")
    current: Any = root
    for part in ref[2:].split("/"):
        current = current[part.replace("~1", "/").replace("~0", "~")]
    return current


def schema_errors(instance: Any, rule: dict[str, Any], root: dict[str, Any], path: str = "$") -> list[str]:
    if "$ref" in rule:
        return schema_errors(instance, resolve_ref(root, rule["$ref"]), root, path)
    errors: list[str] = []
    for child in rule.get("allOf", []):
        errors.extend(schema_errors(instance, child, root, path))
    if "oneOf" in rule:
        matches = [not schema_errors(instance, child, root, path) for child in rule["oneOf"]]
        if sum(matches) != 1:
            errors.append(f"{path}: oneOf matched {sum(matches)} branches")
            return errors
    if "anyOf" in rule and not any(not schema_errors(instance, child, root, path) for child in rule["anyOf"]):
        errors.append(f"{path}: anyOf matched no branches")
    if "not" in rule and not schema_errors(instance, rule["not"], root, path):
        errors.append(f"{path}: forbidden schema matched")
    if "if" in rule:
        branch = "then" if not schema_errors(instance, rule["if"], root, path) else "else"
        if branch in rule:
            errors.extend(schema_errors(instance, rule[branch], root, path))
    if "const" in rule and instance != rule["const"]:
        errors.append(f"{path}: const mismatch")
    if "enum" in rule and instance not in rule["enum"]:
        errors.append(f"{path}: enum mismatch")

    allowed = rule.get("type")
    if allowed is not None:
        allowed_types = [allowed] if isinstance(allowed, str) else allowed
        type_ok = any(
            (kind == "null" and instance is None)
            or (kind == "object" and isinstance(instance, dict))
            or (kind == "array" and isinstance(instance, list))
            or (kind == "string" and isinstance(instance, str))
            or (kind == "integer" and isinstance(instance, int) and not isinstance(instance, bool))
            or (kind == "number" and isinstance(instance, (int, float)) and not isinstance(instance, bool))
            or (kind == "boolean" and isinstance(instance, bool))
            for kind in allowed_types
        )
        if not type_ok:
            errors.append(f"{path}: type mismatch")
            return errors

    if isinstance(instance, str):
        if len(instance) < rule.get("minLength", 0):
            errors.append(f"{path}: too short")
        if "maxLength" in rule and len(instance) > rule["maxLength"]:
            errors.append(f"{path}: too long")
        if "pattern" in rule and re.search(rule["pattern"], instance) is None:
            errors.append(f"{path}: pattern mismatch")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in rule and instance < rule["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in rule and instance > rule["maximum"]:
            errors.append(f"{path}: above maximum")
    if isinstance(instance, list):
        if len(instance) < rule.get("minItems", 0):
            errors.append(f"{path}: too few items")
        if "maxItems" in rule and len(instance) > rule["maxItems"]:
            errors.append(f"{path}: too many items")
        if rule.get("uniqueItems") and len({canonical_bytes(item) for item in instance}) != len(instance):
            errors.append(f"{path}: duplicate items")
        if isinstance(rule.get("items"), dict):
            for index, item in enumerate(instance):
                errors.extend(schema_errors(item, rule["items"], root, f"{path}[{index}]"))
        if "contains" in rule and not any(not schema_errors(item, rule["contains"], root, f"{path}[{index}]") for index, item in enumerate(instance)):
            errors.append(f"{path}: contains matched no items")
    if isinstance(instance, dict):
        required = rule.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing {key}")
        if len(instance) < rule.get("minProperties", 0):
            errors.append(f"{path}: too few properties")
        properties = rule.get("properties", {})
        if rule.get("additionalProperties") is False:
            for key in instance:
                if key not in properties:
                    errors.append(f"{path}: unknown property {key}")
        elif isinstance(rule.get("additionalProperties"), dict):
            for key in instance:
                if key not in properties:
                    errors.extend(schema_errors(instance[key], rule["additionalProperties"], root, f"{path}.{key}"))
        for key, child in properties.items():
            if key in instance:
                errors.extend(schema_errors(instance[key], child, root, f"{path}.{key}"))
    return errors


def validate(instance: Any, schema: dict[str, Any], definition: str) -> bool:
    return not schema_errors(instance, schema["$defs"][definition], schema)


def validate_document(instance: Any, schema: dict[str, Any]) -> bool:
    return not schema_errors(instance, schema, schema)


def contract_record(registry: dict[str, Any], contract_name: str) -> dict[str, Any]:
    matches = [row for row in registry["contracts"] if f"{row['name']}/{row['version']}" == contract_name]
    if len(matches) != 1:
        raise ValueError(f"contract registry lookup is not unique: {contract_name}")
    return matches[0]


def expected_contract_ref(registry: dict[str, Any], contract_name: str, schema_sha: str, registry_sha: str) -> dict[str, str]:
    return {"schema_id": contract_record(registry, contract_name)["schema_id"], "schema_sha256": schema_sha, "registry_sha256": registry_sha}


def embedded_contract_refs_valid(value: Any, registry: dict[str, Any], schema_sha: str, registry_sha: str) -> bool:
    by_schema_id: dict[str, dict[str, Any]] = {}
    for record in registry["contracts"]:
        schema_id = record["schema_id"]
        if schema_id in by_schema_id:
            return False
        by_schema_id[schema_id] = record

    def walk(current: Any) -> bool:
        if isinstance(current, list):
            return all(walk(item) for item in current)
        if not isinstance(current, dict):
            return True
        if "contract" in current:
            reference = current["contract"]
            if not isinstance(reference, dict):
                return False
            record = by_schema_id.get(reference.get("schema_id"))
            if record is None:
                return False
            name = f"{record['name']}/{record['version']}"
            if reference != expected_contract_ref(registry, name, schema_sha, registry_sha):
                return False
        return all(walk(item) for item in current.values())

    return walk(value)


def validate_registered(
    instance: Any, schema: dict[str, Any], registry: dict[str, Any], contract_name: str, schema_sha: str, registry_sha: str,
) -> bool:
    record = contract_record(registry, contract_name)
    definition = record["schema_pointer"].removeprefix("#/$defs/")
    if not validate(instance, schema, definition):
        return False
    if isinstance(instance, dict) and "contract" in instance and instance.get("contract") != expected_contract_ref(registry, contract_name, schema_sha, registry_sha):
        return False
    return embedded_contract_refs_valid(instance, registry, schema_sha, registry_sha)


def artifact_bytes(value: str | None) -> bytes | None:
    if value is None:
        return None
    return base64.b64decode(value, validate=True)


def encoded_bytes(value: bytes | None) -> str | None:
    return None if value is None else base64.b64encode(value).decode("ascii")


def json_pointer(document: Any, pointer: str) -> Any:
    pointer = pointer.removeprefix("#")
    if pointer == "":
        return document
    current = document
    for token in pointer.removeprefix("/").split("/"):
        current = current[token.replace("~1", "/").replace("~0", "~")]
    return current


def set_pointer(document: dict[str, Any], pointer: str, value: Any) -> None:
    tokens = [token.replace("~1", "/").replace("~0", "~") for token in pointer.removeprefix("/").split("/")]
    current = document
    for token in tokens[:-1]:
        current = current.setdefault(token, {})
    current[tokens[-1]] = value


def vector_ids(suite: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for key, value in suite.items():
        if key.endswith("_vectors") or key == "journal_fault_matrix":
            ids.extend(str(item["id"]) for item in value)
    return sorted(ids)


def render_create(template: str, data: dict[str, Any]) -> str:
    def esc_table(value: str) -> str:
        return value.replace("\\", "\\\\").replace("|", "\\|")

    def bullets(values: list[str]) -> str:
        return "\n".join(f"- {value}" for value in sorted(set(values))) if values else "- TBD"

    rows = sorted(data["artifact_rows"], key=lambda row: tuple(row[key].encode() for key in ("artifact", "path", "baseline_status", "notes")))
    table = "\n".join(
        f"| {esc_table(row['artifact'])} | {esc_table(row['path'])} | {esc_table(row['baseline_status'])} | {esc_table(row['notes'])} |"
        for row in rows
    ) or "| TBD | TBD | TBD | TBD |"
    replacements = {
        "{{CREATED_AT}}": data["created_at"], "{{WORKSTREAM_ID}}": data["workstream_id"],
        "{{WORKSTREAM_NAME}}": data["name"], "{{FDE_OWNER}}": data["fde_owner"],
        "{{BUSINESS_OWNER}}": data["business_owner"], "{{BMM_PHASE}}": data["phase"],
        "{{ADP_STATUS}}": data["status"], "{{SCOPE_SUMMARY}}": data["scope_summary"],
        "{{ARTIFACT_TABLE}}": table, "{{DEPENDS_ON}}": bullets(data["depends_on"]),
        "{{IMPACTS}}": bullets(data["impacts"]), "{{L0_REFERENCES}}": bullets(data["l0_references"]),
    }
    rendered = template
    for token, replacement in replacements.items():
        if rendered.count(token) < 1:
            raise ValueError(f"template token multiplicity: {token}")
        rendered = rendered.replace(token, replacement)
    if re.search(r"\{\{[^{}]+\}\}", rendered):
        raise ValueError("unresolved template token")
    return rendered


def meeting_block(record: dict[str, Any]) -> str:
    return (
        f"<!-- adp:meeting-history:v1 command_id={record['command_id']} entry_id={record['entry_id']} observed_at={record['observed_at']} -->\n"
        f"### Meeting Sync Update: {record['observed_at'][:10]} - {record['entry_id']}\n\n"
        f"- Source: {record['source_path']} @ {record['source_fingerprint']}\n"
        f"- Classification: {record['classification']}\n- Update: {record['summary']}\n"
        f"- Owner: {record['owner']}\n- Due / trigger: {record['due_trigger']}\n- Status: {record['status']}\n\n"
    )


MEETING_HISTORY_BLOCK_RE = re.compile(
    r"<!-- adp:meeting-history:v1 command_id=([^\s]+) entry_id=([^\s]+) observed_at=([^\s]+) -->\n"
    r"### Meeting Sync Update: ([0-9]{4}-[0-9]{2}-[0-9]{2}) - ([^\n]+)\n\n"
    r"- Source: ([^\n]+) @ (sha256:[0-9a-f]{64})\n"
    r"- Classification: ([^\n]+)\n- Update: ([^\n]+)\n"
    r"- Owner: ([^\n]+)\n- Due / trigger: ([^\n]+)\n- Status: ([^\n]+)\n\n"
)


def parse_meeting_history(section: str) -> list[dict[str, Any]]:
    if section == "## Meeting Sync History":
        return []
    prefix = "## Meeting Sync History\n\n"
    if not section.startswith(prefix):
        raise ValueError("Meeting Sync History framing is not canonical")
    body = section[len(prefix):] + "\n\n"
    rows: list[dict[str, Any]] = []
    position = 0
    while position < len(body):
        match = MEETING_HISTORY_BLOCK_RE.match(body, position)
        if match is None:
            raise ValueError("Meeting Sync History block is not canonical")
        command_id, entry_id, observed_at, observed_date, heading_entry_id, source_path, source_fingerprint, classification, summary, owner, due_trigger, status = match.groups()
        row = {
            "entry_id": entry_id, "command_id": command_id, "observed_at": observed_at,
            "source_path": source_path, "source_fingerprint": source_fingerprint,
            "classification": classification, "summary": summary, "owner": owner,
            "due_trigger": due_trigger, "status": status,
        }
        if heading_entry_id != entry_id or observed_date != observed_at[:10] or meeting_block(row) != match.group(0):
            raise ValueError("Meeting Sync History identity is inconsistent")
        rows.append(row)
        position = match.end()
    keys = [(row["observed_at"], row["entry_id"]) for row in rows]
    if keys != sorted(keys, key=lambda row: (row[0].encode("utf-8"), row[1].encode("utf-8"))) or len(keys) != len(set(keys)):
        raise ValueError("Meeting Sync History keys are not canonical and unique")
    return rows


def migrate_wdr(value: str, timestamp: str) -> str:
    legacy_updates = list(re.finditer(
        r"(?m)^(?:<!-- adp-meeting-sync:[^\n]+ -->\n)?## Meeting Sync Update: [^\n]+$", value
    ))
    if legacy_updates:
        if re.search(r"(?m)^## Meeting Sync History$", value):
            raise ValueError("mixed legacy and canonical meeting history is ambiguous")
        first = legacy_updates[0]
        value = value[: first.start()] + "## Meeting Sync History\n\n" + value[first.start():]
        value = re.sub(r"(?m)^## Meeting Sync Update:", "### Meeting Sync Update:", value)
    matches = list(re.finditer(r"(?m)^## ([^\n]+)\n", value))
    preamble = value[: matches[0].start()].strip("\n") if matches else value.strip("\n")
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        sections[match.group(1)] = value[match.start():end].strip("\n")
    status = sections["Project Status"]
    if not re.search(r"(?mi)^- Last status sync:", status):
        status = re.sub(r"(?mi)^(- Next actions:[^\n]*)$", rf"\1\n- Last status sync: {timestamp}", status)
        sections["Project Status"] = status
    order = ["Identity", "BMM Artifact Index", "Scope", "Acceptance", "Project Status", "Roadmap", "Cross-Workstream Links", "Decisions and Evidence", "Checkpoint Sync Log", "Meeting Sync History", "Record Rule"]
    body = [sections[name] for name in order if name in sections]
    return "\n\n".join(([preamble] if preamble else []) + body) + "\n"


WDR_SECTION_ORDER = [
    "Identity", "BMM Artifact Index", "Scope", "Acceptance", "Project Status", "Roadmap",
    "Cross-Workstream Links", "Decisions and Evidence", "Checkpoint Sync Log", "Meeting Sync History", "Record Rule",
]
WDR_REQUIRED_SECTIONS = {
    "Identity", "BMM Artifact Index", "Scope", "Acceptance", "Project Status",
    "Cross-Workstream Links", "Decisions and Evidence", "Record Rule",
}
WDR_REQUIRED_LABELS = {
    "Identity": ["Workstream ID", "Name", "FDE owner", "Business owner", "Current BMM phase", "Current ADP status"],
    "Scope": ["In scope", "Out of scope", "Key assumptions", "Open questions"],
    "Acceptance": ["Acceptance criteria", "Acceptance owner", "Evidence required", "Current readiness", "Unclosed gaps"],
    "Project Status": ["Progress", "Blockers", "Risks", "Dependencies", "Scope or change notes", "Next actions"],
    "Decisions and Evidence": ["Decision links", "Business Decision Packet links", "Evidence links", "Customer/business confirmations"],
}


def split_wdr(value: str) -> tuple[str, dict[str, str]]:
    matches = list(re.finditer(r"(?m)^## ([^\n]+)\n", value))
    if not matches:
        raise ValueError("WDR has no sections")
    preamble = value[: matches[0].start()].strip("\n")
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1)
        if name in sections:
            raise ValueError("duplicate WDR section")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        sections[name] = value[match.start():end].strip("\n")
    return preamble, sections


def complete_wdr_valid(value: str, workstream_id: str) -> bool:
    try:
        if not value.endswith("\n") or "\r" in value or "\0" in value or "{{" in value:
            return False
        preamble, sections = split_wdr(value)
        if not preamble.startswith("# Workstream Delivery Record\n\nCreated: ") or not WDR_REQUIRED_SECTIONS <= set(sections):
            return False
        positions = [WDR_SECTION_ORDER.index(name) for name in sections]
        if positions != sorted(positions):
            return False
        for section, labels in WDR_REQUIRED_LABELS.items():
            for label in labels:
                if len(re.findall(rf"(?m)^- {re.escape(label)}: [^\r\n]+$", sections[section])) != 1:
                    return False
        identities = re.findall(r"(?m)^- Workstream ID: ([^\r\n]+)$", sections["Identity"])
        if identities != [workstream_id]:
            return False
        return "| Artifact | Path / Link | Baseline Status | Notes |" in sections["BMM Artifact Index"] and sections["Record Rule"].startswith("## Record Rule\n\n")
    except (KeyError, ValueError):
        return False


def fixture_wdr(workstream_id: str) -> str:
    return f"""# Workstream Delivery Record

Created: 2026-07-24T02:00:00Z

## Identity

- Workstream ID: {workstream_id}
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
"""


def _replace_wdr_label(section: str, label: str, value: str) -> str:
    updated, count = re.subn(rf"(?m)^- {re.escape(label)}: [^\r\n]+$", f"- {label}: {value}", section)
    if count != 1:
        raise ValueError(f"WDR label is missing or ambiguous: {label}")
    return updated


def _parse_wdr_list(value: str) -> list[str]:
    if value == "TBD":
        return []
    result: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(value):
        if value.startswith("; ", index):
            item = "".join(current)
            if not item:
                raise ValueError("empty WDR collection item")
            result.append(item)
            current = []
            index += 2
            continue
        if value[index] == "\\":
            if value.startswith("\\TBD", index) and not current and (index + 4 == len(value) or value.startswith("; ", index + 4)):
                current.extend("TBD")
                index += 4
                continue
            if index + 1 >= len(value) or value[index + 1] not in {"\\", ";"}:
                raise ValueError("non-canonical WDR collection escape")
            current.append(value[index + 1])
            index += 2
            continue
        current.append(value[index])
        index += 1
    item = "".join(current)
    if not item:
        raise ValueError("empty WDR collection item")
    result.append(item)
    if any(unicodedata.normalize("NFC", item) != item for item in result):
        raise ValueError("non-NFC WDR collection item")
    if _render_wdr_list(result) != value:
        raise ValueError("WDR collection is not byte-canonical")
    return result


def _render_wdr_list(values: list[str]) -> str:
    if not values:
        return "TBD"
    if any(not value or "\n" in value or "\r" in value or unicodedata.normalize("NFC", value) != value for value in values):
        raise ValueError("WDR collection item is not canonical")
    rendered = []
    for value in values:
        escaped = value.replace("\\", "\\\\").replace(";", "\\;")
        rendered.append("\\TBD" if value == "TBD" else escaped)
    return "; ".join(rendered)


MANAGED_ACTION_RE = re.compile(
    r"^\[action_id:([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)\] ([^:]+): (.+) \(due: ([^)]+)\)$"
)


def parse_managed_action_summary(value: str) -> dict[str, str] | None:
    match = MANAGED_ACTION_RE.fullmatch(value)
    if match is None:
        if value.startswith("[action_id:"):
            raise ValueError("malformed managed action marker")
        return None
    action_id, owner, action, due_trigger = match.groups()
    decoded = {
        "action_id": action_id,
        "owner": urllib.parse.unquote(owner),
        "action": urllib.parse.unquote(action),
        "due_trigger": urllib.parse.unquote(due_trigger),
    }
    if rendered_action_summary(decoded) != value:
        raise ValueError("managed action marker is not canonically escaped")
    return decoded


def partition_next_actions(values: list[str]) -> tuple[list[str], list[str]]:
    manual: list[str] = []
    managed: list[tuple[str, str]] = []
    for value in values:
        parsed = parse_managed_action_summary(value)
        if parsed is None:
            manual.append(value)
        else:
            managed.append((parsed["action_id"], value))
    ids = [action_id for action_id, _ in managed]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate managed action marker")
    ordered = sorted(managed, key=lambda row: row[0].encode("utf-8"))
    if managed != ordered:
        raise ValueError("managed action markers are not ordered")
    return manual, [value for _, value in ordered]


def _collection_value(current: str, patch: dict[str, Any]) -> str:
    existing = _parse_wdr_list(current)
    incoming = list(dict.fromkeys(patch["values"]))
    if patch["mode"] == "replace":
        result = incoming
    elif patch["mode"] == "add":
        result = existing + [item for item in incoming if item not in existing]
    else:
        result = [item for item in existing if item not in set(incoming)]
    return _render_wdr_list(result)


def apply_wdr_patch(before: str, command: dict[str, Any], action_summaries: list[str] | None = None) -> str:
    preamble, sections = split_wdr(before)
    mutation = copy.deepcopy(command["set"])
    if command.get("evidence"):
        canonical_evidence(command["evidence"])
    current_fields = {"status", "phase", "progress", "blockers", "risks", "dependencies", "change_notes", "refresh_actions"}
    if current_fields & set(mutation) and command.get("evidence"):
        observed_at = max(row["observed_at"] for row in command["evidence"])
        current_sync = _wdr_label(sections["Project Status"], "Last status sync")
        if _utc_instant(observed_at) < _utc_instant(current_sync):
            raise ValueError("WDR evidence predates Last status sync")
        if "last_status_sync" in mutation and mutation["last_status_sync"] != observed_at:
            raise ValueError("Last status sync does not match command evidence")
        mutation["last_status_sync"] = observed_at
    identity_labels = {"status": "Current ADP status", "phase": "Current BMM phase"}
    status_labels = {
        "progress": "Progress", "blockers": "Blockers", "risks": "Risks", "dependencies": "Dependencies",
        "change_notes": "Scope or change notes", "last_status_sync": "Last status sync",
    }
    for field, label in identity_labels.items():
        if field in mutation:
            sections["Identity"] = _replace_wdr_label(sections["Identity"], label, mutation[field])
    for field, label in status_labels.items():
        if field not in mutation:
            continue
        if field == "last_status_sync" and not re.search(r"(?m)^- Last status sync:", sections["Project Status"]):
            sections["Project Status"] = _replace_wdr_label(sections["Project Status"], "Next actions", re.search(r"(?m)^- Next actions: ([^\n]+)$", sections["Project Status"]).group(1)) + f"\n- Last status sync: {mutation[field]}"
        elif isinstance(mutation[field], dict):
            current = re.search(rf"(?m)^- {re.escape(label)}: ([^\n]+)$", sections["Project Status"])
            if current is None:
                raise ValueError(f"missing collection label: {label}")
            sections["Project Status"] = _replace_wdr_label(sections["Project Status"], label, _collection_value(current.group(1), mutation[field]))
        else:
            sections["Project Status"] = _replace_wdr_label(sections["Project Status"], label, mutation[field])
    if mutation.get("refresh_actions"):
        existing = _parse_wdr_list(_wdr_label(sections["Project Status"], "Next actions"))
        manual, _ = partition_next_actions(existing)
        summaries = action_summaries or []
        _, managed = partition_next_actions(summaries)
        sections["Project Status"] = _replace_wdr_label(
            sections["Project Status"], "Next actions", _render_wdr_list(manual + managed)
        )
    if "meeting_history_append" in mutation:
        existing_rows = parse_meeting_history(sections.get("Meeting Sync History", "## Meeting Sync History"))
        incoming_rows = mutation["meeting_history_append"]
        incoming_keys = [(row["observed_at"], row["entry_id"]) for row in incoming_rows]
        if len(incoming_keys) != len(set(incoming_keys)):
            raise ValueError("duplicate Meeting Sync History key in command")
        merged = {(row["observed_at"], row["entry_id"]): row for row in existing_rows}
        changed = False
        for row in incoming_rows:
            key = (row["observed_at"], row["entry_id"])
            if key in merged:
                if meeting_block(merged[key]) != meeting_block(row):
                    raise ValueError("Meeting Sync History key has different bytes")
                continue
            merged[key] = row
            changed = True
        if changed:
            ordered = [merged[key] for key in sorted(merged, key=lambda row: (row[0].encode("utf-8"), row[1].encode("utf-8")))]
            sections["Meeting Sync History"] = "## Meeting Sync History\n\n" + "".join(meeting_block(row) for row in ordered).rstrip("\n")
    if "roadmap" in mutation:
        roadmap = mutation["roadmap"]
        lines = roadmap.get("lines") if isinstance(roadmap, dict) else None
        if (
            not isinstance(roadmap, dict) or roadmap.get("mode") != "replace" or not isinstance(lines, list) or len(lines) < 2
            or any(not isinstance(line, str) or "\n" in line or "\r" in line or "\x00" in line or unicodedata.normalize("NFC", line) != line or re.match(r"^##(?: |$)", line) for line in lines)
        ):
            raise ValueError("Roadmap mutation is not byte-canonical")
        sections["Roadmap"] = "## Roadmap\n\n" + "\n".join(lines)
    heading_by_slug = {
        "identity": "Identity", "bmm-artifact-index": "BMM Artifact Index", "scope": "Scope", "acceptance": "Acceptance",
        "roadmap": "Roadmap", "cross-workstream-links": "Cross-Workstream Links", "decisions-evidence": "Decisions and Evidence",
        "record-rule": "Record Rule", "checkpoint-sync-log": "Checkpoint Sync Log",
    }
    for owned in mutation.get("owned_sections", []):
        heading = heading_by_slug[owned["section"]]
        if not owned["lines"] or any(re.match(r"^##(?: |$)", line) for line in owned["lines"]):
            raise ValueError("owned section lines may not inject headings")
        body = "\n".join(owned["lines"])
        if owned["mode"] == "append" and heading in sections:
            sections[heading] = sections[heading].rstrip("\n") + "\n" + body
        else:
            sections[heading] = f"## {heading}\n\n{body}"
    return "\n\n".join([preamble] + [sections[name] for name in WDR_SECTION_ORDER if name in sections]) + "\n"


def wdr_current_signature(value: str, workstream_id: str) -> dict[str, Any]:
    if not complete_wdr_valid(value, workstream_id):
        raise ValueError("WDR current signature requires a canonical record")
    _, sections = split_wdr(value)
    identity, status = sections["Identity"], sections["Project Status"]
    return {
        "status": _wdr_label(identity, "Current ADP status"),
        "phase": _wdr_label(identity, "Current BMM phase"),
        "progress": _wdr_label(status, "Progress"),
        "blockers": _parse_wdr_list(_wdr_label(status, "Blockers")),
        "risks": _parse_wdr_list(_wdr_label(status, "Risks")),
        "dependencies": _parse_wdr_list(_wdr_label(status, "Dependencies")),
        "change_notes": _wdr_label(status, "Scope or change notes"),
        "next_actions": _parse_wdr_list(_wdr_label(status, "Next actions")),
        "last_status_sync": _wdr_label(status, "Last status sync"),
    }


def wdr_counter_delta(before: str, after: str, workstream_id: str) -> tuple[int, int]:
    if before == after:
        return 0, 0
    current_changed = wdr_current_signature(before, workstream_id) != wdr_current_signature(after, workstream_id)
    return (1 if current_changed else 0), 1


def canonical_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def drift_semantics(value: dict[str, Any]) -> bool:
    selected = value["selected_workstreams"]
    rows = value["workstreams"]
    row_ids = [row["workstream_id"] for row in rows]
    coverage = len(row_ids) == len(set(row_ids)) and sorted(row_ids) == sorted(selected)
    all_in_sync = coverage and all(row["status"] == "in-sync" for row in rows)
    return coverage and ((value["overall_status"] == "in-sync") is all_in_sync)


def _wdr_label(section: str, label: str) -> str:
    matches = re.findall(rf"(?m)^- {re.escape(label)}: ([^\r\n]+)$", section)
    if len(matches) != 1:
        raise ValueError(f"WDR label is missing or ambiguous: {label}")
    return matches[0]


def parse_wdr_current(raw: bytes, workstream_id: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("WDR is not UTF-8") from error
    if not complete_wdr_valid(text, workstream_id):
        raise ValueError("WDR is incomplete")
    _, sections = split_wdr(text)
    identity = sections["Identity"]
    status = sections["Project Status"]
    next_actions = _parse_wdr_list(_wdr_label(status, "Next actions"))
    _, managed = partition_next_actions(next_actions)
    action_ids = [parse_managed_action_summary(summary)["action_id"] for summary in managed]
    return {
        "workstream_id": workstream_id,
        "phase": _wdr_label(identity, "Current BMM phase"),
        "status": _wdr_label(identity, "Current ADP status"),
        "progress": _wdr_label(status, "Progress"),
        "blockers": _parse_wdr_list(_wdr_label(status, "Blockers")),
        "risks": _parse_wdr_list(_wdr_label(status, "Risks")),
        "dependencies": _parse_wdr_list(_wdr_label(status, "Dependencies")),
        "action_ids": action_ids,
        "next_actions": next_actions,
    }


def status_intent_fixture(registry: dict[str, Any], schema_sha: str, registry_sha: str) -> dict[str, Any]:
    evidence_a = {"source_path": "meetings/m1.md", "source_fingerprint": "sha256:" + "a" * 64, "observed_at": "2026-07-24T02:00:00Z"}
    evidence_b = {"source_path": "checkpoints/c1.md", "source_fingerprint": "sha256:" + "b" * 64, "observed_at": "2026-07-24T02:01:00Z"}
    intents = [
        {
            "contract": expected_contract_ref(registry, "status-mutation-intent/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "intent_id": "intent-checkout-blockers", "origin_producer": "adp-meeting-sync",
            "workstream_id": "l1-checkout", "set": {"blockers": {"mode": "replace", "values": ["Access"]}}, "evidence": [evidence_a],
        },
        {
            "contract": expected_contract_ref(registry, "status-mutation-intent/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "intent_id": "intent-checkout-progress", "origin_producer": "adp-bmm-checkpoint-sync",
            "workstream_id": "l1-checkout", "set": {"progress": "Implementation active", "risks": {"mode": "replace", "values": ["Schedule"]}}, "evidence": [evidence_b],
        },
    ]
    intents.sort(key=lambda row: (row["workstream_id"].encode("utf-8"), row["intent_id"].encode("utf-8")))
    action_command = {
        "contract": expected_contract_ref(registry, "action-ledger-mutation/2.0.0", schema_sha, registry_sha),
        "schema_version": "2.0.0", "command_id": "cmd-action-before-status", "operation": "create", "action_id": "A-STATUS-1",
        "create": {"owner": "FDE-C", "status": "open", "action": "Resolve access", "due_trigger": "next sync", "closure_criteria": "access confirmed", "routing_scope_id": "l1-checkout", "affected_workstreams": ["l1-checkout"]},
        "evidence": [evidence_a],
    }
    patch = {
        "contract": expected_contract_ref(registry, "wdr-mutation/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "command_id": "cmd-status-l1-checkout",
        "issuer": {"producer_id": "adp-status-sync", "capability_id": "sha256:" + "0" * 64},
        "operation": "patch", "workstream_id": "l1-checkout", "expected_wdr_revision": 4, "expected_file_generation": 7,
        "set": {"blockers": {"mode": "replace", "values": ["Access"]}, "progress": "Implementation active", "risks": {"mode": "replace", "values": ["Schedule"]}},
        "consumed_intent_ids": sorted([sha256_bytes(canonical_bytes(row)) for row in intents], key=lambda value: value.encode("utf-8")),
        "evidence": sorted([evidence_a, evidence_b], key=evidence_order_key),
    }
    bindings = [
        {"intent_id": intent["intent_id"], "command_id": patch["command_id"], "fields": sorted(intent["set"], key=lambda value: value.encode("utf-8"))}
        for intent in intents
    ]
    batch = {
        "contract": expected_contract_ref(registry, "status-sync-batch/2.0.0", schema_sha, registry_sha),
        "schema_version": "2.0.0", "batch_id": "batch-status-1",
        "execution_policy": "ordered-stop-on-first-failure-no-rollback",
        "command_order": [action_command["command_id"], patch["command_id"]],
        "accepted_intent_ids": sorted([row["intent_id"] for row in intents], key=lambda value: value.encode("utf-8")),
        "accepted_intents": intents, "intent_bindings": bindings, "action_commands": [action_command], "wdr_patches": [patch],
    }
    return batch


def status_intent_application_semantics(
    batch: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
) -> bool:
    if not validate_registered(batch, schema, registry, "status-sync-batch/2.0.0", schema_sha, registry_sha):
        return False
    intents = batch["accepted_intents"]
    intent_ids = [row["intent_id"] for row in intents]
    if intents != sorted(intents, key=lambda row: (row["workstream_id"].encode("utf-8"), row["intent_id"].encode("utf-8"))):
        return False
    if len(intent_ids) != len(set(intent_ids)) or batch["accepted_intent_ids"] != sorted(intent_ids, key=lambda value: value.encode("utf-8")):
        return False
    if not all(validate_registered(row, schema, registry, "status-mutation-intent/1.0.0", schema_sha, registry_sha) for row in intents):
        return False
    actions = batch["action_commands"]
    patches = batch["wdr_patches"]
    if not all(validate_registered(row, schema, registry, "action-ledger-mutation/2.0.0", schema_sha, registry_sha) for row in actions):
        return False
    if not all(validate_registered(row, schema, registry, "wdr-mutation/1.0.0", schema_sha, registry_sha) for row in patches):
        return False
    try:
        for row in intents + actions + patches:
            canonical_evidence(row["evidence"])
    except ValueError:
        return False
    if actions != sorted(actions, key=lambda row: row["command_id"].encode("utf-8")) or patches != sorted(patches, key=lambda row: (row["workstream_id"].encode("utf-8"), row["command_id"].encode("utf-8"))):
        return False
    patch_workstreams = [row["workstream_id"] for row in patches]
    intent_workstreams = {row["workstream_id"] for row in intents}
    if len(patch_workstreams) != len(set(patch_workstreams)) or set(patch_workstreams) != intent_workstreams:
        return False
    all_commands = actions + patches
    command_ids = [row["command_id"] for row in all_commands]
    if len(command_ids) != len(set(command_ids)) or batch["command_order"] != command_ids:
        return False
    patch_by_id = {row["command_id"]: row for row in patches}
    bindings = batch["intent_bindings"]
    if bindings != sorted(bindings, key=lambda row: row["intent_id"].encode("utf-8")):
        return False
    by_intent: dict[str, list[dict[str, Any]]] = {}
    for binding in bindings:
        by_intent.setdefault(binding["intent_id"], []).append(binding)
    if set(by_intent) != set(intent_ids) or any(len(rows) != 1 for rows in by_intent.values()):
        return False
    merged_by_command: dict[str, dict[str, Any]] = {}
    evidence_by_command: dict[str, list[dict[str, Any]]] = {}
    workstream_by_command: dict[str, str] = {}
    for intent in intents:
        binding = by_intent[intent["intent_id"]][0]
        patch = patch_by_id.get(binding["command_id"])
        fields = sorted(intent["set"], key=lambda value: value.encode("utf-8"))
        if patch is None or patch["workstream_id"] != intent["workstream_id"] or binding["fields"] != fields:
            return False
        merged = merged_by_command.setdefault(patch["command_id"], {})
        for field, value in intent["set"].items():
            if field in merged and canonical_bytes(merged[field]) != canonical_bytes(value):
                return False
            merged[field] = copy.deepcopy(value)
        evidence_by_command.setdefault(patch["command_id"], []).extend(copy.deepcopy(intent["evidence"]))
        workstream_by_command[patch["command_id"]] = intent["workstream_id"]
    if set(merged_by_command) != set(patch_by_id):
        return False
    for command_id, patch in patch_by_id.items():
        evidence = {canonical_bytes(row): row for row in evidence_by_command[command_id]}
        expected_evidence = sorted(evidence.values(), key=evidence_order_key)
        if not (
            patch["issuer"]["producer_id"] == "adp-status-sync"
            and patch["operation"] == "patch"
            and patch["workstream_id"] == workstream_by_command[command_id]
            and patch["set"] == merged_by_command[command_id]
            and patch.get("consumed_intent_ids") == sorted(
                [sha256_bytes(canonical_bytes(intent)) for intent in intents if by_intent[intent["intent_id"]][0]["command_id"] == command_id],
                key=lambda value: value.encode("utf-8"),
            )
            and patch["evidence"] == expected_evidence
        ):
            return False
    return batch["execution_policy"] == "ordered-stop-on-first-failure-no-rollback"


def program_status_wdr_fixture(
    suite: dict[str, Any], registry: dict[str, Any], schema_sha: str, registry_sha: str,
) -> dict[str, Any]:
    workstream_id = "l1-checkout"
    raw = fixture_wdr(workstream_id).encode("utf-8")
    state = {
        "contract": expected_contract_ref(registry, "wdr-file-state/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "workstream_id": workstream_id, "record_path": f"workstreams/{workstream_id}/delivery-record.md",
        "record_fingerprint": sha256_bytes(raw), "wdr_revision": 4, "file_generation": 7, "lifecycle": "active",
    }
    payload = copy.deepcopy(next(row["instance"] for row in suite["contract_schema_vectors"] if row["id"] == "program-status-payload-schema-valid"))
    current = parse_wdr_current(raw, workstream_id)
    payload["workstream_current"] = [{
        **{key: current[key] for key in ("workstream_id", "phase", "status", "progress", "blockers", "risks", "dependencies", "action_ids")},
        "wdr_fingerprint": state["record_fingerprint"], "wdr_revision": state["wdr_revision"], "file_generation": state["file_generation"],
    }]
    return {"selected_workstreams": [workstream_id], "wdrs": {workstream_id: raw}, "wdr_states": {workstream_id: state}, "payload": payload}


def program_status_current_from_wdr_semantics(
    package: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
) -> bool:
    selected = package["selected_workstreams"]
    payload = package["payload"]
    if selected != sorted(set(selected), key=lambda value: value.encode("utf-8")):
        return False
    if set(package["wdrs"]) != set(selected) or set(package["wdr_states"]) != set(selected):
        return False
    if not validate_registered(payload, schema, registry, "program-status-payload/2.0.0", schema_sha, registry_sha):
        return False
    expected_rows = []
    try:
        for workstream_id in selected:
            raw = package["wdrs"][workstream_id]
            state = package["wdr_states"][workstream_id]
            if not validate_registered(state, schema, registry, "wdr-file-state/1.0.0", schema_sha, registry_sha):
                return False
            if not (
                state["workstream_id"] == workstream_id
                and state["record_path"] == f"workstreams/{workstream_id}/delivery-record.md"
                and state["record_fingerprint"] == sha256_bytes(raw)
                and state["lifecycle"] == "active"
            ):
                return False
            current = parse_wdr_current(raw, workstream_id)
            expected_rows.append({
                **{key: current[key] for key in ("workstream_id", "phase", "status", "progress", "blockers", "risks", "dependencies", "action_ids")},
                "wdr_fingerprint": state["record_fingerprint"], "wdr_revision": state["wdr_revision"], "file_generation": state["file_generation"],
            })
    except (KeyError, TypeError, ValueError):
        return False
    return payload["workstream_current"] == expected_rows


def drift_content_fixture(registry: dict[str, Any], schema_sha: str, registry_sha: str) -> dict[str, Any]:
    rows, ledger_raw, ledger_state = refresh_ledger_fixture(registry, schema_sha, registry_sha)
    workstream_id = "l1-checkout"
    snapshot = action_snapshot(rows, workstream_id, ledger_state["ledger_fingerprint"], ledger_state["ledger_revision"])
    wdr_command = {"set": {"refresh_actions": True}}
    wdr_raw = apply_wdr_patch(fixture_wdr(workstream_id), wdr_command, [row["rendered_summary"] for row in snapshot["actions"]]).encode("utf-8")
    wdr_state = {
        "contract": expected_contract_ref(registry, "wdr-file-state/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "workstream_id": workstream_id, "record_path": f"workstreams/{workstream_id}/delivery-record.md",
        "record_fingerprint": sha256_bytes(wdr_raw), "wdr_revision": 5, "file_generation": 8, "lifecycle": "active",
    }
    sidecar = {
        "contract": expected_contract_ref(registry, "wdr-action-projection/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "workstream_id": workstream_id,
        "ledger_fingerprint": ledger_state["ledger_fingerprint"], "ledger_revision": ledger_state["ledger_revision"],
        "wdr_revision": wdr_state["wdr_revision"], "file_generation": wdr_state["file_generation"],
        "renderer_id": "urn:adp:wdr-action-renderer:1.0.0", "renderer_sha256": registry["protocol"]["sha256"],
        "actions": copy.deepcopy(snapshot["actions"]),
    }
    package = {
        "generation_id": "sha256:" + "1" * 64, "selection_policy_id": "sha256:" + "2" * 64,
        "selected_workstreams": [workstream_id], "ledger_raw": ledger_raw, "ledger_state": ledger_state,
        "wdrs": {workstream_id: wdr_raw}, "wdr_states": {workstream_id: wdr_state}, "sidecars": {workstream_id: sidecar},
    }
    package["verdict"] = expected_drift_verdict(package, registry, schema_sha, registry_sha)
    return package


def drift_finding(workstream_id: str, kind: str, action_diff: dict[str, Any] | None = None) -> dict[str, Any]:
    source_path = f"workstreams/{workstream_id}/delivery-record.md"
    body = {
        "kind": "action-projection-drift" if action_diff is not None else kind,
        "repairability": "repairable" if action_diff is not None else "non-repairable",
        "severity": "blocked" if action_diff is not None else "warning",
        "workstream_id": workstream_id,
        "action_id": None if action_diff is None else action_diff["action_id"],
        "action_diff": copy.deepcopy(action_diff),
        "source_path": source_path,
        "source_line": 42 if action_diff is not None else None,
    }
    identity_body = {key: value for key, value in body.items() if key not in {"source_path", "source_line"}}
    return {"finding_id": sha256_bytes(canonical_bytes(identity_body)), **body}


def expected_drift_verdict(package: dict[str, Any], registry: dict[str, Any], schema_sha: str, registry_sha: str) -> dict[str, Any]:
    ledger_raw = package["ledger_raw"]
    ledger_state = package["ledger_state"]
    ledger_rows = parse_action_ledger(ledger_raw)
    rows = []
    for workstream_id in package["selected_workstreams"]:
        wdr_raw = package["wdrs"][workstream_id]
        wdr_state = package["wdr_states"][workstream_id]
        sidecar = package["sidecars"][workstream_id]
        findings: list[dict[str, Any]] = []
        action_diffs: list[dict[str, Any]] = []
        expected_snapshot = action_snapshot(ledger_rows, workstream_id, ledger_state["ledger_fingerprint"], ledger_state["ledger_revision"])
        if sidecar["ledger_fingerprint"] != ledger_state["ledger_fingerprint"]:
            findings.append(drift_finding(workstream_id, "ledger-fingerprint-mismatch"))
        if sidecar["ledger_revision"] != ledger_state["ledger_revision"]:
            findings.append(drift_finding(workstream_id, "ledger-revision-mismatch"))
        expected_by_id = {row["action_id"]: row for row in expected_snapshot["actions"]}
        sidecar_by_id = {row["action_id"]: row for row in sidecar["actions"]}
        current = parse_wdr_current(wdr_raw, workstream_id)
        _, current_managed = partition_next_actions(current["next_actions"])
        wdr_by_id = {
            parsed["action_id"]: summary
            for summary in current_managed
            if (parsed := parse_managed_action_summary(summary)) is not None
        }
        all_action_ids = sorted(set(expected_by_id) | set(sidecar_by_id) | set(wdr_by_id), key=lambda value: value.encode("utf-8"))
        for action_id in all_action_ids:
            expected = expected_by_id.get(action_id)
            sidecar_record = sidecar_by_id.get(action_id)
            wdr_summary = wdr_by_id.get(action_id)
            projection_present = sidecar_record is not None or wdr_summary is not None
            rendered = wdr_summary if wdr_summary is not None else (sidecar_record or {}).get("rendered_summary")
            if expected is not None and (sidecar_record is None or wdr_summary is None):
                drift_kind = "missing-from-wdr"
            elif expected is None and projection_present:
                drift_kind = "orphan-in-wdr"
            elif expected is not None and (
                sidecar_record != expected or wdr_summary != expected["rendered_summary"]
            ):
                drift_kind = "content-mismatch"
            else:
                continue
            diff = {
                "action_id": action_id,
                "drift_kind": drift_kind,
                "ledger_present": expected is not None,
                "wdr_present": projection_present,
                "ledger_revision": None if expected is None else expected["action_revision"],
                "wdr_rendered_sha256": None if rendered is None else sha256_bytes(rendered.encode("utf-8")),
            }
            action_diffs.append(diff)
            findings.append(drift_finding(workstream_id, "action-projection-drift", diff))
        if sidecar["wdr_revision"] != wdr_state["wdr_revision"] or sidecar["file_generation"] != wdr_state["file_generation"]:
            findings.append(drift_finding(workstream_id, "wdr-lineage-mismatch"))
        findings = sorted({row["finding_id"]: row for row in findings}.values(), key=lambda row: row["finding_id"].encode("utf-8"))
        rows.append({
            "workstream_id": workstream_id, "wdr_fingerprint": sha256_bytes(wdr_raw),
            "wdr_revision": wdr_state["wdr_revision"], "file_generation": wdr_state["file_generation"],
            "sidecar_fingerprint": sha256_bytes(canonical_bytes(sidecar)), "sidecar_ledger_fingerprint": sidecar["ledger_fingerprint"],
            "status": "in-sync" if not findings else "drift", "action_diffs": action_diffs,
            "findings": findings, "finding_ids": [row["finding_id"] for row in findings],
        })
    verdict = {
        "contract": expected_contract_ref(registry, "action-projection-drift-verdict/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "generation_id": package["generation_id"], "selection_policy_id": package["selection_policy_id"],
        "ledger_fingerprint": ledger_state["ledger_fingerprint"], "selected_workstreams": copy.deepcopy(package["selected_workstreams"]),
        "workstreams": rows, "overall_status": "in-sync" if all(row["status"] == "in-sync" for row in rows) else "degraded",
    }
    verdict["verdict_id"] = sha256_bytes(canonical_bytes(verdict))
    return verdict


def action_projection_drift_content_semantics(
    package: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
) -> bool:
    try:
        selected = package["selected_workstreams"]
        if selected != sorted(set(selected), key=lambda value: value.encode("utf-8")):
            return False
        if set(package["wdrs"]) != set(selected) or set(package["wdr_states"]) != set(selected) or set(package["sidecars"]) != set(selected):
            return False
        ledger_rows = parse_action_ledger(package["ledger_raw"])
        ledger_state = package["ledger_state"]
        if not validate_registered(ledger_state, schema, registry, "action-ledger-state/1.0.0", schema_sha, registry_sha):
            return False
        if ledger_state != action_ledger_state_document(
            ledger_rows, package["ledger_raw"], ledger_state["ledger_revision"], ledger_state["applied_commands"], registry, schema_sha, registry_sha
        ):
            return False
        for workstream_id in selected:
            state = package["wdr_states"][workstream_id]
            sidecar = package["sidecars"][workstream_id]
            raw = package["wdrs"][workstream_id]
            if not validate_registered(state, schema, registry, "wdr-file-state/1.0.0", schema_sha, registry_sha):
                return False
            if not validate_registered(sidecar, schema, registry, "wdr-action-projection/1.0.0", schema_sha, registry_sha):
                return False
            if not (
                state["workstream_id"] == workstream_id
                and state["record_path"] == f"workstreams/{workstream_id}/delivery-record.md"
                and state["record_fingerprint"] == sha256_bytes(raw)
                and state["lifecycle"] == "active"
                and sidecar["workstream_id"] == workstream_id
                and sidecar["renderer_id"] == "urn:adp:wdr-action-renderer:1.0.0"
                and sidecar["renderer_sha256"] == registry["protocol"]["sha256"]
            ):
                return False
        verdict = package["verdict"]
        return (
            validate_registered(verdict, schema, registry, "action-projection-drift-verdict/1.0.0", schema_sha, registry_sha)
            and verdict == expected_drift_verdict(package, registry, schema_sha, registry_sha)
        )
    except (KeyError, TypeError, ValueError):
        return False


def _semver_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value)
    if match is None:
        raise ValueError("runtime version is not strict semver")
    return tuple(int(part) for part in match.groups())


def _conformance_signing_payload(row: dict[str, Any]) -> bytes:
    body = copy.deepcopy(row)
    body.pop("result_id", None)
    body["provenance"].pop("signature", None)
    return canonical_bytes(body)


FIXTURE_RELEASE_SIGNERS = (
    ("python-production-adapter", "native-posix", "python-production-build-1", ["production-adapter", "real-posix-fault-injection"], "cpython", "3.10.0", "fixture-posix-ci", "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f", "macos", "posix-flock"),
    ("node-production-adapter", "native-windows", "node-production-build-1", ["native-windows-ci", "production-adapter"], "node", "22.0.0", "fixture-windows-ci", "1f1e1d1c1b1a191817161514131211100f0e0d0c0b0a09080706050403020100", "windows", "windows-lockfileex"),
)


def _fixture_release_trust_roots() -> list[dict[str, Any]]:
    return [
        {
            "key_id": signer_key,
            "platform": platform,
            "implementation_owner_id": f"owner-{implementation_id}",
            "allowed_implementation_ids": [implementation_id],
            "public_key_base64": base64.b64encode(ed25519_public_key(bytes.fromhex(seed_hex))).decode("ascii"),
            "not_before": "2026-01-01T00:00:00Z",
            "not_after": "2027-01-01T00:00:00Z",
        }
        for implementation_id, platform, _, _, _, _, signer_key, seed_hex, _, _ in FIXTURE_RELEASE_SIGNERS
    ]


def design_release_registry_fixture(registry: dict[str, Any]) -> dict[str, Any]:
    fixture = copy.deepcopy(registry)
    fixture["conformance_suite"]["implementation_conformance_status"] = "passed"
    fixture["evidence_trust"]["trust_roots"] = _fixture_release_trust_roots()
    return fixture


def _is_design_release_registry(registry: dict[str, Any]) -> bool:
    return (
        registry["conformance_suite"]["implementation_conformance_status"] == "passed"
        and registry["evidence_trust"]["trust_roots"] == _fixture_release_trust_roots()
    )


def release_gate_accepts(
    receipts: list[dict[str, Any]], expected_ids: list[str], hashes: dict[str, str], registry: dict[str, Any], evidence_blobs: dict[str, bytes],
    security_context: dict[str, Any],
) -> bool:
    if not isinstance(security_context, dict) or security_context != {
        "clock_source": "host-secure-clock-v1",
        "evaluation_time": security_context.get("evaluation_time"),
        "available": True,
    }:
        return False
    try:
        evaluation_time = _utc_instant(security_context["evaluation_time"])
    except (KeyError, TypeError, ValueError):
        return False
    if len(receipts) < 2:
        return False
    implementation_ids = {row["implementation_id"] for row in receipts}
    build_ids = {row["adapter_build_id"] for row in receipts}
    signer_ids = {row["provenance"]["signer_key_id"] for row in receipts}
    if len(implementation_ids) != len(receipts) or len(build_ids) != len(receipts) or len(signer_ids) != len(receipts):
        return False
    platforms = {row["platform"] for row in receipts}
    if not {"native-posix", "native-windows"} <= platforms:
        return False
    trust_roots = {row["key_id"]: row for row in registry["evidence_trust"]["trust_roots"]}
    if (
        len(trust_roots) != len(registry["evidence_trust"]["trust_roots"])
        or len(trust_roots) < registry["evidence_trust"]["minimum_production_trust_roots"]
    ):
        return False
    lock_profile = registry["lock_profile"]
    profile_body = {key: value for key, value in lock_profile.items() if key != "profile_id"}
    if lock_profile["profile_id"] != sha256_bytes(canonical_bytes(profile_body)):
        return False
    replay_keys: set[tuple[str, str, int, str]] = set()
    owner_ids: set[str] = set()
    for row in receipts:
        body = dict(row)
        result_id = body.pop("result_id", None)
        if result_id != sha256_bytes(canonical_bytes(body)):
            return False
        if row["evidence_kind"] != "implementation-conformance" or not row["native_durability_exercised"]:
            return False
        if row["failed_vector_ids"] or sorted(row["passed_vector_ids"]) != expected_ids:
            return False
        if any(row[f"{name}_sha256"] != hashes[name] for name in ("registry", "suite", "schema", "protocol")):
            return False
        runtime = row["runtime"]
        policy = registry["runtime_policy"].get(runtime["implementation"])
        runtime_version = _semver_tuple(runtime["version"])
        if (
            policy is None
            or not (_semver_tuple(policy["minimum_inclusive"]) <= runtime_version < _semver_tuple(policy["maximum_exclusive"]))
            or ("allowed_major_versions" in policy and runtime_version[0] not in policy["allowed_major_versions"])
        ):
            return False
        if runtime["build_digest"] != row["adapter_build_id"]:
            return False
        classes = set(row["evidence_classes"])
        if "production-adapter" not in classes:
            return False
        if row["platform"] == "native-posix" and "real-posix-fault-injection" not in classes:
            return False
        if row["platform"] == "native-windows" and "native-windows-ci" not in classes:
            return False
        provenance = row["provenance"]
        root = trust_roots.get(provenance["signer_key_id"])
        if (
            root is None or root["platform"] != row["platform"]
            or root.get("allowed_implementation_ids") != [row["implementation_id"]]
            or not isinstance(root.get("implementation_owner_id"), str)
            or not root["implementation_owner_id"]
            or provenance["signature_algorithm"] != registry["evidence_trust"]["signature_algorithm"]
        ):
            return False
        owner_ids.add(root["implementation_owner_id"])
        executed_at = _utc_instant(row["executed_at"])
        if runtime["implementation"] == "cpython" and evaluation_time >= _utc_instant(policy["support_review_before"]):
            return False
        if (
            provenance["signed_at"] != row["executed_at"]
            or executed_at > evaluation_time
            or not (_utc_instant(root["not_before"]) <= executed_at < _utc_instant(root["not_after"]))
            or not (_utc_instant(root["not_before"]) <= evaluation_time < _utc_instant(root["not_after"]))
        ):
            return False
        expected_family = "posix" if row["platform"] == "native-posix" else "windows"
        if provenance["os_family"] != expected_family:
            return False
        replay_key = (provenance["signer_key_id"], provenance["ci_run_id"], provenance["ci_attempt"], provenance["evidence_nonce"])
        if replay_key in replay_keys:
            return False
        replay_keys.add(replay_key)
        try:
            public_key = base64.b64decode(root["public_key_base64"], validate=True)
            signature = base64.b64decode(provenance["signature"], validate=True)
        except (ValueError, base64.binascii.Error):
            return False
        if not ed25519_verify(public_key, _conformance_signing_payload(row), signature):
            return False
        blob_ids = {provenance["test_log_sha256"], provenance["fault_matrix_sha256"], row["lock_evidence"]["evidence_log_sha256"]}
        if any(blob_id not in evidence_blobs or sha256_bytes(evidence_blobs[blob_id]) != blob_id for blob_id in blob_ids):
            return False
        lock = row["lock_evidence"]
        expected_primitive = "posix-flock" if row["platform"] == "native-posix" else "windows-lockfileex"
        if not (
            lock["lock_profile_id"] == lock_profile["profile_id"] and lock["primitive"] == expected_primitive
            and lock["fact_lock_path"] == lock_profile["fact_lock"]["path"]
            and lock["panel_lock_path"] == lock_profile["panel_lock"]["path"]
            and lock["filesystem_kind"] in lock_profile["supported_filesystems"]
            and all(lock[field] for field in ("multiprocess_contention_passed", "crash_release_passed", "order_passed", "timeout_passed", "upgrade_rejected"))
        ):
            return False
    return len(owner_ids) == len(receipts)


def implementation_conformance_receipts(
    expected_ids: list[str], hashes: dict[str, str], registry: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    rows = []
    blobs: dict[str, bytes] = {}
    for implementation_id, platform, build_label, classes, runtime_name, runtime_version, signer_key, seed_hex, os_name, primitive in FIXTURE_RELEASE_SIGNERS:
        build_id = sha256_bytes(build_label.encode("utf-8"))
        test_log = f"{platform}:full-suite:{len(expected_ids)}:passed\n".encode("utf-8")
        fault_log = f"{platform}:native-fault-matrix:passed\n".encode("utf-8")
        lock_log = f"{platform}:multiprocess-crash-order-timeout-upgrade:passed\n".encode("utf-8")
        for blob in (test_log, fault_log, lock_log):
            blobs[sha256_bytes(blob)] = blob
        row = {
            "schema_version": "1.0.0", "evidence_kind": "implementation-conformance",
            "implementation_id": implementation_id, "implementation_version": "1.0.0", "platform": platform,
            "host_platform": f"{os_name}-x86_64", "runtime": {"implementation": runtime_name, "version": runtime_version, "executable_sha256": sha256_bytes(f"{runtime_name}-executable".encode()), "build_digest": build_id},
            "native_durability_exercised": True,
            "registry_sha256": hashes["registry"], "suite_sha256": hashes["suite"], "schema_sha256": hashes["schema"],
            "protocol_sha256": hashes["protocol"], "passed_vector_ids": copy.deepcopy(expected_ids), "failed_vector_ids": [],
            "executed_at": "2026-07-24T03:00:00Z", "adapter_build_id": build_id, "evidence_classes": classes,
            "lock_evidence": {
                "lock_profile_id": registry["lock_profile"]["profile_id"], "primitive": primitive,
                "fact_lock_path": registry["lock_profile"]["fact_lock"]["path"], "panel_lock_path": registry["lock_profile"]["panel_lock"]["path"],
                "filesystem_kind": "local", "multiprocess_contention_passed": True, "crash_release_passed": True,
                "order_passed": True, "timeout_passed": True, "upgrade_rejected": True, "evidence_log_sha256": sha256_bytes(lock_log),
            },
            "provenance": {
                "ci_run_id": f"ci-{runtime_name}-001", "ci_attempt": 1, "os_family": "posix" if platform == "native-posix" else "windows",
                "os_name": os_name, "os_version": "2026.1", "architecture": "x86_64", "test_log_sha256": sha256_bytes(test_log),
                "fault_matrix_sha256": sha256_bytes(fault_log), "signer_key_id": signer_key, "signature_algorithm": "Ed25519",
                "evidence_nonce": f"nonce-{runtime_name}-001", "signed_at": "2026-07-24T03:00:00Z", "signature": "",
            },
        }
        row["provenance"]["signature"] = base64.b64encode(ed25519_sign(bytes.fromhex(seed_hex), _conformance_signing_payload(row))).decode("ascii")
        row["result_id"] = sha256_bytes(canonical_bytes(row))
        rows.append(row)
    return rows, blobs


def _receipt_evidence_blob_ids(receipt: dict[str, Any]) -> list[str]:
    return sorted({
        receipt["provenance"]["test_log_sha256"],
        receipt["provenance"]["fault_matrix_sha256"],
        receipt["lock_evidence"]["evidence_log_sha256"],
    }, key=lambda value: value.encode("utf-8"))


def release_evidence_set_fixture(
    receipts: list[dict[str, Any]], evidence_blobs: dict[str, bytes], registry: dict[str, Any],
    schema_sha: str, registry_sha: str,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    entries: list[dict[str, Any]] = []
    store: dict[str, bytes] = {}
    for receipt in sorted(receipts, key=lambda row: row["result_id"].encode("utf-8")):
        receipt_raw = canonical_bytes(receipt)
        receipt_path = runtime_path(registry, "release_evidence_receipt_template", result_id=receipt["result_id"])
        store[receipt_path] = receipt_raw
        blobs = []
        for blob_id in _receipt_evidence_blob_ids(receipt):
            blob_path = runtime_path(registry, "release_evidence_blob_template", blob_id=blob_id)
            raw = evidence_blobs[blob_id]
            store[blob_path] = raw
            blobs.append({"sha256": blob_id, "path": blob_path})
        entries.append({
            "result_id": receipt["result_id"], "receipt_path": receipt_path,
            "receipt_sha256": sha256_bytes(receipt_raw), "evidence_blobs": blobs,
        })
    release_set = {
        "contract": expected_contract_ref(registry, "release-evidence-set/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "set_generation": 1, "previous_set_id": None,
        "trust_domain": "design-mock" if _is_design_release_registry(registry) else "production",
        "registry_sha256": registry_sha, "accepted_at": "2026-07-24T03:00:01Z", "entries": entries,
    }
    release_set["release_evidence_set_id"] = sha256_bytes(canonical_bytes(release_set))
    set_raw = canonical_bytes(release_set)
    set_path = runtime_path(registry, "release_evidence_set_archive_template", release_set_id=release_set["release_evidence_set_id"])
    transition_id = "release-evidence-bootstrap-1"
    transition = {
        "contract": expected_contract_ref(registry, "release-evidence-transition-receipt/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "transition_id": transition_id,
        "before_generation": 0, "before_set_id": None, "after_generation": 1,
        "after_set_id": release_set["release_evidence_set_id"], "journal_id": "journal-release-evidence-bootstrap-1",
        "status": "committed", "committed_at": release_set["accepted_at"],
    }
    transition["receipt_id"] = sha256_bytes(canonical_bytes(transition))
    transition_raw = canonical_bytes(transition)
    transition_path = runtime_path(registry, "release_evidence_transition_receipt_template", transaction_id=transition_id)
    core_journal, core_marker = transition_journal_fixture(
        "release-evidence", transition_id, transition["journal_id"],
        [
            {"role": "release-evidence", "operation": "create", "path": set_path, "before_raw": None, "after_raw": set_raw},
            {"role": "release-evidence", "operation": "create", "path": registry["runtime_paths"]["release_evidence_set"]["path"], "before_raw": None, "after_raw": set_raw},
        ],
        transition_path, transition_raw, registry, schema_sha, registry_sha,
    )
    core_journal_path = runtime_path(registry, "release_evidence_journal_template", transaction_id=transition_id)
    core_marker_path = runtime_path(registry, "release_evidence_terminal_marker_template", transaction_id=transition_id)
    core_journal_raw, core_marker_raw = canonical_bytes(core_journal), canonical_bytes(core_marker)
    history = {
        "contract": expected_contract_ref(registry, "release-evidence-history-index/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "current_generation": 1,
        "current_set_id": release_set["release_evidence_set_id"],
        "entries": [{
            "set_generation": 1, "set_id": release_set["release_evidence_set_id"],
            "accepted_at": release_set["accepted_at"],
            "set_path": set_path, "set_sha256": sha256_bytes(set_raw),
            "transition_receipt_path": transition_path, "transition_receipt_sha256": sha256_bytes(transition_raw),
            "journal_path": core_journal_path, "journal_sha256": sha256_bytes(core_journal_raw),
            "terminal_marker_path": core_marker_path, "terminal_marker_sha256": sha256_bytes(core_marker_raw),
        }],
    }
    history["index_id"] = sha256_bytes(canonical_bytes(history))
    store[registry["runtime_paths"]["release_evidence_set"]["path"]] = set_raw
    store[registry["runtime_paths"]["release_evidence_history_index"]["path"]] = canonical_bytes(history)
    store[set_path] = set_raw
    store[transition_path] = transition_raw
    store[core_journal_path] = core_journal_raw
    store[core_marker_path] = core_marker_raw
    return release_set, store


def load_release_evidence_set(
    package: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
    expected_ids: list[str], hashes: dict[str, str], security_context: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, bytes]] | None:
    try:
        registry_raw = package["registry_raw"]
        if not isinstance(registry_raw, bytes) or canonical_bytes(registry) != registry_raw or sha256_bytes(registry_raw) != registry_sha:
            return None
        store = package["release_store"]
        set_path = registry["runtime_paths"]["release_evidence_set"]["path"]
        set_raw = store[set_path]
        release_set = json.loads(set_raw)
        evaluation_time = _utc_instant(security_context["evaluation_time"])
        if not (
            canonical_bytes(release_set) == set_raw
            and validate_registered(release_set, schema, registry, "release-evidence-set/1.0.0", schema_sha, registry_sha)
            and release_set["release_evidence_set_id"] == sha256_bytes(canonical_bytes({key: value for key, value in release_set.items() if key != "release_evidence_set_id"}))
            and release_set["registry_sha256"] == registry_sha
            and release_set["trust_domain"] == ("design-mock" if _is_design_release_registry(registry) else "production")
            and _utc_instant(release_set["accepted_at"]) <= evaluation_time
        ):
            return None
        history_path = registry["runtime_paths"]["release_evidence_history_index"]["path"]
        history_raw = store[history_path]
        history = json.loads(history_raw)
        if not (
            canonical_bytes(history) == history_raw
            and validate_registered(history, schema, registry, "release-evidence-history-index/1.0.0", schema_sha, registry_sha)
            and history["index_id"] == sha256_bytes(canonical_bytes({key: value for key, value in history.items() if key != "index_id"}))
            and history["current_generation"] == release_set["set_generation"]
            and history["current_set_id"] == release_set["release_evidence_set_id"]
        ):
            return None
        expected_paths = {set_path, history_path}
        previous_set_id = None
        previous_accepted_at: datetime | None = None
        for expected_generation, entry in enumerate(history["entries"], start=1):
            archive_path = runtime_path(registry, "release_evidence_set_archive_template", release_set_id=entry["set_id"])
            archive_raw = store[entry["set_path"]]
            archive_set = json.loads(archive_raw)
            transition_raw = store[entry["transition_receipt_path"]]
            transition = json.loads(transition_raw)
            transition_path = runtime_path(registry, "release_evidence_transition_receipt_template", transaction_id=transition["transition_id"])
            journal_raw = store[entry["journal_path"]]
            terminal_raw = store[entry["terminal_marker_path"]]
            transition_journal = json.loads(journal_raw)
            terminal_marker = json.loads(terminal_raw)
            accepted_at = _utc_instant(archive_set["accepted_at"])
            if not (
                entry["set_generation"] == expected_generation
                and entry["set_path"] == archive_path
                and entry["set_sha256"] == sha256_bytes(archive_raw)
                and entry["transition_receipt_path"] == transition_path
                and entry["transition_receipt_sha256"] == sha256_bytes(transition_raw)
                and canonical_bytes(archive_set) == archive_raw
                and validate_registered(archive_set, schema, registry, "release-evidence-set/1.0.0", schema_sha, registry_sha)
                and archive_set["release_evidence_set_id"] == entry["set_id"]
                and archive_set["set_generation"] == expected_generation
                and archive_set["previous_set_id"] == previous_set_id
                and canonical_bytes(transition) == transition_raw
                and validate_registered(transition, schema, registry, "release-evidence-transition-receipt/1.0.0", schema_sha, registry_sha)
                and transition["receipt_id"] == sha256_bytes(canonical_bytes({key: value for key, value in transition.items() if key != "receipt_id"}))
                and transition["before_generation"] == expected_generation - 1
                and transition["before_set_id"] == previous_set_id
                and transition["after_generation"] == expected_generation
                and transition["after_set_id"] == entry["set_id"]
                and transition["committed_at"] == archive_set["accepted_at"]
                and entry["accepted_at"] == archive_set["accepted_at"]
                and (previous_accepted_at is None or previous_accepted_at < accepted_at)
                and accepted_at <= evaluation_time
                and entry["journal_path"] == runtime_path(registry, "release_evidence_journal_template", transaction_id=transition["transition_id"])
                and entry["journal_sha256"] == sha256_bytes(journal_raw)
                and entry["terminal_marker_path"] == runtime_path(registry, "release_evidence_terminal_marker_template", transaction_id=transition["transition_id"])
                and entry["terminal_marker_sha256"] == sha256_bytes(terminal_raw)
                and canonical_bytes(transition_journal) == journal_raw
                and canonical_bytes(terminal_marker) == terminal_raw
                and journal_semantics(transition_journal, terminal_marker, schema, registry, schema_sha, registry_sha)
                and terminal_marker["state"] == "committed"
                and transition_journal["transaction_id"] == transition["transition_id"]
                and transition_journal["journal_id"] == transition["journal_id"]
                and transition_journal["receipt_target_paths"] == [entry["transition_receipt_path"]]
            ):
                return None
            expected_paths.update({entry["set_path"], entry["transition_receipt_path"], entry["journal_path"], entry["terminal_marker_path"]})
            historical_receipts: list[dict[str, Any]] = []
            historical_blobs: dict[str, bytes] = {}
            for historical_entry in archive_set["entries"]:
                expected_paths.add(historical_entry["receipt_path"])
                expected_paths.update(blob["path"] for blob in historical_entry["evidence_blobs"])
                historical_receipt_raw = store[historical_entry["receipt_path"]]
                historical_receipt = json.loads(historical_receipt_raw)
                if not (
                    canonical_bytes(historical_receipt) == historical_receipt_raw
                    and sha256_bytes(historical_receipt_raw) == historical_entry["receipt_sha256"]
                    and historical_receipt["result_id"] == historical_entry["result_id"]
                    and validate(historical_receipt, schema, "conformanceResultV1")
                ):
                    return None
                expected_historical_blobs = [
                    {"sha256": blob_id, "path": runtime_path(registry, "release_evidence_blob_template", blob_id=blob_id)}
                    for blob_id in _receipt_evidence_blob_ids(historical_receipt)
                ]
                if historical_entry["evidence_blobs"] != expected_historical_blobs:
                    return None
                for blob in historical_entry["evidence_blobs"]:
                    blob_raw = store[blob["path"]]
                    if sha256_bytes(blob_raw) != blob["sha256"]:
                        return None
                    historical_blobs[blob["sha256"]] = blob_raw
                historical_receipts.append(historical_receipt)
            if not release_gate_accepts(
                historical_receipts, expected_ids, hashes, registry, historical_blobs,
                {"clock_source": "host-secure-clock-v1", "evaluation_time": archive_set["accepted_at"], "available": True},
            ):
                return None
            previous_set_id = entry["set_id"]
            previous_accepted_at = accepted_at
        if len(history["entries"]) != history["current_generation"] or previous_set_id != history["current_set_id"]:
            return None
        entries = release_set["entries"]
        if entries != sorted(entries, key=lambda row: row["result_id"].encode("utf-8")):
            return None
        result_ids = [row["result_id"] for row in entries]
        receipt_paths = [row["receipt_path"] for row in entries]
        if len(result_ids) != len(set(result_ids)) or len(receipt_paths) != len(set(receipt_paths)):
            return None
        receipts: list[dict[str, Any]] = []
        blobs: dict[str, bytes] = {}
        for entry in entries:
            expected_receipt_path = runtime_path(registry, "release_evidence_receipt_template", result_id=entry["result_id"])
            if entry["receipt_path"] != expected_receipt_path:
                return None
            receipt_raw = store[entry["receipt_path"]]
            receipt = json.loads(receipt_raw)
            if not (
                canonical_bytes(receipt) == receipt_raw and sha256_bytes(receipt_raw) == entry["receipt_sha256"]
                and receipt["result_id"] == entry["result_id"] and validate(receipt, schema, "conformanceResultV1")
            ):
                return None
            expected_paths.add(entry["receipt_path"])
            expected_blob_ids = _receipt_evidence_blob_ids(receipt)
            if entry["evidence_blobs"] != [
                {"sha256": blob_id, "path": runtime_path(registry, "release_evidence_blob_template", blob_id=blob_id)}
                for blob_id in expected_blob_ids
            ]:
                return None
            for blob in entry["evidence_blobs"]:
                raw = store[blob["path"]]
                if sha256_bytes(raw) != blob["sha256"]:
                    return None
                expected_paths.add(blob["path"])
                blobs[blob["sha256"]] = raw
            receipts.append(receipt)
        if set(store) != expected_paths or not release_gate_accepts(receipts, expected_ids, hashes, registry, blobs, security_context):
            return None
        return release_set, receipts, blobs
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def release_evidence_transition_fixture(
    receipts: list[dict[str, Any]], evidence_blobs: dict[str, bytes], registry: dict[str, Any],
    schema_sha: str, registry_sha: str, after_accepted_at: str = "2026-07-24T03:10:00Z",
) -> dict[str, Any]:
    before_set, before_store = release_evidence_set_fixture(receipts, evidence_blobs, registry, schema_sha, registry_sha)
    before_history_path = registry["runtime_paths"]["release_evidence_history_index"]["path"]
    before_history_raw = before_store[before_history_path]
    before_history = json.loads(before_history_raw)
    after_set = copy.deepcopy(before_set)
    after_set.update({
        "set_generation": 2, "previous_set_id": before_set["release_evidence_set_id"],
        "accepted_at": after_accepted_at,
    })
    after_set["release_evidence_set_id"] = sha256_bytes(canonical_bytes({key: value for key, value in after_set.items() if key != "release_evidence_set_id"}))
    after_set_raw = canonical_bytes(after_set)
    after_archive_path = runtime_path(registry, "release_evidence_set_archive_template", release_set_id=after_set["release_evidence_set_id"])
    transition_id = "release-evidence-transition-2"
    journal_id = "journal-release-evidence-transition-2"
    transition = {
        "contract": expected_contract_ref(registry, "release-evidence-transition-receipt/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "transition_id": transition_id,
        "before_generation": 1, "before_set_id": before_set["release_evidence_set_id"],
        "after_generation": 2, "after_set_id": after_set["release_evidence_set_id"],
        "journal_id": journal_id, "status": "committed", "committed_at": after_set["accepted_at"],
    }
    transition["receipt_id"] = sha256_bytes(canonical_bytes(transition))
    transition_raw = canonical_bytes(transition)
    transition_path = runtime_path(registry, "release_evidence_transition_receipt_template", transaction_id=transition_id)
    current_path = registry["runtime_paths"]["release_evidence_set"]["path"]
    core_journal, core_marker = transition_journal_fixture(
        "release-evidence", transition_id, journal_id,
        [
            {"role": "release-evidence", "operation": "create", "path": after_archive_path, "before_raw": None, "after_raw": after_set_raw},
            {"role": "release-evidence", "operation": "replace", "path": current_path, "before_raw": canonical_bytes(before_set), "after_raw": after_set_raw},
        ],
        transition_path, transition_raw, registry, schema_sha, registry_sha,
    )
    core_journal_path = runtime_path(registry, "release_evidence_journal_template", transaction_id=transition_id)
    core_marker_path = runtime_path(registry, "release_evidence_terminal_marker_template", transaction_id=transition_id)
    core_journal_raw, core_marker_raw = canonical_bytes(core_journal), canonical_bytes(core_marker)
    after_history = copy.deepcopy(before_history)
    after_history.update({"current_generation": 2, "current_set_id": after_set["release_evidence_set_id"]})
    after_history["entries"].append({
        "set_generation": 2, "set_id": after_set["release_evidence_set_id"],
        "accepted_at": after_set["accepted_at"],
        "set_path": after_archive_path, "set_sha256": sha256_bytes(after_set_raw),
        "transition_receipt_path": transition_path, "transition_receipt_sha256": sha256_bytes(transition_raw),
        "journal_path": core_journal_path, "journal_sha256": sha256_bytes(core_journal_raw),
        "terminal_marker_path": core_marker_path, "terminal_marker_sha256": sha256_bytes(core_marker_raw),
    })
    after_history["index_id"] = sha256_bytes(canonical_bytes({key: value for key, value in after_history.items() if key != "index_id"}))
    after_history_raw = canonical_bytes(after_history)
    journal, marker = transition_journal_fixture(
        "release-evidence", transition_id, journal_id,
        [
            {"role": "release-evidence", "operation": "create", "path": after_archive_path, "before_raw": None, "after_raw": after_set_raw},
            {"role": "release-evidence", "operation": "replace", "path": current_path, "before_raw": canonical_bytes(before_set), "after_raw": after_set_raw},
            {"role": "history-index", "operation": "replace", "path": before_history_path, "before_raw": before_history_raw, "after_raw": after_history_raw},
        ],
        transition_path, transition_raw, registry, schema_sha, registry_sha,
    )
    final_store = copy.deepcopy(before_store)
    final_store.update({
        current_path: after_set_raw, after_archive_path: after_set_raw,
        before_history_path: after_history_raw, transition_path: transition_raw,
        core_journal_path: core_journal_raw, core_marker_path: core_marker_raw,
    })
    target_images = {
        after_archive_path: {"before": None, "after": after_set_raw},
        current_path: {"before": canonical_bytes(before_set), "after": after_set_raw},
        before_history_path: {"before": before_history_raw, "after": after_history_raw},
        transition_path: {"before": None, "after": transition_raw},
    }
    return {
        "before_set": before_set, "after_set": after_set,
        "before_history": before_history, "after_history": after_history,
        "transition_receipt": transition, "journal": journal, "marker": marker,
        "before_store": before_store, "final_store": final_store, "target_images": target_images,
    }


def release_evidence_transition_semantics(
    package: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
    expected_ids: list[str], hashes: dict[str, str], security_context: dict[str, Any],
) -> bool:
    try:
        before_set, after_set = package["before_set"], package["after_set"]
        before_history, after_history = package["before_history"], package["after_history"]
        receipt, journal, marker = package["transition_receipt"], package["journal"], package["marker"]
        registered = (
            (before_set, "release-evidence-set/1.0.0", "release_evidence_set_id"),
            (after_set, "release-evidence-set/1.0.0", "release_evidence_set_id"),
            (before_history, "release-evidence-history-index/1.0.0", "index_id"),
            (after_history, "release-evidence-history-index/1.0.0", "index_id"),
            (receipt, "release-evidence-transition-receipt/1.0.0", "receipt_id"),
        )
        if not all(
            validate_registered(document, schema, registry, name, schema_sha, registry_sha)
            and document[identity] == sha256_bytes(canonical_bytes({key: value for key, value in document.items() if key != identity}))
            for document, name, identity in registered
        ) or not journal_semantics(journal, marker, schema, registry, schema_sha, registry_sha):
            return False
        if not (
            after_set["set_generation"] == before_set["set_generation"] + 1
            and after_set["previous_set_id"] == before_set["release_evidence_set_id"]
            and receipt["before_generation"] == before_set["set_generation"]
            and receipt["before_set_id"] == before_set["release_evidence_set_id"]
            and receipt["after_generation"] == after_set["set_generation"]
            and receipt["after_set_id"] == after_set["release_evidence_set_id"]
            and receipt["journal_id"] == journal["journal_id"]
            and receipt["committed_at"] == after_set["accepted_at"]
            and after_history["entries"][:-1] == before_history["entries"]
            and after_history["current_generation"] == after_set["set_generation"]
            and after_history["current_set_id"] == after_set["release_evidence_set_id"]
        ):
            return False
        current_path = registry["runtime_paths"]["release_evidence_set"]["path"]
        history_path = registry["runtime_paths"]["release_evidence_history_index"]["path"]
        archive_path = runtime_path(registry, "release_evidence_set_archive_template", release_set_id=after_set["release_evidence_set_id"])
        receipt_path = runtime_path(registry, "release_evidence_transition_receipt_template", transaction_id=receipt["transition_id"])
        expected_targets = [
            ("release-evidence", "create", archive_path, None, canonical_bytes(after_set)),
            ("release-evidence", "replace", current_path, canonical_bytes(before_set), canonical_bytes(after_set)),
            ("history-index", "replace", history_path, canonical_bytes(before_history), canonical_bytes(after_history)),
            ("receipt", "create", receipt_path, None, canonical_bytes(receipt)),
        ]
        for target, (role, operation, path, before_raw, after_raw) in zip(journal["targets"], expected_targets):
            if not (
                target["role"] == role and target["operation"] == operation and target["path"] == path
                and target["before_sha256"] == (None if before_raw is None else sha256_bytes(before_raw))
                and target["after_sha256"] == sha256_bytes(after_raw)
            ):
                return False
        load_package = {"registry_raw": canonical_bytes(registry), "release_store": package["final_store"]}
        return load_release_evidence_set(
            load_package, registry, schema, schema_sha, registry_sha, expected_ids, hashes, security_context,
        ) is not None
    except (KeyError, TypeError, ValueError):
        return False


def recover_transition_store(
    root: Path, manifest_path: str, marker_path: str, schema_path: Path, registry_path: Path,
    schema_sha: str, registry_sha: str, recovered_at: str,
) -> bool:
    try:
        schema = json.loads(schema_path.read_bytes())
        registry = json.loads(registry_path.read_bytes())
        manifest_raw = (root / manifest_path).read_bytes()
        manifest = json.loads(manifest_raw)
        if (
            canonical_bytes(manifest) != manifest_raw
            or not validate_registered(manifest, schema, registry, "transaction-journal-manifest/1.0.0", schema_sha, registry_sha)
            or manifest["manifest_id"] != sha256_bytes(canonical_bytes({key: value for key, value in manifest.items() if key != "manifest_id"}))
        ):
            return False
        marker_file = root / marker_path
        marker = None
        if marker_file.exists():
            marker_raw = marker_file.read_bytes()
            marker = json.loads(marker_raw)
            if canonical_bytes(marker) != marker_raw or not journal_semantics(manifest, marker, schema, registry, schema_sha, registry_sha):
                return False
        roll_forward = marker is not None and marker["state"] == "committed"
        target_images: dict[str, tuple[bytes | None, bytes | None]] = {}
        for target in manifest["targets"]:
            images: list[bytes | None] = []
            for side in ("before", "after"):
                locator = target[f"{side}_image"]
                expected_hash = target[f"{side}_sha256"]
                if locator is None:
                    if expected_hash is not None:
                        return False
                    images.append(None)
                    continue
                raw = (root / locator["path"]).read_bytes()
                if sha256_bytes(raw) != expected_hash or locator["sha256"] != expected_hash:
                    return False
                images.append(raw)
            target_images[target["path"]] = (images[0], images[1])
            current_path = root / target["path"]
            current_raw = current_path.read_bytes() if current_path.exists() else None
            if current_raw not in target_images[target["path"]]:
                return False
        ordered_targets = manifest["targets"] if roll_forward else list(reversed(manifest["targets"]))
        chosen_index = 1 if roll_forward else 0
        for target in ordered_targets:
            raw = target_images[target["path"]][chosen_index]
            target_path = root / target["path"]
            if raw is None:
                if target_path.exists():
                    target_path.unlink()
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(raw)
        receipt = {
            "contract": expected_contract_ref(registry, "recovery-receipt/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "journal_id": manifest["journal_id"],
            "transaction_id": manifest["transaction_id"],
            "outcome": "rolled-forward" if roll_forward else "rolled-back", "recovered_at": recovered_at,
            "target_states": ["after" if roll_forward else "before"] * len(manifest["targets"]), "error_code": None,
        }
        receipt["receipt_id"] = sha256_bytes(canonical_bytes(receipt))
        if not validate_registered(receipt, schema, registry, "recovery-receipt/1.0.0", schema_sha, registry_sha):
            return False
        receipt_path = root / manifest["recovery_receipt_path"]
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_bytes(canonical_bytes(receipt))
        return all(
            ((root / target["path"]).read_bytes() if (root / target["path"]).exists() else None)
            == target_images[target["path"]][chosen_index]
            for target in manifest["targets"]
        )
    except (KeyError, OSError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def release_evidence_recovery_semantics(
    package: dict[str, Any], crash_after: int, committed_marker: bool,
    registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
) -> bool:
    journal, marker = package["journal"], package["marker"]
    targets = journal["targets"]
    target_images = package.get("target_images")
    if not 0 <= crash_after <= len(targets) or not isinstance(target_images, dict) or set(target_images) != {row["path"] for row in targets}:
        return False
    child = (
        "import importlib.util,pathlib,sys;"
        "s=importlib.util.spec_from_file_location('transition_runner',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "ok=m.recover_transition_store(pathlib.Path(sys.argv[2]),sys.argv[3],sys.argv[4],pathlib.Path(sys.argv[5]),pathlib.Path(sys.argv[6]),sys.argv[7],sys.argv[8],sys.argv[9]);"
        "raise SystemExit(0 if ok else 1)"
    )
    with tempfile.TemporaryDirectory() as folder_name:
        root = Path(folder_name)
        schema_path = root / "control/schema.json"
        registry_path = root / "control/registry.json"
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path.write_bytes(canonical_bytes(schema))
        registry_path.write_bytes(canonical_bytes(registry))
        manifest_path = root / journal["manifest_path"]
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes(canonical_bytes(journal))
        for index, target in enumerate(targets):
            images = target_images[target["path"]]
            before_raw, after_raw = images["before"], images["after"]
            if (
                (None if before_raw is None else sha256_bytes(before_raw)) != target["before_sha256"]
                or (None if after_raw is None else sha256_bytes(after_raw)) != target["after_sha256"]
            ):
                return False
            for side, raw in (("before", before_raw), ("after", after_raw)):
                locator = target[f"{side}_image"]
                if raw is not None:
                    image_path = root / locator["path"]
                    image_path.parent.mkdir(parents=True, exist_ok=True)
                    image_path.write_bytes(raw)
            current_raw = after_raw if index < crash_after else before_raw
            current_path = root / target["path"]
            if current_raw is not None:
                current_path.parent.mkdir(parents=True, exist_ok=True)
                current_path.write_bytes(current_raw)
        if committed_marker:
            marker_path = root / journal["terminal_marker_path"]
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            marker_path.write_bytes(canonical_bytes(marker))
        arguments = [
            sys.executable, "-c", child, str(Path(__file__).resolve()), str(root), journal["manifest_path"],
            journal["terminal_marker_path"], str(schema_path), str(registry_path), schema_sha, registry_sha,
            "2026-07-24T03:30:00Z",
        ]
        first = subprocess.run(arguments, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if first.returncode != 0:
            return False
        first_receipt = (root / journal["recovery_receipt_path"]).read_bytes()
        second = subprocess.run(arguments, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if second.returncode != 0 or (root / journal["recovery_receipt_path"]).read_bytes() != first_receipt:
            return False
        recovery = json.loads(first_receipt)
        expected_side = "after" if committed_marker else "before"
        return (
            canonical_bytes(recovery) == first_receipt
            and validate_registered(recovery, schema, registry, "recovery-receipt/1.0.0", schema_sha, registry_sha)
            and recovery["outcome"] == ("rolled-forward" if committed_marker else "rolled-back")
            and recovery["target_states"] == [expected_side] * len(targets)
            and all(
                ((root / target["path"]).read_bytes() if (root / target["path"]).exists() else None)
                == target_images[target["path"]][expected_side]
                for target in targets
            )
        )


def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "skills").is_dir() and (parent / "_bmad-output").is_dir():
            return parent
    raise ValueError("repository root is not resolvable")


def writer_runtime_fixture(
    registry: dict[str, Any], capability_registry: dict[str, Any], schema_sha: str, registry_sha: str,
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    project_root = _repository_root()
    capabilities = {row["producer_id"]: row for row in capability_registry["capabilities"] if row["status"] == "active"}
    inventory: list[dict[str, Any]] = []
    store: dict[str, bytes] = {}
    for spec in registry["strict_rollout"]["writer_specs"]:
        producer_id = spec["producer_id"]
        artifacts = []
        for artifact_path in spec["artifact_paths"]:
            raw = (project_root / artifact_path).read_bytes()
            store[artifact_path] = raw
            artifacts.append({"path": artifact_path, "sha256": sha256_bytes(raw)})
        manifest = {
            "contract": expected_contract_ref(registry, "writer-build-manifest/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "producer_id": producer_id, "artifacts": artifacts,
        }
        manifest["build_id"] = sha256_bytes(canonical_bytes(manifest))
        capability = capabilities[producer_id]
        receipt = {
            "contract": expected_contract_ref(registry, "writer-fence-receipt/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "producer_id": producer_id, "writer_build_id": manifest["build_id"],
            "coordinator_id": registry["strict_rollout"]["required_fence"], "capability_id": capability["capability_id"],
            "capability_epoch": capability_registry["capability_epoch"], "lock_profile_id": registry["lock_profile"]["profile_id"],
            "fenced_at": "2026-07-24T02:59:00Z",
        }
        receipt["receipt_id"] = sha256_bytes(canonical_bytes(receipt))
        store[spec["manifest_path"]] = canonical_bytes(manifest)
        store[spec["receipt_path"]] = canonical_bytes(receipt)
        inventory.append({
            "producer_id": producer_id, "writer_build_id": manifest["build_id"],
            "fence_receipt_id": receipt["receipt_id"], "capability_id": capability["capability_id"],
        })
    return inventory, store


def writer_fence_fixture(
    registry: dict[str, Any], schema_sha: str, registry_sha: str, expected_ids: list[str], hashes: dict[str, str], activation_epoch: int = 1,
    suite: dict[str, Any] | None = None, schema: dict[str, Any] | None = None, project_root: Path | None = None,
    first_publication: bool = False,
) -> dict[str, Any]:
    memory_root = "123e4567-e89b-42d3-a456-426614174000"
    project_root_instance = "123e4567-e89b-42d3-a456-426614174001"
    capability_registry = fact_attribution_fixture(schema_sha, registry_sha, registry, "action")["capability_registry"]
    writer_inventory, writer_store = writer_runtime_fixture(registry, capability_registry, schema_sha, registry_sha)

    rows, ledger_raw, ledger_state = refresh_ledger_fixture(registry, schema_sha, registry_sha)
    action_flow = action_flow_document(rows, ledger_raw, ledger_state["ledger_revision"], registry, schema_sha, registry_sha)
    fact_state = {
        "contract": expected_contract_ref(registry, "fact-generation-state/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "fact_generation": 12, "last_transaction_id": "tx-strict-facts-1",
    }
    fact_state["state_id"] = sha256_bytes(canonical_bytes(fact_state))
    fact_command_index = {
        "contract": expected_contract_ref(registry, "fact-command-receipt-index/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "next_sequence": 1, "entries": [],
    }
    fact_command_index["index_id"] = sha256_bytes(canonical_bytes(fact_command_index))
    mutation_intent_outbox = {
        "contract": expected_contract_ref(registry, "mutation-intent-outbox/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "outbox_generation": 1, "entries": [],
    }
    mutation_intent_outbox["outbox_id"] = sha256_bytes(canonical_bytes(mutation_intent_outbox))
    intent_convergence = {
        "contract": expected_contract_ref(registry, "intent-convergence-verdict/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "outbox_id": mutation_intent_outbox["outbox_id"],
        "evaluated_through_sequence": 0, "pending_intent_ids": [], "failed_intent_ids": [],
        "waived_intent_ids": [], "status": "converged",
    }
    intent_convergence["verdict_id"] = sha256_bytes(canonical_bytes(intent_convergence))

    workstream_id = "l1-checkout"
    snapshot = action_snapshot(rows, workstream_id, ledger_state["ledger_fingerprint"], ledger_state["ledger_revision"])
    wdr_raw = apply_wdr_patch(
        fixture_wdr(workstream_id),
        {
            "set": {"refresh_actions": True},
            "evidence": [{"source_path": "meetings/m1.md", "source_fingerprint": "sha256:" + "c" * 64, "observed_at": "2026-07-24T02:00:00Z"}],
        },
        [row["rendered_summary"] for row in snapshot["actions"]],
    ).encode("utf-8")
    wdr_state = {
        "contract": expected_contract_ref(registry, "wdr-file-state/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "workstream_id": workstream_id,
        "record_path": f"workstreams/{workstream_id}/delivery-record.md", "record_fingerprint": sha256_bytes(wdr_raw),
        "wdr_revision": 5, "file_generation": 8, "lifecycle": "active",
    }
    sidecar = {
        "contract": expected_contract_ref(registry, "wdr-action-projection/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "workstream_id": workstream_id,
        "ledger_fingerprint": ledger_state["ledger_fingerprint"], "ledger_revision": ledger_state["ledger_revision"],
        "wdr_revision": wdr_state["wdr_revision"], "file_generation": wdr_state["file_generation"],
        "renderer_id": "urn:adp:wdr-action-renderer:1.0.0", "renderer_sha256": registry["protocol"]["sha256"],
        "actions": snapshot["actions"],
    }
    workstream_documents = [{"record_path": wdr_state["record_path"], "wdr_raw": wdr_raw, "state": wdr_state, "sidecar": sidecar}]
    workstreams = [{
        "workstream_id": workstream_id, "wdr_fingerprint": sha256_bytes(wdr_raw),
        "wdr_revision": wdr_state["wdr_revision"], "file_generation": wdr_state["file_generation"],
        "sidecar_fingerprint": sha256_bytes(canonical_bytes(sidecar)),
    }]

    generation_id = sha256_bytes(b"strict-generation-1")
    panel_id = sha256_bytes(b"strict-panel-1")
    pointer_rows = []
    for kind, instance_key in (
        ("state-audit", None), ("action-projection-drift-verdict", None), ("program-status", None),
        ("roadmap", None), ("flow-graph", None), ("meeting-pack", "fde-morning"),
        ("meeting-pack", "business-biweekly"), ("management-panel", None),
    ):
        template = "management_panel_template" if kind == "management-panel" else "canonical_projection_template"
        pointer_rows.append({
            "kind": kind, "instance_key": instance_key,
            "id": sha256_bytes(f"projection:{kind}:{instance_key or 'singleton'}".encode()),
            "manifest_id": sha256_bytes(f"manifest:{kind}:{instance_key or 'singleton'}".encode()),
            "canonical_path": runtime_path(registry, template, generation_id=generation_id, projection_kind=kind, instance_key=instance_key),
        })
    pointer_rows.sort(key=lambda row: (row["kind"].encode("utf-8"), (row["instance_key"] or "").encode("utf-8")))
    current_pointer = {
        "contract": expected_contract_ref(registry, "panel-current-pointer/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "generation_id": generation_id, "panel_id": panel_id, "projections": pointer_rows,
    }
    current_pointer["pointer_id"] = sha256_bytes(canonical_bytes(current_pointer))
    panel_state = {
        "contract": expected_contract_ref(registry, "panel-state/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "panel_generation": 8, "current_pointer_id": current_pointer["pointer_id"],
    }
    panel_state["state_id"] = sha256_bytes(canonical_bytes(panel_state))

    refresh_nodes = [{
        "instance_key": row["instance_key"] or "singleton", "projection_kind": row["kind"], "disposition": "produced",
        "invalidation_reasons": [],
        "output": {"kind": row["kind"], "id": row["id"], "manifest_id": row["manifest_id"], "generation_id": generation_id},
        "error_code": None,
    } for row in pointer_rows]
    refresh_receipt = {
        "contract": expected_contract_ref(registry, "refresh-run-receipt/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "refresh_id": "refresh-snapshot-fixture",
        "snapshot_id": sha256_bytes(b"snapshot:2026-07-24T03:00:00Z"), "snapshot_lock_receipt_id": sha256_bytes(b"snapshot-lock:2026-07-24T03:00:00Z"),
        "generation_id": generation_id,
        "expected_fact_generation": fact_state["fact_generation"], "expected_panel_generation": 7,
        "status": "published", "nodes": refresh_nodes, "retry_from_instance_key": None,
        "source_as_of": "2026-07-24T03:00:00Z",
    }
    refresh_receipt["receipt_id"] = sha256_bytes(canonical_bytes(refresh_receipt))

    published_targets = []
    for index, row in enumerate(pointer_rows):
        target = mutation_target("panel" if row["kind"] == "management-panel" else "projection", "create", index, row["canonical_path"])
        target["after_sha256"] = row["id"]
        target["after_image"]["sha256"] = row["id"]
        published_targets.append(target)
    pointer_target = mutation_target("pointer", "replace", len(published_targets), registry["runtime_paths"]["panel_current_pointer"]["path"])
    _set_target_after(pointer_target, current_pointer)
    panel_state_target = mutation_target("panel-state", "replace", len(published_targets) + 1, registry["runtime_paths"]["panel_state"]["path"])
    _set_target_after(panel_state_target, panel_state)
    publication_receipt = {
        "contract": expected_contract_ref(registry, "panel-publication-receipt/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "transaction_id": "tx-strict-panel-1", "journal_id": "journal-strict-panel-1",
        "command_fingerprint": sha256_bytes(canonical_bytes({
            "transaction_id": "tx-strict-panel-1", "generation_id": generation_id,
            "selection_policy_id": sha256_bytes(b"strict-selection-1"), "panel_id": panel_id,
        })),
        "generation_id": generation_id, "selection_policy_id": sha256_bytes(b"strict-selection-1"), "panel_id": panel_id,
        "lineage_index_id": sha256_bytes(canonical_bytes(published_targets)),
        "lineage_targets": copy.deepcopy(published_targets),
        "before_panel_generation": 7, "after_panel_generation": panel_state["panel_generation"],
        "before_pointer_id": sha256_bytes(b"strict-before-pointer-1"), "after_pointer_id": current_pointer["pointer_id"],
        "published_targets": published_targets, "pointer_target": pointer_target,
        "panel_state_target": panel_state_target, "status": "committed",
    }
    publication_receipt["receipt_id"] = sha256_bytes(canonical_bytes(publication_receipt))

    lineage_package = None
    if suite is not None and schema is not None and project_root is not None:
        lineage_package = strict_lineage_fixture(
            suite, registry, schema, schema_sha, registry_sha, project_root, fact_state,
            ledger_raw, ledger_state, workstream_documents, first_publication,
        )
        refresh_receipt = lineage_package["refresh_receipt"]
        publication_receipt = lineage_package["publication_graph"]["receipt"]
        current_pointer = lineage_package["publication_graph"]["pointer"]
        panel_state = lineage_package["publication_graph"]["state"]
        generation_id = lineage_package["generation"]["generation_id"]
        panel_id = lineage_package["panel"]["panel_id"]

    root_registry = {
        "contract": expected_contract_ref(registry, "root-registry-state/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "roots": [
            {"role": "memory", "root_instance_id": memory_root, "canonical_path_hash": sha256_bytes(b"/canonical/memory")},
            {"role": "project", "root_instance_id": project_root_instance, "canonical_path_hash": sha256_bytes(b"/canonical/project")},
        ],
        "created_at": "2026-07-24T01:00:00Z",
    }
    root_registry["registry_state_id"] = sha256_bytes(canonical_bytes(root_registry))
    receipts, evidence_blobs = implementation_conformance_receipts(expected_ids, hashes, registry)
    release_evidence_set, release_store = release_evidence_set_fixture(
        receipts, evidence_blobs, registry, schema_sha, registry_sha,
    )
    release_history = json.loads(release_store[registry["runtime_paths"]["release_evidence_history_index"]["path"]])
    activation_state = {
        "contract": expected_contract_ref(registry, "strict-activation-state/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "activation_epoch": activation_epoch, "mode": "strict",
        "attestation_id": "sha256:" + "0" * 64, "changed_at": "2026-07-24T03:00:03Z",
    }
    activation_state_binding_id = sha256_bytes(canonical_bytes({
        key: value for key, value in activation_state.items() if key not in {"attestation_id", "state_id"}
    }))
    attestation = {
        "contract": expected_contract_ref(registry, "writer-fence-migration-attestation/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "attestation_id": "sha256:" + "0" * 64, "attested_at": "2026-07-24T03:00:02Z",
        "binding_scope": "immutable-writer-fence",
        "registry_sha256": registry_sha, "protocol_sha256": hashes["protocol"],
        "release_evidence_set_id": release_evidence_set["release_evidence_set_id"],
        "release_evidence_history_index_id": release_history["index_id"],
        "activation_state_binding_id": activation_state_binding_id,
        "memory_root_instance_id": memory_root, "root_registry_state_id": root_registry["registry_state_id"],
        "capability_registry_id": capability_registry["capability_registry_id"], "capability_epoch": capability_registry["capability_epoch"],
        "activation_epoch": activation_epoch, "fact_generation": fact_state["fact_generation"],
        "writer_inventory": copy.deepcopy(writer_inventory),
        "ledger": {
            "ledger_fingerprint": ledger_state["ledger_fingerprint"], "ledger_revision": ledger_state["ledger_revision"],
            "ledger_state_id": ledger_state["state_id"], "action_flow_fingerprint": sha256_bytes(canonical_bytes(action_flow)),
        },
        "workstreams": workstreams, "full_refresh_receipt_id": refresh_receipt["receipt_id"],
        "published_generation_id": generation_id, "panel_publication_receipt_id": publication_receipt["receipt_id"],
        "current_pointer_id": current_pointer["pointer_id"],
        "lineage_index_id": lineage_package["lineage_index"]["index_id"] if lineage_package is not None else sha256_bytes(b"design-lineage-index"),
        "lineage_index_path": lineage_package["lineage_index_path"] if lineage_package is not None else runtime_path(registry, "generation_lineage_index_template", generation_id=generation_id),
    }
    attestation["attestation_id"] = sha256_bytes(canonical_bytes({key: value for key, value in attestation.items() if key != "attestation_id"}))
    activation_state["attestation_id"] = attestation["attestation_id"]
    activation_state["state_id"] = sha256_bytes(canonical_bytes(activation_state))
    return {
        "surface": "publish", "attestation_path": registry["runtime_paths"]["writer_fence_attestation"]["path"],
        "capability_lifecycle_operation": None,
        "registry_raw": canonical_bytes(registry), "release_evidence_set": release_evidence_set,
        "release_store": release_store, "release_receipts": receipts, "evidence_blobs": evidence_blobs,
        "refresh_completed_at": "2026-07-24T03:00:00Z", "publication_completed_at": "2026-07-24T03:00:01Z",
        "paths": {
            "root_registry": registry["runtime_paths"]["root_registry_state"]["path"],
            "capability_registry": registry["runtime_paths"]["writer_capability_registry"]["path"],
            "fact_state": registry["runtime_paths"]["fact_generation"]["path"],
            "ledger": registry["runtime_paths"]["action_ledger"]["path"],
            "ledger_state": registry["runtime_paths"]["action_ledger_state"]["path"],
            "action_flow": registry["runtime_paths"]["action_flow_index"]["path"],
            "release_evidence_set": registry["runtime_paths"]["release_evidence_set"]["path"],
            "release_evidence_history_index": registry["runtime_paths"]["release_evidence_history_index"]["path"],
            "pointer": registry["runtime_paths"]["panel_current_pointer"]["path"],
            "panel_state": registry["runtime_paths"]["panel_state"]["path"],
            "activation_state": registry["runtime_paths"]["strict_activation_state"]["path"],
        },
        "writer_store": writer_store,
        "lineage_store": lineage_package["lineage_store"] if lineage_package is not None else {},
        "live_leaf_store": lineage_package["leaf_store"] if lineage_package is not None else {},
        "documents": {
            "root_registry": root_registry, "capability_registry": capability_registry, "fact_state": fact_state,
            "fact_command_index": fact_command_index, "mutation_intent_outbox": mutation_intent_outbox,
            "intent_convergence": intent_convergence,
            "ledger_raw": ledger_raw, "ledger_state": ledger_state, "action_flow": action_flow,
            "workstreams": workstream_documents, "refresh_receipt": refresh_receipt,
            "publication_receipt": publication_receipt, "current_pointer": current_pointer,
            "panel_state": panel_state, "activation_state": activation_state,
            "release_evidence_set": release_evidence_set,
            "release_evidence_history_index": release_history,
        },
        "attestation": attestation,
    }


def rebind_writer_fence_attestation(package: dict[str, Any]) -> None:
    attestation = package["attestation"]
    attestation["attestation_id"] = sha256_bytes(canonical_bytes({key: value for key, value in attestation.items() if key != "attestation_id"}))


def strict_writer_inventory_semantics(
    package: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
) -> bool:
    try:
        capabilities = package["documents"]["capability_registry"]
        attestation = package["attestation"]
        active = {row["producer_id"]: row for row in capabilities["capabilities"] if row["status"] == "active"}
        specs = registry["strict_rollout"]["writer_specs"]
        required = registry["strict_rollout"]["authoritative_writers"]
        if (
            [row["producer_id"] for row in specs] != required
            or set(active) != set(required)
            or len(active) != len(capabilities["capabilities"])
        ):
            return False
        store = package["writer_store"]
        expected_paths = {path for spec in specs for path in (*spec["artifact_paths"], spec["manifest_path"], spec["receipt_path"])}
        if set(store) != expected_paths or any(not isinstance(raw, bytes) for raw in store.values()):
            return False
        derived = []
        for spec in specs:
            capability = active[spec["producer_id"]]
            if (
                capability_record_digest(capability) != capability["capability_id"]
                or capability["authorization_record_digest"] != capability["capability_id"]
                or any(capability[name] != spec[name] for name in ("allowed_operations", "allowed_fields", "allowed_sections"))
            ):
                return False
            manifest_raw = store[spec["manifest_path"]]
            receipt_raw = store[spec["receipt_path"]]
            manifest = json.loads(manifest_raw)
            receipt = json.loads(receipt_raw)
            expected_artifacts = [{"path": path, "sha256": sha256_bytes(store[path])} for path in spec["artifact_paths"]]
            if not (
                canonical_bytes(manifest) == manifest_raw
                and canonical_bytes(receipt) == receipt_raw
                and validate_registered(manifest, schema, registry, "writer-build-manifest/1.0.0", schema_sha, registry_sha)
                and validate_registered(receipt, schema, registry, "writer-fence-receipt/1.0.0", schema_sha, registry_sha)
                and manifest["producer_id"] == spec["producer_id"]
                and manifest["artifacts"] == expected_artifacts
                and manifest["build_id"] == sha256_bytes(canonical_bytes({key: value for key, value in manifest.items() if key != "build_id"}))
                and receipt["receipt_id"] == sha256_bytes(canonical_bytes({key: value for key, value in receipt.items() if key != "receipt_id"}))
                and receipt["producer_id"] == spec["producer_id"]
                and receipt["writer_build_id"] == manifest["build_id"]
                and receipt["coordinator_id"] == registry["strict_rollout"]["required_fence"]
                and receipt["capability_id"] == capability["capability_id"]
                and receipt["capability_epoch"] == capabilities["capability_epoch"]
                and receipt["lock_profile_id"] == registry["lock_profile"]["profile_id"]
            ):
                return False
            derived.append({
                "producer_id": spec["producer_id"], "writer_build_id": manifest["build_id"],
                "fence_receipt_id": receipt["receipt_id"], "capability_id": capability["capability_id"],
            })
        return attestation["writer_inventory"] == derived
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def strict_activation_control_semantics(
    package: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
    expected_ids: list[str], hashes: dict[str, str], security_context: dict[str, Any],
) -> bool:
    try:
        if (
            registry["strict_rollout"].get("capability_lifecycle_rule")
            != "strict-mode-prohibits-runtime-create-rotate-revoke;rollback-to-legacy-increment-activation-epoch-reviewed-reprovision-and-full-refresh-required"
            or registry["strict_rollout"].get("capability_lifecycle_error") != "CAPABILITY_LIFECYCLE_REQUIRES_ROLLBACK"
            or registry["strict_rollout"].get("activation_algorithm")
            != "release-gate-passed-and-content-addressed-current-root-release-authority-capability-epoch-writer-build-fence-activation-epoch-exact-match-with-mutable-facts-and-publication-state-live-receipt-cas-validated"
            or package.get("capability_lifecycle_operation") is not None
        ):
            return False
        if registry["conformance_suite"]["implementation_conformance_status"] != "passed":
            return False
        if package["attestation_path"] != registry["runtime_paths"]["writer_fence_attestation"]["path"]:
            return False
        attestation = package["attestation"]
        activation = package["documents"]["activation_state"]
        capabilities = package["documents"]["capability_registry"]
        loaded_release = load_release_evidence_set(
            package, registry, schema, schema_sha, registry_sha, expected_ids, hashes, security_context,
        )
        if loaded_release is None:
            return False
        release_set, _, _ = loaded_release
        release_history = package["documents"]["release_evidence_history_index"]
        if not (
            package["documents"]["release_evidence_set"] == release_set
            and validate_registered(release_history, schema, registry, "release-evidence-history-index/1.0.0", schema_sha, registry_sha)
            and release_history["index_id"] == sha256_bytes(canonical_bytes({key: value for key, value in release_history.items() if key != "index_id"}))
            and validate_registered(attestation, schema, registry, "writer-fence-migration-attestation/1.0.0", schema_sha, registry_sha)
            and validate_registered(activation, schema, registry, "strict-activation-state/1.0.0", schema_sha, registry_sha)
            and validate_registered(capabilities, schema, registry, "writer-capability-registry/1.0.0", schema_sha, registry_sha)
        ):
            return False
        activation_binding_id = sha256_bytes(canonical_bytes({
            key: value for key, value in activation.items() if key not in {"attestation_id", "state_id"}
        }))
        return (
            attestation["attestation_id"] == sha256_bytes(canonical_bytes({key: value for key, value in attestation.items() if key != "attestation_id"}))
            and activation["state_id"] == sha256_bytes(canonical_bytes({key: value for key, value in activation.items() if key != "state_id"}))
            and capabilities["capability_registry_id"] == sha256_bytes(canonical_bytes({key: value for key, value in capabilities.items() if key != "capability_registry_id"}))
            and attestation["registry_sha256"] == registry_sha
            and attestation["protocol_sha256"] == hashes["protocol"]
            and attestation["release_evidence_set_id"] == release_set["release_evidence_set_id"]
            and attestation["release_evidence_history_index_id"] == release_history["index_id"]
            and attestation["activation_state_binding_id"] == activation_binding_id
            and activation["mode"] == "strict"
            and activation["attestation_id"] == attestation["attestation_id"]
            and activation["activation_epoch"] == attestation["activation_epoch"]
            and capabilities["capability_registry_id"] == attestation["capability_registry_id"]
            and capabilities["capability_epoch"] == attestation["capability_epoch"]
            and strict_writer_inventory_semantics(package, registry, schema, schema_sha, registry_sha)
        )
    except (KeyError, TypeError, ValueError):
        return False


def strict_writer_fence_activation_semantics(
    package: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
    expected_ids: list[str], hashes: dict[str, str], security_context: dict[str, Any],
) -> bool:
    try:
        attestation, documents, paths = package["attestation"], package["documents"], package["paths"]
        if package["surface"] not in {"open", "inspect", "publish"}:
            return False
        if not strict_activation_control_semantics(package, registry, schema, schema_sha, registry_sha, expected_ids, hashes, security_context):
            return False
        loaded_release = load_release_evidence_set(
            package, registry, schema, schema_sha, registry_sha, expected_ids, hashes, security_context,
        )
        if loaded_release is None:
            return False
        release_set, _, _ = loaded_release
        if not validate_registered(attestation, schema, registry, "writer-fence-migration-attestation/1.0.0", schema_sha, registry_sha):
            return False
        if attestation["attestation_id"] != sha256_bytes(canonical_bytes({key: value for key, value in attestation.items() if key != "attestation_id"})):
            return False
        release_history = documents["release_evidence_history_index"]
        if attestation["registry_sha256"] != registry_sha or attestation["protocol_sha256"] != hashes["protocol"] or attestation["release_evidence_set_id"] != release_set["release_evidence_set_id"] or attestation["release_evidence_history_index_id"] != release_history["index_id"]:
            return False
        expected_paths = {
            "root_registry": registry["runtime_paths"]["root_registry_state"]["path"],
            "capability_registry": registry["runtime_paths"]["writer_capability_registry"]["path"],
            "fact_state": registry["runtime_paths"]["fact_generation"]["path"],
            "ledger": registry["runtime_paths"]["action_ledger"]["path"],
            "ledger_state": registry["runtime_paths"]["action_ledger_state"]["path"],
            "action_flow": registry["runtime_paths"]["action_flow_index"]["path"],
            "release_evidence_set": registry["runtime_paths"]["release_evidence_set"]["path"],
            "release_evidence_history_index": registry["runtime_paths"]["release_evidence_history_index"]["path"],
            "pointer": registry["runtime_paths"]["panel_current_pointer"]["path"],
            "panel_state": registry["runtime_paths"]["panel_state"]["path"],
            "activation_state": registry["runtime_paths"]["strict_activation_state"]["path"],
        }
        if paths != expected_paths:
            return False

        root_registry = documents["root_registry"]
        capability_registry = documents["capability_registry"]
        fact_state = documents["fact_state"]
        ledger_raw = documents["ledger_raw"]
        ledger_state = documents["ledger_state"]
        action_flow = documents["action_flow"]
        refresh_receipt = documents["refresh_receipt"]
        publication_receipt = documents["publication_receipt"]
        current_pointer = documents["current_pointer"]
        panel_state = documents["panel_state"]
        activation_state = documents["activation_state"]
        release_evidence_set = documents["release_evidence_set"]
        release_evidence_history_index = documents["release_evidence_history_index"]
        registered = (
            (root_registry, "root-registry-state/1.0.0", "registry_state_id"),
            (capability_registry, "writer-capability-registry/1.0.0", "capability_registry_id"),
            (fact_state, "fact-generation-state/1.0.0", "state_id"),
            (ledger_state, "action-ledger-state/1.0.0", "state_id"),
            (refresh_receipt, "refresh-run-receipt/1.0.0", "receipt_id"),
            (publication_receipt, "panel-publication-receipt/1.0.0", "receipt_id"),
            (current_pointer, "panel-current-pointer/1.0.0", "pointer_id"),
            (panel_state, "panel-state/1.0.0", "state_id"),
            (activation_state, "strict-activation-state/1.0.0", "state_id"),
            (release_evidence_set, "release-evidence-set/1.0.0", "release_evidence_set_id"),
            (release_evidence_history_index, "release-evidence-history-index/1.0.0", "index_id"),
        )
        for document, contract_name, identity_field in registered:
            if not validate_registered(document, schema, registry, contract_name, schema_sha, registry_sha):
                return False
            if document[identity_field] != sha256_bytes(canonical_bytes({key: value for key, value in document.items() if key != identity_field})):
                return False
        if not validate(action_flow, schema, "actionFlowIndexV1"):
            return False
        root_rows = {row["role"]: row for row in root_registry["roots"]}
        if set(root_rows) != {"memory", "project"} or root_rows["memory"]["root_instance_id"] != attestation["memory_root_instance_id"] or root_registry["registry_state_id"] != attestation["root_registry_state_id"]:
            return False

        registry_body = {key: value for key, value in capability_registry.items() if key != "capability_registry_id"}
        if capability_registry["capability_registry_id"] != sha256_bytes(canonical_bytes(registry_body)):
            return False
        active_capabilities = {row["producer_id"]: row for row in capability_registry["capabilities"] if row["status"] == "active"}
        writer_specs = registry["strict_rollout"]["writer_specs"]
        if (
            len(active_capabilities) != 9 or len(capability_registry["capabilities"]) != 9
            or [row["producer_id"] for row in writer_specs] != registry["strict_rollout"]["authoritative_writers"]
            or set(active_capabilities) != set(registry["strict_rollout"]["authoritative_writers"])
            or any(capability_record_digest(row) != row["capability_id"] or row["authorization_record_digest"] != row["capability_id"] for row in active_capabilities.values())
        ):
            return False
        store = package["writer_store"]
        expected_store_paths = {
            path for spec in writer_specs for path in (spec["artifact_paths"] + [spec["manifest_path"], spec["receipt_path"]])
        }
        if set(store) != expected_store_paths or any(not isinstance(raw, bytes) for raw in store.values()):
            return False
        derived_writers = []
        for spec in writer_specs:
            producer_id = spec["producer_id"]
            capability = active_capabilities[producer_id]
            if any(capability[name] != spec[name] for name in ("allowed_operations", "allowed_fields", "allowed_sections")):
                return False
            manifest_raw = store[spec["manifest_path"]]
            receipt_raw = store[spec["receipt_path"]]
            manifest = json.loads(manifest_raw)
            receipt = json.loads(receipt_raw)
            expected_artifacts = [{"path": path, "sha256": sha256_bytes(store[path])} for path in spec["artifact_paths"]]
            if not (
                canonical_bytes(manifest) == manifest_raw and canonical_bytes(receipt) == receipt_raw
                and validate_registered(manifest, schema, registry, "writer-build-manifest/1.0.0", schema_sha, registry_sha)
                and validate_registered(receipt, schema, registry, "writer-fence-receipt/1.0.0", schema_sha, registry_sha)
                and manifest["producer_id"] == producer_id and manifest["artifacts"] == expected_artifacts
                and manifest["build_id"] == sha256_bytes(canonical_bytes({key: value for key, value in manifest.items() if key != "build_id"}))
                and receipt["receipt_id"] == sha256_bytes(canonical_bytes({key: value for key, value in receipt.items() if key != "receipt_id"}))
                and receipt["producer_id"] == producer_id and receipt["writer_build_id"] == manifest["build_id"]
                and receipt["coordinator_id"] == registry["strict_rollout"]["required_fence"]
                and receipt["capability_id"] == capability["capability_id"]
                and receipt["capability_epoch"] == capability_registry["capability_epoch"]
                and receipt["lock_profile_id"] == registry["lock_profile"]["profile_id"]
            ):
                return False
            derived_writers.append({
                "producer_id": producer_id, "writer_build_id": manifest["build_id"],
                "fence_receipt_id": receipt["receipt_id"], "capability_id": capability["capability_id"],
            })
        writers = attestation["writer_inventory"]
        if writers != derived_writers:
            return False

        if not isinstance(ledger_raw, bytes):
            return False
        rows = parse_action_ledger(ledger_raw)
        expected_ledger_state = action_ledger_state_document(
            rows, ledger_raw, ledger_state["ledger_revision"], ledger_state["applied_commands"], registry, schema_sha, registry_sha,
        )
        expected_flow = action_flow_document(rows, ledger_raw, ledger_state["ledger_revision"], registry, schema_sha, registry_sha)
        if ledger_state != expected_ledger_state or action_flow != expected_flow:
            return False

        actual_workstreams = []
        workstream_docs = documents["workstreams"]
        for item in workstream_docs:
            raw, state, sidecar = item["wdr_raw"], item["state"], item["sidecar"]
            workstream_id = state["workstream_id"]
            if not isinstance(raw, bytes) or item["record_path"] != f"workstreams/{workstream_id}/delivery-record.md":
                return False
            if not validate_registered(state, schema, registry, "wdr-file-state/1.0.0", schema_sha, registry_sha) or not validate_registered(sidecar, schema, registry, "wdr-action-projection/1.0.0", schema_sha, registry_sha):
                return False
            if not complete_wdr_valid(raw.decode("utf-8"), workstream_id) or state != {
                "contract": expected_contract_ref(registry, "wdr-file-state/1.0.0", schema_sha, registry_sha),
                "schema_version": "1.0.0", "workstream_id": workstream_id, "record_path": item["record_path"],
                "record_fingerprint": sha256_bytes(raw), "wdr_revision": state["wdr_revision"],
                "file_generation": state["file_generation"], "lifecycle": "active",
            }:
                return False
            snapshot = action_snapshot(rows, workstream_id, ledger_state["ledger_fingerprint"], ledger_state["ledger_revision"])
            expected_sidecar = {
                "contract": expected_contract_ref(registry, "wdr-action-projection/1.0.0", schema_sha, registry_sha),
                "schema_version": "1.0.0", "workstream_id": workstream_id,
                "ledger_fingerprint": ledger_state["ledger_fingerprint"], "ledger_revision": ledger_state["ledger_revision"],
                "wdr_revision": state["wdr_revision"], "file_generation": state["file_generation"],
                "renderer_id": "urn:adp:wdr-action-renderer:1.0.0", "renderer_sha256": registry["protocol"]["sha256"],
                "actions": snapshot["actions"],
            }
            _, managed = partition_next_actions(wdr_current_signature(raw.decode("utf-8"), workstream_id)["next_actions"])
            if sidecar != expected_sidecar or managed != [row["rendered_summary"] for row in sidecar["actions"]]:
                return False
            actual_workstreams.append({
                "workstream_id": workstream_id, "wdr_fingerprint": sha256_bytes(raw),
                "wdr_revision": state["wdr_revision"], "file_generation": state["file_generation"],
                "sidecar_fingerprint": sha256_bytes(canonical_bytes(sidecar)),
            })
        actual_workstreams.sort(key=lambda row: row["workstream_id"].encode("utf-8"))
        workstream_ids = [row["workstream_id"] for row in actual_workstreams]
        if not workstream_ids or workstream_ids != sorted(set(workstream_ids), key=lambda value: value.encode("utf-8")):
            return False

        lineage = load_strict_lineage(
            package, registry, schema, schema_sha, registry_sha,
            verify_live_leaves=package["surface"] != "inspect",
        )
        if lineage is None or lineage["generation"]["fact_generation"] != fact_state["fact_generation"]:
            return False
        refresh_receipt = lineage["refresh_receipt"]
        publication_receipt = lineage["graph"]["receipt"]
        current_pointer = lineage["graph"]["pointer"]
        panel_state = lineage["graph"]["state"]

        pointer_body = {key: value for key, value in current_pointer.items() if key != "pointer_id"}
        if current_pointer["pointer_id"] != sha256_bytes(canonical_bytes(pointer_body)):
            return False
        expected_pointer_paths = []
        for row in current_pointer["projections"]:
            template = "management_panel_template" if row["kind"] == "management-panel" else "canonical_projection_template"
            expected_path = runtime_path(registry, template, generation_id=current_pointer["generation_id"], projection_kind=row["kind"], instance_key=row["instance_key"])
            if row["canonical_path"] != expected_path:
                return False
            expected_pointer_paths.append(("panel" if row["kind"] == "management-panel" else "projection", expected_path))
        node_rows = sorted(
            (row["projection_kind"], row["instance_key"], row["output"]["id"], row["output"]["manifest_id"], row["output"]["generation_id"])
            for row in refresh_receipt["nodes"] if row["disposition"] in {"produced", "reused"} and row["output"] is not None
        )
        pointer_node_rows = sorted((row["kind"], row["instance_key"] or "singleton", row["id"], row["manifest_id"], current_pointer["generation_id"]) for row in current_pointer["projections"])
        if node_rows != pointer_node_rows or refresh_receipt["status"] != "published" or refresh_receipt["retry_from_instance_key"] is not None:
            return False
        published_paths = [(row["role"], row["path"]) for row in publication_receipt["published_targets"]]
        if published_paths != expected_pointer_paths:
            return False
        if not (
            publication_receipt["status"] == "committed"
            and publication_receipt["generation_id"] == current_pointer["generation_id"]
            and publication_receipt["panel_id"] == current_pointer["panel_id"]
            and publication_receipt["after_pointer_id"] == current_pointer["pointer_id"]
            and publication_receipt["after_panel_generation"] == panel_state["panel_generation"]
            and panel_state["current_pointer_id"] == current_pointer["pointer_id"]
            and publication_receipt["pointer_target"]["path"] == paths["pointer"]
            and publication_receipt["pointer_target"]["after_sha256"] == sha256_bytes(canonical_bytes(current_pointer))
            and publication_receipt["panel_state_target"]["path"] == paths["panel_state"]
            and publication_receipt["panel_state_target"]["after_sha256"] == sha256_bytes(canonical_bytes(panel_state))
            and refresh_receipt["generation_id"] == current_pointer["generation_id"]
            and refresh_receipt["expected_fact_generation"] == fact_state["fact_generation"]
            and refresh_receipt["expected_panel_generation"] == publication_receipt["before_panel_generation"]
        ):
            return False
        if not (
            activation_state["mode"] == "strict" and activation_state["attestation_id"] == attestation["attestation_id"]
            and activation_state["activation_epoch"] == attestation["activation_epoch"]
            and attestation["activation_state_binding_id"] == sha256_bytes(canonical_bytes({
                key: value for key, value in activation_state.items() if key not in {"attestation_id", "state_id"}
            }))
        ):
            return False
        activation_summary = {
            "memory_root_instance_id": root_rows["memory"]["root_instance_id"],
            "root_registry_state_id": root_registry["registry_state_id"],
            "capability_registry_id": capability_registry["capability_registry_id"],
            "capability_epoch": capability_registry["capability_epoch"], "activation_epoch": activation_state["activation_epoch"],
            "writer_inventory": derived_writers,
        }
        if attestation["binding_scope"] != "immutable-writer-fence" or {
            key: attestation[key] for key in activation_summary
        } != activation_summary:
            return False
        attested_at = _utc_instant(attestation["attested_at"])
        if attested_at < _utc_instant(release_set["accepted_at"]):
            return False
        return True
    except (KeyError, TypeError, ValueError, UnicodeDecodeError):
        return False


def activation_transition_fixture(
    writer_package: dict[str, Any], registry: dict[str, Any], schema_sha: str, registry_sha: str,
) -> dict[str, Any]:
    roots = copy.deepcopy(writer_package["documents"]["root_registry"])
    old_capability = copy.deepcopy(writer_package["documents"]["capability_registry"])
    new_capability = copy.deepcopy(old_capability)
    new_capability["capability_epoch"] += 1
    new_capability["capability_registry_id"] = sha256_bytes(canonical_bytes({key: value for key, value in new_capability.items() if key != "capability_registry_id"}))
    old_activation = copy.deepcopy(writer_package["documents"]["activation_state"])
    old_attestation = copy.deepcopy(writer_package["attestation"])
    legacy_activation = {
        "contract": copy.deepcopy(old_activation["contract"]), "schema_version": "1.0.0",
        "activation_epoch": old_activation["activation_epoch"] + 1, "mode": "legacy",
        "attestation_id": None, "changed_at": "2026-07-24T03:06:00Z",
    }
    legacy_activation["state_id"] = sha256_bytes(canonical_bytes(legacy_activation))
    final_activation = {
        "contract": copy.deepcopy(old_activation["contract"]), "schema_version": "1.0.0",
        "activation_epoch": legacy_activation["activation_epoch"], "mode": "strict",
        "attestation_id": "sha256:" + "0" * 64, "changed_at": "2026-07-24T03:20:00Z",
    }
    refresh_receipt = copy.deepcopy(writer_package["documents"]["refresh_receipt"])
    new_attestation = copy.deepcopy(old_attestation)
    new_attestation.update({
        "activation_epoch": legacy_activation["activation_epoch"],
        "capability_epoch": new_capability["capability_epoch"],
        "capability_registry_id": new_capability["capability_registry_id"],
        "full_refresh_receipt_id": refresh_receipt["receipt_id"], "attested_at": "2026-07-24T03:19:00Z",
        "activation_state_binding_id": sha256_bytes(canonical_bytes({
            key: value for key, value in final_activation.items() if key not in {"attestation_id", "state_id"}
        })),
    })
    new_attestation["attestation_id"] = sha256_bytes(canonical_bytes({key: value for key, value in new_attestation.items() if key != "attestation_id"}))
    final_activation["attestation_id"] = new_attestation["attestation_id"]
    final_activation["state_id"] = sha256_bytes(canonical_bytes(final_activation))
    states = {
        "rollback": (old_activation, legacy_activation, old_capability, old_capability, old_attestation),
        "reprovision": (legacy_activation, legacy_activation, old_capability, new_capability, None),
        "record-refresh": (legacy_activation, legacy_activation, new_capability, new_capability, None),
        "attest": (legacy_activation, legacy_activation, new_capability, new_capability, None),
        "enable": (legacy_activation, final_activation, new_capability, new_capability, None),
    }
    lifecycle_id = sha256_bytes(canonical_bytes({
        "initial_activation_state_id": old_activation["state_id"],
        "target_activation_epoch": legacy_activation["activation_epoch"],
        "operations": ["rollback", "reprovision", "record-refresh", "attest", "enable"],
    }))
    lifecycle_index = {
        "contract": expected_contract_ref(registry, "activation-lifecycle-index/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "lifecycle_id": lifecycle_id,
        "activation_epoch": legacy_activation["activation_epoch"], "entries": [], "terminal_status": "in-progress",
    }
    steps = []
    previous_receipt: dict[str, Any] | None = None
    for sequence, operation in enumerate(("rollback", "reprovision", "record-refresh", "attest", "enable"), start=1):
        before_activation, after_activation, before_capability, after_capability, applicable_attestation = states[operation]
        authority = runtime_authority_from_documents(
            registry, schema_sha, registry_sha, registry["strict_rollout"]["activation_administrator_producer_id"], before_capability, roots,
            before_activation, applicable_attestation,
        )
        context = authority[-1]
        transition_id = f"activation-{sequence}-{operation}"
        journal_id = f"journal-{transition_id}"
        command = {
            "contract": expected_contract_ref(registry, "activation-transition-command/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "lifecycle_id": lifecycle_id, "step_ordinal": sequence,
            "predecessor_receipt_id": None if previous_receipt is None else previous_receipt["receipt_id"],
            "transition_id": transition_id, "operation": operation,
            "authority_context_id": context["context_id"], "fact_lock_profile_id": registry["lock_profile"]["profile_id"],
            "expected_activation_epoch": before_activation["activation_epoch"],
            "expected_capability_epoch": before_capability["capability_epoch"],
            "expected_activation_state_id": before_activation["state_id"],
            "expected_capability_registry_id": before_capability["capability_registry_id"],
            "expected_attestation_id": old_attestation["attestation_id"] if operation == "rollback" else (new_attestation["attestation_id"] if operation == "enable" else None),
            "expected_attestation_sha256": (
                sha256_bytes(canonical_bytes(old_attestation)) if operation in {"rollback", "attest"}
                else (sha256_bytes(canonical_bytes(new_attestation)) if operation == "enable" else None)
            ),
            "approved_by": ["operator-a", "operator-b"], "requested_at": f"2026-07-24T03:{5 + sequence:02d}:00Z",
        }
        full_refresh_id = refresh_receipt["receipt_id"] if operation in {"record-refresh", "attest", "enable"} else None
        attestation_id = new_attestation["attestation_id"] if operation in {"attest", "enable"} else None
        receipt = {
            "contract": expected_contract_ref(registry, "activation-transition-receipt/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "lifecycle_id": lifecycle_id, "step_ordinal": sequence,
            "predecessor_receipt_id": command["predecessor_receipt_id"], "transition_id": transition_id, "operation": operation,
            "before_activation_epoch": before_activation["activation_epoch"], "after_activation_epoch": after_activation["activation_epoch"],
            "before_capability_epoch": before_capability["capability_epoch"], "after_capability_epoch": after_capability["capability_epoch"],
            "before_activation_state_id": before_activation["state_id"], "after_activation_state_id": after_activation["state_id"],
            "before_capability_registry_id": before_capability["capability_registry_id"],
            "after_capability_registry_id": after_capability["capability_registry_id"],
            "before_attestation_id": old_attestation["attestation_id"] if operation == "rollback" else (new_attestation["attestation_id"] if operation == "enable" else None),
            "after_attestation_id": new_attestation["attestation_id"] if operation in {"attest", "enable"} else None,
            "full_refresh_receipt_id": full_refresh_id, "attestation_id": attestation_id,
            "journal_id": journal_id, "status": "committed", "completed_at": f"2026-07-24T03:{6 + sequence:02d}:00Z",
        }
        receipt["receipt_id"] = sha256_bytes(canonical_bytes(receipt))
        before_lifecycle_index = None if sequence == 1 else copy.deepcopy(lifecycle_index)
        lifecycle_index["entries"].append({
            "step_ordinal": sequence, "transition_id": transition_id, "operation": operation,
            "predecessor_receipt_id": receipt["predecessor_receipt_id"], "receipt_id": receipt["receipt_id"],
            "receipt_path": runtime_path(registry, "activation_transition_receipt_template", transaction_id=transition_id),
            "receipt_sha256": sha256_bytes(canonical_bytes(receipt)),
        })
        if operation == "enable":
            lifecycle_index["terminal_status"] = "enabled"
        lifecycle_index["index_id"] = sha256_bytes(canonical_bytes({key: value for key, value in lifecycle_index.items() if key != "index_id"}))
        if operation in {"rollback", "enable"}:
            role, path, target_before, target_after, target_operation = (
                "activation-state", registry["runtime_paths"]["strict_activation_state"]["path"],
                canonical_bytes(before_activation), canonical_bytes(after_activation), "replace",
            )
        elif operation == "reprovision":
            role, path, target_before, target_after, target_operation = (
                "capability-registry", registry["runtime_paths"]["writer_capability_registry"]["path"],
                canonical_bytes(before_capability), canonical_bytes(after_capability), "replace",
            )
        elif operation == "record-refresh":
            role, path, target_before, target_after, target_operation = (
                "transition-state", runtime_path(registry, "activation_transition_state_template", transaction_id=transition_id),
                None, canonical_bytes(refresh_receipt), "create",
            )
        else:
            role, path, target_before, target_after, target_operation = (
                "attestation", registry["runtime_paths"]["writer_fence_attestation"]["path"],
                canonical_bytes(old_attestation), canonical_bytes(new_attestation), "replace",
            )
        receipt_path = runtime_path(registry, "activation_transition_receipt_template", transaction_id=transition_id)
        lifecycle_path = runtime_path(registry, "activation_lifecycle_index_template", lifecycle_id=lifecycle_id)
        journal, marker = transition_journal_fixture(
            "activation", transition_id, journal_id,
            [
                {"role": role, "operation": target_operation, "path": path, "before_raw": target_before, "after_raw": target_after},
                {"role": "activation-lifecycle-index", "operation": "create" if sequence == 1 else "replace", "path": lifecycle_path, "before_raw": None if before_lifecycle_index is None else canonical_bytes(before_lifecycle_index), "after_raw": canonical_bytes(lifecycle_index)},
            ],
            receipt_path, canonical_bytes(receipt), registry, schema_sha, registry_sha,
        )
        target_images = {
            path: {"before": target_before, "after": target_after},
            lifecycle_path: {"before": None if before_lifecycle_index is None else canonical_bytes(before_lifecycle_index), "after": canonical_bytes(lifecycle_index)},
            receipt_path: {"before": None, "after": canonical_bytes(receipt)},
        }
        steps.append({
            "command": command, "receipt": receipt, "journal": journal, "marker": marker, "authority": authority,
            "before_activation": before_activation, "after_activation": after_activation,
            "before_capability": before_capability, "after_capability": after_capability,
            "refresh_receipt": refresh_receipt if operation in {"record-refresh", "attest", "enable"} else None,
            "attestation": new_attestation if operation in {"attest", "enable"} else None,
            "attestation_preimage": old_attestation if operation == "attest" else None,
            "before_lifecycle_index": before_lifecycle_index, "after_lifecycle_index": copy.deepcopy(lifecycle_index),
            "target_images": target_images,
        })
        previous_receipt = receipt
    return {"roots": roots, "steps": steps, "lifecycle_index": lifecycle_index, "initial_attestation": old_attestation, "final_activation": final_activation, "final_capability": new_capability, "final_attestation": new_attestation}


def activation_transition_semantics(
    package: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
) -> bool:
    try:
        steps = package["steps"]
        lifecycle = package["lifecycle_index"]
        operations = ["rollback", "reprovision", "record-refresh", "attest", "enable"]
        expected_lifecycle_id = sha256_bytes(canonical_bytes({
            "initial_activation_state_id": steps[0]["before_activation"]["state_id"],
            "target_activation_epoch": steps[0]["after_activation"]["activation_epoch"],
            "operations": operations,
        }))
        if (
            [row["command"]["operation"] for row in steps] != operations
            or not validate_registered(lifecycle, schema, registry, "activation-lifecycle-index/1.0.0", schema_sha, registry_sha)
            or lifecycle["index_id"] != sha256_bytes(canonical_bytes({key: value for key, value in lifecycle.items() if key != "index_id"}))
            or lifecycle["lifecycle_id"] != expected_lifecycle_id
            or lifecycle["activation_epoch"] != steps[0]["after_activation"]["activation_epoch"]
            or lifecycle["terminal_status"] != "enabled" or len(lifecycle["entries"]) != 5
        ):
            return False
        previous_receipt: dict[str, Any] | None = None
        previous_lifecycle_index: dict[str, Any] | None = None
        for expected_ordinal, step in enumerate(steps, start=1):
            command, receipt, journal, marker = (step[name] for name in ("command", "receipt", "journal", "marker"))
            before_activation, after_activation = step["before_activation"], step["after_activation"]
            before_capability, after_capability = step["before_capability"], step["after_capability"]
            capability_raw, root_raw, activation_raw, attestation_raw, context = step["authority"]
            administrator = next(
                row for row in before_capability["capabilities"]
                if row["producer_id"] == registry["strict_rollout"]["activation_administrator_producer_id"] and row["status"] == "active"
            )
            if not (
                validate_registered(command, schema, registry, "activation-transition-command/1.0.0", schema_sha, registry_sha)
                and validate_registered(receipt, schema, registry, "activation-transition-receipt/1.0.0", schema_sha, registry_sha)
                and receipt["receipt_id"] == sha256_bytes(canonical_bytes({key: value for key, value in receipt.items() if key != "receipt_id"}))
                and command["authority_context_id"] == context["context_id"]
                and context["principal_id"] == administrator["principal_id"]
                and command["fact_lock_profile_id"] == registry["lock_profile"]["profile_id"]
                and runtime_authority_binding_semantics(
                    registry, schema, schema_sha, registry_sha, capability_raw, root_raw, activation_raw, attestation_raw, context,
                )
                and journal_semantics(journal, marker, schema, registry, schema_sha, registry_sha)
                and command["expected_activation_epoch"] == before_activation["activation_epoch"]
                and command["expected_capability_epoch"] == before_capability["capability_epoch"]
                and command["expected_activation_state_id"] == before_activation["state_id"]
                and command["expected_capability_registry_id"] == before_capability["capability_registry_id"]
                and command["lifecycle_id"] == receipt["lifecycle_id"] == lifecycle["lifecycle_id"]
                and command["step_ordinal"] == receipt["step_ordinal"] == expected_ordinal
                and command["predecessor_receipt_id"] == receipt["predecessor_receipt_id"] == (None if previous_receipt is None else previous_receipt["receipt_id"])
                and command["expected_attestation_id"] == receipt["before_attestation_id"]
                and command["expected_attestation_sha256"] == (
                    sha256_bytes(canonical_bytes(package["initial_attestation"])) if command["operation"] in {"rollback", "attest"}
                    else (sha256_bytes(canonical_bytes(package["final_attestation"])) if command["operation"] == "enable" else None)
                )
                and receipt["before_activation_state_id"] == before_activation["state_id"]
                and receipt["after_activation_state_id"] == after_activation["state_id"]
                and receipt["before_capability_registry_id"] == before_capability["capability_registry_id"]
                and receipt["after_capability_registry_id"] == after_capability["capability_registry_id"]
                and receipt["journal_id"] == journal["journal_id"]
                and receipt["status"] == "committed" and marker["state"] == "committed"
                and command["approved_by"] == sorted(set(command["approved_by"]), key=lambda value: value.encode("utf-8"))
            ):
                return False
            before_lifecycle_index = step["before_lifecycle_index"]
            after_lifecycle_index = step["after_lifecycle_index"]
            expected_entry = {
                "step_ordinal": expected_ordinal, "transition_id": command["transition_id"], "operation": command["operation"],
                "predecessor_receipt_id": receipt["predecessor_receipt_id"], "receipt_id": receipt["receipt_id"],
                "receipt_path": runtime_path(registry, "activation_transition_receipt_template", transaction_id=command["transition_id"]),
                "receipt_sha256": sha256_bytes(canonical_bytes(receipt)),
            }
            if not (
                (before_lifecycle_index is None) == (expected_ordinal == 1)
                and (before_lifecycle_index is None or (
                    validate_registered(before_lifecycle_index, schema, registry, "activation-lifecycle-index/1.0.0", schema_sha, registry_sha)
                    and before_lifecycle_index["index_id"] == sha256_bytes(canonical_bytes({key: value for key, value in before_lifecycle_index.items() if key != "index_id"}))
                    and before_lifecycle_index == previous_lifecycle_index
                ))
                and validate_registered(after_lifecycle_index, schema, registry, "activation-lifecycle-index/1.0.0", schema_sha, registry_sha)
                and after_lifecycle_index["index_id"] == sha256_bytes(canonical_bytes({key: value for key, value in after_lifecycle_index.items() if key != "index_id"}))
                and after_lifecycle_index["lifecycle_id"] == expected_lifecycle_id
                and after_lifecycle_index["activation_epoch"] == lifecycle["activation_epoch"]
                and after_lifecycle_index["entries"] == ([] if before_lifecycle_index is None else before_lifecycle_index["entries"]) + [expected_entry]
                and after_lifecycle_index["terminal_status"] == ("enabled" if expected_ordinal == len(operations) else "in-progress")
            ):
                return False
            if previous_receipt is not None:
                previous_step = steps[command["step_ordinal"] - 2]
                if not (
                    before_activation == previous_step["after_activation"]
                    and before_capability == previous_step["after_capability"]
                    and receipt["before_activation_state_id"] == previous_receipt["after_activation_state_id"]
                    and receipt["before_capability_registry_id"] == previous_receipt["after_capability_registry_id"]
                    and receipt["before_attestation_id"] == previous_receipt["after_attestation_id"]
                ):
                    return False
            operation = command["operation"]
            business_targets = [row for row in journal["targets"] if row["role"] not in {"receipt", "activation-lifecycle-index"}]
            lifecycle_targets = [row for row in journal["targets"] if row["role"] == "activation-lifecycle-index"]
            receipt_targets = [row for row in journal["targets"] if row["role"] == "receipt"]
            if len(business_targets) != 1 or len(lifecycle_targets) != 1 or len(receipt_targets) != 1:
                return False
            target = business_targets[0]
            if operation in {"rollback", "enable"}:
                expected_role = "activation-state"
                expected_path = registry["runtime_paths"]["strict_activation_state"]["path"]
                expected_operation = "replace"
                expected_before = canonical_bytes(before_activation)
                expected_after = canonical_bytes(after_activation)
            elif operation == "reprovision":
                expected_role = "capability-registry"
                expected_path = registry["runtime_paths"]["writer_capability_registry"]["path"]
                expected_operation = "replace"
                expected_before = canonical_bytes(before_capability)
                expected_after = canonical_bytes(after_capability)
            elif operation == "record-refresh":
                expected_role = "transition-state"
                expected_path = runtime_path(registry, "activation_transition_state_template", transaction_id=command["transition_id"])
                expected_operation = "create"
                expected_before = None
                expected_after = canonical_bytes(step["refresh_receipt"])
            else:
                expected_role = "attestation"
                expected_path = registry["runtime_paths"]["writer_fence_attestation"]["path"]
                expected_operation = "replace"
                expected_before = canonical_bytes(step["attestation_preimage"])
                expected_after = canonical_bytes(step["attestation"])
            receipt_path = runtime_path(registry, "activation_transition_receipt_template", transaction_id=command["transition_id"])
            lifecycle_target = lifecycle_targets[0]
            if not (
                target["role"] == expected_role and target["path"] == expected_path and target["operation"] == expected_operation
                and target["after_sha256"] == sha256_bytes(expected_after)
                and target["before_sha256"] == (None if expected_before is None else sha256_bytes(expected_before))
                and lifecycle_target["path"] == runtime_path(registry, "activation_lifecycle_index_template", lifecycle_id=lifecycle["lifecycle_id"])
                and lifecycle_target["operation"] == ("create" if expected_ordinal == 1 else "replace")
                and lifecycle_target["before_sha256"] == (None if before_lifecycle_index is None else sha256_bytes(canonical_bytes(before_lifecycle_index)))
                and lifecycle_target["after_sha256"] == sha256_bytes(canonical_bytes(after_lifecycle_index))
                and receipt_targets[0]["path"] == receipt_path
                and receipt_targets[0]["after_sha256"] == sha256_bytes(canonical_bytes(receipt))
            ):
                return False
            if operation == "rollback" and not (
                after_activation["activation_epoch"] == before_activation["activation_epoch"] + 1
                and after_activation["mode"] == "legacy" and after_activation["attestation_id"] is None
                and after_capability == before_capability and receipt["full_refresh_receipt_id"] is None and receipt["attestation_id"] is None
            ):
                return False
            if operation == "reprovision" and not (
                after_activation == before_activation and after_capability["capability_epoch"] == before_capability["capability_epoch"] + 1
                and receipt["full_refresh_receipt_id"] is None and receipt["attestation_id"] is None
            ):
                return False
            if operation in {"record-refresh", "attest", "enable"}:
                refresh = step["refresh_receipt"]
                if not (
                    isinstance(refresh, dict)
                    and validate_registered(refresh, schema, registry, "refresh-run-receipt/1.0.0", schema_sha, registry_sha)
                    and receipt["full_refresh_receipt_id"] == refresh["receipt_id"]
                ):
                    return False
            if operation in {"attest", "enable"}:
                attestation = step["attestation"]
                if not (
                    isinstance(attestation, dict)
                    and validate_registered(attestation, schema, registry, "writer-fence-migration-attestation/1.0.0", schema_sha, registry_sha)
                    and attestation["attestation_id"] == sha256_bytes(canonical_bytes({key: value for key, value in attestation.items() if key != "attestation_id"}))
                    and attestation["activation_epoch"] == after_activation["activation_epoch"]
                    and attestation["capability_registry_id"] == after_capability["capability_registry_id"]
                    and receipt["attestation_id"] == attestation["attestation_id"]
                ):
                    return False
            if operation == "enable" and not (
                after_activation["mode"] == "strict"
                and after_activation["activation_epoch"] == before_activation["activation_epoch"]
                and after_activation["attestation_id"] == receipt["attestation_id"]
            ):
                return False
            previous_receipt = receipt
            previous_lifecycle_index = after_lifecycle_index
        return (
            previous_receipt is not None and package["final_activation"] == steps[-1]["after_activation"]
            and package["final_capability"] == steps[-1]["after_capability"]
            and package["final_attestation"] == steps[-1]["attestation"]
            and package["lifecycle_index"] == steps[-1]["after_lifecycle_index"]
        )
    except (KeyError, TypeError, ValueError):
        return False


def resolved_selection(policy: dict[str, Any]) -> list[str]:
    inventory = policy["physical_workstream_inventory"]
    catalog_ids = [row["workstream_id"] for row in policy["workstream_catalog"]]
    inventory_ids = [row["workstream_id"] for row in inventory]
    inventory_sources = [(source["root_instance_id"], source["path"]) for row in inventory for source in (row["wdr_source"], row["sidecar_source"])]
    if (
        not physical_inventory_rows_valid(inventory)
        or not physical_inventory_rows_valid(policy["workstream_catalog"])
        or len(catalog_ids) != len(set(catalog_ids))
        or len(inventory_ids) != len(set(inventory_ids))
        or len(inventory_sources) != len(set(inventory_sources))
        or policy["physical_workstream_inventory_id"] != canonical_inventory_id(inventory)
        or policy["workstream_catalog_id"] != canonical_catalog_id(policy["workstream_catalog"])
        or policy["workstream_catalog"] != inventory
    ):
        return []
    included = catalog_ids if policy["include_workstreams"] == "all" else list(policy["include_workstreams"])
    if any(item not in catalog_ids for item in included + list(policy["exclude_workstreams"])):
        return []
    return sorted(set(included) - set(policy["exclude_workstreams"]), key=lambda value: value.encode("utf-8"))


def expected_projection_instances(registry: dict[str, Any], policy: dict[str, Any]) -> dict[str, list[str | None]]:
    expected = {profile["projection"]: [None] for profile in registry["projection_input_profiles"]}
    expected["meeting-pack"] = list(policy["meeting_kinds"])
    return expected


def panel_binding_semantics(panel: dict[str, Any], built: dict[str, list[dict[str, Any]]], registry: dict[str, Any], policy: dict[str, Any], generation: dict[str, Any]) -> bool:
    panel_items = built.get("management-panel", [])
    if len(panel_items) != 1 or panel_items[0]["envelope"]["payload"] != panel:
        return False
    expected_instances = expected_projection_instances(registry, policy)
    if set(built) != set(expected_instances):
        return False
    for kind, keys in expected_instances.items():
        actual_keys = [item["envelope"]["instance_key"] for item in built[kind]]
        if sorted(actual_keys, key=lambda value: (value is not None, b"" if value is None else value.encode("utf-8"))) != sorted(keys, key=lambda value: (value is not None, b"" if value is None else value.encode("utf-8"))):
            return False
        if any(item["envelope"]["generation_id"] != generation["generation_id"] for item in built[kind]):
            return False
    for binding in registry["panel_binding_map"]:
        items = built[binding["projection_kind"]]
        if binding["cardinality"] == "one" and len(items) != 1:
            return False
        if binding["cardinality"] == "one-per-meeting-kind" and {item["envelope"]["instance_key"] for item in items} != set(policy["meeting_kinds"]):
            return False
        try:
            values = [json_pointer(item["envelope"]["payload"], binding["source_pointer"]) for item in items]
            if binding["merge_mode"] == "object-by-key":
                keys = [json_pointer(value, binding["key_pointer"]) for value in values]
                if len(keys) != len(set(keys)):
                    return False
                expected_value = {key: value for key, value in sorted(zip(keys, values), key=lambda row: row[0].encode("utf-8"))}
            else:
                expected_value = values[0]
            if json_pointer(panel, binding["panel_pointer"]) != expected_value:
                return False
        except (KeyError, IndexError, TypeError):
            return False
    return True


def snapshot_time_fixture(
    registry: dict[str, Any], schema_sha: str, registry_sha: str, policy: dict[str, Any], refresh_receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    refresh_id = "refresh-snapshot-fixture" if refresh_receipt is None else refresh_receipt["refresh_id"]
    source_as_of = _utc_instant(policy["as_of"])
    render_time = lambda value: value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    request = {
        "contract": expected_contract_ref(registry, "refresh-request/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "refresh_id": refresh_id,
        "requested_source_as_of": policy["as_of"], "requested_at": render_time(source_as_of - timedelta(seconds=2)),
    }
    request["request_id"] = sha256_bytes(canonical_bytes(request))
    lock_receipt = {
        "contract": expected_contract_ref(registry, "snapshot-lock-receipt/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "refresh_request_id": request["request_id"],
        "snapshot_id": policy["snapshot_id"], "lock_profile_id": registry["lock_profile"]["profile_id"],
        "root_registry_state_id": sha256_bytes(b"snapshot-root-registry"), "fact_generation": 7,
        "maximum_fact_observed_at": render_time(source_as_of - timedelta(seconds=1)), "source_as_of": policy["as_of"],
        "acquired_at": policy["as_of"],
    }
    lock_receipt["receipt_id"] = sha256_bytes(canonical_bytes(lock_receipt))
    return {"request": request, "lock_receipt": lock_receipt, "evaluation_time": render_time(source_as_of + timedelta(seconds=1))}


def _source_time_values(document: Any, pointer: str) -> list[Any]:
    parts = pointer.strip("/").split("/") if pointer else []
    values = [document]
    for part in parts:
        next_values: list[Any] = []
        for value in values:
            if part == "*" and isinstance(value, list):
                next_values.extend(value)
            elif isinstance(value, dict) and part in value:
                next_values.append(value[part])
            else:
                return []
        values = next_values
    return values


def source_as_of_semantics(
    panel: dict[str, Any], policy: dict[str, Any], refresh_receipt: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None, schema: dict[str, Any] | None = None,
    schema_sha: str | None = None, registry_sha: str | None = None, snapshot: dict[str, Any] | None = None,
) -> bool:
    expected = policy["as_of"]
    documents: dict[str, list[dict[str, Any]]] = {
        "management-panel-payload/2.0.0": [panel],
        "state-audit-payload/2.0.0": [panel["sync"]["audit"]],
        "program-status-payload/2.0.0": [panel["sync"]["canonical"]["status"]],
        "roadmap-payload/2.0.0": [panel["sync"]["canonical"]["roadmap"]],
        "meeting-pack-payload/2.0.0": list(panel["sync"]["canonical"]["meetings"].values()),
        "flow-graph-payload/1.0.0": [panel["sync"]["canonical"]["flow"]],
        "refresh-run-receipt/1.0.0": [] if refresh_receipt is None else [refresh_receipt],
    }
    if registry is None:
        return all(
            value == expected
            for contract_documents in documents.values() for document in contract_documents
            for value in (
                [document.get("sync", {}).get("source_as_of")] if "sync" in document
                else [document.get("source_as_of", document.get("state", {}).get("as_of"))]
            )
        )
    if {row["contract"] for row in registry["source_time_bindings"]} != set(documents):
        return False
    for binding in registry["source_time_bindings"]:
        for document in documents[binding["contract"]]:
            values = _source_time_values(document, binding["pointer"])
            if not values or any(value != expected for value in values):
                return False
    if refresh_receipt is None or schema is None or schema_sha is None or registry_sha is None:
        return True
    snapshot = snapshot or snapshot_time_fixture(registry, schema_sha, registry_sha, policy, refresh_receipt)
    request, lock_receipt = snapshot["request"], snapshot["lock_receipt"]
    try:
        return bool(
            validate_registered(request, schema, registry, "refresh-request/1.0.0", schema_sha, registry_sha)
            and validate_registered(lock_receipt, schema, registry, "snapshot-lock-receipt/1.0.0", schema_sha, registry_sha)
            and request["request_id"] == sha256_bytes(canonical_bytes({key: value for key, value in request.items() if key != "request_id"}))
            and lock_receipt["receipt_id"] == sha256_bytes(canonical_bytes({key: value for key, value in lock_receipt.items() if key != "receipt_id"}))
            and request["requested_source_as_of"] == lock_receipt["source_as_of"] == expected
            and policy["snapshot_id"] == refresh_receipt["snapshot_id"] == lock_receipt["snapshot_id"]
            and policy["snapshot_lock_receipt_id"] == refresh_receipt["snapshot_lock_receipt_id"] == lock_receipt["receipt_id"]
            and lock_receipt["lock_profile_id"] == registry["lock_profile"]["profile_id"]
            and _utc_instant(request["requested_at"]) <= _utc_instant(lock_receipt["acquired_at"])
            and _utc_instant(lock_receipt["maximum_fact_observed_at"]) <= _utc_instant(expected)
            and _utc_instant(expected) == _utc_instant(lock_receipt["acquired_at"]) <= _utc_instant(snapshot["evaluation_time"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def intent_convergence_semantics(
    outbox: dict[str, Any], verdict: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any],
    schema_sha: str, registry_sha: str, consumed_receipts: dict[str, bytes] | None = None,
) -> bool:
    try:
        if not (
            validate_registered(outbox, schema, registry, "mutation-intent-outbox/1.0.0", schema_sha, registry_sha)
            and validate_registered(verdict, schema, registry, "intent-convergence-verdict/1.0.0", schema_sha, registry_sha)
            and outbox["outbox_id"] == sha256_bytes(canonical_bytes({key: value for key, value in outbox.items() if key != "outbox_id"}))
            and verdict["verdict_id"] == sha256_bytes(canonical_bytes({key: value for key, value in verdict.items() if key != "verdict_id"}))
            and verdict["outbox_id"] == outbox["outbox_id"]
        ):
            return False
        entries = outbox["entries"]
        sequences = [row["sequence"] for row in entries]
        intent_ids = [row["intent_id"] for row in entries]
        if (
            sequences != list(range(1, len(entries) + 1))
            or len(intent_ids) != len(set(intent_ids))
            or len({row["source_command_id"] for row in entries}) != len(entries)
            or any(row["field_set"] != sorted(row["field_set"], key=lambda value: value.encode("utf-8")) for row in entries)
        ):
            return False
        pending: list[str] = []
        for row in entries:
            intent = row["intent"]
            if not (
                validate_registered(intent, schema, registry, "status-mutation-intent/1.0.0", schema_sha, registry_sha)
                and row["intent_id"] == sha256_bytes(canonical_bytes(intent))
                and row["producer_id"] == intent["origin_producer"]
                and row["workstream_id"] == intent["workstream_id"]
                and row["field_set"] == sorted(intent["set"], key=lambda value: value.encode("utf-8"))
            ):
                return False
            status = row["status"]
            if status == "pending":
                pending.append(row["intent_id"])
                if row["consumed_receipt_id"] is not None or row["last_error"] is not None:
                    return False
            elif status == "consumed":
                receipt_id = row["consumed_receipt_id"]
                if row["attempts"] < 1 or row["last_error"] is not None or receipt_id is None:
                    return False
                if consumed_receipts is not None:
                    raw = consumed_receipts.get(receipt_id)
                    if raw is None:
                        return False
                    receipt = json.loads(raw)
                    if not (
                        canonical_bytes(receipt) == raw
                        and receipt["receipt_id"] == receipt_id
                        and validate_registered(receipt, schema, registry, "fact-mutation-receipt/1.0.0", schema_sha, registry_sha)
                    ):
                        return False
            else:
                return False
        pending.sort(key=lambda value: value.encode("utf-8"))
        expected_status = "pending" if pending else "converged"
        return (
            verdict["evaluated_through_sequence"] == (sequences[-1] if sequences else 0)
            and verdict["pending_intent_ids"] == pending
            and verdict["failed_intent_ids"] == []
            and verdict["waived_intent_ids"] == []
            and verdict["status"] == expected_status
        )
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def resolve_fact_command_replay(
    index: dict[str, Any], receipt_store: dict[str, bytes], command_id: str, command_fingerprint: str,
    registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
) -> tuple[str, dict[str, Any] | None]:
    try:
        if not (
            validate_registered(index, schema, registry, "fact-command-receipt-index/1.0.0", schema_sha, registry_sha)
            and index["index_id"] == sha256_bytes(canonical_bytes({key: value for key, value in index.items() if key != "index_id"}))
        ):
            return "invalid", None
        entries = index["entries"]
        if (
            [row["sequence"] for row in entries] != list(range(1, len(entries) + 1))
            or index["next_sequence"] != len(entries) + 1
            or len({row["command_id"] for row in entries}) != len(entries)
            or len({row["transaction_id"] for row in entries}) != len(entries)
            or len({row["receipt_id"] for row in entries}) != len(entries)
            or set(receipt_store) != {row["receipt_path"] for row in entries}
        ):
            return "invalid", None
        by_command: dict[str, dict[str, Any]] = {}
        for row in entries:
            expected_paths = {
                runtime_path(registry, "fact_receipt_template", transaction_id=row["transaction_id"]),
                runtime_path(registry, "repair_fact_receipt_template", transaction_id=row["transaction_id"]),
            }
            raw = receipt_store.get(row["receipt_path"])
            if row["receipt_path"] not in expected_paths or raw is None or sha256_bytes(raw) != row["receipt_sha256"]:
                return "invalid", None
            receipt = json.loads(raw)
            if not (
                canonical_bytes(receipt) == raw
                and validate_registered(receipt, schema, registry, "fact-mutation-receipt/1.0.0", schema_sha, registry_sha)
                and receipt["receipt_id"] == row["receipt_id"]
                and receipt["transaction_id"] == row["transaction_id"]
                and receipt["authorization"]["authorized_command_fingerprint"] == row["command_fingerprint"]
            ):
                return "invalid", None
            by_command[row["command_id"]] = {"entry": row, "receipt": receipt}
        match = by_command.get(command_id)
        if match is None:
            return "new", None
        if match["entry"]["command_fingerprint"] != command_fingerprint:
            return "conflict", None
        return "noop", match["receipt"]
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return "invalid", None


def publication_eligibility_semantics(
    panel: dict[str, Any], physical_inventory: dict[str, Any], policy: dict[str, Any], generation: dict[str, Any], registry: dict[str, Any],
    schema: dict[str, Any], schema_sha: str, registry_sha: str, built: dict[str, list[dict[str, Any]]] | None = None,
    outbox: dict[str, Any] | None = None, convergence_verdict: dict[str, Any] | None = None,
    consumed_receipts: dict[str, bytes] | None = None,
) -> bool:
    sync = panel["sync"]
    audit = sync["audit"]
    drift = sync["action_projection"]
    policy_body = {key: value for key, value in policy.items() if key != "policy_id"}
    generation_body = {key: value for key, value in generation.items() if key != "generation_id"}
    status_ids = [row["workstream_id"] for row in sync["canonical"]["status"]["workstream_current"]]
    selected = resolved_selection(policy)
    catalog = panel_binding_catalog(registry, schema_sha, registry_sha)
    inventory_sources = [source for row in policy["physical_workstream_inventory"] for source in (row["wdr_source"], row["sidecar_source"])]
    inventory_map = {(row["root_instance_id"], row["path"]): row for row in inventory_sources}
    generation_inventory = [row for row in generation["leaf_sources"] if row["source_kind"] in {"selected-physical-wdr", "wdr-action-sidecar"}]
    generation_inventory_map = {(row["root_instance_id"], row["path"]): row for row in generation_inventory}
    inventory_leaf_ok = len(inventory_map) == len(inventory_sources) and generation_inventory_map == inventory_map
    memory_roots = [row["root_instance_id"] for row in generation.get("roots", []) if row.get("root") == "memory"]
    lineage_scope_ok = True if built is None else all(
        item["manifest"]["selection_policy_id"] == policy["policy_id"]
        and item["receipt"]["selection_policy_id"] == policy["policy_id"]
        and item["envelope"]["generation_id"] == generation["generation_id"]
        for instances in built.values() for item in instances
    )
    if outbox is None:
        outbox = {
            "contract": expected_contract_ref(registry, "mutation-intent-outbox/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "outbox_generation": 1, "entries": [],
        }
        outbox["outbox_id"] = sha256_bytes(canonical_bytes(outbox))
    convergence_verdict = convergence_verdict or audit["intent_convergence"]
    convergence_ok = (
        audit["intent_convergence"] == convergence_verdict
        and intent_convergence_semantics(
            outbox, convergence_verdict, registry, schema, schema_sha, registry_sha, consumed_receipts,
        )
    )
    scope_ok = (
        validate_registered(physical_inventory, schema, registry, "physical-workstream-inventory/1.0.0", schema_sha, registry_sha)
        and validate_registered(policy, schema, registry, "selection-policy/1.0.0", schema_sha, registry_sha)
        and validate_registered(generation, schema, registry, "generation-envelope/1.0.0", schema_sha, registry_sha)
        and validate_registered(panel, schema, registry, "management-panel-payload/2.0.0", schema_sha, registry_sha)
        and validate_registered(audit, schema, registry, "state-audit-payload/2.0.0", schema_sha, registry_sha)
        and validate_registered(drift, schema, registry, "action-projection-drift-verdict/1.0.0", schema_sha, registry_sha)
        and physical_inventory["attestation_id"] == sha256_bytes(canonical_bytes({key: value for key, value in physical_inventory.items() if key != "attestation_id"}))
        and physical_inventory["inventory_id"] == canonical_inventory_id(physical_inventory["workstreams"])
        and physical_inventory["workstreams"] == policy["physical_workstream_inventory"] == policy["workstream_catalog"]
        and physical_inventory["inventory_id"] == policy["physical_workstream_inventory_id"]
        and physical_inventory["fact_generation"] == generation["fact_generation"]
        and memory_roots == [physical_inventory["memory_root_instance_id"]]
        and policy["policy_id"] == sha256_bytes(canonical_bytes(policy_body))
        and generation["generation_id"] == sha256_bytes(canonical_bytes(generation_body))
        and bool(selected)
        and generation["physical_workstream_inventory_id"] == policy["physical_workstream_inventory_id"]
        and generation["workstream_catalog_id"] == policy["workstream_catalog_id"]
        and generation["panel_catalog_id"] == catalog["catalog_id"]
        and validate_registered(catalog, schema, registry, "panel-binding-catalog/1.0.0", schema_sha, registry_sha)
        and inventory_leaf_ok
        and sync["selection_policy_id"] == policy["policy_id"] == generation["selection_policy_id"] == drift["selection_policy_id"] == audit["selection_policy_id"]
        and sync["generation_id"] == generation["generation_id"] == drift["generation_id"]
        and sorted(status_ids, key=lambda value: value.encode("utf-8")) == selected
        and sorted(drift["selected_workstreams"], key=lambda value: value.encode("utf-8")) == selected
        and sorted(audit["selected_workstreams"], key=lambda value: value.encode("utf-8")) == selected
        and lineage_scope_ok
        and source_as_of_semantics(panel, policy, registry=registry)
        and (built is None or panel_binding_semantics(panel, built, registry, policy, generation))
    )
    eligible = (
        sync["artifact_integrity"] == "pass"
        and sync["business_freshness"] == "fresh"
        and audit["audit_status"] == "pass"
        and audit["execution_disposition"] == "ready"
        and convergence_ok
        and convergence_verdict["status"] == "converged"
        and drift_semantics(drift)
        and drift["overall_status"] == "in-sync"
        and scope_ok
    )
    return (sync["publication_eligibility"] == "eligible") is eligible


def capability_record_digest(record: dict[str, Any]) -> str:
    body = {key: value for key, value in record.items() if key not in {"capability_id", "authorization_record_digest"}}
    return sha256_bytes(canonical_bytes(body))


def authority_native_fixture(
    registry: dict[str, Any], producer_id: str, platform: str = "posix",
) -> tuple[str, str, str, dict[str, Any], dict[str, Any]]:
    profile = registry["runtime_authority_profile"]
    executable_sha256 = sha256_bytes(f"runtime-executable:{producer_id}".encode("utf-8"))
    adapter = profile["principal_adapters"][platform]
    if platform == "posix":
        identity_preimage = {
            "adapter_id": adapter["id"], "effective_uid_decimal": "501",
            "executable_device_decimal": "16777234", "executable_inode_decimal": "1001",
            "executable_sha256": executable_sha256, "service_manager": "launchd", "service_unit": producer_id,
        }
    else:
        identity_preimage = {
            "adapter_id": adapter["id"], "token_user_sid_sddl": "S-1-5-21-1000",
            "token_elevation_type": "full", "token_impersonation_level": "SecurityImpersonation",
            "executable_volume_serial_hex": "A1B2C3D4", "executable_file_id_hex": "0000000000001001",
            "executable_sha256": executable_sha256, "service_name": producer_id,
        }
    if list(identity_preimage) != adapter["preimage_fields"]:
        raise ValueError("native identity fixture does not match canonical preimage field order")
    effective_identity_sha256 = sha256_bytes(canonical_bytes(identity_preimage))
    verification = {
        "adapter_boundary": profile["adapter_boundary"], "native_api_observed": True,
        "opened_executable_handle": True, "path_alias_rejected": True,
        "namespace_or_token_verified": True, "service_identity_verified": True,
    }
    principal_body = {
        "authority_profile_id": profile["profile_id"], "platform": platform,
        "native_preimage": identity_preimage,
    }
    return sha256_bytes(canonical_bytes(principal_body)), effective_identity_sha256, executable_sha256, identity_preimage, verification


def authority_principal_fixture(registry: dict[str, Any], producer_id: str, platform: str = "posix") -> tuple[str, str, str]:
    principal_id, identity_sha, executable_sha, _, _ = authority_native_fixture(registry, producer_id, platform)
    return principal_id, identity_sha, executable_sha


def capability_registry_fixture(
    registry: dict[str, Any], schema_sha: str, registry_sha: str, platform: str = "posix",
) -> dict[str, Any]:
    capabilities = []
    for spec in registry["strict_rollout"]["writer_specs"]:
        principal_id, _, _ = authority_principal_fixture(registry, spec["producer_id"], platform)
        record = {
            "producer_id": spec["producer_id"], "principal_id": principal_id, "status": "active",
            "allowed_operations": sorted(copy.deepcopy(spec["allowed_operations"])),
            "allowed_fields": sorted(copy.deepcopy(spec["allowed_fields"])),
            "allowed_sections": sorted(copy.deepcopy(spec["allowed_sections"])),
        }
        digest = capability_record_digest(record)
        record["capability_id"] = digest
        record["authorization_record_digest"] = digest
        capabilities.append(record)
    document = {
        "contract": expected_contract_ref(registry, "writer-capability-registry/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "capability_epoch": 3,
        "capabilities": sorted(capabilities, key=lambda row: row["producer_id"].encode("utf-8")),
    }
    document["capability_registry_id"] = sha256_bytes(canonical_bytes(document))
    return document


def expected_action_delta(command: dict[str, Any]) -> dict[str, Any]:
    operation = command["operation"]
    before = command.get("expected_revision") if operation == "patch" else None
    changed = command["set"] if operation == "patch" else command["create"]
    return {
        "action_id": command["action_id"],
        "operation": operation,
        "before_revision": before,
        "after_revision": 1 if before is None else before + 1,
        "changed_fields": sorted(changed, key=lambda value: value.encode("utf-8")),
        "evidence_fingerprints": sorted({row["source_fingerprint"] for row in command["evidence"]}, key=lambda value: value.encode("utf-8")),
    }


def mutation_target(role: str, operation: str, order: int, path: str) -> dict[str, Any]:
    root = "123e4567-e89b-42d3-a456-426614174000"
    before_hash = None if operation == "create" else "sha256:" + str((order + 1) % 10) * 64
    after_hash = None if operation == "remove" else "sha256:" + str((order + 6) % 10) * 64
    before_image = None if before_hash is None else {"root_instance_id": root, "path": f"state/transactions/pending/images/{order}-before", "sha256": before_hash}
    after_image = None if after_hash is None else {"root_instance_id": root, "path": f"state/transactions/pending/images/{order}-after", "sha256": after_hash}
    return {
        "role": role, "operation": operation, "apply_order": order, "root_instance_id": root, "path": path,
        "before_sha256": before_hash, "after_sha256": after_hash, "before_image": before_image, "after_image": after_image,
    }


def journal_fixture(
    kind: str, schema_sha: str, registry_sha: str, registry: dict[str, Any],
    business_paths: list[str | dict[str, str]] | None = None,
    include_intent_outbox: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    contract = {"schema_id": "urn:adp:panel-sync-contracts:2026-07-24#transaction-journal-manifest-v1", "schema_sha256": schema_sha, "registry_sha256": registry_sha}
    transaction_id = f"tx-{kind}-1"
    token = filesystem_token(transaction_id)
    journal_dir = registry["runtime_paths"]["journal_dir_template"].replace("{transaction_token}", token)
    if kind == "panel":
        generation_id = "sha256:" + "a" * 64
        receipt_paths = [f"receipts/panel/{token}.json"]
        targets = [
            mutation_target("projection", "create", 0, runtime_path(registry, "canonical_projection_template", generation_id=generation_id, projection_kind="program-status", instance_key="singleton")),
            mutation_target("panel", "create", 1, runtime_path(registry, "management_panel_template", generation_id=generation_id, instance_key="singleton")),
            mutation_target("lineage-object", "create", 2, runtime_path(registry, "selection_policy_template", generation_id=generation_id)),
            mutation_target("lineage-index", "create", 3, runtime_path(registry, "generation_lineage_index_template", generation_id=generation_id)),
            mutation_target("panel-state", "replace", 4, registry["runtime_paths"]["panel_state"]["path"]),
        ]
    else:
        receipt_paths = [f"receipts/fact/{token}.json"] if kind != "repair" else [f"receipts/repair/{token}-fact.json"]
        paths = ["actions/action-ledger.md"] if business_paths is None else business_paths
        targets = [
            mutation_target(
                "business",
                target_path.get("operation", "replace") if isinstance(target_path, dict) else "replace",
                index,
                target_path["path"] if isinstance(target_path, dict) else target_path,
            )
            for index, target_path in enumerate(paths)
        ]
        targets.append(mutation_target("fact-generation", "replace", len(targets), "state/fact-generation.json"))
        if kind in {"fact", "repair"}:
            targets.append(mutation_target(
                "fact-command-index", "replace", len(targets),
                registry["runtime_paths"]["fact_command_receipt_index"]["path"],
            ))
        if kind == "fact" and include_intent_outbox:
            targets.append(mutation_target(
                "intent-outbox", "replace", len(targets),
                registry["runtime_paths"]["mutation_intent_outbox"]["path"],
            ))
    if kind == "repair":
        targets.append(mutation_target(
            "nonce", "replace", len(targets),
            runtime_path(registry, "repair_nonce_template", nonce_id="sha256:" + "1" * 64),
        ))
    for receipt_path in receipt_paths:
        targets.append(mutation_target("receipt", "create", len(targets), receipt_path))
    if kind == "panel":
        targets.append(mutation_target("pointer", "replace", len(targets), registry["runtime_paths"]["panel_current_pointer"]["path"]))
    for target in targets:
        if target["before_image"] is not None:
            target["before_image"]["path"] = f"{journal_dir}/images/{target['apply_order']}-before"
        if target["after_image"] is not None:
            target["after_image"]["path"] = f"{journal_dir}/images/{target['apply_order']}-after"
    manifest = {
        "contract": contract, "schema_version": "1.0.0", "journal_id": f"journal-{kind}-1", "transaction_id": transaction_id, "journal_dir": journal_dir,
        "manifest_path": runtime_path(registry, "journal_manifest_template", transaction_id=transaction_id),
        "prepared_marker_path": runtime_path(registry, "journal_prepared_marker_template", transaction_id=transaction_id),
        "terminal_marker_path": runtime_path(registry, "journal_terminal_marker_template", transaction_id=transaction_id),
        "recovery_receipt_path": runtime_path(registry, "journal_recovery_receipt_template", transaction_id=transaction_id),
        "transaction_kind": kind, "authorization": None, "targets": targets, "receipt_target_paths": receipt_paths,
        "prepared_at": "2026-07-24T02:00:00Z",
    }
    if kind == "panel":
        manifest["manifest_path"] = runtime_path(registry, "publication_journal_template", generation_id=generation_id)
        manifest["terminal_marker_path"] = runtime_path(registry, "publication_marker_template", generation_id=generation_id)
    manifest["manifest_id"] = sha256_bytes(canonical_bytes(manifest))
    marker = {
        "contract": {"schema_id": "urn:adp:panel-sync-contracts:2026-07-24#journal-marker-v1", "schema_sha256": schema_sha, "registry_sha256": registry_sha},
        "schema_version": "1.0.0", "journal_id": manifest["journal_id"], "manifest_id": manifest["manifest_id"],
        "state": "committed", "marked_at": "2026-07-24T02:00:01Z",
    }
    marker["marker_id"] = sha256_bytes(canonical_bytes(marker))
    return manifest, marker


def journal_semantics(
    manifest: dict[str, Any], marker: dict[str, Any], schema: dict[str, Any], registry: dict[str, Any], schema_sha: str, registry_sha: str,
) -> bool:
    if not validate_registered(manifest, schema, registry, "transaction-journal-manifest/1.0.0", schema_sha, registry_sha) or not validate_registered(marker, schema, registry, "journal-marker/1.0.0", schema_sha, registry_sha):
        return False
    manifest_body = {key: value for key, value in manifest.items() if key != "manifest_id"}
    marker_body = {key: value for key, value in marker.items() if key != "marker_id"}
    if manifest["manifest_id"] != sha256_bytes(canonical_bytes(manifest_body)) or marker["marker_id"] != sha256_bytes(canonical_bytes(marker_body)):
        return False
    targets = manifest["targets"]
    if marker["state"] not in {"committed", "rolled-back"}:
        return False
    if manifest["journal_dir"] != f"state/transactions/{filesystem_token(manifest['transaction_id'])}":
        return False
    transaction_id = manifest["transaction_id"]
    kind = manifest["transaction_kind"]
    expected_local_paths = {
        "manifest_path": runtime_path(registry, "journal_manifest_template", transaction_id=transaction_id),
        "prepared_marker_path": runtime_path(registry, "journal_prepared_marker_template", transaction_id=transaction_id),
        "terminal_marker_path": runtime_path(registry, "journal_terminal_marker_template", transaction_id=transaction_id),
        "recovery_receipt_path": runtime_path(registry, "journal_recovery_receipt_template", transaction_id=transaction_id),
    }
    if kind == "panel":
        lineage_indexes = [row for row in targets if row["role"] == "lineage-index"]
        template = registry["runtime_paths"]["generation_lineage_index_template"]["path"]
        prefix, suffix = template.split("{generation_token}")
        if len(lineage_indexes) != 1 or not lineage_indexes[0]["path"].startswith(prefix) or not lineage_indexes[0]["path"].endswith(suffix):
            return False
        generation_token = lineage_indexes[0]["path"][len(prefix):len(lineage_indexes[0]["path"]) - len(suffix)]
        if not re.fullmatch(r"h_[0-9a-f]{64}", generation_token):
            return False
        expected_local_paths["manifest_path"] = registry["runtime_paths"]["publication_journal_template"]["path"].replace("{generation_token}", generation_token)
        expected_local_paths["terminal_marker_path"] = registry["runtime_paths"]["publication_marker_template"]["path"].replace("{generation_token}", generation_token)
    if any(manifest[name] != value for name, value in expected_local_paths.items()) or len(set(expected_local_paths.values())) != 4:
        return False
    if [row["apply_order"] for row in targets] != list(range(len(targets))):
        return False
    identities = [(row["root_instance_id"], row["path"]) for row in targets]
    if len(identities) != len(set(identities)):
        return False
    for row in targets:
        before = row["before_image"]
        after = row["after_image"]
        if row["operation"] == "create" and (row["before_sha256"] is not None or before is not None or row["after_sha256"] is None or after is None):
            return False
        if row["operation"] == "replace" and (row["before_sha256"] is None or before is None or row["after_sha256"] is None or after is None):
            return False
        if row["operation"] == "remove" and (row["before_sha256"] is None or before is None or row["after_sha256"] is not None or after is not None):
            return False
        for locator, expected in ((before, row["before_sha256"]), (after, row["after_sha256"])):
            if locator is not None and (locator["root_instance_id"] != row["root_instance_id"] or locator["sha256"] != expected):
                return False
        if before is not None and before["path"] != runtime_path(registry, "journal_before_image_template", transaction_id=transaction_id, apply_order=row["apply_order"]):
            return False
        if after is not None and after["path"] != runtime_path(registry, "journal_after_image_template", transaction_id=transaction_id, apply_order=row["apply_order"]):
            return False
    role_counts = {role: sum(row["role"] == role for row in targets) for role in {row["role"] for row in targets}}
    if kind == "fact" and not (
        set(role_counts) <= {"business", "fact-generation", "fact-command-index", "intent-outbox", "receipt"}
        and (role_counts.get("business", 0) >= 1 or role_counts.get("intent-outbox") == 1)
        and role_counts.get("fact-generation") == 1
        and role_counts.get("fact-command-index") == 1 and role_counts.get("intent-outbox", 0) in {0, 1}
        and role_counts.get("receipt") == 1
    ):
        return False
    if kind == "repair" and not (role_counts == {"business": role_counts.get("business", 0), "fact-generation": 1, "fact-command-index": 1, "nonce": 1, "receipt": 1} and role_counts["business"] >= 1):
        return False
    if kind == "repair-attempt" and role_counts != {"repair-attempt-ledger": 1, "repair-index": 1, "receipt": 1}:
        return False
    if kind == "panel" and not (
        role_counts == {
            "projection": role_counts.get("projection", 0), "panel": 1,
            "lineage-object": role_counts.get("lineage-object", 0), "lineage-index": 1,
            "pointer": 1, "panel-state": 1, "receipt": 1,
        }
        and role_counts["projection"] >= 1 and role_counts["lineage-object"] >= 1
        and targets[-1]["role"] == "pointer"
    ):
        return False
    if kind == "release-evidence" and role_counts not in (
        {"release-evidence": 2, "receipt": 1},
        {"release-evidence": 2, "history-index": 1, "receipt": 1},
    ):
        return False
    if kind == "activation" and not (
        role_counts.get("receipt") == 1
        and role_counts.get("activation-lifecycle-index") == 1
        and set(role_counts) <= {"activation-state", "capability-registry", "attestation", "transition-state", "activation-lifecycle-index", "receipt"}
        and sum(count for role, count in role_counts.items() if role not in {"receipt", "activation-lifecycle-index"}) == 1
    ):
        return False
    receipt_paths = [row["path"] for row in targets if row["role"] == "receipt"]
    expected_count = 1
    if receipt_paths != manifest["receipt_target_paths"] or len(receipt_paths) != expected_count:
        return False
    return marker["journal_id"] == manifest["journal_id"] and marker["manifest_id"] == manifest["manifest_id"]


def transition_journal_fixture(
    kind: str, transaction_id: str, journal_id: str, target_specs: list[dict[str, Any]],
    receipt_path: str, receipt_raw: bytes, registry: dict[str, Any], schema_sha: str, registry_sha: str,
    terminal_state: str = "committed",
) -> tuple[dict[str, Any], dict[str, Any]]:
    journal_dir = registry["runtime_paths"]["journal_dir_template"].replace("{transaction_token}", filesystem_token(transaction_id))
    targets: list[dict[str, Any]] = []
    for index, spec in enumerate(target_specs):
        before_raw, after_raw = spec.get("before_raw"), spec.get("after_raw")
        operation = spec["operation"]
        target = mutation_target(spec["role"], operation, index, spec["path"])
        target["before_sha256"] = None if before_raw is None else sha256_bytes(before_raw)
        target["after_sha256"] = None if after_raw is None else sha256_bytes(after_raw)
        target["before_image"] = None if before_raw is None else {
            "root_instance_id": target["root_instance_id"], "path": f"{journal_dir}/images/{index}-before", "sha256": target["before_sha256"],
        }
        target["after_image"] = None if after_raw is None else {
            "root_instance_id": target["root_instance_id"], "path": f"{journal_dir}/images/{index}-after", "sha256": target["after_sha256"],
        }
        targets.append(target)
    receipt_target = mutation_target("receipt", "create", len(targets), receipt_path)
    receipt_target["after_sha256"] = sha256_bytes(receipt_raw)
    receipt_target["after_image"]["sha256"] = receipt_target["after_sha256"]
    receipt_target["after_image"]["path"] = f"{journal_dir}/images/{len(targets)}-after"
    targets.append(receipt_target)
    manifest = {
        "contract": expected_contract_ref(registry, "transaction-journal-manifest/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "journal_id": journal_id, "transaction_id": transaction_id,
        "journal_dir": journal_dir,
        "manifest_path": runtime_path(registry, "journal_manifest_template", transaction_id=transaction_id),
        "prepared_marker_path": runtime_path(registry, "journal_prepared_marker_template", transaction_id=transaction_id),
        "terminal_marker_path": runtime_path(registry, "journal_terminal_marker_template", transaction_id=transaction_id),
        "recovery_receipt_path": runtime_path(registry, "journal_recovery_receipt_template", transaction_id=transaction_id),
        "transaction_kind": kind, "authorization": None, "targets": targets,
        "receipt_target_paths": [receipt_path], "prepared_at": "2026-07-24T03:09:00Z",
    }
    manifest["manifest_id"] = sha256_bytes(canonical_bytes(manifest))
    marker = {
        "contract": expected_contract_ref(registry, "journal-marker/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "journal_id": journal_id, "manifest_id": manifest["manifest_id"],
        "state": terminal_state, "marked_at": "2026-07-24T03:10:01Z",
    }
    marker["marker_id"] = sha256_bytes(canonical_bytes(marker))
    return manifest, marker


WDR_CREATE_SECTIONS = {
    "identity", "bmm-artifact-index", "scope", "acceptance", "project-status", "next-actions",
    "roadmap", "cross-workstream-links", "decisions-evidence", "record-rule",
}


def command_kind(command: dict[str, Any]) -> str:
    schema_id = command.get("contract", {}).get("schema_id", "")
    if schema_id.endswith("#wdr-command-v1"):
        return "wdr"
    if schema_id.endswith("#owned-fact-command-v1"):
        return "owned"
    if schema_id.endswith("#producer-intent-outbox-command-v1"):
        return "intent"
    if schema_id.endswith("#bootstrap-migration-command-v1"):
        return "bootstrap"
    return "action"


def command_producer(command: dict[str, Any]) -> str:
    return command["issuer"]["producer_id"] if command_kind(command) in {"wdr", "owned", "intent", "bootstrap"} else "adp-status-sync"


def command_permissions(command: dict[str, Any], registry: dict[str, Any]) -> tuple[set[str], set[str]]:
    if command_kind(command) == "action":
        return set(command["set"] if command["operation"] == "patch" else command["create"]), set()
    if command_kind(command) == "owned":
        fields = {"owned_facts"}
        if "status_intents" in command:
            fields.add("status_intents")
        return fields, set()
    if command_kind(command) == "intent":
        return {"status_intents"}, set()
    if command["operation"] == "create":
        return {"owned_sections"}, set(WDR_CREATE_SECTIONS)
    fields = set(command["set"])
    permission_fields = set(fields)
    if "status_intents" in command:
        permission_fields.add("status_intents")
    if "consumed_intent_ids" in command:
        permission_fields.add("consumed_intent_ids")
    rows = {row["field"]: row for row in registry["wdr_field_section_map"]}
    if set(rows) != {"status", "phase", "progress", "blockers", "risks", "dependencies", "change_notes", "last_status_sync", "refresh_actions", "roadmap", "meeting_history_append", "owned_sections"}:
        raise ValueError("WDR field-section registry is incomplete")
    sections: set[str] = set()
    for field in fields:
        rule = rows[field]
        sections.update(rule.get("sections", []))
        if rule.get("sections_from_payload"):
            sections.update(row["section"] for row in command["set"][field])
    return permission_fields, sections


def expected_fact_business_targets(command: dict[str, Any], registry: dict[str, Any]) -> list[dict[str, str]]:
    if command_kind(command) == "intent":
        return []
    if command_kind(command) == "action":
        return [
            {"root_instance_id": "123e4567-e89b-42d3-a456-426614174000", "path": registry["runtime_paths"][name]["path"], "operation": "replace"}
            for name in ("action_ledger", "action_ledger_state", "action_flow_index")
        ]
    if command_kind(command) == "owned":
        return [{
            "root_instance_id": "123e4567-e89b-42d3-a456-426614174000",
            "path": command["target_path"],
            "operation": "create" if command["operation"] == "create" else "replace",
        }]
    workstream_id = command["workstream_id"]
    operation = "create" if command["operation"] == "create" else "replace"
    paths = [f"workstreams/{workstream_id}/delivery-record.md", f"workstreams/{workstream_id}/delivery-record.state.json"]
    if command["operation"] == "create" or command["set"].get("refresh_actions"):
        paths.append(f"workstreams/{workstream_id}/action-projection.json")
    return [{"root_instance_id": "123e4567-e89b-42d3-a456-426614174000", "path": path, "operation": operation} for path in paths]


def action_ledger_state_document(
    rows: list[dict[str, Any]], ledger_raw: bytes, ledger_revision: int, applied_commands: list[dict[str, Any]],
    registry: dict[str, Any], schema_sha: str, registry_sha: str,
) -> dict[str, Any]:
    document = {
        "contract": expected_contract_ref(registry, "action-ledger-state/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "ledger_path": registry["runtime_paths"]["action_ledger"]["path"],
        "ledger_fingerprint": sha256_bytes(ledger_raw), "ledger_revision": ledger_revision,
        "actions": [
            {
                "action_id": row["action_id"], "action_revision": row["action_revision"],
                "row_fingerprint": sha256_bytes((render_action_ledger_row(row) + "\n").encode("utf-8")),
            }
            for row in rows
        ],
        "applied_commands": sorted(copy.deepcopy(applied_commands), key=lambda row: row["command_id"].encode("utf-8")),
    }
    document["state_id"] = sha256_bytes(canonical_bytes(document))
    return document


def action_flow_document(
    rows: list[dict[str, Any]], ledger_raw: bytes, ledger_revision: int,
    registry: dict[str, Any], schema_sha: str, registry_sha: str,
) -> dict[str, Any]:
    del ledger_revision, registry, schema_sha, registry_sha

    def relation_ids(value: str) -> list[str]:
        if value == "-":
            return []
        values = re.split(r"\s*[;,]\s*", value)
        if any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", item) is None for item in values):
            raise ValueError("action-flow relation ID is invalid")
        return sorted(set(values), key=lambda item: item.encode("utf-8"))

    actions: list[dict[str, Any]] = []
    ledger_fingerprint = sha256_bytes(ledger_raw)
    for row in rows:
        try:
            baseline_revision = int(row["baseline_revision"])
            if baseline_revision < 1 or not action_row_chronology_valid(row):
                continue
            related_plan_item_ids = relation_ids(row["related_plan_items"])
            related_flow_edge_ids = relation_ids(row["related_flow_edges"])
        except (TypeError, ValueError):
            continue
        actions.append({
            "action_id": row["action_id"], "status": row["status"], "created_at": row["created_at"],
            "updated_at": row["last_updated"], "started_at": None if row["started_at"] == "-" else row["started_at"],
            "done_at": None if row["done_at"] == "-" else row["done_at"],
            "cancelled_at": None if row["cancelled_at"] == "-" else row["cancelled_at"],
            "baseline_revision": baseline_revision, "related_plan_item_ids": related_plan_item_ids,
            "related_flow_edge_ids": related_flow_edge_ids,
            "source": {
                "artifact_id": "ACTION-LEDGER", "artifact_path": "actions/action-ledger.md",
                "source_fingerprint": ledger_fingerprint,
            },
        })
    return {
        "action_flow_schema_version": "1.0.0",
        "actions": sorted(actions, key=lambda row: row["action_id"].encode("utf-8")),
        "compatibility": {
            "strategy": "preserve-unmapped",
            "migration_error_code": "ADP-ACTION-FLOW-MIGRATION-REQUIRED",
        },
    }


def refresh_ledger_fixture(registry: dict[str, Any], schema_sha: str, registry_sha: str) -> tuple[list[dict[str, Any]], bytes, dict[str, Any]]:
    rows = [
        {
            "action_id": "A-FLOW-1", "status": "open", "owner": "FDE-C", "routing_scope_id": "l1-payments",
            "affected_workstreams": ["l1-checkout"], "action": "Ship checkout", "source": "meetings/m1.md@sha256:" + "c" * 64,
            "reason": "cmd-action-prior", "due_trigger": "next sync", "closure_criteria": "release accepted",
            "closure_criteria_verifiable": "true", "created_at": "2026-07-23T01:00:00Z", "started_at": "-",
            "done_at": "-", "cancelled_at": "-", "baseline_revision": "-", "related_plan_items": "-", "related_flow_edges": "-",
            "last_updated": "2026-07-24T01:00:00Z", "owning_workflow": "adp-status-sync", "action_revision": 4,
        },
        {
            "action_id": "A-OTHER-1", "status": "blocked", "owner": "FDE-O", "routing_scope_id": "l1-other",
            "affected_workstreams": [], "action": "Unrelated action", "source": "meetings/m0.md@sha256:" + "a" * 64,
            "reason": "cmd-other-prior", "due_trigger": "later", "closure_criteria": "other accepted",
            "closure_criteria_verifiable": "false", "created_at": "2026-07-22T01:00:00Z", "started_at": "2026-07-23T01:00:00Z",
            "done_at": "-", "cancelled_at": "-", "baseline_revision": "-", "related_plan_items": "-", "related_flow_edges": "-",
            "last_updated": "2026-07-23T01:00:00Z", "owning_workflow": "adp-status-sync", "action_revision": 2,
        },
        {
            "action_id": "A-TERMINAL-1", "status": "done", "owner": "FDE-T", "routing_scope_id": "l1-checkout",
            "affected_workstreams": [], "action": "Closed action", "source": "meetings/m0.md@sha256:" + "b" * 64,
            "reason": "cmd-terminal-prior", "due_trigger": "complete", "closure_criteria": "closed",
            "closure_criteria_verifiable": "true", "created_at": "2026-07-22T02:00:00Z", "started_at": "2026-07-23T01:30:00Z",
            "done_at": "2026-07-23T02:00:00Z", "cancelled_at": "-", "baseline_revision": "-", "related_plan_items": "-", "related_flow_edges": "-",
            "last_updated": "2026-07-23T02:00:00Z", "owning_workflow": "adp-status-sync", "action_revision": 3,
        },
    ]
    rows.sort(key=lambda row: row["action_id"].encode("utf-8"))
    raw = render_action_ledger(rows)
    state = action_ledger_state_document(rows, raw, 11, [], registry, schema_sha, registry_sha)
    return rows, raw, state


STATUS_INTENT_FIELDS = {
    "status", "phase", "progress", "blockers", "risks", "dependencies", "change_notes", "refresh_actions",
}


def command_intent_outbox_mode(command: dict[str, Any], registry: dict[str, Any]) -> str:
    producer = command_producer(command)
    if producer == "adp-meeting-sync" and command_kind(command) == "intent":
        return "emit"
    if producer == "adp-meeting-sync" and command.get("set", {}).get("meeting_history_append"):
        return "emit"
    if producer == "adp-bmm-checkpoint-sync" and command.get("set", {}).get("owned_sections"):
        return "emit"
    authorized_owned_profiles = {
        row["profile_id"]
        for row in registry["owned_fact_target_profiles"]
        if row["producer_id"] == producer
    }
    if (
        producer == "adp-risk-dependency-change-review"
        and command_kind(command) == "owned"
        and command.get("target_profile_id") in authorized_owned_profiles
    ):
        return "emit"
    if (
        producer == "adp-status-sync" and command_kind(command) == "wdr" and command.get("operation") == "patch"
        and (STATUS_INTENT_FIELDS - {"refresh_actions"}).intersection(command.get("set", {}))
    ):
        return "consume"
    return "none"


def status_intents_for_command(command: dict[str, Any], registry: dict[str, Any]) -> list[dict[str, Any]]:
    mode = command_intent_outbox_mode(command, registry)
    if mode == "none":
        return []
    return copy.deepcopy(command.get("status_intents", [])) if mode == "emit" else []


def meeting_plan_intent_carrier_semantics(
    plan: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
) -> bool:
    try:
        if not validate_registered(plan, schema, registry, "meeting-sync-plan/2.0.0", schema_sha, registry_sha):
            return False
        plan_intent_bytes = [canonical_bytes(row) for row in plan["status_intents"]]
        if len(plan_intent_bytes) != len(set(plan_intent_bytes)):
            return False
        carried: list[bytes] = []
        command_ids: list[str] = []
        for command in plan["intent_outbox_commands"]:
            if not validate_registered(
                command, schema, registry, "producer-intent-outbox-command/1.0.0", schema_sha, registry_sha,
            ):
                return False
            if not (
                command["issuer"]["producer_id"] == "adp-meeting-sync"
                and command["operation"] == "append-intents"
                and command["source_instance_id"] == plan["meeting_instance_id"]
                and all(intent["origin_producer"] == "adp-meeting-sync" for intent in command["status_intents"])
            ):
                return False
            expected_evidence = {
                canonical_bytes(row): row
                for intent in command["status_intents"] for row in intent["evidence"]
            }
            if command["evidence"] != sorted(expected_evidence.values(), key=evidence_order_key):
                return False
            command_ids.append(command["command_id"])
            carried.extend(canonical_bytes(row) for row in command["status_intents"])
        if command_ids != sorted(set(command_ids), key=lambda value: value.encode("utf-8")):
            return False
        return sorted(carried) == sorted(plan_intent_bytes)
    except (KeyError, TypeError, ValueError):
        return False


def meeting_plan_intent_fixture(
    registry: dict[str, Any], schema_sha: str, registry_sha: str,
) -> dict[str, Any]:
    evidence = [{
        "source_path": "meetings/m-intent-only.md", "source_fingerprint": "sha256:" + "7" * 64,
        "observed_at": "2026-07-24T02:00:00Z",
    }]
    intent = {
        "contract": expected_contract_ref(registry, "status-mutation-intent/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "intent_id": "meeting-M-INTENT-1-status",
        "origin_producer": "adp-meeting-sync", "workstream_id": "l1-checkout",
        "set": {"progress": "Intent-only update"}, "evidence": copy.deepcopy(evidence),
    }
    capability = next(
        row for row in capability_registry_fixture(registry, schema_sha, registry_sha)["capabilities"]
        if row["producer_id"] == "adp-meeting-sync"
    )
    carrier = {
        "contract": expected_contract_ref(registry, "producer-intent-outbox-command/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "command_id": "cmd-meeting-M-INTENT-1-intents",
        "issuer": {"producer_id": "adp-meeting-sync", "capability_id": capability["capability_id"]},
        "operation": "append-intents", "source_instance_id": "meeting-M-INTENT-1",
        "status_intents": [copy.deepcopy(intent)], "evidence": copy.deepcopy(evidence),
    }
    return {
        "contract": expected_contract_ref(registry, "meeting-sync-plan/2.0.0", schema_sha, registry_sha),
        "schema_version": "2.0.0", "meeting_instance_id": "meeting-M-INTENT-1",
        "action_commands": [], "status_intents": [intent], "intent_outbox_commands": [carrier],
        "history_patches": [], "evidence_only_items": [],
    }


def pending_outbox_entry(
    intent: dict[str, Any], source_command_id: str, source_command_fingerprint: str, sequence: int,
) -> dict[str, Any]:
    return {
        "sequence": sequence, "intent_id": sha256_bytes(canonical_bytes(intent)), "intent": copy.deepcopy(intent),
        "source_command_id": source_command_id, "source_command_fingerprint": source_command_fingerprint,
        "producer_id": intent["origin_producer"], "workstream_id": intent["workstream_id"],
        "field_set": sorted(intent["set"], key=lambda value: value.encode("utf-8")),
        "status": "pending", "attempts": 0, "last_error": None,
        "created_at": min(row["observed_at"] for row in intent["evidence"]), "consumed_receipt_id": None,
    }


def fact_attribution_fixture(
    schema_sha: str, registry_sha: str, registry: dict[str, Any], fixture_kind: str = "action", create_command: dict[str, Any] | None = None,
    workstream_id: str = "l1-checkout", before_fact_generation: int = 7, prior_transaction_id: str = "tx-prior-1",
    orphan_action_id: str | None = None,
) -> dict[str, Any]:
    evidence = [{"source_path": "meetings/m1.md", "source_fingerprint": "sha256:" + "c" * 64, "observed_at": "2026-07-24T02:00:00Z"}]
    refresh_rows: list[dict[str, Any]] | None = None
    refresh_ledger_raw: bytes | None = None
    refresh_ledger_state: dict[str, Any] | None = None
    if fixture_kind in {"action", "action-create", "action-terminal"}:
        operation = "create" if fixture_kind == "action-create" else "patch"
        command = {
            "contract": {"schema_id": "urn:adp:panel-sync-contracts:2026-07-24#action-command-v2", "schema_sha256": schema_sha, "registry_sha256": registry_sha},
            "schema_version": "2.0.0", "command_id": f"cmd-{fixture_kind}-1", "operation": operation,
            "action_id": "A-TERMINAL-1" if fixture_kind == "action-terminal" else "A-FLOW-1", "evidence": evidence,
        }
        if operation == "patch":
            command.update({
                "expected_revision": 3 if fixture_kind == "action-terminal" else 4,
                "set": {"owner": "FDE-T" if fixture_kind == "action-terminal" else "FDE-C"},
            })
        else:
            command["create"] = {"owner": "FDE-C", "status": "open", "action": "Ship checkout", "due_trigger": "next sync", "closure_criteria": "release accepted", "routing_scope_id": "l1-checkout", "affected_workstreams": ["l1-checkout"]}
    elif fixture_kind == "intent-only":
        command = {
            "contract": expected_contract_ref(registry, "producer-intent-outbox-command/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "command_id": "cmd-meeting-intents-1",
            "issuer": {"producer_id": "adp-meeting-sync", "capability_id": "sha256:" + "0" * 64},
            "operation": "append-intents", "source_instance_id": "meeting-M-INTENTS-1",
            "status_intents": [], "evidence": evidence,
        }
    elif fixture_kind == "wdr-create":
        if create_command is None:
            raise ValueError("wdr-create fixture requires a rendered create command")
        command = copy.deepcopy(create_command)
    elif fixture_kind in {"owned-risk-flow", "owned-decision"}:
        if fixture_kind == "owned-risk-flow":
            target_profile_id = "risk-flow-index-v1"
            target_path = "views/risk-flow.json"
            before_owned = canonical_bytes({
                "risk_flow_schema_version": "1.0.0", "risks": [],
                "compatibility": {"strategy": "preserve-unmapped", "migration_error_code": "ADP-RISK-FLOW-MIGRATION-REQUIRED"},
            })
            after_owned = canonical_bytes({
                "risk_flow_schema_version": "1.0.0", "risks": [{
                    "risk_id": "RISK-1", "lifecycle": "open", "relation_state": "at-risk",
                    "observed_at": "2026-07-24T02:00:00Z", "terminal_at": None,
                    "baseline_revision": 1, "related_plan_item_ids": [], "related_flow_edge_ids": [],
                    "rule_id": "RULE-1", "sources": [{
                        "artifact_id": "WDR-1", "artifact_path": "workstreams/l1-checkout/delivery-record.md",
                        "field": "Risks", "source_fingerprint": "sha256:" + "c" * 64,
                    }],
                }],
                "compatibility": {"strategy": "preserve-unmapped", "migration_error_code": "ADP-RISK-FLOW-MIGRATION-REQUIRED"},
            })
        else:
            target_profile_id = "workstream-decision-v1"
            target_path = f"workstreams/{workstream_id}/decisions.md"
            before_owned = b"# Decisions\n\n- ADR-1: pending\n"
            after_owned = b"# Decisions\n\n- ADR-1: accepted\n"
        command = {
            "contract": expected_contract_ref(registry, "owned-fact-command/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "command_id": f"cmd-{fixture_kind}-1", "operation": "patch",
            "issuer": {"producer_id": "adp-risk-dependency-change-review", "capability_id": "sha256:" + "0" * 64},
            "target_profile_id": target_profile_id, "target_path": target_path,
            "expected_before_sha256": sha256_bytes(before_owned),
            "after_bytes": encoded_bytes(after_owned), "after_sha256": sha256_bytes(after_owned), "evidence": evidence,
        }
    else:
        wdr_set: dict[str, Any]
        producer = "adp-status-sync"
        if fixture_kind == "wdr-status":
            wdr_set = {"progress": "Implementation active", "blockers": {"mode": "replace", "values": ["Access"]}, "risks": {"mode": "replace", "values": ["Schedule"]}}
        elif fixture_kind == "wdr-meeting-history":
            producer = "adp-meeting-sync"
            wdr_set = {"meeting_history_append": [{"entry_id": "meeting-entry-1", "command_id": "cmd-wdr-meeting-history-1", "observed_at": "2026-07-24T02:00:00Z", "source_path": "meetings/m1.md", "source_fingerprint": "sha256:" + "c" * 64, "classification": "wdr_update", "summary": "Progress reviewed", "owner": "FDE-C", "due_trigger": "next sync", "status": "noted"}]}
        elif fixture_kind == "wdr-owned-section":
            producer = "adp-bmm-checkpoint-sync"
            wdr_set = {"owned_sections": [{"section": "checkpoint-sync-log", "mode": "append", "lines": ["Checkpoint reviewed"]}]}
        elif fixture_kind == "wdr-roadmap":
            wdr_set = {
                "roadmap": {
                    "mode": "replace",
                    "lines": [
                        "| Milestone ID | Milestone | Status |",
                        "| --- | --- | --- |",
                        "| MS-CHECKOUT | Checkout complete | in-progress |",
                    ],
                }
            }
        elif fixture_kind == "wdr-refresh-actions":
            wdr_set = {"refresh_actions": True}
        elif fixture_kind == "wdr-identity":
            wdr_set = {"status": "blocked", "phase": "validation"}
        elif fixture_kind == "wdr-risk-direct":
            producer = "adp-risk-dependency-change-review"
            wdr_set = {"risks": {"mode": "replace", "values": ["Schedule"]}}
        elif fixture_kind == "wdr-risk-reauthorized":
            wdr_set = {"risks": {"mode": "replace", "values": ["Schedule"]}}
        else:
            raise ValueError(f"unknown fact fixture kind: {fixture_kind}")
        command = {
            "contract": {"schema_id": "urn:adp:panel-sync-contracts:2026-07-24#wdr-command-v1", "schema_sha256": schema_sha, "registry_sha256": registry_sha},
            "schema_version": "1.0.0", "command_id": f"cmd-{fixture_kind}-1", "issuer": {"producer_id": producer, "capability_id": "sha256:" + "0" * 64},
            "operation": "patch", "workstream_id": workstream_id, "expected_wdr_revision": 4, "expected_file_generation": 7, "set": wdr_set, "evidence": evidence,
        }
        if fixture_kind == "wdr-refresh-actions":
            refresh_rows, refresh_ledger_raw, refresh_ledger_state = refresh_ledger_fixture(registry, schema_sha, registry_sha)
            command["action_snapshot"] = action_snapshot(
                refresh_rows, command["workstream_id"], refresh_ledger_state["ledger_fingerprint"], refresh_ledger_state["ledger_revision"]
            )
    explicit_intent_sets = {
        "intent-only": {"progress": "Intent-only meeting update"},
        "wdr-meeting-history": {"blockers": {"mode": "replace", "values": ["Access"]}},
        "wdr-owned-section": {"progress": "Checkpoint reviewed", "risks": {"mode": "replace", "values": ["Schedule"]}},
        "owned-risk-flow": {"risks": {"mode": "replace", "values": ["RISK-1: at-risk"]}},
        "owned-decision": {"dependencies": {"mode": "replace", "values": ["ADR-1: accepted"]}},
    }
    consumed_outbox_intents: list[dict[str, Any]] = []
    if fixture_kind in explicit_intent_sets:
        command["status_intents"] = [{
            "contract": expected_contract_ref(registry, "status-mutation-intent/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "intent_id": f"intent-{command['command_id']}",
            "origin_producer": command_producer(command), "workstream_id": command.get("workstream_id", workstream_id),
            "set": copy.deepcopy(explicit_intent_sets[fixture_kind]), "evidence": copy.deepcopy(command["evidence"]),
        }]
    elif fixture_kind == "wdr-status":
        consumed_outbox_intents = copy.deepcopy(status_intent_fixture(registry, schema_sha, registry_sha)["accepted_intents"])
    elif fixture_kind in {"wdr-identity", "wdr-risk-reauthorized"}:
        consumed_outbox_intents = [{
            "contract": expected_contract_ref(registry, "status-mutation-intent/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "intent_id": f"intent-{fixture_kind}-1",
            "origin_producer": (
                "adp-bmm-checkpoint-sync" if fixture_kind == "wdr-identity"
                else "adp-risk-dependency-change-review"
            ),
            "workstream_id": command["workstream_id"], "set": copy.deepcopy(command["set"]),
            "evidence": copy.deepcopy(command["evidence"]),
        }]
    if consumed_outbox_intents:
        command["consumed_intent_ids"] = sorted(
            [sha256_bytes(canonical_bytes(intent)) for intent in consumed_outbox_intents], key=lambda value: value.encode("utf-8")
        )
        intent_evidence = {
            canonical_bytes(row): row
            for intent in consumed_outbox_intents for row in intent["evidence"]
        }
        command["evidence"] = sorted(intent_evidence.values(), key=evidence_order_key)
    registry_doc = capability_registry_fixture(registry, schema_sha, registry_sha)
    capabilities = registry_doc["capabilities"]
    producer = command_producer(command)
    cap = next(row for row in capabilities if row["producer_id"] == producer)
    if "issuer" in command:
        command["issuer"]["capability_id"] = cap["capability_id"]
    authorization = {
        "producer_id": cap["producer_id"], "capability_id": cap["capability_id"], "capability_epoch": 3, "principal_id": cap["principal_id"],
        "capability_registry_id": registry_doc["capability_registry_id"], "authorization_record_digest": cap["authorization_record_digest"],
        "authorized_command_fingerprint": sha256_bytes(canonical_bytes(command)),
    }
    outbox_mode = command_intent_outbox_mode(command, registry)
    outbox_intents = status_intents_for_command(command, registry)
    if outbox_mode == "consume":
        outbox_intents = copy.deepcopy(consumed_outbox_intents)
    expected_targets = expected_fact_business_targets(command, registry)
    journal, marker = journal_fixture(
        "fact", schema_sha, registry_sha, registry, expected_targets,
        include_intent_outbox=outbox_mode != "none",
    )
    journal["authorization"] = copy.deepcopy(authorization)
    before_state = {
        "contract": {"schema_id": "urn:adp:panel-sync-contracts:2026-07-24#fact-generation-state-v1", "schema_sha256": schema_sha, "registry_sha256": registry_sha},
        "schema_version": "1.0.0", "fact_generation": before_fact_generation, "last_transaction_id": prior_transaction_id,
    }
    before_state["state_id"] = sha256_bytes(canonical_bytes(before_state))
    after_state = {
        "contract": copy.deepcopy(before_state["contract"]), "schema_version": "1.0.0", "fact_generation": before_fact_generation + 1, "last_transaction_id": journal["transaction_id"],
    }
    after_state["state_id"] = sha256_bytes(canonical_bytes(after_state))
    generation_target = next(row for row in journal["targets"] if row["role"] == "fact-generation")
    generation_target["path"] = "state/fact-generation.json"
    generation_target["before_sha256"] = sha256_bytes(canonical_bytes(before_state))
    generation_target["after_sha256"] = sha256_bytes(canonical_bytes(after_state))
    generation_target["before_image"]["sha256"] = generation_target["before_sha256"]
    generation_target["after_image"]["sha256"] = generation_target["after_sha256"]

    def finalized(document: dict[str, Any], identity_field: str) -> dict[str, Any]:
        document[identity_field] = sha256_bytes(canonical_bytes(document))
        return document

    business_contents: list[tuple[bytes | None, bytes | None]] = []
    if command_kind(command) == "action":
        before_rows: list[dict[str, Any]] = []
        if command["operation"] == "patch":
            before_rows = [{
                "action_id": command["action_id"], "status": "done" if fixture_kind == "action-terminal" else "open",
                "owner": "FDE-T" if fixture_kind == "action-terminal" else "FDE-A", "routing_scope_id": "l1-checkout",
                "affected_workstreams": ["l1-checkout"], "action": "Ship checkout",
                "source": "meetings/prior.md@sha256:" + "a" * 64, "reason": "cmd-action-prior", "due_trigger": "next sync",
                "closure_criteria": "release accepted", "closure_criteria_verifiable": "true",
                "created_at": "2026-07-23T01:00:00Z", "started_at": "2026-07-23T02:00:00Z" if fixture_kind == "action-terminal" else "-",
                "done_at": "2026-07-24T01:00:00Z" if fixture_kind == "action-terminal" else "-", "cancelled_at": "-",
                "baseline_revision": "-", "related_plan_items": "-", "related_flow_edges": "-",
                "last_updated": "2026-07-24T01:00:00Z", "owning_workflow": "adp-status-sync",
                "action_revision": 3 if fixture_kind == "action-terminal" else 4,
            }]
        before_ledger_bytes = render_action_ledger(before_rows)
        after_rows = apply_action_command(before_rows, command)
        after_ledger_bytes = render_action_ledger(after_rows)
        before_revision = 4
        applied = [{"command_id": command["command_id"], "command_fingerprint": authorization["authorized_command_fingerprint"], "action_id": command["action_id"]}]
        before_ledger_state = action_ledger_state_document(before_rows, before_ledger_bytes, before_revision, [], registry, schema_sha, registry_sha)
        after_ledger_state = action_ledger_state_document(after_rows, after_ledger_bytes, before_revision + 1, applied, registry, schema_sha, registry_sha)
        before_flow = action_flow_document(before_rows, before_ledger_bytes, before_revision, registry, schema_sha, registry_sha)
        after_flow = action_flow_document(after_rows, after_ledger_bytes, before_revision + 1, registry, schema_sha, registry_sha)
        business_contents = [
            (before_ledger_bytes, after_ledger_bytes),
            (canonical_bytes(before_ledger_state), canonical_bytes(after_ledger_state)),
            (canonical_bytes(before_flow), canonical_bytes(after_flow)),
        ]
    elif command_kind(command) == "owned":
        business_contents = [(before_owned, after_owned)]
    elif command_kind(command) == "intent":
        business_contents = []
    else:
        workstream_id = command["workstream_id"]
        record_path = f"workstreams/{workstream_id}/delivery-record.md"
        state_contract = expected_contract_ref(registry, "wdr-file-state/1.0.0", schema_sha, registry_sha)
        sidecar_contract = expected_contract_ref(registry, "wdr-action-projection/1.0.0", schema_sha, registry_sha)
        if command["operation"] == "create":
            before_wdr = None
            after_wdr = command["rendered_record"].encode()
            before_wdr_state = None
            after_wdr_state = {"contract": state_contract, "schema_version": "1.0.0", "workstream_id": workstream_id, "record_path": record_path, "record_fingerprint": sha256_bytes(after_wdr), "wdr_revision": 1, "file_generation": 1, "lifecycle": "active"}
            before_sidecar = None
            after_sidecar = {"contract": sidecar_contract, "schema_version": "1.0.0", "workstream_id": workstream_id, "ledger_fingerprint": "sha256:" + "0" * 64, "ledger_revision": 0, "wdr_revision": 1, "file_generation": 1, "renderer_id": "urn:adp:wdr-action-renderer:1.0.0", "renderer_sha256": registry["protocol"]["sha256"], "actions": []}
        else:
            orphan_record = None if orphan_action_id is None else {
                "action_id": orphan_action_id, "owner": "FDE-O", "action": "Remove orphan projection", "due_trigger": "next sync",
                "status": "open", "action_revision": 1, "routing_scope_id": workstream_id, "affected_workstreams": [workstream_id],
            }
            if orphan_record is not None:
                orphan_record["rendered_summary"] = rendered_action_summary(orphan_record)
            before_projection_actions = []
            if orphan_record is not None:
                before_projection_actions = copy.deepcopy(command["action_snapshot"]["actions"]) + [orphan_record]
                before_projection_actions.sort(key=lambda row: row["action_id"].encode("utf-8"))
            before_wdr_text = fixture_wdr(workstream_id)
            if before_projection_actions:
                before_wdr_text = apply_wdr_patch(
                    before_wdr_text, {"set": {"refresh_actions": True}},
                    [row["rendered_summary"] for row in before_projection_actions],
                )
            before_wdr = before_wdr_text.encode()
            before_sidecar_value = {
                "contract": sidecar_contract, "schema_version": "1.0.0", "workstream_id": workstream_id,
                "ledger_fingerprint": refresh_ledger_state["ledger_fingerprint"] if command["set"].get("refresh_actions") else "sha256:" + "d" * 64,
                "ledger_revision": refresh_ledger_state["ledger_revision"] if command["set"].get("refresh_actions") else 4,
                "wdr_revision": 4, "file_generation": 7, "renderer_id": "urn:adp:wdr-action-renderer:1.0.0",
                "renderer_sha256": registry["protocol"]["sha256"], "actions": before_projection_actions,
            }
            after_sidecar = copy.deepcopy(before_sidecar_value)
            summaries = [row["rendered_summary"] for row in after_sidecar["actions"]]
            if command["set"].get("refresh_actions"):
                if refresh_ledger_state is None:
                    raise ValueError("refresh fixture has no ledger snapshot")
                summaries = [row["rendered_summary"] for row in command["action_snapshot"]["actions"]]
            after_wdr = apply_wdr_patch(before_wdr.decode(), command, summaries).encode()
            revision_delta, generation_delta = wdr_counter_delta(before_wdr.decode(), after_wdr.decode(), workstream_id)
            if command["set"].get("refresh_actions"):
                after_sidecar.update({
                    "ledger_fingerprint": refresh_ledger_state["ledger_fingerprint"], "ledger_revision": refresh_ledger_state["ledger_revision"],
                    "wdr_revision": 4 + revision_delta, "file_generation": 7 + generation_delta,
                    "actions": copy.deepcopy(command["action_snapshot"]["actions"]),
                })
            before_wdr_state = {"contract": state_contract, "schema_version": "1.0.0", "workstream_id": workstream_id, "record_path": record_path, "record_fingerprint": sha256_bytes(before_wdr), "wdr_revision": 4, "file_generation": 7, "lifecycle": "active"}
            after_wdr_state = {"contract": state_contract, "schema_version": "1.0.0", "workstream_id": workstream_id, "record_path": record_path, "record_fingerprint": sha256_bytes(after_wdr), "wdr_revision": 4 + revision_delta, "file_generation": 7 + generation_delta, "lifecycle": "active"}
            before_sidecar = canonical_bytes(before_sidecar_value) if command["set"].get("refresh_actions") else None
        business_contents = [(before_wdr, after_wdr), (None if before_wdr_state is None else canonical_bytes(before_wdr_state), canonical_bytes(after_wdr_state))]
        if command["operation"] == "create" or command["set"].get("refresh_actions"):
            business_contents.append((before_sidecar, canonical_bytes(after_sidecar)))

    business_targets = [row for row in journal["targets"] if row["role"] == "business"]
    artifacts = []
    if len(business_targets) != len(expected_targets) or len(expected_targets) != len(business_contents):
        raise ValueError("fact fixture business targets and content must have equal cardinality")
    for target, expected, (before_bytes, after_bytes) in zip(business_targets, expected_targets, business_contents):
        target.update({key: expected[key] for key in ("root_instance_id", "path", "operation")})
        target["before_sha256"] = None if before_bytes is None else sha256_bytes(before_bytes)
        target["after_sha256"] = None if after_bytes is None else sha256_bytes(after_bytes)
        target["before_image"] = None if before_bytes is None else {"root_instance_id": target["root_instance_id"], "path": f"{journal['journal_dir']}/images/{target['apply_order']}-before", "sha256": target["before_sha256"]}
        target["after_image"] = None if after_bytes is None else {"root_instance_id": target["root_instance_id"], "path": f"{journal['journal_dir']}/images/{target['apply_order']}-after", "sha256": target["after_sha256"]}
        artifacts.append({"root_instance_id": target["root_instance_id"], "path": target["path"], "operation": target["operation"], "before_bytes": encoded_bytes(before_bytes), "after_bytes": encoded_bytes(after_bytes)})
    proof = {
        "contract": expected_contract_ref(registry, "fact-mutation-proof/1.0.0", schema_sha, registry_sha), "schema_version": "1.0.0",
        "transaction_id": journal["transaction_id"], "host_principal_id": cap["principal_id"], "authorized_command_fingerprint": authorization["authorized_command_fingerprint"],
        "business_artifacts": artifacts, "read_artifacts": [],
    }
    if command_kind(command) == "wdr" and command["operation"] == "patch" and command["set"].get("refresh_actions"):
        if refresh_ledger_raw is None or refresh_ledger_state is None:
            raise ValueError("refresh command has no read snapshot")
        proof["read_artifacts"] = [
            {
                "root_instance_id": "123e4567-e89b-42d3-a456-426614174000",
                "path": registry["runtime_paths"]["action_ledger"]["path"], "sha256": sha256_bytes(refresh_ledger_raw),
                "bytes": encoded_bytes(refresh_ledger_raw),
            },
            {
                "root_instance_id": "123e4567-e89b-42d3-a456-426614174000",
                "path": registry["runtime_paths"]["action_ledger_state"]["path"], "sha256": sha256_bytes(canonical_bytes(refresh_ledger_state)),
                "bytes": encoded_bytes(canonical_bytes(refresh_ledger_state)),
            },
        ]
    proof["proof_id"] = sha256_bytes(canonical_bytes(proof))
    business = [copy.deepcopy(row) for row in journal["targets"] if row["role"] == "business"]
    generation = copy.deepcopy(generation_target)
    receipt = {
        "contract": {"schema_id": "urn:adp:panel-sync-contracts:2026-07-24#fact-mutation-receipt-v1", "schema_sha256": schema_sha, "registry_sha256": registry_sha},
        "schema_version": "1.0.0", "transaction_id": journal["transaction_id"], "journal_id": journal["journal_id"],
        "authorization": copy.deepcopy(authorization),
        "initiator": {key: authorization[key] for key in ("producer_id", "capability_id", "capability_epoch", "principal_id")},
        "before_fact_generation": before_fact_generation, "after_fact_generation": before_fact_generation + 1, "business_targets": business, "generation_state_target": generation,
        "action_deltas": [expected_action_delta(command)] if command_kind(command) == "action" else [],
        "status": "committed",
    }
    receipt["receipt_id"] = sha256_bytes(canonical_bytes(receipt))
    receipt_target = next(row for row in journal["targets"] if row["role"] == "receipt")
    receipt_target["after_sha256"] = sha256_bytes(canonical_bytes(receipt))
    receipt_target["after_image"]["sha256"] = receipt_target["after_sha256"]
    before_command_index = {
        "contract": expected_contract_ref(registry, "fact-command-receipt-index/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "next_sequence": 1, "entries": [],
    }
    before_command_index["index_id"] = sha256_bytes(canonical_bytes(before_command_index))
    command_index = {
        "contract": copy.deepcopy(before_command_index["contract"]), "schema_version": "1.0.0", "next_sequence": 2,
        "entries": [{
            "sequence": 1, "command_id": command["command_id"],
            "command_fingerprint": authorization["authorized_command_fingerprint"],
            "transaction_id": journal["transaction_id"], "receipt_id": receipt["receipt_id"],
            "receipt_path": receipt_target["path"], "receipt_sha256": sha256_bytes(canonical_bytes(receipt)),
        }],
    }
    command_index["index_id"] = sha256_bytes(canonical_bytes(command_index))
    index_target = next(row for row in journal["targets"] if row["role"] == "fact-command-index")
    index_target["before_sha256"] = sha256_bytes(canonical_bytes(before_command_index))
    index_target["after_sha256"] = sha256_bytes(canonical_bytes(command_index))
    index_target["before_image"]["sha256"] = index_target["before_sha256"]
    index_target["after_image"]["sha256"] = index_target["after_sha256"]
    before_outbox = None
    after_outbox = None
    if outbox_mode != "none":
        if not outbox_intents:
            raise ValueError("intent outbox mode requires exact typed intents")
        if outbox_mode == "emit":
            before_entries: list[dict[str, Any]] = []
            source_command_id = command["command_id"]
            source_command_fingerprint = authorization["authorized_command_fingerprint"]
        else:
            source_command_id = f"source-{command['command_id']}"
            before_entries = [
                pending_outbox_entry(intent, f"{source_command_id}-{sequence}", sha256_bytes(canonical_bytes(intent)), sequence)
                for sequence, intent in enumerate(outbox_intents, start=1)
            ]
        before_outbox = {
            "contract": expected_contract_ref(registry, "mutation-intent-outbox/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "outbox_generation": 1, "entries": before_entries,
        }
        before_outbox["outbox_id"] = sha256_bytes(canonical_bytes(before_outbox))
        after_entries = copy.deepcopy(before_entries)
        if outbox_mode == "emit":
            after_entries.extend(
                pending_outbox_entry(intent, source_command_id, source_command_fingerprint, len(before_entries) + sequence)
                for sequence, intent in enumerate(outbox_intents, start=1)
            )
        else:
            consumed_ids = set(command["consumed_intent_ids"])
            for entry in after_entries:
                if entry["intent_id"] in consumed_ids:
                    entry.update({"status": "consumed", "attempts": entry["attempts"] + 1, "consumed_receipt_id": receipt["receipt_id"]})
        after_outbox = {
            "contract": copy.deepcopy(before_outbox["contract"]), "schema_version": "1.0.0",
            "outbox_generation": 2, "entries": after_entries,
        }
        after_outbox["outbox_id"] = sha256_bytes(canonical_bytes(after_outbox))
        outbox_target = next(row for row in journal["targets"] if row["role"] == "intent-outbox")
        outbox_target["before_sha256"] = sha256_bytes(canonical_bytes(before_outbox))
        outbox_target["after_sha256"] = sha256_bytes(canonical_bytes(after_outbox))
        outbox_target["before_image"]["sha256"] = outbox_target["before_sha256"]
        outbox_target["after_image"]["sha256"] = outbox_target["after_sha256"]
    journal["manifest_id"] = sha256_bytes(canonical_bytes({key: value for key, value in journal.items() if key != "manifest_id"}))
    marker["manifest_id"] = journal["manifest_id"]
    marker["marker_id"] = sha256_bytes(canonical_bytes({key: value for key, value in marker.items() if key != "marker_id"}))
    return {
        "capability_registry": registry_doc, "command": command, "journal": journal, "marker": marker,
        "before_state": before_state, "after_state": after_state, "receipt": receipt, "proof": proof,
        "before_command_index": before_command_index, "command_index": command_index,
        "before_outbox": before_outbox, "after_outbox": after_outbox,
    }


def rebind_fact_graph(graph: dict[str, Any]) -> None:
    registry_doc = graph["capability_registry"]
    for row in registry_doc["capabilities"]:
        digest = capability_record_digest(row)
        row["capability_id"] = digest
        row["authorization_record_digest"] = digest
    registry_doc["capabilities"].sort(key=lambda row: row["producer_id"].encode("utf-8"))
    registry_doc["capability_registry_id"] = sha256_bytes(canonical_bytes({key: value for key, value in registry_doc.items() if key != "capability_registry_id"}))
    producer = command_producer(graph["command"])
    cap = next(row for row in registry_doc["capabilities"] if row["producer_id"] == producer)
    if "issuer" in graph["command"]:
        graph["command"]["issuer"]["capability_id"] = cap["capability_id"]
    auth = {
        "producer_id": cap["producer_id"], "capability_id": cap["capability_id"], "capability_epoch": registry_doc["capability_epoch"], "principal_id": cap["principal_id"],
        "capability_registry_id": registry_doc["capability_registry_id"], "authorization_record_digest": cap["authorization_record_digest"],
        "authorized_command_fingerprint": sha256_bytes(canonical_bytes(graph["command"])),
    }
    graph["journal"]["authorization"] = copy.deepcopy(auth)
    graph["receipt"]["authorization"] = copy.deepcopy(auth)
    graph["receipt"]["initiator"] = {key: auth[key] for key in ("producer_id", "capability_id", "capability_epoch", "principal_id")}
    graph["proof"]["host_principal_id"] = cap["principal_id"]
    graph["proof"]["authorized_command_fingerprint"] = auth["authorized_command_fingerprint"]
    graph["proof"]["proof_id"] = sha256_bytes(canonical_bytes({key: value for key, value in graph["proof"].items() if key != "proof_id"}))
    graph["receipt"]["receipt_id"] = sha256_bytes(canonical_bytes({key: value for key, value in graph["receipt"].items() if key != "receipt_id"}))
    receipt_target = next(row for row in graph["journal"]["targets"] if row["role"] == "receipt")
    receipt_target["after_sha256"] = sha256_bytes(canonical_bytes(graph["receipt"]))
    receipt_target["after_image"]["sha256"] = receipt_target["after_sha256"]
    if "before_command_index" in graph and "command_index" in graph:
        before_index = graph["before_command_index"]
        previous_entries = copy.deepcopy(before_index["entries"])
        sequence = before_index["next_sequence"]
        graph["command_index"] = {
            "contract": copy.deepcopy(before_index["contract"]), "schema_version": "1.0.0", "next_sequence": sequence + 1,
            "entries": previous_entries + [{
                "sequence": sequence, "command_id": graph["command"]["command_id"],
                "command_fingerprint": auth["authorized_command_fingerprint"],
                "transaction_id": graph["journal"]["transaction_id"], "receipt_id": graph["receipt"]["receipt_id"],
                "receipt_path": receipt_target["path"], "receipt_sha256": sha256_bytes(canonical_bytes(graph["receipt"])),
            }],
        }
        graph["command_index"]["index_id"] = sha256_bytes(canonical_bytes(graph["command_index"]))
        index_target = next(row for row in graph["journal"]["targets"] if row["role"] == "fact-command-index")
        index_target["before_sha256"] = sha256_bytes(canonical_bytes(before_index))
        index_target["after_sha256"] = sha256_bytes(canonical_bytes(graph["command_index"]))
        index_target["before_image"]["sha256"] = index_target["before_sha256"]
        index_target["after_image"]["sha256"] = index_target["after_sha256"]
    graph["journal"]["manifest_id"] = sha256_bytes(canonical_bytes({key: value for key, value in graph["journal"].items() if key != "manifest_id"}))
    graph["marker"]["manifest_id"] = graph["journal"]["manifest_id"]
    graph["marker"]["marker_id"] = sha256_bytes(canonical_bytes({key: value for key, value in graph["marker"].items() if key != "marker_id"}))


def legacy_ledger_fixture(declared_format: str) -> bytes | None:
    if declared_format == "absent":
        return None
    columns = ACTION_LEDGER_LEGACY_12_COLUMNS if declared_format == "legacy12" else ACTION_LEDGER_LEGACY_20_COLUMNS
    values = {
        "Action ID": "A-MIG-1", "Status": "open", "Owner": "FDE-M", "Workstream": "l1-checkout",
        "Affected Workstreams": "l1-payments", "Action": "Preserve migrated action", "Source": "meetings/legacy.md",
        "Reason": "legacy import", "Due / Trigger": "next gate", "Closure Criteria": "accepted",
        "Closure Criteria Verifiable": "true", "Created At": "2026-07-22T01:00:00Z", "Started At": "-",
        "Done At": "-", "Cancelled At": "-", "Baseline Revision": "3", "Related Plan Items": "PLAN-1",
        "Related Flow Edges": "EDGE-1", "Last Updated": "2026-07-23T01:00:00Z", "Owning Workflow": "adp-status-sync",
    }
    header = "| " + " | ".join(columns) + " |\n"
    separator = "| " + " | ".join("---" for _ in columns) + " |\n"
    row = "| " + " | ".join(_ledger_cell(values[column]) for column in columns) + " |\n"
    return (ACTION_LEDGER_PREAMBLE + header + separator + row).encode("utf-8")


def legacy_wdr_fixture(workstream_id: str) -> bytes:
    value = fixture_wdr(workstream_id).replace("- Last status sync: 2026-07-24T01:00:00Z\n", "")
    value += "\n## Checkpoint Sync Log\n\n- legacy checkpoint preserved\n"
    return value.encode("utf-8")


def bootstrap_migration_fixture(
    scenario: dict[str, Any], registry: dict[str, Any], schema_sha: str, registry_sha: str,
) -> dict[str, Any]:
    command = {
        "contract": expected_contract_ref(registry, "bootstrap-migration-command/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "command_id": "bootstrap-migration-1", "operation": "bootstrap",
        "issuer": {"producer_id": "adp-status-sync", "capability_id": "sha256:" + "0" * 64},
        "action_ledger": {
            "format": scenario["ledger_format"], "expected_fingerprint": None,
            "state_expected": "absent", "action_flow_preimage": scenario.get("action_flow_preimage", "absent"),
        },
        "workstreams": [], "observed_at": "2026-07-24T02:00:00Z",
    }
    before_ledger = legacy_ledger_fixture(scenario["ledger_format"])
    command["action_ledger"]["expected_fingerprint"] = None if before_ledger is None else sha256_bytes(before_ledger)
    rows = parse_action_ledger_ingress(before_ledger, scenario["ledger_format"])
    after_ledger = render_action_ledger(rows)
    after_ledger_state = action_ledger_state_document(rows, after_ledger, 0, [], registry, schema_sha, registry_sha)
    before_flow = None
    if command["action_ledger"]["action_flow_preimage"] == "brownfield-v1":
        before_flow = canonical_bytes(action_flow_document(rows, before_ledger, 0, registry, schema_sha, registry_sha))
    after_flow = canonical_bytes(action_flow_document(rows, after_ledger, 0, registry, schema_sha, registry_sha))
    targets: list[dict[str, str]] = [
        {"path": registry["runtime_paths"]["action_ledger"]["path"], "operation": "create" if before_ledger is None else "replace"},
        {"path": registry["runtime_paths"]["action_ledger_state"]["path"], "operation": "create"},
        {"path": registry["runtime_paths"]["action_flow_index"]["path"], "operation": "create" if before_flow is None else "replace"},
    ]
    contents: list[tuple[bytes | None, bytes | None]] = [
        (before_ledger, after_ledger), (None, canonical_bytes(after_ledger_state)), (before_flow, after_flow),
    ]
    for workstream_id in scenario.get("workstreams", ["l1-checkout"]):
        before_wdr = legacy_wdr_fixture(workstream_id)
        command["workstreams"].append({
            "workstream_id": workstream_id, "record_format": "legacy", "expected_record_fingerprint": sha256_bytes(before_wdr),
            "state_expected": "absent", "sidecar_expected": "absent",
        })
        after_wdr = migrate_wdr(before_wdr.decode("utf-8"), command["observed_at"]).encode("utf-8")
        record_path = f"workstreams/{workstream_id}/delivery-record.md"
        state = {
            "contract": expected_contract_ref(registry, "wdr-file-state/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "workstream_id": workstream_id, "record_path": record_path,
            "record_fingerprint": sha256_bytes(after_wdr), "wdr_revision": 0, "file_generation": 1, "lifecycle": "active",
        }
        snapshot = action_snapshot(rows, workstream_id, after_ledger_state["ledger_fingerprint"], 0)
        sidecar = {
            "contract": expected_contract_ref(registry, "wdr-action-projection/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "workstream_id": workstream_id,
            "ledger_fingerprint": after_ledger_state["ledger_fingerprint"], "ledger_revision": 0,
            "wdr_revision": 0, "file_generation": 1, "renderer_id": "urn:adp:wdr-action-renderer:1.0.0",
            "renderer_sha256": registry["protocol"]["sha256"], "actions": snapshot["actions"],
        }
        targets.extend([
            {"path": record_path, "operation": "replace"},
            {"path": f"workstreams/{workstream_id}/delivery-record.state.json", "operation": "create"},
            {"path": f"workstreams/{workstream_id}/action-projection.json", "operation": "create"},
        ])
        contents.extend([(before_wdr, after_wdr), (None, canonical_bytes(state)), (None, canonical_bytes(sidecar))])
    command["workstreams"].sort(key=lambda row: row["workstream_id"].encode("utf-8"))

    skeleton = fact_attribution_fixture(schema_sha, registry_sha, registry, "action-create")
    journal, marker = journal_fixture("fact", schema_sha, registry_sha, registry, targets)
    before_state, after_state = skeleton["before_state"], skeleton["after_state"]
    generation_target = next(row for row in journal["targets"] if row["role"] == "fact-generation")
    generation_target["before_sha256"] = sha256_bytes(canonical_bytes(before_state))
    generation_target["after_sha256"] = sha256_bytes(canonical_bytes(after_state))
    generation_target["before_image"] = {"root_instance_id": generation_target["root_instance_id"], "path": f"{journal['journal_dir']}/images/{generation_target['apply_order']}-before", "sha256": generation_target["before_sha256"]}
    generation_target["after_image"] = {"root_instance_id": generation_target["root_instance_id"], "path": f"{journal['journal_dir']}/images/{generation_target['apply_order']}-after", "sha256": generation_target["after_sha256"]}
    artifacts: list[dict[str, Any]] = []
    business_targets = [row for row in journal["targets"] if row["role"] == "business"]
    for target, (before_bytes, after_bytes) in zip(business_targets, contents):
        target["before_sha256"] = None if before_bytes is None else sha256_bytes(before_bytes)
        target["after_sha256"] = None if after_bytes is None else sha256_bytes(after_bytes)
        target["before_image"] = None if before_bytes is None else {"root_instance_id": target["root_instance_id"], "path": f"{journal['journal_dir']}/images/{target['apply_order']}-before", "sha256": target["before_sha256"]}
        target["after_image"] = None if after_bytes is None else {"root_instance_id": target["root_instance_id"], "path": f"{journal['journal_dir']}/images/{target['apply_order']}-after", "sha256": target["after_sha256"]}
        artifacts.append({
            "root_instance_id": target["root_instance_id"], "path": target["path"], "operation": target["operation"],
            "before_bytes": encoded_bytes(before_bytes), "after_bytes": encoded_bytes(after_bytes),
        })
    proof = {
        "contract": expected_contract_ref(registry, "fact-mutation-proof/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "transaction_id": journal["transaction_id"], "host_principal_id": "sha256:" + "b" * 64,
        "authorized_command_fingerprint": "sha256:" + "0" * 64, "business_artifacts": artifacts, "read_artifacts": [],
        "proof_id": "sha256:" + "0" * 64,
    }
    receipt = {
        "contract": expected_contract_ref(registry, "fact-mutation-receipt/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "receipt_id": "sha256:" + "0" * 64, "transaction_id": journal["transaction_id"],
        "journal_id": journal["journal_id"], "authorization": copy.deepcopy(skeleton["receipt"]["authorization"]),
        "initiator": copy.deepcopy(skeleton["receipt"]["initiator"]), "before_fact_generation": before_state["fact_generation"],
        "after_fact_generation": after_state["fact_generation"], "business_targets": copy.deepcopy(business_targets),
        "generation_state_target": copy.deepcopy(generation_target), "action_deltas": [], "status": "committed",
    }
    graph = {
        "capability_registry": skeleton["capability_registry"], "command": command, "journal": journal, "marker": marker,
        "before_state": before_state, "after_state": after_state, "receipt": receipt, "proof": proof,
        "before_command_index": copy.deepcopy(skeleton["before_command_index"]),
        "command_index": copy.deepcopy(skeleton["command_index"]),
    }
    rebind_fact_graph(graph)
    return graph


def bootstrap_migration_semantics(
    graph: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
) -> bool:
    try:
        command, journal, marker, proof, receipt = (graph[name] for name in ("command", "journal", "marker", "proof", "receipt"))
        registry_doc = graph["capability_registry"]
        if not all((
            validate_registered(command, schema, registry, "bootstrap-migration-command/1.0.0", schema_sha, registry_sha),
            validate_registered(registry_doc, schema, registry, "writer-capability-registry/1.0.0", schema_sha, registry_sha),
            validate_registered(proof, schema, registry, "fact-mutation-proof/1.0.0", schema_sha, registry_sha),
            validate_registered(receipt, schema, registry, "fact-mutation-receipt/1.0.0", schema_sha, registry_sha),
            validate_registered(graph["before_state"], schema, registry, "fact-generation-state/1.0.0", schema_sha, registry_sha),
            validate_registered(graph["after_state"], schema, registry, "fact-generation-state/1.0.0", schema_sha, registry_sha),
            journal_semantics(journal, marker, schema, registry, schema_sha, registry_sha),
        )):
            return False
        cap = next(row for row in registry_doc["capabilities"] if row["producer_id"] == "adp-status-sync" and row["status"] == "active")
        registry_body = {key: value for key, value in registry_doc.items() if key != "capability_registry_id"}
        if registry_doc["capability_registry_id"] != sha256_bytes(canonical_bytes(registry_body)) or capability_record_digest(cap) != cap["capability_id"] or "bootstrap" not in cap["allowed_operations"]:
            return False
        expected_auth = {
            "producer_id": cap["producer_id"], "capability_id": cap["capability_id"], "capability_epoch": registry_doc["capability_epoch"],
            "principal_id": cap["principal_id"], "capability_registry_id": registry_doc["capability_registry_id"],
            "authorization_record_digest": cap["authorization_record_digest"], "authorized_command_fingerprint": sha256_bytes(canonical_bytes(command)),
        }
        if command["issuer"] != {"producer_id": cap["producer_id"], "capability_id": cap["capability_id"]} or journal["authorization"] != expected_auth or receipt["authorization"] != expected_auth:
            return False
        if proof["proof_id"] != sha256_bytes(canonical_bytes({key: value for key, value in proof.items() if key != "proof_id"})) or receipt["receipt_id"] != sha256_bytes(canonical_bytes({key: value for key, value in receipt.items() if key != "receipt_id"})):
            return False
        business = [row for row in journal["targets"] if row["role"] == "business"]
        if receipt["business_targets"] != business or receipt["action_deltas"] or len(proof["business_artifacts"]) != len(business):
            return False
        ledger_decl = command["action_ledger"]
        expected_targets = [
            {"root_instance_id": "123e4567-e89b-42d3-a456-426614174000", "path": registry["runtime_paths"]["action_ledger"]["path"], "operation": "create" if ledger_decl["format"] == "absent" else "replace"},
            {"root_instance_id": "123e4567-e89b-42d3-a456-426614174000", "path": registry["runtime_paths"]["action_ledger_state"]["path"], "operation": "create"},
            {"root_instance_id": "123e4567-e89b-42d3-a456-426614174000", "path": registry["runtime_paths"]["action_flow_index"]["path"], "operation": "create" if ledger_decl["action_flow_preimage"] == "absent" else "replace"},
        ]
        for row in command["workstreams"]:
            prefix = f"workstreams/{row['workstream_id']}"
            expected_targets.extend([
                {"root_instance_id": "123e4567-e89b-42d3-a456-426614174000", "path": f"{prefix}/delivery-record.md", "operation": "replace"},
                {"root_instance_id": "123e4567-e89b-42d3-a456-426614174000", "path": f"{prefix}/delivery-record.state.json", "operation": "create"},
                {"root_instance_id": "123e4567-e89b-42d3-a456-426614174000", "path": f"{prefix}/action-projection.json", "operation": "create"},
            ])
        if [{key: row[key] for key in ("root_instance_id", "path", "operation")} for row in business] != expected_targets:
            return False
        decoded: list[tuple[bytes | None, bytes | None]] = []
        for target, artifact in zip(business, proof["business_artifacts"]):
            if {key: artifact[key] for key in ("root_instance_id", "path", "operation")} != {key: target[key] for key in ("root_instance_id", "path", "operation")}:
                return False
            before_bytes, after_bytes = artifact_bytes(artifact["before_bytes"]), artifact_bytes(artifact["after_bytes"])
            if target["before_sha256"] != (None if before_bytes is None else sha256_bytes(before_bytes)) or target["after_sha256"] != (None if after_bytes is None else sha256_bytes(after_bytes)):
                return False
            decoded.append((before_bytes, after_bytes))
        before_ledger, after_ledger = decoded[0]
        if ledger_decl["expected_fingerprint"] != (None if before_ledger is None else sha256_bytes(before_ledger)):
            return False
        rows = parse_action_ledger_ingress(before_ledger, ledger_decl["format"])
        if after_ledger != render_action_ledger(rows) or decoded[1][0] is not None or decoded[2][0] is not None and ledger_decl["action_flow_preimage"] == "absent":
            return False
        after_ledger_state = json.loads(decoded[1][1])
        expected_state = action_ledger_state_document(rows, after_ledger, 0, [], registry, schema_sha, registry_sha)
        expected_flow = action_flow_document(rows, after_ledger, 0, registry, schema_sha, registry_sha)
        if after_ledger_state != expected_state or json.loads(decoded[2][1]) != expected_flow:
            return False
        expected_flow_before = None if ledger_decl["action_flow_preimage"] == "absent" else canonical_bytes(action_flow_document(rows, before_ledger, 0, registry, schema_sha, registry_sha))
        if decoded[2][0] != expected_flow_before:
            return False
        offset = 3
        for workstream in command["workstreams"]:
            before_wdr, after_wdr = decoded[offset]
            if before_wdr is None or sha256_bytes(before_wdr) != workstream["expected_record_fingerprint"] or decoded[offset + 1][0] is not None or decoded[offset + 2][0] is not None:
                return False
            workstream_id = workstream["workstream_id"]
            if after_wdr != migrate_wdr(before_wdr.decode("utf-8"), command["observed_at"]).encode("utf-8") or not complete_wdr_valid(after_wdr.decode("utf-8"), workstream_id):
                return False
            state = json.loads(decoded[offset + 1][1]); sidecar = json.loads(decoded[offset + 2][1])
            expected_wdr_state = {
                "contract": expected_contract_ref(registry, "wdr-file-state/1.0.0", schema_sha, registry_sha), "schema_version": "1.0.0",
                "workstream_id": workstream_id, "record_path": f"workstreams/{workstream_id}/delivery-record.md",
                "record_fingerprint": sha256_bytes(after_wdr), "wdr_revision": 0, "file_generation": 1, "lifecycle": "active",
            }
            snapshot = action_snapshot(rows, workstream_id, expected_state["ledger_fingerprint"], 0)
            if state != expected_wdr_state or sidecar != {
                "contract": expected_contract_ref(registry, "wdr-action-projection/1.0.0", schema_sha, registry_sha), "schema_version": "1.0.0",
                "workstream_id": workstream_id, "ledger_fingerprint": expected_state["ledger_fingerprint"], "ledger_revision": 0,
                "wdr_revision": 0, "file_generation": 1, "renderer_id": "urn:adp:wdr-action-renderer:1.0.0",
                "renderer_sha256": registry["protocol"]["sha256"], "actions": snapshot["actions"],
            }:
                return False
            offset += 3
        before_state, after_state = graph["before_state"], graph["after_state"]
        generation = next(row for row in journal["targets"] if row["role"] == "fact-generation")
        receipt_target = next(row for row in journal["targets"] if row["role"] == "receipt")
        return (
            offset == len(decoded) and marker["state"] == "committed" and after_state["fact_generation"] == before_state["fact_generation"] + 1
            and after_state["last_transaction_id"] == journal["transaction_id"]
            and generation["before_sha256"] == sha256_bytes(canonical_bytes(before_state))
            and generation["after_sha256"] == sha256_bytes(canonical_bytes(after_state))
            and receipt_target["after_sha256"] == sha256_bytes(canonical_bytes(receipt))
        )
    except (KeyError, StopIteration, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False


def runtime_authority_fixture(
    registry: dict[str, Any], schema_sha: str, registry_sha: str, producer_id: str = "adp-status-sync",
    platform: str = "posix",
) -> tuple[bytes, bytes, bytes, bytes | None, dict[str, Any]]:
    registry_doc = capability_registry_fixture(registry, schema_sha, registry_sha, platform)
    capability = next(row for row in registry_doc["capabilities"] if row["producer_id"] == producer_id and row["status"] == "active")
    principal_id, effective_identity_sha256, executable_sha256, native_preimage, native_verification = authority_native_fixture(registry, producer_id, platform)
    profile = registry["runtime_authority_profile"]
    memory_root = "123e4567-e89b-42d3-a456-426614174000"
    root_registry = {
        "contract": expected_contract_ref(registry, "root-registry-state/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "roots": [
            {"role": "memory", "root_instance_id": memory_root, "canonical_path_hash": sha256_bytes(b"/canonical/memory")},
            {"role": "project", "root_instance_id": "123e4567-e89b-42d3-a456-426614174001", "canonical_path_hash": sha256_bytes(b"/canonical/project")},
        ], "created_at": "2026-07-24T01:00:00Z",
    }
    root_registry["registry_state_id"] = sha256_bytes(canonical_bytes(root_registry))
    activation = {
        "contract": expected_contract_ref(registry, "strict-activation-state/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "activation_epoch": 1, "mode": "legacy", "attestation_id": None,
        "changed_at": "2026-07-24T01:00:01Z",
    }
    activation["state_id"] = sha256_bytes(canonical_bytes(activation))
    root_raw = canonical_bytes(root_registry)
    capability_raw = canonical_bytes(registry_doc)
    activation_raw = canonical_bytes(activation)
    context = {
        "contract": expected_contract_ref(registry, "runtime-authority-context/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "authority_profile_id": profile["profile_id"],
        "memory_root_instance_id": memory_root,
        "root_registry_path": registry["runtime_paths"]["root_registry_state"]["path"],
        "root_registry_sha256": sha256_bytes(root_raw), "root_registry_state_id": root_registry["registry_state_id"],
        "capability_registry_root_instance_id": memory_root,
        "capability_registry_path": registry["runtime_paths"]["writer_capability_registry"]["path"],
        "capability_registry_sha256": sha256_bytes(capability_raw), "capability_registry_id": registry_doc["capability_registry_id"],
        "activation_state_path": registry["runtime_paths"]["strict_activation_state"]["path"],
        "activation_state_sha256": sha256_bytes(activation_raw), "activation_state_id": activation["state_id"],
        "activation_mode": activation["mode"], "attestation_path": registry["runtime_paths"]["writer_fence_attestation"]["path"],
        "attestation_sha256": None,
        "fact_lock_profile_id": registry["lock_profile"]["profile_id"],
        "fact_lock_path": registry["runtime_paths"]["fact_lock"]["path"], "lock_mode": "exclusive",
        "activation_epoch": 1, "attestation_id": None, "capability_epoch": registry_doc["capability_epoch"],
        "platform": platform, "principal_adapter_id": profile["principal_adapters"][platform]["id"],
        "native_preimage": native_preimage, "native_verification": native_verification,
        "effective_identity_sha256": effective_identity_sha256, "executable_sha256": executable_sha256,
        "principal_id": principal_id,
    }
    if capability["principal_id"] != principal_id:
        raise ValueError("native runtime principal fixture does not match provisioned capability")
    context["context_id"] = sha256_bytes(canonical_bytes(context))
    return capability_raw, root_raw, activation_raw, None, context


def runtime_authority_from_documents(
    registry: dict[str, Any], schema_sha: str, registry_sha: str, producer_id: str,
    capability: dict[str, Any], roots: dict[str, Any], activation: dict[str, Any],
    attestation: dict[str, Any] | None, platform: str = "posix",
) -> tuple[bytes, bytes, bytes, bytes | None, dict[str, Any]]:
    profile = registry["runtime_authority_profile"]
    capability_raw, root_raw, activation_raw = (canonical_bytes(value) for value in (capability, roots, activation))
    attestation_raw = None if attestation is None else canonical_bytes(attestation)
    principal_id, effective_identity_sha256, executable_sha256, native_preimage, native_verification = authority_native_fixture(registry, producer_id, platform)
    active = next(row for row in capability["capabilities"] if row["producer_id"] == producer_id and row["status"] == "active")
    memory_root = next(row["root_instance_id"] for row in roots["roots"] if row["role"] == "memory")
    if active["principal_id"] != principal_id:
        raise ValueError("native authority does not match the active capability")
    context = {
        "contract": expected_contract_ref(registry, "runtime-authority-context/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "authority_profile_id": profile["profile_id"],
        "memory_root_instance_id": memory_root,
        "root_registry_path": registry["runtime_paths"]["root_registry_state"]["path"],
        "root_registry_sha256": sha256_bytes(root_raw), "root_registry_state_id": roots["registry_state_id"],
        "capability_registry_root_instance_id": memory_root,
        "capability_registry_path": registry["runtime_paths"]["writer_capability_registry"]["path"],
        "capability_registry_sha256": sha256_bytes(capability_raw), "capability_registry_id": capability["capability_registry_id"],
        "activation_state_path": registry["runtime_paths"]["strict_activation_state"]["path"],
        "activation_state_sha256": sha256_bytes(activation_raw), "activation_state_id": activation["state_id"],
        "activation_mode": activation["mode"], "attestation_path": registry["runtime_paths"]["writer_fence_attestation"]["path"],
        "attestation_sha256": None if attestation_raw is None else sha256_bytes(attestation_raw),
        "fact_lock_profile_id": registry["lock_profile"]["profile_id"],
        "fact_lock_path": registry["runtime_paths"]["fact_lock"]["path"], "lock_mode": "exclusive",
        "activation_epoch": activation["activation_epoch"], "attestation_id": None if attestation is None else attestation["attestation_id"],
        "capability_epoch": capability["capability_epoch"], "platform": platform,
        "principal_adapter_id": profile["principal_adapters"][platform]["id"],
        "native_preimage": native_preimage, "native_verification": native_verification,
        "effective_identity_sha256": effective_identity_sha256, "executable_sha256": executable_sha256,
        "principal_id": principal_id,
    }
    context["context_id"] = sha256_bytes(canonical_bytes(context))
    return capability_raw, root_raw, activation_raw, attestation_raw, context


def _owned_fact_profile(command: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any] | None:
    matches = [row for row in registry["owned_fact_target_profiles"] if row["profile_id"] == command.get("target_profile_id")]
    if len(matches) != 1:
        return None
    profile = matches[0]
    path = command.get("target_path")
    rule = profile["path_rule"]
    path_matches = False
    if isinstance(path, str):
        if rule["kind"] == "exact":
            path_matches = path == rule["value"]
        elif rule["kind"] == "workstream-file":
            segment = registry["owned_fact_path_segment_rules"][rule["segment_rule"]]
            path_matches = re.fullmatch(rf"{re.escape(rule['base'])}/({segment['pattern'][1:-1]})/{re.escape(rule['filename'])}", path) is not None
        elif rule["kind"] == "directory-file":
            segment = registry["owned_fact_path_segment_rules"][rule["segment_rule"]]
            path_matches = re.fullmatch(rf"{re.escape(rule['base'])}/({segment['pattern'][1:-1]}){re.escape(rule['suffix'])}", path) is not None
    return profile if (
        path_matches and command.get("operation") in profile["operations"]
        and command.get("issuer", {}).get("producer_id") == profile["producer_id"]
        and profile["root"] == "memory"
    ) else None


def _owned_fact_content_valid(raw: bytes, profile: dict[str, Any], registry: dict[str, Any]) -> bool:
    try:
        if profile["content_rule"] == "markdown-byte-invariants-v1":
            rule = registry["owned_fact_content_rules"][profile["content_rule"]]
            text = raw.decode("utf-8")
            return (
                rule["encoding"] == "utf-8" and rule["nonempty"] and bool(text)
                and rule["line_ending"] == "LF" and rule["final_lf"] and text.endswith("\n")
                and rule["unicode_normalization"] == "NFC" and unicodedata.normalize("NFC", text) == text
                and "\r" not in text and "\0" not in text
                and rule["structural_grammar"] is None and rule["canonical_renderer"] is None
            )
        if profile["content_rule"] == "json-schema":
            schema_path = _repository_root() / profile["schema_path"]
            schema_raw = schema_path.read_bytes()
            payload = json.loads(raw)
            payload_schema = json.loads(schema_raw)
            return (
                canonical_bytes(payload) == raw
                and sha256_bytes(schema_raw) == profile["schema_sha256"]
                and payload_schema.get("$id") == profile["schema_id"]
                and not schema_errors(payload, payload_schema, payload_schema)
            )
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False
    return False


def runtime_authority_binding_semantics(
    registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
    capability_raw: bytes, root_raw: bytes, activation_raw: bytes, attestation_raw: bytes | None,
    context: dict[str, Any],
) -> bool:
    try:
        capability = json.loads(capability_raw)
        roots = json.loads(root_raw)
        activation = json.loads(activation_raw)
        attestation = None if attestation_raw is None else json.loads(attestation_raw)
        profile = registry["runtime_authority_profile"]
        native_preimage = context["native_preimage"]
        native_verification = context["native_verification"]
        principal_body = {
            "authority_profile_id": context["authority_profile_id"], "platform": context["platform"],
            "native_preimage": native_preimage,
        }
        root_rows = {row["role"]: row for row in roots["roots"]}
        return bool(
            canonical_bytes(capability) == capability_raw and canonical_bytes(roots) == root_raw
            and canonical_bytes(activation) == activation_raw
            and (attestation is None or canonical_bytes(attestation) == attestation_raw)
            and validate_registered(capability, schema, registry, "writer-capability-registry/1.0.0", schema_sha, registry_sha)
            and validate_registered(roots, schema, registry, "root-registry-state/1.0.0", schema_sha, registry_sha)
            and validate_registered(activation, schema, registry, "strict-activation-state/1.0.0", schema_sha, registry_sha)
            and validate_registered(context, schema, registry, "runtime-authority-context/1.0.0", schema_sha, registry_sha)
            and profile["profile_id"] == sha256_bytes(canonical_bytes({key: value for key, value in profile.items() if key != "profile_id"}))
            and context["context_id"] == sha256_bytes(canonical_bytes({key: value for key, value in context.items() if key != "context_id"}))
            and context["authority_profile_id"] == profile["profile_id"]
            and context["memory_root_instance_id"] == root_rows["memory"]["root_instance_id"]
            and context["root_registry_path"] == registry["runtime_paths"]["root_registry_state"]["path"]
            and context["root_registry_sha256"] == sha256_bytes(root_raw)
            and context["root_registry_state_id"] == roots["registry_state_id"]
            and roots["registry_state_id"] == sha256_bytes(canonical_bytes({key: value for key, value in roots.items() if key != "registry_state_id"}))
            and context["capability_registry_root_instance_id"] == root_rows["memory"]["root_instance_id"]
            and context["capability_registry_path"] == registry["runtime_paths"]["writer_capability_registry"]["path"]
            and context["capability_registry_sha256"] == sha256_bytes(capability_raw)
            and context["capability_registry_id"] == capability["capability_registry_id"]
            and capability["capability_registry_id"] == sha256_bytes(canonical_bytes({key: value for key, value in capability.items() if key != "capability_registry_id"}))
            and context["activation_state_path"] == registry["runtime_paths"]["strict_activation_state"]["path"]
            and context["activation_state_sha256"] == sha256_bytes(activation_raw)
            and context["activation_state_id"] == activation["state_id"]
            and activation["state_id"] == sha256_bytes(canonical_bytes({key: value for key, value in activation.items() if key != "state_id"}))
            and context["activation_mode"] == activation["mode"]
            and context["activation_epoch"] == activation["activation_epoch"]
            and context["attestation_path"] == registry["runtime_paths"]["writer_fence_attestation"]["path"]
            and context["fact_lock_profile_id"] == registry["lock_profile"]["profile_id"]
            and context["fact_lock_path"] == registry["runtime_paths"]["fact_lock"]["path"]
            and context["lock_mode"] == profile["required_lock_mode"] == "exclusive"
            and context["principal_adapter_id"] == profile["principal_adapters"][context["platform"]]["id"]
            and native_preimage["adapter_id"] == context["principal_adapter_id"]
            and list(native_preimage) == profile["principal_adapters"][context["platform"]]["preimage_fields"]
            and native_preimage["executable_sha256"] == context["executable_sha256"]
            and context["effective_identity_sha256"] == sha256_bytes(canonical_bytes(native_preimage))
            and native_verification == {
                "adapter_boundary": profile["adapter_boundary"], "native_api_observed": True,
                "opened_executable_handle": True, "path_alias_rejected": True,
                "namespace_or_token_verified": True, "service_identity_verified": True,
            }
            and context["principal_id"] == sha256_bytes(canonical_bytes(principal_body))
            and context["capability_epoch"] == capability["capability_epoch"]
            and (
                activation["mode"] != "strict"
                and attestation is None and context["attestation_id"] is None and context["attestation_sha256"] is None
                or activation["mode"] == "strict"
                and isinstance(attestation, dict)
                and validate_registered(attestation, schema, registry, "writer-fence-migration-attestation/1.0.0", schema_sha, registry_sha)
                and attestation["attestation_id"] == sha256_bytes(canonical_bytes({key: value for key, value in attestation.items() if key != "attestation_id"}))
                and context["attestation_id"] == activation["attestation_id"] == attestation["attestation_id"]
                and context["attestation_sha256"] == sha256_bytes(attestation_raw)
                and attestation["activation_epoch"] == activation["activation_epoch"]
                and attestation["capability_registry_id"] == capability["capability_registry_id"]
                and attestation["capability_epoch"] == capability["capability_epoch"]
                and attestation["root_registry_state_id"] == roots["registry_state_id"]
            )
        )
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def fact_attribution_semantics(
    graph: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
    runtime_capability_bytes: bytes, runtime_root_registry_bytes: bytes, runtime_activation_bytes: bytes,
    runtime_attestation_bytes: bytes | None, authority_context: dict[str, Any],
) -> bool:
    required = {
        "command", "journal", "marker", "before_state", "after_state", "receipt", "proof",
        "before_command_index", "command_index", "before_outbox", "after_outbox",
    }
    if not required <= set(graph):
        return False
    if not all(isinstance(raw, bytes) for raw in (runtime_capability_bytes, runtime_root_registry_bytes, runtime_activation_bytes)) or not isinstance(authority_context, dict):
        return False
    if not runtime_authority_binding_semantics(
        registry, schema, schema_sha, registry_sha, runtime_capability_bytes, runtime_root_registry_bytes,
        runtime_activation_bytes, runtime_attestation_bytes, authority_context,
    ):
        return False
    try:
        registry_doc = json.loads(runtime_capability_bytes)
        root_registry = json.loads(runtime_root_registry_bytes)
        activation = json.loads(runtime_activation_bytes)
        attestation = None if runtime_attestation_bytes is None else json.loads(runtime_attestation_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if canonical_bytes(registry_doc) != runtime_capability_bytes or canonical_bytes(root_registry) != runtime_root_registry_bytes or canonical_bytes(activation) != runtime_activation_bytes or (attestation is not None and canonical_bytes(attestation) != runtime_attestation_bytes):
        return False
    profile = registry["runtime_authority_profile"]
    profile_body = {key: value for key, value in profile.items() if key != "profile_id"}
    context_body = {key: value for key, value in authority_context.items() if key != "context_id"}
    principal_body = {
        "authority_profile_id": authority_context.get("authority_profile_id"),
        "platform": authority_context.get("platform"),
        "native_preimage": authority_context.get("native_preimage"),
    }
    if not (
        validate_registered(authority_context, schema, registry, "runtime-authority-context/1.0.0", schema_sha, registry_sha)
        and validate_registered(root_registry, schema, registry, "root-registry-state/1.0.0", schema_sha, registry_sha)
        and validate_registered(activation, schema, registry, "strict-activation-state/1.0.0", schema_sha, registry_sha)
        and profile["profile_id"] == sha256_bytes(canonical_bytes(profile_body))
        and authority_context["context_id"] == sha256_bytes(canonical_bytes(context_body))
        and authority_context["authority_profile_id"] == profile["profile_id"]
        and authority_context["memory_root_instance_id"] == "123e4567-e89b-42d3-a456-426614174000"
        and authority_context["root_registry_path"] == registry["runtime_paths"]["root_registry_state"]["path"]
        and authority_context["root_registry_sha256"] == sha256_bytes(runtime_root_registry_bytes)
        and authority_context["root_registry_state_id"] == root_registry["registry_state_id"]
        and root_registry["registry_state_id"] == sha256_bytes(canonical_bytes({key: value for key, value in root_registry.items() if key != "registry_state_id"}))
        and {row["role"]: row["root_instance_id"] for row in root_registry["roots"]}.get("memory") == authority_context["memory_root_instance_id"]
        and authority_context["capability_registry_root_instance_id"] == authority_context["memory_root_instance_id"]
        and authority_context["capability_registry_path"] == registry["runtime_paths"]["writer_capability_registry"]["path"]
        and authority_context["capability_registry_sha256"] == sha256_bytes(runtime_capability_bytes)
        and authority_context["capability_registry_id"] == registry_doc["capability_registry_id"]
        and authority_context["activation_state_path"] == registry["runtime_paths"]["strict_activation_state"]["path"]
        and authority_context["activation_state_sha256"] == sha256_bytes(runtime_activation_bytes)
        and authority_context["activation_state_id"] == activation["state_id"]
        and activation["state_id"] == sha256_bytes(canonical_bytes({key: value for key, value in activation.items() if key != "state_id"}))
        and authority_context["activation_mode"] == activation["mode"]
        and authority_context["activation_epoch"] == activation["activation_epoch"]
        and authority_context["attestation_path"] == registry["runtime_paths"]["writer_fence_attestation"]["path"]
        and authority_context["fact_lock_profile_id"] == registry["lock_profile"]["profile_id"]
        and authority_context["fact_lock_path"] == registry["runtime_paths"]["fact_lock"]["path"]
        and authority_context["lock_mode"] == profile["required_lock_mode"] == "exclusive"
        and authority_context["principal_adapter_id"] == profile["principal_adapters"][authority_context["platform"]]["id"]
        and authority_context["principal_id"] == sha256_bytes(canonical_bytes(principal_body))
        and authority_context["capability_epoch"] == registry_doc["capability_epoch"]
    ):
        return False
    if activation["mode"] == "strict":
        if not (
            isinstance(attestation, dict)
            and validate_registered(attestation, schema, registry, "writer-fence-migration-attestation/1.0.0", schema_sha, registry_sha)
            and attestation["attestation_id"] == sha256_bytes(canonical_bytes({key: value for key, value in attestation.items() if key != "attestation_id"}))
            and authority_context["attestation_id"] == activation["attestation_id"] == attestation["attestation_id"]
            and authority_context["attestation_sha256"] == sha256_bytes(runtime_attestation_bytes)
            and attestation["activation_epoch"] == activation["activation_epoch"]
            and attestation["capability_registry_id"] == registry_doc["capability_registry_id"]
            and attestation["capability_epoch"] == registry_doc["capability_epoch"]
            and attestation["root_registry_state_id"] == root_registry["registry_state_id"]
        ):
            return False
    elif not (attestation is None and authority_context["attestation_id"] is None and authority_context["attestation_sha256"] is None):
        return False
    if "capability_registry" in graph and canonical_bytes(graph["capability_registry"]) != runtime_capability_bytes:
        return False
    command, journal, marker, before_state, after_state, receipt, proof = (graph[name] for name in ("command", "journal", "marker", "before_state", "after_state", "receipt", "proof"))
    before_command_index, command_index = graph["before_command_index"], graph["command_index"]
    action_ref = expected_contract_ref(registry, "action-ledger-mutation/2.0.0", schema_sha, registry_sha)
    wdr_ref = expected_contract_ref(registry, "wdr-mutation/1.0.0", schema_sha, registry_sha)
    owned_ref = expected_contract_ref(registry, "owned-fact-command/1.0.0", schema_sha, registry_sha)
    intent_ref = expected_contract_ref(registry, "producer-intent-outbox-command/1.0.0", schema_sha, registry_sha)
    if command.get("contract") == action_ref:
        kind, command_valid = "action", validate_registered(command, schema, registry, "action-ledger-mutation/2.0.0", schema_sha, registry_sha)
    elif command.get("contract") == wdr_ref:
        kind, command_valid = "wdr", validate_registered(command, schema, registry, "wdr-mutation/1.0.0", schema_sha, registry_sha)
    elif command.get("contract") == owned_ref:
        kind, command_valid = "owned", validate_registered(command, schema, registry, "owned-fact-command/1.0.0", schema_sha, registry_sha)
    elif command.get("contract") == intent_ref:
        kind, command_valid = "intent", validate_registered(command, schema, registry, "producer-intent-outbox-command/1.0.0", schema_sha, registry_sha)
    else:
        return False
    if not all((
        validate_registered(registry_doc, schema, registry, "writer-capability-registry/1.0.0", schema_sha, registry_sha), command_valid,
        validate_registered(before_state, schema, registry, "fact-generation-state/1.0.0", schema_sha, registry_sha),
        validate_registered(after_state, schema, registry, "fact-generation-state/1.0.0", schema_sha, registry_sha),
        validate_registered(receipt, schema, registry, "fact-mutation-receipt/1.0.0", schema_sha, registry_sha),
        validate_registered(proof, schema, registry, "fact-mutation-proof/1.0.0", schema_sha, registry_sha),
        validate_registered(before_command_index, schema, registry, "fact-command-receipt-index/1.0.0", schema_sha, registry_sha),
        validate_registered(command_index, schema, registry, "fact-command-receipt-index/1.0.0", schema_sha, registry_sha),
        journal_semantics(journal, marker, schema, registry, schema_sha, registry_sha),
    )):
        return False
    if kind == "wdr" and command["operation"] == "patch":
        history_rows = command["set"].get("meeting_history_append", [])
        if any(row["command_id"] != command["command_id"] for row in history_rows):
            return False
    auth = receipt["authorization"]
    registry_body = {key: value for key, value in registry_doc.items() if key != "capability_registry_id"}
    if registry_doc["capability_registry_id"] != sha256_bytes(canonical_bytes(registry_body)):
        return False
    required_producers = set(registry["strict_rollout"]["authoritative_writers"])
    writer_specs = {row["producer_id"]: row for row in registry["strict_rollout"]["writer_specs"]}
    capability_ids = [row["capability_id"] for row in registry_doc["capabilities"]]
    active_producers = [row["producer_id"] for row in registry_doc["capabilities"] if row["status"] == "active"]
    if len(capability_ids) != len(set(capability_ids)) or len(active_producers) != len(set(active_producers)) or set(active_producers) != required_producers:
        return False
    if registry_doc["capabilities"] != sorted(registry_doc["capabilities"], key=lambda row: row["producer_id"].encode("utf-8")):
        return False
    for row in registry_doc["capabilities"]:
        if capability_record_digest(row) != row["capability_id"] or row["capability_id"] != row["authorization_record_digest"]:
            return False
        if any(row[name] != sorted(row[name], key=lambda value: value.encode("utf-8")) for name in ("allowed_operations", "allowed_fields", "allowed_sections")):
            return False
        spec = writer_specs.get(row["producer_id"])
        if spec is None or any(row[name] != spec[name] for name in ("allowed_operations", "allowed_fields", "allowed_sections")):
            return False
    matches = [row for row in registry_doc["capabilities"] if row["producer_id"] == auth["producer_id"] and row["status"] == "active"]
    if len(matches) != 1:
        return False
    cap = matches[0]
    if capability_record_digest(cap) != cap["capability_id"] or cap["capability_id"] != cap["authorization_record_digest"]:
        return False
    command_fields, command_sections = command_permissions(command, registry)
    if command["operation"] not in cap["allowed_operations"] or not command_fields <= set(cap["allowed_fields"]) or not command_sections <= set(cap["allowed_sections"]):
        return False
    if kind in {"wdr", "owned", "intent"} and command["issuer"] != {"producer_id": cap["producer_id"], "capability_id": cap["capability_id"]}:
        return False
    expected_auth = {
        "producer_id": cap["producer_id"], "capability_id": cap["capability_id"], "capability_epoch": registry_doc["capability_epoch"], "principal_id": cap["principal_id"],
        "capability_registry_id": registry_doc["capability_registry_id"], "authorization_record_digest": cap["authorization_record_digest"],
        "authorized_command_fingerprint": sha256_bytes(canonical_bytes(command)),
    }
    if auth != expected_auth or journal["authorization"] != expected_auth:
        return False
    proof_body = {key: value for key, value in proof.items() if key != "proof_id"}
    if not (
        proof["proof_id"] == sha256_bytes(canonical_bytes(proof_body))
        and proof["transaction_id"] == journal["transaction_id"]
        and proof["host_principal_id"] == cap["principal_id"] == authority_context["principal_id"]
        and proof["authorized_command_fingerprint"] == expected_auth["authorized_command_fingerprint"]
    ):
        return False
    if receipt["initiator"] != {key: expected_auth[key] for key in ("producer_id", "capability_id", "capability_epoch", "principal_id")}:
        return False
    if receipt["transaction_id"] != journal["transaction_id"] or receipt["journal_id"] != journal["journal_id"]:
        return False
    if receipt["receipt_id"] != sha256_bytes(canonical_bytes({key: value for key, value in receipt.items() if key != "receipt_id"})):
        return False
    if receipt["after_fact_generation"] != receipt["before_fact_generation"] + 1:
        return False
    business = [row for row in journal["targets"] if row["role"] == "business"]
    generation = [row for row in journal["targets"] if row["role"] == "fact-generation"]
    expected_targets = expected_fact_business_targets(command, registry)
    artifacts = proof["business_artifacts"]
    if receipt["business_targets"] != business or len(generation) != 1 or receipt["generation_state_target"] != generation[0] or len(business) != len(expected_targets) or len(artifacts) != len(expected_targets):
        return False
    decoded: list[tuple[bytes | None, bytes | None]] = []
    try:
        for target, expected_target, artifact in zip(business, expected_targets, artifacts):
            identity = {key: target[key] for key in ("root_instance_id", "path", "operation")}
            if identity != expected_target or {key: artifact[key] for key in identity} != expected_target:
                return False
            before_bytes, after_bytes = artifact_bytes(artifact["before_bytes"]), artifact_bytes(artifact["after_bytes"])
            if target["before_sha256"] != (None if before_bytes is None else sha256_bytes(before_bytes)) or target["after_sha256"] != (None if after_bytes is None else sha256_bytes(after_bytes)):
                return False
            decoded.append((before_bytes, after_bytes))
    except (ValueError, TypeError):
        return False

    read_artifacts = proof["read_artifacts"]
    read_values: dict[str, bytes] = {}
    try:
        for artifact in read_artifacts:
            raw = artifact_bytes(artifact["bytes"])
            if raw is None or artifact["sha256"] != sha256_bytes(raw) or artifact["path"] in read_values:
                return False
            if artifact["root_instance_id"] != "123e4567-e89b-42d3-a456-426614174000":
                return False
            read_values[artifact["path"]] = raw
    except (ValueError, TypeError):
        return False

    if kind == "action":
        try:
            before_ledger, after_ledger = decoded[0]
            before_ledger_state, after_ledger_state = (json.loads(value) for value in decoded[1])
            before_flow, after_flow = (json.loads(value) for value in decoded[2])
            before_rows = parse_action_ledger(before_ledger)
            after_rows = parse_action_ledger(after_ledger)
            expected_after_rows = apply_action_command(before_rows, command)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return False
        if read_artifacts or before_ledger is None or after_ledger is None or after_ledger != render_action_ledger(expected_after_rows) or after_rows != expected_after_rows or not all((
            validate_registered(before_ledger_state, schema, registry, "action-ledger-state/1.0.0", schema_sha, registry_sha),
            validate_registered(after_ledger_state, schema, registry, "action-ledger-state/1.0.0", schema_sha, registry_sha),
            validate_registered(before_flow, schema, registry, "action-flow-index/1.0.0", schema_sha, registry_sha),
            validate_registered(after_flow, schema, registry, "action-flow-index/1.0.0", schema_sha, registry_sha),
        )):
            return False
        delta = expected_action_delta(command)
        applied_record = {"command_id": command["command_id"], "command_fingerprint": expected_auth["authorized_command_fingerprint"], "action_id": command["action_id"]}
        expected_before_state = action_ledger_state_document(
            before_rows, before_ledger, before_ledger_state["ledger_revision"], before_ledger_state["applied_commands"], registry, schema_sha, registry_sha
        )
        expected_after_state = action_ledger_state_document(
            expected_after_rows, after_ledger, before_ledger_state["ledger_revision"] + 1,
            before_ledger_state["applied_commands"] + [applied_record], registry, schema_sha, registry_sha,
        )
        expected_before_flow = action_flow_document(before_rows, before_ledger, before_ledger_state["ledger_revision"], registry, schema_sha, registry_sha)
        expected_after_flow = action_flow_document(expected_after_rows, after_ledger, before_ledger_state["ledger_revision"] + 1, registry, schema_sha, registry_sha)
        if not (
            before_ledger_state == expected_before_state
            and after_ledger_state == expected_after_state
            and before_flow == expected_before_flow
            and after_flow == expected_after_flow
            and next(row for row in expected_after_rows if row["action_id"] == command["action_id"])["action_revision"] == delta["after_revision"]
        ):
            return False
    elif kind == "owned":
        profile = _owned_fact_profile(command, registry)
        before_owned, after_owned = decoded[0]
        if not (
            profile is not None and not read_artifacts and len(decoded) == 1 and after_owned is not None
            and command["expected_before_sha256"] == (None if before_owned is None else sha256_bytes(before_owned))
            and command["after_bytes"] == encoded_bytes(after_owned)
            and command["after_sha256"] == sha256_bytes(after_owned)
            and _owned_fact_content_valid(after_owned, profile, registry)
            and (command["operation"] == "create") == (before_owned is None)
            and canonical_evidence(command["evidence"]) == command["evidence"]
        ):
            return False
    elif kind == "intent":
        if decoded or read_artifacts:
            return False
    else:
        before_wdr, after_wdr = decoded[0]
        try:
            before_wdr_state = None if decoded[1][0] is None else json.loads(decoded[1][0])
            after_wdr_state = json.loads(decoded[1][1])
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        workstream_id = command["workstream_id"]
        if after_wdr is None or not complete_wdr_valid(after_wdr.decode("utf-8"), workstream_id) or not validate_registered(after_wdr_state, schema, registry, "wdr-file-state/1.0.0", schema_sha, registry_sha):
            return False
        if not (
            after_wdr_state["record_fingerprint"] == sha256_bytes(after_wdr)
            and after_wdr_state["workstream_id"] == workstream_id
            and after_wdr_state["record_path"] == f"workstreams/{workstream_id}/delivery-record.md"
            and after_wdr_state["lifecycle"] == "active"
        ):
            return False
        if command["operation"] == "create":
            if read_artifacts or before_wdr is not None or before_wdr_state is not None or after_wdr.decode() != command["rendered_record"] or command["rendered_sha256"] != sha256_bytes(after_wdr) or after_wdr_state["wdr_revision"] != 1 or after_wdr_state["file_generation"] != 1:
                return False
            side_before, side_after = decoded[2]
            if side_before is not None:
                return False
        else:
            if before_wdr is None or before_wdr_state is None or not complete_wdr_valid(before_wdr.decode("utf-8"), workstream_id) or not validate_registered(before_wdr_state, schema, registry, "wdr-file-state/1.0.0", schema_sha, registry_sha):
                return False
            try:
                revision_delta, generation_delta = wdr_counter_delta(before_wdr.decode(), after_wdr.decode(), workstream_id)
            except (UnicodeDecodeError, ValueError):
                return False
            if not (
                before_wdr_state["record_fingerprint"] == sha256_bytes(before_wdr)
                and before_wdr_state["workstream_id"] == workstream_id
                and before_wdr_state["record_path"] == f"workstreams/{workstream_id}/delivery-record.md"
                and before_wdr_state["lifecycle"] == "active"
                and before_wdr_state["wdr_revision"] == command["expected_wdr_revision"]
                and before_wdr_state["file_generation"] == command["expected_file_generation"]
                and after_wdr_state["workstream_id"] == workstream_id
                and after_wdr_state["record_path"] == f"workstreams/{workstream_id}/delivery-record.md"
                and after_wdr_state["lifecycle"] == "active"
                and after_wdr_state["wdr_revision"] == before_wdr_state["wdr_revision"] + revision_delta
                and after_wdr_state["file_generation"] == before_wdr_state["file_generation"] + generation_delta
                and generation_delta == 1
            ):
                return False
            side_before, side_after = decoded[2] if command["set"].get("refresh_actions") else (None, None)
        action_summaries: list[str] = []
        if command["operation"] == "create" or command["set"].get("refresh_actions"):
            try:
                sidecar_after = json.loads(side_after)
                sidecar_before = None if side_before is None else json.loads(side_before)
            except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
                return False
            if not validate_registered(sidecar_after, schema, registry, "wdr-action-projection/1.0.0", schema_sha, registry_sha):
                return False
            if not (
                sidecar_after["workstream_id"] == workstream_id
                and sidecar_after["renderer_id"] == "urn:adp:wdr-action-renderer:1.0.0"
                and sidecar_after["renderer_sha256"] == registry["protocol"]["sha256"]
                and sidecar_after["wdr_revision"] == after_wdr_state["wdr_revision"]
                and sidecar_after["file_generation"] == after_wdr_state["file_generation"]
            ):
                return False
            if command["operation"] == "create":
                if sidecar_after["actions"] or sidecar_after["wdr_revision"] != 1 or sidecar_after["file_generation"] != 1:
                    return False
            elif not (
                validate_registered(sidecar_before, schema, registry, "wdr-action-projection/1.0.0", schema_sha, registry_sha)
                and sidecar_before["workstream_id"] == workstream_id
                and sidecar_before["renderer_id"] == "urn:adp:wdr-action-renderer:1.0.0"
                and sidecar_before["renderer_sha256"] == registry["protocol"]["sha256"]
                and sidecar_before["wdr_revision"] == command["expected_wdr_revision"]
                and sidecar_before["file_generation"] == command["expected_file_generation"]
                and sidecar_after["wdr_revision"] == sidecar_before["wdr_revision"] + revision_delta
                and sidecar_after["file_generation"] == sidecar_before["file_generation"] + generation_delta
            ):
                return False
            action_summaries = [row["rendered_summary"] for row in sidecar_after["actions"]]
            if command["operation"] == "create":
                if sidecar_after["ledger_revision"] != 0:
                    return False
            else:
                ledger_path = registry["runtime_paths"]["action_ledger"]["path"]
                ledger_state_path = registry["runtime_paths"]["action_ledger_state"]["path"]
                if list(read_values) != [ledger_path, ledger_state_path]:
                    return False
                try:
                    ledger_rows = parse_action_ledger(read_values[ledger_path])
                    ledger_state = json.loads(read_values[ledger_state_path])
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                    return False
                if not validate_registered(ledger_state, schema, registry, "action-ledger-state/1.0.0", schema_sha, registry_sha):
                    return False
                expected_ledger_state = action_ledger_state_document(
                    ledger_rows, read_values[ledger_path], ledger_state["ledger_revision"], ledger_state["applied_commands"], registry, schema_sha, registry_sha
                )
                expected_snapshot = action_snapshot(
                    ledger_rows, workstream_id, ledger_state["ledger_fingerprint"], ledger_state["ledger_revision"]
                )
                if not (
                    ledger_state == expected_ledger_state
                    and command.get("action_snapshot") == expected_snapshot
                    and sidecar_after["ledger_fingerprint"] == expected_snapshot["ledger_fingerprint"]
                    and sidecar_after["ledger_revision"] == expected_snapshot["ledger_revision"]
                    and sidecar_after["actions"] == expected_snapshot["actions"]
                ):
                    return False
        elif read_artifacts:
            return False
        if command["operation"] == "patch" and after_wdr != apply_wdr_patch(before_wdr.decode(), command, action_summaries).encode():
            return False
    state_target = generation[0]
    memory_root = "123e4567-e89b-42d3-a456-426614174000"
    receipt_template = "repair_fact_receipt_template" if journal["transaction_kind"] == "repair" else "fact_receipt_template"
    receipt_path = registry["runtime_paths"][receipt_template]["path"].replace("{transaction_token}", filesystem_token(journal["transaction_id"]))
    receipt_targets = [row for row in journal["targets"] if row["role"] == "receipt"]
    matching_receipts = [row for row in receipt_targets if row["path"] == receipt_path]
    index_targets = [row for row in journal["targets"] if row["role"] == "fact-command-index"]
    expected_index_entry = {
        "sequence": before_command_index["next_sequence"], "command_id": command["command_id"],
        "command_fingerprint": expected_auth["authorized_command_fingerprint"],
        "transaction_id": journal["transaction_id"], "receipt_id": receipt["receipt_id"],
        "receipt_path": receipt_path, "receipt_sha256": sha256_bytes(canonical_bytes(receipt)),
    }
    outbox_mode = command_intent_outbox_mode(command, registry)
    outbox_targets = [row for row in journal["targets"] if row["role"] == "intent-outbox"]
    before_outbox, after_outbox = graph["before_outbox"], graph["after_outbox"]
    if outbox_mode == "none":
        outbox_ok = (
            before_outbox is None and after_outbox is None and not outbox_targets
            and "status_intents" not in command and "consumed_intent_ids" not in command
        )
    else:
        emitted_intents = status_intents_for_command(command, registry)
        outbox_ok = bool(
            isinstance(before_outbox, dict) and isinstance(after_outbox, dict)
            and ((outbox_mode == "emit" and bool(emitted_intents) and "consumed_intent_ids" not in command)
                 or (outbox_mode == "consume" and not emitted_intents and bool(command.get("consumed_intent_ids"))))
            and validate_registered(before_outbox, schema, registry, "mutation-intent-outbox/1.0.0", schema_sha, registry_sha)
            and validate_registered(after_outbox, schema, registry, "mutation-intent-outbox/1.0.0", schema_sha, registry_sha)
            and before_outbox["outbox_id"] == sha256_bytes(canonical_bytes({key: value for key, value in before_outbox.items() if key != "outbox_id"}))
            and after_outbox["outbox_id"] == sha256_bytes(canonical_bytes({key: value for key, value in after_outbox.items() if key != "outbox_id"}))
            and after_outbox["outbox_generation"] == before_outbox["outbox_generation"] + 1
            and len(outbox_targets) == 1
            and outbox_targets[0]["path"] == registry["runtime_paths"]["mutation_intent_outbox"]["path"]
            and outbox_targets[0]["before_sha256"] == sha256_bytes(canonical_bytes(before_outbox))
            and outbox_targets[0]["after_sha256"] == sha256_bytes(canonical_bytes(after_outbox))
        )
        if outbox_ok:
            for document in (before_outbox, after_outbox):
                entries = document["entries"]
                if [row["sequence"] for row in entries] != list(range(1, len(entries) + 1)):
                    outbox_ok = False
                    break
                for row in entries:
                    if not (
                        validate_registered(row["intent"], schema, registry, "status-mutation-intent/1.0.0", schema_sha, registry_sha)
                        and row["intent_id"] == sha256_bytes(canonical_bytes(row["intent"]))
                        and row["producer_id"] == row["intent"]["origin_producer"]
                        and row["workstream_id"] == row["intent"]["workstream_id"]
                        and row["field_set"] == sorted(row["intent"]["set"], key=lambda value: value.encode("utf-8"))
                    ):
                        outbox_ok = False
                        break
        if outbox_ok and outbox_mode == "emit":
            before_entries, after_entries = before_outbox["entries"], after_outbox["entries"]
            appended = after_entries[len(before_entries):]
            outbox_ok = bool(emitted_intents and after_entries[:len(before_entries)] == before_entries and len(appended) == len(emitted_intents))
            if outbox_ok:
                for entry, intent in zip(appended, emitted_intents):
                    if not (
                        entry["intent"] == intent and entry["intent_id"] == sha256_bytes(canonical_bytes(intent))
                        and entry["source_command_id"] == command["command_id"]
                        and entry["source_command_fingerprint"] == expected_auth["authorized_command_fingerprint"]
                        and entry["producer_id"] == command_producer(command)
                        and entry["status"] == "pending" and entry["attempts"] == 0
                        and entry["last_error"] is None and entry["consumed_receipt_id"] is None
                    ):
                        outbox_ok = False
                        break
        elif outbox_ok:
            before_entries, after_entries = before_outbox["entries"], after_outbox["entries"]
            consumed_ids = command.get("consumed_intent_ids", [])
            selected = {row["intent_id"]: row for row in before_entries if row["intent_id"] in set(consumed_ids)}
            complete_pending_ids = sorted(
                (
                    row["intent_id"]
                    for row in before_entries
                    if row["workstream_id"] == command["workstream_id"] and row["status"] == "pending"
                ),
                key=lambda value: value.encode("utf-8"),
            )
            outbox_ok = bool(
                consumed_ids == sorted(set(consumed_ids), key=lambda value: value.encode("utf-8"))
                and consumed_ids == complete_pending_ids
                and len(consumed_ids) > 0 and set(selected) == set(consumed_ids)
                and len(before_entries) == len(after_entries)
            )
            merged: dict[str, Any] = {}
            evidence_rows: list[dict[str, Any]] = []
            if outbox_ok:
                for before_entry, after_entry in zip(before_entries, after_entries):
                    stable = {"status", "attempts", "consumed_receipt_id"}
                    if {key: value for key, value in after_entry.items() if key not in stable} != {key: value for key, value in before_entry.items() if key not in stable}:
                        outbox_ok = False
                        break
                    if before_entry["intent_id"] not in selected:
                        if after_entry != before_entry:
                            outbox_ok = False
                            break
                        continue
                    intent = before_entry["intent"]
                    if not (
                        before_entry["status"] == "pending" and before_entry["consumed_receipt_id"] is None
                        and before_entry["workstream_id"] == command["workstream_id"]
                        and after_entry["status"] == "consumed"
                        and after_entry["attempts"] == before_entry["attempts"] + 1
                        and after_entry["consumed_receipt_id"] == receipt["receipt_id"]
                    ):
                        outbox_ok = False
                        break
                    for field, value in intent["set"].items():
                        if field in merged and canonical_bytes(merged[field]) != canonical_bytes(value):
                            outbox_ok = False
                            break
                        merged[field] = copy.deepcopy(value)
                    evidence_rows.extend(copy.deepcopy(intent["evidence"]))
            if outbox_ok:
                unique_evidence = {canonical_bytes(row): row for row in evidence_rows}
                expected_evidence = sorted(unique_evidence.values(), key=evidence_order_key)
                outbox_ok = (
                    merged == {key: value for key, value in command["set"].items() if key in STATUS_INTENT_FIELDS}
                    and command["evidence"] == expected_evidence
                )
    return (
        marker["state"] == "committed"
        and before_state["state_id"] == sha256_bytes(canonical_bytes({key: value for key, value in before_state.items() if key != "state_id"}))
        and after_state["state_id"] == sha256_bytes(canonical_bytes({key: value for key, value in after_state.items() if key != "state_id"}))
        and before_state["fact_generation"] == receipt["before_fact_generation"]
        and after_state["fact_generation"] == receipt["after_fact_generation"]
        and after_state["last_transaction_id"] == journal["transaction_id"]
        and state_target["root_instance_id"] == memory_root
        and state_target["path"] == registry["runtime_paths"]["fact_generation"]["path"]
        and state_target["before_sha256"] == sha256_bytes(canonical_bytes(before_state))
        and state_target["after_sha256"] == sha256_bytes(canonical_bytes(after_state))
        and len(matching_receipts) == 1 and matching_receipts[0]["root_instance_id"] == memory_root
        and matching_receipts[0]["after_sha256"] == sha256_bytes(canonical_bytes(receipt))
        and before_command_index["index_id"] == sha256_bytes(canonical_bytes({key: value for key, value in before_command_index.items() if key != "index_id"}))
        and command_index["index_id"] == sha256_bytes(canonical_bytes({key: value for key, value in command_index.items() if key != "index_id"}))
        and command_index["next_sequence"] == before_command_index["next_sequence"] + 1
        and command_index["entries"] == before_command_index["entries"] + [expected_index_entry]
        and len(index_targets) == 1
        and index_targets[0]["path"] == registry["runtime_paths"]["fact_command_receipt_index"]["path"]
        and index_targets[0]["before_sha256"] == sha256_bytes(canonical_bytes(before_command_index))
        and index_targets[0]["after_sha256"] == sha256_bytes(canonical_bytes(command_index))
        and outbox_ok
        and receipt["action_deltas"] == ([expected_action_delta(command)] if kind == "action" else [])
    )


def selection_policy_fixture(registry: dict[str, Any], schema_sha: str, registry_sha: str) -> dict[str, Any]:
    memory_root_id = "123e4567-e89b-42d3-a456-426614174000"
    def catalog_source(path: str, kind: str) -> dict[str, Any]:
        fingerprint = sha256_bytes(f"memory\0{path}".encode("utf-8"))
        return {"root": "memory", "root_instance_id": memory_root_id, "path": path, "category": "fact", "source_kind": kind, "fingerprint": fingerprint, "blob_id": fingerprint, "affects": ["/"]}
    catalog = [{
        "workstream_id": "l1-checkout",
        "wdr_source": catalog_source("workstreams/l1-checkout/delivery-record.md", "selected-physical-wdr"),
        "sidecar_source": catalog_source("workstreams/l1-checkout/action-projection.json", "wdr-action-sidecar"),
    }]
    snapshot_id = sha256_bytes(b"snapshot:2026-07-24T02:00:00Z")
    request = {
        "contract": expected_contract_ref(registry, "refresh-request/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "refresh_id": "refresh-snapshot-fixture",
        "requested_source_as_of": "2026-07-24T02:00:00Z", "requested_at": "2026-07-24T01:59:58Z",
    }
    request["request_id"] = sha256_bytes(canonical_bytes(request))
    lock_receipt = {
        "contract": expected_contract_ref(registry, "snapshot-lock-receipt/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "refresh_request_id": request["request_id"], "snapshot_id": snapshot_id,
        "lock_profile_id": registry["lock_profile"]["profile_id"], "root_registry_state_id": sha256_bytes(b"snapshot-root-registry"),
        "fact_generation": 7, "maximum_fact_observed_at": "2026-07-24T01:59:59Z",
        "source_as_of": "2026-07-24T02:00:00Z", "acquired_at": "2026-07-24T02:00:00Z",
    }
    lock_receipt["receipt_id"] = sha256_bytes(canonical_bytes(lock_receipt))
    policy = {
        "contract": {"schema_id": "urn:adp:panel-sync-contracts:2026-07-24#selection-policy-v1", "schema_sha256": schema_sha, "registry_sha256": registry_sha},
        "schema_version": "1.0.0", "snapshot_id": snapshot_id,
        "snapshot_lock_receipt_id": lock_receipt["receipt_id"],
        "physical_workstream_inventory": copy.deepcopy(catalog), "physical_workstream_inventory_id": canonical_inventory_id(catalog),
        "workstream_catalog": catalog, "workstream_catalog_id": canonical_catalog_id(catalog),
        "include_workstreams": "all", "exclude_workstreams": [],
        "meeting_kinds": ["business-biweekly", "fde-morning"], "as_of": "2026-07-24T02:00:00Z", "previous_program_status_id": None,
    }
    policy["policy_id"] = sha256_bytes(canonical_bytes(policy))
    return policy


def physical_inventory_fixture(
    registry: dict[str, Any], policy: dict[str, Any], fact_generation: int, schema_sha: str, registry_sha: str,
) -> dict[str, Any]:
    inventory = {
        "contract": expected_contract_ref(registry, "physical-workstream-inventory/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "memory_root_instance_id": "123e4567-e89b-42d3-a456-426614174000",
        "fact_generation": fact_generation, "workstreams": copy.deepcopy(policy["physical_workstream_inventory"]),
        "inventory_id": policy["physical_workstream_inventory_id"],
    }
    inventory["attestation_id"] = sha256_bytes(canonical_bytes(inventory))
    return inventory


def generation_fixture(
    registry: dict[str, Any], policy: dict[str, Any], schema_sha: str, registry_sha: str,
    raw_sources: dict[tuple[str, str], bytes] | None = None,
) -> dict[str, Any]:
    universe = [row["workstream_id"] for row in policy["workstream_catalog"]]
    included = universe if policy["include_workstreams"] == "all" else list(policy["include_workstreams"])
    selected = sorted(set(included) - set(policy["exclude_workstreams"]), key=lambda value: value.encode("utf-8"))
    by_leaf: dict[tuple[str, str], dict[str, Any]] = {}
    for profile in registry["projection_input_profiles"]:
        for source in materialize_profile_sources(profile, selected, policy, raw_sources):
            key = (source["root_instance_id"], source["path"])
            existing = by_leaf.get(key)
            if existing is not None and existing != source:
                raise ValueError(f"conflicting physical leaf metadata: {key}")
            by_leaf[key] = source
    for row in policy["physical_workstream_inventory"]:
        for source in (row["wdr_source"], row["sidecar_source"]):
            by_leaf.setdefault((source["root_instance_id"], source["path"]), copy.deepcopy(source))
    catalog = panel_binding_catalog(registry, schema_sha, registry_sha)
    envelope = {
        "contract": {"schema_id": "urn:adp:panel-sync-contracts:2026-07-24#generation-envelope-v1", "schema_sha256": schema_sha, "registry_sha256": registry_sha},
        "schema_version": "1.0.0", "fact_generation": 7, "selection_policy_id": policy["policy_id"],
        "physical_workstream_inventory_id": policy["physical_workstream_inventory_id"], "workstream_catalog_id": policy["workstream_catalog_id"], "panel_catalog_id": catalog["catalog_id"],
        "roots": [
            {"root": "memory", "root_instance_id": "123e4567-e89b-42d3-a456-426614174000"},
            {"root": "project", "root_instance_id": "123e4567-e89b-42d3-a456-426614174001"},
        ],
        "leaf_sources": sorted(by_leaf.values(), key=lambda row: (row["root_instance_id"], row["path"])),
    }
    envelope["generation_id"] = sha256_bytes(canonical_bytes(envelope))
    return envelope


def panel_fixture(contract_vectors: list[dict[str, Any]], registry: dict[str, Any], schema_sha: str, registry_sha: str, project_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    instances = {item["id"]: item["instance"] for item in contract_vectors if item.get("expected_valid")}
    audit = copy.deepcopy(instances["state-audit-payload-schema-valid"])
    status = copy.deepcopy(instances["program-status-payload-schema-valid"])
    status["progress"] = json.loads((project_root / "skills/adp-program-status/assets/fixtures/progress-v3/golden-measurable-boundary.json").read_text(encoding="utf-8"))
    roadmap = copy.deepcopy(instances["roadmap-payload-schema-valid"])
    meeting = copy.deepcopy(instances["meeting-pack-payload-schema-valid"])
    business_meeting = copy.deepcopy(meeting)
    business_meeting["scenario"] = "business-biweekly"
    business_meeting["meeting_pack_id"] = "sha256:" + "7" * 63 + "8"
    compatibility_path = project_root / "_bmad-output/planning-artifacts/architecture/architecture-bmad-ai-delivery-pmo-2026-07-24/contracts/fixtures/PANEL-V1-COMPATIBILITY.json"
    compatibility = json.loads(compatibility_path.read_text(encoding="utf-8"))
    composition_inputs = copy.deepcopy(compatibility["composition_inputs"])
    status.setdefault("extensions", {})["panel_v1_source"] = copy.deepcopy(composition_inputs["program_status"])
    status["overall_status"] = composition_inputs["program_status"]["overall_status"]
    roadmap.setdefault("extensions", {})["panel_v1_source"] = copy.deepcopy(composition_inputs["roadmap"])
    meeting.setdefault("extensions", {})["panel_v1_source"] = copy.deepcopy(composition_inputs["meeting_packs"]["fde-morning"])
    business_meeting.setdefault("extensions", {})["panel_v1_source"] = copy.deepcopy(composition_inputs["meeting_packs"]["business-biweekly"])
    flow = copy.deepcopy(composition_inputs["flow_graph"])
    source_as_of = flow["state"]["as_of"]
    if any(scope["as_of"] != source_as_of for scope in flow["overlays"]["scopes"]):
        raise ValueError("Panel v1 compatibility flow has inconsistent source times")
    policy = selection_policy_fixture(registry, schema_sha, registry_sha)
    policy["as_of"] = source_as_of
    policy["snapshot_id"] = sha256_bytes(f"snapshot:{source_as_of}".encode("utf-8"))
    snapshot = snapshot_time_fixture(registry, schema_sha, registry_sha, policy, None)
    policy["snapshot_lock_receipt_id"] = snapshot["lock_receipt"]["receipt_id"]
    policy["policy_id"] = sha256_bytes(canonical_bytes({key: value for key, value in policy.items() if key != "policy_id"}))
    generation = generation_fixture(registry, policy, schema_sha, registry_sha)
    for document in (audit, status, roadmap, meeting, business_meeting):
        document["source_as_of"] = source_as_of
    audit["selection_policy_id"] = policy["policy_id"]
    audit["selected_workstreams"] = ["l1-checkout"]
    empty_outbox = {
        "contract": expected_contract_ref(registry, "mutation-intent-outbox/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "outbox_generation": 1, "entries": [],
    }
    empty_outbox["outbox_id"] = sha256_bytes(canonical_bytes(empty_outbox))
    convergence = {
        "contract": expected_contract_ref(registry, "intent-convergence-verdict/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "outbox_id": empty_outbox["outbox_id"],
        "evaluated_through_sequence": 0, "pending_intent_ids": [], "failed_intent_ids": [],
        "waived_intent_ids": [], "status": "converged",
    }
    convergence["verdict_id"] = sha256_bytes(canonical_bytes(convergence))
    audit["intent_convergence"] = convergence
    drift = {
        "contract": {"schema_id": "urn:adp:panel-sync-contracts:2026-07-24#action-projection-drift-verdict-v1", "schema_sha256": schema_sha, "registry_sha256": registry_sha},
        "schema_version": "1.0.0", "verdict_id": "sha256:" + "8" * 64,
        "generation_id": generation["generation_id"], "selection_policy_id": policy["policy_id"],
        "ledger_fingerprint": "sha256:" + "b" * 64, "selected_workstreams": ["l1-checkout"],
        "workstreams": [{"workstream_id": "l1-checkout", "wdr_fingerprint": "sha256:" + "c" * 64, "wdr_revision": 4, "file_generation": 7, "sidecar_fingerprint": "sha256:" + "d" * 64, "sidecar_ledger_fingerprint": "sha256:" + "b" * 64, "status": "in-sync", "action_diffs": [], "findings": [], "finding_ids": []}],
        "overall_status": "in-sync",
    }
    audit["repair"]["drift_verdict_id"] = drift["verdict_id"]
    skeleton = {
        "panel_schema_version": "2.0.0", "panel_id": "sha256:" + "f" * 64,
        "model_v1": copy.deepcopy(compatibility["model_v1"]),
        "sync": {
            "generation_id": generation["generation_id"], "selection_policy_id": policy["policy_id"], "source_as_of": source_as_of,
            "artifact_integrity": "pass", "business_freshness": "fresh", "publication_eligibility": "eligible", "canonical": {},
            "compatibility_inputs": {key: copy.deepcopy(composition_inputs[key]) for key in ("request", "history", "shareable_policy")},
        },
    }
    return skeleton, {"state-audit": audit, "action-projection-drift-verdict": drift, "program-status": status, "roadmap": roadmap, "flow-graph": flow, "meeting-pack": [meeting, business_meeting]}, compatibility, policy, generation


def registry_dag_semantics(registry: dict[str, Any], mutation: str = "none") -> bool:
    derived = sorted((upstream["kind"], profile["projection"]) for profile in registry["projection_input_profiles"] for upstream in profile["direct_upstreams"])
    declared = sorted((edge["from"], edge["to"]) for edge in registry["projection_dag"])
    if derived != declared or len(declared) != len(set(declared)):
        return False
    kind_adjacency: dict[str, set[str]] = {}
    kinds = {profile["projection"] for profile in registry["projection_input_profiles"]}
    indegree = {node: 0 for node in kinds}
    for source, target in declared:
        kind_adjacency.setdefault(source, set()).add(target)
        indegree[target] += 1
    ready = sorted((node for node, count in indegree.items() if count == 0), key=lambda value: value.encode("utf-8"))
    visited: list[str] = []
    while ready:
        node = ready.pop(0)
        visited.append(node)
        for target in sorted(kind_adjacency.get(node, set()), key=lambda value: value.encode("utf-8")):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=lambda value: value.encode("utf-8"))
    if len(visited) != len(kinds):
        return False

    instance_keys = {kind: (["business-biweekly", "fde-morning"] if kind == "meeting-pack" else [None]) for kind in kinds}
    node_name = lambda kind, key: f"{kind}@{key if key is not None else '-'}"
    nodes = {node_name(kind, key) for kind, keys in instance_keys.items() for key in keys}
    adjacency: dict[str, set[str]] = {node: set() for node in nodes}
    direct_upstreams: dict[str, set[str]] = {node: set() for node in nodes}
    for source_kind, target_kind in declared:
        for source_key in instance_keys[source_kind]:
            for target_key in instance_keys[target_kind]:
                source, target = node_name(source_kind, source_key), node_name(target_kind, target_key)
                adjacency[source].add(target)
                direct_upstreams[target].add(source)
    ordered_nodes = [node_name(kind, key) for kind in visited for key in instance_keys[kind]]
    profiles = {row["projection"]: row for row in registry["projection_input_profiles"]}
    leaf_inputs: dict[str, set[str]] = {}
    for kind, keys in instance_keys.items():
        for key in keys:
            node = node_name(kind, key)
            for source in profiles[kind]["required_sources"]:
                identity = {"category": source["category"], "source_kind": source["source_kind"], "enumerator": source["enumerator"]}
                if kind == "meeting-pack" and source["enumerator"]["id"] == "selected-receipts-v1":
                    identity["instance_key"] = key
                leaf = "leaf:" + sha256_bytes(canonical_bytes(identity))
                leaf_inputs.setdefault(node, set()).add(leaf)

    baseline_leaf_ids = {leaf: sha256_bytes(leaf.encode()) for values in leaf_inputs.values() for leaf in values}
    baseline_ids: dict[str, str] = {}
    recorded_inputs: dict[str, dict[str, str]] = {}
    for node in ordered_nodes:
        inputs = {name: baseline_leaf_ids[name] for name in sorted(leaf_inputs[node])}
        inputs.update({name: baseline_ids[name] for name in sorted(direct_upstreams[node])})
        recorded_inputs[node] = inputs
        baseline_ids[node] = sha256_bytes(canonical_bytes({"instance": node, "inputs": inputs}))

    def descendants(seeds: set[str]) -> set[str]:
        result: set[str] = set()
        pending = list(seeds)
        while pending:
            for item in adjacency.get(pending.pop(), set()):
                if item not in result and item not in seeds:
                    result.add(item)
                    pending.append(item)
        return result

    changes: list[tuple[str, set[str]]] = [(node, {node}) for node in ordered_nodes]
    for leaf in sorted(baseline_leaf_ids):
        owners = {node for node, leaves in leaf_inputs.items() if leaf in leaves}
        changes.append((leaf, owners))
    for changed, seeds in changes:
        expected = set(seeds) | descendants(seeds)
        if changed in nodes:
            expected.remove(changed)
        current_leaf_ids = dict(baseline_leaf_ids)
        current_ids = dict(baseline_ids)
        if changed in nodes:
            current_ids[changed] = sha256_bytes(canonical_bytes({"instance": changed, "previous": baseline_ids[changed]}))
        else:
            current_leaf_ids[changed] = sha256_bytes(canonical_bytes({"leaf": changed, "previous": baseline_leaf_ids[changed]}))
        invalidated: set[str] = set()
        for node in ordered_nodes:
            if node == changed:
                continue
            inputs = {name: current_leaf_ids[name] for name in sorted(leaf_inputs[node])}
            inputs.update({name: current_ids[name] for name in sorted(direct_upstreams[node])})
            if inputs != recorded_inputs[node]:
                invalidated.add(node)
                if mutation != "stop-after-direct":
                    current_ids[node] = sha256_bytes(canonical_bytes({"instance": node, "inputs": inputs, "revision": "recomputed"}))
        if invalidated != expected:
            return False
    meeting_a, meeting_b = node_name("meeting-pack", "business-biweekly"), node_name("meeting-pack", "fde-morning")
    if meeting_b in descendants({meeting_a}) or meeting_a in descendants({meeting_b}) or descendants({meeting_a}) != {node_name("management-panel", None)}:
        return False
    return True


SEMANTIC_VALIDATOR_SPECS = {
    "panel-publication-eligibility/1.0.0": {
        "scope": ["physical-workstream-inventory/1.0.0", "selection-policy/1.0.0", "panel-binding-catalog/1.0.0", "generation-envelope/1.0.0", "management-panel-payload/2.0.0", "state-audit-payload/2.0.0", "action-projection-drift-verdict/1.0.0", "projection-dependency-manifest/1.0.0", "producer-receipt/1.0.0", "writer-fence-migration-attestation/1.0.0"],
        "algorithm": "fresh-physical-inventory-attestation-byte-equal-policy-catalog-generation-bidirectional-first-nonempty-selection-and-generation-audit-drift-manifest-receipt-exact-scope-plus-strict-writer-fence-gate",
    },
    "projection-registry-closure/1.0.0": {
        "scope": ["dependency_enumerators", "projection_input_profiles", "projection_dag", "canonical_array_ordering", "identity_set_fields", "semantic_sequence_fields", "runtime_paths", "wdr_field_section_map", "owned_fact_target_profiles", "source_time_bindings", "live_inspect_read_profile"],
        "algorithm": "execute-all-enumerators-and-leaf-plus-instance-expanded-changed-input-dag-invalidation-and-exact-read-sets-typed-all-ordering-rules-owned-fact-targets-source-times-and-runtime-path-known-answers-from-registry",
    },
    "fact-receipt-attribution/1.0.0": {
        "scope": ["runtime-authority-context/1.0.0", "root-registry-state/1.0.0", "writer-capability-registry/1.0.0", "strict-activation-state/1.0.0", "writer-fence-migration-attestation/1.0.0", "action-ledger-mutation/2.0.0", "owned-fact-command/1.0.0", "action-ledger-state/1.0.0", "action-flow-index/1.0.0", "wdr-mutation/1.0.0", "wdr-file-state/1.0.0", "wdr-action-projection/1.0.0", "transaction-journal-manifest/1.0.0", "journal-marker/1.0.0", "fact-generation-state/1.0.0", "fact-mutation-receipt/1.0.0", "fact-mutation-proof/1.0.0", "strict_rollout", "runtime_authority_profile", "runtime_paths", "lock_profile", "wdr_field_section_map", "owned_fact_target_profiles"],
        "algorithm": "exact-contract-negotiation-one-discriminated-command-independent-native-runtime-authority-context-bound-under-lock-to-current-root-activation-attestation-capability-bytes-active-host-capability-and-command-derived-action-ledger-renderer-wdr-or-registry-allowlisted-owned-fact-after-state-plus-exact-target-cas-before-after-and-read-byte-proof-bound-transaction",
    },
    "owned-fact-command-semantics/1.0.0": {
        "scope": ["owned-fact-command/1.0.0", "owned_fact_target_profiles", "runtime-authority-context/1.0.0", "root-registry-state/1.0.0", "writer-capability-registry/1.0.0", "strict-activation-state/1.0.0", "writer-fence-migration-attestation/1.0.0", "fact-mutation-receipt/1.0.0", "fact-mutation-proof/1.0.0", "runtime_authority_profile", "lock_profile", "runtime_paths"],
        "algorithm": "registry-profile-exact-producer-operation-root-path-content-schema-cas-before-after-generation-journal-receipt-and-restart",
    },
    "transaction-journal-semantics/1.0.0": {
        "scope": ["transaction-journal-manifest/1.0.0", "journal-marker/1.0.0", "runtime_paths"],
        "algorithm": "transaction-kind-role-closure-journal-local-images-contiguous-order-unique-target-exact-receipts-and-terminal-marker",
    },
    "repair-graph-semantics/1.0.0": {
        "scope": ["audit-finding-repair/2.0.0", "runtime-authority-context/1.0.0", "writer-capability-registry/1.0.0", "strict-activation-state/1.0.0", "writer-fence-migration-attestation/1.0.0", "wdr-mutation/1.0.0", "wdr-file-state/1.0.0", "wdr-action-projection/1.0.0", "repair-dry-run-request/1.0.0", "repair-dry-run-result/1.0.0", "repair-apply-request/1.0.0", "repair-run-receipt/1.0.0", "repair-nonce-state/1.0.0", "repair-receipt-index/1.0.0", "transaction-journal-manifest/1.0.0", "recovery-receipt/1.0.0", "fact-generation-state/1.0.0", "fact-mutation-receipt/1.0.0", "fact-mutation-proof/1.0.0", "strict_rollout", "runtime_authority_profile", "runtime_paths", "lock_profile", "wdr_field_section_map", "identity_set_fields"],
        "algorithm": "validate-contract-bound-identity-set-canonical-blocked-invalidated-rolled-back-or-committed-repair-graph-durable-lookup-index-and-reuse-independent-native-runtime-authority-fact-attribution-for-refresh-actions-wdr-only-effects",
    },
    "release-evidence-transition-semantics/1.0.0": {
        "scope": ["release-evidence-set/1.0.0", "release-evidence-transition-receipt/1.0.0", "release-evidence-history-index/1.0.0", "transaction-journal-manifest/1.0.0", "journal-marker/1.0.0", "runtime_paths", "runtime_policy", "evidence_trust"],
        "algorithm": "current-set-generation-cas-stage-journal-commit-history-chain-scoped-retention-and-restart-recovery",
    },
    "activation-transition-semantics/1.0.0": {
        "scope": ["activation-transition-command/1.0.0", "activation-transition-receipt/1.0.0", "runtime-authority-context/1.0.0", "root-registry-state/1.0.0", "strict-activation-state/1.0.0", "writer-capability-registry/1.0.0", "writer-fence-migration-attestation/1.0.0", "refresh-run-receipt/1.0.0", "transaction-journal-manifest/1.0.0", "journal-marker/1.0.0", "runtime_authority_profile", "lock_profile", "runtime_paths", "strict_rollout"],
        "algorithm": "rollback-reprovision-record-refresh-attest-enable-ordered-epoch-cas-exact-target-journal-receipt-and-crash-recovery",
    },
    "panel-publication-graph/1.0.0": {
        "scope": ["transaction-journal-manifest/1.0.0", "journal-marker/1.0.0", "panel-current-pointer/1.0.0", "panel-state/1.0.0", "panel-publication-receipt/1.0.0", "canonical-projection-envelope/1.0.0", "runtime_paths"],
        "algorithm": "independent-cardinality-and-registry-derived-generation-kind-instance-token-exact-target-path-before-after-state-pointer-bound-publication",
    },
    "panel-binding-semantics/1.0.0": {
        "scope": ["panel-binding-catalog/1.0.0", "canonical-projection-envelope/1.0.0", "management-panel-payload/2.0.0"],
        "algorithm": "resolve-every-binding-from-exact-same-generation-upstream-envelope-and-compare-panel-target",
    },
    "panel-v1-same-generation-composition/1.0.0": {
        "scope": ["management-panel-payload/2.0.0", "management-panel-current-view/2.0.0", "management-panel-model/1.0.0", "management-panel-manifest/1.0.0", "panel_v1_composition", "panel_v2_consumer"],
        "algorithm": "recompose-legacy-aggregate-model-from-canonical-overlay-and-compatibility-corpus;execute-pinned-v2-current-consumer-from-sync-only",
    },
    "status-intent-application/1.0.0": {
        "scope": ["status-mutation-intent/1.0.0", "status-sync-batch/2.0.0", "wdr-mutation/1.0.0"],
        "algorithm": "exact-intent-id-content-field-evidence-workstream-to-single-reauthorized-wdr-command-binding-reject-conflicts-and-ordered-stop-on-first-failure-no-rollback",
    },
    "meeting-plan-intent-carriers/1.0.0": {
        "scope": ["meeting-sync-plan/2.0.0", "producer-intent-outbox-command/1.0.0", "status-mutation-intent/1.0.0"],
        "algorithm": "meeting-plan-intents-equal-exact-deduplicated-command-carried-intents-with-same-meeting-origin-workstream-evidence-and-canonical-bytes-including-zero-history",
    },
    "program-status-current-from-wdr/1.0.0": {
        "scope": ["program-status-payload/2.0.0", "wdr-file-state/1.0.0", "selection-policy/1.0.0"],
        "algorithm": "parse-complete-selected-wdr-current-labels-and-require-exact-program-status-workstream-row-plus-wdr-fingerprint-revision-generation",
    },
    "action-projection-drift-content/1.0.0": {
        "scope": ["action-ledger-state/1.0.0", "wdr-file-state/1.0.0", "wdr-action-projection/1.0.0", "action-projection-drift-verdict/1.0.0", "selection-policy/1.0.0"],
        "algorithm": "exact-active-ledger-routing-or-affected-membership-to-sidecar-record-rendered-wdr-summary-and-verdict-fingerprints-no-false-green",
    },
    "bootstrap-migration-attribution/1.0.0": {
        "scope": ["bootstrap-migration-command/1.0.0", "action-ledger-state/1.0.0", "action-flow-index/1.0.0", "wdr-file-state/1.0.0", "wdr-action-projection/1.0.0", "writer-capability-registry/1.0.0", "transaction-journal-manifest/1.0.0", "journal-marker/1.0.0", "fact-generation-state/1.0.0", "fact-mutation-receipt/1.0.0", "fact-mutation-proof/1.0.0", "runtime_paths"],
        "algorithm": "declared-pinned-absent-or-legacy12-or-legacy20-and-legacy-wdr-preimages-to-command-derived-mixed-create-replace-canonical-state-flow-sidecar-byte-proof-journal-receipt",
    },
    "strict-writer-fence-activation/1.0.0": {
        "scope": ["writer-fence-migration-attestation/1.0.0", "release-evidence-set/1.0.0", "release-evidence-transition-receipt/1.0.0", "release-evidence-history-index/1.0.0", "conformance-result/1.0.0", "writer-build-manifest/1.0.0", "writer-fence-receipt/1.0.0", "generation-lineage-index/1.0.0", "publication-absence-proof/1.0.0", "strict-activation-state/1.0.0", "root-registry-state/1.0.0", "writer-capability-registry/1.0.0", "fact-generation-state/1.0.0", "action-ledger-state/1.0.0", "action-flow-index/1.0.0", "wdr-file-state/1.0.0", "wdr-action-projection/1.0.0", "refresh-run-receipt/1.0.0", "panel-publication-receipt/1.0.0", "panel-current-pointer/1.0.0", "panel-state/1.0.0", "strict_rollout", "runtime_paths", "lock_profile", "runtime_policy", "evidence_trust", "source_time_bindings"],
        "algorithm": "open-inspect-publish-all-require-external-trusted-evaluation-time-durable-journaled-content-addressed-release-evidence-history-and-current-set-and-current-byte-derived-writer-build-capability-lock-fact-ledger-wdr-sidecar-full-content-addressed-lineage-projection-panel-publication-pointer-activation-state-binding-exact-match",
    },
    "live-inspect-semantics/1.0.0": {
        "scope": ["strict-writer-fence-activation/1.0.0", "writer-fence-migration-attestation/1.0.0", "release-evidence-set/1.0.0", "release-evidence-history-index/1.0.0", "conformance-result/1.0.0", "writer-build-manifest/1.0.0", "writer-fence-receipt/1.0.0", "strict-activation-state/1.0.0", "root-registry-state/1.0.0", "writer-capability-registry/1.0.0", "action-ledger-state/1.0.0", "action-flow-index/1.0.0", "wdr-file-state/1.0.0", "wdr-action-projection/1.0.0", "refresh-run-receipt/1.0.0", "panel-publication-receipt/1.0.0", "panel-state/1.0.0", "generation-lineage-index/1.0.0", "publication-absence-proof/1.0.0", "panel-refresh-status/1.0.0", "panel-current-pointer/1.0.0", "fact-generation-state/1.0.0", "generation-envelope/1.0.0", "projection-dependency-manifest/1.0.0", "producer-receipt/1.0.0", "canonical-projection-envelope/1.0.0", "management-panel-payload/2.0.0", "strict_rollout", "runtime_paths", "lock_profile", "runtime_policy", "evidence_trust", "live_inspect_read_profile", "source_time_bindings"],
        "algorithm": "compose-and-execute-complete-registered-strict-writer-fence-gate-with-external-trusted-time-then-restart-safe-instrumented-resolve-pointer-generation-index-load-canonical-raw-bytes-under-fact-shared-lock-reenumerate-leaves-compare-actual-root-path-contract-read-set-before-read-lock-release-then-write-only-refresh-status",
    },
}

SEMANTIC_VALIDATOR_SPECS.update(json.loads(r'''{"panel-publication-eligibility/1.0.0":{"scope":["physical-workstream-inventory/1.0.0","selection-policy/1.0.0","panel-binding-catalog/1.0.0","generation-envelope/1.0.0","management-panel-payload/2.0.0","state-audit-payload/2.0.0","intent-convergence-verdict/1.0.0","action-projection-drift-verdict/1.0.0","projection-dependency-manifest/1.0.0","producer-receipt/1.0.0","writer-fence-migration-attestation/1.0.0"],"algorithm":"fresh-physical-inventory-attestation-byte-equal-policy-catalog-generation-bidirectional-first-nonempty-selection-and-generation-audit-drift-intent-convergence-manifest-receipt-exact-scope-plus-strict-writer-fence-gate"},"fact-receipt-attribution/1.0.0":{"scope":["runtime-authority-context/1.0.0","root-registry-state/1.0.0","writer-capability-registry/1.0.0","strict-activation-state/1.0.0","writer-fence-migration-attestation/1.0.0","action-ledger-mutation/2.0.0","owned-fact-command/1.0.0","status-mutation-intent/1.0.0","action-ledger-state/1.0.0","action-flow-index/1.0.0","wdr-mutation/1.0.0","wdr-file-state/1.0.0","wdr-action-projection/1.0.0","fact-command-receipt-index/1.0.0","mutation-intent-outbox/1.0.0","transaction-journal-manifest/1.0.0","journal-marker/1.0.0","fact-generation-state/1.0.0","fact-mutation-receipt/1.0.0","fact-mutation-proof/1.0.0","strict_rollout","runtime_authority_profile","runtime_paths","lock_profile","wdr_field_section_map","owned_fact_target_profiles"],"algorithm":"exact-contract-negotiation-typed-native-preimage-authority-command-derived-targets-byte-proof-command-receipt-index-plus-command-bound-exact-emitted-intents-or-complete-sorted-aggregated-consumed-intent-set-in-one-recoverable-fact-transaction"},"repair-graph-semantics/1.0.0":{"scope":["action-projection-drift-verdict/1.0.0","audit-finding-repair/2.0.0","action-ledger-state/1.0.0","runtime-authority-context/1.0.0","writer-capability-registry/1.0.0","strict-activation-state/1.0.0","writer-fence-migration-attestation/1.0.0","wdr-mutation/1.0.0","wdr-file-state/1.0.0","wdr-action-projection/1.0.0","repair-dry-run-request/1.0.0","repair-dry-run-result/1.0.0","repair-apply-request/1.0.0","repair-run-receipt/1.0.0","repair-nonce-state/1.0.0","repair-receipt-index/1.0.0","repair-attempt-ledger/1.0.0","transaction-journal-manifest/1.0.0","journal-marker/1.0.0","recovery-receipt/1.0.0","fact-generation-state/1.0.0","fact-mutation-receipt/1.0.0","fact-mutation-proof/1.0.0","strict_rollout","runtime_authority_profile","runtime_paths","lock_profile","wdr_field_section_map","identity_set_fields"],"algorithm":"derive-lossless-typed-audit-findings-from-exact-validated-drift-reparse-raw-ledger-state-wdr-and-sidecar-to-prove-presence-revision-and-diffs-then-separate-business-and-attempt-journals-with-deterministic-terminal-marker-bound-attempt-identity-and-idempotent-registered-path-recovery"},"release-evidence-transition-semantics/1.0.0":{"scope":["release-evidence-set/1.0.0","release-evidence-transition-receipt/1.0.0","release-evidence-history-index/1.0.0","transaction-journal-manifest/1.0.0","journal-marker/1.0.0","recovery-receipt/1.0.0","runtime_paths","runtime_policy","evidence_trust"],"algorithm":"validate-every-historical-receipt-blob-signature-policy-and-monotonic-chronology-plus-content-addressed-journal-marker-chain-and-fresh-process-image-recovery"},"activation-transition-semantics/1.0.0":{"scope":["activation-transition-command/1.0.0","activation-transition-receipt/1.0.0","activation-lifecycle-index/1.0.0","runtime-authority-context/1.0.0","root-registry-state/1.0.0","strict-activation-state/1.0.0","writer-capability-registry/1.0.0","writer-fence-migration-attestation/1.0.0","refresh-run-receipt/1.0.0","transaction-journal-manifest/1.0.0","journal-marker/1.0.0","recovery-receipt/1.0.0","runtime_authority_profile","lock_profile","runtime_paths","strict_rollout"],"algorithm":"recompute-fixed-five-step-lifecycle-id-first-step-creates-index-later-steps-exact-prefix-cas-each-entry-derived-from-committed-receipt-raw-hash-and-registered-path-plus-state-attestation-cas-and-fresh-process-recovery"},"panel-publication-graph/1.0.0":{"scope":["transaction-journal-manifest/1.0.0","journal-marker/1.0.0","generation-lineage-index/1.0.0","panel-current-pointer/1.0.0","panel-state/1.0.0","panel-publication-receipt/1.0.0","canonical-projection-envelope/1.0.0","runtime_paths"],"algorithm":"complete-immutable-lineage-and-index-in-same-journal-before-pointer-last-plus-durable-command-fingerprint-replay-lookup-and-fresh-process-recovery"},"status-intent-application/1.0.0":{"scope":["status-mutation-intent/1.0.0","status-sync-batch/2.0.0","wdr-mutation/1.0.0","mutation-intent-outbox/1.0.0"],"algorithm":"exact-canonical-intent-hash-content-field-evidence-workstream-to-single-reauthorized-wdr-command-and-complete-sorted-consumed-id-binding-reject-conflicts-and-ordered-stop-on-first-failure-no-rollback"},"action-projection-drift-content/1.0.0":{"scope":["action-ledger-state/1.0.0","wdr-file-state/1.0.0","wdr-action-projection/1.0.0","action-projection-drift-verdict/1.0.0","audit-finding-repair/2.0.0","selection-policy/1.0.0"],"algorithm":"exact-active-ledger-routing-or-affected-membership-to-sidecar-record-rendered-wdr-summary-and-lossless-content-addressed-typed-finding-verdict-no-false-green"},"strict-writer-fence-activation/1.0.0":{"scope":["writer-fence-migration-attestation/1.0.0","release-evidence-set/1.0.0","release-evidence-transition-receipt/1.0.0","release-evidence-history-index/1.0.0","conformance-result/1.0.0","writer-build-manifest/1.0.0","writer-fence-receipt/1.0.0","strict-activation-state/1.0.0","root-registry-state/1.0.0","writer-capability-registry/1.0.0","strict_rollout","runtime_paths","lock_profile","runtime_policy","evidence_trust"],"algorithm":"external-trusted-time-and-durable-release-authority-plus-current-root-capability-epoch-writer-build-and-fence-byte-closure-authorize-immutable-writer-fence-only-while-mutable-facts-pointer-lineage-and-panel-generation-are-live-receipt-cas-validated"},"snapshot-time-authority/1.0.0":{"scope":["refresh-request/1.0.0","snapshot-lock-receipt/1.0.0","selection-policy/1.0.0","refresh-run-receipt/1.0.0","source_time_bindings","lock_profile"],"algorithm":"request-time-host-time-lock-acquisition-maximum-fact-time-and-all-registered-source-time-carriers-equal-one-trusted-snapshot-boundary"},"intent-outbox-convergence/1.0.0":{"scope":["mutation-intent-outbox/1.0.0","intent-convergence-verdict/1.0.0","status-mutation-intent/1.0.0","status-sync-batch/2.0.0","wdr-mutation/1.0.0","fact-mutation-receipt/1.0.0","state-audit-payload/2.0.0","management-panel-payload/2.0.0"],"algorithm":"producer-command-binds-exact-typed-intent-by-canonical-hash-and-aggregated-status-sync-command-atomically-consumes-complete-sorted-same-workstream-pending-set-with-one-receipt-while-prefix-preserving-unrelated-rows-and-only-pending-or-consumed-states-are-allowed-failed-waived-arrays-are-empty-and-any-pending-blocks-fresh-eligible"},"fact-command-replay/1.0.0":{"scope":["fact-command-receipt-index/1.0.0","fact-mutation-receipt/1.0.0","transaction-journal-manifest/1.0.0","runtime_paths"],"algorithm":"global-monotonic-sequence-command-id-plus-fingerprint-to-exact-receipt-path-same-fingerprint-noop-different-fingerprint-conflict"}}'''))


SEMANTIC_VALIDATOR_SPECS_SHA256 = "sha256:506f6079cf7197921c74d5b98f170181b0872009c1412ebd521361d6d0e887f5"


def semantic_registry_semantics(registry: dict[str, Any]) -> bool:
    rows = registry.get("semantic_validators")
    if not isinstance(rows, list):
        return False
    ids = [row.get("id") for row in rows]
    return (
        len(ids) == len(set(ids)) == 21
        and all(isinstance(row.get("scope"), list) and row["scope"] and isinstance(row.get("algorithm"), str) and row["algorithm"] for row in rows)
        and sha256_bytes(canonical_bytes(rows)) == SEMANTIC_VALIDATOR_SPECS_SHA256
    )


def runtime_paths_semantics(registry: dict[str, Any]) -> bool:
    expected_keys = {
        "journal_dir_template", "action_ledger", "action_ledger_state", "action_flow_index", "fact_generation",
        "fact_command_receipt_index", "mutation_intent_outbox", "intent_convergence_verdict",
        "root_registry_state", "writer_capability_registry", "panel_current_pointer", "panel_state", "strict_activation_state", "fact_receipt_template", "panel_receipt_template",
        "repair_fact_receipt_template", "repair_receipt_template", "canonical_projection_template", "management_panel_template",
        "writer_fence_attestation", "fact_lock", "panel_lock", "panel_refresh_status", "generation_lineage_index_template",
        "generation_envelope_template", "selection_policy_template", "physical_inventory_template", "panel_binding_catalog_template",
        "dependency_manifest_template", "producer_receipt_template", "refresh_receipt_generation_template",
        "publication_receipt_generation_template", "publication_journal_template", "publication_marker_template",
        "before_pointer_template", "before_panel_state_template",
        "journal_manifest_template", "journal_prepared_marker_template", "journal_terminal_marker_template",
        "journal_recovery_receipt_template", "journal_tombstone_template", "journal_before_image_template",
        "journal_after_image_template", "repair_nonce_template", "release_evidence_set",
        "release_evidence_receipt_template", "release_evidence_blob_template",
        "publication_absence_proof_template", "repair_receipt_index", "release_evidence_history_index",
        "release_evidence_set_archive_template", "release_evidence_transition_receipt_template",
        "release_evidence_journal_template", "release_evidence_terminal_marker_template",
        "activation_transition_receipt_template", "activation_transition_state_template", "activation_lifecycle_index_template",
        "repair_attempt_ledger", "refresh_request_template", "snapshot_lock_receipt_template",
    }
    paths = registry.get("runtime_paths")
    if not isinstance(paths, dict) or set(paths) != expected_keys or paths["journal_dir_template"] != "state/transactions/{transaction_token}":
        return False
    generation_id = "sha256:" + "a" * 64
    try:
        known = [
            runtime_path(registry, "canonical_projection_template", generation_id=generation_id, projection_kind="program-status", instance_key=None),
            runtime_path(registry, "canonical_projection_template", generation_id=generation_id, projection_kind="meeting-pack", instance_key="fde-morning"),
            runtime_path(registry, "management_panel_template", generation_id=generation_id, projection_kind="management-panel", instance_key=None),
            runtime_path(registry, "journal_manifest_template", transaction_id="tx-known-answer"),
            runtime_path(registry, "journal_before_image_template", transaction_id="tx-known-answer", apply_order=11),
            runtime_path(registry, "journal_after_image_template", transaction_id="tx-known-answer", apply_order=11),
            runtime_path(registry, "repair_nonce_template", nonce_id="sha256:" + "b" * 64),
            runtime_path(registry, "release_evidence_receipt_template", result_id="sha256:" + "c" * 64),
            runtime_path(registry, "release_evidence_blob_template", blob_id="sha256:" + "d" * 64),
            runtime_path(registry, "publication_absence_proof_template", generation_id=generation_id),
            runtime_path(registry, "release_evidence_set_archive_template", release_set_id="sha256:" + "e" * 64),
            runtime_path(registry, "release_evidence_transition_receipt_template", transaction_id="release-known-answer"),
            runtime_path(registry, "activation_transition_receipt_template", transaction_id="activation-known-answer"),
            runtime_path(registry, "activation_transition_state_template", transaction_id="activation-known-answer"),
        ]
        if len(known) != len(set(known)) or not all(path and "{" not in path for path in known):
            return False
        for name, record in paths.items():
            if name == "journal_dir_template":
                continue
            if not isinstance(record, dict) or record.get("root") != "memory" or set(record) != {"root", "path"}:
                return False
            candidate = record["path"]
            if candidate.startswith("/") or "\\" in candidate or ":" in candidate or ".." in candidate.split("/"):
                return False
        try:
            runtime_path(registry, "canonical_projection_template", generation_id="SHA256:" + "a" * 64, projection_kind="program-status", instance_key=None)
            return False
        except ValueError:
            pass
        try:
            runtime_path(registry, "canonical_projection_template", generation_id=generation_id, projection_kind="meeting-pack", instance_key="e\u0301")
            return False
        except ValueError:
            pass
    except (KeyError, TypeError, ValueError):
        return False
    return True


def enumerator_temp_tree_semantics() -> bool:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "visible").mkdir()
        (root / "visible" / "nested").mkdir()
        (root / ".hidden").mkdir()
        (root / "visible" / "a.json").write_text("a", encoding="utf-8")
        (root / "visible" / "nested" / "b.json").write_text("b", encoding="utf-8")
        (root / ".hidden" / "c.json").write_text("c", encoding="utf-8")
        (root / "visible" / "link.json").symlink_to(root / "visible" / "a.json")
        paths = []
        for path in root.glob("visible/**/*.json"):
            relative = path.relative_to(root).as_posix()
            if path.is_file() and not path.is_symlink() and not any(part.startswith(".") for part in Path(relative).parts):
                paths.append(unicodedata.normalize("NFC", relative))
        return sorted(paths, key=lambda value: value.encode("utf-8")) == ["visible/a.json", "visible/nested/b.json"]


def physical_inventory_rows_valid(rows: list[dict[str, Any]]) -> bool:
    if not rows or rows != sorted(rows, key=lambda row: row["workstream_id"].encode("utf-8")):
        return False
    workstream_ids: list[str] = []
    physical_ids: list[tuple[str, str]] = []
    for row in rows:
        workstream_id = row.get("workstream_id")
        if not isinstance(workstream_id, str) or workstream_id != unicodedata.normalize("NFC", workstream_id) or not re.fullmatch(r"(?!program$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", workstream_id):
            return False
        workstream_ids.append(workstream_id)
        expected = (
            ("wdr_source", f"workstreams/{workstream_id}/delivery-record.md", "selected-physical-wdr"),
            ("sidecar_source", f"workstreams/{workstream_id}/action-projection.json", "wdr-action-sidecar"),
        )
        for field, source_path, source_kind in expected:
            source = row.get(field)
            if not isinstance(source, dict) or source.get("root") != "memory" or source.get("path") != source_path or source.get("category") != "fact" or source.get("source_kind") != source_kind or source.get("affects") != ["/"]:
                return False
            physical_ids.append((source.get("root_instance_id"), source_path))
    return len(workstream_ids) == len(set(workstream_ids)) and len(physical_ids) == len(set(physical_ids))


def enumerate_physical_workstreams(
    memory_root: Path, memory_root_id: str, schema: dict[str, Any], registry: dict[str, Any], schema_sha: str, registry_sha: str,
) -> list[dict[str, Any]]:
    workstreams_root = memory_root / "workstreams"
    if not workstreams_root.is_dir() or workstreams_root.is_symlink():
        raise ValueError("physical workstream root is missing or unsafe")
    rows: list[dict[str, Any]] = []
    for folder in sorted(workstreams_root.iterdir(), key=lambda item: item.name.encode("utf-8")):
        if folder.name.startswith("."):
            if any(item.name in {"delivery-record.md", "action-projection.json"} for item in folder.rglob("*")):
                raise ValueError("hidden physical workstream")
            continue
        if not folder.is_dir() or folder.is_symlink():
            raise ValueError("physical workstream entry is not a regular directory")
        workstream_id = folder.name
        if workstream_id != unicodedata.normalize("NFC", workstream_id) or not re.fullmatch(r"(?!program$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", workstream_id):
            raise ValueError("invalid physical workstream identity")
        wdr = folder / "delivery-record.md"
        sidecar = folder / "action-projection.json"
        for nested in folder.rglob("*"):
            if nested.parent != folder and nested.name in {wdr.name, sidecar.name}:
                raise ValueError("nested physical workstream artifact")
            if nested.is_symlink():
                raise ValueError("symlinked physical workstream artifact")
        if not (wdr.is_file() and sidecar.is_file()) or wdr.is_symlink() or sidecar.is_symlink() or not os.access(wdr, os.R_OK) or not os.access(sidecar, os.R_OK):
            raise ValueError("unpaired or unreadable physical workstream")
        wdr_bytes = wdr.read_bytes()
        wdr_text = wdr_bytes.decode("utf-8")
        identity_matches = re.findall(r"(?m)^- Workstream ID: ([^\r\n]+)$", wdr_text)
        sidecar_bytes = sidecar.read_bytes()
        sidecar_value = json.loads(sidecar_bytes)
        if (
            identity_matches != [workstream_id]
            or not complete_wdr_valid(wdr_text, workstream_id)
            or not isinstance(sidecar_value, dict)
            or sidecar_value.get("workstream_id") != workstream_id
            or not validate_registered(sidecar_value, schema, registry, "wdr-action-projection/1.0.0", schema_sha, registry_sha)
            or sidecar_bytes != canonical_bytes(sidecar_value)
        ):
            raise ValueError("physical workstream content identity mismatch")

        def source(source_path: str, source_kind: str, raw: bytes) -> dict[str, Any]:
            digest = sha256_bytes(raw)
            return {"root": "memory", "root_instance_id": memory_root_id, "path": source_path, "category": "fact", "source_kind": source_kind, "fingerprint": digest, "blob_id": digest, "affects": ["/"]}

        rows.append({
            "workstream_id": workstream_id,
            "wdr_source": source(f"workstreams/{workstream_id}/delivery-record.md", "selected-physical-wdr", wdr_bytes),
            "sidecar_source": source(f"workstreams/{workstream_id}/action-projection.json", "wdr-action-sidecar", sidecar_bytes),
        })
    if not physical_inventory_rows_valid(rows):
        raise ValueError("physical workstream inventory is empty, duplicate, or noncanonical")
    return rows


def physical_workstream_inventory_temp_tree_semantics(
    mutation: str, schema: dict[str, Any], registry: dict[str, Any], schema_sha: str, registry_sha: str,
) -> bool:
    memory_root_id = "123e4567-e89b-42d3-a456-426614174000"
    with tempfile.TemporaryDirectory() as folder:
        memory_root = Path(folder)
        (memory_root / "workstreams").mkdir()
        workstream_ids = [] if mutation == "empty" else ["l1-checkout", "l1-payments"]
        for workstream_id in workstream_ids:
            target = memory_root / "workstreams" / workstream_id
            target.mkdir()
            wdr = fixture_wdr(workstream_id)
            sidecar = {
                "contract": expected_contract_ref(registry, "wdr-action-projection/1.0.0", schema_sha, registry_sha), "schema_version": "1.0.0",
                "workstream_id": workstream_id, "ledger_fingerprint": "sha256:" + "d" * 64, "ledger_revision": 4, "wdr_revision": 4, "file_generation": 7,
                "renderer_id": "urn:adp:wdr-action-renderer:1.0.0", "renderer_sha256": registry["protocol"]["sha256"], "actions": [],
            }
            if mutation == "invalid-wdr" and workstream_id == workstream_ids[-1]:
                wdr = f"# invalid except identity\n\n- Workstream ID: {workstream_id}\n"
            if mutation == "invalid-sidecar" and workstream_id == workstream_ids[-1]:
                sidecar = {"workstream_id": workstream_id}
            if mutation == "sidecar-fake-anchor" and workstream_id == workstream_ids[-1]:
                sidecar["contract"]["schema_id"] = "urn:adp:panel-sync-contracts:2026-07-24#unknown-sidecar-v1"
            if mutation == "sidecar-schema-hash" and workstream_id == workstream_ids[-1]:
                sidecar["contract"]["schema_sha256"] = "sha256:" + "f" * 64
            if mutation == "sidecar-registry-hash" and workstream_id == workstream_ids[-1]:
                sidecar["contract"]["registry_sha256"] = "sha256:" + "f" * 64
            if not (mutation == "sidecar-without-wdr" and workstream_id == workstream_ids[-1]):
                (target / "delivery-record.md").write_text(wdr, encoding="utf-8")
            if not (mutation == "wdr-without-sidecar" and workstream_id == workstream_ids[-1]):
                sidecar_bytes = canonical_bytes(sidecar)
                if mutation == "sidecar-noncanonical" and workstream_id == workstream_ids[-1]:
                    sidecar_bytes = json.dumps(sidecar, ensure_ascii=False, indent=2).encode("utf-8")
                (target / "action-projection.json").write_bytes(sidecar_bytes)
        try:
            rows = enumerate_physical_workstreams(memory_root, memory_root_id, schema, registry, schema_sha, registry_sha)
            if mutation == "duplicate-physical-identity":
                rows.append(copy.deepcopy(rows[0]))
            valid = physical_inventory_rows_valid(rows)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            valid = False
        return valid


def _enumerated_paths(source: dict[str, Any], selected: list[str], policy: dict[str, Any] | None = None) -> list[str]:
    enumerator = source["enumerator"]
    kind = enumerator["id"]
    if kind == "exact-path-v1":
        return [enumerator["path"]]
    if kind in {"selected-workstreams-v1", "selected-sidecars-v1", "selected-workstream-file-v1"}:
        return [f"{enumerator['base']}/{workstream_id}/{enumerator['filename']}" for workstream_id in selected]
    if kind == "selected-immutable-snapshot-v1":
        if policy is not None and policy.get("previous_program_status_id") is None:
            return []
        return [f"{enumerator['base']}/h_{'1' * 64}.json"]
    if kind == "selected-baseline-history-v1":
        return [f"{enumerator['base']}/revision-3.md"]
    if kind == "selected-receipts-v1":
        return [f"{enumerator['base']}/fde-morning.json"]
    if kind == "glob-kind-v1":
        suffix = ".json" if "json" in enumerator["glob"] else ".md"
        return [f"{enumerator['base']}/fixture-{source['source_kind']}{suffix}"]
    raise ValueError(f"unsupported dependency enumerator: {kind}")


def materialize_profile_sources(
    profile: dict[str, Any], selected: list[str], policy: dict[str, Any] | None = None,
    raw_sources: dict[tuple[str, str], bytes] | None = None,
) -> list[dict[str, Any]]:
    root_ids = {"memory": "123e4567-e89b-42d3-a456-426614174000", "project": "123e4567-e89b-42d3-a456-426614174001"}
    policy_sources: dict[tuple[str, str], dict[str, Any]] = {}
    if policy is not None:
        for collection_name in ("physical_workstream_inventory", "workstream_catalog"):
            for workstream in policy[collection_name]:
                for field in ("wdr_source", "sidecar_source"):
                    source = workstream[field]
                    key = (source["root_instance_id"], source["path"])
                    existing = policy_sources.get(key)
                    if existing is not None and existing != source:
                        raise ValueError(f"conflicting policy source metadata: {key}")
                    policy_sources[key] = copy.deepcopy(source)
    records: list[dict[str, Any]] = []
    for source in profile["required_sources"]:
        for source_path in _enumerated_paths(source, selected, policy):
            root = source["enumerator"]["root"]
            root_instance_id = root_ids[root]
            policy_source = policy_sources.get((root_instance_id, source_path))
            if policy_source is not None:
                expected_metadata = {
                    "root": root, "root_instance_id": root_instance_id, "path": source_path,
                    "category": source["category"], "source_kind": source["source_kind"],
                    "affects": sorted(source["affects"], key=lambda value: value.encode("utf-8")),
                }
                actual_metadata = {key: policy_source[key] for key in expected_metadata}
                if actual_metadata != expected_metadata:
                    raise ValueError(f"policy source does not match dependency declaration: {(root_instance_id, source_path)}")
                if raw_sources is not None and (raw := raw_sources.get((root, source_path))) is not None:
                    fingerprint = sha256_bytes(raw)
                    if policy_source["fingerprint"] != fingerprint or policy_source["blob_id"] != fingerprint:
                        raise ValueError(f"policy source fingerprint does not match consumed bytes: {(root_instance_id, source_path)}")
                records.append(policy_source)
                continue
            raw = (raw_sources or {}).get((root, source_path), f"{root}\0{source_path}".encode("utf-8"))
            fingerprint = sha256_bytes(raw)
            records.append({
                "root": root,
                "root_instance_id": root_instance_id,
                "path": source_path,
                "category": source["category"],
                "source_kind": source["source_kind"],
                "fingerprint": fingerprint,
                "blob_id": fingerprint,
                "affects": sorted(source["affects"], key=lambda value: value.encode("utf-8")),
            })
    identities = [(row["root_instance_id"], row["path"]) for row in records]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate physical source identity")
    return sorted(records, key=lambda row: (row["root_instance_id"], row["path"]))


def instrumented_read_trace(
    profile: dict[str, Any], selected: list[str], mutation: str = "none", policy: dict[str, Any] | None = None,
    raw_sources: dict[tuple[str, str], bytes] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed = materialize_profile_sources(profile, selected, policy, raw_sources)
    blob_store = {(row["root_instance_id"], row["path"]): copy.deepcopy(row) for row in allowed}
    read_plan = list(blob_store)
    if mutation == "drop-one-declared-read":
        read_plan = read_plan[:-1]
    elif mutation == "drop-action-ledger-state":
        read_plan = [key for key in read_plan if blob_store[key]["source_kind"] != "action-ledger-state"]
    actual: list[dict[str, Any]] = []
    for key in read_plan:
        actual.append(copy.deepcopy(blob_store[key]))
    if mutation == "add-undeclared-read":
        extra = copy.deepcopy(allowed[0])
        extra["path"] = f"undeclared/{profile['projection']}.json"
        extra["fingerprint"] = extra["blob_id"] = sha256_bytes(extra["path"].encode("utf-8"))
        actual.append(extra)
    elif mutation == "add-undeclared-ledger-state-read":
        extra = copy.deepcopy(next(row for row in allowed if row["source_kind"] == "action-ledger-state"))
        extra["path"] = "state/unregistered-action-ledger-shadow.json"
        extra["fingerprint"] = extra["blob_id"] = sha256_bytes(extra["path"].encode("utf-8"))
        actual.append(extra)
    actual.sort(key=lambda row: (row["root_instance_id"], row["path"]))
    return allowed, actual


def ordering_component(value: Any, key_type: str) -> tuple[int, Any]:
    if value is None:
        return (0, b"")
    if key_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("integer ordering component required")
        return (1, value)
    if not isinstance(value, str) or value != unicodedata.normalize("NFC", value):
        raise ValueError("NFC string ordering component required")
    return (1, value.encode("utf-8"))


def ordering_rule_key(value: Any, key_spec: str, key_types: list[str] | None = None) -> tuple[tuple[int, Any], ...]:
    fields = [None] if key_spec == "utf8-nfc-scalar" else key_spec.split(",")
    types = key_types or ["string"] * len(fields)
    if len(types) != len(fields) or any(kind not in {"string", "integer"} for kind in types):
        raise ValueError("ordering key types do not match key fields")
    if key_spec == "utf8-nfc-scalar":
        return (ordering_component(value, types[0]),)
    return tuple(ordering_component(value.get(key), kind) for key, kind in zip(fields, types))


def representative_ordering_documents(
    suite: dict[str, Any], schema: dict[str, Any], registry: dict[str, Any], project_root: Path, schema_sha: str, registry_sha: str,
) -> dict[str, dict[str, Any]]:
    contract = lambda anchor: {"schema_id": f"urn:adp:panel-sync-contracts:2026-07-24#{anchor}", "schema_sha256": schema_sha, "registry_sha256": registry_sha}
    valid_instances = {row["id"]: copy.deepcopy(row["instance"]) for row in suite["contract_schema_vectors"] if row["expected_valid"]}
    source = lambda name, root, path, kind, digit: {
        "root": root, "root_instance_id": "123e4567-e89b-42d3-a456-426614174000" if root == "memory" else "123e4567-e89b-42d3-a456-426614174001",
        "path": path, "category": "fact", "source_kind": kind, "fingerprint": "sha256:" + digit * 64,
        "blob_id": "sha256:" + digit * 64, "affects": [f"/{name}"],
    }
    policy = selection_policy_fixture(registry, schema_sha, registry_sha)
    second_catalog = copy.deepcopy(policy["workstream_catalog"][0])
    second_catalog["workstream_id"] = "l1-payments"
    for field, filename in (("wdr_source", "delivery-record.md"), ("sidecar_source", "action-projection.json")):
        source_row = second_catalog[field]
        source_row["path"] = f"workstreams/l1-payments/{filename}"
        source_row["fingerprint"] = source_row["blob_id"] = sha256_bytes(f"memory\0{source_row['path']}".encode("utf-8"))
    policy["workstream_catalog"].append(second_catalog)
    policy["physical_workstream_inventory"].append(copy.deepcopy(second_catalog))
    policy["physical_workstream_inventory_id"] = canonical_inventory_id(policy["physical_workstream_inventory"])
    policy["workstream_catalog_id"] = canonical_catalog_id(policy["workstream_catalog"])
    policy["include_workstreams"] = ["l1-checkout", "l1-payments"]
    policy["exclude_workstreams"] = ["l1-legacy", "l1-retired"]
    policy["meeting_kinds"] = ["business-biweekly", "fde-morning"]
    policy["policy_id"] = sha256_bytes(canonical_bytes({key: value for key, value in policy.items() if key != "policy_id"}))
    generation = generation_fixture(registry, policy, schema_sha, registry_sha)

    manifest = {
        "contract": contract("dependency-manifest-v1"), "schema_version": "1.0.0", "producer": {"skill": "adp-program-status", "version": "1.0.0"},
        "projection": {"kind": "program-status", "id": "sha256:" + "1" * 64}, "generation_id": generation["generation_id"],
        "input_profile_id": "program-status/1.0.0", "selection_policy_id": policy["policy_id"],
        "sources": [source("a", "memory", "a.md", "action-ledger", "2"), source("b", "memory", "b.md", "status-signals", "3")],
        "upstreams": [
            {"kind": "flow-graph", "id": "sha256:" + "4" * 64, "manifest_id": "sha256:" + "5" * 64, "generation_id": generation["generation_id"]},
            {"kind": "roadmap", "id": "sha256:" + "6" * 64, "manifest_id": "sha256:" + "7" * 64, "generation_id": generation["generation_id"]},
        ],
        "manifest_id": "sha256:" + "8" * 64,
    }
    projections = [
        {"kind": "action-projection-drift-verdict", "instance_key": None, "id": "sha256:" + "1" * 64, "manifest_id": "sha256:" + "2" * 64, "canonical_path": "g/action-drift.json"},
        {"kind": "flow-graph", "instance_key": None, "id": "sha256:" + "3" * 64, "manifest_id": "sha256:" + "4" * 64, "canonical_path": "g/flow.json"},
        {"kind": "management-panel", "instance_key": None, "id": "sha256:" + "5" * 64, "manifest_id": "sha256:" + "6" * 64, "canonical_path": "g/panel.json"},
        {"kind": "meeting-pack", "instance_key": None, "id": "sha256:" + "7" * 64, "manifest_id": "sha256:" + "8" * 64, "canonical_path": "g/meeting-none.json"},
        {"kind": "meeting-pack", "instance_key": "fde-morning", "id": "sha256:" + "9" * 64, "manifest_id": "sha256:" + "a" * 64, "canonical_path": "g/meeting-fde.json"},
        {"kind": "program-status", "instance_key": None, "id": "sha256:" + "b" * 64, "manifest_id": "sha256:" + "c" * 64, "canonical_path": "g/status.json"},
        {"kind": "roadmap", "instance_key": None, "id": "sha256:" + "d" * 64, "manifest_id": "sha256:" + "e" * 64, "canonical_path": "g/roadmap.json"},
        {"kind": "state-audit", "instance_key": None, "id": "sha256:" + "f" * 64, "manifest_id": "sha256:" + "0" * 64, "canonical_path": "g/audit.json"},
    ]
    pointer = {"contract": contract("panel-current-pointer-v1"), "schema_version": "1.0.0", "generation_id": generation["generation_id"], "panel_id": "sha256:" + "1" * 64, "projections": projections, "pointer_id": "sha256:" + "2" * 64}
    journal, _ = journal_fixture("panel", schema_sha, registry_sha, registry)
    journal["targets"] = [mutation_target("projection", "create", index, f"views/generations/g1/p{index}.json") for index in range(7)] + [
        mutation_target("panel", "create", 7, "views/management-panel/g1.json"),
        mutation_target("pointer", "replace", 8, "views/management-panel/current-pointer.json"),
        mutation_target("panel-state", "replace", 9, "state/panel-generation.json"),
        mutation_target("receipt", "create", 10, journal["receipt_target_paths"][0]),
    ]
    for target in journal["targets"]:
        if target["before_image"] is not None:
            target["before_image"]["path"] = f"{journal['journal_dir']}/images/{target['apply_order']}-before"
        if target["after_image"] is not None:
            target["after_image"]["path"] = f"{journal['journal_dir']}/images/{target['apply_order']}-after"
    journal["manifest_id"] = sha256_bytes(canonical_bytes({key: value for key, value in journal.items() if key != "manifest_id"}))
    repair = repair_graph_fixture(schema_sha, registry_sha, registry)
    refresh = {
        "contract": contract("refresh-run-receipt-v1"), "schema_version": "1.0.0", "refresh_id": "refresh-snapshot-fixture",
        "snapshot_id": policy["snapshot_id"], "snapshot_lock_receipt_id": policy["snapshot_lock_receipt_id"], "generation_id": generation["generation_id"],
        "expected_fact_generation": 7, "expected_panel_generation": 4, "status": "planned",
        "nodes": [
            {"instance_key": "a-node", "projection_kind": "state-audit", "disposition": "planned", "invalidation_reasons": [], "output": None, "error_code": None},
            {"instance_key": "b-node", "projection_kind": "program-status", "disposition": "planned", "invalidation_reasons": [], "output": None, "error_code": None},
        ],
        "retry_from_instance_key": None, "source_as_of": "2026-07-24T02:00:00Z", "receipt_id": "sha256:" + "3" * 64,
    }
    fact_receipt = fact_attribution_fixture(schema_sha, registry_sha, registry)["receipt"]
    second_delta = copy.deepcopy(fact_receipt["action_deltas"][0])
    second_delta.update({"action_id": "A-FLOW-2", "before_revision": 2, "after_revision": 3})
    fact_receipt["action_deltas"].append(second_delta)
    action_graph = fact_attribution_fixture(schema_sha, registry_sha, registry)
    action_artifacts = {row["path"]: row for row in action_graph["proof"]["business_artifacts"]}
    ledger_state = json.loads(artifact_bytes(action_artifacts[registry["runtime_paths"]["action_ledger_state"]["path"]]["after_bytes"]))
    second_state_action = copy.deepcopy(ledger_state["actions"][0]); second_state_action["action_id"] = "A-FLOW-2"; second_state_action["row_fingerprint"] = "sha256:" + "3" * 64
    ledger_state["actions"].append(second_state_action); ledger_state["actions"].sort(key=lambda row: row["action_id"].encode("utf-8"))
    ledger_state["applied_commands"].extend([
        {"command_id": "cmd-applied-a", "command_fingerprint": "sha256:" + "1" * 64, "action_id": "A-FLOW-1"},
        {"command_id": "cmd-applied-b", "command_fingerprint": "sha256:" + "2" * 64, "action_id": "A-FLOW-1"},
    ])
    ledger_state["applied_commands"].sort(key=lambda row: row["command_id"].encode("utf-8"))
    ledger_state["state_id"] = sha256_bytes(canonical_bytes({key: value for key, value in ledger_state.items() if key != "state_id"}))
    legacy_flow_raw = legacy_ledger_fixture("legacy20")
    legacy_flow_rows = parse_action_ledger_ingress(legacy_flow_raw, "legacy20")
    action_flow = action_flow_document(
        legacy_flow_rows, render_action_ledger(legacy_flow_rows), 0, registry, schema_sha, registry_sha,
    )
    action_flow["actions"][0]["related_plan_item_ids"] = ["PLAN-1", "PLAN-2"]
    action_flow["actions"][0]["related_flow_edge_ids"] = ["EDGE-1", "EDGE-2"]
    second_flow = copy.deepcopy(action_flow["actions"][0]); second_flow["action_id"] = "A-FLOW-2"
    action_flow["actions"].append(second_flow); action_flow["actions"].sort(key=lambda row: row["action_id"].encode("utf-8"))
    refresh_graph = fact_attribution_fixture(schema_sha, registry_sha, registry, "wdr-refresh-actions")
    wdr_command = copy.deepcopy(refresh_graph["command"])
    wdr_command["evidence"].append({
        "source_path": "checkpoints/c1.md", "source_fingerprint": "sha256:" + "b" * 64,
        "observed_at": "2026-07-24T02:01:00Z",
    })
    wdr_command["evidence"].sort(key=evidence_order_key)
    refresh_rows = parse_action_ledger(artifact_bytes(next(row for row in refresh_graph["proof"]["read_artifacts"] if row["path"] == registry["runtime_paths"]["action_ledger"]["path"])["bytes"]))
    extra_snapshot = action_snapshot(refresh_rows, "l1-other", wdr_command["action_snapshot"]["ledger_fingerprint"], wdr_command["action_snapshot"]["ledger_revision"])["actions"][0]
    wdr_command["action_snapshot"]["actions"].append(extra_snapshot)
    wdr_command["action_snapshot"]["actions"].sort(key=lambda row: row["action_id"].encode("utf-8"))
    sidecar_path = f"workstreams/{wdr_command['workstream_id']}/action-projection.json"
    sidecar = json.loads(artifact_bytes(next(row for row in refresh_graph["proof"]["business_artifacts"] if row["path"] == sidecar_path)["after_bytes"]))
    sidecar["actions"].append(copy.deepcopy(extra_snapshot)); sidecar["actions"].sort(key=lambda row: row["action_id"].encode("utf-8"))
    status_batch = status_intent_fixture(registry, schema_sha, registry_sha)
    meeting_plan = meeting_plan_intent_fixture(registry, schema_sha, registry_sha)
    second_action = copy.deepcopy(status_batch["action_commands"][0]); second_action["command_id"] = "cmd-action-second"; second_action["action_id"] = "A-STATUS-2"
    status_batch["action_commands"].append(second_action); status_batch["action_commands"].sort(key=lambda row: row["command_id"].encode("utf-8"))
    second_patch = copy.deepcopy(status_batch["wdr_patches"][0]); second_patch["command_id"] = "cmd-status-l1-payments"; second_patch["workstream_id"] = "l1-payments"
    status_batch["wdr_patches"].append(second_patch); status_batch["wdr_patches"].sort(key=lambda row: (row["workstream_id"].encode("utf-8"), row["command_id"].encode("utf-8")))
    status_batch["command_order"] = [row["command_id"] for row in status_batch["action_commands"] + status_batch["wdr_patches"]]
    status_intent = copy.deepcopy(status_batch["accepted_intents"][0])
    status_intent["evidence"] = copy.deepcopy(status_batch["wdr_patches"][0]["evidence"])
    action_command = copy.deepcopy(status_batch["action_commands"][0])
    action_command["evidence"] = copy.deepcopy(status_batch["wdr_patches"][0]["evidence"])
    drift = {
        "contract": contract("action-projection-drift-verdict-v1"), "schema_version": "1.0.0", "verdict_id": "sha256:" + "4" * 64,
        "generation_id": generation["generation_id"], "selection_policy_id": policy["policy_id"], "ledger_fingerprint": "sha256:" + "5" * 64,
        "selected_workstreams": ["l1-checkout", "l1-payments"],
        "workstreams": [
            {"workstream_id": "l1-checkout", "wdr_fingerprint": "sha256:" + "6" * 64, "wdr_revision": 4, "file_generation": 7, "sidecar_fingerprint": "sha256:" + "7" * 64, "sidecar_ledger_fingerprint": "sha256:" + "5" * 64, "status": "in-sync", "action_diffs": [{"action_id": "A-ORDER-1", "drift_kind": "missing-from-wdr", "ledger_present": True, "wdr_present": False, "ledger_revision": 1, "wdr_rendered_sha256": None}, {"action_id": "A-ORDER-2", "drift_kind": "content-mismatch", "ledger_present": True, "wdr_present": True, "ledger_revision": 2, "wdr_rendered_sha256": "sha256:" + "a" * 64}], "finding_ids": ["sha256:" + "1" * 64, "sha256:" + "2" * 64]},
            {"workstream_id": "l1-payments", "wdr_fingerprint": "sha256:" + "8" * 64, "wdr_revision": 5, "file_generation": 8, "sidecar_fingerprint": "sha256:" + "9" * 64, "sidecar_ledger_fingerprint": "sha256:" + "5" * 64, "status": "in-sync", "action_diffs": [{"action_id": "A-ORDER-3", "drift_kind": "orphan-in-wdr", "ledger_present": False, "wdr_present": True, "ledger_revision": None, "wdr_rendered_sha256": "sha256:" + "b" * 64}, {"action_id": "A-ORDER-4", "drift_kind": "content-mismatch", "ledger_present": True, "wdr_present": True, "ledger_revision": 3, "wdr_rendered_sha256": "sha256:" + "c" * 64}], "finding_ids": ["sha256:" + "3" * 64, "sha256:" + "4" * 64]},
        ], "overall_status": "in-sync",
    }
    for drift_row in drift["workstreams"]:
        drift_row["findings"] = sorted(
            [drift_finding(drift_row["workstream_id"], "action-projection-drift", diff) for diff in drift_row["action_diffs"]],
            key=lambda row: row["finding_id"].encode("utf-8"),
        )
        drift_row["finding_ids"] = [row["finding_id"] for row in drift_row["findings"]]
    preview = lambda name, digit: {"path": f"{name}.md", "fingerprint": "sha256:" + digit * 64, "content": name}
    state_audit = valid_instances["state-audit-payload-schema-valid"]
    state_audit["source_preview"] = [preview("a", "a"), preview("b", "b")]
    status = valid_instances["program-status-payload-schema-valid"]
    second_workstream = copy.deepcopy(status["workstream_current"][0])
    second_workstream["workstream_id"] = "l1-payments"
    status["workstream_current"].append(second_workstream)
    status["source_preview"] = [preview("a", "c"), preview("b", "d")]
    roadmap = valid_instances["roadmap-payload-schema-valid"]
    roadmap["roadmap_state"] = "populated"
    roadmap["milestone_timeline"] = [
        {"milestone_id": "a-milestone", "title": "A", "scope_id": "program", "status": "planned", "target": "gate-a", "owner": "FDE-A", "source_refs": ["a.md"]},
        {"milestone_id": "b-milestone", "title": "B", "scope_id": "program", "status": "planned", "target": "gate-b", "owner": "FDE-B", "source_refs": ["b.md"]},
    ]
    roadmap["unscheduled_milestones"] = [
        {"milestone_id": "a-unscheduled", "title": "A", "scope_id": "program", "status": "pending", "owner": "FDE-A", "reason": "awaiting gate", "source_refs": ["a.md"]},
        {"milestone_id": "b-unscheduled", "title": "B", "scope_id": "program", "status": "pending", "owner": "FDE-B", "reason": "awaiting gate", "source_refs": ["b.md"]},
    ]
    roadmap["source_preview"] = [preview("a", "e"), preview("b", "f")]
    meeting = valid_instances["meeting-pack-payload-schema-valid"]
    meeting["boards"] = [{"board_id": "a-board", "title": "A", "items": []}, {"board_id": "b-board", "title": "B", "items": []}]
    meeting["source_preview"] = [preview("a", "1"), preview("b", "2")]
    panel, upstreams, _, _, _ = panel_fixture(suite["contract_schema_vectors"], registry, schema_sha, registry_sha, project_root)
    for binding in registry["panel_binding_map"]:
        payload = upstreams[binding["projection_kind"]]
        set_pointer(panel, binding["panel_pointer"], {row["scenario"]: row for row in payload} if binding["merge_mode"] == "object-by-key" else payload)
    panel["panel_id"] = sha256_bytes(canonical_bytes({key: value for key, value in panel.items() if key != "panel_id"}))
    panel["model_v1"]["views"] = sorted(panel["model_v1"]["views"], key=lambda row: ordering_rule_key(row, "view_id"))
    bootstrap = bootstrap_migration_fixture(
        {"ledger_format": "legacy20", "action_flow_preimage": "brownfield-v1", "workstreams": ["l1-checkout", "l1-payments"]},
        registry, schema_sha, registry_sha,
    )["command"]
    expected_ids = sorted({
        row["id"]
        for key, values in suite.items()
        if key.endswith("_vectors") or key == "journal_fault_matrix"
        for row in values
    })
    hashes = {
        "registry": registry_sha, "schema": schema_sha,
        "protocol": registry["protocol"]["sha256"], "suite": registry["conformance_suite"]["sha256"],
    }
    writer_package = writer_fence_fixture(
        registry, schema_sha, registry_sha, expected_ids, hashes,
        suite=suite, schema=schema, project_root=project_root,
    )
    activation_lifecycle = activation_transition_fixture(
        writer_package, registry, schema_sha, registry_sha,
    )["lifecycle_index"]
    writer_attestation = writer_package["attestation"]
    second_attested_workstream = copy.deepcopy(writer_attestation["workstreams"][0])
    second_attested_workstream["workstream_id"] = "l1-payments"
    writer_attestation["workstreams"].append(second_attested_workstream)
    writer_attestation["workstreams"].sort(key=lambda row: row["workstream_id"].encode("utf-8"))
    writer_attestation["attestation_id"] = sha256_bytes(canonical_bytes({
        key: value for key, value in writer_attestation.items() if key != "attestation_id"
    }))
    manifest_path = registry["strict_rollout"]["writer_specs"][1]["manifest_path"]
    writer_manifest = json.loads(writer_package["writer_store"][manifest_path])
    lineage_index = json.loads(writer_package["lineage_store"][writer_attestation["lineage_index_path"]])
    owned_command = fact_attribution_fixture(schema_sha, registry_sha, registry, "owned-risk-flow")["command"]
    second_evidence = copy.deepcopy(owned_command["evidence"][0])
    second_evidence.update({
        "source_path": "risk/source-z.md", "source_fingerprint": "sha256:" + "f" * 64,
        "observed_at": "2026-07-24T02:01:00Z",
    })
    owned_command["evidence"].append(second_evidence)
    owned_command["evidence"].sort(key=evidence_order_key)
    repair_index = copy.deepcopy(repair["repair_index"])
    second_repair_entry = copy.deepcopy(repair_index["entries"][0])
    second_repair_entry.update({
        "lookup_id": "sha256:" + "f" * 64, "sequence": 2, "transaction_id": "tx-repair-ordering-2",
        "receipt_path": "receipts/repair/tx-repair-ordering-2.json", "receipt_sha256": "sha256:" + "e" * 64,
    })
    repair_index["entries"].append(second_repair_entry)
    repair_index["entries"].sort(key=lambda row: row["sequence"])
    repair_index["index_id"] = sha256_bytes(canonical_bytes({key: value for key, value in repair_index.items() if key != "index_id"}))
    repair_attempt_ledger = copy.deepcopy(repair["attempt_ledger"])
    second_attempt = copy.deepcopy(repair_attempt_ledger["attempts"][0])
    second_attempt.update({
        "sequence": 2, "lookup_id": "sha256:" + "0" * 64, "transaction_id": "tx-repair-ordering-2",
        "repair_receipt_path": "receipts/repair/tx-repair-ordering-2.json",
        "repair_receipt_sha256": "sha256:" + "e" * 64, "recorded_at": "2026-07-24T02:13:00Z",
    })
    repair_attempt_ledger["attempts"].append(second_attempt)
    repair_attempt_ledger["next_sequence"] = 3
    repair_attempt_ledger["ledger_id"] = sha256_bytes(canonical_bytes({key: value for key, value in repair_attempt_ledger.items() if key != "ledger_id"}))
    fact_command_index = copy.deepcopy(action_graph["command_index"])
    second_fact_entry = copy.deepcopy(fact_command_index["entries"][0])
    second_fact_entry.update({
        "sequence": 2, "command_id": "cmd-ordering-2", "command_fingerprint": "sha256:" + "0" * 64,
        "transaction_id": "tx-fact-ordering-2", "receipt_path": "receipts/fact/ordering-2.json",
        "receipt_sha256": "sha256:" + "e" * 64,
    })
    fact_command_index["entries"].append(second_fact_entry)
    fact_command_index["next_sequence"] = 3
    fact_command_index["index_id"] = sha256_bytes(canonical_bytes({key: value for key, value in fact_command_index.items() if key != "index_id"}))
    ordering_intents = [
        {
            "contract": contract("status-mutation-intent-v1"), "schema_version": "1.0.0",
            "intent_id": "intent-ordering-1", "origin_producer": "adp-meeting-sync", "workstream_id": "l1-checkout",
            "set": {"blockers": {"mode": "replace", "values": ["Access"]}, "progress": "Active"},
            "evidence": [{"source_path": "meetings/m1.md", "source_fingerprint": "sha256:" + "a" * 64, "observed_at": "2026-07-24T02:00:00Z"}],
        },
        {
            "contract": contract("status-mutation-intent-v1"), "schema_version": "1.0.0",
            "intent_id": "intent-ordering-2", "origin_producer": "adp-risk-dependency-change-review", "workstream_id": "l1-payments",
            "set": {"risks": {"mode": "replace", "values": ["Schedule"]}, "status": "at-risk"},
            "evidence": [{"source_path": "risks/r1.json", "source_fingerprint": "sha256:" + "c" * 64, "observed_at": "2026-07-24T02:01:00Z"}],
        },
    ]
    mutation_outbox = {
        "contract": contract("mutation-intent-outbox-v1"), "schema_version": "1.0.0", "outbox_generation": 2,
        "entries": [
            {
                "sequence": 1, "intent_id": sha256_bytes(canonical_bytes(ordering_intents[0])), "intent": ordering_intents[0], "source_command_id": "cmd-intent-ordering-1",
                "source_command_fingerprint": "sha256:" + "a" * 64, "producer_id": "adp-meeting-sync",
                "workstream_id": "l1-checkout", "field_set": ["blockers", "progress"], "status": "consumed",
                "attempts": 1, "last_error": None, "created_at": "2026-07-24T02:00:00Z",
                "consumed_receipt_id": "sha256:" + "b" * 64,
            },
            {
                "sequence": 2, "intent_id": sha256_bytes(canonical_bytes(ordering_intents[1])), "intent": ordering_intents[1], "source_command_id": "cmd-intent-ordering-2",
                "source_command_fingerprint": "sha256:" + "c" * 64, "producer_id": "adp-risk-dependency-change-review",
                "workstream_id": "l1-payments", "field_set": ["risks", "status"], "status": "pending",
                "attempts": 0, "last_error": None, "created_at": "2026-07-24T02:01:00Z", "consumed_receipt_id": None,
            },
        ],
    }
    mutation_outbox["outbox_id"] = sha256_bytes(canonical_bytes(mutation_outbox))
    release_history = copy.deepcopy(writer_package["documents"]["release_evidence_history_index"])
    second_history_entry = copy.deepcopy(release_history["entries"][0])
    second_history_entry.update({
        "set_generation": 2, "set_id": "sha256:" + "f" * 64,
        "set_path": "state/release-evidence/sets/h_" + "f" * 64 + ".json",
        "set_sha256": "sha256:" + "e" * 64,
        "transition_receipt_path": "receipts/release-evidence/release-evidence-ordering-2.json",
        "transition_receipt_sha256": "sha256:" + "d" * 64,
    })
    release_history["entries"].append(second_history_entry)
    release_history["current_generation"] = 2
    release_history["current_set_id"] = second_history_entry["set_id"]
    release_history["index_id"] = sha256_bytes(canonical_bytes({key: value for key, value in release_history.items() if key != "index_id"}))
    inspect_verdict = {
        "inspected_generation_id": generation["generation_id"], "inspected_pointer_id": pointer["pointer_id"],
        "outcome": "stale", "inspected_at": "2026-07-24T03:05:00Z", "observed_fact_generation": generation["fact_generation"],
        "changed_sources": ["a.md", "b.md"], "error_code": "SOURCE_DRIFT",
    }
    inspect_verdict["verdict_id"] = sha256_bytes(canonical_bytes(inspect_verdict))
    refresh_status = {
        "contract": contract("panel-refresh-status-v1"), "schema_version": "1.0.0", "current_run_id": None,
        "current_status": "dirty", "last_successful_generation_id": generation["generation_id"],
        "last_successful_refresh_at": "2026-07-24T03:00:00Z", "pending_invalidations": [], "latest_inspect": inspect_verdict,
    }
    refresh_status["state_id"] = sha256_bytes(canonical_bytes(refresh_status))
    return {
        "physical-workstream-inventory/1.0.0": physical_inventory_fixture(registry, policy, generation["fact_generation"], schema_sha, registry_sha),
        "selection-policy/1.0.0": policy,
        "generation-envelope/1.0.0": generation,
        "projection-dependency-manifest/1.0.0": manifest,
        "panel-current-pointer/1.0.0": pointer,
        "transaction-journal-manifest/1.0.0": journal,
        "audit-finding-repair/2.0.0": repair["audit"],
        "refresh-run-receipt/1.0.0": refresh,
        "fact-mutation-receipt/1.0.0": fact_receipt,
        "action-ledger-state/1.0.0": ledger_state,
        "action-flow-index/1.0.0": action_flow,
        "wdr-action-projection/1.0.0": sidecar,
        "wdr-mutation/1.0.0": wdr_command,
        "status-sync-batch/2.0.0": status_batch,
        "status-mutation-intent/1.0.0": status_intent,
        "action-ledger-mutation/2.0.0": action_command,
        "action-projection-drift-verdict/1.0.0": drift,
        "program-status-payload/2.0.0": status,
        "roadmap-payload/2.0.0": roadmap,
        "meeting-pack-payload/2.0.0": meeting,
        "management-panel-payload/2.0.0": panel,
        "state-audit-payload/2.0.0": state_audit,
        "bootstrap-migration-command/1.0.0": bootstrap,
        "writer-fence-migration-attestation/1.0.0": writer_attestation,
        "writer-build-manifest/1.0.0": writer_manifest,
        "generation-lineage-index/1.0.0": lineage_index,
        "panel-refresh-status/1.0.0": refresh_status,
        "release-evidence-set/1.0.0": writer_package["release_evidence_set"],
        "owned-fact-command/1.0.0": owned_command,
        "repair-receipt-index/1.0.0": repair_index,
        "repair-attempt-ledger/1.0.0": repair_attempt_ledger,
        "fact-command-receipt-index/1.0.0": fact_command_index,
        "mutation-intent-outbox/1.0.0": mutation_outbox,
        "activation-lifecycle-index/1.0.0": activation_lifecycle,
        "release-evidence-history-index/1.0.0": release_history,
    }


def all_ordering_rules_semantics(
    registry: dict[str, Any], schema: dict[str, Any], suite: dict[str, Any], project_root: Path,
    schema_sha: str, registry_sha: str, mutation: str,
) -> bool:
    def arrays_at_pointer(document: Any, pointer: str) -> list[list[Any]]:
        parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer.removeprefix("/").split("/") if part != ""]

        def expand(current: Any, remaining: list[str]) -> list[Any]:
            if not remaining:
                return [current]
            head, tail = remaining[0], remaining[1:]
            if head == "*":
                if not isinstance(current, list):
                    raise ValueError("ordering wildcard requires an array")
                return [value for item in current for value in expand(item, tail)]
            if isinstance(current, dict) and head in current:
                return expand(current[head], tail)
            if isinstance(current, list) and head.isdigit() and int(head) < len(current):
                return expand(current[int(head)], tail)
            raise ValueError("ordering pointer does not resolve")

        arrays = expand(document, parts)
        if not arrays or any(not isinstance(value, list) for value in arrays):
            raise ValueError("ordering pointer must resolve to arrays")
        return arrays

    documents = representative_ordering_documents(suite, schema, registry, project_root, schema_sha, registry_sha)
    if set(documents) != {row["contract"] for row in registry["canonical_array_ordering"]}:
        return False
    for contract_name, document in documents.items():
        if not validate_registered(document, schema, registry, contract_name, schema_sha, registry_sha):
            return False
    if mutation == "nfc-key-collision":
        document = documents["state-audit-payload/2.0.0"]
        document["source_preview"] = [
            {"path": "é.md", "fingerprint": "sha256:" + "1" * 64, "content": "a"},
            {"path": "é.md", "fingerprint": "sha256:" + "2" * 64, "content": "b"},
        ]
    elif mutation == "non-nfc-scalar-key":
        documents["state-audit-payload/2.0.0"]["source_preview"][1]["path"] = "é.md"
    elif mutation == "non-nfc-composite-key":
        documents["generation-envelope/1.0.0"]["leaf_sources"][1]["path"] = "é.md"
    for rule in registry["canonical_array_ordering"]:
        try:
            arrays = arrays_at_pointer(documents[rule["contract"]], rule["pointer"])
        except (KeyError, IndexError, TypeError, ValueError):
            return False
        for values in arrays:
            if len(values) < 2:
                return False
            if mutation == "reverse-each-rule":
                values.reverse()
            elif mutation == "duplicate-each-rule-key":
                values.append(copy.deepcopy(values[0]))
            try:
                keys = [ordering_rule_key(value, rule["key"], rule.get("key_types")) for value in values]
            except ValueError:
                return False
            is_canonical = keys == sorted(keys) and len(keys) == len(set(keys))
            if not is_canonical:
                return False
    if mutation == "nullable-key":
        values = json_pointer(documents["panel-current-pointer/1.0.0"], "/projections")
        meeting_keys = [ordering_rule_key(value, "kind,instance_key") for value in values if value["kind"] == "meeting-pack"]
        return len(meeting_keys) == 2 and meeting_keys[0][1][0] == 0 and meeting_keys[1][1][0] == 1
    return True


def identity_sets_valid(documents: dict[str, Any], registry: dict[str, Any], required_contracts: set[str] | None = None) -> bool:
    def expand(current: Any, parts: list[str]) -> tuple[bool, list[Any]]:
        if not parts:
            return True, [current]
        head, rest = parts[0], parts[1:]
        if head == "*":
            if not isinstance(current, list):
                return False, []
            values: list[Any] = []
            for item in current:
                found, expanded = expand(item, rest)
                if not found:
                    return False, []
                values.extend(expanded)
            return True, values
        return expand(current[head], rest) if isinstance(current, dict) and head in current else (False, [])

    for rule in registry.get("identity_set_fields", []):
        if required_contracts is not None and rule["contract"] not in required_contracts:
            continue
        found, arrays = expand(documents.get(rule["contract"]), rule["pointer_template"].strip("/").split("/"))
        if not found:
            return False
        for values in arrays:
            if not isinstance(values, list):
                return False
            normalized = [unicodedata.normalize("NFC", str(value)) for value in values]
            canonical_values = [value for _, value in sorted(zip(normalized, values), key=lambda row: row[0].encode("utf-8"))]
            if values != canonical_values or any(str(value) != key for value, key in zip(values, normalized)) or len(normalized) != len(set(normalized)):
                return False
    return True


def identity_set_semantics(registry: dict[str, Any], schema_sha: str, registry_sha: str, mutation: str = "none") -> bool:
    fact = fact_attribution_fixture(schema_sha, registry_sha, registry)
    repair = repair_graph_fixture(schema_sha, registry_sha, registry)
    refresh = fact_attribution_fixture(schema_sha, registry_sha, registry, "wdr-refresh-actions")
    action_artifacts = {row["path"]: row for row in fact["proof"]["business_artifacts"]}
    action_flow = json.loads(artifact_bytes(action_artifacts[registry["runtime_paths"]["action_flow_index"]["path"]]["after_bytes"]))
    sidecar_path = f"workstreams/{refresh['command']['workstream_id']}/action-projection.json"
    sidecar = json.loads(artifact_bytes(next(row for row in refresh["proof"]["business_artifacts"] if row["path"] == sidecar_path)["after_bytes"]))
    inspect_verdict = {
        "inspected_generation_id": "sha256:" + "1" * 64, "inspected_pointer_id": "sha256:" + "2" * 64,
        "outcome": "stale", "inspected_at": "2026-07-24T03:05:00Z", "observed_fact_generation": 7,
        "changed_sources": ["a.md", "b.md"], "error_code": "SOURCE_DRIFT", "verdict_id": "sha256:" + "3" * 64,
    }
    refresh_status = {
        "contract": expected_contract_ref(registry, "panel-refresh-status/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "current_run_id": None, "current_status": "dirty",
        "last_successful_generation_id": "sha256:" + "1" * 64, "last_successful_refresh_at": "2026-07-24T03:00:00Z",
        "pending_invalidations": [], "latest_inspect": inspect_verdict, "state_id": "sha256:" + "4" * 64,
    }
    identity_intent = {
        "contract": expected_contract_ref(registry, "status-mutation-intent/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "intent_id": "intent-identity-1", "origin_producer": "adp-meeting-sync",
        "workstream_id": "l1-checkout", "set": {"blockers": {"mode": "replace", "values": ["Access"]}, "progress": "Active"},
        "evidence": [{"source_path": "meetings/m1.md", "source_fingerprint": "sha256:" + "6" * 64, "observed_at": "2026-07-24T02:00:00Z"}],
    }
    mutation_outbox = {
        "contract": expected_contract_ref(registry, "mutation-intent-outbox/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "outbox_generation": 1,
        "entries": [{
            "sequence": 1, "intent_id": sha256_bytes(canonical_bytes(identity_intent)), "intent": identity_intent, "source_command_id": "cmd-identity-intent-1",
            "source_command_fingerprint": "sha256:" + "6" * 64, "producer_id": "adp-meeting-sync",
            "workstream_id": "l1-checkout", "field_set": ["blockers", "progress"], "status": "pending",
            "attempts": 0, "last_error": None, "created_at": "2026-07-24T02:00:00Z", "consumed_receipt_id": None,
        }],
    }
    mutation_outbox["outbox_id"] = sha256_bytes(canonical_bytes(mutation_outbox))
    convergence = {
        "contract": expected_contract_ref(registry, "intent-convergence-verdict/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "outbox_id": mutation_outbox["outbox_id"], "evaluated_through_sequence": 1,
        "pending_intent_ids": [mutation_outbox["entries"][0]["intent_id"]], "failed_intent_ids": [],
        "waived_intent_ids": [], "status": "pending",
    }
    convergence["verdict_id"] = sha256_bytes(canonical_bytes(convergence))
    documents = {
        "writer-capability-registry/1.0.0": fact["capability_registry"],
        "repair-dry-run-request/1.0.0": repair["dry_request"],
        "audit-finding-repair/2.0.0": repair["audit"],
        "fact-mutation-receipt/1.0.0": fact["receipt"],
        "action-flow-index/1.0.0": action_flow,
        "wdr-action-projection/1.0.0": sidecar,
        "wdr-mutation/1.0.0": refresh["command"],
        "status-sync-batch/2.0.0": status_intent_fixture(registry, schema_sha, registry_sha),
        "panel-refresh-status/1.0.0": refresh_status,
        "activation-transition-command/1.0.0": {"approved_by": ["operator-a", "operator-b"]},
        "mutation-intent-outbox/1.0.0": mutation_outbox,
        "intent-convergence-verdict/1.0.0": convergence,
    }
    if mutation == "permute-status-fields":
        status = next(row for row in fact["capability_registry"]["capabilities"] if row["producer_id"] == "adp-status-sync")
        status["allowed_fields"].reverse()
    elif mutation == "nfc-collision":
        repair["dry_request"]["authorization_scopes"] = ["repair:e\u0301", "repair:\u00e9"]
    elif mutation == "non-nfc-scalar":
        repair["dry_request"]["authorization_scopes"] = ["repair:e\u0301"]
    return identity_sets_valid(documents, registry)


def repair_binding_input(dry_request: dict[str, Any], audit_id: str, outcome: str, schema_sha: str, registry_sha: str) -> dict[str, Any]:
    batch = dry_request["batch"]
    return {
        "project_root_instance_id": dry_request["project_root_instance_id"],
        "memory_root_instance_id": dry_request["memory_root_instance_id"],
        "principal": dry_request["principal"],
        "authorization_scopes": dry_request["authorization_scopes"],
        "audit_id": audit_id,
        "batch_id": batch["batch_id"],
        "batch_digest": batch["batch_digest"],
        "read_set": batch["read_set"],
        "outcome": outcome,
        "contract_hashes": {"schema": schema_sha, "registry": registry_sha},
    }


def repair_lookup_id(batch: dict[str, Any]) -> str:
    command = batch["command"]
    return sha256_bytes(canonical_bytes({
        "workflow": command["workflow"], "workstream_id": command["workstream_id"],
        "operation": command["operation"], "finding_ids": batch["finding_ids"],
    }))


def repair_attempt_binding(transaction_id: str, journal_id: str, marker: dict[str, Any], recovery: dict[str, Any] | None) -> dict[str, Any]:
    marker_raw = canonical_bytes(marker)
    recovery_raw = None if recovery is None else canonical_bytes(recovery)
    body = {
        "business_transaction_id": transaction_id, "business_journal_id": journal_id,
        "business_marker_id": marker["marker_id"], "business_marker_sha256": sha256_bytes(marker_raw),
        "recovery_receipt_id": None if recovery is None else recovery["receipt_id"],
        "recovery_receipt_sha256": None if recovery_raw is None else sha256_bytes(recovery_raw),
    }
    digest = sha256_bytes(canonical_bytes(body)).removeprefix("sha256:")
    return {
        **body, "attempt_transaction_id": f"repair-attempt:{digest}",
        "attempt_journal_id": f"journal-repair-attempt:{digest}",
    }


def repair_graph_fixture(
    schema_sha: str, registry_sha: str, registry: dict[str, Any] | None = None, outcome: str = "committed",
    target_workstream_id: str = "l1-checkout", fact_generation: int = 7, token_char: str = "A", transaction_suffix: str = "1",
    prior_transaction_id: str = "tx-prior-1",
) -> dict[str, Any]:
    contract = lambda anchor: {"schema_id": f"urn:adp:panel-sync-contracts:2026-07-24#{anchor}", "schema_sha256": schema_sha, "registry_sha256": registry_sha}
    drift_rows = []
    for drift_workstream, drift_action, revision in (("l1-checkout", "A-FLOW-1", 4), ("l1-other", "A-OTHER-1", 2)):
        is_orphan = outcome == "orphan" and drift_workstream == target_workstream_id
        if is_orphan:
            drift_action = "A-ORPHAN-1"
        diff = {
            "action_id": drift_action, "drift_kind": "orphan-in-wdr" if is_orphan else "missing-from-wdr",
            "ledger_present": not is_orphan, "wdr_present": is_orphan,
            "ledger_revision": None if is_orphan else revision,
            "wdr_rendered_sha256": sha256_bytes(f"orphan:{drift_action}".encode()) if is_orphan else None,
        }
        finding = drift_finding(drift_workstream, "action-projection-drift", diff)
        drift_rows.append({
            "workstream_id": drift_workstream, "wdr_fingerprint": sha256_bytes(fixture_wdr(drift_workstream).encode()),
            "wdr_revision": 4, "file_generation": 7, "sidecar_fingerprint": sha256_bytes(f"sidecar:{drift_workstream}".encode()),
            "sidecar_ledger_fingerprint": "sha256:" + "d" * 64, "status": "drift",
            "action_diffs": [diff], "findings": [finding], "finding_ids": [finding["finding_id"]],
        })
    drift_verdict = {
        "contract": contract("action-projection-drift-verdict-v1"), "schema_version": "1.0.0",
        "generation_id": sha256_bytes(b"repair-drift-generation"), "selection_policy_id": sha256_bytes(b"repair-drift-selection"),
        "ledger_fingerprint": "sha256:" + "d" * 64, "selected_workstreams": ["l1-checkout", "l1-other"],
        "workstreams": drift_rows, "overall_status": "degraded",
    }
    drift_verdict["verdict_id"] = sha256_bytes(canonical_bytes(drift_verdict))
    repair_ledger_fingerprint = "sha256:" + "d" * 64
    repair_ledger_rows: list[dict[str, Any]] = []
    repair_ledger_state: dict[str, Any] | None = None
    if registry is not None:
        repair_ledger_rows, repair_ledger_raw, repair_ledger_state = refresh_ledger_fixture(registry, schema_sha, registry_sha)
        repair_ledger_fingerprint = sha256_bytes(repair_ledger_raw)
    drift_verdict["ledger_fingerprint"] = repair_ledger_fingerprint
    for drift_row in drift_verdict["workstreams"]:
        drift_row["sidecar_ledger_fingerprint"] = repair_ledger_fingerprint
        is_orphan_row = outcome == "orphan" and drift_row["workstream_id"] == target_workstream_id
        orphan_record = None if not is_orphan_row else {
            "action_id": "A-ORPHAN-1", "owner": "FDE-O", "action": "Remove orphan projection", "due_trigger": "next sync",
            "status": "open", "action_revision": 1, "routing_scope_id": drift_row["workstream_id"],
            "affected_workstreams": [drift_row["workstream_id"]],
        }
        if orphan_record is not None:
            orphan_record["rendered_summary"] = rendered_action_summary(orphan_record)
        expected_actions = []
        if orphan_record is not None:
            expected_actions = action_snapshot(
                repair_ledger_rows, drift_row["workstream_id"], repair_ledger_fingerprint,
                repair_ledger_state["ledger_revision"],
            )["actions"] + [orphan_record]
            expected_actions.sort(key=lambda row: row["action_id"].encode("utf-8"))
        expected_sidecar = {
            "contract": expected_contract_ref(registry, "wdr-action-projection/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "workstream_id": drift_row["workstream_id"],
            "ledger_fingerprint": repair_ledger_fingerprint, "ledger_revision": 11,
            "wdr_revision": 4, "file_generation": 7,
            "renderer_id": "urn:adp:wdr-action-renderer:1.0.0", "renderer_sha256": registry["protocol"]["sha256"],
            "actions": expected_actions,
        }
        drift_row["sidecar_fingerprint"] = sha256_bytes(canonical_bytes(expected_sidecar))
        if orphan_record is not None:
            orphan_wdr = apply_wdr_patch(
                fixture_wdr(drift_row["workstream_id"]), {"set": {"refresh_actions": True}},
                [row["rendered_summary"] for row in expected_actions],
            ).encode()
            drift_row["wdr_fingerprint"] = sha256_bytes(orphan_wdr)
            drift_row["action_diffs"][0]["wdr_rendered_sha256"] = sha256_bytes(orphan_record["rendered_summary"].encode("utf-8"))
            finding = drift_finding(drift_row["workstream_id"], "action-projection-drift", drift_row["action_diffs"][0])
            drift_row["findings"] = [finding]
            drift_row["finding_ids"] = [finding["finding_id"]]
    drift_verdict["verdict_id"] = sha256_bytes(canonical_bytes({key: value for key, value in drift_verdict.items() if key != "verdict_id"}))
    audit_id = sha256_bytes(canonical_bytes({"drift_verdict_id": drift_verdict["verdict_id"], "finding_algorithm": "drift-finding-to-repair-v2"}))

    def make_batch(workstream_id: str, finding_id: str, action_ids: list[str], revisions: list[int], digit: str) -> tuple[dict[str, Any], dict[str, Any]]:
        command = {"workflow": "adp-status-sync", "workstream_id": workstream_id, "operation": "refresh_actions", "expected_wdr_revision": 4, "expected_file_generation": 7, "action_ids": action_ids}
        source_path = f"workstreams/{workstream_id}/delivery-record.md"
        source_fingerprint = next(row for row in drift_rows if row["workstream_id"] == workstream_id)["wdr_fingerprint"]
        read_set = {
            "ledger_fingerprint": repair_ledger_fingerprint,
            "action_revisions": [{"action_id": action_id, "expected_present": True, "revision": revision} for action_id, revision in zip(action_ids, revisions)],
            "wdr_revisions": [{"workstream_id": workstream_id, "wdr_revision": 4, "file_generation": 7, "fingerprint": source_fingerprint}],
            "source_records": [{"root_instance_id": "123e4567-e89b-42d3-a456-426614174000", "path": source_path, "fingerprint": source_fingerprint}],
            "fact_generation": fact_generation,
        }
        core = {"based_on_audit_id": audit_id, "finding_ids": [finding_id], "command": command, "read_set": read_set}
        batch_digest = sha256_bytes(canonical_bytes(core))
        identity = {"workflow": command["workflow"], "workstream_id": workstream_id, "operation": command["operation"], "finding_ids": [finding_id], "batch_digest": batch_digest}
        batch = {"batch_id": sha256_bytes(canonical_bytes(identity)), **core, "batch_digest": batch_digest}
        finding = {
            "finding_id": finding_id, "kind": "action-projection-drift", "severity": "blocked", "workflow": "adp-status-sync",
            "workstream_id": workstream_id, "operation": "refresh_actions", "entity_refs": [{"entity_type": "action", "id": action_id} for action_id in action_ids],
            "action_ids": action_ids, "source_path": source_path, "source_line": 42, "repair_batch_id": batch["batch_id"],
        }
        return batch, finding

    rows = [
        make_batch(
            drift_row["workstream_id"], drift_row["finding_ids"][0], [drift_row["action_diffs"][0]["action_id"]],
            [drift_row["action_diffs"][0]["ledger_revision"] or 1], digit,
        )
        for drift_row, digit in zip(drift_rows, ("3", "4"))
    ]
    batches = sorted((row[0] for row in rows), key=lambda row: row["batch_id"].encode("utf-8"))
    findings = [row[1] for row in rows]
    findings.sort(key=lambda row: tuple(str(row[key]).encode("utf-8") for key in ("workflow", "workstream_id", "operation", "finding_id")))
    target_batch = next(row for row in batches if row["command"]["workstream_id"] == target_workstream_id)
    if outcome == "orphan":
        old_batch_id = target_batch["batch_id"]
        target_batch["read_set"]["action_revisions"][0].update({"expected_present": False, "revision": None})
        core = {key: target_batch[key] for key in ("based_on_audit_id", "finding_ids", "command", "read_set")}
        target_batch["batch_digest"] = sha256_bytes(canonical_bytes(core))
        identity = {"workflow": target_batch["command"]["workflow"], "workstream_id": target_batch["command"]["workstream_id"], "operation": target_batch["command"]["operation"], "finding_ids": target_batch["finding_ids"], "batch_digest": target_batch["batch_digest"]}
        target_batch["batch_id"] = sha256_bytes(canonical_bytes(identity))
        next(row for row in findings if row["repair_batch_id"] == old_batch_id)["repair_batch_id"] = target_batch["batch_id"]
    audit = {"contract": contract("audit-finding-repair-v2"), "schema_version": "2.0.0", "audit_id": audit_id, "drift_verdict_id": drift_verdict["verdict_id"], "findings": findings, "repair_batches": batches}
    dry_request = {
        "contract": contract("repair-dry-run-request-v1"), "schema_version": "1.0.0",
        "project_root_instance_id": "123e4567-e89b-42d3-a456-426614174001", "memory_root_instance_id": "123e4567-e89b-42d3-a456-426614174000",
        "principal": "operator-1", "authorization_scopes": ["repair:actions"], "batch": copy.deepcopy(target_batch),
    }
    binding_digest = sha256_bytes(canonical_bytes(repair_binding_input(dry_request, audit_id, "applicable", schema_sha, registry_sha)))
    if len(token_char) != 1 or not token_char.isascii() or not token_char.isalnum():
        raise ValueError("repair fixture token_char must be one ASCII alphanumeric")
    token = token_char * 43
    issued_at = "2026-07-24T02:00:00Z"
    expires_at = "2026-07-24T02:15:00Z"
    dry_result = {
        "contract": contract("repair-dry-run-result-v1"), "schema_version": "1.0.0", "dry_run_id": sha256_bytes(canonical_bytes(dry_request)),
        "batch_id": target_batch["batch_id"], "outcome": "applicable", "binding_digest": binding_digest, "token": token,
        "issued_at": issued_at, "expires_at": expires_at, "error_code": None,
    }
    apply_request = {
        "contract": contract("repair-apply-request-v1"), "schema_version": "1.0.0", "principal": dry_request["principal"],
        "batch_id": target_batch["batch_id"], "batch_digest": target_batch["batch_digest"], "token": token, "applied_at": "2026-07-24T02:10:00Z",
    }
    token_hash = sha256_bytes(token.encode("utf-8"))
    transaction_id = f"tx-repair-{transaction_suffix}"

    nonce_states: list[dict[str, Any]] = []
    for status, reserved_by, tx_id, previous in (
        ("unused", None, None, None),
        ("reserved", dry_request["principal"], transaction_id, "previous"),
        ("consumed", dry_request["principal"], transaction_id, "previous"),
    ):
        nonce = {
            "contract": contract("repair-nonce-state-v1"), "schema_version": "1.0.0", "nonce_id": token_hash, "token_hash": token_hash,
            "batch_id": target_batch["batch_id"], "binding_digest": binding_digest, "status": status, "expires_at": expires_at,
            "reserved_by": reserved_by, "transaction_id": tx_id, "previous_state_id": None if previous is None else nonce_states[-1]["state_id"],
        }
        nonce["state_id"] = sha256_bytes(canonical_bytes(nonce))
        nonce_states.append(nonce)

    if registry is None:
        raise ValueError("repair graph fixture requires the contract registry")
    fact_graph = fact_attribution_fixture(
        schema_sha, registry_sha, registry, "wdr-refresh-actions", workstream_id=target_workstream_id,
        before_fact_generation=fact_generation, prior_transaction_id=prior_transaction_id,
        orphan_action_id="A-ORPHAN-1" if outcome == "orphan" else None,
    )
    journal, marker = journal_fixture("repair", schema_sha, registry_sha, registry)
    journal["transaction_id"] = transaction_id
    journal["journal_id"] = f"journal-repair-{transaction_suffix}"
    journal["journal_dir"] = registry["runtime_paths"]["journal_dir_template"].replace("{transaction_token}", filesystem_token(transaction_id))
    journal["manifest_path"] = runtime_path(registry, "journal_manifest_template", transaction_id=transaction_id)
    journal["prepared_marker_path"] = runtime_path(registry, "journal_prepared_marker_template", transaction_id=transaction_id)
    journal["terminal_marker_path"] = runtime_path(registry, "journal_terminal_marker_template", transaction_id=transaction_id)
    journal["recovery_receipt_path"] = runtime_path(registry, "journal_recovery_receipt_template", transaction_id=transaction_id)
    authorization = copy.deepcopy(fact_graph["receipt"]["authorization"])
    journal["authorization"] = copy.deepcopy(authorization)
    workstream_id = target_batch["command"]["workstream_id"]
    project_root_id = dry_request["memory_root_instance_id"]
    business_rows = copy.deepcopy([row for row in fact_graph["journal"]["targets"] if row["role"] == "business"])
    for row in business_rows:
        row["root_instance_id"] = project_root_id
        row["before_image"]["root_instance_id"] = project_root_id
        row["after_image"]["root_instance_id"] = project_root_id
    generation_row = mutation_target("fact-generation", "replace", len(business_rows), "state/fact-generation.json")
    command_index_row = mutation_target(
        "fact-command-index", "replace", len(business_rows) + 1,
        registry["runtime_paths"]["fact_command_receipt_index"]["path"],
    )
    nonce_path = runtime_path(registry, "repair_nonce_template", nonce_id=token_hash)
    nonce_row = mutation_target("nonce", "replace", len(business_rows) + 2, nonce_path)
    token_name = filesystem_token(transaction_id)
    receipt_paths = [runtime_path(registry, "repair_fact_receipt_template", transaction_id=transaction_id)]
    receipt_rows = [mutation_target("receipt", "create", len(business_rows) + 3 + index, path) for index, path in enumerate(receipt_paths)]
    journal["targets"] = business_rows + [generation_row, command_index_row, nonce_row] + receipt_rows
    journal["receipt_target_paths"] = receipt_paths
    _reindex_targets(journal["targets"], journal["journal_dir"])
    before_fact_state = copy.deepcopy(fact_graph["before_state"])
    after_fact_state = copy.deepcopy(fact_graph["after_state"])
    after_fact_state["last_transaction_id"] = transaction_id
    after_fact_state["state_id"] = sha256_bytes(canonical_bytes({key: value for key, value in after_fact_state.items() if key != "state_id"}))
    generation_row = next(row for row in journal["targets"] if row["role"] == "fact-generation")
    generation_row["before_sha256"] = sha256_bytes(canonical_bytes(before_fact_state))
    generation_row["after_sha256"] = sha256_bytes(canonical_bytes(after_fact_state))
    generation_row["before_image"]["sha256"] = generation_row["before_sha256"]
    generation_row["after_image"]["sha256"] = generation_row["after_sha256"]
    proof = copy.deepcopy(fact_graph["proof"])
    proof["transaction_id"] = transaction_id
    proof["proof_id"] = sha256_bytes(canonical_bytes({key: value for key, value in proof.items() if key != "proof_id"}))
    nonce_target = next(row for row in journal["targets"] if row["role"] == "nonce")
    nonce_target["path"] = nonce_path
    nonce_target["before_sha256"] = sha256_bytes(canonical_bytes(nonce_states[1]))
    nonce_target["after_sha256"] = sha256_bytes(canonical_bytes(nonce_states[2]))
    nonce_target["before_image"]["sha256"] = nonce_target["before_sha256"]
    nonce_target["after_image"]["sha256"] = nonce_target["after_sha256"]
    receipt_targets = [row for row in journal["targets"] if row["role"] == "receipt"]

    business_targets = [copy.deepcopy(row) for row in journal["targets"] if row["role"] == "business"]
    generation_target = copy.deepcopy(next(row for row in journal["targets"] if row["role"] == "fact-generation"))
    fact_receipt = {
        "contract": contract("fact-mutation-receipt-v1"), "schema_version": "1.0.0", "transaction_id": transaction_id, "journal_id": journal["journal_id"],
        "authorization": copy.deepcopy(authorization), "initiator": {key: authorization[key] for key in ("producer_id", "capability_id", "capability_epoch", "principal_id")},
        "before_fact_generation": before_fact_state["fact_generation"], "after_fact_generation": after_fact_state["fact_generation"],
        "business_targets": business_targets, "generation_state_target": generation_target, "action_deltas": [], "status": "committed",
    }
    fact_receipt["receipt_id"] = sha256_bytes(canonical_bytes(fact_receipt))
    repair_receipt = {
        "contract": contract("repair-run-receipt-v1"), "schema_version": "1.0.0", "batch_id": target_batch["batch_id"], "outcome": "committed",
        "nonce_status": "consumed", "nonce_state_id": nonce_states[2]["state_id"], "fact_receipt_id": fact_receipt["receipt_id"],
        "transaction_id": transaction_id, "journal_id": journal["journal_id"],
        "attempt_transaction_id": None, "attempt_journal_id": None, "business_marker_id": None, "business_marker_sha256": None,
        "recovery_receipt_id": None, "recovery_receipt_sha256": None, "retry_required": False, "error_code": None,
    }
    repair_receipt["receipt_id"] = sha256_bytes(canonical_bytes(repair_receipt))
    for target, receipt in zip(receipt_targets, (fact_receipt,)):
        target["after_sha256"] = sha256_bytes(canonical_bytes(receipt))
        target["after_image"]["sha256"] = target["after_sha256"]
    before_command_index = copy.deepcopy(fact_graph["before_command_index"])
    command_index = {
        "contract": copy.deepcopy(before_command_index["contract"]), "schema_version": "1.0.0",
        "next_sequence": before_command_index["next_sequence"] + 1,
        "entries": before_command_index["entries"] + [{
            "sequence": before_command_index["next_sequence"], "command_id": fact_graph["command"]["command_id"],
            "command_fingerprint": authorization["authorized_command_fingerprint"],
            "transaction_id": transaction_id, "receipt_id": fact_receipt["receipt_id"],
            "receipt_path": receipt_paths[0], "receipt_sha256": sha256_bytes(canonical_bytes(fact_receipt)),
        }],
    }
    command_index["index_id"] = sha256_bytes(canonical_bytes(command_index))
    command_index_row["before_sha256"] = sha256_bytes(canonical_bytes(before_command_index))
    command_index_row["after_sha256"] = sha256_bytes(canonical_bytes(command_index))
    command_index_row["before_image"]["sha256"] = command_index_row["before_sha256"]
    command_index_row["after_image"]["sha256"] = command_index_row["after_sha256"]
    journal_body = {key: value for key, value in journal.items() if key != "manifest_id"}
    journal["manifest_id"] = sha256_bytes(canonical_bytes(journal_body))
    marker.update({"journal_id": journal["journal_id"], "manifest_id": journal["manifest_id"], "state": "committed"})
    marker["marker_id"] = sha256_bytes(canonical_bytes({key: value for key, value in marker.items() if key != "marker_id"}))
    graph = {
        "drift_verdict": drift_verdict, "audit": audit, "dry_request": dry_request, "dry_result": dry_result, "apply_request": apply_request,
        "nonce_states": nonce_states, "journal": journal, "marker": marker, "fact_receipt": fact_receipt, "repair_receipt": repair_receipt,
        "capability_registry": fact_graph["capability_registry"], "fact_command": fact_graph["command"], "before_state": before_fact_state,
        "after_state": after_fact_state, "proof": proof,
        "before_command_index": before_command_index, "command_index": command_index,
    }
    if outcome == "blocked":
        graph["dry_result"].update({
            "outcome": "blocked", "binding_digest": sha256_bytes(canonical_bytes(repair_binding_input(dry_request, audit_id, "blocked", schema_sha, registry_sha))),
            "token": None, "expires_at": None, "error_code": "REPAIR_PRECONDITION_FAILED",
        })
        blocked_receipt = {
            "contract": contract("repair-run-receipt-v1"), "schema_version": "1.0.0", "batch_id": target_batch["batch_id"], "outcome": "blocked",
            "nonce_status": None, "nonce_state_id": None, "fact_receipt_id": None, "transaction_id": None, "journal_id": None,
            "attempt_transaction_id": None, "attempt_journal_id": None, "business_marker_id": None, "business_marker_sha256": None,
            "recovery_receipt_id": None, "recovery_receipt_sha256": None,
            "retry_required": True, "error_code": "REPAIR_PRECONDITION_FAILED",
        }
        blocked_receipt["receipt_id"] = sha256_bytes(canonical_bytes(blocked_receipt))
        return {"drift_verdict": drift_verdict, "audit": audit, "dry_request": dry_request, "dry_result": graph["dry_result"], "repair_receipt": blocked_receipt}
    if outcome == "rolled-back":
        final_nonce = graph["nonce_states"][-1]
        final_nonce["status"] = "invalidated"
        final_nonce["state_id"] = sha256_bytes(canonical_bytes({key: value for key, value in final_nonce.items() if key != "state_id"}))
        nonce_target["after_sha256"] = sha256_bytes(canonical_bytes(final_nonce))
        nonce_target["after_image"]["sha256"] = nonce_target["after_sha256"]
        graph["marker"]["state"] = "rolled-back"
        recovery = {
            "contract": contract("recovery-receipt-v1"), "schema_version": "1.0.0", "journal_id": journal["journal_id"], "transaction_id": transaction_id,
            "outcome": "rolled-back", "recovered_at": "2026-07-24T02:11:00Z", "target_states": ["before"] * len(journal["targets"]), "error_code": None,
        }
        recovery["receipt_id"] = sha256_bytes(canonical_bytes(recovery))
        rolled_receipt = {
            "contract": contract("repair-run-receipt-v1"), "schema_version": "1.0.0", "batch_id": target_batch["batch_id"], "outcome": "rolled-back",
            "nonce_status": "invalidated", "nonce_state_id": final_nonce["state_id"], "fact_receipt_id": None,
            "transaction_id": transaction_id, "journal_id": journal["journal_id"],
            "attempt_transaction_id": None, "attempt_journal_id": None, "business_marker_id": None, "business_marker_sha256": None,
            "recovery_receipt_id": None, "recovery_receipt_sha256": None,
            "retry_required": True, "error_code": "REPAIR_TRANSACTION_ROLLED_BACK",
        }
        rolled_receipt["receipt_id"] = sha256_bytes(canonical_bytes(rolled_receipt))
        journal["manifest_id"] = sha256_bytes(canonical_bytes({key: value for key, value in journal.items() if key != "manifest_id"}))
        graph["marker"]["manifest_id"] = journal["manifest_id"]
        graph["marker"]["marker_id"] = sha256_bytes(canonical_bytes({key: value for key, value in graph["marker"].items() if key != "marker_id"}))
        graph.update({"fact_receipt": None, "repair_receipt": rolled_receipt, "recovery_receipt": recovery})
    journal["manifest_id"] = sha256_bytes(canonical_bytes({key: value for key, value in journal.items() if key != "manifest_id"}))
    graph["marker"]["manifest_id"] = journal["manifest_id"]
    graph["marker"]["marker_id"] = sha256_bytes(canonical_bytes({key: value for key, value in graph["marker"].items() if key != "marker_id"}))
    recovery = graph.get("recovery_receipt")
    if isinstance(recovery, dict):
        recovery["target_states"] = ["before"] * len(journal["targets"])
        recovery["receipt_id"] = sha256_bytes(canonical_bytes({key: value for key, value in recovery.items() if key != "receipt_id"}))
    handoff = repair_attempt_binding(transaction_id, journal["journal_id"], graph["marker"], recovery if isinstance(recovery, dict) else None)
    graph["repair_receipt"].update({key: handoff[key] for key in (
        "attempt_transaction_id", "attempt_journal_id", "business_marker_id", "business_marker_sha256",
        "recovery_receipt_id", "recovery_receipt_sha256",
    )})
    graph["repair_receipt"]["receipt_id"] = sha256_bytes(canonical_bytes({key: value for key, value in graph["repair_receipt"].items() if key != "receipt_id"}))
    before_index = {
        "contract": expected_contract_ref(registry, "repair-receipt-index/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "entries": [],
    }
    before_index["index_id"] = sha256_bytes(canonical_bytes(before_index))
    terminal_receipt = graph["repair_receipt"]
    repair_receipt_path = runtime_path(registry, "repair_receipt_template", transaction_id=transaction_id)
    lookup_id = repair_lookup_id(target_batch)
    after_index = {
        "contract": copy.deepcopy(before_index["contract"]), "schema_version": "1.0.0",
        "entries": [{
            "lookup_id": lookup_id, "sequence": 1, "batch_id": target_batch["batch_id"],
            "transaction_id": transaction_id,
            **{key: handoff[key] for key in ("attempt_transaction_id", "attempt_journal_id", "business_marker_id", "business_marker_sha256", "recovery_receipt_id", "recovery_receipt_sha256")},
            "outcome": terminal_receipt["outcome"],
            "receipt_path": repair_receipt_path, "receipt_sha256": sha256_bytes(canonical_bytes(terminal_receipt)),
        }],
    }
    after_index["index_id"] = sha256_bytes(canonical_bytes(after_index))
    before_attempt_ledger = {
        "contract": expected_contract_ref(registry, "repair-attempt-ledger/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "next_sequence": 1, "attempts": [],
    }
    before_attempt_ledger["ledger_id"] = sha256_bytes(canonical_bytes(before_attempt_ledger))
    attempt_entry = {
        "sequence": 1, "lookup_id": lookup_id, "batch_id": target_batch["batch_id"],
        "transaction_id": transaction_id, "business_journal_id": journal["journal_id"],
        **{key: handoff[key] for key in ("attempt_transaction_id", "attempt_journal_id", "business_marker_id", "business_marker_sha256", "recovery_receipt_id", "recovery_receipt_sha256")},
        "business_terminal_state": "committed" if terminal_receipt["outcome"] == "committed" else "rolled-back",
        "repair_receipt_id": terminal_receipt["receipt_id"], "repair_receipt_path": repair_receipt_path,
        "repair_receipt_sha256": sha256_bytes(canonical_bytes(terminal_receipt)), "recorded_at": "2026-07-24T02:12:00Z",
    }
    after_attempt_ledger = {
        "contract": copy.deepcopy(before_attempt_ledger["contract"]), "schema_version": "1.0.0",
        "next_sequence": 2, "attempts": [attempt_entry],
    }
    after_attempt_ledger["ledger_id"] = sha256_bytes(canonical_bytes(after_attempt_ledger))
    attempt_transaction_id = handoff["attempt_transaction_id"]
    attempt_journal, attempt_marker = transition_journal_fixture(
        "repair-attempt", attempt_transaction_id, handoff["attempt_journal_id"],
        [
            {"role": "repair-attempt-ledger", "operation": "replace", "path": registry["runtime_paths"]["repair_attempt_ledger"]["path"], "before_raw": canonical_bytes(before_attempt_ledger), "after_raw": canonical_bytes(after_attempt_ledger)},
            {"role": "repair-index", "operation": "replace", "path": registry["runtime_paths"]["repair_receipt_index"]["path"], "before_raw": canonical_bytes(before_index), "after_raw": canonical_bytes(after_index)},
        ],
        repair_receipt_path, canonical_bytes(terminal_receipt), registry, schema_sha, registry_sha,
    )
    journal["manifest_id"] = sha256_bytes(canonical_bytes({key: value for key, value in journal.items() if key != "manifest_id"}))
    graph["marker"]["manifest_id"] = journal["manifest_id"]
    graph["marker"]["marker_id"] = sha256_bytes(canonical_bytes({key: value for key, value in graph["marker"].items() if key != "marker_id"}))
    if isinstance(graph.get("recovery_receipt"), dict):
        graph["recovery_receipt"]["target_states"] = ["before"] * len(journal["targets"])
        graph["recovery_receipt"]["receipt_id"] = sha256_bytes(canonical_bytes({key: value for key, value in graph["recovery_receipt"].items() if key != "receipt_id"}))
    graph.update({
        "before_repair_index": before_index, "repair_index": after_index,
        "before_attempt_ledger": before_attempt_ledger, "attempt_ledger": after_attempt_ledger,
        "attempt_journal": attempt_journal, "attempt_marker": attempt_marker,
    })
    return graph


def repair_graph_semantics(
    graph: dict[str, Any], schema: dict[str, Any], registry: dict[str, Any], schema_sha: str, registry_sha: str,
    runtime_capability_bytes: bytes | None = None, runtime_root_registry_bytes: bytes | None = None,
    runtime_activation_bytes: bytes | None = None, runtime_attestation_bytes: bytes | None = None,
    authority_context: dict[str, Any] | None = None,
) -> bool:
    contract_names = {
        "drift_verdict": "action-projection-drift-verdict/1.0.0",
        "audit": "audit-finding-repair/2.0.0", "dry_request": "repair-dry-run-request/1.0.0",
        "dry_result": "repair-dry-run-result/1.0.0", "repair_receipt": "repair-run-receipt/1.0.0",
    }
    if not all(validate_registered(graph[name], schema, registry, contract_name, schema_sha, registry_sha) for name, contract_name in contract_names.items()):
        return False
    identity_documents = {
        "audit-finding-repair/2.0.0": graph["audit"], "repair-dry-run-request/1.0.0": graph["dry_request"],
    }
    if "capability_registry" in graph:
        identity_documents["writer-capability-registry/1.0.0"] = graph["capability_registry"]
    if isinstance(graph.get("fact_receipt"), dict):
        identity_documents["fact-mutation-receipt/1.0.0"] = graph["fact_receipt"]
    if not identity_sets_valid(identity_documents, registry, set(identity_documents)):
        return False

    audit = graph["audit"]
    drift = graph["drift_verdict"]
    if not (
        drift["verdict_id"] == sha256_bytes(canonical_bytes({key: value for key, value in drift.items() if key != "verdict_id"}))
        and audit["drift_verdict_id"] == drift["verdict_id"]
        and audit["audit_id"] == sha256_bytes(canonical_bytes({"drift_verdict_id": drift["verdict_id"], "finding_algorithm": "drift-finding-to-repair-v2"}))
    ):
        return False
    expected_drift_findings: dict[str, dict[str, Any]] = {}
    for drift_row in drift["workstreams"]:
        derived_ids = [row["finding_id"] for row in drift_row["findings"]]
        if drift_row["finding_ids"] != derived_ids or derived_ids != sorted(set(derived_ids), key=lambda value: value.encode("utf-8")):
            return False
        action_diffs = []
        for typed in drift_row["findings"]:
            identity_body = {key: value for key, value in typed.items() if key not in {"finding_id", "source_path", "source_line"}}
            if typed["finding_id"] != sha256_bytes(canonical_bytes(identity_body)) or typed["workstream_id"] != drift_row["workstream_id"]:
                return False
            if typed["repairability"] == "repairable":
                diff = typed["action_diff"]
                if not isinstance(diff, dict) or typed["kind"] != "action-projection-drift" or typed["action_id"] != diff["action_id"]:
                    return False
                action_diffs.append(diff)
                refs, action_ids, repair_batch = [{"entity_type": "action", "id": diff["action_id"]}], [diff["action_id"]], "required"
            else:
                if typed["action_id"] is not None or typed["action_diff"] is not None:
                    return False
                refs, action_ids, repair_batch = [{"entity_type": "workstream", "id": drift_row["workstream_id"]}], [], None
            expected_drift_findings[typed["finding_id"]] = {
                "finding_id": typed["finding_id"], "kind": typed["kind"], "severity": typed["severity"],
                "workflow": "adp-status-sync", "workstream_id": drift_row["workstream_id"], "operation": "refresh_actions",
                "entity_refs": refs, "action_ids": action_ids, "source_path": typed["source_path"], "source_line": typed["source_line"],
                "repair_batch_required": repair_batch,
            }
        if action_diffs != drift_row["action_diffs"]:
            return False
    batches = {row["batch_id"]: row for row in audit["repair_batches"]}
    findings = {row["finding_id"]: row for row in audit["findings"]}
    if len(batches) != len(audit["repair_batches"]) or len(findings) != len(audit["findings"]):
        return False
    if set(findings) != set(expected_drift_findings):
        return False
    for finding_id, expected_finding in expected_drift_findings.items():
        required = expected_finding.pop("repair_batch_required")
        if {key: findings[finding_id][key] for key in expected_finding} != expected_finding:
            return False
        if (findings[finding_id]["repair_batch_id"] is not None) != (required == "required"):
            return False
    repairable = [row for row in findings.values() if row["severity"] == "blocked"]
    if any(row["repair_batch_id"] is None for row in repairable):
        return False
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for finding in repairable:
        key = (finding["workflow"], finding["workstream_id"], finding["operation"])
        groups.setdefault(key, []).append(finding)
    batches_by_group: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for batch in batches.values():
        key = (batch["command"]["workflow"], batch["command"]["workstream_id"], batch["command"]["operation"])
        batches_by_group.setdefault(key, []).append(batch)
    if set(groups) != set(batches_by_group) or any(len(rows) != 1 for rows in batches_by_group.values()):
        return False
    for key, group_findings in groups.items():
        batch = batches_by_group[key][0]
        expected_finding_ids = sorted((row["finding_id"] for row in group_findings), key=lambda value: value.encode("utf-8"))
        if batch["finding_ids"] != expected_finding_ids or any(row["repair_batch_id"] != batch["batch_id"] for row in group_findings):
            return False
    for finding in findings.values():
        action_refs = [row["id"] for row in finding["entity_refs"] if row["entity_type"] == "action"]
        if action_refs != finding["action_ids"] or len(action_refs) != len(set(action_refs)):
            return False
        if finding["repair_batch_id"] is None:
            continue
        if finding["repair_batch_id"] not in batches or finding["finding_id"] not in batches[finding["repair_batch_id"]]["finding_ids"]:
            return False
    for batch in batches.values():
        if batch["based_on_audit_id"] != audit["audit_id"] or any(fid not in findings or findings[fid]["repair_batch_id"] != batch["batch_id"] for fid in batch["finding_ids"]):
            return False
        finding_actions = sorted({aid for fid in batch["finding_ids"] for aid in findings[fid]["action_ids"]}, key=lambda value: value.encode())
        command_actions = batch["command"]["action_ids"]
        reads = batch["read_set"]["action_revisions"]
        read_actions = [row["action_id"] for row in reads]
        if command_actions != sorted(command_actions, key=lambda value: value.encode()) or read_actions != sorted(read_actions, key=lambda value: value.encode()) or finding_actions != command_actions or command_actions != read_actions or len(read_actions) != len(set(read_actions)):
            return False
        wdrs = batch["read_set"]["wdr_revisions"]
        if len(wdrs) != 1 or wdrs[0]["workstream_id"] != batch["command"]["workstream_id"] or wdrs[0]["wdr_revision"] != batch["command"]["expected_wdr_revision"] or wdrs[0]["file_generation"] != batch["command"]["expected_file_generation"]:
            return False
        sources = [(row["root_instance_id"], row["path"]) for row in batch["read_set"]["source_records"]]
        if len(sources) != len(set(sources)):
            return False
        core = {key: batch[key] for key in ("based_on_audit_id", "finding_ids", "command", "read_set")}
        if batch["batch_digest"] != sha256_bytes(canonical_bytes(core)):
            return False
        identity = {"workflow": batch["command"]["workflow"], "workstream_id": batch["command"]["workstream_id"], "operation": batch["command"]["operation"], "finding_ids": batch["finding_ids"], "batch_digest": batch["batch_digest"]}
        if batch["batch_id"] != sha256_bytes(canonical_bytes(identity)):
            return False

    dry_request, dry_result = (graph[name] for name in ("dry_request", "dry_result"))
    batch = batches.get(dry_request["batch"]["batch_id"])
    if batch is None or dry_request["batch"] != batch:
        return False
    expected_binding = sha256_bytes(canonical_bytes(repair_binding_input(dry_request, audit["audit_id"], dry_result["outcome"], schema_sha, registry_sha)))
    if dry_result["outcome"] == "blocked":
        receipt = graph["repair_receipt"]
        allowed_keys = {"drift_verdict", "audit", "dry_request", "dry_result", "repair_receipt"}
        return (
            set(graph) == allowed_keys
            and dry_result["dry_run_id"] == sha256_bytes(canonical_bytes(dry_request))
            and dry_result["batch_id"] == batch["batch_id"] and dry_result["binding_digest"] == expected_binding
            and dry_result["token"] is None and dry_result["expires_at"] is None and bool(dry_result["error_code"])
            and receipt["receipt_id"] == sha256_bytes(canonical_bytes({key: value for key, value in receipt.items() if key != "receipt_id"}))
            and receipt["batch_id"] == batch["batch_id"] and receipt["outcome"] == "blocked"
            and receipt["nonce_status"] is None and receipt["nonce_state_id"] is None and receipt["fact_receipt_id"] is None
            and receipt["transaction_id"] is None and receipt["journal_id"] is None and receipt["retry_required"] and bool(receipt["error_code"])
            and all(receipt[key] is None for key in (
                "attempt_transaction_id", "attempt_journal_id", "business_marker_id", "business_marker_sha256",
                "recovery_receipt_id", "recovery_receipt_sha256",
            ))
        )
    extended = {
        "apply_request": "repair-apply-request/1.0.0", "journal": "transaction-journal-manifest/1.0.0",
        "marker": "journal-marker/1.0.0", "attempt_journal": "transaction-journal-manifest/1.0.0",
        "attempt_marker": "journal-marker/1.0.0", "before_attempt_ledger": "repair-attempt-ledger/1.0.0",
        "attempt_ledger": "repair-attempt-ledger/1.0.0",
    }
    if not all(name in graph and validate_registered(graph[name], schema, registry, contract_name, schema_sha, registry_sha) for name, contract_name in extended.items()):
        return False
    apply_request = graph["apply_request"]
    nonce_states = graph.get("nonce_states")
    if not isinstance(nonce_states, list) or len(nonce_states) != 3 or not all(validate_registered(row, schema, registry, "repair-nonce-state/1.0.0", schema_sha, registry_sha) for row in nonce_states):
        return False
    if dry_result["token"] is None:
        return False
    try:
        issued = datetime.fromisoformat(dry_result["issued_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(dry_result["expires_at"].replace("Z", "+00:00"))
        applied = datetime.fromisoformat(apply_request["applied_at"].replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if not (issued <= applied <= expires <= issued + timedelta(minutes=15)):
        return False
    if not (
        dry_result["dry_run_id"] == sha256_bytes(canonical_bytes(dry_request))
        and dry_result["batch_id"] == batch["batch_id"]
        and dry_result["outcome"] == "applicable"
        and dry_result["binding_digest"] == expected_binding
        and apply_request["principal"] == dry_request["principal"]
        and apply_request["batch_id"] == batch["batch_id"]
        and apply_request["batch_digest"] == batch["batch_digest"]
        and apply_request["token"] == dry_result["token"]
    ):
        return False

    token_hash = sha256_bytes(dry_result["token"].encode("utf-8"))
    expected_final_status = "consumed" if graph["repair_receipt"]["outcome"] == "committed" else "invalidated"
    if [row["status"] for row in nonce_states] != ["unused", "reserved", expected_final_status]:
        return False
    for index, nonce in enumerate(nonce_states):
        body = {key: value for key, value in nonce.items() if key != "state_id"}
        if not (
            nonce["state_id"] == sha256_bytes(canonical_bytes(body))
            and nonce["nonce_id"] == token_hash == nonce["token_hash"]
            and nonce["batch_id"] == batch["batch_id"]
            and nonce["binding_digest"] == expected_binding
            and nonce["expires_at"] == dry_result["expires_at"]
            and nonce["previous_state_id"] == (None if index == 0 else nonce_states[index - 1]["state_id"])
        ):
            return False
    final_nonce = nonce_states[-1]
    if nonce_states[0]["reserved_by"] is not None or nonce_states[0]["transaction_id"] is not None:
        return False
    if any(row["reserved_by"] != dry_request["principal"] or row["transaction_id"] != graph["journal"]["transaction_id"] for row in nonce_states[1:]):
        return False

    journal, marker = graph["journal"], graph["marker"]
    if journal["transaction_kind"] != "repair" or not journal_semantics(journal, marker, schema, registry, schema_sha, registry_sha):
        return False
    business = [row for row in journal["targets"] if row["role"] == "business"]
    generation = [row for row in journal["targets"] if row["role"] == "fact-generation"]
    nonce_targets = [row for row in journal["targets"] if row["role"] == "nonce"]
    receipt_targets = [row for row in journal["targets"] if row["role"] == "receipt"]
    if len(generation) != 1 or len(nonce_targets) != 1 or len(receipt_targets) != 1:
        return False
    nonce_target = nonce_targets[0]
    if not (
        nonce_target["before_sha256"] == sha256_bytes(canonical_bytes(nonce_states[1]))
        and nonce_target["after_sha256"] == sha256_bytes(canonical_bytes(final_nonce))
        and nonce_target["path"] == runtime_path(registry, "repair_nonce_template", nonce_id=token_hash)
    ):
        return False
    before_index, after_index = graph.get("before_repair_index"), graph.get("repair_index")
    attempt_journal, attempt_marker = graph["attempt_journal"], graph["attempt_marker"]
    if attempt_journal["transaction_kind"] != "repair-attempt" or not journal_semantics(attempt_journal, attempt_marker, schema, registry, schema_sha, registry_sha):
        return False
    index_target = next(row for row in attempt_journal["targets"] if row["role"] == "repair-index")
    attempt_target = next(row for row in attempt_journal["targets"] if row["role"] == "repair-attempt-ledger")
    attempt_receipt_target = next(row for row in attempt_journal["targets"] if row["role"] == "receipt")
    before_attempt, after_attempt = graph["before_attempt_ledger"], graph["attempt_ledger"]
    if not (
        isinstance(before_index, dict) and isinstance(after_index, dict)
        and validate_registered(before_index, schema, registry, "repair-receipt-index/1.0.0", schema_sha, registry_sha)
        and validate_registered(after_index, schema, registry, "repair-receipt-index/1.0.0", schema_sha, registry_sha)
        and before_index["index_id"] == sha256_bytes(canonical_bytes({key: value for key, value in before_index.items() if key != "index_id"}))
        and after_index["index_id"] == sha256_bytes(canonical_bytes({key: value for key, value in after_index.items() if key != "index_id"}))
        and len(after_index["entries"]) == len(before_index["entries"]) + 1
        and after_index["entries"][:-1] == before_index["entries"]
        and index_target["path"] == registry["runtime_paths"]["repair_receipt_index"]["path"]
        and index_target["before_sha256"] == sha256_bytes(canonical_bytes(before_index))
        and index_target["after_sha256"] == sha256_bytes(canonical_bytes(after_index))
        and before_attempt["ledger_id"] == sha256_bytes(canonical_bytes({key: value for key, value in before_attempt.items() if key != "ledger_id"}))
        and after_attempt["ledger_id"] == sha256_bytes(canonical_bytes({key: value for key, value in after_attempt.items() if key != "ledger_id"}))
        and len(after_attempt["attempts"]) == len(before_attempt["attempts"]) + 1
        and after_attempt["attempts"][:-1] == before_attempt["attempts"]
        and after_attempt["next_sequence"] == before_attempt["next_sequence"] + 1
        and attempt_target["path"] == registry["runtime_paths"]["repair_attempt_ledger"]["path"]
        and attempt_target["before_sha256"] == sha256_bytes(canonical_bytes(before_attempt))
        and attempt_target["after_sha256"] == sha256_bytes(canonical_bytes(after_attempt))
    ):
        return False
    index_entry = after_index["entries"][-1]
    repair_receipt = graph["repair_receipt"]
    recovery_for_handoff = graph.get("recovery_receipt")
    expected_handoff = repair_attempt_binding(
        journal["transaction_id"], journal["journal_id"], marker,
        recovery_for_handoff if isinstance(recovery_for_handoff, dict) else None,
    )
    handoff_fields = (
        "attempt_transaction_id", "attempt_journal_id", "business_marker_id", "business_marker_sha256",
        "recovery_receipt_id", "recovery_receipt_sha256",
    )
    if not (
        attempt_journal["transaction_id"] == expected_handoff["attempt_transaction_id"]
        and attempt_journal["journal_id"] == expected_handoff["attempt_journal_id"]
        and attempt_journal["transaction_id"] != journal["transaction_id"]
        and attempt_journal["journal_id"] != journal["journal_id"]
        and all(repair_receipt[key] == expected_handoff[key] for key in handoff_fields)
        and all(index_entry[key] == expected_handoff[key] for key in handoff_fields)
        and index_entry["lookup_id"] == repair_lookup_id(batch)
        and index_entry["sequence"] == len(after_index["entries"])
        and index_entry["batch_id"] == batch["batch_id"]
        and index_entry["transaction_id"] == journal["transaction_id"]
        and index_entry["outcome"] == repair_receipt["outcome"]
        and index_entry["receipt_path"] == runtime_path(registry, "repair_receipt_template", transaction_id=journal["transaction_id"])
        and index_entry["receipt_sha256"] == sha256_bytes(canonical_bytes(repair_receipt))
        and after_index["entries"] == sorted(after_index["entries"], key=lambda row: row["sequence"])
        and [row["sequence"] for row in after_index["entries"]] == list(range(1, len(after_index["entries"]) + 1))
        and after_attempt["attempts"][-1] == {
            "sequence": index_entry["sequence"], "lookup_id": index_entry["lookup_id"], "batch_id": index_entry["batch_id"],
            "transaction_id": index_entry["transaction_id"], "business_journal_id": journal["journal_id"],
            **{key: expected_handoff[key] for key in handoff_fields},
            "business_terminal_state": index_entry["outcome"], "repair_receipt_id": repair_receipt["receipt_id"],
            "repair_receipt_path": index_entry["receipt_path"], "repair_receipt_sha256": index_entry["receipt_sha256"],
            "recorded_at": after_attempt["attempts"][-1]["recorded_at"],
        }
        and attempt_receipt_target["path"] == index_entry["receipt_path"]
        and attempt_receipt_target["after_sha256"] == index_entry["receipt_sha256"]
    ):
        return False

    if graph["repair_receipt"]["outcome"] == "rolled-back":
        receipt = graph["repair_receipt"]
        recovery = graph.get("recovery_receipt")
        return (
            marker["state"] == "rolled-back" and graph.get("fact_receipt") is None
            and isinstance(recovery, dict) and validate_registered(recovery, schema, registry, "recovery-receipt/1.0.0", schema_sha, registry_sha)
            and recovery["receipt_id"] == sha256_bytes(canonical_bytes({key: value for key, value in recovery.items() if key != "receipt_id"}))
            and recovery["journal_id"] == journal["journal_id"] and recovery["transaction_id"] == journal["transaction_id"]
            and recovery["outcome"] == "rolled-back" and recovery["target_states"] == ["before"] * len(journal["targets"])
            and receipt["receipt_id"] == sha256_bytes(canonical_bytes({key: value for key, value in receipt.items() if key != "receipt_id"}))
            and receipt["batch_id"] == batch["batch_id"] and receipt["nonce_status"] == "invalidated"
            and receipt["nonce_state_id"] == final_nonce["state_id"] and receipt["fact_receipt_id"] is None
            and receipt["transaction_id"] == journal["transaction_id"] and receipt["journal_id"] == journal["journal_id"]
            and receipt["retry_required"] and bool(receipt["error_code"])
            and attempt_marker["state"] == "committed"
        )
    if marker["state"] != "committed" or graph["repair_receipt"]["outcome"] != "committed" or not validate_registered(graph.get("fact_receipt"), schema, registry, "fact-mutation-receipt/1.0.0", schema_sha, registry_sha):
        return False

    fact_receipt, repair_receipt = graph["fact_receipt"], graph["repair_receipt"]
    fact_command = graph.get("fact_command")
    if not isinstance(fact_command, dict) or not (
        fact_command.get("operation") == "patch"
        and fact_command.get("workstream_id") == batch["command"]["workstream_id"]
        and fact_command.get("expected_wdr_revision") == batch["command"]["expected_wdr_revision"]
        and fact_command.get("expected_file_generation") == batch["command"]["expected_file_generation"]
        and fact_command.get("set") == {"refresh_actions": True}
    ):
        return False
    fact_subgraph = {
        "capability_registry": graph.get("capability_registry"), "command": fact_command, "journal": journal, "marker": marker,
        "before_state": graph.get("before_state"), "after_state": graph.get("after_state"), "receipt": fact_receipt, "proof": graph.get("proof"),
        "before_command_index": graph.get("before_command_index"), "command_index": graph.get("command_index"),
        "before_outbox": graph.get("before_outbox"), "after_outbox": graph.get("after_outbox"),
    }
    if any(value is None for value in (runtime_capability_bytes, runtime_root_registry_bytes, runtime_activation_bytes, authority_context)) or not fact_attribution_semantics(
        fact_subgraph, registry, schema, schema_sha, registry_sha, runtime_capability_bytes,
        runtime_root_registry_bytes, runtime_activation_bytes, runtime_attestation_bytes, authority_context,
    ):
        return False
    try:
        proof_by_path = {row["path"]: row for row in graph["proof"]["business_artifacts"]}
        workstream_id = batch["command"]["workstream_id"]
        wdr_path = f"workstreams/{workstream_id}/delivery-record.md"
        wdr_state_path = f"workstreams/{workstream_id}/delivery-record.state.json"
        sidecar_path = f"workstreams/{workstream_id}/action-projection.json"
        wdr_before = artifact_bytes(proof_by_path[wdr_path]["before_bytes"])
        wdr_state_before = json.loads(artifact_bytes(proof_by_path[wdr_state_path]["before_bytes"]))
        sidecar_before = json.loads(artifact_bytes(proof_by_path[sidecar_path]["before_bytes"]))
        sidecar_after = json.loads(artifact_bytes(proof_by_path[sidecar_path]["after_bytes"]))
        reads = {row["path"]: artifact_bytes(row["bytes"]) for row in graph["proof"]["read_artifacts"]}
        ledger_raw = reads[registry["runtime_paths"]["action_ledger"]["path"]]
        ledger_state = json.loads(reads[registry["runtime_paths"]["action_ledger_state"]["path"]])
        ledger_rows = parse_action_ledger(ledger_raw)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    if not (
        validate_registered(ledger_state, schema, registry, "action-ledger-state/1.0.0", schema_sha, registry_sha)
        and ledger_state == action_ledger_state_document(
            ledger_rows, ledger_raw, ledger_state["ledger_revision"], ledger_state["applied_commands"], registry, schema_sha, registry_sha
        )
        and batch["read_set"]["ledger_fingerprint"] == sha256_bytes(ledger_raw) == ledger_state["ledger_fingerprint"]
    ):
        return False
    ledger_by_id = {row["action_id"]: row for row in ledger_rows}
    for claim in batch["read_set"]["action_revisions"]:
        row = ledger_by_id.get(claim["action_id"])
        if claim["expected_present"] != (row is not None) or claim["revision"] != (None if row is None else row["action_revision"]):
            return False
    expected_drift_row = expected_drift_verdict({
        "generation_id": drift["generation_id"], "selection_policy_id": drift["selection_policy_id"],
        "selected_workstreams": [workstream_id], "ledger_raw": ledger_raw, "ledger_state": ledger_state,
        "wdrs": {workstream_id: wdr_before}, "wdr_states": {workstream_id: wdr_state_before},
        "sidecars": {workstream_id: sidecar_before},
    }, registry, schema_sha, registry_sha)["workstreams"][0]
    actual_drift_rows = [row for row in drift["workstreams"] if row["workstream_id"] == workstream_id]
    expected_after_snapshot = action_snapshot(ledger_rows, workstream_id, ledger_state["ledger_fingerprint"], ledger_state["ledger_revision"])
    if (
        wdr_before is None
        or sha256_bytes(wdr_before) != batch["read_set"]["wdr_revisions"][0]["fingerprint"]
        or len(actual_drift_rows) != 1 or actual_drift_rows[0] != expected_drift_row
        or sidecar_after["ledger_fingerprint"] != batch["read_set"]["ledger_fingerprint"]
        or sidecar_after["actions"] != expected_after_snapshot["actions"]
    ):
        return False
    expected_business_paths = [
        f"workstreams/{batch['command']['workstream_id']}/delivery-record.md",
        f"workstreams/{batch['command']['workstream_id']}/delivery-record.state.json",
        f"workstreams/{batch['command']['workstream_id']}/action-projection.json",
    ]
    if [row["path"] for row in business] != expected_business_paths or any(row["root_instance_id"] != dry_request["memory_root_instance_id"] for row in business):
        return False
    if not (
        fact_receipt["receipt_id"] == sha256_bytes(canonical_bytes({key: value for key, value in fact_receipt.items() if key != "receipt_id"}))
        and fact_receipt["transaction_id"] == journal["transaction_id"]
        and fact_receipt["journal_id"] == journal["journal_id"]
        and fact_receipt["authorization"] == journal["authorization"]
        and fact_receipt["authorization"]["authorized_command_fingerprint"] == sha256_bytes(canonical_bytes(graph["fact_command"]))
        and fact_receipt["business_targets"] == business
        and fact_receipt["generation_state_target"] == generation[0]
        and fact_receipt["before_fact_generation"] == batch["read_set"]["fact_generation"]
        and fact_receipt["after_fact_generation"] == batch["read_set"]["fact_generation"] + 1
        and fact_receipt["action_deltas"] == []
    ):
        return False
    if not (
        repair_receipt["receipt_id"] == sha256_bytes(canonical_bytes({key: value for key, value in repair_receipt.items() if key != "receipt_id"}))
        and repair_receipt["batch_id"] == batch["batch_id"]
        and repair_receipt["outcome"] == "committed"
        and repair_receipt["nonce_status"] == final_nonce["status"]
        and repair_receipt["nonce_state_id"] == final_nonce["state_id"]
        and repair_receipt["fact_receipt_id"] == fact_receipt["receipt_id"]
        and repair_receipt["transaction_id"] == journal["transaction_id"]
        and repair_receipt["journal_id"] == journal["journal_id"]
    ):
        return False
    return (
        receipt_targets[0]["path"] == runtime_path(registry, "repair_fact_receipt_template", transaction_id=journal["transaction_id"])
        and receipt_targets[0]["after_sha256"] == sha256_bytes(canonical_bytes(fact_receipt))
        and attempt_receipt_target["path"] == runtime_path(registry, "repair_receipt_template", transaction_id=journal["transaction_id"])
        and attempt_receipt_target["after_sha256"] == sha256_bytes(canonical_bytes(repair_receipt))
    )


def two_batch_repair_restart_semantics(
    schema: dict[str, Any], registry: dict[str, Any], schema_sha: str, registry_sha: str,
) -> bool:
    def group_key(graph: dict[str, Any]) -> tuple[str, str, str, tuple[str, ...]]:
        batch = graph["dry_request"]["batch"]
        command = batch["command"]
        return command["workflow"], command["workstream_id"], command["operation"], tuple(batch["finding_ids"])

    def graph_valid(graph: dict[str, Any]) -> bool:
        return repair_graph_semantics(
            graph, schema, registry, schema_sha, registry_sha,
            *runtime_authority_fixture(registry, schema_sha, registry_sha, "adp-status-sync"),
        )

    probe = repair_graph_fixture(schema_sha, registry_sha, registry)
    ordered_workstreams = [row["command"]["workstream_id"] for row in probe["audit"]["repair_batches"]]
    if len(ordered_workstreams) != 2 or len(set(ordered_workstreams)) != 2:
        return False
    first_workstream, second_workstream = ordered_workstreams
    first = repair_graph_fixture(
        schema_sha, registry_sha, registry, target_workstream_id=first_workstream,
        fact_generation=7, token_char="A", transaction_suffix="batch-a",
    )
    stale_second = repair_graph_fixture(
        schema_sha, registry_sha, registry, outcome="rolled-back", target_workstream_id=second_workstream,
        fact_generation=7, token_char="B", transaction_suffix="batch-b-stale",
    )
    retry_second = repair_graph_fixture(
        schema_sha, registry_sha, registry, target_workstream_id=second_workstream,
        fact_generation=8, token_char="C", transaction_suffix="batch-b-retry",
        prior_transaction_id=first["after_state"]["last_transaction_id"],
    )
    if not all(graph_valid(graph) for graph in (first, stale_second, retry_second)):
        return False

    first_key = group_key(first)
    second_key = group_key(stale_second)
    if first_key == second_key:
        return False
    current_fact_state = copy.deepcopy(first["after_state"])
    if stale_second["before_state"] == current_fact_state:
        return False
    if not (
        stale_second["repair_receipt"]["outcome"] == "rolled-back"
        and stale_second["repair_receipt"]["nonce_status"] == "invalidated"
        and stale_second["repair_receipt"]["retry_required"]
        and stale_second["fact_receipt"] is None
    ):
        return False
    if not (
        group_key(retry_second) == second_key
        and retry_second["before_state"] == current_fact_state
        and retry_second["dry_request"]["batch"]["read_set"]["fact_generation"] == 8
        and retry_second["dry_result"]["token"] != stale_second["dry_result"]["token"]
        and retry_second["dry_result"]["binding_digest"] != stale_second["dry_result"]["binding_digest"]
        and retry_second["dry_request"]["batch"]["batch_id"] != stale_second["dry_request"]["batch"]["batch_id"]
    ):
        return False
    lookup_order = [repair_lookup_id(first["dry_request"]["batch"]), repair_lookup_id(stale_second["dry_request"]["batch"])]
    if lookup_order[0] == lookup_order[1]:
        return False

    def index_document(graphs: list[dict[str, Any]]) -> dict[str, Any]:
        entries = []
        for sequence, graph in enumerate(graphs, start=1):
            receipt = graph["repair_receipt"]
            path = runtime_path(registry, "repair_receipt_template", transaction_id=graph["journal"]["transaction_id"])
            entries.append({
                "lookup_id": repair_lookup_id(graph["dry_request"]["batch"]), "sequence": sequence,
                "batch_id": graph["dry_request"]["batch"]["batch_id"], "transaction_id": graph["journal"]["transaction_id"],
                **{key: receipt[key] for key in (
                    "attempt_transaction_id", "attempt_journal_id", "business_marker_id", "business_marker_sha256",
                    "recovery_receipt_id", "recovery_receipt_sha256",
                )},
                "outcome": receipt["outcome"], "receipt_path": path, "receipt_sha256": sha256_bytes(canonical_bytes(receipt)),
            })
        index = {
            "contract": expected_contract_ref(registry, "repair-receipt-index/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "entries": entries,
        }
        index["index_id"] = sha256_bytes(canonical_bytes(index))
        return index

    def attempt_handoff_fault_probe(graph: dict[str, Any]) -> bool:
        journal = graph["journal"]
        business_marker = graph["marker"]
        attempt_journal = graph["attempt_journal"]
        attempt_marker = graph["attempt_marker"]
        before_by_role = {
            "repair-attempt-ledger": canonical_bytes(graph["before_attempt_ledger"]),
            "repair-index": canonical_bytes(graph["before_repair_index"]),
            "receipt": None,
        }
        after_by_role = {
            "repair-attempt-ledger": canonical_bytes(graph["attempt_ledger"]),
            "repair-index": canonical_bytes(graph["repair_index"]),
            "receipt": canonical_bytes(graph["repair_receipt"]),
        }
        child = r'''
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]);business_manifest_path=sys.argv[2];business_marker_path=sys.argv[3];marked_at=sys.argv[4]
canon=lambda value:json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
digest=lambda raw:'sha256:'+hashlib.sha256(raw).hexdigest()
token=lambda value:'i_'+hashlib.sha256(value.encode()).hexdigest()
bj_raw=(root/business_manifest_path).read_bytes();bm_raw=(root/business_marker_path).read_bytes()
bj=json.loads(bj_raw);bm=json.loads(bm_raw)
assert canon(bj)==bj_raw and canon(bm)==bm_raw and bm['state'] in {'committed','rolled-back'}
assert bm['journal_id']==bj['journal_id'] and bm['manifest_id']==bj['manifest_id']
assert bm['marker_id']==digest(canon({k:v for k,v in bm.items() if k!='marker_id'}))
recovery_path=root/bj['recovery_receipt_path'];recovery=None
if recovery_path.exists():
 recovery_raw=recovery_path.read_bytes();recovery=json.loads(recovery_raw);assert canon(recovery)==recovery_raw
body={'business_transaction_id':bj['transaction_id'],'business_journal_id':bj['journal_id'],'business_marker_id':bm['marker_id'],'business_marker_sha256':digest(bm_raw),'recovery_receipt_id':None if recovery is None else recovery['receipt_id'],'recovery_receipt_sha256':None if recovery is None else digest(recovery_raw)}
suffix=digest(canon(body)).removeprefix('sha256:');attempt_tx='repair-attempt:'+suffix;attempt_journal_id='journal-repair-attempt:'+suffix
attempt_dir=pathlib.Path('state/transactions')/token(attempt_tx);attempt_manifest_path=attempt_dir/'manifest.json'
aj_raw=(root/attempt_manifest_path).read_bytes();aj=json.loads(aj_raw)
assert canon(aj)==aj_raw and aj['manifest_id']==digest(canon({k:v for k,v in aj.items() if k!='manifest_id'}))
assert aj['transaction_id']==attempt_tx and aj['journal_id']==attempt_journal_id and pathlib.Path(aj['journal_dir'])==attempt_dir
assert [row['role'] for row in aj['targets']]==['repair-attempt-ledger','repair-index','receipt']
for target in aj['targets']:
 before=None if target['before_image'] is None else (root/target['before_image']['path']).read_bytes()
 after=None if target['after_image'] is None else (root/target['after_image']['path']).read_bytes()
 assert (None if before is None else digest(before))==target['before_sha256'] and (None if after is None else digest(after))==target['after_sha256']
 target_path=root/target['path'];current=target_path.read_bytes() if target_path.exists() else None
 assert current in (before,after)
 target_path.parent.mkdir(parents=True,exist_ok=True)
 if after is None:
  if target_path.exists():target_path.unlink()
 else:target_path.write_bytes(after)
marker={'contract':bm['contract'],'schema_version':'1.0.0','journal_id':aj['journal_id'],'manifest_id':aj['manifest_id'],'state':'committed','marked_at':marked_at}
marker['marker_id']=digest(canon(marker));terminal=root/aj['terminal_marker_path'];terminal.parent.mkdir(parents=True,exist_ok=True);terminal.write_bytes(canon(marker))
loaded=[(root/target['path']).read_bytes() for target in aj['targets']]
assert all(digest(raw)==target['after_sha256'] for raw,target in zip(loaded,aj['targets']))
attempt_ledger,repair_index,repair_receipt=(json.loads(raw) for raw in loaded)
index_entry=repair_index['entries'][-1];attempt_entry=attempt_ledger['attempts'][-1]
expected={**body,'attempt_transaction_id':attempt_tx,'attempt_journal_id':attempt_journal_id}
fields=['attempt_transaction_id','attempt_journal_id','business_marker_id','business_marker_sha256','recovery_receipt_id','recovery_receipt_sha256']
assert all(document[field]==expected[field] for document in (repair_receipt,index_entry,attempt_entry) for field in fields)
assert index_entry['receipt_sha256']==digest(canon(repair_receipt))==attempt_entry['repair_receipt_sha256']
assert json.loads(terminal.read_bytes())==marker
print(json.dumps([marker['marker_id'],attempt_ledger['ledger_id'],repair_index['index_id'],repair_receipt['receipt_id']],separators=(',',':')))
'''

        def write(root: Path, path: str, raw: bytes) -> None:
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)

        expected_output = [
            attempt_marker["marker_id"], graph["attempt_ledger"]["ledger_id"],
            graph["repair_index"]["index_id"], graph["repair_receipt"]["receipt_id"],
        ]
        for applied_count in range(len(attempt_journal["targets"]) + 1):
            with tempfile.TemporaryDirectory() as folder_name:
                root = Path(folder_name)
                write(root, journal["manifest_path"], canonical_bytes(journal))
                write(root, journal["terminal_marker_path"], canonical_bytes(business_marker))
                if isinstance(graph.get("recovery_receipt"), dict):
                    write(root, journal["recovery_receipt_path"], canonical_bytes(graph["recovery_receipt"]))
                write(root, attempt_journal["manifest_path"], canonical_bytes(attempt_journal))
                for target in attempt_journal["targets"]:
                    before_raw = before_by_role[target["role"]]
                    after_raw = after_by_role[target["role"]]
                    if before_raw is not None:
                        write(root, target["before_image"]["path"], before_raw)
                    write(root, target["after_image"]["path"], after_raw)
                    selected = after_raw if target["apply_order"] < applied_count else before_raw
                    if selected is not None:
                        write(root, target["path"], selected)
                completed = subprocess.run(
                    [
                        sys.executable, "-c", child, str(root), journal["manifest_path"],
                        journal["terminal_marker_path"], attempt_marker["marked_at"],
                    ],
                    check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30,
                )
                if completed.returncode != 0:
                    return False
                try:
                    observed = json.loads(completed.stdout)
                except json.JSONDecodeError:
                    return False
                if observed != expected_output:
                    return False
                if (root / attempt_journal["terminal_marker_path"]).read_bytes() != canonical_bytes(attempt_marker):
                    return False
                for target in attempt_journal["targets"]:
                    if (root / target["path"]).read_bytes() != after_by_role[target["role"]]:
                        return False
        return True

    child = (
        "import hashlib,json,pathlib,sys;"
        "root=pathlib.Path(sys.argv[1]);idx=json.loads((root/sys.argv[2]).read_bytes());"
        "body={k:v for k,v in idx.items() if k!='index_id'};"
        "canon=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode();"
        "assert idx['index_id']=='sha256:'+hashlib.sha256(canon).hexdigest();"
        "latest={};"
        "exec(\"for e in idx['entries']:\\n raw=(root/e['receipt_path']).read_bytes()\\n assert e['receipt_sha256']=='sha256:'+hashlib.sha256(raw).hexdigest()\\n r=json.loads(raw)\\n assert r['batch_id']==e['batch_id'] and r['outcome']==e['outcome']\\n latest[e['lookup_id']]=r['outcome']\");"
        "print(json.dumps([latest.get(x) for x in json.loads(sys.argv[3])]))"
    )

    def fresh_snapshot(folder: Path, graphs: list[dict[str, Any]]) -> list[str | None] | None:
        index = index_document(graphs)
        index_path = registry["runtime_paths"]["repair_receipt_index"]["path"]
        target = folder / index_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(canonical_bytes(index))
        for graph in graphs:
            receipt_path = runtime_path(registry, "repair_receipt_template", transaction_id=graph["journal"]["transaction_id"])
            receipt_target = folder / receipt_path
            receipt_target.parent.mkdir(parents=True, exist_ok=True)
            receipt_target.write_bytes(canonical_bytes(graph["repair_receipt"]))
        completed = subprocess.run(
            [sys.executable, "-c", child, str(folder), index_path, json.dumps(lookup_order)],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30,
        )
        return json.loads(completed.stdout) if completed.returncode == 0 else None

    with tempfile.TemporaryDirectory() as folder_name:
        folder = Path(folder_name)
        before_retry = fresh_snapshot(folder, [first, stale_second])
        after_retry = fresh_snapshot(folder, [first, stale_second, retry_second])
    return (
        before_retry == ["committed", "rolled-back"]
        and after_retry == ["committed", "committed"]
        and attempt_handoff_fault_probe(first)
        and attempt_handoff_fault_probe(stale_second)
        and retry_second["after_state"]["fact_generation"] == 9
        and stale_second["repair_receipt"]["retry_required"]
        and not retry_second["repair_receipt"]["retry_required"]
    )


def payload_binding_valid(binding: dict[str, Any], payload: dict[str, Any], schema: dict[str, Any], project_root: Path) -> bool:
    root = project_root if binding["schema_root"] == "project" else project_root / "_bmad-output/planning-artifacts/architecture/architecture-bmad-ai-delivery-pmo-2026-07-24"
    bound_schema = json.loads((root / binding["schema_path"]).read_text(encoding="utf-8"))
    if binding["schema_pointer"]:
        rule = json_pointer(bound_schema, binding["schema_pointer"])
        return not schema_errors(payload, rule, bound_schema)
    return validate_document(payload, bound_schema)


def panel_v1_compatibility_valid(panel: dict[str, Any], compatibility: dict[str, Any], project_root: Path) -> bool:
    sys.path.insert(0, str(project_root / "skills/adp-management-panel/scripts"))
    import panel_model  # type: ignore

    model = panel["model_v1"]
    model_schema = panel_model.load_json(panel_model.PANEL_SCHEMA_PATH)
    manifest_schema = panel_model.load_json(panel_model.MANIFEST_SCHEMA_PATH)
    if panel_model.validate_schema(model, model_schema) or panel_model.validate_schema(model["manifest"], manifest_schema):
        return False
    if sorted(view["view_id"] for view in model["views"]) != sorted(compatibility["required_view_ids"]):
        return False
    if sorted(model["data"]) != sorted(compatibility["required_data_keys"]):
        return False
    if sorted(model["data"]["flows"]) != sorted(compatibility["required_flow_keys"]) or sorted(model["data"]["meetings"]) != sorted(compatibility["required_meeting_keys"]):
        return False
    for scenario, keys in compatibility["required_board_keys"].items():
        if sorted(model["data"]["meetings"][scenario]["boards"]) != keys:
            return False
    for check in compatibility["consumer_binding_checks"]:
        try:
            target = json_pointer(model, check["target_pointer"])
        except (KeyError, IndexError, TypeError):
            return False
        if sha256_bytes(canonical_bytes(target)) != check["target_sha256"] or not check["copy_equal"]:
            return False
    current = panel["sync"]["canonical"]["status"]["workstream_current"]
    return bool(current) and all("workstream_id" in row and "progress" in row and "blockers" in row and "risks" in row for row in current)


def panel_v1_composition_valid(panel: dict[str, Any], registry: dict[str, Any], project_root: Path) -> bool:
    sys.path.insert(0, str(project_root / "skills/adp-management-panel/scripts"))
    import panel_model  # type: ignore

    inputs = copy.deepcopy(panel["sync"]["compatibility_inputs"])
    inputs["meeting_packs"] = {}
    canonical_payloads = {
        "program-status": panel["sync"]["canonical"]["status"],
        "roadmap": panel["sync"]["canonical"]["roadmap"],
        "flow-graph": panel["sync"]["canonical"]["flow"],
        "meeting-pack": panel["sync"]["canonical"]["meetings"],
    }
    try:
        for binding in registry["panel_v1_composition"]["source_bindings"]:
            source = canonical_payloads[binding["projection_kind"]]
            if binding["projection_kind"] == "meeting-pack":
                source = source[binding["instance_key"]]
            value = copy.deepcopy(panel_model.resolve_pointer(source, binding["source_pointer"]))
            if binding["projection_kind"] == "program-status":
                for key in registry["panel_v1_composition"]["program_status_overlay"]:
                    value[key] = copy.deepcopy(source[key])
            parts = binding["input_key"].split("/")
            target = inputs
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        expected = panel_model.compose_panel(inputs)
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    return canonical_bytes(expected) == canonical_bytes(panel["model_v1"])


def _escape_current_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


def expected_panel_v2_current_view(panel: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    spec = registry["panel_v2_consumer"]
    rows = copy.deepcopy(json_pointer(panel, spec["primary_source_pointer"]))
    if not isinstance(rows, list) or not rows:
        raise ValueError("current workstream rows are required")
    required = set(spec["required_fields"])
    normalized_rows = []
    for row in rows:
        if not required <= set(row):
            raise ValueError("current workstream fields are incomplete")
        current = {key: row[key] for key in ("workstream_id", "progress", "blockers", "risks")}
        if any(not isinstance(current[key], str) or not current[key].strip() or current[key] != unicodedata.normalize("NFC", current[key]) for key in ("workstream_id", "progress")):
            raise ValueError("invalid current workstream scalar")
        for key in ("blockers", "risks"):
            if not isinstance(current[key], list) or any(not isinstance(value, str) or not value.strip() or value != unicodedata.normalize("NFC", value) for value in current[key]):
                raise ValueError(f"invalid {key}")
        normalized_rows.append(current)
    normalized_rows.sort(key=lambda row: row["workstream_id"].encode("utf-8"))
    if len({row["workstream_id"] for row in normalized_rows}) != len(normalized_rows):
        raise ValueError("duplicate workstream_id")
    html = "".join(
        f'<section data-workstream-id="{_escape_current_html(row["workstream_id"])}">'
        f'<h3>{_escape_current_html(row["workstream_id"])}</h3>'
        f'<p data-field="progress">{_escape_current_html(row["progress"])}</p>'
        f'<ul data-field="blockers">{"".join(f"<li>{_escape_current_html(value)}</li>" for value in row["blockers"])}</ul>'
        f'<ul data-field="risks">{"".join(f"<li>{_escape_current_html(value)}</li>" for value in row["risks"])}</ul>'
        "</section>"
        for row in normalized_rows
    )
    return {"schema_version": "2.0.0", "consumer_id": spec["id"], "source_panel_id": panel["panel_id"], "source_pointer": spec["primary_source_pointer"], "rows": normalized_rows, "html": html}


def execute_panel_v2_consumer(panel: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], project_root: Path) -> dict[str, Any] | None:
    spec = registry["panel_v2_consumer"]
    artifact = next(row for row in registry["pinned_source_artifacts"] if row["id"] == spec["artifact_id"])
    completed = subprocess.run(["node", str(project_root / artifact["path"]), "--trace"], input=json.dumps(panel, ensure_ascii=False), text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        return None
    try:
        traced = json.loads(completed.stdout)
        actual = traced["result"]
        reads = traced["accessed_pointers"]
        expected = expected_panel_v2_current_view(panel, registry)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    declared = spec["source_pointers"]
    forbidden = spec["forbidden_source_prefixes"]
    reads_ok = (
        reads == [spec["primary_source_pointer"], "/panel_id"]
        and set(reads) == set(declared)
        and all(not (pointer == prefix or pointer.startswith(prefix + "/")) for pointer in reads for prefix in forbidden)
    )
    return actual if reads_ok and actual == expected and validate(actual, schema, "managementPanelCurrentViewV2") else None


def build_projection_lineage(
    panel: dict[str, Any], upstreams: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any],
    schema_sha: str, registry_sha: str, project_root: Path, policy: dict[str, Any],
    read_mutation: tuple[str, str] | None = None,
    raw_sources: dict[tuple[str, str], bytes] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    generation_id = panel["sync"]["generation_id"]
    selection_id = panel["sync"]["selection_policy_id"]
    selected = resolved_selection(policy)
    bindings = {row["projection_kind"]: row for row in registry["projection_payload_bindings"]}
    profiles = {row["projection"]: row for row in registry["projection_input_profiles"]}
    payloads = {
        key: sorted(
            (value if isinstance(value, list) else [value]),
            key=lambda payload: (payload.get("scenario") is not None, (payload.get("scenario") or "").encode("utf-8")),
        )
        for key, value in upstreams.items()
    }
    payloads["management-panel"] = [panel]
    built: dict[str, list[dict[str, Any]]] = {}
    valid = True
    expected_instances = expected_projection_instances(registry, policy)
    if set(payloads) != set(expected_instances):
        valid = False
    for kind, expected_keys in expected_instances.items():
        actual_keys = [payload.get("scenario") if kind == "meeting-pack" else None for payload in payloads.get(kind, [])]
        if sorted(actual_keys, key=lambda value: (value is not None, b"" if value is None else value.encode("utf-8"))) != sorted(expected_keys, key=lambda value: (value is not None, b"" if value is None else value.encode("utf-8"))):
            valid = False
    for profile in registry["projection_input_profiles"]:
        kind = profile["projection"]
        binding = bindings[kind]
        built[kind] = []
        for index, payload in enumerate(payloads[kind]):
            valid = valid and payload_binding_valid(binding, payload, schema, project_root)
            if kind == "management-panel":
                valid = valid and payload.get("sync", {}).get("source_as_of") == policy["as_of"]
            elif kind in {"state-audit", "program-status", "roadmap", "meeting-pack"}:
                valid = valid and payload.get("source_as_of") == policy["as_of"]
            instance_key = payload.get("scenario") if kind == "meeting-pack" else None
            envelope = {
                "contract": {"schema_id": "urn:adp:panel-sync-contracts:2026-07-24#canonical-projection-envelope-v1", "schema_sha256": schema_sha, "registry_sha256": registry_sha},
                "schema_version": "1.0.0", "projection_kind": kind, "instance_key": instance_key, "generation_id": generation_id,
                "payload_schema_id": binding["schema_id"], "payload_schema_sha256": binding["schema_sha256"],
                "payload_sha256": sha256_bytes(canonical_bytes(payload)), "payload": payload,
            }
            envelope["projection_id"] = sha256_bytes(canonical_bytes(envelope))
            predecessors = [item["handle"] for dependency in profile["direct_upstreams"] for item in built[dependency["kind"]]]
            mutation = read_mutation[1] if read_mutation is not None and read_mutation[0] == kind else "none"
            allowed_sources, actual_reads = instrumented_read_trace(profile, selected, mutation, policy, raw_sources)
            manifest = {
                "contract": {"schema_id": "urn:adp:panel-sync-contracts:2026-07-24#dependency-manifest-v1", "schema_sha256": schema_sha, "registry_sha256": registry_sha},
                "schema_version": "1.0.0", "producer": {"skill": f"adp-{kind}", "version": "1.0.0"},
                "projection": {"kind": kind, "id": envelope["projection_id"]}, "generation_id": generation_id,
                "input_profile_id": profile["profile_id"], "selection_policy_id": selection_id, "sources": copy.deepcopy(actual_reads), "upstreams": predecessors,
            }
            manifest["manifest_id"] = sha256_bytes(canonical_bytes(manifest))
            handle = {"kind": kind, "id": envelope["projection_id"], "manifest_id": manifest["manifest_id"], "generation_id": generation_id}
            receipt = {
                "contract": {"schema_id": "urn:adp:panel-sync-contracts:2026-07-24#producer-receipt-v1", "schema_sha256": schema_sha, "registry_sha256": registry_sha},
                "schema_version": "1.0.0", "generation_id": generation_id, "input_profile_id": profile["profile_id"], "selection_policy_id": selection_id,
                "consumed_sources": copy.deepcopy(actual_reads), "consumed_predecessors": predecessors, "output": copy.deepcopy(handle), "status": "produced", "error_code": None,
            }
            receipt["receipt_id"] = sha256_bytes(canonical_bytes(receipt))
            valid = valid and all((
                validate_registered(envelope, schema, registry, "canonical-projection-envelope/1.0.0", schema_sha, registry_sha),
                validate_registered(manifest, schema, registry, "projection-dependency-manifest/1.0.0", schema_sha, registry_sha),
                validate_registered(receipt, schema, registry, "producer-receipt/1.0.0", schema_sha, registry_sha),
            ))
            built[kind].append({"envelope": envelope, "manifest": manifest, "receipt": receipt, "handle": handle, "allowed_sources": allowed_sources, "actual_reads": actual_reads})
    return built, valid


def projection_lineage_semantics(
    built: dict[str, list[dict[str, Any]]], registry: dict[str, Any], schema: dict[str, Any], generation: dict[str, Any],
    policy: dict[str, Any], schema_sha: str, registry_sha: str,
) -> bool:
    profiles = {row["projection"]: row for row in registry["projection_input_profiles"]}
    expected_instances = expected_projection_instances(registry, policy)
    if set(built) != set(expected_instances):
        return False
    for kind, expected_keys in expected_instances.items():
        actual_keys = [item["envelope"]["instance_key"] for item in built[kind]]
        if sorted(actual_keys, key=lambda value: (value is not None, b"" if value is None else value.encode("utf-8"))) != sorted(expected_keys, key=lambda value: (value is not None, b"" if value is None else value.encode("utf-8"))):
            return False
    physical_ids = [(row["root_instance_id"], row["path"]) for row in generation["leaf_sources"]]
    if len(physical_ids) != len(set(physical_ids)):
        return False
    generations: set[str] = set()
    for kind, instances in built.items():
        for item in instances:
            envelope, manifest, receipt, handle = item["envelope"], item["manifest"], item["receipt"], item["handle"]
            if not all((
                validate_registered(envelope, schema, registry, "canonical-projection-envelope/1.0.0", schema_sha, registry_sha),
                validate_registered(manifest, schema, registry, "projection-dependency-manifest/1.0.0", schema_sha, registry_sha),
                validate_registered(receipt, schema, registry, "producer-receipt/1.0.0", schema_sha, registry_sha),
            )):
                return False
            generations.add(envelope["generation_id"])
            expected_projection_id = sha256_bytes(canonical_bytes({key: value for key, value in envelope.items() if key != "projection_id"}))
            expected_manifest_id = sha256_bytes(canonical_bytes({key: value for key, value in manifest.items() if key != "manifest_id"}))
            expected_receipt_id = sha256_bytes(canonical_bytes({key: value for key, value in receipt.items() if key != "receipt_id"}))
            if envelope["payload_sha256"] != sha256_bytes(canonical_bytes(envelope["payload"])) or envelope["projection_id"] != expected_projection_id:
                return False
            if manifest["manifest_id"] != expected_manifest_id or receipt["receipt_id"] != expected_receipt_id:
                return False
            binding = next(row for row in registry["projection_payload_bindings"] if row["projection_kind"] == kind)
            if envelope["payload_schema_id"] != binding["schema_id"] or envelope["payload_schema_sha256"] != binding["schema_sha256"]:
                return False
            if manifest["projection"] != {"kind": kind, "id": envelope["projection_id"]} or handle != receipt["output"]:
                return False
            if receipt["generation_id"] != envelope["generation_id"] or manifest["generation_id"] != envelope["generation_id"] or receipt["input_profile_id"] != profiles[kind]["profile_id"]:
                return False
            if manifest["selection_policy_id"] != policy["policy_id"] or receipt["selection_policy_id"] != policy["policy_id"]:
                return False
            if manifest["sources"] != item["actual_reads"] or receipt["consumed_sources"] != item["actual_reads"] or item["actual_reads"] != item["allowed_sources"]:
                return False
            generation_leaves = {(row["root_instance_id"], row["path"]): row for row in generation["leaf_sources"]}
            for source in item["actual_reads"]:
                leaf = generation_leaves.get((source["root_instance_id"], source["path"]))
                if leaf is None or leaf["fingerprint"] != source["fingerprint"] or leaf["blob_id"] != source["blob_id"]:
                    return False
            expected_predecessors = [dependency_item["handle"] for dependency in profiles[kind]["direct_upstreams"] for dependency_item in built[dependency["kind"]]]
            if manifest["upstreams"] != expected_predecessors or receipt["consumed_predecessors"] != expected_predecessors:
                return False
    return len(generations) == 1 and generations == {generation["generation_id"]}


def _set_target_after(target: dict[str, Any], document: dict[str, Any]) -> None:
    digest = sha256_bytes(canonical_bytes(document))
    target["after_sha256"] = digest
    target["after_image"]["sha256"] = digest


def _reindex_targets(targets: list[dict[str, Any]], journal_dir: str) -> None:
    for index, target in enumerate(targets):
        target["apply_order"] = index
        if target["before_image"] is not None:
            target["before_image"]["path"] = f"{journal_dir}/images/{index}-before"
        if target["after_image"] is not None:
            target["after_image"]["path"] = f"{journal_dir}/images/{index}-after"


def _finalize_panel_publication_graph(graph: dict[str, Any]) -> None:
    pointer = graph["pointer"]
    pointer["pointer_id"] = sha256_bytes(canonical_bytes({key: value for key, value in pointer.items() if key != "pointer_id"}))
    state = graph["state"]
    state["state_id"] = sha256_bytes(canonical_bytes({key: value for key, value in state.items() if key != "state_id"}))
    receipt = graph["receipt"]
    receipt["receipt_id"] = sha256_bytes(canonical_bytes({key: value for key, value in receipt.items() if key != "receipt_id"}))
    journal = graph["journal"]
    for target in journal["targets"]:
        if target["role"] == "pointer":
            _set_target_after(target, pointer)
        elif target["role"] == "panel-state":
            _set_target_after(target, state)
        elif target["role"] == "receipt":
            _set_target_after(target, receipt)
    journal["receipt_target_paths"] = [target["path"] for target in journal["targets"] if target["role"] == "receipt"]
    journal["manifest_id"] = sha256_bytes(canonical_bytes({key: value for key, value in journal.items() if key != "manifest_id"}))
    marker = graph["marker"]
    marker["manifest_id"] = journal["manifest_id"]
    marker["marker_id"] = sha256_bytes(canonical_bytes({key: value for key, value in marker.items() if key != "marker_id"}))


def panel_publication_fixture(
    panel: dict[str, Any], built: dict[str, list[dict[str, Any]]], policy: dict[str, Any], generation: dict[str, Any],
    registry: dict[str, Any], schema_sha: str, registry_sha: str, mutation: str = "none",
    physical_inventory: dict[str, Any] | None = None, refresh_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = lambda anchor: {"schema_id": f"urn:adp:panel-sync-contracts:2026-07-24#{anchor}", "schema_sha256": schema_sha, "registry_sha256": registry_sha}
    transaction_id = "tx-panel-1"
    token = filesystem_token(transaction_id)
    journal_dir = f"state/transactions/{token}"
    first_publication = mutation in {"first-publication", "first-publication-idempotent"}
    items = sorted(
        (item for instances in built.values() for item in instances),
        key=lambda item: (item["handle"]["kind"].encode("utf-8"), (item["envelope"]["instance_key"] or "").encode("utf-8")),
    )
    pointers: list[dict[str, Any]] = []
    for item in items:
        handle, envelope = item["handle"], item["envelope"]
        template_name = "management_panel_template" if handle["kind"] == "management-panel" else "canonical_projection_template"
        canonical_path = runtime_path(
            registry, template_name, generation_id=generation["generation_id"],
            projection_kind=handle["kind"], instance_key=envelope["instance_key"],
        )
        pointers.append({"kind": handle["kind"], "instance_key": envelope["instance_key"], "id": handle["id"], "manifest_id": handle["manifest_id"], "canonical_path": canonical_path})
    pointer = {
        "contract": contract("panel-current-pointer-v1"), "schema_version": "1.0.0", "generation_id": generation["generation_id"],
        "panel_id": panel["panel_id"], "projections": pointers,
    }
    pointer["pointer_id"] = sha256_bytes(canonical_bytes(pointer))
    before_pointer = None
    before_state = None
    if not first_publication:
        before_pointer = {
            "contract": contract("panel-current-pointer-v1"), "schema_version": "1.0.0", "generation_id": "sha256:" + "0" * 64,
            "panel_id": "sha256:" + "0" * 64, "projections": copy.deepcopy(pointers),
        }
        before_pointer["pointer_id"] = sha256_bytes(canonical_bytes(before_pointer))
        before_state = {"contract": contract("panel-state-v1"), "schema_version": "1.0.0", "panel_generation": 7, "current_pointer_id": before_pointer["pointer_id"]}
        before_state["state_id"] = sha256_bytes(canonical_bytes(before_state))
    before_generation = 0 if first_publication else before_state["panel_generation"]
    state = {"contract": contract("panel-state-v1"), "schema_version": "1.0.0", "panel_generation": before_generation + 1, "current_pointer_id": pointer["pointer_id"]}
    state["state_id"] = sha256_bytes(canonical_bytes(state))
    if physical_inventory is None:
        physical_inventory = physical_inventory_fixture(registry, policy, generation["fact_generation"], schema_sha, registry_sha)
    if refresh_receipt is None:
        nodes = [{
            "instance_key": item["envelope"]["instance_key"] or "singleton", "projection_kind": item["handle"]["kind"],
            "disposition": "produced", "invalidation_reasons": [], "output": copy.deepcopy(item["handle"]), "error_code": None,
        } for item in items]
        nodes.sort(key=lambda row: (row["instance_key"].encode("utf-8"), row["projection_kind"].encode("utf-8")))
        refresh_receipt = {
            "contract": expected_contract_ref(registry, "refresh-run-receipt/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "refresh_id": "refresh-snapshot-fixture",
            "snapshot_id": policy["snapshot_id"], "snapshot_lock_receipt_id": policy["snapshot_lock_receipt_id"],
            "generation_id": generation["generation_id"], "expected_fact_generation": generation["fact_generation"],
            "expected_panel_generation": before_generation, "status": "published", "nodes": nodes,
            "retry_from_instance_key": None, "source_as_of": policy["as_of"],
        }
        refresh_receipt["receipt_id"] = sha256_bytes(canonical_bytes(refresh_receipt))

    lineage_documents: dict[str, bytes] = {}
    objects: list[dict[str, Any]] = []
    identity_fields = {
        "generation-envelope/1.0.0": "generation_id", "selection-policy/1.0.0": "policy_id",
        "physical-workstream-inventory/1.0.0": "attestation_id", "panel-binding-catalog/1.0.0": "catalog_id",
        "canonical-projection-envelope/1.0.0": "projection_id", "projection-dependency-manifest/1.0.0": "manifest_id",
        "producer-receipt/1.0.0": "receipt_id", "refresh-run-receipt/1.0.0": "receipt_id",
        "panel-current-pointer/1.0.0": "pointer_id", "panel-state/1.0.0": "state_id",
        "publication-absence-proof/1.0.0": "proof_id",
    }

    def add_object(
        object_kind: str, contract_name: str, document: dict[str, Any], path: str,
        projection_kind: str | None = None, instance_key: str | None = None,
    ) -> None:
        raw = canonical_bytes(document)
        lineage_documents[path] = raw
        objects.append({
            "object_kind": object_kind, "projection_kind": projection_kind, "instance_key": instance_key,
            "contract_name": contract_name, "object_id": document[identity_fields[contract_name]],
            "root": "memory", "root_instance_id": "123e4567-e89b-42d3-a456-426614174000",
            "path": path, "cardinality": "one", "sha256": sha256_bytes(raw),
        })

    generation_id = generation["generation_id"]
    add_object("generation", "generation-envelope/1.0.0", generation, runtime_path(registry, "generation_envelope_template", generation_id=generation_id))
    add_object("selection-policy", "selection-policy/1.0.0", policy, runtime_path(registry, "selection_policy_template", generation_id=generation_id))
    add_object("physical-inventory", "physical-workstream-inventory/1.0.0", physical_inventory, runtime_path(registry, "physical_inventory_template", generation_id=generation_id))
    catalog = panel_binding_catalog(registry, schema_sha, registry_sha)
    add_object("panel-binding-catalog", "panel-binding-catalog/1.0.0", catalog, runtime_path(registry, "panel_binding_catalog_template", generation_id=generation_id))
    for kind, instances in built.items():
        for item in instances:
            instance_key = item["envelope"]["instance_key"]
            envelope_template = "management_panel_template" if kind == "management-panel" else "canonical_projection_template"
            add_object("projection-envelope", "canonical-projection-envelope/1.0.0", item["envelope"], runtime_path(registry, envelope_template, generation_id=generation_id, projection_kind=kind, instance_key=instance_key), kind, instance_key)
            add_object("dependency-manifest", "projection-dependency-manifest/1.0.0", item["manifest"], runtime_path(registry, "dependency_manifest_template", generation_id=generation_id, projection_kind=kind, instance_key=instance_key), kind, instance_key)
            add_object("producer-receipt", "producer-receipt/1.0.0", item["receipt"], runtime_path(registry, "producer_receipt_template", generation_id=generation_id, projection_kind=kind, instance_key=instance_key), kind, instance_key)
    add_object("refresh-receipt", "refresh-run-receipt/1.0.0", refresh_receipt, runtime_path(registry, "refresh_receipt_generation_template", generation_id=generation_id))
    if first_publication:
        absence_proof = {
            "contract": expected_contract_ref(registry, "publication-absence-proof/1.0.0", schema_sha, registry_sha),
            "schema_version": "1.0.0", "generation_id": generation_id,
            "memory_root_instance_id": "123e4567-e89b-42d3-a456-426614174000",
            "pointer_path": registry["runtime_paths"]["panel_current_pointer"]["path"],
            "panel_state_path": registry["runtime_paths"]["panel_state"]["path"],
            "pointer_absent": True, "panel_state_absent": True,
            "fact_lock_profile_id": registry["lock_profile"]["profile_id"],
            "panel_lock_profile_id": registry["lock_profile"]["profile_id"], "observed_at": policy["as_of"],
        }
        absence_proof["proof_id"] = sha256_bytes(canonical_bytes(absence_proof))
        add_object("publication-absence-proof", "publication-absence-proof/1.0.0", absence_proof, runtime_path(registry, "publication_absence_proof_template", generation_id=generation_id))
    else:
        add_object("before-pointer", "panel-current-pointer/1.0.0", before_pointer, runtime_path(registry, "before_pointer_template", generation_id=generation_id))
        add_object("before-panel-state", "panel-state/1.0.0", before_state, runtime_path(registry, "before_panel_state_template", generation_id=generation_id))
    objects.sort(key=lambda row: (row["object_kind"].encode("utf-8"), (row["projection_kind"] or "").encode("utf-8"), (row["instance_key"] or "").encode("utf-8")))
    lineage_index = {
        "contract": expected_contract_ref(registry, "generation-lineage-index/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "generation_id": generation_id, "objects": objects,
    }
    lineage_index["index_id"] = sha256_bytes(canonical_bytes(lineage_index))
    lineage_index_path = runtime_path(registry, "generation_lineage_index_template", generation_id=generation_id)
    lineage_documents[lineage_index_path] = canonical_bytes(lineage_index)
    lineage_targets: list[dict[str, Any]] = []
    for row in objects:
        role = "lineage-object"
        if row["object_kind"] == "projection-envelope":
            role = "panel" if row["projection_kind"] == "management-panel" else "projection"
        target = mutation_target(role, "create", len(lineage_targets), row["path"])
        target["after_sha256"] = row["sha256"]
        target["after_image"]["sha256"] = row["sha256"]
        lineage_targets.append(target)
    index_target = mutation_target("lineage-index", "create", len(lineage_targets), lineage_index_path)
    _set_target_after(index_target, lineage_index)
    lineage_targets.append(index_target)

    state_target = mutation_target("panel-state", "create" if first_publication else "replace", len(lineage_targets), registry["runtime_paths"]["panel_state"]["path"])
    if not first_publication:
        state_target["before_sha256"] = sha256_bytes(canonical_bytes(before_state))
        state_target["before_image"]["sha256"] = state_target["before_sha256"]
    _set_target_after(state_target, state)
    receipt_target = mutation_target("receipt", "create", len(lineage_targets) + 1, registry["runtime_paths"]["panel_receipt_template"]["path"].replace("{transaction_token}", token))
    pointer_target = mutation_target("pointer", "create" if first_publication else "replace", len(lineage_targets) + 2, registry["runtime_paths"]["panel_current_pointer"]["path"])
    if not first_publication:
        pointer_target["before_sha256"] = sha256_bytes(canonical_bytes(before_pointer))
        pointer_target["before_image"]["sha256"] = pointer_target["before_sha256"]
    _set_target_after(pointer_target, pointer)
    command_fingerprint = sha256_bytes(canonical_bytes({
        "transaction_id": transaction_id, "generation_id": generation["generation_id"],
        "selection_policy_id": policy["policy_id"], "panel_id": panel["panel_id"],
    }))
    published_targets = [target for target in lineage_targets if target["role"] in {"projection", "panel"}]
    receipt = {
        "contract": contract("panel-publication-receipt-v1"), "schema_version": "1.0.0", "transaction_id": transaction_id, "journal_id": "journal-panel-1",
        "command_fingerprint": command_fingerprint,
        "generation_id": generation["generation_id"], "selection_policy_id": policy["policy_id"], "panel_id": panel["panel_id"],
        "lineage_index_id": lineage_index["index_id"], "lineage_targets": copy.deepcopy(lineage_targets),
        "before_panel_generation": before_generation, "after_panel_generation": state["panel_generation"],
        "before_pointer_id": None if first_publication else before_pointer["pointer_id"], "after_pointer_id": pointer["pointer_id"],
        "published_targets": copy.deepcopy(published_targets), "pointer_target": copy.deepcopy(pointer_target), "panel_state_target": copy.deepcopy(state_target), "status": "committed",
    }
    receipt["receipt_id"] = sha256_bytes(canonical_bytes(receipt))
    _set_target_after(receipt_target, receipt)
    journal = {
        "contract": contract("transaction-journal-manifest-v1"), "schema_version": "1.0.0", "journal_id": "journal-panel-1", "transaction_id": transaction_id, "journal_dir": journal_dir,
        "manifest_path": runtime_path(registry, "publication_journal_template", generation_id=generation_id),
        "prepared_marker_path": runtime_path(registry, "journal_prepared_marker_template", transaction_id=transaction_id),
        "terminal_marker_path": runtime_path(registry, "publication_marker_template", generation_id=generation_id),
        "recovery_receipt_path": runtime_path(registry, "journal_recovery_receipt_template", transaction_id=transaction_id),
        "transaction_kind": "panel", "authorization": None,
        "targets": copy.deepcopy(lineage_targets) + [state_target, receipt_target, pointer_target],
        "receipt_target_paths": [receipt_target["path"]], "prepared_at": "2026-07-24T02:00:00Z",
    }
    _reindex_targets(journal["targets"], journal_dir)
    receipt["published_targets"] = copy.deepcopy([target for target in journal["targets"] if target["role"] in {"projection", "panel"}])
    receipt["lineage_targets"] = copy.deepcopy([target for target in journal["targets"] if target["role"] in {"projection", "panel", "lineage-object", "lineage-index"}])
    receipt["lineage_index_id"] = lineage_index["index_id"]
    receipt["pointer_target"] = copy.deepcopy(next(target for target in journal["targets"] if target["role"] == "pointer"))
    receipt["panel_state_target"] = copy.deepcopy(next(target for target in journal["targets"] if target["role"] == "panel-state"))
    journal["manifest_id"] = sha256_bytes(canonical_bytes(journal))
    marker = {"contract": contract("journal-marker-v1"), "schema_version": "1.0.0", "journal_id": journal["journal_id"], "manifest_id": journal["manifest_id"], "state": "committed", "marked_at": "2026-07-24T02:00:01Z"}
    marker["marker_id"] = sha256_bytes(canonical_bytes(marker))
    graph = {
        "panel": panel, "built": built, "policy": policy, "generation": generation,
        "before_pointer": before_pointer, "pointer": pointer, "before_state": before_state, "state": state,
        "receipt": receipt, "journal": journal, "marker": marker, "physical_inventory": physical_inventory,
        "refresh_receipt": refresh_receipt, "lineage_index": lineage_index,
        "lineage_index_path": lineage_index_path, "lineage_documents": lineage_documents,
    }
    if mutation == "omit-projection":
        removed = next(target for target in graph["journal"]["targets"] if target["role"] == "projection")
        graph["journal"]["targets"].remove(removed)
        _reindex_targets(graph["journal"]["targets"], graph["journal"]["journal_dir"])
        graph["receipt"]["published_targets"] = copy.deepcopy([target for target in graph["journal"]["targets"] if target["role"] in {"projection", "panel"}])
        graph["receipt"]["pointer_target"] = copy.deepcopy(next(target for target in graph["journal"]["targets"] if target["role"] == "pointer"))
        graph["receipt"]["panel_state_target"] = copy.deepcopy(next(target for target in graph["journal"]["targets"] if target["role"] == "panel-state"))
    elif mutation == "wrong-role":
        target = next(target for target in graph["journal"]["targets"] if target["role"] == "projection")
        target["role"] = "business"
    elif mutation == "pointer-generation":
        graph["pointer"]["generation_id"] = "sha256:" + "e" * 64
    elif mutation == "state-generation-jump":
        graph["state"]["panel_generation"] = 99
    elif mutation == "receipt-selection":
        graph["receipt"]["selection_policy_id"] = "sha256:" + "e" * 64
    elif mutation == "receipt-target-mismatch":
        graph["receipt"]["published_targets"][0]["path"] = "views/generations/other.json"
    elif mutation == "noncommitted-marker":
        graph["marker"]["state"] = "prepared"
    elif mutation == "panel-generation-jump":
        graph["receipt"]["after_panel_generation"] = 99
    elif mutation == "redirect-pointer":
        next(row for row in graph["journal"]["targets"] if row["role"] == "pointer")["path"] = "wrong/current-pointer.json"
    elif mutation == "redirect-state":
        next(row for row in graph["journal"]["targets"] if row["role"] == "panel-state")["path"] = "wrong/panel-state.json"
    elif mutation == "redirect-receipt":
        next(row for row in graph["journal"]["targets"] if row["role"] == "receipt")["path"] = "wrong/panel-receipt.json"
    elif mutation == "substitute-before-pointer":
        graph["before_pointer"]["panel_id"] = "sha256:" + "e" * 64
    elif mutation == "substitute-before-state":
        graph["before_state"]["panel_generation"] = 6
    elif mutation == "pointer-not-last":
        pointer_row = next(row for row in graph["journal"]["targets"] if row["role"] == "pointer")
        graph["journal"]["targets"].remove(pointer_row)
        graph["journal"]["targets"].insert(-1, pointer_row)
    elif mutation == "lineage-index-target-missing":
        graph["journal"]["targets"].remove(next(row for row in graph["journal"]["targets"] if row["role"] == "lineage-index"))
    elif mutation == "lineage-object-target-missing":
        graph["journal"]["targets"].remove(next(row for row in graph["journal"]["targets"] if row["role"] == "lineage-object"))
    _finalize_panel_publication_graph(graph)
    if mutation == "journal-adjunct-tamper":
        graph["journal"]["prepared_at"] = "2026-07-24T02:00:02Z"
    elif mutation == "marker-adjunct-tamper":
        graph["marker"]["marked_at"] = "2026-07-24T02:00:02Z"
    elif mutation == "receipt-adjunct-tamper":
        graph["receipt"]["status"] = "committed"
        graph["receipt"]["before_panel_generation"] += 1
    return graph


def panel_publication_target_images(graph: dict[str, Any]) -> dict[str, dict[str, bytes | None]]:
    after_by_path = copy.deepcopy(graph["lineage_documents"])
    receipt_path = next(row["path"] for row in graph["journal"]["targets"] if row["role"] == "receipt")
    after_by_path.update({
        receipt_path: canonical_bytes(graph["receipt"]),
        graph["journal"]["manifest_path"]: canonical_bytes(graph["journal"]),
        graph["journal"]["terminal_marker_path"]: canonical_bytes(graph["marker"]),
        graph["journal"]["targets"][-1]["path"]: canonical_bytes(graph["pointer"]),
        next(row["path"] for row in graph["journal"]["targets"] if row["role"] == "panel-state"): canonical_bytes(graph["state"]),
    })
    images: dict[str, dict[str, bytes | None]] = {}
    for target in graph["journal"]["targets"]:
        if target["role"] == "pointer":
            before_raw = None if graph["before_pointer"] is None else canonical_bytes(graph["before_pointer"])
        elif target["role"] == "panel-state":
            before_raw = None if graph["before_state"] is None else canonical_bytes(graph["before_state"])
        else:
            before_raw = None
        images[target["path"]] = {"before": before_raw, "after": after_by_path[target["path"]]}
    return images


def panel_publication_semantics(
    graph: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
) -> bool:
    before_pointer, pointer, before_state, state, receipt, journal, marker = (graph[name] for name in ("before_pointer", "pointer", "before_state", "state", "receipt", "journal", "marker"))
    first_publication = before_pointer is None and before_state is None
    if (before_pointer is None) != (before_state is None):
        return False
    registered = [
        (pointer, "panel-current-pointer/1.0.0"), (state, "panel-state/1.0.0"),
        (receipt, "panel-publication-receipt/1.0.0"),
    ]
    if not first_publication:
        registered.extend(((before_pointer, "panel-current-pointer/1.0.0"), (before_state, "panel-state/1.0.0")))
    if not all(validate_registered(document, schema, registry, contract_name, schema_sha, registry_sha) for document, contract_name in registered):
        return False
    lineage_index = graph.get("lineage_index")
    lineage_documents = graph.get("lineage_documents")
    if not isinstance(lineage_index, dict) or not isinstance(lineage_documents, dict):
        return False
    if not journal_semantics(journal, marker, schema, registry, schema_sha, registry_sha):
        return False
    if not panel_binding_semantics(graph["panel"], graph["built"], registry, graph["policy"], graph["generation"]):
        return False
    if pointer["pointer_id"] != sha256_bytes(canonical_bytes({key: value for key, value in pointer.items() if key != "pointer_id"})):
        return False
    if state["state_id"] != sha256_bytes(canonical_bytes({key: value for key, value in state.items() if key != "state_id"})):
        return False
    if not first_publication and (
        before_pointer["pointer_id"] != sha256_bytes(canonical_bytes({key: value for key, value in before_pointer.items() if key != "pointer_id"}))
        or before_state["state_id"] != sha256_bytes(canonical_bytes({key: value for key, value in before_state.items() if key != "state_id"}))
    ):
        return False
    if receipt["receipt_id"] != sha256_bytes(canonical_bytes({key: value for key, value in receipt.items() if key != "receipt_id"})):
        return False
    rows = lineage_index["objects"]
    expected_order = sorted(rows, key=lambda row: (row["object_kind"].encode("utf-8"), (row["projection_kind"] or "").encode("utf-8"), (row["instance_key"] or "").encode("utf-8")))
    descriptor_keys = [(row["object_kind"], row["projection_kind"], row["instance_key"]) for row in rows]
    if not (
        validate_registered(lineage_index, schema, registry, "generation-lineage-index/1.0.0", schema_sha, registry_sha)
        and lineage_index["index_id"] == sha256_bytes(canonical_bytes({key: value for key, value in lineage_index.items() if key != "index_id"}))
        and lineage_index["generation_id"] == graph["generation"]["generation_id"]
        and rows == expected_order and len(descriptor_keys) == len(set(descriptor_keys))
        and set(lineage_documents) == {graph["lineage_index_path"], *(row["path"] for row in rows)}
    ):
        return False
    actual_descriptors = {
        (row["object_kind"], row["projection_kind"], row["instance_key"]): (row["contract_name"], row["root"], row["path"], row["cardinality"])
        for row in rows
    }
    if actual_descriptors != expected_lineage_descriptors(registry, graph["generation"]["generation_id"], graph["policy"], first_publication):
        return False
    identity_fields = {
        "generation-envelope/1.0.0": "generation_id", "selection-policy/1.0.0": "policy_id",
        "physical-workstream-inventory/1.0.0": "attestation_id", "panel-binding-catalog/1.0.0": "catalog_id",
        "canonical-projection-envelope/1.0.0": "projection_id", "projection-dependency-manifest/1.0.0": "manifest_id",
        "producer-receipt/1.0.0": "receipt_id", "refresh-run-receipt/1.0.0": "receipt_id",
        "panel-current-pointer/1.0.0": "pointer_id", "panel-state/1.0.0": "state_id",
        "publication-absence-proof/1.0.0": "proof_id",
    }
    memory_root = next(row["root_instance_id"] for row in graph["generation"]["roots"] if row["root"] == "memory")
    for row in rows:
        raw = lineage_documents.get(row["path"])
        if raw is None:
            return False
        document = json.loads(raw)
        if not (
            row["root"] == "memory" and row["root_instance_id"] == memory_root and row["cardinality"] == "one"
            and canonical_bytes(document) == raw and sha256_bytes(raw) == row["sha256"]
            and validate_registered(document, schema, registry, row["contract_name"], schema_sha, registry_sha)
            and document[identity_fields[row["contract_name"]]] == row["object_id"]
        ):
            return False
    index_raw = lineage_documents.get(graph["lineage_index_path"])
    if index_raw != canonical_bytes(lineage_index):
        return False

    items = sorted((item for instances in graph["built"].values() for item in instances), key=lambda item: (item["handle"]["kind"].encode("utf-8"), (item["envelope"]["instance_key"] or "").encode("utf-8")))
    expected_pointers = []
    expected_targets = []
    for item in items:
        envelope, handle = item["envelope"], item["handle"]
        template_name = "management_panel_template" if handle["kind"] == "management-panel" else "canonical_projection_template"
        try:
            target_path = runtime_path(
                registry, template_name, generation_id=graph["generation"]["generation_id"],
                projection_kind=handle["kind"], instance_key=envelope["instance_key"],
            )
        except ValueError:
            return False
        expected_pointers.append({"kind": handle["kind"], "instance_key": envelope["instance_key"], "id": handle["id"], "manifest_id": handle["manifest_id"], "canonical_path": target_path})
        role = "panel" if handle["kind"] == "management-panel" else "projection"
        matches = [target for target in journal["targets"] if target["role"] == role and target["path"] == target_path]
        if len(matches) != 1 or matches[0]["after_sha256"] != sha256_bytes(canonical_bytes(envelope)):
            return False
        expected_targets.append(matches[0])
    lineage_targets = [target for target in journal["targets"] if target["role"] in {"projection", "panel", "lineage-object", "lineage-index"}]
    for row in rows:
        expected_role = "lineage-object" if row["object_kind"] != "projection-envelope" else ("panel" if row["projection_kind"] == "management-panel" else "projection")
        matches = [target for target in lineage_targets if target["path"] == row["path"]]
        if len(matches) != 1 or matches[0]["role"] != expected_role or matches[0]["operation"] != "create" or matches[0]["after_sha256"] != row["sha256"]:
            return False
    index_targets = [target for target in lineage_targets if target["role"] == "lineage-index"]
    if len(index_targets) != 1 or index_targets[0]["path"] != graph["lineage_index_path"] or index_targets[0]["after_sha256"] != sha256_bytes(index_raw):
        return False
    pointer_targets = [target for target in journal["targets"] if target["role"] == "pointer"]
    state_targets = [target for target in journal["targets"] if target["role"] == "panel-state"]
    receipt_targets = [target for target in journal["targets"] if target["role"] == "receipt"]
    if pointer["projections"] != expected_pointers or len(pointer_targets) != 1 or len(state_targets) != 1 or len(receipt_targets) != 1:
        return False
    if pointer_targets[0]["after_sha256"] != sha256_bytes(canonical_bytes(pointer)) or state_targets[0]["after_sha256"] != sha256_bytes(canonical_bytes(state)) or receipt_targets[0]["after_sha256"] != sha256_bytes(canonical_bytes(receipt)):
        return False
    token = filesystem_token(journal["transaction_id"])
    common_targets_ok = (
        pointer_targets[0]["root_instance_id"] == memory_root and pointer_targets[0]["path"] == registry["runtime_paths"]["panel_current_pointer"]["path"]
        and state_targets[0]["root_instance_id"] == memory_root and state_targets[0]["path"] == registry["runtime_paths"]["panel_state"]["path"]
        and receipt_targets[0]["root_instance_id"] == memory_root and receipt_targets[0]["path"] == registry["runtime_paths"]["panel_receipt_template"]["path"].replace("{transaction_token}", token)
    )
    preimage_targets_ok = (
        pointer_targets[0]["operation"] == "create" and state_targets[0]["operation"] == "create"
        and pointer_targets[0]["before_sha256"] is None and pointer_targets[0]["before_image"] is None
        and state_targets[0]["before_sha256"] is None and state_targets[0]["before_image"] is None
        and receipt["before_panel_generation"] == 0 and receipt["before_pointer_id"] is None
    ) if first_publication else (
        pointer_targets[0]["operation"] == "replace" and state_targets[0]["operation"] == "replace"
        and pointer_targets[0]["before_sha256"] == sha256_bytes(canonical_bytes(before_pointer))
        and state_targets[0]["before_sha256"] == sha256_bytes(canonical_bytes(before_state))
        and receipt["before_pointer_id"] == before_pointer["pointer_id"] == before_state["current_pointer_id"]
        and receipt["before_panel_generation"] == before_state["panel_generation"]
    )
    if not common_targets_ok or not preimage_targets_ok:
        return False
    panel_envelope = next(item["envelope"] for item in items if item["handle"]["kind"] == "management-panel")
    return (
        pointer["generation_id"] == graph["generation"]["generation_id"] == receipt["generation_id"]
        and pointer["panel_id"] == graph["panel"]["panel_id"] == panel_envelope["payload"]["panel_id"] == receipt["panel_id"]
        and receipt["selection_policy_id"] == graph["policy"]["policy_id"]
        and receipt["command_fingerprint"] == sha256_bytes(canonical_bytes({
            "transaction_id": journal["transaction_id"], "generation_id": graph["generation"]["generation_id"],
            "selection_policy_id": graph["policy"]["policy_id"], "panel_id": graph["panel"]["panel_id"],
        }))
        and receipt["lineage_index_id"] == lineage_index["index_id"]
        and receipt["lineage_targets"] == lineage_targets
        and receipt["after_panel_generation"] == receipt["before_panel_generation"] + 1 == state["panel_generation"]
        and receipt["after_pointer_id"] == pointer["pointer_id"] == state["current_pointer_id"]
        and receipt["published_targets"] == expected_targets
        and receipt["pointer_target"] == pointer_targets[0]
        and receipt["panel_state_target"] == state_targets[0]
        and journal["targets"] == lineage_targets + state_targets + receipt_targets + pointer_targets
        and journal["targets"][-1] == pointer_targets[0]
        and receipt["transaction_id"] == journal["transaction_id"]
        and receipt["journal_id"] == journal["journal_id"]
    )


def strict_lineage_fixture(
    suite: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
    project_root: Path, fact_state: dict[str, Any], ledger_raw: bytes, ledger_state: dict[str, Any],
    workstream_documents: list[dict[str, Any]], first_publication: bool = False,
) -> dict[str, Any]:
    panel, upstreams, _, policy, _ = panel_fixture(suite["contract_schema_vectors"], registry, schema_sha, registry_sha, project_root)
    workstream_by_id = {row["state"]["workstream_id"]: row for row in workstream_documents}
    for collection_name in ("physical_workstream_inventory", "workstream_catalog"):
        for row in policy[collection_name]:
            live = workstream_by_id[row["workstream_id"]]
            row["wdr_source"]["fingerprint"] = sha256_bytes(live["wdr_raw"])
            row["wdr_source"]["blob_id"] = row["wdr_source"]["fingerprint"]
            sidecar_raw = canonical_bytes(live["sidecar"])
            row["sidecar_source"]["fingerprint"] = sha256_bytes(sidecar_raw)
            row["sidecar_source"]["blob_id"] = row["sidecar_source"]["fingerprint"]
    policy["physical_workstream_inventory_id"] = canonical_inventory_id(policy["physical_workstream_inventory"])
    policy["workstream_catalog_id"] = canonical_catalog_id(policy["workstream_catalog"])
    policy["policy_id"] = sha256_bytes(canonical_bytes({key: value for key, value in policy.items() if key != "policy_id"}))
    lineage_action_flow = action_flow_document(
        parse_action_ledger(ledger_raw), ledger_raw, ledger_state["ledger_revision"],
        registry, schema_sha, registry_sha,
    )
    live_documents: dict[str, bytes] = {
        registry["runtime_paths"]["action_ledger"]["path"]: ledger_raw,
        registry["runtime_paths"]["action_ledger_state"]["path"]: canonical_bytes(ledger_state),
        registry["runtime_paths"]["action_flow_index"]["path"]: canonical_bytes(lineage_action_flow),
    }
    for item in workstream_documents:
        live_documents[item["record_path"]] = item["wdr_raw"]
        workstream_id = item["state"]["workstream_id"]
        live_documents[f"workstreams/{workstream_id}/delivery-record.state.json"] = canonical_bytes(item["state"])
        live_documents[f"workstreams/{workstream_id}/action-projection.json"] = canonical_bytes(item["sidecar"])
    raw_sources = {("memory", path): raw for path, raw in live_documents.items()}
    generation = generation_fixture(registry, policy, schema_sha, registry_sha, raw_sources)
    generation["fact_generation"] = fact_state["fact_generation"]
    leaf_store: dict[str, bytes] = {}
    for source in generation["leaf_sources"]:
        raw = live_documents.get(source["path"], f"{source['root']}\0{source['path']}".encode("utf-8"))
        leaf_store[f"{source['root_instance_id']}\0{source['path']}"] = raw
    generation["generation_id"] = sha256_bytes(canonical_bytes({key: value for key, value in generation.items() if key != "generation_id"}))
    physical_inventory = physical_inventory_fixture(registry, policy, fact_state["fact_generation"], schema_sha, registry_sha)
    catalog = panel_binding_catalog(registry, schema_sha, registry_sha)

    program_status = upstreams["program-status"]
    current_rows = []
    for workstream_id in resolved_selection(policy):
        item = workstream_by_id[workstream_id]
        current = parse_wdr_current(item["wdr_raw"], workstream_id)
        current_rows.append({
            **{key: current[key] for key in ("workstream_id", "phase", "status", "progress", "blockers", "risks", "dependencies", "action_ids")},
            "wdr_fingerprint": sha256_bytes(item["wdr_raw"]), "wdr_revision": item["state"]["wdr_revision"],
            "file_generation": item["state"]["file_generation"],
        })
    program_status["workstream_current"] = current_rows
    upstreams["state-audit"]["selection_policy_id"] = policy["policy_id"]
    upstreams["state-audit"]["selected_workstreams"] = resolved_selection(policy)
    drift_package = {
        "generation_id": generation["generation_id"], "selection_policy_id": policy["policy_id"],
        "selected_workstreams": resolved_selection(policy), "ledger_raw": ledger_raw, "ledger_state": ledger_state,
        "wdrs": {row["state"]["workstream_id"]: row["wdr_raw"] for row in workstream_documents},
        "wdr_states": {row["state"]["workstream_id"]: row["state"] for row in workstream_documents},
        "sidecars": {row["state"]["workstream_id"]: row["sidecar"] for row in workstream_documents},
    }
    upstreams["action-projection-drift-verdict"] = expected_drift_verdict(drift_package, registry, schema_sha, registry_sha)
    panel["sync"]["generation_id"] = generation["generation_id"]
    panel["sync"]["selection_policy_id"] = policy["policy_id"]
    for binding in registry["panel_binding_map"]:
        payload = upstreams[binding["projection_kind"]]
        value = {row["scenario"]: copy.deepcopy(row) for row in payload} if binding["merge_mode"] == "object-by-key" else copy.deepcopy(payload)
        set_pointer(panel, binding["panel_pointer"], value)
    panel["panel_id"] = sha256_bytes(canonical_bytes({key: value for key, value in panel.items() if key != "panel_id"}))
    built, outer_ok = build_projection_lineage(
        panel, upstreams, registry, schema, schema_sha, registry_sha, project_root, policy,
        raw_sources=raw_sources,
    )
    lineage_ok = projection_lineage_semantics(built, registry, schema, generation, policy, schema_sha, registry_sha)
    if not outer_ok or not lineage_ok:
        raise ValueError(f"strict lineage fixture is invalid: outer={outer_ok},lineage={lineage_ok}")
    nodes = []
    for instances in built.values():
        for item in instances:
            nodes.append({
                "instance_key": item["envelope"]["instance_key"] or "singleton", "projection_kind": item["handle"]["kind"],
                "disposition": "produced", "invalidation_reasons": [], "output": copy.deepcopy(item["handle"]), "error_code": None,
            })
    nodes.sort(key=lambda row: (row["instance_key"].encode("utf-8"), row["projection_kind"].encode("utf-8")))
    refresh_receipt = {
        "contract": expected_contract_ref(registry, "refresh-run-receipt/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "refresh_id": "refresh-snapshot-fixture",
        "snapshot_id": policy["snapshot_id"], "snapshot_lock_receipt_id": policy["snapshot_lock_receipt_id"], "generation_id": generation["generation_id"],
        "expected_fact_generation": fact_state["fact_generation"],
        "expected_panel_generation": 0 if first_publication else 7,
        "status": "published", "nodes": nodes, "retry_from_instance_key": None, "source_as_of": policy["as_of"],
    }
    refresh_receipt["receipt_id"] = sha256_bytes(canonical_bytes(refresh_receipt))
    publication_graph = panel_publication_fixture(
        panel, built, policy, generation, registry, schema_sha, registry_sha,
        "first-publication" if first_publication else "none", physical_inventory, refresh_receipt,
    )
    if not panel_publication_semantics(publication_graph, registry, schema, schema_sha, registry_sha):
        raise ValueError("strict publication fixture is invalid")
    lineage_store = copy.deepcopy(publication_graph["lineage_documents"])
    journal = publication_graph["journal"]
    marker = publication_graph["marker"]
    receipt = publication_graph["receipt"]
    receipt_path = next(row["path"] for row in journal["targets"] if row["role"] == "receipt")
    lineage_store.update({
        journal["manifest_path"]: canonical_bytes(journal),
        journal["terminal_marker_path"]: canonical_bytes(marker),
        receipt_path: canonical_bytes(receipt),
        registry["runtime_paths"]["panel_current_pointer"]["path"]: canonical_bytes(publication_graph["pointer"]),
        registry["runtime_paths"]["panel_state"]["path"]: canonical_bytes(publication_graph["state"]),
    })
    return {
        "panel": panel, "policy": policy, "physical_inventory": physical_inventory, "catalog": catalog,
        "generation": generation, "built": built, "publication_graph": publication_graph, "refresh_receipt": refresh_receipt,
        "lineage_index": publication_graph["lineage_index"], "lineage_index_path": publication_graph["lineage_index_path"],
        "lineage_store": lineage_store, "leaf_store": leaf_store,
    }


def expected_lineage_descriptors(
    registry: dict[str, Any], generation_id: str, policy: dict[str, Any], first_publication: bool = False,
) -> dict[tuple[str, str | None, str | None], tuple[str, str, str, str]]:
    descriptors: dict[tuple[str, str | None, str | None], tuple[str, str, str, str]] = {}

    def singleton(object_kind: str, contract_name: str, path: str) -> None:
        descriptors[(object_kind, None, None)] = (contract_name, "memory", path, "one")

    singleton("generation", "generation-envelope/1.0.0", runtime_path(registry, "generation_envelope_template", generation_id=generation_id))
    singleton("selection-policy", "selection-policy/1.0.0", runtime_path(registry, "selection_policy_template", generation_id=generation_id))
    singleton("physical-inventory", "physical-workstream-inventory/1.0.0", runtime_path(registry, "physical_inventory_template", generation_id=generation_id))
    singleton("panel-binding-catalog", "panel-binding-catalog/1.0.0", runtime_path(registry, "panel_binding_catalog_template", generation_id=generation_id))
    for projection_kind, instance_keys in expected_projection_instances(registry, policy).items():
        for instance_key in instance_keys:
            envelope_template = "management_panel_template" if projection_kind == "management-panel" else "canonical_projection_template"
            descriptors[("projection-envelope", projection_kind, instance_key)] = (
                "canonical-projection-envelope/1.0.0", "memory",
                runtime_path(registry, envelope_template, generation_id=generation_id, projection_kind=projection_kind, instance_key=instance_key),
                "one",
            )
            descriptors[("dependency-manifest", projection_kind, instance_key)] = (
                "projection-dependency-manifest/1.0.0", "memory",
                runtime_path(registry, "dependency_manifest_template", generation_id=generation_id, projection_kind=projection_kind, instance_key=instance_key),
                "one",
            )
            descriptors[("producer-receipt", projection_kind, instance_key)] = (
                "producer-receipt/1.0.0", "memory",
                runtime_path(registry, "producer_receipt_template", generation_id=generation_id, projection_kind=projection_kind, instance_key=instance_key),
                "one",
            )
    singleton("refresh-receipt", "refresh-run-receipt/1.0.0", runtime_path(registry, "refresh_receipt_generation_template", generation_id=generation_id))
    if first_publication:
        singleton("publication-absence-proof", "publication-absence-proof/1.0.0", runtime_path(registry, "publication_absence_proof_template", generation_id=generation_id))
    else:
        singleton("before-pointer", "panel-current-pointer/1.0.0", runtime_path(registry, "before_pointer_template", generation_id=generation_id))
        singleton("before-panel-state", "panel-state/1.0.0", runtime_path(registry, "before_panel_state_template", generation_id=generation_id))
    return descriptors


def load_strict_lineage(
    package: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
    verify_live_leaves: bool = True,
) -> dict[str, Any] | None:
    try:
        store = package["lineage_store"]
        pointer_path = registry["runtime_paths"]["panel_current_pointer"]["path"]
        pointer_raw = store.get(pointer_path)
        if not isinstance(pointer_raw, bytes):
            return None
        live_pointer = json.loads(pointer_raw)
        expected_index_path = runtime_path(
            registry, "generation_lineage_index_template", generation_id=live_pointer["generation_id"],
        )
        if set(store) == set() or expected_index_path not in store:
            return None
        index_raw = store[expected_index_path]
        index = json.loads(index_raw)
        if not (
            canonical_bytes(index) == index_raw
            and validate_registered(index, schema, registry, "generation-lineage-index/1.0.0", schema_sha, registry_sha)
            and index["index_id"] == sha256_bytes(canonical_bytes({key: value for key, value in index.items() if key != "index_id"}))
            and index["generation_id"] == live_pointer["generation_id"]
        ):
            return None
        rows = index["objects"]
        expected_order = sorted(rows, key=lambda row: (row["object_kind"].encode("utf-8"), (row["projection_kind"] or "").encode("utf-8"), (row["instance_key"] or "").encode("utf-8")))
        keys = [(row["object_kind"], row["projection_kind"], row["instance_key"]) for row in rows]
        if rows != expected_order or len(keys) != len(set(keys)):
            return None
        journal_path = runtime_path(registry, "publication_journal_template", generation_id=live_pointer["generation_id"])
        marker_path = runtime_path(registry, "publication_marker_template", generation_id=live_pointer["generation_id"])
        panel_state_path = registry["runtime_paths"]["panel_state"]["path"]
        journal_raw, marker_raw = store[journal_path], store[marker_path]
        journal, marker = json.loads(journal_raw), json.loads(marker_raw)
        receipt_targets = [row for row in journal["targets"] if row["role"] == "receipt"]
        if len(receipt_targets) != 1:
            return None
        receipt_path = receipt_targets[0]["path"]
        receipt_raw = store[receipt_path]
        publication_receipt = json.loads(receipt_raw)
        pointer_raw, panel_state_raw = store[pointer_path], store[panel_state_path]
        current_pointer, panel_state = json.loads(pointer_raw), json.loads(panel_state_raw)
        expected_store_paths = {
            expected_index_path, *(row["path"] for row in rows), journal_path, marker_path,
            receipt_path, pointer_path, panel_state_path,
        }
        if set(store) != expected_store_paths or not (
            canonical_bytes(journal) == journal_raw and canonical_bytes(marker) == marker_raw
            and canonical_bytes(publication_receipt) == receipt_raw
            and canonical_bytes(current_pointer) == pointer_raw and canonical_bytes(panel_state) == panel_state_raw
            and validate_registered(publication_receipt, schema, registry, "panel-publication-receipt/1.0.0", schema_sha, registry_sha)
            and validate_registered(current_pointer, schema, registry, "panel-current-pointer/1.0.0", schema_sha, registry_sha)
            and validate_registered(panel_state, schema, registry, "panel-state/1.0.0", schema_sha, registry_sha)
            and publication_receipt["receipt_id"] == sha256_bytes(canonical_bytes({key: value for key, value in publication_receipt.items() if key != "receipt_id"}))
            and current_pointer["pointer_id"] == sha256_bytes(canonical_bytes({key: value for key, value in current_pointer.items() if key != "pointer_id"}))
            and panel_state["state_id"] == sha256_bytes(canonical_bytes({key: value for key, value in panel_state.items() if key != "state_id"}))
            and receipt_targets[0]["after_sha256"] == sha256_bytes(receipt_raw)
            and journal_semantics(journal, marker, schema, registry, schema_sha, registry_sha)
        ):
            return None
        identity_fields = {
            "generation-envelope/1.0.0": "generation_id", "selection-policy/1.0.0": "policy_id", "physical-workstream-inventory/1.0.0": "attestation_id",
            "panel-binding-catalog/1.0.0": "catalog_id", "canonical-projection-envelope/1.0.0": "projection_id",
            "projection-dependency-manifest/1.0.0": "manifest_id", "producer-receipt/1.0.0": "receipt_id",
            "refresh-run-receipt/1.0.0": "receipt_id", "panel-publication-receipt/1.0.0": "receipt_id",
            "panel-current-pointer/1.0.0": "pointer_id", "panel-state/1.0.0": "state_id",
            "transaction-journal-manifest/1.0.0": "manifest_id", "journal-marker/1.0.0": "marker_id",
            "publication-absence-proof/1.0.0": "proof_id",
        }
        documents: dict[tuple[str, str | None, str | None], dict[str, Any]] = {}
        for row in rows:
            raw = store[row["path"]]
            document = json.loads(raw)
            identity_field = identity_fields[row["contract_name"]]
            if not (
                canonical_bytes(document) == raw and sha256_bytes(raw) == row["sha256"]
                and validate_registered(document, schema, registry, row["contract_name"], schema_sha, registry_sha)
                and document[identity_field] == row["object_id"]
            ):
                return None
            documents[(row["object_kind"], row["projection_kind"], row["instance_key"])] = document
        generation = documents[("generation", None, None)]
        policy = documents[("selection-policy", None, None)]
        physical_inventory = documents[("physical-inventory", None, None)]
        catalog = documents[("panel-binding-catalog", None, None)]
        first_publication = panel_state["panel_generation"] == 1
        expected_descriptors = expected_lineage_descriptors(registry, generation["generation_id"], policy, first_publication)
        actual_descriptors = {
            (row["object_kind"], row["projection_kind"], row["instance_key"]): (
                row["contract_name"], row["root"], row["path"], row["cardinality"]
            )
            for row in rows
        }
        if actual_descriptors != expected_descriptors:
            return None
        memory_root_id = next(row["root_instance_id"] for row in package["documents"]["root_registry"]["roots"] if row["role"] == "memory")
        if any(row["root"] != "memory" or row["root_instance_id"] != memory_root_id or row["cardinality"] != "one" for row in rows):
            return None
        expected_instances = expected_projection_instances(registry, policy)
        expected_projection_keys = {(kind, instance_key) for kind, values in expected_instances.items() for instance_key in values}
        actual_projection_keys = {(kind, instance) for kind, instance in ((row[1], row[2]) for row in documents if row[0] == "projection-envelope")}
        if expected_projection_keys != actual_projection_keys:
            return None
        built: dict[str, list[dict[str, Any]]] = {kind: [] for kind in expected_instances}
        for kind, instance_key in sorted(expected_projection_keys, key=lambda row: (row[0].encode("utf-8"), (row[1] or "").encode("utf-8"))):
            envelope = documents[("projection-envelope", kind, instance_key)]
            manifest = documents[("dependency-manifest", kind, instance_key)]
            receipt = documents[("producer-receipt", kind, instance_key)]
            built[kind].append({"envelope": envelope, "manifest": manifest, "receipt": receipt, "handle": receipt["output"], "allowed_sources": manifest["sources"], "actual_reads": manifest["sources"]})
        panel = next(item["envelope"]["payload"] for item in built["management-panel"])
        absence_proof = documents.get(("publication-absence-proof", None, None))
        if first_publication:
            if not isinstance(absence_proof, dict) or absence_proof != {
                "contract": expected_contract_ref(registry, "publication-absence-proof/1.0.0", schema_sha, registry_sha),
                "schema_version": "1.0.0", "generation_id": generation["generation_id"],
                "memory_root_instance_id": memory_root_id,
                "pointer_path": registry["runtime_paths"]["panel_current_pointer"]["path"],
                "panel_state_path": registry["runtime_paths"]["panel_state"]["path"],
                "pointer_absent": True, "panel_state_absent": True,
                "fact_lock_profile_id": registry["lock_profile"]["profile_id"],
                "panel_lock_profile_id": registry["lock_profile"]["profile_id"],
                "observed_at": absence_proof["observed_at"], "proof_id": absence_proof["proof_id"],
            } or absence_proof["proof_id"] != sha256_bytes(canonical_bytes({key: value for key, value in absence_proof.items() if key != "proof_id"})):
                return None
        graph = {
            "panel": panel, "built": built, "policy": policy, "generation": generation,
            "before_pointer": None if first_publication else documents[("before-pointer", None, None)],
            "pointer": current_pointer,
            "before_state": None if first_publication else documents[("before-panel-state", None, None)],
            "state": panel_state,
            "receipt": publication_receipt, "journal": journal, "marker": marker,
            "physical_inventory": physical_inventory, "refresh_receipt": documents[("refresh-receipt", None, None)],
            "lineage_index": index, "lineage_index_path": expected_index_path,
            "lineage_documents": {path: store[path] for path in {expected_index_path, *(row["path"] for row in rows)}},
        }
        refresh_receipt = documents[("refresh-receipt", None, None)]
        leaf_store = package["live_leaf_store"]
        expected_leaf_keys = {f"{row['root_instance_id']}\0{row['path']}" for row in generation["leaf_sources"]}
        if verify_live_leaves and (
            set(leaf_store) != expected_leaf_keys
            or any(sha256_bytes(leaf_store[f"{row['root_instance_id']}\0{row['path']}"]) != row["fingerprint"] for row in generation["leaf_sources"])
        ):
            return None
        if not (
            projection_lineage_semantics(built, registry, schema, generation, policy, schema_sha, registry_sha)
            and panel_publication_semantics(graph, registry, schema, schema_sha, registry_sha)
            and publication_eligibility_semantics(
                panel, physical_inventory, policy, generation, registry, schema, schema_sha, registry_sha, built,
                package["documents"].get("mutation_intent_outbox"),
                package["documents"].get("intent_convergence"),
            )
            and catalog == panel_binding_catalog(registry, schema_sha, registry_sha)
            and validate_registered(refresh_receipt, schema, registry, "refresh-run-receipt/1.0.0", schema_sha, registry_sha)
            and source_as_of_semantics(panel, policy, refresh_receipt)
        ):
            return None
        pointer_nodes = sorted((row["kind"], row["instance_key"] or "singleton", row["id"], row["manifest_id"], graph["pointer"]["generation_id"]) for row in graph["pointer"]["projections"])
        receipt_nodes = sorted((row["projection_kind"], row["instance_key"], row["output"]["id"], row["output"]["manifest_id"], row["output"]["generation_id"]) for row in refresh_receipt["nodes"] if row["output"] is not None)
        if pointer_nodes != receipt_nodes or refresh_receipt["status"] != "published" or refresh_receipt["retry_from_instance_key"] is not None:
            return None
        return {"index": index, "documents": documents, "graph": graph, "refresh_receipt": refresh_receipt, "generation": generation, "policy": policy}
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def live_inspect_fixture(
    suite: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
    project_root: Path, expected_ids: list[str], artifact_hashes: dict[str, str],
) -> dict[str, Any]:
    package = writer_fence_fixture(
        registry, schema_sha, registry_sha, expected_ids, artifact_hashes,
        suite=suite, schema=schema, project_root=project_root,
    )
    package["fact_read_lock"] = {
        "profile_id": registry["lock_profile"]["profile_id"], "path": registry["lock_profile"]["fact_lock"]["path"],
        "mode": "shared", "acquired": True,
    }
    package["surface"] = "inspect"
    package["inspect_write_paths"] = [registry["runtime_paths"]["panel_refresh_status"]["path"]]
    package["inspect_read_set_additions"] = []
    package["inspected_at"] = "2026-07-24T03:05:00Z"
    return package


def _inspect_status(
    package: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
    outcome: str, changed_sources: list[str], error_code: str | None,
) -> dict[str, Any] | None:
    pointer = package["documents"]["current_pointer"]
    fact_state = package["documents"]["fact_state"]
    verdict = {
        "inspected_generation_id": pointer["generation_id"],
        "inspected_pointer_id": pointer["pointer_id"], "outcome": outcome,
        "inspected_at": package["inspected_at"], "observed_fact_generation": fact_state["fact_generation"],
        "changed_sources": sorted(set(changed_sources), key=lambda value: value.encode("utf-8")), "error_code": error_code,
    }
    verdict["verdict_id"] = sha256_bytes(canonical_bytes(verdict))
    status = {
        "contract": expected_contract_ref(registry, "panel-refresh-status/1.0.0", schema_sha, registry_sha),
        "schema_version": "1.0.0", "current_run_id": None,
        "current_status": "idle" if outcome == "fresh" else ("dirty" if outcome == "stale" else "blocked"),
        "last_successful_generation_id": pointer["generation_id"],
        "last_successful_refresh_at": package["refresh_completed_at"], "pending_invalidations": [],
        "latest_inspect": verdict,
    }
    status["state_id"] = sha256_bytes(canonical_bytes(status))
    return status if (
        validate_registered(status, schema, registry, "panel-refresh-status/1.0.0", schema_sha, registry_sha)
        and verdict["verdict_id"] == sha256_bytes(canonical_bytes({key: value for key, value in verdict.items() if key != "verdict_id"}))
        and status["state_id"] == sha256_bytes(canonical_bytes({key: value for key, value in status.items() if key != "state_id"}))
    ) else None


def live_inspect_read_set_semantics(package: dict[str, Any], registry: dict[str, Any]) -> bool:
    fixed_contracts = {
        "root_registry_state": "root-registry-state/1.0.0", "strict_activation_state": "strict-activation-state/1.0.0",
        "writer_fence_attestation": "writer-fence-migration-attestation/1.0.0",
        "writer_capability_registry": "writer-capability-registry/1.0.0",
        "release_evidence_set": "release-evidence-set/1.0.0",
        "release_evidence_history_index": "release-evidence-history-index/1.0.0",
        "panel_current_pointer": "panel-current-pointer/1.0.0", "panel_state": "panel-state/1.0.0",
        "fact_generation": "fact-generation-state/1.0.0", "action_ledger": "raw/action-ledger-v2",
        "action_ledger_state": "action-ledger-state/1.0.0", "action_flow_index": "action-flow-index/1.0.0",
        "fact_command_receipt_index": "fact-command-receipt-index/1.0.0",
        "mutation_intent_outbox": "mutation-intent-outbox/1.0.0",
        "intent_convergence_verdict": "intent-convergence-verdict/1.0.0",
    }
    profile = registry["live_inspect_read_profile"]
    if profile["fixed_runtime_path_keys"] != list(fixed_contracts):
        return False
    expected: set[tuple[str, str, str]] = {
        ("memory", registry["runtime_paths"][key]["path"], fixed_contracts[key]) for key in profile["fixed_runtime_path_keys"]
    }
    for spec in registry["strict_rollout"]["writer_specs"]:
        expected.update(("project", path, "raw/writer-artifact") for path in spec["artifact_paths"])
        expected.add(("project", spec["manifest_path"], "writer-build-manifest/1.0.0"))
        expected.add(("project", spec["receipt_path"], "writer-fence-receipt/1.0.0"))
    history = package["documents"]["release_evidence_history_index"]
    release_contracts = {
        registry["runtime_paths"]["release_evidence_set"]["path"]: "release-evidence-set/1.0.0",
        registry["runtime_paths"]["release_evidence_history_index"]["path"]: "release-evidence-history-index/1.0.0",
    }
    for entry in history["entries"]:
        release_contracts[entry["set_path"]] = "release-evidence-set/1.0.0"
        release_contracts[entry["transition_receipt_path"]] = "release-evidence-transition-receipt/1.0.0"
        archive = json.loads(package["release_store"][entry["set_path"]])
        for evidence in archive["entries"]:
            release_contracts[evidence["receipt_path"]] = "conformance-result/1.0.0"
            for blob in evidence["evidence_blobs"]:
                release_contracts[blob["path"]] = "raw/conformance-evidence"
    expected.update(("memory", path, contract) for path, contract in release_contracts.items())
    current_pointer = package["documents"]["current_pointer"]
    index_path = runtime_path(
        registry, "generation_lineage_index_template", generation_id=current_pointer["generation_id"],
    )
    index = json.loads(package["lineage_store"][index_path])
    lineage_contracts = {index_path: "generation-lineage-index/1.0.0", **{row["path"]: row["contract_name"] for row in index["objects"]}}
    expected.update(("memory", path, contract) for path, contract in lineage_contracts.items())
    for row in package["documents"]["workstreams"]:
        workstream_id = row["state"]["workstream_id"]
        records = {
            ("memory", row["record_path"], "raw/workstream-delivery-record"),
            ("memory", f"workstreams/{workstream_id}/delivery-record.state.json", "wdr-file-state/1.0.0"),
            ("memory", f"workstreams/{workstream_id}/action-projection.json", "wdr-action-projection/1.0.0"),
        }
        expected.update(records)
    generation_path = runtime_path(registry, "generation_envelope_template", generation_id=current_pointer["generation_id"])
    generation = json.loads(package["lineage_store"][generation_path])
    root_roles = {row["root_instance_id"]: row["role"] for row in package["documents"]["root_registry"]["roots"]}
    dynamic_reads = {(row["root"], row["path"], "raw/live-source") for row in generation["leaf_sources"]}
    dynamic_reads.update(
        (root_roles.get(key.split("\0", 1)[0], "unregistered"), key.split("\0", 1)[1], "raw/live-source")
        for key in package["live_leaf_store"]
    )
    expected.update(dynamic_reads)

    raw_store: dict[tuple[str, str], bytes | None] = {}
    conflicting_identity = False

    def add_raw(root: str, path: str, raw: bytes | None) -> None:
        nonlocal conflicting_identity
        key = (root, path)
        if key in raw_store and raw_store[key] != raw:
            conflicting_identity = True
        else:
            raw_store[key] = raw

    fixed_raw = {
        "root_registry_state": canonical_bytes(package["documents"]["root_registry"]),
        "strict_activation_state": canonical_bytes(package["documents"]["activation_state"]),
        "writer_fence_attestation": canonical_bytes(package["attestation"]),
        "writer_capability_registry": canonical_bytes(package["documents"]["capability_registry"]),
        "release_evidence_set": package["release_store"].get(registry["runtime_paths"]["release_evidence_set"]["path"]),
        "release_evidence_history_index": package["release_store"].get(registry["runtime_paths"]["release_evidence_history_index"]["path"]),
        "panel_current_pointer": package["lineage_store"].get(registry["runtime_paths"]["panel_current_pointer"]["path"]),
        "panel_state": package["lineage_store"].get(registry["runtime_paths"]["panel_state"]["path"]),
        "fact_generation": canonical_bytes(package["documents"]["fact_state"]),
        "action_ledger": package["documents"]["ledger_raw"],
        "action_ledger_state": canonical_bytes(package["documents"]["ledger_state"]),
        "action_flow_index": canonical_bytes(package["documents"]["action_flow"]),
        "fact_command_receipt_index": canonical_bytes(package["documents"]["fact_command_index"]),
        "mutation_intent_outbox": canonical_bytes(package["documents"]["mutation_intent_outbox"]),
        "intent_convergence_verdict": canonical_bytes(package["documents"]["intent_convergence"]),
    }
    for key, raw in fixed_raw.items():
        add_raw("memory", registry["runtime_paths"][key]["path"], raw)
    for path, raw in package["writer_store"].items():
        add_raw("project", path, raw)
    for path, raw in package["release_store"].items():
        add_raw("memory", path, raw)
    for path, raw in package["lineage_store"].items():
        add_raw("memory", path, raw)
    for row in package["documents"]["workstreams"]:
        workstream_id = row["state"]["workstream_id"]
        add_raw("memory", row["record_path"], row["wdr_raw"])
        add_raw("memory", f"workstreams/{workstream_id}/delivery-record.state.json", canonical_bytes(row["state"]))
        add_raw("memory", f"workstreams/{workstream_id}/action-projection.json", canonical_bytes(row["sidecar"]))
    for identity, raw in package["live_leaf_store"].items():
        root_instance_id, path = identity.split("\0", 1)
        add_raw(root_roles.get(root_instance_id, "unregistered"), path, raw)
    if conflicting_identity or any(root not in {"memory", "project"} for root, _, _ in expected):
        return False

    additions = [
        (row["root"], row["path"], row["contract_name"])
        for row in package.get("inspect_read_set_additions", [])
    ]
    read_plan = sorted(expected, key=lambda row: tuple(value.encode("utf-8") for value in row)) + additions
    mutation = package.get("inspect_read_mutation", "none")
    if mutation == "omit-one" and read_plan:
        read_plan = read_plan[1:]
    elif mutation == "duplicate" and read_plan:
        read_plan.insert(1, read_plan[0])
    elif mutation == "wrong-root" and read_plan:
        root, path, contract_name = read_plan[0]
        read_plan[0] = ("project" if root == "memory" else "memory", path, contract_name)
    elif mutation == "alias" and read_plan:
        root, path, contract_name = read_plan[0]
        read_plan[0] = (root, f"./{path}", contract_name)

    actual: list[tuple[str, str, str]] = []
    absent_attempts: set[tuple[str, str, str]] = set()
    for index_in_plan, read in enumerate(read_plan):
        root, path, contract_name = read
        if (
            root not in {"memory", "project"}
            or not path or path.startswith("/") or path != unicodedata.normalize("NFC", path)
            or "\\" in path or ":" in path or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            return False
        raw = raw_store.get((root, path))
        if raw is None:
            absent_attempts.add(read)
            continue
        if not isinstance(raw, bytes):
            return False
        # A read becomes observable only after the resolver consumed all returned bytes.
        consumed = len(raw) == len(bytes(raw)) and sha256_bytes(bytes(raw)).startswith("sha256:")
        if mutation == "unconsumed" and index_in_plan == 0:
            consumed = False
        if consumed:
            actual.append(read)
    if len(actual) != len(set(actual)):
        return False
    actual_set = set(actual)
    expected_absent = {
        read for read in expected
        if read[2] == "raw/live-source" and raw_store.get((read[0], read[1])) is None
    }
    return (
        not additions
        and absent_attempts == expected_absent
        and actual_set == expected - expected_absent
    )


def live_inspect_semantics(
    package: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
    expected_ids: list[str], hashes: dict[str, str], security_context: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        status_path = registry["runtime_paths"]["panel_refresh_status"]["path"]
        if package["inspect_write_paths"] != [status_path]:
            return None
        if not isinstance(security_context, dict) or not security_context.get("available"):
            return _inspect_status(package, registry, schema, schema_sha, registry_sha, "unverifiable", [], "TRUSTED_CLOCK_UNAVAILABLE")
        if security_context.get("clock_source") != "host-secure-clock-v1" or security_context.get("evaluation_time") != package["inspected_at"]:
            return None
        lock = package["fact_read_lock"]
        if not (
            lock["acquired"] and lock["mode"] == "shared"
            and lock["profile_id"] == registry["lock_profile"]["profile_id"]
            and lock["path"] == registry["lock_profile"]["fact_lock"]["path"]
        ):
            return _inspect_status(package, registry, schema, schema_sha, registry_sha, "unverifiable", [], "FACT_READ_LOCK_UNAVAILABLE")
        strict_valid = strict_writer_fence_activation_semantics(
            package, registry, schema, schema_sha, registry_sha, expected_ids, hashes, security_context,
        )
        if not strict_valid:
            if not strict_activation_control_semantics(
                package, registry, schema, schema_sha, registry_sha, expected_ids, hashes, security_context,
            ):
                return _inspect_status(package, registry, schema, schema_sha, registry_sha, "migration-required", [], "STRICT_ACTIVATION_REQUIRED")
            diagnostic_lineage = load_strict_lineage(package, registry, schema, schema_sha, registry_sha, verify_live_leaves=False)
            if diagnostic_lineage is None:
                return _inspect_status(package, registry, schema, schema_sha, registry_sha, "migration-required", [], "PUBLISHED_LINEAGE_INVALID")
            diagnostic_fact = package["documents"]["fact_state"]
            if (
                validate_registered(diagnostic_fact, schema, registry, "fact-generation-state/1.0.0", schema_sha, registry_sha)
                and diagnostic_fact["state_id"] == sha256_bytes(canonical_bytes({key: value for key, value in diagnostic_fact.items() if key != "state_id"}))
                and diagnostic_fact["fact_generation"] != diagnostic_lineage["generation"]["fact_generation"]
            ):
                return _inspect_status(
                    package, registry, schema, schema_sha, registry_sha, "stale",
                    [registry["runtime_paths"]["fact_generation"]["path"]], "SOURCE_DRIFT",
                )
            return _inspect_status(package, registry, schema, schema_sha, registry_sha, "migration-required", [], "STRICT_ACTIVATION_REQUIRED")
        pointer_path = registry["runtime_paths"]["panel_current_pointer"]["path"]
        pointer_raw = package["lineage_store"].get(pointer_path)
        if not isinstance(pointer_raw, bytes):
            return _inspect_status(package, registry, schema, schema_sha, registry_sha, "migration-required", [], "PUBLISHED_LINEAGE_INVALID")
        pointer = json.loads(pointer_raw)
        if not (
            canonical_bytes(pointer) == pointer_raw
            and validate_registered(pointer, schema, registry, "panel-current-pointer/1.0.0", schema_sha, registry_sha)
            and pointer["pointer_id"] == sha256_bytes(canonical_bytes({key: value for key, value in pointer.items() if key != "pointer_id"}))
        ):
            return _inspect_status(package, registry, schema, schema_sha, registry_sha, "migration-required", [], "PUBLISHED_LINEAGE_INVALID")
        lineage = load_strict_lineage(package, registry, schema, schema_sha, registry_sha, verify_live_leaves=False)
        if lineage is None:
            return _inspect_status(package, registry, schema, schema_sha, registry_sha, "migration-required", [], "PUBLISHED_LINEAGE_INVALID")
        fact_state = package["documents"]["fact_state"]
        if not (
            validate_registered(fact_state, schema, registry, "fact-generation-state/1.0.0", schema_sha, registry_sha)
            and fact_state["state_id"] == sha256_bytes(canonical_bytes({key: value for key, value in fact_state.items() if key != "state_id"}))
        ):
            return _inspect_status(package, registry, schema, schema_sha, registry_sha, "migration-required", [], "FACT_STATE_INVALID")
        generation = lineage["generation"]
        expected_leaves = {f"{row['root_instance_id']}\0{row['path']}": row for row in generation["leaf_sources"]}
        live_store = package["live_leaf_store"]
        changed = []
        for key in sorted(set(expected_leaves) | set(live_store), key=lambda value: value.encode("utf-8")):
            source = expected_leaves.get(key)
            raw = live_store.get(key)
            if source is None:
                changed.append(key.split("\0", 1)[1])
            elif key not in live_store:
                changed.append(source["path"])
            elif not isinstance(raw, bytes):
                return _inspect_status(package, registry, schema, schema_sha, registry_sha, "unverifiable", changed, "SOURCE_UNREADABLE")
            elif sha256_bytes(raw) != source["fingerprint"]:
                changed.append(source["path"])
        if fact_state["fact_generation"] != generation["fact_generation"]:
            changed.append(registry["runtime_paths"]["fact_generation"]["path"])
        if not live_inspect_read_set_semantics(package, registry):
            return None
        return _inspect_status(
            package, registry, schema, schema_sha, registry_sha,
            "stale" if changed else "fresh", changed, "SOURCE_DRIFT" if changed else None,
        )
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def panel_publication_idempotent_replay_semantics(graph: dict[str, Any]) -> bool:
    receipt, pointer, state = graph["receipt"], graph["pointer"], graph["state"]
    return bool(
        graph["before_pointer"] is None and graph["before_state"] is None
        and receipt["before_pointer_id"] is None and receipt["before_panel_generation"] == 0
        and receipt["after_pointer_id"] == pointer["pointer_id"]
        and receipt["after_panel_generation"] == state["panel_generation"] == 1
        and receipt["transaction_id"] == graph["journal"]["transaction_id"]
        and receipt["receipt_id"] == sha256_bytes(canonical_bytes({key: value for key, value in receipt.items() if key != "receipt_id"}))
        and pointer["pointer_id"] == sha256_bytes(canonical_bytes({key: value for key, value in pointer.items() if key != "pointer_id"}))
        and state["state_id"] == sha256_bytes(canonical_bytes({key: value for key, value in state.items() if key != "state_id"}))
    )


def resolve_panel_publication_replay_store(
    root: Path, transaction_id: str, command_fingerprint: str, schema_path: Path, registry_path: Path,
    schema_sha: str, registry_sha: str,
) -> str:
    try:
        schema_raw, registry_raw = schema_path.read_bytes(), registry_path.read_bytes()
        if sha256_bytes(schema_raw) != schema_sha or sha256_bytes(registry_raw) != registry_sha:
            return "invalid"
        schema, registry = json.loads(schema_raw), json.loads(registry_raw)
        memory_root = root / "memory"
        receipt_path = runtime_path(registry, "panel_receipt_template", transaction_id=transaction_id)
        receipt_file = memory_root / receipt_path
        if not receipt_file.exists():
            return "new"
        receipt_raw = receipt_file.read_bytes()
        receipt = json.loads(receipt_raw)
        if not (
            canonical_bytes(receipt) == receipt_raw
            and validate_registered(receipt, schema, registry, "panel-publication-receipt/1.0.0", schema_sha, registry_sha)
            and receipt["receipt_id"] == sha256_bytes(canonical_bytes({key: value for key, value in receipt.items() if key != "receipt_id"}))
            and receipt["transaction_id"] == transaction_id
        ):
            return "invalid"
        if receipt["command_fingerprint"] != command_fingerprint:
            return "conflict"
        generation_id = receipt["generation_id"]
        index_path = runtime_path(registry, "generation_lineage_index_template", generation_id=generation_id)
        index_raw = (memory_root / index_path).read_bytes()
        index = json.loads(index_raw)
        journal_path = runtime_path(registry, "publication_journal_template", generation_id=generation_id)
        marker_path = runtime_path(registry, "publication_marker_template", generation_id=generation_id)
        pointer_path = registry["runtime_paths"]["panel_current_pointer"]["path"]
        state_path = registry["runtime_paths"]["panel_state"]["path"]
        lineage_paths = {
            index_path, *(row["path"] for row in index["objects"]), journal_path, marker_path,
            receipt_path, pointer_path, state_path,
        }
        lineage_store = {path: (memory_root / path).read_bytes() for path in lineage_paths}
        root_registry_path = registry["runtime_paths"]["root_registry_state"]["path"]
        attestation_path = registry["runtime_paths"]["writer_fence_attestation"]["path"]
        outbox_path = registry["runtime_paths"]["mutation_intent_outbox"]["path"]
        convergence_path = registry["runtime_paths"]["intent_convergence_verdict"]["path"]
        root_registry_raw = (memory_root / root_registry_path).read_bytes()
        attestation_raw = (memory_root / attestation_path).read_bytes()
        outbox_raw, convergence_raw = (memory_root / outbox_path).read_bytes(), (memory_root / convergence_path).read_bytes()
        root_registry, attestation = json.loads(root_registry_raw), json.loads(attestation_raw)
        outbox, convergence = json.loads(outbox_raw), json.loads(convergence_raw)
        if any(
            canonical_bytes(document) != raw
            for document, raw in (
                (root_registry, root_registry_raw), (attestation, attestation_raw),
                (outbox, outbox_raw), (convergence, convergence_raw),
            )
        ):
            return "invalid"
        loaded = load_strict_lineage(
            {
                "attestation": attestation,
                "lineage_store": lineage_store,
                "documents": {
                    "root_registry": root_registry,
                    "mutation_intent_outbox": outbox,
                    "intent_convergence": convergence,
                },
                "live_leaf_store": {},
            },
            registry, schema, schema_sha, registry_sha, verify_live_leaves=False,
        )
        return "noop" if loaded is not None and loaded["graph"]["receipt"] == receipt else "invalid"
    except (KeyError, OSError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return "invalid"


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix().encode("utf-8"))
    }


def first_publication_fresh_process_semantics(
    suite: dict[str, Any], registry: dict[str, Any], schema: dict[str, Any], schema_sha: str, registry_sha: str,
    project_root: Path, expected_ids: list[str], hashes: dict[str, str],
) -> bool:
    package = writer_fence_fixture(
        registry, schema_sha, registry_sha, expected_ids, hashes, suite=suite, schema=schema,
        project_root=project_root, first_publication=True,
    )
    package.update({
        "surface": "inspect",
        "fact_read_lock": {
            "profile_id": registry["lock_profile"]["profile_id"], "path": registry["lock_profile"]["fact_lock"]["path"],
            "mode": "shared", "acquired": True,
        },
        "inspect_write_paths": [registry["runtime_paths"]["panel_refresh_status"]["path"]],
        "inspected_at": "2026-07-24T03:05:00Z",
    })
    security_context = {"clock_source": "host-secure-clock-v1", "evaluation_time": package["inspected_at"], "available": True}
    status = live_inspect_semantics(package, registry, schema, schema_sha, registry_sha, expected_ids, hashes, security_context)
    if status is None or status["latest_inspect"]["outcome"] != "fresh":
        return False
    lineage = load_strict_lineage(package, registry, schema, schema_sha, registry_sha, verify_live_leaves=False)
    if lineage is None or not panel_publication_idempotent_replay_semantics(lineage["graph"]):
        return False
    child = (
        "import importlib.util,pathlib,sys;"
        "spec=importlib.util.spec_from_file_location('panel_sync_runner',sys.argv[1]);"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "outcome=m.resolve_panel_publication_replay_store(pathlib.Path(sys.argv[2]),sys.argv[3],sys.argv[4],pathlib.Path(sys.argv[5]),pathlib.Path(sys.argv[6]),sys.argv[7],sys.argv[8]);"
        "sys.stdout.write(outcome);raise SystemExit(0)"
    )
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        memory_root = root / "memory"
        control_root = root / "control"
        control_root.mkdir(parents=True, exist_ok=True)
        schema_path, registry_path = control_root / "schema.json", control_root / "registry.json"
        schema_path.write_bytes((Path(__file__).resolve().parents[1] / "panel-sync-contracts.schema.json").read_bytes())
        registry_path.write_bytes(canonical_bytes(registry))
        store = copy.deepcopy(package["lineage_store"])
        store[registry["runtime_paths"]["root_registry_state"]["path"]] = canonical_bytes(package["documents"]["root_registry"])
        store[registry["runtime_paths"]["writer_fence_attestation"]["path"]] = canonical_bytes(package["attestation"])
        store[registry["runtime_paths"]["mutation_intent_outbox"]["path"]] = canonical_bytes(package["documents"]["mutation_intent_outbox"])
        store[registry["runtime_paths"]["intent_convergence_verdict"]["path"]] = canonical_bytes(package["documents"]["intent_convergence"])
        for path, raw in store.items():
            target = memory_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        before = _tree_bytes(root)
        arguments = [
            sys.executable, "-c", child, str(Path(__file__).resolve()), str(root),
            lineage["graph"]["receipt"]["transaction_id"], lineage["graph"]["receipt"]["command_fingerprint"],
            str(schema_path), str(registry_path), schema_sha, registry_sha,
        ]
        first = subprocess.run(arguments, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        second = subprocess.run(arguments, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        conflict_arguments = list(arguments)
        conflict_arguments[6] = "sha256:" + "f" * 64
        conflict = subprocess.run(conflict_arguments, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        after = _tree_bytes(root)
    return (
        first.returncode == second.returncode == conflict.returncode == 0
        and first.stdout == second.stdout == b"noop" and conflict.stdout == b"conflict"
        and before == after
    )


def semantic_validator_dispatch(
    registry: dict[str, Any], schema: dict[str, Any], suite: dict[str, Any], project_root: Path,
    schema_sha: str, registry_sha: str, omitted_handler: str | None = None,
) -> tuple[bool, set[str]]:
    if not semantic_registry_semantics(registry):
        return False, set()

    panel, upstreams, _, policy, generation = panel_fixture(suite["contract_schema_vectors"], registry, schema_sha, registry_sha, project_root)
    physical_inventory = physical_inventory_fixture(registry, policy, generation["fact_generation"], schema_sha, registry_sha)
    for binding in registry["panel_binding_map"]:
        payload = upstreams[binding["projection_kind"]]
        value = {row["scenario"]: copy.deepcopy(row) for row in payload} if binding["merge_mode"] == "object-by-key" else copy.deepcopy(payload)
        set_pointer(panel, binding["panel_pointer"], value)
    panel["panel_id"] = sha256_bytes(canonical_bytes({key: value for key, value in panel.items() if key != "panel_id"}))
    built, outer_ok = build_projection_lineage(panel, upstreams, registry, schema, schema_sha, registry_sha, project_root, policy)
    lineage_ok = outer_ok and projection_lineage_semantics(built, registry, schema, generation, policy, schema_sha, registry_sha)
    publication_graph = panel_publication_fixture(panel, built, policy, generation, registry, schema_sha, registry_sha)
    create_vector = next(row for row in suite["wdr_vectors"] if row["id"] == "create-byte-exact")
    create_command = copy.deepcopy(create_vector["command"])
    create_input = copy.deepcopy(create_command["create_input"])
    create_input["input_id"] = sha256_bytes(canonical_bytes({key: value for key, value in create_input.items() if key != "input_id"}))
    create_command["create_input"] = create_input
    fact_graphs = [
        fact_attribution_fixture(schema_sha, registry_sha, registry, fixture_kind)
        for fixture_kind in (
            "action", "wdr-status", "wdr-meeting-history", "wdr-owned-section", "wdr-roadmap",
            "wdr-refresh-actions", "intent-only", "owned-risk-flow", "owned-decision",
        )
    ]
    fact_graphs.append(fact_attribution_fixture(schema_sha, registry_sha, registry, "wdr-create", create_command))
    journal, marker = journal_fixture("fact", schema_sha, registry_sha, registry)
    repair_graph = repair_graph_fixture(schema_sha, registry_sha, registry)
    status_batch = status_intent_fixture(registry, schema_sha, registry_sha)
    meeting_plan = meeting_plan_intent_fixture(registry, schema_sha, registry_sha)
    program_status_package = program_status_wdr_fixture(suite, registry, schema_sha, registry_sha)
    drift_content_package = drift_content_fixture(registry, schema_sha, registry_sha)
    expected_ids = sorted({
        row["id"]
        for key, values in suite.items()
        if key.endswith("_vectors") or key == "journal_fault_matrix"
        for row in values
    })
    artifact_hashes = {
        "registry": registry_sha,
        "schema": schema_sha,
        "protocol": registry["protocol"]["sha256"],
        "suite": registry["conformance_suite"]["sha256"],
    }
    strict_registry = design_release_registry_fixture(registry)
    strict_registry_sha = sha256_bytes(canonical_bytes(strict_registry))
    strict_hashes = {**artifact_hashes, "registry": strict_registry_sha}
    strict_suite = replace_tokens(suite, {registry_sha: strict_registry_sha})
    bootstrap_graph = bootstrap_migration_fixture(
        {"ledger_format": "legacy20", "action_flow_preimage": "brownfield-v1", "workstreams": ["l1-checkout"]},
        registry, schema_sha, registry_sha,
    )
    writer_fence_package = writer_fence_fixture(
        strict_registry, schema_sha, strict_registry_sha, expected_ids, strict_hashes,
        suite=strict_suite, schema=schema, project_root=project_root,
    )
    release_receipts, release_blobs = implementation_conformance_receipts(expected_ids, strict_hashes, strict_registry)
    release_transition = release_evidence_transition_fixture(
        release_receipts, release_blobs, strict_registry, schema_sha, strict_registry_sha,
    )
    activation_transition = activation_transition_fixture(
        writer_fence_package, strict_registry, schema_sha, strict_registry_sha,
    )
    inspect_package = live_inspect_fixture(
        strict_suite, strict_registry, schema, schema_sha, strict_registry_sha, project_root, expected_ids, strict_hashes,
    )
    security_context = {"clock_source": "host-secure-clock-v1", "evaluation_time": "2026-07-24T03:05:00Z", "available": True}
    transition_security_context = {"clock_source": "host-secure-clock-v1", "evaluation_time": "2026-07-24T03:15:00Z", "available": True}

    def snapshot_authority() -> bool:
        lineage = load_strict_lineage(
            writer_fence_package, strict_registry, schema, schema_sha, strict_registry_sha,
            verify_live_leaves=False,
        )
        if lineage is None:
            return False
        return source_as_of_semantics(
            lineage["graph"]["panel"], lineage["policy"], lineage["refresh_receipt"], strict_registry,
            schema, schema_sha, strict_registry_sha,
        )

    def intent_convergence() -> bool:
        return intent_convergence_semantics(
            inspect_package["documents"]["mutation_intent_outbox"],
            inspect_package["documents"]["intent_convergence"],
            strict_registry, schema, schema_sha, strict_registry_sha,
        )

    def fact_replay() -> bool:
        graph = fact_graphs[0]
        receipt_path = graph["command_index"]["entries"][0]["receipt_path"]
        outcome, receipt = resolve_fact_command_replay(
            graph["command_index"], {receipt_path: canonical_bytes(graph["receipt"])},
            graph["command"]["command_id"], sha256_bytes(canonical_bytes(graph["command"])),
            registry, schema, schema_sha, registry_sha,
        )
        conflict, _ = resolve_fact_command_replay(
            graph["command_index"], {receipt_path: canonical_bytes(graph["receipt"])},
            graph["command"]["command_id"], "sha256:" + "f" * 64,
            registry, schema, schema_sha, registry_sha,
        )
        return outcome == "noop" and receipt == graph["receipt"] and conflict == "conflict"

    def registry_closure() -> bool:
        registered_enumerators = {row["id"] for row in registry["dependency_enumerators"]}
        profile_enumerators = {source["enumerator"]["id"] for profile in registry["projection_input_profiles"] for source in profile["required_sources"]}
        supported = profile_enumerators | {"physical-workstream-inventory-v1"}
        profiles_exact = all(
            instrumented_read_trace(profile, ["l1-checkout"], "none", policy)[0]
            == instrumented_read_trace(profile, ["l1-checkout"], "none", policy)[1]
            for profile in registry["projection_input_profiles"]
        )
        return (
            registered_enumerators == supported
            and registry_dag_semantics(registry)
            and enumerator_temp_tree_semantics()
            and physical_workstream_inventory_temp_tree_semantics("none", schema, registry, schema_sha, registry_sha)
            and profiles_exact
            and all_ordering_rules_semantics(registry, schema, suite, project_root, schema_sha, registry_sha, "none")
            and identity_set_semantics(registry, schema_sha, registry_sha)
            and runtime_paths_semantics(registry)
        )

    handlers = {
        "panel-publication-eligibility/1.0.0": lambda: lineage_ok and publication_eligibility_semantics(panel, physical_inventory, policy, generation, registry, schema, schema_sha, registry_sha, built),
        "projection-registry-closure/1.0.0": registry_closure,
        "fact-receipt-attribution/1.0.0": lambda: all(
            fact_attribution_semantics(
                graph, registry, schema, schema_sha, registry_sha,
                *runtime_authority_fixture(registry, schema_sha, registry_sha, command_producer(graph["command"])),
            )
            for graph in fact_graphs
        ),
        "owned-fact-command-semantics/1.0.0": lambda: all(
            fact_attribution_semantics(
                graph, registry, schema, schema_sha, registry_sha,
                *runtime_authority_fixture(registry, schema_sha, registry_sha, command_producer(graph["command"])),
            )
            for graph in fact_graphs if command_kind(graph["command"]) == "owned"
        ),
        "transaction-journal-semantics/1.0.0": lambda: journal_semantics(journal, marker, schema, registry, schema_sha, registry_sha),
        "repair-graph-semantics/1.0.0": lambda: repair_graph_semantics(
            repair_graph, schema, registry, schema_sha, registry_sha,
            *runtime_authority_fixture(registry, schema_sha, registry_sha, "adp-status-sync"),
        ),
        "release-evidence-transition-semantics/1.0.0": lambda: release_evidence_transition_semantics(
            release_transition, strict_registry, schema, schema_sha, strict_registry_sha,
            expected_ids, strict_hashes, transition_security_context,
        ),
        "activation-transition-semantics/1.0.0": lambda: activation_transition_semantics(
            activation_transition, strict_registry, schema, schema_sha, strict_registry_sha,
        ),
        "panel-publication-graph/1.0.0": lambda: lineage_ok and panel_publication_semantics(publication_graph, registry, schema, schema_sha, registry_sha),
        "panel-binding-semantics/1.0.0": lambda: lineage_ok and panel_binding_semantics(panel, built, registry, policy, generation),
        "panel-v1-same-generation-composition/1.0.0": lambda: panel_v1_composition_valid(panel, registry, project_root) and execute_panel_v2_consumer(panel, registry, schema, project_root) is not None,
        "status-intent-application/1.0.0": lambda: status_intent_application_semantics(status_batch, registry, schema, schema_sha, registry_sha),
        "meeting-plan-intent-carriers/1.0.0": lambda: meeting_plan_intent_carrier_semantics(meeting_plan, registry, schema, schema_sha, registry_sha),
        "program-status-current-from-wdr/1.0.0": lambda: program_status_current_from_wdr_semantics(program_status_package, registry, schema, schema_sha, registry_sha),
        "action-projection-drift-content/1.0.0": lambda: action_projection_drift_content_semantics(drift_content_package, registry, schema, schema_sha, registry_sha),
        "bootstrap-migration-attribution/1.0.0": lambda: bootstrap_migration_semantics(bootstrap_graph, registry, schema, schema_sha, registry_sha),
        "strict-writer-fence-activation/1.0.0": lambda: strict_writer_fence_activation_semantics(
            writer_fence_package, strict_registry, schema, schema_sha, strict_registry_sha, expected_ids, strict_hashes, security_context,
        ),
        "live-inspect-semantics/1.0.0": lambda: (
            (status := live_inspect_semantics(
                inspect_package, strict_registry, schema, schema_sha, strict_registry_sha, expected_ids, strict_hashes, security_context,
            )) is not None
            and status["latest_inspect"]["outcome"] == "fresh"
        ),
        "snapshot-time-authority/1.0.0": snapshot_authority,
        "intent-outbox-convergence/1.0.0": intent_convergence,
        "fact-command-replay/1.0.0": fact_replay,
    }
    if omitted_handler is not None:
        handlers.pop(omitted_handler, None)

    executed: set[str] = set()
    results: list[bool] = []
    for row in registry["semantic_validators"]:
        handler = handlers.get(row["id"])
        if handler is None:
            continue
        executed.add(row["id"])
        try:
            result = bool(handler())
            results.append(result)
        except (KeyError, IndexError, TypeError, ValueError, OSError, subprocess.SubprocessError):
            results.append(False)
    registered_ids = {row["id"] for row in registry["semantic_validators"]}
    return executed == registered_ids == set(handlers) and all(results), executed


def run(suite: dict[str, Any], schema: dict[str, Any], registry: dict[str, Any], substitutions: dict[str, str], project_root: Path, actual_hashes: dict[str, str]) -> tuple[list[str], list[str]]:
    suite = replace_tokens(suite, substitutions)
    passed: list[str] = []
    failed: list[str] = []

    def check(vector_id: str, condition: bool) -> None:
        (passed if condition else failed).append(vector_id)

    for item in suite["canonical_json_vectors"]:
        try:
            value = "".join(chr(unit) for unit in item["input_code_units"]) if "input_code_units" in item else item["input"]
            actual = canonical_bytes(value).decode()
            check(item["id"], "expected_utf8" in item and actual == item["expected_utf8"])
        except ValueError:
            check(item["id"], item.get("expected_error") in {"JCS_INVALID_UNICODE", "JCS_NUMBER_PROFILE_INVALID"})

    for item in suite["contract_schema_vectors"]:
        check(item["id"], validate(item["instance"], schema, item["schema_def"]) is item["expected_valid"])

    pinned = {item["id"]: item for item in registry["pinned_source_artifacts"]}
    template = (project_root / pinned["workstream-delivery-record/1.0.0"]["path"]).read_text(encoding="utf-8")
    current_fields = {"status", "phase", "progress", "blockers", "risks", "dependencies", "change_notes", "last_status_sync", "refresh_actions", "roadmap"}
    for item in suite["wdr_vectors"]:
        vector_id = item["id"]
        if vector_id == "create-byte-exact":
            command = dict(item["command"])
            create_input = dict(command["create_input"])
            create_input.pop("input_id")
            create_input["input_id"] = sha256_bytes(canonical_bytes(create_input))
            command["create_input"] = create_input
            rendered = render_create(template, create_input)
            expected_logical = dict(create_input); expected_logical.pop("input_id")
            condition = rendered == command["rendered_record"] and validate(command, schema, "wdrCommandV1") and command["workstream_id"] == command["create_input"]["workstream_id"] and expected_logical == item["create_input_without_identity"]
            check(vector_id, condition and sha256_bytes(rendered.encode()) == command["rendered_sha256"])
        elif vector_id == "collection-add-and-revision":
            values = ["access"]
            for value in item["patch"]["blockers"]["values"]:
                if value not in values:
                    values.append(value)
            rendered = "; ".join(value.replace("\\", "\\\\").replace(";", "\\;") for value in values)
            check(vector_id, item["before"].replace("- Blockers: access", f"- Blockers: {rendered}") == item["expected"] and item["after_wdr_revision"] == item["before_wdr_revision"] + 1)
        elif vector_id == "meeting-region-whole-file":
            records = sorted(item["records"], key=lambda row: (row["observed_at"].encode(), row["entry_id"].encode()))
            actual = item["before"].replace("## Record Rule\n", "## Meeting Sync History\n\n" + "".join(meeting_block(row) for row in records) + "## Record Rule\n")
            check(vector_id, actual == item["expected"])
        elif vector_id == "legacy-section-order-and-first-status-patch":
            actual = migrate_wdr(item["before"], item["patch"]["last_status_sync"])
            check(vector_id, actual == item["expected"] and item["after_wdr_revision"] == 1 and item["after_file_generation"] == 1)
        elif vector_id == "mixed-patch-command-level-revision":
            check(vector_id, bool(current_fields & set(item["fields"])) and item["expected_wdr_revision"] == item["before_wdr_revision"] + 1 and item["expected_file_generation"] == item["before_file_generation"] + 1)
        elif vector_id == "meeting-status-intent-routed":
            check(vector_id, item["origin_producer"] == "adp-meeting-sync" and item["command_issuer"] == "adp-status-sync" and set(item["intent_fields"]) <= current_fields)
        elif vector_id.startswith("wdr-meeting-history-"):
            record = {
                "entry_id": "M-REPLAY-1", "command_id": "cmd-replay-1", "observed_at": "2026-07-24T02:00:00Z",
                "source_path": "meetings/replay.md", "source_fingerprint": "sha256:" + "a" * 64,
                "classification": "wdr_update", "summary": "Reviewed", "owner": "FDE-C",
                "due_trigger": "next sync", "status": "noted",
            }
            before = fixture_wdr("l1-checkout")
            try:
                if item["mutation"] == "meeting-history-duplicate-command-key":
                    apply_wdr_patch(before, {"set": {"meeting_history_append": [record, copy.deepcopy(record)]}})
                    valid = False
                else:
                    once = apply_wdr_patch(before, {"set": {"meeting_history_append": [record]}})
                    if item["mutation"] == "meeting-history-identical-replay":
                        twice = apply_wdr_patch(once, {"set": {"meeting_history_append": [copy.deepcopy(record)]}})
                        valid = twice == once and wdr_counter_delta(once, twice, "l1-checkout") == (0, 0)
                    elif item["mutation"] == "meeting-history-conflicting-replay":
                        apply_wdr_patch(once, {"set": {"meeting_history_append": [{**record, "summary": "Different bytes"}]}})
                        valid = False
                    elif item["mutation"] == "meeting-history-multi-entry-command":
                        second = {**record, "entry_id": "M-REPLAY-2", "observed_at": "2026-07-24T02:01:00Z"}
                        merged = apply_wdr_patch(once, {"set": {"meeting_history_append": [second, copy.deepcopy(record)]}})
                        rows = parse_meeting_history(split_wdr(merged)[1]["Meeting Sync History"])
                        valid = (
                            len(rows) == 2
                            and {row["command_id"] for row in rows} == {record["command_id"]}
                            and [(row["observed_at"], row["entry_id"]) for row in rows] == [
                                ("2026-07-24T02:00:00Z", "M-REPLAY-1"),
                                ("2026-07-24T02:01:00Z", "M-REPLAY-2"),
                            ]
                        )
                    elif item["mutation"] == "meeting-history-invalid-calendar-time":
                        _utc_instant("2026-02-30T02:00:00Z")
                        valid = False
                    else:
                        earlier = {**record, "entry_id": "M-REPLAY-0", "command_id": "cmd-replay-0", "observed_at": "2026-07-24T01:00:00Z"}
                        merged = apply_wdr_patch(once, {"set": {"meeting_history_append": [copy.deepcopy(record), earlier]}})
                        rows = parse_meeting_history(split_wdr(merged)[1]["Meeting Sync History"])
                        replayed = apply_wdr_patch(merged, {"set": {"meeting_history_append": [earlier, copy.deepcopy(record)]}})
                        valid = [(row["observed_at"], row["entry_id"]) for row in rows] == [
                            ("2026-07-24T01:00:00Z", "M-REPLAY-0"), ("2026-07-24T02:00:00Z", "M-REPLAY-1")
                        ] and replayed == merged
            except ValueError:
                valid = item.get("expected_error") == "WDR_MUTATION_INVALID"
            check(vector_id, valid)
        elif vector_id in {"wdr-noncanonical-literal-tbd-rejected", "wdr-noncanonical-escape-rejected"}:
            try:
                _parse_wdr_list("TBD; review" if item["mutation"] == "noncanonical-literal-tbd" else "review\\x")
                valid = False
            except ValueError:
                valid = item["expected_error"] == "WDR_MUTATION_INVALID"
            check(vector_id, valid)
        elif vector_id == "wdr-next-actions-manual-first-managed-by-id":
            first = "[action_id:A-A-1] FDE-A: First (due: next sync)"
            second = "[action_id:A-B-1] FDE-B: Second (due: later)"
            after = apply_wdr_patch(fixture_wdr("l1-checkout"), {"set": {"refresh_actions": True}}, [first, second])
            actions = wdr_current_signature(after, "l1-checkout")["next_actions"]
            manual, managed = partition_next_actions(actions)
            check(vector_id, actions == ["review", first, second] and manual == ["review"] and managed == [first, second])
        elif vector_id == "wdr-roadmap-byte-exact-replace-replay":
            before = fixture_wdr("l1-checkout")
            patch = {"set": {"roadmap": {"mode": "replace", "lines": ["| Milestone | Target |", "| --- | --- |", "| M1 | Gate A |"]}}}
            once = apply_wdr_patch(before, patch)
            twice = apply_wdr_patch(once, patch)
            roadmap_section = split_wdr(once)[1]["Roadmap"]
            check(vector_id, roadmap_section == "## Roadmap\n\n| Milestone | Target |\n| --- | --- |\n| M1 | Gate A |" and twice == once)
        elif vector_id == "wdr-owned-sections-byte-exact-all":
            before = fixture_wdr("l1-checkout")
            _, before_sections = split_wdr(before)
            headings = {
                "acceptance": "Acceptance", "scope": "Scope", "cross-workstream-links": "Cross-Workstream Links",
                "decisions-evidence": "Decisions and Evidence", "checkpoint-sync-log": "Checkpoint Sync Log",
            }
            allowed = sorted({
                section for spec in registry["strict_rollout"]["writer_specs"]
                if "patch" in spec["allowed_operations"] and "owned_sections" in spec["allowed_fields"]
                for section in spec["allowed_sections"]
            }, key=lambda value: value.encode("utf-8"))
            valid = allowed == sorted(headings, key=lambda value: value.encode("utf-8"))
            for slug in allowed:
                heading = headings[slug]
                replace_patch = {"set": {"owned_sections": [{"section": slug, "mode": "replace", "lines": [f"Replacement {slug}"]}]}}
                replaced = apply_wdr_patch(before, replace_patch)
                replayed = apply_wdr_patch(replaced, replace_patch)
                append_patch = {"set": {"owned_sections": [{"section": slug, "mode": "append", "lines": [f"Append {slug}"]}]}}
                appended = apply_wdr_patch(before, append_patch)
                replaced_section = split_wdr(replaced)[1][heading]
                appended_section = split_wdr(appended)[1][heading]
                expected_append = (
                    before_sections[heading].rstrip("\n") + f"\nAppend {slug}"
                    if heading in before_sections else f"## {heading}\n\nAppend {slug}"
                )
                valid = valid and replaced_section == f"## {heading}\n\nReplacement {slug}" and appended_section == expected_append and replayed == replaced
            check(vector_id, valid)
        elif vector_id == "wdr-owned-section-heading-injection-rejected":
            try:
                apply_wdr_patch(fixture_wdr("l1-checkout"), {"set": {"owned_sections": [{"section": "checkpoint-sync-log", "mode": "append", "lines": ["## Injected"]}]}})
                valid = False
            except ValueError:
                valid = item["expected_error"] == "WDR_MUTATION_INVALID"
            check(vector_id, valid)
        else:
            issuer = item["issuer"]["producer_id"]
            fields = set(item["set"])
            host_matches = item.get("host_capability_producer", issuer) == issuer
            allowed = host_matches and ((fields <= current_fields and issuer == "adp-status-sync") or (fields == {"meeting_history_append"} and issuer == "adp-meeting-sync"))
            check(vector_id, not allowed and item["expected_error"] == "WDR_WRITER_UNAUTHORIZED")

    for item in suite["legacy_adapter_vectors"]:
        vector_id = item["id"]
        if vector_id == "meeting-existing-action-owner-status-patch":
            source = item["input"]["item"]
            actual = {"operation": "patch", "action_id": source["action_id"], "set": {"owner": source["owner"], "status": source["status"], "action": source["text"]}, "observed_at": canonical_timestamp(item["input"]["meeting"]["started_at"])}
            check(vector_id, actual == item["expected"])
        elif vector_id == "status-alias-precedence-presence":
            source = item["input"]["action"]
            actual = {"operation": "patch", "action_id": source["action_id"], "set": {"due_trigger": source["due_or_trigger"], "owner": source["owner"]}}
            check(vector_id, actual == item["expected"] and all(key not in actual["set"] for key in item["forbidden_output_fields"]))
        elif vector_id == "status-missing-observed-at":
            check(vector_id, "observed_at" not in item["input"] and item["expected_error"] == "LEGACY_EVIDENCE_TIMESTAMP_REQUIRED")
        elif vector_id == "meeting-offset-fraction-normalization":
            check(vector_id, canonical_timestamp(item["input"]) == item["expected"])
        elif vector_id == "program-action-routing-scope":
            actual = {"routing_scope_id": item["input"]["workstream"], "affected_workstreams": sorted(set(item["input"]["affected_workstreams"]))}
            check(vector_id, actual == item["expected"])
        else:
            actual = "ACT-" + hashlib.sha256(canonical_bytes(item["identity_input"])).hexdigest()[:20].upper()
            check(vector_id, actual == item["expected"])

    profiles = {profile["projection"]: profile for profile in registry["projection_input_profiles"]}
    for item in suite["projection_vectors"]:
        vector_id = item["id"]
        if vector_id == "registry-refresh-output-not-a-leaf":
            declared: set[str] = set()
            for profile in profiles.values():
                for source in profile["required_sources"]:
                    enum = source["enumerator"]
                    if enum["id"] == "exact-path-v1":
                        declared.add(enum["path"])
            check(vector_id, sorted(declared & set(item["publication_paths"])) == item["expected_intersection"])
        elif vector_id == "meeting-pack-object-by-key":
            actual = {row["scenario"]: row for row in sorted(item["inputs"], key=lambda row: row["scenario"].encode())}
            check(vector_id, actual == item["expected"])
        elif vector_id == "meeting-pack-duplicate-key":
            keys = [row["scenario"] for row in item["inputs"]]
            check(vector_id, len(keys) != len(set(keys)) and item["expected_error"] == "PANEL_BINDING_COLLISION")
        elif vector_id == "complete-generation-envelope":
            body = dict(item["input_without_identity"])
            body["roots"] = sorted(body["roots"], key=lambda row: row["root"].encode())
            body["leaf_sources"] = sorted(body["leaf_sources"], key=lambda row: (row["root_instance_id"], row["path"], row["category"], row["source_kind"]))
            body[item["identity_field"]] = sha256_bytes(canonical_bytes(body))
            check(vector_id, validate(body, schema, "generationEnvelopeV1"))
        elif vector_id == "identity-array-permutation-stable":
            check(vector_id, sorted(set(item["left"]), key=lambda value: value.encode()) == sorted(set(item["right"]), key=lambda value: value.encode()) == item["expected_canonical"])
        elif vector_id == "rfc6901-root-pointer":
            check(vector_id, item["document"] == item["expected_root"] and item["document"][""] == item["expected_empty_member"] and item["root_pointer"] == "" and item["empty_member_pointer"] == "/")
        elif vector_id.startswith("registry-dag-"):
            valid = registry_dag_semantics(registry, item.get("mutation", "none"))
            check(vector_id, valid if item.get("expected") else (not valid and item["expected_error"] == "DAG_INVALIDATION_INCOMPLETE"))
        elif vector_id == "dependency-enumerator-temp-tree-valid":
            check(vector_id, enumerator_temp_tree_semantics())
        elif vector_id.startswith("physical-workstream-inventory-"):
            valid = physical_workstream_inventory_temp_tree_semantics(
                item["mutation"], schema, registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"]
            )
            check(vector_id, valid if item.get("expected") else (not valid and item["expected_error"] == "PHYSICAL_WORKSTREAM_INVENTORY_INVALID"))
        elif vector_id == "optional-snapshot-null-enumerates-empty":
            source = {"enumerator": {"id": "selected-immutable-snapshot-v1", "base": "snapshots/program-status"}}
            check(vector_id, _enumerated_paths(source, ["l1-checkout"], {"previous_program_status_id": None}) == [])
        elif vector_id == "physical-leaf-metadata-conflict-rejected":
            left = ("root", "same/path")
            records = {left: {"category": "fact", "source_kind": "one"}}
            conflict = left in records and records[left] != {"category": "fact", "source_kind": "two"}
            check(vector_id, conflict and item["expected_error"] == "DEPENDENCY_IDENTITY_CONFLICT")
        elif vector_id.startswith("identity-set-fields-"):
            valid = identity_set_semantics(registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], item["mutation"])
            check(vector_id, valid if item.get("expected") else (not valid and item["expected_error"] == "CANONICAL_ORDER_INVALID"))
        elif vector_id.startswith("profile-read-set-"):
            traces = [instrumented_read_trace(profile, ["l1-checkout"], item["mutation"]) for profile in profiles.values()]
            exact = all(allowed == actual for allowed, actual in traces)
            if item["mutation"] == "drop-one-declared-read":
                exact = exact or not all(len(actual) + 1 == len(allowed) and all(row in allowed for row in actual) for allowed, actual in traces)
            elif item["mutation"] == "add-undeclared-read":
                exact = exact or not all(len(actual) == len(allowed) + 1 and any(row not in allowed for row in actual) for allowed, actual in traces)
            check(vector_id, exact if item.get("expected") else (not exact and item["expected_error"] in {"DECLARED_DEPENDENCY_UNCONSUMED", "UNDECLARED_DEPENDENCY"}))
        elif vector_id.startswith("all-ordering-rules-"):
            valid = all_ordering_rules_semantics(
                registry, schema, suite, project_root, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], item["mutation"],
            )
            check(vector_id, valid if item.get("expected") else (not valid and item["expected_error"] == "CANONICAL_ORDER_INVALID"))
        else:
            required_ok = all(set(kinds) <= {source["source_kind"] for source in profiles[name]["required_sources"]} for name, kinds in item["required"].items())
            panel_kinds = {source["source_kind"] for source in profiles["management-panel"]["required_sources"]}
            check(vector_id, required_ok and not (panel_kinds & set(item["panel_forbidden_live_source_kinds"])))

    for item in suite["semantic_validator_vectors"]:
        candidate = copy.deepcopy(registry)
        if item["mutation"] == "omit":
            candidate["semantic_validators"].pop()
        elif item["mutation"] == "add":
            candidate["semantic_validators"].append({"id": "unknown/1.0.0", "scope": ["x"], "algorithm": "unknown"})
        elif item["mutation"] == "algorithm":
            candidate["semantic_validators"][0]["algorithm"] = "changed"
        elif item["mutation"] == "scope":
            candidate["semantic_validators"][0]["scope"] = ["unrelated/9.9.9"]
        omitted_handler = "fact-receipt-attribution/1.0.0" if item["mutation"] == "handler-omission" else None
        valid, executed = semantic_validator_dispatch(
            candidate, schema, suite, project_root, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], omitted_handler,
        )
        registered_ids = {row["id"] for row in candidate.get("semantic_validators", [])}
        if item.get("expected"):
            check(item["id"], valid and executed == registered_ids)
        else:
            check(item["id"], not valid and item["expected_error"] in {"SEMANTIC_VALIDATOR_REGISTRY_INVALID", "SEMANTIC_VALIDATOR_DISPATCH_INCOMPLETE"})

    for item in suite["runtime_vectors"]:
        vector_id = item["id"]
        if vector_id == "bootstrap-generation-zero":
            check(vector_id, item["fact_state_without_id"]["fact_generation"] == 0 and item["panel_state_without_id"]["panel_generation"] == 0)
        elif vector_id == "filesystem-token-hash-id":
            check(vector_id, "h_" + item["input"].split(":", 1)[1] == item["expected"])
        elif vector_id == "filesystem-token-command-id":
            check(vector_id, "i_" + hashlib.sha256(item["input"].encode()).hexdigest() == item["expected"])
        elif "template" in item:
            try:
                actual = runtime_path(
                    registry, item["template"], generation_id=item["generation_id"],
                    projection_kind=item["projection_kind"], instance_key=item["instance_key"],
                )
                check(vector_id, "expected" in item and actual == item["expected"])
            except ValueError:
                check(vector_id, item.get("expected_error") == "RUNTIME_PATH_INVALID")
        else:
            check(vector_id, ":" in item["input"] and item["expected_error"] == "DEPENDENCY_PATH_UNSAFE")

    for item in suite["mutation_semantics_vectors"]:
        mutation = item["mutation"]
        evidence = {
            "source_path": "meetings/edge.md", "source_fingerprint": "sha256:" + "a" * 64,
            "observed_at": "2026-07-24T02:00:00Z",
        }
        create = {
            "command_id": "cmd-edge-create", "operation": "create", "action_id": "A-EDGE-1",
            "create": {
                "owner": "FDE-C", "status": "open", "action": "Verify edge behavior", "due_trigger": "next sync",
                "closure_criteria": "evidence linked", "routing_scope_id": "l1-checkout", "affected_workstreams": [],
            },
            "evidence": [evidence],
        }
        row = action_row_from_create(create)
        try:
            if mutation == "action-stale-evidence":
                apply_action_command([row], {
                    "command_id": "cmd-edge-patch", "operation": "patch", "action_id": "A-EDGE-1", "expected_revision": 1,
                    "set": {"owner": "FDE-D"}, "evidence": [{**evidence, "observed_at": "2026-07-24T01:59:59Z"}],
                })
                valid = False
            elif mutation == "action-created-after-updated":
                candidate = copy.deepcopy(row); candidate["created_at"] = "2026-07-24T02:00:01Z"
                valid = not action_row_chronology_valid(candidate)
            elif mutation == "action-lifecycle-inversion":
                candidate = copy.deepcopy(row)
                candidate.update({
                    "status": "done", "started_at": "2026-07-24T02:03:00Z", "done_at": "2026-07-24T02:02:00Z",
                    "last_updated": "2026-07-24T02:04:00Z",
                })
                valid = not action_row_chronology_valid(candidate)
            elif mutation == "wdr-stale-evidence":
                apply_wdr_patch(fixture_wdr("l1-checkout"), {
                    "set": {"progress": "Stale progress"},
                    "evidence": [{**evidence, "observed_at": "2026-07-24T00:59:59Z"}],
                })
                valid = False
            elif mutation == "wdr-noop-counter":
                before = fixture_wdr("l1-checkout")
                after = apply_wdr_patch(before, {"set": {"progress": "Initial progress"}})
                valid = wdr_counter_delta(before, after, "l1-checkout") == (0, 0)
            elif mutation == "wdr-current-counter":
                before = fixture_wdr("l1-checkout")
                after = apply_wdr_patch(before, {"set": {"progress": "Changed progress"}})
                valid = wdr_counter_delta(before, after, "l1-checkout") == (1, 1)
            elif mutation == "wdr-history-counter":
                before = fixture_wdr("l1-checkout")
                after = apply_wdr_patch(before, {"set": {"meeting_history_append": [{
                    "entry_id": "M-EDGE-1", "command_id": "cmd-edge-history", "observed_at": "2026-07-24T02:00:00Z",
                    "source_path": "meetings/edge.md", "source_fingerprint": "sha256:" + "a" * 64,
                    "classification": "wdr_update", "summary": "Reviewed", "owner": "FDE-C", "due_trigger": "next sync", "status": "noted",
                }]}})
                valid = wdr_counter_delta(before, after, "l1-checkout") == (0, 1)
            elif mutation == "manual-managed-next-actions":
                summary = rendered_action_summary(row)
                before = fixture_wdr("l1-checkout")
                after = apply_wdr_patch(before, {"set": {"refresh_actions": True}}, [summary])
                current = wdr_current_signature(after, "l1-checkout")
                manual, managed = partition_next_actions(current["next_actions"])
                valid = manual == ["review"] and managed == [summary] and wdr_counter_delta(before, after, "l1-checkout") == (1, 1)
            elif mutation == "malformed-managed-marker":
                partition_next_actions(["[action_id:A-EDGE-1] malformed"])
                valid = False
            elif mutation == "duplicate-managed-marker":
                summary = rendered_action_summary(row)
                partition_next_actions([summary, summary])
                valid = False
            elif mutation == "literal-tbd":
                valid = _render_wdr_list(["TBD"]) == "\\TBD" and _parse_wdr_list("\\TBD") == ["TBD"] and _parse_wdr_list("TBD") == []
            else:
                encoded_row = {
                    "action_id": "A-ENC-1", "owner": "FDE:甲", "action": "A (due: x); 付款/100%", "due_trigger": "gate)二",
                }
                expected = "[action_id:A-ENC-1] FDE%3A%E7%94%B2: A %28due%3A x%29%3B %E4%BB%98%E6%AC%BE%2F100%25 (due: gate%29%E4%BA%8C)"
                rendered = rendered_action_summary(encoded_row)
                valid = rendered == expected and parse_managed_action_summary(rendered) == encoded_row
        except ValueError:
            expected_error = "ACTION_MUTATION_INVALID" if mutation.startswith("action-") else "WDR_MUTATION_INVALID"
            valid = item.get("expected_error") == expected_error
        check(item["id"], valid)

    for item in suite["bootstrap_migration_vectors"]:
        vector_id = item["id"]
        if item["mutation"] == "legacy-meeting-history":
            before = legacy_wdr_fixture("l1-checkout").decode("utf-8").replace(
                "## Record Rule\n",
                "<!-- adp-meeting-sync:2026-07-23 -->\n## Meeting Sync Update: 2026-07-23\n\n- Update: preserved legacy body\n\n## Record Rule\n",
            )
            actual = migrate_wdr(before, "2026-07-24T02:00:00Z")
            valid = (
                "## Meeting Sync History\n\n<!-- adp-meeting-sync:2026-07-23 -->\n### Meeting Sync Update: 2026-07-23" in actual
                and "- Update: preserved legacy body" in actual
                and actual.index("## Meeting Sync History") < actual.index("## Record Rule")
                and complete_wdr_valid(actual, "l1-checkout")
            )
        elif item["mutation"] == "mixed-meeting-history":
            before = legacy_wdr_fixture("l1-checkout").decode("utf-8").replace(
                "## Record Rule\n", "## Meeting Sync History\n\ncanonical\n\n## Meeting Sync Update: 2026-07-23\n\nlegacy\n\n## Record Rule\n",
            )
            try:
                migrate_wdr(before, "2026-07-24T02:00:00Z")
                valid = True
            except ValueError:
                valid = False
        else:
            scenario = {
                "ledger_format": item["ledger_format"], "action_flow_preimage": item["action_flow_preimage"],
                "workstreams": ["l1-checkout"],
            }
            graph = bootstrap_migration_fixture(scenario, registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
            mutation = item["mutation"]
            if mutation == "malformed-ledger":
                malformed = b"# Action Ledger\n\nmalformed\n"
                artifact = graph["proof"]["business_artifacts"][0]
                artifact["before_bytes"] = encoded_bytes(malformed)
                target = [row for row in graph["journal"]["targets"] if row["role"] == "business"][0]
                target["before_sha256"] = sha256_bytes(malformed); target["before_image"]["sha256"] = target["before_sha256"]
                graph["receipt"]["business_targets"][0] = copy.deepcopy(target)
                graph["command"]["action_ledger"]["expected_fingerprint"] = sha256_bytes(malformed)
                rebind_fact_graph(graph)
            elif mutation == "ledger-cas":
                graph["command"]["action_ledger"]["expected_fingerprint"] = "sha256:" + "f" * 64
                rebind_fact_graph(graph)
            elif mutation == "wdr-cas":
                graph["command"]["workstreams"][0]["expected_record_fingerprint"] = "sha256:" + "f" * 64
                rebind_fact_graph(graph)
            elif mutation == "action-flow-shape":
                replacement = canonical_bytes({"incompatible": True})
                artifact = graph["proof"]["business_artifacts"][2]
                artifact["after_bytes"] = encoded_bytes(replacement)
                target = [row for row in graph["journal"]["targets"] if row["role"] == "business"][2]
                target["after_sha256"] = sha256_bytes(replacement); target["after_image"]["sha256"] = target["after_sha256"]
                graph["receipt"]["business_targets"][2] = copy.deepcopy(target)
                rebind_fact_graph(graph)
            elif mutation == "missing-state-target":
                business = [row for row in graph["journal"]["targets"] if row["role"] == "business"]
                graph["journal"]["targets"].remove(business[1])
                graph["proof"]["business_artifacts"].pop(1)
                _reindex_targets(graph["journal"]["targets"], graph["journal"]["journal_dir"])
                graph["receipt"]["business_targets"] = copy.deepcopy([row for row in graph["journal"]["targets"] if row["role"] == "business"])
                graph["receipt"]["generation_state_target"] = copy.deepcopy(next(row for row in graph["journal"]["targets"] if row["role"] == "fact-generation"))
                rebind_fact_graph(graph)
            elif mutation == "repeat-write":
                target = [row for row in graph["journal"]["targets"] if row["role"] == "business"][1]
                artifact = graph["proof"]["business_artifacts"][1]
                prior = artifact_bytes(artifact["after_bytes"])
                target["operation"] = "replace"; target["before_sha256"] = sha256_bytes(prior)
                target["before_image"] = {"root_instance_id": target["root_instance_id"], "path": f"{graph['journal']['journal_dir']}/images/{target['apply_order']}-before", "sha256": target["before_sha256"]}
                artifact["operation"] = "replace"; artifact["before_bytes"] = encoded_bytes(prior)
                graph["receipt"]["business_targets"][1] = copy.deepcopy(target)
                rebind_fact_graph(graph)
            if mutation == "preservation":
                before_rows = parse_action_ledger_ingress(artifact_bytes(graph["proof"]["business_artifacts"][0]["before_bytes"]), "legacy20")
                after_rows = parse_action_ledger(artifact_bytes(graph["proof"]["business_artifacts"][0]["after_bytes"]))
                preserved = all(before_rows[0][field] == after_rows[0][field] for field in ACTION_LEDGER_FIELDS[:-1]) and after_rows[0]["action_revision"] == 1
                flow = json.loads(artifact_bytes(graph["proof"]["business_artifacts"][2]["after_bytes"]))
                valid = preserved and validate(flow, schema, "actionFlowIndexV1") and flow["actions"][0]["related_plan_item_ids"] == ["PLAN-1"] and flow["actions"][0]["related_flow_edge_ids"] == ["EDGE-1"]
            elif mutation == "crash-matrix":
                targets = graph["journal"]["targets"]
                valid = bootstrap_migration_semantics(graph, registry, schema, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"]) and all(
                    row["after_image"] is not None and row["after_image"]["sha256"] == row["after_sha256"]
                    and (row["operation"] != "create" or row["before_image"] is None)
                    for row in targets
                )
            elif mutation == "idempotent-retry":
                again = bootstrap_migration_fixture(scenario, registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
                valid = (
                    bootstrap_migration_semantics(graph, registry, schema, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
                    and bootstrap_migration_semantics(again, registry, schema, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
                    and canonical_bytes(graph["receipt"]) == canonical_bytes(again["receipt"])
                )
            else:
                valid = bootstrap_migration_semantics(graph, registry, schema, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
        check(vector_id, valid if item.get("expected") == "valid" else (not valid and item["expected_error"] == "BOOTSTRAP_MIGRATION_INVALID"))

    def rebind_identity(document: dict[str, Any], identity_field: str) -> None:
        document[identity_field] = sha256_bytes(canonical_bytes({key: value for key, value in document.items() if key != identity_field}))

    def rebind_attestation_activation(package: dict[str, Any]) -> None:
        activation = package["documents"]["activation_state"]
        package["attestation"]["activation_state_binding_id"] = sha256_bytes(canonical_bytes({
            key: value for key, value in activation.items() if key not in {"attestation_id", "state_id"}
        }))
        rebind_writer_fence_attestation(package)
        if activation["mode"] == "strict":
            activation["attestation_id"] = package["attestation"]["attestation_id"]
        rebind_identity(activation, "state_id")

    expected_ids = sorted({
        row["id"] for key, values in suite.items() if key.endswith("_vectors") or key == "journal_fault_matrix" for row in values
    })
    strict_registry = design_release_registry_fixture(registry)
    strict_registry_sha = sha256_bytes(canonical_bytes(strict_registry))
    strict_hashes = {**actual_hashes, "registry": strict_registry_sha}
    strict_suite = replace_tokens(suite, {substitutions["$REGISTRY_SHA256"]: strict_registry_sha})
    for item in suite["strict_activation_vectors"]:
        activation_epoch = 2 if item["mutation"] == "reenable" else 1
        vector_registry = registry if item["mutation"] == "pending-status" else strict_registry
        vector_registry_sha = substitutions["$REGISTRY_SHA256"] if item["mutation"] == "pending-status" else strict_registry_sha
        vector_hashes = actual_hashes if item["mutation"] == "pending-status" else strict_hashes
        vector_suite = suite if item["mutation"] == "pending-status" else strict_suite
        if item["mutation"] == "activation-algorithm":
            vector_registry = copy.deepcopy(strict_registry)
            vector_registry["strict_rollout"]["activation_algorithm"] = "mutable-snapshots-exact-match"
            vector_registry_sha = sha256_bytes(canonical_bytes(vector_registry))
            vector_hashes = {**strict_hashes, "registry": vector_registry_sha}
            vector_suite = replace_tokens(strict_suite, {strict_registry_sha: vector_registry_sha})
        package = writer_fence_fixture(
            vector_registry, substitutions["$SCHEMA_SHA256"], vector_registry_sha, expected_ids, vector_hashes, activation_epoch,
            suite=vector_suite, schema=schema, project_root=project_root,
        )
        package["surface"] = item.get("surface", "publish")
        mutation = item["mutation"]
        docs = package["documents"]
        if mutation == "pending-status": pass
        elif mutation == "missing-attestation": del package["attestation"]
        elif mutation == "attestation-id": package["attestation"]["attestation_id"] = "sha256:" + "f" * 64
        elif mutation == "attestation-path": package["attestation_path"] = "state/other-attestation.json"
        elif mutation == "root-registry-id": docs["root_registry"]["registry_state_id"] = "sha256:" + "f" * 64
        elif mutation == "root-rebound":
            next(row for row in docs["root_registry"]["roots"] if row["role"] == "memory")["root_instance_id"] = "123e4567-e89b-42d3-a456-426614174099"
            rebind_identity(docs["root_registry"], "registry_state_id")
        elif mutation == "capability-registry-id": docs["capability_registry"]["capability_registry_id"] = "sha256:" + "f" * 64
        elif mutation == "capability-epoch":
            docs["capability_registry"]["capability_epoch"] += 1; rebind_identity(docs["capability_registry"], "capability_registry_id")
        elif mutation == "writer-subset":
            del package["writer_store"][registry["strict_rollout"]["writer_specs"][0]["artifact_paths"][0]]
        elif mutation == "writer-build":
            artifact_path = registry["strict_rollout"]["writer_specs"][0]["artifact_paths"][0]
            package["writer_store"][artifact_path] += b"\nchanged"
        elif mutation == "fence-receipt":
            receipt_path = registry["strict_rollout"]["writer_specs"][0]["receipt_path"]
            package["writer_store"][receipt_path] = b"{}"
        elif mutation == "writer-receipt-missing":
            del package["writer_store"][registry["strict_rollout"]["writer_specs"][0]["receipt_path"]]
        elif mutation == "writer-receipt-aliased":
            first, second = registry["strict_rollout"]["writer_specs"][:2]
            package["writer_store"][second["receipt_path"]] = package["writer_store"][first["receipt_path"]]
        elif mutation == "writer-receipt-stale":
            receipt_path = registry["strict_rollout"]["writer_specs"][0]["receipt_path"]
            receipt = json.loads(package["writer_store"][receipt_path])
            receipt["capability_epoch"] = max(0, receipt["capability_epoch"] - 1)
            rebind_identity(receipt, "receipt_id")
            package["writer_store"][receipt_path] = canonical_bytes(receipt)
        elif mutation == "capability-missing":
            docs["capability_registry"]["capabilities"].pop()
            rebind_identity(docs["capability_registry"], "capability_registry_id")
        elif mutation == "capability-revoked":
            capability = docs["capability_registry"]["capabilities"][0]
            capability["status"] = "revoked"
            capability["capability_id"] = capability["authorization_record_digest"] = capability_record_digest(capability)
            rebind_identity(docs["capability_registry"], "capability_registry_id")
        elif mutation == "capability-wrong-scope":
            capability = docs["capability_registry"]["capabilities"][0]
            capability["allowed_fields"] = []
            capability["capability_id"] = capability["authorization_record_digest"] = capability_record_digest(capability)
            rebind_identity(docs["capability_registry"], "capability_registry_id")
        elif mutation == "capability-stale-epoch":
            docs["capability_registry"]["capability_epoch"] += 1
            rebind_identity(docs["capability_registry"], "capability_registry_id")
        elif mutation == "capability-lifecycle-attempt":
            package["capability_lifecycle_operation"] = "rotate"
        elif mutation == "release-set-missing":
            del package["release_store"][vector_registry["runtime_paths"]["release_evidence_set"]["path"]]
        elif mutation == "release-unindexed-receipt":
            package["release_store"][runtime_path(
                vector_registry, "release_evidence_receipt_template", result_id="sha256:" + "f" * 64,
            )] = b"{}"
        elif mutation == "release-blob-missing":
            blob_path = docs["release_evidence_set"]["entries"][0]["evidence_blobs"][0]["path"]
            del package["release_store"][blob_path]
        elif mutation == "release-receipt-path-substitution":
            release_set = docs["release_evidence_set"]
            entry = release_set["entries"][0]
            old_path = entry["receipt_path"]
            entry["receipt_path"] = "receipts/conformance/substituted.json"
            package["release_store"][entry["receipt_path"]] = package["release_store"].pop(old_path)
            rebind_identity(release_set, "release_evidence_set_id")
            set_path = vector_registry["runtime_paths"]["release_evidence_set"]["path"]
            package["release_store"][set_path] = canonical_bytes(release_set)
            package["attestation"]["release_evidence_set_id"] = release_set["release_evidence_set_id"]
            rebind_attestation_activation(package)
        elif mutation == "registry-raw-substitution":
            package["registry_raw"] += b"\n"
        elif mutation == "lineage-index-missing":
            del package["lineage_store"][package["attestation"]["lineage_index_path"]]
        elif mutation == "lineage-object-missing":
            row = package["attestation"]["lineage_index_path"]
            index = json.loads(package["lineage_store"][row])
            del package["lineage_store"][index["objects"][0]["path"]]
        elif mutation == "lineage-object-extra":
            package["lineage_store"]["views/generations/unexpected.json"] = b"{}"
        elif mutation == "lineage-indexed-extra":
            index_path = package["attestation"]["lineage_index_path"]
            index = json.loads(package["lineage_store"][index_path])
            source = next(row for row in index["objects"] if row["object_kind"] == "projection-envelope" and row["projection_kind"] == "state-audit")
            document = json.loads(package["lineage_store"][source["path"]])
            document["instance_key"] = "unexpected"
            rebind_identity(document, "projection_id")
            object_path = runtime_path(
                vector_registry, "canonical_projection_template", generation_id=index["generation_id"],
                projection_kind="state-audit", instance_key="unexpected",
            )
            raw = canonical_bytes(document)
            package["lineage_store"][object_path] = raw
            index["objects"].append({
                **source, "instance_key": "unexpected", "object_id": document["projection_id"],
                "path": object_path, "sha256": sha256_bytes(raw),
            })
            index["objects"].sort(key=lambda row: (row["object_kind"].encode("utf-8"), (row["projection_kind"] or "").encode("utf-8"), (row["instance_key"] or "").encode("utf-8")))
            rebind_identity(index, "index_id")
            package["lineage_store"][index_path] = canonical_bytes(index)
            package["attestation"]["lineage_index_id"] = index["index_id"]
            rebind_attestation_activation(package)
        elif mutation == "lineage-object-redirected":
            index_path = package["attestation"]["lineage_index_path"]
            index = json.loads(package["lineage_store"][index_path])
            target = next(row for row in index["objects"] if row["object_kind"] == "selection-policy")
            old_path = target["path"]
            target["path"] = "views/generations/redirected-selection-policy.json"
            package["lineage_store"][target["path"]] = package["lineage_store"].pop(old_path)
            rebind_identity(index, "index_id")
            package["lineage_store"][index_path] = canonical_bytes(index)
            package["attestation"]["lineage_index_id"] = index["index_id"]
            rebind_attestation_activation(package)
        elif mutation == "lineage-singleton-metadata":
            index_path = package["attestation"]["lineage_index_path"]
            index = json.loads(package["lineage_store"][index_path])
            target = next(row for row in index["objects"] if row["object_kind"] == "selection-policy")
            target["projection_kind"] = "program-status"
            index["objects"].sort(key=lambda row: (row["object_kind"].encode("utf-8"), (row["projection_kind"] or "").encode("utf-8"), (row["instance_key"] or "").encode("utf-8")))
            rebind_identity(index, "index_id")
            package["lineage_store"][index_path] = canonical_bytes(index)
            package["attestation"]["lineage_index_id"] = index["index_id"]
            rebind_attestation_activation(package)
        elif mutation in {"lineage-object-tampered", "panel-byte-tampered", "current-pointer-raw-tampered"}:
            index = json.loads(package["lineage_store"][package["attestation"]["lineage_index_path"]])
            if mutation == "current-pointer-raw-tampered":
                object_path = registry["runtime_paths"]["panel_current_pointer"]["path"]
            else:
                candidates = [row for row in index["objects"] if row["object_kind"] == "projection-envelope"]
                target = next(row for row in candidates if row["projection_kind"] == "management-panel") if mutation == "panel-byte-tampered" else candidates[0]
                object_path = target["path"]
            package["lineage_store"][object_path] += b"\n"
        elif mutation == "lineage-leaf-stale":
            leaf_key = next(iter(package["live_leaf_store"]))
            package["live_leaf_store"][leaf_key] += b"\n"
        elif mutation == "fact-generation":
            docs["fact_state"]["fact_generation"] += 1; rebind_identity(docs["fact_state"], "state_id")
        elif mutation == "ledger-bytes": docs["ledger_raw"] += b"\n"
        elif mutation == "ledger-state":
            docs["ledger_state"]["ledger_revision"] += 1; rebind_identity(docs["ledger_state"], "state_id")
        elif mutation == "action-flow": docs["action_flow"]["compatibility"]["migration_error_code"] = "CHANGED"
        elif mutation == "wdr-bytes": docs["workstreams"][0]["wdr_raw"] = docs["workstreams"][0]["wdr_raw"].replace(b"Initial progress", b"Changed progress")
        elif mutation == "wdr-state": docs["workstreams"][0]["state"]["wdr_revision"] += 1
        elif mutation == "sidecar": docs["workstreams"][0]["sidecar"]["renderer_sha256"] = "sha256:" + "f" * 64
        elif mutation == "workstream-omitted": docs["workstreams"].pop()
        elif mutation == "refresh-receipt-id": docs["refresh_receipt"]["receipt_id"] = "sha256:" + "f" * 64
        elif mutation == "refresh-status":
            docs["refresh_receipt"]["status"] = "dirty"; rebind_identity(docs["refresh_receipt"], "receipt_id")
            package["attestation"]["full_refresh_receipt_id"] = docs["refresh_receipt"]["receipt_id"]; rebind_attestation_activation(package)
        elif mutation == "diagnostic-fact-snapshot":
            package["attestation"]["fact_generation"] += 100; rebind_attestation_activation(package)
        elif mutation == "diagnostic-ledger-snapshot":
            package["attestation"]["ledger"]["ledger_fingerprint"] = "sha256:" + "f" * 64; rebind_attestation_activation(package)
        elif mutation == "diagnostic-wdr-snapshot":
            package["attestation"]["workstreams"][0]["wdr_fingerprint"] = "sha256:" + "f" * 64; rebind_attestation_activation(package)
        elif mutation == "diagnostic-sidecar-snapshot":
            package["attestation"]["workstreams"][0]["sidecar_fingerprint"] = "sha256:" + "f" * 64; rebind_attestation_activation(package)
        elif mutation == "diagnostic-refresh-snapshot":
            package["attestation"]["full_refresh_receipt_id"] = "sha256:" + "f" * 64; rebind_attestation_activation(package)
        elif mutation == "diagnostic-publication-snapshot":
            package["attestation"]["published_generation_id"] = "sha256:" + "f" * 64; rebind_attestation_activation(package)
        elif mutation == "diagnostic-pointer-snapshot":
            package["attestation"]["current_pointer_id"] = "sha256:" + "f" * 64; rebind_attestation_activation(package)
        elif mutation == "refresh-fact-generation":
            docs["refresh_receipt"]["expected_fact_generation"] += 1; rebind_identity(docs["refresh_receipt"], "receipt_id")
            refresh_path = runtime_path(registry, "refresh_receipt_generation_template", generation_id=package["attestation"]["published_generation_id"])
            package["lineage_store"][refresh_path] += b"\n"
        elif mutation == "refresh-panel-generation":
            docs["refresh_receipt"]["expected_panel_generation"] += 1; rebind_identity(docs["refresh_receipt"], "receipt_id")
            refresh_path = runtime_path(registry, "refresh_receipt_generation_template", generation_id=package["attestation"]["published_generation_id"])
            package["lineage_store"][refresh_path] += b"\n"
        elif mutation == "refresh-generation":
            docs["refresh_receipt"]["generation_id"] = "sha256:" + "f" * 64; rebind_identity(docs["refresh_receipt"], "receipt_id")
            refresh_path = runtime_path(registry, "refresh_receipt_generation_template", generation_id=package["attestation"]["published_generation_id"])
            package["lineage_store"][refresh_path] += b"\n"
        elif mutation == "source-as-of-refresh-mismatch":
            refresh = docs["refresh_receipt"]
            refresh["source_as_of"] = "2026-07-24T02:00:01Z"
            rebind_identity(refresh, "receipt_id")
            refresh_path = runtime_path(vector_registry, "refresh_receipt_generation_template", generation_id=package["attestation"]["published_generation_id"])
            refresh_raw = canonical_bytes(refresh)
            package["lineage_store"][refresh_path] = refresh_raw
            index_path = package["attestation"]["lineage_index_path"]
            index = json.loads(package["lineage_store"][index_path])
            row = next(row for row in index["objects"] if row["object_kind"] == "refresh-receipt")
            row["object_id"] = refresh["receipt_id"]
            row["sha256"] = sha256_bytes(refresh_raw)
            rebind_identity(index, "index_id")
            package["lineage_store"][index_path] = canonical_bytes(index)
            package["attestation"]["full_refresh_receipt_id"] = refresh["receipt_id"]
            package["attestation"]["lineage_index_id"] = index["index_id"]
            rebind_attestation_activation(package)
        elif mutation == "publication-receipt-id": docs["publication_receipt"]["receipt_id"] = "sha256:" + "f" * 64
        elif mutation == "publication-generation":
            docs["publication_receipt"]["generation_id"] = "sha256:" + "f" * 64; rebind_identity(docs["publication_receipt"], "receipt_id")
            package["attestation"]["panel_publication_receipt_id"] = docs["publication_receipt"]["receipt_id"]; rebind_attestation_activation(package)
        elif mutation == "pointer-id": docs["current_pointer"]["pointer_id"] = "sha256:" + "f" * 64
        elif mutation == "pointer-path":
            docs["current_pointer"]["projections"][0]["canonical_path"] = "views/generations/wrong.json"; rebind_identity(docs["current_pointer"], "pointer_id")
            package["lineage_store"][registry["runtime_paths"]["panel_current_pointer"]["path"]] += b"\n"
        elif mutation == "panel-state":
            docs["panel_state"]["panel_generation"] += 1; rebind_identity(docs["panel_state"], "state_id")
            package["lineage_store"][registry["runtime_paths"]["panel_state"]["path"]] += b"\n"
        elif mutation == "activation-mode":
            docs["activation_state"]["mode"] = "legacy"; docs["activation_state"]["attestation_id"] = None; rebind_identity(docs["activation_state"], "state_id")
        elif mutation == "activation-epoch":
            docs["activation_state"]["activation_epoch"] += 1; rebind_identity(docs["activation_state"], "state_id")
        elif mutation == "activation-attestation":
            docs["activation_state"]["attestation_id"] = "sha256:" + "f" * 64; rebind_identity(docs["activation_state"], "state_id")
        elif mutation == "activation-binding":
            docs["activation_state"]["changed_at"] = "2026-07-24T03:00:04Z"; rebind_identity(docs["activation_state"], "state_id")
        elif mutation == "stale-attestation":
            package["attestation"]["attested_at"] = "2026-07-24T02:59:59Z"; rebind_attestation_activation(package)
        elif mutation == "rollback":
            docs["activation_state"]["activation_epoch"] += 1; docs["activation_state"]["mode"] = "legacy"; docs["activation_state"]["attestation_id"] = None; rebind_identity(docs["activation_state"], "state_id")
        elif mutation == "manual-flip": package["release_store"] = {}
        valid = strict_writer_fence_activation_semantics(
            package, vector_registry, schema, substitutions["$SCHEMA_SHA256"], vector_registry_sha, expected_ids, vector_hashes,
            {"clock_source": "host-secure-clock-v1", "evaluation_time": item.get("evaluation_time", "2026-07-24T03:05:00Z"), "available": item.get("clock_available", True)},
        )
        check(item["id"], valid if item.get("expected") == "valid" else (not valid and item["expected_error"] == "MIGRATION_REQUIRED"))

    release_receipts, release_blobs = implementation_conformance_receipts(expected_ids, strict_hashes, strict_registry)
    release_transition_base = release_evidence_transition_fixture(
        release_receipts, release_blobs, strict_registry,
        substitutions["$SCHEMA_SHA256"], strict_registry_sha,
    )

    def rebind_transition_journal(package: dict[str, Any], receipt_key: str | None = None) -> None:
        if receipt_key is not None:
            receipt = package[receipt_key]
            rebind_identity(receipt, "receipt_id")
            receipt_target = next(row for row in package["journal"]["targets"] if row["role"] == "receipt")
            receipt_target["after_sha256"] = sha256_bytes(canonical_bytes(receipt))
            receipt_target["after_image"]["sha256"] = receipt_target["after_sha256"]
        rebind_identity(package["journal"], "manifest_id")
        package["marker"]["manifest_id"] = package["journal"]["manifest_id"]
        rebind_identity(package["marker"], "marker_id")

    for item in suite["release_transition_vectors"]:
        package = copy.deepcopy(release_transition_base)
        mutation = item["mutation"]
        if mutation == "before-generation":
            package["transition_receipt"]["before_generation"] += 1
            rebind_transition_journal(package, "transition_receipt")
        elif mutation == "before-set":
            package["transition_receipt"]["before_set_id"] = "sha256:" + "f" * 64
            rebind_transition_journal(package, "transition_receipt")
        elif mutation == "after-set":
            package["transition_receipt"]["after_set_id"] = "sha256:" + "f" * 64
            rebind_transition_journal(package, "transition_receipt")
        elif mutation == "journal-id":
            package["transition_receipt"]["journal_id"] = "journal-substituted"
            rebind_transition_journal(package, "transition_receipt")
        elif mutation == "target-path":
            next(row for row in package["journal"]["targets"] if row["role"] == "release-evidence")["path"] = "state/release-evidence/substituted.json"
            rebind_transition_journal(package)
        elif mutation.startswith("history-") and mutation != "history-chronology":
            historical = package["after_history"]["entries"][0]
            if mutation == "history-receipt-tamper":
                tamper_path = historical["transition_receipt_path"]
            elif mutation == "history-journal-tamper":
                tamper_path = historical["journal_path"]
            elif mutation == "history-marker-tamper":
                tamper_path = historical["terminal_marker_path"]
            else:
                historical_set = json.loads(package["final_store"][historical["set_path"]])
                tamper_path = historical_set["entries"][0]["evidence_blobs"][0]["path"]
            package["final_store"][tamper_path] += b"\n"
        elif mutation == "history-chronology":
            package = release_evidence_transition_fixture(
                release_receipts, release_blobs, strict_registry,
                substitutions["$SCHEMA_SHA256"], strict_registry_sha,
                after_accepted_at="2026-07-24T02:59:59Z",
            )
        if mutation in {"recovery-uncommitted", "recovery-committed"}:
            valid = release_evidence_recovery_semantics(
                package, item["crash_after"], mutation == "recovery-committed",
                strict_registry, schema, substitutions["$SCHEMA_SHA256"], strict_registry_sha,
            )
        elif mutation in {"recovery-all-uncommitted", "recovery-all-committed"}:
            valid = all(
                release_evidence_recovery_semantics(
                    package, crash_after, mutation == "recovery-all-committed",
                    strict_registry, schema, substitutions["$SCHEMA_SHA256"], strict_registry_sha,
                )
                for crash_after in range(len(package["journal"]["targets"]) + 1)
            )
        else:
            security_context = {
                "clock_source": "host-secure-clock-v1",
                "evaluation_time": item.get("evaluation_time", "2026-07-24T03:15:00Z"),
                "available": mutation != "clock-unavailable",
            }
            valid = release_evidence_transition_semantics(
                package, strict_registry, schema, substitutions["$SCHEMA_SHA256"], strict_registry_sha,
                expected_ids, strict_hashes, security_context,
            )
        check(
            item["id"], valid if item.get("expected") == "valid"
            else (not valid and item["expected_error"] == "RELEASE_TRANSITION_INVALID"),
        )

    transition_writer_package = writer_fence_fixture(
        strict_registry, substitutions["$SCHEMA_SHA256"], strict_registry_sha, expected_ids, strict_hashes,
        suite=strict_suite, schema=schema, project_root=project_root,
    )
    activation_transition_base = activation_transition_fixture(
        transition_writer_package, strict_registry, substitutions["$SCHEMA_SHA256"], strict_registry_sha,
    )
    for item in suite["activation_transition_vectors"]:
        package = copy.deepcopy(activation_transition_base)
        mutation = item["mutation"]
        if mutation == "operation-order":
            package["steps"][0], package["steps"][1] = package["steps"][1], package["steps"][0]
        elif mutation == "activation-cas":
            package["steps"][0]["command"]["expected_activation_epoch"] += 1
        elif mutation == "capability-cas":
            package["steps"][1]["command"]["expected_capability_epoch"] += 1
        elif mutation == "authority":
            package["steps"][0]["command"]["authority_context_id"] = "sha256:" + "f" * 64
        elif mutation == "approval-order":
            package["steps"][0]["command"]["approved_by"].reverse()
        elif mutation == "target-path":
            step = package["steps"][0]
            next(row for row in step["journal"]["targets"] if row["role"] != "receipt")["path"] = "state/substituted-activation.json"
            rebind_transition_journal(step)
        elif mutation == "refresh-binding":
            step = package["steps"][2]
            step["receipt"]["full_refresh_receipt_id"] = "sha256:" + "f" * 64
            rebind_transition_journal(step, "receipt")
        elif mutation == "attestation-binding":
            step = package["steps"][3]
            step["receipt"]["attestation_id"] = "sha256:" + "f" * 64
            rebind_transition_journal(step, "receipt")
        elif mutation == "attestation-preimage-cas":
            package["steps"][3]["command"]["expected_attestation_sha256"] = "sha256:" + "f" * 64
        elif mutation == "predecessor-rebind":
            step = package["steps"][1]
            substituted = "sha256:" + "f" * 64
            step["command"]["predecessor_receipt_id"] = substituted
            step["receipt"]["predecessor_receipt_id"] = substituted
            rebind_transition_journal(step, "receipt")
        elif mutation == "forged-lifecycle-receipt":
            step = package["steps"][1]
            step["after_lifecycle_index"]["entries"][-1]["receipt_id"] = "sha256:" + "f" * 64
            rebind_identity(step["after_lifecycle_index"], "index_id")
            target = next(row for row in step["journal"]["targets"] if row["role"] == "activation-lifecycle-index")
            target["after_sha256"] = sha256_bytes(canonical_bytes(step["after_lifecycle_index"]))
            target["after_image"]["sha256"] = target["after_sha256"]
            rebind_transition_journal(step)
        elif mutation == "broken-lifecycle-prefix":
            step = package["steps"][1]
            step["before_lifecycle_index"]["terminal_status"] = "enabled"
            rebind_identity(step["before_lifecycle_index"], "index_id")
            target = next(row for row in step["journal"]["targets"] if row["role"] == "activation-lifecycle-index")
            target["before_sha256"] = sha256_bytes(canonical_bytes(step["before_lifecycle_index"]))
            target["before_image"]["sha256"] = target["before_sha256"]
            rebind_transition_journal(step)
        elif mutation == "first-lifecycle-replace":
            step = package["steps"][0]
            next(row for row in step["journal"]["targets"] if row["role"] == "activation-lifecycle-index")["operation"] = "replace"
            rebind_transition_journal(step)
        elif mutation == "uncommitted-lifecycle-receipt":
            step = package["steps"][1]
            step["receipt"]["status"] = "rolled-back"
            rebind_transition_journal(step, "receipt")
        elif mutation == "disconnected-chain":
            step = package["steps"][1]
            disconnected_activation = copy.deepcopy(package["steps"][0]["before_activation"])
            step["before_activation"] = disconnected_activation
            step["after_activation"] = copy.deepcopy(disconnected_activation)
            step["authority"] = runtime_authority_from_documents(
                strict_registry, substitutions["$SCHEMA_SHA256"], strict_registry_sha,
                strict_registry["strict_rollout"]["activation_administrator_producer_id"],
                step["before_capability"], package["roots"], disconnected_activation,
                package["initial_attestation"],
            )
            step["command"]["authority_context_id"] = step["authority"][-1]["context_id"]
            step["command"]["expected_activation_epoch"] = disconnected_activation["activation_epoch"]
            step["command"]["expected_activation_state_id"] = disconnected_activation["state_id"]
            step["receipt"]["before_activation_epoch"] = disconnected_activation["activation_epoch"]
            step["receipt"]["after_activation_epoch"] = disconnected_activation["activation_epoch"]
            step["receipt"]["before_activation_state_id"] = disconnected_activation["state_id"]
            step["receipt"]["after_activation_state_id"] = disconnected_activation["state_id"]
            rebind_transition_journal(step, "receipt")
        if mutation in {"recovery-uncommitted", "recovery-committed"}:
            step = package["steps"][item["step"] - 1]
            valid = release_evidence_recovery_semantics(
                step, item["crash_after"], mutation == "recovery-committed",
                strict_registry, schema, substitutions["$SCHEMA_SHA256"], strict_registry_sha,
            )
        elif mutation in {"recovery-all-uncommitted", "recovery-all-committed"}:
            valid = all(
                release_evidence_recovery_semantics(
                    step, crash_after, mutation == "recovery-all-committed",
                    strict_registry, schema, substitutions["$SCHEMA_SHA256"], strict_registry_sha,
                )
                for step in package["steps"]
                for crash_after in range(len(step["journal"]["targets"]) + 1)
            )
        else:
            valid = activation_transition_semantics(
                package, strict_registry, schema, substitutions["$SCHEMA_SHA256"], strict_registry_sha,
            )
        check(
            item["id"], valid if item.get("expected") == "valid"
            else (not valid and item["expected_error"] == "ACTIVATION_TRANSITION_INVALID"),
        )

    for item in suite["source_time_vectors"]:
        panel, upstreams, _, policy, _ = panel_fixture(
            suite["contract_schema_vectors"], registry,
            substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], project_root,
        )
        for binding in registry["panel_binding_map"]:
            payload = upstreams[binding["projection_kind"]]
            value = (
                {row["scenario"]: copy.deepcopy(row) for row in payload}
                if binding["merge_mode"] == "object-by-key" else copy.deepcopy(payload)
            )
            set_pointer(panel, binding["panel_pointer"], value)
        refresh_receipt = {"source_as_of": policy["as_of"]}
        mismatch = "2026-07-24T02:00:01Z"
        mutation = item["mutation"]
        if mutation == "panel":
            panel["sync"]["source_as_of"] = mismatch
        elif mutation == "audit":
            panel["sync"]["audit"]["source_as_of"] = mismatch
        elif mutation == "status":
            panel["sync"]["canonical"]["status"]["source_as_of"] = mismatch
        elif mutation == "roadmap":
            panel["sync"]["canonical"]["roadmap"]["source_as_of"] = mismatch
        elif mutation == "meeting":
            next(iter(panel["sync"]["canonical"]["meetings"].values()))["source_as_of"] = mismatch
        elif mutation == "flow-state":
            panel["sync"]["canonical"]["flow"]["state"]["as_of"] = mismatch
        elif mutation == "flow-scope":
            scopes = panel["sync"]["canonical"]["flow"]["overlays"]["scopes"]
            if scopes:
                scopes[0]["as_of"] = mismatch
            else:
                scopes.append({"as_of": mismatch})
        elif mutation == "refresh":
            refresh_receipt["source_as_of"] = mismatch
        valid = source_as_of_semantics(panel, policy, refresh_receipt, registry=registry)
        check(
            item["id"], valid if item.get("expected") == "valid"
            else (not valid and item["expected_error"] == "SOURCE_AS_OF_MISMATCH"),
        )

    snapshot_lineage = load_strict_lineage(
        transition_writer_package, strict_registry, schema, substitutions["$SCHEMA_SHA256"], strict_registry_sha,
        verify_live_leaves=False,
    )
    for item in suite["snapshot_authority_vectors"]:
        if snapshot_lineage is None:
            check(item["id"], False)
            continue
        panel = copy.deepcopy(snapshot_lineage["graph"]["panel"])
        policy = copy.deepcopy(snapshot_lineage["policy"])
        refresh_receipt = copy.deepcopy(snapshot_lineage["refresh_receipt"])
        vector_registry = copy.deepcopy(strict_registry)
        snapshot = snapshot_time_fixture(
            vector_registry, substitutions["$SCHEMA_SHA256"], strict_registry_sha, policy, refresh_receipt,
        )
        mutation = item["mutation"]
        if mutation == "future-source-time":
            snapshot["evaluation_time"] = "2026-01-01T00:00:00Z"
        elif mutation == "older-than-maximum-fact":
            snapshot["lock_receipt"]["maximum_fact_observed_at"] = "2026-07-24T03:00:01Z"
            rebind_identity(snapshot["lock_receipt"], "receipt_id")
        elif mutation == "request-after-lock":
            snapshot["request"]["requested_at"] = "2026-07-24T03:00:01Z"
            rebind_identity(snapshot["request"], "request_id")
            snapshot["lock_receipt"]["refresh_request_id"] = snapshot["request"]["request_id"]
            rebind_identity(snapshot["lock_receipt"], "receipt_id")
        elif mutation == "request-binding":
            snapshot["lock_receipt"]["refresh_request_id"] = "sha256:" + "f" * 64
            rebind_identity(snapshot["lock_receipt"], "receipt_id")
        elif mutation == "policy-lock-id":
            policy["snapshot_lock_receipt_id"] = "sha256:" + "f" * 64
        elif mutation == "policy-snapshot-id":
            policy["snapshot_id"] = "sha256:" + "f" * 64
        elif mutation == "refresh-snapshot-id":
            refresh_receipt["snapshot_id"] = "sha256:" + "f" * 64
        elif mutation == "registry-binding-omission":
            vector_registry["source_time_bindings"].pop()
        valid = source_as_of_semantics(
            panel, policy, refresh_receipt, vector_registry, schema,
            substitutions["$SCHEMA_SHA256"], strict_registry_sha, snapshot,
        )
        check(
            item["id"], valid if item.get("expected") == "valid"
            else (not valid and item["expected_error"] == "SNAPSHOT_TIME_INVALID"),
        )

    fact_replay_base = fact_attribution_fixture(
        substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], registry,
    )
    for item in suite["fact_replay_vectors"]:
        index = copy.deepcopy(fact_replay_base["command_index"])
        entry = index["entries"][0]
        receipt_store = {entry["receipt_path"]: canonical_bytes(fact_replay_base["receipt"])}
        command_id = fact_replay_base["command"]["command_id"]
        fingerprint = sha256_bytes(canonical_bytes(fact_replay_base["command"]))
        mutation = item["mutation"]
        if mutation == "fingerprint-conflict":
            fingerprint = "sha256:" + "f" * 64
        elif mutation == "new-command":
            command_id = "cmd-fact-replay-new"
        elif mutation == "missing-receipt":
            receipt_store.clear()
        elif mutation == "tampered-receipt":
            receipt_store[entry["receipt_path"]] += b"\n"
        elif mutation == "wrong-receipt-path":
            old_path = entry["receipt_path"]
            entry["receipt_path"] = "receipts/fact/wrong.json"
            receipt_store[entry["receipt_path"]] = receipt_store.pop(old_path)
            rebind_identity(index, "index_id")
        elif mutation == "wrong-receipt-hash":
            entry["receipt_sha256"] = "sha256:" + "f" * 64
            rebind_identity(index, "index_id")
        elif mutation == "sequence-gap":
            entry["sequence"] = 2
            rebind_identity(index, "index_id")
        elif mutation == "duplicate-command-id":
            duplicate = copy.deepcopy(entry)
            duplicate["sequence"] = 2
            index["entries"].append(duplicate)
            index["next_sequence"] = 3
            rebind_identity(index, "index_id")
        outcome, _ = resolve_fact_command_replay(
            index, receipt_store, command_id, fingerprint,
            registry, schema, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"],
        )
        check(item["id"], outcome == item["expected_outcome"])

    def convergence_verdict(outbox: dict[str, Any]) -> dict[str, Any]:
        pending = sorted(
            (row["intent_id"] for row in outbox["entries"] if row["status"] in {"pending", "processing"}),
            key=lambda value: value.encode("utf-8"),
        )
        failed = sorted(
            (row["intent_id"] for row in outbox["entries"] if row["status"] == "failed"),
            key=lambda value: value.encode("utf-8"),
        )
        waived = sorted(
            (row["intent_id"] for row in outbox["entries"] if row["status"] == "waived"),
            key=lambda value: value.encode("utf-8"),
        )
        verdict = {
            "contract": expected_contract_ref(
                strict_registry, "intent-convergence-verdict/1.0.0",
                substitutions["$SCHEMA_SHA256"], strict_registry_sha,
            ),
            "schema_version": "1.0.0", "outbox_id": outbox["outbox_id"],
            "evaluated_through_sequence": outbox["entries"][-1]["sequence"] if outbox["entries"] else 0,
            "pending_intent_ids": pending, "failed_intent_ids": failed, "waived_intent_ids": waived,
            "status": "failed" if failed else "pending" if pending else "waived" if waived else "converged",
        }
        verdict["verdict_id"] = sha256_bytes(canonical_bytes(verdict))
        return verdict

    strict_intent_graphs = {
        "meeting": fact_attribution_fixture(
            substitutions["$SCHEMA_SHA256"], strict_registry_sha, strict_registry, "wdr-meeting-history",
        ),
        "checkpoint": fact_attribution_fixture(
            substitutions["$SCHEMA_SHA256"], strict_registry_sha, strict_registry, "wdr-owned-section",
        ),
        "risk": fact_attribution_fixture(
            substitutions["$SCHEMA_SHA256"], strict_registry_sha, strict_registry, "owned-risk-flow",
        ),
        "risk-decision": fact_attribution_fixture(
            substitutions["$SCHEMA_SHA256"], strict_registry_sha, strict_registry, "owned-decision",
        ),
        "status-consume": fact_attribution_fixture(
            substitutions["$SCHEMA_SHA256"], strict_registry_sha, strict_registry, "wdr-status",
        ),
    }
    eligibility_lineage = snapshot_lineage
    for item in suite["intent_outbox_vectors"]:
        scenario, mutation = item["scenario"], item["mutation"]
        if scenario in strict_intent_graphs:
            graph = copy.deepcopy(strict_intent_graphs[scenario])
            if mutation == "missing-target":
                graph["journal"]["targets"] = [
                    row for row in graph["journal"]["targets"] if row["role"] != "intent-outbox"
                ]
                _reindex_targets(graph["journal"]["targets"], graph["journal"]["journal_dir"])
                rebind_fact_graph(graph)
            elif mutation in {"emitted-status", "consumed-receipt"}:
                entry = graph["after_outbox"]["entries"][0]
                if mutation == "emitted-status":
                    entry.update({
                        "status": "consumed", "attempts": 1, "last_error": None,
                        "consumed_receipt_id": graph["receipt"]["receipt_id"],
                    })
                else:
                    entry["consumed_receipt_id"] = "sha256:" + "f" * 64
                rebind_identity(graph["after_outbox"], "outbox_id")
                target = next(row for row in graph["journal"]["targets"] if row["role"] == "intent-outbox")
                target["after_sha256"] = sha256_bytes(canonical_bytes(graph["after_outbox"]))
                target["after_image"]["sha256"] = target["after_sha256"]
                rebind_fact_graph(graph)
            elif mutation == "intent-digest-substitution":
                graph["command"]["status_intents"][0]["set"] = {"progress": "Substituted"}
                rebind_fact_graph(graph)
            elif mutation == "missing-command-intent":
                del graph["command"]["status_intents"]
                rebind_fact_graph(graph)
            elif mutation in {"omitted-consumed-intent", "extra-consumed-intent"}:
                if mutation == "omitted-consumed-intent":
                    graph["command"]["consumed_intent_ids"] = graph["command"]["consumed_intent_ids"][1:]
                else:
                    graph["command"]["consumed_intent_ids"].append("sha256:" + "f" * 64)
                    graph["command"]["consumed_intent_ids"].sort(key=lambda value: value.encode("utf-8"))
                rebind_fact_graph(graph)
            elif mutation in {"terminal-consumed-intent", "cross-workstream-consumed-intent"}:
                before_entry = graph["before_outbox"]["entries"][0]
                after_entry = graph["after_outbox"]["entries"][0]
                if mutation == "terminal-consumed-intent":
                    before_entry.update({"status": "consumed", "attempts": 1, "consumed_receipt_id": graph["receipt"]["receipt_id"]})
                else:
                    old_id = before_entry["intent_id"]
                    for entry in (before_entry, after_entry):
                        entry["intent"]["workstream_id"] = "l1-other"
                        entry["workstream_id"] = "l1-other"
                        entry["intent_id"] = sha256_bytes(canonical_bytes(entry["intent"]))
                    graph["command"]["consumed_intent_ids"] = sorted(
                        [after_entry["intent_id"] if value == old_id else value for value in graph["command"]["consumed_intent_ids"]],
                        key=lambda value: value.encode("utf-8"),
                    )
                for name in ("before_outbox", "after_outbox"):
                    rebind_identity(graph[name], "outbox_id")
                target = next(row for row in graph["journal"]["targets"] if row["role"] == "intent-outbox")
                target["before_sha256"] = sha256_bytes(canonical_bytes(graph["before_outbox"]))
                target["after_sha256"] = sha256_bytes(canonical_bytes(graph["after_outbox"]))
                target["before_image"]["sha256"] = target["before_sha256"]
                target["after_image"]["sha256"] = target["after_sha256"]
                rebind_fact_graph(graph)
            elif mutation == "extra-same-workstream-pending":
                extra = copy.deepcopy(graph["before_outbox"]["entries"][0])
                extra["sequence"] = len(graph["before_outbox"]["entries"]) + 1
                extra["intent"]["intent_id"] = "meeting-extra-same-workstream"
                extra["intent"]["set"] = {"risks": {"mode": "add", "values": ["late carrier"]}}
                extra["intent_id"] = sha256_bytes(canonical_bytes(extra["intent"]))
                extra["source_command_id"] = "cmd-extra-same-workstream"
                extra["source_command_fingerprint"] = "sha256:" + "e" * 64
                extra["field_set"] = ["risks"]
                graph["before_outbox"]["entries"].append(extra)
                graph["after_outbox"]["entries"].append(copy.deepcopy(extra))
                for name in ("before_outbox", "after_outbox"):
                    rebind_identity(graph[name], "outbox_id")
                target = next(row for row in graph["journal"]["targets"] if row["role"] == "intent-outbox")
                target["before_sha256"] = sha256_bytes(canonical_bytes(graph["before_outbox"]))
                target["after_sha256"] = sha256_bytes(canonical_bytes(graph["after_outbox"]))
                target["before_image"]["sha256"] = target["before_sha256"]
                target["after_image"]["sha256"] = target["after_sha256"]
                rebind_fact_graph(graph)
            elif mutation in {"denied-status-intents-capability", "denied-consumed-intent-ids-capability"}:
                capability = next(
                    row for row in graph["capability_registry"]["capabilities"]
                    if row["producer_id"] == command_producer(graph["command"])
                )
                denied = "status_intents" if mutation == "denied-status-intents-capability" else "consumed_intent_ids"
                capability["allowed_fields"].remove(denied)
                rebind_fact_graph(graph)
            authority = runtime_authority_fixture(
                strict_registry, substitutions["$SCHEMA_SHA256"], strict_registry_sha,
                command_producer(graph["command"]),
            )
            valid = fact_attribution_semantics(
                graph, strict_registry, schema, substitutions["$SCHEMA_SHA256"], strict_registry_sha,
                *authority,
            )
        elif scenario in {"pending", "failed", "consumed"}:
            source_graph = strict_intent_graphs["status-consume" if scenario == "consumed" else "meeting"]
            outbox = copy.deepcopy(source_graph["after_outbox"])
            if scenario == "failed":
                outbox["entries"][0].update({"status": "failed", "attempts": 1, "last_error": "STATUS_SYNC_FAILED"})
                rebind_identity(outbox, "outbox_id")
            verdict = convergence_verdict(outbox)
            if mutation == "invented-id":
                verdict["pending_intent_ids"].append("sha256:" + "f" * 64)
                verdict["pending_intent_ids"].sort(key=lambda value: value.encode("utf-8"))
                rebind_identity(verdict, "verdict_id")
            elif mutation == "omitted-id":
                verdict["pending_intent_ids"].clear()
                rebind_identity(verdict, "verdict_id")
            consumed_receipts = None
            if scenario == "consumed":
                consumed_receipts = {} if mutation == "missing-consumed-receipt" else {
                    source_graph["receipt"]["receipt_id"]: canonical_bytes(source_graph["receipt"]),
                }
            valid = intent_convergence_semantics(
                outbox, verdict, strict_registry, schema,
                substitutions["$SCHEMA_SHA256"], strict_registry_sha, consumed_receipts,
            )
        else:
            if eligibility_lineage is None:
                check(item["id"], False)
                continue
            panel = copy.deepcopy(eligibility_lineage["graph"]["panel"])
            source_graph = strict_intent_graphs["meeting"]
            outbox = copy.deepcopy(source_graph["after_outbox"])
            if scenario == "eligibility-failed":
                outbox["entries"][0].update({"status": "failed", "attempts": 1, "last_error": "STATUS_SYNC_FAILED"})
                rebind_identity(outbox, "outbox_id")
            verdict = convergence_verdict(outbox)
            panel["sync"]["audit"]["intent_convergence"] = copy.deepcopy(verdict)
            if mutation == "mark-ineligible":
                panel["sync"]["publication_eligibility"] = "ineligible"
            valid = publication_eligibility_semantics(
                panel, eligibility_lineage["graph"]["physical_inventory"], eligibility_lineage["policy"],
                eligibility_lineage["generation"], strict_registry, schema,
                substitutions["$SCHEMA_SHA256"], strict_registry_sha, eligibility_lineage["graph"]["built"],
                outbox, verdict,
            )
        check(
            item["id"], valid if item.get("expected") == "valid"
            else (not valid and item["expected_error"] in {"INTENT_OUTBOX_INVALID", "FACT_ATTRIBUTION_INVALID"}),
        )

    for item in suite["publication_replay_vectors"]:
        valid = first_publication_fresh_process_semantics(
            strict_suite, strict_registry, schema, substitutions["$SCHEMA_SHA256"], strict_registry_sha,
            project_root, expected_ids, strict_hashes,
        )
        check(item["id"], valid and item.get("expected") == "valid")

    first_publication_package = writer_fence_fixture(
        strict_registry, substitutions["$SCHEMA_SHA256"], strict_registry_sha, expected_ids, strict_hashes,
        suite=strict_suite, schema=schema, project_root=project_root, first_publication=True,
    )
    first_publication_lineage = load_strict_lineage(
        first_publication_package, strict_registry, schema,
        substitutions["$SCHEMA_SHA256"], strict_registry_sha, verify_live_leaves=False,
    )
    for item in suite["publication_recovery_vectors"]:
        if first_publication_lineage is None:
            check(item["id"], False)
            continue
        graph = first_publication_lineage["graph"]
        recovery_package = {
            "journal": graph["journal"], "marker": graph["marker"],
            "target_images": panel_publication_target_images(graph),
        }
        if item["cut"] == "before-lineage-index":
            crash_after = next(
                index for index, row in enumerate(graph["journal"]["targets"])
                if row["role"] == "lineage-index"
            )
        elif item["cut"] == "before-pointer":
            crash_after = next(
                index for index, row in enumerate(graph["journal"]["targets"])
                if row["role"] == "pointer"
            )
        else:
            crash_after = len(graph["journal"]["targets"])
        valid = release_evidence_recovery_semantics(
            recovery_package, crash_after, item["committed"], strict_registry, schema,
            substitutions["$SCHEMA_SHA256"], strict_registry_sha,
        )
        check(item["id"], valid and item.get("expected") == "valid")

    for item in suite["journal_fault_matrix"]:
        vector_id = item["id"]
        if vector_id == "first-create-absent-target":
            check(vector_id, item["before_sha256"] is None and item["primitive"] == "durable_create")
        elif vector_id == "created-target-rollback-to-absence":
            check(vector_id, item["operation"] == "create" and item["primitive"] == "durable_remove_to_tombstone" and item["expected"] == "missing")
        elif vector_id == "journal-image-locators-and-order":
            check(vector_id, [row["apply_order"] for row in item["targets"]] == item["expected_orders"] and all(row["after_image"] for row in item["targets"]))
        elif vector_id.startswith("journal-"):
            manifest, marker = journal_fixture(item["transaction_kind"], substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], registry)
            mutation = item["mutation"]
            if mutation == "create-has-before":
                target = next(row for row in manifest["targets"] if row["operation"] == "create")
                target["before_sha256"] = target["after_sha256"]
                target["before_image"] = copy.deepcopy(target["after_image"])
            elif mutation == "remove-has-after":
                target = manifest["targets"][0]
                target["operation"] = "remove"
            elif mutation == "duplicate-order":
                manifest["targets"][1]["apply_order"] = 0
            elif mutation == "gapped-order":
                manifest["targets"][1]["apply_order"] = 4
            elif mutation == "duplicate-target":
                manifest["targets"][1]["root_instance_id"] = manifest["targets"][0]["root_instance_id"]
                manifest["targets"][1]["path"] = manifest["targets"][0]["path"]
            elif mutation == "wrong-receipt-path":
                manifest["receipt_target_paths"] = ["receipts/other.json"]
            elif mutation == "one-repair-receipt":
                manifest["targets"].pop()
                manifest["receipt_target_paths"].pop()
            elif mutation == "locator-hash-mismatch":
                manifest["targets"][0]["before_image"]["sha256"] = "sha256:" + "f" * 64
            elif mutation == "foreign-image-locator":
                manifest["targets"][0]["before_image"]["path"] = f"state/transactions/{filesystem_token('other-transaction')}/images/0-before"
            elif mutation == "parent-image-locator":
                manifest["targets"][0]["before_image"]["path"] = f"{manifest['journal_dir']}/images/../images/0-before"
            elif mutation == "manifest-path-substitution":
                manifest["manifest_path"] = f"{manifest['journal_dir']}/journal.json"
            elif mutation == "terminal-marker-path-substitution":
                manifest["terminal_marker_path"] = f"{manifest['journal_dir']}/done.json"
            elif mutation == "recovery-path-substitution":
                manifest["recovery_receipt_path"] = f"{manifest['journal_dir']}/recovered.json"
            elif mutation == "marker-manifest-mismatch":
                marker["manifest_id"] = "sha256:" + "f" * 64
            elif mutation == "panel-wrong-role":
                next(row for row in manifest["targets"] if row["role"] == "projection")["role"] = "business"
            elif mutation == "noncommitted-marker":
                marker["state"] = "prepared"
                marker["marker_id"] = sha256_bytes(canonical_bytes({key: value for key, value in marker.items() if key != "marker_id"}))
            valid = journal_semantics(
                manifest, marker, schema, registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"]
            )
            check(vector_id, valid if item.get("expected") == "valid" else (not valid and item["expected_error"] == "JOURNAL_INVALID"))
        else:
            states = list(item["targets"].values())
            if item["marker"]:
                actual = "committed" if all(state == "after" for state in states) else "CORRUPT_COMMITTED_TRANSACTION"
            elif "unknown" in states:
                actual = "CORRUPT_UNCOMMITTED_TRANSACTION"
            else:
                actual = "rolled-forward" if all(state == "after" for state in states) else "rolled-back"
            check(vector_id, actual == item.get("expected", item.get("expected_error")))

    for item in suite["receipt_vectors"]:
        if item["id"] == "receipt-not-self-referential":
            valid = not item["expected_receipt_contains_own_target"] and item["journal_target_roles"].count("receipt") == 1
        elif item["id"] == "rollback-receipt-is-journal-local":
            valid = not item["recovery_receipt_target"] and item["expected"] == "original-before-images-restored"
        elif item["id"] == "capability-id-known-answer":
            preimage = canonical_bytes(item["record_without_identity"])
            valid = preimage.decode() == item["expected_preimage"] and sha256_bytes(preimage) == item["expected_digest"]
        elif item["id"].startswith("fact-attribution-") or item.get("command_kind") in {"owned-risk-flow", "owned-decision"}:
            fixture_kind = item.get("command_kind", "action")
            create_command = None
            if fixture_kind == "wdr-create":
                create_command = copy.deepcopy(next(row["command"] for row in suite["wdr_vectors"] if row["id"] == "create-byte-exact"))
                create_input = {key: value for key, value in create_command["create_input"].items() if key != "input_id"}
                create_command["create_input"]["input_id"] = sha256_bytes(canonical_bytes(create_input))
            graph = fact_attribution_fixture(
                substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], registry, fixture_kind, create_command
            )
            runtime_capability_bytes, runtime_root_registry_bytes, runtime_activation_bytes, runtime_attestation_bytes, authority_context = runtime_authority_fixture(
                registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], command_producer(graph["command"]),
            )
            capability_registry, command, journal, receipt = (graph[name] for name in ("capability_registry", "command", "journal", "receipt"))
            mutation = item["mutation"]

            def rebind_targets() -> None:
                _reindex_targets(journal["targets"], journal["journal_dir"])
                receipt["business_targets"] = copy.deepcopy([row for row in journal["targets"] if row["role"] == "business"])
                receipt["generation_state_target"] = copy.deepcopy(next(row for row in journal["targets"] if row["role"] == "fact-generation"))
                rebind_fact_graph(graph)

            def replace_business_after(path: str, raw: bytes) -> None:
                artifact = next(row for row in graph["proof"]["business_artifacts"] if row["path"] == path)
                target = next(row for row in journal["targets"] if row["role"] == "business" and row["path"] == path)
                artifact["after_bytes"] = encoded_bytes(raw)
                target["after_sha256"] = sha256_bytes(raw)
                target["after_image"]["sha256"] = target["after_sha256"]

            def rebind_action_after(field: str, value: Any) -> None:
                artifacts = {row["path"]: row for row in graph["proof"]["business_artifacts"]}
                ledger_path = registry["runtime_paths"]["action_ledger"]["path"]
                state_path = registry["runtime_paths"]["action_ledger_state"]["path"]
                flow_path = registry["runtime_paths"]["action_flow_index"]["path"]
                rows = parse_action_ledger(artifact_bytes(artifacts[ledger_path]["after_bytes"]))
                row = next(row for row in rows if row["action_id"] == command["action_id"])
                row[field] = value
                raw = render_action_ledger(rows)
                old_state = json.loads(artifact_bytes(artifacts[state_path]["after_bytes"]))
                state = action_ledger_state_document(rows, raw, old_state["ledger_revision"], old_state["applied_commands"], registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
                flow = action_flow_document(rows, raw, old_state["ledger_revision"], registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
                replace_business_after(ledger_path, raw)
                replace_business_after(state_path, canonical_bytes(state))
                replace_business_after(flow_path, canonical_bytes(flow))
                rebind_targets()

            def rebind_refresh_outputs() -> None:
                workstream_id = command["workstream_id"]
                wdr_path = f"workstreams/{workstream_id}/delivery-record.md"
                state_path = f"workstreams/{workstream_id}/delivery-record.state.json"
                sidecar_path = f"workstreams/{workstream_id}/action-projection.json"
                artifacts = {row["path"]: row for row in graph["proof"]["business_artifacts"]}
                before_wdr = artifact_bytes(artifacts[wdr_path]["before_bytes"])
                sidecar = json.loads(artifact_bytes(artifacts[sidecar_path]["after_bytes"]))
                snapshot = command["action_snapshot"]
                sidecar["ledger_fingerprint"] = snapshot["ledger_fingerprint"]
                sidecar["ledger_revision"] = snapshot["ledger_revision"]
                sidecar["actions"] = copy.deepcopy(snapshot["actions"])
                after_wdr = apply_wdr_patch(before_wdr.decode("utf-8"), command, [row["rendered_summary"] for row in sidecar["actions"]]).encode("utf-8")
                state = json.loads(artifact_bytes(artifacts[state_path]["after_bytes"]))
                state["record_fingerprint"] = sha256_bytes(after_wdr)
                replace_business_after(wdr_path, after_wdr)
                replace_business_after(state_path, canonical_bytes(state))
                replace_business_after(sidecar_path, canonical_bytes(sidecar))
                rebind_targets()

            if mutation == "forged-producer":
                receipt["authorization"]["producer_id"] = "adp-meeting-sync"
            elif mutation == "forged-capability":
                receipt["authorization"]["capability_id"] = "sha256:" + "f" * 64
            elif mutation == "forged-principal":
                receipt["authorization"]["principal_id"] = "sha256:" + "f" * 64
            elif mutation == "runtime-principal-mismatch":
                authority_context["principal_id"] = "sha256:" + "f" * 64
            elif mutation == "runtime-capability-bytes-tamper":
                runtime_capability_bytes += b"\n"
            elif mutation == "fully-rebound-forged-graph":
                capability = next(row for row in capability_registry["capabilities"] if row["producer_id"] == command_producer(command))
                capability["principal_id"] = "sha256:" + "f" * 64
                rebind_fact_graph(graph)
            elif mutation == "roadmap-heading-injection":
                command["set"]["roadmap"]["lines"][0] = "## Injected"
                rebind_fact_graph(graph)
            elif mutation == "roadmap-owned-section-substitution":
                command["set"] = {"owned_sections": [{"section": "roadmap", "mode": "replace", "lines": ["Milestone", "Target"]}]}
                rebind_fact_graph(graph)
            elif mutation == "meeting-history-outer-command-mismatch":
                command["set"]["meeting_history_append"][0]["command_id"] = "cmd-other-history-1"
                rebind_fact_graph(graph)
            elif mutation == "command-fingerprint":
                receipt["authorization"]["authorized_command_fingerprint"] = "sha256:" + "f" * 64
            elif mutation == "wrong-target":
                receipt["business_targets"][0]["path"] = "actions/other-ledger.md"
            elif mutation == "generation-jump":
                receipt["after_fact_generation"] = 99
            elif mutation == "revision-jump":
                receipt["action_deltas"][0]["after_revision"] = 99
            elif mutation in {"unequal-record-digests", "forged-record-digest"}:
                capability = next(row for row in capability_registry["capabilities"] if row["producer_id"] == "adp-status-sync")
                capability["authorization_record_digest"] = "sha256:" + "f" * 64
                if mutation == "forged-record-digest":
                    capability["capability_id"] = "sha256:" + "f" * 64
                capability_registry["capability_registry_id"] = sha256_bytes(canonical_bytes({key: value for key, value in capability_registry.items() if key != "capability_registry_id"}))
            elif mutation in {"denied-operation", "denied-field", "denied-section", "denied-section-last"}:
                capability = next(row for row in capability_registry["capabilities"] if row["producer_id"] == command_producer(command))
                if mutation == "denied-operation":
                    capability["allowed_operations"] = ["create"] if command["operation"] != "create" else ["patch"]
                elif mutation == "denied-field":
                    field = sorted(command_permissions(command, registry)[0], key=lambda value: value.encode("utf-8"))[0]
                    capability["allowed_fields"].remove(field)
                else:
                    sections = sorted(command_permissions(command, registry)[1], key=lambda value: value.encode("utf-8"))
                    section = sections[-1] if mutation == "denied-section-last" else sections[0]
                    capability["allowed_sections"].remove(section)
                rebind_fact_graph(graph)
            elif mutation in {"duplicate-producer", "duplicate-capability"}:
                capability_registry["capabilities"].append(copy.deepcopy(next(row for row in capability_registry["capabilities"] if row["producer_id"] == "adp-status-sync")))
                rebind_fact_graph(graph)
            elif mutation == "receipt-target-hash":
                next(row for row in journal["targets"] if row["role"] == "receipt")["after_sha256"] = "sha256:" + "f" * 64
            elif mutation == "receipt-target-path":
                next(row for row in journal["targets"] if row["role"] == "receipt")["path"] = "receipts/fact/wrong.json"
            elif mutation == "before-state":
                graph["before_state"]["fact_generation"] = 6
                graph["before_state"]["state_id"] = sha256_bytes(canonical_bytes({key: value for key, value in graph["before_state"].items() if key != "state_id"}))
                state_target = next(row for row in journal["targets"] if row["role"] == "fact-generation")
                state_target["before_sha256"] = sha256_bytes(canonical_bytes(graph["before_state"]))
                state_target["before_image"]["sha256"] = state_target["before_sha256"]
                journal["manifest_id"] = sha256_bytes(canonical_bytes({key: value for key, value in journal.items() if key != "manifest_id"}))
                graph["marker"]["manifest_id"] = journal["manifest_id"]
                graph["marker"]["marker_id"] = sha256_bytes(canonical_bytes({key: value for key, value in graph["marker"].items() if key != "marker_id"}))
            elif mutation in {"fake-command-anchor", "command-schema-hash", "command-registry-hash"}:
                field = {"fake-command-anchor": "schema_id", "command-schema-hash": "schema_sha256", "command-registry-hash": "registry_sha256"}[mutation]
                command["contract"][field] = "urn:adp:panel-sync-contracts:2026-07-24#unknown-command-v1" if field == "schema_id" else "sha256:" + "f" * 64
            elif mutation in {"capability-contract-anchor", "before-state-contract-hash", "journal-contract-hash", "marker-contract-hash", "receipt-contract-hash", "proof-contract-hash"}:
                document, field = {
                    "capability-contract-anchor": (capability_registry, "schema_id"),
                    "before-state-contract-hash": (graph["before_state"], "schema_sha256"),
                    "journal-contract-hash": (journal, "registry_sha256"),
                    "marker-contract-hash": (graph["marker"], "schema_sha256"),
                    "receipt-contract-hash": (receipt, "registry_sha256"),
                    "proof-contract-hash": (graph["proof"], "schema_sha256"),
                }[mutation]
                document["contract"][field] = "urn:adp:panel-sync-contracts:2026-07-24#unknown-capability-v1" if field == "schema_id" else "sha256:" + "f" * 64
            elif mutation in {"wrong-root", "wrong-operation", "create-as-replace", "replace-as-create"}:
                target = next(row for row in journal["targets"] if row["role"] == "business")
                artifact = next(row for row in graph["proof"]["business_artifacts"] if row["path"] == target["path"])
                if mutation == "wrong-root":
                    target["root_instance_id"] = artifact["root_instance_id"] = "123e4567-e89b-42d3-a456-426614174099"
                    for locator in (target["before_image"], target["after_image"]):
                        if locator is not None:
                            locator["root_instance_id"] = target["root_instance_id"]
                else:
                    operation = "replace" if mutation == "create-as-replace" else ("create" if mutation == "replace-as-create" else "remove")
                    target["operation"] = artifact["operation"] = operation
                rebind_targets()
            elif mutation in {"stale-wdr-revision", "stale-file-generation"}:
                command["expected_wdr_revision" if mutation == "stale-wdr-revision" else "expected_file_generation"] -= 1
                rebind_fact_graph(graph)
            elif mutation in {"before-byte-substitution", "after-byte-substitution"}:
                artifact = graph["proof"]["business_artifacts"][0]
                artifact["before_bytes" if mutation == "before-byte-substitution" else "after_bytes"] = encoded_bytes(b"substituted")
                rebind_fact_graph(graph)
            elif mutation in {"missing-ledger-state-target", "missing-flow-target", "missing-sidecar-target"}:
                if mutation == "missing-sidecar-target":
                    path = f"workstreams/{command['workstream_id']}/action-projection.json"
                else:
                    runtime_name = "action_ledger_state" if mutation == "missing-ledger-state-target" else "action_flow_index"
                    path = registry["runtime_paths"][runtime_name]["path"]
                journal["targets"] = [row for row in journal["targets"] if not (row["role"] == "business" and row["path"] == path)]
                graph["proof"]["business_artifacts"] = [row for row in graph["proof"]["business_artifacts"] if row["path"] != path]
                rebind_targets()
            elif mutation == "extra-business-target":
                source_target = next(row for row in journal["targets"] if row["role"] == "business")
                extra_target = copy.deepcopy(source_target)
                extra_target["path"] = "actions/extra-derived-index.json"
                insertion = next(index for index, row in enumerate(journal["targets"]) if row["role"] == "fact-generation")
                journal["targets"].insert(insertion, extra_target)
                extra_artifact = copy.deepcopy(graph["proof"]["business_artifacts"][0])
                extra_artifact["path"] = extra_target["path"]
                graph["proof"]["business_artifacts"].append(extra_artifact)
                rebind_targets()
            elif mutation in {"rebound-owner", "rebound-status", "rebound-action", "rebound-due", "rebound-closure", "rebound-route", "rebound-affected"}:
                field, value = {
                    "rebound-owner": ("owner", "FDE-X"),
                    "rebound-status": ("status", "blocked"),
                    "rebound-action": ("action", "Substituted action"),
                    "rebound-due": ("due_trigger", "later gate"),
                    "rebound-closure": ("closure_criteria", "Substituted closure"),
                    "rebound-route": ("routing_scope_id", "l1-payments"),
                    "rebound-affected": ("affected_workstreams", ["l1-payments"]),
                }[mutation]
                rebind_action_after(field, value)
            elif mutation == "reopen-terminal":
                command["set"]["status"] = "open"
                receipt["action_deltas"] = [expected_action_delta(command)]
                rebind_action_after("status", "open")
            elif mutation in {"refresh-stale-ledger-fingerprint", "refresh-stale-ledger-revision", "refresh-missing-active", "refresh-extra-action"}:
                snapshot = command["action_snapshot"]
                if mutation == "refresh-stale-ledger-fingerprint":
                    snapshot["ledger_fingerprint"] = "sha256:" + "f" * 64
                elif mutation == "refresh-stale-ledger-revision":
                    snapshot["ledger_revision"] -= 1
                elif mutation == "refresh-missing-active":
                    snapshot["actions"].pop()
                else:
                    ledger_raw = artifact_bytes(next(row for row in graph["proof"]["read_artifacts"] if row["path"] == registry["runtime_paths"]["action_ledger"]["path"])["bytes"])
                    other = action_snapshot(parse_action_ledger(ledger_raw), "l1-other", snapshot["ledger_fingerprint"], snapshot["ledger_revision"])["actions"][0]
                    snapshot["actions"].append(other)
                    snapshot["actions"].sort(key=lambda row: row["action_id"].encode("utf-8"))
                rebind_refresh_outputs()
            elif mutation == "refresh-read-bytes":
                read = graph["proof"]["read_artifacts"][0]
                raw = b"substituted ledger snapshot\n"
                read["bytes"] = encoded_bytes(raw)
                read["sha256"] = sha256_bytes(raw)
                rebind_fact_graph(graph)
            elif mutation == "runtime-principal-mismatch":
                authority_context["principal_id"] = "sha256:" + "f" * 64
            elif mutation == "runtime-capability-bytes-tampered":
                runtime_capability_bytes += b"\n"
            valid = fact_attribution_semantics(
                graph, registry, schema, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"],
                runtime_capability_bytes, runtime_root_registry_bytes, runtime_activation_bytes,
                runtime_attestation_bytes, authority_context,
            )
            valid = valid if item.get("expected") == "valid" else (not valid and item["expected_error"] == "FACT_ATTRIBUTION_INVALID")
        else:
            delta = item["action_delta"]
            valid = item["initiator"]["producer_id"] == "adp-status-sync" and delta["operation"] == "patch" and delta["after_revision"] == delta["before_revision"] + 1 and delta["changed_fields"] == ["owner"]
        check(item["id"], valid)

    for item in suite["legacy_wdr_update_vectors"]:
        typed = item["typed_status_payload"]
        mutations = 1 if typed and typed.get("set") else 0
        gap = None if mutations else "LEGACY_STATUS_INTENT_REQUIRED"
        check(item["id"], item["expected_history_records"] == 1 and item["expected_current_mutations"] == mutations and item["expected_gap"] == gap)

    for item in suite["meeting_plan_vectors"]:
        plan = meeting_plan_intent_fixture(
            registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"],
        )
        mutation = item["mutation"]
        if mutation == "omit-carrier":
            plan["intent_outbox_commands"] = []
        elif mutation == "extra-intent":
            extra = copy.deepcopy(plan["status_intents"][0])
            extra["intent_id"] = "meeting-M-INTENT-1-extra"
            extra["set"] = {"blockers": {"mode": "add", "values": ["unexpected"]}}
            plan["status_intents"].append(extra)
        elif mutation == "duplicate-intent":
            plan["status_intents"].append(copy.deepcopy(plan["status_intents"][0]))
        elif mutation == "wrong-meeting":
            plan["intent_outbox_commands"][0]["source_instance_id"] = "meeting-M-OTHER"
        elif mutation == "evidence-substitution":
            plan["intent_outbox_commands"][0]["evidence"][0]["source_path"] = "meetings/other.md"
        valid = meeting_plan_intent_carrier_semantics(
            plan, registry, schema, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"],
        )
        check(
            item["id"], valid if item.get("expected") == "valid"
            else (not valid and item["expected_error"] == "MEETING_PLAN_INTENT_CARRIER_INVALID"),
        )

    for item in suite["status_intent_vectors"]:
        batch = status_intent_fixture(registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
        mutation = item["mutation"]
        if mutation == "omit-accepted-id":
            batch["accepted_intent_ids"].pop()
        elif mutation == "omit-field":
            batch["wdr_patches"][0]["set"].pop("progress")
        elif mutation == "substitute-field":
            batch["wdr_patches"][0]["set"]["progress"] = "Substituted"
        elif mutation == "drop-evidence":
            batch["wdr_patches"][0]["evidence"].pop()
        elif mutation == "cross-workstream":
            batch["wdr_patches"][0]["workstream_id"] = "l1-payments"
        elif mutation == "conflict":
            batch["accepted_intents"][0]["set"]["progress"] = "Conflicting progress"
            batch["intent_bindings"][0]["fields"] = sorted(batch["accepted_intents"][0]["set"], key=lambda value: value.encode("utf-8"))
        elif mutation == "wrong-command-order":
            batch["command_order"].reverse()
        elif mutation == "split-same-workstream":
            original = batch["wdr_patches"][0]
            split = copy.deepcopy(original)
            split["command_id"] = "cmd-status-l1-checkout-z"
            split["set"] = {"blockers": original["set"].pop("blockers")}
            split["evidence"] = [copy.deepcopy(batch["accepted_intents"][0]["evidence"][0])]
            original["evidence"] = [copy.deepcopy(batch["accepted_intents"][1]["evidence"][0])]
            batch["wdr_patches"].append(split)
            batch["intent_bindings"][0]["command_id"] = split["command_id"]
            batch["command_order"].append(split["command_id"])
        valid = status_intent_application_semantics(
            batch, registry, schema, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"]
        )
        check(item["id"], valid if item.get("expected") == "valid" else (not valid and item["expected_error"] == "STATUS_INTENT_APPLICATION_INVALID"))

    for item in suite["program_status_wdr_vectors"]:
        package = program_status_wdr_fixture(suite, registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
        mutation = item["mutation"]
        if mutation == "stale-progress":
            package["payload"]["workstream_current"][0]["progress"] = "Carried forward progress"
        elif mutation == "stale-blockers":
            package["payload"]["workstream_current"][0]["blockers"] = ["Old blocker"]
        elif mutation == "stale-phase":
            package["payload"]["workstream_current"][0]["phase"] = "legacy phase"
        elif mutation == "lineage":
            package["payload"]["workstream_current"][0]["wdr_fingerprint"] = "sha256:" + "f" * 64
        elif mutation == "wdr-change-panel":
            before_package = copy.deepcopy(package)
            workstream_id = package["selected_workstreams"][0]
            before_raw = package["wdrs"][workstream_id]
            command = {"set": {"progress": "Current progress changed", "blockers": {"mode": "replace", "values": ["New blocker"]}}}
            after_raw = apply_wdr_patch(before_raw.decode("utf-8"), command).encode("utf-8")
            package["wdrs"][workstream_id] = after_raw
            state = package["wdr_states"][workstream_id]
            state["record_fingerprint"] = sha256_bytes(after_raw); state["wdr_revision"] += 1; state["file_generation"] += 1
            current = parse_wdr_current(after_raw, workstream_id)
            package["payload"]["workstream_current"][0] = {
                **{key: current[key] for key in ("workstream_id", "phase", "status", "progress", "blockers", "risks", "dependencies", "action_ids")},
                "wdr_fingerprint": state["record_fingerprint"], "wdr_revision": state["wdr_revision"], "file_generation": state["file_generation"],
            }
            before_panel = {"panel_id": "sha256:" + "1" * 64, "sync": {"canonical": {"status": before_package["payload"]}}}
            after_panel = {"panel_id": "sha256:" + "1" * 64, "sync": {"canonical": {"status": package["payload"]}}}
            valid = (
                program_status_current_from_wdr_semantics(before_package, registry, schema, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
                and program_status_current_from_wdr_semantics(package, registry, schema, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
                and expected_panel_v2_current_view(before_panel, registry) != expected_panel_v2_current_view(after_panel, registry)
            )
            check(item["id"], valid and item["expected"] == "panel-output-changed")
            continue
        valid = program_status_current_from_wdr_semantics(
            package, registry, schema, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"]
        )
        check(item["id"], valid if item.get("expected") == "valid" else (not valid and item["expected_error"] == "PROGRAM_STATUS_WDR_INVALID"))

    for item in suite["drift_content_vectors"]:
        package = drift_content_fixture(registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
        mutation = item["mutation"]
        if mutation in {"owner", "action", "due", "status", "reported-owner"}:
            rows = parse_action_ledger(package["ledger_raw"])
            row = next(row for row in rows if row["action_id"] == "A-FLOW-1")
            field, value = {
                "owner": ("owner", "FDE-X"), "reported-owner": ("owner", "FDE-X"),
                "action": ("action", "Changed action"), "due": ("due_trigger", "later gate"), "status": ("status", "blocked"),
            }[mutation]
            row[field] = value
            package["ledger_raw"] = render_action_ledger(rows)
            old_state = package["ledger_state"]
            package["ledger_state"] = action_ledger_state_document(
                rows, package["ledger_raw"], old_state["ledger_revision"], old_state["applied_commands"],
                registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"],
            )
            if mutation == "reported-owner":
                package["verdict"] = expected_drift_verdict(package, registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
        elif mutation == "missing-active":
            package["sidecars"]["l1-checkout"]["actions"].pop()
        elif mutation == "retained-terminal":
            rows = parse_action_ledger(package["ledger_raw"])
            terminal = next(row for row in rows if row["action_id"] == "A-TERMINAL-1")
            retained = action_snapshot([{**terminal, "status": "open"}], "l1-checkout", package["ledger_state"]["ledger_fingerprint"], package["ledger_state"]["ledger_revision"])["actions"][0]
            package["sidecars"]["l1-checkout"]["actions"].append(retained)
            package["sidecars"]["l1-checkout"]["actions"].sort(key=lambda row: row["action_id"].encode("utf-8"))
        elif mutation == "fingerprint":
            package["sidecars"]["l1-checkout"]["ledger_fingerprint"] = "sha256:" + "f" * 64
        elif mutation in {"wdr-missing-marker", "wdr-orphan-marker", "wdr-content-marker", "empty-ledger-wdr-marker"}:
            workstream_id = "l1-checkout"
            sidecar = package["sidecars"][workstream_id]
            summaries: list[str] = []
            if mutation == "wdr-content-marker":
                changed = copy.deepcopy(sidecar["actions"][0])
                changed["owner"] = "FDE-X"
                changed["rendered_summary"] = rendered_action_summary(changed)
                summaries = [changed["rendered_summary"]]
            elif mutation in {"wdr-orphan-marker", "empty-ledger-wdr-marker"}:
                orphan = {
                    "action_id": "A-ORPHAN-1", "owner": "FDE-O", "action": "Remove orphan", "due_trigger": "next sync",
                    "status": "open", "action_revision": 1, "routing_scope_id": workstream_id,
                    "affected_workstreams": [workstream_id],
                }
                summaries = [rendered_action_summary(orphan)]
            if mutation == "empty-ledger-wdr-marker":
                rows = parse_action_ledger(package["ledger_raw"])
                next(row for row in rows if row["action_id"] == "A-FLOW-1")["status"] = "done"
                package["ledger_raw"] = render_action_ledger(rows)
                old_state = package["ledger_state"]
                package["ledger_state"] = action_ledger_state_document(
                    rows, package["ledger_raw"], old_state["ledger_revision"], old_state["applied_commands"],
                    registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"],
                )
                sidecar["actions"] = []
                sidecar["ledger_fingerprint"] = package["ledger_state"]["ledger_fingerprint"]
            raw = apply_wdr_patch(fixture_wdr(workstream_id), {"set": {"refresh_actions": True}}, summaries).encode("utf-8")
            package["wdrs"][workstream_id] = raw
            package["wdr_states"][workstream_id]["record_fingerprint"] = sha256_bytes(raw)
            package["verdict"] = expected_drift_verdict(
                package, registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"],
            )
        valid = action_projection_drift_content_semantics(
            package, registry, schema, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"]
        )
        if valid and item.get("expected_action_id"):
            action_diffs = package["verdict"]["workstreams"][0]["action_diffs"]
            valid = any(
                row["action_id"] == item["expected_action_id"] and row["drift_kind"] == item["expected_drift_kind"]
                for row in action_diffs
            )
        check(item["id"], valid if item.get("expected") == "valid" else (not valid and item["expected_error"] == "DRIFT_CONTENT_INVALID"))

    for item in suite["finding_identity_vectors"]:
        diff = {
            "action_id": "A-FLOW-1", "drift_kind": "content-mismatch", "ledger_present": True,
            "wdr_present": True, "ledger_revision": 4, "wdr_rendered_sha256": "sha256:" + "a" * 64,
        }
        original = drift_finding("l1-checkout", "action-projection-drift", diff)
        moved = copy.deepcopy(original)
        moved["source_path"] = "diagnostics/moved-delivery-record.md"
        moved["source_line"] = 2042
        moved_identity = {key: value for key, value in moved.items() if key not in {"finding_id", "source_path", "source_line"}}
        check(
            item["id"], item["expected"] == "stable"
            and original["finding_id"] == sha256_bytes(canonical_bytes(moved_identity)),
        )

    for item in suite["drift_vectors"]:
        if item["id"] == "drift-sidecar-change-invalidates":
            profile = profiles["action-projection-drift-verdict"]
            reads_sidecar = any(source["source_kind"] == "wdr-action-sidecar" for source in profile["required_sources"])
            check(item["id"], reads_sidecar and item["before"] != item["after"] and item["expected_required_projection"] == profile["projection"])
        else:
            valid = drift_semantics(item)
            check(item["id"], valid if item.get("expected") == "valid" else (not valid and item["expected_error"] == "DRIFT_COVERAGE_INVALID"))

    for item in suite["panel_binding_vectors"]:
        panel, upstreams, compatibility, policy, generation = panel_fixture(suite["contract_schema_vectors"], registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], project_root)
        physical_inventory = physical_inventory_fixture(
            registry, policy, generation["fact_generation"], substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"]
        )
        mutation = item.get("mutation", "none")

        def extra_workstream_row() -> dict[str, Any]:
            extra = copy.deepcopy(policy["physical_workstream_inventory"][0])
            extra["workstream_id"] = "l1-payments"
            for field, filename in (("wdr_source", "delivery-record.md"), ("sidecar_source", "action-projection.json")):
                extra[field]["path"] = f"workstreams/l1-payments/{filename}"
                extra[field]["fingerprint"] = extra[field]["blob_id"] = sha256_bytes(f"memory\0{extra[field]['path']}".encode("utf-8"))
            return extra

        def rebind_policy() -> None:
            policy["physical_workstream_inventory"].sort(key=lambda row: row["workstream_id"].encode("utf-8"))
            policy["workstream_catalog"].sort(key=lambda row: row["workstream_id"].encode("utf-8"))
            policy["physical_workstream_inventory_id"] = canonical_inventory_id(policy["physical_workstream_inventory"])
            policy["workstream_catalog_id"] = canonical_catalog_id(policy["workstream_catalog"])
            policy["policy_id"] = sha256_bytes(canonical_bytes({key: value for key, value in policy.items() if key != "policy_id"}))

        def rebind_physical_inventory() -> None:
            physical_inventory["inventory_id"] = canonical_inventory_id(physical_inventory["workstreams"])
            physical_inventory["attestation_id"] = sha256_bytes(canonical_bytes({key: value for key, value in physical_inventory.items() if key != "attestation_id"}))

        if mutation == "physical-attestation-fact-generation":
            physical_inventory["fact_generation"] -= 1
            rebind_physical_inventory()
        elif mutation == "physical-attestation-root":
            physical_inventory["memory_root_instance_id"] = "123e4567-e89b-42d3-a456-426614174099"
            rebind_physical_inventory()
        elif mutation == "physical-attestation-workstreams-omitted":
            physical_inventory["workstreams"] = []
            rebind_physical_inventory()
        elif mutation == "physical-attestation-missing":
            physical_inventory.pop("attestation_id")
        elif mutation == "physical-attestation-contract-hash":
            physical_inventory["contract"]["registry_sha256"] = "sha256:" + "f" * 64
        elif mutation == "selection-policy-contract-hash":
            policy["contract"]["schema_sha256"] = "sha256:" + "f" * 64
        elif mutation == "generation-contract-hash":
            generation["contract"]["registry_sha256"] = "sha256:" + "f" * 64
        elif mutation == "panel-embedded-contract-hash":
            upstreams["action-projection-drift-verdict"]["contract"]["schema_sha256"] = "sha256:" + "f" * 64
        elif mutation == "all-catalog-subset":
            extra = extra_workstream_row()
            policy["physical_workstream_inventory"].append(copy.deepcopy(extra))
            policy["workstream_catalog"].append(copy.deepcopy(extra))
            rebind_policy()
            generation = generation_fixture(registry, policy, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
        elif mutation == "inventory-catalog-omission":
            policy["physical_workstream_inventory"].append(extra_workstream_row())
            rebind_policy()
            generation = generation_fixture(registry, policy, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
        elif mutation == "catalog-extra-row":
            policy["workstream_catalog"].append(extra_workstream_row())
            rebind_policy()
            generation = generation_fixture(registry, policy, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
        elif mutation == "duplicate-physical-identity":
            policy["physical_workstream_inventory"].append(copy.deepcopy(policy["physical_workstream_inventory"][0]))
            rebind_policy()
            generation = generation_fixture(registry, policy, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
        elif mutation == "empty-all":
            policy["physical_workstream_inventory"] = []
            policy["workstream_catalog"] = []
            rebind_policy()
            generation = generation_fixture(registry, policy, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])
        elif mutation == "uncataloged-generation-pair":
            extra = extra_workstream_row()
            generation["leaf_sources"].extend([copy.deepcopy(extra["wdr_source"]), copy.deepcopy(extra["sidecar_source"])])
            generation["leaf_sources"].sort(key=lambda row: (row["root_instance_id"], row["path"]))
            generation["generation_id"] = sha256_bytes(canonical_bytes({key: value for key, value in generation.items() if key != "generation_id"}))
        elif mutation in {"generation-wdr-without-sidecar", "generation-sidecar-without-wdr"}:
            removed_kind = "wdr-action-sidecar" if mutation == "generation-wdr-without-sidecar" else "selected-physical-wdr"
            generation["leaf_sources"] = [row for row in generation["leaf_sources"] if row["source_kind"] != removed_kind]
            generation["generation_id"] = sha256_bytes(canonical_bytes({key: value for key, value in generation.items() if key != "generation_id"}))
        elif mutation == "panel-catalog-id":
            generation["panel_catalog_id"] = "sha256:" + "e" * 64
            generation["generation_id"] = sha256_bytes(canonical_bytes({key: value for key, value in generation.items() if key != "generation_id"}))

        if mutation in {"all-catalog-subset", "inventory-catalog-omission", "catalog-extra-row", "duplicate-physical-identity", "empty-all", "uncataloged-generation-pair", "generation-wdr-without-sidecar", "generation-sidecar-without-wdr", "panel-catalog-id"}:
            panel["sync"]["generation_id"] = generation["generation_id"]
            panel["sync"]["selection_policy_id"] = policy["policy_id"]
            upstreams["state-audit"]["selection_policy_id"] = policy["policy_id"]
            upstreams["action-projection-drift-verdict"]["selection_policy_id"] = policy["policy_id"]
            upstreams["action-projection-drift-verdict"]["generation_id"] = generation["generation_id"]
        if mutation.startswith("invalid-outer:") and mutation != "invalid-outer:management-panel":
            kind = mutation.split(":", 1)[1]
            target = upstreams[kind][0] if isinstance(upstreams[kind], list) else upstreams[kind]
            target.clear()
        elif mutation == "source-as-of-mismatch":
            upstreams["roadmap"]["source_as_of"] = "2026-07-24T02:00:01Z"
        for binding in registry["panel_binding_map"]:
            payload = upstreams[binding["projection_kind"]]
            if binding["merge_mode"] == "object-by-key":
                set_pointer(panel, binding["panel_pointer"], {row.get("scenario", f"invalid-{index}"): copy.deepcopy(row) for index, row in enumerate(payload)})
            else:
                set_pointer(panel, binding["panel_pointer"], copy.deepcopy(payload))
        if mutation in {"selected-drift", "selected-missing", "selected-malformed"}:
            row = panel["sync"]["action_projection"]["workstreams"][0]
            row["status"] = mutation.removeprefix("selected-")
            if row["status"] in {"missing", "malformed"}:
                row["wdr_fingerprint"] = None
                row["sidecar_fingerprint"] = None
            panel["sync"]["action_projection"]["overall_status"] = "blocked"
        elif mutation == "blocked-audit":
            panel["sync"]["audit"]["audit_status"] = "blocked"
            panel["sync"]["audit"]["execution_disposition"] = "blocked"
            panel["sync"]["artifact_integrity"] = "blocked"
        elif mutation == "freshness-disagreement":
            panel["sync"]["business_freshness"] = "stale"
        elif mutation == "selection-omitted":
            panel["sync"]["action_projection"]["selected_workstreams"] = []
            panel["sync"]["action_projection"]["workstreams"] = []
        elif mutation == "selection-policy-mismatch":
            panel["sync"]["audit"]["selection_policy_id"] = "sha256:" + "e" * 64
        elif mutation == "stale-v1-visible":
            panel["model_v1"]["data"]["status"]["progress"]["overall"]["forecast_summary"] = "stale-but-schema-valid"
        elif mutation == "current-fields-live":
            upstreams["program-status"]["workstream_current"][0].update({"progress": "LATEST CURRENT PROGRESS", "blockers": ["LATEST BLOCKER"], "risks": ["LATEST RISK"]})
            panel["sync"]["canonical"]["status"]["workstream_current"] = copy.deepcopy(upstreams["program-status"]["workstream_current"])
        elif mutation == "program-status-overlay-mismatch":
            upstreams["program-status"]["overall_status"] = "latest-status"
            panel["sync"]["canonical"]["status"]["overall_status"] = "latest-status"
        elif mutation == "same-generation-upstream-mismatch":
            upstreams["program-status"]["workstream_current"][0]["progress"] = "NEW SAME-GENERATION VALUE"
        elif mutation == "omit-v1-history":
            panel["model_v1"]["data"].pop("history")
        elif mutation == "omit-v1-board":
            panel["model_v1"]["data"]["meetings"]["fde-morning"]["boards"].pop("fde_period_delta")
        elif mutation == "invalid-outer:management-panel":
            panel.pop("sync")
        if "sync" in panel:
            panel["panel_id"] = sha256_bytes(canonical_bytes({key: value for key, value in panel.items() if key != "panel_id"}))
        nested_ok = True
        for binding in [row for row in registry["nested_payload_bindings"] if row["projection_kind"] == "program-status"]:
            nested_schema = json.loads((project_root / binding["schema_path"]).read_text(encoding="utf-8"))
            try:
                nested_value = json_pointer(upstreams[binding["projection_kind"]], binding["payload_pointer"])
                nested_ok = nested_ok and validate_document(nested_value, nested_schema)
            except (KeyError, IndexError, TypeError):
                nested_ok = False
        panel_schema_valid = validate(panel, schema, "managementPanelPayloadV2")
        compatibility_ok = panel_schema_valid and panel_v1_compatibility_valid(panel, compatibility, project_root)
        composition_ok = panel_schema_valid and panel_v1_composition_valid(panel, registry, project_root)
        current_ok = panel_schema_valid and panel["sync"]["canonical"]["status"]["workstream_current"] == upstreams["program-status"]["workstream_current"]
        if panel_schema_valid:
            read_mutation = (
                ("program-status", "drop-one-declared-read") if mutation == "lineage-missing-read"
                else (("program-status", "add-undeclared-read") if mutation == "lineage-extra-read"
                else (("action-projection-drift-verdict", "drop-action-ledger-state") if mutation == "drift-ledger-state-missing-read"
                else (("action-projection-drift-verdict", "add-undeclared-ledger-state-read") if mutation == "drift-ledger-state-extra-read" else None)))
            )
            built, outer_ok = build_projection_lineage(panel, upstreams, registry, schema, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], project_root, policy, read_mutation)
            if mutation == "payload-hash-mismatch":
                built["program-status"][0]["envelope"]["payload_sha256"] = "sha256:" + "f" * 64
            elif mutation == "generation-mismatch":
                built["roadmap"][0]["envelope"]["generation_id"] = "sha256:" + "f" * 64
            elif mutation == "manifest-receipt-mismatch":
                built["meeting-pack"][0]["receipt"]["output"]["manifest_id"] = "sha256:" + "f" * 64
            elif mutation == "omit-state-audit-producer":
                built["state-audit"] = []
            lineage_ok = outer_ok and projection_lineage_semantics(
                built, registry, schema, generation, policy, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"]
            )
        else:
            outer_ok = False
            lineage_ok = False
        publication_ok = panel_schema_valid and publication_eligibility_semantics(
            panel, physical_inventory, policy, generation, registry, schema,
            substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], built if panel_schema_valid else None,
        )
        binding_ok = panel_schema_valid and panel_binding_semantics(panel, built, registry, policy, generation)
        if mutation in {"selected-drift", "selected-missing", "selected-malformed", "blocked-audit", "freshness-disagreement", "selection-omitted", "selection-policy-mismatch", "all-catalog-subset", "inventory-catalog-omission", "catalog-extra-row", "duplicate-physical-identity", "empty-all", "uncataloged-generation-pair", "generation-wdr-without-sidecar", "generation-sidecar-without-wdr", "panel-catalog-id", "physical-attestation-fact-generation", "physical-attestation-root", "physical-attestation-workstreams-omitted", "physical-attestation-missing", "physical-attestation-contract-hash", "selection-policy-contract-hash", "generation-contract-hash", "panel-embedded-contract-hash"}:
            valid = panel_schema_valid and not publication_ok
        elif mutation in {"omit-v1-history", "omit-v1-board"}:
            valid = panel_schema_valid and not compatibility_ok
        elif mutation in {"stale-v1-visible", "program-status-overlay-mismatch"}:
            valid = panel_schema_valid and not composition_ok
        elif mutation == "same-generation-upstream-mismatch":
            valid = panel_schema_valid and lineage_ok and not binding_ok
        elif mutation == "omit-state-audit-producer":
            valid = panel_schema_valid and not lineage_ok and not binding_ok
        elif mutation == "source-as-of-mismatch":
            valid = panel_schema_valid and not outer_ok and not publication_ok
        elif mutation.startswith("invalid-outer:"):
            valid = not outer_ok
        elif mutation in {"payload-hash-mismatch", "generation-mismatch", "manifest-receipt-mismatch", "lineage-missing-read", "lineage-extra-read", "drift-ledger-state-missing-read", "drift-ledger-state-extra-read"}:
            valid = outer_ok and not lineage_ok
        else:
            valid = panel_schema_valid and nested_ok and compatibility_ok and composition_ok and current_ok and publication_ok and lineage_ok and binding_ok
        check(item["id"], valid)

    for item in suite["panel_v1_composition_vectors"]:
        panel, upstreams, _, _, _ = panel_fixture(suite["contract_schema_vectors"], registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], project_root)
        for binding in registry["panel_binding_map"]:
            payload = upstreams[binding["projection_kind"]]
            value = {row["scenario"]: copy.deepcopy(row) for row in payload} if binding["merge_mode"] == "object-by-key" else copy.deepcopy(payload)
            set_pointer(panel, binding["panel_pointer"], value)
        if item["mutation"] == "current-fields-live":
            upstreams["program-status"]["workstream_current"][0].update({"progress": "LATEST CURRENT PROGRESS", "blockers": ["LATEST BLOCKER"], "risks": ["LATEST RISK"]})
            panel["sync"]["canonical"]["status"]["workstream_current"] = copy.deepcopy(upstreams["program-status"]["workstream_current"])
        elif item["mutation"] == "program-status-overlay-mismatch":
            upstreams["program-status"]["overall_status"] = "latest-status"
            panel["sync"]["canonical"]["status"]["overall_status"] = "latest-status"
        elif item["mutation"] == "stale-v1-visible":
            panel["model_v1"]["data"]["status"]["progress"]["overall"]["forecast_summary"] = "stale-but-schema-valid"
        valid = panel_v1_composition_valid(panel, registry, project_root)
        check(item["id"], valid if item["expected"] == "byte-exact" else not valid)

    for item in suite["panel_v2_consumer_vectors"]:
        panel, upstreams, _, _, _ = panel_fixture(suite["contract_schema_vectors"], registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], project_root)
        for binding in registry["panel_binding_map"]:
            payload = upstreams[binding["projection_kind"]]
            value = {row["scenario"]: copy.deepcopy(row) for row in payload} if binding["merge_mode"] == "object-by-key" else copy.deepcopy(payload)
            set_pointer(panel, binding["panel_pointer"], value)
        panel["panel_id"] = sha256_bytes(canonical_bytes({key: value for key, value in panel.items() if key != "panel_id"}))
        baseline_model = canonical_bytes(panel["model_v1"])
        baseline_view = execute_panel_v2_consumer(panel, registry, schema, project_root)
        if item["mutation"] == "current-fields-live":
            panel["sync"]["canonical"]["status"]["workstream_current"][0].update({"progress": "LATEST CURRENT PROGRESS", "blockers": ["LATEST BLOCKER"], "risks": ["LATEST RISK"]})
        elif item["mutation"] == "legacy-model-only":
            panel["model_v1"]["data"]["status"]["progress"]["overall"]["forecast_summary"] = "legacy-only-change"
        elif item["mutation"] == "missing-current-field":
            panel["sync"]["canonical"]["status"]["workstream_current"][0].pop("progress")
        elif item["mutation"] == "duplicate-row":
            panel["sync"]["canonical"]["status"]["workstream_current"].append(copy.deepcopy(panel["sync"]["canonical"]["status"]["workstream_current"][0]))
        elif item["mutation"] == "non-nfc-row":
            panel["sync"]["canonical"]["status"]["workstream_current"][0]["progress"] = "e\u0301"
        elif item["mutation"] == "normalized-collision":
            extra = copy.deepcopy(panel["sync"]["canonical"]["status"]["workstream_current"][0])
            panel["sync"]["canonical"]["status"]["workstream_current"][0]["workstream_id"] = "\u00e9"
            extra["workstream_id"] = "e\u0301"
            panel["sync"]["canonical"]["status"]["workstream_current"].append(extra)
        elif item["mutation"] == "html-metacharacters":
            panel["sync"]["canonical"]["status"]["workstream_current"][0].update({
                "progress": "A&B <C> \"D\" 'E'", "blockers": ["<blocked>"], "risks": ["R&D"],
            })
        panel["panel_id"] = sha256_bytes(canonical_bytes({key: value for key, value in panel.items() if key != "panel_id"}))
        current_view = execute_panel_v2_consumer(panel, registry, schema, project_root)
        if item.get("expected") == "valid":
            valid = baseline_view is not None and current_view == baseline_view
        elif item.get("expected") == "visible-change":
            valid = baseline_view is not None and current_view is not None and baseline_view["html"] != current_view["html"] and "LATEST CURRENT PROGRESS" in current_view["html"] and canonical_bytes(panel["model_v1"]) == baseline_model
        elif item.get("expected") == "current-view-unchanged":
            valid = baseline_view is not None and current_view is not None and baseline_view["rows"] == current_view["rows"] and baseline_view["html"] == current_view["html"] and "legacy-only-change" not in current_view["html"]
        elif item.get("expected") == "escaped":
            valid = (
                current_view is not None and "A&amp;B &lt;C&gt; &quot;D&quot; &#39;E&#39;" in current_view["html"]
                and "&lt;blocked&gt;" in current_view["html"] and "R&amp;D" in current_view["html"]
            )
        else:
            valid = current_view is None and item["expected_error"] == "PANEL_V2_CONSUMER_INVALID"
        check(item["id"], valid)

    for item in suite["panel_publication_vectors"]:
        panel, upstreams, compatibility, policy, generation = panel_fixture(suite["contract_schema_vectors"], registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], project_root)
        for binding in registry["panel_binding_map"]:
            payload = upstreams[binding["projection_kind"]]
            set_pointer(panel, binding["panel_pointer"], {row["scenario"]: row for row in payload} if binding["merge_mode"] == "object-by-key" else payload)
        panel["panel_id"] = sha256_bytes(canonical_bytes({key: value for key, value in panel.items() if key != "panel_id"}))
        built, outer_ok = build_projection_lineage(panel, upstreams, registry, schema, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], project_root, policy)
        lineage_ok = outer_ok and projection_lineage_semantics(
            built, registry, schema, generation, policy, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"]
        )
        graph = panel_publication_fixture(panel, built, policy, generation, registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], item["mutation"])
        valid = lineage_ok and panel_publication_semantics(
            graph, registry, schema, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"]
        )
        if item["mutation"] == "first-publication-idempotent":
            replay = panel_publication_fixture(
                panel, built, policy, generation, registry,
                substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], item["mutation"],
            )
            valid = valid and graph == replay
        check(item["id"], valid if item.get("expected") == "valid" else (not valid and item["expected_error"] == "PANEL_PUBLICATION_GRAPH_INVALID"))

    for item in suite["nested_payload_vectors"]:
        binding = next(row for row in registry["nested_payload_bindings"] if row["projection_kind"] == item["projection_kind"] and row["payload_pointer"] == item["payload_pointer"])
        nested_schema = json.loads((project_root / binding["schema_path"]).read_text(encoding="utf-8"))
        instance = json.loads((project_root / item["fixture_path"]).read_text(encoding="utf-8")) if "fixture_path" in item else item["instance"]
        check(item["id"], validate_document(instance, nested_schema) and canonical_bytes(instance) == canonical_bytes(json.loads(canonical_bytes(instance))))

    expected_ids = vector_ids(suite)
    release_seeds = {
        "fixture-posix-ci": bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"),
        "fixture-windows-ci": bytes.fromhex("1f1e1d1c1b1a191817161514131211100f0e0d0c0b0a09080706050403020100"),
    }
    release_registry = design_release_registry_fixture(registry)
    release_registry_sha = sha256_bytes(canonical_bytes(release_registry))
    release_hashes = {**actual_hashes, "registry": release_registry_sha}

    def resign_release_receipt(receipt: dict[str, Any], signer_key: str | None = None) -> None:
        receipt.pop("result_id", None)
        key_id = signer_key or receipt["provenance"]["signer_key_id"]
        receipt["provenance"]["signature"] = base64.b64encode(ed25519_sign(release_seeds[key_id], _conformance_signing_payload(receipt))).decode("ascii")
        receipt["result_id"] = sha256_bytes(canonical_bytes(receipt))

    for item in suite["release_gate_vectors"]:
        mutation = item["mutation"]
        vector_registry = registry if mutation == "production-trust-unprovisioned" else release_registry
        vector_hashes = actual_hashes if mutation == "production-trust-unprovisioned" else release_hashes
        receipts, evidence_blobs = implementation_conformance_receipts(expected_ids, vector_hashes, vector_registry)
        if mutation == "passed-subset":
            receipts[0]["passed_vector_ids"] = expected_ids[:-1]; resign_release_receipt(receipts[0])
        elif mutation == "duplicate-implementation":
            receipts[1]["implementation_id"] = receipts[0]["implementation_id"]; resign_release_receipt(receipts[1])
        elif mutation == "duplicate-build":
            receipts[1]["adapter_build_id"] = receipts[0]["adapter_build_id"]
            receipts[1]["runtime"]["build_digest"] = receipts[0]["adapter_build_id"]; resign_release_receipt(receipts[1])
        elif mutation == "platform-substitution":
            receipts[0]["platform"] = "native-windows"; resign_release_receipt(receipts[0])
        elif mutation == "evidence-class-omission":
            receipts[0]["evidence_classes"] = ["production-adapter"]; resign_release_receipt(receipts[0])
        elif mutation == "extra-vector":
            receipts[0]["passed_vector_ids"].append("not-in-suite"); resign_release_receipt(receipts[0])
        elif mutation == "artifact-hash":
            receipts[0]["schema_sha256"] = "sha256:" + "f" * 64; resign_release_receipt(receipts[0])
        elif mutation == "result-id":
            receipts[0]["result_id"] = "sha256:" + "f" * 64
        elif mutation == "unknown-signer":
            receipts[0]["provenance"]["signer_key_id"] = "unknown-ci-key"
            receipts[0]["result_id"] = sha256_bytes(canonical_bytes({key: value for key, value in receipts[0].items() if key != "result_id"}))
        elif mutation == "signature-tamper":
            receipts[0]["provenance"]["signature"] = "A" * 86 + "=="
            receipts[0]["result_id"] = sha256_bytes(canonical_bytes({key: value for key, value in receipts[0].items() if key != "result_id"}))
        elif mutation == "log-tamper":
            blob_id = receipts[0]["provenance"]["test_log_sha256"]; evidence_blobs[blob_id] += b"tampered"
        elif mutation == "replay":
            replay = copy.deepcopy(receipts[0])
            replay["implementation_id"] = "python-production-adapter-replay"
            replay["adapter_build_id"] = sha256_bytes(b"python-production-build-replay")
            replay["runtime"]["build_digest"] = replay["adapter_build_id"]
            resign_release_receipt(replay, "fixture-posix-ci"); receipts.append(replay)
        elif mutation == "build-mismatch":
            receipts[0]["runtime"]["build_digest"] = "sha256:" + "f" * 64; resign_release_receipt(receipts[0])
        elif mutation == "runtime-3.9":
            receipts[0]["runtime"]["version"] = "3.9.0"; resign_release_receipt(receipts[0])
        elif mutation == "runtime-node-18":
            receipts[1]["runtime"]["version"] = "18.0.0"; resign_release_receipt(receipts[1])
        elif mutation == "runtime-node-20":
            receipts[1]["runtime"]["version"] = "20.0.0"; resign_release_receipt(receipts[1])
        elif mutation == "runtime-node-21":
            receipts[1]["runtime"]["version"] = "21.0.0"; resign_release_receipt(receipts[1])
        elif mutation == "runtime-node-23":
            receipts[1]["runtime"]["version"] = "23.0.0"; resign_release_receipt(receipts[1])
        elif mutation == "runtime-node-24":
            receipts[1]["runtime"]["version"] = "24.0.0"; resign_release_receipt(receipts[1])
        elif mutation == "runtime-node-25":
            receipts[1]["runtime"]["version"] = "25.0.0"; resign_release_receipt(receipts[1])
        elif mutation.startswith("lock-"):
            lock = receipts[0]["lock_evidence"]
            field_by_mutation = {
                "lock-contention": "multiprocess_contention_passed", "lock-crash-release": "crash_release_passed",
                "lock-order": "order_passed", "lock-timeout": "timeout_passed", "lock-upgrade": "upgrade_rejected",
            }
            if mutation in field_by_mutation:
                lock[field_by_mutation[mutation]] = False; resign_release_receipt(receipts[0])
            elif mutation == "lock-primitive":
                lock["primitive"] = "windows-lockfileex"; resign_release_receipt(receipts[0])
            elif mutation == "lock-profile":
                lock["lock_profile_id"] = "sha256:" + "f" * 64; resign_release_receipt(receipts[0])
            else:
                del evidence_blobs[lock["evidence_log_sha256"]]
        accepted = all(validate(row, schema, "conformanceResultV1") for row in receipts) and release_gate_accepts(
            receipts, expected_ids, vector_hashes, vector_registry, evidence_blobs,
            {
                "clock_source": "host-secure-clock-v1",
                "evaluation_time": item.get(
                    "evaluation_time",
                    "2026-09-01T00:00:00Z" if mutation == "python-review-deadline" else "2026-07-24T03:05:00Z",
                ),
                "available": item.get("clock_available", True),
            },
        )
        check(item["id"], accepted if item.get("expected") == "accepted" else (not accepted and item["expected_error"] == "CONFORMANCE_EVIDENCE_INCOMPLETE"))

    for item in suite["repair_vectors"]:
        vector_id = item["id"]
        if vector_id == "cross-field-action-set-valid":
            check(vector_id, item["finding_action_ids"] == item["command_action_ids"] == item["read_set_action_ids"])
        elif vector_id == "cross-field-action-set-mismatch":
            check(vector_id, item["finding_action_ids"] != item["command_action_ids"] and item["expected_error"] == "REPAIR_BATCH_INVALID")
        elif vector_id == "orphan-action-expected-absent":
            record = item["read_records"][0]
            check(vector_id, record["expected_present"] is False and record["revision"] is None and item["finding_action_ids"] == item["command_action_ids"] == [record["action_id"]])
        elif vector_id == "duplicate-action-read-record-rejected":
            ids = [row["action_id"] for row in item["read_records"]]
            check(vector_id, len(ids) != len(set(ids)) and item["expected_error"] == "REPAIR_BATCH_INVALID")
        elif vector_id == "repair-sort-key-vs-group-key":
            ordered = sorted(item["findings"], key=lambda row: (row["workflow"], row["workstream_id"], row["operation"], row["finding_id"]))
            groups = {(row["workflow"], row["workstream_id"], row["operation"]) for row in ordered}
            check(vector_id, len(groups) == item["expected_group_count"] and [row["finding_id"] for row in ordered] == item["expected_finding_order"])
        elif vector_id == "nonce-reserve-consume-cas":
            check(vector_id, item["events"] == ["unused", "reserved", "consumed"] and item["replay_from"] == "consumed" and item["expected_error"] == "REPAIR_TOKEN_REPLAY")
        elif vector_id.startswith("repair-graph-"):
            mutation = item["mutation"]
            fixture_outcome = item.get("fixture_outcome", "blocked" if mutation == "blocked" else ("rolled-back" if mutation == "rolled-back" else ("orphan" if mutation == "orphan-null-revision" else "committed")))
            graph = repair_graph_fixture(
                substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], registry, fixture_outcome
            )
            repair_runtime = runtime_authority_fixture(
                registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"], "adp-status-sync",
            )
            batch = next(row for row in graph["audit"]["repair_batches"] if row["batch_id"] == graph["dry_request"]["batch"]["batch_id"])
            finding = next(row for row in graph["audit"]["findings"] if row["repair_batch_id"] == batch["batch_id"])
            if mutation == "dangling-finding-batch":
                finding["repair_batch_id"] = "sha256:" + "f" * 64
            elif mutation == "batch-omits-finding":
                batch["finding_ids"] = ["other-finding"]
            elif mutation == "audit-mismatch":
                batch["based_on_audit_id"] = "sha256:" + "f" * 64
            elif mutation == "action-union-mismatch":
                batch["command"]["action_ids"] = ["A-FLOW-OTHER"]
            elif mutation == "duplicate-source":
                batch["read_set"]["source_records"].append(copy.deepcopy(batch["read_set"]["source_records"][0]))
            elif mutation == "duplicate-wdr":
                batch["read_set"]["wdr_revisions"].append(copy.deepcopy(batch["read_set"]["wdr_revisions"][0]))
            elif mutation == "wdr-revision-mismatch":
                batch["command"]["expected_wdr_revision"] = 99
            elif mutation == "cross-batch-token":
                graph["nonce_states"][-1]["batch_id"] = "sha256:" + "f" * 64
            elif mutation == "binding-digest-mismatch":
                graph["dry_result"]["binding_digest"] = "sha256:" + "f" * 64
            elif mutation == "recomputed-substitution":
                request_batch = graph["dry_request"]["batch"]
                request_batch["read_set"]["ledger_fingerprint"] = "sha256:" + "e" * 64
                core = {key: request_batch[key] for key in ("based_on_audit_id", "finding_ids", "command", "read_set")}
                request_batch["batch_digest"] = sha256_bytes(canonical_bytes(core))
                identity = {"workflow": request_batch["command"]["workflow"], "workstream_id": request_batch["command"]["workstream_id"], "operation": request_batch["command"]["operation"], "finding_ids": request_batch["finding_ids"], "batch_digest": request_batch["batch_digest"]}
                request_batch["batch_id"] = sha256_bytes(canonical_bytes(identity))
                graph["dry_result"]["dry_run_id"] = sha256_bytes(canonical_bytes(graph["dry_request"]))
                graph["dry_result"]["batch_id"] = request_batch["batch_id"]
                graph["dry_result"]["binding_digest"] = sha256_bytes(canonical_bytes(repair_binding_input(graph["dry_request"], graph["audit"]["audit_id"], "applicable", substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"])))
            elif mutation == "overlong-expiry":
                graph["dry_result"]["expires_at"] = "2026-07-24T02:15:01Z"
            elif mutation == "expired-apply":
                graph["apply_request"]["applied_at"] = "2026-07-24T02:15:01Z"
            elif mutation == "invalid-nonce-transition":
                graph["nonce_states"][-1]["previous_state_id"] = graph["nonce_states"][0]["state_id"]
            elif mutation == "journal-nonce-mismatch":
                next(row for row in graph["journal"]["targets"] if row["role"] == "nonce")["after_sha256"] = "sha256:" + "f" * 64
            elif mutation == "nonce-path-substitution":
                next(row for row in graph["journal"]["targets"] if row["role"] == "nonce")["path"] = "state/nonces/substituted.json"
            elif mutation == "journal-fact-receipt-mismatch":
                [row for row in graph["journal"]["targets"] if row["role"] == "receipt"][0]["after_sha256"] = "sha256:" + "f" * 64
            elif mutation == "repair-receipt-mismatch":
                graph["repair_receipt"]["fact_receipt_id"] = "sha256:" + "f" * 64
            elif mutation == "noncommitted-marker":
                graph["marker"]["state"] = "prepared"
                graph["marker"]["marker_id"] = sha256_bytes(canonical_bytes({key: value for key, value in graph["marker"].items() if key != "marker_id"}))
            elif mutation == "scope-non-nfc":
                graph["dry_request"]["authorization_scopes"] = ["repair:e\u0301"]
            elif mutation == "scope-nfc-collision":
                graph["dry_request"]["authorization_scopes"] = ["repair:e\u0301", "repair:\u00e9"]
            elif mutation == "finding-action-ref-missing":
                finding["entity_refs"] = []
            elif mutation == "finding-action-ref-extra":
                finding["entity_refs"].append({"entity_type": "action", "id": "A-EXTRA-1"})
            elif mutation == "finding-action-ref-duplicate":
                finding["entity_refs"].append(copy.deepcopy(finding["entity_refs"][0]))
            elif mutation == "audit-contract-hash":
                graph["audit"]["contract"]["registry_sha256"] = "sha256:" + "f" * 64
            elif mutation == "fact-receipt-contract-hash" and graph.get("fact_receipt") is not None:
                graph["fact_receipt"]["contract"]["schema_sha256"] = "sha256:" + "f" * 64
            elif mutation == "split-group":
                duplicate = copy.deepcopy(batch)
                duplicate["batch_id"] = "sha256:" + "e" * 64
                graph["audit"]["repair_batches"].append(duplicate)
            elif mutation == "overlapping-batches":
                other = next(row for row in graph["audit"]["repair_batches"] if row["batch_id"] != batch["batch_id"])
                other["finding_ids"].append(finding["finding_id"])
            elif mutation == "orphan-batch":
                orphan = copy.deepcopy(batch)
                orphan["batch_id"] = "sha256:" + "e" * 64
                orphan["command"]["workstream_id"] = "l1-orphan"
                graph["audit"]["repair_batches"].append(orphan)
            elif mutation == "group-mismatch":
                batch["command"]["workstream_id"] = "l1-other"
            elif mutation == "blocked-without-batch":
                finding["repair_batch_id"] = None
            elif mutation == "absent-claim-present-row":
                batch["read_set"]["action_revisions"][0].update({"expected_present": False, "revision": None})
            elif mutation == "wrong-ledger-revision":
                batch["read_set"]["action_revisions"][0]["revision"] += 1
            elif mutation == "invented-drift-action":
                drift_row = next(row for row in graph["drift_verdict"]["workstreams"] if row["workstream_id"] == batch["command"]["workstream_id"])
                drift_row["action_diffs"][0]["action_id"] = "A-INVENTED-1"
            elif mutation == "attempt-transaction-id":
                graph["attempt_journal"]["transaction_id"] = "repair-attempt:substituted"
            elif mutation == "business-marker-binding":
                graph["repair_receipt"]["business_marker_sha256"] = "sha256:" + "f" * 64
            elif mutation == "recovery-binding":
                graph["repair_receipt"]["recovery_receipt_sha256"] = "sha256:" + "f" * 64
            valid = repair_graph_semantics(
                graph, schema, registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"],
                *repair_runtime,
            )
            check(vector_id, valid if item.get("expected") == "valid" else (not valid and item["expected_error"] in {"REPAIR_BATCH_INVALID", "REPAIR_TOKEN_INVALID", "REPAIR_TRANSACTION_INVALID"}))
        elif vector_id == "repair-two-batches-cas-partial-retry":
            check(
                vector_id,
                item.get("execution") == "complete-wire-graphs-with-restart"
                and two_batch_repair_restart_semantics(
                    schema, registry, substitutions["$SCHEMA_SHA256"], substitutions["$REGISTRY_SHA256"],
                ),
            )
        else:
            used: set[str] = set()
            committed: list[str] = []
            valid = True
            for event in item["events"]:
                if event["event"] != "apply":
                    continue
                valid = valid and event["token"] not in used
                used.add(event["token"])
                if event["outcome"] == "committed" and event["batch"] not in committed:
                    committed.append(event["batch"])
            check(vector_id, valid and committed == item["expected_committed_batches"] and item["expected_reused_tokens"] == [])

    for item in suite["refresh_vectors"]:
        if item["id"].startswith("live-inspect-"):
            vector_registry = registry if item["mutation"] == "pending-registry" else strict_registry
            vector_registry_sha = substitutions["$REGISTRY_SHA256"] if item["mutation"] == "pending-registry" else strict_registry_sha
            vector_hashes = actual_hashes if item["mutation"] == "pending-registry" else strict_hashes
            package = live_inspect_fixture(
                suite if item["mutation"] == "pending-registry" else strict_suite,
                vector_registry, schema, substitutions["$SCHEMA_SHA256"], vector_registry_sha,
                project_root, expected_ids, vector_hashes,
            )
            mutation = item["mutation"]
            if mutation in {"source-drift", "source-unreadable", "missing-leaf"}:
                fixed_paths = {
                    value["path"] for value in vector_registry["runtime_paths"].values()
                    if isinstance(value, dict) and "path" in value and "{" not in value["path"]
                }
                fixed_paths.update(
                    path
                    for row in package["documents"]["workstreams"]
                    for path in (
                        row["record_path"],
                        f"workstreams/{row['state']['workstream_id']}/delivery-record.state.json",
                        f"workstreams/{row['state']['workstream_id']}/action-projection.json",
                    )
                )
                leaf_key = next(
                    key for key in package["live_leaf_store"]
                    if key.split("\0", 1)[1] not in fixed_paths
                )
                if mutation == "source-drift":
                    package["live_leaf_store"][leaf_key] += b"\n"
                elif mutation == "source-unreadable":
                    package["live_leaf_store"][leaf_key] = None
                else:
                    del package["live_leaf_store"][leaf_key]
            elif mutation == "fact-generation-drift":
                package["documents"]["fact_state"]["fact_generation"] += 1
                rebind_identity(package["documents"]["fact_state"], "state_id")
            elif mutation == "lock-unavailable":
                package["fact_read_lock"]["acquired"] = False
            elif mutation == "activation-rollback":
                activation = package["documents"]["activation_state"]
                activation["activation_epoch"] += 1
                activation["mode"] = "legacy"
                activation["attestation_id"] = None
                rebind_identity(activation, "state_id")
            elif mutation == "activation-epoch":
                package["documents"]["activation_state"]["activation_epoch"] += 1
                rebind_identity(package["documents"]["activation_state"], "state_id")
            elif mutation == "capability-epoch":
                package["documents"]["capability_registry"]["capability_epoch"] += 1
                rebind_identity(package["documents"]["capability_registry"], "capability_registry_id")
            elif mutation == "attestation-replacement":
                package["attestation"]["attested_at"] = "2026-07-24T03:00:04Z"
                rebind_writer_fence_attestation(package)
            elif mutation == "stale-activation-snapshot":
                attestation = package["attestation"]
                attestation["fact_generation"] -= 1
                attestation["ledger"]["ledger_fingerprint"] = "sha256:" + "e" * 64
                attestation["workstreams"][0]["wdr_fingerprint"] = "sha256:" + "d" * 64
                attestation["published_generation_id"] = "sha256:" + "c" * 64
                attestation["current_pointer_id"] = "sha256:" + "b" * 64
                attestation["lineage_index_id"] = "sha256:" + "a" * 64
                attestation["lineage_index_path"] = "views/generations/activation-baseline/index.json"
                rebind_attestation_activation(package)
            elif mutation == "writer-build-change":
                artifact_path = vector_registry["strict_rollout"]["writer_specs"][0]["artifact_paths"][0]
                package["writer_store"][artifact_path] += b"\nchanged-after-activation"
            elif mutation == "design-only-evidence":
                package["release_store"] = {}
            elif mutation == "root-registry-substitution":
                roots = package["documents"]["root_registry"]
                next(row for row in roots["roots"] if row["role"] == "memory")["root_instance_id"] = "123e4567-e89b-42d3-a456-426614174099"
                rebind_identity(roots, "registry_state_id")
            elif mutation == "ledger-substitution":
                package["documents"]["ledger_raw"] += b"\nsubstituted"
            elif mutation == "wdr-substitution":
                package["documents"]["workstreams"][0]["wdr_raw"] += b"\nsubstituted"
            elif mutation == "sidecar-substitution":
                package["documents"]["workstreams"][0]["sidecar"]["ledger_revision"] += 1
            elif mutation in {"refresh-receipt-substitution", "publication-receipt-substitution"}:
                index = json.loads(package["lineage_store"][package["attestation"]["lineage_index_path"]])
                if mutation == "refresh-receipt-substitution":
                    target = next(row for row in index["objects"] if row["object_kind"] == "refresh-receipt")
                    target_path = target["path"]
                else:
                    journal_path = runtime_path(vector_registry, "publication_journal_template", generation_id=index["generation_id"])
                    journal = json.loads(package["lineage_store"][journal_path])
                    target_path = next(row["path"] for row in journal["targets"] if row["role"] == "receipt")
                package["lineage_store"][target_path] += b"\n"
            elif mutation == "read-set-extra-writer":
                package["inspect_read_set_additions"].append({
                    "root": "project", "path": "skills/unregistered-writer.py", "contract_name": "raw/writer-artifact",
                })
            elif mutation in {"omit-one", "duplicate", "wrong-root", "alias", "unconsumed"}:
                package["inspect_read_mutation"] = mutation
            elif mutation in {"lineage-root-instance-substitution", "lineage-cardinality-substitution"}:
                index_path = package["attestation"]["lineage_index_path"]
                index = json.loads(package["lineage_store"][index_path])
                target = next(row for row in index["objects"] if row["object_kind"] == "selection-policy")
                if mutation == "lineage-root-instance-substitution":
                    target["root_instance_id"] = "123e4567-e89b-42d3-a456-426614174099"
                else:
                    target["cardinality"] = "many"
                rebind_identity(index, "index_id")
                package["lineage_store"][index_path] = canonical_bytes(index)
                package["attestation"]["lineage_index_id"] = index["index_id"]
                rebind_attestation_activation(package)
            elif mutation == "lineage-index-missing":
                del package["lineage_store"][package["attestation"]["lineage_index_path"]]
            elif mutation == "lineage-object-missing":
                index = json.loads(package["lineage_store"][package["attestation"]["lineage_index_path"]])
                del package["lineage_store"][index["objects"][0]["path"]]
            elif mutation == "panel-byte-tampered":
                index = json.loads(package["lineage_store"][package["attestation"]["lineage_index_path"]])
                target = next(row for row in index["objects"] if row["object_kind"] == "projection-envelope" and row["projection_kind"] == "management-panel")
                package["lineage_store"][target["path"]] += b"\n"
            elif mutation == "pointer-byte-tampered":
                package["lineage_store"][vector_registry["runtime_paths"]["panel_current_pointer"]["path"]] += b"\n"
            elif mutation == "extra-leaf":
                package["live_leaf_store"]["123e4567-e89b-42d3-a456-426614174000\0unexpected/source.md"] = b"unexpected"
            elif mutation == "extra-write":
                package["inspect_write_paths"].append("state/unexpected.json")
            status = live_inspect_semantics(
                package, vector_registry, schema, substitutions["$SCHEMA_SHA256"], vector_registry_sha, expected_ids, vector_hashes,
                {"clock_source": "host-secure-clock-v1", "evaluation_time": package["inspected_at"], "available": item.get("clock_available", True)},
            )
            if item.get("expected_error") == "LIVE_INSPECT_INVALID":
                check(item["id"], status is None)
            else:
                verdict = status["latest_inspect"] if status else {}
                check(
                    item["id"],
                    status is not None
                    and verdict.get("outcome") == item["expected_outcome"]
                    and verdict.get("error_code") == item["expected_error"]
                    and package["inspect_write_paths"] == [vector_registry["runtime_paths"]["panel_refresh_status"]["path"]],
                )
        elif item["id"] == "producer-blocked-has-no-output":
            check(item["id"], item["status"] == "blocked" and item["output"] is None and bool(item["error_code"]))
        elif item["id"] == "dirty-run-retry-cursor":
            blocked = next(row for row in item["nodes"] if row["disposition"] == "blocked")
            check(item["id"], item["status"] == "dirty" and blocked["output"] is None and item["retry_from_instance_key"] == blocked["instance_key"])
        else:
            check(item["id"], False)

    for item in suite["platform_vectors"]:
        if item["id"] == "posix-symlink" and os.name == "posix":
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "target"
                target.write_text("x")
                link = Path(folder) / "link"
                link.symlink_to(target)
                check(item["id"], link.is_symlink() and item["expected_error"] == "DEPENDENCY_PATH_UNSAFE")
        else:
            check(item["id"], item.get("expected_error") in {"DEPENDENCY_PATH_UNSAFE", "DURABILITY_UNAVAILABLE"})

    expected = expected_ids
    if sorted(passed + failed) != expected:
        failed.append("suite-vector-accounting")
    return sorted(passed), sorted(set(failed))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--platform", choices=["posix-design-model", "windows-design-model"], required=True)
    parser.add_argument("--executed-at", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    paths = {name: Path(getattr(args, name)) for name in ("suite", "schema", "protocol", "registry")}
    registry = json.loads(paths["registry"].read_text(encoding="utf-8"))
    schema = json.loads(paths["schema"].read_text(encoding="utf-8"))
    suite = json.loads(paths["suite"].read_text(encoding="utf-8"))
    actual_hashes = {name: sha256_bytes(path.read_bytes()) for name, path in paths.items()}
    if registry["schema_bundle"]["sha256"] != actual_hashes["schema"] or registry["protocol"]["sha256"] != actual_hashes["protocol"] or registry["conformance_suite"]["sha256"] != actual_hashes["suite"]:
        raise SystemExit("registry artifact hash mismatch")
    release_validator = registry["conformance_suite"]["release_gate_validator"]
    if release_validator["id"] != "conformance-release-gate/1.0.0" or release_validator["protocol_sha256"] != actual_hashes["protocol"]:
        raise SystemExit("release gate validator pin mismatch")
    for contract in registry["contracts"]:
        definition = contract["schema_pointer"].removeprefix("#/$defs/")
        target = schema["$defs"].get(definition)
        if not target or target.get("$anchor") != contract["schema_id"].rsplit("#", 1)[1]:
            raise SystemExit(f"registry pointer/anchor mismatch: {contract['name']}")
    project_root = Path(args.project_root)
    document_workspace = paths["registry"].resolve().parent.parent
    for artifact in registry["pinned_source_artifacts"]:
        if sha256_bytes((project_root / artifact["path"]).read_bytes()) != artifact["sha256"]:
            raise SystemExit(f"pinned source hash mismatch: {artifact['id']}")
    profile_kinds = {profile["projection"] for profile in registry["projection_input_profiles"]}
    binding_kinds = {binding["projection_kind"] for binding in registry["projection_payload_bindings"]}
    envelope_kinds = set(schema["$defs"]["canonicalProjectionEnvelopeV1"]["properties"]["projection_kind"]["enum"])
    if profile_kinds != binding_kinds or profile_kinds != envelope_kinds:
        raise SystemExit("profile/payload-binding/envelope projection kind mismatch")
    for binding in registry["projection_payload_bindings"]:
        if binding["schema_root"] not in {"document-workspace", "project"}:
            raise SystemExit(f"invalid payload schema root: {binding['projection_kind']}")
        root = document_workspace if binding["schema_root"] == "document-workspace" else project_root
        binding_path = root / binding["schema_path"]
        raw = binding_path.read_bytes()
        if sha256_bytes(raw) != binding["schema_sha256"]:
            raise SystemExit(f"payload schema hash mismatch: {binding['projection_kind']}")
        binding_schema = json.loads(raw)
        target = json_pointer(binding_schema, binding["schema_pointer"])
        fragment = binding["schema_id"].rsplit("#", 1)
        identity_ok = binding_schema.get("$id") == binding["schema_id"] if len(fragment) == 1 else target.get("$anchor") == fragment[1]
        if not identity_ok:
            raise SystemExit(f"payload schema pointer/id mismatch: {binding['projection_kind']}")
    for binding in registry["nested_payload_bindings"]:
        if binding["projection_kind"] not in profile_kinds or binding["schema_root"] not in {"document-workspace", "project"}:
            raise SystemExit(f"invalid nested payload binding: {binding['projection_kind']} {binding['payload_pointer']}")
        root = document_workspace if binding["schema_root"] == "document-workspace" else project_root
        raw = (root / binding["schema_path"]).read_bytes()
        nested_schema = json.loads(raw)
        if sha256_bytes(raw) != binding["schema_sha256"] or nested_schema.get("$id") != binding["schema_id"]:
            raise SystemExit(f"nested payload schema pin mismatch: {binding['payload_pointer']}")
        parent = next(row for row in registry["projection_payload_bindings"] if row["projection_kind"] == binding["projection_kind"])
        parent_schema = json.loads(((document_workspace if parent["schema_root"] == "document-workspace" else project_root) / parent["schema_path"]).read_text(encoding="utf-8"))
        parent_rule = json_pointer(parent_schema, parent["schema_pointer"])
        if binding["projection_kind"] == "management-panel":
            json_pointer(parent_rule, "/properties/model_v1")
        else:
            json_pointer(parent_rule, "/properties" + binding["payload_pointer"])
    substitutions = {"$SCHEMA_SHA256": actual_hashes["schema"], "$REGISTRY_SHA256": actual_hashes["registry"]}
    passed, failed = run(suite, schema, registry, substitutions, project_root, actual_hashes)
    result = {
        "schema_version": "1.0.0", "evidence_kind": "design-fixture-check",
        "implementation_id": "python-reference-adapter", "implementation_version": "1.2.0",
        "platform": args.platform, "host_platform": sys.platform,
        "runtime": {
            "implementation": "cpython", "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "executable_sha256": sha256_bytes(Path(sys.executable).read_bytes()),
            "build_digest": sha256_bytes(Path(__file__).read_bytes()),
        },
        "native_durability_exercised": False,
        "registry_sha256": actual_hashes["registry"], "suite_sha256": actual_hashes["suite"],
        "schema_sha256": actual_hashes["schema"], "protocol_sha256": actual_hashes["protocol"],
        "passed_vector_ids": passed, "failed_vector_ids": failed, "executed_at": args.executed_at,
    }
    result["result_id"] = sha256_bytes(canonical_bytes(result))
    if not validate(result, schema, "conformanceResultV1"):
        raise SystemExit("result receipt failed schema validation")
    with Path(args.output).open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
