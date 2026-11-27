"""One execute = build + all verification, compact keyword output.

Chain: loader.run_part(build) with stdout captured -> report_data (dfam=False) + dfam_check + printability.check_body per body
(print orientation) -> interference over root occurrences -> param_sweep (live edit of every swept param and combo, DfAM +
print check per probe, restored)
-> view.save_screenshot. Static: param_lint over entry + parts/ (BUILD_ONLY_PARAM lines).
Output: one line per body, one summary line, DETAIL_MAX detail lines per body / for interference. TIME <stage> <s:.2f> lines are printed live (not part of the returned text).
Build log: every START / TIME / SKIP line is also appended + flushed to the log file as it happens; START <stage> is written before
each stage, so a hang shows the stage. Log path: log_path > env FUSIONKIT_LOG > tempdir/fusion_kit_build_check.log; each call
appends a 'RUN <iso time>' header and the file keeps the last LOG_KEEP_RUNS runs (per-run TIME deltas stay comparable).
param_sweep probe TIME lines stay on stdout only.
Build stdout (entry-script report() calls) is never printed on success; on error its last BUILD_TAIL_LINES lines are printed.
Stage selection: checks (dfam / print / interf / skq). Time budget: budget_s counts CHECK time only (clock starts after the
build), checked after each finished stage, before each comp and before interference. Once exceeded, remaining stages print
'SKIP <stage> budget' (a comp not started: 'SKIP comp <name> budget') and show 'skipped'.
A stage that did not run never shows ok (off / skipped). TIME lines: build, report, dfam:<comp>, print:<comp>, body_check:<comp>
(= dfam + print + overhead), interf, sweep, skq, total.

Pure parts (format_check) take fake inputs and are offline-tested. build_and_check is the Fusion side.
No try/except swallowing: any error propagates (CLAUDE.md: exception = debug info).
"""

import contextlib
import io
import os
import tempfile
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime

import adsk.core
import adsk.fusion

from fusion_kit import feature_quality, graph, sketch_quality
from fusion_kit.component import active_design
from fusion_kit.dfam import dfam_check, summarize_findings
from fusion_kit.loader import run_part
from fusion_kit.param_lint import format_lint, lint_entry
from fusion_kit.param_sweep import SweepRow, format_sweep, sweep_params, sweep_token
from fusion_kit.printability import IDENTITY, Rot, check_body
from fusion_kit.report import design_stats, report_data
from fusion_kit.report import interference as find_interference
from fusion_kit.user_params import ParamEntry
from fusion_kit.view import save_screenshot

DETAIL_MAX = 3  # detail lines per body (dfam FAIL + print FAIL), and for interference; overflow -> "... +N more"
BUILD_TAIL_LINES = 20  # captured build stdout lines printed when the build raises
ALL_CHECKS = frozenset({"dfam", "print", "interf", "skq"})
LOG_ENV = "FUSIONKIT_LOG"
LOG_FILE_NAME = "fusion_kit_build_check.log"
LOG_KEEP_RUNS = 5  # build log keeps the last N build_and_check runs (oldest trimmed at the start of a run)
RUN_PREFIX = "RUN "  # header line that opens every run in the build log
PRINT_GROUP = "print"  # body attribute group (not DfAM): print-check opt-out
NOT_PRINTED_ATTR = "not_printed"  # value = reason, one line (see mark_not_printed)


@dataclass(frozen=True)
class BodyCheck:
    name: str
    vol_cm3: float
    bbox_mm: tuple[float, float, float]
    dfam: str  # summarize_findings text, e.g. "dfam=0F/1W"; or "dfam=off" / "dfam=skipped" / "dfam=not_printed"
    dfam_fails: list[str]  # Finding.line() of FAIL findings
    overhang_mm2: float | None  # None = print stage did not run
    hanging_mm: float | None  # None = print stage did not run
    print_ok: bool | None  # None = print stage did not run
    print_fails: list[str]  # PRINT_FAIL detail lines (empty when print_ok)
    print_status: str = "off"  # shown as print= / ovh= / hang= when print_ok is None: "off" | "skipped" | "not_printed"


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


