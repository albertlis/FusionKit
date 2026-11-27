"""Offline tests: features.bed_chamfer two-distance chamfer, face angle from horizontal (default 46 deg).

No Fusion runtime: adsk stubs come from conftest.py. Bed loops are faked via monkeypatch of
features_bed._bed_loops; chamfer faces are SimpleNamespace objects whose evaluator returns a chosen normal z.
"""

import inspect
import math
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit import features, features_bed
from fusion_kit.dfam import bed_wall_ratio
from fusion_kit.dfam_rules import bed_chamfer_feasible, check_edges
from fusion_kit.dfam_types import EdgeFact
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
    monkeypatch.setattr(features_bed, "_bed_loops", lambda _body, _only, _bed: [(True, [MagicMock(), MagicMock()])])
    monkeypatch.setattr(features_bed, "_bed_dihedral_deg", lambda _e, _b: 90.0)
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
    # wall leg second = (1 mm) * ratio90/ratio90 = 1 mm at 90 deg (scaled per phi since the 90-deg footprint rule)
    assert evaluate(exprs[1], {}) == pytest.approx(evaluate("1 mm", {}))
    ratio = evaluate(exprs[0], {}) / evaluate(exprs[1], {})
    assert ratio == pytest.approx(math.tan(math.radians(44.0)), rel=1e-9)


def test_bed_chamfer_expression_text_carries_angle(monkeypatch: pytest.MonkeyPatch) -> None:
    # timeline text, not Python floats: bed leg = distance * tan(90 - angle); wall leg = distance / (sin phi + cos phi * tan angle)
    _CORE.ValueInput.createByString.reset_mock()
    _run(monkeypatch, [0.695])
    exprs = [c.args[0] for c in _CORE.ValueInput.createByString.call_args_list]
    assert exprs[0] == "(1 mm) * tan(44.0 deg)"
    assert exprs[1] == "(1 mm) / (sin(90.0 deg) + cos(90.0 deg) * tan(46.0 deg))"


def test_bed_chamfer_default_angle_is_46() -> None:
    assert inspect.signature(features.bed_chamfer).parameters["angle_deg"].default == 46.0


@pytest.mark.parametrize("angle", [30.0, 45.0, 90.0])
def test_bed_chamfer_angle_outside_45_90_raises(monkeypatch: pytest.MonkeyPatch, angle: float) -> None:
    # 45: |n_z| 0.7071 fails the DfAM limit in both orientations. 30: wrong orientation (60 deg face) would pass |n_z|.
    comp: Any = MagicMock()
    monkeypatch.setattr(features_bed, "_bed_loops", lambda _body, _only, _bed: [(True, [MagicMock()])])
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


def _face_angle_deg(phi_deg: float, ratio: float) -> float:
    """Chamfer face angle from bed for bed/wall leg ratio on an edge of interior dihedral phi (inverse of bed_wall_ratio)."""
    phi = math.radians(phi_deg)
    cot_a = (ratio - math.cos(phi)) / math.sin(phi)
    return math.degrees(math.atan(1.0 / cot_a))


def test_bed_leg_ratio_square_corner_is_cot() -> None:
    assert bed_wall_ratio(90.0, 46.0) == pytest.approx(1.0 / math.tan(math.radians(46.0)), rel=1e-12)


@pytest.mark.parametrize("phi", [75.0, 90.0, 105.0])
def test_bed_leg_ratio_gives_requested_face_angle(phi: float) -> None:
    assert _face_angle_deg(phi, bed_wall_ratio(phi, 46.0)) == pytest.approx(46.0, abs=1e-9)


def test_square_ratio_on_dovetail_edge_gives_live_angle() -> None:
    # live side_wall run (bed +x): 105 deg edges with the single 90 deg ratio gave 38.27 deg (flip False), 36.73 (flip True)
    assert _face_angle_deg(105.0, 1.0 / math.tan(math.radians(46.0))) == pytest.approx(38.27, abs=0.01)
    assert _face_angle_deg(105.0, math.tan(math.radians(46.0))) == pytest.approx(36.73, abs=0.01)


