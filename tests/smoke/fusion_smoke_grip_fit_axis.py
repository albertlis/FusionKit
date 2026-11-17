"""Live smoke for fusion_kit.grip_fit with axis="x".

Case A: hole, axis x, zone X 0..10, entry low   -> lead-in at X=0, fin tip r 10.4. Bed "-x" (hole opens on X=0).
Case B: hole, axis x, zone X 10..20, entry high -> lead-in at X=20, fin tip r 10.4. Bed "+x" (hole opens on X=20).
Case C: shaft, axis x, zone X -8.5..-1.5, entry low (peg tip) -> fin tip r 4.6. Bed "+x" (plate back face, peg up).

DfAM-clean test blocks: bed_chamfer on the bed face outline + 1 mm chamfer on the 4 X-parallel corners
(vertical in print frame, grip axis = print Z). Goal: report() 0 DFAM_FAIL per comp.

Probes: fin tip on the seed-fin ray (radial direction, tangential 0). Relief span on a ray at 11.25 deg
(midway between fins: 16 fins, 22.5 deg pitch), so no fin material is hit.

New document only (documents.add); closed unsaved in finally (doc.close(False)). Run via loader.run_part.
"""

import math
import re

import adsk.core
import adsk.fusion

from fusion_kit.component import new_comp
from fusion_kit.dfam import BedDir
from fusion_kit.enums import DOC_TYPE_DESIGN
from fusion_kit.features import bed_chamfer, chamfer, combine, extrude, profile, rotate_body_about_y
from fusion_kit.grip_fit import EntrySide, GripAxis, _normal_sign, axis_frame, fin_footprint_rad, grip_fit
from fusion_kit.report import inside, report
from fusion_kit.sketch import circle, fix_point, new_sketch, rect_xy_sketch

STEP_MM = 0.01
TOL_MM = 0.02
PROBE_DEG = 11.25
SHAFT_FIN_COUNT = 9  # max_fin_count for d8 relief 0.8 t 0.8 i 0.6 lean 45 (band min gap; 10 overlaps)
_SHAFT_LO, _SHAFT_HI = fin_footprint_rad("shaft", 8.0, 0.8, 0.8, 0.6, math.radians(45.0), 3.5)
SHAFT_PROBE_DEG = math.degrees((_SHAFT_HI + _SHAFT_LO + 2 * math.pi / SHAFT_FIN_COUNT) / 2)  # mid gap at r 3.5
ZERO = ("0 mm", "0 mm", "0 mm")
FAILS: list[str] = []


def _pt(axis: GripAxis, along: float, rad: float, tan: float) -> tuple[float, float, float]:
    """Model-space mm point from (along, radial, tangential) coordinates on the grip axis."""
    a, r, t = axis_frame(axis)
    out = [0.0, 0.0, 0.0]
    out[a] = along
    out[r] = rad
    out[t] = tan
    return (out[0], out[1], out[2])


def _probe(radius: float, deg: float = PROBE_DEG) -> tuple[float, float]:
    """(radial, tangential) of a point at radius on the ray between two fins (deg from the seed ray)."""
    ang = math.radians(deg)
    return radius * math.cos(ang), radius * math.sin(ang)


def _check(label: str, got: float | None, want: float) -> None:
    ok = got is not None and abs(got - want) <= TOL_MM
    print(f"{'PASS' if ok else 'FAIL'} {label}: got {got} mm, expect {want} +/-{TOL_MM}")
    if not ok:
        FAILS.append(label)


def _first_inside(body: adsk.fusion.BRepBody, axis: GripAxis, along: float, r0: float, r1: float) -> float | None:
    """Smallest radius in [r0, r1] on the seed ray (tangential 0) that is inside body, None if none."""
    r = r0
    while r <= r1 + 1e-9:
        if inside(body, *_pt(axis, along, r, 0.0)):
            return round(r, 3)
        r += STEP_MM
    return None


