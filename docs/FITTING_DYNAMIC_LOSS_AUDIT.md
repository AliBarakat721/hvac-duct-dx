# Fitting & Dynamic Pressure Loss Engine Audit

## Scope

This audit covers the fitting-loss phase implemented on
`feature/fitting-dynamic-losses`, based on
`feature/critical-path-pressure-analysis`. It is intentionally limited to
coefficient-driven local-loss calculations. It does not add automatic
balancing, airflow redistribution, fan selection, FESP, CAD extraction, or
fitting geometry generation.

## Existing implementation found

The existing sizing engine accepts `DuctSegmentInput.fitting_k` as one
aggregate, user-entered coefficient. `evaluate_rectangular_duct()` computes
that loss from the rectangular duct dynamic pressure, and
`DuctSizingResult.total_modeled_loss_inwg` is the sum of straight friction and
that aggregate fitting loss. The network and critical-path engines consume
those fields. Replacing this behavior would risk changing existing results and
would double count losses.

## Data-source decision

No authoritative, licensed fitting-coefficient database is bundled with this
repository, and no geometry-specific coefficients are claimed by this phase.
The engine therefore has **no built-in K values** for elbows, tees, wyes,
transitions, dampers, louvers, screens, or other fittings. A coefficient must
be supplied by the caller with:

- a non-empty coefficient ID;
- a fitting category;
- a non-negative finite K;
- an explicit reference-velocity convention;
- a source classification and source reference; and
- a validity state that is not `UNSUPPORTED`.

Tests use synthetic values and label them `USER_SUPPLIED` fixtures. They are
not design data. The package distinguishes `USER_SUPPLIED`, `MANUFACTURER`,
`VERIFIED_REFERENCE`, and `ESTIMATE` provenance, but does not upgrade any
caller-supplied value to verified status.

## Calculation contract

For every result, the engine records the reference velocity convention and the
calculated velocity in FPM. The calculation is:

\[
\Delta P = K q_{ref} = K \frac{\rho V_{ref}^2}{2}
\]

Air density is supplied in kg/m³, internal velocity is converted to m/s, and
pressure is returned in Pa and in.w.g. using the repository constant
`PA_PER_IN_WG`. Airflow is CFM and rectangular/round geometry is in inches.
The reference velocity can be derived from inlet, outlet, branch, or
straight-through area, or supplied explicitly with `USER_DEFINED`.

## Supported method versus supported data

The `FittingType` enum provides typed categories for common elbows, tees,
wyes, laterals, takeoffs, transitions, expansions, contractions, entrances,
exits, dampers, louvers, screens, flexible connections, and an `OTHER`
category. This is a **metadata and validation capability**, not a coefficient
database. A category is calculable only when the caller provides a matching K
record and enough geometry for its selected reference velocity (or an explicit
reference velocity).

Missing K, missing geometry, invalid dimensions, invalid airflow, invalid
metadata, unsupported coefficient status, and non-finite calculations raise a
traceable `FittingCalculationError`; they never fall back to zero or an
arbitrary default.

## Integration and double counting

`calculate_fitting_losses()` calculates and deterministically orders multiple
fittings while rejecting duplicate fitting IDs. `integrate_fitting_losses()`
aggregates fittings for one segment. If an existing segment fitting-loss value
is supplied, the adapter requires it to match the new aggregate and marks the
loss as already included. It does not add a second loss to
`DuctSizingResult.total_modeled_loss_inwg`. A mismatch is rejected rather than
silently combined.

The existing sizing, network, and critical-path APIs remain unchanged. This
phase exposes a separate opt-in fitting API so callers can migrate from the
aggregate `fitting_k` field only when they have traceable fitting data.

## Numerical example (synthetic coefficient only)

For a user-supplied `K = 0.50`, air density `1.20 kg/m³`, and explicit
reference velocity `1,000 FPM`:

- `V_ref = 1,000 × 0.00508 = 5.08 m/s`;
- `q_ref = 0.5 × 1.20 × 5.08² = 15.48384 Pa`;
- `ΔP = 0.50 × 15.48384 = 7.74192 Pa`;
- `ΔP = 7.74192 / 249.08891 = 0.03108 in.w.g.` approximately.

The K value in this example is a test fixture, not a recommended engineering
coefficient.
