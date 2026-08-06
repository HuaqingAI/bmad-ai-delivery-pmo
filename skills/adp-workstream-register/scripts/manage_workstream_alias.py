#!/usr/bin/env python3
"""Retire one duplicate workstream as an immutable alias of a canonical workstream."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, re, secrets, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SKILLS_ROOT = Path(__file__).resolve().parents[2]
STATUS_SCRIPT = SKILLS_ROOT / "adp-status-sync/scripts/sync_status.py"
TOKEN_REL = Path("state/workstream-alias-tokens")
REGISTRY_REL = Path("state/workstream-aliases.json")
RECEIPT_REL = Path("receipts/workstream-alias")
PLACEHOLDERS = {"", "tbd", "todo", "none", "n/a", "na", "unknown", "draft", "fill missing state", "fill missing project-level state and link current bmm artifacts", "see cross-workstream links"}

def load_status():
    spec = importlib.util.spec_from_file_location("adp_alias_status", STATUS_SCRIPT)
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    return module

def args():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("project_root")
    p.add_argument("--canonical",required=True); p.add_argument("--alias",required=True)
    p.add_argument("--memory-root",default="_bmad-output/adp/memory"); p.add_argument("--principal",default="adp-workstream-register")
    p.add_argument("--dry-run",action="store_true"); p.add_argument("--token"); p.add_argument("--fail-after-stage",action="store_true",help=argparse.SUPPRESS)
    p.add_argument("-o","--output"); return p.parse_args()

def emit(v,o):
    text=json.dumps(v,ensure_ascii=False,indent=2); Path(o).write_text(text+"\n",encoding="utf-8") if o else print(text)

def clean(v): return " ".join(str(v or "").split()).casefold()

def bullets(text):
    return {m.group(1).strip().casefold():m.group(2).strip() for m in re.finditer(r"^\s*-\s*([^:]+):\s*(.*)$",text,re.M)}

def exact_refs(text, alias):
    return [i+1 for i,line in enumerate(text.splitlines()) if re.fullmatch(rf"\s*-\s*{re.escape(alias)}\s*",line,re.I)]

def token_path(memory,token): return memory/TOKEN_REL/(hashlib.sha256(token.encode()).hexdigest()+".json")

def snapshot(st,memory,canonical,alias):
    canonical=st.normalize_id(canonical); alias=st.normalize_id(alias)
    if canonical==alias: raise st.StatusSyncContractError("WORKSTREAM_ALIAS_INVALID","canonical and alias must differ")
    cp=memory/"workstreams"/canonical/"delivery-record.md"; ap=memory/"workstreams"/alias/"delivery-record.md"
    if not cp.is_file() or not ap.is_file(): raise st.StatusSyncContractError("WORKSTREAM_ALIAS_MISSING","both WDRs must exist")
    ct=cp.read_text(encoding="utf-8-sig"); at=ap.read_text(encoding="utf-8-sig"); cb=bullets(ct); ab=bullets(at)
    if clean(cb.get("workstream id"))!=canonical or clean(ab.get("workstream id"))!=alias: raise st.StatusSyncContractError("WORKSTREAM_ALIAS_IDENTITY_INVALID","WDR identity does not match directory")
    conflicts=[]
    for key in ("fde owner","business owner"):
        if clean(ab.get(key)) not in PLACEHOLDERS and clean(ab.get(key))!=clean(cb.get(key)): conflicts.append({"kind":"identity","field":key,"alias":ab.get(key),"canonical":cb.get(key)})
    for key in ("progress","blockers","risks","scope or change notes","next actions"):
        if clean(ab.get(key)) not in PLACEHOLDERS: conflicts.append({"kind":"alias-fact","field":key,"value":ab.get(key)})
    ledger=memory/st.ACTION_LEDGER_REL; ledger_state_path=memory/st.ACTION_LEDGER_STATE_REL
    rows=st.parse_action_ledger(ledger) if ledger.is_file() else []
    alias_actions=[r.get("Action ID","") for r in rows if st.safe_normalize_id(r.get("Workstream",""))==alias or alias in st.parse_workstream_cell(r.get("Affected Workstreams",""))]
    if alias_actions: conflicts.append({"kind":"actions","action_ids":sorted(alias_actions)})
    refs=[]
    for record in sorted((memory/"workstreams").glob("*/delivery-record.md")):
        if record==ap: continue
        lines=exact_refs(record.read_text(encoding="utf-8-sig"),alias)
        if lines: refs.append({"path":record.relative_to(memory).as_posix(),"lines":lines,"fingerprint":st.sha256_bytes(record.read_bytes())})
    read_set={"canonical":st.sha256_bytes(cp.read_bytes()),"alias":st.sha256_bytes(ap.read_bytes()),"ledger":st.optional_sha256_file(ledger),"ledger_state":st.optional_sha256_file(ledger_state_path),"references":refs}
    body={"canonical":canonical,"alias":alias,"read_set":read_set,"conflicts":conflicts}
    return {**body,"snapshot_id":st.content_id(body),"can_apply":not conflicts,"canonical_path":cp,"alias_path":ap,"rows":rows}

def binding(st,snap,principal): return {"snapshot_id":snap["snapshot_id"],"principal":principal,"canonical":snap["canonical"],"alias":snap["alias"],"read_set":snap["read_set"]}

def issue(st,memory,snap,principal):
    token="wsalias_"+secrets.token_urlsafe(32); now=datetime.now(timezone.utc); b=binding(st,snap,principal)
    state={"schema_version":"1.0.0","token_hash":st.sha256_bytes(token.encode()),"principal":principal,"binding":b,"binding_digest":st.content_id(b),"status":"unused","issued_at":now.isoformat(),"expires_at":(now+timedelta(minutes=15)).isoformat(),"previous_state_id":None}; state["state_id"]=st.content_id(state); st.write_json_atomic(token_path(memory,token),state); return token

def apply(st,memory,snap,principal,tp,ts,fail):
    with tempfile.TemporaryDirectory(prefix=".workstream-alias-",dir=memory.parent) as td:
        staged=Path(td)/"memory"; st.copy_memory_tree(memory,staged)
        changed_wdr=[]
        for ref in snap["read_set"]["references"]:
            path=staged/ref["path"]; before=path.read_bytes(); text=path.read_text(encoding="utf-8-sig")
            text=re.sub(rf"(?m)^(\s*-\s*){re.escape(snap['alias'])}(\s*)$",rf"\1{snap['canonical']}\2",text)
            path.write_text(text,encoding="utf-8",newline="\n"); state=st.update_wdr_state(path,before,path.read_bytes()); changed_wdr.append((path,state))
        registry_path=staged/REGISTRY_REL; registry=st.load_json_object(registry_path) or {"schema_version":"1.0.0","aliases":[]}
        aliases=[x for x in registry.get("aliases",[]) if isinstance(x,dict) and x.get("alias_workstream_id")!=snap["alias"]]
        recorded=datetime.now(timezone.utc).isoformat(); alias_record={"status":"retired-alias","alias_workstream_id":snap["alias"],"canonical_workstream_id":snap["canonical"],"retired_at":recorded,"source_snapshot_id":snap["snapshot_id"]}; aliases.append(alias_record); registry["aliases"]=sorted(aliases,key=lambda x:x["alias_workstream_id"]); registry["state_id"]=st.content_id({k:v for k,v in registry.items() if k!="state_id"}); st.write_json_atomic(registry_path,registry)
        st.write_json_atomic(staged/"workstreams"/snap["alias"]/"workstream-alias.json",alias_record)
        if (staged/st.ACTION_LEDGER_REL).is_file() and (staged/st.ACTION_LEDGER_STATE_REL).is_file():
            ls=st.load_json_object(staged/st.ACTION_LEDGER_STATE_REL)
            for path,state in changed_wdr: st.write_action_projection_sidecar(staged,path.parent.name,snap["rows"],ls,wdr_state=state)
        receipt={"schema_version":"1.0.0","receipt_type":"workstream-alias-retirement","outcome":"committed","principal":principal,"canonical_workstream_id":snap["canonical"],"alias_workstream_id":snap["alias"],"snapshot_id":snap["snapshot_id"],"read_set":snap["read_set"],"recorded_at":recorded}; receipt["receipt_id"]=st.content_id(receipt); rr=RECEIPT_REL/(receipt["receipt_id"].removeprefix("sha256:")+".json"); st.write_json_atomic(staged/rr,receipt)
        consumed=dict(ts); consumed.update({"previous_state_id":ts["state_id"],"status":"consumed","receipt_id":receipt["receipt_id"]}); consumed.pop("state_id",None); consumed["state_id"]=st.content_id(consumed); tr=tp.relative_to(memory); st.write_json_atomic(staged/tr,consumed)
        changed=st.changed_staged_files(memory,staged)
        if fail: raise st.StatusSyncContractError("WORKSTREAM_ALIAS_INJECTED_FAILURE","injected failure after staging")
        publication=st.publish_staged_files(memory,staged,changed,transaction_kind="workstream-alias-retirement")
    return receipt,memory/rr,publication,[p.as_posix() for p in changed]

def main():
    a=args(); st=load_status(); project=st.require_project_root(a.project_root); memory=st.resolve_memory_root(project,a.memory_root); principal=" ".join(a.principal.split())
    try:
      with st.fact_write_lock(memory):
        st.recover_status_transactions(memory); snap=snapshot(st,memory,a.canonical,a.alias)
        if a.dry_run:
            token=issue(st,memory,snap,principal) if snap["can_apply"] else None; emit({"ok":True,"mode":"workstream-alias-retire","dry_run":True,"verification_status":"verified" if snap["can_apply"] else "blocked","can_apply":snap["can_apply"],"conflicts":snap["conflicts"],"references":snap["read_set"]["references"],"token":token},a.output); return 0
        if not snap["can_apply"]: raise st.StatusSyncContractError("WORKSTREAM_ALIAS_CONFLICT","alias contains facts/actions requiring explicit migration",{"conflicts":snap["conflicts"]})
        if not a.token: raise st.StatusSyncContractError("WORKSTREAM_ALIAS_TOKEN_REQUIRED","apply requires dry-run token")
        tp=token_path(memory,a.token); ts=st.load_json_object(tp); body=dict(ts); claimed=body.pop("state_id",None)
        if not ts or claimed!=st.content_id(body) or ts.get("token_hash")!=st.sha256_bytes(a.token.encode()) or ts.get("status")!="unused" or ts.get("principal")!=principal: raise st.StatusSyncContractError("WORKSTREAM_ALIAS_TOKEN_INVALID","token is invalid or used")
        if datetime.now(timezone.utc)>datetime.fromisoformat(ts["expires_at"]): raise st.StatusSyncContractError("WORKSTREAM_ALIAS_TOKEN_EXPIRED","token expired")
        b=binding(st,snap,principal)
        if ts.get("binding")!=b or ts.get("binding_digest")!=st.content_id(b): raise st.StatusSyncContractError("WORKSTREAM_ALIAS_READ_SET_STALE","facts changed after dry-run")
        receipt,rp,pub,changed=apply(st,memory,snap,principal,tp,ts,a.fail_after_stage); emit({"ok":True,"mode":"workstream-alias-retire","outcome":"committed","receipt":receipt,"receipt_path":str(rp),"publication":pub,"changed_paths":changed},a.output); return 0
    except st.StatusSyncContractError as e:
      out={"ok":False,"error_code":e.error_code,"error":str(e)}; out.update(e.details); emit(out,a.output); return 2
if __name__=="__main__": sys.exit(main())
