"""Offline tests: fusion_kit.graph.collect and fusion_kit.timeline.item_params.

No Fusion runtime: adsk stubs come from conftest.py. Timeline and entities are SimpleNamespace fakes.
"""

import math
import sys
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit.graph import GraphNode, collect, flag
from fusion_kit.timeline import item_params

_FUS: Any = sys.modules["adsk.fusion"]
_CORE: Any = sys.modules["adsk.core"]
HEALTHY = _FUS.FeatureHealthStates.HealthyFeatureHealthState
ERROR = _FUS.FeatureHealthStates.ErrorFeatureHealthState
SUPPRESSED = _FUS.FeatureHealthStates.SuppressedFeatureHealthState


@pytest.fixture
def casts() -> Iterator[None]:
    """Sketch / Feature .cast answer by the fake's .kind, like Fusion's cast (None on mismatch)."""
    _FUS.Sketch.cast.side_effect = lambda e: e if getattr(e, "kind", "") == "sketch" else None
    _FUS.Feature.cast.side_effect = lambda e: e if getattr(e, "kind", "") == "feature" else None
    _FUS.ConstructionPlane.cast.side_effect = lambda e: e if getattr(e, "kind", "") == "plane" else None
    _FUS.ConstructionAxis.cast.side_effect = lambda e: e if getattr(e, "kind", "") == "axis" else None
    # sweep path / loft sections / sketch entities: no such fakes here, so every cast misses (None), as in Fusion
    for api in (_FUS.SweepFeature, _FUS.LoftFeature, _FUS.SketchEntity, _CORE.ObjectCollection):
        api.cast.side_effect = lambda e: None
    yield
    _FUS.Sketch.cast.side_effect = None
    _FUS.Feature.cast.side_effect = None
    _FUS.ConstructionPlane.cast.side_effect = None
    _FUS.ConstructionAxis.cast.side_effect = None
    for api in (_FUS.SweepFeature, _FUS.LoftFeature, _FUS.SketchEntity, _CORE.ObjectCollection):
        api.cast.side_effect = None


def _entity(kind: str, object_type: str, **extra: Any) -> Any:
    no_attr = SimpleNamespace(itemByName=lambda group, attr: None)
    return SimpleNamespace(kind=kind, objectType=object_type, attributes=no_attr, **extra)


def _item(name: str, entity: Any, *, group: bool = False, suppressed: bool = False, healthy: bool = True) -> Any:
    state = SUPPRESSED if suppressed else (HEALTHY if healthy else ERROR)  # Fusion: suppressed item = Suppressed state
    return SimpleNamespace(
        name=name,
        isGroup=group,
        isSuppressed=suppressed,
        healthState=state,
        entity=entity,
    )


def test_item_params_converts_units_and_filters_by_owner(casts: None) -> None:
    feat = _entity("feature", "adsk::fusion::ExtrudeFeature", entityToken="tok_ext")
    other = _entity("sketch", "adsk::fusion::Sketch", entityToken="tok_sk")
    params = [
        SimpleNamespace(name="depth", value=2.0, unit="cm", expression="20 mm", createdBy=feat),
        SimpleNamespace(name="ang", value=math.pi / 2, unit="deg", expression="90 deg", createdBy=feat),
        SimpleNamespace(name="count", value=3.0, unit="", expression="3", createdBy=feat),
        SimpleNamespace(name="sk_dim", value=5.0, unit="mm", expression="5 mm", createdBy=other),
    ]
    design = SimpleNamespace(allComponents=[SimpleNamespace(modelParameters=params)])
    assert item_params(design, feat) == [
        ("depth", 20.0, "20 mm", "cm"),
        ("ang", pytest.approx(90.0), "90 deg", "deg"),
        ("count", 3.0, "3", ""),
    ]


def test_item_params_length_units_from_internal_cm(casts: None) -> None:
    feat = _entity("feature", "adsk::fusion::ExtrudeFeature", entityToken="tok_units")
    # Fusion .value is cm whatever the unit: 1 ft = 30.48 cm, 1 in = 2.54 cm, 1 yd = 91.44 cm, 1 um = 1e-4 cm, 1 mil = 0.00254 cm
    specs = [
        ("ft", 30.48, 304.8),
        ("in", 2.54, 25.4),
        ("yd", 91.44, 914.4),
        ("um", 0.0001, 0.001),
        ("mil", 0.00254, 0.0254),
    ]
    params = [SimpleNamespace(name=u, value=v, unit=u, expression=f"1 {u}", createdBy=feat) for u, v, _mm in specs]
    design = SimpleNamespace(allComponents=[SimpleNamespace(modelParameters=params)])
    rows = item_params(design, feat)
    assert [(unit, value) for _name, value, _expr, unit in rows] == [(u, pytest.approx(mm)) for u, _v, mm in specs]


