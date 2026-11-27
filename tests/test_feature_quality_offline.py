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


def _attrs(value: str | None = None, role: str | None = None, warp: bool = False) -> Any:
    """Feature attributes: skq/skq_skip = value, fk/role = role, dfam/warp_groove = tag when warp; any other (group, name) -> None (like adsk)."""
    table = {
        (fq.ATTR_GROUP, fq.ATTR_NAME): value,
        (fq.ROLE_GROUP, fq.ROLE_NAME): role,
        (fq.DFAM_GROUP, fq.WARP_GROOVE_ATTR): "1" if warp else None,
    }

    def item(group: str, name: str) -> Any:
        v = table.get((group, name))
        return None if v is None else SimpleNamespace(value=v)

    return SimpleNamespace(itemByName=item)


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


# ---------------------------------------------------------------------------
# single pass: running index, each body face read once, budget stop
# ---------------------------------------------------------------------------
class _CountingFace:
    """BRepFace stand-in: every .geometry read increments reads[0]."""

    def __init__(self, token: str, geom: Any, reads: list[int]) -> None:
        self.entityToken = token
        self._geom = geom
        self._reads = reads

    @property
    def geometry(self) -> Any:
        self._reads[0] += 1
        return self._geom


def _timeline_of_bodies(n: int, faces_per_body: int, reads: list[int]) -> Any:
    """n extrudes, each on its own body with faces_per_body planar faces at 1..faces_per_body mm (no end match)."""
    items: list[tuple[str, Any]] = []
    for k in range(n):
        faces = [
            _CountingFace(
                f"b{k}f{j}", _obj("Plane", ns="core", origin=P(0, 0, (1.0 + j) / 10.0), normal=P(0, 0, 1)), reads
            )
            for j in range(faces_per_body)
        ]
        body = SimpleNamespace(entityToken=f"b{k}", faces=_coll(*faces))
        items.append((f"ext{k}", _extrude(_profile(f"p{k}"), _dist(5.0), bodies=[body])))
    return _design(items)


def test_check_reads_each_body_face_once() -> None:
    small: list[int] = [0]
    big: list[int] = [0]
    assert fq.check(_timeline_of_bodies(20, 4, small)) == []
    assert fq.check(_timeline_of_bodies(40, 4, big)) == []
    assert small[0] == 20 * 4
    assert big[0] == 40 * 4  # linear in T: doubling T doubles reads (old prefix rescan: ~4x)


def _circle_loop(token: str) -> Any:
    """Profile loop of one SketchCircle with entityToken (thin rule reads the token)."""
    curve = SimpleNamespace(sketchEntity=_obj("SketchCircle", entityToken=token))
    return SimpleNamespace(profileCurves=_coll(curve))


def _mixed_design() -> Any:
    """Timeline hitting every rule once: cross-body plane, own end cap that must be ignored, twin, cut, hole, offset midplane,
    edge_by_edge, BRepFace profile skipped, thin-as-offset."""
    body_a = SimpleNamespace(entityToken="A", faces=_coll(_end_face("old_face", z_cm=1.0)))
    body_b = SimpleNamespace(entityToken="B", faces=_coll(_end_face("own_cap", z_cm=1.2)))
    return _design(
        [
            ("Plane20", _plane_at_z_mm(20.0)),
            ("base", _extrude(_profile("p1"), _dist(5.0), bodies=[body_a])),
            (
                "cap_join",
                _extrude(_profile("p3"), _dist(12.0), bodies=[body_b], endFaces=_coll(_end_face("own_cap", z_cm=1.2))),
            ),
            ("lid", _extrude(_profile("p2"), _dist(20.0))),
            ("lid_mirror", _extrude(_profile("p2"), _dist(-20.0))),
            ("cut_deep", _extrude(_profile("p4"), _dist(30.0), op=CUT, bodies=[_cut_body()])),
            ("hole1", _extrude(_profile("p5", loops=[_circle_loop("circ")]), _dist(3.0), op=CUT)),
            ("wall_mid", _offset_plane("wall_h/2")),
            ("r1", _fillet("r_lid")),
            ("r2", _fillet("r_lid")),
            ("face_cut", _extrude(_brep_profile("f9"), _dist(10.0), op=CUT, bodies=[_cut_body()])),
            ("thin_ok", _extrude(_offset_profile(["c1", "c2"], ["c1", "c2"]), _dist(10.0))),
        ]
    )


