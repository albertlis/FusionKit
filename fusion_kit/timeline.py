"""Timeline labels and a compact timeline dump (input for the CadSimplifier review)."""

import math
import os
import re
import sys

import adsk.core
import adsk.fusion

from fusion_kit.component import active_design
from fusion_kit.graph import ANGLE_UNITS, LENGTH_UNITS, ParamRow

# Fusion internal length is cm, display mm. Private per module (same factor as graph.py), never imported across modules.
_CM_TO_MM = 10.0

# Labels are snake_case: tells a given name from Fusion defaults ("Extrude7", "Sketch12") without a list of them.
LABEL_RE = re.compile(r"^[a-z][a-z0-9_]*\Z")  # \Z, not $: $ also matches before a trailing "\n"
# Numbered label ("mount_hole_2"): usually one element repeated by hand instead of a pattern / mirror.
NUMBERED_RE = re.compile(r"_\d+$")
# Source tag: "basename.py:line" of the build-script line that labelled an entity (written by _tag_source).
SRC_GROUP = "fk"
SRC_ATTR = "src"
_PKG_DIR = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))


def check_label(label: str) -> str:
    """Return label if snake_case (LABEL_RE), else ValueError."""
    if not LABEL_RE.match(label):
        raise ValueError(f"label {label!r} must be snake_case ([a-z][a-z0-9_]*)")
    return label


def _in_pkg(filename: str) -> bool:
    """True if filename lies inside the fusion_kit package directory."""
    return os.path.normcase(os.path.abspath(filename)).startswith(_PKG_DIR + os.sep)


def _tag_source(
    entity: adsk.fusion.Sketch | adsk.fusion.Feature | adsk.fusion.ConstructionPlane | adsk.fusion.ConstructionAxis,
) -> None:
    """Attach 'basename.py:line' of the first frame outside fusion_kit (the build script) as attribute SRC_GROUP/SRC_ATTR.

    No outside frame -> nothing written; the graph then shows an empty src for the entity (visible as empty src in the graph).
    Existing attribute is deleted first, so re-labelling replaces it.
    """
    f = sys._getframe()
    while f is not None and _in_pkg(f.f_code.co_filename):
        f = f.f_back
    if f is None:
        return
    attrs = entity.attributes
    old = attrs.itemByName(SRC_GROUP, SRC_ATTR)
    if old is not None:
        old.deleteMe()
    attrs.add(SRC_GROUP, SRC_ATTR, f"{os.path.basename(f.f_code.co_filename)}:{f.f_lineno}")


def set_label(
    entity: adsk.fusion.Sketch | adsk.fusion.Feature | adsk.fusion.ConstructionPlane | adsk.fusion.ConstructionAxis,
    label: str,
) -> None:
    """Name a timeline entity (sketch, feature, construction plane / axis); label validated by check_label.

    Also tags the entity with its build-script source line (_tag_source).
    """
    entity.name = check_label(label)
    _tag_source(entity)


def source_of(
    entity: adsk.fusion.Sketch | adsk.fusion.Feature | adsk.fusion.ConstructionPlane | adsk.fusion.ConstructionAxis,
) -> str | None:
    """'basename.py:line' of the build-script line that labelled entity (set_label), else None."""
    attr = entity.attributes.itemByName(SRC_GROUP, SRC_ATTR)
    return attr.value if attr is not None else None


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


def _param_row(p: adsk.fusion.ModelParameter) -> ParamRow:
    """(name, value, expression, unit). Value: mm (length unit), deg (angle unit), raw (unitless).

    None when the parameter has no value or its unit is not one of LENGTH_UNITS / ANGLE_UNITS / "".
    Fusion .value is internal (cm, radians) whatever p.unit says (gotchas.md), so lengths always go through _CM_TO_MM.
    """
    value = p.value
    unit = p.unit
    shown: float | None = None
    if value is not None:
        if unit in LENGTH_UNITS:
            shown = value * _CM_TO_MM
        elif unit in ANGLE_UNITS:
            shown = math.degrees(value)
        elif unit == "":
            shown = value
    return (p.name, shown, p.expression, unit)


def param_index(design: adsk.fusion.Design) -> dict[str, list[ParamRow]]:
    """entityToken of the creating feature -> its ParamRows. One walk over all model parameters.

    Build once per collect and pass it to item_params; item_params without it walks the design on every call.
    """
    out: dict[str, list[ParamRow]] = {}
    for comp in design.allComponents:
        for p in comp.modelParameters:
            owner = adsk.fusion.Feature.cast(p.createdBy)
            if owner is not None:
                out.setdefault(owner.entityToken, []).append(_param_row(p))
    return out


def item_params(
    design: adsk.fusion.Design,
    feature: adsk.fusion.Feature,
    index: dict[str, list[ParamRow]] | None = None,
) -> list[ParamRow]:
    """ParamRows of the model parameters created by feature (see _param_row). index: param_index(design) if reused."""
    rows = param_index(design) if index is None else index
    return list(rows.get(feature.entityToken, []))


def _parent_sketches(ent: adsk.core.Base) -> list[str]:
    """Names of sketches whose profiles feed a feature (its `profile` attribute, any feature kind), else []."""
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


def _sketch_names(e: adsk.core.Base | None) -> list[str]:
    """Parent sketch names of a Profile, a SketchEntity or an ObjectCollection of them. [] for faces, edges, None."""
    if e is None:
        return []
    coll = adsk.core.ObjectCollection.cast(e)
    if coll is not None:
        return [name for i in range(coll.count) for name in _sketch_names(coll.item(i))]
    prof = adsk.fusion.Profile.cast(e)
    if prof is not None:
        return [prof.parentSketch.name]
    sk_ent = adsk.fusion.SketchEntity.cast(e)
    return [sk_ent.parentSketch.name] if sk_ent is not None else []


def _loft_sketches(ent: adsk.core.Base) -> list[str]:
    """Sketch names behind loft sections (LoftFeature.loftSections -> LoftSection.entity), else []."""
    loft = adsk.fusion.LoftFeature.cast(ent)
    if loft is None:
        return []
    secs = loft.loftSections
    return [name for j in range(secs.count) for name in _sketch_names(secs.item(j).entity)]


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

    design = active_design()
    unnamed, numbered = label_stats(design)
    print(json.dumps({"doc": design.parentDocument.name, "unnamed": unnamed, "numbered": numbered}))
    for row in dump(design):
        print(json.dumps(row, separators=(",", ":")))
