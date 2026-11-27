"""Offline tests: skipped inner bed loops -> body attr dfam/bed_loop_skip -> DfAM bed edge; add() exceptions per loop.

No Fusion runtime: adsk stubs from conftest.py. Bed loops faked via monkeypatch of features_bed._bed_loops; chamfer sets
accepted/rejected by a test rule, add() may raise.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit import features, features_bed
from fusion_kit.dfam_bed import (
    add_bed_loop_skips,
    format_loop_centres,
    loop_centres_from_body,
    loop_tokens_at_centres,
    parse_loop_centres,
    print_frame_centre_mm,
)
from fusion_kit.dfam_rules import check_edges
from fusion_kit.dfam_types import BED_LOOP_SKIP_ATTR, DFAM_GROUP, EdgeFact


def _edge(name: str, phi: float, lo: tuple[float, float, float], hi: tuple[float, float, float]) -> SimpleNamespace:
    box = SimpleNamespace(
        minPoint=SimpleNamespace(x=lo[0], y=lo[1], z=lo[2]), maxPoint=SimpleNamespace(x=hi[0], y=hi[1], z=hi[2])
    )
    return SimpleNamespace(name=name, phi=phi, length=0.5, boundingBox=box)


def _outer() -> list[SimpleNamespace]:
    return [_edge("o1", 90.0, (0.0, 0.0, 0.0), (1.0, 1.0, 0.0)), _edge("o2", 90.0, (0.0, 0.0, 0.0), (1.0, 1.0, 0.0))]


def _hole() -> list[SimpleNamespace]:
    """Hole bbox 2..3 mm in x and y: centre (2.50, 2.50, 0.00) mm."""
    return [_edge("h1", 90.0, (0.2, 0.2, 0.0), (0.3, 0.3, 0.0)), _edge("h2", 90.0, (0.2, 0.2, 0.0), (0.3, 0.3, 0.0))]


def _face(nz: float) -> SimpleNamespace:
    ev = SimpleNamespace(getNormalAtPoint=lambda _p: (True, SimpleNamespace(x=0.0, y=0.0, z=nz)))
    return SimpleNamespace(pointOnFace=MagicMock(), evaluator=ev)


def _body(stored: str | None = None) -> MagicMock:
    """Body mock whose attributes.itemByName returns the stored ("dfam", "bed_loop_skip") value or None."""
    body: Any = MagicMock(name="body")
    body.attributes.itemByName.return_value = None if stored is None else SimpleNamespace(value=stored)
    return body


def _run(
    monkeypatch: pytest.MonkeyPatch,
    loops: list[tuple[bool, list[SimpleNamespace]]],
    accept: Any,
    body: Any,
    add_raises: dict[str, str] | None = None,
    **kw: Any,
) -> tuple[Any, Any]:
    """bed_chamfer over fake loops. accept(edge_names, flip) decides each set; add_raises maps a set's edge name
    to the RuntimeError message add() raises when that name is in the feature (name-based, first match)."""
    monkeypatch.setattr(features_bed, "_bed_loops", lambda _b, _o, _bed: loops)
    monkeypatch.setattr(features_bed, "_bed_dihedral_deg", lambda e, _b: e.phi)
    monkeypatch.setattr(features_bed, "collection", lambda items: list(items))
    comp: Any = MagicMock()
    created: list[Any] = []

    def new_input() -> Any:
        ci = MagicMock()
        created.append(ci)
        return ci

    def add(ci: Any) -> Any:
        feat = MagicMock()
        feat.faces = []
        for call in ci.chamferEdgeSets.addTwoDistancesChamferEdgeSet.call_args_list:
            names = [e.name for e in call.args[0]]
            for name, msg in (add_raises or {}).items():
                if name in names:
                    raise RuntimeError(msg)
            feat.faces.append(_face(0.695 if accept(names, call.args[3]) else 0.5))
        return feat

    comp.features.chamferFeatures.createInput2.side_effect = new_input
    comp.features.chamferFeatures.add.side_effect = add
    result = features.bed_chamfer(comp, body, "1 mm", label="bed_t", **kw)
    return result, created


def _no_hole(names: list[str], flip: bool) -> bool:
    return not any(n.startswith("h") for n in names)


def _kept(created: list[Any]) -> list[list[str]]:
    return [
        [e.name for e in c.args[0]] for c in created[-1].chamferEdgeSets.addTwoDistancesChamferEdgeSet.call_args_list
    ]


# --- attr format / merge (pure + mock) -------------------------------------------------------------------------------


def test_format_parse_roundtrip() -> None:
    centres = [(2.5, 2.5, 0.0), (1.0, -3.25, 7.0)]
    value = format_loop_centres(centres)
    assert value == "2.500,2.500,0.000;1.000,-3.250,7.000"
    assert parse_loop_centres(value) == centres
    assert parse_loop_centres("") == []


def test_add_bed_loop_skips_writes_attr_on_empty_body() -> None:
    body = _body()
    value = add_bed_loop_skips(body, [(2.5, 2.5, 0.0)])
    assert value == "2.500,2.500,0.000"
    body.attributes.add.assert_called_once_with(DFAM_GROUP, BED_LOOP_SKIP_ATTR, "2.500,2.500,0.000")


def test_add_bed_loop_skips_merges_and_dedupes() -> None:
    body = _body(stored="2.500,2.500,0.000")
    value = add_bed_loop_skips(body, [(2.52, 2.48, 0.0), (7.0, 1.0, 0.0)])  # first one within 0.05 mm: dropped
    assert value == "2.500,2.500,0.000;7.000,1.000,0.000"
    body.attributes.add.assert_called_with(DFAM_GROUP, BED_LOOP_SKIP_ATTR, value)
    assert parse_loop_centres(value) == [(2.5, 2.5, 0.0), (7.0, 1.0, 0.0)]


# --- print frame / DfAM matching -----------------------------------------------------------------------------------


def test_print_frame_plus_x_bed_lands_on_floor() -> None:
    lo, hi = (0.0, 0.0, 0.0), (10.0, 20.0, 30.0)  # mm; X-max face is the bed
    on_bed = print_frame_centre_mm((10.0, 5.0, 5.0), "+x", lo, hi)
    opposite = print_frame_centre_mm((0.0, 5.0, 5.0), "+x", lo, hi)
    assert on_bed[2] == pytest.approx(0.0, abs=1e-9)  # bed face on min Z
    assert opposite[2] == pytest.approx(10.0, abs=1e-9)  # X extent 10 mm = height in print frame


def test_print_frame_minus_z_is_identity() -> None:
    assert print_frame_centre_mm((1.0, 2.0, 3.0), "-z", (0.0, 0.0, 0.0), (5.0, 5.0, 5.0)) == (1.0, 2.0, 3.0)


def test_loop_tokens_match_only_recorded_centre() -> None:
    loops = [((2.5, 2.5, 0.0), [1, 2]), ((9.0, 9.0, 0.0), [3])]
    assert loop_tokens_at_centres(loops, [(2.52, 2.48, 0.0)]) == {1, 2}
    assert loop_tokens_at_centres(loops, [(5.0, 5.0, 0.0)]) == set()


def test_sharp_inner_bed_rim_is_dfam_fail() -> None:
    # what dfam_facts emits for a skipped window/hole rim: bed kind, sharp, phi 90 -> FAIL unless dfam_skip
    rim = EdgeFact(label="h", kind="bed", treatment="sharp", size_mm=0.0, room_mm=10.0, length_mm=10.0, phi_deg=90.0)
    assert [(f.rule, f.level) for f in check_edges([rim])] == [("bed_chamfer", "FAIL")]


# --- features: attr write and add() exceptions ---------------------------------------------------------------------


def test_skipped_inner_loop_recorded_on_body(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _body()
    _run(monkeypatch, [(True, _outer()), (False, _hole())], _no_hole, body, inner_loops=True)
    body.attributes.add.assert_any_call(DFAM_GROUP, BED_LOOP_SKIP_ATTR, "2.500,2.500,0.000")


def test_default_call_writes_no_skip_attr(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _body()
    _run(monkeypatch, [(True, _outer())], lambda _n, _f: True, body)
    names = [c.args[1] for c in body.attributes.add.call_args_list]
    assert BED_LOOP_SKIP_ATTR not in names


def test_add_exception_on_one_loop_is_skipped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = _body()
    feat, created = _run(
        monkeypatch,
        [(True, _outer()), (False, _hole())],
        lambda _n, _f: True,
        body,
        add_raises={"h1": "ASM_BL_UNFIN_SHEET"},
        inner_loops=True,
    )
    assert feat is not None
    assert _kept(created) == [["o1", "o2"]]
    assert "add() failed: ASM_BL_UNFIN_SHEET" in capsys.readouterr().out
    body.attributes.add.assert_any_call(DFAM_GROUP, BED_LOOP_SKIP_ATTR, "2.500,2.500,0.000")


def test_default_single_loop_add_exception_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    # default contract unchanged: no fallback, add() error surfaces as before
    with pytest.raises(RuntimeError, match="ASM_BL_UNFIN_SHEET"):
        _run(monkeypatch, [(True, _outer())], lambda _n, _f: True, _body(), add_raises={"o1": "ASM_BL_UNFIN_SHEET"})


def test_default_multi_loop_failure_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # default (no inner_loops/exclude/strict): two outer loops both rejected -> RuntimeError, no per-loop fallback
    loops = [(True, _outer()), (True, _hole())]
    with pytest.raises(RuntimeError, match="bed_t"):
        _run(monkeypatch, loops, lambda _n, _f: False, _body())
