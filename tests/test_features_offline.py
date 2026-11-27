"""Offline tests for fusion_kit.features: profile, fillet/chamfer, extrude (options, bodies, name),
select_edges, combine, pattern qty guard, face_normal, collection; plus expr.as_str / _vi.

No Fusion runtime: adsk stubs come from conftest.py. Geometry is faked with plain objects;
units are Fusion cm.
"""

import itertools
import math
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from fusion_kit import enums, features, features_axes, features_pattern
from fusion_kit.expr import Ex, as_str
from fusion_kit.features import (
    _OPS,
    _vi,
    chamfer,
    collection,
    combine,
    extrude,
    face_normal,
    fillet,
    mirror,
    pattern_circular,
    pattern_grid,
    pattern_rectangular,
    profile,
    select_edges,
)
from fusion_kit.sketch import poly

_CORE: Any = sys.modules["adsk.core"]
_FUS: Any = sys.modules["adsk.fusion"]


# ---------------------------------------------------------------------------
# Fake geometry: 3D point / box (cm), profiles, sketches
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Pt:
    x: float
    y: float
    z: float = 0.0

    def distanceTo(self, other: "_Pt") -> float:
        return math.dist((self.x, self.y, self.z), (other.x, other.y, other.z))


@dataclass(frozen=True)
class _Box:
    minPoint: _Pt
    maxPoint: _Pt


class _Prof:
    """Profile stand-in: bbox corners (x0, y0, x1, y1), loop count, area."""

    def __init__(self, x0: float, y0: float, x1: float, y1: float, loops: int, area: float) -> None:
        self.boundingBox = _Box(_Pt(x0, y0), _Pt(x1, y1))
        self.profileLoops = SimpleNamespace(count=loops)
        self._area = area

    def areaProperties(self) -> SimpleNamespace:
        return SimpleNamespace(area=self._area)


def _sk(*profs: _Prof) -> Any:
    return SimpleNamespace(profiles=list(profs))


# ---------------------------------------------------------------------------
# profile()
# ---------------------------------------------------------------------------
def test_profile_outer_false_without_single_loop_raises() -> None:
    sk = _sk(_Prof(0, 0, 10, 10, loops=2, area=60), _Prof(20, 0, 30, 10, loops=2, area=60))
    with pytest.raises(ValueError, match="none single-loop"):
        profile(sk, outer=False)


def test_profile_outer_false_picks_smallest_single_loop() -> None:
    small = _Prof(0, 0, 2, 2, loops=1, area=4)
    sk = _sk(
        _Prof(0, 0, 10, 10, loops=1, area=100),
        _Prof(0, 0, 10, 10, loops=2, area=60),
        small,
    )
    assert profile(sk, outer=False) is small


def test_profile_outer_true_disjoint_solid_raises() -> None:
    annulus = _Prof(0, 0, 10, 10, loops=2, area=60)
    stray = _Prof(20, 0, 25, 5, loops=1, area=25)
    with pytest.raises(ValueError, match="outside the outermost"):
        profile(_sk(annulus, stray), outer=True)


def test_profile_outer_true_concentric_nested_accepted() -> None:
    outer = _Prof(0, 0, 10, 10, loops=2, area=60)
    ring = _Prof(2, 2, 8, 8, loops=2, area=20)
    disc = _Prof(3, 3, 7, 7, loops=1, area=16)
    assert profile(_sk(ring, disc, outer), outer=True) is outer


def test_profiles_returns_all_then_filters_by_predicate() -> None:
    annulus = _Prof(0, 0, 10, 10, loops=2, area=60)
    disc = _Prof(20, 0, 25, 5, loops=1, area=25)
    sk = _sk(annulus, disc)
    assert features.profiles(sk) == [annulus, disc]
    assert features.profiles(sk, lambda p: p.profileLoops.count > 1) == [annulus]


# ---------------------------------------------------------------------------
# sketch.poly() length guard — must raise before any sketch call
# ---------------------------------------------------------------------------
def test_poly_rejects_fewer_than_three_points() -> None:
    s: Any = SimpleNamespace()  # no modelToSketchSpace: guard has to fire first
    with pytest.raises(ValueError, match=">=3 points"):
        poly(s, [(0.0, 0.0), (10.0, 0.0)])


# ---------------------------------------------------------------------------
# expr.as_str unwrap, shared by _vi / sketch
# ---------------------------------------------------------------------------
def test_as_str_passthrough_and_ex() -> None:
    assert as_str("plate_length") == "plate_length"
    assert as_str(Ex("(a + b)", 3.0, False)) == "(a + b)"


def test_vi_unwraps_ex_to_expression() -> None:
    _CORE.ValueInput.createByString.reset_mock()
    _vi(Ex("(a + b)", 3.0, False))
    assert _CORE.ValueInput.createByString.call_args.args == ("(a + b)",)


# ---------------------------------------------------------------------------
# fillet / chamfer tangent_chain pass-through (default off)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("flag", [False, True])
def test_fillet_passes_tangent_chain(flag: bool) -> None:
    comp: Any = MagicMock()
    edges: list[Any] = [MagicMock()]
    fillet(comp, edges, "1 mm", tangent_chain=flag, label="t_fillet")
    add_set = comp.features.filletFeatures.createInput.return_value.edgeSetInputs.addConstantRadiusEdgeSet
    assert add_set.call_args.args[2] is flag


@pytest.mark.parametrize("flag", [False, True])
def test_chamfer_passes_tangent_chain(flag: bool) -> None:
    comp: Any = MagicMock()
    edges: list[Any] = [MagicMock()]
    chamfer(comp, edges, "1 mm", tangent_chain=flag, label="t_chamfer")
    add_set = comp.features.chamferFeatures.createInput2.return_value.chamferEdgeSets.addEqualDistanceChamferEdgeSet
    assert add_set.call_args.args[2] is flag


def test_chamfer_without_distance2_uses_equal_set() -> None:
    comp: Any = MagicMock()
    chamfer(comp, [MagicMock()], "1 mm", label="t_chamfer")
    sets = comp.features.chamferFeatures.createInput2.return_value.chamferEdgeSets
    sets.addTwoDistancesChamferEdgeSet.assert_not_called()
    sets.addEqualDistanceChamferEdgeSet.assert_called_once()


@pytest.mark.parametrize("flag", [False, True])
def test_chamfer_distance2_uses_two_distance_set(flag: bool) -> None:
    comp: Any = MagicMock()
    chamfer(
        comp,
        [MagicMock()],
        "1 mm",
        tangent_chain=flag,
        distance2="2 mm",
        label="t_chamfer",
    )
    sets = comp.features.chamferFeatures.createInput2.return_value.chamferEdgeSets
    sets.addEqualDistanceChamferEdgeSet.assert_not_called()
    add_two = sets.addTwoDistancesChamferEdgeSet
    add_two.assert_called_once()
    assert add_two.call_args.args[3] is False  # isFlipped
    assert add_two.call_args.args[4] is flag  # isTangentChain
    _CORE.ValueInput.createByString.assert_any_call("1 mm")
    _CORE.ValueInput.createByString.assert_any_call("2 mm")


def test_mirror_passes_collection_and_plane_then_names_feature() -> None:
    comp: Any = MagicMock()
    feats: list[Any] = [MagicMock(name="f1"), MagicMock(name="f2")]
    plane: Any = MagicMock(name="plane")
    feat = mirror(comp, feats, plane, label="t_mirror")
    mf = comp.features.mirrorFeatures
    create_args = mf.createInput.call_args.args
    assert create_args[0] is _CORE.ObjectCollection.create.return_value
    assert create_args[1] is plane
    mf.add.assert_called_once_with(mf.createInput.return_value)
    assert feat is mf.add.return_value
    assert feat.name == "t_mirror"