def test_item_params_unknown_unit_gives_none(casts: None) -> None:
    feat = _entity("feature", "adsk::fusion::ExtrudeFeature", entityToken="tok_unknown")
    params = [SimpleNamespace(name="odd", value=1.0, unit="furlong", expression="1 furlong", createdBy=feat)]
    design = SimpleNamespace(allComponents=[SimpleNamespace(modelParameters=params)])
    assert item_params(design, feat) == [("odd", None, "1 furlong", "furlong")]


class _CountingDesign:
    """Design fake counting allComponents reads: one read = one walk over all model parameters."""

    def __init__(self, timeline: SimpleNamespace, components: list[SimpleNamespace]) -> None:
        self.timeline = timeline
        self._components = components
        self.walks = 0

    @property
    def allComponents(self) -> list[SimpleNamespace]:
        self.walks += 1
        return self._components


def test_collect_walks_model_parameters_once(casts: None) -> None:
    feats = [_entity("feature", "adsk::fusion::ExtrudeFeature", entityToken=f"tok_{k}") for k in range(3)]
    params = [
        SimpleNamespace(name=f"d{k}", value=1.0, unit="cm", expression="1 cm", createdBy=f) for k, f in enumerate(feats)
    ]
    items = [_item(f"body_{k}", f) for k, f in enumerate(feats)]
    timeline = SimpleNamespace(count=len(items), item=lambda i: items[i])
    design = _CountingDesign(timeline, [SimpleNamespace(modelParameters=params)])
    nodes = collect(design)
    assert [n.params for n in nodes] == [[(f"d{k}", 10.0, "1 cm", "cm")] for k in range(3)]
    assert design.walks == 1


@pytest.fixture
def profiles() -> Iterator[None]:
    """Profile.cast answers by .kind == 'profile'; ObjectCollection.cast answers None (one profile, not a collection)."""
    _FUS.Profile.cast.side_effect = lambda e: e if getattr(e, "kind", "") == "profile" else None
    _CORE.ObjectCollection.cast.side_effect = lambda e: None
    yield
    _FUS.Profile.cast.side_effect = None
    _CORE.ObjectCollection.cast.side_effect = None


def test_sweep_profile_recorded_as_input(casts: None, profiles: None) -> None:
    sk = _entity("sketch", "adsk::fusion::Sketch", profiles=SimpleNamespace(count=1), name="profile_sketch")
    prof = _entity("profile", "adsk::fusion::Profile", parentSketch=sk)
    sweep = _entity("feature", "adsk::fusion::SweepFeature", entityToken="tok_sweep", profile=prof)
    items = [_item("profile_sketch", sk), _item("body_sweep", sweep)]
    design = SimpleNamespace(timeline=SimpleNamespace(count=2, item=lambda i: items[i]), allComponents=[])
    nodes = collect(design)
    assert nodes[1].kind == "sweep"
    assert nodes[1].inputs == ["profile_sketch"]


def test_collect_kinds_params_and_flags(casts: None) -> None:
    sk = _entity("sketch", "adsk::fusion::Sketch", profiles=SimpleNamespace(count=2))
    plane = _entity("plane", "adsk::fusion::ConstructionPlane")
    feat = _entity("feature", "adsk::fusion::ExtrudeFeature", entityToken="tok_ext")
    items = [
        _item("base_sketch", sk),
        _item("grp", None, group=True),
        _item("base_plane", plane),
        _item("body_extrude", feat, suppressed=True, healthy=False),
    ]
    design = SimpleNamespace(timeline=SimpleNamespace(count=len(items), item=lambda i: items[i]), allComponents=[])
    assert collect(design) == [
        GraphNode(
            idx=0,
            kind="sketch",
            label="base_sketch",
            params=[("profiles", 2.0, "2", "")],
            inputs=[],
            outputs=[],
        ),
        GraphNode(idx=2, kind="plane", label="base_plane", params=[], inputs=[], outputs=[]),
        GraphNode(
            idx=3,
            kind="extrude",
            label="body_extrude",
            params=[],
            inputs=[],
            outputs=[],
            suppressed=True,
        ),
    ]


def test_suppressed_item_is_not_unhealthy_but_error_is(casts: None) -> None:
    sup = _entity("feature", "adsk::fusion::ExtrudeFeature", entityToken="tok_supp")
    bad = _entity("feature", "adsk::fusion::FilletFeature", entityToken="tok_bad")
    items = [_item("side_extrude", sup, suppressed=True), _item("broken_fillet", bad, healthy=False)]
    design = SimpleNamespace(timeline=SimpleNamespace(count=2, item=lambda i: items[i]), allComponents=[])
    nodes = collect(design)
    flag(nodes)
    assert nodes[0].healthy is True and nodes[0].flags == ["suppressed"]
    assert nodes[1].healthy is False and nodes[1].flags == ["unhealthy"]
