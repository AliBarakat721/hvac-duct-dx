# Data sources and redistribution policy

The SQLite database and workbook files in `data/` are application inputs, not
part of the Python source license. Before a public release, the repository owner
must confirm the right to redistribute each manufacturer workbook and extracted
reference text. If a source is only licensed for internal use, keep it outside
the public repository and pass it to `build_db.py` with `--qdmt-xlsx` or the
other source options.

HAP reports in `tests/hap/fixtures/` must be anonymized and limited to data that
can legally be shared. Do not add customer names, project addresses, serial
numbers, or proprietary manuals. The engineering notes cite standards and
manufacturer material for traceability; they do not grant redistribution rights
to those sources.