def test_mirror_defaults_to_optimized_compute() -> None:
    assert enums.OPTIMIZED_PATTERN != enums.ADJUST_PATTERN  # default check must not pass by equal values
    comp: Any = MagicMock()
    mirror(comp, [MagicMock()], MagicMock(), label="t_mirror")
    inp = comp.features.mirrorFeatures.createInput.return_value
    assert inp.patternComputeOption is enums.OPTIMIZED_PATTERN


def test_mirror_compute_override_sets_given_option() -> None:
    comp: Any = MagicMock()
    mirror(
        comp,
        [MagicMock()],
        MagicMock(),
        label="t_mirror",
        compute=enums.IDENTICAL_PATTERN,
    )
    inp = comp.features.mirrorFeatures.createInput.return_value
    assert inp.patternComputeOption is enums.IDENTICAL_PATTERN


# ---------------------------------------------------------------------------
# extrude(): fake ExtrudeFeatureInput + comp (options: sides, symmetric, start offset)
# ---------------------------------------------------------------------------
class _FakeExtInput:
    def __init__(self) -> None:
        self.participantBodies: list[object] | None = None
        self.startExtent: object | None = None
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def setOneSideExtent(self, extent: object, direction: object) -> bool:
        self.calls.append(("one", (extent, direction)))
        return True

    def setSymmetricExtent(self, distance: object, full_length: bool) -> bool:
        self.calls.append(("sym", (distance, full_length)))
        return True


def _attrs_stub() -> SimpleNamespace:
    """Fusion entity .attributes: nothing stored, add() is a no-op. set_label -> _tag_source needs it."""
    return SimpleNamespace(itemByName=lambda g, n: None, add=lambda g, n, v: None)


class _FakeExtrudes:
    def __init__(self, inp: _FakeExtInput) -> None:
        self.inp = inp
        self.op: object = None

    def createInput(self, coll: object, op: object) -> _FakeExtInput:
        self.op = op
        return self.inp

    def add(self, inp: object) -> SimpleNamespace:
        return SimpleNamespace(inp=inp, attributes=_attrs_stub())  # set_label assigns .name and reads .attributes


def _comp(inp: _FakeExtInput) -> tuple[Any, _FakeExtrudes]:
    ext = _FakeExtrudes(inp)
    comp = SimpleNamespace(features=SimpleNamespace(extrudeFeatures=ext), bRepBodies=["bodyA", "bodyB"])
    return comp, ext


def test_extrude_default_is_one_side_no_offset() -> None:
    inp = _FakeExtInput()
    comp, ext = _comp(inp)
    extrude(comp, MagicMock(name="prof"), "10 mm", label="t_extrude")
    assert ext.op is enums.OP_NEW
    assert inp.participantBodies is None
    assert inp.startExtent is None
    assert [c[0] for c in inp.calls] == ["one"]


def test_extrude_cut_sets_participant_bodies_of_component() -> None:
    inp = _FakeExtInput()
    comp, ext = _comp(inp)
    extrude(comp, MagicMock(name="prof"), "10 mm", op="cut", label="t_extrude")
    assert ext.op is enums.OP_CUT
    assert inp.participantBodies == ["bodyA", "bodyB"]


def test_op_map_uses_typed_enum_constants() -> None:
    assert _OPS == {
        "new": enums.OP_NEW,
        "join": enums.OP_JOIN,
        "cut": enums.OP_CUT,
        "intersect": enums.OP_INTERSECT,
    }
    assert enums.OP_NEW is _FUS.FeatureOperations.NewBodyFeatureOperation
    assert enums.OP_INTERSECT is _FUS.FeatureOperations.IntersectFeatureOperation


def test_extrude_start_offset_sets_from_entity_start() -> None:
    inp = _FakeExtInput()
    comp, _ = _comp(inp)
    _FUS.OffsetStartDefinition.create.reset_mock()
    extrude(comp, MagicMock(name="prof"), "11 mm", start_offset="-2 mm", label="t_extrude")
    _FUS.OffsetStartDefinition.create.assert_called_once()
    _CORE.ValueInput.createByString.assert_any_call("-2 mm")
    assert inp.startExtent is not None
    assert [c[0] for c in inp.calls] == ["one"]


def test_extrude_symmetric_full_length_default() -> None:
    inp = _FakeExtInput()
    comp, _ = _comp(inp)
    extrude(comp, MagicMock(name="prof"), "20 mm", symmetric=True, label="t_extrude")
    assert [c[0] for c in inp.calls] == ["sym"]
    assert inp.calls[0][1][1] is True
    assert inp.startExtent is None


def test_extrude_symmetric_half_length_passes_false() -> None:
    inp = _FakeExtInput()
    comp, _ = _comp(inp)
    extrude(comp, MagicMock(name="prof"), "20 mm", symmetric=True, full_length=False, label="t_extrude")
    assert inp.calls[0][1][1] is False


def test_extrude_symmetric_with_start_offset_raises() -> None:
    inp = _FakeExtInput()
    comp, _ = _comp(inp)
    with pytest.raises(ValueError, match="symmetric"):
        extrude(comp, MagicMock(name="prof"), "20 mm", start_offset="-1 mm", symmetric=True, label="t_extrude")
    assert inp.calls == []


def test_extrude_profile_list_passes_object_collection(
    object_collection: MagicMock,
) -> None:
    comp = _extrude_comp(_FakeExtInput(), [SimpleNamespace(name="r")])
    p1, p2 = MagicMock(name="p1"), MagicMock(name="p2")
    extrude(comp, [p1, p2], "10 mm", label="t_extrude")
    assert comp.features.extrudeFeatures.createInput.call_args.args[0] is object_collection
    assert object_collection.add.call_args_list == [call(p1), call(p2)]


def test_extrude_empty_profile_list_rejected() -> None:
    comp = _extrude_comp(_FakeExtInput(), [SimpleNamespace(name="r")])
    with pytest.raises(ValueError, match="profile"):
        extrude(comp, [], "10 mm", label="t_extrude")
    comp.features.extrudeFeatures.createInput.assert_not_called()


# ---------------------------------------------------------------------------
# extrude(bodies=, name=): comp whose add() returns a feature exposing bodies.item(0)
# ---------------------------------------------------------------------------
def _extrude_comp(inp: _FakeExtInput, bodies_out: list[Any]) -> Any:
    """comp whose extrude add() returns a feature exposing bodies.item(0) = bodies_out[0]."""
    ext = MagicMock(name="extrudeFeatures")
    ext.createInput.return_value = inp
    ext.add.return_value = SimpleNamespace(
        bodies=SimpleNamespace(count=len(bodies_out), item=lambda i: bodies_out[i]), attributes=_attrs_stub()
    )
    return SimpleNamespace(features=SimpleNamespace(extrudeFeatures=ext), bRepBodies=["A", "B"])


def test_extrude_cut_bodies_limits_participants() -> None:
    inp = _FakeExtInput()
    comp = _extrude_comp(inp, [SimpleNamespace(name="r")])
    extrude(comp, MagicMock(name="prof"), "10 mm", op="cut", bodies=["A"], label="t_extrude")  # type: ignore[arg-type]
    assert inp.participantBodies == ["A"]


