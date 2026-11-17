"""Offline test: sketch.mm() must not return a stale cached .value of a derived user parameter.

Fake units manager mimics Fusion: evaluateExpression resolves user-parameter names through the
CACHED .value, which is not recomputed for derived params until Fusion rebuilds. This is the live
failure: ensure_params changed roller_flange_end 1.5 -> 3.5, derived wall_inner_x was still 44.5.
"""

import re
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from fusion_kit import sketch

_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|cm)?|([A-Za-z_]\w*)")


class StaleParam:
    def __init__(self, name: str, unit: str, expression: str, cached_cm: float) -> None:
        self.name = name
        self.unit = unit
        self.expression = expression  # what Fusion stores; set by ensure_params
        self.value = cached_cm  # internal cm; stays stale for derived params


class _UserParams:
    def __init__(self, params: dict[str, StaleParam]) -> None:
        self._params = params

    def itemByName(self, name: str) -> StaleParam | None:
        return self._params.get(name)


class _UnitsManager:
    """Numbers: literal, unit suffix or the units arg. Names: cached .value. Returns internal cm."""

    def __init__(self, params: dict[str, StaleParam]) -> None:
        self._params = params

    def evaluateExpression(self, expression: str, units: str) -> float:
        def tok(m: re.Match[str]) -> str:
            if m.group(3) is not None:
                return repr(self._params[m.group(3)].value)
            factor = 0.1 if m.group(2) == "mm" else 1.0  # bare number = dimensionless
            return repr(float(m.group(1)) * factor)

        return float(eval(_TOKEN.sub(tok, expression), {"__builtins__": {}}, {}))


class StaleDesign:
    def __init__(self) -> None:
        self.params: dict[str, StaleParam] = {}
        self.userParameters = _UserParams(self.params)
        self.unitsManager = _UnitsManager(self.params)


@pytest.fixture
def fusion_design(monkeypatch: pytest.MonkeyPatch) -> Iterator[StaleDesign]:
    design = StaleDesign()
    fake_adsk = SimpleNamespace(
        core=SimpleNamespace(Application=SimpleNamespace(get=lambda: SimpleNamespace(activeProduct=design))),
        fusion=SimpleNamespace(Design=SimpleNamespace(cast=lambda obj: obj)),
    )
    monkeypatch.setattr(sketch, "adsk", fake_adsk)
    yield design


def _state_before_change(design: StaleDesign) -> None:
    # cached values are internal cm (mm / 10)
    design.params["roller_flange_end"] = StaleParam("roller_flange_end", "mm", "1.5 mm", 0.15)
    design.params["wall_inner_x"] = StaleParam("wall_inner_x", "mm", "43 mm + roller_flange_end", 4.45)
    design.params["wall_thickness"] = StaleParam("wall_thickness", "mm", "7 mm", 0.7)
    design.params["wall_outer_x"] = StaleParam("wall_outer_x", "mm", "wall_inner_x + wall_thickness", 5.15)


def _upstream_change(design: StaleDesign) -> None:
    """ensure_params: leaf expression set -> leaf value recomputed; derived .value left stale."""
    leaf = design.params["roller_flange_end"]
    leaf.expression = "3.5 mm"
    leaf.value = 0.35


def test_mm_derived_fresh_after_upstream_change(fusion_design: StaleDesign) -> None:
    _state_before_change(fusion_design)
    _upstream_change(fusion_design)
    # premise: Fusion's own evaluation of the derived name reads the stale cache (48 mm)
    assert fusion_design.unitsManager.evaluateExpression(
        "wall_inner_x + wall_thickness / 2", "mm"
    ) * 10 == pytest.approx(48.0)
    # fresh: 46.5 + 3.5 = 50
    assert sketch.mm("wall_inner_x + wall_thickness / 2") == pytest.approx(50.0)


def test_mm_two_level_derived_fresh(fusion_design: StaleDesign) -> None:
    _state_before_change(fusion_design)
    _upstream_change(fusion_design)
    # wall_outer_x -> wall_inner_x -> roller_flange_end; fresh 46.5 + 7 = 53.5 (cache says 51.5)
    assert sketch.mm("wall_outer_x") == pytest.approx(53.5)


def test_mm_literals_and_units_unchanged(fusion_design: StaleDesign) -> None:
    fusion_design.params["pitch"] = StaleParam("pitch", "cm", "2 cm", 2.0)
    assert sketch.mm("2.5 mm + 1 cm") == pytest.approx(12.5)
    assert sketch.mm("pitch * 2") == pytest.approx(40.0)
