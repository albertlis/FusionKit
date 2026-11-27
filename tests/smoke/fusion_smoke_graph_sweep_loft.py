"""Live smoke test for fusion_kit.graph inputs of sweep (profile) and loft (sections) features.

Run inside Fusion via fusion_kit.loader.run_part. Not a pytest module (name does not start with test_).
Creates a NEW unsaved design document with, in timeline order:
  - sweep_profile_sketch (circle) + sweep_path_sketch (line) -> sweep_tube (SweepFeature, new body)
    The path cannot be read (SweepFeature.path raises RuntimeError in Fusion, docs/gotchas.md), so the sweep is
    unreadable and sweep_path_sketch stays unflagged by the conservative fallback.
  - stray_sketch: nothing consumes it -> must be the ONLY unused_sketch flag (created after the sweep, before the
    loft, so the later loft with readable sections does not hide it)
  - offset construction plane -> loft_section_b_sketch on it; loft_section_a_sketch on xy -> loft_blend (LoftFeature)
Prints collect -> flag -> render, then asserts. Closes the document with doc.close(False).
Assertions raise inside run(); MCP rolls back the whole call on failure.
"""

import adsk.core
import adsk.fusion

from fusion_kit.component import new_comp
from fusion_kit.enums import DOC_TYPE_DESIGN, PARAMETRIC_DESIGN
from fusion_kit.features import profile
from fusion_kit.graph import GraphNode, collect, flag, render
from fusion_kit.sketch import new_sketch, rect, to_sketch
from fusion_kit.timeline import set_label


def _node(nodes: list[GraphNode], label: str) -> GraphNode:
    hits = [n for n in nodes if n.label == label]
    assert len(hits) == 1, f"label {label!r}: {len(hits)} nodes"
    return hits[0]


def run(_context: str) -> None:
    app = adsk.core.Application.get()
    print(f"SWEEPSMOKE docs before: {app.documents.count}")
    doc = app.documents.add(DOC_TYPE_DESIGN)
    print(f"SWEEPSMOKE doc opened: {doc.name}")
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        assert design is not None
        design.designType = PARAMETRIC_DESIGN
        comp = new_comp(design.rootComponent, "frame")

        # (a) sweep: circle profile at origin on xy, straight path along +Z from the profile centre (perpendicular)
        sp = new_sketch(comp, "xy", label="sweep_profile_sketch")
        sp.sketchCurves.sketchCircles.addByCenterRadius(to_sketch(sp, 0, 0, 0), 0.25)  # r 2.5 mm, cm
        sk_path = new_sketch(comp, "xz", label="sweep_path_sketch")
        path_line = sk_path.sketchCurves.sketchLines.addByTwoPoints(
            to_sketch(sk_path, 0, 0, 0), to_sketch(sk_path, 0, 0, 20)
        )
        path = comp.features.createPath(path_line, True)
        sweep_in = comp.features.sweepFeatures.createInput(
            profile(sp), path, adsk.fusion.FeatureOperations.NewBodyFeatureOperation
        )
        sweep = comp.features.sweepFeatures.add(sweep_in)
        set_label(sweep, "sweep_tube")

        stray = new_sketch(comp, "xy", label="stray_sketch")  # nothing consumes it: the only unused_sketch
        rect(stray, (30.0, 0.0, 0.0), (40.0, 5.0, 0.0))

        # (b) loft: section a on xy, section b on a construction plane offset 20 mm up; both away from the tube
        pin = comp.constructionPlanes.createInput()
        pin.setByOffset(comp.xYConstructionPlane, adsk.core.ValueInput.createByString("20 mm"))
        plane = comp.constructionPlanes.add(pin)
        sa = new_sketch(comp, "xy", label="loft_section_a_sketch")
        sa.sketchCurves.sketchCircles.addByCenterRadius(to_sketch(sa, 15, 0, 0), 3.0)  # r 3 mm at x 15 mm
        sb = new_sketch(comp, plane, label="loft_section_b_sketch")
        sb.sketchCurves.sketchCircles.addByCenterRadius(to_sketch(sb, 15, 0, 20), 2.0)  # r 2 mm at x 15 mm, z 20 mm
        loft_in = comp.features.loftFeatures.createInput(adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        loft_in.loftSections.add(profile(sa))
        loft_in.loftSections.add(profile(sb))
        loft = comp.features.loftFeatures.add(loft_in)
        set_label(loft, "loft_blend")

        nodes = collect(design)
        flag(nodes)
        print("SWEEPSMOKE graph (collect -> flag -> render):")
        print(render(nodes, "sweep_loft_graph"))

        sw = _node(nodes, "sweep_tube")
        assert sw.kind == "sweep", f"sweep kind {sw.kind!r}"
        assert "sweep_profile_sketch" in sw.inputs, f"sweep inputs (profile) {sw.inputs}"
        assert "unused_sketch" not in _node(nodes, "sweep_path_sketch").flags, "sweep path sketch flagged unused"
        print(f"SWEEPSMOKE sweep_tube inputs={sw.inputs} flags={sw.flags}")

        lf = _node(nodes, "loft_blend")
        assert lf.kind == "loft", f"loft kind {lf.kind!r}"
        assert "loft_section_a_sketch" in lf.inputs, f"loft inputs (section a) {lf.inputs}"
        assert "loft_section_b_sketch" in lf.inputs, f"loft inputs (section b) {lf.inputs}"
        print(f"SWEEPSMOKE loft_blend inputs={lf.inputs} flags={lf.flags}")

        unused = [n.label for n in nodes if "unused_sketch" in n.flags]
        assert unused == ["stray_sketch"], f"unused_sketch flags {unused}"
        print(f"SWEEPSMOKE unused_sketch={unused}")
    finally:
        doc.close(False)
    print(f"SWEEPSMOKE docs after close: {app.documents.count}")
    print("SWEEPSMOKE doc closed (close(False))")
