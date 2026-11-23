"""Offline tests: sketch_quality rules (pure facts), sketch adapter + check() with fakes, report / build_check wiring.

No Fusion runtime: adsk is the MagicMock stub from conftest; fakes are SimpleNamespace / _Coll.
"""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit import build_check, report, sketch_quality
from fusion_kit.sketch_quality import Cons, Ent, SketchFacts, check_facts, skq_token


class _Coll[T]:
    """Fusion-style collection: count property + item(index). Fake Fusion objects are typed Any in this file."""

    def __init__(self, items: list[T]) -> None:
        self._items = items

    @property
    def count(self) -> int:
        return len(self._items)

    def item(self, index: int) -> T:
        return self._items[index]

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)


def _facts(
    name: str = "sk",
    *,
    full: bool = True,
    dims: list[str] | None = None,
    dim_refs: list[tuple[str, ...]] | None = None,
    cons: list[Cons] | None = None,
    ents: list[Ent] | None = None,
) -> SketchFacts:
    dim_list = dims or []
    return SketchFacts(
        name,
        full,
        tuple(dim_list),
        tuple(dim_refs) if dim_refs is not None else tuple(() for _ in dim_list),
        tuple(cons or []),
        tuple(ents or []),
    )


def _rules(lines: list[str]) -> list[str]:
    return [line.split(" ")[1].rstrip(":") for line in lines if not line.startswith("SKQ_WARN skq_skip")]


# --- rule table ---------------------------------------------------------------------------------


def test_rules_derived_from_rule_funcs() -> None:
    assert sketch_quality.RULES == frozenset(sketch_quality._RULE_FUNCS)
    assert "underconstrained" in sketch_quality.RULES


# --- underconstrained ---------------------------------------------------------------------------


def test_underconstrained_hit() -> None:
    lines = check_facts(_facts(full=False))
    assert lines == ["SKQ_WARN underconstrained: sk not fully constrained"]


def test_underconstrained_not_hit() -> None:
    assert check_facts(_facts(full=True)) == []


# --- expr_dim -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "operand"),
    [
        ("w/2", "w"),
        ("50 mm / 2", "50 mm"),
        ("pitch * 2", None),
        ("pitch + 1 mm", None),
        ("50 mm", None),
        ("-2 mm", None),
        ("2 * 50 mm", None),
        ("", None),
        ("d/2.5", None),
        ("d/20", None),
        ("2*pi*r", None),
        ("width - 2*wall", None),
        ("wall*1", None),
    ],
)
def test_half_operand(expr: str, operand: str | None) -> None:
    assert sketch_quality.half_operand(expr) == operand


def test_expr_dim_hit() -> None:
    lines = check_facts(_facts(dims=["width", "width / 2"]))
    assert _rules(lines) == ["expr_dim"]
    assert lines[0].startswith("SKQ_WARN expr_dim: sk dim 'width / 2'")


@pytest.mark.parametrize(
    ("dims", "fires"),
    [
        (["width", "width / 2"], True),
        (["50 mm", "50 mm / 2"], True),
        (["width", "width/2"], True),
        (["width - 2*wall", "wall"], False),
        (["r", "2*pi*r"], False),
        (["wall", "wall*1"], False),
        (["d", "d/2.5"], False),
        (["d", "d/20"], False),
        (["pitch", "pitch * 2"], False),  # *2 is no longer in scope: expr_dim is /2 only
        (["50 mm / 2", "100 mm"], False),  # X must be another dimension's expression
        (["w/2"], False),  # no other dimension holds X
    ],
)
def test_expr_dim_rule(dims: list[str], fires: bool) -> None:
    assert _rules(check_facts(_facts(dims=dims))) == (["expr_dim"] if fires else [])


def test_expr_dim_not_hit_for_literal() -> None:
    assert check_facts(_facts(dims=["50 mm", "12 mm"])) == []


