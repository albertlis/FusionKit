"""Offline tests for fusion_kit.dfam pure rule core (no Fusion runtime; no adsk used)."""

import math
from dataclasses import replace
from typing import Any

import pytest

from fusion_kit import dfam
from fusion_kit.dfam import ADVISORY, BodyFact, EdgeFact, Finding, HoleFact

_HOLE_BASE = HoleFact(
    label="h",
    axis_vertical=False,
    radius_mm=2.0,
    peak_mm=dfam.teardrop_peak_mm(2.0),
    has_seam_notch=True,
    on_bed=False,
    bed_step_unsupported=False,
    side_screw=False,
    has_relief=False,
)
_EDGE_BASE = EdgeFact(label="e", kind="bed", treatment="chamfer", size_mm=1.0, room_mm=3.1)
_BODY_BASE = BodyFact(
    name="b",
    bed_max_side_mm=20.0,
    has_warp_grooves=False,
    height_mm=10.0,
    footprint_min_mm=10.0,
    min_wall_mm=2.0,
    vertical_wall_mm=[],
    has_cavity=False,
)


def _hole(**kw: Any) -> HoleFact:
    return replace(_HOLE_BASE, **kw)


def _edge(**kw: Any) -> EdgeFact:
    return replace(_EDGE_BASE, **kw)


def _body(**kw: Any) -> BodyFact:
    return replace(_BODY_BASE, **kw)


def _rules(findings: list[Finding]) -> list[str]:
    return [f.rule for f in findings]


# ---------------------------------------------------------------------------
# Finding / teardrop geometry
# ---------------------------------------------------------------------------
def test_finding_line_format() -> None:
    assert Finding("teardrop", "FAIL", "x").line() == "DFAM_FAIL teardrop: x"
    assert Finding("relief_slot", "WARN", "y").line() == "DFAM_WARN relief_slot: y"


@pytest.mark.parametrize(
    ("r", "deg", "expected"),
    [
        (1.0, 60.0, 1.0 / math.sin(math.radians(60.0))),
        (2.0, 60.0, 2.0 / math.sin(math.radians(60.0))),
        (1.0, 45.0, 1.0 / math.sin(math.radians(45.0))),
    ],
)
def test_teardrop_peak_formula(r: float, deg: float, expected: float) -> None:
    assert dfam.teardrop_peak_mm(r, deg) == pytest.approx(expected, rel=1e-12)


def test_teardrop_peak_60deg_factor() -> None:
    assert dfam.teardrop_peak_mm(1.0) == pytest.approx(1.1547005, abs=1e-6)
    assert dfam.teardrop_peak_mm(1.0) == dfam.teardrop_peak_mm(1.0, dfam.TEARDROP_WALL_DEG)


# ---------------------------------------------------------------------------
# check_holes
# ---------------------------------------------------------------------------
def test_horizontal_peak_none_fails_teardrop() -> None:
    out = dfam.check_holes([_hole(peak_mm=None)])
    assert _rules(out) == ["teardrop"]
    assert out[0].level == "FAIL"
    assert "got none" in out[0].msg


def test_horizontal_peak_exact_passes() -> None:
    assert dfam.check_holes([_hole(peak_mm=dfam.teardrop_peak_mm(2.0))]) == []


def test_horizontal_peak_97pct_fails_teardrop() -> None:
    peak = dfam.teardrop_peak_mm(2.0) * 0.97
    assert _rules(dfam.check_holes([_hole(peak_mm=peak)])) == ["teardrop"]


def test_horizontal_peak_98pct_boundary_passes() -> None:
    # rule is strict '<' against req * 0.98: exactly 0.98 * req is not a violation
    peak = dfam.teardrop_peak_mm(2.0) * 0.98
    assert dfam.check_holes([_hole(peak_mm=peak)]) == []


def test_vertical_hole_ignores_peak() -> None:
    assert dfam.check_holes([_hole(axis_vertical=True, peak_mm=None)]) == []


def test_vertical_no_seam_notch_fails() -> None:
    out = dfam.check_holes([_hole(axis_vertical=True, has_seam_notch=False)])
    assert _rules(out) == ["seam_notch"]
    assert out[0].level == "FAIL"


def test_vertical_with_seam_notch_passes() -> None:
    assert dfam.check_holes([_hole(axis_vertical=True, has_seam_notch=True)]) == []


@pytest.mark.parametrize(
    ("radius", "expected"),
    [
        (0.995, ["first_layer"]),  # diameter 1.99 < 2.0
        (1.0, []),  # diameter 2.0 exactly: not below
        (1.5, []),
    ],
)
def test_first_layer_min_diameter_on_bed(radius: float, expected: list[str]) -> None:
    out = dfam.check_holes([_hole(on_bed=True, radius_mm=radius, peak_mm=dfam.teardrop_peak_mm(radius))])
    assert _rules(out) == expected


