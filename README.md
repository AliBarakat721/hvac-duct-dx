# HVAC Duct DX

HVAC DX equipment and diffuser-selection tools with HAP input handling, catalog-based diffuser screening, and geometry-aware room layout evaluation.

## Diffuser engineering basis

The diffuser workflow is documented in [`docs/ASHRAE_ENGINEERING_BASIS.md`](docs/ASHRAE_ENGINEERING_BASIS.md).

The documented status is:

> **ASHRAE-aligned screening; ADPI not verified.**

The project distinguishes between catalog selection, geometry-aware T50/L screening, and full design verification. Full occupied-zone ADPI, jet-interaction mapping, overhead-heating verification, VAV operating-point verification, and final room acoustic verification require additional validated inputs and are not silently inferred.

## Main diffuser paths

- `main.py` — GUI Diffuser Selection and geometry-aware layout placement.
- `diffuser_calculator.py` — catalog ranking, pressure acceptance, characteristic-length and T50/L screening.
- `diffuser_report.py` — Word/Excel report integration using the GUI result or the same geometry-aware path for fresh rectangular-room calculations.
- `diffuser_cli.py` — command-line selection using the geometry-aware layout path for rectangular rooms.
- `catalog_data.py` — source-linked rule registry with implementation status.

## Validation status

```bash
python3 -m compileall -q .
```

The repository intentionally reports unresolved or unimplemented engineering topics instead of presenting them as verified results.

## Manual equipment overrides

The requested `53QDMT30-708` unit is included in `hvac_equipment.sqlite` as a
manual override. Its source record is documented in
`data/manual_unit_overrides.json` and contains only the user-provided values:
26,540 Btu/h, 220/1/50, 3/4 in gas × 3/8 in liquid piping, and 790 CFM at
High speed and 0.16 in.wg. Unprovided cooling, heating, sound, and fan-curve
values are intentionally not inferred.
