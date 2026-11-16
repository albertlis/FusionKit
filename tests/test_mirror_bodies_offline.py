"""Offline tests for fusion_kit.features.mirror_bodies and the cut-extrude hint in mirror / pattern helpers.

No Fusion runtime: adsk stubs come from conftest.py. ExtrudeFeature is swapped for a fake class via monkeypatch
so isinstance() in the helper sees it.
"""

import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from fusion_kit import enums, features
from fusion_kit.features import mirror, mirror_bodies, pattern_circular, pattern_rectangular

_FUS: Any = sys.modules["adsk.fusion"]


class _FakeExtrude:
    """ExtrudeFeature stand-in: operation + bodies.count (mirror() reads bodies.count)."""

    def __init__(self, operation: object) -> None:
        self.operation = operation
        self.bodies = SimpleNamespace(count=1)


@pytest.fixture
def fake_extrude_class(monkeypatch: pytest.MonkeyPatch) -> type:
    # raising=False: MagicMock fusion stub has no ExtrudeFeature attr until patched
    monkeypatch.setattr(_FUS, "ExtrudeFeature", _FakeExtrude, raising=False)
    return _FakeExtrude


# ---------------------------------------------------------------------------
# mirror_bodies
# ---------------------------------------------------------------------------
def test_mirror_bodies_collection_plane_and_returned_bodies(object_collection: MagicMock) -> None:
    comp: Any = MagicMock()
    mf = comp.features.mirrorFeatures
    b1: Any = MagicMock(name="b1")
    b2: Any = MagicMock(name="b2")
    m1: Any = MagicMock(name="m1")
    m2: Any = MagicMock(name="m2")
    mf.add.return_value.bodies = [m1, m2]
    plane: Any = MagicMock(name="plane")

    out = mirror_bodies(comp, [b1, b2], plane, label="t_mirror_bodies")

    object_collection.add.assert_has_calls([call(b1), call(b2)])
    assert mf.createInput.call_args.args == (object_collection, plane)
    mf.add.assert_called_once_with(mf.createInput.return_value)
    assert mf.createInput.return_value.patternComputeOption is enums.OPTIMIZED_PATTERN
    assert out == [m1, m2]
    assert mf.add.return_value.name == "t_mirror_bodies"


def test_mirror_bodies_empty_input_raises_before_add() -> None:
    comp: Any = MagicMock()
    with pytest.raises(ValueError, match="no bodies"):
        mirror_bodies(comp, [], MagicMock(), label="t_mirror_bodies")
    comp.features.mirrorFeatures.add.assert_not_called()


def test_mirror_bodies_bad_label_raises_before_create() -> None:
    comp: Any = MagicMock()
    with pytest.raises(ValueError, match="snake_case"):
        mirror_bodies(comp, [MagicMock()], MagicMock(), label="Bad-Label")
    comp.features.mirrorFeatures.createInput.assert_not_called()


def test_mirror_bodies_no_result_raises_and_keeps_label() -> None:
    comp: Any = MagicMock()
    mf = comp.features.mirrorFeatures
    mf.add.return_value.bodies = []
    with pytest.raises(RuntimeError, match="produced no body"):
        mirror_bodies(comp, [MagicMock()], MagicMock(), label="t_mirror_bodies")
    assert mf.add.return_value.name == "t_mirror_bodies"


# ---------------------------------------------------------------------------
# cut detection (pure) + hint wrapping
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("ops", "expected"),
    [
        ([], False),
        ([enums.OP_NEW, enums.OP_JOIN], False),
        ([enums.OP_NEW, enums.OP_CUT], True),
        ([enums.OP_CUT], True),
    ],
)
def test_has_cut_pure(ops: list[object], expected: bool) -> None:
    assert features._has_cut(ops, enums.OP_CUT) is expected


def test_mirror_cut_failure_raises_hint_chained_from_original(fake_extrude_class: type) -> None:
    comp: Any = MagicMock()
    original = RuntimeError("ASM_PATTERN_FEATURES_NO_PASTE_INT_EDGES")
    comp.features.mirrorFeatures.add.side_effect = original
    with pytest.raises(RuntimeError, match="coplanar faces") as info:
        mirror(comp, [fake_extrude_class(enums.OP_CUT)], MagicMock(), label="t_mirror")
    assert info.value.__cause__ is original


def test_mirror_non_cut_failure_propagates_unchanged(fake_extrude_class: type) -> None:
    comp: Any = MagicMock()
    err = RuntimeError("boom")
    comp.features.mirrorFeatures.add.side_effect = err
    with pytest.raises(RuntimeError) as info:
        mirror(comp, [fake_extrude_class(enums.OP_JOIN), MagicMock()], MagicMock(), label="t_mirror")
    assert info.value is err


def test_pattern_rectangular_cut_failure_raises_hint(fake_extrude_class: type) -> None:
    comp: Any = MagicMock()
    comp.parentDesign.unitsManager.evaluateExpression.return_value = 4.0
    comp.features.rectangularPatternFeatures.add.side_effect = RuntimeError("ASM_PATTERN")
    with pytest.raises(RuntimeError, match="coplanar faces"):
        pattern_rectangular(comp, [fake_extrude_class(enums.OP_CUT)], MagicMock(), "4", "10 mm", label="t_pat")


def test_pattern_circular_cut_failure_raises_hint(fake_extrude_class: type) -> None:
    comp: Any = MagicMock()
    comp.parentDesign.unitsManager.evaluateExpression.return_value = 6.0
    comp.features.circularPatternFeatures.add.side_effect = RuntimeError("ASM_PATTERN")
    with pytest.raises(RuntimeError, match="coplanar faces"):
        pattern_circular(comp, [fake_extrude_class(enums.OP_CUT)], MagicMock(), "6", label="t_cpat")
