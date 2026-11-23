"""Live parametric robustness: set each swept User Parameter to its range ends and check the model.

Catches what a build cannot: a parameter edited in the Fusion Parameters dialog that breaks the model
(failed timeline feature, body count change, Fusion refuses the value), does nothing (BUILD-ONLY param:
Python read it at build time, so geometry never follows), or stays healthy but prints wrong (DfAM FAIL or
print FAIL that the baseline does not have).

Fusion side (sweep_params): for each swept param, set userParameters.itemByName(n).expression to each
candidate, read timeline health, native body geometry and per-body DfAM / print check (same calls as
build_check), restore the original expression (try/finally). combos: several params set together per extra
probe, restored the same way. The baseline (all originals) is measured once per sweep_params call; body
verdicts are cached per call by geometry fingerprint + print frame + suppression state, so an unchanged body
skips its DfAM / print check on later probes.
Warp grooves (features tagged WARP_GROOVE_ATTR by features.warp_relief_grooves): a probe of a param that no
groove expression refers to runs with the groove timeline items suppressed (restored in finally); its baseline
is the originals with the grooves suppressed too, so DfAM / print / no-effect compare like with like. Probes of
params a groove expression refers to keep the grooves and compare with the full baseline. Limit: on suppressed
probes the groove features are not health-checked.
Pure side (candidates, unhealthy_names, geometry, same_geometry, new_fails, judge, format_sweep, sweep_token):
offline-tested.

FAIL lines start with `DFAM_FAIL param_sweep:` so the dfam Stop hook blocks like any DfAM FAIL:
- rejected: Fusion refused the expression (RuntimeError text recorded, not swallowed silently)
- unhealthy: timeline feature(s) with failed health, not suppressed
- bodies: body count differs from expected (baseline/original probe, or bodies_expected)
- no effect: every probe gives identical geometry -> BUILD-ONLY param (skipped when no_geometry=True and for combos)
- dfam: probe has a DfAM FAIL finding ('rule: msg') the baseline does not have; first one shown, '(+N more)'
- print FAIL: probe fails the print check while the baseline passes; failing check_body fields shown
  (ovh, hang, floating, bed)
Cost: one Fusion recompute per probe; DfAM / print check per body per probe, except bodies whose geometry
fingerprint and frame match an earlier verdict in the same sweep (TIME probe lines show cache_hits=N).
"""

import math
import re
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace

import adsk.fusion

from fusion_kit.dfam import DFAM_GROUP, dfam_check
from fusion_kit.features import WARP_GROOVE_ATTR
from fusion_kit.printability import IDENTITY, PrintReport, Rot, check_body
from fusion_kit.user_params import ParamEntry, SweepRange, entry_sweep

Xyz = tuple[float, float, float]
Box = tuple[float, float, float, float, float, float]  # (min xyz, max xyz), mm, design-native (root) frame
BodyVerdict = tuple[tuple[str, ...], float, bool, str]  # (DfAM FAIL 'rule: msg' tuple, overhang mm2, print ok, reason)

VOL_TOL_CM3 = 1e-6  # same geometry: volume within 1e-6 cm3 (= 1e-3 mm3)
BOX_TOL_MM = 1e-3  # same geometry: each bbox coordinate within 1e-3 mm
COM_TOL_MM = (
    1e-3  # same geometry: each body's centre of mass coordinate within 1e-3 mm (catches holes sliding inside a body)
)


@dataclass(frozen=True)
class ItemFact:
    """Timeline item as read from Fusion. healthy = healthState == HealthyFeatureHealthState."""

    name: str
    is_group: bool
    suppressed: bool
    healthy: bool


@dataclass(frozen=True)
class BodyFact:
    """Native body as read from Fusion: volume cm3, bounding box corners cm, centre of mass cm."""

    vol_cm3: float
    lo_cm: Xyz
    hi_cm: Xyz
    com_cm: Xyz


@dataclass(frozen=True)
class Probe:
    """One measurement with the expression set. error set -> Fusion refused the expression, other fields zero.

    dfam_fails: 'rule: msg' of every DfAM FAIL finding over all bodies (build_check line text minus 'DFAM_FAIL ').
    overhang_mm2: print overhang area summed over all bodies. print_ok: every body passes check_body in its frame.
    print_reason: failing check_body fields ('hang 12.3 mm, floating 2'), joined over bodies; '' when print_ok.
    """

    value: str
    error: str | None
    unhealthy: tuple[str, ...]
    bodies: int
    vol_cm3: float
    box_mm: Box | None
    dfam_fails: tuple[str, ...] = ()
    overhang_mm2: float = 0.0
    print_ok: bool = True
    print_reason: str = ""
    com_mm: tuple[Xyz, ...] = ()  # per-body centre of mass mm, same body order as box/volume (empty when no bodies)


