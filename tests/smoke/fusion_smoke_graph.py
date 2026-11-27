"""Live smoke test for fusion_kit.graph (run inside Fusion via fusion_kit.loader.run_part).

Not a pytest module (name does not start with test_). Creates a NEW unsaved design document,
builds one labelled block, prints the build graph (collect -> flag -> render), changes the user
parameter, re-collects, prints the graph again and the diff. Closes the document with doc.close(False).
Assertions raise inside run(); MCP rolls back the whole call on failure.
"""

import os
import re

import adsk.core
import adsk.fusion

from fusion_kit.component import new_comp
from fusion_kit.enums import DOC_TYPE_DESIGN, PARAMETRIC_DESIGN
from fusion_kit.features import extrude, fillet, profile, select_edges
from fusion_kit.features_axes import axis_z_through
from fusion_kit.features_pattern import pattern_rectangular
from fusion_kit.graph import GraphNode, collect, diff, flag, render
from fusion_kit.sketch import anchor, dim, new_sketch, rect
from fusion_kit.user_params import ensure_params

SRC_RE = re.compile(r"^fusion_smoke_graph\.py:(\d+)$")
BASENAME = os.path.basename(__file__)


def _node(nodes: list[GraphNode], label: str) -> GraphNode:
    hits = [n for n in nodes if n.label == label]
    assert len(hits) == 1, f"label {label!r}: {len(hits)} nodes"
    return hits[0]


def _check_src(nodes: list[GraphNode], file_lines: list[str], labels: set[str]) -> None:
    """Every non-occurrence node carries src 'fusion_smoke_graph.py:<int line>'; passed labels must sit on that line."""
    checked = 0
    for n in nodes:
        if n.kind == "occurrence":  # new_comp row: not labelled by a helper, no src
            continue
        m = SRC_RE.match(n.src)
        assert m is not None, f"{n.label!r} kind={n.kind} src={n.src!r} does not match {BASENAME}:<line>"
        line_no = int(m.group(1))
        assert 1 <= line_no <= len(file_lines), f"{n.label!r} src line {line_no} out of range"
        if n.label in labels:
            assert f'label="{n.label}"' in file_lines[line_no - 1], (
                f"{n.label!r} src line {line_no} is {file_lines[line_no - 1]!r}"
            )
        checked += 1
    print(f"SMOKE src check ok: {checked} nodes")


def run(_context: str) -> None:
    app = adsk.core.Application.get()
    print(f"SMOKE docs before: {app.documents.count}")
    doc = app.documents.add(DOC_TYPE_DESIGN)
    print(f"SMOKE doc opened: {doc.name}")
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        assert design is not None
        design.designType = PARAMETRIC_DESIGN

        ensure_params(
            design,
            {
                "wall_len": ("10 mm", "mm", "wall length (extrude depth)"),
                "block_w": ("20 mm", "mm", "block width"),
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
        fillet(body, select_edges(body_obj, axis="z"), "1 mm", label="block_corner_fillet")
        center_axis = axis_z_through(body, x="block_w / 2", y="wall_len / 2", label="block_center_axis")
        pattern_rectangular(body, [body_obj], center_axis, "3", "15 mm", label="block_pattern")
        stray = new_sketch(body, "xy", label="stray_sketch")  # nothing consumes it: must be flagged unused_sketch
        rect(stray, (0.0, 0.0, 0.0), (5.0, 5.0, 0.0))

        nodes0 = collect(design)
        flag(nodes0)
        print("SMOKE graph before (wall_len=10 mm):")
        print(render(nodes0, "smoke_graph"))

        with open(__file__, encoding="utf-8") as f:
            file_lines = f.read().splitlines()
        _check_src(nodes0, file_lines, {"block_sketch", "block_extrude", "block_corner_fillet", "block_center_axis"})

        ext0 = _node(nodes0, "block_extrude")
        assert "block_sketch" in ext0.inputs, f"extrude inputs {ext0.inputs}"
        wl = [v for _n, v, e, _u in ext0.params if e == "wall_len"]
        assert wl and wl[0] is not None and abs(wl[0] - 10.0) < 1e-4, f"extrude params {ext0.params}"
        print(f"SMOKE extrude inputs={ext0.inputs} params={ext0.params}")

        pat = _node(nodes0, "block_pattern")
        qty = [row for row in pat.params if row[2] == "3"]  # unitless count: unit "" and value 3, no mm suffix
        assert qty and qty[0][3] == "" and qty[0][1] == 3.0, f"pattern qty row {pat.params}"
        dist = [row for row in pat.params if row[2] == "15 mm"]  # length: unit reported by Fusion, value in mm
        assert dist and dist[0][3] == "mm" and dist[0][1] is not None and abs(dist[0][1] - 15.0) < 1e-4, (
            f"pattern distance row {pat.params}"
        )
        assert "unused_sketch" in _node(nodes0, "stray_sketch").flags, "stray sketch not flagged unused_sketch"
        assert "unused_sketch" not in _node(nodes0, "block_sketch").flags, "block_sketch wrongly flagged unused_sketch"
        print(f"SMOKE pattern params={pat.params} flags={pat.flags}")

        p = design.userParameters.itemByName("wall_len")
        assert p is not None
        p.expression = "12 mm"
        nodes1 = collect(design)
        flag(nodes1)
        print("SMOKE graph after wall_len=12 mm:")
        print(render(nodes1, "smoke_graph"))

        changes = diff(nodes0, nodes1)
        print("SMOKE diff:")
        for line in changes:
            print(line)
        assert any(line.startswith("~ block_extrude ") for line in changes), f"no extrude change in {changes}"
    finally:
        doc.close(False)
    print(f"SMOKE docs after close: {app.documents.count}")
    print("SMOKE doc closed (close(False))")
