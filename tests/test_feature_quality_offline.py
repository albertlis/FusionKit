"""Offline tests: fusion_kit.feature_quality — per rule hit / no-hit / opt-out, plus check() end to end.

No Fusion runtime: entities are SimpleNamespace / MagicMock fakes carrying objectType like adsk objects.
Lengths: fakes hold cm (adsk units); expectations are written in mm.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit import feature_quality as fq

CUT = 1  # FeatureOperations.CutFeatureOperation
JOIN = 0


def P(x: float, y: float, z: float) -> Any:
    return SimpleNamespace(x=x, y=y, z=z)


def _attrs(value: str | None = None) -> Any:
    attr = None if value is None else SimpleNamespace(value=value)
    return SimpleNamespace(itemByName=lambda group, name: attr)


def _obj(otype: str, ns: str = "fusion", **kw: Any) -> Any:
    return SimpleNamespace(objectType=f"adsk::{ns}::{otype}", **kw)


def _feat(otype: str, skip: str | None = None, **kw: Any) -> Any:
    return _obj(otype, isSuppressed=False, attributes=_attrs(skip), **kw)


def _coll(*items: Any) -> Any:
    return SimpleNamespace(count=len(items), item=lambda i: items[i])


def _dist(mm: float) -> Any:
    return _obj("DistanceExtentDefinition", distance=SimpleNamespace(value=mm / 10.0, expression=f"{mm} mm"))


def _profile(token: str = "p1", loops: list[Any] | None = None, constraints: list[Any] | None = None) -> Any:
    sketch = SimpleNamespace(geometricConstraints=_coll(*(constraints or [])))
    return _obj(
        "Profile",
        entityToken=token,
        plane=SimpleNamespace(origin=P(0, 0, 0), normal=P(0, 0, 1)),
        profileLoops=_coll(*(loops or [])),
        parentSketch=sketch,
    )


def _extrude(profile: Any, extent: Any, op: int = JOIN, skip: str | None = None, **kw: Any) -> Any:
    return _feat(
        "ExtrudeFeature",
        skip,
        profile=profile,
        extentOne=extent,
        extentTwo=None,
        operation=op,
        isThinExtrude=kw.pop("thin", False),
        bodies=kw.pop("bodies", []),
        **kw,
    )


def _comp(token: str | None) -> Any:
    return None if token is None else SimpleNamespace(entityToken=token)


def _plane_at_z_mm(z_mm: float, component: str | None = None) -> Any:
    geom = _obj("Plane", ns="core", origin=P(0, 0, z_mm / 10.0), normal=P(0, 0, 1))
    return _feat("ConstructionPlane", geometry=geom, parentComponent=_comp(component))


def _design(items: list[tuple[str, Any]]) -> Any:
    rows = [SimpleNamespace(name=n, isGroup=False, entity=e) for n, e in items]
    return SimpleNamespace(timeline=SimpleNamespace(count=len(rows), item=lambda i: rows[i]))


# ---------------------------------------------------------------------------
# plumbing: skip-value parsing, run detection
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("cut_blind_through:wall is open by design", {"cut_blind_through"}),
        ("cut_blind_through:a;twin_extrudes:b", {"cut_blind_through", "twin_extrudes"}),
        ("hole_as_extrude:reason: with colon", {"hole_as_extrude"}),
        ("cut_blind_through:", set()),
        ("bogus_rule:reason", set()),
        ("no separator", set()),
        ("junk;cut_blind_through:ok", {"cut_blind_through"}),
    ],
)
def test_parse_skip_value(value: str, expected: set[str]) -> None:
    assert fq._parse_skip_value(value) == expected


def test_consecutive_runs_finds_only_runs_of_two_or_more() -> None:
    keys: list[tuple[str, str] | None] = [("F", "r"), ("F", "r"), None, ("F", "s"), ("F", "s"), ("F", "s")]
    assert fq._consecutive_runs(keys) == [(0, 1), (3, 5)]


def test_consecutive_runs_single_and_empty() -> None:
    assert fq._consecutive_runs([("F", "r")]) == []
    assert fq._consecutive_runs([]) == []


# ---------------------------------------------------------------------------
# rule 1: extent_distance_matches_body
# ---------------------------------------------------------------------------
def test_extent_distance_hit_end_on_earlier_plane() -> None:
    ext = _extrude(_profile(), _dist(10.0))
    assert fq.rule_extent_distance_matches_body(ext, [("Plane1", _plane_at_z_mm(10.0))]) is not None


def test_extent_distance_hit_within_tolerance() -> None:
    ext = _extrude(_profile(), _dist(10.0))
    assert fq.rule_extent_distance_matches_body(ext, [("Plane1", _plane_at_z_mm(10.005))]) is not None


def test_extent_distance_no_hit_outside_tolerance() -> None:
    ext = _extrude(_profile(), _dist(10.0))
    assert fq.rule_extent_distance_matches_body(ext, [("Plane1", _plane_at_z_mm(10.05))]) is None


def test_extent_distance_no_hit_when_end_not_on_plane() -> None:
    ext = _extrude(_profile(), _dist(5.0))
    assert fq.rule_extent_distance_matches_body(ext, [("Plane1", _plane_at_z_mm(10.0))]) is None


def test_extent_distance_no_hit_shallow_0_3_mm_extrude_on_face() -> None:
    # 0.30 mm extrude matching a face: below SHALLOW_DEPTH_MM -> intentional, no line
    ext = _extrude(_profile(), _dist(0.3))
    assert fq.rule_extent_distance_matches_body(ext, [("Plane1", _plane_at_z_mm(0.3))]) is None


def test_extent_distance_hit_at_shallow_threshold_1_mm() -> None:
    ext = _extrude(_profile(), _dist(1.0))
    assert fq.rule_extent_distance_matches_body(ext, [("Plane1", _plane_at_z_mm(1.0))]) is not None


def test_extent_distance_no_hit_for_two_sided_extrude() -> None:
    ext = _extrude(_profile(), _dist(10.0))
    ext.extentTwo = _dist(10.0)
    assert fq.rule_extent_distance_matches_body(ext, [("Plane1", _plane_at_z_mm(10.0))]) is None


def test_extent_distance_hit_on_earlier_body_face() -> None:
    face = _obj("BRepFace", geometry=_obj("Plane", ns="core", origin=P(0, 0, 1.0), normal=P(0, 0, 1)))
    body_feat = _feat("ExtrudeFeature", bodies=[SimpleNamespace(faces=_coll(face))])
    ext = _extrude(_profile(), _dist(10.0))
    assert fq.rule_extent_distance_matches_body(ext, [("Base", body_feat)]) is not None


def _end_face(token: str, z_cm: float = 1.0) -> Any:
    return _obj(
        "BRepFace", entityToken=token, geometry=_obj("Plane", ns="core", origin=P(0, 0, z_cm), normal=P(0, 0, 1))
    )


def test_extent_distance_no_hit_join_end_cap_on_own_body() -> None:
    # join 0..10 mm: end cap at z=10 mm is a face of the joined body, which the base feature also reports.
    # The join created it (endFaces) -> excluded by entityToken.
    cap = _end_face("cap")
    body = SimpleNamespace(entityToken="b1", faces=_coll(cap))
    base = _feat("ExtrudeFeature", bodies=[body])
    join = _extrude(_profile(), _dist(10.0), op=JOIN, bodies=[body], endFaces=_coll(cap))
    assert fq.rule_extent_distance_matches_body(join, [("Base", base)]) is None


def test_extent_distance_hit_pre_existing_face_on_own_body() -> None:
    # join 0..10 mm; same body also has an older face at z=10 mm that this join did not create -> flagged
    cap = _end_face("cap")
    older = _end_face("older")
    body = SimpleNamespace(entityToken="b1", faces=_coll(cap, older))
    base = _feat("ExtrudeFeature", bodies=[body])
    join = _extrude(_profile(), _dist(10.0), op=JOIN, bodies=[body], endFaces=_coll(cap))
    assert fq.rule_extent_distance_matches_body(join, [("Base", base)]) is not None


def test_extent_distance_no_crash_without_face_attrs() -> None:
    # no endFaces/startFaces/sideFaces attr on the feature (getattr -> None): nothing excluded, no AttributeError
    body = SimpleNamespace(entityToken="b1", faces=_coll(_end_face("other", z_cm=0.5)))
    base = _feat("ExtrudeFeature", bodies=[body])
    join = _extrude(_profile(), _dist(10.0), op=JOIN, bodies=[body])
    assert fq.rule_extent_distance_matches_body(join, [("Base", base)]) is None


# ---------------------------------------------------------------------------
# rule 2: cut_blind_through
# ---------------------------------------------------------------------------
def _cut_body(z_max_cm: float = 1.0) -> Any:
    box = SimpleNamespace(minPoint=P(-1, -1, 0), maxPoint=P(1, 1, z_max_cm))
    return SimpleNamespace(boundingBox=box)


def test_cut_blind_through_hit_deeper_than_body() -> None:
    cut = _extrude(_profile(), _dist(30.0), op=CUT, bodies=[_cut_body()])
    assert fq.rule_cut_blind_through(cut, []) is not None


def test_cut_blind_through_hit_negative_depth_past_body() -> None:
    cut = _extrude(_profile(), _dist(-30.0), op=CUT, bodies=[_cut_body()])
    assert fq.rule_cut_blind_through(cut, []) is not None


def test_cut_blind_through_no_hit_within_body() -> None:
    cut = _extrude(_profile(), _dist(5.0), op=CUT, bodies=[_cut_body()])
    assert fq.rule_cut_blind_through(cut, []) is None


def test_cut_blind_through_no_hit_for_join() -> None:
    join = _extrude(_profile(), _dist(30.0), op=JOIN, bodies=[_cut_body()])
    assert fq.rule_cut_blind_through(join, []) is None


def test_cut_blind_through_no_hit_0_3_mm_groove_ending_at_bbox_minus_0_00() -> None:
    # warp groove: body lies behind the sketch plane (bbox max z = 0) -> far side -0.00; 0.30 mm is intentional shallow
    body = SimpleNamespace(boundingBox=SimpleNamespace(minPoint=P(-1, -1, -1), maxPoint=P(1, 1, 0.0)))
    cut = _extrude(_profile(), _dist(0.3), op=CUT, bodies=[body])
    assert fq.rule_cut_blind_through(cut, []) is None


@pytest.mark.parametrize(("depth_mm", "hit"), [(0.99, False), (1.0, True)])
def test_cut_blind_through_shallow_threshold(depth_mm: float, hit: bool) -> None:
    # body 0.5 mm deep from the plane: 1.0 mm cut overshoots 0.5 mm -> hit; below 1.0 mm skipped
    cut = _extrude(_profile(), _dist(depth_mm), op=CUT, bodies=[_cut_body(z_max_cm=0.05)])
    assert (fq.rule_cut_blind_through(cut, []) is not None) is hit


def test_cut_blind_through_hit_5_mm_cut_2_mm_past_bbox() -> None:
    cut = _extrude(_profile(), _dist(5.0), op=CUT, bodies=[_cut_body(z_max_cm=0.3)])
    assert fq.rule_cut_blind_through(cut, []) is not None


def test_cut_blind_through_no_hit_overshoot_within_tolerance() -> None:
    # 3.005 mm cut on a 3 mm body: overshoot 0.005 mm <= 0.01 mm tolerance
    cut = _extrude(_profile(), _dist(3.005), op=CUT, bodies=[_cut_body(z_max_cm=0.3)])
    assert fq.rule_cut_blind_through(cut, []) is None


# ---------------------------------------------------------------------------
# rule 3: twin_extrudes
# ---------------------------------------------------------------------------
def test_twin_extrudes_hit_opposite_sign_same_profile() -> None:
    first = _extrude(_profile("p"), _dist(5.0))
    second = _extrude(_profile("p"), _dist(-5.0))
    assert fq.rule_twin_extrudes(second, [("Extrude1", first)]) is not None


def test_twin_extrudes_no_hit_same_sign() -> None:
    first = _extrude(_profile("p"), _dist(5.0))
    second = _extrude(_profile("p"), _dist(5.0))
    assert fq.rule_twin_extrudes(second, [("Extrude1", first)]) is None


def test_twin_extrudes_no_hit_different_profile() -> None:
    first = _extrude(_profile("p"), _dist(5.0))
    second = _extrude(_profile("q"), _dist(-5.0))
    assert fq.rule_twin_extrudes(second, [("Extrude1", first)]) is None


# ---------------------------------------------------------------------------
# rule 4: hole_as_extrude
# ---------------------------------------------------------------------------
def _loop(*sketch_types: str) -> Any:
    curves = [SimpleNamespace(sketchEntity=_obj(t)) for t in sketch_types]
    return SimpleNamespace(profileCurves=_coll(*curves))


def test_hole_as_extrude_hit_single_circle_cut() -> None:
    cut = _extrude(_profile(loops=[_loop("SketchCircle")]), _dist(10.0), op=CUT)
    assert fq.rule_hole_as_extrude(cut, []) is not None


def test_hole_as_extrude_no_hit_for_join_circle() -> None:
    join = _extrude(_profile(loops=[_loop("SketchCircle")]), _dist(10.0), op=JOIN)
    assert fq.rule_hole_as_extrude(join, []) is None


def test_hole_as_extrude_no_hit_for_line_profile() -> None:
    cut = _extrude(_profile(loops=[_loop("SketchLine", "SketchLine", "SketchLine")]), _dist(10.0), op=CUT)
    assert fq.rule_hole_as_extrude(cut, []) is None


# ---------------------------------------------------------------------------
# rule 6: plane_offset_expr
# ---------------------------------------------------------------------------
def _offset_plane(expr: str, z_mm: float = 10.0, component: str | None = None) -> Any:
    defn = _obj("ConstructionPlaneOffsetDefinition", offset=SimpleNamespace(expression=expr))
    geom = _obj("Plane", ns="core", origin=P(0, 0, z_mm / 10.0), normal=P(0, 0, 1))
    return _feat("ConstructionPlane", definition=defn, geometry=geom, parentComponent=_comp(component))


# reference planes = origin plane (0 mm, implicit for axis normals) + Plane20 -> midpoint at 10 mm
@pytest.mark.parametrize(
    ("expr", "hit"),
    [("wall_h/2", True), ("wall_h / 2", True), ("wall_h", False), ("wall_h/2.5", False), ("wall_h/20", False)],
)
def test_plane_offset_expr(expr: str, hit: bool) -> None:
    earlier = [("Plane20", _plane_at_z_mm(20.0))]
    assert (fq.rule_plane_offset_expr(_offset_plane(expr), earlier) is not None) is hit


def test_plane_offset_expr_origin_half_alone_no_hit() -> None:
    # x/2 offset of a single origin plane: one reference plane only, no pair -> no line
    assert fq.rule_plane_offset_expr(_offset_plane("roller_axis_spacing/2", z_mm=-5.0), []) is None


def test_plane_offset_expr_not_midpoint_no_hit() -> None:
    earlier = [("Plane20", _plane_at_z_mm(20.0))]
    assert fq.rule_plane_offset_expr(_offset_plane("wall_h/2", z_mm=12.0), earlier) is None


def test_plane_offset_expr_reference_in_other_component_no_hit() -> None:
    earlier = [("Plane20", _plane_at_z_mm(20.0, component="B"))]
    assert fq.rule_plane_offset_expr(_offset_plane("wall_h/2", component="A"), earlier) is None


def test_check_plane_offset_midplane_gives_one_line() -> None:
    design = _design([("Plane20", _plane_at_z_mm(20.0)), ("mid_plane", _offset_plane("wall_h/2"))])
    assert fq.check(design) == [
        "SKQ_WARN plane_offset_expr: mid_plane offset 'wall_h/2' is half a distance -> plane_midplane (Midplane)"
    ]


# ---------------------------------------------------------------------------
# rule 7: edge_by_edge (sequence rule over timeline entries)
# ---------------------------------------------------------------------------
def _fillet(expr: str, skip: str | None = None) -> Any:
    edge_set = _obj("ConstantRadiusFilletEdgeSet", radius=SimpleNamespace(expression=expr))
    return _feat("FilletFeature", skip, edgeSets=_coll(edge_set))


def _chamfer(expr: str) -> Any:
    edge_set = _obj("EqualDistanceChamferEdgeSet", distance=SimpleNamespace(expression=expr))
    return _feat("ChamferFeature", edgeSets=_coll(edge_set))


def test_edge_by_edge_hit_second_of_same_radius_pair() -> None:
    hits = fq.rule_edge_by_edge([("fillet_a", _fillet("r_lid")), ("fillet_b", _fillet("r_lid"))])
    assert [name for name, _ in hits] == ["fillet_b"]


def test_edge_by_edge_no_hit_different_expression() -> None:
    assert fq.rule_edge_by_edge([("fillet_a", _fillet("r_lid")), ("fillet_b", _fillet("r_box"))]) == []


def test_edge_by_edge_no_hit_fillet_then_chamfer_same_expression() -> None:
    assert fq.rule_edge_by_edge([("fillet_a", _fillet("0.5 mm")), ("chamfer_b", _chamfer("0.5 mm"))]) == []


def test_edge_by_edge_no_hit_when_separated() -> None:
    entries = [("fillet_a", _fillet("r_lid")), ("extrude_x", _feat("ExtrudeFeature")), ("fillet_b", _fillet("r_lid"))]
    assert fq.rule_edge_by_edge(entries) == []


def test_edge_by_edge_skip_on_second_member() -> None:
    skipped = _fillet("r_lid", skip="edge_by_edge:two edge sets needed for the lid seam")
    assert fq.rule_edge_by_edge([("fillet_a", _fillet("r_lid")), ("fillet_b", skipped)]) == []


# ---------------------------------------------------------------------------
# rule 8: thin_as_offset
# ---------------------------------------------------------------------------
def _offset_profile(children: list[str], loop_tokens: list[str]) -> Any:
    curves = [SimpleNamespace(sketchEntity=SimpleNamespace(entityToken=t)) for t in loop_tokens]
    loop = SimpleNamespace(profileCurves=_coll(*curves))
    child_curves = [SimpleNamespace(entityToken=t) for t in children]
    constraint = _obj("OffsetConstraint", childCurves=child_curves)
    return _profile(loops=[loop], constraints=[constraint])


def test_thin_as_offset_hit_profile_of_offset_curves() -> None:
    ext = _extrude(_offset_profile(["c1", "c2"], ["c1", "c2"]), _dist(10.0))
    assert fq.rule_thin_as_offset(ext, []) is not None


def test_thin_as_offset_no_hit_single_offset_curve() -> None:
    ext = _extrude(_offset_profile(["c1"], ["c1", "c2"]), _dist(10.0))
    assert fq.rule_thin_as_offset(ext, []) is None


def test_thin_as_offset_no_hit_already_thin() -> None:
    ext = _extrude(_offset_profile(["c1", "c2"], ["c1", "c2"]), _dist(10.0), thin=True)
    assert fq.rule_thin_as_offset(ext, []) is None


# ---------------------------------------------------------------------------
# check(): end to end, opt-out attribute, suppressed items
# ---------------------------------------------------------------------------
def test_check_reports_hit_with_feature_name() -> None:
    design = _design([("Plane1", _plane_at_z_mm(10.0)), ("lid_extrude", _extrude(_profile(), _dist(10.0)))])
    expected = (
        "SKQ_WARN extent_distance_matches_body: lid_extrude "
        "distance 10.00 mm ends on an earlier face/plane -> use to= (ToEntityExtent)"
    )
    assert fq.check(design) == [expected]


def test_check_opt_out_attribute_silences_rule() -> None:
    skip = "extent_distance_matches_body:lid sits on the base by design"
    design = _design([("Plane1", _plane_at_z_mm(10.0)), ("lid_extrude", _extrude(_profile(), _dist(10.0), skip=skip))])
    assert fq.check(design) == []


def test_check_skips_suppressed_items() -> None:
    suppressed = _extrude(_profile(), _dist(10.0))
    suppressed.isSuppressed = True
    design = _design([("Plane1", _plane_at_z_mm(10.0)), ("lid_extrude", suppressed)])
    assert fq.check(design) == []


def test_check_edge_by_edge_line_uses_second_name() -> None:
    design = _design([("fillet_a", _fillet("r_lid")), ("fillet_b", _fillet("r_lid"))])
    assert fq.check(design) == ["SKQ_WARN edge_by_edge: fillet_b same r_lid as fillet_a -> one edge set, tangent chain"]


def test_check_empty_timeline() -> None:
    assert fq.check(_design([])) == []


# ---------------------------------------------------------------------------
# BRepFace profiles (direct face, not a sketch profile): skipped, never raise
# ---------------------------------------------------------------------------
def _brep_profile(token: str = "f1") -> Any:
    return _obj("BRepFace", entityToken=token, geometry=_obj("Plane", ns="core", origin=P(0, 0, 0), normal=P(0, 0, 1)))


def test_brep_face_profile_skipped_no_crash() -> None:
    cut = _extrude(_brep_profile("f1"), _dist(30.0), op=CUT, bodies=[_cut_body()])
    join = _extrude(_brep_profile("f2"), _dist(10.0), op=JOIN, bodies=[_cut_body()])
    base = _extrude(_profile("base"), _dist(5.0), bodies=[_cut_body()])
    earlier = [("Base", base)]
    for ent in (cut, join):
        for rule, fn in fq.SINGLE_RULES:
            assert fn(ent, earlier) is None, rule
    design = _design([("Base", base), ("cut_face", cut), ("join_face", join)])
    assert fq.check(design) == []
