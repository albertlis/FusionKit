"""param_sweep: pure flag/format logic + sweep_params on a fake design (no Fusion, no live adsk)."""

import math
import sys
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit import param_sweep as ps_mod
from fusion_kit.dfam import DFAM_GROUP
from fusion_kit.features import WARP_GROOVE_ATTR
from fusion_kit.param_sweep import (
    BodyFact,
    ItemFact,
    Probe,
    SweepRow,
    _centres_mm,
    _print_fail_reason,
    candidates,
    format_sweep,
    geometry,
    judge,
    same_geometry,
    sweep_params,
    sweep_token,
    unhealthy_names,
)
from fusion_kit.printability import IDENTITY, PrintReport, Rot, rot_x

_FUS = sys.modules["adsk.fusion"]
_sweep: Callable[..., list[SweepRow]] = sweep_params  # fakes are not adsk.fusion.Design: loose typing for the fake only
HEALTHY = _FUS.FeatureHealthStates.HealthyFeatureHealthState
DAMAGED = _FUS.FeatureHealthStates.ErrorFeatureHealthState


@pytest.fixture(autouse=True)
def _fake_body_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """dfam_check / check_body need a real BRepBody: fake bodies carry their verdict in .verdict instead."""
    monkeypatch.setattr(ps_mod, "body_verdict", lambda body, up_rot: body.verdict)


def _probe(
    value: str,
    *,
    unhealthy: tuple[str, ...] = (),
    bodies: int = 1,
    vol: float = 1.0,
    box: float = 10.0,
    error: str | None = None,
    dfam: tuple[str, ...] = (),
    ovh: float = 0.0,
    print_ok: bool = True,
    print_reason: str = "",
    com: tuple[tuple[float, float, float], ...] = (),
) -> Probe:
    box_mm = None if bodies == 0 else (0.0, 0.0, 0.0, box, 10.0, 10.0)
    return Probe(
        value=value,
        error=error,
        unhealthy=unhealthy,
        bodies=bodies,
        vol_cm3=vol,
        box_mm=box_mm,
        com_mm=com,
        dfam_fails=dfam,
        overhang_mm2=ovh,
        print_ok=print_ok,
        print_reason=print_reason,
    )


# ---- pure: candidates / unhealthy / geometry / same_geometry ----


def test_candidates_baseline_then_min_max() -> None:
    assert candidates("5 mm", {"min": "2 mm", "max": "9 mm"}) == ["5 mm", "2 mm", "9 mm"]


def test_candidates_values_mode_and_dedupe() -> None:
    assert candidates("1", {"values": ["0", "1"]}) == ["1", "0"]
    assert candidates("2 mm", {"min": "2 mm", "max": "2 mm"}) == ["2 mm"]


def test_candidates_without_range_is_baseline_only() -> None:
    assert candidates("5 mm", {"no_geometry": True}) == ["5 mm"]


def test_unhealthy_names_skips_groups_suppressed_and_healthy() -> None:
    items = [
        ItemFact("plate", False, False, True),
        ItemFact("grp", True, False, False),
        ItemFact("hole_off", False, True, False),
        ItemFact("boss", False, False, False),
        ItemFact("chamfer", False, False, False),
    ]
    assert unhealthy_names(items) == ("boss", "chamfer")


def test_geometry_totals_and_bbox_mm() -> None:
    bodies = [
        BodyFact(2.0, (0.0, 0.0, 0.0), (1.0, 2.0, 3.0), (0.5, 1.0, 1.5)),
        BodyFact(0.5, (-1.0, 0.0, 0.0), (0.0, 1.0, 1.0), (-0.5, 0.5, 0.5)),
    ]
    count, vol, box = geometry(bodies)
    assert count == 2
    assert vol == pytest.approx(2.5)
    assert box == pytest.approx((-10.0, 0.0, 0.0, 10.0, 20.0, 30.0))


def test_geometry_empty_has_no_box() -> None:
    assert geometry([]) == (0, 0.0, None)


def test_centres_mm_converts_cm_and_keeps_body_order() -> None:
    bodies = [
        BodyFact(2.0, (0.0, 0.0, 0.0), (1.0, 2.0, 3.0), (0.5, 1.0, 1.5)),
        BodyFact(0.5, (-1.0, 0.0, 0.0), (0.0, 1.0, 1.0), (-0.5, 0.25, 0.75)),
    ]
    got = _centres_mm(bodies)
    assert len(got) == 2
    assert got[0] == pytest.approx((5.0, 10.0, 15.0))
    assert got[1] == pytest.approx((-5.0, 2.5, 7.5))
    assert _centres_mm([]) == ()