# --- pattern_expr -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "pair"),
    [
        ("pitch*2", ("pitch", 2)),
        ("2*pitch", ("pitch", 2)),
        ("pitch * 3", ("pitch", 3)),
        ("pitch*1", None),
        ("pitch*0", None),
        ("pitch", None),
        ("2*pi*r", None),
        ("pitch*2.5", None),
        ("width - 2*wall", None),
        ("50 mm*2", None),
        ("pitch*2 + 1", None),
    ],
)
def test_pattern_multiple(expr: str, pair: tuple[str, int] | None) -> None:
    assert sketch_quality.pattern_multiple(expr) == pair


def test_pattern_expr_hit() -> None:
    lines = check_facts(_facts(dims=["pitch*2", "pitch*3"]))
    assert _rules(lines) == ["pattern_expr"]
    assert lines[0] == (
        "SKQ_WARN pattern_expr: sk per-instance multiples of pitch (n=2) → one seed + pattern feature "
        "(pattern_rectangular/pattern_circular)"
    )


@pytest.mark.parametrize(
    ("dims", "fires"),
    [
        (["pitch*2", "pitch*3"], True),
        (["pitch * 2", "3*pitch"], True),
        (["pitch*2"], False),  # single multiple: no pattern
        (["2*pi*r"], False),  # product of names, not name*int
        (["2*pi*r", "3*pi*r"], False),
        (["width - 2*wall", "wall"], False),  # width-2*wall is not a pure multiple
        (["pitch*2", "pitch*2"], False),  # same int: dup_dim_expr's job
        (["pitch*2", "gap*3"], False),  # different names
    ],
)
def test_pattern_expr_rule(dims: list[str], fires: bool) -> None:
    assert ("pattern_expr" in _rules(check_facts(_facts(dims=dims)))) == fires


def test_pattern_expr_skip_turns_into_skip_warn() -> None:
    lines = check_facts(_facts(dims=["pitch*2", "pitch*3"]), skip={"pattern_expr": "two rows, deliberate"})
    assert lines == ["SKQ_WARN skq_skip: sk pattern_expr skipped: two rows, deliberate"]


# --- dim_ratio----------------------------------------------------------------------------------


def _distinct_dims(n: int) -> list[str]:
    return [f"{i + 1} mm" for i in range(n)]  # distinct, so dup_dim_expr stays quiet


def test_dim_ratio_hit() -> None:
    cons = [Cons("CoincidentConstraint")] * 3
    lines = check_facts(_facts(dims=_distinct_dims(4), cons=cons))
    assert _rules(lines) == ["dim_ratio"]


def test_dim_ratio_not_hit_at_three_dims() -> None:
    assert check_facts(_facts(dims=_distinct_dims(3), cons=[])) == []


def test_dim_ratio_not_hit_when_constraints_cover() -> None:
    cons = [Cons("CoincidentConstraint")] * 4
    assert check_facts(_facts(dims=_distinct_dims(4), cons=cons)) == []


def test_dim_ratio_recipe_text() -> None:
    assert check_facts(_facts(dims=_distinct_dims(4), cons=[])) == [
        (
            "SKQ_WARN dim_ratio: sk dims=4 > constraints=0 (over-dimensioned) -> "
            "replace point-coordinate dims with horizontal/vertical/coincident/collinear constraints"
        )
    ]


# --- fix_constraint -----------------------------------------------------------------------------


def test_fix_constraint_hit() -> None:
    ents = [Ent("p1", "point", is_fixed=True)]
    lines = check_facts(_facts(ents=ents))
    assert lines == ["SKQ_WARN fix_constraint: sk isFixed x1: p1 -> constrain instead"]


def test_fix_constraint_not_hit() -> None:
    assert check_facts(_facts(ents=[Ent("p1", "point")])) == []


def test_fix_constraint_skips_origin_point() -> None:
    assert check_facts(_facts(ents=[Ent("o", "point", is_fixed=True, is_origin=True)])) == []


def test_fix_constraint_skips_reference_point() -> None:
    # unconstrained reference point still gets projected_unused; only the fix_constraint line must be absent
    lines = check_facts(_facts(ents=[Ent("r", "point", is_fixed=True, is_reference=True)]))
    assert [line for line in lines if "fix_constraint" in line] == []


