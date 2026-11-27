"""Offline tests for fusion_kit.features.groove_positions (pure function, no Fusion)."""

import itertools
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import MagicMock

import pytest

from fusion_kit import features, features_axes, features_warp
from fusion_kit.dfam import BedDir
from fusion_kit.enums import IDENTICAL_PATTERN
from fusion_kit.expr import evaluate
from fusion_kit.features import GROOVE_EDGE_MIN_MM, groove_positions


def _fresh_mm(names: dict[str, float]) -> Any:
    """eval_fresh stand-in: user-param expr -> internal cm (Fusion units) from the given mm values."""
    return lambda design, expr, units: evaluate(expr, names) / 10


@pytest.fixture(autouse=True)
def _param_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests passing *_expr with a MagicMock comp: user-param values come from here, not snapshot_geo(MagicMock)."""
    names = {
        "warp_groove_width": 0.8,
        "warp_groove_depth": 0.3,
        "warp_groove_margin": 5.0,
        "warp_groove_target_pitch": 30.0,
    }
    monkeypatch.setattr(features_warp, "eval_fresh", _fresh_mm(names))


def test_span_at_or_below_35_has_no_grooves() -> None:
    assert groove_positions(35.0) == []
    assert groove_positions(10.0) == []


def test_span_just_over_35_gives_two_grooves_not_one() -> None:
    # 1 groove never qualifies in dfam._warp_family (needs >= 2 parallel lines) -> warp_grooves FAIL on 35-60 mm beds.
    assert groove_positions(36.0) == pytest.approx([-15.0, 15.0])


@pytest.mark.parametrize("length", [38.0, 59.0])
def test_span_35_to_60_gives_two_grooves_inside_span(length: float) -> None:
    pos = groove_positions(length)
    half = length / 2
    assert len(pos) >= 2
    assert all(-half < p < half for p in pos)
    edges = [-half, *pos, half]
    assert all(b - a < 35.0 for a, b in itertools.pairwise(edges))


def test_pitch_wider_than_span_uses_thirds() -> None:
    pos = groove_positions(36.0, pitch_mm=40.0)  # pitch pair at +-20 would sit outside +-18
    assert pos == pytest.approx([-6.0, 6.0])
    assert all(-18.0 < p < 18.0 for p in pos)


def test_100_mm_gives_three_symmetric_grooves() -> None:
    pos = groove_positions(100.0)
    assert pos == pytest.approx([-30.0, 0.0, 30.0])
    assert pos == pytest.approx([-p for p in reversed(pos)])


def test_even_count_is_symmetric_about_zero() -> None:
    pos = groove_positions(65.0)  # floor(65/30) = 2
    assert pos == pytest.approx([-15.0, 15.0])


def test_spacing_equals_pitch() -> None:
    pos = groove_positions(200.0, pitch_mm=25.0)
    gaps = [b - a for a, b in itertools.pairwise(pos)]
    assert len(gaps) == len(pos) - 1
    assert gaps == pytest.approx([25.0] * len(gaps))


def _segments(length: float, pos: list[float]) -> list[float]:
    edges = [-length / 2, *pos, length / 2]
    return [b - a for a, b in itertools.pairwise(edges)]


def test_l36_pitch34_keeps_outer_grooves_margin_from_edge() -> None:
    # pitch pair at +-17 sat 1 mm from the bed edge (overlaps the 1 mm bed chamfer) -> equal segments instead
    pos = groove_positions(36.0, pitch_mm=34.0)
    assert pos == pytest.approx([-6.0, 6.0])
    assert _segments(36.0, pos)[0] >= GROOVE_EDGE_MIN_MM


def test_l104_pitch50_no_segment_over_35() -> None:
    # pitch 50 would leave a 50 mm middle segment -> n = 2 at equal segments 104 / 3
    pos = groove_positions(104.0, pitch_mm=50.0)
    assert pos == pytest.approx([-104.0 / 6, 104.0 / 6])
    assert all(s < 35.0 for s in _segments(104.0, pos))


def test_l38_pitch30_unchanged() -> None:
    assert groove_positions(38.0, pitch_mm=30.0) == pytest.approx([-15.0, 15.0])


@pytest.mark.parametrize(
    ("length", "pitch"),
    list(
        itertools.product(
            [36.0, 38.0, 59.0, 65.0, 104.0, 110.0, 150.0, 170.0, 200.0],
            [5.0, 25.0, 30.0, 34.0, 40.0, 50.0, 80.0],
        )
    ),
)
def test_invariants_hold_for_any_length_and_pitch(length: float, pitch: float) -> None:
    pos = groove_positions(length, pitch)
    assert len(pos) >= 2
    assert pos == pytest.approx([-p for p in reversed(pos)])
    segs = _segments(length, pos)
    assert all(s < 35.0 for s in segs)
    assert segs[0] >= GROOVE_EDGE_MIN_MM and segs[-1] >= GROOVE_EDGE_MIN_MM


# ---------------------------------------------------------------------------
# _relief_family: pattern runs on the model origin axis, no construction axis/planes
# ---------------------------------------------------------------------------
def _relief_body() -> Any:
    # bbox in cm: 100 mm x 100 mm -> both families span 100 mm -> centres -30, 0, 30 mm
    return SimpleNamespace(
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0),
            maxPoint=SimpleNamespace(x=10.0, y=10.0),
        )
    )


def _cube3() -> Any:
    # cm: 100 mm cube -> both in-plane spans 100 mm (bed -z) -> centres -30, 0, 30 mm about 50 mm
    return SimpleNamespace(
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            maxPoint=SimpleNamespace(x=10.0, y=10.0, z=10.0),
        )
    )


def _patch_bed(monkeypatch: pytest.MonkeyPatch) -> None:
    # bed plane normal +Z, bed -z -> depth_sign +1
    plane = SimpleNamespace(geometry=SimpleNamespace(normal=SimpleNamespace(x=0.0, y=0.0, z=1.0)))
    monkeypatch.setattr(features_warp, "axis_plane", lambda comp, axis, expr, value_mm, label: plane)
    monkeypatch.setattr(features_warp, "set_bed", lambda body, bed: None)


def _fake_fusion(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Fake sketch / seed rect / extrude / pattern. rec.sketches = labels, rec.rects = [(args, kwargs)] per
    _seed_rect call, rec.extrudes = [(prof, distance, kwargs)], rec.pattern = pattern_rectangular mock."""
    rec = SimpleNamespace(
        sketches=[], rects=[], extrudes=[], pattern=MagicMock(name="pattern_rectangular", return_value="pat")
    )

    def fake_sketch(comp: Any, plane: Any, *, label: str) -> SimpleNamespace:
        rec.sketches.append(label)
        return SimpleNamespace(label=label)

    def fake_extrude(comp: Any, prof: Any, distance: str, **kwargs: Any) -> str:
        rec.extrudes.append((prof, distance, kwargs))
        return f"feat:{kwargs['label']}"

    monkeypatch.setattr(features_warp, "new_sketch", fake_sketch)
    monkeypatch.setattr(features_warp, "_seed_rect", lambda sk, *c, **kw: rec.rects.append((c, kw)))
    monkeypatch.setattr(features_warp, "profile", lambda sk: "prof")
    monkeypatch.setattr(features_warp, "profiles", lambda sk: ["p1", "p2"])
    monkeypatch.setattr(features_warp, "extrude", fake_extrude)
    monkeypatch.setattr(features_warp, "pattern_rectangular", rec.pattern)
    return rec


