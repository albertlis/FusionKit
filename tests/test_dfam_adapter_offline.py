"""Offline tests: fusion_kit.dfam adsk adapter (facts_from_body, dfam_check, skip, legacy) and report() dfam output.

No Fusion runtime: adsk stubs from conftest.py. Bodies/faces/edges are SimpleNamespace fakes; cast() answers
by fake .tag like Fusion's cast (None on mismatch). features helpers needing real geometry are monkeypatched.
"""

import sys
from collections import Counter
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit import dfam_body, features
from fusion_kit import report as rep_mod
from fusion_kit.dfam import (
    BodyFact,
    Finding,
    dfam_check,
    facts_from_body,
    parse_skip,
    skip_from_body,
)

_CORE: Any = sys.modules["adsk.core"]
_FUS: Any = sys.modules["adsk.fusion"]


def _v(x: float, y: float, z: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=z)


class _Attrs:
    """Fake attributes collection: itemByName(group, name) -> attr or None."""

    def __init__(self, items: dict[tuple[str, str], Any] | None = None) -> None:
        self._items = items or {}

    def itemByName(self, group: str, name: str) -> Any:
        return self._items.get((group, name))


@pytest.fixture
def casts() -> Iterator[None]:
    """Plane / Cylinder / Line3D.cast answer by fake .tag (None on mismatch), like Fusion."""
    for cls, tag in (("Plane", "plane"), ("Cylinder", "cyl"), ("Line3D", "line")):
        getattr(_CORE, cls).cast.side_effect = lambda g, _t=tag: g if getattr(g, "tag", "") == _t else None
    yield
    for cls in ("Plane", "Cylinder", "Line3D"):
        getattr(_CORE, cls).cast.side_effect = None


@pytest.fixture
def box_features(monkeypatch: pytest.MonkeyPatch) -> None:
    """planar_faces -> faces named 'bed'; convex_edges -> all given; face_normal -> fake .normal."""
    monkeypatch.setattr(features, "face_normal", lambda face, point=None: face.normal)
    monkeypatch.setattr(
        features,
        "planar_faces",
        lambda body, axis, at_mm, normal_sign=None, tol_mm=0.01: [f for f in body.faces if f.name == "bed"],
    )
    monkeypatch.setattr(features, "convex_edges", lambda body, edges, probe_mm=0.1: list(edges))


def _face(
    name: str,
    normal: tuple[float, float, float],
    lo: tuple[float, float, float],
    hi: tuple[float, float, float],
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        tempId=f"f:{name}",
        geometry=SimpleNamespace(tag="plane"),
        normal=_v(*normal),
        boundingBox=SimpleNamespace(minPoint=_v(*lo), maxPoint=_v(*hi)),
        pointOnFace=_v(*lo),
        area=1.0,
        edges=[],
        loops=[],
        attributes=_Attrs(),
    )


def _edge(
    token: str,
    fa: SimpleNamespace,
    fb: SimpleNamespace,
    p: tuple[float, float, float],
    q: tuple[float, float, float],
) -> SimpleNamespace:
    mid = tuple((a + b) / 2 for a, b in zip(p, q))
    e = SimpleNamespace(
        tempId=token,
        geometry=SimpleNamespace(tag="line"),
        faces=[fa, fb],
        startVertex=SimpleNamespace(geometry=_v(*p)),
        endVertex=SimpleNamespace(geometry=_v(*q)),
        pointOnEdge=_v(*mid),
        length=sum((b - a) ** 2 for a, b in zip(p, q)) ** 0.5,
    )
    fa.edges.append(e)
    fb.edges.append(e)
    return e


