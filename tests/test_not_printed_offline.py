"""not_printed body flag: flagged bodies skip DfAM + print check; unflagged bodies unchanged. Offline, fake body."""

from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit import build_check as bc_mod
from fusion_kit.build_check import BodyCheck, Summary, _Budget, format_check


class _Attrs:
    """Stand-in for body.attributes: add upserts, itemByName returns object with .value or None."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def add(self, group: str, name: str, value: str) -> None:
        self.store[(group, name)] = value

    def itemByName(self, group: str, name: str) -> SimpleNamespace | None:
        return SimpleNamespace(value=self.store[(group, name)]) if (group, name) in self.store else None


def _fake_body(reason: str | None = None) -> SimpleNamespace:
    """1 x 0.5 x 0.2 cm body (bbox 10 x 5 x 2 mm, vol 0.1 cm3); flagged with reason when given."""
    body = SimpleNamespace(
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0, z=0.0), maxPoint=SimpleNamespace(x=1.0, y=0.5, z=0.2)
        ),
        physicalProperties=SimpleNamespace(volume=0.1),
        attributes=_Attrs(),
    )
    if reason is not None:
        bc_mod.mark_not_printed(body, reason)
    return body


def _summary() -> Summary:
    return Summary(interf=0, interf_lines=[], unhealthy=0, unnamed=0, unconstr=0, png=None)


def _must_not_run(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("DfAM / print check must not run on a not_printed body")


def test_flagged_body_skips_dfam_and_print(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(bc_mod, "dfam_check", _must_not_run)
    monkeypatch.setattr(bc_mod, "check_body", _must_not_run)
    budget = _Budget(frozenset({"dfam", "print"}), None, None)
    check = bc_mod._body_check("bolt", _fake_body("M3 bolt installed"), bc_mod.IDENTITY, budget)
    assert check.print_ok is None
    assert check.print_fails == [] and check.dfam_fails == []
    assert check.print_status == "not_printed"
    out = format_check([check], _summary())
    assert (
        out.splitlines()[0]
        == "bolt vol=0.1 bbox=10x5x2 dfam=not_printed ovh=not_printed hang=not_printed print=not_printed"
    )
    assert "PRINT_FAIL" not in out and "DFAM_FAIL" not in out
    assert capsys.readouterr().out == "SKIP not_printed bolt: M3 bolt installed\n"


def test_unflagged_body_checked_as_before(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[str] = []
    monkeypatch.setattr(bc_mod, "dfam_check", lambda body: calls.append("dfam") or [])
    monkeypatch.setattr(
        bc_mod,
        "check_body",
        lambda body, up_rot: (
            calls.append("print")
            or SimpleNamespace(
                ok=True,
                worst_overhang_deg=0.0,
                hanging_max_mm=0.0,
                floating_points=0,
                bed_contact_kind="bed",
                overhang_area_mm2=0.0,
                hanging_total_mm=0.0,
            )
        ),
    )
    budget = _Budget(frozenset({"dfam", "print"}), None, None)
    check = bc_mod._body_check("roller", _fake_body(), bc_mod.IDENTITY, budget)
    assert calls == ["dfam", "print"]
    assert check.print_ok is True
    assert check.print_status == "off"
    assert "SKIP not_printed" not in capsys.readouterr().out


def test_mark_and_read_round_trip() -> None:
    body = _fake_body()
    assert bc_mod.not_printed_from_body(body) is None
    assert bc_mod.mark_not_printed(body, "M3 bolt, installed") == "M3 bolt, installed"
    assert bc_mod.not_printed_from_body(body) == "M3 bolt, installed"


@pytest.mark.parametrize("reason", ["", "   ", " padded", "two\nlines"])
def test_mark_rejects_bad_reason_and_writes_nothing(reason: str) -> None:
    body = _fake_body()
    with pytest.raises(ValueError, match="mark_not_printed"):
        bc_mod.mark_not_printed(body, reason)
    assert bc_mod.not_printed_from_body(body) is None


def test_dfam_fail_on_flagged_body_not_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flagged body with a would-be DFAM FAIL: the fail line never reaches the output."""
    monkeypatch.setattr(bc_mod, "dfam_check", _must_not_run)
    budget = _Budget(frozenset({"dfam", "print"}), None, None)
    check: BodyCheck = bc_mod._body_check("shim", _fake_body("shim, installed"), bc_mod.IDENTITY, budget)
    assert check.dfam_fails == []
    assert "DFAM_FAIL" not in format_check([check], _summary())