MIXED_FINDINGS: list[str] = [  # produced by the pre-change (prefix-rescan) implementation on _mixed_design()
    "SKQ_WARN extent_distance_matches_body: lid distance 20.00 mm ends on an earlier face/plane -> use to= (ToEntityExtent)",
    "SKQ_WARN extent_distance_matches_body: lid_mirror distance 20.00 mm ends on an earlier face/plane -> use to= (ToEntityExtent)",
    "SKQ_WARN twin_extrudes: lid_mirror opposite-sign extrude of same profile as lid -> symmetric / two sides",
    "SKQ_WARN cut_blind_through: cut_deep cut 30.00 mm runs past body end at 10.00 mm -> through_all",
    "SKQ_WARN hole_as_extrude: hole1 cut of a single circle profile -> holes.hole_simple / holes.hole_counterbore",
    "SKQ_WARN plane_offset_expr: wall_mid offset 'wall_h/2' is half a distance -> plane_midplane (Midplane)",
    "SKQ_WARN extent_distance_matches_body: thin_ok distance 10.00 mm ends on an earlier face/plane -> use to= (ToEntityExtent)",
    "SKQ_WARN thin_as_offset: thin_ok profile drawn as offset curves -> thin extrude (thickness param)",
    "SKQ_WARN edge_by_edge: r2 same r_lid as r1 -> one edge set, tangent chain",
]


def test_check_findings_equal_old_implementation() -> None:
    assert fq.check(_mixed_design()) == MIXED_FINDINGS


def test_check_budget_stops_with_skip_line() -> None:
    design = _design(
        [
            ("Plane1", _plane_at_z_mm(10.0)),
            ("lid_a", _extrude(_profile(), _dist(10.0))),
            ("filler", _feat("ExtrudeFeature")),
            ("lid_b", _extrude(_profile(), _dist(10.0))),
        ]
    )
    calls = [0]

    def over_after_two() -> bool:
        calls[0] += 1
        return calls[0] > 2

    assert fq.check(design, over_budget=over_after_two) == [
        "SKQ_WARN extent_distance_matches_body: lid_a distance 10.00 mm ends on an earlier face/plane -> use to= (ToEntityExtent)",
        "SKIP fq budget: 2 of 4 entries not checked",
    ]
    assert calls[0] == 3  # polled before each entry; stopped at entry index 2
    assert fq.check(design, over_budget=lambda: True) == ["SKIP fq budget: 4 of 4 entries not checked"]


# ---------------------------------------------------------------------------
# finishing ops: fillet / chamfer last on a body, one feature per kind (finishing_not_last, finishing_split)
# ---------------------------------------------------------------------------
def _on_body(otype: str, token: str, skip: str | None = None, role: str | None = None, warp: bool = False) -> Any:
    return _obj(
        otype,
        isSuppressed=False,
        attributes=_attrs(skip, role, warp),
        bodies=_coll(SimpleNamespace(entityToken=token)),
        operation=JOIN,
        profile=None,
        extentOne=None,
        extentTwo=None,
        isThinExtrude=False,
    )


def _fillet_on(token: str, skip: str | None = None) -> Any:
    return _on_body("FilletFeature", token, skip)


def _chamfer_on(token: str, skip: str | None = None) -> Any:
    return _on_body("ChamferFeature", token, skip)