def _literal_calls(
    monkeypatch: pytest.MonkeyPatch, along: Literal["x", "y"], **kw: Any
) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
    """_literal_grooves on the 100 mm body (_relief_body), bed -z: (seed rect args, kwargs) per groove rect."""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(features_warp, "_seed_rect", lambda sk, *c, **k: calls.append((c, k)))
    features_warp._literal_grooves(
        MagicMock(name="sketch"),
        _relief_body(),
        along,
        width_mm=0.8,
        pitch_mm=30.0,
        bed_dir="-z",
        bed_mm=0.0,
        width_expr=kw.get("width_expr"),
        run_expr=kw.get("run_expr"),
    )
    return calls


def _literal_corners(monkeypatch: pytest.MonkeyPatch, along: Literal["x", "y"]) -> list[tuple[Any, ...]]:
    return [c for c, _ in _literal_calls(monkeypatch, along)]


def test_literal_families_each_own_sketch_and_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    # Live bug: both literal families in ONE sketch -> crossing rects enclose cells and profiles() returns them too
    # (housing_warp_sketch: 33 profiles, 3 x 852.64 mm2 = 29.2 mm cells) -> whole bed face recessed 0.6 mm, overhang.
    # Parallel strips of one family enclose nothing: one sketch + one cut per literal family, no pattern.
    rec = _fake_fusion(monkeypatch)
    _patch_bed(monkeypatch)
    rect_sketch: list[str] = []
    monkeypatch.setattr(features_warp, "_seed_rect", lambda sk, *c, **k: rect_sketch.append(sk.label))
    comp = MagicMock(name="comp")
    through = MagicMock(name="axis_through")
    for axis_fn in ("axis_x_through", "axis_y_through", "axis_z_through"):
        monkeypatch.setattr(features_axes, axis_fn, through)
    out = features.warp_relief_grooves(comp, _cube3(), label="warp_t", width_mm=0.8, depth_mm=0.3, pitch_mm=30.0)
    assert rec.sketches == ["warp_t_x_sketch", "warp_t_y_sketch"]
    assert rect_sketch == ["warp_t_x_sketch"] * 3 + ["warp_t_y_sketch"] * 3
    assert [kw["label"] for _, _, kw in rec.extrudes] == ["warp_t_x", "warp_t_y"]
    assert all(kw["op"] == "cut" for _, _, kw in rec.extrudes)
    assert all(prof == ["p1", "p2"] for prof, _, _ in rec.extrudes)
    assert all(distance == "0.300000 mm" for _, distance, _ in rec.extrudes)
    assert out == ["feat:warp_t_x", "feat:warp_t_y"]
    rec.pattern.assert_not_called()
    through.assert_not_called()
    comp.constructionAxes.add.assert_not_called()
    comp.constructionPlanes.add.assert_not_called()


