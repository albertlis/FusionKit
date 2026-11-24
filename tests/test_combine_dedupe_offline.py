"""Offline tests for features._dedupe_tools and combine() dedupe (no Fusion; adsk stubs from conftest.py).

Bodies are plain dataclasses with an entityToken str; identity checks (`is`) prove which object survived.
"""

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit import features
from fusion_kit import features_pattern
from fusion_kit.features import _dedupe_tools, combine


@dataclass(frozen=True)
class _Body:
    entityToken: str
    name: str = ""


def test_duplicates_removed_first_wins() -> None:
    a1 = _Body("A", "a1")
    a2 = _Body("A", "a2")
    b = _Body("B")
    out = _dedupe_tools(_Body("T"), [a1, b, a2])
    assert len(out) == 2
    assert out[0] is a1
    assert out[1] is b


def test_target_removed() -> None:
    target = _Body("T", "target")
    t_copy = _Body("T", "copy_of_target")
    x = _Body("X")
    y = _Body("Y")
    out = _dedupe_tools(target, [x, t_copy, y])
    assert [o.entityToken for o in out] == ["X", "Y"]
    assert all(o is not target and o is not t_copy for o in out)


def test_order_kept() -> None:
    c, b, a = _Body("C"), _Body("B"), _Body("A")
    out = _dedupe_tools(_Body("T"), [c, b, a])
    assert len(out) == 3
    assert all(o is x for o, x in zip(out, [c, b, a]))


def test_empty_tools_raises() -> None:
    with pytest.raises(ValueError, match="no tool bodies left"):
        _dedupe_tools(_Body("T"), [])


def test_only_target_duplicates_raises() -> None:
    with pytest.raises(ValueError, match="no tool bodies left"):
        _dedupe_tools(_Body("T"), [_Body("T"), _Body("T", "again")])


def test_only_repeats_of_one_body_keeps_one() -> None:
    a = _Body("A")
    out = _dedupe_tools(_Body("T"), [a, _Body("A"), _Body("A")])
    assert len(out) == 1 and out[0] is a


def test_combine_passes_deduped_tools_to_fusion(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Any] = []

    def fake_collection(items: Any) -> list[Any]:
        captured.append(list(items))
        return captured[-1]

    monkeypatch.setattr(features_pattern, "collection", fake_collection)
    comp = MagicMock()
    target = _Body("T")
    a = _Body("A")
    b = _Body("B")
    combine(comp, target, [a, _Body("A"), target, b], op="cut", label="dedupe_combine")
    assert captured == [[a, b]]
    comp.features.combineFeatures.createInput.assert_called_once()


def test_combine_raises_before_any_fusion_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(features_pattern, "collection", lambda items: list(items))
    comp = MagicMock()
    target = _Body("T")
    with pytest.raises(ValueError, match="no tool bodies left"):
        combine(comp, target, [_Body("T")], op="cut", label="dedupe_combine")
    comp.features.combineFeatures.createInput.assert_not_called()
    comp.features.combineFeatures.add.assert_not_called()
