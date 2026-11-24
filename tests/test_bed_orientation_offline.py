"""Offline tests: bed orientation ('-z' default, '+z', '-x', '+x', '-y', '+y') for dfam bed helpers and features.bed_chamfer.

'+x' = the bed is the X-max face (outward normal +X). No Fusion runtime: adsk stubs come from conftest.py.
Bed edges are faked via monkeypatch of features._bed_outer_edges; chamfer faces are SimpleNamespace objects
whose evaluator returns a chosen normal vector.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit import dfam, features
from fusion_kit import features_bed

Normal = tuple[float, float, float]


def _normal_on(axis: int, value: float) -> Normal:
    n = [0.0, 0.0, 0.0]
    n[axis] = value
    return (n[0], n[1], n[2])


def _face(n: Normal) -> SimpleNamespace:
    x, y, z = n
    ev = SimpleNamespace(getNormalAtPoint=lambda _p: (True, SimpleNamespace(x=x, y=y, z=z)))
    return SimpleNamespace(pointOnFace=MagicMock(), evaluator=ev)


def _run(monkeypatch: pytest.MonkeyPatch, normals: list[Normal], **kw: Any) -> tuple[Any, list[str]]:
    """Call bed_chamfer; attempt i creates a feature whose single chamfer face has normal normals[i]."""
    comp: Any = MagicMock()
    feats: list[Any] = []
    for n in normals:
        f = MagicMock()
        f.faces = [_face(n)]
        feats.append(f)
    comp.features.chamferFeatures.add.side_effect = feats
    seen: list[str] = []

    def edges(_body: Any, _only: bool, bed: str) -> list[Any]:
        seen.append(bed)
        return [MagicMock(), MagicMock()]

    monkeypatch.setattr(features_bed, "_bed_outer_edges", edges)
    monkeypatch.setattr(features_bed, "_bed_dihedral_deg", lambda _e, _b: 90.0)
    features.bed_chamfer(comp, MagicMock(), "1 mm", label="bed_t", **kw)
    return comp, seen


def _flips(comp: Any) -> list[bool]:
    sets = comp.features.chamferFeatures.createInput2.return_value.chamferEdgeSets
    return [c.args[3] for c in sets.addTwoDistancesChamferEdgeSet.call_args_list]


@pytest.mark.parametrize(
    ("bed", "axis", "sign"),
    [("-z", 2, -1), ("+z", 2, 1), ("-x", 0, -1), ("+x", 0, 1), ("-y", 1, -1), ("+y", 1, 1)],
)
def test_bed_axis_maps_direction(bed: str, axis: int, sign: int) -> None:
    assert dfam.bed_axis(bed) == (axis, sign)


@pytest.mark.parametrize("bad", ["x", "z", "+X", "", "+w", "-zz"])
def test_bed_axis_rejects_unknown(bad: str) -> None:
    with pytest.raises(ValueError, match="bed"):
        dfam.bed_axis(bad)


@pytest.mark.parametrize("bed", ["-z", "+x", "-x", "+y"])
def test_bed_chamfer_reads_normal_on_bed_axis(monkeypatch: pytest.MonkeyPatch, bed: str) -> None:
    axis, _ = dfam.bed_axis(bed)
    # 46 deg face, normal only on the bed axis -> accepted first try, edge finder got this bed
    comp, seen = _run(monkeypatch, [_normal_on(axis, 0.695)], bed=bed)
    assert seen == [bed]
    assert _flips(comp) == [False]


def test_bed_chamfer_ignores_z_normal_when_bed_is_x(monkeypatch: pytest.MonkeyPatch) -> None:
    # bed '+x': x component 0.5 (60 deg face, wrong) while z = 0.695 (would pass a z-only check) -> both rejected
    with pytest.raises(RuntimeError, match="bed_t"):
        _run(monkeypatch, [(0.5, 0.0, 0.695), (0.5, 0.0, 0.695)], bed="+x")


def test_bed_chamfer_records_bed_on_body(monkeypatch: pytest.MonkeyPatch) -> None:
    body: Any = MagicMock()
    comp: Any = MagicMock()
    f = MagicMock()
    f.faces = [_face(_normal_on(0, 0.695))]
    comp.features.chamferFeatures.add.side_effect = [f]
    monkeypatch.setattr(features_bed, "_bed_outer_edges", lambda _b, _o, _bed: [MagicMock()])
    monkeypatch.setattr(features_bed, "_bed_dihedral_deg", lambda _e, _b: 90.0)
    features.bed_chamfer(comp, body, "1 mm", label="bed_t", bed="+x")
    body.attributes.add.assert_called_once_with("dfam", "bed", "+x")


def test_bed_from_body_default_and_stored() -> None:
    body: Any = MagicMock()
    body.attributes.itemByName.return_value = None
    assert dfam.bed_from_body(body) == "-z"
    body.attributes.itemByName.return_value = SimpleNamespace(value="+x")
    assert dfam.bed_from_body(body) == "+x"


def test_set_bed_writes_attr_and_rejects_unknown() -> None:
    body: Any = MagicMock()
    assert dfam.set_bed(body, "+x") == "+x"
    body.attributes.add.assert_called_once_with("dfam", "bed", "+x")
    bad: Any = "+q"  # invalid on purpose; Any keeps basedpyright quiet without cast/ignore
    with pytest.raises(ValueError, match="bed"):
        dfam.set_bed(MagicMock(), bad)