def test_extrude_cut_without_bodies_uses_all_component_bodies() -> None:
    inp = _FakeExtInput()
    comp = _extrude_comp(inp, [SimpleNamespace(name="r")])
    extrude(comp, MagicMock(name="prof"), "10 mm", op="cut", label="t_extrude")
    assert inp.participantBodies == ["A", "B"]


def test_extrude_name_renames_first_result_body() -> None:
    inp = _FakeExtInput()
    body = SimpleNamespace(name="Body1")
    comp = _extrude_comp(inp, [body])
    extrude(comp, MagicMock(name="prof"), "10 mm", name="plate_A", label="t_extrude")
    assert body.name == "plate_A"


def test_extrude_without_name_keeps_fusion_name() -> None:
    inp = _FakeExtInput()
    body = SimpleNamespace(name="Body1")
    comp = _extrude_comp(inp, [body])
    extrude(comp, MagicMock(name="prof"), "10 mm", label="t_extrude")
    assert body.name == "Body1"


def test_extrude_name_rejected_for_join_cut_intersect() -> None:
    # Non-new ops: Fusion's first result body is the PARTICIPANT; renaming it would clobber the host body.
    inp = _FakeExtInput()
    body = SimpleNamespace(name="Body1")
    comp = _extrude_comp(inp, [body])
    with pytest.raises(ValueError, match="name"):
        extrude(comp, MagicMock(name="prof"), "10 mm", op="cut", name="plate_A", label="t_extrude")
    assert body.name == "Body1"
    comp.features.extrudeFeatures.add.assert_not_called()


def test_extrude_name_with_no_result_bodies_raises() -> None:
    inp = _FakeExtInput()
    comp = _extrude_comp(inp, [])
    with pytest.raises(RuntimeError, match="no result bodies"):
        extrude(comp, MagicMock(name="prof"), "10 mm", name="plate_A", label="t_extrude")


def test_extrude_empty_bodies_list_rejected() -> None:
    inp = _FakeExtInput()
    comp = _extrude_comp(inp, [SimpleNamespace(name="r")])
    with pytest.raises(ValueError, match="bodies"):
        extrude(comp, MagicMock(name="prof"), "10 mm", op="cut", bodies=[], label="t_extrude")
    comp.features.extrudeFeatures.createInput.assert_not_called()


# ---------------------------------------------------------------------------
# revolve(bodies=): same participant rule as extrude
# ---------------------------------------------------------------------------
class _FakeRevInput:
    def __init__(self) -> None:
        self.participantBodies: list[object] | None = None
        self.angle_calls: list[tuple[bool, object]] = []

    def setAngleExtent(self, is_symmetric: bool, angle: object) -> bool:
        self.angle_calls.append((is_symmetric, angle))
        return True


def _rev_comp(inp: _FakeRevInput) -> Any:
    rf = MagicMock(name="revolveFeatures")
    rf.createInput.return_value = inp
    rf.add.return_value = SimpleNamespace(name="Revolve1", attributes=_attrs_stub())
    return SimpleNamespace(features=SimpleNamespace(revolveFeatures=rf), bRepBodies=["A", "B"])


def test_revolve_cut_defaults_to_all_component_bodies() -> None:
    inp = _FakeRevInput()
    comp = _rev_comp(inp)
    features.revolve(comp, MagicMock(name="prof"), MagicMock(name="axis"), "cut", label="t_rev")
    assert inp.participantBodies == ["A", "B"]
    assert len(inp.angle_calls) == 1


def test_revolve_cut_bodies_limits_participants() -> None:
    inp = _FakeRevInput()
    comp = _rev_comp(inp)
    body_a = MagicMock(name="A")
    features.revolve(comp, MagicMock(name="prof"), MagicMock(name="axis"), "cut", bodies=[body_a], label="t_rev")
    assert inp.participantBodies == [body_a]


def test_revolve_new_ignores_bodies() -> None:
    inp = _FakeRevInput()
    comp = _rev_comp(inp)
    features.revolve(comp, MagicMock(name="prof"), MagicMock(name="axis"), "new", bodies=[MagicMock()], label="t_rev")
    assert inp.participantBodies is None


def test_revolve_empty_bodies_rejected_before_create_input() -> None:
    inp = _FakeRevInput()
    comp = _rev_comp(inp)
    with pytest.raises(ValueError, match="bodies"):
        features.revolve(comp, MagicMock(name="prof"), MagicMock(name="axis"), "cut", bodies=[], label="t_rev")
    comp.features.revolveFeatures.createInput.assert_not_called()


# ---------------------------------------------------------------------------
# select_edges(): fake topology, units cm. Faces own loops (isOuter, edges); an edge's faces
# are the faces whose loops contain it — same shape as the real BRepFace/BRepLoop API.
# ---------------------------------------------------------------------------
_ids = itertools.count(1)


class _Edge:
    def __init__(self, a: _Pt, b: _Pt) -> None:
        self.startVertex = SimpleNamespace(geometry=a)
        self.endVertex = SimpleNamespace(geometry=b)
        self.geometry: object = None  # Line3D stand-in not needed: no axis/circle_r filter in these tests
        self.tempId = next(_ids)
        self.boundingBox = _Box(
            _Pt(min(a.x, b.x), min(a.y, b.y), min(a.z, b.z)),
            _Pt(max(a.x, b.x), max(a.y, b.y), max(a.z, b.z)),
        )
        self.faces: list[SimpleNamespace] = []  # linked by _box_body from the face loops


def _face(maxz: float, *loops: tuple[bool, list[_Edge]]) -> SimpleNamespace:
    """Fake BRepFace: bbox maxZ and loops given as (isOuter, edges)."""
    return SimpleNamespace(
        boundingBox=SimpleNamespace(maxPoint=SimpleNamespace(z=maxz)),
        loops=[SimpleNamespace(isOuter=outer, edges=edges) for outer, edges in loops],
    )


def _rect_edges(x0: float, x1: float, y0: float, y1: float, z: float) -> list[_Edge]:
    """4 edges at height z; edge i runs corner i -> corner i+1, corners (x0,y0),(x1,y0),(x1,y1),(x0,y1)."""
    c = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return [_Edge(_Pt(*c[i], z), _Pt(*c[(i + 1) % 4], z)) for i in range(4)]


