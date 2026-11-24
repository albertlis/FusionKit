"""component.active_design(): raises RuntimeError when no design is active, never returns None.

Offline: fake adsk Application and Design.cast patched onto the component module.
"""

from types import SimpleNamespace

import pytest

from fusion_kit import component


class _Design:
    """Stand-in for adsk.fusion.Design. cast() keeps only designs, as Fusion does for other products."""


def _fake_adsk(active_product: object) -> SimpleNamespace:
    def cast(obj: object) -> _Design | None:
        return obj if isinstance(obj, _Design) else None

    app = SimpleNamespace(activeProduct=active_product)
    return SimpleNamespace(
        core=SimpleNamespace(Application=SimpleNamespace(get=lambda: app)),
        fusion=SimpleNamespace(Design=SimpleNamespace(cast=cast)),
    )


def test_active_design_raises_when_no_product(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(component, "adsk", _fake_adsk(None))
    with pytest.raises(RuntimeError, match="no active Fusion design"):
        component.active_design()


def test_active_design_raises_when_product_not_design(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(component, "adsk", _fake_adsk(object()))
    with pytest.raises(RuntimeError, match="no active Fusion design"):
        component.active_design()


def test_active_design_returns_design(monkeypatch: pytest.MonkeyPatch) -> None:
    design = _Design()
    monkeypatch.setattr(component, "adsk", _fake_adsk(design))
    assert component.active_design() is design
