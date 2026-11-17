"""Offline tests for the fin count / root overlap rule in fusion_kit.grip_fit (no Fusion runtime, adsk stubs from conftest).

Pins: float twin of fin_corners matches the expression version; fin footprint on the relief circle; fin_gap_mm /
max_fin_count; grip_fit raises ValueError before any Fusion call when fin_count exceeds max_fin_count.
"""

import math
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit.expr import evaluate
from fusion_kit.grip_fit import (
    FIN_GAP_MM,
    GripKind,
    _fin_quad_mm,
    fin_corners,
    fin_footprint_rad,
    fin_gap_mm,
    grip_fit,
    max_fin_count,
)

LEAN_45 = math.radians(45.0)
HOLE_CASE: tuple[GripKind, float, float] = ("hole", 22.2, 1.2)
SHAFT_CASE: tuple[GripKind, float, float] = ("shaft", 8.0, 0.8)


def test_fin_quad_float_twin_matches_fin_corners() -> None:
    for kind, diameter, relief in (HOLE_CASE, SHAFT_CASE):
        expr = fin_corners(kind, f"{diameter} mm", "0.6 mm", "0.8 mm", f"{relief} mm", "45 deg")
        got = _fin_quad_mm(kind, diameter, relief, 0.8, 0.6, LEAN_45)
        for (er, et), (fr, ft) in zip(expr, got, strict=True):
            assert math.isclose(evaluate(er, {}), fr, abs_tol=1e-9)
            assert math.isclose(evaluate(et, {}), ft, abs_tol=1e-9)


def test_footprint_lies_on_relief_circle_inside_quad() -> None:
    kind, diameter, relief = SHAFT_CASE
    rho = diameter / 2 - relief
    lo, hi = fin_footprint_rad(kind, diameter, relief, 0.8, 0.6, LEAN_45, rho)
    assert lo < hi
    mid = (lo + hi) / 2
    assert math.hypot(rho * math.cos(mid), rho * math.sin(mid)) == pytest.approx(rho)
    # the footprint mid point is a real fin point: its radial coord lies inside the quad radial span
    quad = _fin_quad_mm(kind, diameter, relief, 0.8, 0.6, LEAN_45)
    r_lo = min(p[0] for p in quad)
    r_hi = max(p[0] for p in quad)
    assert r_lo <= rho * math.cos(mid) <= r_hi


def test_shaft_d8_relief08_16_fins_overlap_is_refused() -> None:
    kind, diameter, relief = SHAFT_CASE
    m = max_fin_count(diameter, kind, relief, 0.8, 0.6, LEAN_45)
    assert m < 16, f"Ø8 shaft max fin count {m}"
    assert fin_gap_mm(16, kind, diameter, relief, 0.8, 0.6, LEAN_45) < FIN_GAP_MM


def test_shaft_d8_grip_fit_16_fins_raises_before_fusion() -> None:
    comp: Any = MagicMock(name="comp")
    body: Any = MagicMock(name="body")
    m = max_fin_count(8.0, "shaft", 0.8, 0.8, 0.6, LEAN_45)
    with pytest.raises(ValueError, match=rf"fin_count 16 exceeds max {m}\b"):
        grip_fit(
            comp,
            body,
            ("0 mm", "0 mm"),
            "-8.5 mm",
            "7 mm",
            "8 mm",
            "shaft",
            axis="x",
            entry="low",
            relief="0.8 mm",
            fin_count="16",
            label="c_grip",
        )
    comp.features.assert_not_called()


def test_hole_d222_relief12_16_fins_passes() -> None:
    kind, diameter, relief = HOLE_CASE
    m = max_fin_count(diameter, kind, relief, 0.8, 0.6, LEAN_45)
    assert m >= 16, f"Ø22.2 hole max fin count {m}"
    assert fin_gap_mm(16, kind, diameter, relief, 0.8, 0.6, LEAN_45) >= FIN_GAP_MM


def test_max_fin_count_is_the_largest_legal_count() -> None:
    for kind, diameter, relief in (SHAFT_CASE, HOLE_CASE):
        m = max_fin_count(diameter, kind, relief, 0.8, 0.6, LEAN_45)
        assert fin_gap_mm(m, kind, diameter, relief, 0.8, 0.6, LEAN_45) >= FIN_GAP_MM
        assert fin_gap_mm(m + 1, kind, diameter, relief, 0.8, 0.6, LEAN_45) < FIN_GAP_MM


def test_report_maxima() -> None:
    shaft = max_fin_count(8.0, "shaft", 0.8, 0.8, 0.6, LEAN_45)
    hole = max_fin_count(22.2, "hole", 1.2, 0.8, 0.6, LEAN_45)
    print(f"MAX_FIN_COUNT shaft d8 relief0.8 t0.8 i0.6 lean45 = {shaft}")
    print(f"MAX_FIN_COUNT hole d22.2 relief1.2 t0.8 i0.6 lean45 = {hole}")
    for rho in (3.2, 3.5, 3.9):
        lo, hi = fin_footprint_rad("shaft", 8.0, 0.8, 0.8, 0.6, LEAN_45, rho)
        print(f"FOOTPRINT shaft d8 rho {rho}: {math.degrees(lo):.2f}..{math.degrees(hi):.2f} deg")
    print(f"GAP14 shaft rho3.2 = {fin_gap_mm(14, 'shaft', 8.0, 0.8, 0.8, 0.6, LEAN_45):.3f} mm")
