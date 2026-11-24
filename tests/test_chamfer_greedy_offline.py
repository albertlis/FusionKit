"""Offline tests for fusion_kit.features.chamfer_greedy and _split_probe (fake edges, chamfer stubbed, no Fusion)."""

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit import features
from fusion_kit import features_edges

COMP: Any = MagicMock(name="comp")


class _Feat:
    def __init__(self, label: str) -> None:
        self.label = label

    def deleteMe(self) -> bool:
        return True


class _FakeChamfer:
    """Stands in for features.chamfer. Records (label, edge names). Probe of an edge in refuse raises RuntimeError."""

    def __init__(self, refuse: Iterable[str] = (), final_raises: bool = False) -> None:
        self.refuse = set(refuse)
        self.final_raises = final_raises
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(
        self,
        comp: Any,
        edges: list[Any],
        distance: Any,
        tangent_chain: bool = False,
        *,
        label: str,
        distance2: Any = None,
    ) -> _Feat:
        names = [e.name for e in edges]
        self.calls.append((label, names))
        if label.endswith("_probe"):
            if names[0] in self.refuse:
                raise RuntimeError("ASM_BL_CAP_COMPLEX")
        elif self.final_raises:
            raise RuntimeError("final chamfer failed")
        return _Feat(label)

    @property
    def finals(self) -> list[list[str]]:
        return [names for lbl, names in self.calls if not lbl.endswith("_probe")]


def _install(monkeypatch: pytest.MonkeyPatch, refuse: Iterable[str] = (), final_raises: bool = False) -> _FakeChamfer:
    fake = _FakeChamfer(refuse, final_raises)
    monkeypatch.setattr(features_edges, "chamfer", fake)
    return fake


def _edge(name: str, length_mm: float, x_mm: float = 0.0, y_mm: float = 0.0) -> Any:
    """Fake BRepEdge: Fusion-internal units (cm) for length and pointOnEdge."""
    return SimpleNamespace(
        name=name,
        length=length_mm / 10.0,
        pointOnEdge=SimpleNamespace(x=x_mm / 10.0, y=y_mm / 10.0),
    )


def test_sliver_edges_skipped_unprobed(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fake = _install(monkeypatch)
    feat: Any = features.chamfer_greedy(
        COMP, [_edge("a", 0.1), _edge("b", 0.5), _edge("c", 0.3)], "size", label="t_chamfer", min_len_mm=0.2
    )
    assert [names[0] for lbl, names in fake.calls if lbl.endswith("_probe")] == ["b", "c"]
    assert fake.finals == [["b", "c"]]
    assert feat.label == "t_chamfer"
    assert "t_chamfer: accepted 2, refused 0, skipped < 0.2 mm 1" in capsys.readouterr().out


def test_refused_probe_dropped_not_raised(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fake = _install(monkeypatch, refuse={"c"})
    feat: Any = features.chamfer_greedy(
        COMP,
        [_edge("b", 0.5), _edge("c", 0.5, x_mm=3.0, y_mm=4.0), _edge("d", 0.5)],
        "size",
        label="t_chamfer",
        min_len_mm=0.2,
    )
    assert fake.finals == [["b", "d"]]
    assert feat.label == "t_chamfer"
    out = capsys.readouterr().out
    assert "t_chamfer: accepted 2, refused 1, skipped < 0.2 mm 0" in out
    assert "t_chamfer: refused at (3.0, 4.0) mm" in out


def test_all_refused_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, refuse={"b", "c"})
    result = features.chamfer_greedy(
        COMP, [_edge("b", 0.5), _edge("c", 0.5)], "size", label="t_chamfer", min_len_mm=0.2
    )
    assert result is None
    assert fake.finals == []
    assert [lbl for lbl, _ in fake.calls] == ["t_chamfer_probe", "t_chamfer_probe"]


def test_all_slivers_returns_none_without_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch)
    assert features.chamfer_greedy(COMP, [_edge("a", 0.1)], "size", label="t_chamfer", min_len_mm=0.2) is None
    assert fake.calls == []


def test_final_chamfer_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, final_raises=True)
    with pytest.raises(RuntimeError, match="final chamfer failed"):
        features.chamfer_greedy(COMP, [_edge("b", 0.5)], "size", label="t_chamfer", min_len_mm=0.2)


def test_split_probe_partitions_and_probes_only_long_edges() -> None:
    probed: list[str] = []

    def probe(e: Any) -> bool:
        probed.append(e.name)
        return e.name != "x"

    acc, ref, short = features._split_probe(
        [_edge("s", 0.1), _edge("x", 1.0), _edge("y", 1.0)], 0.02, lambda e: e.length, probe
    )
    assert [e.name for e in acc] == ["y"]
    assert [e.name for e in ref] == ["x"]
    assert [e.name for e in short] == ["s"]
    assert probed == ["x", "y"]
