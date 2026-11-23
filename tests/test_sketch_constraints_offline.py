"""Offline check of fusion_kit.sketch constraint helpers: offset_curves binds the distance expression;
center_on_origin = midpoint of construction diagonal at origin."""

import sys
from typing import Any
from unittest.mock import MagicMock

from fusion_kit.sketch import center_on_origin, offset_curves

_CORE: Any = sys.modules["adsk.core"]


def _sketch() -> MagicMock:
    return MagicMock(name="sketch")


def test_offset_curves_binds_distance_expression() -> None:
    s = _sketch()
    curves = [MagicMock(name="c0"), MagicMock(name="c1")]
    value = MagicMock(name="value_input")
    _CORE.ValueInput.createByString.return_value = value
    inp = MagicMock(name="offset_input")
    s.geometricConstraints.createOffsetInput.return_value = inp

    got = offset_curves(s, curves, "wall_thickness")

    assert got is s.geometricConstraints.addOffset2.return_value
    _CORE.ValueInput.createByString.assert_called_with("wall_thickness")
    s.geometricConstraints.createOffsetInput.assert_called_once_with(curves, value)
    s.geometricConstraints.addOffset2.assert_called_once_with(inp)


def test_center_on_origin_midpoint_of_construction_diagonal() -> None:
    s = _sketch()
    bottom, right, top, left = (MagicMock(name=n) for n in ("bottom", "right", "top", "left"))
    diag = MagicMock(name="diag")
    s.sketchCurves.sketchLines.addByTwoPoints.return_value = diag

    got = center_on_origin(s, [bottom, right, top, left])

    assert got is s.geometricConstraints.addMidPoint.return_value
    s.sketchCurves.sketchLines.addByTwoPoints.assert_called_once_with(bottom.startSketchPoint, top.startSketchPoint)
    assert diag.isConstruction is True
    s.geometricConstraints.addMidPoint.assert_called_once_with(s.originPoint, diag)