def _last_inside(body: adsk.fusion.BRepBody, axis: GripAxis, along: float, r0: float, r1: float) -> float | None:
    """Largest radius in [r0, r1] on the seed ray that is inside body, None if none."""
    r = r1
    while r >= r0 - 1e-9:
        if inside(body, *_pt(axis, along, r, 0.0)):
            return round(r, 3)
        r -= STEP_MM
    return None


def _not_inside_runs(
    body: adsk.fusion.BRepBody, axis: GripAxis, rad: float, tan: float, a0: float, a1: float
) -> list[tuple[float, float]]:
    """Runs of [a0, a1] (along axis) where the probe point at (rad, tan) is NOT inside body. Ends = first sample outside/inside."""
    runs: list[tuple[float, float]] = []
    start: float | None = None
    a = a0
    while a <= a1 + 1e-9:
        outside = not inside(body, *_pt(axis, a, rad, tan))
        if outside and start is None:
            start = a
        if not outside and start is not None:
            runs.append((round(start, 3), round(a, 3)))
            start = None
        a += STEP_MM
    if start is not None:
        runs.append((round(start, 3), round(a, 3)))
    return runs


def _first_along(
    body: adsk.fusion.BRepBody, axis: GripAxis, rad: float, tan: float, a_from: float, a_to: float
) -> float | None:
    """First along-axis position walking from a_from to a_to where the probe point is inside body."""
    n = round(abs(a_to - a_from) / STEP_MM)
    sgn = 1.0 if a_to >= a_from else -1.0
    for i in range(n + 1):
        a = a_from + sgn * i * STEP_MM
        if inside(body, *_pt(axis, a, rad, tan)):
            return round(a, 3)
    return None


def _plane_sign(comp: adsk.fusion.Component, axis: GripAxis, start_mm: float) -> tuple[int | None, str]:
    """_normal_sign of the grip start plane (axis-normal, origin at start). Returns (sign, description)."""
    along = axis_frame(axis)[0]
    hits: list[tuple[int, str]] = []
    for i in range(comp.constructionPlanes.count):
        plane = comp.constructionPlanes.item(i)
        n = plane.geometry.normal
        o = plane.geometry.origin
        nc = (n.x, n.y, n.z)
        oc = (o.x * 10.0, o.y * 10.0, o.z * 10.0)  # cm -> mm
        if abs(abs(nc[along]) - 1.0) > 1e-6:
            continue
        if abs(oc[along] - start_mm) > 1e-3:
            continue
        hits.append(
            (
                _normal_sign(plane, axis),
                f"normal=({nc[0]:.3f},{nc[1]:.3f},{nc[2]:.3f}) origin_mm=({oc[0]:.3f},{oc[1]:.3f},{oc[2]:.3f})",
            )
        )
    if len(hits) != 1:
        FAILS.append(f"start plane count {len(hits)} for start {start_mm}")
        return None, f"{len(hits)} matching planes"
    return hits[0][0], hits[0][1]


def _timeline_unnamed(text: str) -> str:
    line = next((ln for ln in text.splitlines() if "timeline_unnamed" in ln), "")
    m = re.search(r"timeline_unnamed\D*(\d+)", line)
    if m is None:
        FAILS.append("timeline_unnamed not parsed")
        return f"UNVERIFIED ({line!r})"
    if int(m.group(1)) != 0:
        FAILS.append(f"timeline_unnamed = {m.group(1)}")
    return line


def _dfam_fails(text: str, name: str) -> None:
    fails = [ln for ln in text.splitlines() if ln.startswith("DFAM_FAIL")]
    print(f"{name}: DFAM_FAIL lines {len(fails)}")
    for ln in fails:
        print("  ", ln)
    if fails:
        FAILS.append(f"{name} DFAM_FAIL {len(fails)}")


def _x_corner_edges(body: adsk.fusion.BRepBody) -> list[adsk.fusion.BRepEdge]:
    """Straight edges parallel to X at |y| = |z| = 15 mm (block/plate outer corners)."""
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


def _dfam_clean(comp: adsk.fusion.Component, bed: BedDir, prefix: str) -> None:
    """bed_chamfer on the bed face outline (stores bed attr), then 1 mm chamfer on the X-parallel corners."""
    bed_chamfer(comp, comp.bRepBodies.item(0), "1 mm", label=f"{prefix}_bed_chamfer", bed=bed)
    body = comp.bRepBodies.item(0)
    chamfer(comp, _x_corner_edges(body), "1 mm", False, label=f"{prefix}_corner_chamfer")