def test_same_geometry_tolerance_and_mismatch() -> None:
    a = _probe("a")
    assert same_geometry(a, _probe("b"))
    assert not same_geometry(a, _probe("b", vol=1.01))
    assert not same_geometry(a, _probe("b", box=11.0))
    assert not same_geometry(a, _probe("b", bodies=2))
    assert not same_geometry(a, _probe("b", error="Expression is invalid."))


def test_same_geometry_com_shift_detected() -> None:
    """Same volume and bbox, one body's centre of mass moved 0.5 mm (hole slid inside body): not same geometry."""
    a = _probe("a", com=((5.0, 5.0, 5.0),))
    assert same_geometry(a, _probe("b", com=((5.0, 5.0, 5.0),)))
    assert same_geometry(a, _probe("b", com=((5.0 + 5e-4, 5.0, 5.0),)))  # within 1e-3 mm tolerance
    assert not same_geometry(a, _probe("b", com=((5.5, 5.0, 5.0),)))


# ---- pure: judge ----


def test_judge_clean_sweep_passes() -> None:
    probes = [_probe("5 mm"), _probe("2 mm", vol=2.0, box=2.0), _probe("9 mm", vol=9.0, box=9.0)]
    assert judge("wall", probes, no_geometry=False) == ()


def test_judge_unhealthy_lists_names() -> None:
    probes = [_probe("5 mm"), _probe("9 mm", unhealthy=("boss", "chamfer_1"), vol=9.0, box=9.0)]
    assert judge("wall", probes, no_geometry=False) == ("DFAM_FAIL param_sweep: wall=9 mm unhealthy: boss, chamfer_1",)


def test_judge_body_count_vs_baseline_and_expected() -> None:
    probes = [_probe("5", bodies=2), _probe("0", bodies=1, vol=0.5)]
    assert judge("screw_hole_count", probes, no_geometry=True) == (
        "DFAM_FAIL param_sweep: screw_hole_count=0 bodies=1 expected=2",
    )
    assert judge("n", [_probe("5 mm", bodies=2)], no_geometry=True, bodies_expected=3) == (
        "DFAM_FAIL param_sweep: n=5 mm bodies=2 expected=3",
    )


def test_judge_rejected_expression_flagged() -> None:
    probes = [_probe("5 mm"), _probe("0 mm", bodies=0, error="Expression is invalid.")]
    assert judge("wall", probes, no_geometry=False) == (
        "DFAM_FAIL param_sweep: wall=0 mm rejected by Fusion: Expression is invalid.",
    )


def test_judge_no_effect_is_build_only_fail() -> None:
    probes = [_probe("5 mm"), _probe("2 mm"), _probe("9 mm")]
    assert judge("print_orientation", probes, no_geometry=False) == (
        "DFAM_FAIL param_sweep: print_orientation no effect on geometry (BUILD-ONLY param) over 5 mm, 2 mm, 9 mm",
    )


def test_judge_no_geometry_skips_no_effect() -> None:
    assert judge("label_text", [_probe("5 mm"), _probe("2 mm")], no_geometry=True) == ()


def test_judge_single_probe_never_no_effect() -> None:
    assert judge("p", [_probe("5 mm")], no_geometry=False) == ()


def test_judge_empty_raises() -> None:
    with pytest.raises(ValueError, match="no probes"):
        judge("p", [], no_geometry=False)


def test_format_sweep_and_token_exact() -> None:
    rows = [
        SweepRow("wall", "5 mm", (), ("DFAM_FAIL param_sweep: wall=0 mm bodies=0 expected=1",)),
        SweepRow("gap", "1 mm", (), ()),
    ]
    assert format_sweep(rows) == [
        "param_sweep: 2 params, 1 fail",
        "DFAM_FAIL param_sweep: wall=0 mm bodies=0 expected=1",
    ]
    assert sweep_token(rows) == "2p/1F"
    assert format_sweep([]) == ["param_sweep: 0 params, 0 fail"]


# ---- sweep_params on a fake design ----


class _FakeParam:
    def __init__(self, owner: "_FakeDesign", name: str) -> None:
        self._owner = owner
        self.name = name

    @property
    def expression(self) -> str:
        return self._owner.exprs[self.name]

    @expression.setter
    def expression(self, expr: str) -> None:
        self._owner.set_expr(self.name, expr)