def _box() -> MagicMock:
    """2 x 2 x 1 cm box = 20 x 20 x 10 mm, plain (no holes, no chamfers). Bed = face named 'bed'."""
    x0, x1, y0, y1, z0, z1 = 0.0, 2.0, 0.0, 2.0, 0.0, 1.0
    bed = _face("bed", (0, 0, -1), (x0, y0, z0), (x1, y1, z0))
    top = _face("top", (0, 0, 1), (x0, y0, z1), (x1, y1, z1))
    sides = {
        "x0": _face("x0", (-1, 0, 0), (x0, y0, z0), (x0, y1, z1)),
        "x1": _face("x1", (1, 0, 0), (x1, y0, z0), (x1, y1, z1)),
        "y0": _face("y0", (0, -1, 0), (x0, y0, z0), (x1, y0, z1)),
        "y1": _face("y1", (0, 1, 0), (x0, y1, z0), (x1, y1, z1)),
    }
    verticals = [
        ("x0", "y0", x0, y0),
        ("x1", "y0", x1, y0),
        ("x1", "y1", x1, y1),
        ("x0", "y1", x0, y1),
    ]
    vert_edges = [_edge(f"v:{sa}{sb}", sides[sa], sides[sb], (x, y, z0), (x, y, z1)) for sa, sb, x, y in verticals]
    side_pts = {
        "x0": ((x0, y0), (x0, y1)),
        "x1": ((x1, y0), (x1, y1)),
        "y0": ((x0, y0), (x1, y0)),
        "y1": ((x0, y1), (x1, y1)),
    }
    bottom = [_edge(f"b:{s}", bed, sides[s], (*a, z0), (*b, z0)) for s, (a, b) in side_pts.items()]
    top_edges = [_edge(f"t:{s}", top, sides[s], (*a, z1), (*b, z1)) for s, (a, b) in side_pts.items()]
    bed.loops = [SimpleNamespace(isOuter=True, edges=bottom)]
    body = MagicMock(
        boundingBox=SimpleNamespace(minPoint=_v(x0, y0, z0), maxPoint=_v(x1, y1, z1)),
        faces=[bed, top, *sides.values()],
        edges=vert_edges + bottom + top_edges,
        shells=SimpleNamespace(count=1),
        attributes=_Attrs(),
    )
    body.name = "box"  # MagicMock(name=...) names the mock repr, not the .name attribute
    return body


def test_facts_box_no_holes(casts: None, box_features: None) -> None:
    body_fact, holes, edges = facts_from_body(_box())
    assert body_fact == BodyFact(
        name="box",
        bed_max_side_mm=20.0,
        has_warp_grooves=False,
        height_mm=10.0,
        footprint_min_mm=20.0,
        min_wall_mm=None,
        vertical_wall_mm=[],
        has_cavity=False,
    )
    assert holes == []
    assert Counter((e.kind, e.treatment, e.size_mm, e.room_mm) for e in edges) == Counter(
        {("vertical", "sharp", 0.0, 10.0): 4, ("bed", "sharp", 0.0, 10.0): 4}
    )


def test_dfam_check_box_fails_and_fail_as_warn_downgrade(casts: None, box_features: None) -> None:
    found = dfam_check(_box())
    assert Counter((f.rule, f.level) for f in found) == Counter(
        {("vertical_corner", "FAIL"): 4, ("bed_chamfer", "FAIL"): 4}
    )
    downgraded = dfam_check(_box(), fail_as_warn=True)
    assert Counter((f.rule, f.level) for f in downgraded) == Counter(
        {("vertical_corner", "WARN"): 4, ("bed_chamfer", "WARN"): 4}
    )


def test_dfam_check_skip_attribute_drops_rule(casts: None, box_features: None) -> None:
    body = _box()
    body.attributes = _Attrs({("dfam", "dfam_skip"): SimpleNamespace(value="vertical_corner:sharp corners by design")})
    found = dfam_check(body)
    assert Counter((f.rule, f.level) for f in found) == Counter({("bed_chamfer", "FAIL"): 4, ("dfam_skip", "WARN"): 1})
    assert [f.msg for f in found if f.rule == "dfam_skip"] == ["vertical_corner skipped: sharp corners by design"]


def test_parse_skip_entries() -> None:
    assert parse_skip("a:x; b:y") == {"a": "x", "b": "y"}
    assert parse_skip("") == {}
    assert parse_skip("a:why: because") == {"a": "why: because"}


@pytest.mark.parametrize("bad", ["rule_without_reason", "a:", ":reason only", "a:x;a:y"])
def test_parse_skip_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_skip(bad)


def test_skip_from_body_absent_and_present() -> None:
    body = MagicMock(attributes=_Attrs())
    assert skip_from_body(body) == {}
    body.attributes = _Attrs({("dfam", "dfam_skip"): SimpleNamespace(value="cavity:escape hole printed")})
    assert skip_from_body(body) == {"cavity": "escape hole printed"}