def _box(comp: adsk.fusion.Component, x0: str, x1: str, prefix: str) -> adsk.fusion.BRepBody:
    """Block X x0..x1, Y -15..15, Z -15..15 (sketch in XY, extruded 30 mm from Z -15)."""
    sk = rect_xy_sketch(comp, f"{prefix}_box_sketch", x0, "-15 mm", x1, "15 mm")
    return extrude(comp, profile(sk), "30 mm", start_offset="-15 mm", label=f"{prefix}_box").bodies.item(0)


def _case_hole(
    root: adsk.fusion.Component, name: str, start: str, entry: EntrySide, bed: BedDir
) -> adsk.fusion.Component:
    comp = new_comp(root, name)
    block = _box(comp, "0 mm", "20 mm", name)
    grip_fit(
        comp, block, ("0 mm", "0 mm"), start, "10 mm", "22 mm", "hole", axis="x", entry=entry, label=f"{name}_grip"
    )
    _dfam_clean(comp, bed, name)
    return comp


def _case_shaft(root: adsk.fusion.Component, name: str) -> adsk.fusion.Component:
    comp = new_comp(root, name)
    # Peg along Z from -8.5 to +1 (1 mm into plate), rotated +90 deg about Y so Z -> X: peg X -8.5..1.
    peg_sk = new_sketch(comp, "xy", label=f"{name}_peg_sketch")
    peg_c = circle(peg_sk, 0.0, 0.0, 4.0, diameter="8 mm")
    fix_point(peg_sk, peg_c.centerSketchPoint, ZERO)
    peg_feat = extrude(comp, profile(peg_sk), "9.5 mm", start_offset="-8.5 mm", label=f"{name}_peg")
    rotate_body_about_y(comp, [peg_feat.bodies.item(0)], x="0 mm", z="0 mm", angle="90 deg", label=f"{name}_peg_to_x")
    peg = comp.bRepBodies.item(0)
    plate_sk = rect_xy_sketch(comp, f"{name}_plate_sketch", "0 mm", "-15 mm", "7 mm", "15 mm")
    plate = extrude(comp, profile(plate_sk), "30 mm", start_offset="-15 mm", label=f"{name}_plate").bodies.item(0)
    combine(comp, plate, [peg], op="join", label=f"{name}_join")
    body = comp.bRepBodies.item(0)
    grip_fit(
        comp,
        body,
        ("0 mm", "0 mm"),
        "-8.5 mm",
        "7 mm",
        "8 mm",
        "shaft",
        axis="x",
        entry="low",
        relief="0.8 mm",
        fin_count=str(SHAFT_FIN_COUNT),
        label=f"{name}_grip",
    )
    _dfam_clean(comp, "+x", name)  # plate back face (X max) on bed, peg up
    return comp


