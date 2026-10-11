"""
extractors.py
=============
Multiple text-extraction engines for HAP reports.
Each extractor returns raw text → fed to parse_hap().

The RTF extractor (RTFNativeText) uses a hand-rolled state machine
instead of a recursive-descent parser. It skips {\\fonttbl}, {\\colortbl}
and — most importantly — {\\pict} groups wholesale. Those groups are where
an 11 MB HAP RTF spends ~90% of its bytes, so skipping them takes the
parse from ~15 s with striprtf down to well under a second.
"""
import logging
import re
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

logger = logging.getLogger(__name__)

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf
    except ImportError:
        pymupdf = None


# ═══════════════════════════════════════════════════════════════
#  RTF → plain text — fast state machine
# ═══════════════════════════════════════════════════════════════

# Matches a control word: \word, \word123, \word-123, with an optional
# trailing space (RTF's delimiter — the space itself is not literal text).
_CONTROL_WORD = re.compile(r"\\([a-zA-Z]+)(-?\d+)?[ ]?")

# Groups whose contents are pure metadata or binary.  Matched against the
# control word that opens the group, e.g. "fonttbl" for {\fonttbl ...}.
# Skipping these groups (especially pict) is where the speedup comes from:
# the ~11 MB image blob inside {\pict ...} is never looked at byte-by-byte.
_SKIP_GROUPS = frozenset({
    # Font / colour / style tables
    "fonttbl", "colortbl", "stylesheet",
    "listtable", "listoverridetable",
    "rsidtbl", "revtbl", "filetbl",
    "latentstyles", "datastore",
    # Document info + generator metadata
    "info", "generator", "xmlnstbl",
    "themedata", "colorschememapping",
    "wgrffmtfilter",
    # Binary blobs
    "pict", "object", "nonshppict",
    "shp", "shpinst", "upr",
    # Header / footer / footnote streams
    "header", "headerr", "headerl", "headerf",
    "footer", "footerr", "footerl", "footerf",
    "footnote", "annotation",
})

# Control words whose whole purpose is to emit a visible character.
# Everything else (\b, \i, \ul, \fs24, \plain, \pard, ...) is formatting
# and gets dropped silently — which is exactly what a text extractor wants.
_CTRL_TO_TEXT = {
    "par": "\n", "line": "\n", "sect": "\n", "page": "\n", "row": "\n",
    "tab": "\t", "cell": "\t",
    "emdash": "\u2014", "endash": "\u2013",
    "lquote": "\u2018", "rquote": "\u2019",
    "ldblquote": "\u201C", "rdblquote": "\u201D",
    "bullet": "\u2022",
    "enspace": "\u2002", "emspace": "\u2003",
}