# ---------------------------------------------------------------------------
# _relief_family: pattern compute + seed centre (regression for live Fusion bugs)
# ---------------------------------------------------------------------------
def _run_family(
    monkeypatch: pytest.MonkeyPatch, along: Literal["x", "y"], corners_out: list[tuple[Any, ...]]
) -> MagicMock:
    axis_fn = "axis_x_through" if along == "x" else "axis_y_through"
    axis = SimpleNamespace(geometry=SimpleNamespace(direction=SimpleNamespace(x=1.0, y=1.0, z=0.0)))
    monkeypatch.setattr(features_axes, axis_fn, lambda comp, **kwargs: axis)
    monkeypatch.setattr(features_warp, "new_sketch", lambda comp, plane, *, label: SimpleNamespace(label=label))
    monkeypatch.setattr(features_warp, "_seed_rect", lambda sk, *corners, **kw: corners_out.append(corners))
    monkeypatch.setattr(features_warp, "profile", lambda sk: "prof")
    monkeypatch.setattr(features_warp, "extrude", lambda comp, prof, distance, **kwargs: "seed")
    pat = MagicMock(name="pattern_rectangular", return_value="pat")
    monkeypatch.setattr(features_warp, "pattern_rectangular", pat)
    features._relief_family(
        MagicMock(name="comp"),
        MagicMock(name="bed"),
        _relief_body(),
        along,
        label="t",
        width_mm=0.8,
        depth_mm=0.3,
        pitch_mm=30.0,
        **_LIVE_KW,
    )
    return pat


@pytest.mark.parametrize("along", ["x", "y"])
def test_relief_family_pattern_uses_identical_compute(
    monkeypatch: pytest.MonkeyPatch, along: Literal["x", "y"]
) -> None:
    # OPTIMIZED pattern after the other family silently truncated the body in live Fusion (vol 43.7 -> 20.5).
    pat = _run_family(monkeypatch, along, [])
    assert pat.call_args.kwargs["compute"] == IDENTICAL_PATTERN


def test_seed_rect_dims_both_corners_to_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: corner-to-corner dims let the solver flip the far edge (live X centre 14.2 instead of 15).
    # Fails on the old rect_xy seed: no fix_point on the far corner, no _seed_rect.
    lines = [MagicMock(name=f"l{i}") for i in range(4)]
    monkeypatch.setattr(features_warp, "rect", lambda s, a, b: lines)
    fix = MagicMock(name="fix_point")
    monkeypatch.setattr(features_warp, "fix_point", fix)
    features._seed_rect(MagicMock(name="sketch"), 14.6, 20.0, 15.4, 21.0)
    assert fix.call_count == 2
    near, far = fix.call_args_list
    assert near.args[1] is lines[0].startSketchPoint  # bottom start = corner a
    assert far.args[1] is lines[2].startSketchPoint  # top start = corner b
    assert [float(v.split()[0]) for v in near.args[2][:2]] == pytest.approx([14.6, 20.0])
    assert [float(v.split()[0]) for v in far.args[2][:2]] == pytest.approx([15.4, 21.0])


def test_x_and_y_seed_centres_equal_expected_coordinate(monkeypatch: pytest.MonkeyPatch) -> None:
    # 100 mm span, pitch 30 -> centres -30/0/30 about body centre 50 -> first groove centre 20 mm for BOTH families.
    cx = _literal_corners(monkeypatch, "x")[0]  # _seed_rect(s, x0, y0, x1, y1), mm floats
    cy = _literal_corners(monkeypatch, "y")[0]
    centre_x = (cx[0] + cx[2]) / 2  # X family spaces along x
    centre_y = (cy[1] + cy[3]) / 2  # Y family spaces along y
    assert centre_x == pytest.approx(20.0)
    assert centre_y == pytest.approx(20.0)
    assert centre_x == pytest.approx(centre_y)


