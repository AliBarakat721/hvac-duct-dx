# HVAC Duct DX

HVAC Duct DX is a Python toolkit and desktop application for Carrier HAP report
import, DX equipment selection, catalog-based diffuser screening, and
geometry-aware diffuser layout evaluation.

Current application version: **0.1.0**. Reports and the GUI expose this version
so an engineering output can be traced to a software release.

## Scope and engineering status

The diffuser workflow is **ASHRAE-aligned screening; ADPI not verified**. It
separates catalog selection, geometry-aware T50/L screening, and full design
verification. Full occupied-zone ADPI, jet-interaction mapping,
overhead-heating verification, VAV operating-point verification, complete duct
system pressure, and final room acoustic verification require additional
validated inputs and are not silently inferred.

Catalog pressure is catalog total-pressure loss; it is not complete branch or
external static pressure. The current rule registry also marks unresolved NC
basis and terminal-velocity definitions explicitly.

## Installation

Python 3.11 and 3.12 are supported; Python 3.12 is recommended. A virtual
environment is the safest installation method:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

On Ubuntu/Debian, Qt may require `libxcb-cursor0`, and Tesseract OCR requires
`tesseract-ocr`:

```bash
sudo apt-get install libxcb-cursor0 tesseract-ocr
```

Optional features are deliberately not part of the core install because they
pull large dependencies:

```bash
python -m pip install -e ".[ocr]"       # EasyOCR / torch
python -m pip install -e ".[room3d]"    # PyVista / VTK 3D room view
python -m pip install -e ".[dev]"       # pytest and Ruff
```

## Running

The desktop application uses the committed SQLite database by default:

```bash
python main.py
```

The database can be placed elsewhere for packaged deployments:

```bash
HVAC_DX_DATABASE=/path/to/hvac_equipment.sqlite python main.py
```

The command-line diffuser workflow supports rectangular rooms and batch CSV
input:

```bash
python diffuser_cli.py --cfm 995 --width 33.5 --length 21.6 --height 9.2 \
  --type square --application conference
python diffuser_cli.py --batch systems.csv
```

The database builder reads the committed QDMT workbook and optional Miraco and
rooftop sources, validates the temporary database, and replaces the output only
after a successful build:

```bash
python build_db.py --dry-run
python build_db.py
python build_db.py --validate-only --db hvac_equipment.sqlite
```

The default QDMT workbook is `data/Ceiling Concealed Ducted Split Systems.xlsx`.
Use `--qdmt-xlsx` when working with a separately licensed source workbook.

## Supported input and output

HAP input supports RTF and PDF extraction plus plain text parsing. The GUI can
also load saved `.ductdx` project files. Selection outputs include CSV, Excel,
Word, PDF, HTML, JSON, Markdown, and AutoCAD DXF schedules. Word and diffuser
reports retain the screening disclaimers and software version.

## Tests and quality checks

```bash
python -m compileall -q .
python -m pytest -q
ruff check .
```

GUI tests use Qt offscreen mode in CI. They are skipped in a minimal local
environment when PySide6 is unavailable.

## Data and licensing

The source code is distributed under the repository's `LICENSE`. Input reports,
manufacturer catalogues, extracted reference text, and spreadsheets may have
separate copyright or redistribution restrictions. Only use data that you own
or are licensed to share. Customer HAP reports must be anonymized before being
used as fixtures. The repository's engineering basis is documented in
`docs/ASHRAE_ENGINEERING_BASIS.md`; it does not replace licensed standards,
manufacturer documentation, or an engineer's final design review.

## Known limitations

The current parser has one minimal golden fixture plus synthetic multi-zone
coverage. More legally shareable HAP layouts are needed before claiming broad
format coverage. VAV min/max flow, overhead heating, close-outlet NC
accumulation, and physically validated jet collision remain documented
post-v1 work. See `docs/HAP_ARCHITECTURE_GAP_ANALYSIS.md` and
`docs/ASHRAE_ENGINEERING_BASIS.md`.

See `CONTRIBUTING.md` for development rules and data-handling requirements.
