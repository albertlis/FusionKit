"""build_check: pure formatter on fake inputs + build stdout handling with fake adsk scene. No live Fusion calls."""

import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit import build_check as bc_mod
from fusion_kit import feature_quality, sketch_quality
from fusion_kit.build_check import BodyCheck, Summary, _check_frame_keys, format_check
from fusion_kit.param_sweep import SweepRow
from fusion_kit.printability import IDENTITY
from fusion_kit.report import ReportData
from fusion_kit.user_params import ParamEntry

_FUS: Any = sys.modules["adsk.fusion"]
_REAL_BODY_CHECK = bc_mod._body_check  # captured before fusion_scene monkeypatches it


@pytest.fixture(autouse=True)
def _no_design_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """SKQ hooks walk design.allComponents / the live timeline; these fake designs have neither -> stub both."""
    monkeypatch.setattr(sketch_quality, "check", lambda design: [])
    monkeypatch.setattr(feature_quality, "check", lambda design: [])


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
    body = SimpleNamespace(
        name="b0",
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0, z=0.0), maxPoint=SimpleNamespace(x=9.0, y=3.4, z=3.4)
        ),
        physicalProperties=SimpleNamespace(volume=20.3217),
    )
    comp = SimpleNamespace(name="roller", entityToken="tok-roller", bRepBodies=_Coll([body]))
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
        lambda name, body, up_rot, budget: BodyCheck(
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
    fusion_scene: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def noisy_run_part(entry_path: str, context: str = "", project_root: str | None = None) -> None:
        print("SECRET_RUN_STDOUT roller report line")

    monkeypatch.setattr(bc_mod, "run_part", noisy_run_part)
    entry = tmp_path / "roller.py"
    entry.write_text("def run(_context):\n    pass\n", encoding="utf-8")
    text = bc_mod.build_and_check(str(entry), interference=False)
    out = capsys.readouterr().out
    assert "SECRET_RUN_STDOUT" not in text
    assert "SECRET_RUN_STDOUT" not in out
    non_time = [ln for ln in out.splitlines() if not ln.startswith("TIME ")]
    assert "\n".join(non_time) == text
    assert text.splitlines() == [
        "roller vol=20.32 bbox=90x34x34 dfam=0F/0W ovh=0 hang=0 print=ok",
        "interf=off unhealthy=0 timeline_unnamed=0 unconstr=1 png=none sweep=off skq=0F/0W",
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


def test_format_check_sweep_token_and_lines() -> None:
    summary = Summary(
        interf=0,
        interf_lines=[],
        unhealthy=0,
        unnamed=0,
        unconstr=0,
        png=None,
        sweep="2p/1F",
        sweep_lines=["param_sweep: 2 params, 1 fail", "DFAM_FAIL param_sweep: wall=0 mm bodies=0 expected=1"],
        lint_lines=["BUILD_ONLY_PARAM gap C:/p/x.py:4"],
    )
    lines = format_check([_body()], summary).splitlines()
    assert lines[1] == "interf=0 unhealthy=0 timeline_unnamed=0 unconstr=0 png=none sweep=2p/1F"
    assert lines[2:4] == ["param_sweep: 2 params, 1 fail", "DFAM_FAIL param_sweep: wall=0 mm bodies=0 expected=1"]
    assert lines[4] == "BUILD_ONLY_PARAM gap C:/p/x.py:4"


def test_build_and_check_runs_sweep_and_lints_entry(
    fusion_scene: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = tmp_path / "roller" / "roller.py"
    entry.parent.mkdir()
    entry.write_text('def run(_context):\n    width = mm("wall")\n', encoding="utf-8")
    monkeypatch.setattr(bc_mod, "run_part", lambda entry_path, context="", project_root=None: None)
    calls: list[tuple[object, object]] = []
    row = SweepRow("wall", "5 mm", (), ("DFAM_FAIL param_sweep: wall=0 mm bodies=0 expected=1",))

    def fake_sweep(
        design: object, spec: object, bodies_expected: int | None = None, **kwargs: object
    ) -> list[SweepRow]:
        calls.append((design, spec))
        return [row]

    monkeypatch.setattr(bc_mod, "sweep_params", fake_sweep)
    spec: dict[str, ParamEntry] = {"wall": ("5 mm", "mm", "w", {"values": ["0 mm"]})}
    text = bc_mod.build_and_check(str(entry), interference=False, params=spec, sweep=True)
    lines = text.splitlines()
    assert lines[1] == "interf=off unhealthy=0 timeline_unnamed=0 unconstr=1 png=none sweep=1p/1F skq=0F/0W"
    assert lines[2:4] == ["param_sweep: 1 params, 1 fail", "DFAM_FAIL param_sweep: wall=0 mm bodies=0 expected=1"]
    assert lines[4].startswith("BUILD_ONLY_PARAM wall ")
    assert lines[4].endswith(":2")
    assert len(calls) == 1
    assert calls[0][1] is spec


def test_build_and_check_sweep_off_and_none_visible(
    fusion_scene: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = tmp_path / "roller.py"
    entry.write_text("def run(_context):\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(bc_mod, "run_part", lambda *args, **kwargs: None)

    def must_not_run(*args: object, **kwargs: object) -> list[SweepRow]:
        raise AssertionError("sweep_params must not run")

    monkeypatch.setattr(bc_mod, "sweep_params", must_not_run)
    off = bc_mod.build_and_check(str(entry), interference=False, params={"w": ("1 mm", "mm", "")}, sweep=False)
    assert off.splitlines()[1].endswith("png=none sweep=off skq=0F/0W")
    none = bc_mod.build_and_check(str(entry), interference=False, sweep=True)
    assert none.splitlines()[1].endswith("png=none sweep=none skq=0F/0W")


def test_build_and_check_passes_combos_and_frames(
    fusion_scene: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = tmp_path / "roller.py"
    entry.write_text("def run(_context):\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(bc_mod, "run_part", lambda *args, **kwargs: None)
    seen: list[dict[str, object]] = []

    def fake_sweep(
        design: object, spec: object, bodies_expected: int | None = None, **kwargs: object
    ) -> list[SweepRow]:
        seen.append({"spec": spec, "bodies_expected": bodies_expected, **kwargs})
        return [SweepRow("wall+gap", "1 mm/2 mm", (), ())]

    monkeypatch.setattr(bc_mod, "sweep_params", fake_sweep)
    combos = [{"wall": "0 mm", "gap": "2 mm"}]
    text = bc_mod.build_and_check(
        str(entry), interference=False, sweep=True, sweep_combos=combos, print_frames={"roller": IDENTITY}
    )
    assert text.splitlines()[1].endswith("png=none sweep=1p/0F skq=0F/0W")
    assert seen == [{"spec": {}, "bodies_expected": None, "combos": combos, "print_frames": {"roller": IDENTITY}}]


def test_root_component_skq_fail_reaches_output(
    fusion_scene: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Root-component sketches are not reached via root.occurrences; their SKQ lines come from sketch_quality.check(design)."""
    entry = tmp_path / "frame.py"
    entry.write_text("def run(_context):\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(bc_mod, "run_part", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sketch_quality, "check", lambda design: ["SKQ_FAIL underconstrained: frame not fully constrained"]
    )
    text = bc_mod.build_and_check(str(entry), interference=False)
    lines = text.splitlines()
    assert lines[1].endswith("sweep=off skq=1F/0W")
    assert lines[2] == "SKQ_FAIL underconstrained: frame not fully constrained"


def test_time_lines_per_stage(
    fusion_scene: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """TIME <stage> lines printed live: build, dfam/check per body (real _body_check, faked checks), interf, sweep, skq, total."""
    monkeypatch.setattr(bc_mod, "_body_check", _REAL_BODY_CHECK)
    monkeypatch.setattr(bc_mod, "dfam_check", lambda body: [])
    monkeypatch.setattr(
        bc_mod,
        "check_body",
        lambda body, up_rot: SimpleNamespace(
            ok=True,
            worst_overhang_deg=0.0,
            hanging_max_mm=0.0,
            floating_points=0,
            bed_contact_kind="bed",
            overhang_area_mm2=0.0,
            hanging_total_mm=0.0,
        ),
    )
    monkeypatch.setattr(bc_mod, "run_part", lambda *args, **kwargs: None)
    monkeypatch.setattr(bc_mod, "sweep_params", lambda design, spec, **kwargs: [SweepRow("wall", "5 mm", (), ())])
    entry = tmp_path / "roller.py"
    entry.write_text("def run(_context):\n    pass\n", encoding="utf-8")
    spec: dict[str, ParamEntry] = {"wall": ("5 mm", "mm", "w", {"values": ["0 mm"]})}
    bc_mod.build_and_check(str(entry), params=spec, sweep=True)
    time_lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("TIME ")]
    assert [ln.split(" ")[1] for ln in time_lines] == [
        "build",
        "dfam:roller",
        "check:roller",
        "interf",
        "sweep",
        "skq",
        "total",
    ]
    assert all(float(ln.rsplit(" ", 1)[1]) >= 0.0 for ln in time_lines)


def test_time_build_printed_when_build_raises(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    entry = tmp_path / "roller.py"
    entry.write_text("def run(_context):\n    pass\n", encoding="utf-8")

    def failing_run_part(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom in build")

    monkeypatch.setattr(bc_mod, "run_part", failing_run_part)
    with pytest.raises(RuntimeError, match="boom in build"):
        bc_mod.build_and_check(str(entry), interference=False)
    out = capsys.readouterr().out.splitlines()
    assert [ln.split(" ")[1] for ln in out if ln.startswith("TIME ")] == ["build", "total"]


def test_timed_prints_tag_and_two_decimals_also_on_raise(capsys: pytest.CaptureFixture[str]) -> None:
    with bc_mod._timed("x"):
        pass
    with pytest.raises(RuntimeError, match="boom"), bc_mod._timed("y"):
        raise RuntimeError("boom")
    lines = capsys.readouterr().out.splitlines()
    assert [ln.split(" ")[1] for ln in lines] == ["x", "y"]
    assert all(len(ln.split(" ")) == 3 and len(ln.split(" ")[2].split(".")[1]) == 2 for ln in lines)


def test_sweep_default_off_even_with_params(
    fusion_scene: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = tmp_path / "roller.py"
    entry.write_text("def run(_context):\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(bc_mod, "run_part", lambda *args, **kwargs: None)

    def must_not_run(*args: object, **kwargs: object) -> list[SweepRow]:
        raise AssertionError("sweep_params must not run without sweep=True")

    monkeypatch.setattr(bc_mod, "sweep_params", must_not_run)
    text = bc_mod.build_and_check(str(entry), interference=False, params={"w": ("1 mm", "mm", "")})
    assert text.splitlines()[1].endswith("png=none sweep=off skq=0F/0W")


def test_checks_dfam_only_runs_no_check_body(
    fusion_scene: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(bc_mod, "_body_check", _REAL_BODY_CHECK)
    dfam_bodies: list[object] = []

    def fake_dfam(body: object) -> list[object]:
        dfam_bodies.append(body)
        return []

    def must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("check_body must not run when print not in checks")

    monkeypatch.setattr(bc_mod, "dfam_check", fake_dfam)
    monkeypatch.setattr(bc_mod, "check_body", must_not_run)
    monkeypatch.setattr(bc_mod, "run_part", lambda *args, **kwargs: None)
    entry = tmp_path / "roller.py"
    entry.write_text("def run(_context):\n    pass\n", encoding="utf-8")
    text = bc_mod.build_and_check(str(entry), checks=frozenset({"dfam"}))
    lines = text.splitlines()
    assert len(dfam_bodies) == 1
    assert lines[0].startswith("roller vol=20.32 bbox=90x34x34 dfam=")
    assert lines[0].endswith("ovh=off hang=off print=off")
    assert lines[1] == "interf=off unhealthy=0 timeline_unnamed=0 unconstr=1 png=none sweep=off skq=off"


def test_budget_exceeded_skips_remaining_stages(
    fusion_scene: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    ticks = iter(float(i) for i in range(1000))  # fake clock: +1 s per call -> budget exceeded at first finished stage
    monkeypatch.setattr(bc_mod, "time", SimpleNamespace(perf_counter=lambda: next(ticks)))
    monkeypatch.setattr(bc_mod, "_body_check", _REAL_BODY_CHECK)

    def must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("stage must not run after budget exceeded")

    monkeypatch.setattr(bc_mod, "dfam_check", must_not_run)
    monkeypatch.setattr(bc_mod, "check_body", must_not_run)
    monkeypatch.setattr(bc_mod, "sweep_params", must_not_run)
    monkeypatch.setattr(bc_mod, "run_part", lambda *args, **kwargs: None)
    entry = tmp_path / "roller.py"
    entry.write_text("def run(_context):\n    pass\n", encoding="utf-8")
    text = bc_mod.build_and_check(str(entry), sweep=True, params={"w": ("1 mm", "mm", "")}, budget_s=0.5)
    lines = text.splitlines()
    assert lines[0] == "roller vol=20.32 bbox=90x34x34 dfam=skipped ovh=skipped hang=skipped print=skipped"
    assert lines[1] == (
        "interf=skipped unhealthy=0 timeline_unnamed=0 unconstr=1 png=none sweep=skipped skq=skipped budget=exceeded"
    )
    skip_lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("SKIP ")]
    assert skip_lines == [
        "SKIP dfam budget",
        "SKIP print budget",
        "SKIP interf budget",
        "SKIP sweep budget",
        "SKIP skq budget",
    ]
    assert "print=ok" not in text
    assert "dfam=0F" not in text
    assert "skq=0F" not in text


def test_unknown_check_name_raises(fusion_scene: None, tmp_path: Path) -> None:
    entry = tmp_path / "roller.py"
    entry.write_text("def run(_context):\n    pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="nope"):
        bc_mod.build_and_check(str(entry), checks=frozenset({"nope"}))
