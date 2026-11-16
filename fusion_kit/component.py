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