@dataclass(frozen=True)
class SweepRow:
    """Result for one swept param (or one combo). probes[0] = original expression (baseline). fails empty = pass."""

    name: str
    original: str
    probes: tuple[Probe, ...]
    fails: tuple[str, ...]


def candidates(original: str, opts: SweepRange) -> list[str]:
    """Expressions to probe, baseline first: original, then min/max (or values in order). Duplicates dropped.

    With neither min/max nor values -> [original] only (judge sees a single probe, no-effect check skipped).
    """
    values = opts.get("values")
    ordered = values if values is not None else [opts.get("min", original), opts.get("max", original)]
    out = [original]
    for expr in ordered:
        if expr not in out:
            out.append(expr)
    return out


def unhealthy_names(items: Sequence[ItemFact]) -> tuple[str, ...]:
    """Names of failed timeline features: not a group, not suppressed, not healthy. Order kept."""
    return tuple(i.name for i in items if not i.is_group and not i.suppressed and not i.healthy)


def geometry(bodies: Sequence[BodyFact]) -> tuple[int, float, Box | None]:
    """(body count, total volume cm3, bbox mm over all bodies). Bbox None when there are no bodies."""
    if not bodies:
        return 0, 0.0, None
    lo: Xyz = (min(b.lo_cm[0] for b in bodies), min(b.lo_cm[1] for b in bodies), min(b.lo_cm[2] for b in bodies))
    hi: Xyz = (max(b.hi_cm[0] for b in bodies), max(b.hi_cm[1] for b in bodies), max(b.hi_cm[2] for b in bodies))
    box: Box = (lo[0] * 10, lo[1] * 10, lo[2] * 10, hi[0] * 10, hi[1] * 10, hi[2] * 10)
    return len(bodies), sum(b.vol_cm3 for b in bodies), box


def _centres_mm(bodies: Sequence[BodyFact]) -> tuple[Xyz, ...]:
    """Per-body centre of mass in mm, body order kept (same order as geometry())."""
    return tuple((x * 10, y * 10, z * 10) for x, y, z in (b.com_cm for b in bodies))


def same_geometry(a: Probe, b: Probe) -> bool:
    """True when both probes measured and body count, volume, bbox and per-body centre of mass agree (tolerances)."""
    if a.error is not None or b.error is not None:
        return False
    if a.bodies != b.bodies or not math.isclose(a.vol_cm3, b.vol_cm3, rel_tol=0.0, abs_tol=VOL_TOL_CM3):
        return False
    if a.box_mm is None or b.box_mm is None:
        if not (a.box_mm is None and b.box_mm is None):
            return False
    elif not all(math.isclose(x, y, rel_tol=0.0, abs_tol=BOX_TOL_MM) for x, y in zip(a.box_mm, b.box_mm, strict=True)):
        return False
    if len(a.com_mm) != len(b.com_mm):
        return False
    return all(
        math.isclose(x, y, rel_tol=0.0, abs_tol=COM_TOL_MM)
        for ca, cb in zip(a.com_mm, b.com_mm, strict=True)
        for x, y in zip(ca, cb, strict=True)
    )


def new_fails(baseline: Probe, probe: Probe) -> list[str]:
    """DfAM FAIL findings of probe that baseline does not have (exact text). Empty when either probe is rejected."""
    if baseline.error is not None or probe.error is not None:
        return []
    return [m for m in probe.dfam_fails if m not in baseline.dfam_fails]


BRIDGE_OK_MM = 0.0  # bridge_ok_mm passed to check_body by body_verdict (hanging edges above it fail print)


def _print_fail_reason(pr: PrintReport, bridge_ok_mm: float = BRIDGE_OK_MM) -> str:
    """Failing check_body fields, comma-joined in ok-rule order ('ovh 4 mm2, hang 12.3 mm, floating 2, bed point').

    Mirrors the printability ok rule (overhang 0, hanging_max <= bridge_ok_mm, no floating points, bed 'area').
    Only the fields that fail are listed. '' when pr.ok.
    """
    if pr.ok:
        return ""
    parts: list[str] = []
    if pr.overhang_area_mm2 > 0.0:
        parts.append(f"ovh {pr.overhang_area_mm2:.3g} mm2")
    if pr.hanging_max_mm > bridge_ok_mm:
        parts.append(f"hang {pr.hanging_max_mm:.3g} mm")
    if pr.floating_points > 0:
        parts.append(f"floating {pr.floating_points}")
    if pr.bed_contact_kind != "area":
        parts.append(f"bed {pr.bed_contact_kind}")
    return ", ".join(parts)


