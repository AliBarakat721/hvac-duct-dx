# HVAC Duct DX — HAP Parser Architecture Gap Analysis

Date: 2026-11-16
Repository: `AliBarakat721/hvac-duct-dx`
Baseline: `1fa5f7ad6d8f77e94e009fbdfb42a4bf82314b5c`

## Executive summary

The repository already has a working, legacy-compatible HAP extraction path, but it is a flat system-level parser rather than the structured HAP report engine described in the design notes. The safe implementation strategy is to preserve the existing API and UI behavior, then add a new `hap/` package beside it. The new package should produce structured, traceable engineering data and expose an adapter back to the current equipment-selection workflow.

No source files or `main` were modified during this audit.

## Current repository state

### Existing HAP-related components

- `extractors.py`: multiple RTF/text/PDF/OCR extraction paths. The RTF extractor includes HAP-specific whitespace normalization.
- `hap_parser.py`: one module containing numeric normalization, heuristic candidate scoring, engineering warnings, the legacy `parse_hap()` tuple API, and `parse_all_systems()`.
- `main.py`: imports `parse_hap`, `parse_hap_detailed`, and `parse_all_systems`; the GUI depends on the current dictionary/tuple contracts.
- `manual_input.py`: also calls the legacy parser functions.
- `G01.RTF`: a real report fixture containing Air System Information, Central Cooling Coil Sizing Data, Heating Coil Sizing Data, Fan Sizing Data, Outdoor Ventilation Air Data, Zone Sizing, Space Loads and Airflows, ventilation analysis, and design-load summaries.
- `project_persistence.py`: versioned JSON persistence for equipment, diffuser, room, and layout state. It is not currently a HAP domain store.
- `tests/`: GUI smoke, catalog, diffuser, and persistence/regression tests. There is no dedicated `tests/hap/` suite or structured-parser golden-master suite.

### Missing pieces relative to the design notes

- No `hap/` package or separation into ingestion, parsing, models, validation, normalization, persistence, export, and application services.
- No structured entities for reports, projects, systems, zones, spaces, loads, ventilation, equipment, envelope data, or hourly data.
- No source-line/data-lineage object attached to extracted engineering values.
- No section splitter, parser registry, profile mechanism, dual-column parser, or hourly-table parser.
- No explicit distinction between extracted, normalized, calculated, reviewed, and approved values.
- No HAP-specific repository interface or project adapter.
- No HAP-to-CAD mapping adapter or HAP-to-Carrier selection gate.
- No dedicated HAP fixture/expected-output regression tests.

## Existing integration constraints

The current public contracts must remain stable during the migration:

```python
parse_hap(text) -> (total_btu, sensible_btu, cfm, heating_btu, esp)
parse_hap_detailed(text) -> dict
parse_all_systems(text) -> list[dict]
```

`main.py` expects the system dictionaries to contain keys such as `name`, `total_btu`, `sensible_btu`, `cfm`, `heating_btu`, `esp`, `status`, `model`, `floor_area_ft2`, `warnings`, and `critical_errors`. The first implementation should not change these contracts or modify `main.py`.

The current project schema uses `Btu/hr` for thermal load, `CFM` for airflow, and `ft`/`ft²`-oriented room inputs. A HAP model layer should normalize into these existing project units where appropriate, while retaining the original value/unit and source metadata.

## What the real fixture proves

`G01.RTF` is not only a single flat sizing summary. It contains multiple logical report sections and repeated values at different engineering levels:

- System-level cooling coil, heating coil, fan, and ventilation sizing.
- Zone sizing summary.
- Space loads and airflows.
- Ventilation analysis tables.
- Air-system and zone design-load summaries.
- Repeated CFM/load values across sections.

This confirms that the parser must preserve scope (`system`, `zone`, or `space`) and source section. It must not merge all matching values into a single global value or sum system coil loads with space loads.

## Recommended target architecture

### Phase 1 — compatibility foundation

Add a package without removing the legacy module:

