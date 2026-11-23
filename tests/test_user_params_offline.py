"""Offline tests for fusion_kit.user_params — fake adsk modules + fake design, no Fusion needed."""

import importlib
import re
import sys
import types
from collections.abc import Iterator

import pytest

_UNIT_WORDS = {"mm", "cm", "deg", "rad"}


class FakeParam:
    """Fusion UserParameter stand-in. Setting an expression fails on unknown names, like Fusion."""

    def __init__(
        self,
        owner: "FakeUserParams",
        name: str,
        unit: str,
        expression: str,
        comment: str,
    ) -> None:
        self._owner = owner
        self.name = name
        self.unit = unit
        self.comment = comment
        self.value = 0.0
        self._expression = ""
        self.expression = expression

    @property
    def expression(self) -> str:
        return self._expression

    @expression.setter
    def expression(self, expr: str) -> None:
        for tok in re.findall(r"[A-Za-z_]\w*", expr):
            if tok not in _UNIT_WORDS and tok not in self._owner.names:
                raise NameError(f"unknown parameter {tok!r} in expression {expr!r}")
        self._expression = expr

    def deleteMe(self) -> bool:
        """Fusion: False while another parameter's expression references this one."""
        for other in self._owner.all():
            if other is not self and re.search(rf"\b{re.escape(self.name)}\b", other.expression):
                return False
        self._owner.remove(self.name)
        return True


class FakeUserParams:
    def __init__(self) -> None:
        self._p: dict[str, FakeParam] = {}

    def all(self) -> list[FakeParam]:
        return list(self._p.values())

    def remove(self, name: str) -> None:
        del self._p[name]

    @property
    def names(self) -> set[str]:
        return set(self._p)

    @property
    def count(self) -> int:
        return len(self._p)

    def item(self, i: int) -> FakeParam:
        return list(self._p.values())[i]

    def itemByName(self, name: str) -> FakeParam | None:
        return self._p.get(name)

    def add(self, name: str, value_input: str, unit: str, comment: str) -> FakeParam:
        p = FakeParam(self, name, unit, value_input, comment)
        self._p[name] = p
        return p


class FakeDesign:
    def __init__(self) -> None:
        self.userParameters = FakeUserParams()


def _param(up: FakeUserParams, name: str) -> FakeParam:
    p = up.itemByName(name)
    assert p is not None, name
    return p


@pytest.fixture
def up_mod(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.ModuleType]:
    """Import fusion_kit.user_params with fake adsk.core / adsk.fusion in sys.modules."""
    adsk = types.ModuleType("adsk")
    core = types.ModuleType("adsk.core")
    fusion = types.ModuleType("adsk.fusion")
    core.__dict__["ValueInput"] = types.SimpleNamespace(createByString=lambda s: s)
    fusion.__dict__["Design"] = object
    adsk.__dict__.update(core=core, fusion=fusion)
    monkeypatch.setitem(sys.modules, "adsk", adsk)
    monkeypatch.setitem(sys.modules, "adsk.core", core)
    monkeypatch.setitem(sys.modules, "adsk.fusion", fusion)
    sys.modules.pop("fusion_kit.user_params", None)
    mod = importlib.import_module("fusion_kit.user_params")
    yield mod
    sys.modules.pop("fusion_kit.user_params", None)


def test_forward_reference_resolves(up_mod: types.ModuleType) -> None:
    """wall refers to gap, defined later in spec: must not raise, both expressions set."""
    design = FakeDesign()
    up_mod.ensure_params(
        design,
        {
            "wall": ("2 * gap", "mm", "wall"),
            "gap": ("1.5 mm", "mm", "gap"),
        },
    )
    up = design.userParameters
    assert _param(up, "wall").expression == "2 * gap"
    assert _param(up, "gap").expression == "1.5 mm"


def test_ensure_params_updates_existing(up_mod: types.ModuleType) -> None:
    """Second call with new expression/comment updates in place, no duplicate."""
    design = FakeDesign()
    up_mod.ensure_params(design, {"gap": ("1 mm", "mm", "a")})
    up_mod.ensure_params(design, {"gap": ("2 mm", "mm", "b")})
    up = design.userParameters
    assert up.count == 1
    assert _param(up, "gap").expression == "2 mm"
    assert _param(up, "gap").comment == "b"


def test_ensure_params_overwrite_false_keeps_existing(up_mod: types.ModuleType) -> None:
    """overwrite=False: existing param keeps Fusion-tuned expression/comment; missing param is added."""
    design = FakeDesign()
    design.userParameters.add("gap", "3 mm", "mm", "tuned in Fusion")
    up_mod.ensure_params(
        design,
        {
            "gap": ("1 mm", "mm", "spec"),
            "wall": ("2 * gap", "mm", "new"),
        },
        overwrite=False,
    )
    up = design.userParameters
    assert up.count == 2
    assert _param(up, "gap").expression == "3 mm"
    assert _param(up, "gap").comment == "tuned in Fusion"
    assert _param(up, "wall").expression == "2 * gap"


def test_ensure_params_overwrite_false_skips_unit_check(
    up_mod: types.ModuleType,
) -> None:
    """overwrite=False leaves existing params entirely alone, incl. unit mismatch (no raise)."""
    design = FakeDesign()
    design.userParameters.add("gap", "1 cm", "cm", "")
    up_mod.ensure_params(design, {"gap": ("1 mm", "mm", "")}, overwrite=False)
    assert _param(design.userParameters, "gap").unit == "cm"