def judge(
    name: str,
    probes: Sequence[Probe],
    no_geometry: bool,
    bodies_expected: int | None = None,
) -> tuple[str, ...]:
    """DFAM_FAIL param_sweep lines for one param or combo. probes[0] = baseline. Empty tuple = pass.

    bodies_expected None -> expected body count = baseline probe's count.
    No-effect check needs >= 2 probes, all measured, and not no_geometry.
    dfam / print lines: probes[1:] only, and only what the baseline does not already have (new_fails, print_ok).
    """
    if not probes:
        raise ValueError(f"judge({name!r}): no probes")
    baseline = probes[0]
    expected = bodies_expected if bodies_expected is not None else baseline.bodies
    fails: list[str] = []
    for p in probes:
        tag = f"{name}={p.value}"
        if p.error is not None:
            fails.append(f"DFAM_FAIL param_sweep: {tag} rejected by Fusion: {p.error}")
            continue
        if p.unhealthy:
            fails.append(f"DFAM_FAIL param_sweep: {tag} unhealthy: {', '.join(p.unhealthy)}")
        if p.bodies != expected:
            fails.append(f"DFAM_FAIL param_sweep: {tag} bodies={p.bodies} expected={expected}")
    for p in probes[1:]:
        if p.error is not None:
            continue
        tag = f"{name}={p.value}"
        new = new_fails(baseline, p)
        if new:
            more = f" (+{len(new) - 1} more)" if len(new) > 1 else ""
            fails.append(f"DFAM_FAIL param_sweep: {tag} dfam: {new[0]}{more}")
        if not p.print_ok and baseline.error is None and baseline.print_ok:
            fails.append(f"DFAM_FAIL param_sweep: {tag} print FAIL: {p.print_reason} (baseline ok)")
    all_measured = all(p.error is None for p in probes)
    if not no_geometry and len(probes) >= 2 and all_measured and all(same_geometry(baseline, p) for p in probes[1:]):
        values = ", ".join(p.value for p in probes)
        fails.append(f"DFAM_FAIL param_sweep: {name} no effect on geometry (BUILD-ONLY param) over {values}")
    return tuple(fails)


def format_sweep(rows: Sequence[SweepRow]) -> list[str]:
    """['param_sweep: N params, F fail', *fail lines]. N = swept params (+ combos), F = rows with >= 1 fail line."""
    failing = sum(1 for r in rows if r.fails)
    fails = [line for r in rows for line in r.fails]
    return [f"param_sweep: {len(rows)} params, {failing} fail", *fails]


def sweep_token(rows: Sequence[SweepRow]) -> str:
    """Summary keyword value: '<N>p/<F>F' (swept params / failing params, combos counted as rows)."""
    failing = sum(1 for r in rows if r.fails)
    return f"{len(rows)}p/{failing}F"


def _refers(name: str, exprs: Sequence[str]) -> bool:
    """True when name occurs as a whole word (not inside a longer identifier) in one of exprs."""
    pat = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
    return any(pat.search(e) for e in exprs)


def _is_groove(item: adsk.fusion.TimelineObject) -> bool:
    """True when the timeline item is a warp-groove feature/sketch (attribute set by features.warp_relief_grooves)."""
    if item.isGroup:
        return False
    attrs = getattr(item.entity, "attributes", None)
    if attrs is None:
        return False
    try:
        return attrs.itemByName(DFAM_GROUP, WARP_GROOVE_ATTR) is not None
    except RuntimeError:  # entity without attribute support
        return False


def _groove_indices(design: adsk.fusion.Design) -> tuple[int, ...]:
    """Timeline indices of the warp-groove items. () = no grooves in the design."""
    tl = design.timeline
    return tuple(i for i in range(tl.count) if _is_groove(tl.item(i)))