def _vert_edges(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> list[_Edge]:
    """4 vertical edges, one per rect corner, same corner order as _rect_edges."""
    return [_Edge(_Pt(x, y, z0), _Pt(x, y, z1)) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]


def _box_body(pocket: bool = False) -> Any:
    """Unit cube 0..1 cm (0..10 mm). pocket=True adds a blind pocket 0.25..0.75 cm, floor at z=0.5, in the top.

    Top face: outer loop = top rim; pocket rim = INNER loop of the top face (hole-like, not outer outline).
    """
    ob, ot, ov = (
        _rect_edges(0, 1, 0, 1, 0.0),
        _rect_edges(0, 1, 0, 1, 1.0),
        _vert_edges(0, 1, 0, 1, 0.0, 1.0),
    )
    top: list[tuple[bool, list[_Edge]]] = [(True, ot)]
    faces = [_face(0.0, (True, ob))]
    faces += [_face(1.0, (True, [ob[i], ov[(i + 1) % 4], ot[i], ov[i]])) for i in range(4)]
    edges = ob + ot + ov
    if pocket:
        pb = _rect_edges(0.25, 0.75, 0.25, 0.75, 0.5)
        pt = _rect_edges(0.25, 0.75, 0.25, 0.75, 1.0)
        pv = _vert_edges(0.25, 0.75, 0.25, 0.75, 0.5, 1.0)
        top.append((False, pt))
        faces.append(_face(0.5, (True, pb)))
        faces += [_face(1.0, (True, [pb[i], pv[(i + 1) % 4], pt[i], pv[i]])) for i in range(4)]
        edges += pb + pt + pv
    faces.insert(1, _face(1.0, *top))
    for e in edges:
        e.faces = [f for f in faces if any(x is e for lp in f.loops for x in lp.edges)]
    return SimpleNamespace(edges=edges, faces=faces)


def test_convex_top_returns_top_rim_only() -> None:
    """Regression: vertical edges (z 0..1, adjacent faces maxZ 1) used to pass convex_top=True -> 8 not 4."""
    got = select_edges(_box_body(), convex_top=True)
    assert len(got) == 4
    assert all(e.boundingBox.minPoint.z == 1.0 and e.boundingBox.maxPoint.z == 1.0 for e in got)


def test_convex_top_false_returns_bottom_rim_and_verticals() -> None:
    got = select_edges(_box_body(), convex_top=False)
    assert len(got) == 8
    assert not any(e.boundingBox.minPoint.z == 1.0 and e.boundingBox.maxPoint.z == 1.0 for e in got)


def test_convex_top_excludes_pocket_rim() -> None:
    """Regression: pocket rim at top z has adjacent faces maxZ 1 too -> passed as convex top (8 not 4).

    Outer top edges have minPoint.x in {0, 1}; pocket rim edges have minPoint.x in {0.25, 0.75}.
    """
    got = select_edges(_box_body(pocket=True), convex_top=True)
    assert len(got) == 4
    assert all(e.boundingBox.minPoint.x in (0.0, 1.0) for e in got)


def test_on_plane_z_selects_top_rim() -> None:
    got = select_edges(_box_body(), on_plane=("z", 10.0))
    assert len(got) == 4
    assert all(e.boundingBox.minPoint.z == 1.0 for e in got)


def test_on_plane_axis_is_case_insensitive() -> None:
    assert len(select_edges(_box_body(), on_plane=("X", 0.0))) == 4


def test_on_plane_no_match_returns_empty() -> None:
    assert select_edges(_box_body(), on_plane=("z", 5.0)) == []


def test_on_plane_bad_axis_raises() -> None:
    with pytest.raises(ValueError, match="axis"):
        select_edges(_box_body(), on_plane=("w", 0.0))


def test_on_plane_rejects_arc_with_endpoints_on_plane_but_bulging_off() -> None:
    """Regression: only end vertices were checked -> arc with both ends at z=0 bulging to z=0.5 passed."""
    arc = _Edge(_Pt(0, 0, 0), _Pt(1, 0, 0))
    arc.boundingBox = _Box(_Pt(0, 0, 0), _Pt(1, 0, 0.5))
    body: Any = SimpleNamespace(edges=[arc])
    assert select_edges(body, on_plane=("z", 0.0)) == []


def test_on_plane_keeps_flat_edge_on_plane() -> None:
    flat = _Edge(_Pt(0, 0, 0), _Pt(1, 0, 0))
    body: Any = SimpleNamespace(edges=[flat])
    assert select_edges(body, on_plane=("z", 0.0)) == [flat]


# ---------------------------------------------------------------------------
# select_edges(pred=, in_box_mm=): EdgeInfo in mm. adsk.core is a MagicMock offline, so isinstance(Circle3D)
# needs real classes: real_circle_types swaps Circle3D / Arc3D for _FakeCircle for one test.
# ---------------------------------------------------------------------------
class _FakeCircle:
    def __init__(self, centre: _Pt, radius: float, normal: tuple[float, float, float]) -> None:
        self.center = centre  # cm
        self.radius = radius  # cm
        self.normal = _Pt(*normal)


@pytest.fixture
def real_circle_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_CORE, "Circle3D", _FakeCircle, raising=False)
    monkeypatch.setattr(_CORE, "Arc3D", _FakeCircle, raising=False)


def _round_edge(centre: _Pt, radius_cm: float, normal: tuple[float, float, float]) -> Any:
    """Closed circle: no vertices (startVertex/endVertex None). bbox = centre +- r on every axis (approximate)."""
    box = _Box(
        _Pt(centre.x - radius_cm, centre.y - radius_cm, centre.z - radius_cm),
        _Pt(centre.x + radius_cm, centre.y + radius_cm, centre.z + radius_cm),
    )
    return SimpleNamespace(
        startVertex=None,
        endVertex=None,
        geometry=_FakeCircle(centre, radius_cm, normal),
        tempId=next(_ids),
        boundingBox=box,
        faces=[],
    )


def test_pred_none_is_default_behaviour() -> None:
    box = _box_body()
    assert len(select_edges(box, pred=None)) == 12
    assert select_edges(box, pred=None) == select_edges(box)


def test_pred_filters_edges_by_bbox_mm(real_circle_types: None) -> None:
    """Without pred: 12 edges. pred ignored -> 12, so this fails."""
    got = select_edges(_box_body(), pred=lambda i: i.bbox_min_mm[2] == 10.0 and i.bbox_max_mm[2] == 10.0)
    assert len(got) == 4
    assert all(e.boundingBox.minPoint.z == 1.0 for e in got)


def test_pred_gets_vertices_in_mm(real_circle_types: None) -> None:
    """Fake vertices are cm: edge 0 runs (0,0,0)->(1,0,0) cm = (0,0,0)->(10,0,0) mm. Only it ends at (10,0,0) mm."""
    box = _box_body()
    assert select_edges(box, pred=lambda i: i.end_mm == (10.0, 0.0, 0.0)) == [box.edges[0]]


def test_pred_and_combined_with_on_plane(real_circle_types: None) -> None:
    box = _box_body()
    assert len(select_edges(box, on_plane=("z", 10.0))) == 4
    got = select_edges(box, on_plane=("z", 10.0), pred=lambda i: i.bbox_min_mm[0] == 0.0)
    assert len(got) == 3  # top rim minus the x = 10 mm edge


def test_pred_false_returns_empty(real_circle_types: None) -> None:
    assert select_edges(_box_body(), pred=lambda _i: False) == []


def test_in_box_mm_keeps_edges_fully_inside(real_circle_types: None) -> None:
    """Top rim bbox z = 10 mm inside [9.5, 10.5]; bottom rim (z 0) and verticals (z 0..10) are not. Ignored -> 12."""
    box = _box_body()
    assert len(select_edges(box)) == 12
    got = select_edges(box, in_box_mm=((0.0, 0.0, 9.5), (10.0, 10.0, 10.5)))
    assert len(got) == 4
    assert all(e.boundingBox.minPoint.z == 1.0 for e in got)


def test_pred_circle_x_normal_radius_and_centre(real_circle_types: None) -> None:
    hit = _round_edge(_Pt(0.0, 0.0, 0.5), 0.2, (1.0, 0.0, 0.0))  # centre z 5 mm, r 2 mm, normal X
    wrong_r = _round_edge(_Pt(0.0, 0.0, 0.5), 0.3, (1.0, 0.0, 0.0))  # r 3 mm
    wrong_n = _round_edge(_Pt(0.0, 0.0, 0.5), 0.2, (0.0, 0.0, 1.0))  # normal Z
    body: Any = SimpleNamespace(edges=[hit, wrong_r, wrong_n])

    def pin(i: Any) -> bool:
        c = i.circle
        return c is not None and c.parallel_to("x") and abs(c.radius_mm - 2.0) < 1e-6 and c.centre_mm[2] == 5.0

    assert select_edges(body, pred=pin) == [hit]
    assert select_edges(body, pred=lambda i: i.start_mm is None and i.end_mm is None) == [hit, wrong_r, wrong_n]


