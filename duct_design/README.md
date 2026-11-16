# `duct_design` package

The package now includes a pure-Python calculation engine (`engine.py`) and a PySide6 tab (`qt_page.py`) integrated into the application's main page stack. It sizes independent, constant-flow rectangular duct sections on a user-selected size grid. It evaluates straight friction through a rectangular-to-round equal-friction correlation and Darcy–Weisbach/Colebrook–White, and reports user-entered aggregate fitting K loss separately.

The engine does not solve connected network topology, branch flow conservation, critical paths, balancing, fan selection, or pressure regain. It does not choose fitting coefficients or verify SMACNA duct construction tables. The UI status `PASS — screening only` means that only the configured maximum velocity and straight-friction limits were met. This is not a construction-ready design or a compliance statement. Read [`docs/DUCT_DESIGN_ENGINE.md`](../docs/DUCT_DESIGN_ENGINE.md) before interpreting results.

`duct_design_page_fixed.py` retains reusable guards for exception-safe calculations and exports, validated height ranges, and perpendicular labels. These helpers remain independent of the current sizing engine and may be used by later drawing/export features.
