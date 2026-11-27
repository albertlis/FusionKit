"""not_printed body flag in param_sweep: a flagged body gives an ok verdict (no DfAM / print call), so the sweep judge
raises no DFAM_FAIL / PRINT_FAIL for it. Unflagged bodies unchanged. Offline, fake bodies."""

from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit import build_check as bc_mod
from fusion_kit import param_sweep as ps_mod
from fusion_kit.param_sweep import Probe, _measure, _VerdictCache, judge
from fusion_kit.printability import IDENTITY

_REAL_BODY_VERDICT = ps_mod.body_verdict  # captured before monkeypatch swaps it per test
_FAIL = SimpleNamespace(level="FAIL", rule="teardrop", msg="hole top peaked")
_OK_REPORT = SimpleNamespace(
    ok=True, overhang_area_mm2=0.0, hanging_max_mm=0.0, floating_points=0, bed_contact_kind="area"
)


class _Attrs:
    """Stand-in for body.attributes: add upserts, itemByName returns object with .value or None."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def add(self, group: str, name: str, value: str) -> None:
        self.store[(group, name)] = value

    def itemByName(self, group: str, name: str) -> SimpleNamespace | None:
        return SimpleNamespace(value=self.store[(group, name)]) if (group, name) in self.store else None


def _body(reason: str | None = None, vol: float = 0.1) -> Any:
    """Fake BRepBody, bbox 10 x 5 x 2 mm; flagged not_printed with reason when given."""
    body = SimpleNamespace(
        name="bolt",
        faces=SimpleNamespace(count=6),
        physicalProperties=SimpleNamespace(volume=vol, centerOfMass=SimpleNamespace(x=0.5, y=0.25, z=0.1)),
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0, z=0.0), maxPoint=SimpleNamespace(x=1.0, y=0.5, z=0.2)
        ),
        attributes=_Attrs(),
    )
    if reason is not None:
        bc_mod.mark_not_printed(body, reason)
    return body


def _design(*bodies: Any) -> Any:
    return SimpleNamespace(allComponents=[SimpleNamespace(name="roller", bRepBodies=list(bodies))])


def _probe(value: str, design: Any, vol: float) -> Probe:
    """Probe built from the real _measure output of design (fresh cache, identity frames)."""
    fails, ovh, ok, reason = _measure(design, {}, _VerdictCache())
    return Probe(
        value=value,
        error=None,
        unhealthy=(),
        bodies=1,
        vol_cm3=vol,
        box_mm=(0.0, 0.0, 0.0, 10.0, 5.0, 2.0),
        dfam_fails=fails,
        overhang_mm2=ovh,
        print_ok=ok,
        print_reason=reason,
    )


def _must_not_run(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("DfAM / print check must not run on a not_printed body")


def test_flagged_body_verdict_is_ok_without_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ps_mod, "dfam_check", _must_not_run)
    monkeypatch.setattr(ps_mod, "check_body", _must_not_run)
    assert _REAL_BODY_VERDICT(_body("M3 bolt installed"), IDENTITY) == ((), 0.0, True, "")


def test_flagged_body_no_fail_in_sweep_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ps_mod, "body_verdict", _REAL_BODY_VERDICT)
    monkeypatch.setattr(ps_mod, "dfam_check", _must_not_run)
    monkeypatch.setattr(ps_mod, "check_body", _must_not_run)
    base = _probe("0.1", _design(_body("M3 bolt installed", vol=0.1)), vol=0.1)
    probe = _probe("0.2", _design(_body("M3 bolt installed", vol=0.2)), vol=0.2)
    assert probe.dfam_fails == () and probe.print_ok is True and probe.print_reason == ""
    lines = judge("bolt", [base, probe], False, None)
    assert not [line for line in lines if "FAIL" in line], lines


def test_flag_reader_round_trips_with_build_check() -> None:
    body = _body()
    assert ps_mod._not_printed(body) is False
    bc_mod.mark_not_printed(body, "M3 bolt installed")
    assert ps_mod._not_printed(body) is True
    assert bc_mod.not_printed_from_body(body) == "M3 bolt installed"


def test_unflagged_body_still_reports_dfam_fail_flagged_sibling_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ps_mod, "body_verdict", _REAL_BODY_VERDICT)
    monkeypatch.setattr(ps_mod, "dfam_check", lambda body: [_FAIL])
    monkeypatch.setattr(ps_mod, "check_body", lambda body, up_rot, bridge_ok_mm=0.0: _OK_REPORT)
    fails, _ovh, ok, _reason = _measure(_design(_body("M3 bolt installed"), _body()), {}, _VerdictCache())
    assert fails == ("teardrop: hole top peaked",)
    assert ok is True
