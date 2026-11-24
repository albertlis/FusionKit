"""Offline tests for fusion_kit.holes.cut_screw_slot (adsk stubs from conftest, no Fusion)."""

import math
import sys
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call

import pytest

import fusion_kit.sketch as fk_sketch
from fusion_kit import dfam, holes
from fusion_kit.enums import NEGATIVE_EXTENT, POSITIVE_EXTENT
from fusion_kit.expr import Ex, evaluate

_CORE: Any = sys.modules["adsk.core"]


class _Rec:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> MagicMock:
        self.calls.append((args, kwargs))
        return MagicMock()  # new_sketch needs a sketch-like return value


def _edge(lo_mm: tuple[float, float, float], hi_mm: tuple[float, float, float]) -> Any:
    def pt(p: tuple[float, float, float]) -> Any:
        return SimpleNamespace(x=p[0] / 10, y=p[1] / 10, z=p[2] / 10)

    return SimpleNamespace(
        boundingBox=SimpleNamespace(minPoint=pt(lo_mm), maxPoint=pt(hi_mm)),
        geometry=None,
        faces=[],
    )


@pytest.fixture
def recs(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    r = SimpleNamespace(extrude=_Rec(), new_sketch=_Rec(), anchor=_Rec(), profile=_Rec())
    monkeypatch.setattr(holes, "extrude", r.extrude)
    monkeypatch.setattr(holes, "new_sketch", r.new_sketch)
    monkeypatch.setattr(holes, "anchor", r.anchor)
    monkeypatch.setattr(holes, "slot_profile", r.profile)
    return r


@pytest.fixture
def sketch() -> MagicMock:
    s = MagicMock(name="sketch")
    s.modelToSketchSpace.side_effect = lambda p: p  # identity: sketch frame == model frame in tests
    s.profiles = [MagicMock(name="prof")]
    return s


def _body(*edges: Any) -> Any:  # fake BRepBody; Any so basedpyright accepts it for the typed body arg
    return SimpleNamespace(edges=list(edges))


def _cs_plane() -> MagicMock:
    return MagicMock(name="cs_plane")


def test_zero_play_cuts_round_hole(recs: SimpleNamespace, sketch: MagicMock, point3d_xyz: None) -> None:
    body = _body(_edge((-2, -2, 0), (2, 2, 0)))  # hole rim on entry plane z=0
    holes.cut_screw_slot(
        MagicMock(name="comp"),
        sketch,
        (0, 0, 0),
        "x",
        4.0,
        0.0,
        8.0,
        body,
        ("z", 0.0),
        depth_mm=4.5,
        cs_plane=_cs_plane(),
        label="t_slot",
    )
    assert sketch.addCenterPointSlot.call_count == 0
    assert sketch.sketchCurves.sketchCircles.addByCenterRadius.call_count == 1
    assert sketch.sketchCurves.sketchCircles.addByCenterRadius.call_args[0][1] == pytest.approx(0.2)
    assert len(recs.anchor.calls) == 2  # main slot + countersink (both play=0 circles)
    # first extrude: main slot cut
    _, ext_kw = recs.extrude.calls[0]
    assert ext_kw["op"] == "cut" and ext_kw["bodies"] == [body] and ext_kw["symmetric"] is False
    # second extrude: conical countersink, depth = -(head_d - d)/2 / tan(46 deg), tapered
    _, cs_kw = recs.extrude.calls[1]
    assert cs_kw["op"] == "cut"
    assert cs_kw["taper"] == "-46.0 deg"
    cs_dist = recs.extrude.calls[1][0][2]
    assert isinstance(cs_dist, Ex)
    assert cs_dist.v == pytest.approx(-2.0 / math.tan(math.radians(46.0)))


def test_face_cut_uses_outer_false_and_into_body_depth(
    recs: SimpleNamespace, sketch: MagicMock, point3d_xyz: None
) -> None:
    body = _body(_edge((-2, -2, 0), (2, 2, 0)))
    holes.cut_screw_slot(
        MagicMock(name="comp"),
        sketch,
        (0, 0, 0),
        "x",
        4.0,
        0.0,
        8.0,
        body,
        ("z", 0.0),
        depth_mm=4.5,
        cs_plane=_cs_plane(),
        label="t_slot",
    )
    # profile: picked by area for this slot/hole (face sketch also holds diamond profiles)
    assert len(recs.profile.calls) >= 1
    prof_args, prof_kw = recs.profile.calls[0]
    assert prof_args == (sketch, 4.0, 0.0)
    assert prof_kw == {}
    # first extrude: one-sided, negative distance == -depth (into body), symmetric False
    ext_args, ext_kw = recs.extrude.calls[0]
    dist = ext_args[2]
    assert isinstance(dist, Ex)
    assert dist.v == pytest.approx(-4.5)
    assert dist.e == "-4.5 mm"
    assert ext_kw["symmetric"] is False


def test_positive_play_cuts_slot_not_circle(recs: SimpleNamespace, sketch: MagicMock, point3d_xyz: None) -> None:
    body = _body(_edge((-2.5, -2, 0), (5, 2, 0)))
    holes.cut_screw_slot(
        MagicMock(name="comp"),
        sketch,
        (0, 0, 0),
        "x",
        4.0,
        5.0,
        8.0,
        body,
        ("z", 0.0),
        depth_mm=4.5,
        cs_plane=_cs_plane(),
        label="t_slot",
    )
    assert sketch.sketchCurves.sketchCircles.addByCenterRadius.call_count == 0
    assert sketch.addCenterPointSlot.call_count == 1
    end_pt = sketch.addCenterPointSlot.call_args[0][1]
    assert end_pt.x == pytest.approx(0.5)  # tip at +5 mm along x, cm
    assert end_pt.y == pytest.approx(0.0) and end_pt.z == pytest.approx(0.0)
    assert sketch.addCenterPointSlot.call_args[0][3] is True  # createWidthDimension
    # countersink extrude is the second call
    assert len(recs.extrude.calls) == 2


def test_countersink_edges_filtered_to_reach(recs: SimpleNamespace, sketch: MagicMock, point3d_xyz: None) -> None:
    near = _edge((-2, -2, 0), (2, 2, 0))  # within reach play 0 + d/2 2 + epsilon
    far = _edge((20, 20, 0), (30, 30, 0))  # same plane, outside reach
    off_plane = _edge((-2, -2, 5), (2, 2, 5))  # other plane z=5
    body = _body(near, far, off_plane)
    # near is in reach so no error raised; far and off_plane filtered out
    holes.cut_screw_slot(
        MagicMock(name="comp"),
        sketch,
        (0, 0, 0),
        "x",
        4.0,
        0.0,
        8.0,
        body,
        ("z", 0.0),
        depth_mm=4.5,
        cs_plane=_cs_plane(),
        label="t_slot",
    )
    # countersink extrude ran (edges not empty)
    assert len(recs.extrude.calls) == 2


def test_no_entry_edges_raises(recs: SimpleNamespace, sketch: MagicMock, point3d_xyz: None) -> None:
    with pytest.raises(RuntimeError, match="no entry edges"):
        holes.cut_screw_slot(
            MagicMock(name="comp"),
            sketch,
            (0, 0, 0),
            "x",
            4.0,
            0.0,
            8.0,
            _body(),
            ("z", 0.0),
            depth_mm=4.5,
            cs_plane=_cs_plane(),
            label="t_slot",
        )
    # countersink extrude not called — raised before it
    assert len(recs.extrude.calls) == 1


def test_small_play_slot_accepted(recs: SimpleNamespace, sketch: MagicMock, point3d_xyz: None) -> None:
    # play is centre travel, not slot length: play 1 < d 4.2 is valid (overall length 2*1 + 4.2)
    body = _body(_edge((-3, -3, 0), (3, 3, 0)))  # within reach play 1 + d/2 2.1 + epsilon
    holes.cut_screw_slot(
        MagicMock(name="comp"),
        sketch,
        (0, 0, 0),
        "x",
        4.2,
        1.0,
        8.0,
        body,
        ("z", 0.0),
        depth_mm=4.5,
        cs_plane=_cs_plane(),
        label="t_slot",
    )
    assert sketch.addCenterPointSlot.call_count == 1
    end_pt = sketch.addCenterPointSlot.call_args[0][1]
    assert end_pt.x == pytest.approx(0.1)  # endpoint offset from centre = play 1 mm, cm
    # countersink extrude ran
    assert len(recs.extrude.calls) == 2


def test_head_not_larger_than_d_rejected(recs: SimpleNamespace, sketch: MagicMock) -> None:
    with pytest.raises(ValueError, match="must exceed d"):
        holes.cut_screw_slot(
            MagicMock(name="comp"),
            sketch,
            (0, 0, 0),
            "x",
            4.0,
            0.0,
            4.0,
            _body(),
            ("z", 0.0),
            depth_mm=4.5,
            cs_plane=_cs_plane(),
            label="t_slot",
        )
    assert sketch.sketchCurves.sketchCircles.addByCenterRadius.call_count == 0


@pytest.mark.parametrize("depth", [0.0, -1.0])
def test_non_positive_depth_rejected(recs: SimpleNamespace, sketch: MagicMock, depth: float) -> None:
    with pytest.raises(ValueError, match="depth must be > 0"):
        holes.cut_screw_slot(
            MagicMock(name="comp"),
            sketch,
            (0, 0, 0),
            "x",
            4.0,
            0.0,
            8.0,
            _body(),
            ("z", 0.0),
            depth_mm=depth,
            cs_plane=_cs_plane(),
            label="t_slot",
        )
    assert recs.extrude.calls == []


def test_cs_plane_omitted_raises_type_error(sketch: MagicMock) -> None:
    """cs_plane is required keyword-only: omitting it is a call-binding TypeError."""
    kwargs: dict[str, Any] = {"depth_mm": 4.5, "label": "t_slot"}  # cs_plane deliberately absent
    with pytest.raises(TypeError, match="cs_plane"):
        holes.cut_screw_slot(
            MagicMock(name="comp"),
            sketch,
            (0, 0, 0),
            "x",
            4.0,
            0.0,
            8.0,
            _body(),
            ("z", 0.0),
            **kwargs,
        )


def test_cs_plane_explicit_none_raises(sketch: MagicMock) -> None:
    none_plane: Any = None  # untyped None on purpose: runtime guard, not static type
    with pytest.raises(ValueError, match="cs_plane is required"):
        holes.cut_screw_slot(
            MagicMock(name="comp"),
            sketch,
            (0, 0, 0),
            "x",
            4.0,
            0.0,
            8.0,
            _body(),
            ("z", 0.0),
            depth_mm=4.5,
            cs_plane=none_plane,
            label="t_slot",
        )


def test_chamfer_reach_takes_slot_outline_not_head() -> None:
    # slot outline edge at play + d/2 = 5.1 mm must be in reach; pad/plate edge at play + head_d/2 = 7.0 mm must be out.
    # Old reach (play + head_d/2 = 7.0) fails the upper bound.
    reach = holes.chamfer_reach_mm(3.0, 4.2)
    assert 5.1 <= reach < 7.0


# --- native HoleFeature helpers (hole_simple / hole_counterbore / hole_countersink) ---


@pytest.fixture
def vi_tagged() -> Iterator[None]:
    """ValueInput.createByString returns ("VI", expr) so tests assert the exact expression passed."""
    _CORE.ValueInput.createByString.side_effect = lambda s: ("VI", s)
    yield
    _CORE.ValueInput.createByString.side_effect = None


def test_hole_simple_all_points_one_feature_expression_depth(vi_tagged: None, object_collection: MagicMock) -> None:
    comp = MagicMock(name="comp")
    hf = comp.features.holeFeatures
    p1, p2 = MagicMock(name="p1"), MagicMock(name="p2")
    feat = holes.hole_simple(comp, [p1, p2], "screw_hole_diameter", "5 mm", label="t_hole")
    hf.createSimpleInput.assert_called_once_with(("VI", "screw_hole_diameter"))
    inp = hf.createSimpleInput.return_value
    assert object_collection.add.call_args_list == [call(p1), call(p2)]
    inp.setPositionBySketchPoints.assert_called_once_with(object_collection)
    inp.setDistanceExtent.assert_called_once_with(("VI", "5 mm"))
    inp.setAllExtent.assert_not_called()
    hf.add.assert_called_once_with(inp)  # one feature for many holes
    assert feat is hf.add.return_value
    assert feat.name == "t_hole"


def test_hole_simple_depth_none_is_through_all_negative_default(vi_tagged: None, object_collection: MagicMock) -> None:
    comp = MagicMock(name="comp")
    hf = comp.features.holeFeatures
    holes.hole_simple(comp, [MagicMock(name="p1")], "4 mm", label="t_hole")
    inp = hf.createSimpleInput.return_value
    inp.setAllExtent.assert_called_once_with(NEGATIVE_EXTENT)
    inp.setDistanceExtent.assert_not_called()


@pytest.mark.parametrize("through_dir", [NEGATIVE_EXTENT, POSITIVE_EXTENT])
def test_hole_through_dir_passed_to_set_all_extent(
    vi_tagged: None, object_collection: MagicMock, through_dir: Any
) -> None:
    comp = MagicMock(name="comp")
    hf = comp.features.holeFeatures
    holes.hole_simple(comp, [MagicMock(name="p1")], "4 mm", None, label="t_hole", through_dir=through_dir)
    hf.createSimpleInput.return_value.setAllExtent.assert_called_once_with(through_dir)


def test_hole_simple_accepts_ex_diameter(vi_tagged: None, object_collection: MagicMock) -> None:
    comp = MagicMock(name="comp")
    holes.hole_simple(comp, [MagicMock(name="p1")], Ex.mm(4.0), label="t_hole")
    comp.features.holeFeatures.createSimpleInput.assert_called_once_with(("VI", Ex.mm(4.0).e))


def test_hole_counterbore_passes_three_sizes(vi_tagged: None, object_collection: MagicMock) -> None:
    comp = MagicMock(name="comp")
    hf = comp.features.holeFeatures
    p1 = MagicMock(name="p1")
    feat = holes.hole_counterbore(comp, [p1], "4 mm", "8 mm", "3 mm", "10 mm", label="t_cb")
    hf.createCounterboreInput.assert_called_once_with(("VI", "4 mm"), ("VI", "8 mm"), ("VI", "3 mm"))
    hf.createSimpleInput.assert_not_called()
    inp = hf.createCounterboreInput.return_value
    inp.setDistanceExtent.assert_called_once_with(("VI", "10 mm"))
    inp.setPositionBySketchPoints.assert_called_once_with(object_collection)
    assert object_collection.add.call_args_list == [call(p1)]
    assert feat.name == "t_cb"


def test_hole_countersink_default_angle_90(vi_tagged: None, object_collection: MagicMock) -> None:
    comp = MagicMock(name="comp")
    hf = comp.features.holeFeatures
    holes.hole_countersink(comp, [MagicMock(name="p1")], "4 mm", "8 mm", label="t_cs")
    hf.createCountersinkInput.assert_called_once_with(("VI", "4 mm"), ("VI", "8 mm"), ("VI", "90 deg"))
    hf.createSimpleInput.assert_not_called()
    hf.createCountersinkInput.return_value.setAllExtent.assert_called_once_with(NEGATIVE_EXTENT)


def test_hole_countersink_explicit_angle(vi_tagged: None, object_collection: MagicMock) -> None:
    comp = MagicMock(name="comp")
    holes.hole_countersink(comp, [MagicMock(name="p1")], "4 mm", "8 mm", "100 deg", "6 mm", label="t_cs")
    comp.features.holeFeatures.createCountersinkInput.assert_called_once_with(
        ("VI", "4 mm"), ("VI", "8 mm"), ("VI", "100 deg")
    )
    comp.features.holeFeatures.createCountersinkInput.return_value.setDistanceExtent.assert_called_once_with(
        ("VI", "6 mm")
    )


@pytest.mark.parametrize(
    "make",
    [
        lambda comp: holes.hole_simple(comp, [], "4 mm", label="t_hole"),
        lambda comp: holes.hole_counterbore(comp, [], "4 mm", "8 mm", "3 mm", label="t_cb"),
        lambda comp: holes.hole_countersink(comp, [], "4 mm", "8 mm", label="t_cs"),
    ],
    ids=["simple", "counterbore", "countersink"],
)
def test_empty_points_raises_before_any_fusion_call(make: Callable[[MagicMock], object]) -> None:
    comp = MagicMock(name="comp")
    with pytest.raises(ValueError, match="points is empty"):
        make(comp)
    assert comp.mock_calls == []


def test_bad_label_raises_before_create_or_add(vi_tagged: None, object_collection: MagicMock) -> None:
    comp = MagicMock(name="comp")
    hf = comp.features.holeFeatures
    with pytest.raises(ValueError, match="snake_case"):
        holes.hole_simple(comp, [MagicMock(name="p1")], "4 mm", label="BadLabel")
    hf.createSimpleInput.assert_not_called()
    hf.add.assert_not_called()


# --- teardrop / seam notch / sacrificial depth ---


def _dist(p: tuple[float, float], q: tuple[float, float]) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _num(expr: str) -> float:
    return float(expr.split()[0])


@pytest.mark.parametrize("r", [1.0, 2.0])
def test_teardrop_peak_distance_60deg(r: float) -> None:
    peak, _, _ = holes.teardrop_points(0.0, 0.0, r, wall_deg=60.0)
    assert _dist(peak, (0.0, 0.0)) == pytest.approx(1.1547 * r, abs=1e-4)
    assert peak[0] == pytest.approx(0.0, abs=1e-9) and peak[1] == pytest.approx(1.1547 * r, abs=1e-4)


@pytest.mark.parametrize("wall", [45.0, 60.0, 75.0])
@pytest.mark.parametrize("up", [(0.0, 1.0), (1.0, 0.0), (1.0, 1.0)])
def test_teardrop_tangents_on_circle_and_perpendicular(wall: float, up: tuple[float, float]) -> None:
    cx, cy, r = 3.0, -1.0, 2.5
    peak, tan_l, tan_r = holes.teardrop_points(cx, cy, r, wall_deg=wall, up=up)
    assert _dist(peak, (cx, cy)) == pytest.approx(r / math.sin(math.radians(wall)), abs=1e-9)
    # peak lies on the up ray from the centre
    assert (peak[0] - cx) * up[1] - (peak[1] - cy) * up[0] == pytest.approx(0.0, abs=1e-9)
    for t in (tan_l, tan_r):
        assert _dist(t, (cx, cy)) == pytest.approx(r, abs=1e-9)  # on circle
        dot = (t[0] - peak[0]) * (t[0] - cx) + (t[1] - peak[1]) * (t[1] - cy)
        assert dot == pytest.approx(0.0, abs=1e-9)  # wall ⟂ radius at tangent point


@pytest.mark.parametrize("wall", [0.0, 90.0, 120.0])
def test_teardrop_points_bad_wall_rejected(wall: float) -> None:
    with pytest.raises(ValueError, match="wall_deg"):
        holes.teardrop_points(0.0, 0.0, 1.0, wall_deg=wall)


def test_teardrop_points_bad_radius_and_up_rejected() -> None:
    with pytest.raises(ValueError, match="r_mm"):
        holes.teardrop_points(0.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="up"):
        holes.teardrop_points(0.0, 0.0, 1.0, up=(0.0, 0.0))


def test_teardrop_points_default_is_45deg_full_point() -> None:
    peak, _, _ = holes.teardrop_points(0.0, 0.0, 2.0)
    assert _dist(peak, (0.0, 0.0)) == pytest.approx(2.0 * math.sqrt(2.0), abs=1e-9)


@pytest.mark.parametrize("r", [1.0, 2.0, 2.25, 4.0])
def test_teardrop_flat_points_flat_top_tangent_flanks(r: float) -> None:
    cx, cy = 3.0, -1.0
    top_l, top_r, tan_r, tan_l = holes.teardrop_flat_points(cx, cy, r, up=(0.0, 1.0))
    h = r + holes.TEARDROP_NOTCH_MM
    assert top_l[1] == pytest.approx(cy + h, abs=1e-9) and top_r[1] == pytest.approx(cy + h, abs=1e-9)  # flat top
    assert top_l[0] - cx == pytest.approx(-(top_r[0] - cx), abs=1e-9)  # symmetric about the hole axis
    assert top_l[0] - cx == pytest.approx(r * math.sqrt(2.0) - h, abs=1e-9)  # corner half-width
    for t in (tan_l, tan_r):
        assert _dist(t, (cx, cy)) == pytest.approx(r, abs=1e-9)  # tangent point on circle
    for corner, t in ((top_l, tan_l), (top_r, tan_r)):
        dot = (corner[0] - t[0]) * (t[0] - cx) + (corner[1] - t[1]) * (t[1] - cy)
        assert dot == pytest.approx(0.0, abs=1e-9)  # flank = tangent line at t (perp. to radius)


def test_teardrop_flat_points_rejects_top_at_or_above_full_peak() -> None:
    with pytest.raises(ValueError, match="top"):
        holes.teardrop_flat_points(0.0, 0.0, 0.5)  # r 0.5: notch top 0.9 >= full 45 deg peak 0.707
    with pytest.raises(ValueError, match="notch_mm"):
        holes.teardrop_flat_points(0.0, 0.0, 1.0, notch_mm=0.0)


@pytest.mark.parametrize("dir_deg", [0.0, 90.0, 200.0])
def test_seam_notch_apex_sides_on_circle_and_90deg(dir_deg: float) -> None:
    cx, cy, r = 1.0, 2.0, 2.0
    apex, p1, p2 = holes.seam_notch_points(cx, cy, r, notch_mm=0.3, dir_deg=dir_deg)
    assert _dist(apex, (cx, cy)) == pytest.approx(r + 0.3, abs=1e-9)  # apex r+0.3
    for p in (p1, p2):
        assert _dist(p, (cx, cy)) == pytest.approx(r, abs=1e-9)  # sides end on circle
    v1 = (p1[0] - apex[0], p1[1] - apex[1])
    v2 = (p2[0] - apex[0], p2[1] - apex[1])
    cos_apex = (v1[0] * v2[0] + v1[1] * v2[1]) / (math.hypot(*v1) * math.hypot(*v2))
    assert cos_apex == pytest.approx(0.0, abs=1e-9)  # 90 deg V


def test_seam_notch_apex_too_far_rejected() -> None:
    with pytest.raises(ValueError, match="miss circle"):
        holes.seam_notch_points(0.0, 0.0, 1.0, notch_mm=1.0)  # R=2 > sqrt(2)


def test_sacrificial_depth() -> None:
    assert holes.sacrificial_depth(3.0) == pytest.approx(2.8)
    assert holes.sacrificial_depth(3.0, layer_mm=0.5) == pytest.approx(2.5)
    with pytest.raises(ValueError, match="must be > 0"):
        holes.sacrificial_depth(0.2)
    with pytest.raises(ValueError, match="must be > 0"):
        holes.sacrificial_depth(0.1)


def test_to_model_maps_plane_coords_by_normal() -> None:
    assert holes._to_model(4.0, 5.0, 3.0, "z") == (4.0, 5.0, 3.0)
    assert holes._to_model(4.0, 5.0, 3.0, "y") == (4.0, 3.0, 5.0)  # XZ plane: u=x, v=z, w=y
    assert holes._to_model(4.0, 5.0, 3.0, "x") == (3.0, 4.0, 5.0)


@pytest.fixture
def seq(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """Record param_poly / circle / fix_point / extrude calls in order (no Fusion)."""
    log: list[tuple[str, Any]] = []

    def fake_poly(s: Any, pts: Any, **kw: Any) -> list[Any]:
        log.append(("param_poly", pts))
        return [SimpleNamespace(startSketchPoint=f"s{i}", endSketchPoint=f"e{i}") for i in range(3)]  # tri lines

    def fake_circle(s: Any, x: float, y: float, r: float, z: float = 0.0, diameter: Any = None) -> Any:
        log.append(("circle", (x, y, r, z, diameter)))
        return SimpleNamespace(centerSketchPoint="csp")

    def fake_fix(s: Any, sp: Any, p: Any) -> None:
        log.append(("fix_point", (sp, p)))

    def fake_extrude(comp: Any, prof: Any, dist: Any, **kw: Any) -> None:
        log.append(("extrude", (prof, dist, kw)))

    monkeypatch.setattr(holes, "param_poly", fake_poly)
    monkeypatch.setattr(holes, "circle", fake_circle)
    monkeypatch.setattr(holes, "fix_point", fake_fix)
    monkeypatch.setattr(holes, "extrude", fake_extrude)
    return log


def test_teardrop_hole_full_point_d10_triangle_then_circle_then_cut(
    seq: list[tuple[str, Any]], sketch: MagicMock
) -> None:
    body = _body()
    holes.teardrop_hole(
        MagicMock(name="comp"), sketch, (0.0, 5.0, 0.0), 5.0, body, 4.0, label="t_drop"
    )  # normal y: circle at x=0, y=5, z=0; d10 > 8 -> full 45 deg point
    assert [name for name, _ in seq] == ["param_poly", "circle", "fix_point", "extrude"]
    pts = seq[0][1]
    assert len(pts) == 3 and all(isinstance(e, str) and e.endswith(" mm") for p in pts for e in p)
    assert _num(pts[0][2]) == pytest.approx(5.0 * math.sqrt(2.0), abs=1e-4)  # apex on +Z, r * sqrt 2
    assert _num(pts[0][1]) == pytest.approx(5.0, abs=1e-9)  # plane y kept
    cx, cy, r, cz, diameter = seq[1][1]
    assert (cx, cy, cz) == (0.0, 5.0, 0.0) and r == 5.0
    assert _num(diameter) == pytest.approx(10.0, abs=1e-9)  # radius dimensioned, not free
    assert seq[2][1][0] == "csp"
    prof, dist, kw = seq[3][1]
    assert prof == sketch.profiles  # all sketch profiles cut
    assert dist.v == pytest.approx(4.0)  # symmetric depth = total length
    assert kw["op"] == "cut" and kw["symmetric"] is True and kw["bodies"] == [body] and kw["label"] == "t_drop"


def test_teardrop_hole_notch_d4_flat_top_then_circle_then_cut(seq: list[tuple[str, Any]], sketch: MagicMock) -> None:
    body = _body()
    holes.teardrop_hole(MagicMock(name="comp"), sketch, (0.0, 5.0, 0.0), 2.0, body, 4.0, label="t_drop")
    assert [name for name, _ in seq] == ["param_poly", "circle", "fix_point", "extrude"]
    pts = seq[0][1]
    assert len(pts) == 4 and all(isinstance(e, str) and e.endswith(" mm") for p in pts for e in p)
    assert _num(pts[0][2]) == pytest.approx(2.4, abs=1e-6)  # flat top at r + 0.4 on +Z
    assert _num(pts[1][2]) == pytest.approx(2.4, abs=1e-6)
    assert _num(pts[0][0]) == pytest.approx(2.0 * math.sqrt(2.0) - 2.4, abs=1e-5)  # corner half-width
    assert _num(pts[1][0]) == pytest.approx(-(2.0 * math.sqrt(2.0) - 2.4), abs=1e-5)
    assert math.hypot(_num(pts[3][0]), _num(pts[3][2])) == pytest.approx(2.0, abs=1e-5)  # tangent point on circle
    assert _num(pts[0][1]) == pytest.approx(5.0, abs=1e-9)  # plane y kept
    _prof, _dist, kw = seq[3][1]
    assert kw["op"] == "cut" and kw["label"] == "t_drop"


@pytest.mark.parametrize(("d_mm", "top_mm"), [(2.0, 1.4), (3.0, 1.9)])
def test_teardrop_hole_notch_d2_d3_flat_top_unchanged(
    d_mm: float, top_mm: float, seq: list[tuple[str, Any]], sketch: MagicMock
) -> None:
    holes.teardrop_hole(MagicMock(name="comp"), sketch, (0.0, 5.0, 0.0), d_mm / 2, _body(), 4.0, label="t_drop")
    pts = seq[0][1]
    assert len(pts) == 4  # flat-topped notch kept
    assert _num(pts[0][2]) == pytest.approx(top_mm, abs=1e-6)
    assert _num(pts[1][2]) == pytest.approx(top_mm, abs=1e-6)


def test_teardrop_hole_d18_full_point_no_raise(seq: list[tuple[str, Any]], sketch: MagicMock) -> None:
    # d1.8: notch top r+0.4 = 1.3 would sit above full 45 deg peak r*sqrt2 = 1.273 -> full point, no ValueError
    holes.teardrop_hole(MagicMock(name="comp"), sketch, (0.0, 5.0, 0.0), 0.9, _body(), 4.0, label="t_drop")
    assert [name for name, _ in seq] == ["param_poly", "circle", "fix_point", "extrude"]
    pts = seq[0][1]
    assert len(pts) == 3
    assert _num(pts[0][2]) == pytest.approx(0.9 * math.sqrt(2.0), abs=1e-5)  # apex on +Z, peak r*sqrt2
    assert _num(pts[0][1]) == pytest.approx(5.0, abs=1e-9)  # plane y kept


def test_teardrop_hole_bad_label_no_sketch_calls(seq: list[tuple[str, Any]], sketch: MagicMock) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        holes.teardrop_hole(MagicMock(name="comp"), sketch, (0.0, 0.0, 0.0), 2.0, _body(), 4.0, label="BadLabel")
    assert seq == []


def test_teardrop_hole_empty_sketch_raises(seq: list[tuple[str, Any]], sketch: MagicMock) -> None:
    sketch.profiles = []
    with pytest.raises(RuntimeError, match="no profiles"):
        holes.teardrop_hole(MagicMock(name="comp"), sketch, (0.0, 0.0, 0.0), 2.0, _body(), 4.0, label="t_drop")
    assert [name for name, _ in seq] == ["param_poly", "circle", "fix_point"]


_LIVE_CENTRE = ("tear_cx", "tear_cy", "tear_cz")  # normal y: plane u = x, v = z, normal coord = y


_LIVE_PARAMS: dict[str, float] = {}  # fake User Parameter values, set per call by _live_call


@pytest.fixture
def live_sketch(monkeypatch: pytest.MonkeyPatch, sketch: MagicMock, point3d_xyz: None) -> MagicMock:  # P3 numeric
    monkeypatch.setattr(holes, "new_sketch", lambda comp, plane, *, label: sketch)
    monkeypatch.setattr(
        holes, "mm", lambda expr: evaluate(expr, _LIVE_PARAMS)
    )  # fake sketch.mm (eval_fresh, no Fusion)
    return sketch


def _live_call(r_mm: float, **kw: Any) -> None:  # r_mm: fake tear_r value (_LIVE_PARAMS), builder takes the expr
    _LIVE_PARAMS.update(tear_cx=0.0, tear_cy=5.0, tear_cz=0.0, tear_r=r_mm, tear_depth=4.0)
    holes.teardrop_hole_live(
        MagicMock(name="comp"),
        MagicMock(name="plane"),
        _LIVE_CENTRE,
        "tear_r",
        label="t_live",
        **kw,
    )


def _live_verts(seq: list[tuple[str, Any]], r_mm: float) -> list[tuple[float, float, float]]:
    env = {"tear_cx": 0.0, "tear_cy": 5.0, "tear_cz": 0.0, "tear_r": r_mm, "tear_depth": 4.0}
    out: list[tuple[float, float, float]] = []
    for p in seq[0][1]:
        x, y, z = (float(eval(e, {"__builtins__": {}}, env)) for e in p)
        out.append((x, y, z))
    return out


def test_teardrop_hole_live_dims_are_expressions(seq: list[tuple[str, Any]], live_sketch: MagicMock) -> None:
    _live_call(1.5, depth="tear_depth")  # d3: full point, 3 vertices -> 3 vertex dims
    assert [name for name, _ in seq] == ["param_poly", "circle", "fix_point", "extrude"]
    pts = seq[0][1]
    assert len(pts) == 3
    for p in pts:
        assert all("tear_" in e for e in p)  # every coordinate is a param expression, no bare floats
        assert p[1] == "tear_cy"  # normal coord = centre expression
    assert seq[1][1][4] == "(2 * (tear_r))"  # circle diameter = expression
    assert seq[2][1][1] == _LIVE_CENTRE  # centre fixed by its expressions
    _prof, dist, kw = seq[3][1]
    assert dist == "tear_depth"
    assert kw["op"] == "cut" and kw["label"] == "t_live_cut" and kw["taper"] is None and kw["to"] is None


@pytest.mark.parametrize("d_mm", [2.0, 3.0, 8.0, 10.0])
def test_teardrop_hole_live_peak_meets_dfam_rule(
    d_mm: float, seq: list[tuple[str, Any]], live_sketch: MagicMock
) -> None:
    r = d_mm / 2
    _live_call(r, depth="tear_depth")
    verts = _live_verts(seq, r)
    peak = max(v[2] for v in verts)  # plane v = z = up
    required = dfam.teardrop_required_peak_mm(r)
    assert peak >= dfam.TEARDROP_TOL * required  # real rule, no re-implementation
    if d_mm <= dfam.TEARDROP_NOTCH_MAX_D_MM:
        assert peak <= dfam.TEARDROP_NOTCH_MAX_RATIO * required  # notch cap, full point must pass it too
    assert peak == pytest.approx(r * math.sqrt(2.0), abs=1e-9)  # always the full 45 deg point
    for v in verts:
        assert v[1] == pytest.approx(5.0, abs=1e-9)  # plane y kept


def test_teardrop_hole_live_tangent_points_on_circle(seq: list[tuple[str, Any]], live_sketch: MagicMock) -> None:
    for r in (1.5, 5.0):
        seq.clear()
        _live_call(r, depth="tear_depth")
        tan_pts = _live_verts(seq, r)[-2:]
        for x, _y, z in tan_pts:
            assert math.hypot(x - 0.0, z - 0.0) == pytest.approx(r, abs=1e-9)  # tangent points sit on the circle


def test_teardrop_hole_live_initial_circle_radius_is_evaluated_r(
    seq: list[tuple[str, Any]], live_sketch: MagicMock
) -> None:
    for r in (1.5, 5.0):  # placeholder radius 1.0 fails both
        seq.clear()
        _live_call(r, depth="tear_depth")
        assert seq[1][0] == "circle"
        assert seq[1][1][2] == pytest.approx(r, abs=1e-9)  # initial circle = evaluated r; diameter expr sets final size


def test_teardrop_hole_live_extent_passthrough(seq: list[tuple[str, Any]], live_sketch: MagicMock) -> None:
    to_plane = MagicMock(name="outer_plane")
    _live_call(2.0, to=to_plane, taper="-45 deg")  # countersink-style: up to plane, tapered
    _prof, dist, kw = seq[3][1]
    assert dist is None and kw["to"] is to_plane and kw["taper"] == "-45 deg"


def test_teardrop_hole_live_needs_exactly_one_extent(seq: list[tuple[str, Any]], live_sketch: MagicMock) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        _live_call(2.0)
    with pytest.raises(ValueError, match="exactly one"):
        _live_call(2.0, depth="tear_depth", to=MagicMock(name="plane"))
    assert seq == []


def test_teardrop_hole_live_bad_label_no_sketch_calls(seq: list[tuple[str, Any]]) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        holes.teardrop_hole_live(
            MagicMock(name="comp"),
            MagicMock(name="plane"),
            _LIVE_CENTRE,
            "tear_r",
            label="BadLabel",
            depth="tear_depth",
        )
    assert seq == []


def test_teardrop_hole_live_empty_sketch_raises(seq: list[tuple[str, Any]], live_sketch: MagicMock) -> None:
    live_sketch.profiles = []
    with pytest.raises(RuntimeError, match="no profiles"):
        _live_call(2.0, depth="tear_depth")
    assert [name for name, _ in seq] == ["param_poly", "circle", "fix_point"]


def test_teardrop_hole_live_expr_only_centre_evaluated(seq: list[tuple[str, Any]], live_sketch: MagicMock) -> None:
    # centre from expressions only: initial circle / vertices land on the evaluated centre expressions
    _LIVE_PARAMS.update(tear_cx=3.0, tear_cy=7.0, tear_cz=-2.0, tear_r=1.5, tear_depth=4.0)
    holes.teardrop_hole_live(
        MagicMock(name="comp"), MagicMock(name="plane"), _LIVE_CENTRE, r="tear_r", label="t_live", depth="tear_depth"
    )
    assert [name for name, _ in seq] == ["param_poly", "circle", "fix_point", "extrude"]
    cx, cy, r, cz, diameter = seq[1][1]
    assert (cx, cy, cz) == pytest.approx((3.0, 7.0, -2.0), abs=1e-9)
    assert r == pytest.approx(1.5, abs=1e-9)
    assert diameter == "(2 * (tear_r))"
    assert seq[2][1][1] == _LIVE_CENTRE
    env = {"tear_cx": 3.0, "tear_cy": 7.0, "tear_cz": -2.0, "tear_r": 1.5, "tear_depth": 4.0}
    peak = tuple(float(eval(e, {"__builtins__": {}}, env)) for e in seq[0][1][0])
    assert peak == pytest.approx((3.0, 7.0, -2.0 + 1.5 * math.sqrt(2.0)), abs=1e-9)  # apex = centre + r*sqrt2 on +Z


def test_teardrop_hole_live_needs_radius(seq: list[tuple[str, Any]], live_sketch: MagicMock) -> None:
    with pytest.raises(TypeError, match="'r'"):  # r required positional-or-keyword: missing -> TypeError, no body run
        holes.teardrop_hole_live(
            MagicMock(name="comp"), MagicMock(name="plane"), _LIVE_CENTRE, label="t_live", depth="tear_depth"
        )
    assert seq == []


class _TPoint:
    """Fake SketchPoint, geometry in cm. move() refused: initial geometry is on the right side of every axis."""

    def __init__(self, x: float, y: float) -> None:
        self.geometry = SimpleNamespace(x=x, y=y, z=0.0)

    def move(self, v: Any) -> bool:
        raise AssertionError("no move expected")


class _TCircle:
    def __init__(self, centre: _TPoint) -> None:
        self.centerSketchPoint = centre


class _TParam:
    def __init__(self, exprs: list[str]) -> None:
        self._exprs = exprs

    @property
    def expression(self) -> str:
        return ""

    @expression.setter
    def expression(self, v: str) -> None:
        self._exprs.append(v)


class _TLines:
    def addByTwoPoints(self, a: Any, b: Any) -> Any:
        sa = a if isinstance(a, _TPoint) else _TPoint(a.x, a.y)
        sb = b if isinstance(b, _TPoint) else _TPoint(b.x, b.y)
        return SimpleNamespace(startSketchPoint=sa, endSketchPoint=sb)


class _TGC:
    def __init__(self, s: "_TSketch") -> None:
        self._s = s

    def __iter__(self) -> Any:
        return iter([])  # no drawing inference

    def addCoincident(self, a: Any, b: Any) -> None:
        self._s.dof += 1 if isinstance(b, _TCircle) else 2  # point on curve 1, point-point 2

    def addTangent(self, a: Any, b: Any) -> None:
        self._s.dof += 1

    def addHorizontal(self, line: Any) -> None:
        self._s.dof += 1

    def addVertical(self, line: Any) -> None:
        self._s.dof += 1

    def addHorizontalPoints(self, a: Any, b: Any) -> None:
        self._s.dof += 1

    def addVerticalPoints(self, a: Any, b: Any) -> None:
        self._s.dof += 1


class _TDims:
    def __init__(self, s: "_TSketch") -> None:
        self._s = s

    def addDistanceDimension(self, a: Any, b: Any, orientation: Any, pos: Any) -> Any:
        self._s.dims += 1
        self._s.dof += 1
        return SimpleNamespace(parameter=_TParam(self._s.exprs))


class _TSketch:
    """Counts dims (+ bound exprs) and DOF removed by constraints. Sketch x = model x, y = model z, z = model y."""

    def __init__(self) -> None:
        self.dims = 0
        self.exprs: list[str] = []
        self.dof = 0
        self.modelToSketchSpace = lambda p: SimpleNamespace(x=p.x, y=p.z, z=p.y)
        self.originPoint = _TPoint(0.0, 0.0)
        self.sketchCurves = SimpleNamespace(sketchLines=_TLines())
        self.geometricConstraints = _TGC(self)
        self.sketchDimensions = _TDims(self)


def _t_circle(s: Any, x: float, y: float, r: float, z: float = 0.0, diameter: str | None = None) -> _TCircle:
    c = fk_sketch.to_sketch(s, x, y, z)
    s.dims += 1
    s.dof += 1
    s.exprs.append(str(diameter))
    return _TCircle(_TPoint(c.x, c.y))


def test_teardrop_live_fewer_dims_no_dup_expr(monkeypatch: pytest.MonkeyPatch, point3d_xyz: None) -> None:
    # Before: 9 dims, tan_l z and tan_r z both "tear_cz + ..." (dup). After: peak v + centre x,y + diameter = 4 dims,
    # tips pinned by 2 point-on-circle + 2 tangent, peak x by vertical to centre. DOF 9 = fully constrained.
    # non-zero centre: a zero coord becomes an alignment constraint in _sketch_dims, not a dim
    _LIVE_PARAMS.update(tear_cx=3.0, tear_cy=5.0, tear_cz=7.0, tear_r=1.5, tear_depth=4.0)

    def ev(expr: str) -> float:
        return evaluate(expr, _LIVE_PARAMS)

    monkeypatch.setattr(fk_sketch, "mm", ev)
    monkeypatch.setattr(holes, "mm", ev)
    sk = _TSketch()
    monkeypatch.setattr(holes, "new_sketch", lambda comp, plane, *, label: sk)
    monkeypatch.setattr(holes, "circle", _t_circle)
    monkeypatch.setattr(holes, "profiles", lambda s: [object()])
    monkeypatch.setattr(holes, "extrude", lambda *a, **k: None)
    holes.teardrop_hole_live(
        MagicMock(name="comp"),
        MagicMock(name="plane"),
        _LIVE_CENTRE,
        "tear_r",
        label="t_live",
        depth="tear_depth",
    )
    assert sk.dims < 9, f"dims {sk.dims}"
    assert len(sk.exprs) == len(set(sk.exprs)), sk.exprs
    assert sk.dof == 9, f"dof {sk.dof}"


def test_seam_notch_triangle_then_cut(seq: list[tuple[str, Any]], sketch: MagicMock) -> None:
    body = _body()
    holes.seam_notch(MagicMock(name="comp"), sketch, (0.0, 0.0, 0.0), 2.0, body, 4.0, label="t_notch")
    assert [name for name, _ in seq] == ["param_poly", "extrude"]
    apex = [_num(e) for e in seq[0][1][0]]  # normal y, dir 90 deg -> plane v = +Z: apex (x, y, z) = (0, 0, 2.3)
    assert apex == pytest.approx([0.0, 0.0, 2.3], abs=1e-6)
    _, dist, kw = seq[1][1]
    assert dist.v == pytest.approx(4.0) and kw["op"] == "cut" and kw["symmetric"] is True and kw["bodies"] == [body]


def test_seam_notch_expr_dims_are_param_names(seq: list[tuple[str, Any]], sketch: MagicMock) -> None:
    body = _body()
    holes.seam_notch(
        MagicMock(name="comp"),
        sketch,
        (0.0, 5.0, 0.0),
        2.0,
        body,
        4.0,
        label="t_notch",
        center_expr=("sn_cx", "sn_cy", "sn_cz"),
        r_expr="sn_r",
        notch_expr="sn_n",
        depth_expr="sn_d",
    )
    assert [name for name, _ in seq] == ["param_poly", "extrude"]
    verts = seq[0][1]
    assert len(verts) == 3
    for v in verts:
        assert v[1] == "sn_cy"  # normal y: plane offset is the centre expression, unchanged
        assert "sn_r" in v[0] + v[2] and "sn_n" in v[0] + v[2]  # apex and sides all built from r and notch names
    _, dist, kw = seq[1][1]
    assert dist == "sn_d" and kw["op"] == "cut" and kw["symmetric"] is True


def test_seam_notch_expr_matches_numeric_geometry(seq: list[tuple[str, Any]], sketch: MagicMock) -> None:
    body = _body()
    names = {"sn_cx": 0.0, "sn_cy": 5.0, "sn_cz": 0.0, "sn_r": 2.0, "sn_n": 0.3}
    holes.seam_notch(
        MagicMock(name="comp"),
        sketch,
        (0.0, 5.0, 0.0),
        2.0,
        body,
        4.0,
        label="t_notch",
        center_expr=("sn_cx", "sn_cy", "sn_cz"),
        r_expr="sn_r",
        notch_expr="sn_n",
    )
    got = [tuple(evaluate(e, names) for e in v) for v in seq[0][1]]
    apex, p1, p2 = holes.seam_notch_points(0.0, 0.0, 2.0, notch_mm=0.3, dir_deg=90.0)
    want = [(apex[0], 5.0, apex[1]), (p1[0], 5.0, p1[1]), (p2[0], 5.0, p2[1])]
    for g, w in zip(got, want):
        assert g == pytest.approx(w, abs=1e-6)


@pytest.mark.parametrize(
    "partial",
    [
        {"center_expr": ("sn_cx", "sn_cy", "sn_cz")},
        {"r_expr": "sn_r"},
        {"notch_expr": "sn_n"},
        {"center_expr": ("sn_cx", "sn_cy", "sn_cz"), "r_expr": "sn_r"},
    ],
    ids=["center_only", "r_only", "notch_only", "center_and_r"],
)
def test_seam_notch_mixed_expr_raises(partial: dict[str, Any], seq: list[tuple[str, Any]], sketch: MagicMock) -> None:
    with pytest.raises(ValueError, match="together"):
        holes.seam_notch(MagicMock(name="comp"), sketch, (0.0, 0.0, 0.0), 2.0, _body(), 4.0, label="t_notch", **partial)
    assert seq == []
