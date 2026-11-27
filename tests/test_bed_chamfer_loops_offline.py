"""Offline tests: bed_chamfer per-loop orientation, skipped-loop report, exclude_points_mm, strict.

No Fusion runtime: adsk stubs from conftest.py. Bed loops are faked via monkeypatch of features_bed._bed_loops; the
chamfer feature accepts or rejects each two-distance set through a test rule (accept(edge_names, flip)).
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit import features, features_bed


def _edge(name: str, phi: float, lo: tuple[float, float, float], hi: tuple[float, float, float]) -> SimpleNamespace:
    """Bed edge with bbox in cm (Fusion units)."""
    box = SimpleNamespace(
        minPoint=SimpleNamespace(x=lo[0], y=lo[1], z=lo[2]), maxPoint=SimpleNamespace(x=hi[0], y=hi[1], z=hi[2])
    )
    return SimpleNamespace(name=name, phi=phi, length=0.5, boundingBox=box)


def _outer() -> list[SimpleNamespace]:
    return [_edge("o1", 90.0, (0.0, 0.0, 0.0), (1.0, 1.0, 0.0)), _edge("o2", 90.0, (0.0, 0.0, 0.0), (1.0, 1.0, 0.0))]


def _hole() -> list[SimpleNamespace]:
    """Hole bbox 2..3 mm in x and y (cm 0.2..0.3): centre (2.50, 2.50, 0.00) mm."""
    return [_edge("h1", 90.0, (0.2, 0.2, 0.0), (0.3, 0.3, 0.0)), _edge("h2", 90.0, (0.2, 0.2, 0.0), (0.3, 0.3, 0.0))]


def _face(nz: float) -> SimpleNamespace:
    ev = SimpleNamespace(getNormalAtPoint=lambda _p: (True, SimpleNamespace(x=0.0, y=0.0, z=nz)))
    return SimpleNamespace(pointOnFace=MagicMock(), evaluator=ev)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    loops: list[tuple[bool, list[SimpleNamespace]]],
    accept: Any,
    add_fail_calls: int = 0,
    **kw: Any,
) -> tuple[Any, list[Any]]:
    """bed_chamfer over fake loops. Every chamfer set is judged by accept(edge_names, flip): accepted set -> face
    |n_z| 0.695 (46 deg), rejected -> 0.5 (60 deg, wrong angle). add_fail_calls: the first N chamferFeatures.add()
    calls raise RuntimeError (ASM). Returns (feature or raised, created createInput2 mocks)."""
    monkeypatch.setattr(features_bed, "_bed_loops", lambda _b, _o, _bed: loops)
    monkeypatch.setattr(features_bed, "_bed_dihedral_deg", lambda e, _b: e.phi)
    monkeypatch.setattr(features_bed, "collection", lambda items: list(items))
    comp: Any = MagicMock()
    created: list[Any] = []
    add_calls = [0]

    def new_input() -> Any:
        ci = MagicMock()
        created.append(ci)
        return ci

    def add(ci: Any) -> Any:
        add_calls[0] += 1
        if add_calls[0] <= add_fail_calls:
            raise RuntimeError("ASM_BL_UNFIN_SHEET")
        feat = MagicMock()
        feat.faces = []
        feat.sets = []  # (edge names, isFlipped) per two-distance set, read by the tests
        for call in ci.chamferEdgeSets.addTwoDistancesChamferEdgeSet.call_args_list:
            names = [e.name for e in call.args[0]]
            feat.sets.append((names, call.args[3]))
            feat.faces.append(_face(0.695 if accept(names, call.args[3]) else 0.5))
        return feat

    comp.features.chamferFeatures.createInput2.side_effect = new_input
    comp.features.chamferFeatures.add.side_effect = add
    result = features.bed_chamfer(comp, MagicMock(), "1 mm", label="bed_t", **kw)
    return result, created


def _kept_names(created: list[Any]) -> list[list[str]]:
    """Edge names of each chamfer set of the last (kept) feature."""
    return [
        [e.name for e in c.args[0]] for c in created[-1].chamferEdgeSets.addTwoDistancesChamferEdgeSet.call_args_list
    ]


def _no_hole(names: list[str], flip: bool) -> bool:
    return not any(n.startswith("h") for n in names)


def test_one_bad_inner_loop_is_skipped_not_raised(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    feat, created = _run(monkeypatch, [(True, _outer()), (False, _hole())], _no_hole, inner_loops=True)
    assert feat is not None
    assert _kept_names(created) == [["o1", "o2"]]  # hole left sharp, outer chamfered in one feature
    out = capsys.readouterr().out
    assert "BED_CHAMFER_LOOP_SKIP centre_mm=(2.50, 2.50, 0.00)" in out
    assert "label=bed_t" in out


def test_all_loops_bad_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="bed_t"):
        _run(monkeypatch, [(True, _outer()), (False, _hole())], lambda _n, _f: False, inner_loops=True)


def test_exclude_point_drops_inner_loop_silently(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # hole would be rejected; excluded by its centre point -> no skip, no raise, outer chamfered
    feat, created = _run(
        monkeypatch,
        [(True, _outer()), (False, _hole())],
        _no_hole,
        inner_loops=True,
        exclude_points_mm=[(2.5, 2.5, 0.0)],
    )
    assert feat is not None
    assert _kept_names(created) == [["o1", "o2"]]
    out = capsys.readouterr().out
    assert "BED_CHAMFER_EXCLUDE" in out
    assert "BED_CHAMFER_LOOP_SKIP" not in out


def test_exclude_point_never_drops_outer_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    # point lies inside the outer loop bbox too: outer must still be chamfered
    feat, created = _run(
        monkeypatch,
        [(True, _outer())],
        lambda _n, _f: True,
        exclude_points_mm=[(5.0, 5.0, 0.0)],
    )
    assert feat is not None
    assert _kept_names(created) == [["o1", "o2"]]


def test_strict_raises_when_a_loop_is_bad(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="strict=True"):
        _run(monkeypatch, [(True, _outer()), (False, _hole())], _no_hole, inner_loops=True, strict=True)


def test_strict_passes_when_all_loops_good(monkeypatch: pytest.MonkeyPatch) -> None:
    feat, created = _run(
        monkeypatch, [(True, _outer()), (False, _hole())], lambda _n, _f: True, inner_loops=True, strict=True
    )
    assert feat is not None
    assert _kept_names(created) == [["o1", "o2", "h1", "h2"]]


def _labels(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, str]]:
    """Record (feature, label) of every feature that gets labelled, i.e. every feature kept on the timeline."""
    labelled: list[tuple[Any, str]] = []
    monkeypatch.setattr(features_bed, "set_label", lambda feat, name: labelled.append((feat, name)))
    return labelled


def _only_own_flip(names: list[str], flip: bool) -> bool:
    """Outer edges (o*) chamfer only with isFlipped=True, hole edges (h*) only with isFlipped=False."""
    return all(n.startswith("o") == flip for n in names)


def test_loop_valid_only_in_other_orientation_gets_own_flip_feature(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # whole set fails both ways; hole fits isFlipped=False, outer only isFlipped=True -> two features, nothing skipped
    labelled = _labels(monkeypatch)
    feat, _created = _run(monkeypatch, [(True, _outer()), (False, _hole())], _only_own_flip, inner_loops=True)
    assert feat is labelled[0][0]
    assert [(f.sets, name) for f, name in labelled] == [
        ([(["h1", "h2"], False)], "bed_t"),
        ([(["o1", "o2"], True)], "bed_t_flip"),
    ]
    out = capsys.readouterr().out
    assert "BED_CHAMFER_LOOP_SKIP" not in out
    assert "needs isFlipped" not in out


def test_other_orientation_loops_failing_combined_are_skipped_with_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # hole fits isFlipped=False alone; two inner loops x, y fit isFlipped=True alone but not together (4 edges).
    # Primary = isFlipped=False (hole); x + y combined fail in their orientation -> both printed as skipped.
    labelled = _labels(monkeypatch)
    x = [_edge("x1", 90.0, (0.5, 0.5, 0.0), (0.6, 0.6, 0.0)), _edge("x2", 90.0, (0.5, 0.5, 0.0), (0.6, 0.6, 0.0))]
    y = [_edge("y1", 90.0, (0.8, 0.8, 0.0), (0.9, 0.9, 0.0)), _edge("y2", 90.0, (0.8, 0.8, 0.0), (0.9, 0.9, 0.0))]

    def pair_only_in_own(names: list[str], flip: bool) -> bool:
        if flip:
            return len(names) == 2 and names[0][0] in "xy"
        return all(n[0] == "h" for n in names)

    feat, _created = _run(monkeypatch, [(False, _hole()), (False, x), (False, y)], pair_only_in_own, inner_loops=True)
    assert feat is labelled[0][0]
    assert [(f.sets, name) for f, name in labelled] == [([(["h1", "h2"], False)], "bed_t")]
    out = capsys.readouterr().out
    assert out.count("BED_CHAMFER_LOOP_SKIP") == 2
    assert out.count("combined isFlipped=True chamfer failed") == 2


def test_strict_accepts_loops_valid_in_other_orientation(monkeypatch: pytest.MonkeyPatch) -> None:
    labelled = _labels(monkeypatch)
    _run(monkeypatch, [(True, _outer()), (False, _hole())], _only_own_flip, inner_loops=True, strict=True)
    assert [(f.sets, name) for f, name in labelled] == [
        ([(["h1", "h2"], False)], "bed_t"),
        ([(["o1", "o2"], True)], "bed_t_flip"),
    ]


def test_strict_raises_when_other_orientation_part_fails_combined(monkeypatch: pytest.MonkeyPatch) -> None:
    labelled = _labels(monkeypatch)
    x = [_edge("x1", 90.0, (0.5, 0.5, 0.0), (0.6, 0.6, 0.0)), _edge("x2", 90.0, (0.5, 0.5, 0.0), (0.6, 0.6, 0.0))]
    y = [_edge("y1", 90.0, (0.8, 0.8, 0.0), (0.9, 0.9, 0.0)), _edge("y2", 90.0, (0.8, 0.8, 0.0), (0.9, 0.9, 0.0))]

    def pair_only_in_own(names: list[str], flip: bool) -> bool:
        if flip:
            return len(names) == 2 and names[0][0] in "xy"
        return all(n[0] == "h" for n in names)

    with pytest.raises(RuntimeError, match="strict=True"):
        _run(monkeypatch, [(False, _hole()), (False, x), (False, y)], pair_only_in_own, inner_loops=True, strict=True)
    assert labelled == []  # no feature kept on the timeline


def test_single_bad_loop_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="bed_t"):
        _run(monkeypatch, [(True, _outer())], lambda _n, _f: False)


def test_default_add_error_falls_back_to_flip_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    # default kwargs (no inner_loops): add() raises on first call -> counted as failed attempt -> isFlipped retry runs
    feat, created = _run(monkeypatch, [(True, _outer())], lambda _n, _f: True, add_fail_calls=1)
    assert feat is not None
    assert len(created) == 2  # first attempt (raised) + flip retry
    assert _kept_names(created) == [["o1", "o2"]]


def test_default_add_error_both_orientations_raises_with_add_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="bed_t.*ASM_BL_UNFIN_SHEET"):
        _run(monkeypatch, [(True, _outer())], lambda _n, _f: True, add_fail_calls=2)


def test_single_bed_loop_path_untouched_by_new_args(monkeypatch: pytest.MonkeyPatch) -> None:
    # default args keep the old contract: one outer loop, accepted -> feature returned
    feat, created = _run(monkeypatch, [(True, _outer())], lambda _n, _f: True)
    assert feat is not None
    assert _kept_names(created) == [["o1", "o2"]]