# ---------------------------------------------------------------------------
# width_expr / depth_expr: expression strings reach the timeline
# ---------------------------------------------------------------------------
def _family_capture(
    monkeypatch: pytest.MonkeyPatch,
    along: Literal["x", "y"],
    direction_sign: float,
    *,
    body: Any | None = None,
    pitch_mm: float = 30.0,
    **kw: Any,
) -> dict[str, Any]:
    """Run _relief_family with fake Fusion calls; return what reached the seed rect, cut depth, pattern spacing."""
    axis_fn = "axis_x_through" if along == "x" else "axis_y_through"
    direction = SimpleNamespace(x=0.0, y=0.0, z=0.0)
    setattr(direction, along, direction_sign)
    axis = SimpleNamespace(geometry=SimpleNamespace(direction=direction))
    seen: dict[str, Any] = {}

    def fake_seed(sk: Any, *corners: Any, **kwargs: Any) -> None:
        seen["rect"] = kwargs.get("exprs")

    def fake_extrude(comp: Any, prof: Any, distance: str, **kwargs: Any) -> str:
        seen["depth"] = distance
        return "seed"

    monkeypatch.setattr(features_axes, axis_fn, lambda comp, **kwargs: axis)
    monkeypatch.setattr(features_warp, "new_sketch", lambda comp, plane, *, label: SimpleNamespace(label=label))
    monkeypatch.setattr(features_warp, "_seed_rect", fake_seed)
    monkeypatch.setattr(features_warp, "profile", lambda sk: "prof")
    monkeypatch.setattr(features_warp, "extrude", fake_extrude)
    pat = MagicMock(name="pattern_rectangular", return_value="pat")
    monkeypatch.setattr(features_warp, "pattern_rectangular", pat)
    features._relief_family(
        MagicMock(name="comp"),
        MagicMock(name="bed"),
        _relief_body() if body is None else body,
        along,
        label="t",
        width_mm=0.8,
        depth_mm=0.3,
        pitch_mm=pitch_mm,
        **kw,
    )
    seen["pattern_distance"] = pat.call_args.args[4]
    seen["pattern_qty"] = pat.call_args.args[3]
    return seen


def test_literal_width_expr_reaches_seed_rect(monkeypatch: pytest.MonkeyPatch) -> None:
    # 100 mm span, centres -30/0/30 about 50 mm -> first groove centre 20 mm; width dims = centre -/+ (width)/2
    calls = _literal_calls(monkeypatch, "x", width_expr="warp_groove_width")
    assert len(calls) == 3
    assert calls[0][1]["exprs"] == {
        0: ("20.000000 mm - (warp_groove_width) / 2", "20.000000 mm + (warp_groove_width) / 2")
    }


