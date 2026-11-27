"""flag() unused_sketch: recorded consumers, conservative fallback for consumers whose inputs are not read. Offline."""

from fusion_kit.graph import GraphNode, flag


def _sketch(idx: int, label: str, profiles: float = 1.0) -> GraphNode:
    return GraphNode(
        idx=idx,
        kind="sketch",
        label=label,
        params=[("profiles", profiles, str(profiles), "")],
        inputs=[],
        outputs=[],
    )


def _feat(idx: int, kind: str, label: str, inputs: list[str] | None = None) -> GraphNode:
    return GraphNode(idx=idx, kind=kind, label=label, params=[], inputs=inputs or [], outputs=[])


def _flagged(nodes: list[GraphNode]) -> dict[str, bool]:
    flag(nodes)
    return {n.label: "unused_sketch" in n.flags for n in nodes}


def test_stray_sketch_with_only_fillet_after_is_flagged() -> None:
    assert _flagged([_sketch(0, "stray"), _feat(1, "fillet", "corner")]) == {"stray": True, "corner": False}


def test_sketch_consumed_by_recorded_input_is_not_flagged() -> None:
    nodes = [_sketch(0, "base_sketch"), _feat(1, "extrude", "base", ["base_sketch"])]
    assert _flagged(nodes) == {"base_sketch": False, "base": False}


def test_later_hole_with_unread_inputs_keeps_sketch_unflagged() -> None:
    assert _flagged([_sketch(0, "hole_sketch"), _feat(1, "hole", "mount_hole")])["hole_sketch"] is False


def test_later_sweep_with_unread_inputs_keeps_sketch_unflagged() -> None:
    assert _flagged([_sketch(0, "rail_sketch"), _feat(1, "sweep", "tube")])["rail_sketch"] is False


def test_later_loft_with_unread_inputs_keeps_sketch_unflagged() -> None:
    assert _flagged([_sketch(0, "section_sketch"), _feat(1, "loft", "blend")])["section_sketch"] is False


def test_later_extrude_with_other_inputs_does_not_hide_stray_sketch() -> None:
    nodes = [_sketch(0, "stray"), _sketch(1, "base_sketch"), _feat(2, "extrude", "base", ["base_sketch"])]
    assert _flagged(nodes) == {"stray": True, "base_sketch": False, "base": False}


def test_earlier_feature_cannot_consume_later_sketch() -> None:
    assert _flagged([_feat(0, "hole", "mount_hole"), _sketch(1, "late_sketch")])["late_sketch"] is True


def test_sketch_without_profiles_never_flagged() -> None:
    assert _flagged([_sketch(0, "empty_sketch", profiles=0.0)]) == {"empty_sketch": False}


def test_sweep_with_read_profile_keeps_unread_path_sketch_unflagged() -> None:
    # sweep path is not readable (SweepFeature.path raises): a sweep stays unreadable, path sketch stays unflagged
    nodes = [_sketch(0, "path_sketch"), _sketch(1, "profile_sketch"), _feat(2, "sweep", "tube", ["profile_sketch"])]
    assert _flagged(nodes) == {"path_sketch": False, "profile_sketch": False, "tube": False}


def test_later_loft_with_read_sections_does_not_hide_stray_sketch() -> None:
    nodes = [_sketch(0, "stray"), _sketch(1, "section_a"), _feat(2, "loft", "blend", ["section_a"])]
    assert _flagged(nodes) == {"stray": True, "section_a": False, "blend": False}
