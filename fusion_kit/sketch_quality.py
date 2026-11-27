"""Sketch quality lines for report() and build_check: SKQ_FAIL / SKQ_WARN.

Rules run over every sketch of a component (report) or of the whole design (check). Rule functions are pure over
SketchFacts; sketch_facts() is the only Fusion reader. Opt-out: sketch attribute group "skq", name "skq_skip",
value "<rule>:<reason>[;<rule>:<reason>...]" (same grammar as dfam_skip, dfam.parse_skip). A skipped rule that fires
emits one SKQ_WARN skq_skip line instead of its FAIL/WARN. A malformed skq_skip value emits one SKQ_WARN
skq_skip_malformed line and all rules run unskipped.
Live Fusion names (profiles, sketchCurves, sketchPoints, geometricConstraints / sketchDimensions entityOne/entityTwo)
are covered by offline fakes only; not yet run against live Fusion.
"""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

import adsk.fusion

from fusion_kit.dfam import parse_skip

SKQ_GROUP = "skq"
SKQ_SKIP_ATTR = "skq_skip"
DIM_RATIO_MIN_DIMS = 3  # dim_ratio fires only above this many dims (plan: "dims > 3")
TANGENT_KIND = "TangentConstraint"
PATTERN_KINDS: frozenset[str] = frozenset({"RectangularPatternConstraint", "CircularPatternConstraint"})
CROSS_RULE = "dup_sketch_cross_comp"  # design-wide WARN: same profile in >= 2 sketches (check() only, not per comp)
CROSS_TRIVIAL_MIN = 3  # group of single-circle profiles is reported only from this many members up
FINGERPRINT_DIGITS = 2  # profile curve lengths / radii rounded to 0.01 mm
_CM_TO_MM = 10.0

# Entity-ref attributes per objectType tail, from .fusion_defs/adsk/fusion.py (class line in brackets).
# Names are exact: a stub class missing from the table has no entity refs (e.g. ConcentricCircle dim: none listed).
# List-valued attributes (entities, parentCurves, childCurves, points) are expanded by _tokens.
CONS_REF_ATTRS: dict[str, tuple[str, ...]] = {
    "CircularPatternConstraint": ("entities", "centerPoint"),  # [48014]
    "CoincidentConstraint": ("point", "entity"),  # [48301]
    "CoincidentToSurfaceConstraint": ("point",),  # [48341]
    "CollinearConstraint": ("lineOne", "lineTwo"),  # [48381]
    "ConcentricConstraint": ("entityOne", "entityTwo"),  # [49137]
    "EqualConstraint": ("curveOne", "curveTwo"),  # [56492]
    "HorizontalConstraint": ("line",),  # [58422]
    "HorizontalPointsConstraint": ("pointOne", "pointTwo"),  # [58456]
    "LineOnPlanarSurfaceConstraint": ("line",),  # [58696]
    "LineParallelToPlanarSurfaceConstraint": ("line",),  # [58736]
    "MidPointConstraint": ("point", "midPointCurve"),  # [59346]
    "OffsetConstraint": ("parentCurves", "childCurves"),  # [60341]
    "ParallelConstraint": ("lineOne", "lineTwo"),  # [60649]
    "PerpendicularConstraint": ("line", "curve"),  # [61182]
    "PerpendicularToSurfaceConstraint": ("curve",),  # [61222]
    "PolygonConstraint": ("points", "centerPoint"),  # [63319]
    "RectangularPatternConstraint": ("entities", "directionOneEntity", "directionTwoEntity"),  # [63401]
    "SmoothConstraint": ("curveOne", "curveTwo"),  # [66315]
    "SymmetryConstraint": ("entityOne", "entityTwo", "symmetryLine"),  # [67598]
    "TangentConstraint": ("curveOne", "curveTwo"),  # [67644]
    "VerticalConstraint": ("line",),  # [69605]
    "VerticalPointsConstraint": ("pointOne", "pointTwo"),  # [69639]
}
DIM_REF_ATTRS: dict[str, tuple[str, ...]] = {
    "SketchAngularDimension": ("lineOne", "lineTwo"),  # [65374]
    "SketchDiameterDimension": ("entity",),  # [65539]
    "SketchDistanceBetweenLineAndPlanarSurfaceDimension": ("line",),  # [65573]
    "SketchDistanceBetweenPointAndSurfaceDimension": ("point",),  # [65613]
    "SketchLinearDiameterDimension": ("line", "entityTwo"),  # [65721]
    "SketchLinearDimension": ("entityOne", "entityTwo"),  # [65761]
    "SketchOffsetDimension": ("line", "entityTwo"),  # [65842]
    "SketchRadialDimension": ("entity",),  # [66017]
    "SketchTangentDistanceDimension": ("entityOne",),  # [66051]
}