def test_literal_no_expr_args_keep_literals(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _literal_calls(monkeypatch, "x")
    assert len(calls) == 3
    assert all(kw["exprs"] is None for _, kw in calls)


def test_depth_text_expr_or_literal_signed() -> None:
    assert features_warp._depth_text(1, 0.3, "warp_groove_depth") == "(warp_groove_depth)"
    assert features_warp._depth_text(-1, 0.3, "warp_groove_depth") == "-(warp_groove_depth)"
    assert features_warp._depth_text(1, 0.3, None) == "0.300000 mm"
    assert features_warp._depth_text(-1, 0.3, None) == "-0.300000 mm"


def test_seed_rect_exprs_replace_axis_corner_dims(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [MagicMock(name=f"l{i}") for i in range(4)]
    monkeypatch.setattr(features_warp, "rect", lambda s, a, b: lines)
    fix = MagicMock(name="fix_point")
    monkeypatch.setattr(features_warp, "fix_point", fix)
    features._seed_rect(MagicMock(name="sketch"), 14.6, 20.0, 15.4, 21.0, exprs={0: ("LO", "HI")})
    near, far = fix.call_args_list
    assert near.args[2] == ("LO", "20.000000 mm", "0.000000 mm")
    assert far.args[2] == ("HI", "21.000000 mm", "0.000000 mm")


def test_warp_relief_grooves_passes_expr_strings_to_literal_families(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _fake_fusion(monkeypatch)
    _patch_bed(monkeypatch)
    out = features.warp_relief_grooves(
        MagicMock(name="comp"),
        _cube3(),
        label="warp_t",
        width_mm=0.8,
        depth_mm=0.3,
        pitch_mm=30.0,
        width_expr="warp_groove_width",
        depth_expr="warp_groove_depth",
    )
    assert len(rec.rects) == 6
    assert all(len(kw["exprs"]) == 1 for _, kw in rec.rects)  # width dims only, no run_expr in literal mode
    assert sorted({next(iter(kw["exprs"])) for _, kw in rec.rects}) == [0, 1]  # x family -> axis 0, y -> axis 1
    assert rec.extrudes[0][1] == "(warp_groove_depth)"
    assert out == ["feat:warp_t_x", "feat:warp_t_y"]


# ---------------------------------------------------------------------------
# bed-direction corner mapping: all 6 beds x both families, real _seed_rect -> model coords of the 2 seed corners
# ---------------------------------------------------------------------------
def _cube_body() -> Any:
    # cm: 40 mm cube -> both in-plane spans 40 mm -> centres -15/+15 about 20 mm -> first centre 5 mm
    return SimpleNamespace(
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            maxPoint=SimpleNamespace(x=4.0, y=4.0, z=4.0),
        )
    )


_BED_FAMILIES = [
    (bed, along)
    for bed in ["-z", "+z", "-x", "+x", "-y", "+y"]
    for along in "xyz"
    if along != "xyz"[features.bed_axis(bed)[0]]
]


@pytest.mark.parametrize(("bed", "along"), _BED_FAMILIES)
def test_seed_corners_are_centre_pm_half_width_in_model_coords(
    monkeypatch: pytest.MonkeyPatch, bed: BedDir, along: Literal["x", "y", "z"]
) -> None:
    fix = MagicMock(name="fix_point")
    monkeypatch.setattr(features_warp, "rect", lambda s, a, b: [MagicMock(name=f"l{k}") for k in range(4)])
    monkeypatch.setattr(features_warp, "fix_point", fix)
    a, sgn = features.bed_axis(bed)
    bed_mm = 40.0 if sgn > 0 else 0.0  # cube: bed plane is the max or min face on the bed axis
    features_warp._literal_grooves(
        MagicMock(name="sketch"),
        _cube_body(),
        along,
        width_mm=0.8,
        pitch_mm=30.0,
        bed_dir=bed,
        bed_mm=bed_mm,
        width_expr="warp_groove_width",
        run_expr=None,
    )
    # first groove rect (centre 5 mm): corners a (bottom) and b (top), model (x, y, z)
    lo, hi = (c.args[2] for c in fix.call_args_list[:2])
    i = "xyz".index(along)  # spacing / width axis
    j = 3 - a - i  # run axis
    assert lo[a] == hi[a] == f"{bed_mm:.6f} mm"  # on the bed plane
    assert lo[i] == "5.000000 mm - (warp_groove_width) / 2"  # centre - w/2
    assert hi[i] == "5.000000 mm + (warp_groove_width) / 2"  # centre + w/2
    assert float(lo[j].split()[0]) == pytest.approx(-1.0)  # run axis: body min - 1 mm overrun
    assert float(hi[j].split()[0]) == pytest.approx(41.0)  # run axis: body max + 1 mm overrun


# ---------------------------------------------------------------------------
# live mode (length_expr / length_exprs): count, spacing, seed centre are Fusion expressions.
# Evaluated offline with fusion_kit.expr.evaluate (mm names); Fusion itself is not run here.
# ---------------------------------------------------------------------------
_LIVE_KW: dict[str, Any] = {
    "length_expr": "bracket_length",
    "margin_mm": 5.0,
    "margin_expr": "warp_groove_margin",
    "target_pitch_expr": "warp_groove_target_pitch",
    "run_expr": ("run_lo", "run_hi"),
}


def _span_body(span_mm: float) -> Any:
    cm = span_mm / 10
    return SimpleNamespace(
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0),
            maxPoint=SimpleNamespace(x=cm, y=cm),
        )
    )


def test_live_mode_expression_strings_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _family_capture(monkeypatch, "x", 1.0, **_LIVE_KW)
    qty = "floor((bracket_length - 2 * (warp_groove_margin)) / (warp_groove_target_pitch)) + 2"
    assert seen["pattern_qty"] == qty
    assert seen["pattern_distance"] == f"(bracket_length - 2 * (warp_groove_margin)) / (({qty}) - 1)"
    lo, hi = seen["rect"][0]
    assert lo == "(0.000000 mm + (warp_groove_margin)) - 0.400000 mm"  # centre - width/2, width literal here
    assert hi == "(0.000000 mm + (warp_groove_margin)) + 0.400000 mm"
    assert seen["rect"][1] == ("(run_lo) - 1.000000 mm", "(run_hi) + 1.000000 mm")  # run axis y: extent -/+ overrun