class _FakeDesign:
    """model(exprs) -> (timeline items, bodies). Setting an expression re-runs the model; 'bad' raises like Fusion."""

    def __init__(self, exprs: dict[str, str], model: Callable[[dict[str, str]], tuple[list, list]]) -> None:
        self.exprs = dict(exprs)
        self._model = model
        self._params = {n: _FakeParam(self, n) for n in exprs}
        self.model_parameters: list[SimpleNamespace] = []
        self._items, self._bodies = model(self.exprs)
        self.userParameters = SimpleNamespace(itemByName=lambda n: self._params.get(n))

    def set_expr(self, name: str, expr: str) -> None:
        if expr == "bad":
            raise RuntimeError("Expression is invalid.")
        self.exprs[name] = expr
        self._items, self._bodies = self._model(self.exprs)

    @property
    def timeline(self) -> SimpleNamespace:
        items = self._items
        return SimpleNamespace(count=len(items), item=lambda i: items[i])

    @property
    def allComponents(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name="root", bRepBodies=self._bodies, modelParameters=self.model_parameters)]


def _item(name: str, healthy: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        name=name, isGroup=False, isSuppressed=False, healthState=HEALTHY if healthy else DAMAGED, entity=None
    )


def _body(
    vol: float,
    x_cm: float,
    verdict: tuple[tuple[str, ...], float, bool, str] = ((), 0.0, True, ""),
) -> Any:  # fake body: loose typing, as _sweep (not a BRepBody)
    return SimpleNamespace(
        name="roller",
        faces=SimpleNamespace(count=6),
        physicalProperties=SimpleNamespace(volume=vol, centerOfMass=SimpleNamespace(x=x_cm / 2, y=0.5, z=0.5)),
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            maxPoint=SimpleNamespace(x=x_cm, y=1.0, z=1.0),
        ),
        verdict=verdict,
        attributes=SimpleNamespace(
            itemByName=lambda group, name: None
        ),  # real body_verdict reads not_printed (none set)
    )


def _outer_model(exprs: dict[str, str]) -> tuple[list, list]:
    """wall = plate width mm, chamfer = outer chamfer mm (vol = wall + chamfer). Chamfer > wall/2 pokes past the
    chamfer: DfAM FAIL vertical_corner + print FAIL, overhang 4*chamfer mm2. Geometry stays healthy."""
    w = float(exprs["wall"].split()[0])
    c = float(exprs["chamfer"].split()[0])
    if c > w / 2:
        verdict: tuple[tuple[str, ...], float, bool, str] = (
            ("vertical_corner: plate pokes past chamfer",),
            4.0 * c,
            False,
            f"ovh {4.0 * c:.3g} mm2",
        )
    else:
        verdict = ((), 0.0, True, "")
    return [_item("plate")], [_body(w + c, w / 10, verdict)]


def _wall_model(exprs: dict[str, str]) -> tuple[list, list]:
    """wall = width in mm: boss fails at >= 10 mm, wall 0 removes the body, body width follows wall."""
    v = float(exprs["wall"].split()[0])
    items = [_item("plate"), _item("boss", healthy=v < 10)]
    bodies = [] if v == 0 else [_body(v, v / 10)]
    return items, bodies


def _const_model(exprs: dict[str, str]) -> tuple[list, list]:
    """Geometry ignores every parameter: BUILD-ONLY behaviour."""
    return [_item("plate")], [_body(1.0, 1.0)]


def test_sweep_clean_passes_and_restores() -> None:
    design = _FakeDesign({"wall": "5 mm"}, _wall_model)
    rows = _sweep(design, {"wall": ("5 mm", "mm", "w", {"min": "2 mm", "max": "9 mm"})})
    assert [r.name for r in rows] == ["wall"]
    assert [p.value for p in rows[0].probes] == ["5 mm", "2 mm", "9 mm"]
    assert rows[0].fails == ()
    assert design.exprs["wall"] == "5 mm"


def test_sweep_unhealthy_edge_flagged_and_restored() -> None:
    design = _FakeDesign({"wall": "5 mm"}, _wall_model)
    rows = _sweep(design, {"wall": ("5 mm", "mm", "w", {"values": ["2 mm", "10 mm"]})})
    assert rows[0].fails == ("DFAM_FAIL param_sweep: wall=10 mm unhealthy: boss",)
    assert design.exprs["wall"] == "5 mm"


def test_sweep_body_loss_flagged() -> None:
    design = _FakeDesign({"wall": "5 mm"}, _wall_model)
    rows = _sweep(design, {"wall": ("5 mm", "mm", "w", {"values": ["0 mm"]})})
    assert rows[0].fails == ("DFAM_FAIL param_sweep: wall=0 mm bodies=0 expected=1",)


def test_sweep_rejected_value_flagged_and_original_restored() -> None:
    design = _FakeDesign({"wall": "5 mm"}, _wall_model)
    rows = _sweep(design, {"wall": ("5 mm", "mm", "w", {"values": ["bad"]})})
    assert rows[0].fails == ("DFAM_FAIL param_sweep: wall=bad rejected by Fusion: Expression is invalid.",)
    assert design.exprs["wall"] == "5 mm"