def test_bed_chamfer_one_edge_set_per_dihedral_group(monkeypatch: pytest.MonkeyPatch) -> None:
    # 90 + 105 deg edges: one feature, one two-distance set per dihedral group, each with its own bed leg
    edges = [SimpleNamespace(phi=90.0), SimpleNamespace(phi=105.0), SimpleNamespace(phi=90.0)]
    monkeypatch.setattr(features_bed, "_bed_loops", lambda _b, _o, _bed: [(True, edges)])
    monkeypatch.setattr(features_bed, "_bed_dihedral_deg", lambda e, _b: e.phi)
    comp: Any = MagicMock()
    feat = MagicMock()
    feat.faces = [_face(0.695)]  # every face at 46 deg from bed (default bed -z -> z axis)
    comp.features.chamferFeatures.add.return_value = feat
    _CORE.ValueInput.createByString.reset_mock()
    features.bed_chamfer(comp, MagicMock(), "1 mm", label="bed_t")
    sets = comp.features.chamferFeatures.createInput2.return_value.chamferEdgeSets
    assert sets.addTwoDistancesChamferEdgeSet.call_count == 2
    exprs = [c.args[0] for c in _CORE.ValueInput.createByString.call_args_list]
    # groups sorted by phi (90, then 105): [bed90, wall90, bed105, wall105]
    cot46 = 1.0 / math.tan(math.radians(46.0))
    assert evaluate(exprs[1], {}) == pytest.approx(evaluate("1 mm", {}))  # 90 group wall = 1 mm
    assert evaluate(exprs[0], {}) == pytest.approx(evaluate("1 mm", {}) * cot46)  # bed footprint = 90-deg footprint
    assert evaluate(exprs[2], {}) == pytest.approx(evaluate(exprs[0], {}))  # 105 group bed footprint identical
    assert evaluate(exprs[2], {}) / evaluate(exprs[3], {}) == pytest.approx(bed_wall_ratio(105.0, 46.0))


class _FakePlane:
    """Stand-in for adsk.core.Plane: _bed_loop_edges runs isinstance(face.geometry, Plane); the stub is a MagicMock."""


def _x_face(nx: float) -> SimpleNamespace:
    """Chamfer face with outward normal (nx, 0, 0): bed '+x' reads the x component."""
    ev = SimpleNamespace(getNormalAtPoint=lambda _p: (True, SimpleNamespace(x=nx, y=0.0, z=0.0)))
    return SimpleNamespace(pointOnFace=MagicMock(), evaluator=ev)


def _edge(name: str, phi: float, length_cm: float = 0.5) -> SimpleNamespace:
    return SimpleNamespace(name=name, phi=phi, length=length_cm)


def _box_face(x_cm: float, loops: list[SimpleNamespace]) -> SimpleNamespace:
    """Planar face flat at X = x_cm (cm): bbox collapsed onto that plane."""
    p = SimpleNamespace(x=x_cm, y=0.0, z=0.0)
    return SimpleNamespace(geometry=_FakePlane(), boundingBox=SimpleNamespace(minPoint=p, maxPoint=p), loops=loops)


