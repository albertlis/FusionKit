"""User Parameter utilities for Fusion 360 parametric scripts."""

import re
from types import SimpleNamespace

import adsk.core
import adsk.fusion

# Fusion stores every length param internally in cm, whatever the display unit: value * 10 -> mm.
_LENGTH_UNITS = {"mm", "cm", "m", "in", "ft"}
_ANGLE_UNITS = {"deg", "rad"}
_IDENT = re.compile(r"(?<![\w.])[A-Za-z_]\w*")


def _prune_stale(design: adsk.fusion.Design, keep: set[str]) -> None:
    """Delete user params whose name is not in keep. Repeats passes until none left.

    deleteMe() returns False (or raises RuntimeError) while another expression still
    references the param, so a pass may skip it; the dependent is deleted first and the
    next pass retries. A pass that deletes nothing with stale params left -> RuntimeError.
    """
    up = design.userParameters
    while True:
        stale = [up.item(i).name for i in range(up.count)]
        stale = [name for name in stale if name not in keep]
        if not stale:
            return
        deleted = False
        for name in stale:
            p = up.itemByName(name)
            assert p is not None  # listed from up.item() this pass
            try:
                ok = p.deleteMe()
            except RuntimeError:
                ok = False
            if ok:
                deleted = True
        if not deleted:
            raise RuntimeError("Cannot delete stale user parameters (still referenced): " + ", ".join(stale))


def ensure_params(
    design: adsk.fusion.Design,
    spec: dict[str, tuple[str, str, str]],
    overwrite: bool = True,
    prune: bool = False,
) -> None:
    """Add or update user parameters. Idempotent.

    spec: name -> (expression, units, comment)
    Two passes, so expressions may reference parameters defined later in spec:
    pass 1 creates missing params with placeholder "0"; pass 2 sets every expression.
    overwrite=True : existing param -> expression (and comment) updated; unit change raises.
    overwrite=False: existing params untouched (user-tuned values in Fusion kept, units not checked);
                     only missing params get their spec expression.
    prune=True     : after add/update, delete every user param not in spec (dependents first);
                     still referenced at the end -> RuntimeError listing names. Default keeps them.
    """
    up = design.userParameters
    missing = {name for name in spec if up.itemByName(name) is None}
    for name, (_, units, comment) in spec.items():
        if name in missing:
            up.add(name, adsk.core.ValueInput.createByString("0"), units, comment)
    for name, (expression, units, comment) in spec.items():
        if not overwrite and name not in missing:
            continue
        p = up.itemByName(name)
        assert p is not None  # created in pass 1 or pre-existing
        if p.unit != units:
            raise ValueError(
                f"Parameter {name!r} already exists with unit {p.unit!r}; "
                f"cannot change to {units!r} in place — "
                "Fusion does not support unit changes on existing parameters. "
                "Delete the parameter in the Parameters dialog and re-run."
            )
        p.expression = expression
        p.comment = comment  # always set; empty string clears the comment
    if prune:
        _prune_stale(design, set(spec))


def snapshot_geo(design: adsk.fusion.Design) -> SimpleNamespace:
    """Snapshot of user parameter values as a SimpleNamespace.

    length params (mm, cm, m, in, ft) -> mm (internal cm * 10).
    Other units (deg, rad, unitless) -> raw internal value (angles stay as stored, radians).
    Returns namespace with one attribute per parameter (readable as g.name).
    """
    up = design.userParameters
    g = SimpleNamespace()
    for i in range(up.count):
        p = up.item(i)
        setattr(g, p.name, p.value * 10 if p.unit in _LENGTH_UNITS else p.value)
    return g


def eval_fresh(design: adsk.fusion.Design, expr: str, units: str) -> float:
    """Expr -> Fusion internal units (cm for length, rad for angle), never from stale cache.

    evaluateExpression and UserParameter.value read the CACHED value of a derived user param; after
    ensure_params changes an upstream param in the same script it is not recomputed yet. So every
    user-param name in expr is replaced by its own fresh value (recursive, from .expression) with an
    explicit unit suffix; only the leftover literals reach Fusion. Non-user names (model params) stay as-is.
    """
    up = design.userParameters

    def fresh(m: re.Match[str]) -> str:
        name = m.group(0)
        p = up.itemByName(name)
        if p is None:
            return name
        if p.unit in _LENGTH_UNITS:
            suffix = " cm"  # eval_fresh returns internal cm for every length unit
        elif p.unit in _ANGLE_UNITS:
            suffix = " rad"
        elif p.unit == "":
            suffix = ""
        else:
            raise RuntimeError(f"user parameter {name!r} unit {p.unit!r} unsupported in expression {expr!r}")
        return f"({eval_fresh(design, p.expression, p.unit):.12f}{suffix})"

    return design.unitsManager.evaluateExpression(_IDENT.sub(fresh, expr), units)
