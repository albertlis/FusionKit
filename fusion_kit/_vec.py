"""Pure 3-vector helpers on plain float tuples. Stdlib only, no adsk. Private: shared by dfam, printability, features, joints.

Numerics match the copies they replace: _norm = sqrt(dot(a, a)); _unit multiplies by 1/n (zero -> (0, 0, 0));
_unit_strict divides by n and raises ValueError on zero-length (features behaviour).
"""

from __future__ import annotations

import math


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a: tuple[float, float, float], k: float) -> tuple[float, float, float]:
    return (a[0] * k, a[1] * k, a[2] * k)


def _cross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm(a: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: tuple[float, float, float]) -> tuple[float, float, float]:
    """Unit vector; zero-length (|a| < 1e-12) -> (0, 0, 0)."""
    n = _norm(a)
    return (0.0, 0.0, 0.0) if n < 1e-12 else _scale(a, 1.0 / n)


def _unit_strict(v: tuple[float, float, float]) -> tuple[float, float, float]:
    """Unit vector; zero-length (|v| < 1e-12) -> ValueError."""
    ln = _norm(v)
    if ln < 1e-12:
        raise ValueError(f"zero-length vector: {v!r}")
    return (v[0] / ln, v[1] / ln, v[2] / ln)
