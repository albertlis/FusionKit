"""Offline: graph.diff matching. Unnamed default names (Extrude7) are matched by occurrence, not by name."""

from fusion_kit.graph import GraphNode, diff


def _ext(idx: int, label: str, distance: float) -> GraphNode:
    return GraphNode(
        idx=idx, kind="extrude", label=label, params=[("distance", distance, "d", "mm")], inputs=[], outputs=[]
    )


def test_unnamed_renumber_is_not_a_plus_minus_pair() -> None:
    # Same two extrudes, default names shifted by one: same features, so no '+' and no '-' line.
    prev = [_ext(0, "Extrude7", 5.0), _ext(1, "Extrude8", 6.0)]
    cur = [_ext(0, "Extrude7", 5.0), _ext(1, "Extrude9", 6.0)]
    assert diff(prev, cur) == []


def test_unnamed_insert_shows_one_change_and_one_plus() -> None:
    prev = [_ext(0, "Extrude7", 5.0), _ext(1, "Extrude8", 6.0)]
    cur = [_ext(0, "Extrude7", 5.0), _ext(1, "Extrude8", 9.0), _ext(2, "Extrude9", 6.0)]
    assert diff(prev, cur) == ["~ Extrude8 distance 6.0 -> 9.0", "+ Extrude9"]


def test_labelled_nodes_still_matched_by_label() -> None:
    prev = [_ext(0, "wall", 5.0), _ext(1, "Extrude8", 6.0)]
    cur = [_ext(0, "Extrude8", 6.0), _ext(1, "wall", 7.0)]
    assert diff(prev, cur) == ["~ wall distance 5.0 -> 7.0"]


def _auto_param(idx: int, auto_name: str, value: float) -> GraphNode:
    # Labelled node whose only parameter carries a Fusion auto id (d<number>) that changes on rebuild.
    return GraphNode(
        idx=idx, kind="extrude", label="wall", params=[(auto_name, value, "", "mm")], inputs=[], outputs=[]
    )


def test_auto_param_id_renumber_is_not_a_change() -> None:
    # Same node, same value, auto id d1 -> d2 after rebuild: no '~' line.
    assert diff([_auto_param(0, "d1", 5.0)], [_auto_param(0, "d2", 5.0)]) == []


def test_auto_param_value_change_is_one_line() -> None:
    # Auto id renumbered AND value changed: exactly one '~' line, labelled by position.
    assert diff([_auto_param(0, "d1", 5.0)], [_auto_param(0, "d2", 7.0)]) == ["~ wall p0 5.0 -> 7.0"]
