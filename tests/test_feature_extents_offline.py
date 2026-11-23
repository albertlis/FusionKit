"""Offline tests: extrude extent options (to / to_offset / through_all / two_sides / start_from + ValueError combos),
revolve symmetric / to=, shell, plane_midplane, axis_by_cylinder.

No Fusion runtime: adsk stubs come from conftest.py (MagicMock). Assertions check the calls made to the stubs.
"""

import sys
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit import enums
from fusion_kit.features import axis_by_cylinder, extrude, plane_midplane, revolve, shell

_CORE: Any = sys.modules["adsk.core"]
_FUS: Any = sys.modules["adsk.fusion"]


@pytest.fixture(autouse=True)
def _reset_extent_stubs() -> Iterator[None]:
    for cls in (
        _FUS.ToEntityExtentDefinition,
        _FUS.ThroughAllExtentDefinition,
        _FUS.DistanceExtentDefinition,
        _FUS.FromEntityStartDefinition,
        _FUS.OffsetStartDefinition,
    ):
        cls.create.reset_mock()
    yield


def _ext_comp() -> tuple[Any, MagicMock]:
    comp: Any = MagicMock(name="comp")
    comp.bRepBodies = ["A"]
    return comp, comp.features.extrudeFeatures.createInput.return_value


def _rev_comp() -> tuple[Any, MagicMock]:
    comp: Any = MagicMock(name="comp")
    return comp, comp.features.revolveFeatures.createInput.return_value


# ---------------------------------------------------------------------------
# extrude: to= / to_offset= / through_all= / two_sides= / start_from=
# ---------------------------------------------------------------------------
def test_extrude_default_distance_unchanged() -> None:
    comp, inp = _ext_comp()
    extrude(comp, MagicMock(name="prof"), "10 mm", label="t_extrude")
    _FUS.DistanceExtentDefinition.create.assert_called_once_with(_CORE.ValueInput.createByString.return_value)
    _CORE.ValueInput.createByString.assert_any_call("10 mm")
    _FUS.ToEntityExtentDefinition.create.assert_not_called()
    inp.setOneSideExtent.assert_called_once_with(
        _FUS.DistanceExtentDefinition.create.return_value, enums.POSITIVE_EXTENT
    )


def test_extrude_to_entity_without_offset_uses_two_arg_create() -> None:
    comp, inp = _ext_comp()
    face = MagicMock(name="face")
    extrude(comp, MagicMock(name="prof"), None, to=face, label="t_extrude")
    _FUS.ToEntityExtentDefinition.create.assert_called_once_with(face, False)
    inp.setOneSideExtent.assert_called_once_with(
        _FUS.ToEntityExtentDefinition.create.return_value, enums.POSITIVE_EXTENT
    )
    _FUS.DistanceExtentDefinition.create.assert_not_called()


def test_extrude_to_entity_with_offset_passes_value_input() -> None:
    comp, inp = _ext_comp()
    face = MagicMock(name="face")
    extrude(comp, MagicMock(name="prof"), None, to=face, to_offset="-2 mm", label="t_extrude")
    _CORE.ValueInput.createByString.assert_any_call("-2 mm")
    args = _FUS.ToEntityExtentDefinition.create.call_args.args
    assert args[0] is face
    assert args[1] is False
    assert args[2] is _CORE.ValueInput.createByString.return_value
    inp.setOneSideExtent.assert_called_once()


def test_extrude_through_all_uses_through_all_definition() -> None:
    comp, inp = _ext_comp()
    extrude(comp, MagicMock(name="prof"), None, through_all=True, op="cut", label="t_extrude")
    _FUS.ThroughAllExtentDefinition.create.assert_called_once_with()
    inp.setOneSideExtent.assert_called_once_with(
        _FUS.ThroughAllExtentDefinition.create.return_value, enums.POSITIVE_EXTENT
    )
    _FUS.DistanceExtentDefinition.create.assert_not_called()
    assert inp.participantBodies == ["A"]


