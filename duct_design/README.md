# `duct_design` package

This package contains testable safety primitives corresponding to the six critical issues listed in the supplied code review: idempotent note construction, safe DXF/DWG error boundaries, perpendicular edge-label placement, validated duct-height limits, exception-safe calculation, and a meaningful `_ensure()` result.

The repository at the time this package was added contains no `duct_design_page.py`, `size_rect`, `run_design`, `export_dxf`, or duct-sizing UI/engine to patch in place. Consequently, `duct_design_page_fixed.py` is an integration-ready controller/helper module, not a replacement for a missing page and not wired into the desktop UI. Once the original page/engine is supplied, these helpers can be connected to its inputs, exporters and Qt message boxes without inventing its data model.

This package does not yet perform duct sizing and does not claim SMACNA compliance. The application-level README remains authoritative about supported product scope.