def test_sweep_prints_time_per_probe_and_combo(capsys: pytest.CaptureFixture[str]) -> None:
    design = _FakeDesign({"wall": "5 mm"}, _wall_model)
    _sweep(design, {"wall": ("5 mm", "mm", "w", {"values": ["2 mm"]})}, combos=[{"wall": "3 mm"}])
    out = capsys.readouterr().out.splitlines()
    lines = [ln for ln in out if ln.startswith("TIME probe:")]
    assert [ln.rsplit(" ", 2)[0] for ln in lines] == ["TIME probe:wall=2 mm", "TIME probe:wall=3 mm"]
    assert any(ln.startswith("TIME baseline ") for ln in out)


def test_sweep_constant_geometry_is_build_only_fail() -> None:
    design = _FakeDesign({"gap": "1 mm"}, _const_model)
    rows = _sweep(design, {"gap": ("1 mm", "mm", "g", {"min": "0.5 mm", "max": "3 mm"})})
    assert rows[0].fails == (
        "DFAM_FAIL param_sweep: gap no effect on geometry (BUILD-ONLY param) over 1 mm, 0.5 mm, 3 mm",
    )


def test_sweep_skips_entries_without_range() -> None:
    design = _FakeDesign({"wall": "5 mm"}, _wall_model)
    spec = {"wall": ("5 mm", "mm", "w"), "x": ("1", "", "x", {"no_geometry": True})}
    assert _sweep(design, spec) == []


def test_sweep_unknown_param_raises() -> None:
    design = _FakeDesign({"wall": "5 mm"}, _wall_model)
    with pytest.raises(ValueError, match="not in design"):
        _sweep(design, {"nope": ("1 mm", "mm", "", {"values": ["2 mm"]})})


# ---- DfAM FAIL / print regression per probe (judge) ----


def test_judge_new_dfam_fail_flagged() -> None:
    probes = [_probe("5 mm"), _probe("9 mm", dfam=("teardrop: hole too small",), vol=9.0, box=9.0)]
    assert judge("wall", probes, no_geometry=False) == (
        "DFAM_FAIL param_sweep: wall=9 mm dfam: teardrop: hole too small",
    )


def test_judge_dfam_fail_already_on_baseline_not_repeated() -> None:
    base = ("teardrop: hole too small",)
    probes = [_probe("5 mm", dfam=base), _probe("9 mm", dfam=base)]
    assert judge("wall", probes, no_geometry=True) == ()


def test_judge_new_dfam_fails_first_shown_with_count() -> None:
    probes = [_probe("5 mm"), _probe("9 mm", dfam=("a: first", "b: second"))]
    assert judge("wall", probes, no_geometry=True) == ("DFAM_FAIL param_sweep: wall=9 mm dfam: a: first (+1 more)",)


def test_judge_print_regression_flagged_with_overhang() -> None:
    probes = [_probe("5 mm"), _probe("9 mm", print_ok=False, print_reason="ovh 12.5 mm2", vol=9.0, box=9.0)]
    assert judge("wall", probes, no_geometry=False) == (
        "DFAM_FAIL param_sweep: wall=9 mm print FAIL: ovh 12.5 mm2 (baseline ok)",
    )


_CLEAN = PrintReport(
    overhang_area_mm2=0.0,
    worst_overhang_deg=math.inf,
    overhangs=[],
    hanging_max_mm=0.0,
    hanging_total_mm=0.0,
    hanging_chains=[],
    floating_points=0,
    floating_coords=[],
    bed_contact_mm2=10.0,
    bed_contact_kind="area",
    height_mm=5.0,
    footprint_xy_mm=(10.0, 10.0),
    tip_ratio=0.0,
    boundary_edges=0,
    nonmanifold_edges=0,
    ok=True,
)


def test_print_fail_reason_lists_failing_fields_only() -> None:
    assert _print_fail_reason(_CLEAN) == ""
    pr = replace(_CLEAN, ok=False, hanging_max_mm=12.3, floating_points=2)
    assert _print_fail_reason(pr) == "hang 12.3 mm, floating 2"
    pr = replace(_CLEAN, ok=False, overhang_area_mm2=4.0, bed_contact_kind="point")
    assert _print_fail_reason(pr) == "ovh 4 mm2, bed point"


def test_print_fail_reason_hang_within_bridge_not_listed() -> None:
    pr = replace(_CLEAN, ok=False, overhang_area_mm2=4.0, hanging_max_mm=0.3)
    assert _print_fail_reason(pr, bridge_ok_mm=1.0) == "ovh 4 mm2"
    assert _print_fail_reason(pr) == "ovh 4 mm2, hang 0.3 mm"


