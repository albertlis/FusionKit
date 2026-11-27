"""Sweep path / loft section sketch reads (timeline._path_sketches, _loft_sketches). Offline: fake Fusion objects.

Each fake carries `kind`; monkeypatched `cast` returns the object only for the matching class, like adsk does.
"""

import sys
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit import timeline

_CORE: Any = sys.modules["adsk.core"]
_FUSION: Any = sys.modules["adsk.fusion"]


def _ent(kind: str, name: str = "") -> SimpleNamespace:
    return SimpleNamespace(kind=kind, parentSketch=SimpleNamespace(name=name))


def _collection(items: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(kind="collection", count=len(items), item=lambda j: items[j])


@pytest.fixture
def cast_stubs() -> Iterator[None]:
    pairs = [
        (_FUSION.LoftFeature, "loft"),
        (_FUSION.Profile, "profile"),
        (_FUSION.SketchEntity, "sketch_entity"),
        (_CORE.ObjectCollection, "collection"),
    ]
    saved = [(api, api.cast) for api, _kind in pairs]
    for api, kind in pairs:
        api.cast = lambda e, k=kind: e if getattr(e, "kind", None) == k else None
    yield
    for api, old in saved:
        api.cast = old


def test_loft_sketches_non_loft_gives_nothing(cast_stubs: None) -> None:
    assert timeline._loft_sketches(_ent("extrude", "base")) == []


def test_loft_sketches_reads_profile_and_collection_sections(cast_stubs: None) -> None:
    secs = [
        SimpleNamespace(entity=_ent("profile", "section_a")),
        SimpleNamespace(entity=_collection([_ent("profile", "section_b"), _ent("profile", "section_b")])),
    ]
    loft = SimpleNamespace(kind="loft", loftSections=SimpleNamespace(count=2, item=lambda j: secs[j]))
    assert timeline._loft_sketches(loft) == ["section_a", "section_b", "section_b"]


def test_loft_sketches_face_section_gives_no_sketch(cast_stubs: None) -> None:
    secs = [SimpleNamespace(entity=_ent("face"))]
    loft = SimpleNamespace(kind="loft", loftSections=SimpleNamespace(count=1, item=lambda j: secs[j]))
    assert timeline._loft_sketches(loft) == []
