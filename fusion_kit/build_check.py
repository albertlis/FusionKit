"""One execute = build + all verification, compact keyword output.

Chain: loader.run_part(build) with stdout captured -> report_data (dfam=False) + dfam_check + printability.check_body per body
(print orientation) -> interference over root occurrences -> view.save_screenshot.
Output: one line per body, one summary line, DETAIL_MAX detail lines per body / for interference.
Build stdout (entry-script report() calls) is never printed on success; on error its last BUILD_TAIL_LINES lines are printed.

Pure parts (format_check) take fake inputs and are offline-tested. build_and_check is the Fusion side.
No try/except swallowing: any error propagates (CLAUDE.md: exception = debug info).
"""

import contextlib
import io
from dataclasses import dataclass

import adsk.core
import adsk.fusion

from fusion_kit.dfam import dfam_check, summarize_findings
from fusion_kit.loader import run_part
from fusion_kit.printability import IDENTITY, Rot, check_body
from fusion_kit.report import interference as find_interference
from fusion_kit.report import report_data
from fusion_kit.view import save_screenshot

DETAIL_MAX = 3  # detail lines per body (dfam FAIL + print FAIL), and for interference; overflow -> "... +N more"
BUILD_TAIL_LINES = 20  # captured build stdout lines printed when the build raises


@dataclass(frozen=True)
class BodyCheck:
    name: str
    vol_cm3: float
    bbox_mm: tuple[float, float, float]
    dfam: str  # summarize_findings text, e.g. "dfam=0F/1W"
    dfam_fails: list[str]  # Finding.line() of FAIL findings
    overhang_mm2: float
    hanging_mm: float
    print_ok: bool
    print_fails: list[str]  # PRINT_FAIL detail lines (empty when print_ok)


@dataclass(frozen=True)
class Summary:
    interf: int
    interf_lines: list[str]
    unhealthy: int
    unnamed: int
    unconstr: int
    png: str | None


def _cap(lines: list[str], limit: int) -> list[str]:
    """First limit-1 lines + '... +N more' when more than limit lines."""
    if len(lines) <= limit:
        return lines
    return lines[: limit - 1] + [f"... +{len(lines) - limit + 1} more"]


def format_check(bodies: list[BodyCheck], summary: Summary) -> str:
    """Compact keyword text: per body 'name vol= bbox= dfam= ovh= hang= print=' + detail lines, then summary line."""
    out: list[str] = []
    for b in bodies:
        x, y, z = (f"{v:.4g}" for v in b.bbox_mm)
        out.append(
            f"{b.name} vol={b.vol_cm3:.4g} bbox={x}x{y}x{z} {b.dfam} "
            f"ovh={b.overhang_mm2:.3g} hang={b.hanging_mm:.3g} print={'ok' if b.print_ok else 'FAIL'}"
        )
        out.extend(_cap(b.dfam_fails + b.print_fails, DETAIL_MAX))
    out.append(
        f"interf={summary.interf} unhealthy={summary.unhealthy} timeline_unnamed={summary.unnamed} "
        f"unconstr={summary.unconstr} png={summary.png or 'none'}"
    )
    out.extend(_cap(summary.interf_lines, DETAIL_MAX))
    return "\n".join(out)


def _check_frame_keys(print_frames: dict[str, Rot], names: list[str]) -> None:
    unknown = sorted(set(print_frames) - set(names))
    if unknown:
        raise ValueError(f"build_check: print_frames keys not components {unknown}; components: {names}")


def _body_check(name: str, body: adsk.fusion.BRepBody, up_rot: Rot) -> BodyCheck:
    box = body.boundingBox
    bbox = (
        (box.maxPoint.x - box.minPoint.x) * 10,
        (box.maxPoint.y - box.minPoint.y) * 10,
        (box.maxPoint.z - box.minPoint.z) * 10,
    )
    findings = dfam_check(body)
    pr = check_body(body, up_rot)
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
    return BodyCheck(
        name=name,
        vol_cm3=body.physicalProperties.volume,
        bbox_mm=bbox,
        dfam=summarize_findings(findings),
        dfam_fails=[f.line() for f in findings if f.level == "FAIL"],
        overhang_mm2=pr.overhang_area_mm2,
        hanging_mm=pr.hanging_total_mm,
        print_ok=pr.ok,
        print_fails=print_fails,
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
) -> str:
    """run_part(entry, 'build') + verify in one execute. Prints and returns the compact text.

    entry_path: part entry script (def run(_context)). Context passed = 'build'. Its stdout is not printed (see _run_build_quiet).
    print_frames: {component name: Rot} print orientation (printability.rot_x/rot_y/rot_z/compose); missing = IDENTITY
        (bed face on min Z). Unknown component name -> ValueError.
    interference: pairs over ROOT occurrences, nominal pose (report.interference).
    screenshot: .png path, 600x400, current camera (view.save_screenshot). None -> png=none.
    Components = unique by entityToken over root occurrences; multi-body comp -> lines 'name[i]'.
    """
    frames = print_frames or {}
    _run_build_quiet(entry_path)
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
            bodies.append(_body_check(name, body, up_rot))

    pairs = find_interference(design, occs) if interference and len(occs) > 1 else []
    summary = Summary(
        interf=len(pairs),
        interf_lines=[f"INTERF {a} x {b} vol_mm3={v:.4g}" for a, b, v in pairs],
        unhealthy=unhealthy,
        unnamed=unnamed,
        unconstr=total - constrained,
        png=save_screenshot(screenshot) if screenshot else None,
    )
    text = format_check(bodies, summary)
    print(text)
    return text