def test_judge_print_fail_flag_carries_reason() -> None:
    probes = [
        _probe("5 mm"),
        _probe("9 mm", print_ok=False, print_reason="hang 12.3 mm, floating 2", vol=9.0, box=9.0),
    ]
    assert judge("wall", probes, no_geometry=False) == (
        "DFAM_FAIL param_sweep: wall=9 mm print FAIL: hang 12.3 mm, floating 2 (baseline ok)",
    )


def test_judge_print_fail_on_baseline_not_repeated() -> None:
    probes = [_probe("5 mm", print_ok=False, ovh=3.0), _probe("9 mm", print_ok=False, ovh=4.0)]
    assert judge("wall", probes, no_geometry=True) == ()


def test_judge_rejected_probe_skips_dfam_and_print() -> None:
    probes = [_probe("5 mm"), _probe("0 mm", bodies=0, error="Expression is invalid.", dfam=("x: y",), print_ok=False)]
    assert judge("wall", probes, no_geometry=False) == (
        "DFAM_FAIL param_sweep: wall=0 mm rejected by Fusion: Expression is invalid.",
    )


# ---- sweep_params: dfam / ovh per probe, combos, print_frames ----


def _hang_model(exprs: dict[str, str]) -> tuple[list, list]:
    """Prints clean up to wall 5 mm; above it check_body fails with a hanging edge and a floating point."""
    v = float(exprs["wall"].split()[0])
    verdict: tuple[tuple[str, ...], float, bool, str] = ((), 0.0, True, "")
    if v > 5:
        verdict = ((), 0.0, False, "hang 12.3 mm, floating 2")
    return [_item("plate")], [_body(v, v / 10, verdict)]


def test_sweep_print_fail_reason_reaches_flag() -> None:
    design = _FakeDesign({"wall": "5 mm"}, _hang_model)
    rows = _sweep(design, {"wall": ("5 mm", "mm", "w", {"values": ["9 mm"]})})
    assert rows[0].fails == ("DFAM_FAIL param_sweep: wall=9 mm print FAIL: hang 12.3 mm, floating 2 (baseline ok)",)
    assert design.exprs["wall"] == "5 mm"


def test_sweep_flags_dfam_and_ovh_per_probe_and_restores() -> None:
    design = _FakeDesign({"wall": "10 mm", "chamfer": "1 mm"}, _outer_model)
    rows = _sweep(design, {"chamfer": ("1 mm", "mm", "c", {"values": ["6 mm"]})})
    assert rows[0].probes[1].dfam_fails == ("vertical_corner: plate pokes past chamfer",)
    assert rows[0].fails == (
        "DFAM_FAIL param_sweep: chamfer=6 mm dfam: vertical_corner: plate pokes past chamfer",
        "DFAM_FAIL param_sweep: chamfer=6 mm print FAIL: ovh 24 mm2 (baseline ok)",
    )
    assert design.exprs == {"wall": "10 mm", "chamfer": "1 mm"}


def test_sweep_combo_sets_params_together_and_restores() -> None:
    design = _FakeDesign({"wall": "10 mm", "chamfer": "1 mm"}, _outer_model)
    rows = _sweep(design, {}, combos=[{"wall": "12 mm", "chamfer": "7 mm"}])
    assert [r.name for r in rows] == ["wall+chamfer"]
    assert rows[0].original == "10 mm/1 mm"
    assert [p.value for p in rows[0].probes] == ["10 mm/1 mm", "12 mm/7 mm"]
    assert rows[0].probes[1].vol_cm3 == pytest.approx(19.0)
    assert rows[0].fails == (
        "DFAM_FAIL param_sweep: wall+chamfer=12 mm/7 mm dfam: vertical_corner: plate pokes past chamfer",
        "DFAM_FAIL param_sweep: wall+chamfer=12 mm/7 mm print FAIL: ovh 28 mm2 (baseline ok)",
    )
    assert design.exprs == {"wall": "10 mm", "chamfer": "1 mm"}


def test_sweep_combo_rejected_value_restores_all() -> None:
    design = _FakeDesign({"wall": "10 mm", "chamfer": "1 mm"}, _outer_model)
    rows = _sweep(design, {}, combos=[{"wall": "12 mm", "chamfer": "bad"}])
    assert rows[0].fails == (
        "DFAM_FAIL param_sweep: wall+chamfer=12 mm/bad rejected by Fusion: Expression is invalid.",
    )
    assert design.exprs == {"wall": "10 mm", "chamfer": "1 mm"}


