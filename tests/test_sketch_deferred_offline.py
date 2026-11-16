"""Offline tests for fusion_kit.sketch.deferred: flag True inside the block, False after normal exit and after an
exception; yields the same sketch object.

No Fusion runtime: adsk stubs come from conftest.py. The sketch is a plain fake with an isComputeDeferred attribute.
"""

from types import SimpleNamespace

import pytest

from fusion_kit.sketch import deferred


def _sketch() -> SimpleNamespace:
    return SimpleNamespace(isComputeDeferred=False)


def test_true_inside_yields_same_object() -> None:
    s = _sketch()
    with deferred(s) as got:
        assert got is s
        assert s.isComputeDeferred is True
    assert s.isComputeDeferred is False


def test_false_after_exception_and_exception_propagates() -> None:
    s = _sketch()
    with pytest.raises(ValueError, match="boom"), deferred(s):
        assert s.isComputeDeferred is True
        raise ValueError("boom")
    assert s.isComputeDeferred is False