def _measure_hole(
    name: str, comp: adsk.fusion.Component, start_mm: float, zone: tuple[float, float], entry: EntrySide
) -> None:
    body = comp.bRepBodies.item(0)
    print(f"== {name} (hole, axis x, zone {zone}, entry {entry})")
    bodies = comp.bRepBodies.count
    lumps = body.lumps.count
    print(f"{name}: bodies={bodies} lumps={lumps}")
    if bodies != 1 or lumps != 1:
        FAILS.append(f"{name} bodies/lumps")
    text = report(comp)
    print(f"{name}: {_timeline_unnamed(text)}")
    _dfam_fails(text, name)
    mid = (zone[0] + zone[1]) / 2
    tip = _first_inside(body, "x", mid, 9.5, 11.5)
    _check(f"{name} fin tip r", tip, 10.4)
    lead_along = zone[0] + 0.2 if entry == "low" else zone[1] - 0.2
    lead = _first_inside(body, "x", lead_along, 9.5, 11.0)
    print(f"{name}: lead probe along={lead_along} r in [9.5,11.0] first solid = {lead} (want None = trimmed)")
    if lead is not None:
        FAILS.append(f"{name} lead-in not trimmed at along {lead_along}")
    pr, pt = _probe(11.6)
    runs = _not_inside_runs(body, "x", pr, pt, 0.01, 19.99)
    print(f"{name}: relief not-inside runs at r=11.6 angle {PROBE_DEG}: {runs} (want one run {zone})")
    if len(runs) != 1 or abs(runs[0][0] - zone[0]) > TOL_MM or abs(runs[0][1] - zone[1]) > TOL_MM:
        FAILS.append(f"{name} relief span {runs} want {zone}")
    # solid is r >= fin tip for a hole: probe 10.5 is inside the fin, outside the bare tip (10.4)
    end = (
        _first_along(body, "x", 10.5, 0.0, 0.0, 3.0)
        if entry == "low"
        else _first_along(body, "x", 10.5, 0.0, 20.0, 17.0)
    )
    print(f"{name}: lead-in end (first along from entry with solid at r=10.5) = {end}")
    sign, desc = _plane_sign(comp, "x", start_mm)
    print(f"{name}: plane normal sign seen by _extrude_zone = {sign} ({desc})")


def _measure_shaft(name: str, comp: adsk.fusion.Component, zone: tuple[float, float]) -> None:
    body = comp.bRepBodies.item(0)
    print(f"== {name} (shaft, axis x, zone {zone}, entry low)")
    bodies = comp.bRepBodies.count
    lumps = body.lumps.count
    print(f"{name}: bodies={bodies} lumps={lumps}")
    if bodies != 1 or lumps != 1:
        FAILS.append(f"{name} bodies/lumps")
    text = report(comp)
    print(f"{name}: {_timeline_unnamed(text)}")
    _dfam_fails(text, name)
    tip = _last_inside(body, "x", (zone[0] + zone[1]) / 2, 0.0, 5.5)
    _check(f"{name} fin tip r", tip, 4.6)
    lead = _last_inside(body, "x", zone[0] + 0.2, 0.0, 5.5)
    print(f"{name}: lead probe along={zone[0] + 0.2} last solid r = {lead} (want < 4.55 = trimmed)")
    if lead is None or lead >= 4.55:
        FAILS.append(f"{name} lead-in not trimmed")
    pr, pt = _probe(3.5, SHAFT_PROBE_DEG)
    runs = _not_inside_runs(body, "x", pr, pt, -8.49, 0.49)
    print(f"{name}: relief not-inside runs at r=3.5 angle {SHAFT_PROBE_DEG}: {runs} (want one run {zone})")
    if len(runs) != 1 or abs(runs[0][0] - zone[0]) > TOL_MM or abs(runs[0][1] - zone[1]) > TOL_MM:
        FAILS.append(f"{name} relief span {runs} want {zone}")
    end = _first_along(body, "x", 4.55, 0.0, zone[0], zone[0] + 2.5)
    print(f"{name}: lead-in end (first along from tip with solid at r=4.55) = {end}")
    sign, desc = _plane_sign(comp, "x", zone[0])
    print(f"{name}: plane normal sign seen by _extrude_zone = {sign} ({desc})")


def run(_context: str) -> None:
    app = adsk.core.Application.get()
    app.documents.add(DOC_TYPE_DESIGN)
    doc = app.activeDocument
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent
    try:
        comp_a = _case_hole(root, "axis_x_hole_low", "0 mm", "low", "-x")
        _measure_hole("A_hole_low", comp_a, 0.0, (0.0, 10.0), "low")
        comp_b = _case_hole(root, "axis_x_hole_high", "10 mm", "high", "+x")
        _measure_hole("B_hole_high", comp_b, 10.0, (10.0, 20.0), "high")
        comp_c = _case_shaft(root, "axis_x_shaft_low")
        _measure_shaft("C_shaft_low", comp_c, (-8.5, -1.5))
    finally:
        doc.close(False)
    if FAILS:
        raise RuntimeError(f"grip_fit axis-x smoke FAIL: {FAILS}")
    print("ALL PASS")
