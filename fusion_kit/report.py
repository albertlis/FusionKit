"""Diagnostic report and measurement helpers for Fusion 360 scripts."""

from collections.abc import Sequence

import adsk.core
import adsk.fusion

from fusion_kit import enums
from fusion_kit.dfam import dfam_check, summarize_findings
from fusion_kit.features import collection, face_normal
from fusion_kit.timeline import label_stats

Xyz = tuple[float, float, float]


def _bbox_cm(boxes: list[adsk.core.BoundingBox3D]) -> tuple[Xyz, Xyz]:
    """(min_xyz, max_xyz) over boxes in cm, native units. Callers convert to mm."""
    lo: Xyz = (
        min(bb.minPoint.x for bb in boxes),
        min(bb.minPoint.y for bb in boxes),
        min(bb.minPoint.z for bb in boxes),
    )
    hi: Xyz = (
        max(bb.maxPoint.x for bb in boxes),
        max(bb.maxPoint.y for bb in boxes),
        max(bb.maxPoint.z for bb in boxes),
    )
    return lo, hi


def report(comp: adsk.fusion.Component, dfam: bool = True) -> str:
    """One-line diagnostic: name, bodies, lumps, bbox mm, volume cm3, sketch constraint status, design timeline health.

    dfam=True: appends ` dfam=<nF>F/<nW>W` to the line, then one DfAM finding per line (dfam_check per body).
    Prints and returns the text (lines joined by "\\n") (docs/gotchas.md: end script with print() self-check).
    bbox is COMP-NATIVE coords (component's own frame, ignores occurrence transform). Moved/rotated occ -> use world_bbox(occ).
    design_timeline_unhealthy is design-wide, not comp-scoped. Timeline groups carry no health state -> skipped.
    """
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)

    bodies = comp.bRepBodies.count
    lumps = sum(b.lumps.count for b in comp.bRepBodies)

    # Bounding box (cm -> mm)
    if bodies > 0:
        lo, hi = _bbox_cm([b.boundingBox for b in comp.bRepBodies])
        dx, dy, dz = ((h - l) * 10 for h, l in zip(hi, lo))
        bbox = f"{dx:.1f}x{dy:.1f}x{dz:.1f}mm"
        vol = sum(b.physicalProperties.volume for b in comp.bRepBodies)
        vol_str = f"{vol:.3f}cm3"
    else:
        bbox = "no-bodies"
        vol_str = "0cm3"

    # Sketch constraint status
    sketches = comp.sketches
    sk_total = sketches.count
    sk_constrained = sum(1 for i in range(sk_total) if sketches.item(i).isFullyConstrained)

    # Design timeline unhealthy count (groups skipped: UnknownFeatureHealthState, not a fault)
    timeline = design.timeline
    items = [timeline.item(i) for i in range(timeline.count)]
    unhealthy = sum(
        1
        for item in items
        if not item.isGroup and item.healthState != adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState
    )

    unnamed, numbered = label_stats(design)
    line = (
        f"{comp.name}: bodies={bodies} lumps={lumps} bbox={bbox} vol={vol_str} "
        f"sketches={sk_constrained}/{sk_total} constrained design_timeline_unhealthy={unhealthy} "
        f"timeline_unnamed={unnamed} timeline_numbered={numbered}"
    )
    lines = [line]
    if dfam:
        found = [f for b in comp.bRepBodies for f in dfam_check(b)]
        lines[0] = f"{line} {summarize_findings(found)}"
        lines.extend(f.line() for f in found)
    text = "\n".join(lines)
    print(text)
    return text


def world_bbox(occ: adsk.fusion.Occurrence) -> tuple[Xyz, Xyz]:
    """(min_xyz, max_xyz) in mm, ROOT (world) coords, over occ.bRepBodies (occurrence proxies).

    Use instead of report() bbox for a moved/rotated occurrence. Raises ValueError if occ has no bodies.
    """
    bodies = occ.bRepBodies
    if bodies.count == 0:
        raise ValueError(f"{occ.name}: no bodies")
    lo, hi = _bbox_cm([b.boundingBox for b in bodies])
    mins: Xyz = (lo[0] * 10, lo[1] * 10, lo[2] * 10)
    maxs: Xyz = (hi[0] * 10, hi[1] * 10, hi[2] * 10)
    return mins, maxs


def inside(body: adsk.fusion.BRepBody, x_mm: float, y_mm: float, z_mm: float) -> bool:
    """True if point (mm) is strictly inside body.

    The point is in the SAME frame as body: proxy from occ.bRepBodies -> world (ROOT) coords;
    native from comp.bRepBodies -> component coords. Points on the surface count as NOT inside.
    """
    point = adsk.core.Point3D.create(x_mm / 10, y_mm / 10, z_mm / 10)
    return body.pointContainment(point) == enums.POINT_INSIDE


def merge_runs(zs: Sequence[float], flags: Sequence[bool]) -> list[tuple[float, float]]:
    """(z_start, z_end) of each run of consecutive True flags. zs in order, same length as flags.

    Pure (no adsk). Single True sample -> (z, z). Run open at the end is kept.
    """
    if len(zs) != len(flags):
        raise ValueError(f"merge_runs: {len(zs)} zs vs {len(flags)} flags")
    runs: list[tuple[float, float]] = []
    start: float | None = None
    last = 0.0
    for z, flag in zip(zs, flags):
        if flag:
            if start is None:
                start = z
            last = z
        elif start is not None:
            runs.append((start, last))
            start = None
    if start is not None:
        runs.append((start, last))
    return runs