def _groove_exprs(design: adsk.fusion.Design, indices: Sequence[int]) -> tuple[str, ...]:
    """Expressions the groove items depend on: their sketch dimensions and the model parameters they created."""
    if not indices:
        return ()
    tl = design.timeline
    entities = [tl.item(i).entity for i in indices]
    tokens = {ent.entityToken for ent in entities}
    out: list[str] = []
    for ent in entities:
        for dim in getattr(ent, "sketchDimensions", ()):
            if dim.parameter is not None:
                out.append(dim.parameter.expression)
    for comp in design.allComponents:
        for p in comp.modelParameters:
            owner = p.createdBy
            if owner is not None and owner.entityToken in tokens:
                out.append(p.expression)
    return tuple(out)


@contextmanager
def _grooves_suppressed(design: adsk.fusion.Design, indices: Sequence[int]) -> Iterator[None]:
    """Suppress the timeline items at indices for the block. Each item's prior isSuppressed is restored in finally."""
    if not indices:
        yield
        return
    tl = design.timeline
    items = [tl.item(i) for i in indices]
    saved = [(item, item.isSuppressed) for item in items]
    try:
        for item in items:
            item.isSuppressed = True
        yield
    finally:
        for item, state in reversed(saved):
            item.isSuppressed = state


def _read_timeline(design: adsk.fusion.Design) -> tuple[ItemFact, ...]:
    timeline = design.timeline
    healthy = adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState
    out: list[ItemFact] = []
    for i in range(timeline.count):
        item = timeline.item(i)
        out.append(
            ItemFact(
                name=item.name,
                is_group=item.isGroup,
                suppressed=item.isSuppressed,
                healthy=item.healthState == healthy,
            )
        )
    return tuple(out)


def _read_bodies(design: adsk.fusion.Design) -> tuple[BodyFact, ...]:
    """Native bodies of every component (occurrence copies not counted; components sit at nominal pose)."""
    out: list[BodyFact] = []
    for comp in design.allComponents:
        for body in comp.bRepBodies:
            box = body.boundingBox
            props = body.physicalProperties
            com = props.centerOfMass
            out.append(
                BodyFact(
                    vol_cm3=props.volume,
                    lo_cm=(box.minPoint.x, box.minPoint.y, box.minPoint.z),
                    hi_cm=(box.maxPoint.x, box.maxPoint.y, box.maxPoint.z),
                    com_cm=(com.x, com.y, com.z),
                )
            )
    return tuple(out)


def body_verdict(body: adsk.fusion.BRepBody, up_rot: Rot) -> BodyVerdict:
    """(DfAM FAIL 'rule: msg' tuple, print overhang mm2, print ok, print fail reason) for one body. Reason '' when ok.

    Same calls as build_check._body_check: dfam_check(body) (reads the body's stored bed) and check_body(body, up_rot).
    Prints TIME dfam:<body> and TIME check:<body>.
    """
    t0 = time.perf_counter()
    fails = tuple(f"{f.rule}: {f.msg}" for f in dfam_check(body) if f.level == "FAIL")
    print(f"TIME dfam:{body.name} {time.perf_counter() - t0:.2f}")
    t0 = time.perf_counter()
    pr = check_body(body, up_rot, bridge_ok_mm=BRIDGE_OK_MM)
    print(f"TIME check:{body.name} {time.perf_counter() - t0:.2f}")
    return fails, pr.overhang_area_mm2, pr.ok, _print_fail_reason(pr)


def _fingerprint(body: adsk.fusion.BRepBody) -> tuple[int, ...]:
    """Geometry key: volume, bbox corners, centre of mass, face count; each coordinate rounded to its same_geometry
    tolerance. Equal keys = same geometry within tolerance. A miss only costs a re-check, never a wrong verdict.
    """
    box = body.boundingBox
    com = body.physicalProperties.centerOfMass
    corners_cm = (box.minPoint.x, box.minPoint.y, box.minPoint.z, box.maxPoint.x, box.maxPoint.y, box.maxPoint.z)
    return (
        round(body.physicalProperties.volume / VOL_TOL_CM3),
        *(round(c * 10 / BOX_TOL_MM) for c in corners_cm),
        *(round(c * 10 / COM_TOL_MM) for c in (com.x, com.y, com.z)),
        body.faces.count,
    )


class _VerdictCache:
    """Body verdicts of one sweep_params call, keyed by (geometry fingerprint, print frame, grooves suppressed).
    hits = reused verdicts."""

    def __init__(self) -> None:
        self._store: dict[tuple[tuple[int, ...], Rot, bool], BodyVerdict] = {}
        self.hits = 0

    def verdict(self, body: adsk.fusion.BRepBody, up_rot: Rot, suppressed: bool = False) -> BodyVerdict:
        key = (_fingerprint(body), up_rot, suppressed)
        found = self._store.get(key)
        if found is not None:
            self.hits += 1
            return found
        found = body_verdict(body, up_rot)
        self._store[key] = found
        return found


