from pathlib import Path

import ezdxf

from duct_design.cad import (
    CadNetworkMapping, CadTerminalMapping, EngineeringCategory, LayerMapping,
    build_network_from_cad, export_dxf, geometry_diagnostics, import_dxf,
)
from duct_design import DuctDesignOptions, calculate_duct_network


def options():
    return DuctDesignOptions(max_velocity_fpm=100_000, max_friction_inwg_100ft=10,
                             min_width_in=8, max_width_in=8, min_height_in=8, max_height_in=8)


def make_dxf(path: Path):
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    a = msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "RETURN_DUCT"})
    b = msp.add_line((20, 0), (10, 0), dxfattribs={"layer": "RETURN_DUCT"})
    t1 = msp.add_circle((0, 0), 1, dxfattribs={"layer": "GRILLES"})
    t2 = msp.add_circle((20, 0), 1, dxfattribs={"layer": "GRILLES"})
    msp.add_lwpolyline([(0, 10), (5, 10), (5, 15)], dxfattribs={"layer": "CENTERLINE"})
    msp.add_arc((30, 30), 5, 0, 90, dxfattribs={"layer": "ANNOTATION"})
    msp.add_text("RETURN", dxfattribs={"layer": "ANNOTATION", "insert": (1, 1)})
    doc.saveas(path)
    return a.dxf.handle, b.dxf.handle, t1.dxf.handle, t2.dxf.handle


def test_dxf_import_extracts_entities_layers_units_and_validation(tmp_path):
    make_dxf(tmp_path / "plan.dxf")
    cad = import_dxf(tmp_path / "plan.dxf")
    assert cad.units == "inches"
    assert {item.kind.value for item in cad.entities} >= {"LINE", "LWPOLYLINE", "ARC", "CIRCLE", "TEXT"}
    assert {"RETURN_DUCT", "GRILLES", "CENTERLINE"} <= set(cad.layers)
    assert not [item for item in geometry_diagnostics(cad) if item.severity == "ERROR"]


def test_cad_mapping_requires_confirmed_layers_and_creates_return_network(tmp_path):
    h1, h2, t1, t2 = make_dxf(tmp_path / "plan.dxf")
    cad = import_dxf(tmp_path / "plan.dxf")
    layers = LayerMapping({"RETURN_DUCT": EngineeringCategory.RETURN_DUCT, "GRILLES": EngineeringCategory.GRILLE})
    mapping = CadNetworkMapping(
        "CAD-RET", "RETURN", (10, 0), (h1, h2),
        (CadTerminalMapping(t1, "RG1", 400, (0, 0), EngineeringCategory.GRILLE),
         CadTerminalMapping(t2, "RG2", 600, (20, 0), EngineeringCategory.GRILLE)),
    )
    network, diagnostics = build_network_from_cad(cad, mapping, layers)
    assert diagnostics == ()
    assert network is not None
    assert network.sink_node_id == network.source_node_id
    assert calculate_duct_network(network, options()).calculated_airflow_cfm == 1000


def test_dxf_export_round_trip_preserves_supported_geometry(tmp_path):
    make_dxf(tmp_path / "source.dxf")
    cad = import_dxf(tmp_path / "source.dxf")
    exported = export_dxf(tmp_path / "exported.dxf", cad)
    round_trip = import_dxf(exported)
    assert exported.exists()
    assert len(round_trip.entities) >= 6
