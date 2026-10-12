# ASHRAE Engineering Basis — Diffuser Selection

## Purpose and compliance statement

This document records the engineering rules used by `hvac-duct-dx`, the source basis for each rule, and the limits of what the software verifies.

The diffuser workflow is **ASHRAE-aligned screening**, not a declaration of full ASHRAE compliance. In particular, the implemented `T50/L` or `T0.25/L` check is a cooling-air-distribution screening method. It is **not a measured or simulated ADPI calculation**, and it does not by itself verify occupied-zone comfort, jet interaction, thermal stratification, or Standard 55 compliance.

The current result status is intentionally explicit:

```text
ASHRAE-aligned screening; ADPI not verified
```

## Source hierarchy

| Source | Use in the project |
|---|---|
| ASHRAE HVAC Applications 2015, Chapter 57, Room Air Distribution, printed pp. 57.5–57.6 | Selection procedure, occupied-zone review, T0.25/L method, characteristic length, heating/VAV limitations, jet-interaction warning |
| ASHRAE HVAC Applications 2015, Chapter 48, printed p. 48.3 | Room background-sound NC/RC goals and the distinction between room criteria and component catalog ratings |
| ASHRAE Fundamentals 2017, Chapter 20, Space Air Diffusion | Fundamentals for room air diffusion and comfort interpretation |
| Al-Andalosia diffuser catalog, supplied `diffuser.pdf` | Product airflow, neck velocity, catalog throw, catalog total-pressure loss, catalog NC and Table 4 operating limits |
| SMACNA Duct Design, Chapter 3 | Characteristic-length interpretations, occupied-zone/drop guidance and opposite-wall throw guidance |
| Carrier source cited by the project | Caution that rated diffuser pressure loss may exclude collar and approach losses |

## Implemented rules

### Airflow and sensible temperature difference

The room supply airflow is taken from the explicit design airflow field. When a sensible load is available, the cooling temperature difference follows:

\[
Q_s = 1.08 \times CFM \times \Delta T
\]

Total cooling load is not substituted for sensible load in this calculation. When the sensible load is unavailable, the software reports the temperature-difference check as unverified rather than inventing a value.

### Catalog selection

Candidate diffusers are screened against the manufacturer catalog for:

- airflow per diffuser and catalog operating range;
- neck velocity validity range;
- catalog throw data;
- catalog total-pressure loss against the configured maximum;
- catalog NC against the configured application limit; and
- catalog Table 4 airflow and ceiling-height limits.

Catalog pressure is labelled **catalog total-pressure loss**. It must not be interpreted as the complete branch static pressure or system external static pressure.

### Geometry-aware placement

The primary selection path uses `select_layout_for_geometry()`. It places candidate outlets inside the room geometry, checks diffuser face clearance and minimum spacing, then evaluates the selected positions. The same geometry-aware path is used by the GUI, fresh report calculations, and the CLI for rectangular rooms.

A saved CAD layout is reused only when its input signature and positions still match the current selection inputs. Otherwise it is re-evaluated or marked for review.

### Characteristic length `L`

The implementation follows the available Table 3 interpretations rather than imposing a generic room-dimension rule:

- nearest wall for a wall-limited jet;
- half the distance to a neighbouring outlet when an intersecting jet is the limiting condition;
- drop to the top of the occupied zone where the applicable rule requires it; and
- opposite-wall guidance where the SMACNA rule applies.

The old rule `T50 >= max(room width, room length) / 2` is removed because it is not a general ASHRAE/SMACNA requirement.

### T50/L screening

ASHRAE Chapter 57 describes the cooling comfort method using cataloged isothermal `T0.25/L`. The supplied catalog exposes `T50`, which the project uses as an approximately 50 fpm terminal-velocity proxy. The output therefore says `T50/L screening` and records the basis; it does not claim that ADPI has been calculated.

The ASHRAE Table 4 bands are stored for the circular ceiling diffuser rows:

| Cooling load | Screening band used by the project | Maximum ADPI shown in the source table |
|---:|---:|---:|
| 60 W/m² | 0.7–1.3 | 93 |
| 125 W/m² | 0.5–1.5 | 88 |
| 190 W/m² | 0.7–1.2 | 83 |
| 250 W/m² | 0.7–1.3 | 76 |

When the sensible load is missing or outside the table coverage, the project uses a conservative `0.7–1.2` screening band and marks the result as screening only.

**Important limitation:** the ASHRAE table does not provide a direct row for the project’s square 4-way and square 3-way products. The circular-ceiling row is currently used as a documented proxy and is marked as an engineering decision, not as direct product-specific ASHRAE verification.

### Pressure acceptance

`pressure_ok` is a hard criterion for a fully compliant layout. A candidate whose catalog total-pressure loss exceeds `max_dp_inwg` cannot be returned as fully compliant. It may appear as a fallback only with an explicit review status and warning.

The check does not include duct, collar, plenum, damper, approach, or branch losses unless they are separately modelled and reported.

## Rules deliberately not represented as full verification

| Rule / topic | Current status | Reason |
|---|---|---|
| Actual ADPI | Not verified | Requires effective draft temperature and velocity at occupied-zone points; T50/L alone is insufficient |
| Jet-to-jet collision | Warning-only geometric screen | The code flags overlapping radial T50 proxy envelopes; direction, actual jet collision, and occupied-zone velocity are not verified |
| Jet-to-wall mapping at all directions | Partial / engineering review | Product-specific directional throw and drop data are incomplete |
| Overhead heating | Not implemented | Requires separate buoyancy and occupied-zone checks; cooling T0.25/L bands must not be reused |
| VAV minimum and maximum flow | Not implemented | Chapter 57 requires both operating points; product data and operating-case inputs are not yet complete |
| NC accumulation by outlet spacing | Pending verification | Chapter 57 notes up to 3 dB addition for identical outlets closer than 3 m, but the product NC basis must be resolved first |
| NC basis | Unresolved | The supplied catalog has conflicting room-attenuation and sound-power descriptions |
| Terminal-velocity labels | Unresolved | Catalog definitions conflict; the code keeps `(T100, T50)` as an explicit assumption pending supplier confirmation |
| Preferred neck velocity band | Engineering decision | The current preferred range is a design-screening preference, not a universal ASHRAE acceptance limit |
| Area per diffuser | Engineering decision | The 60–400 ft² rule is a project rule of thumb, not a direct ASHRAE diffuser-compliance criterion |

## Reporting requirements

Every generated selection should preserve the following distinctions:

1. `Catalog selection` — whether the product can carry the required airflow within catalog limits.
2. `Geometry-aware screening` — whether the candidate can be placed and its T50/L screen evaluated for the room geometry.
3. `Design verification` — whether occupied-zone comfort, acoustic room performance, heating/VAV cases, and manufacturer installation conditions have been verified.

The current software can report the first two levels for the implemented inputs. It must not label them as complete design verification.

## Change-control rule for future additions

Before adding a new engineering formula or acceptance limit:

1. record the exact source and printed page/table;
2. state whether the rule is product-specific or general;
3. add a status: `supported`, `engineer_decision`, `pending_verification`, `unresolved`, or `not_implemented`;
4. add a regression case for the intended call path; and
5. ensure the GUI, report, Excel, and CLI expose the same result and verification status.

Do not add an ADPI equation from T50/L alone, convert catalog total pressure to static pressure by assumption, apply cooling bands to heating, or create a generic jet-collision equation without validated data.
