"""Offline tests: grip-fin exemption (dfam vertical_corner / bed_chamfer skip for fin side edges).

Pure core only (no adsk). Fin side faces carry attribute ("dfam", "grip_fin"); the adapter sets
EdgeFact.on_grip_fin when any adjacent face is tagged.
"""

from dataclasses import replace
from typing import Any

import pytest

from fusion_kit.dfam import EdgeFact, check_edges


def _edge(**kw: Any) -> EdgeFact:
    base = EdgeFact(label="fin", kind="vertical", treatment="sharp", size_mm=0.0, room_mm=5.0)
    return replace(base, **kw)


@pytest.mark.parametrize(
    ("kind", "treatment", "size"),
    [
        ("vertical", "sharp", 0.0),
        ("vertical", "fillet", 1.0),
        ("bed", "sharp", 0.0),
        ("bed", "fillet", 1.0),
        ("bed", "chamfer", 0.3),
    ],
)
def test_grip_fin_edge_exempt(kind: str, treatment: str, size: float) -> None:
    out = check_edges([_edge(kind=kind, treatment=treatment, size_mm=size, on_grip_fin=True)])
    assert out == []


@pytest.mark.parametrize(
    ("kind", "treatment", "size", "rule"),
    [
        ("vertical", "sharp", 0.0, "vertical_corner"),
        ("bed", "sharp", 0.0, "bed_chamfer"),
    ],
)
def test_untagged_edge_still_fails(kind: str, treatment: str, size: float, rule: str) -> None:
    out = check_edges([_edge(kind=kind, treatment=treatment, size_mm=size, on_grip_fin=False)])
    assert [f.rule for f in out] == [rule]
    assert out[0].level == "FAIL"


def test_grip_fin_does_not_exempt_mating() -> None:
    out = check_edges([_edge(kind="mating", treatment="sharp", size_mm=0.0, on_grip_fin=True)])
    assert [f.rule for f in out] == ["joint_chamfer"]


def test_grip_fin_default_is_not_exempt() -> None:
    out = check_edges([_edge(kind="vertical", treatment="sharp", size_mm=0.0)])
    assert [f.rule for f in out] == ["vertical_corner"]