def test_live_mode_values_at_100_mm() -> None:
    names = {"bracket_length": 100.0, "warp_groove_margin": 5.0, "warp_groove_target_pitch": 30.0}
    qty = "floor((bracket_length - 2 * (warp_groove_margin)) / (warp_groove_target_pitch)) + 2"
    assert evaluate(qty, names) == 5  # floor(90 / 30) + 2
    assert evaluate(f"(bracket_length - 2 * (warp_groove_margin)) / (({qty}) - 1)", names) == pytest.approx(22.5)


@pytest.mark.parametrize("span_mm", [36.0, 40.0, 44.0, 48.0, 60.0, 100.0, 104.5])
@pytest.mark.parametrize("margin_mm", [3.0, 5.0])
@pytest.mark.parametrize("pitch_mm", [25.0, 30.0, 34.0])
def test_live_mode_expressions_hold_for_any_span(
    monkeypatch: pytest.MonkeyPatch, span_mm: float, margin_mm: float, pitch_mm: float
) -> None:
    seen = _family_capture(
        monkeypatch,
        "x",
        1.0,
        body=_span_body(span_mm),
        length_expr="bracket_length",
        margin_mm=margin_mm,
        margin_expr="warp_groove_margin",
        target_pitch_expr="warp_groove_target_pitch",
        run_expr=("run_lo", "run_hi"),
        pitch_mm=pitch_mm,
    )
    names = {
        "bracket_length": span_mm,
        "warp_groove_margin": margin_mm,
        "warp_groove_target_pitch": pitch_mm,
        "run_lo": 0.0,
        "run_hi": span_mm,
    }
    n = evaluate(seen["pattern_qty"], names)
    step = evaluate(seen["pattern_distance"], names)
    lo, hi = (evaluate(e, names) for e in seen["rect"][0])
    assert n >= 2 and n == int(n)
    assert step <= pitch_mm + 1e-9  # dfam: segments < 35 mm
    assert step < 35.0
    assert (lo + hi) / 2 == pytest.approx(margin_mm)  # first centre at margin from bed edge
    assert (lo + hi) / 2 + (n - 1) * step == pytest.approx(span_mm - margin_mm)  # last centre at margin from far edge
    run_lo, run_hi = (evaluate(e, names) for e in seen["rect"][1])  # run-axis seed ends: body extent -/+ overrun
    assert run_lo == pytest.approx(-1.0)
    assert run_hi == pytest.approx(span_mm + 1.0)


def test_live_mode_margin_below_edge_min_rejected() -> None:
    with pytest.raises(ValueError):
        features._relief_family(
            MagicMock(name="comp"),
            MagicMock(name="bed"),
            _span_body(100.0),
            "x",
            label="t",
            width_mm=0.8,
            depth_mm=0.3,
            pitch_mm=30.0,
            **{**_LIVE_KW, "margin_mm": GROOVE_EDGE_MIN_MM - 1.0},
        )


def test_live_mode_short_span_still_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    # live families ignore the 35 mm gate: span 30, margin 5 -> free 20 -> count 2, spacing 20 (<= pitch)
    seen = _family_capture(monkeypatch, "x", 1.0, body=_span_body(30.0), **_LIVE_KW)
    names = {
        "bracket_length": 30.0,
        "warp_groove_margin": 5.0,
        "warp_groove_target_pitch": 30.0,
        "run_lo": 0.0,
        "run_hi": 30.0,
    }
    assert evaluate(seen["pattern_qty"], names) == 2
    assert evaluate(seen["pattern_distance"], names) == pytest.approx(20.0)


def test_live_mode_without_run_expr_raises() -> None:
    kw = {k: v for k, v in _LIVE_KW.items() if k != "run_expr"}
    with pytest.raises(ValueError):  # no literal run-axis fallback
        features._relief_family(
            MagicMock(name="comp"),
            MagicMock(name="bed"),
            _span_body(100.0),
            "x",
            label="t",
            width_mm=0.8,
            depth_mm=0.3,
            pitch_mm=30.0,
            **kw,
        )


