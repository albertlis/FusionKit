"""Demo bracket body: plate, upright wall with filleted outer corners, two gussets, gable-roof wall slot with
chamfered corners, bed chamfer, warp grooves, 2x2 mounting holes.

Every timeline builder gets a snake_case label. Holes are cut last: a later join would close them.
"""

import adsk.core
import adsk.fusion

from fusion_kit import features, holes, sketch
from fusion_kit.component import new_comp
from fusion_kit.enums import NEGATIVE_EXTENT

COMP_NAME = "demo_bracket"

# Outer vertical corners of plate and wall ends, (x, y) mm expressions. Wall inner face: y = plate_width - wall_thickness.
OUTER_CORNERS: list[tuple[str, str]] = [
    ("0 mm", "0 mm"),
    ("plate_length", "0 mm"),
    ("0 mm", "plate_width - wall_thickness"),
    ("plate_length", "plate_width - wall_thickness"),
    ("0 mm", "plate_width"),
    ("plate_length", "plate_width"),
]


def new_part(root: adsk.fusion.Component) -> adsk.fusion.Component:
    return new_comp(root, COMP_NAME)


def only_body(comp: adsk.fusion.Component) -> adsk.fusion.BRepBody:
    if comp.bRepBodies.count != 1:
        raise RuntimeError(f"expected 1 body in {COMP_NAME}, got {comp.bRepBodies.count}")
    return comp.bRepBodies.item(0)


def _vertical_edges_at(body: adsk.fusion.BRepBody, points: list[tuple[str, str]]) -> list[adsk.fusion.BRepEdge]:
    """Z-parallel edges at each (x, y) corner (mm expressions). Raises if a corner has no vertical edge."""
    vertical = features.select_edges(body, axis="z")
    out: list[adsk.fusion.BRepEdge] = []
    for x_expr, y_expr in points:
        near = {e.tempId for e in features.edges_at_xy(body, sketch.mm(x_expr), sketch.mm(y_expr))}
        hits = [e for e in vertical if e.tempId in near]
        if not hits:
            raise RuntimeError(f"no vertical edge at x={x_expr}, y={y_expr}")
        out.extend(hits)
    return out


def plate(comp: adsk.fusion.Component) -> adsk.fusion.BRepBody:
    s = sketch.rect_xy_sketch(comp, "plate_sketch", "0 mm", "0 mm", "plate_length", "plate_width")
    feat = features.extrude(comp, features.profile(s), "plate_thickness", label="plate")
    return feat.bodies.item(0)


def wall(comp: adsk.fusion.Component, body: adsk.fusion.BRepBody) -> adsk.fusion.BRepBody:
    s = sketch.rect_xy_sketch(
        comp, "wall_sketch", "0 mm", "plate_width - wall_thickness", "plate_length", "plate_width"
    )
    feat = features.extrude(comp, features.profile(s), "wall_height", op="join", bodies=[body], label="wall")
    # Fillet after the join: plate corners and wall ends are one body. DfAM vertical_corner: fillet >= 3 mm passes.
    corners = _vertical_edges_at(feat.bodies.item(0), OUTER_CORNERS)
    fillet = features.fillet(comp, corners, "vertical_corner_fillet_radius", label="outer_corner_fillet")
    return fillet.bodies.item(0)


def gussets(comp: adsk.fusion.Component, body: adsk.fusion.BRepBody) -> adsk.fusion.BRepBody:
    # ONE seed gusset at +gusset_x_offset from plate centre, mirrored about the plate centre plane.
    seed_plane = features.axis_plane(comp, "x", "plate_length / 2 + gusset_x_offset", 75.0, label="gusset_plane")
    s = sketch.new_sketch(comp, seed_plane, label="gusset_sketch")
    sketch.param_poly(
        s,
        [
            ("plate_length / 2 + gusset_x_offset", "plate_width - wall_thickness", "plate_thickness"),
            ("plate_length / 2 + gusset_x_offset", "plate_width - wall_thickness - gusset_size", "plate_thickness"),
            ("plate_length / 2 + gusset_x_offset", "plate_width - wall_thickness", "plate_thickness + gusset_size"),
        ],
    )
    seed = features.extrude(
        comp, features.profile(s), "gusset_thickness", symmetric=True, full_length=True, label="gusset"
    )
    seed_body = seed.bodies.item(0)
    mid = features.axis_plane(comp, "x", "plate_length / 2", 45.0, label="mid_plane")
    mirrored = features.mirror_bodies(comp, [seed_body], mid, label="gusset_mirror")
    joined = features.combine(comp, body, [seed_body, *mirrored], op="join", label="join_gussets")
    return joined.bodies.item(0)