# ---------------------------------------------------------------------------
# combine()
# ---------------------------------------------------------------------------
def _combine_comp() -> tuple[Any, SimpleNamespace]:
    inp = SimpleNamespace(operation=None, isKeepToolBodies=None)
    cf = MagicMock(name="combineFeatures")
    cf.createInput.return_value = inp
    return SimpleNamespace(features=SimpleNamespace(combineFeatures=cf)), inp


def test_combine_cut_consumes_tool_by_default() -> None:
    comp, inp = _combine_comp()
    target, tool = MagicMock(name="target"), MagicMock(name="tool")
    combine(comp, target, [tool], op="cut", label="t_combine")
    assert comp.features.combineFeatures.createInput.call_args.args[0] is target
    assert inp.operation is enums.OP_CUT
    assert inp.isKeepToolBodies is False
    comp.features.combineFeatures.add.assert_called_once_with(inp)


def test_combine_keep_tools_and_intersect() -> None:
    comp, inp = _combine_comp()
    combine(
        comp,
        MagicMock(name="target"),
        [MagicMock(name="tool")],
        op="intersect",
        keep_tools=True,
        label="t_combine",
    )
    assert inp.operation is enums.OP_INTERSECT
    assert inp.isKeepToolBodies is True


# ---------------------------------------------------------------------------
# pattern_rectangular / pattern_circular: qty < 2 -> None, no feature
# ---------------------------------------------------------------------------
@pytest.fixture
def pattern_comp(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(features_pattern, "_count", lambda c, e: float(e))  # "1" -> 1.0
    return SimpleNamespace(
        features=SimpleNamespace(
            rectangularPatternFeatures=MagicMock(name="rect"),
            circularPatternFeatures=MagicMock(name="circ"),
        )
    )


def test_pattern_rectangular_qty_one_returns_none(pattern_comp: Any) -> None:
    got = pattern_rectangular(pattern_comp, [MagicMock()], MagicMock(), "1", "5 mm", label="t_pattern")
    assert got is None
    pattern_comp.features.rectangularPatternFeatures.createInput.assert_not_called()
    pattern_comp.features.rectangularPatternFeatures.add.assert_not_called()


def test_pattern_circular_qty_one_returns_none(pattern_comp: Any) -> None:
    got = pattern_circular(pattern_comp, [MagicMock()], MagicMock(), "1", label="t_pattern")
    assert got is None
    pattern_comp.features.circularPatternFeatures.add.assert_not_called()


def test_pattern_circular_takes_entities_kwarg(pattern_comp: Any) -> None:
    got = pattern_circular(pattern_comp, entities=[MagicMock()], axis=MagicMock(), qty="3", label="t_pattern")
    assert got is pattern_comp.features.circularPatternFeatures.add.return_value


def test_count_evaluates_in_comp_design() -> None:
    um = MagicMock(name="unitsManager")
    um.evaluateExpression.return_value = 4.0
    comp: Any = SimpleNamespace(parentDesign=SimpleNamespace(unitsManager=um))
    assert features._count(comp, "hole_count") == 4.0
    um.evaluateExpression.assert_called_once_with("hole_count", "")


def test_pattern_rectangular_always_adds_feature_at_qty_one(pattern_comp: Any) -> None:
    got = pattern_rectangular(pattern_comp, [MagicMock()], MagicMock(), "1", "5 mm", label="t_pattern", always=True)
    assert got is pattern_comp.features.rectangularPatternFeatures.add.return_value
    pattern_comp.features.rectangularPatternFeatures.add.assert_called_once()


def test_pattern_rectangular_always_default_false_keeps_none(pattern_comp: Any) -> None:
    got = pattern_rectangular(pattern_comp, [MagicMock()], MagicMock(), "1", "5 mm", label="t_pattern", always=False)
    assert got is None
    pattern_comp.features.rectangularPatternFeatures.add.assert_not_called()


def test_pattern_rectangular_qty_two_adds_feature(pattern_comp: Any) -> None:
    got = pattern_rectangular(pattern_comp, [MagicMock()], MagicMock(), "2", "5 mm", label="t_pattern")
    assert got is pattern_comp.features.rectangularPatternFeatures.add.return_value
    pattern_comp.features.rectangularPatternFeatures.add.assert_called_once()


def test_pattern_rectangular_defaults_to_optimized_and_overrides(
    pattern_comp: Any,
) -> None:
    pattern_rectangular(pattern_comp, [MagicMock()], MagicMock(), "2", "5 mm", label="t_pattern")
    inp = pattern_comp.features.rectangularPatternFeatures.createInput.return_value
    assert inp.patternComputeOption is enums.OPTIMIZED_PATTERN
    pattern_rectangular(
        pattern_comp,
        [MagicMock()],
        MagicMock(),
        "2",
        "5 mm",
        label="t_pattern",
        compute=enums.ADJUST_PATTERN,
    )
    assert inp.patternComputeOption is enums.ADJUST_PATTERN


def test_pattern_circular_defaults_to_optimized_and_overrides(
    pattern_comp: Any,
) -> None:
    pattern_circular(pattern_comp, [MagicMock()], MagicMock(), "3", label="t_pattern")
    inp = pattern_comp.features.circularPatternFeatures.createInput.return_value
    assert inp.patternComputeOption is enums.OPTIMIZED_PATTERN
    pattern_circular(
        pattern_comp,
        [MagicMock()],
        MagicMock(),
        "3",
        label="t_pattern",
        compute=enums.IDENTICAL_PATTERN,
    )
    assert inp.patternComputeOption is enums.IDENTICAL_PATTERN


def test_pattern_grid_defaults_to_optimized_and_overrides() -> None:
    comp: Any = MagicMock()
    pattern_grid(
        comp,
        [MagicMock()],
        MagicMock(),
        "2",
        "5 mm",
        MagicMock(),
        "3",
        "5 mm",
        label="t_grid",
    )
    inp = comp.features.rectangularPatternFeatures.createInput.return_value
    assert inp.patternComputeOption is enums.OPTIMIZED_PATTERN
    pattern_grid(
        comp,
        [MagicMock()],
        MagicMock(),
        "2",
        "5 mm",
        MagicMock(),
        "3",
        "5 mm",
        label="t_grid",
        compute=enums.ADJUST_PATTERN,
    )
    assert inp.patternComputeOption is enums.ADJUST_PATTERN


# ---------------------------------------------------------------------------
# collection(): python iterable -> ObjectCollection, in order
# ---------------------------------------------------------------------------
def test_collection_adds_items_in_order(object_collection: MagicMock) -> None:
    a, b = MagicMock(name="a"), MagicMock(name="b")
    assert collection([a, b]) is object_collection
    assert object_collection.add.call_args_list == [call(a), call(b)]


def test_collection_empty_creates_empty_collection(
    object_collection: MagicMock,
) -> None:
    assert collection([]) is object_collection
    object_collection.add.assert_not_called()


# ---------------------------------------------------------------------------
# face_normal(): evaluator-based, respects face orientation
# ---------------------------------------------------------------------------
def _normal_face(ok: bool = True) -> tuple[Any, object, MagicMock]:
    vec = object()
    ev = MagicMock(name="evaluator")
    ev.getNormalAtPoint.return_value = (ok, vec)
    point = object()
    return SimpleNamespace(pointOnFace=point, evaluator=ev), vec, ev


def test_face_normal_defaults_to_point_on_face() -> None:
    face, vec, ev = _normal_face()
    assert face_normal(face) is vec
    ev.getNormalAtPoint.assert_called_once_with(face.pointOnFace)


def test_face_normal_uses_given_point() -> None:
    face, vec, ev = _normal_face()
    p: Any = object()
    assert face_normal(face, p) is vec
    ev.getNormalAtPoint.assert_called_once_with(p)


def test_face_normal_raises_when_evaluator_fails() -> None:
    face, _, _ = _normal_face(ok=False)
    with pytest.raises(ValueError, match="normal"):
        face_normal(face)


# ---------------------------------------------------------------------------
# move_body / rotate_body / axis_y_through / rotate_body_about_y
# ---------------------------------------------------------------------------
def _move_comp(name: str = "sub_comp") -> tuple[Any, MagicMock, MagicMock]:
    inp = MagicMock(name="move_input")
    inp.defineAsTranslateXYZ.return_value = True
    inp.defineAsRotate.return_value = True
    mv = MagicMock(name="moveFeatures")
    mv.createInput2.return_value = inp
    root = SimpleNamespace(name="root")
    comp = SimpleNamespace(
        name=name,
        features=SimpleNamespace(moveFeatures=mv),
        parentDesign=SimpleNamespace(rootComponent=root),
    )
    return comp, inp, mv


def test_move_body_translates_with_expressions_in_design_space(
    object_collection: MagicMock,
) -> None:
    comp, inp, mv = _move_comp()
    _CORE.ValueInput.createByString.reset_mock()
    body = MagicMock(name="body")
    features.move_body(comp, body, dx="-outer_width/2 - 10 mm", label="t_move")
    assert mv.createInput2.call_args.args == (object_collection,)
    object_collection.add.assert_called_once_with(body)
    assert inp.defineAsTranslateXYZ.call_args.args[3] is True
    assert call("-outer_width/2 - 10 mm") in _CORE.ValueInput.createByString.call_args_list
    assert call("0 mm") in _CORE.ValueInput.createByString.call_args_list  # dy / dz defaults
    mv.add.assert_called_once_with(inp)


def test_move_body_list_adds_bodies_in_order(object_collection: MagicMock) -> None:
    comp, _, _ = _move_comp()
    a, b = MagicMock(name="a"), MagicMock(name="b")
    features.move_body(comp, [a, b], label="t_move")
    assert object_collection.add.call_args_list == [call(a), call(b)]


def test_move_body_names_feature_and_rejects_bad_label_before_add() -> None:
    comp, _inp, mv = _move_comp()
    feat = MagicMock(name="feat")
    mv.add.return_value = feat
    got = features.move_body(comp, MagicMock(name="body"), dz="5 mm", label="t_move")
    assert got is feat and feat.name == "t_move"
    comp2, _, mv2 = _move_comp()
    with pytest.raises(ValueError, match="snake_case"):
        features.move_body(comp2, MagicMock(name="body"), label="Bad-Name")
    mv2.add.assert_not_called()


def test_move_body_failed_define_raises_and_adds_nothing() -> None:
    comp, inp, mv = _move_comp()
    inp.defineAsTranslateXYZ.return_value = False
    with pytest.raises(RuntimeError, match="defineAsTranslateXYZ"):
        features.move_body(comp, MagicMock(name="body"), label="t_move")
    mv.add.assert_not_called()


def test_rotate_body_defines_rotate_about_given_axis() -> None:
    comp, inp, mv = _move_comp()
    axis = MagicMock(name="axis")
    features.rotate_body(comp, MagicMock(name="body"), axis=axis, angle="90 deg", label="t_rot")
    assert inp.defineAsRotate.call_args.args[0] is axis
    assert _CORE.ValueInput.createByString.call_args.args == ("90 deg",)
    mv.add.assert_called_once_with(inp)


def test_rotate_body_rejects_bad_label_before_add() -> None:
    comp, _, mv = _move_comp()
    with pytest.raises(ValueError, match="snake_case"):
        features.rotate_body(comp, MagicMock(name="body"), axis=MagicMock(), angle="1 deg", label="Rot")
    mv.add.assert_not_called()


def test_rotate_body_failed_define_raises() -> None:
    comp, inp, mv = _move_comp()
    inp.defineAsRotate.return_value = False
    with pytest.raises(RuntimeError, match="defineAsRotate"):
        features.rotate_body(comp, MagicMock(name="body"), axis=MagicMock(), angle="1 deg", label="t_rot")
    mv.add.assert_not_called()


def test_axis_y_through_intersects_two_planes_and_names_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, float, str]] = []

    def fake_plane(comp: Any, axis: str, expr: str, value_mm: float, *, label: str) -> str:
        calls.append((axis, expr, value_mm, label))
        return MagicMock(name=f"plane_{axis}")

    monkeypatch.setattr(features_axes, "axis_plane", fake_plane)
    inp = MagicMock(name="axis_input")
    inp.setByTwoPlanes.return_value = True
    axis = MagicMock(name="axis")
    ca = MagicMock(name="constructionAxes", count=0)
    ca.createInput.return_value = inp
    ca.add.return_value = axis
    comp: Any = SimpleNamespace(
        constructionAxes=ca,
        constructionPlanes=MagicMock(name="constructionPlanes", count=0),
    )
    x = Ex("louver_x", 12.5, False, "mm")
    z = Ex.mm(0.0)
    got = features.axis_y_through(comp, x=x, z=z, label="t_axis")
    assert got is axis and axis.name == "t_axis"
    assert calls == [
        ("x", "louver_x", 12.5, "t_axis_plane_x"),
        ("z", "0.0 mm", 0.0, "t_axis_plane_z"),
    ]
    inp.setByTwoPlanes.assert_called_once()
    pa, pb = inp.setByTwoPlanes.call_args.args
    assert pa.isLightBulbOn is False and pb.isLightBulbOn is False  # helper planes hidden
    assert axis.isLightBulbOn is False  # helper axis hidden
    ca.add.assert_called_once_with(inp)