T_co = TypeVar("T_co", covariant=True)


class _Coll(Protocol[T_co]):
    """Fusion collection surface used here: count property + item(index)."""

    @property
    def count(self) -> int: ...

    def item(self, index: int) -> T_co: ...


@dataclass(frozen=True)
class Ent:
    token: str
    kind: str  # line | arc | circle | point
    is_fixed: bool = False
    is_reference: bool = False
    in_profile: bool = False
    is_origin: bool = False  # sketch origin point (always isFixed; never a constrain-instead target)


@dataclass(frozen=True)
class Cons:
    kind: str  # objectType tail, e.g. TangentConstraint
    refs: tuple[str, ...] = ()  # entity tokens the constraint names (entityOne / entityTwo)


Sig = tuple[tuple[str, float], ...]  # sorted (curve kind, rounded mm) over all profile curves of a sketch


@dataclass(frozen=True)
class SketchFacts:
    name: str
    fully_constrained: bool
    dim_exprs: tuple[str, ...]  # parameter expression per sketch dimension, "" when none
    dim_refs: tuple[tuple[str, ...], ...]  # entity tokens per dimension
    cons: tuple[Cons, ...]
    ents: tuple[Ent, ...]
    profile_sig: Sig = ()  # () = no profile curves (never grouped by dup_sketch_cross_comp)


Finding = tuple[str, str, str]  # (rule, level "FAIL" | "WARN", detail)


def half_operand(expr: str) -> str | None:
    """X when expr is exactly 'X / 2' (divisor 2 only: 2.5, 20, '2 * X', '+'/'-'/'*' in X -> None). Pure.

    -> X when expr is exactly `X / 2` (divisor 2 only; 2.5, 20, `2*X`, operators in X -> None)
    (expr_dim rule). Pure
    """
    m = re.fullmatch(r"([^+\-*/()]+?)\s*/\s*2", expr.strip())
    operand = m.group(1).strip() if m else ""
    return operand or None


_MULT_NAME_FIRST = re.compile(r"([A-Za-z_]\w*)\s*\*\s*(\d+)")  # pitch*2
_MULT_NUM_FIRST = re.compile(r"(\d+)\s*\*\s*([A-Za-z_]\w*)")  # 2*pitch


def pattern_multiple(expr: str) -> tuple[str, int] | None:
    """(name, n) when expr is exactly `name*n` or `n*name` with integer n >= 2 (pattern_expr rule). Pure.

    -> (name, n) when expr is exactly `name*n` or `n*name` with integer n >= 2 (pattern_expr rule).
    Pure
    """
    text = expr.strip()
    m = _MULT_NAME_FIRST.fullmatch(text)
    if m:
        name, n = m.group(1), int(m.group(2))
    else:
        m = _MULT_NUM_FIRST.fullmatch(text)
        if m is None:
            return None
        n, name = int(m.group(1)), m.group(2)
    return (name, n) if n >= 2 else None


_DOUBLE_NEG_PAREN = re.compile(r"(?<![\w.)])-\(-\(([^()]*)\)\)")  # -(-(X)) -> X, X may hold +/-
_DOUBLE_NEG_TERM = re.compile(r"(?<![\w.)])-\(-([\w.*/]+)\)")  # -(-X) -> X, X without +/- (-(-a+b) stays)


