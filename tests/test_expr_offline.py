"""Tests for fusion_kit/expr.py — boundary, precedence, value/expression consistency."""

import math
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fusion_kit.expr import Ex, deg, evaluate, near, trig


# ---------------------------------------------------------------------------
# Tiny mm-only expression evaluator (test helper only)
# ---------------------------------------------------------------------------
def eval_expr(e: str) -> float:
    """Evaluate a Fusion expression string that uses only mm / deg / trig."""
    s = re.sub(
        r"([-+]?\d+(?:\.\d+)?)\s+deg",
        lambda m: str(math.radians(float(m.group(1)))),
        e,
    )
    s = re.sub(r"([-+]?\d+(?:\.\d+)?)\s+mm", r"\1", s)
    s = re.sub(r"\b(sin|cos|tan)\b", r"math.\1", s)
    return eval(s, {"math": math})


# ---------------------------------------------------------------------------
# Helpers to build non-const Ex nodes (bypass const-fold so we see expressions)
# ---------------------------------------------------------------------------
def nc(label: str, v: float) -> Ex:
    """Non-const Ex backed by a mm literal label."""
    return Ex(f"{v} mm", v, False)


# ---------------------------------------------------------------------------
# 1. Add — smoke + const-fold
# ---------------------------------------------------------------------------
def test_add_smoke():
    r = Ex.mm(5) + Ex.mm(3)
    assert r.v == 8
    assert r.e == "8 mm"  # const-folded literal


# ---------------------------------------------------------------------------
# 2. Sub — non-const value correct
# ---------------------------------------------------------------------------
def test_sub_value():
    a, b = nc("a", 7), nc("b", 3)
    r = a - b
    assert r.v == 4


# ---------------------------------------------------------------------------
# 3. Sub — expression parenthesisation preserves precedence
#    a - (b - c) must not collapse to a - b - c
# ---------------------------------------------------------------------------
def test_sub_expr_parens_preserve_precedence():
    a = nc("a", 10)
    b = nc("b", 3)
    c = nc("c", 1)
    result = a - (b - c)  # python: 10 - (3-1) = 8
    assert result.v == 8
    assert near(eval_expr(result.e), result.v)


# ---------------------------------------------------------------------------
# 4. Rsub — float - Ex order
# ---------------------------------------------------------------------------
def test_rsub_float_order():
    ex = nc("ex", 5)
    r = 10.0 - ex
    assert r.v == 5.0
    assert near(eval_expr(r.e), r.v)


# ---------------------------------------------------------------------------
# 5. Mul scalar — value correct
# ---------------------------------------------------------------------------
def test_mul_scalar_value():
    ex = nc("ex", 6)
    r = ex * 3
    assert r.v == 18


# ---------------------------------------------------------------------------
# 6. Mul Ex*Ex — expression has parens around both operands
# ---------------------------------------------------------------------------
def test_mul_ex_ex_parens():
    a = nc("a", 4)
    b = nc("b", 5)
    r = a * b
    assert r.v == 20
    # expression must wrap both sides so a complex sub-expression stays safe
    assert r.e.startswith("(") and ") * (" in r.e and r.e.endswith(")")


# ---------------------------------------------------------------------------
# 7. Div — value correct
# ---------------------------------------------------------------------------
def test_div_value():
    ex = nc("ex", 12)
    r = ex / 4
    assert r.v == 3.0
    assert near(eval_expr(r.e), r.v)


# ---------------------------------------------------------------------------
# 8. Const-fold suppressed when one operand is non-const
# ---------------------------------------------------------------------------
def test_const_fold_suppressed_when_non_const():
    a = Ex.mm(3)  # const
    b = nc("b", 5)  # non-const
    r = a + b
    assert not r.const
    assert "+" in r.e  # expression preserved, not a bare literal


# ---------------------------------------------------------------------------
# 9. Near boundary — exclusive < 1e-3
# ---------------------------------------------------------------------------
def test_near_just_inside():
    assert near(0, 0.0009) is True


def test_near_just_outside():
    assert near(0, 0.001) is False  # exactly 1e-3 is outside (strict <)


# ---------------------------------------------------------------------------
# 10. Deg — value in radians
# ---------------------------------------------------------------------------
def test_deg_value():
    d = deg(90)
    assert near(d.v, math.pi / 2)


# ---------------------------------------------------------------------------
# 11. Trig — sin(90°) ≈ 1
# ---------------------------------------------------------------------------
def test_trig_sin():
    r = trig("sin", deg(90))
    assert near(r.v, 1.0)


# ---------------------------------------------------------------------------
# 12. Expression evaluates to same float as .v (compound expression)
# ---------------------------------------------------------------------------
def test_expr_consistency_compound():
    a = Ex("10.0 mm", 10, False)
    b = Ex("3.0 mm", 3, False)
    c = Ex("1.0 mm", 1, False)
    result = (a - b) * 2 + c  # (10-3)*2 + 1 = 15
    assert result.v == 15
    assert near(eval_expr(result.e), result.v)


# ---------------------------------------------------------------------------
# 13. Deg arithmetic keeps unit through const folding
# ---------------------------------------------------------------------------
def test_deg_add_folds_in_deg():
    r = deg(30) + deg(15)
    assert r.e == "45.0 deg" and r.unit == "deg"
    assert near(eval_expr(r.e), math.radians(45))


def test_deg_mul_folds_in_deg():
    r = deg(30) * 2
    assert r.e.endswith(" deg") and near(eval_expr(r.e), math.radians(60))


def test_deg_neg_folds_in_deg():
    r = -deg(30)
    assert r.e.endswith(" deg") and near(eval_expr(r.e), math.radians(-30))


def test_mm_plus_deg_raises():

    with pytest.raises(ValueError):
        Ex.mm(5) + deg(30)
    with pytest.raises(ValueError):
        deg(30) - 1.0


# ---------------------------------------------------------------------------
# evaluate(): Fusion unit suffixes -> mm (lengths) / radians (angles)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("expr", "mm"),
    [
        ("2 mm", 2.0),
        ("2 cm", 20.0),
        ("1 in", 25.4),
        ("1 ft", 304.8),
        ("0.5 m", 500.0),
        ("2 * gap + 1 cm", 16.0),
    ],
)
def test_evaluate_length_units_to_mm(expr: str, mm: float) -> None:
    assert evaluate(expr, {"gap": 3.0}) == pytest.approx(mm)


def test_evaluate_angle_units_to_radians() -> None:
    assert evaluate("90 deg", {}) == pytest.approx(math.pi / 2)
    assert evaluate("1 rad", {}) == pytest.approx(1.0)
