# Critical Path Pressure Analysis — Pre-Implementation Audit

**Audit base:** `feature/duct-network-engine-implementation` at `53d1fc78cf0da32e5eddc9300221dd78721fe749`
**PR #3:** Open; the new work is intentionally stacked on the compare branch.

## Verified architecture

The connected network model is in `duct_design/network/models.py`. A `DuctNetwork`
contains typed nodes, directed `DuctNetworkSegment` edges, terminal demands, a
source node, and a `NetworkType`. `validation.py` enforces a single rooted,
directed tree with one incoming segment per non-source node. `topology.py` builds
deterministic indexes and `terminal_paths`, ordered from source to terminal.
`flow_solver.py` performs a reverse-topological demand aggregation. The
orchestrator in `network_engine.py` passes each calculated segment demand to the
existing sizing engine and returns one `DuctSizingResult` per segment.

## Pressure fields and units

`duct_design.engine.DuctSizingResult` exposes:

- `straight_friction_loss_inwg`: straight-duct friction loss for that segment;
- `fitting_loss_inwg`: aggregate entered-K fitting loss for that segment;
- `total_modeled_loss_inwg`: the modeled total; and
- `velocity_pressure_inwg`, which is not a path-loss component.

The suffix and the documentation establish **inches water gauge (`in.w.g.`)**.
The existing engine calculates total modeled loss as straight loss plus fitting
loss. The pressure analysis must therefore sum `total_modeled_loss_inwg` once
per segment and must not add the component fields again.

## Integration decision

The new analysis consumes a validated `DuctNetwork` and the corresponding
`DuctNetworkResult`; it does not recalculate friction, fitting K, airflow, or
sizing. It reuses `build_network_topology()` and the result's existing sizing
objects. Each terminal path is traced using the topology's source-to-terminal
segment tuple, so shared upstream segments occur once per terminal path and
parallel branches are never summed together.

`NetworkType.RETURN` is currently a classification only. The verified solver is
source-to-terminal and has no terminal-to-fan reverse-flow semantics. The new
analysis will return an explicit unsupported-operation diagnostic for RETURN
rather than applying the supply algorithm silently.

## Scope boundary

The result is modeled duct-segment critical-path loss only. It is not fan
external static pressure (FESP): filters, coils, equipment, dampers, louvers,
attenuators, terminal/system boundaries, and other non-modeled components remain
outside this phase.