def test_warp_relief_grooves_routes_length_expr_per_family(monkeypatch: pytest.MonkeyPatch) -> None:
    body: Any = SimpleNamespace(
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            maxPoint=SimpleNamespace(x=10.0, y=10.0, z=1.0),
        )
    )
    _patch_bed(monkeypatch)
    rec = _fake_fusion(monkeypatch)
    live_calls: list[tuple[str, dict[str, Any]]] = []
    lit_calls: list[tuple[str, dict[str, Any]]] = []

    def fake_family(comp: Any, bed_plane: Any, b: Any, along: str, **kwargs: Any) -> list[str]:
        live_calls.append((along, kwargs))
        return ["f"]

    def fake_literal(s: Any, b: Any, along: str, **kwargs: Any) -> None:
        lit_calls.append((along, kwargs))

    monkeypatch.setattr(features_warp, "_relief_family", fake_family)
    monkeypatch.setattr(features_warp, "_literal_grooves", fake_literal)
    out = features.warp_relief_grooves(
        MagicMock(name="comp"),
        body,
        label="warp_t",
        width_mm=0.8,
        depth_mm=0.3,
        pitch_mm=30.0,
        length_exprs={"x": "top_flange_width"},
        run_exprs={"y": ("body_y_min", "body_y_max")},
        margin_mm=5.0,
        margin_expr="warp_groove_margin",
        target_pitch_expr="warp_groove_target_pitch",
    )
    assert {along: kw["length_expr"] for along, kw in live_calls} == {"x": "top_flange_width"}
    assert {along: kw["run_expr"] for along, kw in live_calls} == {"x": ("body_y_min", "body_y_max")}
    assert all(kw["margin_expr"] == "warp_groove_margin" for _, kw in live_calls)
    assert all(kw["target_pitch_expr"] == "warp_groove_target_pitch" for _, kw in live_calls)
    assert [along for along, _ in lit_calls] == ["y"]  # literal family joins the one sketch
    assert lit_calls[0][1]["run_expr"] is None  # run axis x is live-family-free here
    assert rec.sketches == ["warp_t_y_sketch"]
    assert out == ["f", "feat:warp_t_y"]


def test_live_seed_centre_starts_from_run_low_edge_when_both_families_live(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both families live: run_exprs names x and y. Family x runs along y and family y along x, so run_exprs[x] is the
    # low edge of family y's spacing axis (and vice versa). Seed centre must start from that expr, else a live edit of
    # the low edge moves the body while the first groove stays at the literal position.
    body: Any = SimpleNamespace(
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            maxPoint=SimpleNamespace(x=10.0, y=10.0, z=1.0),
        )
    )
    plane = SimpleNamespace(geometry=SimpleNamespace(normal=SimpleNamespace(x=0.0, y=0.0, z=1.0)))
    monkeypatch.setattr(features_warp, "axis_plane", lambda comp, axis, expr, value_mm, label: plane)
    monkeypatch.setattr(features_warp, "set_bed", lambda body, bed: None)
    monkeypatch.setattr(features_warp, "new_sketch", lambda comp, plane, *, label: SimpleNamespace(label=label))
    monkeypatch.setattr(features_warp, "profile", lambda sk: "prof")
    monkeypatch.setattr(features_warp, "extrude", lambda comp, prof, distance, **kwargs: "seed")
    monkeypatch.setattr(features_warp, "pattern_rectangular", MagicMock(name="pattern_rectangular", return_value="pat"))
    seeds: list[Any] = []
    monkeypatch.setattr(features_warp, "_seed_rect", lambda sk, *corners, **kw: seeds.append(kw["exprs"]))
    features.warp_relief_grooves(
        MagicMock(name="comp"),
        body,
        label="warp_t",
        pitch_mm=30.0,
        length_exprs={"x": "L_x", "y": "L_y"},
        run_exprs={"x": ("lo_x", "hi_x"), "y": ("lo_y", "hi_y")},
        margin_mm=5.0,
        margin_expr="warp_groove_margin",
        target_pitch_expr="warp_groove_target_pitch",
    )
    # families build in order x, y: family x spaces along x (axis 0), family y along y (axis 1)
    assert seeds[0][0][0] == "((lo_x) + (warp_groove_margin)) - 0.400000 mm"
    assert seeds[1][1][0] == "((lo_y) + (warp_groove_margin)) - 0.400000 mm"


def test_warp_relief_grooves_rejects_bad_length_exprs() -> None:
    with pytest.raises(ValueError):  # margin_expr / target_pitch_expr missing
        features.warp_relief_grooves(
            MagicMock(name="comp"),
            _span_body(100.0),
            label="t",
            pitch_mm=30.0,
            length_exprs={"x": "a"},
        )
    with pytest.raises(ValueError):  # z is the bed normal for bed "-z": not a groove family
        features.warp_relief_grooves(
            MagicMock(name="comp"),
            _span_body(100.0),
            label="t",
            pitch_mm=30.0,
            length_exprs={"z": "a"},
            margin_expr="m",
            target_pitch_expr="p",
        )
    with pytest.raises(ValueError):  # live x runs along y: run_exprs must name y (no literal fallback)
        features.warp_relief_grooves(
            MagicMock(name="comp"),
            _span_body(100.0),
            label="t",
            pitch_mm=30.0,
            length_exprs={"x": "a"},
            margin_expr="m",
            target_pitch_expr="p",
        )
    with pytest.raises(ValueError):  # run_exprs key x: run axis of no live family
        features.warp_relief_grooves(
            MagicMock(name="comp"),
            _span_body(100.0),
            label="t",
            pitch_mm=30.0,
            length_exprs={"x": "a"},
            run_exprs={"y": ("l", "h"), "x": ("l", "h")},
            margin_expr="m",
            target_pitch_expr="p",
        )