def _log_path(log_path: str | None) -> str | None:
    """Explicit log_path > env FUSIONKIT_LOG > tempdir/fusion_kit_build_check.log. Explicit '' = no log file (None)."""
    if log_path is not None:
        return log_path or None
    return os.environ.get(LOG_ENV) or os.path.join(tempfile.gettempdir(), LOG_FILE_NAME)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _open_run(path: str) -> None:
    """Append a RUN header for this build_and_check call. Keeps the last LOG_KEEP_RUNS runs: when more exist, everything
    before the oldest kept header is dropped (preamble without header included). Rewrites the file, so no partial run."""
    kept: list[str] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            kept = f.read().splitlines()
    headers = [i for i, ln in enumerate(kept) if ln.startswith(RUN_PREFIX)]
    keep_old = LOG_KEEP_RUNS - 1  # the new run is one of the LOG_KEEP_RUNS
    if len(headers) > keep_old:
        kept = kept[headers[len(headers) - keep_old] :]
    kept.append(f"{RUN_PREFIX}{_now_iso()}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(ln + "\n" for ln in kept))


class _Log:
    """Build log file. Each line is appended and flushed when written, so it survives a run that is killed mid-check."""

    def __init__(self, path: str | None) -> None:
        self.path = path
        if path is not None:
            _open_run(path)  # RUN header + trim to the last runs; lines of this run are appended by write()

    def write(self, line: str) -> None:
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()


def _say(line: str, log: _Log | None) -> None:
    """Print a stage line and append it to the build log."""
    print(line)
    if log is not None:
        log.write(line)


class _Budget:
    """Stage gate: checks selection + time budget. Clock = time.perf_counter, created AFTER the build (build not charged)."""

    def __init__(self, checks: frozenset[str], budget_s: float | None, log: _Log | None = None) -> None:
        self.checks = checks
        self.budget_s = budget_s
        self.log = log
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
            _say(f"SKIP {stage} budget", self.log)
            return "skipped"
        return None

    def comp_gate(self, name: str) -> bool:
        """Per-comp gate, called before a comp's first body. True = comp not started (budget exceeded), 'SKIP comp <name> budget' printed."""
        if self.exceeded:
            _say(f"SKIP comp {name} budget", self.log)
            return True
        return False

    def over_limit(self) -> bool:
        """Live check (no state change): True when elapsed since start > budget_s. Stages that loop (skq) ask this per item."""
        return self.budget_s is not None and time.perf_counter() - self.t0 > self.budget_s

    def done(self) -> None:
        """Call after a finished stage: exceeded = True when elapsed since start > budget_s."""
        if self.over_limit():
            self.exceeded = True


def _cap(lines: list[str], limit: int) -> list[str]:
    """First limit-1 lines + '... +N more' when more than limit lines."""
    if len(lines) <= limit:
        return lines
    return lines[: limit - 1] + [f"... +{len(lines) - limit + 1} more"]


@contextlib.contextmanager
def _timed(tag: str, log: _Log | None = None, note: str = "") -> Iterator[None]:
    """Print 'TIME <tag> <s:.2f>' when the block exits, also when it raises.
    With log: 'START <tag> [note]' is appended to the log before the block runs."""
    if log is not None:
        log.write(f"START {tag} {note}".rstrip())
    t0 = time.perf_counter()
    try:
        yield
    finally:
        _say(f"TIME {tag} {time.perf_counter() - t0:.2f}", log)


def format_check(bodies: list[BodyCheck], summary: Summary) -> str:
    """Compact keyword text: per body 'name vol= bbox= dfam= ovh= hang= print=' + detail lines, then summary line.

    Summary line ends with ' sweep=<token>' when summary.sweep is set, ' skq=<token>' when set (or stage_states['skq']),
    ' budget=exceeded' when summary.budget_exceeded. sweep_lines (never capped: header + fails)
    and capped lint_lines follow the interference lines.

    -> compact text from BodyCheck / Summary. Pure
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


def mark_not_printed(body: adsk.fusion.BRepBody, reason: str) -> str:
    """Flag a body that is not printed (bolt, installed shim, bearing, assembly copy). build_check skips its DfAM and print
    checks and prints 'SKIP not_printed <body>: <reason>'. Opt-in: unflagged bodies are checked as before.
    Reason: non-empty, no surrounding spaces, no line breaks. Validation runs before the write. Returns reason.
    """
    if not reason or reason != reason.strip() or "\n" in reason or "\r" in reason:
        raise ValueError(f"mark_not_printed: reason must be non-empty, trimmed, one line; got {reason!r}")
    body.attributes.add(PRINT_GROUP, NOT_PRINTED_ATTR, reason)
    return reason


def not_printed_from_body(body: adsk.fusion.BRepBody) -> str | None:
    """not_printed attribute ("print", "not_printed") -> reason. Absent -> None."""
    attr = body.attributes.itemByName(PRINT_GROUP, NOT_PRINTED_ATTR)
    return None if attr is None else attr.value


def _geometry(body: adsk.fusion.BRepBody) -> tuple[tuple[float, float, float], float]:
    """(bbox mm, volume cm3) of one body."""
    box = body.boundingBox
    bbox = (
        (box.maxPoint.x - box.minPoint.x) * 10,
        (box.maxPoint.y - box.minPoint.y) * 10,
        (box.maxPoint.z - box.minPoint.z) * 10,
    )
    return bbox, body.physicalProperties.volume


def _skipped_body_check(name: str, body: adsk.fusion.BRepBody, budget: _Budget) -> BodyCheck:
    """Comp not started (budget exceeded before it): geometry only; dfam / print tokens 'skipped' (or 'off' when not selected)."""
    bbox, vol = _geometry(body)
    return BodyCheck(
        name=name,
        vol_cm3=vol,
        bbox_mm=bbox,
        dfam="dfam=off" if "dfam" not in budget.checks else "dfam=skipped",
        dfam_fails=[],
        overhang_mm2=None,
        hanging_mm=None,
        print_ok=None,
        print_fails=[],
        print_status="off" if "print" not in budget.checks else "skipped",
    )


def _body_check(name: str, body: adsk.fusion.BRepBody, up_rot: Rot, budget: _Budget) -> BodyCheck:
    bbox, vol = _geometry(body)
    reason = not_printed_from_body(body)
    if reason is not None:  # flagged: no DfAM, no print check, so no DFAM / PRINT_FAIL lines for this body
        _say(f"SKIP not_printed {name}: {reason}", budget.log)
        return BodyCheck(
            name=name,
            vol_cm3=vol,
            bbox_mm=bbox,
            dfam="dfam=not_printed",
            dfam_fails=[],
            overhang_mm2=None,
            hanging_mm=None,
            print_ok=None,
            print_fails=[],
            print_status="not_printed",
        )
    dfam_state = budget.gate("dfam")
    if dfam_state is None:
        with _timed(f"dfam:{name}", budget.log):
            findings = dfam_check(body)
        budget.done()
        dfam_tok = summarize_findings(findings)
        dfam_fails = [f.line() for f in findings if f.level == "FAIL"]
    else:
        dfam_tok = f"dfam={dfam_state}"
        dfam_fails = []
    print_state = budget.gate("print")
    if print_state is None:
        with _timed(f"print:{name}", budget.log):
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
        vol_cm3=vol,
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
    log_path: str | None = None,
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
    budget_s: seconds of CHECK time (clock starts after the build; build duration never counts). Checked after each finished
        stage, before each comp and before interference. Exceeded -> remaining stages skipped: 'SKIP <stage> budget' lines
        (comp not started: 'SKIP comp <name> budget'), token 'skipped', summary ends ' budget=exceeded'. None = no budget
        (override for a slow check set). Default 30.0.
    log_path: build log file (every START / TIME / SKIP line, appended + flushed as it happens; START is written before each
        stage so a hang shows the stage). None -> env FUSIONKIT_LOG, else tempdir/fusion_kit_build_check.log. '' -> no file.
        Each call appends a RUN header; the file keeps the last LOG_KEEP_RUNS runs. Interference START line carries pairs=<n occurrence pairs>.
    Design-wide stats (timeline health, labels) computed ONCE per build (report.design_stats), passed to report_data per comp.
    Always: param_lint over entry + parts/ -> BUILD_ONLY_PARAM lines (static, not blocking). Entry file must exist.
    Components = unique by entityToken over root occurrences; multi-body comp -> lines 'name[i]'.

    run_part(entry, "build") with stdout captured (printed only on error: last 20 lines, exception
    re-raised) + report_data (dfam=False) + dfam + check_body per body (print_frames {comp name:
    Rot}, default IDENTITY) + interference over root occurrences + param_sweep.sweep_params (params
    = the PARAMS spec; sweep_combos = [{param: expr}] extra probes, run without params too;
    sweep=none when params and sweep_combos omitted, sweep=off when sweep=False; print_frames passed
    to the per-probe DfAM/print check; FAIL lines DFAM_FAIL param_sweep, block the Stop hook) +
    param_lint.lint_entry over entry + parts/ (BUILD_ONLY_PARAM lines, not blocking) +
    save_screenshot; SKQ lines = sketch_quality.check(design) (all components incl. root) +
    feature_quality.check(design), summary skq=<F>F/<W>W; prints and returns compact keyword lines
    (one per body, summary with sweep=<N>p/<F>F, max 3 detail lines per body).
    Build log on (log_path not ''): also fk_graph_<root>.txt/.json next to it + GRAPH summary lines (graph.write_graph,
    outside the CHECK budget).
    """
    unknown_checks = checks - ALL_CHECKS
    if unknown_checks:
        raise ValueError(f"build_check: unknown checks {sorted(unknown_checks)}; allowed {sorted(ALL_CHECKS)}")
    log = _Log(_log_path(log_path))
    with _timed("total", log):
        frames = print_frames or {}
        lint_lines = format_lint(lint_entry(entry_path))
        with _timed("build", log):
            _run_build_quiet(entry_path)
        budget = _Budget(
            checks, budget_s, log
        )  # clock starts after the build: build time never counts against budget_s
        design = active_design()
        root = design.rootComponent
        occs = [root.occurrences.item(i) for i in range(root.occurrences.count)]
        comps: dict[str, adsk.fusion.Component] = {}
        for occ in occs:
            comps.setdefault(occ.component.entityToken, occ.component)
        comp_list = list(comps.values())
        _check_frame_keys(frames, [c.name for c in comp_list])

        with _timed("report", log):
            stats = design_stats(design)  # design-wide: walked once per build, not once per comp
            reports = [(comp, report_data(comp, dfam=False, stats=stats, skq=False)) for comp in comp_list]
        constrained = sum(data.sketches_constrained for _, data in reports)
        total = sum(data.sketches_total for _, data in reports)

        bodies: list[BodyCheck] = []
        for comp, _data in reports:
            up_rot = frames.get(comp.name, IDENTITY)
            multi = comp.bRepBodies.count > 1
            comp_skipped = budget.comp_gate(comp.name)  # per comp: budget exceeded -> comp not started, no checks run
            for i, body in enumerate(comp.bRepBodies):
                name = f"{comp.name}[{i}]" if multi else comp.name
                if comp_skipped:
                    bodies.append(_skipped_body_check(name, body, budget))
                    continue
                with _timed(f"body_check:{name}", log):
                    bodies.append(_body_check(name, body, up_rot, budget))

        stage_states: dict[str, str] = {}
        interf_state = "off" if not interference else budget.gate("interf")  # budget checked BEFORE the O(n^2) pass
        if interf_state is None:
            n_occ = len(occs)
            with _timed("interf", log, note=f"pairs={n_occ * (n_occ - 1) // 2}"):
                pairs = find_interference(design, occs) if n_occ > 1 else []
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
            with _timed("sweep", log):
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
            with _timed("skq", log):
                skq_lines += sketch_quality.check(
                    design, over_budget=budget.over_limit
                )  # all components incl. root (root.occurrences misses root sketches); stops per sketch when over budget
                skq_lines += feature_quality.check(design, over_budget=budget.over_limit)  # stops per entry over budget
            budget.done()
            skq_tok = sketch_quality.skq_token(skq_lines)
        else:
            stage_states["skq"] = skq_state

        summary = Summary(
            interf=len(pairs),
            interf_lines=[f"INTERF {a} x {b} vol_mm3={v:.4g}" for a, b, v in pairs],
            unhealthy=stats.unhealthy,
            unnamed=stats.unnamed,
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
        if log.path is not None:  # build graph next to the build log; log_path='' disables it
            try:
                # Deliberate degradation: the graph is advisory and must never discard a finished check. A failure
                # prints one visible 'GRAPH failed' line; text is still returned.
                with _timed("graph", log):
                    nodes = graph.collect(design)
                    graph_dir = os.path.dirname(os.path.abspath(log.path))
                    for line in graph.write_graph(nodes, graph_dir, root.name, skq_lines, key=entry_path):
                        _say(line, log)
            except Exception as exc:  # noqa: BLE001 - deliberate: graph failure degrades to a visible line
                _say(f"GRAPH failed ({type(exc).__name__}: {str(exc)[:120]})", log)
    return text
