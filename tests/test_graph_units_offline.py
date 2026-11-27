"""Offline: render() unit suffix and flag() hardcoded rule for synthetic params (measured live, fusion_smoke_graph.py)."""

from fusion_kit.graph import GraphNode, ParamRow, flag, from_json, render, to_json


def _node(params: list[ParamRow]) -> GraphNode:
    return GraphNode(idx=0, kind="sketch", label="base_sketch", params=params, inputs=[], outputs=[], src="x.py:1")


def test_profiles_count_has_no_mm_unit() -> None:
    text = render([_node([("profiles", 2.0, "2", "")])], "t")
    assert "profiles=2.0 " in text
    assert "profiles=2.0mm" not in text


def test_profiles_count_not_flagged_hardcoded() -> None:
    nodes = [_node([("profiles", 2.0, "2", "")])]
    flag(nodes)
    assert "hardcoded" not in nodes[0].flags


def test_angle_param_rendered_in_deg() -> None:
    text = render([_node([("d6", 0.0, "0.0 deg", "rad")])], "t")
    assert "d6=0.0deg" in text
    assert "d6=0.0mm" not in text


def test_unitless_count_not_hardcoded_and_no_mm_suffix() -> None:
    nodes = [_node([("pattern_qty", 4.0, "4", "")])]
    flag(nodes)
    assert "hardcoded" not in nodes[0].flags
    assert "pattern_qty=4.0 " in render(nodes, "t")
    assert "pattern_qty=4.0mm" not in render(nodes, "t")


def test_dimension_literal_still_hardcoded() -> None:
    nodes = [_node([("distance", 20.0, "20 mm", "cm")])]
    flag(nodes)
    assert "hardcoded" in nodes[0].flags
    assert "distance=20.0mm" in render(nodes, "t")


def test_length_unit_renders_mm_whatever_the_unit() -> None:
    assert "span=304.8mm" in render([_node([("span", 304.8, "1 ft", "ft")])], "t")


def test_unknown_unit_renders_question_mark() -> None:
    assert "odd=?" in render([_node([("odd", None, "1 furlong", "furlong")])], "t")


def test_unit_survives_json_round_trip() -> None:
    nodes = [_node([("distance", 20.0, "20 mm", "cm")])]
    assert from_json(to_json(nodes)) == nodes


def _extrude(params: list[ParamRow]) -> GraphNode:
    return GraphNode(idx=0, kind="extrude", label="block_extrude", params=params, inputs=[], outputs=[], src="x.py:1")


def test_zero_deg_taper_not_hardcoded() -> None:
    # Fusion adds taperAngleOne = '0.0 deg' to every extrude; the user never typed it, so a healthy extrude is not WARN.
    nodes = [_extrude([("d7", 0.0, "0.0 deg", "deg")])]
    flag(nodes)
    assert "hardcoded" not in nodes[0].flags


def test_zero_rad_angle_not_hardcoded() -> None:
    nodes = [_extrude([("d7", 0.0, "0.0", "rad")])]
    flag(nodes)
    assert "hardcoded" not in nodes[0].flags


def test_nonzero_deg_taper_still_hardcoded() -> None:
    nodes = [_extrude([("d7", 5.0, "5 deg", "deg")])]
    flag(nodes)
    assert "hardcoded" in nodes[0].flags
