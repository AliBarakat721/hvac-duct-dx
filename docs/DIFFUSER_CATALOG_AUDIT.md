# Diffuser catalog data audit

## Scope and source files

This audit concerns the Al-Andalosia `diffuser.pdf` supplied with the task. Source-page references below are **printed page numbers** in that 24-page file. The separate `CeilingConcealedDuctedSplitSystems.pdf` is a catalogue for DX indoor/outdoor equipment (capacity, airflow, ESP and electrical/operating data); it is not a diffuser performance catalogue and is not used to populate diffuser rows.

The normalized records are exposed by `diffuser_catalog_records.py`. They currently cover tables already used by the diffuser calculator: ceiling-height flow/ΔT limits (p.5), supply circular CCD (p.6), return circular CCD (p.7), square 4-way 4SCD (p.11), and square 3-way 3SCD (p.14). Every operating point stores model/family, face and neck geometry, direction, airflow, neck velocity, total pressure, throw range, acoustic representation, printed page, notes and a verification status. The p.8 SI selection chart is kept as a separate data structure because it is a distinct sizing guide, not another row in the p.6 performance table.

The module is an audit/normalization layer; it does not claim that the current calculator has been fully migrated to these records. Legacy calculation arrays remain active for compatibility.

## Confirmed differences and unresolved source questions

1. **Printed total flow versus legacy flow.** Some square-family `cfm` arrays in `catalog_data.py` are equal to the geometric face area multiplied by tabulated velocity, not to the rounded total CFM printed by the vendor. For example, the 9×9 row at 300 FPM is recorded in the printed total-flow table as 168 CFM, while the legacy array holds 169 CFM. Normalized rows preserve both values and emit an audit warning; this work does not change the calculator's active airflow interpolation.
2. **Directional flow rounding.** The p.11 4SCD table lists CFM per side as rounded integers. Four times each per-side value can differ from the printed total by a few CFM. The audit checks this with an explicit 5 CFM tolerance and retains both source values; it does not force a rounded sum to equal the total.
3. **AK/effective area.** AK is distinct from geometric neck/face area. The printed 18×18 4SCD AK appears as `0.13`, while the legacy metadata records `1.13`; the raw and legacy values are stored separately and the validator emits an explicit warning rather than silently “correcting” the printed value. The physical interpretation and correct value require supplier confirmation.
4. **Throw endpoints.** The vendor's definitions on printed pp.3 and 10 do not consistently identify the minimum/maximum throw endpoints with a terminal velocity. The records retain the published ranges but intentionally do not relabel them as T100/T50. The calculator's current T100/T50 interpretation remains a known assumption.
5. **Acoustics.** Circular tables show numeric NC values while square tables use colour bands. The colour legend includes a room-attenuation assumption; the p.8 selection guide separately lists LwA and LwNC. These quantities are not interchangeable. The records preserve the printed NC label/band and any separate LwA field; they do not calculate room NC.
6. **Page 8 SI selection guide.** The vendor says the maximum flow assumes LwA 40 dB(A), and the minimum flow should maintain effective discharge velocity of at least 2 m/s. It includes `Aeff`, dimensions C/C1/C2 and LwA/LwNC ranges. This is recorded separately and must be reconciled with p.6 before it drives automatic selection.
7. **Unsupported models.** The PDF contains 1-way/2-way square and rectangular models, 3-way/4-way rectangular models, and 4CCD round-neck square-face tables. They are not normalized as active models yet. No jet diffuser performance table is present in this PDF despite the introductory mention. These require separate transcription and source checks before support can be claimed.

## Automated integrity checks

`validate_operating_points()` checks that velocities are positive, unique and strictly increasing per model/direction; airflow is positive and increasing; total pressure does not decrease with velocity; throw ranges are non-negative and ordered; known four-side 4SCD flows reconcile to the published total within the documented rounding tolerance; and the 18×18 AK mismatch is surfaced. It emits warnings—not silent corrections—when legacy calculator flow differs from the printed total. Missing per-direction flow data for 3SCD are explicitly left unverified. The p.5 ceiling limits are exposed as source-traced rows and remain the common table used by the calculator.

`performance()` still returns endpoint-clamped values for compatibility, but now provides `in_range=False` and a warning that endpoint values were clamped and not extrapolated. Selection results carry that warning. Out-of-range endpoint values must not be interpreted as a validated operating point.

## Recommended next work

Before adding more model families, request supplier confirmation for AK and throw definitions, then manually transcribe/check the remaining p.12–25 tables (square/rectangular 1/2/3/4-way and 4CCD). Add the missing per-direction flow values when the source provides them, and keep sound power, catalog NC and room NC as separate fields. Only after these checks should the normalized records become the sole calculation source and be used to rank or reject diffuser selections.