def test_dfam_check_fail_as_warn_downgrades_fail_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bf = BodyFact(
        name="wide",
        bed_max_side_mm=40.0,
        has_warp_grooves=False,
        height_mm=5.0,
        footprint_min_mm=40.0,
        min_wall_mm=None,
        vertical_wall_mm=[],
        has_cavity=False,
    )
    monkeypatch.setattr(dfam_body, "facts_from_body", lambda body, tags=None, bed_loop_centres=None: (bf, [], []))
    body = MagicMock(attributes=_Attrs())
    assert [(f.rule, f.level) for f in dfam_check(body)] == [("warp_grooves", "FAIL")]
    assert [(f.rule, f.level) for f in dfam_check(body, fail_as_warn=True)] == [("warp_grooves", "WARN")]


class _Bodies(list[Any]):
    """Stand-in for comp.bRepBodies: iterable with .count."""

    @property
    def count(self) -> int:
        return len(self)


def _comp(n: int) -> MagicMock:
    bodies = [
        SimpleNamespace(
            name=f"b{i}",
            boundingBox=SimpleNamespace(minPoint=_v(0, 0, 0), maxPoint=_v(2, 2, 1)),
            lumps=SimpleNamespace(count=1),
            physicalProperties=SimpleNamespace(volume=0.4),
        )
        for i in range(n)
    ]
    comp = MagicMock(bRepBodies=_Bodies(bodies), sketches=SimpleNamespace(count=0))
    comp.name = "comp"  # MagicMock(name=...) names the mock repr, not the .name attribute
    return comp


@pytest.fixture
def design_stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Design.cast -> empty timeline; label_stats -> (0, 0). Restored afterwards."""
    design = SimpleNamespace(timeline=SimpleNamespace(count=0, item=lambda i: None))
    _FUS.Design.cast.side_effect = lambda obj: design
    monkeypatch.setattr(rep_mod, "label_stats", lambda d: (0, 0))
    yield
    _FUS.Design.cast.side_effect = None


def test_report_dfam_lines(design_stub: None, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_check(body: Any, fail_as_warn: bool = False) -> list[Finding]:
        seen.append(body.name)
        return [Finding("warp_grooves", "FAIL", "x"), Finding("cavity", "WARN", "y")]

    monkeypatch.setattr(rep_mod, "dfam_check", fake_check)
    text = rep_mod.report(_comp(2))
    head, *rest = text.split("\n")
    assert head.startswith("comp: bodies=2 ")
    assert "timeline_unnamed=0" in head
    assert head.endswith("dfam=2F/2W")
    assert rest == ["DFAM_FAIL warp_grooves: x", "DFAM_WARN cavity: y"] * 2
    assert seen == ["b0", "b1"]


def test_report_dfam_off(design_stub: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_run(body: Any, fail_as_warn: bool = False) -> list[Finding]:
        raise AssertionError("dfam_check called with dfam=False")

    monkeypatch.setattr(rep_mod, "dfam_check", must_not_run)
    text = rep_mod.report(_comp(1), dfam=False)
    assert "bodies=1" in text
    assert "dfam=" not in text
    assert "\n" not in text


def test_report_data_fields_dfam_off(design_stub: None) -> None:
    data = rep_mod.report_data(_comp(2), dfam=False)
    assert data.name == "comp"
    assert data.bodies == 2
    assert data.lumps == 2
    assert data.bbox_mm == pytest.approx((20.0, 20.0, 10.0))
    assert data.vol_cm3 == pytest.approx(0.8)
    assert (data.sketches_constrained, data.sketches_total) == (0, 0)
    assert (data.unhealthy, data.unnamed, data.numbered) == (0, 0, 0)
    assert data.findings is None


def test_report_data_no_bodies_text(design_stub: None) -> None:
    data = rep_mod.report_data(_comp(0), dfam=False)
    assert data.bbox_mm is None
    assert data.vol_cm3 == 0.0
    assert rep_mod.format_report(data) == (
        "comp: bodies=0 lumps=0 bbox=no-bodies vol=0cm3 sketches=0/0 constrained "
        "design_timeline_unhealthy=0 timeline_unnamed=0 timeline_numbered=0 suppressed=0"
    )


def test_report_text_is_format_report_of_data(design_stub: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        rep_mod,
        "dfam_check",
        lambda body, fail_as_warn=False: [Finding("warp_grooves", "FAIL", "x")],
    )
    comp = _comp(1)
    data = rep_mod.report_data(comp)
    assert data.findings is not None
    assert len(data.findings) == 1
    assert rep_mod.report(comp) == rep_mod.format_report(data)