def _fin_check(items: list[tuple[str, Any]]) -> list[str]:
    return fq.check(_design(items))


def test_finishing_not_last_fillet_then_extrude_same_body() -> None:
    assert _fin_check([("r_outer", _fillet_on("A")), ("pocket", _on_body("ExtrudeFeature", "A"))]) == [
        "SKQ_WARN finishing_not_last: pocket r_outer (FilletFeature) on same body is not last -> fillet/chamfer after ExtrudeFeature",
        "SKQ_WARN finish_ops_order: pocket after fillet on A -> move before finishing block (timeline order)",
    ]


@pytest.mark.parametrize(
    "otype",
    [
        "ExtrudeFeature",
        "RevolveFeature",
        "CombineFeature",
        "RectangularPatternFeature",
        "CircularPatternFeature",
        "MirrorFeature",
    ],
)
def test_finishing_not_last_every_modifier_kind(otype: str) -> None:
    got = _fin_check([("ch", _chamfer_on("A")), ("mod", _on_body(otype, "A"))])
    assert got == [
        f"SKQ_WARN finishing_not_last: mod ch (ChamferFeature) on same body is not last -> fillet/chamfer after {otype}",
        "SKQ_WARN finish_ops_order: mod after chamfer on A -> move before finishing block (timeline order)",
    ]


def test_finishing_no_hit_modifier_on_other_body() -> None:
    assert _fin_check([("r", _fillet_on("A")), ("pocket", _on_body("ExtrudeFeature", "B"))]) == []


def test_finishing_no_hit_fillet_is_last() -> None:
    assert _fin_check([("base", _on_body("ExtrudeFeature", "A")), ("r", _fillet_on("A"))]) == []


def test_finishing_warp_grooves_after_bed_chamfer_exempt() -> None:
    assert _fin_check([("bed_chamfer", _chamfer_on("A")), ("warp_grooves", _on_body("ExtrudeFeature", "A"))]) == []


def test_finishing_warp_labeled_fillet_exempt() -> None:
    assert _fin_check([("warp_corner_fillet", _fillet_on("A")), ("pocket", _on_body("ExtrudeFeature", "A"))]) == []


def test_finishing_not_last_reported_once_per_op() -> None:
    got = [
        line
        for line in _fin_check(
            [("r", _fillet_on("A")), ("p1", _on_body("ExtrudeFeature", "A")), ("p2", _on_body("ExtrudeFeature", "A"))]
        )
        if line.startswith("SKQ_WARN finishing_not_last:")
    ]
    assert len(got) == 1
    assert got[0].startswith("SKQ_WARN finishing_not_last: p1 ")


def test_finishing_split_second_fillet_same_body() -> None:
    assert _fin_check([("r1", _fillet_on("A")), ("r2", _fillet_on("A"))]) == [
        "SKQ_WARN finishing_split: r2 2nd fillet on same body (first: r1) -> one feature: features.fillet_groups"
    ]


def test_finishing_split_second_chamfer_same_body() -> None:
    assert _fin_check([("c1", _chamfer_on("A")), ("c2", _chamfer_on("A"))]) == [
        "SKQ_WARN finishing_split: c2 2nd chamfer on same body (first: c1) -> one feature: features.chamfer_groups"
    ]


def test_finishing_split_third_op_not_repeated() -> None:
    got = _fin_check([("r1", _fillet_on("A")), ("r2", _fillet_on("A")), ("r3", _fillet_on("A"))])
    assert len(got) == 1
    assert got[0].startswith("SKQ_WARN finishing_split: r2 ")


def test_finishing_split_no_hit_one_per_kind_or_other_body() -> None:
    assert _fin_check([("r1", _fillet_on("A")), ("c1", _chamfer_on("A")), ("r2", _fillet_on("B"))]) == []


