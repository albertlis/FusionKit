"""Offline test: features_warp._resolve must read derived user params fresh (eval_fresh), not the cached .value."""

import re
from types import SimpleNamespace

import pytest

from fusion_kit import features_warp

_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|cm)?|([A-Za-z_]\w*)")


class _Param:
    def __init__(self, name: str, unit: str, expression: str, cached_cm: float) -> None:
        self.name = name
        self.unit = unit
        self.expression = expression  # what Fusion stores
        self.value = cached_cm  # internal cm; stays stale for derived params


class _Design:
    """Fake design: evaluateExpression resolves names through the CACHED .value (like Fusion before rebuild)."""

    def __init__(self, params: list[_Param]) -> None:
        self._params = {p.name: p for p in params}
        self.userParameters = self
        self.unitsManager = self

    @property
    def count(self) -> int:
        return len(self._params)

    def item(self, i: int) -> _Param:
        return list(self._params.values())[i]

    def itemByName(self, name: str) -> _Param | None:
        return self._params.get(name)

    def evaluateExpression(self, expression: str, units: str) -> float:
        def tok(m: re.Match[str]) -> str:
            if m.group(3) is not None:
                return repr(self._params[m.group(3)].value)
            factor = 0.1 if m.group(2) == "mm" else 1.0  # bare number = dimensionless
            return repr(float(m.group(1)) * factor)

        return float(eval(_TOKEN.sub(tok, expression), {"__builtins__": {}}, {}))


def test_resolve_derived_param_fresh_after_upstream_change() -> None:
    # ensure_params: roller_flange_end 1.5 -> 3.5 mm; derived wall_inner_x cache still 44.5 mm (4.45 cm)
    design = _Design(
        [
            _Param("roller_flange_end", "mm", "3.5 mm", 0.35),
            _Param("wall_inner_x", "mm", "43 mm + roller_flange_end", 4.45),
        ]
    )
    comp = SimpleNamespace(parentDesign=design)
    assert features_warp._resolve(comp, "wall_inner_x", None, 0.0) == pytest.approx(46.5)  # fresh 43 + 3.5
