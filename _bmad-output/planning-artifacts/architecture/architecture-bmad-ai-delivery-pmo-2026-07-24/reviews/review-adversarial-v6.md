# Architecture Spine Adversarial / Data-Integrity Review v6

## Frozen Review Base

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `a2b6e97c447adaf108539d42f47d3727532af66723cc0e578d5e97ae14187e42` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `b763091093b4e27b748f046d04736f498f40c5d7624204f7aadb94156b7100eb` |
| `contracts/CONTRACT-REGISTRY.json` | `5630c8ff49a2b3173150be3835ba2bd6297d74dbe2a73439f57b6df5713dd1c8` |
| `contracts/panel-sync-contracts.schema.json` | `a858fb31c06e4bd2aab5f02ef54cba1b5f4d6e028aecfda95a452960e78ecf73` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `5cf7ed4e1b249ec994a77e70b5691d6a66e1b9eb4711d11221e1aae3fcccabc2` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `e6cafdb8e8f6b04e286b136ba225cb7e7e6eca757e208a2edc5e42f84f1961c6` |
| `contracts/conformance/python-result.json` | `7d36c52e7b156eba8dbd4655c4eaaa65ac817e940e729ea8d46b902fa440a24b` |
| `contracts/conformance/node-result.json` | `2083ede0d1194261b8f3cad718aa1bac52b6a0a3d231a02f7f7683a4369807d6` |

Read-only checks found 40 registered contracts, 8 input profiles, 8 payload bindings, 16 DAG edges, and 12 canonical-array rules. Profile upstreams equal the DAG edge set, profile kinds equal payload-binding kinds, all JSON parses, Draft 2020-12 schema compilation passes, and architecture lint reports zero findings. Both stored receipts bind the frozen registry/suite/schema/protocol hashes, contain 58 passed and zero failed vector IDs, and have valid recomputed `result_id` values. The Node design runner reproduced 58/58. These are design-fixture checks only; `native_durability_exercised=false` and `implementation_conformance_status=pending` are accurately disclosed.

## Verdict

**FAIL. Critical: 0. High: 4.** The v5 action-ID, status-intent routing, read-profile/DAG, orphan repair, journal-image, and refresh-state issues are materially improved. However, the frozen contract set still has one binding that cannot produce a schema-valid Panel, permits semantically empty or false-green Panel data, forbids the mutation attribution that the spine requires, and does not define a machine-closed production evidence acceptance gate. Pending native/production evidence is not itself a finding; the High finding is that future receipts cannot be accepted or rejected unambiguously.

## Critical

None.

## High

### H1 - The drift binding targets a path forbidden by the Panel v2 schema

The registry binds `action-projection-drift-verdict` to `/meta/freshness/action_projection` in both the binding map and Management Panel profile (`CONTRACT-REGISTRY.json:480,658,664`). The target schema instead requires `/meta/action_projection`, sets `meta.additionalProperties=false`, and declares no `freshness` member (`panel-sync-contracts.schema.json:596-607`). A strict binder cannot apply the registered pointer and then validate the result. An implementation that creates `meta.freshness` fails the target schema; one that silently rewrites the pointer violates the registry; one that drops the verdict violates the required binding. This blocks the very drift gate intended to prevent a stale Panel publication.

**Required fix:** choose one canonical target path, update binding map/profile/allowed-affects/schema together, and add an end-to-end vector that applies every binding to a blank target skeleton and validates the resulting `managementPanelPayloadV2`.

### H2 - The payload contracts do not fix the Panel's actual status semantics and admit false-green publication inputs

The target schemas pin only outer shells: program-status `progress` and `flow_state`, roadmap items, meeting `boards`, and all Panel `audit/status/roadmap/flow/meetings/views` values are unconstrained objects (`panel-sync-contracts.schema.json:605-620,1188-1231`). The positive vectors deliberately prove that empty `{}` payload sections pass (`CONFORMANCE-VECTORS.json:70-133`). Two producers can therefore choose incompatible progress/blocker/risk structures while both satisfy the same binding. This also conflicts with the protocol rule that consumers read only schema-declared fields (`WDR-AND-TRANSACTION-PROTOCOL.md:56`): the fields the Panel actually needs inside those objects are not declared.