def test_first_layer_not_checked_off_bed() -> None:
    out = dfam.check_holes([_hole(on_bed=False, radius_mm=0.5, peak_mm=dfam.teardrop_peak_mm(0.5))])
    assert out == []


def test_bed_step_unsupported_fails_counterbore() -> None:
    out = dfam.check_holes([_hole(bed_step_unsupported=True)])
    assert _rules(out) == ["counterbore_down"]
    assert out[0].level == "FAIL"
    assert dfam.check_holes([_hole(bed_step_unsupported=False)]) == []


def test_side_screw_without_relief_warns() -> None:
    out = dfam.check_holes([_hole(side_screw=True, has_relief=False)])
    assert _rules(out) == ["relief_slot"]
    assert out[0].level == "WARN"


def test_side_screw_with_relief_passes() -> None:
    assert dfam.check_holes([_hole(side_screw=True, has_relief=True)]) == []


def test_no_side_screw_no_relief_warning() -> None:
    assert dfam.check_holes([_hole(side_screw=False, has_relief=False)]) == []


# ---------------------------------------------------------------------------
# check_edges
# ---------------------------------------------------------------------------
def test_vertical_sharp_fails() -> None:
    out = dfam.check_edges([_edge(kind="vertical", treatment="sharp", size_mm=0.0)])
    assert _rules(out) == ["vertical_corner"]
    assert out[0].level == "FAIL"


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (2.0, ["vertical_corner"]),
        (2.99, ["vertical_corner"]),
        (3.0, []),
        (3.5, []),
    ],
)
def test_vertical_fillet_min_3mm(size: float, expected: list[str]) -> None:
    out = dfam.check_edges([_edge(kind="vertical", treatment="fillet", size_mm=size)])
    assert _rules(out) == expected


def test_vertical_fillet_small_msg_says_use_chamfer() -> None:
    out = dfam.check_edges([_edge(kind="vertical", treatment="fillet", size_mm=2.99)])
    assert "does nothing, use chamfer" in out[0].msg


def test_vertical_chamfer_not_flagged() -> None:
    # spec: only sharp and fillet<3 are vertical violations
    assert dfam.check_edges([_edge(kind="vertical", treatment="chamfer", size_mm=0.5)]) == []


def test_bed_sharp_fails() -> None:
    out = dfam.check_edges([_edge(kind="bed", treatment="sharp", size_mm=0.0, room_mm=5.0)])
    assert _rules(out) == ["bed_chamfer"]
    assert out[0].level == "FAIL"


def test_bed_fillet_fails_with_chamfer_msg() -> None:
    out = dfam.check_edges([_edge(kind="bed", treatment="fillet", size_mm=1.0, room_mm=5.0)])
    assert _rules(out) == ["bed_chamfer"]
    assert "needs chamfer, not fillet" in out[0].msg


@pytest.mark.parametrize(
    ("size", "room", "fails"),
    [
        (0.39, 3.1, True),  # < required 1.0 (room >= 3)
        (0.39, 2.9, True),  # < required 0.5 (room < 3)
        (0.4, 2.9, True),  # < required 0.5 (room < 3)
        (0.4, 3.1, True),  # required 1.0
        (0.5, 2.9, False),  # small chamfer allowed when room < 3
        (0.5, 3.0, True),  # room exactly 3.0 is not < 3 -> required 1.0
        (0.5, 3.1, True),
        (1.0, 3.1, False),
        (1.0, 2.9, False),
    ],
)
def test_bed_chamfer_size_matrix(size: float, room: float, fails: bool) -> None:
    out = dfam.check_edges([_edge(kind="bed", treatment="chamfer", size_mm=size, room_mm=room)])
    assert _rules(out) == (["bed_chamfer"] if fails else [])


def test_mating_sharp_fails() -> None:
    out = dfam.check_edges([_edge(kind="mating", treatment="sharp", size_mm=0.0)])
    assert _rules(out) == ["joint_chamfer"]
    assert out[0].level == "FAIL"


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0.2, ["joint_chamfer"]),
        (0.29, ["joint_chamfer"]),
        (0.3, []),
        (0.5, []),
    ],
)
def test_mating_chamfer_min_0_3(size: float, expected: list[str]) -> None:
    out = dfam.check_edges([_edge(kind="mating", treatment="chamfer", size_mm=size)])
    assert _rules(out) == expected


