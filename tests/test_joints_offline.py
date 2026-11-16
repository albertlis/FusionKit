"""Offline tests for fusion_kit.joints: dovetail_profile geometry, _is_axial, mark_mating attribute tagging.

No Fusion runtime: adsk stubs come from conftest.py; faces are MagicMocks. dovetail() (Fusion adapter) is not exercised.
"""

import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fusion_kit import features, joints, sketch
from fusion_kit.joints import _is_axial, dovetail_profile, mark_mating

CASES = [
    (10.0, 8.0, 15.0, 0.2),
    (6.0, 4.0, 30.0, 0.1),
    (12.0, 10.0, 0.0, 0.3),
    (8.0, 5.0, 45.0, 0.0),
    (10.0, 8.0, 30.0, 0.2),
]


def _right_flank_x(poly: list[tuple[float, float]], y: float) -> float:
    """x of the right flank (vertex 1 -> vertex 2) at height y, linear."""
    (x0, y0), (x1, y1) = poly[1], poly[2]
    return x0 + (x1 - x0) * (y - y0) / (y1 - y0)


def _width_at(poly: list[tuple[float, float]], y: float) -> float:
    return 2 * _right_flank_x(poly, y)


def _perp_dist(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Perpendicular distance from point p to the infinite line through a and b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    return abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / math.hypot(dx, dy)


@pytest.mark.parametrize(("w", "d", "ang", "c"), CASES)
def test_female_wider_by_2c_over_cos_at_every_y(w: float, d: float, ang: float, c: float) -> None:
    male, female = dovetail_profile(w, d, ang, c)
    h = c / math.cos(math.radians(ang))  # horizontal flank shift per side
    for i in range(11):
        y = d * i / 10
        assert _width_at(female, y) - _width_at(male, y) == pytest.approx(2 * h, abs=1e-9)


@pytest.mark.parametrize(("w", "d", "ang", "c"), CASES)
def test_flank_gap_is_perpendicular_clearance(w: float, d: float, ang: float, c: float) -> None:
    male, female = dovetail_profile(w, d, ang, c)
    # (male edge a, male edge b, female edge a, female edge b) per flank; right then left
    flanks = (
        (male[1], male[2], female[1], female[2]),
        (male[0], male[3], female[0], female[3]),
    )
    for m_a, m_b, f_a, f_b in flanks:
        assert _perp_dist(f_a, m_a, m_b) == pytest.approx(c, abs=1e-6)
        assert _perp_dist(f_b, m_a, m_b) == pytest.approx(c, abs=1e-6)


@pytest.mark.parametrize(("w", "d", "ang", "c"), CASES)
def test_tip_deeper_by_clearance(w: float, d: float, ang: float, c: float) -> None:
    male, female = dovetail_profile(w, d, ang, c)
    assert max(y for _, y in female) - max(y for _, y in male) == pytest.approx(c, abs=1e-9)
    assert min(y for _, y in female) == 0.0  # base flush at y=0


@pytest.mark.parametrize(("w", "d", "ang", "c"), CASES)
def test_flank_angle_from_depth_axis(w: float, d: float, ang: float, c: float) -> None:
    male, female = dovetail_profile(w, d, ang, c)
    for poly in (male, female):
        (x0, y0), (x1, y1) = poly[1], poly[2]
        assert math.degrees(math.atan2(x1 - x0, y1 - y0)) == pytest.approx(ang, abs=1e-9)


@pytest.mark.parametrize(("w", "d", "ang", "c"), CASES)
def test_male_base_width_and_tip(w: float, d: float, ang: float, c: float) -> None:
    male, _ = dovetail_profile(w, d, ang, c)
    assert male[0] == (-w / 2, 0.0)
    assert male[1] == (w / 2, 0.0)
    assert _width_at(male, d) == pytest.approx(w + 2 * d * math.tan(math.radians(ang)), abs=1e-9)


def test_zero_clearance_socket_equals_tail() -> None:
    male, female = dovetail_profile(10.0, 8.0, 15.0, 0.0)
    assert male == female


@pytest.mark.parametrize(
    ("w", "d", "ang", "c"),
    [(0.0, 8.0, 15.0, 0.2), (10.0, 0.0, 15.0, 0.2), (10.0, 8.0, 90.0, 0.2), (10.0, 8.0, 15.0, -0.1)],
)
def test_invalid_inputs_raise(w: float, d: float, ang: float, c: float) -> None:
    with pytest.raises(ValueError):
        dovetail_profile(w, d, ang, c)


def test_is_axial() -> None:
    assert _is_axial((0.0, 0.0, 2.0), (0.0, 0.0, -1.0))
    assert not _is_axial((1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    assert not _is_axial((0.0, 0.0, 0.0), (0.0, 0.0, 1.0))


def test_mark_mating_tags_each_face() -> None:
    faces = [MagicMock(name="face_a"), MagicMock(name="face_b")]
    mark_mating(faces)
    for f in faces:
        f.attributes.add.assert_called_once_with("dfam", "mating", "1")


def test_mark_mating_empty_is_noop() -> None:
    mark_mating([])  # must not raise


def test_dovetail_length_has_no_scientific_notation(monkeypatch: pytest.MonkeyPatch) -> None:
    lengths: list[str] = []

    def fake_extrude(comp: MagicMock, prof: MagicMock, length: str, **kw: object) -> MagicMock:
        lengths.append(length)
        return MagicMock()

    monkeypatch.setattr(features, "extrude", fake_extrude)
    monkeypatch.setattr(features, "profile", lambda sk, **kw: MagicMock())
    monkeypatch.setattr(features, "convex_edges", lambda body, edges, **kw: [])
    monkeypatch.setattr(sketch, "to_sketch", lambda sk, x, y, z: SimpleNamespace(x=x, y=y, z=z))
    monkeypatch.setattr(joints, "_sketch_normal", lambda sk: (0.0, 0.0, 1.0))
    joints.dovetail(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), (0.0, 0.0, 0.0), 10.0, 5.0, 1e-5, label="dovetail_t"
    )
    assert lengths == ["0.000010 mm", "0.000010 mm"]  # 1e-05 would be rejected by Fusion expressions
