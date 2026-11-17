"""Live smoke: bed '+x' end to end. Plate 7 x 170 x 35 mm (thin in X); bed = X-max face (170 x 35 mm).

Run inside Fusion via fusion_kit.loader.run_part. Not a pytest module (name does not start with test_).
Creates a NEW unsaved document and closes it with doc.close(False) in finally. Raises at the end if a check failed.
"""

import adsk.core
import adsk.fusion

from fusion_kit.dfam import bed_from_body, print_frame_body
from fusion_kit.enums import DOC_TYPE_DESIGN, PARAMETRIC_DESIGN
from fusion_kit.features import bed_chamfer, extrude, profile, warp_relief_grooves
from fusion_kit.printability import check_body
from fusion_kit.report import report
from fusion_kit.sketch import anchor, dim, new_sketch, rect

FAILS: list[str] = []


def _check(label: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'} {label}: {detail}")
    if not ok:
        FAILS.append(label)


def _plate(comp: adsk.fusion.Component, w_mm: float, d_mm: float, h_mm: float) -> adsk.fusion.BRepBody:
    s = new_sketch(comp, "xy", label="plate_sketch")
    lines = rect(s, (0.0, 0.0, 0.0), (w_mm, d_mm, 0.0))
    dim(s, lines[0].startSketchPoint, lines[0].endSketchPoint, f"{w_mm} mm")
    dim(s, lines[1].startSketchPoint, lines[1].endSketchPoint, f"{d_mm} mm")
    anchor(s, lines[0].startSketchPoint)
    extrude(comp, profile(s), f"{h_mm} mm", label="plate_body")
    body = comp.bRepBodies.item(0)
    assert body is not None
    return body


def _dims_mm(b: adsk.fusion.BRepBody) -> tuple[float, float, float]:
    bb = b.boundingBox
    return (
        (bb.maxPoint.x - bb.minPoint.x) * 10,
        (bb.maxPoint.y - bb.minPoint.y) * 10,
        (bb.maxPoint.z - bb.minPoint.z) * 10,
    )


def run(_context: str) -> None:
    app = adsk.core.Application.get()
    doc = app.documents.add(DOC_TYPE_DESIGN)
    design = adsk.fusion.Design.cast(app.activeProduct)
    design.designType = PARAMETRIC_DESIGN
    comp = design.rootComponent
    try:
        body = _plate(comp, 7.0, 170.0, 35.0)
        d0 = _dims_mm(body)
        _check("plate", abs(d0[0] - 7.0) < 0.01 and abs(d0[1] - 170.0) < 0.01, f"dims mm {d0}")

        bed_chamfer(comp, body, "1 mm", label="bed_chamfer", bed="+x")
        print("after bed_chamfer: bed attr =", bed_from_body(body), "vol cm3 =", body.physicalProperties.volume)
        _check("bed attr after chamfer", bed_from_body(body) == "+x", f"bed_from_body = {bed_from_body(body)}")

        grooves = warp_relief_grooves(comp, body, label="warp", bed="+x")
        vol_after = body.physicalProperties.volume
        print("grooves features:", len(grooves), "vol cm3 =", vol_after)
        _check("bed attr after grooves", bed_from_body(body) == "+x", f"bed_from_body = {bed_from_body(body)}")

        # print-frame copy: rigid (same volume), bed to min Z, thickness 7 mm now along Z
        probe = body.faces.item(0)
        probe.attributes.add("dfam", "probe", "1")
        pbody = print_frame_body(body)
        pd = _dims_mm(pbody)
        _check(
            "print frame dims",
            abs(pd[2] - 7.0) < 0.01 and abs(min(pd[:2]) - 35.0) < 0.01 and abs(max(pd[:2]) - 170.0) < 0.01,
            f"print-frame dims mm {pd} (expect z=7, x/y = 170/35 in some order)",
        )
        _check(
            "print frame min z = 0",
            abs(pbody.boundingBox.minPoint.z) < 1e-6,
            f"minZ cm = {pbody.boundingBox.minPoint.z}",
        )
        # temporary body: volume reads 0 and faces take no attributes (live); check topology instead
        _check(
            "print frame topology",
            len(pbody.faces) == len(body.faces),
            f"faces copy {len(pbody.faces)} vs original {len(body.faces)}; copy volume (temp) = {pbody.physicalProperties.volume}",
        )

        text = report(comp)
        bad = [ln for ln in text.splitlines() if ln.startswith(("DFAM_FAIL bed_chamfer", "DFAM_FAIL warp_grooves"))]
        _check("report bed rules", not bad, f"{len(bad)} bed_chamfer/warp_grooves FAIL: {bad[:3]}")

        pr = check_body(body)
        print("check_body (bed +x, identity up_rot):", str(pr)[:300])
    finally:
        doc.close(False)
    if FAILS:
        raise RuntimeError(f"bed smoke FAIL: {FAILS}")
