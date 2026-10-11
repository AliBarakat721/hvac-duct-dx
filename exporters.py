"""
exporters.py
============
Multi-format export: CSV, Excel, Word, PDF, HTML, JSON, Markdown, AutoCAD DXF.
Pure functions — no GUI dependencies.
"""
import csv
import datetime


def export_csv(path, headers, rows):
    """Simple CSV with UTF-8 BOM (Excel-friendly)."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def _status_column_index(headers):
    """Return the 0-based index of the status column, or None."""
    for name in ("Status", "Selection Status"):
        try:
            return list(headers).index(name)
        except ValueError:
            continue
    return None


def _status_color(val):
    """Hex colour for a status string, or None."""
    s = str(val)
    if "NOT SUITABLE" in s:
        return "DC2626"
    if "BEST MATCH" in s:
        return "B45309"
    if "SUITABLE" in s:
        return "059669"
    return None


def export_excel(path, headers, rows, summary_text="", extra_sheet_builder=None):
    """Styled Excel with title, colors, freeze panes, auto-fit."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Selection"

    # Title block
    ws["A1"] = "Duct DX Equipment Selection Report"
    ws["A1"].font = Font(bold=True, size=14, color="2563EB")
    ws["A2"] = summary_text
    ws["A2"].font = Font(italic=True, size=10, color="6B7280")

    # Header row
    hdr_row = 4
    blue = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
    for col, h in enumerate(headers, start=1):
        c = ws.cell(row=hdr_row, column=col, value=h)
        c.fill = blue
        c.font = Font(bold=True, color="FFFFFF", size=11)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Data rows
    status_col = _status_column_index(headers)
    for r, row in enumerate(rows, start=hdr_row + 1):
        for c, val in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=val)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            if status_col is not None and (c - 1) == status_col:
                color = _status_color(val)
                if color:
                    cell.font = Font(bold=True, color=color)

    # Auto-fit columns with safety minimum width
    for col in ws.columns:
        mx = 0
        for cell in col:
            if cell.row < hdr_row:
                continue
            if cell.value is not None:
                mx = max(mx, len(str(cell.value)))
        letter = col[0].column_letter
        ws.column_dimensions[letter].width = max(12, min(mx + 3, 45))

    ws.freeze_panes = "A" + str(hdr_row + 1)
    if extra_sheet_builder is not None:
        extra_sheet_builder(wb)
    wb.save(path)