def _inside_or_on(body: adsk.fusion.BRepBody, x_mm: float, y_mm: float, z_mm: float) -> bool:
    point = adsk.core.Point3D.create(x_mm / 10, y_mm / 10, z_mm / 10)
    return body.pointContainment(point) in (enums.POINT_INSIDE, enums.POINT_ON)


def inside_runs(
    body: adsk.fusion.BRepBody,
    x_mm: float,
    y_mm: float,
    z0_mm: float,
    z1_mm: float,
    step_mm: float = 0.1,
) -> list[tuple[float, float]]:
    """(z_start, z_end) mm runs where vertical line (x_mm, y_mm) is INSIDE or ON body, z0..z1.

    Samples every step_mm, both ends included; consecutive inside/on samples merged via merge_runs.
    Run ends are sample positions: error <= step_mm. Same frame rule as inside(): body frame.
    """
    if z1_mm < z0_mm:
        raise ValueError(f"inside_runs: z1_mm {z1_mm} < z0_mm {z0_mm}")
    if step_mm <= 0:
        raise ValueError(f"inside_runs: step_mm must be > 0, got {step_mm}")
    n = round((z1_mm - z0_mm) / step_mm)
    zs = [round(z0_mm + k * step_mm, 6) for k in range(n + 1)]
    flags = [_inside_or_on(body, x_mm, y_mm, z) for z in zs]
    return merge_runs(zs, flags)


def _entity_name(entity: adsk.core.Base) -> str:
    """Occurrence name; BRepBody proxy -> its occurrence name; native body -> component name."""
    occ = adsk.fusion.Occurrence.cast(entity)
    if occ is not None:
        return occ.name
    body = adsk.fusion.BRepBody.cast(entity)
    if body is None:
        raise ValueError("interference: entity is neither Occurrence nor BRepBody")
    ctx = body.assemblyContext
    return ctx.name if ctx is not None else body.parentComponent.name


def interference(
    design: adsk.fusion.Design,
    items: Sequence[adsk.fusion.Occurrence | adsk.fusion.BRepBody],
) -> list[tuple[str, str, float]]:
    """Interfering pairs among items: (name1, name2, volume mm3), volume > 1e-3 mm3, rounded to 3 places.

    NOMINAL POSE ONLY: analyzeInterference reads the current transforms. Moving parts: set occ.transform2
    at each travel limit and call again (docs/gotchas.md). Coincident faces are not interference.
    Empty list = no interference. Names: assemblyContext occurrence name if set, else component name.
    Live (2026-10-09): result entities came back with no assemblyContext -> component names; occurrences
    sharing ONE component get identical names. Give each part its own component.
    """
    inp = design.createInterferenceInput(collection(items))
    inp.areCoincidentFacesIncluded = False
    res = design.analyzeInterference(inp)
    out: list[tuple[str, str, float]] = []
    for i in range(res.count):
        r = res.item(i)
        vol = r.interferenceBody.volume * 1000  # cm3 -> mm3
        if vol > 1e-3:
            out.append((_entity_name(r.entityOne), _entity_name(r.entityTwo), round(vol, 3)))
    return out


def _min_normal_z(face: adsk.fusion.BRepFace, n: int = 9) -> float:
    """Lowest normal z over an n x n parameter grid (points off the trimmed face skipped).

    Curved faces: one pointOnFace sample can land on a bore side and miss its downward crown.
    # ponytail: grid sampling, can miss a crown narrower than 1/n of the range; raise n if it matters.
    """
    ev = face.evaluator
    rng = ev.parametricRange()
    lo, hi = rng.minPoint, rng.maxPoint
    best = face_normal(face).z
    for i in range(n):
        for j in range(n):
            p = adsk.core.Point2D.create(lo.x + (hi.x - lo.x) * i / (n - 1), lo.y + (hi.y - lo.y) * j / (n - 1))
            if not ev.isParameterOnFace(p):
                continue
            ok, nv = ev.getNormalAtParameter(p)
            if ok:
                best = min(best, nv.z)
    return best


def overhangs(
    body: adsk.fusion.BRepBody,
    limit: float = 0.707,
    bed_tol_mm: float = 0.01,
    max_hole_d_mm: float = 8.0,
) -> list[tuple[str, float, float]]:
    """FDM overhang scan: downward faces with -n_z > limit. Empty list = OK.

    Normal via face_normal (orientation-aware) at face.pointOnFace. limit 0.707 = 45 deg (exact 45 deg chamfer = 0.7071).
    Skips bed faces (bbox maxZ within bed_tol_mm of body minZ) and cylinders with diameter <= max_hole_d_mm
    (small horizontal holes print fine). Upward faces never flagged.
    Returns (kind, n_z rounded 3, z_mm of pointOnFace) per flagged face; kind = "cylinder" | "plane" | "other".
    """
    body_min_z = body.boundingBox.minPoint.z * 10
    out: list[tuple[str, float, float]] = []
    for face in body.faces:
        if face.boundingBox.maxPoint.z * 10 - body_min_z <= bed_tol_mm:
            continue
        geom = face.geometry
        cyl = adsk.core.Cylinder.cast(geom)
        if cyl is not None and cyl.radius * 2 * 10 <= max_hole_d_mm:
            continue
        n_z = face_normal(face).z if adsk.core.Plane.cast(geom) else _min_normal_z(face)
        if -n_z <= limit:
            continue
        if cyl is not None:
            kind = "cylinder"
        elif adsk.core.Plane.cast(geom) is not None:
            kind = "plane"
        else:
            kind = "other"
        out.append((kind, round(n_z, 3), face.pointOnFace.z * 10))
    return out