def _strip_outer_parens(s: str) -> str:
    """'(X)' -> 'X' only when the first '(' closes at the end ('(a)+(b)' stays)."""
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if depth == 0 and i < len(s) - 1:
                return s
        s = s[1:-1]
    return s


def _norm(expr: str) -> str:
    """Canonical form for equality: no whitespace, -(-X) -> X, redundant outer parens stripped. Pure."""
    s = "".join(expr.split())
    while True:
        prev = s
        s = _DOUBLE_NEG_PAREN.sub(r"\1", s)
        s = _DOUBLE_NEG_TERM.sub(r"\1", s)
        s = _strip_outer_parens(s)
        if s == prev:
            return s


def _rule_underconstrained(f: SketchFacts) -> list[Finding]:
    return [] if f.fully_constrained else [("underconstrained", "WARN", "not fully constrained")]


def _rule_sketch_fillet(f: SketchFacts) -> list[Finding]:
    line_tokens = {e.token for e in f.ents if e.kind == "line"}
    dim_tokens = {t for refs in f.dim_refs for t in refs}
    out: list[Finding] = []
    for arc in (e for e in f.ents if e.kind == "arc"):
        if arc.token in dim_tokens:
            continue
        partners = {
            r
            for c in f.cons
            if c.kind == TANGENT_KIND and arc.token in c.refs
            for r in c.refs
            if r != arc.token and r in line_tokens
        }
        if len(partners) >= 2:
            out.append(
                (
                    "sketch_fillet",
                    "WARN",
                    f"arc {arc.token} tangent to {len(partners)} lines, no dimension -> 3D fillet",
                )
            )
    return out


def _rule_expr_dim(f: SketchFacts) -> list[Finding]:
    """dim 'X / 2' where X is the expression of another dimension in this sketch -> midpoint / symmetric recipe."""
    out: list[Finding] = []
    norms = [_norm(o) for o in f.dim_exprs]  # once per dim: was O(D^2) _norm calls (rebuilt 'others' per dim)
    counts = Counter(norms)
    for i, e in enumerate(f.dim_exprs):
        x = half_operand(e)
        if x is None:
            continue
        target = _norm(x)
        # some OTHER dim j != i has norms[j] == target
        if counts[target] - int(norms[i] == target) > 0:
            out.append(("expr_dim", "WARN", f"dim '{e}' -> midpoint / symmetry / pattern / equal constraint"))
    return out


def _rule_dim_ratio(f: SketchFacts) -> list[Finding]:
    n_dim = len(f.dim_exprs)
    if n_dim > DIM_RATIO_MIN_DIMS and n_dim > len(f.cons):
        return [
            (
                "dim_ratio",
                "WARN",
                (
                    f"dims={n_dim} > constraints={len(f.cons)} (over-dimensioned) -> "
                    "replace point-coordinate dims with horizontal/vertical/coincident/collinear constraints"
                ),
            )
        ]
    return []


def _rule_fix_constraint(f: SketchFacts) -> list[Finding]:
    fixed = [e.token for e in f.ents if e.is_fixed and not (e.is_reference or e.is_origin)]
    if not fixed:
        return []
    return [("fix_constraint", "WARN", f"isFixed x{len(fixed)}: {', '.join(fixed)} -> constrain instead")]


def _rule_sketch_pattern(f: SketchFacts) -> list[Finding]:
    kinds = sorted({c.kind for c in f.cons if c.kind in PATTERN_KINDS})
    if not kinds:
        return []
    return [("sketch_pattern", "WARN", f"{', '.join(kinds)} in sketch -> pattern feature")]


def _rule_dup_dim_expr(f: SketchFacts) -> list[Finding]:
    """One line per sketch: count of repeated exprs, most-repeated example."""
    counts = Counter(n for n in (_norm(e) for e in f.dim_exprs) if n)
    dup = sorted(((expr, k) for expr, k in counts.items() if k >= 2), key=lambda t: (-t[1], t[0]))
    if not dup:
        return []
    top_expr, top_k = dup[0]
    return [
        (
            "dup_dim_expr",
            "WARN",
            (
                f"{len(dup)} repeated exprs (e.g. {top_expr} x{top_k}) -> "
                "horizontal/vertical/collinear for shared coordinates, equal/symmetry for equal lengths"
            ),
        )
    ]


