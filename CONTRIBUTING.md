# Contributing

## Development setup

Use Python 3.11 or 3.12 in a virtual environment:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

For optional features:

```bash
python -m pip install -e ".[ocr]"       # EasyOCR; downloads torch
python -m pip install -e ".[room3d]"    # PyVista/VTK 3D view
```

On Ubuntu/Debian, install `libxcb-cursor0` for Qt and `tesseract-ocr` for OCR.

## Checks before a pull request

```bash
python -m compileall -q .
python -m pytest -q
ruff check .
```

Set `QT_QPA_PLATFORM=offscreen` for headless GUI tests.

## Engineering and data rules

Do not add customer-identifying HAP reports, proprietary manuals, copyrighted
catalog pages, or extracted book chapters. Use anonymized, minimal fixtures and
record the source/licensing status for any dataset added to the repository.
Diffuser results are screening results: the project must not claim that T50/L is
verified ADPI or that catalog pressure is complete system static pressure.

Every new engineering rule needs a source, status, regression test, and a clear
statement of whether it is product-specific or an engineering decision.