def _window_body(monkeypatch: pytest.MonkeyPatch, inner_len_cm: float = 0.5) -> Any:
    """Bed face at X max (1 cm): outer loop o1, o2 (phi 90) + window loop i1 (90), i2 (105). Wall face at X 0 with
    hole w1: not the bed, must never be chamfered. Real _bed_loop_edges runs (only the dihedral is faked)."""
    body: Any = MagicMock(name="body")
    body.boundingBox = SimpleNamespace(
        minPoint=SimpleNamespace(x=0.0, y=0.0, z=0.0), maxPoint=SimpleNamespace(x=1.0, y=1.0, z=1.0)
    )
    outer = SimpleNamespace(isOuter=True, edges=[_edge("o1", 90.0), _edge("o2", 90.0)])
    window = SimpleNamespace(isOuter=False, edges=[_edge("i1", 90.0, inner_len_cm), _edge("i2", 105.0, inner_len_cm)])
    hole = SimpleNamespace(isOuter=False, edges=[_edge("w1", 90.0)])
    body.faces = [_box_face(1.0, [outer, window]), _box_face(0.0, [hole])]
    # swap the module's adsk name, not adsk.core.Plane: patching the MagicMock stub leaves it without Plane after undo
    fake_adsk = SimpleNamespace(core=SimpleNamespace(Plane=_FakePlane, ValueInput=_CORE.ValueInput))
    monkeypatch.setattr(features_bed, "adsk", fake_adsk)
    monkeypatch.setattr(features_bed, "_bed_dihedral_deg", lambda e, _b: e.phi)
    monkeypatch.setattr(features_bed, "collection", lambda items: list(items))
    return body


def _window_sets(monkeypatch: pytest.MonkeyPatch, inner_len_cm: float = 0.5, **kw: Any) -> list[list[str]]:
    """Edge names of each two-distance set bed_chamfer(bed='+x') builds, in call order."""
    body = _window_body(monkeypatch, inner_len_cm)
    comp: Any = MagicMock()
    feat = MagicMock()
    feat.faces = [_x_face(0.695)]
    comp.features.chamferFeatures.add.return_value = feat
    features.bed_chamfer(comp, body, "1 mm", label="bed_t", bed="+x", **kw)
    sets = comp.features.chamferFeatures.createInput2.return_value.chamferEdgeSets
    return [[e.name for e in c.args[0]] for c in sets.addTwoDistancesChamferEdgeSet.call_args_list]


def test_inner_loops_chamfered_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    # window i1 joins the phi-90 group of the outer loop, i2 (phi 105) gets its own set; hole w1 on the wall is absent
    assert _window_sets(monkeypatch, inner_loops=True) == [["o1", "o2", "i1"], ["i2"]]


