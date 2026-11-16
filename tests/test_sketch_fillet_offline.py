"""Offline check of fusion_kit.sketch.fillet_corner: addFillet args (cm), radial dim bound to expr, None -> raise."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fusion_kit.sketch import fillet_corner


def _sketch(arc: MagicMock | None) -> tuple[MagicMock, MagicMock, MagicMock]:
    s = MagicMock(name="sketch")
    s.sketchCurves.sketchArcs.addFillet.return_value = arc
    rdim = MagicMock(name="radial_dim")
    s.sketchDimensions.addRadialDimension.return_value = rdim
    return s, rdim, arc or MagicMock()


def test_fillet_corner_passes_corner_points_and_radius_in_cm() -> None:
    arc = MagicMock(name="arc")
    arc.centerSketchPoint.geometry = SimpleNamespace(x=1.0, y=2.0, z=0.0)
    s, _, _ = _sketch(arc)
    la, lb = MagicMock(name="la"), MagicMock(name="lb")
    corner = MagicMock(name="corner")

    got = fillet_corner(s, la, lb, corner, "inner_fillet_radius", 4.0)

    assert got is arc
    s.sketchCurves.sketchArcs.addFillet.assert_called_once_with(la, corner.geometry, lb, corner.geometry, 0.4)


def test_fillet_corner_binds_radial_dim_to_expression() -> None:
    arc = MagicMock(name="arc")
    arc.centerSketchPoint.geometry = SimpleNamespace(x=1.0, y=2.0, z=0.0)
    s, rdim, _ = _sketch(arc)

    fillet_corner(s, MagicMock(), MagicMock(), MagicMock(), "inner_fillet_radius", 4.0)

    s.sketchDimensions.addRadialDimension.assert_called_once()
    assert s.sketchDimensions.addRadialDimension.call_args.args[0] is arc
    assert rdim.parameter.expression == "inner_fillet_radius"


def test_fillet_corner_raises_when_fusion_returns_none() -> None:
    s, _, _ = _sketch(None)
    with pytest.raises(RuntimeError, match="addFillet returned None"):
        fillet_corner(s, MagicMock(), MagicMock(), MagicMock(), "inner_fillet_radius", 4.0)
    s.sketchDimensions.addRadialDimension.assert_not_called()
