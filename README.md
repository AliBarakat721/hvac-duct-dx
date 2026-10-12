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