def test_sweep_combo_unknown_name_raises_before_setting() -> None:
    design = _FakeDesign({"wall": "10 mm"}, _wall_model)
    with pytest.raises(ValueError, match="not in design"):
        _sweep(design, {}, combos=[{"wall": "12 mm", "nope": "1 mm"}])
    assert design.exprs == {"wall": "10 mm"}


def test_sweep_empty_combo_raises() -> None:
    design = _FakeDesign({"wall": "5 mm"}, _wall_model)
    with pytest.raises(ValueError, match="empty combo"):
        _sweep(design, {}, combos=[{}])


def test_sweep_print_frames_reach_body_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Rot] = []

    def recording(body: SimpleNamespace, up_rot: Rot) -> tuple[tuple[str, ...], float, bool, str]:
        seen.append(up_rot)
        return body.verdict

    monkeypatch.setattr(ps_mod, "body_verdict", recording)
    design = _FakeDesign({"wall": "5 mm"}, _wall_model)
    spec = {"wall": ("5 mm", "mm", "w", {"values": ["2 mm"]})}
    _sweep(design, spec, print_frames={"root": rot_x(90.0)})
    assert seen and all(r == rot_x(90.0) for r in seen)
    seen.clear()
    _sweep(design, spec)
    assert seen and all(r == IDENTITY for r in seen)


_OK_REPORT = SimpleNamespace(
    ok=True, overhang_area_mm2=0.0, hanging_max_mm=0.0, floating_points=0, bed_contact_kind="area"
)
_REAL_BODY_VERDICT: Callable[..., tuple[tuple[str, ...], float, bool, str]] = (
    ps_mod.body_verdict
)  # captured before the autouse fixture swaps it for body.verdict; fakes are not BRepBody, loose typing as _sweep


def test_body_verdict_prints_dfam_and_check_time(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ps_mod, "dfam_check", lambda body: [])
    monkeypatch.setattr(ps_mod, "check_body", lambda body, up_rot, bridge_ok_mm=0.0: _OK_REPORT)
    assert _REAL_BODY_VERDICT(_body(1.0, 1.0), IDENTITY) == ((), 0.0, True, "")
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2
    assert out[0].startswith("TIME dfam:roller ")
    assert out[1].startswith("TIME check:roller ")


def test_verdict_cache_reuses_same_fingerprint_and_reruns_on_change(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[SimpleNamespace] = []

    def counting(body: SimpleNamespace, up_rot: Rot) -> tuple[tuple[str, ...], float, bool, str]:
        seen.append(body)
        return body.verdict

    monkeypatch.setattr(ps_mod, "body_verdict", counting)
    cache = ps_mod._VerdictCache()
    verdict: Callable[..., object] = cache.verdict  # fakes are not BRepBody: loose typing, as _sweep
    a, b, c = _body(1.0, 1.0), _body(1.0, 1.0), _body(2.0, 1.0)
    verdict(a, IDENTITY)
    verdict(b, IDENTITY)
    assert (cache.hits, len(seen)) == (1, 1)  # same fingerprint -> hit
    verdict(c, IDENTITY)
    assert (cache.hits, len(seen)) == (1, 2)  # volume changed -> re-run
    verdict(a, rot_x(90.0))
    assert (cache.hits, len(seen)) == (1, 3)  # other print frame -> re-run


def test_sweep_unchanged_body_skips_checks_and_reports_hits(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ps_mod, "body_verdict", _REAL_BODY_VERDICT)
    dfam_calls: list[SimpleNamespace] = []

    def counting_dfam(body: SimpleNamespace) -> list[object]:
        dfam_calls.append(body)
        return []

    monkeypatch.setattr(ps_mod, "dfam_check", counting_dfam)
    monkeypatch.setattr(ps_mod, "check_body", lambda body, up_rot, bridge_ok_mm=0.0: _OK_REPORT)

    def wall_and_fixed(exprs: dict[str, str]) -> tuple[list, list]:
        w = float(exprs["wall"].split()[0])
        return [_item("plate")], [_body(w, w / 10), _body(1.0, 0.5)]

    design = _FakeDesign({"wall": "5 mm"}, wall_and_fixed)
    rows = _sweep(design, {"wall": ("5 mm", "mm", "w", {"values": ["2 mm", "9 mm"]})})
    assert rows[0].fails == ()
    assert len(dfam_calls) == 4  # baseline: 2 bodies; each probe: moving body only, fixed body hits cache
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("TIME probe:")]
    assert [ln.rsplit(" ", 1)[1] for ln in lines] == ["cache_hits=1", "cache_hits=1"]
    assert design.exprs["wall"] == "5 mm"


