"""Offline tests: sketch_quality dup_sketch_cross_comp (curve_sig, cross_dup_lines, check() over fake design).

No Fusion runtime: fakes are SimpleNamespace / _Coll. Lengths and radii are cm (adsk units); expectations in mm.
"""

import itertools
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit import sketch_quality as sq
from fusion_kit.report import DesignStats, report_data

_TOKENS = itertools.count()


class _Coll:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    @property
    def count(self) -> int:
        return len(self._items)

    def item(self, index: int) -> Any:
        return self._items[index]


def _line(length_cm: float) -> Any:
    return SimpleNamespace(objectType="adsk::fusion::SketchLine", entityToken=f"l{next(_TOKENS)}", length=length_cm)


def _circle(radius_cm: float) -> Any:
    return SimpleNamespace(objectType="adsk::fusion::SketchCircle", entityToken=f"c{next(_TOKENS)}", radius=radius_cm)


def _sketch(
    name: str, *, lines_cm: tuple[float, ...] = (), circles_cm: tuple[float, ...] = (), skip: str | None = None
) -> Any:
    curves = [_line(x) for x in lines_cm] + [_circle(r) for r in circles_cm]
    loop = SimpleNamespace(profileCurves=_Coll([SimpleNamespace(sketchEntity=c) for c in curves]))
    profiles = _Coll([SimpleNamespace(profileLoops=_Coll([loop]))] if curves else [])

    def item(group: str, attr: str) -> Any:
        return SimpleNamespace(value=skip) if skip is not None and (group, attr) == ("skq", "skq_skip") else None

    return SimpleNamespace(
        name=name,
        isFullyConstrained=True,
        profiles=profiles,
        sketchCurves=SimpleNamespace(sketchLines=_Coll([]), sketchArcs=_Coll([]), sketchCircles=_Coll([])),
        sketchPoints=_Coll([]),
        originPoint=None,
        geometricConstraints=_Coll([]),
        sketchDimensions=_Coll([]),
        attributes=SimpleNamespace(itemByName=item),
    )


def _rect(name: str, a_cm: float, b_cm: float, **kw: Any) -> Any:
    return _sketch(name, lines_cm=(a_cm, b_cm, a_cm, b_cm), **kw)


RECT_SIG = (("SketchLine", 20.0), ("SketchLine", 20.0), ("SketchLine", 30.0), ("SketchLine", 30.0))
CIRCLE_SIG = (("SketchCircle", 5.0),)
TEXT = (
    "SKQ_WARN dup_sketch_cross_comp: {names} same profile -> one sketch + extrudes as new bodies / copy body / pattern"
)


# --- curve_sig -----------------------------------------------------------------------------------


def test_curve_sig_line_length_mm() -> None:
    assert sq.curve_sig(_line(2.5)) == ("SketchLine", 25.0)


def test_curve_sig_circle_radius_mm() -> None:
    assert sq.curve_sig(_circle(0.5)) == ("SketchCircle", 5.0)


def test_curve_sig_other_kind_is_zero() -> None:
    assert sq.curve_sig(SimpleNamespace(objectType="adsk::fusion::SketchEllipse")) == ("SketchEllipse", 0.0)


def test_curve_sig_rounds_to_hundredth_mm() -> None:
    assert sq.curve_sig(_line(2.00004)) == ("SketchLine", 20.0)
    assert sq.curve_sig(_line(2.00004)) != sq.curve_sig(_line(2.0051))


# --- cross_dup_lines (pure) ----------------------------------------------------------------------


def test_cross_dup_hit_two_sketches() -> None:
    lines = sq.cross_dup_lines([("a", RECT_SIG, False), ("b", RECT_SIG, False)])
    assert lines == [TEXT.format(names="a, b")]


def test_cross_dup_hit_three_sketches_one_line() -> None:
    lines = sq.cross_dup_lines([("a", RECT_SIG, False), ("b", RECT_SIG, False), ("c", RECT_SIG, False)])
    assert lines == [TEXT.format(names="a, b, c")]


def test_cross_dup_not_hit_different_profiles() -> None:
    other = (("SketchLine", 10.0), ("SketchLine", 10.0), ("SketchLine", 10.0), ("SketchLine", 10.0))
    assert sq.cross_dup_lines([("a", RECT_SIG, False), ("b", other, False)]) == []


