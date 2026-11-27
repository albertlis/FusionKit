"""Offline perf guards for sketch_quality.check_sketches / check: COM-call count per sketch on fake Fusion objects,
_norm calls in expr_dim, and the per-sketch skq budget stop. No Fusion runtime.

Regression target: the skq stage ran >550 s live while the other stages took ~10 s. Sketch reads were measured
live at ~1 s for 116 sketches (pawl_noback), so these tests pin the sketch-side cost: linear in entities, one stop
check per sketch.
"""

from types import SimpleNamespace
from typing import Any

from fusion_kit import build_check, sketch_quality
from fusion_kit.sketch_quality import SketchFacts


class _Meter:
    """Counts fake COM calls: every attribute read on a _Obj and every count / item read on a _Coll."""

    def __init__(self) -> None:
        self.calls = 0


class _Obj:
    """Fake Fusion object. Each attribute read = one COM call. Missing attribute -> AttributeError (getattr default works)."""

    def __init__(self, meter: _Meter, **attrs: Any) -> None:
        object.__setattr__(self, "_meter", meter)
        object.__setattr__(self, "_attrs", attrs)

    def __getattr__(self, name: str) -> Any:
        attrs = object.__getattribute__(self, "_attrs")
        if name not in attrs:
            raise AttributeError(name)
        object.__getattribute__(self, "_meter").calls += 1
        return attrs[name]


class _Coll:
    """Fusion-style collection; meter None = uncounted (outer collections)."""

    def __init__(self, meter: _Meter | None, items: list[Any]) -> None:
        self._meter = meter
        self._items = items

    @property
    def count(self) -> int:
        if self._meter is not None:
            self._meter.calls += 1
        return len(self._items)

    def item(self, index: int) -> Any:
        if self._meter is not None:
            self._meter.calls += 1
        return self._items[index]


class _NoAttrs:
    def itemByName(self, group: str, name: str) -> None:
        return None


def _sketch(meter: _Meter, n: int, name: str = "sk", *, full: bool = True) -> _Obj:
    """n lines (one profile over all of them), n endpoint points + origin, n coincident constraints, n distinct dims."""
    origin = _Obj(meter, entityToken=f"{name}_o", isFixed=True, isReference=False)
    lines = [_Obj(meter, entityToken=f"{name}_l{i}", isFixed=False, isReference=False) for i in range(n)]
    points = [_Obj(meter, entityToken=f"{name}_p{i}", isFixed=False, isReference=False) for i in range(n)]
    cons = [
        _Obj(meter, objectType="adsk::fusion::CoincidentConstraint", point=points[i], entity=lines[i]) for i in range(n)
    ]
    dims = [
        _Obj(
            meter,
            objectType="adsk::fusion::SketchLinearDimension",
            parameter=_Obj(meter, expression=f"{i + 1} mm"),
        )
        for i in range(n)
    ]
    curves = _Obj(meter, sketchLines=_Coll(meter, lines), sketchArcs=_Coll(meter, []), sketchCircles=_Coll(meter, []))
    profile = _Obj(
        meter,
        profileLoops=_Coll(
            meter, [_Obj(meter, profileCurves=_Coll(meter, [_Obj(meter, sketchEntity=c) for c in lines]))]
        ),
    )
    return _Obj(
        meter,
        name=name,
        isFullyConstrained=full,
        profiles=_Coll(meter, [profile]),
        sketchCurves=curves,
        sketchPoints=_Coll(meter, [*points, origin]),
        originPoint=origin,
        geometricConstraints=_Coll(meter, cons),
        sketchDimensions=_Coll(meter, dims),
        attributes=_NoAttrs(),
    )


def _com_calls(sketches: list[_Obj], meter: _Meter) -> int:
    meter.calls = 0
    sketch_quality.check_sketches(_Coll(None, sketches))
    return meter.calls


# --- linear scaling of Fusion reads ----------------------------------------------------------------


def test_com_calls_scale_linearly_with_entities_in_one_sketch() -> None:
    meter = _Meter()
    c100 = _com_calls([_sketch(meter, 100)], meter)
    c200 = _com_calls([_sketch(meter, 200)], meter)
    c400 = _com_calls([_sketch(meter, 400)], meter)
    assert 1.8 <= c200 / c100 <= 2.2, (c100, c200)
    assert 1.8 <= c400 / c200 <= 2.2, (c200, c400)