def rtf_to_text(raw: str) -> str:
    """Convert an RTF document to plain text, fast.

    Three properties of the input drive the design:

    1. RTF has only three special bytes — ``{``, ``}`` and ``\\``.
       Everything between them is literal text, so we bulk-copy runs with
       ``raw[i:j]`` (one C-level slice) instead of stepping char by char.
       That single change takes the hot loop from ~11 million Python
       iterations to a few dozen.

    2. Metadata groups (``{\\fonttbl ...}``, ``{\\colortbl ...}``) and
       binary blobs (``{\\pict ...}``) can be megabytes long and contain
       no user-visible text.  We skip them by counting brace depth, never
       looking at the bytes inside.

    3. Only a handful of control words matter for plain text
       (``\\par`` → newline, ``\\tab`` → tab, ``\\'XX`` → character).
       Everything else is dropped.

    The result preserves line and tab boundaries but not spaces inside
    control-word delimiters — HAP's ``label\\t\\nvalue`` pattern is fixed
    afterwards by :func:`_normalize_rtf_ws`.
    """
    out: list[str] = []
    i = 0
    n = len(raw)
    skipping = 0  # 0 = normal; >0 = brace depth inside a skipped group

    while i < n:
        # ── Skip mode: only count braces, emit nothing ─────────────
        if skipping:
            a = raw.find("{", i)
            b = raw.find("}", i)
            if a == -1 and b == -1:
                break  # malformed — no closing brace found
            if a == -1 or (b != -1 and b < a):
                skipping -= 1
                i = b + 1
            else:
                skipping += 1
                i = a + 1
            continue

        # ── Bulk copy: jump to the next special byte ───────────────
        nxt = n
        k = raw.find("{", i)
        if k != -1 and k < nxt:
            nxt = k
        k = raw.find("}", i)
        if k != -1 and k < nxt:
            nxt = k
        k = raw.find("\\", i)
        if k != -1 and k < nxt:
            nxt = k

        if nxt > i:
            out.append(raw[i:nxt])
            i = nxt
            if i >= n:
                break

        c = raw[i]

        # ── { ──────────────────────────────────────────────────────
        if c == "{":
            i += 1
            # {\* marks an "unknown destination".  In practice every one
            # of these is Word / report-generator metadata — safe to skip.
            if i + 1 < n and raw[i] == "\\" and raw[i + 1] == "*":
                skipping = 1
                i += 2
            continue

        # ── } ──────────────────────────────────────────────────────
        if c == "}":
            i += 1
            continue

        # ── \ ──────────────────────────────────────────────────────
        # Hex escape \'XX — checked first, because the control-word
        # regex does not cover the apostrophe.
        if i + 3 < n and raw[i + 1] == "'":
            try:
                out.append(chr(int(raw[i + 2:i + 4], 16)))
            except ValueError:
                pass
            i += 4
            continue

        m = _CONTROL_WORD.match(raw, i)
        if m:
            word = m.group(1).lower()

            if word in _SKIP_GROUPS:
                skipping = 1
                i = m.end()
                continue

            if word in _CTRL_TO_TEXT:
                out.append(_CTRL_TO_TEXT[word])
                i = m.end()
                continue

            if word == "u":
                # \uN — signed 16-bit code point, followed by a one-
                # character (or \'XX) ANSI fallback that must be eaten.
                code = int(m.group(2) or 0)
                if code < 0:
                    code += 0x10000
                try:
                    out.append(chr(code))
                except (ValueError, OverflowError):
                    # Surrogate halves (from a Word-encoded emoji, etc.)
                    # are not representable as standalone Python chars;
                    # drop them.  HAP reports never contain these.
                    pass
                j = m.end()
                if j < n:
                    if raw[j] == "\\" and j + 3 <= n and raw[j + 1] == "'":
                        j += 4        # \'XX fallback
                    else:
                        j += 1        # single-char fallback
                i = j
                continue

            # Unknown control word — drop it silently.
            i = m.end()
            continue

        # ── Escaped literals: \{ \} \\ \~ \_ \- \<newline> ─────────
        if i + 1 < n:
            c2 = raw[i + 1]
            if c2 in "{}\\":
                out.append(c2)
                i += 2
                continue
            if c2 == "~":
                out.append("\u00A0")      # non-breaking space
                i += 2
                continue
            if c2 == "_":
                out.append("\u2011")      # non-breaking hyphen
                i += 2
                continue
            if c2 in " \n\r":
                # \<space> = literal space; \<newline> = line continuation.
                if c2 == " ":
                    out.append(" ")
                i += 2
                continue

        # Unknown backslash escape — drop both characters.
        i += 2

    return "".join(out)


def _normalize_rtf_ws(s: str) -> str:
    """Collapse the tab/newline pattern HAP's RTF emits between labels.

    After rtf_to_text(), HAP table cells look like::

        Total coil load\\t\\n25.3\\n\\tMBH\\n

    ``parse_hap()`` looks for a whole ``label  value  unit`` on one line,
    so we replace the ``\\t\\n`` and ``\\n\\t`` transitions with two
    spaces, giving::

        Total coil load  25.3  MBH\\n

    Only RTF extraction needs this; PDFs and OCR text never produce it.
    """
    if "\t\n" in s:
        s = s.replace("\t\n", "  ")
    if "\n\t" in s:
        s = s.replace("\n\t", "  ")
    return s


def _read_rtf_bytes(path) -> str:
    """Read an RTF file, trying the encodings HAP actually emits.

    RTF files are nominally 7-bit ASCII with ``\\'XX`` escapes for
    non-ASCII — but some generators write raw cp1252 bytes.  We try
    UTF-8 first, then cp1252, and fall back to latin-1 (which never
    raises) so the extractor can never lose data to a decode error.
    """
    data = Path(path).read_bytes()
    for enc in ("utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


# ═══════════════════════════════════════════════════════════════
#  Extractor base + implementations
# ═══════════════════════════════════════════════════════════════

class BaseExtractor:
    name = "Base"
    description = ""

    def is_available(self):
        return True

    def can_handle(self, path):
        return True

    def extract(self, path):
        raise NotImplementedError


_IMAGE_PDF_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".pdf")