def test_sweep_baseline_measured_once_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[int] = []
    real_read = ps_mod._read_bodies

    def counting_read(design: Any) -> tuple[ps_mod.BodyFact, ...]:
        reads.append(1)
        return real_read(design)

    monkeypatch.setattr(ps_mod, "_read_bodies", counting_read)
    design = _FakeDesign({"wall": "10 mm", "chamfer": "1 mm"}, _outer_model)
    spec = {
        "wall": ("10 mm", "mm", "w", {"values": ["12 mm"]}),
        "chamfer": ("1 mm", "mm", "c", {"values": ["2 mm"]}),
    }
    rows = _sweep(design, spec, combos=[{"wall": "12 mm", "chamfer": "2 mm"}])
    assert [r.name for r in rows] == ["wall", "chamfer", "wall+chamfer"]
    assert len(reads) == 4  # one baseline + one per probe (wall, chamfer, combo); old code: 6
    assert rows[1].probes[0].value == "1 mm"
    assert rows[2].probes[0].value == "10 mm/1 mm"
    assert design.exprs == {"wall": "10 mm", "chamfer": "1 mm"}


# ---- warp grooves: suppressed on probes of params no groove expression refers to ----


def _groove_item(name: str, token: str, exprs: list[str]) -> SimpleNamespace:
    """Timeline item of a warp-groove feature: entity tagged DFAM_GROUP / WARP_GROOVE_ATTR, sketch dimensions = exprs."""
    tagged = {(DFAM_GROUP, WARP_GROOVE_ATTR)}
    attrs = SimpleNamespace(itemByName=lambda group, attr: object() if (group, attr) in tagged else None)
    dims = [SimpleNamespace(parameter=SimpleNamespace(expression=e)) for e in exprs]
    entity = SimpleNamespace(entityToken=token, attributes=attrs, sketchDimensions=dims)
    return SimpleNamespace(name=name, isGroup=False, isSuppressed=False, healthState=HEALTHY, entity=entity)


def _grooved(
    model: Callable[[dict[str, str]], tuple[list, list]], groove: SimpleNamespace
) -> Callable[[dict[str, str]], tuple[list, list]]:
    """Model plus one persistent groove item (same object across rebuilds, so its suppression state survives)."""

    def run(exprs: dict[str, str]) -> tuple[list, list]:
        items, bodies = model(exprs)
        return [*items, groove], bodies

    return run


def _record_verdicts(monkeypatch: pytest.MonkeyPatch, groove: SimpleNamespace, seen: list[bool]) -> None:
    """Record the groove suppression state seen at every body verdict (one entry per measured body)."""
    monkeypatch.setattr(
        ps_mod, "body_verdict", lambda body, up_rot: (seen.append(groove.isSuppressed), body.verdict)[1]
    )


def test_sweep_non_groove_probe_suppresses_grooves_and_restores(monkeypatch: pytest.MonkeyPatch) -> None:
    groove = _groove_item("warp_grooves_z_seed", "tok_g", ["warp_groove_width / 2"])
    seen: list[bool] = []
    _record_verdicts(monkeypatch, groove, seen)
    design = _FakeDesign({"wall": "5 mm", "warp_groove_width": "0.8 mm"}, _grooved(_wall_model, groove))
    rows = _sweep(design, {"wall": ("5 mm", "mm", "w", {"values": ["2 mm"]})})
    assert rows[0].fails == ()
    assert seen == [False, True, True]  # full baseline, grooves-off baseline, grooves-off probe
    assert groove.isSuppressed is False
    assert design.exprs["wall"] == "5 mm"


def _width_model(exprs: dict[str, str]) -> tuple[list, list]:
    """Body volume follows warp_groove_width, so a width probe changes the geometry (fresh verdict, no cache hit)."""
    w = float(exprs["warp_groove_width"].split()[0])
    return [_item("plate")], [_body(1.0 + w, 1.0)]


def test_sweep_groove_param_probe_keeps_grooves(monkeypatch: pytest.MonkeyPatch) -> None:
    groove = _groove_item("warp_grooves_z_seed", "tok_g", ["warp_groove_width / 2"])
    seen: list[bool] = []
    _record_verdicts(monkeypatch, groove, seen)
    design = _FakeDesign({"wall": "5 mm", "warp_groove_width": "0.8 mm"}, _grooved(_width_model, groove))
    rows = _sweep(design, {"warp_groove_width": ("0.8 mm", "mm", "gw", {"values": ["1 mm"], "no_geometry": True})})
    assert rows[0].fails == ()
    assert seen == [False, False]  # groove-referencing param: full baseline and probe, no suppression
    assert groove.isSuppressed is False
    assert design.exprs["warp_groove_width"] == "0.8 mm"


