"""Offline tests: dfam.format_skip / set_skip (dfam_skip attribute writer). No Fusion runtime; attributes faked."""

from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit.dfam import SKIPPABLE_RULES, format_skip, parse_skip, set_skip, skip_from_body


class _Attrs:
    """Fake Attributes: add() upserts like Fusion (same group+name -> value replaced)."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], Any] = {}
        self.add_calls: list[tuple[str, str, str]] = []

    def add(self, group: str, name: str, value: str) -> Any:
        self.add_calls.append((group, name, value))
        attr = self.items.get((group, name))
        if attr is None:
            attr = SimpleNamespace(value=value)
            self.items[(group, name)] = attr
        else:
            attr.value = value
        return attr

    def itemByName(self, group: str, name: str) -> Any:
        return self.items.get((group, name))


def _body() -> SimpleNamespace:
    return SimpleNamespace(attributes=_Attrs())


GOOD = {
    "vertical_corner": "louver lattice: slats/ribs 0.75-1.6 mm, fillet r3 impossible, outer corners chamfered",
    "bed_chamfer": "inner reflector plates 1.6 mm stand on bed, 1 mm chamfer does not fit",
}


def test_format_skip_round_trip() -> None:
    value = format_skip(GOOD)
    assert value == (
        "vertical_corner:louver lattice: slats/ribs 0.75-1.6 mm, fillet r3 impossible, outer corners chamfered; "
        "bed_chamfer:inner reflector plates 1.6 mm stand on bed, 1 mm chamfer does not fit"
    )
    assert parse_skip(value) == GOOD


def test_format_skip_empty_and_every_known_rule() -> None:
    assert format_skip({}) == ""
    assert parse_skip("") == {}
    for rule in sorted(SKIPPABLE_RULES):
        assert parse_skip(format_skip({rule: "x"})) == {rule: "x"}


def test_format_skip_unknown_rule_rejected() -> None:
    with pytest.raises(ValueError, match="unknown rule 'bed_chamfr'"):
        format_skip({"bed_chamfr": "typo"})


def test_format_skip_semicolon_in_reason_rejected() -> None:
    with pytest.raises(ValueError, match="may not contain ';'"):
        format_skip({"cavity": "escape hole; printed"})


@pytest.mark.parametrize("reason", ["", "   ", " padded", "padded "])
def test_format_skip_bad_reason_rejected(reason: str) -> None:
    with pytest.raises(ValueError, match="reason"):
        format_skip({"cavity": reason})


def test_format_skip_colon_in_reason_round_trips() -> None:
    assert parse_skip(format_skip({"cavity": "why: because"})) == {"cavity": "why: because"}


def test_set_skip_writes_attribute_and_upserts() -> None:
    body = _body()
    value = set_skip(body, {"cavity": "escape hole printed"})
    assert value == "cavity:escape hole printed"
    assert body.attributes.add_calls == [("dfam", "dfam_skip", value)]
    assert skip_from_body(body) == {"cavity": "escape hole printed"}
    set_skip(body, {"cavity": "other reason", "min_wall": "thin label"})
    assert skip_from_body(body) == {"cavity": "other reason", "min_wall": "thin label"}
    assert len(body.attributes.items) == 1


def test_set_skip_invalid_leaves_body_untouched() -> None:
    body = _body()
    with pytest.raises(ValueError):
        set_skip(body, {"not_a_rule": "x"})
    with pytest.raises(ValueError):
        set_skip(body, {"cavity": "a;b"})
    assert body.attributes.add_calls == []
    assert body.attributes.items == {}