def test_no_family_built_creates_no_bed_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    # literal mode, both spans 30 mm (<= 35): no family builds -> no orphan bed plane; body still gets its bed attr
    body: Any = SimpleNamespace(
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            maxPoint=SimpleNamespace(x=3.0, y=3.0, z=1.0),
        )
    )
    plane_fn = MagicMock(name="axis_plane")
    bed_fn = MagicMock(name="set_bed")
    monkeypatch.setattr(features_warp, "axis_plane", plane_fn)
    monkeypatch.setattr(features_warp, "set_bed", bed_fn)
    out = features.warp_relief_grooves(MagicMock(name="comp"), body, label="t", pitch_mm=30.0)
    assert out == []
    plane_fn.assert_not_called()
    bed_fn.assert_called_once()


def _two_axis_body() -> Any:
    return SimpleNamespace(
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            maxPoint=SimpleNamespace(x=100.0, y=100.0, z=1.0),
        )
    )


def test_expr_only_resolves_float_twins_from_params(monkeypatch: pytest.MonkeyPatch) -> None:
    # no *_mm passed: initial-placement / validation floats come from user params via expr.evaluate
    plane = SimpleNamespace(geometry=SimpleNamespace(normal=SimpleNamespace(x=0.0, y=0.0, z=1.0)))
    names = {
        "warp_groove_width": 0.4,
        "warp_groove_depth": 0.3,
        "warp_groove_margin": 5.0,
        "warp_groove_target_pitch": 30.0,
        "top_flange_width": 100.0,
    }
    monkeypatch.setattr(features_warp, "eval_fresh", _fresh_mm(names))
    monkeypatch.setattr(features_warp, "axis_plane", lambda comp, axis, expr, value_mm, label: plane)
    monkeypatch.setattr(features_warp, "set_bed", lambda body, bed: None)
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_family(comp: Any, bed_plane: Any, b: Any, along: str, **kwargs: Any) -> list[str]:
        calls.append((along, kwargs))
        return ["f"]

    monkeypatch.setattr(features_warp, "_relief_family", fake_family)
    _fake_fusion(monkeypatch)  # literal family y: its rects + cut go through the fakes
    features.warp_relief_grooves(
        MagicMock(name="comp"),
        _two_axis_body(),
        label="warp_t",
        width_expr="warp_groove_width * 2",
        depth_expr="warp_groove_depth",
        length_exprs={"x": "top_flange_width"},
        run_exprs={"y": ("0 mm", "100 mm")},
        margin_expr="warp_groove_margin",
        target_pitch_expr="warp_groove_target_pitch",
    )
    kw = dict(calls)["x"]
    assert kw["width_mm"] == pytest.approx(
        0.8
    )  # 0.4 * 2 from expr; default 0.8 would also pass -> depth/margin prove it
    assert kw["depth_mm"] == pytest.approx(0.3)  # default would be 0.6
    assert kw["margin_mm"] == pytest.approx(5.0)  # default would be 0.0
    assert kw["pitch_mm"] == pytest.approx(30.0)
    assert kw["width_expr"] == "warp_groove_width * 2"


def test_expr_only_margin_below_edge_min_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    plane = SimpleNamespace(geometry=SimpleNamespace(normal=SimpleNamespace(x=0.0, y=0.0, z=1.0)))
    names = {"warp_groove_margin": 2.0, "warp_groove_target_pitch": 30.0, "top_flange_width": 100.0}
    monkeypatch.setattr(features_warp, "eval_fresh", _fresh_mm(names))
    monkeypatch.setattr(features_warp, "axis_plane", lambda comp, axis, expr, value_mm, label: plane)
    monkeypatch.setattr(features_warp, "set_bed", lambda body, bed: None)
    with pytest.raises(ValueError, match="GROOVE_EDGE_MIN_MM"):
        features.warp_relief_grooves(
            MagicMock(name="comp"),
            _two_axis_body(),
            label="warp_t",
            length_exprs={"x": "top_flange_width"},
            run_exprs={"y": ("0 mm", "100 mm")},
            margin_expr="warp_groove_margin",
            target_pitch_expr="warp_groove_target_pitch",
        )
