# Phase 6 Fan Static Pressure — Dependency Audit

**Audit base:** `feature/critical-path-pressure-analysis` at `864d6aaa44db05b0dd193c8392551612728ff78c`
**PR #4:** open, so this work is stacked on the verified Phase 3 branch.

## Verified phases

### Phase 3 — Critical Path Pressure Analysis

Implemented in `duct_design/network/pressure_analysis.py` and exported through
`duct_design`. It consumes `DuctNetworkResult`, uses the validated directed
SUPPLY topology, and reports ordered terminal paths, per-segment straight and
fitting losses, total modeled path loss, and the deterministic critical terminal.
The actual loss fields are `straight_friction_loss_inwg`,
`fitting_loss_inwg`, and `total_modeled_loss_inwg`. Units are inches water gauge.
RETURN is explicitly unsupported because the existing network solver does not
implement terminal-to-fan reverse flow.

### Phase 4 — Duct Pressure Balancing

No Phase 4 branch, package, API, test suite, or pull request was found in the
remote repository. No balancing pressure difference or damper resistance can
therefore be consumed. The Phase 6 engine will not invent a balancing result;
balancing contributions must be supplied explicitly as pressure components.

### Phase 5 — Fitting & Dynamic Pressure Loss

No Phase 5 branch, package, API, test suite, or pull request was found in the
remote repository. The existing duct sizer has an aggregate user-entered
`fitting_k` and calculates `fitting_loss_inwg` using the segment's actual
rectangular velocity pressure. It is not a fitting geometry/K-factor database.
Phase 6 will reuse those verified per-segment outputs through Phase 3 and will
not recalculate or fabricate fitting coefficients.

## Available equipment data

The repository contains HAP parsing data and an equipment SQLite catalog, but no
stable fan-pressure component model that exposes validated filter, coil,
damper, or accessory air-side pressure drops for this calculation. The new
engine therefore accepts typed user/manufacturer/measured pressure contributions
with explicit units, airflow, data source, and fan-boundary classification.

## Integration decision and completeness gate

The new `duct_design.fan` package will:

1. adapt a verified Phase 3 critical path into traceable duct-friction and
   fitting contributions without double-counting segment totals;
2. accept explicit supply/return/equipment pressure paths and components;
3. convert Pa, kPa, and in.w.g. through one validated conversion layer;
4. calculate a transparent modeled total-pressure budget and an external-static
   budget only when fan reference planes and component boundaries are defined;
5. report missing Phase 4/5 data and incomplete boundaries as diagnostics.

This is a modeled pressure-budget engine, not an unconditional FESP claim. A
`PRELIMINARY` result remains preliminary when the input does not prove that all
filters, coils, dampers, equipment internals, terminals, and other system
components are represented. Internal equipment losses are never automatically
added to external static pressure.
