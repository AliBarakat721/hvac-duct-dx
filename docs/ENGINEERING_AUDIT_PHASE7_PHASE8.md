# Phase 7/8 Implementation and Eight-Phase Engineering Audit

**Repository:** `AliBarakat721/hvac-duct-dx`  
**Audit date:** 2026-11-17  
**Final working branch:** `feature/cad-integration`  
**Starting integration branch:** `feature/duct-network-engine-implementation` (PR #3 was open)

## A. Executive summary

Phase 7 and Phase 8 were implemented on separate dependent branches. Phase 7 adds explicit physical return-air semantics using a configured return sink, preserves the existing supply rooted-tree solver, reuses the existing sizing engine, and adds numerical regression tests. Phase 8 adds a DXF-only CAD pipeline with file validation, entity extraction, unit normalization, stable handles, layer suggestions, explicit manual mapping, network conversion, export, and a reusable Qt import/layer page.

The implementation is tested and reviewable, but it is not a claim of production HVAC design readiness. Complete critical-path pressure integration, branch balancing, fitting coefficient selection, complete fan external static pressure, native DWG, and a full navigation-level CAD drawing editor remain incomplete or outside the verified branch dependency.

## B. Git status and pull requests

- **PR #3:** `OPEN`, `feature/duct-network-engine-implementation` → `main`: [PR #3](https://github.com/AliBarakat721/hvac-duct-dx/pull/3). No merge commit was present.
- **Phase 7 branch:** `feature/supply-return-network-engine`; commit `ebc19554575ac9346a076b27722f09b614efbd60`; [PR #7](https://github.com/AliBarakat721/hvac-duct-dx/pull/7), base `feature/duct-network-engine-implementation`, state `OPEN`.
- **Phase 8 branch:** `feature/cad-integration`; commit `25c4e6e0913897930f15e065b8c53720baffb6a5`; [PR #8](https://github.com/AliBarakat721/hvac-duct-dx/pull/8), base `feature/supply-return-network-engine`, state `OPEN`.
- **Repository main at inspection:** `9130399359acf3843c7bf146214f2f9ee7161e0e`.
- **Phase 7 dependency branch at inspection:** `53d1fc78cf0da32e5eddc9300221dd78721fe749`.
- No branch was merged, deleted, force-pushed, reset, or cleaned. Final working tree was clean after commit.

## C. Phase 7 report

### Supply architecture

`duct_design/network/models.py:DuctNetwork` models a typed directed network. Supply edges run from the single `SOURCE` node through `JUNCTION` nodes to `TERMINAL` nodes. `flow_solver.py:_solve_topology` aggregates downstream terminal demand in deterministic reverse topological order. `network_engine.py:calculate_duct_network` sends each calculated segment CFM into the existing `run_design()` or `evaluate_rectangular_duct()` sizing APIs; it does not duplicate duct-sizing equations.

### Return architecture

`duct_design/network/models.py:DuctNetwork.sink_node_id` provides explicit return-sink semantics. A physically correct return graph is authored as `return grille → return branch → return main → equipment sink`. `return_flow.py:return_as_supply_network` reverses only the internal graph representation for reuse by the existing rooted-tree calculation, while `network_engine.py:_calculate_explicit_return_network` restores original segment endpoints and terminal paths in the result. Return airflow is independently calculated from return terminal demands; no supply-equals-return assumption is introduced.

New engineering callers should specify `network_type=RETURN`, a sink node typed `END`, and `sink_node_id`. The historical source-rooted `RETURN` shape remains accepted for compatibility with the pre-existing test fixture; it should not be used as evidence of reverse-direction correctness.

### Flow conservation and sizing

For supply, source flow equals the sum of terminal demands. For return, sink flow equals the sum collected from return terminals. Shared upstream/shared return-main segments carry the sum below them. Duplicate IDs, invalid references, cycles, disconnected graphs, invalid roles, negative/non-finite values, and invalid dimensions remain diagnosed by the existing validation layer. Zero-demand branches remain explicit and are not sized.

### Pressure integration and limitations

The result still reports `NetworkPressureStatus.NOT_CALCULATED`. Existing segment-level straight friction and entered aggregate-K loss are preserved, but critical-path pressure analysis was not present on the Phase 7 base branch. No complete FESP claim is made.

### Tests

`tests/test_supply_return_network.py` verifies a two-grille return network, shared return main flow, reverse physical paths, explicit sink validation, wrong terminal role rejection, and independent solver airflow. The pre-existing `tests/test_duct_network.py` regression suite also passed.

## D. Phase 8 report

### Supported formats and import pipeline

`duct_design/cad/io.py:import_dxf` supports DXF input through `ezdxf`, validates extension, existence, and file size, parses model space without executing macros/scripts, detects `$INSUNITS`, normalizes coordinates to a requested target unit, preserves entity handles, and reports malformed or unsupported entities as diagnostics. Native DWG is explicitly not supported.

Supported extraction includes LINE, LWPOLYLINE, POLYLINE, ARC, CIRCLE, INSERT, TEXT, and MTEXT. Unsupported entities are retained as `UNSUPPORTED` diagnostics rather than silently discarded. `geometry_diagnostics` checks zero-length and duplicate line/polyline geometry.

### Layer mapping and network conversion

`suggest_layer_mapping` provides conservative suggestions based on layer names. `LayerMapping` requires an explicit caller-approved mapping. `build_network_from_cad` accepts only confirmed duct categories, explicit segment handles, explicit terminal mappings, a declared network type, a root/sink coordinate, and a configured tolerance. It does not fabricate duct dimensions from arbitrary outlines or infer airflow direction from proximity. Segment direction must be authored in the mapping: source-to-terminal for supply and grille-to-sink for return.

The generated engineering objects use the existing `DuctNetwork`, `DuctNetworkSegment`, `DuctNode`, and `DuctTerminal` models, preserving CAD handles as segment/terminal mapping references. Engineering calculations are then performed by `calculate_duct_network`, not by a second CAD-specific solver.

### Export and GUI

`export_dxf` writes a separate DXF working representation with geometry and mapped layers; it does not overwrite the source file. `duct_design/cad/qt_page.py:CadIntegrationPage` provides a reusable PySide6 import button, layer list, entity count, and validation summary. The page is not yet registered as a replacement navigation tab in `main.py` or `ui_shell.py`; this is reported as remaining integration work rather than claimed complete.

### Tests

`tests/test_phase7_phase8.py` creates representative DXF fixtures and verifies units, layers, LINE/LWPOLYLINE/ARC/CIRCLE/TEXT extraction, geometry validation, explicit return CAD-to-network conversion, calculation integration, and DXF export/import round-trip.

## E. Eight-phase audit matrix

| Phase | Implementation Status | Engineering Validation | Test Status | Remaining Work |
|---|---|---|---|---|
| 1. Duct sizing | Partial | Equations and Darcy factor are implemented and regression-tested; it remains a preliminary equivalent-round screening engine | Passed in full suite | Validate against project-approved tables, construction class, leakage, and commercial schedules |
| 2. Connected network | Complete | Rooted-tree roles, connectivity, conservation, deterministic ordering, and sizing integration verified | Existing network suite passed | General mesh/converging flow remains unsupported |
| 3. Critical path pressure | Partial | Implementation exists on separate unmerged branch, but Phase 7 base result remains `NOT_CALCULATED` | Not part of final branch full workflow | Merge/integrate with supply and explicit return semantics and verify all path directions |
| 4. Pressure balancing | Missing | No verified solver for resistance adjustment or redistribution | No balancing tests found | Implement a convergent balancing model and field/design distinction |
| 5. Fitting/dynamic loss | Partial | Aggregate user-entered K is supported; geometry-specific coefficient selection is not verified | Separate branch tests exist, not integrated here | Integrate fitting objects, coefficient provenance, reference velocity, and double-counting rules |
| 6. Fan static pressure | Partial | Separate fan budget implementation exists on another branch; complete system boundary/equipment basis is not integrated into final branch | Separate branch tests exist, not final workflow | Integrate supply/return critical paths, filters/coils/equipment, and explicit FESP boundary planes |
| 7. Supply/return network | Complete | Supply aggregation and explicit return sink-directed aggregation verified numerically; pressure dependency remains separate | 46 network tests passed in focused run; full suite passed | Integrate critical-path pressure and balancing when dependencies are merged |
| 8. CAD integration | Partial | DXF parsing, units, handles, mapping, conversion, and round-trip export verified; DWG and full app navigation are not | 3 CAD tests passed; full suite passed | Register page in main navigation, add visual canvas/editing, and add combined supply+return drawing fixture |

## F. Critical and high findings

1. **HIGH — Complete system pressure/FESP workflow is not integrated.** Evidence: `duct_design/network/models.py:DuctNetworkResult` defaults `network_pressure_status` to `NOT_CALCULATED`; `duct_design/network/network_engine.py:calculate_duct_network` returns no network critical pressure. The pressure and fan modules inspected under `feature/critical-path-pressure-analysis` and `feature/fan-static-pressure-engine` are separate branches, not the final Phase 8 base. Impact: results must not be used as complete fan static pressure. Recommended correction: merge and adapt those modules to explicit return paths, then add an end-to-end supply+return pressure-budget fixture.

2. **HIGH — CAD page is reusable but not application-wired.** Evidence: `duct_design/cad/qt_page.py:CadIntegrationPage` exists, while repository GUI entry points remain `main.py` and `ui_shell.py`; no final navigation registration was added. Impact: a user cannot necessarily reach the new workflow through the shipped application without host integration. Recommended correction: add a small navigation entry and offscreen smoke test using the existing Qt architecture, without duplicating engineering logic.

3. **MEDIUM — Complete cross-phase E2E coverage is not yet demonstrated.** Evidence: `tests/test_phase7_phase8.py` verifies CAD-to-return-network-to-sizing and DXF round-trip, but does not run one drawing containing both supply and return networks through pressure analysis and export. Recommended correction: add a fixture with both networks, multiple branches, different flows/sizes, and a verified numerical expected-result ledger after the pressure dependency is integrated.

4. **MEDIUM — Native DWG is unavailable.** Evidence: `duct_design/cad/io.py:import_dxf` rejects non-DXF extensions. This is correctly disclosed and avoids an unverified proprietary conversion claim. Recommended correction: document and test an approved external DWG-to-DXF conversion workflow if product requirements require DWG.

## G. Test results

Executed on `feature/cad-integration` after both commits:

- `python -m pytest -q`: **138 passed**, **0 failed**, **3 warnings** (PySide6 deprecation warnings in the pre-existing drag/drop smoke test).
- Focused Phase 7 run: **46 passed** (`tests/test_duct_network.py` plus `tests/test_supply_return_network.py`).
- New CAD tests: **3 passed** (`tests/test_phase7_phase8.py`).
- `ruff check .`: **clean**.
- `python -m compileall -q duct_design`: **clean**.
- `git diff --check`: **clean**.
- GUI/offscreen tests: included in the full 138-test run and passed; the new CAD page itself has no dedicated navigation smoke test.
- End-to-end: CAD-to-return-network-to-sizing was executed; a complete CAD→supply+return→pressure→FESP→export workflow was **not claimed** because the pressure/FESP dependency is not integrated on this branch.

## H. Engineering readiness assessment

- **Duct sizing reliability:** suitable as a preliminary, independently tested screening engine; not construction-document ready without project criteria and licensed table verification.
- **Network calculation reliability:** reliable for validated directed rooted trees, including explicit return trees; unsupported for general multi-inlet meshes and balancing.
- **Supply/return reliability:** good for the tested tree semantics when return sink direction is explicit; legacy source-rooted return objects require migration.
- **Pressure analysis reliability:** not complete on the final branch; do not use as FESP.
- **CAD integration reliability:** suitable for validated DXF inspection and explicit mapping workflows; not native DWG or a full interactive drawing editor.
- **End-to-end workflow reliability:** partial; the tested mapping/calculation path works, but combined pressure/FESP and full GUI navigation are pending.

## I. Remaining roadmap, ranked

1. **Highest engineering risk:** integrate and verify critical-path pressure, fitting losses, and fan pressure budgets on explicit supply and return paths; add independent numerical fixtures and prevent FESP double counting.
2. **Highest user impact:** register `CadIntegrationPage` in the existing Qt navigation and add a GUI smoke test for import, layer review, validation errors, and export.
3. **High dependency:** add a combined representative supply+return CAD fixture and a full cross-phase test only after pressure analysis is integrated.
4. **Medium:** replace legacy source-rooted RETURN fixtures with explicit sink-directed models and keep a migration diagnostic for old serialized projects.
5. **Medium:** extend CAD geometry mapping for confirmed fittings, blocks, rectangular outlines, and dimension metadata without inferring uncertain engineering properties.
6. **Lower priority:** add a documented DWG conversion adapter if the product can rely on a verified external converter; otherwise keep DXF as the supported interchange boundary.