def _measure(
    design: adsk.fusion.Design, print_frames: Mapping[str, Rot], cache: _VerdictCache, suppressed: bool = False
) -> tuple[tuple[str, ...], float, bool, str]:
    """DfAM FAIL tuple, summed overhang mm2, all-bodies print ok, print fail reason ('; ' over failing bodies).

    Frame per component name, missing = IDENTITY. Verdicts come from cache (fingerprint hit = no DfAM / print call).
    suppressed: the grooves are suppressed in this measurement (part of the cache key).
    """
    fails: list[str] = []
    overhang = 0.0
    ok = True
    reasons: list[str] = []
    for comp in design.allComponents:
        up_rot = print_frames.get(comp.name, IDENTITY)
        for body in comp.bRepBodies:
            body_fails, body_ovh, body_ok, body_reason = cache.verdict(body, up_rot, suppressed)
            fails.extend(body_fails)
            overhang += body_ovh
            ok = ok and body_ok
            if body_reason:
                reasons.append(body_reason)
    return tuple(fails), overhang, ok, "; ".join(reasons)


def _measure_probe(
    design: adsk.fusion.Design,
    value: str,
    print_frames: Mapping[str, Rot],
    cache: _VerdictCache,
    suppressed: bool = False,
) -> Probe:
    bodies = _read_bodies(design)
    count, vol, box = geometry(bodies)
    dfam, overhang, print_ok, print_reason = _measure(design, print_frames, cache, suppressed)
    return Probe(
        value=value,
        error=None,
        unhealthy=unhealthy_names(_read_timeline(design)),
        bodies=count,
        vol_cm3=vol,
        box_mm=box,
        dfam_fails=dfam,
        overhang_mm2=overhang,
        print_ok=print_ok,
        print_reason=print_reason,
        com_mm=_centres_mm(bodies),
    )


def _set_and_measure(
    design: adsk.fusion.Design,
    sets: Sequence[tuple[adsk.fusion.UserParameter, str]],
    value: str,
    print_frames: Mapping[str, Rot],
    cache: _VerdictCache,
    off: Sequence[int] = (),
) -> Probe:
    """Set each (param, expr) in order, then measure, with the timeline items at off suppressed (restored after).

    RuntimeError from a setter -> error Probe (a finding). Caller restores the originals.
    """
    with _grooves_suppressed(design, off):
        try:
            for param, expr in sets:
                param.expression = expr
        except RuntimeError as exc:
            return Probe(value=value, error=str(exc), unhealthy=(), bodies=0, vol_cm3=0.0, box_mm=None)
        return _measure_probe(design, value, print_frames, cache, suppressed=bool(off))


def _probe(
    design: adsk.fusion.Design,
    param: adsk.fusion.UserParameter,
    expr: str,
    print_frames: Mapping[str, Rot],
    cache: _VerdictCache,
    off: Sequence[int] = (),
) -> Probe:
    """Set expr, measure. RuntimeError from the expression setter is recorded as Probe.error (a finding)."""
    return _set_and_measure(design, [(param, expr)], expr, print_frames, cache, off)


def _combo_row(
    design: adsk.fusion.Design,
    combo: Sequence[tuple[str, adsk.fusion.UserParameter, str]],
    print_frames: Mapping[str, Rot],
    bodies_expected: int | None,
    baseline: Probe,
    cache: _VerdictCache,
    off: Sequence[int] = (),
) -> SweepRow:
    """One extra probe with every (name, param, expr) of combo set together. baseline = sweep baseline matching off
    (originals, same grooves state). Name 'a+b', value 'expr_a/expr_b'. No-effect check off (a combo is one extra
    point, not a range). Restores every original in finally.
    """
    name = "+".join(n for n, _, _ in combo)
    originals = [(param, param.expression) for _, param, _ in combo]
    original = "/".join(expr for _, expr in originals)
    value = "/".join(expr for _, _, expr in combo)
    try:
        h0 = cache.hits
        t0 = time.perf_counter()
        probe = _set_and_measure(design, [(param, expr) for _, param, expr in combo], value, print_frames, cache, off)
        print(f"TIME probe:{name}={value} {time.perf_counter() - t0:.2f} cache_hits={cache.hits - h0}")
    finally:
        for param, orig in reversed(originals):
            param.expression = orig
    probes = (replace(baseline, value=original), probe)
    fails = judge(name, probes, no_geometry=True, bodies_expected=bodies_expected)
    return SweepRow(name=name, original=original, probes=probes, fails=fails)