def slot(comp: adsk.fusion.Component, body: adsk.fusion.BRepBody) -> adsk.fusion.BRepBody:
    # Gable-roof slot: vertical sides of slot_width, pointed roof rising slot_roof_rise. No flat ceiling (no bridge).
    s = sketch.new_sketch(comp, comp.xZConstructionPlane, label="slot_sketch")
    sketch.param_poly(
        s,
        [
            ("plate_length / 2 - slot_length / 2", "0 mm", "slot_center_height - slot_width / 2"),
            ("plate_length / 2 + slot_length / 2", "0 mm", "slot_center_height - slot_width / 2"),
            ("plate_length / 2 + slot_length / 2", "0 mm", "slot_center_height + slot_width / 2"),
            ("plate_length / 2", "0 mm", "slot_center_height + slot_width / 2 + slot_roof_rise"),
            ("plate_length / 2 - slot_length / 2", "0 mm", "slot_center_height + slot_width / 2"),
        ],
    )
    feat = features.extrude(
        comp,
        features.profile(s),
        "200 mm",
        op="cut",
        symmetric=True,
        full_length=True,
        bodies=[body],
        label="wall_slot",
    )
    cut = feat.bodies.item(0)
    # Slot vertical corners at both slot ends, on wall inner and outer face. DfAM vertical_corner: chamfer passes.
    x_left = "plate_length / 2 - slot_length / 2"
    x_right = "plate_length / 2 + slot_length / 2"
    corners = _vertical_edges_at(
        cut,
        [
            (x_left, "plate_width - wall_thickness"),
            (x_left, "plate_width"),
            (x_right, "plate_width - wall_thickness"),
            (x_right, "plate_width"),
        ],
    )
    chamfer = features.chamfer(comp, corners, "slot_corner_chamfer_size", label="slot_corner_chamfer")
    return chamfer.bodies.item(0)


def bed_chamfer(comp: adsk.fusion.Component, body: adsk.fusion.BRepBody) -> adsk.fusion.BRepBody:
    feat = features.bed_chamfer(comp, body, "1 mm", label="bed_chamfer")
    return feat.bodies.item(0)


def warp_grooves(comp: adsk.fusion.Component, body: adsk.fusion.BRepBody) -> None:
    features.warp_relief_grooves(comp, body, label="warp_grooves")


def mounting_holes(comp: adsk.fusion.Component) -> None:
    # ONE seed hole dimensioned to the corner offset, then a 2x2 grid pattern.
    s = sketch.new_sketch(comp, "xy", label="mounting_hole_sketch")
    c = sketch.circle(s, 8.0, 8.0, 2.75, diameter="mounting_hole_diameter")
    sketch.anchor(s, c.centerSketchPoint, h="hole_edge_offset", v="hole_edge_offset")
    seed = holes.hole_simple(
        comp,
        [c.centerSketchPoint],
        "mounting_hole_diameter",
        label="mounting_hole",
        through_dir=NEGATIVE_EXTENT,
    )
    features.pattern_grid(
        comp,
        [seed],
        comp.xConstructionAxis,
        "2",
        "plate_length - 2 * hole_edge_offset",
        comp.yConstructionAxis,
        "2",
        "plate_width - 2 * hole_edge_offset",
        label="mounting_hole_grid",
    )
