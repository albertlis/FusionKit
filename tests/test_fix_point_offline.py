"""Offline tests for fusion_kit.sketch.fix_point: a point on the wrong side of a sketch axis is moved to the target side.

No Fusion runtime: adsk stubs and Point3D fake come from conftest.py (point3d_xyz fixture).
mm(), _sketch_dims() and anchor() are monkeypatched; the sketch maps model (x, y, z) -> sketch (-z, y, 0),
the live-probed mapping of a seed rect on the X=0 plane (sketch x = -model Z).
Sketch coords are cm; flip jitter = fk_sketch.RECT_JITTER (0.04 cm).
"""

from types import SimpleNamespace
from typing import Any

import pytest

import fusion_kit.sketch as fk_sketch
from fusion_kit.sketch import fix_point


class _Vec:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.x, self.y, self.z = x, y, z

    @property
    def length(self) -> float:
        return (self.x**2 + self.y**2 + self.z**2) ** 0.5


class _Pt:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.x, self.y, self.z = x, y, z

    def vectorTo(self, other: "_Pt") -> _Vec:
        return _Vec(other.x - self.x, other.y - self.y, other.z - self.z)


class _SketchPoint:
    """Fake SketchPoint: move() applies the translation unless refused (Fusion: constraint blocks the move)."""

    def __init__(self, x: float, y: float, log: list[Any], refused: bool = False) -> None:
        self._p = _Pt(x, y, 0.0)
        self._log = log
        self._refused = refused

    @property
    def geometry(self) -> _Pt:
        return self._p

    def move(self, v: _Vec) -> bool:
        self._log.append(("move", v))
        if self._refused:
            return False
        self._p = _Pt(self._p.x + v.x, self._p.y + v.y, self._p.z + v.z)
        return True


def _sketch(log: list[Any]) -> Any:
    # model (x, y, z) mm / 10 -> sketch cm: sketch x = -model z, sketch y = model y, sketch z = 0
    return SimpleNamespace(modelToSketchSpace=lambda p: _Pt(-p.z, p.y, 0.0), log=log)


@pytest.fixture
def log() -> list[Any]:
    return []


@pytest.fixture(autouse=True)
def fake_calls(monkeypatch: pytest.MonkeyPatch, log: list[Any]) -> None:
    monkeypatch.setattr(fk_sketch, "mm", lambda expr: float(expr))
    monkeypatch.setattr(fk_sketch, "_sketch_dims", lambda s, p: log.append(("dims", p)) or ("h", "v"))
    monkeypatch.setattr(fk_sketch, "anchor", lambda s, sp, h, v: log.append(("anchor", h, v)))


def test_wrong_side_x_moves_only_x(point3d_xyz: None, log: list[Any]) -> None:
    # live case #2: hi corner, point dragged to sketch (3.08, 23.18) mm -> x on wrong side; target model (0, 34, 5.4)
    # -> sketch target (-0.54, 3.40) cm. x flips: -0.54 - 0.04 = -0.58 cm. y same side: not moved.
    sp = _SketchPoint(0.308, 2.318, log)
    fix_point(_sketch(log), sp, ("0", "34", "5.4"))
    assert [e[0] for e in log] == ["move", "dims", "anchor"]
    _, v = log[0]
    assert (v.x, v.y, v.z) == pytest.approx((-0.888, 0.0, 0.0))  # -0.58 - 0.308 on x, 0 on y
    assert (sp.geometry.x, sp.geometry.y) == pytest.approx((-0.58, 2.318))


def test_wrong_side_y_moves_only_y(point3d_xyz: None, log: list[Any]) -> None:
    # target model (0, -1, 4.6) -> sketch (-0.46, -0.10) cm. current x same side (not moved), y flips:
    # -0.10 - 0.04 = -0.14 cm.
    sp = _SketchPoint(-0.40, 0.05, log)
    fix_point(_sketch(log), sp, ("0", "-1", "4.6"))
    _, v = log[0]
    assert (v.x, v.y, v.z) == pytest.approx((0.0, -0.19, 0.0))  # -0.14 - 0.05 on y, 0 on x
    assert (sp.geometry.x, sp.geometry.y) == pytest.approx((-0.40, -0.14))


def test_both_axes_flip_moves_both(point3d_xyz: None, log: list[Any]) -> None:
    # target sketch (-0.54, 3.40) cm; current (0.20, -0.30) on wrong side of both axes -> (-0.58, 3.44)
    sp = _SketchPoint(0.20, -0.30, log)
    fix_point(_sketch(log), sp, ("0", "34", "5.4"))
    _, v = log[0]
    assert (v.x, v.y) == pytest.approx((-0.78, 3.74))
    assert (sp.geometry.x, sp.geometry.y) == pytest.approx((-0.58, 3.44))


@pytest.mark.parametrize("cur", [(-2.102, -0.080), (-0.47, -0.09), (-0.46, -0.10)])
def test_same_side_no_move(point3d_xyz: None, log: list[Any], cur: tuple[float, float]) -> None:
    # live case #1 (both axes same side as target -0.46, -0.10 cm), and param_poly-like offsets on the correct side:
    # no move() call at all.
    sp = _SketchPoint(cur[0], cur[1], log)
    fix_point(_sketch(log), sp, ("0", "-1", "4.6"))
    assert [e[0] for e in log] == ["dims", "anchor"]
    assert (sp.geometry.x, sp.geometry.y) == pytest.approx(cur)


def test_target_on_axis_never_moved(point3d_xyz: None, log: list[Any]) -> None:
    # target sketch x = 0 (model z = 0): |t| <= 1e-6 -> x never moved, even though current x is on the other side.
    sp = _SketchPoint(-0.05, 3.30, log)
    fix_point(_sketch(log), sp, ("0", "34", "0"))
    assert [e[0] for e in log] == ["dims", "anchor"]
    assert (sp.geometry.x, sp.geometry.y) == pytest.approx((-0.05, 3.30))


def test_refused_flip_raises_before_dims(point3d_xyz: None, log: list[Any]) -> None:
    sp = _SketchPoint(0.308, 2.318, log, refused=True)
    with pytest.raises(RuntimeError, match="SketchPoint.move refused"):
        fix_point(_sketch(log), sp, ("0", "34", "5.4"))
    assert [e[0] for e in log] == ["move"]
