# ARCHITECTURE-SPINE Brownfield Reality / Currentness Review v12

## Verdict

**PASS WITH FOLLOW-UPS. Critical: 0. High: 0. Medium: 2. Low: 1.** The committed spine claims are grounded in the current repository and the frozen contract bytes. The five diagnosed brownfield limitations remain observable in production code, all 13 registered source pins match, the registry inventory matches the stated 44/13/9/7/6/15/27/9/11/8 counts, and both independent reference runners reproduce the checked-in 305/305 design results byte for byte. The v11 High defects are closed by executable negatives and equivalent Python/Node handlers. The package also keeps the correct evidence boundary: the target runtime modules are not deployed, both architecture documents remain draft, and implementation conformance remains pending.

The remaining findings do not make any current decision false. They concern the durability of the brownfield evidence snapshot, Python 3.10's short remaining support runway, and a standards citation update.

## Frozen Review Target

- Repository: `HEAD=a5d873e0ad3e7d60e7157f76096c6ac65085bee3` on `master`.
- Architecture spine raw SHA-256: `352f2da359b3616cb77743676c5bb2a67a2509d43598941b99b2a31c2e4a1eac`.
- Analysis plan raw SHA-256: `22b9e68c6b685dc6008f44eb431871420fbc438fa34997a1a5b8e84b090f6d0d`.
- Pre-existing unrelated worktree content was limited to `skills/reports/adp-operational-artifacts-panel-refresh-plan.md` and `skills/reports/eval-runs/`; it was not modified.
- This reviewer modified no architecture, contract, fixture, runner, or production-code file. It created only this review.

## Critical Findings

None.

## High Findings

None.

## Medium Findings

### M1 - The reviewed production behavior sources are current but not frozen by the contract package