def sweep_params(
    design: adsk.fusion.Design,
    spec: Mapping[str, ParamEntry],
    bodies_expected: int | None = None,
    combos: list[dict[str, str]] | None = None,
    print_frames: Mapping[str, Rot] | None = None,
) -> list[SweepRow]:
    """Sweep every spec param that declares min+max or values. Restores each original expression.

    Params without a range (no 4th item, or only no_geometry) are skipped. Spec name not in the design
    -> ValueError. Output: one SweepRow per swept param, in spec order, then one row per combo.
    combos: each dict = {param name: expr}, all set together as one extra probe (baseline = originals).
    Unknown name or empty combo -> ValueError before anything is set.
    print_frames: {component name: Rot} as build_and_check (missing = IDENTITY); feeds the DfAM / print check per probe.
    Baseline (unchanged originals) is measured once per call and shared by every row. Body verdicts are cached per
    call: a body whose fingerprint and frame match an earlier measurement is not re-checked.
    Warp grooves: a row whose params no groove expression refers to runs with the groove items suppressed and is
    compared with the originals measured with the grooves suppressed (measured once, lazily). Other rows keep the
    grooves and are compared with the full baseline.
    Read `fails` for DFAM_FAIL lines.
    """
    frames: Mapping[str, Rot] = print_frames or {}
    up = design.userParameters
    resolved: list[list[tuple[str, adsk.fusion.UserParameter, str]]] = []
    for combo in combos or []:
        if not combo:
            raise ValueError("param_sweep: empty combo")
        triples: list[tuple[str, adsk.fusion.UserParameter, str]] = []
        for name, expr in combo.items():
            param = up.itemByName(name)
            if param is None:
                raise ValueError(f"param_sweep: user parameter {name!r} not in design")
            triples.append((name, param, expr))
        resolved.append(triples)
    swept: list[tuple[str, SweepRange, adsk.fusion.UserParameter, str]] = []
    for name, entry in spec.items():
        opts = entry_sweep(entry)
        if opts is None or ("values" not in opts and "min" not in opts):
            continue
        param = up.itemByName(name)
        if param is None:
            raise ValueError(f"param_sweep: user parameter {name!r} not in design")
        swept.append((name, opts, param, param.expression))
    if not swept and not resolved:
        return []
    cache = _VerdictCache()
    grooves = _groove_indices(design)
    groove_exprs = _groove_exprs(design, grooves)
    h0 = cache.hits
    t0 = time.perf_counter()
    baseline = _measure_probe(design, "", frames, cache)
    print(f"TIME baseline {time.perf_counter() - t0:.2f} cache_hits={cache.hits - h0}")
    bases: dict[bool, Probe] = {False: baseline}  # key: grooves suppressed

    def off_for(names: Sequence[str]) -> tuple[int, ...]:
        """Groove items to suppress for a probe setting names: none when a name feeds a groove expression."""
        if not grooves or any(_refers(n, groove_exprs) for n in names):
            return ()
        return grooves

    def base_for(off: Sequence[int]) -> Probe:
        """Originals measured in the same grooves state as the probe (suppressed once, then reused)."""
        key = bool(off)
        if key not in bases:
            with _grooves_suppressed(design, off):
                bases[key] = _measure_probe(design, "", frames, cache, suppressed=True)
        return bases[key]

    rows: list[SweepRow] = []
    for name, opts, param, original in swept:
        off = off_for([name])
        probes: list[Probe] = [replace(base_for(off), value=original)]
        try:
            for expr in candidates(original, opts)[1:]:
                h0 = cache.hits
                t0 = time.perf_counter()
                probes.append(_probe(design, param, expr, frames, cache, off))
                print(f"TIME probe:{name}={expr} {time.perf_counter() - t0:.2f} cache_hits={cache.hits - h0}")
        finally:
            param.expression = original
        fails = judge(name, probes, bool(opts.get("no_geometry", False)), bodies_expected)
        rows.append(SweepRow(name=name, original=original, probes=tuple(probes), fails=fails))
    for triples in resolved:
        off = off_for([n for n, _, _ in triples])
        rows.append(_combo_row(design, triples, frames, bodies_expected, base_for(off), cache, off))
    return rows
