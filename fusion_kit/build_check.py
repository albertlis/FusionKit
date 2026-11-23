"""One execute = build + all verification, compact keyword output.

Chain: loader.run_part(build) with stdout captured -> report_data (dfam=False) + dfam_check + printability.check_body per body
(print orientation) -> interference over root occurrences -> param_sweep (live edit of every swept param and combo, DfAM +
print check per probe, restored)
-> view.save_screenshot. Static: param_lint over entry + parts/ (BUILD_ONLY_PARAM lines).
Output: one line per body, one summary line, DETAIL_MAX detail lines per body / for interference. TIME <stage> <s:.2f> lines are printed live (not part of the returned text).
Build stdout (entry-script report() calls) is never printed on success; on error its last BUILD_TAIL_LINES lines are printed.
Stage selection: checks (dfam / print / interf / skq). Time budget: budget_s, checked after each finished stage; once exceeded,
remaining stages print 'SKIP <stage> budget' and show 'skipped'. A stage that did not run never shows ok (off / skipped).

Pure parts (format_check) take fake inputs and are offline-tested. build_and_check is the Fusion side.
No try/except swallowing: any error propagates (CLAUDE.md: exception = debug info).
"""

import contextlib
import io
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

import adsk.core
import adsk.fusion

from fusion_kit import feature_quality, sketch_quality
from fusion_kit.dfam import dfam_check, summarize_findings
from fusion_kit.loader import run_part
from fusion_kit.param_lint import format_lint, lint_entry
from fusion_kit.param_sweep import SweepRow, format_sweep, sweep_params, sweep_token
from fusion_kit.printability import IDENTITY, Rot, check_body
from fusion_kit.report import interference as find_interference
from fusion_kit.report import report_data
from fusion_kit.user_params import ParamEntry
from fusion_kit.view import save_screenshot

DETAIL_MAX = 3  # detail lines per body (dfam FAIL + print FAIL), and for interference; overflow -> "... +N more"
BUILD_TAIL_LINES = 20  # captured build stdout lines printed when the build raises
ALL_CHECKS = frozenset({"dfam", "print", "interf", "skq"})


@dataclass(frozen=True)
class BodyCheck:
    name: str
    vol_cm3: float
    bbox_mm: tuple[float, float, float]
    dfam: str  # summarize_findings text, e.g. "dfam=0F/1W"; or "dfam=off" / "dfam=skipped" when the stage did not run
    dfam_fails: list[str]  # Finding.line() of FAIL findings
    overhang_mm2: float | None  # None = print stage did not run
    hanging_mm: float | None  # None = print stage did not run
    print_ok: bool | None  # None = print stage did not run
    print_fails: list[str]  # PRINT_FAIL detail lines (empty when print_ok)
    print_status: str = "off"  # shown as print= / ovh= / hang= when print_ok is None: "off" | "skipped"


@dataclass(frozen=True)
class Summary:
    interf: int
    interf_lines: list[str]
    unhealthy: int
    unnamed: int
    unconstr: int
    png: str | None
    sweep: str | None = (
        None  # "<N>p/<F>F" swept/failing params, "none" (no params given), "off" (sweep=False), "skipped" (budget); None = omit
    )
    sweep_lines: list[str] = field(default_factory=list)  # format_sweep lines: header + DFAM_FAIL param_sweep lines
    lint_lines: list[str] = field(default_factory=list)  # BUILD_ONLY_PARAM lines (not blocking)
    skq: str | None = None  # "<F>F/<W>W" SKQ_FAIL / SKQ_WARN count; None = omit
    skq_lines: list[str] = field(default_factory=list)  # SKQ_FAIL / SKQ_WARN lines (never capped)
    stage_states: dict[str, str] = field(default_factory=dict)  # "interf" / "skq" -> "off" | "skipped" when not run
    budget_exceeded: bool = False  # True -> summary ends with ' budget=exceeded'


