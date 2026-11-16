"""Offline tests for fusion_kit._vec: dot/cross/sub/scale/norm/unit and zero-vector behaviour of _unit vs _unit_strict."""

from __future__ import annotations

import pytest

from fusion_kit._vec import _cross, _dot, _norm, _scale, _sub, _unit, _unit_strict


def test_dot_orthogonal_axes_zero() -> None:
    assert _dot((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)) == 0.0
    assert _dot((1.0, 2.0, 3.0), (4.0, 5.0, 6.0)) == 32.0


def test_cross_right_hand_axes() -> None:
    assert _cross((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)) == (0.0, 0.0, 1.0)
    assert _cross((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)) == (1.0, 0.0, 0.0)
    assert _cross((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)) == (0.0, 1.0, 0.0)


def test_cross_orthogonal_to_inputs_and_antisymmetric() -> None:
    a = (1.0, -2.0, 0.5)
    b = (0.3, 4.0, -1.5)
    c = _cross(a, b)
    assert _dot(c, a) == pytest.approx(0.0, abs=1e-12)
    assert _dot(c, b) == pytest.approx(0.0, abs=1e-12)
    assert _cross(b, a) == pytest.approx(tuple(-x for x in c))


def test_sub_and_scale() -> None:
    assert _sub((1.0, 2.0, 3.0), (0.5, 0.5, 0.5)) == (0.5, 1.5, 2.5)
    assert _scale((1.0, -2.0, 0.0), 3.0) == (3.0, -6.0, 0.0)


def test_norm_length() -> None:
    assert _norm((3.0, 4.0, 0.0)) == 5.0
    assert _norm((1.0, 2.0, 2.0)) == 3.0
    assert _norm((0.0, 0.0, 0.0)) == 0.0


def test_unit_normalises() -> None:
    u = _unit((0.0, 3.0, 4.0))
    assert u == pytest.approx((0.0, 0.6, 0.8))
    assert _norm(u) == pytest.approx(1.0)


def test_unit_zero_vector_returns_zero() -> None:
    assert _unit((0.0, 0.0, 0.0)) == (0.0, 0.0, 0.0)
    assert _unit((1e-13, 0.0, 0.0)) == (0.0, 0.0, 0.0)  # below 1e-12 threshold


def test_unit_strict_normalises() -> None:
    assert _unit_strict((0.0, 0.0, -2.0)) == pytest.approx((0.0, 0.0, -1.0))


@pytest.mark.parametrize("v", [(0.0, 0.0, 0.0), (1e-13, 0.0, 0.0)])
def test_unit_strict_zero_raises(v: tuple[float, float, float]) -> None:
    with pytest.raises(ValueError, match="zero-length vector"):
        _unit_strict(v)


def test_unit_and_strict_agree_on_nonzero() -> None:
    v = (0.2, -1.1, 3.7)
    assert _unit(v) == pytest.approx(_unit_strict(v), rel=1e-15)
