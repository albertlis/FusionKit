"""Offline tests for fusion_kit.features guards: single_body, mirror pre-check.

No Fusion runtime: adsk stubs come from conftest.py. Bodies and features are plain fakes; units are Fusion cm.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit import features


def _body(name: str, x0: float = 0.0, x1: float = 1.0) -> Any:
    bb = SimpleNamespace(
        minPoint=SimpleNamespace(x=x0, y=0.0, z=0.0),
        maxPoint=SimpleNamespace(x=x1, y=2.0, z=3.0),
    )
    return SimpleNamespace(name=name, boundingBox=bb)


def _bodies_comp(*bodies: Any) -> Any:
    items = list(bodies)
    return SimpleNamespace(bRepBodies=SimpleNamespace(count=len(items), item=lambda i: items[i]))


# ---------------------------------------------------------------------------
# single_body
# ---------------------------------------------------------------------------
def test_single_body_returns_only_match_with_prefix() -> None:
    korpus = _body("korpus_wall")
    comp = _bodies_comp(korpus, _body("snap_tongue"))
    assert features.single_body(comp, "korpus") is korpus


def test_single_body_empty_prefix_matches_any_single_body() -> None:
    b = _body("anything")
    assert features.single_body(_bodies_comp(b)) is b


def test_single_body_zero_matches_raises_with_count() -> None:
    with pytest.raises(RuntimeError, match=r"single_body\('korpus'\): 0 bodies, expected 1"):
        features.single_body(_bodies_comp(_body("other")), "korpus")


def test_single_body_many_lists_each_name_and_bbox_mm() -> None:
    comp = _bodies_comp(_body("korpus_a", 0.0, 1.0), _body("korpus_b", 0.5, 2.0))
    with pytest.raises(RuntimeError) as exc:
        features.single_body(comp, "korpus")
    msg = str(exc.value)
    assert "2 bodies, expected 1" in msg
    assert "korpus_a X 0.00..10.00" in msg  # 1.0 cm -> 10.00 mm
    assert "korpus_b X 5.00..20.00" in msg  # 0.5 cm .. 2.0 cm -> 5.00 .. 20.00 mm


# ---------------------------------------------------------------------------
# mirror pre-check
# ---------------------------------------------------------------------------
def _feat(bodies: int) -> Any:
    return SimpleNamespace(bodies=SimpleNamespace(count=bodies), name="feat")


def test_mirror_empty_feats_raises_naming_label() -> None:
    comp: Any = MagicMock()
    with pytest.raises(ValueError, match="mirror 't_mirror': no features given"):
        features.mirror(comp, [], MagicMock(), label="t_mirror")
    comp.features.mirrorFeatures.add.assert_not_called()


def test_mirror_no_feature_with_body_raises_naming_label() -> None:
    comp: Any = MagicMock()
    with pytest.raises(ValueError, match="mirror 't_mirror': none of 2 feature"):
        features.mirror(comp, [_feat(0), _feat(0)], MagicMock(), label="t_mirror")
    comp.features.mirrorFeatures.add.assert_not_called()


def test_mirror_one_feature_with_body_is_enough() -> None:
    comp: Any = MagicMock()
    feat = features.mirror(comp, [_feat(0), _feat(1)], MagicMock(), label="t_mirror")
    comp.features.mirrorFeatures.add.assert_called_once()
    assert feat is comp.features.mirrorFeatures.add.return_value