def _bed_chamfer_tagged(token: str) -> Any:
    """Chamfer as built by features_bed.bed_chamfer: attribute fk/role = bed_chamfer, label not required."""
    return _on_body("ChamferFeature", token, role=fq.ROLE_BED_CHAMFER)


def test_finishing_split_bed_chamfer_tagged_not_first() -> None:
    # tagged bed chamfer is not counted as first: the plain chamfer after it is the first counted one
    assert _fin_check([("housing_bed_chamfer", _bed_chamfer_tagged("A")), ("c1", _chamfer_on("A"))]) == []


def test_finishing_split_bed_chamfer_tagged_not_second() -> None:
    assert _fin_check([("c1", _chamfer_on("A")), ("bed", _bed_chamfer_tagged("A"))]) == []


def test_finishing_split_tagged_bed_chamfer_without_bed_label_exempt() -> None:
    # attribute decides, label irrelevant
    assert _fin_check([("c1", _chamfer_on("A")), ("outer_edge_break", _bed_chamfer_tagged("A"))]) == []


def test_finishing_split_bed_chamfer_label_fallback_not_counted() -> None:
    # no attribute (older build): label containing bed_chamfer is enough, either position
    assert _fin_check([("c1", _chamfer_on("A")), ("housing_bed_chamfer", _chamfer_on("A"))]) == []
    assert _fin_check([("wall_bracket_bed_chamfer", _chamfer_on("A")), ("c1", _chamfer_on("A"))]) == []


def test_finishing_split_two_bed_chamfers_no_hit() -> None:
    assert _fin_check([("bed_a", _bed_chamfer_tagged("A")), ("bed_b", _bed_chamfer_tagged("A"))]) == []


def test_finishing_split_plain_chamfer_after_bed_chamfer_is_first() -> None:
    # bed chamfer exempt; the 2nd plain chamfer is the 2nd counted one -> WARN, first = c1
    assert _fin_check([("bed", _bed_chamfer_tagged("A")), ("c1", _chamfer_on("A")), ("c2", _chamfer_on("A"))]) == [
        "SKQ_WARN finishing_split: c2 2nd chamfer on same body (first: c1) -> one feature: features.chamfer_groups"
    ]


def test_finishing_split_bed_label_on_fillet_not_exempt() -> None:
    # exemption is chamfer-only: a fillet with bed_chamfer in its name still counts
    assert _fin_check([("r1", _fillet_on("A")), ("bed_chamfer_fillet", _fillet_on("A"))]) == [
        "SKQ_WARN finishing_split: bed_chamfer_fillet 2nd fillet on same body (first: r1) -> one feature: features.fillet_groups"
    ]


def test_finishing_bed_chamfer_still_not_last_checked() -> None:
    # exemption is finishing_split only: a bed chamfer followed by a plain extrude still WARNs finishing_not_last
    assert _fin_check(
        [("housing_bed_chamfer", _bed_chamfer_tagged("A")), ("pocket", _on_body("ExtrudeFeature", "A"))]
    ) == [
        "SKQ_WARN finishing_not_last: pocket housing_bed_chamfer (ChamferFeature) on same body is not last -> fillet/chamfer after ExtrudeFeature",
        "SKQ_WARN finish_ops_order: pocket after chamfer on A -> move before finishing block (timeline order)",
    ]


def test_finishing_opt_out_split_and_not_last() -> None:
    assert _fin_check([("r1", _fillet_on("A")), ("r2", _fillet_on("A", "finishing_split:two radii on purpose"))]) == []
    got = _fin_check(
        [
            ("r1", _fillet_on("A", "finishing_not_last:needed before pocket")),
            ("pocket", _on_body("ExtrudeFeature", "A")),
        ]
    )
    assert [line for line in got if "finishing_not_last" in line] == []


# ---------------------------------------------------------------------------
# warp_not_last: warp relief grooves are the last features of a body (after bed chamfer and every other op)
# ---------------------------------------------------------------------------
def _groove(token: str, name: str = "warp_grooves") -> tuple[str, Any]:
    return (name, _on_body("ExtrudeFeature", token, warp=True))