def _rule_pattern_expr(f: SketchFacts) -> list[Finding]:
    """>= 2 dims `name*n` / `n*name` on one name with >= 2 distinct n -> per-instance copies, use one seed + pattern."""
    by_name: dict[str, list[int]] = {}
    for e in f.dim_exprs:
        m = pattern_multiple(e)
        if m is not None:
            by_name.setdefault(m[0], []).append(m[1])
    return [
        (
            "pattern_expr",
            "WARN",
            (
                f"per-instance multiples of {name} (n={len(ns)}) → one seed + pattern feature "
                "(pattern_rectangular/pattern_circular)"
            ),
        )
        for name, ns in sorted(by_name.items())
        if len(set(ns)) >= 2
    ]


def _rule_projected_unused(f: SketchFacts) -> list[Finding]:
    used = {t for c in f.cons for t in c.refs} | {t for refs in f.dim_refs for t in refs}
    return [
        ("projected_unused", "WARN", f"projected {e.token} not in any profile, no constraint or dimension")
        for e in f.ents
        if e.is_reference and not e.in_profile and e.token not in used
    ]


# Single source of truth: rule name -> rule function. RULES is derived from it.
_RULE_FUNCS: dict[str, Callable[[SketchFacts], list[Finding]]] = {
    "underconstrained": _rule_underconstrained,
    "sketch_fillet": _rule_sketch_fillet,
    "expr_dim": _rule_expr_dim,
    "dim_ratio": _rule_dim_ratio,
    "fix_constraint": _rule_fix_constraint,
    "sketch_pattern": _rule_sketch_pattern,
    "dup_dim_expr": _rule_dup_dim_expr,
    "pattern_expr": _rule_pattern_expr,
    "projected_unused": _rule_projected_unused,
}
RULES: frozenset[str] = frozenset(_RULE_FUNCS) | {CROSS_RULE}


def check_facts(f: SketchFacts, skip: dict[str, str] | None = None) -> list[str]:
    """SKQ lines for one sketch. skip = {rule: reason}: a skipped rule that fires -> one SKQ_WARN skq_skip line.
    Unknown skip rule -> one SKQ_WARN skq_skip_unknown line, that rule ignored (never raises). Pure.

    -> SKQ_FAIL/SKQ_WARN lines for one SketchFacts; skip {rule: reason} -> SKQ_WARN skq_skip;
    unknown rule -> SKQ_WARN skq_skip_unknown (ignored, never raises). Pure
    """
    skip = skip or {}
    unknown = sorted(set(skip) - RULES)
    lines: list[str] = []
    if unknown:
        lines.append(f"SKQ_WARN skq_skip_unknown: {f.name} unknown rule(s) {unknown} ignored")
    known = {rule: reason for rule, reason in skip.items() if rule in RULES}
    skipped_seen: set[str] = set()
    for fn in _RULE_FUNCS.values():
        for rule, level, detail in fn(f):
            if rule in known:
                if rule not in skipped_seen:
                    skipped_seen.add(rule)
                    lines.append(f"SKQ_WARN skq_skip: {f.name} {rule} skipped: {known[rule]}")
                continue
            lines.append(f"SKQ_{level} {rule}: {f.name} {detail}")
    return lines


def skq_token(lines: list[str]) -> str:
    """'<F>F/<W>W' count of SKQ_FAIL / SKQ_WARN lines (summary keyword value). Pure.

    -> '<F>F/<W>W' (summary keyword value). Pure
    """
    fails = sum(1 for line in lines if line.startswith("SKQ_FAIL "))
    warns = sum(1 for line in lines if line.startswith("SKQ_WARN "))
    return f"{fails}F/{warns}W"


