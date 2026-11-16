"""Live smoke test for fusion_kit (run inside Fusion via fusion_kit.loader.run_part).

Not a pytest module (name does not start with test_). Creates a NEW unsaved document
and closes it at the end with doc.close(False).
"""

import math

import adsk.core
import adsk.fusion

from fusion_kit.component import new_comp
from fusion_kit.enums import DOC_TYPE_DESIGN, PARAMETRIC_DESIGN
from fusion_kit.expr import Ex
from fusion_kit.features import (
    bed_chamfer,
    extrude,
    fillet,
    pattern_circular,
    profile,
    revolve,
    select_edges,
)
from fusion_kit.report import report
from fusion_kit.sketch import anchor, circle, dim, new_sketch, rect
from fusion_kit.user_params import ensure_params

P3 = adsk.core.Point3D.create


def _bbox_mm(comp: adsk.fusion.Component) -> str:
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for body in comp.bRepBodies:
        bb = body.boundingBox
        xs += [bb.minPoint.x, bb.maxPoint.x]
        ys += [bb.minPoint.y, bb.maxPoint.y]
        zs += [bb.minPoint.z, bb.maxPoint.z]
    return (
        f"x[{min(xs) * 10:.2f},{max(xs) * 10:.2f}] "
        f"y[{min(ys) * 10:.2f},{max(ys) * 10:.2f}] "
        f"z[{min(zs) * 10:.2f},{max(zs) * 10:.2f}] mm"
    )


def _pc(comp: adsk.fusion.Component, x_cm: float, y_cm: float, z_cm: float) -> str:
    body = comp.bRepBodies.item(0)
    assert body is not None
    return f"{body.pointContainment(P3(x_cm, y_cm, z_cm))}"


def _vol_cm3(comp: adsk.fusion.Component) -> float:
    return sum(b.physicalProperties.volume for b in comp.bRepBodies)


def _faces(comp: adsk.fusion.Component) -> int:
    return sum(b.faces.count for b in comp.bRepBodies)


def _is_plate_corner(e: adsk.fusion.BRepEdge) -> bool:
    """Vertical edge whose both ends sit on a plate outline corner (x in {0,6} cm, y in {0,4} cm)."""
    g = e.geometry
    if not isinstance(g, adsk.core.Line3D):
        return False
    return all(
        any(abs(p.x - cx) < 1e-4 for cx in (0.0, 6.0)) and any(abs(p.y - cy) < 1e-4 for cy in (0.0, 4.0))
        for p in (g.startPoint, g.endPoint)
    )