class _Budget:
    """Stage gate: checks selection + time budget. Clock = time.perf_counter, started at build_and_check entry."""

    def __init__(self, checks: frozenset[str], budget_s: float | None) -> None:
        self.checks = checks
        self.budget_s = budget_s
        self.t0 = time.perf_counter()
        self.exceeded = False

    def gate(self, stage: str) -> str | None:
        """None = run the stage. 'off' = stage not in checks (no line). 'skipped' = budget exceeded (SKIP line printed)."""
        if stage not in self.checks:
            return "off"
        return self.skip_if_over(stage)

    def skip_if_over(self, stage: str) -> str | None:
        """Budget-only gate (no checks membership). 'skipped' + 'SKIP <stage> budget' line when exceeded, else None."""
        if self.exceeded:
            print(f"SKIP {stage} budget")
            return "skipped"
        return None

    def done(self) -> None:
        """Call after a finished stage: exceeded = True when elapsed since start > budget_s."""
        if self.budget_s is not None and time.perf_counter() - self.t0 > self.budget_s:
            self.exceeded = True


def _cap(lines: list[str], limit: int) -> list[str]:
    """First limit-1 lines + '... +N more' when more than limit lines."""
    if len(lines) <= limit:
        return lines
    return lines[: limit - 1] + [f"... +{len(lines) - limit + 1} more"]