def test_axis_y_through_str_expr_value_from_fusion_in_mm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, float, str]] = []

    def fake_plane(comp: Any, axis: str, expr: str, value_mm: float, *, label: str) -> str:
        calls.append((axis, expr, value_mm, label))
        return MagicMock(name=f"plane_{axis}")

    monkeypatch.setattr(features_axes, "axis_plane", fake_plane)
    inp = MagicMock(name="axis_input")
    inp.setByTwoPlanes.return_value = True
    ca = MagicMock(name="constructionAxes", count=0)
    ca.createInput.return_value = inp
    udm = MagicMock(name="unitsManager")
    # Fusion evaluateExpression returns internal cm: 12.5 mm -> 1.25, 0 mm -> 0.0
    udm.evaluateExpression.side_effect = lambda expr, units: {
        "front_louver_seed_x": 1.25,
        "0 mm": 0.0,
    }[expr]
    comp: Any = SimpleNamespace(
        constructionAxes=ca,
        constructionPlanes=MagicMock(name="constructionPlanes", count=0),
        parentDesign=SimpleNamespace(unitsManager=udm),
    )
    features.axis_y_through(comp, x="front_louver_seed_x", z="0 mm", label="t_axis")
    udm.evaluateExpression.assert_any_call("front_louver_seed_x", "mm")
    assert calls[0] == ("x", "front_louver_seed_x", 12.5, "t_axis_plane_x")
    assert calls[1] == ("z", "0 mm", 0.0, "t_axis_plane_z")