```text
hap/
  __init__.py
  exceptions.py
  ingestion/
    __init__.py
    file_detector.py
    rtf_extractor.py
    pdf_extractor.py
    text_extractor.py
    document_normalizer.py
  parsing/
    __init__.py
    parser.py
    section_splitter.py
    key_value_parser.py
    table_parser.py
    parser_registry.py
    profiles/
  models/
    __init__.py
    common.py
    report.py
    project.py
    system.py
    zone.py
    space.py
    loads.py
    equipment.py
    ventilation.py
    hourly_data.py
  normalization/
    __init__.py
    unit_converter.py
    field_normalizer.py
  validation/
    __init__.py
    validator.py
    engineering_validator.py
    consistency_checker.py
  integration/
    __init__.py
    legacy_adapter.py
    carrier_adapter.py
```

The first slice should implement only ingestion, section splitting, source-aware key/value extraction, core Pydantic models, unit conversion, and a result object. It should parse the known `G01.RTF` sections without yet attempting every HAP table type.

### Phase 2 — structured parser coverage

Implement dedicated parsers for:

1. System information.
2. Cooling sizing.
3. Heating sizing.
4. Fan sizing.
5. Outdoor ventilation.
6. Zone sizing.
7. Space loads and airflows.
8. Dual-column cooling/heating tables.
9. Envelope and design-load tables.
10. Hourly tables only after representative fixtures are available.

Each parser should receive a section object and return typed records, not mutate global parser state.

### Phase 3 — validation and approval gate

Validation should return structured issues with severity, code, message, source sections, and affected entity IDs. At minimum:

- Unit compatibility.
- Total ≈ sensible + latent where all values are available.
- SHR consistency.
- Cooling coil/fan/zone airflow consistency, with scope and design-condition checks.
- Missing required fields.
- Ambiguous or duplicate identifiers.
- Protection against double counting system and space loads.

Carrier selection should consume only an explicit approved/usable projection, not raw imported data.

### Phase 4 — persistence and export

Do not introduce SQLite in the first implementation. The current project persistence is a mature JSON store, but its schema is not a natural home for the full HAP object graph. Prefer:

- A standalone HAP JSON document or report payload stored under a versioned project field.
- A repository interface that can later be backed by SQLite.
- JSON/CSV export first.
- Excel export after the structured schema stabilizes.

A future SQLite migration is justified only when multiple reports, import history, or cross-report querying become requirements.

## Proposed first public interfaces

```python
class HAPReportParser:
    def parse(self, file_path: str) -> HAPParseResult: ...

class HAPParseResult:
    report: HAPReport
    project: HAPProject
    sections: dict[str, HAPSection]
    validation: ValidationResult
    warnings: list[ValidationIssue]
    errors: list[ValidationIssue]

class SourceValue:
    value: float | str | None
    original_value: float | str | None
    original_unit: str | None
    normalized_value: float | None
    normalized_unit: str | None
    source_section: str
    source_line: int | None
    extraction_method: str
    confidence: float
    state: str  # extracted, normalized, calculated, reviewed, approved
```

The legacy adapter should translate a validated system-level record into the existing dictionary shape consumed by `main.py`; it should not make the legacy UI depend directly on all new model classes.

## Test plan

Before broad implementation, add a dedicated HAP test tree:

```text
tests/hap/
  ingestion/
  parsing/
  models/
  validation/
  integration/
  fixtures/
    rtf/G01.RTF
    expected/G01.json
    malformed/
```

Required initial tests:

- Existing `parse_hap()` and `parse_all_systems()` behavior remains unchanged.
- `G01.RTF` yields one system with the expected cooling, heating, airflow, ESP, and ventilation values.
- Values retain source section and original units.
- Cooling and heating values remain distinct.
- Zone/space values are not confused with system-coil values.
- Missing optional sections produce warnings, not total import failure.
- Tons and MBH normalize correctly to Btu/hr.
- Total/sensible/SHR validation uses tolerance.
- Cross-section airflow mismatch produces a warning with both source sections.
- Malformed/corrupt input produces typed parser errors.

The environment used for this audit does not have `pytest` installed, so the existing test suite was not executed. The repository declares `pytest>=8.0` in `requirements.txt`.

## Recommended next implementation step

Create branch `feature/hap-structured-report-parser` from `main`, add the package skeleton and typed core models, and implement one vertical slice for `G01.RTF`:

`file detection → extraction → section splitting → system/cooling/heating/fan/ventilation parsing → validation → legacy adapter → golden-master test`.

Do not modify `main.py` in that slice. Integrate with the GUI only after the structured result and compatibility adapter pass regression tests.