def test_ensure_params_unit_change_raises(up_mod: types.ModuleType) -> None:
    """Existing param with different unit -> ValueError (Fusion cannot change units in place)."""
    design = FakeDesign()
    design.userParameters.add("gap", "1 mm", "mm", "")
    with pytest.raises(ValueError, match="cannot change"):
        up_mod.ensure_params(design, {"gap": ("1 cm", "cm", "")})


@pytest.mark.parametrize(
    "unit, stored, expected",
    [
        ("mm", 2.5, 25.0),
        ("cm", 2.5, 25.0),
        ("deg", 0.5, 0.5),
        ("rad", 0.5, 0.5),
        ("in", 2.5, 25.0),
        ("ft", 2.5, 25.0),
        ("m", 2.5, 25.0),
        ("", 3.0, 3.0),
    ],
)
def test_snapshot_converts_by_unit(up_mod: types.ModuleType, unit: str, stored: float, expected: float) -> None:
    """Length (mm, cm) -> mm via *10; angles and unitless stay as stored."""
    design = FakeDesign()
    p = design.userParameters.add("x", "0", unit, "")
    p.value = stored
    g = up_mod.snapshot_geo(design)
    assert g.x == pytest.approx(expected)


def test_prune_deletes_stale_keeps_spec(up_mod: types.ModuleType) -> None:
    """prune=True: param not in spec deleted; spec params (old and new) kept."""
    design = FakeDesign()
    design.userParameters.add("gap", "1 mm", "mm", "")
    design.userParameters.add("pin_offset", "2 mm", "mm", "")
    up_mod.ensure_params(design, {"gap": ("1 mm", "mm", "")}, prune=True)
    assert design.userParameters.names == {"gap"}


def test_prune_default_false_deletes_nothing(up_mod: types.ModuleType) -> None:
    """Default prune=False: stale param stays."""
    design = FakeDesign()
    design.userParameters.add("pin_offset", "2 mm", "mm", "")
    up_mod.ensure_params(design, {"gap": ("1 mm", "mm", "")})
    assert design.userParameters.names == {"gap", "pin_offset"}


def test_prune_dependents_first(up_mod: types.ModuleType) -> None:
    """wall refers to gap; both stale. gap refused on pass 1, wall deleted, gap on pass 2."""
    design = FakeDesign()
    design.userParameters.add("gap", "1 mm", "mm", "")
    design.userParameters.add("wall", "2 * gap", "mm", "")
    up_mod.ensure_params(design, {"keep": ("1 mm", "mm", "")}, prune=True)
    assert design.userParameters.names == {"keep"}


def test_prune_undeletable_raises_with_name(up_mod: types.ModuleType) -> None:
    """stale gap still referenced by kept wall -> never deletable -> RuntimeError naming gap."""
    design = FakeDesign()
    design.userParameters.add("gap", "1 mm", "mm", "")
    design.userParameters.add("wall", "2 * gap", "mm", "")
    with pytest.raises(RuntimeError, match="gap"):
        up_mod.ensure_params(design, {"wall": ("2 * gap", "mm", "")}, prune=True)


def test_four_item_entry_writes_expression_not_range(up_mod: types.ModuleType) -> None:
    """4th item = live-sweep range only; Fusion gets expression, units, comment from the first three."""
    design = FakeDesign()
    up_mod.ensure_params(design, {"gap": ("1 mm", "mm", "g", {"min": "0.5 mm", "max": "3 mm"})})
    p = _param(design.userParameters, "gap")
    assert p.expression == "1 mm"
    assert p.comment == "g"


@pytest.mark.parametrize(
    "entry",
    [
        ("1 mm", "mm", "", {"foo": "x"}),
        ("1 mm", "mm", "", {"min": "0 mm"}),
        ("1 mm", "mm", "", {"values": ["0"], "min": "0 mm", "max": "2 mm"}),
        ("1 mm", "mm", "", {"values": []}),
        ("1 mm", "mm"),
    ],
)
def test_bad_spec_entry_rejected_before_any_write(up_mod: types.ModuleType, entry: tuple) -> None:
    design = FakeDesign()
    with pytest.raises(ValueError):
        up_mod.ensure_params(design, {"gap": entry})
    assert design.userParameters.count == 0


def test_entry_sweep_and_fields(up_mod: types.ModuleType) -> None:
    assert up_mod.entry_sweep(("1 mm", "mm", "")) is None
    assert up_mod.entry_sweep(("1 mm", "mm", "", {"values": ["0", "1"]})) == {"values": ["0", "1"]}
    assert up_mod.entry_fields(("1 mm", "mm", "c", {"no_geometry": True})) == ("1 mm", "mm", "c")


def test_expr_derive_accepts_four_item_entries() -> None:
    from fusion_kit.expr import derive

    plain = derive({"a": ("2 mm", "mm", "")}, {})
    with_sweep = derive({"a": ("2 mm", "mm", "", {"min": "1 mm", "max": "3 mm"})}, {})
    assert with_sweep == plain
    assert "a" in with_sweep
