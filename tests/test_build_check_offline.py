"""build_check: pure formatter on fake inputs + build stdout handling with fake adsk scene. No live Fusion calls."""

import sys
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit import build_check as bc_mod
from fusion_kit.build_check import BodyCheck, Summary, _check_frame_keys, format_check
from fusion_kit.printability import IDENTITY
from fusion_kit.report import ReportData

_FUS: Any = sys.modules["adsk.fusion"]


def _body(
    dfam_fails: list[str] | None = None, print_ok: bool = True, print_fails: list[str] | None = None
) -> BodyCheck:
    return BodyCheck(
        name="roller",
        vol_cm3=20.3217,
        bbox_mm=(90.0, 34.0, 34.0),
        dfam="dfam=0F/1W",
        dfam_fails=dfam_fails or [],
        overhang_mm2=0.0,
        hanging_mm=0.0,
        print_ok=print_ok,
        print_fails=print_fails or [],
    )


def _summary(interf: int = 0, interf_lines: list[str] | None = None, png: str | None = None) -> Summary:
    return Summary(
        interf=interf,
        interf_lines=interf_lines or [],
        unhealthy=0,
        unnamed=0,
        unconstr=0,
        png=png,
    )


def test_keyword_lines_exact() -> None:
    out = format_check([_body()], _summary())
    assert out == (
        "roller vol=20.32 bbox=90x34x34 dfam=0F/1W ovh=0 hang=0 print=ok\n"
        "interf=0 unhealthy=0 timeline_unnamed=0 unconstr=0 png=none"
    )


def test_png_path_in_summary() -> None:
    out = format_check([_body()], _summary(png="C:/shots/roller.png"))
    assert out.splitlines()[-1].endswith("png=C:/shots/roller.png")


def test_dfam_fail_details_capped_to_three_lines() -> None:
    fails = [f"DFAM_FAIL r{i}: m" for i in range(5)]
    lines = format_check([_body(dfam_fails=fails)], _summary()).splitlines()
    assert lines[1:4] == ["DFAM_FAIL r0: m", "DFAM_FAIL r1: m", "... +3 more"]
    assert lines[4].startswith("interf=")


def test_print_fail_sets_flag_and_detail_line() -> None:
    detail = "PRINT_FAIL roller: worst_deg=30 hanging_max_mm=2.5 floating=1 bed=edge"
    lines = format_check([_body(print_ok=False, print_fails=[detail])], _summary()).splitlines()
    assert lines[0].endswith("print=FAIL")
    assert lines[1] == detail


def test_interference_lines_listed_and_capped() -> None:
    pairs = [f"INTERF a x b{i} vol_mm3=1.5" for i in range(4)]
    lines = format_check([], _summary(interf=4, interf_lines=pairs)).splitlines()
    assert lines[0].startswith("interf=4 ")
    assert lines[1:] == ["INTERF a x b0 vol_mm3=1.5", "INTERF a x b1 vol_mm3=1.5", "... +2 more"]


def test_multi_body_lines_in_order() -> None:
    out = format_check([_body(), BodyCheck(**{**_body().__dict__, "name": "roller[1]"})], _summary())
    names = [ln.split(" ")[0] for ln in out.splitlines()[:2]]
    assert names == ["roller", "roller[1]"]


def test_frame_keys_unknown_raises() -> None:
    _check_frame_keys({"roller": IDENTITY}, ["roller", "beam"])
    with pytest.raises(ValueError, match="rolr"):
        _check_frame_keys({"rolr": IDENTITY}, ["roller", "beam"])


class _Coll:
    """Stand-in for comp.bRepBodies: iterable with .count."""

    def __init__(self, items: list[Any]) -> None:
        self._items = items
        self.count = len(items)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._items)


@pytest.fixture
def fusion_scene(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """One root occurrence 'roller' with one body. Design.cast -> that design; report_data / _body_check faked."""
    comp = SimpleNamespace(name="roller", entityToken="tok-roller", bRepBodies=_Coll([SimpleNamespace(name="b0")]))
    occ = SimpleNamespace(name="roller:1", component=comp)
    root = SimpleNamespace(occurrences=SimpleNamespace(count=1, item=lambda i: occ))
    design = SimpleNamespace(rootComponent=root)
    _FUS.Design.cast.side_effect = lambda obj: design
    monkeypatch.setattr(
        bc_mod,
        "report_data",
        lambda c, dfam: ReportData(
            name=c.name,
            bodies=1,
            lumps=1,
            bbox_mm=(90.0, 34.0, 34.0),
            vol_cm3=20.3217,
            sketches_constrained=2,
            sketches_total=3,
            unhealthy=0,
            unnamed=0,
            numbered=0,
            findings=None,
        ),
    )
    monkeypatch.setattr(
        bc_mod,
        "_body_check",
        lambda name, body, up_rot: BodyCheck(
            name=name,
            vol_cm3=20.3217,
            bbox_mm=(90.0, 34.0, 34.0),
            dfam="dfam=0F/0W",
            dfam_fails=[],
            overhang_mm2=0.0,
            hanging_mm=0.0,
            print_ok=True,
            print_fails=[],
        ),
    )
    yield
    _FUS.Design.cast.side_effect = None


def test_build_stdout_not_returned_or_printed(
    fusion_scene: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def noisy_run_part(entry_path: str, context: str = "", project_root: str | None = None) -> None:
        print("SECRET_RUN_STDOUT roller report line")

    monkeypatch.setattr(bc_mod, "run_part", noisy_run_part)
    text = bc_mod.build_and_check("C:/parts/roller/roller.py", interference=False)
    out = capsys.readouterr().out
    assert "SECRET_RUN_STDOUT" not in text
    assert "SECRET_RUN_STDOUT" not in out
    assert out == text + "\n"
    assert text.splitlines() == [
        "roller vol=20.32 bbox=90x34x34 dfam=0F/0W ovh=0 hang=0 print=ok",
        "interf=0 unhealthy=0 timeline_unnamed=0 unconstr=1 png=none",
    ]


def test_build_error_prints_stdout_tail_and_reraises(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing_run_part(entry_path: str, context: str = "", project_root: str | None = None) -> None:
        for k in range(25):
            print(f"step {k}")
        raise RuntimeError("boom in build")

    monkeypatch.setattr(bc_mod, "run_part", failing_run_part)
    with pytest.raises(RuntimeError, match="boom in build"):
        bc_mod._run_build_quiet("C:/parts/roller/roller.py")
    out = capsys.readouterr().out
    assert "step 24" in out
    assert "step 5" in out
    assert "step 4" not in out
