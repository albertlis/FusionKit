"""Offline tests: features.bed_chamfer two-distance chamfer, face angle from horizontal (default 46 deg).

No Fusion runtime: adsk stubs come from conftest.py. Bed edges are faked via monkeypatch of
features._bed_outer_edges; chamfer faces are SimpleNamespace objects whose evaluator returns a chosen normal z.
"""

import inspect
import math
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit import features
from fusion_kit.expr import evaluate

_CORE: Any = sys.modules["adsk.core"]


def _face(nz: float) -> SimpleNamespace:
    ev = SimpleNamespace(getNormalAtPoint=lambda _p: (True, SimpleNamespace(x=0.0, y=0.0, z=nz)))
    return SimpleNamespace(pointOnFace=MagicMock(), evaluator=ev)


def _run(monkeypatch: pytest.MonkeyPatch, nzs: list[float], **kw: Any) -> tuple[Any, list[Any]]:
    """Call bed_chamfer; attempt i creates a feature whose chamfer face has |n_z| = nzs[i]."""
    comp: Any = MagicMock()
    feats: list[Any] = []
    for nz in nzs:
        f = MagicMock()
        f.faces = [_face(nz)]
        feats.append(f)
    comp.features.chamferFeatures.add.side_effect = feats
    monkeypatch.setattr(features, "_bed_outer_edges", lambda _body, _only: [MagicMock(), MagicMock()])
    features.bed_chamfer(comp, MagicMock(), "1 mm", label="bed_t", **kw)
    return comp, feats


def _sets(comp: Any) -> Any:
    return comp.features.chamferFeatures.createInput2.return_value.chamferEdgeSets


def test_bed_chamfer_uses_two_distance_set(monkeypatch: pytest.MonkeyPatch) -> None:
    comp, _ = _run(monkeypatch, [0.695])
    sets = _sets(comp)
    sets.addTwoDistancesChamferEdgeSet.assert_called_once()
    sets.addEqualDistanceChamferEdgeSet.assert_not_called()
    assert sets.addTwoDistancesChamferEdgeSet.call_args.args[3] is False  # isFlipped first try


def test_bed_chamfer_distance_ratio_is_tan44(monkeypatch: pytest.MonkeyPatch) -> None:
    _CORE.ValueInput.createByString.reset_mock()
    _run(monkeypatch, [0.695])
    exprs = [c.args[0] for c in _CORE.ValueInput.createByString.call_args_list]
    assert exprs == ["(1 mm) / tan(46.0 deg)", "1 mm"]  # bed leg first, wall leg second
    ratio = evaluate(exprs[0], {}) / evaluate(exprs[1], {})
    assert ratio == pytest.approx(math.tan(math.radians(44.0)), rel=1e-9)


def test_bed_chamfer_default_angle_is_46() -> None:
    assert inspect.signature(features.bed_chamfer).parameters["angle_deg"].default == 46.0


@pytest.mark.parametrize("angle", [30.0, 45.0, 90.0])
def test_bed_chamfer_angle_outside_45_90_raises(monkeypatch: pytest.MonkeyPatch, angle: float) -> None:
    # 45: |n_z| 0.7071 fails the DfAM limit in both orientations. 30: wrong orientation (60 deg face) would pass |n_z|.
    comp: Any = MagicMock()
    monkeypatch.setattr(features, "_bed_outer_edges", lambda _body, _only: [MagicMock()])
    with pytest.raises(ValueError, match="angle_deg"):
        features.bed_chamfer(comp, MagicMock(), "1 mm", label="bed_t", angle_deg=angle)
    comp.features.chamferFeatures.createInput2.assert_not_called()


def test_bed_chamfer_flip_retry_when_first_orientation_steep(monkeypatch: pytest.MonkeyPatch) -> None:
    # first orientation gives 44 deg face (|n_z| 0.719) -> rejected, deleted, retried flipped
    comp, feats = _run(monkeypatch, [0.719, 0.695])
    flips = [c.args[3] for c in _sets(comp).addTwoDistancesChamferEdgeSet.call_args_list]
    assert flips == [False, True]
    feats[0].deleteMe.assert_called_once()
    feats[1].deleteMe.assert_not_called()


def test_bed_chamfer_rejects_wrong_angle_below_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # angle 46: first orientation gives 60 deg face (|n_z| 0.5 < 0.707 but wrong angle) -> rejected; flipped 46 deg accepted
    comp, feats = _run(monkeypatch, [0.5, 0.695])
    flips = [c.args[3] for c in _sets(comp).addTwoDistancesChamferEdgeSet.call_args_list]
    assert flips == [False, True]
    feats[0].deleteMe.assert_called_once()
    feats[1].deleteMe.assert_not_called()


def test_bed_chamfer_raises_when_no_orientation_matches_angle(monkeypatch: pytest.MonkeyPatch) -> None:
    # 44 deg (flipped, |n_z| 0.719) and 60 deg (|n_z| 0.5): neither is 46 +- 0.5 -> RuntimeError
    with pytest.raises(RuntimeError, match="bed_t"):
        _run(monkeypatch, [0.719, 0.5])