# --- sketch_pattern -----------------------------------------------------------------------------


def test_sketch_pattern_hit() -> None:
    lines = check_facts(_facts(cons=[Cons("RectangularPatternConstraint")]))
    assert _rules(lines) == ["sketch_pattern"]


def test_sketch_pattern_not_hit_for_tangent() -> None:
    assert check_facts(_facts(cons=[Cons("TangentConstraint", ("a", "b"))])) == []


# --- dup_dim_expr -------------------------------------------------------------------------------


def test_dup_dim_expr_hit() -> None:
    lines = check_facts(_facts(dims=["50 mm", "50  mm"]))
    assert _rules(lines) == ["dup_dim_expr"]
    assert "(e.g. 50mm x2)" in lines[0]


def test_dup_dim_expr_not_hit() -> None:
    assert check_facts(_facts(dims=["50 mm", "60 mm"])) == []


@pytest.mark.parametrize(
    ("a", "b", "dup"),
    [
        ("a/2", "-(-(a/2))", True),  # double negation collapses
        ("a/2", "-(-a/2)", True),  # -(-X) with X = a/2
        ("roller_length/2", "-(-(roller_length/2))", True),  # live-model shape
        ("(a/2)", "a/2", True),  # redundant outer parens
        ("a / 2", "a/2", True),  # whitespace
        ("a+b", "-(-a+b)", False),  # -(-a+b) = a-b, not a+b
        ("a/2", "-(a/2)", False),  # single negation is a different value
    ],
)
def test_dup_dim_expr_normalisation(a: str, b: str, dup: bool) -> None:
    lines = [line for line in check_facts(_facts(dims=[a, b])) if "dup_dim_expr" in line]
    assert len(lines) == (1 if dup else 0)


def test_dup_dim_expr_one_line_per_sketch() -> None:
    names = ["alpha", "beta", "gamma", "delta", "eps"]  # 5 expressions, each used twice
    lines = check_facts(_facts(dims=[*names, *names], cons=[Cons("CoincidentConstraint")] * 10))
    dup = [line for line in lines if "dup_dim_expr" in line]
    assert len(dup) == 1
    assert dup[0] == (
        "SKQ_WARN dup_dim_expr: sk 5 repeated exprs (e.g. alpha x2) -> "
        "horizontal/vertical/collinear for shared coordinates, equal/symmetry for equal lengths"
    )


# --- projected_unused ---------------------------------------------------------------------------


def test_projected_unused_hit() -> None:
    ents = [Ent("l1", "line", is_reference=True, in_profile=False)]
    assert _rules(check_facts(_facts(ents=ents))) == ["projected_unused"]


def test_projected_unused_not_hit_in_profile() -> None:
    ents = [Ent("l1", "line", is_reference=True, in_profile=True)]
    assert check_facts(_facts(ents=ents)) == []


def test_projected_unused_not_hit_when_constrained() -> None:
    ents = [Ent("l1", "line", is_reference=True)]
    assert check_facts(_facts(ents=ents, cons=[Cons("CoincidentConstraint", ("l1", "p9"))])) == []


def test_projected_unused_not_hit_when_dimensioned() -> None:
    ents = [Ent("l1", "line", is_reference=True)]
    assert check_facts(_facts(ents=ents, dims=["5 mm"], dim_refs=[("l1",)])) == []


# --- sketch_fillet ------------------------------------------------------------------------------


def _fillet_ents() -> list[Ent]:
    return [Ent("arc", "arc"), Ent("l1", "line"), Ent("l2", "line")]


def test_sketch_fillet_hit() -> None:
    cons = [Cons("TangentConstraint", ("arc", "l1")), Cons("TangentConstraint", ("arc", "l2"))]
    lines = check_facts(_facts(ents=_fillet_ents(), cons=cons))
    assert _rules(lines) == ["sketch_fillet"]