def curve_sig(curve: object) -> tuple[str, float]:
    """(objectType tail, mm) for one profile curve: SketchLine -> length, SketchArc / SketchCircle -> radius,
    other kinds 0.0. Fusion lengths are cm; rounded to FINGERPRINT_DIGITS. Pure over duck-typed objects.

    -> (kind, mm) for one profile curve
    """
    kind = _kind(curve)
    if kind == "SketchLine":
        size = getattr(curve, "length", 0.0)
    elif kind in ("SketchArc", "SketchCircle"):
        size = getattr(curve, "radius", 0.0)
    else:
        size = 0.0
    return kind, round(float(size) * _CM_TO_MM, FINGERPRINT_DIGITS)


def _trivial_sig(sig: Sig) -> bool:
    """Exactly one curve and it is a circle."""
    return len(sig) == 1 and sig[0][0] == "SketchCircle"


def cross_dup_lines(items: list[tuple[str, Sig, bool]]) -> list[str]:
    """items: (sketch name, profile_sig, skip CROSS_RULE) per checked sketch. One SKQ_WARN dup_sketch_cross_comp line
    per group of >= 2 sketches with equal non-empty profile_sig (skipped members left out). Single-circle groups need
    CROSS_TRIVIAL_MIN members. Dict grouping + sort inside each sig: O(n log n) in sketches. Pure.

    -> one SKQ_WARN line per duplicate group, first-seen order
    """
    groups: dict[Sig, list[str]] = {}
    for name, sig, skipped in items:
        if sig and not skipped:
            groups.setdefault(sig, []).append(name)
    out: list[str] = []
    for sig, names in groups.items():
        if len(names) < 2 or (_trivial_sig(sig) and len(names) < CROSS_TRIVIAL_MIN):
            continue
        out.append(
            f"SKQ_WARN {CROSS_RULE}: {', '.join(names)} same profile -> "
            "one sketch + extrudes as new bodies / copy body / pattern"
        )
    return out


def _items[T](coll: _Coll[T]) -> list[T]:
    return [coll.item(i) for i in range(coll.count)]


def _kind(obj: object) -> str:
    """adsk objectType 'adsk::fusion::TangentConstraint' -> 'TangentConstraint'; '' when absent."""
    t = getattr(obj, "objectType", "")
    return t.rsplit("::", 1)[-1] if isinstance(t, str) else ""


def _tokens(obj: object, names: tuple[str, ...]) -> tuple[str, ...]:
    """entityToken of each named ref attribute; list-valued attributes expanded; None skipped."""
    out: list[str] = []
    for name in names:
        val = getattr(obj, name, None)
        for ent in val if isinstance(val, list) else [val]:
            if ent is not None:
                out.append(str(ent.entityToken))
    return tuple(out)


def sketch_facts(sk: adsk.fusion.Sketch) -> SketchFacts:
    """Read SketchFacts from a live sketch. Only Fusion reader in this module.

    -> SketchFacts read from a live sketch (profiles, sketchCurves, sketchPoints,
    geometricConstraints, sketchDimensions)
    """
    in_profile: set[str] = set()
    sig: list[tuple[str, float]] = []
    for prof in _items(sk.profiles):
        for loop in _items(prof.profileLoops):
            for pc in _items(loop.profileCurves):
                curve = pc.sketchEntity
                in_profile.add(curve.entityToken)
                sig.append(curve_sig(curve))
    origin = sk.originPoint
    origin_token = origin.entityToken if origin is not None else None
    ents: list[Ent] = []
    curves = sk.sketchCurves
    for coll, kind in (
        (curves.sketchLines, "line"),
        (curves.sketchArcs, "arc"),
        (curves.sketchCircles, "circle"),
    ):
        for c in _items(coll):
            tok = c.entityToken  # one COM read per entity (was 2: token read twice)
            ents.append(Ent(tok, kind, c.isFixed, c.isReference, tok in in_profile))
    for p in _items(sk.sketchPoints):
        tok = p.entityToken
        ents.append(Ent(tok, "point", p.isFixed, p.isReference, is_origin=tok == origin_token))
    cons: list[Cons] = []
    for c in _items(sk.geometricConstraints):
        kind = _kind(c)  # objectType read once per constraint (was 2-3)
        cons.append(Cons(kind, _tokens(c, CONS_REF_ATTRS.get(kind, ()))))
    dims = _items(sk.sketchDimensions)
    dim_exprs = tuple(d.parameter.expression if d.parameter is not None else "" for d in dims)
    dim_refs = tuple(_tokens(d, DIM_REF_ATTRS.get(_kind(d), ())) for d in dims)
    return SketchFacts(
        sk.name, sk.isFullyConstrained, dim_exprs, dim_refs, tuple(cons), tuple(ents), tuple(sorted(sig))
    )


