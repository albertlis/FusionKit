"""Offline tests for fusion_kit.features.groove_positions (pure function, no Fusion)."""

import itertools
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import MagicMock

import pytest

from fusion_kit import features
from fusion_kit.enums import IDENTICAL_PATTERN
from fusion_kit.features import groove_positions


def test_span_at_or_below_35_has_no_grooves() -> None:
    assert groove_positions(35.0) == []
    assert groove_positions(10.0) == []


def test_span_just_over_35_gives_one_centred_groove() -> None:
    assert groove_positions(36.0) == [0.0]


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


# ---------------------------------------------------------------------------
# _relief_family: pattern step follows Fusion's built axis direction
# ---------------------------------------------------------------------------
def _relief_body() -> Any:
    # bbox in cm: 100 mm x 100 mm -> both families span 100 mm -> centres -30, 0, 30 mm
    return SimpleNamespace(
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0),
            maxPoint=SimpleNamespace(x=10.0, y=10.0),
        )
    )


@pytest.mark.parametrize(
    ("along", "axis_fn", "dir_sign", "want_distance"),
    [
        ("x", "axis_x_through", 1.0, "30.000000 mm"),
        ("x", "axis_x_through", -1.0, "-30.000000 mm"),
        ("y", "axis_y_through", 1.0, "30.000000 mm"),
        ("y", "axis_y_through", -1.0, "-30.000000 mm"),
    ],
)
def test_relief_family_negates_pitch_on_negative_axis(
    monkeypatch: pytest.MonkeyPatch,
    along: Literal["x", "y"],
    axis_fn: str,
    dir_sign: float,
    want_distance: str,
) -> None:
    direction = SimpleNamespace(x=0.0, y=0.0, z=0.0)
    setattr(direction, along, dir_sign)  # only the family's own axis component carries the sign
    axis = SimpleNamespace(geometry=SimpleNamespace(direction=direction))
    monkeypatch.setattr(features, axis_fn, lambda comp, **kwargs: axis)
    monkeypatch.setattr(features, "new_sketch", lambda comp, plane, *, label: SimpleNamespace(label=label))
    monkeypatch.setattr(features, "_seed_rect", lambda sk, *corners, **kw: None)
    monkeypatch.setattr(features, "profile", lambda sk: "prof")
    monkeypatch.setattr(features, "extrude", lambda comp, prof, distance, **kwargs: "seed")
    pat = MagicMock(name="pattern_rectangular", return_value="pat")
    monkeypatch.setattr(features, "pattern_rectangular", pat)

    out = features._relief_family(
        MagicMock(name="comp"),
        MagicMock(name="bed"),
        _relief_body(),
        along,
        label="t",
        width_mm=0.8,
        depth_mm=0.3,
        pitch_mm=30.0,
    )

    args = pat.call_args.args
    assert args[2] is axis
    assert args[3] == "3"
    assert args[4] == want_distance
    assert out == ["seed", "pat"]


# ---------------------------------------------------------------------------
# _relief_family: pattern compute + seed centre (regression for live Fusion bugs)
# ---------------------------------------------------------------------------
def _run_family(
    monkeypatch: pytest.MonkeyPatch, along: Literal["x", "y"], corners_out: list[tuple[Any, ...]]
) -> MagicMock:
    axis_fn = "axis_x_through" if along == "x" else "axis_y_through"
    axis = SimpleNamespace(geometry=SimpleNamespace(direction=SimpleNamespace(x=1.0, y=1.0, z=0.0)))
    monkeypatch.setattr(features, axis_fn, lambda comp, **kwargs: axis)
    monkeypatch.setattr(features, "new_sketch", lambda comp, plane, *, label: SimpleNamespace(label=label))
    monkeypatch.setattr(features, "_seed_rect", lambda sk, *corners, **kw: corners_out.append(corners))
    monkeypatch.setattr(features, "profile", lambda sk: "prof")
    monkeypatch.setattr(features, "extrude", lambda comp, prof, distance, **kwargs: "seed")
    pat = MagicMock(name="pattern_rectangular", return_value="pat")
    monkeypatch.setattr(features, "pattern_rectangular", pat)
    features._relief_family(
        MagicMock(name="comp"),
        MagicMock(name="bed"),
        _relief_body(),
        along,
        label="t",
        width_mm=0.8,
        depth_mm=0.3,
        pitch_mm=30.0,
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
    monkeypatch.setattr(features, "rect", lambda s, a, b: lines)
    fix = MagicMock(name="fix_point")
    monkeypatch.setattr(features, "fix_point", fix)
    features._seed_rect(MagicMock(name="sketch"), 14.6, 20.0, 15.4, 21.0)
    assert fix.call_count == 2
    near, far = fix.call_args_list
    assert near.args[1] is lines[0].startSketchPoint  # bottom start = corner a
    assert far.args[1] is lines[2].startSketchPoint  # top start = corner b
    assert [float(v.split()[0]) for v in near.args[2][:2]] == pytest.approx([14.6, 20.0])
    assert [float(v.split()[0]) for v in far.args[2][:2]] == pytest.approx([15.4, 21.0])


def test_x_and_y_seed_centres_equal_expected_coordinate(monkeypatch: pytest.MonkeyPatch) -> None:
    # 100 mm span, pitch 30 -> centres -30/0/30 about body centre 50 -> first groove centre 20 mm for BOTH families.
    corners_x: list[tuple[Any, ...]] = []
    corners_y: list[tuple[Any, ...]] = []
    _run_family(monkeypatch, "x", corners_x)
    _run_family(monkeypatch, "y", corners_y)
    cx = corners_x[0]  # _seed_rect(s, x0, y0, x1, y1), mm floats
    cy = corners_y[0]
    centre_x = (cx[0] + cx[2]) / 2  # X family spaces along x
    centre_y = (cy[1] + cy[3]) / 2  # Y family spaces along y
    assert centre_x == pytest.approx(20.0)
    assert centre_y == pytest.approx(20.0)
    assert centre_x == pytest.approx(centre_y)