class TesseractOCR(BaseExtractor):
    name = "Tesseract OCR"
    description = "Works on images and scanned PDFs (slow but universal)"

    def can_handle(self, path):
        # Images / PDF only. Without this it inherited BaseExtractor's
        # `return True` and tried to open .RTF files as images.
        return str(path).lower().endswith(_IMAGE_PDF_EXTS)

    def is_available(self):
        if pytesseract is None:
            return False
        try:
            pytesseract.get_tesseract_version()
            return True
        except (OSError, RuntimeError):
            return False

    def extract(self, path):
        path = str(path)
        if path.lower().endswith(".pdf"):
            if pymupdf is None:
                raise RuntimeError("Install pymupdf: pip install pymupdf")
            doc = pymupdf.open(path)
            chunks = []
            for page in doc:
                pix = page.get_pixmap(matrix=pymupdf.Matrix(2.5, 2.5), alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                chunks.append(self._ocr(img))
            doc.close()
            return "\n".join(chunks)
        return self._ocr(Image.open(path))

    def _ocr(self, image):
        if pytesseract is None:
            raise RuntimeError(
                "Tesseract OCR requires pytesseract. Install: pip install pytesseract"
            )
        w, h = image.size
        scale = 4 if max(w, h) < 1200 else 3
        image = image.resize((w * scale, h * scale), Image.Resampling.LANCZOS)
        image = image.convert("L")
        image = ImageEnhance.Contrast(image).enhance(2.6)
        image = ImageEnhance.Sharpness(image).enhance(2.0)
        image = image.filter(ImageFilter.UnsharpMask(radius=1, percent=80, threshold=2))
        texts = []
        errors = []
        for config in ("--psm 4 --oem 3", "--psm 6 --oem 3", "--psm 11 --oem 3"):
            try:
                texts.append(pytesseract.image_to_string(image, config=config))
            except Exception as exc:
                # Tesseract/Pillow failures vary by platform and subprocess
                # backend; retain the retry across PSM modes and log each cause.
                logger.warning(
                    "OCR failed with %s (%s)", config, exc, exc_info=True)
                errors.append(f"{config}: {exc}")
        if not texts:
            raise RuntimeError(
                "Tesseract OCR failed on all PSM modes:\n" + "\n".join(errors)
            )
        return "\n\n===PSM_SEPARATOR===\n\n".join(texts)


class PDFNativeText(BaseExtractor):
    name = "PDF Native Text"
    description = "Fast & exact — only for digital PDFs (not scanned images)"

    def is_available(self):
        return pymupdf is not None

    def can_handle(self, path):
        return str(path).lower().endswith(".pdf")

    def extract(self, path):
        if pymupdf is None:
            raise RuntimeError("Install pymupdf: pip install pymupdf")
        doc = pymupdf.open(str(path))
        chunks = []
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text")
            chunks.append(f"\n=== PAGE {i} ===\n{text}")
        doc.close()
        return "\n".join(chunks)


class EasyOCR(BaseExtractor):
    name = "EasyOCR"
    description = "Alternative OCR (better on some fonts). Install: pip install easyocr"

    _reader = None

    def can_handle(self, path):
        return str(path).lower().endswith(_IMAGE_PDF_EXTS)

    def is_available(self):
        try:
            import easyocr  # noqa
            return True
        except ImportError:
            return False

    def extract(self, path):
        import easyocr
        import numpy as np
        if EasyOCR._reader is None:
            EasyOCR._reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        path = str(path)
        images = []
        if path.lower().endswith(".pdf"):
            if pymupdf is None:
                raise RuntimeError("Install pymupdf")
            doc = pymupdf.open(path)
            for page in doc:
                pix = page.get_pixmap(matrix=pymupdf.Matrix(2.5, 2.5), alpha=False)
                images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
            doc.close()
        else:
            images.append(Image.open(path))
        chunks = []
        for img in images:
            arr = np.array(img)
            results = EasyOCR._reader.readtext(arr, detail=0, paragraph=True)
            chunks.append("\n".join(results))
        return "\n".join(chunks)


class RTFNativeText(BaseExtractor):
    """Fast, dependency-free extractor for HAP's .RTF reports.

    Uses :func:`rtf_to_text` (hand-rolled state machine) and then
    :func:`_normalize_rtf_ws` to repair HAP's label/value line wrapping
    so the resulting text feeds straight into ``parse_hap()`` without any
    further changes to the parser.
    """

    name = "RTF Native Text"
    description = ("Fast & exact for HAP RTF reports "
                   "(11 MB file → under 1 s)")

    def is_available(self):
        return True  # pure Python, no external tools required

    def can_handle(self, path):
        return str(path).lower().endswith(".rtf")

    def extract(self, path):
        raw = _read_rtf_bytes(path)
        return _normalize_rtf_ws(rtf_to_text(raw))


ALL_EXTRACTORS = [TesseractOCR, PDFNativeText, RTFNativeText, EasyOCR]


def get_available_extractors():
    out = []
    for cls in ALL_EXTRACTORS:
        inst = cls()
        if inst.is_available():
            out.append((inst.name, inst))
    return out


def pick_extractor(path, extractors, preferred=None):
    """Choose the engine that should read ``path``.

    ``extractors`` is the {name: instance} map the GUI keeps
    (``App._extractor_map``). The engine the user selected (``preferred``)
    wins if it can handle the file; otherwise the first available engine
    that can is used (e.g. "RTF Native Text" for .RTF).

    Returns ``(name, instance)``, or ``(None, None)`` if nothing can read it.
    """
    inst = extractors.get(preferred)
    if inst is not None and inst.can_handle(path):
        return preferred, inst
    for name, inst in extractors.items():
        if inst.can_handle(path):
            return name, inst
    return None, None