def test_com_calls_scale_linearly_with_sketch_count() -> None:
    meter = _Meter()
    c50 = _com_calls([_sketch(meter, 20, f"a{i}") for i in range(50)], meter)
    c100 = _com_calls([_sketch(meter, 20, f"b{i}") for i in range(100)], meter)
    assert 1.8 <= c100 / c50 <= 2.2, (c50, c100)


def test_live_sized_design_com_calls_bounded() -> None:
    # ~150 sketches x ~20 entities (~3000 entities): one pass, a few COM reads per entity
    meter = _Meter()
    calls = _com_calls([_sketch(meter, 10, f"s{i}") for i in range(150)], meter)
    entities = 150 * (10 + 10 + 1)  # lines + points + origin
    assert calls <= 40 * entities, calls


# --- expr_dim: _norm called once per dim, not once per (dim, other dim) ------------------------------


def test_expr_dim_norm_calls_linear_in_dims(monkeypatch: Any) -> None:
    calls = {"n": 0}
    real_norm = sketch_quality._norm

    def counting_norm(expr: str) -> str:
        calls["n"] += 1
        return real_norm(expr)

    monkeypatch.setattr(sketch_quality, "_norm", counting_norm)
    d = 200
    facts = SketchFacts("sk", True, tuple(f"d{i} mm" for i in range(d)), tuple(() for _ in range(d)), (), ())
    assert sketch_quality._rule_expr_dim(facts) == []
    assert calls["n"] <= 3 * d, calls["n"]


def test_expr_dim_still_fires_with_precomputed_norms() -> None:
    facts = SketchFacts("sk", True, ("w", "w / 2", "z"), ((), (), ()), (), ())
    out = sketch_quality._rule_expr_dim(facts)
    assert out == [("expr_dim", "WARN", "dim 'w / 2' -> midpoint / symmetry / pattern / equal constraint")]


# --- in-stage skq budget ----------------------------------------------------------------------------


def test_skq_budget_stops_before_next_sketch() -> None:
    meter = _Meter()
    sketches = [_sketch(meter, 5, f"s{i}", full=False) for i in range(10)]
    seen = {"n": 0}

    def over() -> bool:
        seen["n"] += 1
        return seen["n"] > 3  # budget exhausted after 3 sketches

    meter.calls = 0
    lines = sketch_quality.check_sketches(_Coll(None, sketches), over_budget=over)
    under = [line for line in lines if line.startswith("SKQ_WARN underconstrained")]
    assert under == [
        "SKQ_WARN underconstrained: s0 not fully constrained",
        "SKQ_WARN underconstrained: s1 not fully constrained",
        "SKQ_WARN underconstrained: s2 not fully constrained",
    ]
    assert "SKIP skq budget: 7 of 10 sketches not checked" in lines
    # cross-comp WARN groups only over the 3 checked sketches (fakes carry no objectType: one shared fingerprint)
    cross = [line for line in lines if line.startswith("SKQ_WARN dup_sketch_cross_comp")]
    assert cross == [
        "SKQ_WARN dup_sketch_cross_comp: s0, s1, s2 same profile -> "
        "one sketch + extrudes as new bodies / copy body / pattern"
    ]
    budget_calls = meter.calls
    assert budget_calls == _com_calls(sketches[:3], meter)  # no Fusion reads for the 7 skipped sketches


def test_skq_budget_none_checks_everything() -> None:
    meter = _Meter()
    sketches = [_sketch(meter, 3, f"s{i}", full=False) for i in range(4)]
    lines = sketch_quality.check_sketches(_Coll(None, sketches), over_budget=None)
    assert len([line for line in lines if line.startswith("SKQ_WARN underconstrained")]) == 4
    assert not any(line.startswith("SKIP") for line in lines)


def test_build_check_budget_over_limit_stops_skq_across_components() -> None:
    meter = _Meter()
    budget_over = build_check._Budget(frozenset({"skq"}), -1.0)  # any elapsed time > -1 s
    assert budget_over.over_limit() is True
    assert build_check._Budget(frozenset({"skq"}), None).over_limit() is False
    design: Any = SimpleNamespace(
        allComponents=_Coll(
            None,
            [
                SimpleNamespace(sketches=_Coll(None, [_sketch(meter, 3, "a")])),
                SimpleNamespace(sketches=_Coll(None, [_sketch(meter, 3, "b"), _sketch(meter, 3, "c")])),
            ],
        )
    )
    meter.calls = 0
    lines = sketch_quality.check(design, over_budget=budget_over.over_limit)
    assert lines == ["SKIP skq budget: 3 of 3 sketches not checked"]
    assert meter.calls == 0