def test_cross_dup_not_hit_single_sketch() -> None:
    assert sq.cross_dup_lines([("a", RECT_SIG, False)]) == []


def test_cross_dup_ignores_empty_profile_sig() -> None:
    assert sq.cross_dup_lines([("a", (), False), ("b", (), False)]) == []


def test_cross_dup_skipped_member_left_out() -> None:
    assert sq.cross_dup_lines([("a", RECT_SIG, False), ("b", RECT_SIG, True)]) == []


def test_cross_dup_single_circle_needs_three() -> None:
    assert sq.cross_dup_lines([("a", CIRCLE_SIG, False), ("b", CIRCLE_SIG, False)]) == []
    three = sq.cross_dup_lines([("a", CIRCLE_SIG, False), ("b", CIRCLE_SIG, False), ("c", CIRCLE_SIG, False)])
    assert three == [TEXT.format(names="a, b, c")]


def test_cross_dup_two_groups_first_seen_order() -> None:
    other = (("SketchLine", 10.0), ("SketchLine", 10.0), ("SketchLine", 10.0), ("SketchLine", 10.0))
    items = [("a", RECT_SIG, False), ("x", other, False), ("b", RECT_SIG, False), ("y", other, False)]
    assert sq.cross_dup_lines(items) == [TEXT.format(names="a, b"), TEXT.format(names="x, y")]


# --- facts / check() over a fake design ------------------------------------------------------------


def test_sketch_facts_profile_sig_sorted() -> None:
    facts = sq.sketch_facts(_rect("a", 3.0, 2.0))
    assert facts.profile_sig == RECT_SIG


def test_check_design_cross_comp_duplicate() -> None:
    a = _rect("a", 2.0, 3.0)
    b = _rect("b", 2.0, 3.0)
    design: Any = SimpleNamespace(
        allComponents=_Coll([SimpleNamespace(sketches=_Coll([a])), SimpleNamespace(sketches=_Coll([b]))])
    )
    assert sq.check(design) == [TEXT.format(names="a, b")]


def test_check_design_different_rects_no_hit() -> None:
    a = _rect("a", 2.0, 3.0)
    b = _rect("b", 4.0, 3.0)
    design: Any = SimpleNamespace(allComponents=_Coll([SimpleNamespace(sketches=_Coll([a, b]))]))
    assert sq.check(design) == []


def test_check_design_skip_opt_out() -> None:
    a = _rect("a", 2.0, 3.0)
    b = _rect("b", 2.0, 3.0, skip="dup_sketch_cross_comp:deliberate copy of the plate")
    design: Any = SimpleNamespace(allComponents=_Coll([SimpleNamespace(sketches=_Coll([a, b]))]))
    assert sq.check(design) == []


def test_check_sketches_same_comp_duplicate() -> None:
    lines = sq.check_sketches(_Coll([_rect("a", 2.0, 3.0), _rect("b", 2.0, 3.0)]))
    assert lines == [TEXT.format(names="a, b")]


def test_check_sketches_different_rects_no_hit() -> None:
    assert sq.check_sketches(_Coll([_rect("a", 2.0, 3.0), _rect("b", 4.0, 3.0)])) == []


def test_cross_rule_registered() -> None:
    assert "dup_sketch_cross_comp" in sq.RULES


class _Bodies:
    """Empty bRepBodies stand-in: count 0, iterates nothing."""

    count = 0

    def __iter__(self) -> Iterator[Any]:
        return iter(())


def _report_comp(*sketches: Any) -> Any:
    return SimpleNamespace(name="plate", bRepBodies=_Bodies(), sketches=_Coll(list(sketches)))


def test_report_path_emits_same_comp_cross_line() -> None:
    comp = _report_comp(_rect("a", 2.0, 3.0), _rect("b", 2.0, 3.0))
    data = report_data(comp, dfam=False, stats=DesignStats(unhealthy=0, suppressed=0, unnamed=0, numbered=0))
    assert data.skq == [TEXT.format(names="a, b")]


def test_report_path_skq_off_has_no_cross_line() -> None:
    comp = _report_comp(_rect("a", 2.0, 3.0), _rect("b", 2.0, 3.0))
    data = report_data(comp, dfam=False, skq=False, stats=DesignStats(unhealthy=0, suppressed=0, unnamed=0, numbered=0))
    assert data.skq == []