def test_axis_y_through_rejects_non_mm_and_bad_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plane = MagicMock(name="axis_plane")
    monkeypatch.setattr(features_axes, "axis_plane", plane)
    comp: Any = SimpleNamespace(constructionAxes=MagicMock(name="ca"))
    with pytest.raises(ValueError, match="expected mm"):
        features.axis_y_through(comp, x=Ex("a", 1.0, False, "deg"), z=Ex.mm(0.0), label="t_axis")
    with pytest.raises(ValueError, match="snake_case"):
        features.axis_y_through(comp, x=Ex.mm(0.0), z=Ex.mm(0.0), label="T")
    plane.assert_not_called()


@pytest.mark.parametrize(
    ("dir_y", "angle", "want"),
    [
        (
            -1.0,
            "90 deg",
            "-(90 deg)",
        ),  # Fusion's -Y axis: negate so positive = right-hand about +Y
        (1.0, "90 deg", "90 deg"),
    ],
)
def test_rotate_body_about_y_builds_axis_then_rotates(
    monkeypatch: pytest.MonkeyPatch, dir_y: float, angle: str, want: str
) -> None:
    axis_sentinel = MagicMock(name="y_axis")
    axis_sentinel.geometry.direction.y = dir_y
    seen: list[tuple[str, str]] = []

    def fake_axis(comp: Any, *, x: Ex, z: Ex, label: str) -> Any:
        seen.append(("axis", label))
        return axis_sentinel

    monkeypatch.setattr(features_axes, "axis_y_through", fake_axis)
    comp, inp, mv = _move_comp()
    _CORE.ValueInput.createByString.reset_mock()
    features.rotate_body_about_y(
        comp,
        MagicMock(name="body"),
        x=Ex.mm(0.0),
        z=Ex.mm(0.0),
        angle=angle,
        label="t_rot",
    )
    assert seen == [("axis", "sub_comp__t_rot_axis")]
    assert inp.defineAsRotate.call_args.args[0] is axis_sentinel
    assert _CORE.ValueInput.createByString.call_args.args == (want,)
    mv.add.assert_called_once_with(inp)


def test_rotate_body_about_y_negates_ex_angle_on_minus_y(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    axis_sentinel = MagicMock(name="y_axis")
    axis_sentinel.geometry.direction.y = -1.0
    monkeypatch.setattr(features_axes, "axis_y_through", lambda comp, *, x, z, label: axis_sentinel)
    comp, _, _ = _move_comp()
    _CORE.ValueInput.createByString.reset_mock()
    features.rotate_body_about_y(
        comp,
        MagicMock(name="body"),
        x=Ex.mm(0.0),
        z=Ex.mm(0.0),
        angle=Ex("louver_angle", 0.5, False, "deg"),
        label="t_rot",
    )
    assert _CORE.ValueInput.createByString.call_args.args == ("-(louver_angle)",)


def test_rotate_body_about_y_axis_in_root_move_in_sub_comp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Live 2026-10-21: axis of the SAME non-root comp as the move -> "3 : Invalid entity".
    # Axis must be built in root; the move feature stays in the body's comp.
    axis_sentinel = MagicMock(name="y_axis")
    axis_sentinel.geometry.direction.y = 1.0
    axis_comps: list[Any] = []

    def fake_axis(comp: Any, *, x: Ex, z: Ex, label: str) -> Any:
        axis_comps.append(comp)
        return axis_sentinel

    monkeypatch.setattr(features_axes, "axis_y_through", fake_axis)
    sub, inp, mv = _move_comp()
    features.rotate_body_about_y(
        sub,
        MagicMock(name="body"),
        x=Ex.mm(0.0),
        z=Ex.mm(0.0),
        angle="90 deg",
        label="t_rot",
    )
    assert axis_comps == [sub.parentDesign.rootComponent]
    assert axis_comps[0] is not sub
    assert inp.defineAsRotate.call_args.args[0] is axis_sentinel
    mv.add.assert_called_once_with(inp)  # move lives in sub comp (body's comp)


class _Named:
    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes = _attrs_stub()
        self.deleted = False

    def deleteMe(self) -> None:
        self.deleted = True


class _NamedColl:
    def __init__(self, *items: _Named) -> None:
        self._items = list(items)

    @property
    def count(self) -> int:
        return len(self._items)

    def item(self, i: int) -> _Named:
        return self._items[i]


def test_axis_y_through_second_call_deletes_previous_constructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Rebuild: previous call's root constructions (incl. Fusion " (n)" duplicates) go; unrelated names stay.
    monkeypatch.setattr(
        features_axes,
        "axis_plane",
        lambda comp, axis, expr, value_mm, *, label: _Named(label),
    )
    old = [
        _Named("t_axis"),
        _Named("t_axis_plane_x"),
        _Named("t_axis_plane_z"),
        _Named("t_axis (1)"),
        _Named("t_axis_plane_x (1)"),
        _Named("t_axis_plane_z (1)"),
        _Named("t_axis_extra"),
        _Named("other_axis"),
    ]
    axes: Any = _NamedColl(*old[:4], old[6], old[7])
    planes = _NamedColl(old[4], old[5])
    new_axis = _Named("new")
    inp = MagicMock(name="axis_input")
    inp.setByTwoPlanes.return_value = True
    axes.createInput = MagicMock(return_value=inp)
    axes.add = MagicMock(return_value=new_axis)
    comp: Any = SimpleNamespace(constructionAxes=axes, constructionPlanes=planes)
    got = features.axis_y_through(comp, x=Ex.mm(1.0), z=Ex.mm(0.0), label="t_axis")
    assert got is new_axis and new_axis.name == "t_axis"
    deleted = {o.name for o in old if o.deleted}
    assert deleted == {
        "t_axis",
        "t_axis (1)",
        "t_axis_plane_x",
        "t_axis_plane_z",
        "t_axis_plane_x (1)",
        "t_axis_plane_z (1)",
    }
    assert not old[6].deleted and not old[7].deleted  # t_axis_extra, other_axis untouched


@pytest.mark.parametrize("name", ["t_axis", "t_axis (1)", "t_axis_plane_x", "t_axis_plane_z (2)"])
def test_owned_by_accepts_own_constructions(name: str) -> None:
    assert features._owned_by(name, "t_axis")


@pytest.mark.parametrize(
    "name",
    [
        "t_axis_extra",
        "t_axis_plane_w",
        "t_axis_plane_x_extra",
        "t_axis_plane_x (1) x",
        "t_axis_plane_xy",
        "other_axis",
    ],
)
def test_owned_by_rejects_foreign_names(name: str) -> None:
    assert not features._owned_by(name, "t_axis")


def test_owned_by_keeps_user_plane_with_same_prefix() -> None:
    # Old prefix match (startswith 'label_plane_') claimed user plane 'roof_plane_trim' for label 'roof' -> deleted on rebuild.
    assert not features._owned_by("roof_plane_trim", "roof")


def test_rotate_body_about_y_root_axis_label_scoped_by_comp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same rotate label in two comps: root helpers must differ, else the 2nd call deletes the 1st comp's axis.
    labels: list[str] = []
    axis_sentinel = MagicMock(name="y_axis")
    axis_sentinel.geometry.direction.y = 1.0

    def fake_axis(comp: Any, *, x: Ex, z: Ex, label: str) -> Any:
        labels.append(label)
        return axis_sentinel

    monkeypatch.setattr(features_axes, "axis_y_through", fake_axis)
    for name in ("comp_a", "Body 1"):
        comp, _, _ = _move_comp(name)
        features.rotate_body_about_y(
            comp,
            MagicMock(name="body"),
            x=Ex.mm(0.0),
            z=Ex.mm(0.0),
            angle="90 deg",
            label="t_rot",
        )
    assert labels == ["comp_a__t_rot_axis", "body_1__t_rot_axis"]


def test_rotate_body_about_x_root_axis_label_scoped_by_comp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    labels: list[str] = []
    axis_sentinel = MagicMock(name="x_axis")
    axis_sentinel.geometry.direction.x = 1.0

    def fake_axis(comp: Any, *, y: Ex, z: Ex, label: str) -> Any:
        labels.append(label)
        return axis_sentinel

    monkeypatch.setattr(features_axes, "axis_x_through", fake_axis)
    for name in ("comp_a", "comp_b"):
        comp, _, _ = _move_comp(name)
        features.rotate_body_about_x(
            comp,
            MagicMock(name="body"),
            y=Ex.mm(0.0),
            z=Ex.mm(0.0),
            angle="90 deg",
            label="t_rot",
        )
    assert labels == ["comp_a__t_rot_axis", "comp_b__t_rot_axis"]


def test_delete_previous_scoped_label_leaves_other_comp_root_axis() -> None:
    a_ax, b_ax = _Named("comp_a__t_rot_axis"), _Named("comp_b__t_rot_axis")
    a_pl, b_pl = (
        _Named("comp_a__t_rot_axis_plane_x"),
        _Named("comp_b__t_rot_axis_plane_x"),
    )
    root: Any = SimpleNamespace(
        constructionAxes=_NamedColl(a_ax, b_ax),
        constructionPlanes=_NamedColl(a_pl, b_pl),
    )
    features._delete_previous(root, "comp_a__t_rot_axis")
    assert a_ax.deleted and a_pl.deleted
    assert not b_ax.deleted and not b_pl.deleted


def test_axis_x_through_intersects_y_z_planes_and_names_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, float, str]] = []

    def fake_plane(comp: Any, axis: str, expr: str, value_mm: float, *, label: str) -> str:
        calls.append((axis, expr, value_mm, label))
        return MagicMock(name=f"plane_{axis}")

    monkeypatch.setattr(features_axes, "axis_plane", fake_plane)
    inp = MagicMock(name="axis_input")
    inp.setByTwoPlanes.return_value = True
    axis = MagicMock(name="axis")
    ca = MagicMock(name="constructionAxes", count=0)
    ca.createInput.return_value = inp
    ca.add.return_value = axis
    comp: Any = SimpleNamespace(
        constructionAxes=ca,
        constructionPlanes=MagicMock(name="constructionPlanes", count=0),
    )
    got = features.axis_x_through(comp, y=Ex("pin_y", 3.0, False, "mm"), z=Ex.mm(0.0), label="t_axis")
    assert got is axis and axis.name == "t_axis"
    assert calls == [
        ("y", "pin_y", 3.0, "t_axis_plane_y"),
        ("z", "0.0 mm", 0.0, "t_axis_plane_z"),
    ]
    inp.setByTwoPlanes.assert_called_once()
    pa, pb = inp.setByTwoPlanes.call_args.args
    assert pa.isLightBulbOn is False and pb.isLightBulbOn is False  # helper planes hidden
    assert axis.isLightBulbOn is False  # helper axis hidden
    ca.add.assert_called_once_with(inp)


