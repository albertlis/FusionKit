"""Component and occurrence creation helpers."""

import adsk.core
import adsk.fusion


def new_comp(root: adsk.fusion.Component, name: str) -> adsk.fusion.Component:
    """Add a named sub-component under root. Returns the Component (not the Occurrence)."""
    return new_occ(root, name).component


def new_occ(root: adsk.fusion.Component, name: str) -> adsk.fusion.Occurrence:
    """Add a named sub-component under root. Returns the Occurrence."""
    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    occ.component.name = name
    return occ


def active_design() -> adsk.fusion.Design:
    """Active Fusion design. RuntimeError when no design is active (never returns None).

    -> adsk.fusion.Design (RuntimeError when no active design)
    """
    design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
    if design is None:
        raise RuntimeError("no active Fusion design")
    return design
