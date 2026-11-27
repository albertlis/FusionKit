"""Offline tests: fusion_kit.timeline — check_label, set_label, label_stats, dump.

No Fusion runtime: adsk stubs come from conftest.py. Timeline and entities are SimpleNamespace fakes;
Sketch / Feature cast() is faked per kind (same style as test_report_offline.py).
"""

import sys
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit.timeline import check_label, dump, label_stats, set_label

_FUS: Any = sys.modules["adsk.fusion"]


def _item(
    name: str,
    *,
    object_type: str = "adsk::fusion::ExtrudeFeature",
    group: bool = False,
) -> Any:
    return SimpleNamespace(
        name=name,
        isGroup=group,
        entity=SimpleNamespace(objectType=object_type),
    )


def _timeline(items: list[Any]) -> Any:
    return SimpleNamespace(
        timeline=SimpleNamespace(count=len(items), item=lambda i: items[i]),
        allComponents=[],
    )


@pytest.fixture
def cast_timeline() -> Iterator[None]:
    """Sketch / Feature .cast answer by the fake's .kind, like Fusion's cast (None on mismatch)."""
    _FUS.Sketch.cast.side_effect = lambda e: e if getattr(e, "kind", "") == "sketch" else None
    _FUS.Feature.cast.side_effect = lambda e: e if getattr(e, "kind", "") == "feature" else None
    yield
    _FUS.Sketch.cast.side_effect = None
    _FUS.Feature.cast.side_effect = None


# ---------------------------------------------------------------------------
# check_label(): snake_case gate
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("label", ["a", "hole_2", "wall_2mm"])
def test_check_label_returns_valid_snake_case_label(label: str) -> None:
    assert check_label(label) == label


@pytest.mark.parametrize("label", ["1abc", "Abc", "a-b", "", "é"])
def test_check_label_rejects_non_snake_case(label: str) -> None:
    with pytest.raises(ValueError, match="must be snake_case"):
        check_label(label)


# ---------------------------------------------------------------------------
# set_label(): validated assignment to entity.name
# ---------------------------------------------------------------------------
def _attrs_stub() -> SimpleNamespace:
    """Fusion entity .attributes: nothing stored, add() is a no-op. set_label -> _tag_source needs it."""
    return SimpleNamespace(itemByName=lambda g, n: None, add=lambda g, n, v: None)


def test_set_label_assigns_checked_name() -> None:
    entity: Any = SimpleNamespace(name="Sketch12", attributes=_attrs_stub())
    set_label(entity, "mount_hole")
    assert entity.name == "mount_hole"


def test_set_label_tags_source_with_caller_line() -> None:
    recorded: list[tuple[str, str, str]] = []
    attrs = SimpleNamespace(itemByName=lambda g, n: None, add=lambda g, n, v: recorded.append((g, n, v)))
    entity: Any = SimpleNamespace(name="Sketch12", attributes=attrs)
    lineno = sys._getframe().f_lineno + 1
    set_label(entity, "mount_hole")
    assert recorded == [("fk", "src", f"test_timeline_offline.py:{lineno}")]


def test_set_label_deletes_existing_source_before_add() -> None:
    calls: list[str] = []
    old = SimpleNamespace(deleteMe=lambda: calls.append("delete"))
    attrs = SimpleNamespace(itemByName=lambda g, n: old, add=lambda g, n, v: calls.append("add"))
    entity: Any = SimpleNamespace(name="Sketch12", attributes=attrs)
    set_label(entity, "mount_hole")
    assert calls == ["delete", "add"]


def test_set_label_invalid_keeps_previous_name() -> None:
    entity: Any = SimpleNamespace(name="Sketch12")
    with pytest.raises(ValueError, match="must be snake_case"):
        set_label(entity, "Mount-Hole")
    assert entity.name == "Sketch12"


# ---------------------------------------------------------------------------
# label_stats(): (unnamed, numbered) over timeline items
# ---------------------------------------------------------------------------
def test_label_stats_counts_default_names_as_unnamed() -> None:
    design = _timeline([_item("Extrude7"), _item("Sketch12"), _item("mount_hole")])
    assert label_stats(design) == (2, 0)


def test_label_stats_counts_trailing_digit_labels_as_numbered() -> None:
    # hole_2 numbered; wall_2mm is a label but digit is not trailing; mount_hole plain.
    design = _timeline([_item("hole_2"), _item("wall_2mm"), _item("mount_hole")])
    assert label_stats(design) == (0, 1)


def test_label_stats_skips_group_items() -> None:
    design = _timeline([_item("Extrude7", group=True)])
    assert label_stats(design) == (0, 0)


def test_label_stats_skips_occurrence_items() -> None:
    design = _timeline([_item("part:1", object_type="adsk::fusion::Occurrence")])
    assert label_stats(design) == (0, 0)


# ---------------------------------------------------------------------------
# dump(): one row per non-group timeline item
# ---------------------------------------------------------------------------
def test_dump_sketch_row_counts_profiles_curves_and_dims(cast_timeline: None) -> None:
    sk = SimpleNamespace(
        kind="sketch",
        objectType="adsk::fusion::Sketch",
        profiles=SimpleNamespace(count=1),
        sketchCurves=SimpleNamespace(count=4),
        sketchDimensions=[SimpleNamespace(parameter=SimpleNamespace(expression="30 mm"))],
    )
    sketch_item = SimpleNamespace(
        name="Sketch12",
        isGroup=False,
        healthState=_FUS.FeatureHealthStates.HealthyFeatureHealthState,
        entity=sk,
    )
    assert dump(_timeline([sketch_item])) == [
        {
            "i": 0,
            "name": "Sketch12",
            "type": "Sketch",
            "healthy": True,
            "profiles": 1,
            "curves": 4,
            "dims": ["30 mm"],
        }
    ]


def test_dump_feature_row_lists_only_own_expressions(cast_timeline: None) -> None:
    feat = SimpleNamespace(kind="feature", entityToken="tok_ext", objectType="adsk::fusion::ExtrudeFeature")
    sketch_owner = SimpleNamespace(kind="sketch", entityToken="tok_sk")
    feature_item = SimpleNamespace(
        name="Extrude7",
        isGroup=False,
        healthState=_FUS.FeatureHealthStates.ErrorFeatureHealthState,
        entity=feat,
    )
    design = _timeline([feature_item])
    design.allComponents = [
        SimpleNamespace(
            modelParameters=[
                SimpleNamespace(createdBy=feat, expression="20 mm"),
                SimpleNamespace(createdBy=sketch_owner, expression="30 mm"),
            ]
        )
    ]
    assert dump(design) == [
        {
            "i": 0,
            "name": "Extrude7",
            "type": "ExtrudeFeature",
            "healthy": False,
            "exprs": ["20 mm"],
        }
    ]
