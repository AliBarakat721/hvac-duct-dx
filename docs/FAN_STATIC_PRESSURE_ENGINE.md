# Fan Static Pressure Calculation Engine

## Scope and dependency status

The Phase 6 engine is implemented in `duct_design/fan/`. The dependency audit
found a verified Phase 3 critical-path engine, but no Phase 4 balancing package
and no Phase 5 fitting/dynamic-loss package in the repository or open PRs. The
engine therefore consumes Phase 3 outputs and accepts explicit, traceable
pressure contributions for balancing dampers, filters, coils, equipment,
attenuators, louvers, terminals, and other components. It does not invent
missing losses.

The result is a modeled fan pressure budget. It is not automatically an
equipment-certified Fan External Static Pressure (FESP) selection.

## Pressure terminology

For air density `rho` and mean velocity `V`:

```text
Pv = rho V² / 2
Pt = Ps + Pv
```

`Pv` is calculated in SI and converted to `in.w.g.`. The modeled fan total
pressure is:

```text
Fan total pressure = sum(selected component losses) + Pv,out - Pv,in
```

The component-loss budget is normalized to `in.w.g.` from Pa, kPa, or in.w.g.
Each contribution retains its component ID, type, airflow, data source, network
and segment references, and whether it is internal or external to the fan
rating boundary.

External static pressure is deliberately narrower:

```text
Modeled external-static budget = sum(losses classified EXTERNAL)
```

It is returned only when inlet and outlet fan reference planes and the boundary
configuration are explicitly declared. Internal equipment losses are included in
the modeled total-pressure budget but are not automatically added to FESP.
`fesp_is_authoritative` remains `False` because the application does not yet
prove complete equipment/system coverage or a manufacturer fan-rating convention.

## System arrangements

- **SUPPLY:** one explicitly selected source-to-outlet pressure path.
- **RETURN:** one explicitly selected inlet-to-fan pressure path. This is an
  explicit pressure path, not a claim that the current network airflow solver
  supports reverse return topology.
- **COMPLETE:** exactly one supply and one return path; they are summed only
  because the caller explicitly selected a complete airflow circuit.

Unrelated paths are not automatically combined. A complete calculation requires
both supply and return paths. A path without contributions is invalid rather than
silently treated as zero.

## Critical-path integration

`pressure_path_from_critical_path()` adapts the verified Phase 3 critical path.
For each ordered segment it creates exactly one straight-friction line and one
fitting line from the existing `DuctSizingResult` fields. The fitting line is
not recalculated and the segment total is not added a second time. Shared
upstream segments appear once because the Phase 3 critical path already contains
each segment once in source-to-terminal order; parallel branches are not summed.

If Phase 3 analysis is incomplete or unsupported, the adapter raises a domain
error rather than fabricating a path.

## Data quality and statuses

All pressure values, airflows, velocities, and density values are finite and
validated. Negative losses, invalid units, duplicate component/path IDs,
airflow mismatches, missing paths, and invalid metadata produce diagnostics.

- `COMPLETE_MODELLED`: all selected modelled paths and boundaries are valid and
  the caller declared known component coverage complete.
- `PRELIMINARY`: the modelled budget is calculable, but component coverage or
  boundary completeness is not established.
- `INCOMPLETE`: a required path or pressure input is invalid or missing.
- `UNSUPPORTED`: reserved for configurations without a supported calculation
  definition.

The current implementation intentionally reports `PRELIMINARY` or `INCOMPLETE`
where Phase 4/5 results, manufacturer data, terminals, equipment internals, or
fan reference definitions remain unverified.

## Worked example

For a supply path with a 100 Pa duct loss, a 0.20 in.w.g. filter loss, and a
0.30 in.w.g. coil loss:

```text
100 Pa / 249.08891 = 0.40146 in.w.g.
component total = 0.40146 + 0.20 + 0.30 = 0.90146 in.w.g.
```

If the filter and coil are classified INTERNAL, the external-static budget is
`0.40146 in.w.g.` while the modeled total-pressure budget includes all three
components. With 500 fpm at the fan inlet and 800 fpm at the outlet, the fan
total-pressure result additionally includes `Pv,out - Pv,in` using the declared
air density.

The calculation does not claim that this is the final fan selection. Filters,
coils, dampers, equipment internals, louvers, attenuators, terminals, leakage,
fan casing effects, and the rating standard must be confirmed by the engineer.
