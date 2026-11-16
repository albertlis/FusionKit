"""Live smoke for fusion_kit.sketch: new_sketch(BRepFace) + to_sketch (run inside Fusion via fusion_kit.loader.run_part).

Not a pytest module (name does not start with test_). Creates a NEW unsaved document and closes it with
doc.close(False) in finally. Never saveAs (Fusion Free doc limit).

Geometry (mm, box x0..50, y0..40, z0..30):
- top hole: sketch on top face z=30, circle r=3 at (25, 20) -> through (symmetric 100 mm cut)
- side hole: sketch on face x=50, circle r=3 at (y=10, z=10) -> through along X
Checks (pointContainment via fusion_kit.report.inside):
- hole centres (mid-depth on the hole axis) -> outside
- points 5 mm from the axis, inside material -> inside
"""

import adsk.core
import adsk.fusion

from fusion_kit.enums import DOC_TYPE_DESIGN, PARAMETRIC_DESIGN
from fusion_kit.features import extrude, profile
from fusion_kit.report import inside
from fusion_kit.sketch import new_sketch, to_sketch

P3 = adsk.core.Point3D.create


def _planar_face(body: adsk.fusion.BRepBody, axis: int, value_cm: float) -> adsk.fusion.BRepFace:
    """Planar face whose plane is perpendicular to axis (0=x,1=y,2=z) and lies at value_cm (cm)."""
    for face in body.faces:
        g = face.geometry
        if not isinstance(g, adsk.core.Plane):
            continue
        n = (g.normal.x, g.normal.y, g.normal.z)
        o = (g.origin.x, g.origin.y, g.origin.z)
        if abs(abs(n[axis]) - 1.0) < 1e-6 and abs(o[axis] - value_cm) < 1e-6:
            return face
    raise RuntimeError(f"no planar face axis={axis} at {value_cm} cm")


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

        base = new_sketch(root, "xy", label="base_sketch")
        base.sketchCurves.sketchLines.addTwoPointRectangle(P3(0.0, 0.0, 0.0), P3(5.0, 4.0, 0.0))
        extrude(root, profile(base), "30 mm", label="base_body")
        body = root.bRepBodies.item(0)
        assert body is not None
        print(f"SMOKE box bodies={root.bRepBodies.count}")

        top = _planar_face(body, 2, 3.0)
        ts = new_sketch(root, top, label="top_hole_sketch")
        ts.sketchCurves.sketchCircles.addByCenterRadius(to_sketch(ts, 25.0, 20.0, 30.0), 0.3)
        # Face sketch auto-projects the face outline -> profile(outer=True) = holed rectangle (wrong, cuts the box away).
        # outer=False = smallest single-loop profile = the circle.
        extrude(root, profile(ts, outer=False), "100 mm", op="cut", symmetric=True, label="top_hole_cut")

        body = root.bRepBodies.item(0)
        assert body is not None
        side = _planar_face(body, 0, 5.0)
        ss = new_sketch(root, side, label="side_hole_sketch")
        ss.sketchCurves.sketchCircles.addByCenterRadius(to_sketch(ss, 50.0, 10.0, 10.0), 0.3)
        extrude(root, profile(ss, outer=False), "100 mm", op="cut", symmetric=True, label="side_hole_cut")

        body = root.bRepBodies.item(0)
        assert body is not None
        print(f"SMOKE after cuts bodies={root.bRepBodies.count}")

        checks: list[tuple[str, tuple[float, float, float], bool]] = [
            ("top hole centre (25,20,15) expect outside", (25.0, 20.0, 15.0), False),
            ("top 5 mm off axis (30,20,15) expect inside", (30.0, 20.0, 15.0), True),
            ("side hole centre (25,10,10) expect outside", (25.0, 10.0, 10.0), False),
            ("side 5 mm off axis (25,10,15) expect inside", (25.0, 10.0, 15.0), True),
        ]
        failures = 0
        for label, (x, y, z), want in checks:
            got = inside(body, x, y, z)
            ok = got == want
            failures += 0 if ok else 1
            print(f"SMOKE {label}: got inside={got} {'OK' if ok else 'FAIL'}")
        assert failures == 0, f"{failures} containment check(s) failed"
    finally:
        doc.close(False)
        print(f"SMOKE docs after close: {app.documents.count}")