def export_word(path, headers, rows, summary_text=""):
    """Word document with title, styled table, and auto-landscape for wide tables."""
    from docx import Document
    from docx.enum.section import WD_ORIENTATION
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt, RGBColor

    doc = Document()
    
    # Auto-switch to landscape if table is wide (> 7 columns)
    if len(headers) > 7:
        section = doc.sections[0]
        section.orientation = WD_ORIENTATION.LANDSCAPE
        section.page_width, section.page_height = Inches(11.0), Inches(8.5)

    # Title
    h = doc.add_heading("Duct DX Equipment Selection Report", level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Summary
    p = doc.add_paragraph(summary_text)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in p.runs:
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(0x6B, 0x72, 0x80)

    doc.add_paragraph()

    # Table
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Light Grid Accent 1"

    hdr = t.rows[0].cells
    for i, htxt in enumerate(headers):
        hdr[i].text = str(htxt)
        for para in hdr[i].paragraphs:
            for run in para.runs:
                run.bold = True
                run.font.size = Pt(9)

    for row in rows:
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = str(val)
            for para in cells[i].paragraphs:
                for run in para.runs:
                    run.font.size = Pt(8)

    doc.save(path)


def export_dxf(path, headers, rows, summary_text="", title="DUCT DX EQUIPMENT SCHEDULE", secondary_tables=None):
    """
    Export consultant-grade AutoCAD DXF schedule (R12 ASCII).
    Supports:
      - High contrast: Color 7 (white on CAD screen, crisp solid black on PDF/paper)
      - Group 72 = 4 (Middle-Center alignment without cell line collisions)
      - Complete Unicode sanitization (clean ASCII)
      - Secondary tables support (e.g. Diffuser Schedule right below Equipment)
    """
    def sanitize(text):
        if text is None:
            return "---"
        s = str(text).strip()
        s = s.replace(chr(13), " ").replace(chr(10), " ")
        s = s.replace("–", "-").replace("—", "-")
        s = s.replace("‘", "'").replace("’", "'")
        s = s.replace("“", '"').replace("”", '"')
        s = s.replace("×", "x")
        s = s.replace("°", "%%d").replace("±", "%%p").replace("Ø", "%%c").replace("²", "^2")
        return s

    dxf_lines = [
        "0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009", "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "STYLE", "70", "1",
        "0", "STYLE", "2", "HVAC_ROMANS", "70", "0", "40", "0.0", "41", "1.0", "50", "0.0", "71", "0", "42", "2.5", "3", "romans.shx", "4", "",
        "0", "ENDTAB",
        "0", "TABLE", "2", "LAYER", "70", "5",
        "0", "LAYER", "2", "M-HVAC-SCHD-GRID", "70", "0", "62", "8", "6", "CONTINUOUS",
        "0", "LAYER", "2", "M-HVAC-SCHD-TITL", "70", "0", "62", "7", "6", "CONTINUOUS",
        "0", "LAYER", "2", "M-HVAC-SCHD-HEAD", "70", "0", "62", "7", "6", "CONTINUOUS",
        "0", "LAYER", "2", "M-HVAC-SCHD-DATA", "70", "0", "62", "7", "6", "CONTINUOUS",
        "0", "LAYER", "2", "M-HVAC-SCHD-NOTE", "70", "0", "62", "1", "6", "CONTINUOUS",
        "0", "ENDTAB",
        "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES"
    ]

    def line(x1, y1, x2, y2, layer="M-HVAC-SCHD-GRID"):
        dxf_lines.extend([
            "0", "LINE", "8", layer,
            "10", f"{x1:.2f}", "20", f"{y1:.2f}", "30", "0.0",
            "11", f"{x2:.2f}", "21", f"{y2:.2f}", "31", "0.0"
        ])

    def text(x, y, txt, height=2.2, layer="M-HVAC-SCHD-DATA", align_center=True):
        if not txt:
            return
        entry = [
            "0", "TEXT", "8", layer, "7", "HVAC_ROMANS",
            "10", f"{x:.2f}", "20", f"{y:.2f}", "30", "0.0",
            "40", f"{height:.2f}", "1", txt
        ]
        if align_center:
            entry.extend(["72", "4", "11", f"{x:.2f}", "21", f"{y:.2f}", "31", "0.0"])
        dxf_lines.extend(entry)

    def draw_table(tbl_title, tbl_headers, tbl_rows, start_y):
        c_headers = [sanitize(h) for h in tbl_headers]
        c_rows = [[sanitize(v) for v in r] for r in tbl_rows]

        col_w = []
        for col_idx, h in enumerate(c_headers):
            max_l = len(h)
            for r in c_rows:
                if col_idx < len(r):
                    max_l = max(max_l, len(str(r[col_idx])))
            col_w.append(max(24.0, max_l * 2.2 + 8.0))

        tot_w = sum(col_w)
        title_h = 14.0
        hdr_h = 10.0
        row_h = 8.5
        n_r = len(c_rows)
        tbl_total_h = title_h + hdr_h + (n_r * row_h)
        bot_y = start_y - tbl_total_h

        # Horizontal Grid Lines
        line(0, start_y, tot_w, start_y)
        line(0, start_y - title_h, tot_w, start_y - title_h)
        line(0, start_y - (title_h + hdr_h), tot_w, start_y - (title_h + hdr_h))

        for r_i in range(n_r + 1):
            y_line = start_y - (title_h + hdr_h + r_i * row_h)
            line(0, y_line, tot_w, y_line)

        # Vertical Lines
        line(0, start_y, 0, bot_y)
        line(tot_w, start_y, tot_w, bot_y)
        cx = 0.0
        for w in col_w[:-1]:
            cx += w
            line(cx, start_y - title_h, cx, bot_y)

        # Title Text
        text(tot_w / 2.0, start_y - (title_h / 2.0), tbl_title, height=4.2, layer="M-HVAC-SCHD-TITL", align_center=True)

        # Headers
        cx = 0.0
        for col_idx, h in enumerate(c_headers):
            mx = cx + col_w[col_idx] / 2.0
            my = start_y - title_h - (hdr_h / 2.0)
            text(mx, my, h, height=2.4, layer="M-HVAC-SCHD-HEAD", align_center=True)
            cx += col_w[col_idx]

        # Data rows
        for r_i, r_data in enumerate(c_rows):
            my = start_y - (title_h + hdr_h + r_i * row_h) - (row_h / 2.0)
            cx = 0.0
            for col_idx, val in enumerate(r_data):
                if col_idx < len(col_w):
                    mx = cx + col_w[col_idx] / 2.0
                    text(mx, my, val, height=2.1, layer="M-HVAC-SCHD-DATA", align_center=True)
                    cx += col_w[col_idx]

        return bot_y, tot_w

    # Draw Primary Table (Equipment Schedule)
    curr_y, primary_w = draw_table(title, headers, rows, 0.0)

    # Draw Secondary Tables (e.g. Diffuser Schedule) if provided
    max_w = primary_w
    if secondary_tables:
        for st in secondary_tables:
            curr_y -= 14.0  # clean gap between tables
            curr_y, s_w = draw_table(st["title"], st["headers"], st["rows"], curr_y)
            max_w = max(max_w, s_w)

    # General Footnotes
    note_y = curr_y - 8.0
    notes = [
        "NOTES AND INSTALLATION SPECIFICATIONS:",
        "1. ALL CAPACITIES ARE AT DESIGN CONDITIONS: 27%%dC DB / 19%%dC WB INDOOR, 43%%dC DB OUTDOOR AMBIENT.",
        "2. DUCTWORK CONTRACTOR SHALL PROVIDE FLEXIBLE CANVAS CONNECTIONS AT UNIT SUPPLY AND RETURN.",
        "3. ELECTRICAL CONTRACTOR SHALL PROVIDE LOCKABLE DISCONNECT ISOLATOR WITHIN SIGHT OF OUTDOOR UNIT.",
        "4. CONDENSATE DRAIN PIPING SHALL BE SLOPED MINIMUM 1:100 (10mm/m) WITH ACCESSIBLE DEEP-SEAL P-TRAP.",
        "5. REFRIGERANT COPPER PIPING PER ASTM B280 WITH MIN. 19mm CLOSED-CELL ELASTOMERIC THERMAL INSULATION.",
        "6. MECHANICAL CONTRACTOR SHALL VERIFY EXACT CEILING VOID HEIGHT ON SITE BEFORE HOISTING INDOOR UNIT."
    ]
    for n in notes:
        text(3.0, note_y, n, height=2.3, layer="M-HVAC-SCHD-NOTE", align_center=False)
        note_y -= 4.8

    dxf_lines.extend(["0", "ENDSEC", "0", "EOF"])

    with open(path, "w", encoding="ascii", errors="replace") as f:
        f.write(chr(10).join(dxf_lines))


FORMATS = {
    "CSV (.csv)":            ("csv",  "CSV Files (*.csv)"),
    "Excel (.xlsx)":         ("xlsx", "Excel Files (*.xlsx)"),
    "Word (.docx)":          ("docx", "Word Files (*.docx)"),
    "PDF (.pdf)":            ("pdf",  "PDF Files (*.pdf)"),
    "HTML (.html)":          ("html", "HTML Files (*.html)"),
    "JSON (.json)":          ("json", "JSON Files (*.json)"),
    "Markdown (.md)":        ("md",   "Markdown Files (*.md)"),
    "AutoCAD DXF (.dxf)":    ("dxf",  "AutoCAD DXF Files (*.dxf)"),
}

TITLE = "Duct DX Equipment Selection Report"


def _esc(v):
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_html(headers, rows, summary_text="", title=TITLE, font_pt=8):
    """Self-contained HTML table."""
    th = "".join(
        f"<th style='background:#2563EB;color:#fff;padding:4px;border:1px solid #1e3a8a'>"
        f"{_esc(h)}</th>" for h in headers)
    body = []
    for i, row in enumerate(rows):
        bg = "#f3f4f6" if i % 2 else "#ffffff"
        tds = []
        for v in row:
            style = f"padding:3px;border:1px solid #cbd5e1;text-align:center;background:{bg};"
            tds.append(f"<td style='{style}'>{_esc(v)}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
        
    stamp = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    return (
        "<html><head><meta charset='utf-8'>"
        f"<title>{_esc(title)}</title></head>"
        f"<body style='font-family:Arial,Helvetica,sans-serif;font-size:{font_pt}pt;color:#111827'>"
        f"<h2 style='color:#2563EB;margin-bottom:2px'>{_esc(title)}</h2>"
        f"<p style='color:#6b7280;margin-top:0'>{_esc(summary_text)}<br>Generated: {stamp}</p>"
        "<table width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse'>"
        f"<tr>{th}</tr>{''.join(body)}</table></body></html>")


def export_html(path, headers, rows, summary_text=""):
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_html(headers, rows, summary_text, font_pt=10))


def export_pdf(path, headers, rows, summary_text=""):
    """PDF via Qt (QTextDocument -> QPdfWriter). A4 landscape."""
    from PySide6.QtCore import QCoreApplication, QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument

    _ = QCoreApplication.instance() or QCoreApplication([])

    writer = QPdfWriter(str(path))
    page_size = getattr(QPageSize.PageSizeId, 'A4', getattr(QPageSize, 'A4', None))
    orientation = getattr(QPageLayout.Orientation, 'Landscape', getattr(QPageLayout, 'Landscape', None))
    unit = getattr(QPageLayout.Unit, 'Millimeter', getattr(QPageLayout, 'Millimeter', None))
    writer.setPageSize(QPageSize(page_size))
    writer.setPageOrientation(orientation)
    writer.setPageMargins(QMarginsF(10, 10, 10, 10), unit)
    writer.setTitle(TITLE)
    doc = QTextDocument()
    font_pt = 6 if len(headers) > 12 else 8
    doc.setHtml(build_html(headers, rows, summary_text, font_pt=font_pt))
    doc.print_(writer)


def export_json(path, headers, rows, summary_text=""):
    import json
    data = {
        "title": TITLE,
        "summary": summary_text,
        "generated": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "rows": [dict(zip(headers, r, strict=True)) for r in rows],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def export_markdown(path, headers, rows, summary_text=""):
    def cell(v):
        return str(v).replace("|", chr(92) + "|").replace(chr(10), " ")
    lines = [f"# {TITLE}", ""]
    if summary_text:
        lines += [summary_text, ""]
    lines.append("| " + " | ".join(cell(h) for h in headers) + " |")
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for r in rows:
        lines.append("| " + " | ".join(cell(v) for v in r) + " |")
    with open(path, "w", encoding="utf-8") as f:
        f.write(chr(10).join(lines) + chr(10))


def export_table(path, ext, headers, rows, summary_text=""):
    """Dispatch a tabular export by file extension."""
    dispatch = {
        "csv": lambda: export_csv(path, headers, rows),
        "xlsx": lambda: export_excel(path, headers, rows, summary_text),
        "pdf": lambda: export_pdf(path, headers, rows, summary_text),
        "html": lambda: export_html(path, headers, rows, summary_text),
        "json": lambda: export_json(path, headers, rows, summary_text),
        "md": lambda: export_markdown(path, headers, rows, summary_text),
        "dxf": lambda: export_dxf(path, headers, rows, summary_text),
    }
    if ext not in dispatch:
        raise ValueError(
            f"export_table does not support '.{ext}'. "
            f"Supported: {sorted(dispatch)}.")
    dispatch[ext]()