def _after(kind: str, name: str = "warp_grooves") -> str:
    return f"{kind} after warp groove {name} on same body -> grooves last"


def test_warp_not_last_extrude_after_groove_same_body() -> None:
    assert _fin_check([_groove("A"), ("pocket", _on_body("ExtrudeFeature", "A"))]) == [
        f"SKQ_WARN warp_not_last: pocket {_after('ExtrudeFeature')}"
    ]


def test_warp_not_last_bed_chamfer_after_groove() -> None:
    assert _fin_check([_groove("A"), ("housing_bed_chamfer", _chamfer_on("A"))]) == [
        f"SKQ_WARN warp_not_last: housing_bed_chamfer {_after('ChamferFeature')}"
    ]


@pytest.mark.parametrize(
    "otype",
    [
        "FilletFeature",
        "ChamferFeature",
        "ExtrudeFeature",
        "RevolveFeature",
        "CombineFeature",
        "RectangularPatternFeature",
        "CircularPatternFeature",
        "MirrorFeature",
    ],
)
def test_warp_not_last_every_kind(otype: str) -> None:
    assert _fin_check([_groove("A"), ("mod", _on_body(otype, "A"))]) == [f"SKQ_WARN warp_not_last: mod {_after(otype)}"]


def test_warp_not_last_untagged_name_fallback() -> None:
    # no attribute (older build): a feature named warp... is the groove
    assert _fin_check(
        [("warp_grooves", _on_body("ExtrudeFeature", "A")), ("pocket", _on_body("ExtrudeFeature", "A"))]
    ) == [f"SKQ_WARN warp_not_last: pocket {_after('ExtrudeFeature')}"]


def test_warp_not_last_untagged_cut_is_not_a_groove() -> None:
    assert _fin_check([("relief", _on_body("ExtrudeFeature", "A")), ("pocket", _on_body("ExtrudeFeature", "A"))]) == []


def test_warp_not_last_tag_decides_not_label() -> None:
    assert _fin_check([_groove("A", "cut_a"), ("pocket", _on_body("ExtrudeFeature", "A"))]) == [
        f"SKQ_WARN warp_not_last: pocket {_after('ExtrudeFeature', 'cut_a')}"
    ]


def test_warp_tagged_groove_not_a_finishing_modifier() -> None:
    # tagged groove with a plain name is exempt from finishing_not_last like a warp-labeled one
    assert _fin_check([("r", _fillet_on("A")), _groove("A", "cut_a")]) == []


def test_warp_not_last_no_hit_other_body_or_before_groove() -> None:
    assert _fin_check([("pocket", _on_body("ExtrudeFeature", "A")), _groove("A")]) == []
    assert _fin_check([_groove("A"), ("pocket", _on_body("ExtrudeFeature", "B"))]) == []


def test_warp_not_last_reported_at_each_later_feature() -> None:
    assert _fin_check(
        [_groove("A"), ("p1", _on_body("ExtrudeFeature", "A")), ("p2", _on_body("ExtrudeFeature", "A"))]
    ) == [
        f"SKQ_WARN warp_not_last: p1 {_after('ExtrudeFeature')}",
        f"SKQ_WARN warp_not_last: p2 {_after('ExtrudeFeature')}",
    ]


def test_warp_not_last_warp_named_and_second_groove_exempt() -> None:
    assert _fin_check([_groove("A"), ("warp_corner_fillet", _fillet_on("A"))]) == []
    assert _fin_check([_groove("A"), _groove("A", "warp_grooves_2")]) == []


def test_warp_not_last_opt_out() -> None:
    assert (
        _fin_check(
            [_groove("A"), ("pocket", _on_body("ExtrudeFeature", "A", "warp_not_last:pocket needs groove first"))]
        )
        == []
    )
