"""Live smoke for new fusion_kit helpers: poly(lock_axes), ensure_params(prune), offset_plane,
planar_faces, overhangs.

Run inside Fusion via fusion_kit.loader.run_part. Not a pytest module (name does not start with
test_). Creates a NEW unsaved document and closes it at the end with doc.close(False).
"""

import adsk.core
import adsk.fusion

from fusion_kit.component import new_comp
from fusion_kit.enums import DOC_TYPE_DESIGN, PARAMETRIC_DESIGN
from fusion_kit.features import extrude, offset_plane, planar_faces, profile
from fusion_kit.report import overhangs
from fusion_kit.sketch import anchor, circle, dim, new_sketch, poly, rect
from fusion_kit.user_params import ensure_params

FAILS: list[str] = []


def _check(label: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'} {label}: {detail}")
    if not ok:
        FAILS.append(label)


def _names(design: adsk.fusion.Design) -> list[str]:
    up = design.userParameters
    return sorted(up.item(i).name for i in range(up.count))


def _hv_count(s: adsk.fusion.Sketch) -> int:
    n = 0
    for c in s.geometricConstraints:
        if adsk.fusion.HorizontalConstraint.cast(c) is not None or adsk.fusion.VerticalConstraint.cast(c) is not None:
            n += 1
    return n


def _box(comp: adsk.fusion.Component, w_mm: float, d_mm: float, h_mm: float) -> adsk.fusion.BRepBody:
    s = new_sketch(comp, "xy", label="box_sketch")
    lines = rect(s, (0.0, 0.0, 0.0), (w_mm, d_mm, 0.0))
    dim(s, lines[0].startSketchPoint, lines[0].endSketchPoint, f"{w_mm} mm")
    dim(s, lines[1].startSketchPoint, lines[1].endSketchPoint, f"{d_mm} mm")
    anchor(s, lines[0].startSketchPoint)
    extrude(comp, profile(s), f"{h_mm} mm", label="box_body")
    body = comp.bRepBodies.item(0)
    assert body is not None
    return body


def _x_hole(comp: adsk.fusion.Component, y_mm: float, z_mm: float, d_mm: float) -> None:
    """Horizontal bore along X. Sketch on YZ plane at x=0, symmetric cut 60 mm (covers box x 0..20)."""
    s = new_sketch(comp, "yz", label="x_hole_sketch")
    c = circle(s, 0.0, y_mm, d_mm / 2, z=z_mm, diameter=f"{d_mm} mm")
    # sketch x = model -Z, sketch y = model +Y (docs/gotchas.md, measured); distances unsigned
    anchor(s, c.centerSketchPoint, f"{z_mm} mm", f"{y_mm} mm")
    extrude(comp, profile(s), "60 mm", op="cut", symmetric=True, label="x_hole_cut")


def run(_context: str) -> None:
    app = adsk.core.Application.get()
    print(f"SMOKE docs before: {app.documents.count}")
    doc = app.documents.add(DOC_TYPE_DESIGN)
    print(f"SMOKE doc opened: {doc.name}")
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        assert design is not None
        design.designType = PARAMETRIC_DESIGN
        root = design.rootComponent

        # ---- 1) poly L-shape, lock_axes=True ----
        print("--- 1) poly lock_axes")
        pts = [(0.0, 0.0), (30.0, 0.0), (30.0, 10.0), (10.0, 10.0), (10.0, 40.0), (0.0, 40.0)]
        comp_free = new_comp(root, "l_free")
        base_hv = _hv_count(poly(new_sketch(comp_free, "xy", label="l_free_sketch"), pts)[0].parentSketch)
        print(f"1 baseline H/V count (lock_axes=False): {base_hv}")

        comp1 = new_comp(root, "l_shape")
        s_lock = new_sketch(comp1, "xy", label="l_sketch")
        lines = poly(s_lock, pts, lock_axes=True)
        hv = _hv_count(s_lock)
        _check(
            "1a poly lock_axes H/V constraints",
            hv == 6 and len(lines) == 6,
            f"hv={hv} lines={len(lines)} baseline_hv={base_hv}",
        )
        extrude(comp1, profile(s_lock), "10 mm", label="l_body")
        body1 = comp1.bRepBodies.item(0)
        assert body1 is not None
        vol_mm3 = body1.physicalProperties.volume * 1000
        bb = body1.boundingBox
        ext = (
            (bb.maxPoint.x - bb.minPoint.x) * 10,
            (bb.maxPoint.y - bb.minPoint.y) * 10,
            (bb.maxPoint.z - bb.minPoint.z) * 10,
        )
        _check("1b L extrude vol 6000 mm3", abs(vol_mm3 - 6000.0) < 1.0, f"vol={vol_mm3:.2f} mm3")
        _check(
            "1c L extents 30x40x10",
            all(abs(a - b) < 0.01 for a, b in zip(ext, (30.0, 40.0, 10.0))),
            f"extents={ext[0]:.2f}x{ext[1]:.2f}x{ext[2]:.2f} mm",
        )

        # ---- 2) ensure_params prune ----
        print("--- 2) ensure_params prune")
        # Fusion rejects the single-letter name "a" ("3 : param name is not valid") -> smoke_ prefix
        ensure_params(
            design,
            {
                "smoke_a": ("10 mm", "mm", ""),
                "smoke_b": ("smoke_a*2", "mm", ""),
                "smoke_c": ("5 mm", "mm", ""),
            },
        )
        print(f"2 names after create smoke_a,smoke_b,smoke_c: {_names(design)}")
        ensure_params(design, {"smoke_a": ("10 mm", "mm", "")}, prune=True)
        names = _names(design)
        _check("2a prune=True spec{smoke_a} leaves only smoke_a", names == ["smoke_a"], f"names={names}")

        # 2b probe: deleteMe on a param still referenced by smoke_b
        ensure_params(design, {"smoke_a": ("10 mm", "mm", ""), "smoke_b": ("smoke_a*2", "mm", "")})
        pa = design.userParameters.itemByName("smoke_a")
        assert pa is not None
        try:
            ret = pa.deleteMe()
            print(f"2b probe smoke_a.deleteMe() (referenced by smoke_b) returned {ret!r}")
        except Exception as e:  # noqa: BLE001 - probe: observe any exception type, this is the measurement
            print(f"2b probe smoke_a.deleteMe() (referenced by smoke_b) raised {type(e).__name__}: {e}")
        print(f"2b names after probe: {_names(design)}")

        # 2c: spec without smoke_a while smoke_b=smoke_a*2 -> RuntimeError expected, naming smoke_a
        ensure_params(design, {"smoke_a": ("10 mm", "mm", ""), "smoke_b": ("smoke_a*2", "mm", "")})
        try:
            ensure_params(design, {"smoke_b": ("smoke_a*2", "mm", "")}, prune=True)
            _check("2c prune spec{smoke_b} with smoke_a referenced -> RuntimeError", False, "no exception raised")
        except RuntimeError as e:
            _check(
                "2c prune spec{smoke_b} with smoke_a referenced -> RuntimeError",
                str(e).rsplit(": ", 1)[-1] == "smoke_a",
                f"RuntimeError: {e}",
            )
        print(f"2c names after: {_names(design)}")

        # ---- 3) planar_faces + offset_plane on 20x20x10 box ----
        print("--- 3) planar_faces / offset_plane")
        comp3 = new_comp(root, "box")
        body3 = _box(comp3, 20.0, 20.0, 10.0)
        top = planar_faces(body3, "z", 10.0)
        _check("3a planar_faces z=10 no sign -> 1 face", len(top) == 1, f"n={len(top)}")
        bot = planar_faces(body3, "z", 0.0, normal_sign=-1)
        _check("3b planar_faces z=0 normal_sign=-1 -> 1 face", len(bot) == 1, f"n={len(bot)}")
        bot_up = planar_faces(body3, "z", 0.0, normal_sign=1)
        _check("3c planar_faces z=0 normal_sign=+1 -> 0 faces", len(bot_up) == 0, f"n={len(bot_up)}")
        plane = offset_plane(comp3, top[0], "5 mm", label="smoke_offset_top")
        z_mm = plane.geometry.origin.z * 10
        _check(
            "3d offset_plane(top,'5 mm') origin z=15 mm",
            abs(z_mm - 15.0) < 1e-3,
            f"origin z={z_mm:.4f} mm name={plane.name}",
        )

        # ---- 4) overhangs ----
        print("--- 4) overhangs")
        comp4a = new_comp(root, "bed_box")
        body4a = _box(comp4a, 20.0, 20.0, 10.0)
        ov_a = overhangs(body4a)
        _check("4a box on bed -> []", ov_a == [], f"{ov_a}")

        comp4b = new_comp(root, "bore_d12_h30")
        _box(comp4b, 20.0, 20.0, 30.0)
        _x_hole(comp4b, 10.0, 15.0, 12.0)
        body4b = comp4b.bRepBodies.item(0)
        assert body4b is not None
        ov_b = overhangs(body4b)
        flagged = [o for o in ov_b if o[0] == "cylinder" and o[1] <= -0.99]
        _check(
            "4b d12 X-bore at mid-height flagged cylinder n_z~-1",
            len(flagged) >= 1,
            f"bodies={comp4b.bRepBodies.count} overhangs={ov_b}",
        )

        comp4c = new_comp(root, "bore_d6_h30")
        _box(comp4c, 20.0, 20.0, 30.0)
        _x_hole(comp4c, 10.0, 15.0, 6.0)
        body4c = comp4c.bRepBodies.item(0)
        assert body4c is not None
        ov_c = overhangs(body4c)
        _check("4c d6 X-bore not flagged -> []", ov_c == [], f"bodies={comp4c.bRepBodies.count} overhangs={ov_c}")

        # DIAG only: literal spec (box 20x20x10, d=12 at mid-height z=5) - d > height, bore breaks through
        comp4d = new_comp(root, "diag_literal_d12_h10")
        _box(comp4d, 20.0, 20.0, 10.0)
        _x_hole(comp4d, 10.0, 5.0, 12.0)
        for i in range(comp4d.bRepBodies.count):
            bd = comp4d.bRepBodies.item(i)
            assert bd is not None
            print(f"DIAG 4d literal body[{i}] overhangs={overhangs(bd)}")
        print(f"DIAG 4d literal bodies={comp4d.bRepBodies.count}")

        print(f"SUMMARY fails={len(FAILS)} {FAILS}")
    finally:
        doc.close(False)
        print(f"SMOKE docs after close: {app.documents.count}")
        print("SMOKE doc closed (close(False))")