def test_sketch_fillet_not_hit_when_arc_dimensioned() -> None:
    cons = [Cons("TangentConstraint", ("arc", "l1")), Cons("TangentConstraint", ("arc", "l2"))]
    assert check_facts(_facts(ents=_fillet_ents(), cons=cons, dims=["4 mm"], dim_refs=[("arc",)])) == []


def test_sketch_fillet_not_hit_with_one_tangent_line() -> None:
    cons = [Cons("TangentConstraint", ("arc", "l1"))]
    assert check_facts(_facts(ents=_fillet_ents(), cons=cons)) == []


# --- skip / token -------------------------------------------------------------------------------


def test_skip_turns_fired_rule_into_skip_warn() -> None:
    lines = check_facts(_facts(dims=["50 mm", "50 mm"]), skip={"dup_dim_expr": "two equal holes on purpose"})
    assert lines == ["SKQ_WARN skq_skip: sk dup_dim_expr skipped: two equal holes on purpose"]


def test_skip_only_applies_to_named_rule() -> None:
    lines = check_facts(_facts(full=False, dims=["50 mm", "50 mm"]), skip={"dup_dim_expr": "x"})
    assert lines[0] == "SKQ_WARN underconstrained: sk not fully constrained"
    assert lines[1] == "SKQ_WARN skq_skip: sk dup_dim_expr skipped: x"
    assert len(lines) == 2


def test_skip_unknown_rule_warns_not_raises() -> None:
    lines = check_facts(_facts(), skip={"no_such_rule": "x"})
    assert lines == ["SKQ_WARN skq_skip_unknown: sk unknown rule(s) ['no_such_rule'] ignored"]


def test_skq_token_counts() -> None:
    lines = ["SKQ_FAIL underconstrained: a x", "SKQ_WARN expr_dim: a y", "SKQ_WARN skq_skip: a z"]
    assert skq_token(lines) == "1F/2W"
    assert skq_token([]) == "0F/0W"


# --- adapter + check() with fake Fusion objects ------------------------------------------------


class _Attrs:
    def __init__(self, value: str | None = None) -> None:
        self.value = value

    def itemByName(self, group: str, name: str) -> SimpleNamespace | None:
        if self.value is None or (group, name) != ("skq", "skq_skip"):
            return None
        return SimpleNamespace(value=self.value)


def _fake_sketch(name: str, *, full: bool, skip: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        isFullyConstrained=full,
        profiles=_Coll([]),
        sketchCurves=SimpleNamespace(sketchLines=_Coll([]), sketchArcs=_Coll([]), sketchCircles=_Coll([])),
        sketchPoints=_Coll([]),
        originPoint=None,
        geometricConstraints=_Coll([]),
        sketchDimensions=_Coll([]),
        attributes=_Attrs(skip),
    )


def test_check_design_underconstrained_fail() -> None:
    comp = SimpleNamespace(sketches=_Coll([_fake_sketch("frame", full=False)]))
    design: Any = SimpleNamespace(allComponents=_Coll([comp]))
    assert sketch_quality.check(design) == ["SKQ_WARN underconstrained: frame not fully constrained"]


def test_check_design_skip_attr_opt_out() -> None:
    sk = _fake_sketch("frame", full=False, skip="underconstrained:frame is a deliberate loose hinge")
    design: Any = SimpleNamespace(allComponents=_Coll([SimpleNamespace(sketches=_Coll([sk]))]))
    assert sketch_quality.check(design) == [
        "SKQ_WARN skq_skip: frame underconstrained skipped: frame is a deliberate loose hinge"
    ]


def test_check_sketches_reads_dimension_expression() -> None:
    sk = _fake_sketch("sk", full=True)
    dim = SimpleNamespace(parameter=SimpleNamespace(expression="width / 2"), entityOne=None, entityTwo=None)
    other = SimpleNamespace(parameter=SimpleNamespace(expression="width"), entityOne=None, entityTwo=None)
    sk.sketchDimensions = _Coll([other, dim])
    sketches: Any = _Coll([sk])
    lines = sketch_quality.check_sketches(sketches)
    assert lines == ["SKQ_WARN expr_dim: sk dim 'width / 2' -> midpoint / symmetry / pattern / equal constraint"]