The spine identifies nine production scripts as the source of its brownfield claims (`ARCHITECTURE-SPINE.md:21-30`), and the companion analysis makes exact behavioral assertions against them (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:73-124`). Those assertions are correct at the reviewed `HEAD`, but the registry's 13 `pinned_source_artifacts` do not include these nine scripts. The only pinned production Python source is the separate Panel v1 composer, `panel_model.py`.

Current raw hashes of the nine evidence sources are:

| Source | Raw SHA-256 |
| --- | --- |
| `skills/adp-meeting-sync/scripts/sync_meeting.py` | `435c0ca4e9262bbce7d753e0a295a74fa03e15a9840f855e0e5926cb5ba77c0d` |
| `skills/adp-status-sync/scripts/sync_status.py` | `372b0b9160d9c7771d519273549d2ace247f8c17012d27fb7e86ba2fcff171ad` |
| `skills/adp-bmm-checkpoint-sync/scripts/sync_bmm_checkpoint.py` | `f9d5c471043c1657a76f9fabc6f0e86f7253fd11fc5d142471691c5408ab6804` |
| `skills/adp-risk-dependency-change-review/scripts/review_risk_dependency_change.py` | `596a7012947c985551138a352cc9a889870c48075f5d988d2ea87248dded1393` |
| `skills/adp-workstream-register/scripts/register_workstream.py` | `67e07457138157e5417d53365ad8bcd543aa3d33d81a16e386f64477fdec1d68` |
| `skills/adp-state-audit/scripts/audit_state.py` | `70df59e42ca3094585dd79574d519c4c09e181c312bd1fe1861c9a94f4b77b74` |
| `skills/adp-state-audit/scripts/panel_audit.py` | `7fc3e6e3e460e1d5eb34fd0180639abfb2ba57ca4f51e8db6eb4bc677d200f20` |
| `skills/adp-agent-program-lead/scripts/adp-state-prepass.py` | `28701301eec0330c81d3c1f30bf36e15dc05b1987a3f2dc58a7d79246dd5ede5` |
| `skills/adp-management-panel/scripts/management_panel.py` | `f70657ffa15dc833ac2ab2195ecc3ea7042da14bfa1842aa268ffc114c6d8171` |

**Risk:** a later production-code change can invalidate the analysis while all normative contract pins and 305 design vectors remain green. That is a currentness/reproducibility gap, not a present factual error.

**Recommended fix:** add a non-normative `brownfield-evidence.json` (or equivalent reviewed-source manifest) binding the reviewed commit plus these raw hashes, and state that it is regenerated when the production baseline changes. Keep it separate from runtime contract negotiation so ordinary production changes do not make all wire documents invalid.

### M2 - Python 3.10 is still supported on the review date but has only a short support runway

The stack binds `Python >=3.10` (`ARCHITECTURE-SPINE.md:196-205`). That matches the brownfield repository: both cross-platform workflows currently test only Python 3.10 (`.github/workflows/adp-meeting-sync.yml:16-34`; `.github/workflows/adp-management-panel.yml:29-41`), and the reviewed baseline passes under local Python 3.12.13. The technology therefore exists and fits today.

However, the live CPython lifecycle page classifies 3.10 as security-only, and [PEP 619](https://peps.python.org/pep-0619/) states that security releases are provided only until October 2026. The architecture date is 2026-07-24, leaving roughly three months before the declared minimum reaches end of life.

**Risk:** implementation conformance may finish after the minimum runtime is unsupported, while current CI supplies no evidence for a newer supported minimum.

**Recommended fix:** before production conformance, choose and test a supported floor (preferably 3.12 or later), or add an explicit October 2026 removal/deprecation gate and a CI lane for the successor version. The current `>=3.10` claim is not false, so this does not block the architecture gate.

## Low Findings

### L1 - Normative keywords cite RFC 2119 without its current BCP 14 update

The protocol says MUST/MUST NOT/SHOULD are interpreted according to RFC 2119 (`WDR-AND-TRANSACTION-PROTOCOL.md:3`). RFC 2119 remains valid, but [RFC 8174](https://www.rfc-editor.org/rfc/rfc8174.html) updates it and the current convention is to cite both as BCP 14, especially when only uppercase words are normative.

**Recommended fix:** change the citation to “BCP 14 (RFC 2119 and RFC 8174)” at the next protocol revision. This is editorial/currentness hygiene; the present uppercase requirements are not semantically ambiguous in this document.

## Brownfield Reality Check

All five user-reported limitations are confirmed against current repository files:

| Claim | Current repository evidence | Result |
| --- | --- | --- |
| Meeting sync cannot mutate an existing action by exact identity | The v1 meeting contract has no operation/action ID/revision, and `build_status_sync_intake()` emits create-shaped payloads. Status sync separately supports exact-ID lookup and merge (`sync_status.py:840-946`), including the unsafe unconditional status assignment at `:907-909`. | Confirmed |
| `wdr_update` does not update Panel current fields | Meeting sync appends `render_wdr_block()` to the WDR (`sync_meeting.py:812-821,1244-1269`); it does not invoke the status-sync Project Status writer. | Confirmed |
| Panel checks do not prove live source currency | `inspect_current()` reads the current HTML and verifies embedded model/manifest/resources (`management_panel.py:1120-1165`) but does not reread live WDR/ledger leaves. Panel audit validates recorded fingerprint shape and generated age, not live equality. | Confirmed |
| WDR/ledger drift detection is partial | Prepass compares action-ID sets (`adp-state-prepass.py:911-958`), and state audit converts those set differences to disagreements (`audit_state.py:2267-2303`); it does not prove content equality or a complete empty-ledger closure. | Confirmed |
| Canonical audit findings lose action IDs | Action IDs participate in `finding_identity_details()` (`audit_state.py:3001-3014`), but `canonical_finding()` does not copy them into its public result (`:2951-2998`); downstream `flatten_findings()` still attempts to read them (`:3341-3362`). | Confirmed |

The target architecture does not pretend these are already fixed. `adp-fact-transaction`, `adp-wdr-mutation`, and `adp-panel-refresh` appear only in the Structural Seed (`ARCHITECTURE-SPINE.md:207-221`) and do not exist as production skill directories. The production Panel remains the pinned v1 implementation; the v2 current consumer is a conformance artifact, not deployed Panel code.

## Contract and Raw-Artifact Reality

The normative chain is internally current and reproducible:

| Artifact | Observed raw SHA-256 | Registry/spine result |
| --- | --- | --- |
| `contracts/CONTRACT-REGISTRY.json` | `f5bcb9820f1b5f755c06a0b357faaa0059d65c7e94d6e17501ca8a626de6cf99` | Matches spine |
| `contracts/panel-sync-contracts.schema.json` | `d700d48bdf952da9f8ea98ca1eb0eb78eb44e64f20dc67ea9b36d81f84837f1c` | Matches registry/spine |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `d1aaa396239fccb60696be50ca43e292c3ee29d7c406bee9ca46762a78419072` | Matches registry/release gate/spine |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `95ac467c7e98d1929e95c4e65622049fe70d08abebb54acc620cd68c26869a88` | Matches registry/spine |
| `contracts/fixtures/PANEL-V1-COMPATIBILITY.json` | `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` | Matches source pin/spine |
| `contracts/conformance/python_runner.py` | `8b5145da68f0ef4922da9b6b8b2350a9669da51bea5e5405e7956b49bf5cfc83` | Matches registry/spine |
| `contracts/conformance/node_runner.mjs` | `d06c6b3590be333bdd72d2d21ebd2d31cffe6540ada1c5cdaeb49165f06e9792` | Matches registry/spine |
| `contracts/conformance/panel_v2_consumer.mjs` | `eaf497fbf79eaedface1408b1ea1679aec7d7e85709fd9e3a386550855bbc423` | Matches source pin/spine |
| `contracts/conformance/python-result.json` | `6872504b3ae29b2969e9a28908348552b6a7265e5a32fb971cbe0ad08993dc52` | Matches spine and fixed-time replay |
| `contracts/conformance/node-result.json` | `13757f6fdc67d3acb1b388e72e397a072992e273749c868954bd7a5bfe5e1c2a` | Matches spine and fixed-time replay |

All 13 registered source artifact hashes match their current raw files. Registry counts independently observed: 44 contracts, 13 pins, 9 dependency enumerators, 7 input profiles, 7 outer payload bindings, 4 nested bindings, 6 Panel bindings, 15 DAG edges, 27 typed array-ordering rules, 9 identity-set rules, 11 runtime paths, and 8 semantic validators. These match AD-11 and the analysis plan.

The prior v11 release-oracle defects now have direct executable coverage:

- Recursive exact contract-reference checking is implemented before registered shape validation (`python_runner.py:235-276`) and exercised by fake-anchor/schema-hash/registry-hash and recursive tamper vectors.
- Action create, status-authorized risk intent, and direct risk WDR rejection are explicit fact-attribution vectors (`CONFORMANCE-VECTORS.json:514-517`).
- WDR/action target sets, state CAS, raw before/after byte proof, and proof contract hashes have independent mutation negatives (`CONFORMANCE-VECTORS.json:518-550`).
- Physical inventory validates WDR grammar, registered sidecar contract, exact hashes, and canonical sidecar bytes; invalid WDR/sidecar/fake-ref/noncanonical vectors are present (`CONFORMANCE-VECTORS.json:424-434`).
- Typed ordering includes integer key types, lone non-NFC rejection, and an 11-target numeric apply-order vector (`CONFORMANCE-VECTORS.json:435-450`).
- Panel v2 uses an instrumented executable consumer and an exact traced read-set vector (`CONFORMANCE-VECTORS.json:634`).

## External Technology Currentness

| Named technology/standard | Currentness and fit check | Result |
| --- | --- | --- |
| Python `>=3.10` | Exists and matches existing code/CI; 3.10 is security-only until October 2026, while the local 3.12.13 baseline passes. | Fit now; Medium runway finding |
| JSON Schema Draft 2020-12 | The [JSON Schema specification page](https://json-schema.org/specification) states “The current version is 2020-12.” All six reviewed schemas parse as JSON and declare the 2020-12 meta-schema URI. | Current and fit |
| RFC 8785 JCS | [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html) exists as the JSON Canonicalization Scheme. Its ECMAScript number-serialization model fits the cross-language Python/Node identity requirement; the package adds explicit safe-integer/schema fraction constraints. | Exists and fit |
| RFC 6901 JSON Pointer | [RFC 6901](https://www.rfc-editor.org/rfc/rfc6901.html) remains the applicable JSON Pointer specification and fits exact source/target binding. | Exists and fit |
| RFC 3339 timestamps | [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339.html) exists; the architecture intentionally narrows wire output to UTC whole seconds. | Exists and fit |
| POSIX/Windows durability profiles | Both are target platform classes, not claimed library versions. The protocol defines exact adapter behavior and the release gate explicitly requires native POSIX fault injection and native Windows CI rather than accepting design models as native evidence. | Fit; correctly evidence-gated |
| CQRS-style materialized projections | This is a named architectural paradigm, not a dependency/version claim. It matches the actual separation between fact mutation and generated Panel artifacts; the spine explicitly rejects event sourcing. | Fit |

## Verification Evidence

- Architecture lint: **PASS, 0 findings** using `python3 .agents/skills/bmad-architecture/scripts/lint_spine.py` (`uv` is unavailable).
- Python/Node syntax checks: **PASS** for both runners and the pinned v2 consumer.
- Fixed-time Python replay: **305 passed / 0 failed**, byte-for-byte equal to checked-in `python-result.json`.
- Fixed-time Node replay: **305 passed / 0 failed**, byte-for-byte equal to checked-in `node-result.json`.
- Passed-vector sequence and set: **exactly equal** between Python and Node.
- Brownfield regression baseline: **205/205 passed**: meeting-sync 25 + DingTalk intake 6, status-sync 29, state-audit 63, management-panel 28, panel-audit 12, state-prepass 10, panel-model 6, Panel contract 26.
- Additional Program Lead regression set: **17/17 passed**: consume-program-status plus render-program-views.
- Six schema files: all parse, all declare Draft 2020-12, and their master/production contract suites above pass.
- Evidence boundary: `ARCHITECTURE-SPINE.md` remains `status: draft`; registry `conformance_suite.implementation_conformance_status` remains `pending`; both receipts remain `design-fixture-check` with `native_durability_exercised=false`.

## Gate Decision

**Reality/currentness gate passes: zero Critical and zero High findings.** The architecture can proceed to the remaining independent reviewer lenses. M1 and M2 should be scheduled before production conformance; L1 can be corrected with the next protocol hash revision.