def test_sweep_exception_restores_grooves_and_param(monkeypatch: pytest.MonkeyPatch) -> None:
    groove = _groove_item("warp_grooves_z_seed", "tok_g", ["warp_groove_width / 2"])
    design = _FakeDesign({"wall": "5 mm", "warp_groove_width": "0.8 mm"}, _grooved(_wall_model, groove))

    def failing(body: SimpleNamespace, up_rot: Rot) -> tuple[tuple[str, ...], float, bool, str]:
        if groove.isSuppressed and design.exprs["wall"] == "2 mm":
            raise RuntimeError("live measure failed")
        return body.verdict

    monkeypatch.setattr(ps_mod, "body_verdict", failing)
    with pytest.raises(RuntimeError, match="live measure failed"):
        _sweep(design, {"wall": ("5 mm", "mm", "w", {"values": ["2 mm"]})})
    assert groove.isSuppressed is False
    assert design.exprs["wall"] == "5 mm"


def test_sweep_combo_without_groove_params_suppresses(monkeypatch: pytest.MonkeyPatch) -> None:
    groove = _groove_item("warp_grooves_z_seed", "tok_g", ["warp_groove_width / 2"])
    seen: list[bool] = []
    _record_verdicts(monkeypatch, groove, seen)
    design = _FakeDesign({"wall": "5 mm", "warp_groove_width": "0.8 mm"}, _grooved(_wall_model, groove))
    rows = _sweep(design, {}, combos=[{"wall": "3 mm"}])
    assert seen == [False, True, True]  # full baseline, grooves-off baseline, grooves-off combo probe
    assert groove.isSuppressed is False
    assert rows[0].probes[0].value == "5 mm"
    assert design.exprs == {"wall": "5 mm", "warp_groove_width": "0.8 mm"}


def test_sweep_build_only_still_flagged_with_grooves() -> None:
    groove = _groove_item("warp_grooves_z_seed", "tok_g", ["warp_groove_width / 2"])
    design = _FakeDesign({"gap": "1 mm", "warp_groove_width": "0.8 mm"}, _grooved(_const_model, groove))
    rows = _sweep(design, {"gap": ("1 mm", "mm", "g", {"min": "0.5 mm", "max": "3 mm"})})
    assert rows[0].fails == (
        "DFAM_FAIL param_sweep: gap no effect on geometry (BUILD-ONLY param) over 1 mm, 0.5 mm, 3 mm",
    )


def test_grooves_suppressed_restores_prior_state_on_exception() -> None:
    g1 = _groove_item("g1", "t1", [])
    g2 = _groove_item("g2", "t2", [])
    g2.isSuppressed = True  # already suppressed by the user: must stay suppressed after the block
    design = _FakeDesign({"wall": "5 mm"}, lambda exprs: ([g1, g2], [_body(1.0, 1.0)]))
    with pytest.raises(RuntimeError, match="boom"), ps_mod._grooves_suppressed(design, (0, 1)):
        assert (g1.isSuppressed, g2.isSuppressed) == (True, True)
        raise RuntimeError("boom")
    assert (g1.isSuppressed, g2.isSuppressed) == (False, True)


def test_refers_and_groove_exprs_collect_dims_and_model_params() -> None:
    assert ps_mod._refers("wall", ["(wall) / 2"]) is True
    assert ps_mod._refers("wall", ["wall_2 + 1"]) is False
    groove = _groove_item("g_seed", "tok_g", ["warp_groove_width / 2"])
    design = _FakeDesign({"wall": "5 mm"}, _grooved(_wall_model, groove))
    design.model_parameters = [
        SimpleNamespace(createdBy=SimpleNamespace(entityToken="tok_g"), expression="warp_groove_depth"),
        SimpleNamespace(createdBy=SimpleNamespace(entityToken="tok_other"), expression="wall"),
    ]
    assert ps_mod._groove_indices(design) == (2,)
    assert ps_mod._groove_exprs(design, ps_mod._groove_indices(design)) == (
        "warp_groove_width / 2",
        "warp_groove_depth",
    )


def test_verdict_cache_separates_suppression_state(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    monkeypatch.setattr(ps_mod, "body_verdict", lambda body, up_rot: (seen.append(1), body.verdict)[1])
    cache = ps_mod._VerdictCache()
    verdict: Callable[..., object] = cache.verdict  # fakes are not BRepBody: loose typing, as _sweep
    a = _body(1.0, 1.0)
    verdict(a, IDENTITY)
    verdict(a, IDENTITY, True)  # same geometry, grooves suppressed: separate verdict
    assert (cache.hits, len(seen)) == (0, 2)
    verdict(a, IDENTITY, True)
    assert (cache.hits, len(seen)) == (1, 2)