def test_extrude_two_sides_sets_both_distances() -> None:
    comp, inp = _ext_comp()
    extrude(comp, MagicMock(name="prof"), None, two_sides=("5 mm", "3 mm"), label="t_extrude")
    assert _FUS.DistanceExtentDefinition.create.call_count == 2
    _CORE.ValueInput.createByString.assert_any_call("5 mm")
    _CORE.ValueInput.createByString.assert_any_call("3 mm")
    inp.setTwoSidesExtent.assert_called_once()
    inp.setOneSideExtent.assert_not_called()


def test_extrude_start_from_sets_from_entity_start_zero_offset() -> None:
    comp, inp = _ext_comp()
    ent = MagicMock(name="plane")
    extrude(comp, MagicMock(name="prof"), "10 mm", start_from=ent, label="t_extrude")
    _FUS.FromEntityStartDefinition.create.assert_called_once_with(ent, _CORE.ValueInput.createByString.return_value)
    _CORE.ValueInput.createByString.assert_any_call("0 mm")
    assert inp.startExtent is _FUS.FromEntityStartDefinition.create.return_value
    _FUS.OffsetStartDefinition.create.assert_not_called()


_ENT = MagicMock(name="ent")
_SIDES = ("1 mm", "2 mm")


@pytest.mark.parametrize(
    ("distance", "kwargs", "match"),
    [
        (None, {"to": _ENT, "through_all": True}, "mutually exclusive"),
        (None, {"to": _ENT, "two_sides": _SIDES}, "mutually exclusive"),
        (None, {"through_all": True, "two_sides": _SIDES}, "mutually exclusive"),
        (None, {"to_offset": "1 mm"}, "to_offset needs to="),
        ("10 mm", {"to": _ENT}, "distance given"),
        ("10 mm", {"through_all": True}, "distance given"),
        ("10 mm", {"two_sides": _SIDES}, "distance given"),
        (None, {}, "distance required"),
        (None, {"to": _ENT, "symmetric": True}, "symmetric only"),
        (None, {"two_sides": _SIDES, "symmetric": True}, "symmetric only"),
        ("10 mm", {"start_from": _ENT, "start_offset": "1 mm"}, "mutually exclusive"),
        ("10 mm", {"start_from": _ENT, "symmetric": True}, "start_from and symmetric"),
        (None, {"two_sides": _SIDES, "start_from": _ENT}, "two_sides starts"),
        (None, {"two_sides": _SIDES, "start_offset": "1 mm"}, "two_sides starts"),
        (None, {"through_all": True, "taper": "-5 deg"}, "taper only"),
        ("10 mm", {"symmetric": True, "start_offset": "1 mm"}, "symmetric"),
    ],
)
def test_extrude_invalid_option_combo_raises_before_create_input(
    distance: str | None, kwargs: dict[str, Any], match: str
) -> None:
    comp, _ = _ext_comp()
    with pytest.raises(ValueError, match=match):
        extrude(comp, MagicMock(name="prof"), distance, label="t_extrude", **kwargs)
    comp.features.extrudeFeatures.createInput.assert_not_called()


# ---------------------------------------------------------------------------
# revolve: full (default) / symmetric / to=
# ---------------------------------------------------------------------------
def test_revolve_default_is_full_one_side_angle() -> None:
    comp, inp = _rev_comp()
    revolve(comp, MagicMock(name="prof"), MagicMock(name="axis"), "new", label="t_rev")
    _CORE.ValueInput.createByString.assert_any_call("360 deg")
    inp.setAngleExtent.assert_called_once_with(False, _CORE.ValueInput.createByString.return_value)


def test_revolve_symmetric_passes_true_and_angle() -> None:
    comp, inp = _rev_comp()
    revolve(comp, MagicMock(name="prof"), MagicMock(name="axis"), "new", "180 deg", symmetric=True, label="t_rev")
    _CORE.ValueInput.createByString.assert_any_call("180 deg")
    inp.setAngleExtent.assert_called_once_with(True, _CORE.ValueInput.createByString.return_value)


