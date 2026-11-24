"""Shared primitives for fusion_kit.features_*: ops enums, ValueInput, collections, face normal, label helpers."""

import re
from collections.abc import Iterable
from typing import Literal

import adsk.core
import adsk.fusion

from fusion_kit.enums import (
    OP_CUT,
    OP_INTERSECT,
    OP_JOIN,
    OP_NEW,
)
from fusion_kit.expr import Ex, as_str
from fusion_kit.timeline import check_label

P3 = adsk.core.Point3D.create


Op = Literal["new", "join", "cut", "intersect"]


CombineOp = Literal["join", "cut", "intersect"]


_OPS: dict[Op, adsk.fusion.FeatureOperations] = {
    "new": OP_NEW,
    "join": OP_JOIN,
    "cut": OP_CUT,
    "intersect": OP_INTERSECT,
}


def _vi(expr: str | Ex) -> adsk.core.ValueInput:
    """ValueInput.createByString from a str expression or Ex."""
    return adsk.core.ValueInput.createByString(as_str(expr))


def collection[Base: adsk.core.Base](items: Iterable[Base]) -> adsk.core.ObjectCollection:
    """ObjectCollection from a python iterable, in order. Fusion APIs take ObjectCollection, not list."""
    coll = adsk.core.ObjectCollection.create()
    for it in items:
        coll.add(it)
    return coll


def _owned_by(name: str, label: str) -> bool:
    """Construction made by _axis_by_planes(label): axis 'label', planes 'label_plane_{x|y|z}', each optionally
    with Fusion's duplicate suffix ' (n)'. Exact match: a user plane 'roof_plane_trim' is not owned by 'roof'."""
    return re.fullmatch(rf"{re.escape(label)}(_plane_[xyz])?( \(\d+\))?", name) is not None


def _root_label(comp: adsk.fusion.Component, label: str) -> str:
    """Label of the root helper axis for a rotate in comp. Scoped by comp name: comps sharing a label must not
    share root constructions. Sanitized scope has no '__', so '<scope>__<label>_axis' is unambiguous."""
    scope = re.sub(r"[^a-z0-9]+", "_", comp.name.lower()).strip("_")
    return check_label(f"{scope}__{label}_axis")


def _delete_previous(comp: adsk.fusion.Component, label: str) -> None:
    """Delete constructionAxes / constructionPlanes of comp left by the previous call with the same label."""
    for coll in (comp.constructionAxes, comp.constructionPlanes):
        for i in range(coll.count - 1, -1, -1):
            item = coll.item(i)
            if _owned_by(item.name, label):
                item.deleteMe()


def face_normal(face: adsk.fusion.BRepFace, point: adsk.core.Point3D | None = None) -> adsk.core.Vector3D:
    """Face normal at point (default face.pointOnFace) via evaluator.getNormalAtPoint.

    Respects face orientation. face.geometry.normal (Plane) ignores it: reported +Z on a groove bottom (gaps file).
    Raises ValueError if the evaluator returns no normal.
    """
    ok, n = face.evaluator.getNormalAtPoint(face.pointOnFace if point is None else point)
    if not ok:
        raise ValueError("face_normal: evaluator returned no normal (point not on face?)")
    return n


def _xyz(v: adsk.core.Vector3D | adsk.core.Point3D) -> tuple[float, float, float]:
    return (v.x, v.y, v.z)


def _axis_index(axis: str) -> int:
    """'x'|'y'|'z' (any case) -> 0|1|2; anything else ValueError."""
    i = "xyz".find(axis.lower())
    if i < 0 or len(axis) != 1:
        raise ValueError(f"axis must be 'x'|'y'|'z', got {axis!r}")
    return i


def _mm(v: float) -> str:
    """Fixed-point mm expression (no scientific notation: Fusion expressions reject '1e-05 mm')."""
    return f"{v:.6f} mm"