def test_mating_fillet_not_flagged_per_spec() -> None:
    # spec lists only sharp and chamfer<0.3 for mating edges
    assert dfam.check_edges([_edge(kind="mating", treatment="fillet", size_mm=0.1)]) == []


# ---------------------------------------------------------------------------
# check_body_facts
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("side", "grooves", "expected"),
    [
        (35.0, False, []),  # exactly 35: not > 35
        (35.01, False, ["warp_grooves"]),
        (35.01, True, []),
        (60.0, True, []),
    ],
)
def test_warp_grooves_side_35(side: float, grooves: bool, expected: list[str]) -> None:
    out = dfam.check_body_facts(_body(bed_max_side_mm=side, has_warp_grooves=grooves))
    assert _rules(out) == expected


@pytest.mark.parametrize(
    ("wall", "expected"),
    [
        (0.99, ["min_wall"]),
        (1.0, []),
        (None, []),
    ],
)
def test_min_wall(wall: float | None, expected: list[str]) -> None:
    out = dfam.check_body_facts(_body(min_wall_mm=wall))
    assert _rules(out) == expected


@pytest.mark.parametrize(
    ("walls", "expected"),
    [
        ([0.9], []),  # boundary: band is exclusive
        ([1.6], []),
        ([0.5, 2.0], []),
        ([1.0], ["petg_wall"]),
        ([0.91], ["petg_wall"]),
        ([1.59], ["petg_wall"]),
        ([1.2, 1.3], ["petg_wall"]),  # one finding per body, not per wall
    ],
)
def test_petg_wall_band(walls: list[float], expected: list[str]) -> None:
    out = dfam.check_body_facts(_body(vertical_wall_mm=walls))
    assert _rules(out) == expected
    if expected:
        assert out[0].level == "FAIL"


@pytest.mark.parametrize(
    ("height", "expected"),
    [
        (40.0, []),  # ratio 4.0 is not > 4
        (40.1, ["slender_fin"]),
    ],
)
def test_slender_fin_ratio(height: float, expected: list[str]) -> None:
    out = dfam.check_body_facts(_body(height_mm=height, footprint_min_mm=10.0))
    assert _rules(out) == expected
    if expected:
        assert out[0].level == "WARN"


def test_footprint_zero_raises() -> None:
    with pytest.raises(ValueError):
        dfam.check_body_facts(_body(footprint_min_mm=0.0))


def test_cavity_warns() -> None:
    out = dfam.check_body_facts(_body(has_cavity=True))
    assert _rules(out) == ["cavity"]
    assert out[0].level == "WARN"


def test_clean_body_no_findings() -> None:
    assert dfam.check_body_facts(_body()) == []


# ---------------------------------------------------------------------------
# check_all / summarize_findings / ADVISORY
# ---------------------------------------------------------------------------
def test_check_all_clean_is_empty() -> None:
    assert dfam.check_all(_body(), [_hole()], [_edge()], {}) == []


def test_check_all_collects_all_groups() -> None:
    out = dfam.check_all(
        _body(has_cavity=True, min_wall_mm=0.5),
        [_hole(peak_mm=None)],
        [_edge(kind="vertical", treatment="sharp", size_mm=0.0)],
        {},
    )
    assert sorted(_rules(out)) == ["cavity", "min_wall", "teardrop", "vertical_corner"]


def test_check_all_skip_drops_rule_and_warns() -> None:
    out = dfam.check_all(_body(), [_hole(peak_mm=None)], [], {"teardrop": "supports ok"})
    assert _rules(out) == ["dfam_skip"]
    assert out[0].level == "WARN"
    assert out[0].msg == "teardrop skipped: supports ok"


def test_check_all_skip_without_match_adds_nothing() -> None:
    assert dfam.check_all(_body(), [_hole()], [_edge()], {"teardrop": "unused"}) == []


def test_check_all_skip_one_warn_per_rule() -> None:
    holes = [_hole(peak_mm=None, label="a"), _hole(peak_mm=None, label="b")]
    out = dfam.check_all(_body(), holes, [], {"teardrop": "r"})
    assert _rules(out) == ["dfam_skip"]


def test_summarize_findings_counts() -> None:
    fs = [
        Finding("a", "FAIL", "1"),
        Finding("b", "FAIL", "2"),
        Finding("c", "WARN", "3"),
    ]
    assert dfam.summarize_findings(fs) == "dfam=2F/1W"
    assert dfam.summarize_findings([]) == "dfam=0F/0W"


def test_advisory_all_warn_and_unique() -> None:
    assert len(ADVISORY) == 7
    assert all(f.level == "WARN" for f in ADVISORY)
    assert len({f.rule for f in ADVISORY}) == 7
