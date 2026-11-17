"""Live smoke for fusion_kit.grip_fit: kind hole + shaft, plus bed "-x" (hole) and bed "+x" (shaft). Run via loader.run_part.

New document only. Root comp "seat": 30x30x10 block, D22 grip hole at origin, build frame (bed -z); tip radius and
lead-in measured here. Sub comp "peg_test": D8 x 12 peg, grip shaft at z 0..10, build frame.
Sub comp "seat_bed_x": same block + hole, then rotated about Y (+90 deg, pivot x=0 z=0) so the grip axis is X and the
hole opens on the X-min face; bed "-x" (bed_chamfer) + 1 mm chamfer on the 4 X-parallel corners (vertical in print frame).
Sub comp "peg_bed_x_plus": peg rotated about Y (-90 deg, pivot 0,0) so its base is the X-max face; bed "+x" via set_bed.
    bed_chamfer on that base is NOT applied: live it fails (ASM_BAD_CONTAINMENT), fins protrude past the base outline.
Prints measurements and every DFAM_FAIL / DFAM_WARN line of report() per comp. Closes the document without saving.
"""

import adsk.core
import adsk.fusion

from fusion_kit.component import new_comp
from fusion_kit.dfam import bed_from_body, check_all, facts_from_body, print_frame_with_tags, set_bed, skip_from_body
from fusion_kit.enums import DOC_TYPE_DESIGN
from fusion_kit.features import bed_chamfer, chamfer, extrude, profile, rotate_body_about_y
from fusion_kit.grip_fit import grip_fit
from fusion_kit.report import inside, report
from fusion_kit.sketch import circle, fix_point, new_sketch, rect_xy_sketch

STEP_MM = 0.01
TOL_MM = 0.02
ZERO = ("0 mm", "0 mm", "0 mm")
FAILS: list[str] = []


def _check(label: str, got: float, want: float) -> None:
    ok = abs(got - want) <= TOL_MM
    print(f"{'PASS' if ok else 'FAIL'} {label}: got {got} mm, expect {want} +/-{TOL_MM}")
    if not ok:
        FAILS.append(label)


def _first_inside(body: adsk.fusion.BRepBody, r0: float, r1: float, z: float) -> float:
    """Smallest radius in [r0, r1] on the +X ray from the axis that is inside body."""
    r = r0
    while r <= r1:
        if inside(body, r, 0.0, z):
            return round(r, 3)
        r += STEP_MM
    raise RuntimeError(f"no solid on +X ray {r0}..{r1} mm at z={z}")


def _last_inside(body: adsk.fusion.BRepBody, r0: float, r1: float, z: float) -> float:
    """Largest radius in [r0, r1] on the +X ray from the axis that is inside body."""
    r = r1
    while r >= r0:
        if inside(body, r, 0.0, z):
            return round(r, 3)
        r -= STEP_MM
    raise RuntimeError(f"no solid on +X ray {r0}..{r1} mm at z={z}")


def _block(comp: adsk.fusion.Component) -> adsk.fusion.BRepBody:
    block_sk = rect_xy_sketch(comp, "block_sketch", "-15 mm", "-15 mm", "15 mm", "15 mm")
    return extrude(comp, profile(block_sk), "10 mm", label="block").bodies.item(0)


def _peg(comp: adsk.fusion.Component) -> adsk.fusion.BRepBody:
    peg_sk = new_sketch(comp, "xy", label="peg_sketch")
    peg_circle = circle(peg_sk, 0.0, 0.0, 4.0, diameter="8 mm")
    fix_point(peg_sk, peg_circle.centerSketchPoint, ZERO)
    return extrude(comp, profile(peg_sk), "12 mm", label="peg").bodies.item(0)


def _x_corner_edges(body: adsk.fusion.BRepBody) -> list[adsk.fusion.BRepEdge]:
    """Straight edges parallel to X at |y| = |z| = 15 mm (block corners after the +90 deg Y rotation)."""
    out: list[adsk.fusion.BRepEdge] = []
    for e in body.edges:
        if adsk.core.Line3D.cast(e.geometry) is None:
            continue
        a, b = e.startVertex.geometry, e.endVertex.geometry
        if abs(a.y - b.y) > 1e-6 or abs(a.z - b.z) > 1e-6 or abs(a.x - b.x) < 1e-6:
            continue
        if abs(abs(a.y) - 1.5) < 1e-4 and abs(abs(a.z) - 1.5) < 1e-4:
            out.append(e)
    return out