def run(_context: str) -> None:
    app = adsk.core.Application.get()
    print(f"SMOKE docs before: {app.documents.count}")
    doc = app.documents.add(DOC_TYPE_DESIGN)
    print(f"SMOKE doc opened: {doc.name}")
    design = adsk.fusion.Design.cast(app.activeProduct)
    assert design is not None
    design.designType = PARAMETRIC_DESIGN

    ensure_params(
        design,
        {
            "plate_length": ("60 mm", "mm", "plate length"),
            "plate_width": ("40 mm", "mm", "plate width"),
            "plate_thickness": ("5 mm", "mm", "plate thickness"),
            "hole_diameter": ("10 mm", "mm", "hole diameter"),
            "sleeve_outer_radius": ("8 mm", "mm", "sleeve outer radius"),
            "sleeve_inner_radius": ("5 mm", "mm", "sleeve inner radius"),
            "sleeve_length": ("20 mm", "mm", "sleeve length"),
        },
    )

    L = Ex("plate_length", 60.0, False, "mm")
    W = Ex("plate_width", 40.0, False, "mm")
    T = Ex("plate_thickness", 5.0, False, "mm")
    D = Ex("hole_diameter", 10.0, False, "mm")
    RO = Ex("sleeve_outer_radius", 8.0, False, "mm")
    RI = Ex("sleeve_inner_radius", 5.0, False, "mm")
    SL = Ex("sleeve_length", 20.0, False, "mm")

    # ---- plate ----
    plate = new_comp(design.rootComponent, "plate")
    s = new_sketch(plate, "xy", label="plate_sketch")
    lines = rect(s, (0.0, 0.0, 0.0), (60.0, 40.0, 0.0))
    dim(s, lines[0].startSketchPoint, lines[0].endSketchPoint, L.e)
    dim(s, lines[1].startSketchPoint, lines[1].endSketchPoint, W.e)
    anchor(s, lines[0].startSketchPoint)
    extrude(plate, profile(s), T, op="new", label="plate_body")

    hs = new_sketch(plate, "xy", label="plate_hole_sketch")
    hc = circle(hs, 30.0, 20.0, 5.0, diameter=D)
    anchor(hs, hc.centerSketchPoint, L / 2, W / 2)
    extrude(plate, profile(hs, outer=False), T, op="cut", label="plate_hole_cut")

    # ---- sleeve (XZ plane, model coords) ----
    sleeve = new_comp(design.rootComponent, "sleeve")
    xz = new_sketch(sleeve, "xz", label="sleeve_sketch")
    sl = rect(xz, (5.0, 0.0, 0.0), (8.0, 0.0, 20.0))
    dim(xz, sl[0].startSketchPoint, sl[0].endSketchPoint, (RO - RI).e)
    dim(xz, sl[1].startSketchPoint, sl[1].endSketchPoint, SL.e)
    anchor(xz, sl[0].startSketchPoint, RI)
    revolve(sleeve, profile(xz), sleeve.zConstructionAxis, "new", label="sleeve_revolve")

    for n, sk in (("plate", s), ("hole", hs), ("sleeve", xz)):
        print(f"{n} sketch isFullyConstrained: {sk.isFullyConstrained}")
    print(f"plate bbox (before): {_bbox_mm(plate)}")
    print(f"sleeve bbox (before): {_bbox_mm(sleeve)}")
    print(f"plate pointContainment hole centre (3.0,2.0,0.25) before: {_pc(plate, 3.0, 2.0, 0.25)}")
    print(f"plate pointContainment solid (1.0,1.0,0.25) before: {_pc(plate, 1.0, 1.0, 0.25)}")
    report(plate)
    report(sleeve)

    # ---- parameter change ----
    p_len = design.userParameters.itemByName("plate_length")
    assert p_len is not None
    p_len.expression = "80 mm"
    print(f"plate bbox (plate_length=80): {_bbox_mm(plate)}")
    print(f"plate pointContainment new hole centre (4.0,2.0,0.25): {_pc(plate, 4.0, 2.0, 0.25)}")
    print(f"plate pointContainment old hole centre (3.0,2.0,0.25): {_pc(plate, 3.0, 2.0, 0.25)}")
    report(plate)
    p_len.expression = "60 mm"
    print(f"plate bbox (restored): {_bbox_mm(plate)}")
    report(plate)

    # ---- (a) block: YZ plane, NEGATIVE model coords, anchored with h/v ----
    ensure_params(
        design,
        {
            "block_extrude": ("10 mm", "mm", "block extrude along X"),
            "block_z_extent": ("10 mm", "mm", "block Z extent"),
            "block_y_extent": ("20 mm", "mm", "block Y extent"),
            "block_corner_z": ("5 mm", "mm", "block corner Z distance from origin"),
            "block_corner_y": (
                "30 mm",
                "mm",
                "block corner |Y| distance from origin (Y negative)",
            ),
        },
    )
    block = new_comp(design.rootComponent, "block")
    bs = new_sketch(block, "yz", label="block_sketch")
    bl = rect(bs, (0.0, -30.0, 5.0), (0.0, -10.0, 15.0))
    dim(bs, bl[0].startSketchPoint, bl[0].endSketchPoint, "block_z_extent")
    dim(bs, bl[1].startSketchPoint, bl[1].endSketchPoint, "block_y_extent")
    anchor(bs, bl[0].startSketchPoint, "block_corner_z", "block_corner_y")
    extrude(block, profile(bs), "block_extrude", label="block_body")
    print(f"a block sketch isFullyConstrained: {bs.isFullyConstrained}")
    print(f"a block bbox (expect y[-30,-10] z[5,15]): {_bbox_mm(block)}")
    print(
        f"a block pointContainment (0.5,-2.0,1.0)cm: {_pc(block, 0.5, -2.0, 1.0)} (-0.5,-2.0,1.0)cm: {_pc(block, -0.5, -2.0, 1.0)}"
    )

    # ---- (b) flange: disc + 1 hole + circular pattern, qty param; (e) forward-ref ensure_params ----
    ensure_params(
        design,
        {
            "flange_hole_bolt_radius": (
                "flange_radius - flange_hole_diameter",
                "mm",
                "bolt circle radius (forward refs)",
            ),
            "flange_radius": ("15 mm", "mm", "flange radius"),
            "flange_hole_diameter": ("4 mm", "mm", "flange hole diameter"),
            "flange_thickness": ("5 mm", "mm", "flange thickness"),
            "flange_hole_count": ("4", "", "flange hole count"),
        },
    )
    flange = new_comp(design.rootComponent, "flange")
    fs = new_sketch(flange, "xy", label="flange_sketch")
    disc = circle(fs, 0.0, 0.0, 15.0, diameter="flange_radius * 2")
    anchor(fs, disc.centerSketchPoint)
    extrude(flange, profile(fs), "flange_thickness", label="flange_body")
    fhs = new_sketch(flange, "xy", label="flange_hole_sketch")
    hole = circle(fhs, 11.0, 0.0, 2.0, diameter="flange_hole_diameter")
    anchor(fhs, hole.centerSketchPoint, "flange_hole_bolt_radius")
    hole_cut = extrude(flange, profile(fhs, outer=False), "flange_thickness", op="cut", label="flange_hole_cut")
    pattern_circular(flange, [hole_cut], flange.zConstructionAxis, "flange_hole_count", label="flange_hole_pattern")
    print(f"b flange bbox: {_bbox_mm(flange)} faces={_faces(flange)} (analytic disc+4 holes vol 3.283 cm3)")
    print(f"b flange vol: {_vol_cm3(flange):.4f} cm3")
    print(
        f"b flange pointContainment solid (0.5,0,0.25)cm: {_pc(flange, 0.5, 0.0, 0.25)} hole centre (1.1,0,0.25)cm: {_pc(flange, 1.1, 0.0, 0.25)}"
    )
    print(
        f"b flange pattern holes at 90deg (0,1.1,0.25)cm: {_pc(flange, 0.0, 1.1, 0.25)} (-1.1,0,0.25)cm: {_pc(flange, -1.1, 0.0, 0.25)}"
    )
    print(
        f"b flange sketch isFullyConstrained: disc={disc.parentSketch.isFullyConstrained} hole={hole.parentSketch.isFullyConstrained}"
    )
    for nm in (
        "flange_hole_bolt_radius",
        "flange_radius",
        "flange_hole_diameter",
        "flange_hole_count",
    ):
        up = design.userParameters.itemByName(nm)
        assert up is not None
        val = up.value * 10 if up.unit == "mm" else up.value
        print(f"e param {nm}: expr={up.expression!r} value={val:.4f} ({'mm' if up.unit == 'mm' else 'unitless'})")

    # ---- (c) fillet vertical convex plate corners via select_edges, tangent_chain default ----
    plate_body = plate.bRepBodies.item(0)
    assert plate_body is not None
    edges_z = select_edges(plate_body, axis="z")
    corners = [e for e in edges_z if _is_plate_corner(e)]
    print(f"c select_edges axis=z: {len(edges_z)} edges, plate outline corners: {len(corners)}")
    v0 = _vol_cm3(plate)
    fillet(plate, corners, "1 mm", label="plate_corner_fillet")
    v1 = _vol_cm3(plate)
    print(f"c fillet r=1mm: vol {v0:.5f} -> {v1:.5f} cm3, delta {v1 - v0:+.5f} cm3 (analytic ~ -0.00429)")

    # ---- (d) bed_chamfer 0.5 mm wall leg, 46 deg default -> bed leg = 0.5 / tan(46 deg) ----
    body2 = plate.bRepBodies.item(0)
    assert body2 is not None
    v2 = _vol_cm3(plate)
    bed_chamfer(plate, body2, "0.5 mm", label="plate_bed_chamfer")
    v3 = _vol_cm3(plate)
    perim_mm = 2 * (60.0 + 40.0) - 4 * (2 * 1.0 - math.pi / 2)  # after r=1 corner fillets
    # two-distance triangle: area = 0.5 * wall_leg * bed_leg, bed_leg = wall_leg / tan(46 deg)
    analytic_cm3 = 0.5 * 0.5 * (0.5 / math.tan(math.radians(46.0))) * perim_mm / 1000.0
    print(
        f"d bed_chamfer 0.5mm wall, 46° two-distance: vol {v2:.5f} -> {v3:.5f} cm3, delta {v3 - v2:+.5f} cm3, analytic ~ -{analytic_cm3:.5f}"
    )

    # ---- (f) report all comps ----
    for c in (plate, sleeve, block, flange):
        report(c)

    doc.close(False)
    print(f"SMOKE docs after close: {app.documents.count}")
    print("SMOKE doc closed (close(False))")
