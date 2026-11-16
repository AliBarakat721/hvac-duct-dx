# HAP Parser Architecture and Current Gaps

**Status:** current-state note, updated for the structured multi-zone parser.

## Current architecture

The repository intentionally keeps two public HAP paths. `hap_parser.py` is the
legacy-compatible facade used by the desktop UI and external callers:

```python
parse_hap(text) -> (total_btu, sensible_btu, cfm, heating_btu, esp)
parse_hap_detailed(text) -> dict
parse_all_systems(text) -> list[dict]
```

The `hap/` package is the structured path. `FileDetector` and the extractors
load RTF/PDF/text input; `SectionSplitter` preserves report sections;
`StructuredHAPParser` produces `HAPProject`, `AirSystem`, `Zone`, and `Space`
objects; and `validate_system` returns structured warnings/errors. Each
`SourceValue` retains the original value and unit, normalized value and unit,
source section, source line, extraction method, confidence, and state.

`hap.legacy_adapter.to_legacy_system()` is the compatibility boundary. The GUI
continues to consume the legacy dictionary shape until an explicit UI migration
is completed. This prevents parser refactors from silently changing equipment
selection behavior.

## Implemented coverage

The structured parser covers system information, cooling and heating sizing,
fan sizing, outdoor ventilation, zone sizing, and space loads for the known HAP
text layout. It preserves zone/space scope and does not merge space loads into
system coil loads. The fixture at `tests/hap/fixtures/G01.RTF` is a minimal
regression fixture, and `tests/hap/expected/G01.json` is its golden structured
result. Multi-zone behavior is also exercised by `tests/hap/test_multizone.py`.

Missing optional sections become warnings, while missing required cooling values
are validation errors. The structured result is not an approval gate for
equipment selection yet.

## Remaining gaps

The parser still needs representative, anonymized and legally shareable
fixtures from multiple HAP export versions, including multi-system reports,
dual-column cooling/heating tables, envelope/design-load tables, and hourly
tables. The current lineage is source-section/source-line metadata rather than
a complete report repository or import history. There is no HAP-specific
persistence schema, approval workflow, HAP-to-CAD mapping, or explicit
Carrier-selection gate based on structured validation.

The legacy parser remains heuristic for OCR-heavy input. Changes to its public
contracts require compatibility tests. New engineering rules must document a
source, scope, status, and regression test.

## Test command

```bash
python -m pytest -q
```

The GUI smoke suite runs when PySide6 is installed. In minimal local
installations it is skipped by `pytest.importorskip`; CI installs the core
requirements and runs it in Qt offscreen mode.
