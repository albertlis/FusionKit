"""Offline tests: feature_quality rules finish_ops_order and finish_ops_split (fillet / chamfer placement and grouping).

No Fusion runtime: SimpleNamespace fakes, same shape as test_feature_quality_offline.py.
Rule lines are filtered by rule name, because body-op kinds also trigger finishing_not_last.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit import feature_quality as fq

JOIN = 0
ORDER_TAIL = "-> move before finishing block (timeline order)"
BODY_OP_KINDS = [
    "ExtrudeFeature",
    "RevolveFeature",
    "HoleFeature",
    "RectangularPatternFeature",
    "CircularPatternFeature",
    "PathPatternFeature",
    "MirrorFeature",
    "CombineFeature",
    "SweepFeature",
    "LoftFeature",
    "ShellFeature",
    "ThreadFeature",
    "SplitBodyFeature",
]


def _attrs(skip: str | None = None, role: str | None = None, warp: bool = False) -> Any:
    table = {
        (fq.ATTR_GROUP, fq.ATTR_NAME): skip,
        (fq.ROLE_GROUP, fq.ROLE_NAME): role,
        (fq.DFAM_GROUP, fq.WARP_GROOVE_ATTR): "1" if warp else None,
    }

    def item(group: str, name: str) -> Any:
        v = table.get((group, name))
        return None if v is None else SimpleNamespace(value=v)

    return SimpleNamespace(itemByName=item)


def _coll(*items: Any) -> Any:
    return SimpleNamespace(count=len(items), item=lambda i: items[i])


def _body(token: str, name: str | None = None) -> Any:
    return SimpleNamespace(entityToken=token) if name is None else SimpleNamespace(entityToken=token, name=name)


def _ent(
    otype: str,
    bodies: list[Any],
    skip: str | None = None,
    *,
    role: str | None = None,
    warp: bool = False,
    edge_sets: list[Any] | tuple[()] = (),
) -> Any:
    return SimpleNamespace(
        objectType=f"adsk::fusion::{otype}",
        isSuppressed=False,
        attributes=_attrs(skip, role, warp),
        bodies=_coll(*bodies),
        edgeSets=_coll(*edge_sets),
        operation=JOIN,
        profile=None,
        extentOne=None,
        extentTwo=None,
        isThinExtrude=False,
    )


def _fillet(bodies: list[Any], *exprs: str, skip: str | None = None) -> Any:
    sets = [
        SimpleNamespace(objectType="adsk::fusion::ConstantRadiusFilletEdgeSet", radius=SimpleNamespace(expression=e))
        for e in exprs
    ]
    return _ent("FilletFeature", bodies, skip, edge_sets=sets)


def _chamfer(bodies: list[Any], *exprs: str, skip: str | None = None) -> Any:
    sets = [
        SimpleNamespace(objectType="adsk::fusion::EqualDistanceChamferEdgeSet", distance=SimpleNamespace(expression=e))
        for e in exprs
    ]
    return _ent("ChamferFeature", bodies, skip, edge_sets=sets)


def _bed_chamfer(bodies: list[Any]) -> Any:
    """features_bed.bed_chamfer shape: two-distance edge set, role attribute tags it."""
    two = SimpleNamespace(
        objectType="adsk::fusion::TwoDistancesChamferEdgeSet",
        distanceOne=SimpleNamespace(expression="bed_leg"),
        distanceTwo=SimpleNamespace(expression="wall_leg"),
    )
    return _ent("ChamferFeature", bodies, role=fq.ROLE_BED_CHAMFER, edge_sets=[two])


def _design(items: list[tuple[str, Any]]) -> Any:
    rows = [SimpleNamespace(name=n, isGroup=False, entity=e) for n, e in items]
    return SimpleNamespace(timeline=SimpleNamespace(count=len(rows), item=lambda i: rows[i]))


def _rule(rule: str, items: list[tuple[str, Any]]) -> list[str]:
    return [line for line in fq.check(_design(items)) if line.startswith(f"SKQ_WARN {rule}:")]


# ---------------------------------------------------------------------------
# finish_ops_order: body-modifying feature after a fillet / chamfer on the same body
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("otype", BODY_OP_KINDS)
def test_order_hit_every_body_op_kind_after_fillet(otype: str) -> None:
    got = _rule("finish_ops_order", [("r", _fillet([_body("A")], "r_lid")), ("op", _ent(otype, [_body("A")]))])
    assert got == [f"SKQ_WARN finish_ops_order: op after fillet on A {ORDER_TAIL}"]


def test_order_hit_after_chamfer_says_chamfer() -> None:
    got = _rule(
        "finish_ops_order", [("c", _chamfer([_body("A")], "wall")), ("pocket", _ent("ExtrudeFeature", [_body("A")]))]
    )
    assert got == [f"SKQ_WARN finish_ops_order: pocket after chamfer on A {ORDER_TAIL}"]


def test_order_hit_bed_chamfer_is_finishing_op() -> None:
    # bed chamfer is a finishing op (ChamferFeature) -> plain extrude after it is out of order
    got = _rule(
        "finish_ops_order",
        [("housing_bed_chamfer", _bed_chamfer([_body("A")])), ("pocket", _ent("ExtrudeFeature", [_body("A")]))],
    )
    assert got == [f"SKQ_WARN finish_ops_order: pocket after chamfer on A {ORDER_TAIL}"]


def test_order_no_hit_warp_grooves_after_bed_chamfer() -> None:
    # grooves follow the bed chamfer by design (warp_not_last says so): exempt
    items = [
        ("housing_bed_chamfer", _bed_chamfer([_body("A")])),
        ("warp_grooves", _ent("ExtrudeFeature", [_body("A")])),
    ]
    assert _rule("finish_ops_order", items) == []


def test_order_no_hit_tagged_warp_groove_plain_name() -> None:
    items = [("r", _fillet([_body("A")], "r_lid")), ("grooves", _ent("ExtrudeFeature", [_body("A")], warp=True))]
    assert _rule("finish_ops_order", items) == []


def test_order_no_hit_warp_labeled_fillet_registers_nothing() -> None:
    items = [("warp_corner_fillet", _fillet([_body("A")], "r_lid")), ("pocket", _ent("ExtrudeFeature", [_body("A")]))]
    assert _rule("finish_ops_order", items) == []


def test_order_no_hit_modifier_on_other_body() -> None:
    items = [("r", _fillet([_body("A")], "r_lid")), ("pocket", _ent("ExtrudeFeature", [_body("B")]))]
    assert _rule("finish_ops_order", items) == []


def test_order_no_hit_finishing_is_last() -> None:
    items = [("base", _ent("ExtrudeFeature", [_body("A")])), ("r", _fillet([_body("A")], "r_lid"))]
    assert _rule("finish_ops_order", items) == []


def test_order_no_hit_fillet_after_chamfer_not_a_body_op() -> None:
    # two finishing ops in a row: finish_ops_split's business, not finish_ops_order's
    items = [("c", _chamfer([_body("A")], "wall")), ("r", _fillet([_body("A")], "r_lid"))]
    assert _rule("finish_ops_order", items) == []


def test_order_no_hit_new_body_extrude_on_other_body() -> None:
    items = [("r", _fillet([_body("A")], "r_lid")), ("shim", _ent("ExtrudeFeature", [_body("C")]))]
    assert _rule("finish_ops_order", items) == []


def test_order_opt_out_on_modifier() -> None:
    items = [
        ("r", _fillet([_body("A")], "r_lid")),
        ("pocket", _ent("ExtrudeFeature", [_body("A")], "finish_ops_order:pocket must cut before the round")),
    ]
    assert _rule("finish_ops_order", items) == []


def test_order_every_offending_feature_reported() -> None:
    items = [
        ("r", _fillet([_body("A")], "r_lid")),
        ("p1", _ent("ExtrudeFeature", [_body("A")])),
        ("p2", _ent("HoleFeature", [_body("A")])),
    ]
    assert _rule("finish_ops_order", items) == [
        f"SKQ_WARN finish_ops_order: p1 after fillet on A {ORDER_TAIL}",
        f"SKQ_WARN finish_ops_order: p2 after fillet on A {ORDER_TAIL}",
    ]


def test_order_body_label_uses_name_when_present() -> None:
    items = [
        ("r", _fillet([_body("A", "Housing")], "r_lid")),
        ("pocket", _ent("ExtrudeFeature", [_body("A", "Housing")])),
    ]
    assert _rule("finish_ops_order", items) == [
        f"SKQ_WARN finish_ops_order: pocket after fillet on Housing {ORDER_TAIL}"
    ]


def test_order_multi_body_one_line_per_feature() -> None:
    items = [("r", _fillet([_body("B")], "r_lid")), ("pocket", _ent("ExtrudeFeature", [_body("A"), _body("B")]))]
    assert _rule("finish_ops_order", items) == [f"SKQ_WARN finish_ops_order: pocket after fillet on B {ORDER_TAIL}"]


# ---------------------------------------------------------------------------
# finish_ops_split: two fillets / two chamfers on one body with the same expression
# ---------------------------------------------------------------------------
def test_split_hit_two_fillets_same_radius() -> None:
    items = [("r1", _fillet([_body("A")], "r_lid")), ("r2", _fillet([_body("A")], "r_lid"))]
    assert _rule("finish_ops_split", items) == [
        "SKQ_WARN finish_ops_split: r1, r2 same r_lid -> one feature with all edges"
    ]


def test_split_hit_two_chamfers_same_distance() -> None:
    items = [("c1", _chamfer([_body("A")], "wall")), ("c2", _chamfer([_body("A")], "wall"))]
    assert _rule("finish_ops_split", items) == [
        "SKQ_WARN finish_ops_split: c1, c2 same wall -> one feature with all edges"
    ]


def test_split_hit_non_adjacent_same_expression() -> None:
    # edge_by_edge only sees neighbours; this rule sees the whole body
    items = [
        ("r1", _fillet([_body("A")], "r_lid")),
        ("c1", _chamfer([_body("A")], "wall")),
        ("r2", _fillet([_body("A")], "r_lid")),
    ]
    assert _rule("finish_ops_split", items) == [
        "SKQ_WARN finish_ops_split: r1, r2 same r_lid -> one feature with all edges"
    ]


def test_split_hit_whitespace_ignored() -> None:
    items = [("r1", _fillet([_body("A")], "0.5 mm")), ("r2", _fillet([_body("A")], "0.5mm"))]
    assert _rule("finish_ops_split", items) == [
        "SKQ_WARN finish_ops_split: r1, r2 same 0.5mm -> one feature with all edges"
    ]


def test_split_no_hit_different_expression() -> None:
    items = [("r1", _fillet([_body("A")], "r_lid")), ("r2", _fillet([_body("A")], "r_box"))]
    assert _rule("finish_ops_split", items) == []


def test_split_no_hit_fillet_and_chamfer_same_expression() -> None:
    items = [("r1", _fillet([_body("A")], "wall")), ("c1", _chamfer([_body("A")], "wall"))]
    assert _rule("finish_ops_split", items) == []


def test_split_no_hit_same_expression_other_body() -> None:
    items = [("r1", _fillet([_body("A")], "r_lid")), ("r2", _fillet([_body("B")], "r_lid"))]
    assert _rule("finish_ops_split", items) == []


def test_split_no_hit_bed_chamfer_never_counts() -> None:
    items = [("housing_bed_chamfer", _bed_chamfer([_body("A")])), ("c1", _chamfer([_body("A")], "bed_leg"))]
    assert _rule("finish_ops_split", items) == []


def test_split_no_hit_one_feature_two_sets_is_not_a_pair() -> None:
    # a single fillet with two sets of the same radius is already one feature
    items = [("r1", _fillet([_body("A")], "r_lid", "r_lid"))]
    assert _rule("finish_ops_split", items) == []


def test_split_opt_out_on_later_feature() -> None:
    items = [
        ("r1", _fillet([_body("A")], "r_lid")),
        ("r2", _fillet([_body("A")], "r_lid", skip="finish_ops_split:same radius on purpose")),
    ]
    assert _rule("finish_ops_split", items) == []


def test_split_one_line_per_extra_feature_names_first() -> None:
    items = [
        ("r1", _fillet([_body("A")], "r_lid")),
        ("r2", _fillet([_body("A")], "r_lid")),
        ("r3", _fillet([_body("A")], "r_lid")),
    ]
    assert _rule("finish_ops_split", items) == [
        "SKQ_WARN finish_ops_split: r1, r2 same r_lid -> one feature with all edges",
        "SKQ_WARN finish_ops_split: r1, r3 same r_lid -> one feature with all edges",
    ]


def test_split_multi_set_feature_counts_each_expression() -> None:
    # grouped fillet (fillet_groups) with sets r_a and r_b; a later single fillet with r_b -> pair on r_b
    items = [("grouped", _fillet([_body("A")], "r_a", "r_b")), ("r_single", _fillet([_body("A")], "r_b"))]
    assert _rule("finish_ops_split", items) == [
        "SKQ_WARN finish_ops_split: grouped, r_single same r_b -> one feature with all edges"
    ]
