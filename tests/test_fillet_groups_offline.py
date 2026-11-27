"""Offline tests: features_edges.fillet_groups / chamfer_groups — one feature, one edge set per group.

adsk stubbed by tests/conftest.py (MagicMock); comp is a MagicMock, _vi is replaced so radii stay readable.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit import features_edges as fe


@pytest.fixture
def vi_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fe, "_vi", lambda value: ("vi", value))


def _comp() -> Any:
    return MagicMock(name="comp")


def test_fillet_groups_one_feature_one_edge_set_per_group(vi_plain: None) -> None:
    comp = _comp()
    e1, e2, e3 = object(), object(), object()
    fe.fillet_groups(comp, [([e1, e2], "1 mm"), ([e3], "2 mm")], label="corner_fillets")
    fi = comp.features.filletFeatures.createInput.return_value
    add_set = fi.edgeSetInputs.addConstantRadiusEdgeSet
    assert add_set.call_count == 2
    assert [c.args[1] for c in add_set.call_args_list] == [("vi", "1 mm"), ("vi", "2 mm")]
    assert [c.args[2] for c in add_set.call_args_list] == [False, False]
    comp.features.filletFeatures.add.assert_called_once_with(fi)


def test_fillet_groups_tangent_chain_applies_to_every_group(vi_plain: None) -> None:
    comp = _comp()
    fe.fillet_groups(comp, [([object()], "1 mm"), ([object()], "1 mm")], tangent_chain=True, label="tc_fillets")
    add_set = comp.features.filletFeatures.createInput.return_value.edgeSetInputs.addConstantRadiusEdgeSet
    assert [c.args[2] for c in add_set.call_args_list] == [True, True]


def test_fillet_groups_label_set_on_feature(vi_plain: None) -> None:
    comp = _comp()
    feat = fe.fillet_groups(comp, [([object()], "1 mm")], label="corner_fillets")
    assert feat.name == "corner_fillets"


def test_fillet_groups_rejects_empty_and_bad_label_before_add(vi_plain: None) -> None:
    comp = _comp()
    with pytest.raises(ValueError):
        fe.fillet_groups(comp, [], label="x_fillet")
    with pytest.raises(ValueError):
        fe.fillet_groups(comp, [([], "1 mm")], label="x_fillet")
    with pytest.raises(ValueError):
        fe.fillet_groups(comp, [([object()], "1 mm")], label="Bad Label")
    comp.features.filletFeatures.add.assert_not_called()


def test_chamfer_groups_one_feature_one_edge_set_per_group(vi_plain: None) -> None:
    comp = _comp()
    e1, e2 = object(), object()
    feat = fe.chamfer_groups(comp, [([e1], "0.5 mm"), ([e2], "1 mm")], label="edge_chamfers")
    ci = comp.features.chamferFeatures.createInput2.return_value
    add_set = ci.chamferEdgeSets.addEqualDistanceChamferEdgeSet
    assert [c.args[1] for c in add_set.call_args_list] == [("vi", "0.5 mm"), ("vi", "1 mm")]
    assert [c.args[2] for c in add_set.call_args_list] == [False, False]
    comp.features.chamferFeatures.add.assert_called_once_with(ci)
    assert feat.name == "edge_chamfers"


def test_chamfer_groups_rejects_empty_and_bad_label_before_add(vi_plain: None) -> None:
    comp = _comp()
    with pytest.raises(ValueError):
        fe.chamfer_groups(comp, [([], "1 mm")], label="x_chamfer")
    with pytest.raises(ValueError):
        fe.chamfer_groups(comp, [([object()], "1 mm")], label="Bad Label")
    comp.features.chamferFeatures.add.assert_not_called()