# --- pattern_expr live skip attribute -----------------------------------------------------------


def test_live_pattern_expr_skip_attr() -> None:
    def dims(*exprs: str) -> _Coll[SimpleNamespace]:
        return _Coll(
            [SimpleNamespace(parameter=SimpleNamespace(expression=e), entityOne=None, entityTwo=None) for e in exprs]
        )

    fires = _fake_sketch("sk", full=True)
    fires.sketchDimensions = dims("pitch*2", "pitch*3")
    assert _check_live([fires]) == [
        (
            "SKQ_WARN pattern_expr: sk per-instance multiples of pitch (n=2) → one seed + pattern feature "
            "(pattern_rectangular/pattern_circular)"
        )
    ]
    skipped = _fake_sketch("sk", full=True, skip="pattern_expr:two rows on purpose")
    skipped.sketchDimensions = dims("pitch*2", "pitch*3")
    assert _check_live([skipped]) == ["SKQ_WARN skq_skip: sk pattern_expr skipped: two rows on purpose"]


# --- ref extraction from live-shaped fakes (attribute names per fusion.py stub) ------------------


def _check_live(sketches: list[SimpleNamespace]) -> list[str]:
    coll: Any = _Coll(sketches)
    return sketch_quality.check_sketches(coll)


def _curve(token: str, *, is_reference: bool = False) -> SimpleNamespace:
    return SimpleNamespace(entityToken=token, isFixed=False, isReference=is_reference)


def _live_sketch(
    *,
    lines: list[SimpleNamespace] | None = None,
    arcs: list[SimpleNamespace] | None = None,
    cons: list[SimpleNamespace] | None = None,
    dims: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    sk = _fake_sketch("sk", full=True)
    sk.sketchCurves = SimpleNamespace(
        sketchLines=_Coll(lines or []), sketchArcs=_Coll(arcs or []), sketchCircles=_Coll([])
    )
    sk.geometricConstraints = _Coll(cons or [])
    sk.sketchDimensions = _Coll(dims or [])
    return sk


def _tangent(curve_one: SimpleNamespace, curve_two: SimpleNamespace) -> SimpleNamespace:
    # stub TangentConstraint exposes only curveOne / curveTwo (no entityOne / entityTwo)
    return SimpleNamespace(objectType="adsk::fusion::TangentConstraint", curveOne=curve_one, curveTwo=curve_two)


def _fillet_sketch(dims: list[SimpleNamespace] | None = None) -> SimpleNamespace:
    arc = _curve("arc")
    l1, l2 = _curve("l1"), _curve("l2")
    return _live_sketch(lines=[l1, l2], arcs=[arc], cons=[_tangent(arc, l1), _tangent(arc, l2)], dims=dims)


def test_live_tangent_curves_feed_sketch_fillet() -> None:
    lines = _check_live([_fillet_sketch()])
    assert lines == ["SKQ_WARN sketch_fillet: sk arc arc tangent to 2 lines, no dimension -> 3D fillet"]


def test_live_coincident_point_entity_counts_as_used() -> None:
    # reference line l1 is tied in only by CoincidentConstraint(point, entity); old reader saw no refs
    l1 = _curve("l1", is_reference=True)
    coincident = SimpleNamespace(
        objectType="adsk::fusion::CoincidentConstraint", point=SimpleNamespace(entityToken="p9"), entity=l1
    )
    assert _check_live([_live_sketch(lines=[l1], cons=[coincident])]) == []


def test_live_radial_dimension_entity_excludes_arc_from_fillet() -> None:
    # control: same sketch without the dimension must fire (old reader fails here, so the test fails on old code)
    assert len(_check_live([_fillet_sketch()])) == 1
    radial = SimpleNamespace(
        objectType="adsk::fusion::SketchRadialDimension",
        entity=_curve("arc"),
        parameter=SimpleNamespace(expression="4 mm"),
    )
    assert _check_live([_fillet_sketch(dims=[radial])]) == []


def test_live_origin_point_not_flagged_fixed() -> None:
    origin = SimpleNamespace(entityToken="o", isFixed=True, isReference=False)
    sk = _live_sketch()
    sk.originPoint = origin
    sk.sketchPoints = _Coll([origin])
    assert _check_live([sk]) == []


def test_live_reference_fixed_point_not_flagged() -> None:
    ref = SimpleNamespace(entityToken="r", isFixed=True, isReference=True)
    origin = SimpleNamespace(entityToken="o", isFixed=False, isReference=False)
    sk = _live_sketch()
    sk.originPoint = origin
    sk.sketchPoints = _Coll([ref, origin])
    # unconstrained reference point still gets projected_unused; only the fix_constraint line must be absent
    assert [line for line in _check_live([sk]) if "fix_constraint" in line] == []


def test_live_plain_fixed_point_still_flagged() -> None:
    pt = SimpleNamespace(entityToken="p", isFixed=True, isReference=False)
    origin = SimpleNamespace(entityToken="o", isFixed=True, isReference=False)
    sk = _live_sketch()
    sk.originPoint = origin
    sk.sketchPoints = _Coll([origin, pt])
    assert _check_live([sk]) == ["SKQ_WARN fix_constraint: sk isFixed x1: p -> constrain instead"]


# --- malformed skq_skip -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "underconstrained",
        "underconstrained:",
        ":reason only",
        "underconstrained:x;underconstrained:y",
        "underconstrained:ok;junk",
    ],
)
def test_malformed_skip_warns_not_raises(value: str) -> None:
    sk = _fake_sketch("frame", full=False, skip=value)
    sketches: Any = _Coll([sk])
    lines = sketch_quality.check_sketches(sketches)
    assert len(lines) == 2
    assert lines[0].startswith("SKQ_WARN skq_skip_malformed: frame ")
    assert lines[1] == "SKQ_WARN underconstrained: frame not fully constrained"


