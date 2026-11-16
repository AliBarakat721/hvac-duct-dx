# Changelog

## Unreleased

- Moved the HAP smoke-test fixture under `tests/hap/fixtures/`.
- Added shared application version metadata and included it in the GUI title/About text and Word report footer.
- Added a `pyproject.toml` with CLI entry points and optional `ocr`, `room3d`, and `dev` extras.
- Removed EasyOCR and VTK/PyVista from the core requirements.
- Fixed the database rebuild default to use the QDMT workbook committed in `data/`.
- Added clearer GUI errors when the equipment database cannot be opened.
- Updated HAP architecture documentation status and CI guidance.

## 0.1.0

Initial public repository baseline: HAP extraction, DX equipment selection, diffuser screening/layout, reports, and project persistence.