def skip_of(sk: adsk.fusion.Sketch) -> tuple[dict[str, str], list[str]]:
    """skq_skip attribute -> ({rule: reason}, warn lines). Absent -> ({}, []). Malformed (no ':', empty rule or
    reason, rule given twice) -> ({}, [one SKQ_WARN skq_skip_malformed]); rules then run unskipped. Never raises.
    Unknown rule -> SKQ_WARN skq_skip_unknown (check_facts).

    -> ({rule: reason}, warn lines) from attribute ("skq", "skq_skip"); absent -> ({}, []);
    malformed -> ({}, [SKQ_WARN skq_skip_malformed]), never raises
    """
    attr = sk.attributes.itemByName(SKQ_GROUP, SKQ_SKIP_ATTR)
    if attr is None:
        return {}, []
    try:
        return parse_skip(attr.value), []
    except ValueError:
        return {}, [
            (
                f"SKQ_WARN skq_skip_malformed: {sk.name} skq_skip {attr.value!r} ignored, "
                "expected <rule>:<reason>[;<rule>:<reason>...]"
            )
        ]


def _check_each(
    sketches: list[adsk.fusion.Sketch], over_budget: Callable[[], bool] | None
) -> tuple[list[str], list[tuple[str, Sig, bool]]]:
    """SKQ lines per sketch, one pass, plus (name, profile_sig, skip CROSS_RULE) per checked sketch for cross_dup_lines.
    over_budget() is asked BEFORE each sketch: True -> stop, one
    'SKIP skq budget: <k> of <N> sketches not checked' line. So the stage overruns the budget by at most one sketch."""
    out: list[str] = []
    sigs: list[tuple[str, Sig, bool]] = []
    for i, sk in enumerate(sketches):
        if over_budget is not None and over_budget():
            out.append(f"SKIP skq budget: {len(sketches) - i} of {len(sketches)} sketches not checked")
            break
        skip, warns = skip_of(sk)
        out.extend(warns)
        facts = sketch_facts(sk)
        out.extend(check_facts(facts, skip))
        sigs.append((facts.name, facts.profile_sig, CROSS_RULE in skip))
    return out, sigs


def check_sketches(sketches: _Coll[adsk.fusion.Sketch], over_budget: Callable[[], bool] | None = None) -> list[str]:
    """SKQ lines over a sketches collection (one component's sketches for report()), then dup_sketch_cross_comp
    lines over the same collection only (same-comp duplicates; design-wide duplicates come from check()).
    over_budget: optional stop test, asked before each sketch (see _check_each). None = no budget.

    -> SKQ lines over one collection of sketches (report() uses the comp's sketches)
    """
    lines, sigs = _check_each(_items(sketches), over_budget)
    return lines + cross_dup_lines(sigs)


def check(design: adsk.fusion.Design, over_budget: Callable[[], bool] | None = None) -> list[str]:
    """SKQ lines over all sketches of all components in the design, then design-wide dup_sketch_cross_comp WARN lines.
    over_budget: optional stop test, asked before each sketch across all components (build_check).

    -> SKQ lines over all sketches of all components (build_check)
    """
    sketches: list[adsk.fusion.Sketch] = []
    for comp in _items(design.allComponents):
        sketches.extend(_items(comp.sketches))
    lines, sigs = _check_each(sketches, over_budget)
    return lines + cross_dup_lines(sigs)
