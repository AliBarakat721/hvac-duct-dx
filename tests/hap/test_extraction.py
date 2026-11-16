from pathlib import Path

from extractors import RTFNativeText
from hap import FileDetector, SectionSplitter

ROOT = Path(__file__).parents[2]


def test_g01_rtf_extracts_text_and_boundaries():
    text = RTFNativeText().extract(ROOT / "tests/hap/fixtures/G01.RTF")
    assert "Central Cooling Coil Sizing Data" in text
    sections = SectionSplitter().split(text)
    assert {"air_system_information", "cooling_sizing", "heating_sizing", "fan_sizing", "ventilation"} <= set(sections)
    assert sections["cooling_sizing"].start_line == 38
    assert sections["cooling_sizing"].end_line > sections["cooling_sizing"].start_line


def test_file_detector_rejects_unknown_extension(tmp_path):
    from hap import UnsupportedReportError
    path = tmp_path / "report.docx"
    path.write_text("not a HAP report")
    try:
        FileDetector().detect(path)
    except UnsupportedReportError as exc:
        assert ".docx" in str(exc)
    else:
        raise AssertionError("unsupported extension was accepted")
