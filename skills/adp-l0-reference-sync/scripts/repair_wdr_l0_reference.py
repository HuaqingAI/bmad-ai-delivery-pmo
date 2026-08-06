#!/usr/bin/env python3
"""Add reviewed L0 references to one existing physical WDR transactionally."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, re, secrets, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SKILLS_ROOT=Path(__file__).resolve().parents[2]; STATUS_SCRIPT=SKILLS_ROOT/"adp-status-sync/scripts/sync_status.py"
TOKEN_REL=Path("state/l0-reference-repair-tokens"); RECEIPT_REL=Path("receipts/l0-reference-repair")
def stmod():
 s=importlib.util.spec_from_file_location("adp_l0_status",STATUS_SCRIPT); m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); return m
def parse():
 p=argparse.ArgumentParser(description=__doc__); p.add_argument("project_root"); p.add_argument("--id",required=True); p.add_argument("--l0-reference",action="append",required=True); p.add_argument("--memory-root",default="_bmad-output/adp/memory"); p.add_argument("--principal",default="adp-l0-reference-sync"); p.add_argument("--dry-run",action="store_true"); p.add_argument("--token"); p.add_argument("--fail-after-stage",action="store_true",help=argparse.SUPPRESS); p.add_argument("-o","--output"); return p.parse_args()
def emit(v,o):
 t=json.dumps(v,ensure_ascii=False,indent=2); Path(o).write_text(t+"\n",encoding="utf-8") if o else print(t)
def token_path(memory,t): return memory/TOKEN_REL/(hashlib.sha256(t.encode()).hexdigest()+".json")
def merge_refs(text,refs):
 lines=text.splitlines(); cross=next((i for i,x in enumerate(lines) if x.strip().casefold()=="## cross-workstream links"),None); existing=[]
 if cross is None:
  project=next((i for i,x in enumerate(lines) if x.strip().casefold()=="## project status"),None)
  if project is not None: insert=next((i for i in range(project+1,len(lines)) if lines[i].startswith("## ")),len(lines))
  else: insert=next((i for i,x in enumerate(lines) if x.strip().casefold() in {"## decisions and evidence","## record rule"}),len(lines))
  block=["## Cross-Workstream Links","","Depends on:","","Impacts:","","L0 references:",""]
  if insert and lines[insert-1].strip(): block.insert(0,"")
  lines[insert:insert]=block; start=insert+block.index("L0 references:"); end=start+2
 else:
  section_end=next((i for i in range(cross+1,len(lines)) if lines[i].startswith("## ")),len(lines))
  start=next((i for i in range(cross+1,section_end) if lines[i].strip().casefold()=="l0 references:"),None)
  if start is None:
   block=[] if section_end and not lines[section_end-1].strip() else [""]
   block.extend(["L0 references:",""])
   lines[section_end:section_end]=block; start=section_end+block.index("L0 references:"); end=start+2
  else:
   end=section_end
   for i in range(start+1,section_end):
    stripped=lines[i].strip()
    if stripped.endswith(":") and not stripped.startswith(("-","*")): end=i; break
   for line in lines[start+1:end]:
    if line.strip().startswith(("- ","* ")): existing.append(re.sub(r"^[-*]\s+","",line.strip()).strip())
 merged=[]
 for value in [*existing,*refs]:
  value=" ".join(value.split())
  if value.casefold() in {"tbd","todo","none","n/a","na","unknown"}: continue
  if value and value.casefold() not in {x.casefold() for x in merged}: merged.append(value)
 block=["L0 references:",""]+[f"- {x}" for x in merged]+[""]
 lines[start:end]=block; return "\n".join(lines).rstrip()+"\n",existing,merged
def snap(st,memory,wid,refs):
 wid=st.normalize_id(wid)
 if st.scope_contract_module().is_virtual_cli_scope_id(wid): raise st.StatusSyncContractError("L0_REFERENCE_REPAIR_TARGET_INVALID","virtual program has no WDR")
 refs=[" ".join(x.split()) for x in refs if " ".join(x.split())]
 if not refs: raise st.StatusSyncContractError("L0_REFERENCE_REPAIR_VALUE_INVALID","at least one non-empty L0 reference is required")
 wdr=st.load_reconciliation_wdr(memory,wid); desired,before,after=merge_refs(wdr["text"],refs)
 ledger=memory/st.ACTION_LEDGER_REL; ls_path=memory/st.ACTION_LEDGER_STATE_REL
 if not ledger.is_file() or not ls_path.is_file(): raise st.StatusSyncContractError("L0_REFERENCE_REPAIR_LINEAGE_MISSING","action ledger lineage is required")
 ls=st.load_existing_json_object(ls_path,"L0_REFERENCE_REPAIR_LINEAGE_INVALID","action ledger state"); st.validate_action_ledger_state(ledger,ls)
 read={"wdr":st.sha256_bytes(wdr["record_path"].read_bytes()),"wdr_state":st.sha256_bytes(wdr["state_path"].read_bytes()),"ledger":st.sha256_bytes(ledger.read_bytes()),"ledger_state":st.sha256_bytes(ls_path.read_bytes()),"projection":st.optional_sha256_file(wdr["record_path"].with_name(st.ACTION_PROJECTION_REL))}
 body={"workstream_id":wid,"requested":refs,"before":before,"after":after,"read_set":read,"desired":st.sha256_bytes(desired.encode())}
 return {**body,"snapshot_id":st.content_id(body),"desired_text":desired,"wdr":wdr,"ledger_state":ls,"rows":st.parse_action_ledger(ledger)}
def bind(st,s,principal): return {k:s[k] for k in ("snapshot_id","workstream_id","requested","read_set")}|{"principal":principal}
def issue(st,memory,s,principal):
 t="l0ref_"+secrets.token_urlsafe(32); now=datetime.now(timezone.utc); b=bind(st,s,principal); state={"schema_version":"1.0.0","token_hash":st.sha256_bytes(t.encode()),"principal":principal,"binding":b,"binding_digest":st.content_id(b),"status":"unused","issued_at":now.isoformat(),"expires_at":(now+timedelta(minutes=15)).isoformat(),"previous_state_id":None}; state["state_id"]=st.content_id(state); st.write_json_atomic(token_path(memory,t),state); return t
def apply(st,memory,s,principal,tp,ts,fail):
 record_rel=Path("workstreams")/s["workstream_id"]/"delivery-record.md"; state_rel=record_rel.with_name("delivery-record.state.json"); proj_rel=record_rel.with_name(st.ACTION_PROJECTION_REL); tr=tp.relative_to(memory)
 with tempfile.TemporaryDirectory(prefix=".l0-ref-repair-",dir=memory.parent) as td:
  staged=Path(td)/"memory"; st.copy_memory_tree(memory,staged); record=staged/record_rel; before=record.read_bytes(); record.write_text(s["desired_text"],encoding="utf-8",newline="\n"); ws=st.update_wdr_state(record,before,record.read_bytes()); st.write_action_projection_sidecar(staged,s["workstream_id"],s["rows"],s["ledger_state"],wdr_state=ws)
  receipt={"schema_version":"1.0.0","receipt_type":"l0-reference-repair","outcome":"committed","principal":principal,"workstream_id":s["workstream_id"],"references_added":sorted(set(s["after"])-set(s["before"])),"snapshot_id":s["snapshot_id"],"read_set":s["read_set"],"recorded_at":datetime.now(timezone.utc).isoformat()}; receipt["receipt_id"]=st.content_id(receipt); rr=RECEIPT_REL/(receipt["receipt_id"].removeprefix("sha256:")+".json"); st.write_json_atomic(staged/rr,receipt)
  consumed=dict(ts); consumed.update({"previous_state_id":ts["state_id"],"status":"consumed","receipt_id":receipt["receipt_id"]}); consumed.pop("state_id",None); consumed["state_id"]=st.content_id(consumed); st.write_json_atomic(staged/tr,consumed)
  changed=st.changed_staged_files(memory,staged)
  if fail: raise st.StatusSyncContractError("L0_REFERENCE_REPAIR_INJECTED_FAILURE","injected failure after staging")
  pub=st.publish_staged_files(memory,staged,changed,transaction_kind="l0-reference-repair")
 return receipt,memory/rr,pub,[x.as_posix() for x in changed]
def main():
 a=parse(); st=stmod(); project=st.require_project_root(a.project_root); memory=st.resolve_memory_root(project,a.memory_root); principal=" ".join(a.principal.split())
 try:
  with st.fact_write_lock(memory):
   st.recover_status_transactions(memory); s=snap(st,memory,a.id,a.l0_reference)
   if a.dry_run:
    t=issue(st,memory,s,principal); emit({"ok":True,"mode":"repair-wdr-l0-reference","dry_run":True,"verification_status":"verified","workstream_id":s["workstream_id"],"before":s["before"],"after":s["after"],"token":t},a.output); return 0
   if not a.token: raise st.StatusSyncContractError("L0_REFERENCE_REPAIR_TOKEN_REQUIRED","apply requires dry-run token")
   tp=token_path(memory,a.token); ts=st.load_json_object(tp); body=dict(ts); claimed=body.pop("state_id",None)
   if not ts or claimed!=st.content_id(body) or ts.get("token_hash")!=st.sha256_bytes(a.token.encode()) or ts.get("status")!="unused" or ts.get("principal")!=principal: raise st.StatusSyncContractError("L0_REFERENCE_REPAIR_TOKEN_INVALID","token invalid or used")
   b=bind(st,s,principal)
   if ts.get("binding")!=b or ts.get("binding_digest")!=st.content_id(b): raise st.StatusSyncContractError("L0_REFERENCE_REPAIR_READ_SET_STALE","facts changed after dry-run")
   receipt,rp,pub,changed=apply(st,memory,s,principal,tp,ts,a.fail_after_stage); emit({"ok":True,"mode":"repair-wdr-l0-reference","outcome":"committed","receipt":receipt,"receipt_path":str(rp),"publication":pub,"changed_paths":changed},a.output); return 0
 except (st.StatusSyncContractError,ValueError) as e:
  emit({"ok":False,"error_code":getattr(e,"error_code","L0_REFERENCE_REPAIR_INVALID"),"error":str(e)},a.output); return 2
if __name__=="__main__": sys.exit(main())