def test_check_unknown_skip_rule_warns_and_keeps_other_findings() -> None:
    sk = _fake_sketch("frame", full=False, skip="bogus_rule:typo in rule name")
    design: Any = SimpleNamespace(allComponents=_Coll([SimpleNamespace(sketches=_Coll([sk]))]))
    assert sketch_quality.check(design) == [
        "SKQ_WARN skq_skip_unknown: frame unknown rule(s) ['bogus_rule'] ignored",
        "SKQ_WARN underconstrained: frame not fully constrained",
    ]


# --- report() wiring ----------------------------------------------------------------------------


def test_report_format_puts_skq_lines_after_summary() -> None:
    data = report.ReportData(
        name="comp",
        bodies=0,
        lumps=0,
        bbox_mm=None,
        vol_cm3=0.0,
        sketches_constrained=0,
        sketches_total=1,
        unhealthy=0,
        unnamed=0,
        numbered=0,
        findings=None,
        skq=["SKQ_WARN underconstrained: sk not fully constrained"],
    )
    lines = report.format_report(data).splitlines()
    assert lines[0].startswith("comp: bodies=0")
    assert lines[1] == "SKQ_WARN underconstrained: sk not fully constrained"


# --- build_check wiring -------------------------------------------------------------------------


def test_build_check_summary_skq_token_present_when_set() -> None:
    summary = build_check.Summary(
        interf=0,
        interf_lines=[],
        unhealthy=0,
        unnamed=0,
        unconstr=0,
        png=None,
        skq="0F/1W",
        skq_lines=["SKQ_WARN underconstrained: sk not fully constrained"],
    )
    lines = build_check.format_check([], summary).splitlines()
    assert lines[0] == "interf=0 unhealthy=0 timeline_unnamed=0 unconstr=0 png=none skq=0F/1W"
    assert lines[1] == "SKQ_WARN underconstrained: sk not fully constrained"
