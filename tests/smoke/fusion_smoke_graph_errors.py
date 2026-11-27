"""Live smoke test for fusion_kit.graph health / suppressed status (run inside Fusion via fusion_kit.loader.run_part).

Not a pytest module (name does not start with test_). Creates a NEW unsaved design document and builds:
  - block_extrude: healthy extrude (must stay healthy, no unhealthy flag)
  - block_corner_fillet: fillet that is broken by a live edit of fil_r (1 mm -> 30 mm, larger than the body)
  - side_extrude: extrude that is suppressed via its timeline item
Prints raw healthState / errorOrWarningMessage / isSuppressed per timeline item, then graph collect -> flag -> render,
then asserts. Closes the document with doc.close(False). Assertions raise inside run(); MCP rolls back the whole call.
"""

import adsk.core
import adsk.fusion

from fusion_kit.component import new_comp
from fusion_kit.enums import DOC_TYPE_DESIGN, PARAMETRIC_DESIGN
from fusion_kit.features import extrude, fillet, profile, select_edges
from fusion_kit.graph import GraphNode, collect, flag, render
from fusion_kit.sketch import anchor, dim, new_sketch, rect
from fusion_kit.user_params import ensure_params


def _node(nodes: list[GraphNode], label: str) -> GraphNode:
    hits = [n for n in nodes if n.label == label]
    assert len(hits) == 1, f"label {label!r}: {len(hits)} nodes"
    return hits[0]


def _line(text: str, label: str) -> str:
    hits = [ln for ln in text.splitlines() if ln.startswith("#") and f" {label} " in ln]
    assert len(hits) == 1, f"render line for {label!r}: {hits}"
    return hits[0]


def run(_context: str) -> None:
    app = adsk.core.Application.get()
    print(f"ERRSMOKE docs before: {app.documents.count}")
    doc = app.documents.add(DOC_TYPE_DESIGN)
    print(f"ERRSMOKE doc opened: {doc.name}")
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        assert design is not None
        design.designType = PARAMETRIC_DESIGN

        ensure_params(
            design,
            {
                "wall_len": ("10 mm", "mm", "wall length (extrude depth)"),
                "block_w": ("20 mm", "mm", "block width"),
                "fil_r": ("1 mm", "mm", "corner fillet radius"),
            },
        )

        body = new_comp(design.rootComponent, "block")
        s = new_sketch(body, "xy", label="block_sketch")
        lines = rect(s, (0.0, 0.0, 0.0), (20.0, 10.0, 0.0))
        dim(s, lines[0].startSketchPoint, lines[0].endSketchPoint, "block_w")
        dim(s, lines[1].startSketchPoint, lines[1].endSketchPoint, "wall_len")
        anchor(s, lines[0].startSketchPoint)
        extrude(body, profile(s), "wall_len", label="block_extrude")

        body_obj = body.bRepBodies.item(0)
        assert body_obj is not None
        fillet(body, select_edges(body_obj, axis="z"), "fil_r", label="block_corner_fillet")

        s2 = new_sketch(body, "xy", label="side_sketch")
        rect(s2, (30.0, 0.0, 0.0), (40.0, 5.0, 0.0))
        extrude(body, profile(s2), "wall_len", label="side_extrude")

        tl = design.timeline
        side = None
        for i in range(tl.count):
            it = tl.item(i)
            if it.name == "side_extrude":
                side = it
        assert side is not None, "side_extrude timeline item not found"
        side.isSuppressed = True

        # break the fillet: radius larger than the 10 mm body height / 20 mm width
        p = design.userParameters.itemByName("fil_r")
        assert p is not None
        p.expression = "30 mm"

        for i in range(tl.count):
            it = tl.item(i)
            print(
                f"ERRSMOKE RAW {i} name={it.name!r} healthState={it.healthState} "
                f"isSuppressed={it.isSuppressed} errorOrWarningMessage={it.errorOrWarningMessage!r}"
            )

        print(f"ERRSMOKE enum Healthy={adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState}")
        print(f"ERRSMOKE enum Error={adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState}")
        print(f"ERRSMOKE enum Suppressed={adsk.fusion.FeatureHealthStates.SuppressedFeatureHealthState}")
        ext_item = next(tl.item(i) for i in range(tl.count) if tl.item(i).name == "block_extrude")
        ext_feat = adsk.fusion.ExtrudeFeature.cast(ext_item.entity)
        print(f"ERRSMOKE block_extrude taper-attrs={[a for a in dir(ext_feat) if 'aper' in a]}")
        ta1 = ext_feat.taperAngleOne
        print(f"ERRSMOKE taperAngleOne type={type(ta1).__name__} name={ta1.name!r} expression={ta1.expression!r}")
        for comp in design.allComponents:
            for p in comp.modelParameters:
                cb = p.createdBy
                if cb is not None and cb.entityToken == ext_feat.entityToken:
                    print(f"ERRSMOKE block_extrude param name={p.name!r} expression={p.expression!r} unit={p.unit!r}")

        nodes = collect(design)
        flag(nodes)
        text = render(nodes, "errors_graph")
        print("ERRSMOKE graph:")
        print(text)

        broken = _node(nodes, "block_corner_fillet")
        assert broken.healthy is False, f"broken fillet healthy={broken.healthy}"
        assert broken.message != "", "broken fillet: empty errorOrWarningMessage"
        assert "unhealthy" in broken.flags, f"broken fillet flags {broken.flags}"
        print(f"ERRSMOKE broken fillet message={broken.message!r} flags={broken.flags}")

        sup = _node(nodes, "side_extrude")
        assert sup.suppressed is True, "side_extrude not suppressed in graph"
        assert "suppressed" in sup.flags, f"side_extrude flags {sup.flags}"
        assert "unhealthy" not in sup.flags, f"side_extrude flags {sup.flags}"
        assert " SUPP " in _line(text, "side_extrude"), _line(text, "side_extrude")

        ok = _node(nodes, "block_extrude")
        assert ok.healthy is True, f"healthy extrude healthy={ok.healthy}"
        assert "unhealthy" not in ok.flags, f"healthy extrude flags {ok.flags}"
        print(f"ERRSMOKE healthy extrude flags={ok.flags} line={_line(text, 'block_extrude')}")
    finally:
        doc.close(False)
    print(f"ERRSMOKE docs after close: {app.documents.count}")
    print("ERRSMOKE doc closed (close(False))")