def test_revolve_to_entity_uses_one_side_to_extent() -> None:
    comp, inp = _rev_comp()
    face = MagicMock(name="face")
    revolve(comp, MagicMock(name="prof"), MagicMock(name="axis"), "new", to=face, label="t_rev")
    inp.setOneSideToExtent.assert_called_once_with(face)
    inp.setAngleExtent.assert_not_called()


@pytest.mark.parametrize(
    ("angle", "kwargs", "match"),
    [
        ("90 deg", {"to": MagicMock(name="face")}, "to and angle"),
        (None, {"to": MagicMock(name="face"), "symmetric": True}, "to and symmetric"),
    ],
)
def test_revolve_to_conflicts_raise_before_create_input(angle: str | None, kwargs: dict[str, Any], match: str) -> None:
    comp, _ = _rev_comp()
    with pytest.raises(ValueError, match=match):
        revolve(comp, MagicMock(name="prof"), MagicMock(name="axis"), "new", angle, label="t_rev", **kwargs)
    comp.features.revolveFeatures.createInput.assert_not_called()


# ---------------------------------------------------------------------------
# shell / plane_midplane / axis_by_cylinder
# ---------------------------------------------------------------------------
def test_shell_empty_faces_rejected() -> None:
    comp: Any = MagicMock(name="comp")
    with pytest.raises(ValueError, match="faces"):
        shell(comp, [], "1.2 mm", label="t_shell")
    comp.features.shellFeatures.createInput.assert_not_called()


def test_shell_sets_inside_thickness_and_collects_faces(object_collection: MagicMock) -> None:
    comp: Any = MagicMock(name="comp")
    face = MagicMock(name="face")
    inp = comp.features.shellFeatures.createInput.return_value
    shell(comp, [face], "1.2 mm", label="t_shell")
    assert comp.features.shellFeatures.createInput.call_args.args[0] is object_collection
    object_collection.add.assert_called_once_with(face)
    _CORE.ValueInput.createByString.assert_any_call("1.2 mm")
    assert inp.insideThickness is _CORE.ValueInput.createByString.return_value
    comp.features.shellFeatures.add.assert_called_once_with(inp)


def test_plane_midplane_failed_define_raises() -> None:
    comp: Any = MagicMock(name="comp")
    comp.constructionPlanes.createInput.return_value.setByTwoPlanes.return_value = False
    with pytest.raises(RuntimeError, match="setByTwoPlanes"):
        plane_midplane(comp, MagicMock(name="p1"), MagicMock(name="p2"), label="t_mid")
    comp.constructionPlanes.add.assert_not_called()


def test_plane_midplane_names_feature() -> None:
    comp: Any = MagicMock(name="comp")
    comp.constructionPlanes.createInput.return_value.setByTwoPlanes.return_value = True
    plane = plane_midplane(comp, MagicMock(name="p1"), MagicMock(name="p2"), label="t_mid")
    assert plane is comp.constructionPlanes.add.return_value
    assert plane.name == "t_mid"


def test_axis_by_cylinder_non_circular_face_raises() -> None:
    comp: Any = MagicMock(name="comp")
    comp.constructionAxes.count = 0  # no previous constructions to delete
    comp.constructionPlanes.count = 0
    comp.constructionAxes.createInput.return_value.setByCircularFace.return_value = False
    with pytest.raises(RuntimeError, match="cylindrical"):
        axis_by_cylinder(comp, MagicMock(name="face"), label="t_axis")
    comp.constructionAxes.add.assert_not_called()


def test_axis_by_cylinder_names_axis() -> None:
    comp: Any = MagicMock(name="comp")
    face = MagicMock(name="face")
    comp.constructionAxes.count = 0  # no previous constructions to delete
    comp.constructionPlanes.count = 0
    inp = comp.constructionAxes.createInput.return_value
    inp.setByCircularFace.return_value = True
    axis = axis_by_cylinder(comp, face, label="t_axis")
    inp.setByCircularFace.assert_called_once_with(face)
    assert axis is comp.constructionAxes.add.return_value
    assert axis.name == "t_axis"