def test_inner_loops_default_keeps_holes_sharp(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _window_sets(monkeypatch) == [["o1", "o2"]]


def test_inner_loops_honour_min_edge_mm(monkeypatch: pytest.MonkeyPatch) -> None:
    # window edges 2 mm < 3 mm -> dropped; outer edges 5 mm kept
    assert _window_sets(monkeypatch, inner_len_cm=0.2, inner_loops=True, min_edge_mm=3.0) == [["o1", "o2"]]


def test_bed_chamfer_feasible_criterion() -> None:
    assert not bed_chamfer_feasible(135.0, 46.0)  # denominator -0.0251: outer tip, no wall leg
    assert bed_chamfer_feasible(90.0, 46.0)
    assert bed_chamfer_feasible(105.0, 46.0)


def _skip_run(
    monkeypatch: pytest.MonkeyPatch, phis: list[float], capsys: pytest.CaptureFixture[str]
) -> tuple[Any, str]:
    """bed_chamfer over bed edges with the given dihedrals; returns (chamferEdgeSets mock, stdout)."""
    edges = [SimpleNamespace(name=f"e{i}", phi=p) for i, p in enumerate(phis)]
    monkeypatch.setattr(features_bed, "_bed_loops", lambda _b, _o, _bed: [(True, edges)])
    monkeypatch.setattr(features_bed, "_bed_dihedral_deg", lambda e, _b: e.phi)
    monkeypatch.setattr(features_bed, "collection", lambda items: list(items))
    comp: Any = MagicMock()
    feat = MagicMock()
    feat.faces = [_face(0.695)]
    comp.features.chamferFeatures.add.return_value = feat
    features.bed_chamfer(comp, MagicMock(), "1 mm", label="bed_t")
    return _sets(comp), capsys.readouterr().out


def test_bed_chamfer_skips_135_deg_outer_tip(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # 135 edge skipped (one WARN-style print), 90 edge still chamfered
    sets, out = _skip_run(monkeypatch, [135.0, 90.0], capsys)
    assert sets.addTwoDistancesChamferEdgeSet.call_count == 1
    assert [e.name for e in sets.addTwoDistancesChamferEdgeSet.call_args.args[0]] == ["e1"]
    assert out.count("BED_CHAMFER_SKIP 1 edges (dihedral 135 deg)") == 1


def test_bed_chamfer_mixed_outline_keeps_only_valid_groups(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sets, out = _skip_run(monkeypatch, [135.0, 90.0, 105.0, 135.0], capsys)
    names = [[e.name for e in c.args[0]] for c in sets.addTwoDistancesChamferEdgeSet.call_args_list]
    assert names == [["e1"], ["e2"]]  # sorted by dihedral: 90 then 105; both 135 tips dropped
    assert "BED_CHAMFER_SKIP 2 edges (dihedral 135 deg)" in out


def test_bed_chamfer_all_edges_skipped_raises(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(ValueError, match="every bed edge skipped"):
        _skip_run(monkeypatch, [135.0, 135.0], capsys)


def _sharp_bed(phi: float | None) -> EdgeFact:
    return EdgeFact(label="e", kind="bed", treatment="sharp", size_mm=0.0, room_mm=10.0, length_mm=10.0, phi_deg=phi)


def test_dfam_skipped_135_bed_edge_is_warn_not_fail() -> None:
    assert [(f.rule, f.level) for f in check_edges([_sharp_bed(135.0)])] == [("bed_chamfer", "WARN")]


def test_dfam_sharp_90_bed_edge_still_fails() -> None:
    assert [(f.rule, f.level) for f in check_edges([_sharp_bed(90.0)])] == [("bed_chamfer", "FAIL")]


def _angle_param(monkeypatch: pytest.MonkeyPatch, deg: float, seen: list[Any] | None = None) -> None:
    """Stand-in for eval_fresh: angle_expr evaluates to `deg` (Fusion internal unit: rad)."""

    def fake(design: Any, expr: str, units: str) -> float:
        if seen is not None:
            seen.append((design, expr, units))
        return math.radians(deg)

    monkeypatch.setattr(features_bed, "eval_fresh", fake)


def test_bed_chamfer_angle_expr_in_timeline_text(monkeypatch: pytest.MonkeyPatch) -> None:
    # angle_expr (user param name) replaces the 46.0 literal in both expressions
    _angle_param(monkeypatch, 46.0)
    _CORE.ValueInput.createByString.reset_mock()
    _run(monkeypatch, [0.695], angle_expr="bed_chamfer_angle")
    exprs = [c.args[0] for c in _CORE.ValueInput.createByString.call_args_list]
    assert exprs[0] == "(1 mm) * tan(90 deg - (bed_chamfer_angle))"
    assert exprs[1] == "(1 mm) / (sin(90.0 deg) + cos(90.0 deg) * tan((bed_chamfer_angle)))"


def test_bed_chamfer_angle_expr_evaluated_fresh_in_deg(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    _angle_param(monkeypatch, 46.0, seen)
    comp, _ = _run(monkeypatch, [0.695], angle_expr="bed_chamfer_angle")
    assert seen == [(comp.parentDesign, "bed_chamfer_angle", "deg")]


def test_bed_chamfer_angle_expr_out_of_range_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # evaluated float 44 fails the (45, 90) check before any chamfer feature is created
    _angle_param(monkeypatch, 44.0)
    comp: Any = MagicMock()
    monkeypatch.setattr(features_bed, "_bed_loops", lambda _b, _o, _bed: [(True, [MagicMock()])])
    with pytest.raises(ValueError, match="angle_deg"):
        features.bed_chamfer(comp, MagicMock(), "1 mm", label="bed_t", angle_expr="bed_chamfer_angle")
    comp.features.chamferFeatures.createInput2.assert_not_called()
