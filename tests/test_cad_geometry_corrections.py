from pathlib import Path

import ezdxf
import pytest

from duct_design.cad import (
    CadNetworkMapping,
    CadTerminalMapping,
    EngineeringCategory,
    LayerMapping,
    build_network_from_cad,
    export_dxf_report,
    import_dxf,
    polyline_length,
)


def test_multivertex_polyline_uses_sum_of_segment_lengths(tmp_path):
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 1
    entity = doc.modelspace().add_lwpolyline([(0, 0), (3, 4), (6, 4)], dxfattribs={"layer": "SUPPLY_DUCT"})
    path = tmp_path / "multi.dxf"
    doc.saveas(path)
    cad = import_dxf(path)
    imported = next(item for item in cad.entities if item.handle == entity.dxf.handle)
    assert polyline_length(imported) == pytest.approx(8.0)
    assert polyline_length(imported) != pytest.approx((52.0) ** 0.5)


def test_bulge_arc_length_is_preserved_numerically(tmp_path):
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 1
    entity = doc.modelspace().add_lwpolyline(
        [(0, 0, 0, 0, 1), (2, 0, 0, 0, 0)],
        format="xyseb",
        dxfattribs={"layer": "SUPPLY_DUCT"},
    )
    path = tmp_path / "bulge.dxf"
    doc.saveas(path)
    imported = next(item for item in import_dxf(path).entities if item.handle == entity.dxf.handle)
    assert imported.bulges == pytest.approx((1.0, 0.0))
    assert polyline_length(imported) == pytest.approx(3.141592653589793)


def test_closed_polyline_length_includes_closing_edge(tmp_path):
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 1
    entity = doc.modelspace().add_lwpolyline([(0, 0), (1, 0), (1, 1), (0, 1)], close=True)
    path = tmp_path / "closed.dxf"
    doc.saveas(path)
    imported = next(item for item in import_dxf(path).entities if item.handle == entity.dxf.handle)
    assert imported.closed
    assert polyline_length(imported) == pytest.approx(4.0)


def _write_boundary_fixture(path: Path):
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    first = msp.add_line((0, 0), (1, 0.00049), dxfattribs={"layer": "SUPPLY_DUCT"})
    second = msp.add_line((1, 0.00051), (2, 0), dxfattribs={"layer": "SUPPLY_DUCT"})
    terminal = msp.add_circle((2, 0), 0.1, dxfattribs={"layer": "DIFFUSERS"})
    doc.saveas(path)
    return first.dxf.handle, second.dxf.handle, terminal.dxf.handle


def test_endpoint_clustering_connects_points_across_quantization_boundary(tmp_path):
    first, second, terminal = _write_boundary_fixture(tmp_path / "boundary.dxf")
    cad = import_dxf(tmp_path / "boundary.dxf")
    network, diagnostics = build_network_from_cad(
        cad,
        CadNetworkMapping(
            "BOUNDARY", "SUPPLY", (0, 0), (first, second),
            (CadTerminalMapping(terminal, "D1", 100, (2, 0), EngineeringCategory.DIFFUSER),),
            tolerance=0.001,
        ),
        LayerMapping({"SUPPLY_DUCT": EngineeringCategory.SUPPLY_DUCT, "DIFFUSERS": EngineeringCategory.DIFFUSER}),
    )
    assert diagnostics == ()
    assert network is not None
    assert [segment.length_ft for segment in network.segments] == pytest.approx([1.0 / 12.0, 1.0 / 12.0])


def test_mapping_rejects_incompatible_layers_and_bad_terminal_entity(tmp_path):
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    duct = msp.add_line((0, 0), (1, 0), dxfattribs={"layer": "RETURN_DUCT"})
    invalid_terminal = msp.add_line((1, 0), (1, 1), dxfattribs={"layer": "DIFFUSERS"})
    path = tmp_path / "invalid.dxf"
    doc.saveas(path)
    cad = import_dxf(path)
    _, diagnostics = build_network_from_cad(
        cad,
        CadNetworkMapping("BAD", "SUPPLY", (0, 0), (duct.dxf.handle,),
                          (CadTerminalMapping(invalid_terminal.dxf.handle, "D1", 100, (1, 0), EngineeringCategory.DIFFUSER),)),
        LayerMapping({"RETURN_DUCT": EngineeringCategory.RETURN_DUCT, "DIFFUSERS": EngineeringCategory.DIFFUSER}),
    )
    codes = {item.code for item in diagnostics}
    assert "incompatible_duct_layer" in codes
    assert "invalid_terminal_entity" in codes


def test_export_preserves_units_bulges_and_closed_state(tmp_path):
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 1
    doc.header["$INSUNITS"] = 2
    entity = doc.modelspace().add_lwpolyline(
        [(0, 0, 0, 0, 1), (2, 0, 0, 0, 0)], format="xyseb", close=True,
        dxfattribs={"layer": "SUPPLY_DUCT"},
    )
    source = tmp_path / "feet.dxf"
    doc.saveas(source)
    cad = import_dxf(source, target_units="feet")
    result = export_dxf_report(tmp_path / "roundtrip.dxf", cad)
    assert result.diagnostics == ()
    exported = import_dxf(result.path, target_units="feet")
    roundtrip = next(item for item in exported.entities if item.kind.value == "LWPOLYLINE")
    assert exported.units == "feet"
    assert roundtrip.closed
    assert roundtrip.bulges == pytest.approx(entity.get_points("xyseb")[0][4:5] + (0.0,))
    assert polyline_length(roundtrip) == pytest.approx(polyline_length(next(item for item in cad.entities if item.kind.value == "LWPOLYLINE")))
