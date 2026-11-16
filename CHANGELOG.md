# Changelog

## [Unreleased]

## [1.0.0] - 2026-11-16

### Added
- Added a feature-request issue template that asks for engineering references where applicable.
- Added source-traceable normalized diffuser operating points and a catalog audit covering printed pages, geometry, throw direction, airflow, velocity, pressure, acoustic labels, and verification status.
- Added diffuser catalog integrity tests for point ordering, directional-flow reconciliation, the CCD SI guide, ceiling limits, the 18×18 AK discrepancy, and out-of-range performance warnings.

### Fixed
- Included the runtime SQLite equipment database in built Python wheels and resolved it from packaged resources when no source-tree database is present.
- Surfaced an explicit warning when diffuser performance is evaluated outside the catalog velocity range; endpoint values remain clamped only for diagnostic compatibility.

### Scope and known limitations
- The diffuser workflow is catalog-based selection and geometry-aware screening; ADPI and full design compliance are not verified.
- This release does not size supply ducts from friction rate or implement Darcy-Weisbach/Colebrook-White, duct-material roughness selection, or commercial duct-size rounding. It is not a substitute for duct-sizing software or final engineering review.
- Vendor questions about the diffuser throw definitions, acoustic basis, the 18×18 AK value, and differences between catalog and legacy airflow values remain documented for supplier confirmation.

## [0.1.0] - Initial public repository baseline

- Moved the HAP smoke-test fixture under `tests/hap/fixtures/`.
- Added shared application version metadata and included it in the GUI title/About text and Word report footer.
- Added a `pyproject.toml` with CLI entry points and optional `ocr`, `room3d`, and `dev` extras.
- Removed EasyOCR and VTK/PyVista from the core requirements.
- Fixed the database rebuild default to use the QDMT workbook committed in `data/`.
- Added clearer GUI errors when the equipment database cannot be opened.
- Updated HAP architecture documentation status and CI guidance.

Initial public repository baseline: HAP extraction, DX equipment selection, diffuser screening/layout, reports, and project persistence.