def run(_context: str) -> None:
    app = adsk.core.Application.get()
    app.documents.add(DOC_TYPE_DESIGN)
    doc = app.activeDocument
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent
    try:
        # 1. hole, build frame (bed -z): tip radius and lead-in cone
        block = _block(root)
        grip_fit(root, block, ("0 mm", "0 mm"), "0 mm", "10 mm", "22 mm", "hole", label="seat")
        _check("HOLE fin tip r mm (D/2-0.6=10.4)", _first_inside(block, 9.5, 11.5, 5.0), 10.4)
        lead_r = _first_inside(
            block, 9.5, 12.5, 0.2
        )  # fin tip (10.4) must be cut away at the entry: first solid > 11.0
        print(f"HOLE lead-in: first solid on +X ray at z=0.2 = {lead_r} mm (expect > 11.0, fin tip cut)")
        if lead_r <= 11.0:
            FAILS.append("HOLE lead-in cut at z=0.2")

        # 2. shaft, build frame (bed -z): tip radius
        shaft_comp = new_comp(root, "peg_test")
        peg = _peg(shaft_comp)
        grip_fit(shaft_comp, peg, ("0 mm", "0 mm"), "0 mm", "10 mm", "8 mm", "shaft", label="peg_grip")
        _check("SHAFT fin tip r mm (d/2+0.6=4.6)", _last_inside(peg, 0.0, 5.5, 5.0), 4.6)

        # 3. hole on bed -x: grip axis along X, hole opening on X-min face
        seat_x = new_comp(root, "seat_bed_x")
        block_x = _block(seat_x)
        grip_fit(seat_x, block_x, ("0 mm", "0 mm"), "0 mm", "10 mm", "22 mm", "hole", label="seat_x")
        _check("HOLE(bed -x, build frame) fin tip r mm", _first_inside(block_x, 9.5, 11.5, 5.0), 10.4)
        rotate_body_about_y(seat_x, [block_x], x="0 mm", z="0 mm", angle="90 deg", label="seat_x_to_bed")
        block_x = seat_x.bRepBodies.item(0)
        bed_chamfer(seat_x, block_x, "1 mm", label="seat_x_bed_chamfer", bed="-x")
        block_x = seat_x.bRepBodies.item(0)
        chamfer(seat_x, _x_corner_edges(block_x), "1 mm", False, label="seat_x_corner_chamfer")
        print("seat_bed_x bed attr =", bed_from_body(seat_x.bRepBodies.item(0)))
        body_x = seat_x.bRepBodies.item(0)
        frame, tags, unmapped = print_frame_with_tags(body_x)
        print("seat_bed_x tag tempId counts:", {k: len(v) for k, v in (tags or {}).items()}, "unmapped:", unmapped)
        fact_t, holes_t, edges_t = facts_from_body(frame, tags)
        fact_n, holes_n, edges_n = facts_from_body(frame, None)  # control: temp faces read no attributes
        fails_t = [f for f in check_all(fact_t, holes_t, edges_t, skip_from_body(body_x)) if f.level == "FAIL"]
        fails_n = [f for f in check_all(fact_n, holes_n, edges_n, skip_from_body(body_x)) if f.level == "FAIL"]
        print(
            "seat_bed_x on_grip_fin edges: with tags",
            sum(e.on_grip_fin for e in edges_t),
            "| control no tags",
            sum(e.on_grip_fin for e in edges_n),
        )
        print("seat_bed_x FAIL count: with tags", len(fails_t), "| control no tags", len(fails_n))
        text_x = report(seat_x)

        # 4. shaft on bed +x: peg base becomes X-max face
        peg_x = new_comp(root, "peg_bed_x_plus")
        peg_b = _peg(peg_x)
        grip_fit(peg_x, peg_b, ("0 mm", "0 mm"), "0 mm", "10 mm", "8 mm", "shaft", label="peg_x_grip")
        rotate_body_about_y(peg_x, [peg_b], x="0 mm", z="0 mm", angle="-90 deg", label="peg_x_to_bed")
        peg_b = peg_x.bRepBodies.item(0)
        set_bed(peg_b, "+x")
        print("peg_bed_x_plus bed attr =", bed_from_body(peg_x.bRepBodies.item(0)))
        text_p = report(peg_x)

        print("=== report root ===")
        text_root = report(root)
        print("=== report peg_test ===")
        text_peg = report(shaft_comp)
        for name, text in (
            ("root", text_root),
            ("peg_test", text_peg),
            ("seat_bed_x", text_x),
            ("peg_bed_x_plus", text_p),
        ):
            fails = [ln for ln in text.splitlines() if ln.startswith("DFAM_FAIL")]
            print(f"{name}: DFAM_FAIL lines {len(fails)}")
            for ln in fails:
                print("  ", ln)
    finally:
        doc.close(False)
    if FAILS:
        raise RuntimeError(f"grip_fit smoke measurement FAIL: {FAILS}")