def test_axis_x_through_rejects_non_mm_and_bad_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plane = MagicMock(name="axis_plane")
    monkeypatch.setattr(features_axes, "axis_plane", plane)
    comp: Any = SimpleNamespace(constructionAxes=MagicMock(name="ca"))
    with pytest.raises(ValueError, match="expected mm"):
        features.axis_x_through(comp, y=Ex("a", 1.0, False, "deg"), z=Ex.mm(0.0), label="t_axis")
    with pytest.raises(ValueError, match="snake_case"):
        features.axis_x_through(comp, y=Ex.mm(0.0), z=Ex.mm(0.0), label="T")
    plane.assert_not_called()


@pytest.mark.parametrize(
    ("dir_x", "angle", "want"),
    [
        (
            -1.0,
            "90 deg",
            "-(90 deg)",
        ),  # Fusion's -X axis: negate so positive = right-hand about +X
        (1.0, "90 deg", "90 deg"),
    ],
)
def test_rotate_body_about_x_builds_axis_then_rotates(
    monkeypatch: pytest.MonkeyPatch, dir_x: float, angle: str, want: str
) -> None:
    axis_sentinel = MagicMock(name="x_axis")
    axis_sentinel.geometry.direction.x = dir_x
    seen: list[tuple[str, str]] = []

    def fake_axis(comp: Any, *, y: Ex, z: Ex, label: str) -> Any:
        seen.append(("axis", label))
        return axis_sentinel

    monkeypatch.setattr(features_axes, "axis_x_through", fake_axis)
    comp, inp, mv = _move_comp()
    _CORE.ValueInput.createByString.reset_mock()
    features.rotate_body_about_x(
        comp,
        MagicMock(name="body"),
        y=Ex.mm(0.0),
        z=Ex.mm(0.0),
        angle=angle,
        label="t_rot",
    )
    assert seen == [("axis", "sub_comp__t_rot_axis")]
    assert inp.defineAsRotate.call_args.args[0] is axis_sentinel
    assert _CORE.ValueInput.createByString.call_args.args == (want,)
    mv.add.assert_called_once_with(inp)


def test_rotate_body_about_x_negates_ex_angle_on_minus_x(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    axis_sentinel = MagicMock(name="x_axis")
    axis_sentinel.geometry.direction.x = -1.0
    monkeypatch.setattr(features_axes, "axis_x_through", lambda comp, *, y, z, label: axis_sentinel)
    comp, _, _ = _move_comp()
    _CORE.ValueInput.createByString.reset_mock()
    features.rotate_body_about_x(
        comp,
        MagicMock(name="body"),
        y=Ex.mm(0.0),
        z=Ex.mm(0.0),
        angle=Ex("louver_angle", 0.5, False, "deg"),
        label="t_rot",
    )
    assert _CORE.ValueInput.createByString.call_args.args == ("-(louver_angle)",)


def test_rotate_body_about_x_axis_in_root_move_in_sub_comp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same live rule as rotate_body_about_y: axis in root, move in body's comp.
    axis_sentinel = MagicMock(name="x_axis")
    axis_sentinel.geometry.direction.x = 1.0
    axis_comps: list[Any] = []

    def fake_axis(comp: Any, *, y: Ex, z: Ex, label: str) -> Any:
        axis_comps.append(comp)
        return axis_sentinel

    monkeypatch.setattr(features_axes, "axis_x_through", fake_axis)
    sub, inp, mv = _move_comp()
    features.rotate_body_about_x(
        sub,
        MagicMock(name="body"),
        y=Ex.mm(0.0),
        z=Ex.mm(0.0),
        angle="90 deg",
        label="t_rot",
    )
    assert axis_comps == [sub.parentDesign.rootComponent]
    assert axis_comps[0] is not sub
    assert inp.defineAsRotate.call_args.args[0] is axis_sentinel
    mv.add.assert_called_once_with(inp)