The drift verdict has the same integrity hole: `workstreams` may be empty and `overall_status="in-sync"` is not conditioned on every selected workstream being present and `in-sync` (`panel-sync-contracts.schema.json:541-568`). The spine says that condition is the publication gate (`ARCHITECTURE-SPINE.md:107`), but the suite has no whole-verdict or publication rejection vector. A buggy producer can emit a schema-valid false green and a Panel implementation can publish it without failing the current 58-vector gate.

**Required fix:** define the minimum canonical shapes for status progress/blockers/risks, roadmap, meeting boards, Panel data/views and source-preview bytes; add semantic constraints for drift coverage/status and Panel publication eligibility; exercise valid and false-green whole envelopes through registry binding and publication validation.

### H3 - The fact mutation receipt cannot carry the required initiating producer/capability

AD-2 and the normative protocol require the coordinator receipt to record the initiating producer/capability (`ARCHITECTURE-SPINE.md:83`; `WDR-AND-TRANSACTION-PROTOCOL.md:49`). `factMutationReceiptV1` neither requires nor defines either field and rejects all undeclared fields with `additionalProperties=false` (`panel-sync-contracts.schema.json:983-1000`). Thus no receipt can be simultaneously schema-valid and compliant with the ownership/audit rule. A coordinator that omits attribution loses the evidence needed to distinguish authorized status-sync mutations from delegated meeting/checkpoint/risk work; a coordinator that records it fails negotiation.

**Required fix:** add typed initiating producer, capability ID/epoch, and principal identity or a pinned authorization reference to the receipt schema and identity rules; add cross-field checks against the journal's authorized command and capability registry.

### H4 - The production conformance acceptance gate is not a closed contract

The suite declares two independent implementations and native POSIX/Windows platforms, but `required_result_fields` does not define result acceptance beyond field presence (`CONFORMANCE-VECTORS.json:4-10`). `conformanceResultV1` independently allows `evidence_kind="implementation-conformance"` with a design-model platform, `native_durability_exercised=false`, any subset of passed IDs, and arbitrary nonempty failed IDs (`panel-sync-contracts.schema.json:1234-1255`). The registry adds `native_windows_evidence_required=true` but no validator ID/hash or acceptance algorithm (`CONTRACT-REGISTRY.json:15-42`). Consequently one release-gate implementation can reject such a receipt while another can count it toward the minimum, and both can point to a different part of the frozen package.

This is distinct from the correctly disclosed pending prerequisite. The architecture may leave production evidence pending, but it must define how strict publication later decides that the prerequisite is satisfied.

**Required fix:** register and pin a release-gate validator that requires exact suite ID equality, `failed_vector_ids=[]`, `passed_vector_ids` equal to the full suite set, distinct implementation provenance, required native platforms, `native_durability_exercised=true`, matching artifact hashes/result IDs, and the specified real fault-injection/native-Windows evidence classes. Add invalid-receipt acceptance vectors.

## Medium

### M1 - The 58/58 design evidence does not exercise several claims attached to it

The runners validate individual payload fixtures, but do not derive and execute all eight payload bindings; their profile check only tests a short required-kind subset (`python_runner.py:266-296`). Journal and repair checks compare fixture labels/arrays rather than validating complete journal/repair documents and cross-field invariants (`python_runner.py:309-369`). The Node result reproducibly reports 58/58, but that count should not be described as covering binding/publication/recovery behavior until whole-contract negative vectors are added.

### M2 - The pinned Python runner is not runnable with the repository's current Python fallback

On this host `python3` is 3.9.6. All vector code executes, but receipt emission fails at `Path.write_text(..., newline="\n")` because that keyword is unsupported (`python_runner.py:428`). The stored Python receipt is internally hash-consistent, but the current documented Python 3 fallback cannot reproduce it end to end. Pin a minimum Python version or use a compatible write primitive.

### M3 - Freshness vocabulary drifts in the human-facing plan

The schema/protocol use `unverifiable`, while the analysis calls offline freshness `not-verifiable` (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:260,495`). Normalize the term before UI and tests bind different values.

## Low

None.

## Exit Conditions

1. Make the Panel binding target and Panel v2 schema agree, then run a whole-catalog binding-to-schema vector.
2. Replace shell payload schemas with the minimum shapes the Panel actually consumes and add false-green drift/publication rejection cases.
3. Make fact mutation attribution schema-valid and cross-bind it to authorization evidence.
4. Pin a deterministic production result acceptance validator; keep implementation status pending until its native evidence rules pass.
5. Repair or version-pin the Python runner and relabel 58/58 narrowly until whole binding/journal/repair checks exist.