@contextlib.contextmanager
def _timed(tag: str) -> Iterator[None]:
    """Print 'TIME <tag> <s:.2f>' when the block exits, also when it raises."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        print(f"TIME {tag} {time.perf_counter() - t0:.2f}")


def format_check(bodies: list[BodyCheck], summary: Summary) -> str:
    """Compact keyword text: per body 'name vol= bbox= dfam= ovh= hang= print=' + detail lines, then summary line.

    Summary line ends with ' sweep=<token>' when summary.sweep is set, ' skq=<token>' when set (or stage_states['skq']),
    ' budget=exceeded' when summary.budget_exceeded. sweep_lines (never capped: header + fails)
    and capped lint_lines follow the interference lines.
    """
    out: list[str] = []
    for b in bodies:
        x, y, z = (f"{v:.4g}" for v in b.bbox_mm)
        ovh = f"{b.overhang_mm2:.3g}" if b.overhang_mm2 is not None else b.print_status
        hang = f"{b.hanging_mm:.3g}" if b.hanging_mm is not None else b.print_status
        ptok = b.print_status if b.print_ok is None else ("ok" if b.print_ok else "FAIL")
        out.append(f"{b.name} vol={b.vol_cm3:.4g} bbox={x}x{y}x{z} {b.dfam} ovh={ovh} hang={hang} print={ptok}")
        out.extend(_cap(b.dfam_fails + b.print_fails, DETAIL_MAX))
    states = summary.stage_states
    summary_line = (
        f"interf={states.get('interf', summary.interf)} unhealthy={summary.unhealthy} "
        f"timeline_unnamed={summary.unnamed} unconstr={summary.unconstr} png={summary.png or 'none'}"
    )
    if summary.sweep is not None:
        summary_line += f" sweep={summary.sweep}"
    if "skq" in states:
        summary_line += f" skq={states['skq']}"
    elif summary.skq is not None:
        summary_line += f" skq={summary.skq}"
    if summary.budget_exceeded:
        summary_line += " budget=exceeded"
    out.append(summary_line)
    out.extend(_cap(summary.interf_lines, DETAIL_MAX))
    out.extend(summary.sweep_lines)
    out.extend(summary.skq_lines)
    out.extend(_cap(summary.lint_lines, DETAIL_MAX))
    return "\n".join(out)


def _check_frame_keys(print_frames: dict[str, Rot], names: list[str]) -> None:
    unknown = sorted(set(print_frames) - set(names))
    if unknown:
        raise ValueError(f"build_check: print_frames keys not components {unknown}; components: {names}")


def _body_check(name: str, body: adsk.fusion.BRepBody, up_rot: Rot, budget: _Budget) -> BodyCheck:
    box = body.boundingBox
    bbox = (
        (box.maxPoint.x - box.minPoint.x) * 10,
        (box.maxPoint.y - box.minPoint.y) * 10,
        (box.maxPoint.z - box.minPoint.z) * 10,
    )
    dfam_state = budget.gate("dfam")
    if dfam_state is None:
        with _timed(f"dfam:{name}"):
            findings = dfam_check(body)
        budget.done()
        dfam_tok = summarize_findings(findings)
        dfam_fails = [f.line() for f in findings if f.level == "FAIL"]
    else:
        dfam_tok = f"dfam={dfam_state}"
        dfam_fails = []
    print_state = budget.gate("print")
    if print_state is None:
        with _timed(f"check:{name}"):
            pr = check_body(body, up_rot)
        budget.done()
        print_ok: bool | None = pr.ok
        print_fails = (
            []
            if pr.ok
            else [
                (
                    f"PRINT_FAIL {name}: worst_deg={pr.worst_overhang_deg:.3g} hanging_max_mm={pr.hanging_max_mm:.3g} "
                    f"floating={pr.floating_points} bed={pr.bed_contact_kind}"
                )
            ]
        )
        overhang: float | None = pr.overhang_area_mm2
        hanging: float | None = pr.hanging_total_mm
    else:
        print_ok = None
        print_fails = []
        overhang = None
        hanging = None
    return BodyCheck(
        name=name,
        vol_cm3=body.physicalProperties.volume,
        bbox_mm=bbox,
        dfam=dfam_tok,
        dfam_fails=dfam_fails,
        overhang_mm2=overhang,
        hanging_mm=hanging,
        print_ok=print_ok,
        print_fails=print_fails,
        print_status=print_state or "off",
    )


def _run_build_quiet(entry_path: str) -> None:
    """run_part(entry, 'build') with its stdout captured instead of printed.

    On exception: last BUILD_TAIL_LINES captured lines printed, then the exception re-raised unchanged.
    """
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            run_part(entry_path, "build")
    except Exception:
        tail = buf.getvalue().splitlines()[-BUILD_TAIL_LINES:]
        print("build_check: build stdout tail:\n" + "\n".join(tail))
        raise


def build_and_check(
    entry_path: str,
    *,
    print_frames: dict[str, Rot] | None = None,
    interference: bool = True,
    screenshot: str | None = None,
    params: Mapping[str, ParamEntry] | None = None,
    sweep: bool = False,
    sweep_combos: list[dict[str, str]] | None = None,
    checks: frozenset[str] = frozenset({"dfam", "print", "interf", "skq"}),
    budget_s: float | None = 30.0,
) -> str:
    """run_part(entry, 'build') + verify in one execute. Prints and returns the compact text.

    entry_path: part entry script (def run(_context)). Context passed = 'build'. Its stdout is not printed (see _run_build_quiet).
    print_frames: {component name: Rot} print orientation (printability.rot_x/rot_y/rot_z/compose); missing = IDENTITY
        (bed face on min Z). Unknown component name -> ValueError.
    interference: pairs over ROOT occurrences, nominal pose (report.interference). False -> interf=off.
    screenshot: .png path, 600x400, current camera (view.save_screenshot). None -> png=none.
    params: the part's PARAMS spec (name -> (expr, units, comment[, SweepRange])). Used only when sweep=True.
    sweep: default False -> sweep=off. True + no params and no sweep_combos -> sweep=none (visible, not silent).
        True + params and/or sweep_combos: param_sweep.sweep_params runs after the build checks; FAIL
        lines are DFAM_FAIL param_sweep (block the Stop hook like DfAM FAIL). Original expressions restored.
    sweep_combos: [{param name: expr}, ...] extra probes, several params set together (param_sweep.sweep_params combos).
        Needs sweep=True. Each probe gets the same DfAM / overhang / print check as the build (print_frames).
    checks: subset of {"dfam", "print", "interf", "skq"}; unknown name -> ValueError. "print" = printability.check_body.
        Stage not selected -> dfam=off / print=off (ovh=off hang=off) / interf=off / skq=off. Never ok.
    budget_s: seconds from entry; checked after each finished stage (build included). Exceeded -> remaining stages skipped:
        'SKIP <stage> budget' lines, token 'skipped', summary ends ' budget=exceeded'. None = no budget.
    Always: param_lint over entry + parts/ -> BUILD_ONLY_PARAM lines (static, not blocking). Entry file must exist.
    Components = unique by entityToken over root occurrences; multi-body comp -> lines 'name[i]'.
    """
    unknown_checks = checks - ALL_CHECKS
    if unknown_checks:
        raise ValueError(f"build_check: unknown checks {sorted(unknown_checks)}; allowed {sorted(ALL_CHECKS)}")
    with _timed("total"):
        budget = _Budget(checks, budget_s)
        frames = print_frames or {}
        lint_lines = format_lint(lint_entry(entry_path))
        with _timed("build"):
            _run_build_quiet(entry_path)
        budget.done()
        design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
        if design is None:
            raise RuntimeError("build_check: no active Fusion design after build")
        root = design.rootComponent
        occs = [root.occurrences.item(i) for i in range(root.occurrences.count)]
        comps: dict[str, adsk.fusion.Component] = {}
        for occ in occs:
            comps.setdefault(occ.component.entityToken, occ.component)
        comp_list = list(comps.values())
        _check_frame_keys(frames, [c.name for c in comp_list])

        bodies: list[BodyCheck] = []
        unhealthy = unnamed = constrained = total = 0
        for comp in comp_list:
            data = report_data(comp, dfam=False)
            constrained += data.sketches_constrained
            total += data.sketches_total
            unhealthy = max(unhealthy, data.unhealthy)  # design-wide value, same on every call
            unnamed = max(unnamed, data.unnamed)
            up_rot = frames.get(comp.name, IDENTITY)
            multi = comp.bRepBodies.count > 1
            for i, body in enumerate(comp.bRepBodies):
                name = f"{comp.name}[{i}]" if multi else comp.name
                bodies.append(_body_check(name, body, up_rot, budget))

        stage_states: dict[str, str] = {}
        interf_state = "off" if not interference else budget.gate("interf")
        if interf_state is None:
            with _timed("interf"):
                pairs = find_interference(design, occs) if len(occs) > 1 else []
            budget.done()
        else:
            pairs = []
            stage_states["interf"] = interf_state

        sweep_tok: str | None
        sweep_lines: list[str] = []
        if not sweep:
            sweep_tok = "off"
        elif params is None and not sweep_combos:
            sweep_tok = "none"
        elif budget.skip_if_over("sweep") is not None:
            sweep_tok = "skipped"
        else:
            with _timed("sweep"):
                rows: list[SweepRow] = sweep_params(
                    design,
                    params if params is not None else {},
                    combos=sweep_combos,
                    print_frames=frames,
                )
            budget.done()
            sweep_tok = sweep_token(rows)
            sweep_lines = format_sweep(rows)

        skq_lines: list[str] = []
        skq_tok: str | None = None
        skq_state = budget.gate("skq")
        if skq_state is None:
            with _timed("skq"):
                skq_lines += sketch_quality.check(
                    design
                )  # all components incl. root (root.occurrences misses root sketches)
                skq_lines += feature_quality.check(design)
            budget.done()
            skq_tok = sketch_quality.skq_token(skq_lines)
        else:
            stage_states["skq"] = skq_state

        summary = Summary(
            interf=len(pairs),
            interf_lines=[f"INTERF {a} x {b} vol_mm3={v:.4g}" for a, b, v in pairs],
            unhealthy=unhealthy,
            unnamed=unnamed,
            unconstr=total - constrained,
            png=save_screenshot(screenshot) if screenshot else None,
            sweep=sweep_tok,
            sweep_lines=sweep_lines,
            lint_lines=lint_lines,
            skq=skq_tok,
            skq_lines=skq_lines,
            stage_states=stage_states,
            budget_exceeded=budget.exceeded,
        )
        text = format_check(bodies, summary)
        print(text)
    return text
