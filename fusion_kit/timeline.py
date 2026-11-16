"""Timeline labels and a compact timeline dump (input for the CadSimplifier review)."""

import re

import adsk.core
import adsk.fusion

# Labels are snake_case: tells a given name from Fusion defaults ("Extrude7", "Sketch12") without a list of them.
LABEL_RE = re.compile(r"^[a-z][a-z0-9_]*\Z")  # \Z, not $: $ also matches before a trailing "\n"
# Numbered label ("mount_hole_2"): usually one element repeated by hand instead of a pattern / mirror.
NUMBERED_RE = re.compile(r"_\d+$")


def check_label(label: str) -> str:
    """Return label if snake_case (LABEL_RE), else ValueError."""
    if not LABEL_RE.match(label):
        raise ValueError(f"label {label!r} must be snake_case ([a-z][a-z0-9_]*)")
    return label


def set_label(
    entity: adsk.fusion.Sketch | adsk.fusion.Feature | adsk.fusion.ConstructionPlane,
    label: str,
) -> None:
    """Name a timeline entity (sketch, feature, construction plane); label validated by check_label."""
    entity.name = check_label(label)


def _is_occurrence(item: adsk.fusion.TimelineObject) -> bool:
    """Occurrence items carry the component name ("part:1"), not a label -> not counted as unnamed."""
    ent = item.entity
    return ent is not None and "Occurrence" in ent.objectType


def label_stats(design: adsk.fusion.Design) -> tuple[int, int]:
    """(unnamed, numbered) over non-group, non-occurrence timeline items."""
    tl = design.timeline
    names = [it.name for it in (tl.item(i) for i in range(tl.count)) if not it.isGroup and not _is_occurrence(it)]
    unnamed = sum(1 for n in names if not LABEL_RE.match(n))
    numbered = sum(1 for n in names if LABEL_RE.match(n) and NUMBERED_RE.search(n))
    return unnamed, numbered


def _feature_exprs(design: adsk.fusion.Design) -> dict[str, list[str]]:
    """entityToken of the creating feature -> its model parameter expressions."""
    out: dict[str, list[str]] = {}
    for comp in design.allComponents:
        for p in comp.modelParameters:
            owner = adsk.fusion.Feature.cast(p.createdBy)
            if owner is None:  # sketch dims are listed per sketch instead
                continue
            out.setdefault(owner.entityToken, []).append(p.expression)
    return out


def _parent_sketches(ent: adsk.core.Base) -> list[str]:
    """Names of sketches whose profiles feed a feature (extrude / revolve), else []."""
    prof = getattr(ent, "profile", None)
    if prof is None:
        return []
    coll = adsk.core.ObjectCollection.cast(prof)
    items = [coll.item(i) for i in range(coll.count)] if coll else [prof]
    names: list[str] = []
    for it in items:
        p = adsk.fusion.Profile.cast(it)
        if p and p.parentSketch.name not in names:
            names.append(p.parentSketch.name)
    return names


def dump(design: adsk.fusion.Design) -> list[dict[str, object]]:
    """One dict per timeline item: i, name, type, healthy; sketches add profiles/curves/dims,
    features add from_sketches/exprs. Compact enough to print as JSON in one read-only execute.
    """
    exprs = _feature_exprs(design)
    tl = design.timeline
    rows: list[dict[str, object]] = []
    for i in range(tl.count):
        it = tl.item(i)
        if it.isGroup:
            continue
        ent = it.entity
        if ent is None:  # entity can be missing (e.g. a broken reference): still list the item
            rows.append({"i": i, "name": it.name, "type": None, "healthy": False})
            continue
        row: dict[str, object] = {
            "i": i,
            "name": it.name,
            "type": ent.objectType.split("::")[-1],
            "healthy": it.healthState == adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState,
        }
        sk = adsk.fusion.Sketch.cast(ent)
        if sk:
            row["profiles"] = sk.profiles.count
            row["curves"] = sk.sketchCurves.count
            row["dims"] = [d.parameter.expression for d in sk.sketchDimensions if d.parameter]
        else:
            src = _parent_sketches(ent)
            if src:
                row["from_sketches"] = src
            feat = adsk.fusion.Feature.cast(ent)
            ex = exprs.get(feat.entityToken) if feat else None
            if ex:
                row["exprs"] = ex
        rows.append(row)
    return rows


def run(_context: str) -> None:
    """Entry for loader.run_part (read-only execute): print the active design's timeline dump as JSON lines."""
    import json

    design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
    unnamed, numbered = label_stats(design)
    print(json.dumps({"doc": design.parentDocument.name, "unnamed": unnamed, "numbered": numbered}))
    for row in dump(design):
        print(json.dumps(row, separators=(",", ":")))
