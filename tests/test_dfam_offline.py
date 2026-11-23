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
    peak_mm=dfam.teardrop_required_peak_mm(2.0),
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


def test_teardrop_peak_45deg_factor() -> None:
    assert dfam.teardrop_peak_mm(1.0) == pytest.approx(math.sqrt(2.0), abs=1e-12)
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
    assert dfam.check_holes([_hole(peak_mm=dfam.teardrop_required_peak_mm(2.0))]) == []


def test_horizontal_peak_97pct_fails_teardrop() -> None:
    peak = dfam.teardrop_required_peak_mm(2.0) * 0.97
    assert _rules(dfam.check_holes([_hole(peak_mm=peak)])) == ["teardrop"]


def test_horizontal_peak_98pct_boundary_passes() -> None:
    # rule is strict '<' against req * 0.98: exactly 0.98 * req is not a violation
    peak = dfam.teardrop_required_peak_mm(2.0) * 0.98
    assert dfam.check_holes([_hole(peak_mm=peak)]) == []


def test_required_peak_notch_up_to_8mm_diameter() -> None:
    assert dfam.teardrop_required_peak_mm(2.25) == pytest.approx(2.65, abs=1e-12)  # d4.5: r + 0.4
    assert dfam.teardrop_required_peak_mm(4.0) == pytest.approx(4.4, abs=1e-12)  # d8 still notch


def test_required_peak_full_point_above_8mm_diameter() -> None:
    assert dfam.teardrop_required_peak_mm(4.01) == pytest.approx(4.01 * math.sqrt(2.0), abs=1e-12)  # d8.02
    assert dfam.teardrop_required_peak_mm(5.0) == pytest.approx(5.0 * math.sqrt(2.0), abs=1e-12)  # d10


def test_required_peak_d18_full_point_when_notch_top_above_peak() -> None:
    # d1.8: notch top r+0.4 = 1.3 > full peak r*sqrt2 = 1.273 -> required = full point; d2 still notch 1.4
    assert dfam.teardrop_required_peak_mm(0.9) == pytest.approx(0.9 * math.sqrt(2.0), abs=1e-12)
    assert dfam.teardrop_required_peak_mm(1.0) == pytest.approx(1.4, abs=1e-12)


def test_full_point_d18_passes() -> None:
    assert dfam.check_holes([_hole(radius_mm=0.9, peak_mm=0.9 * math.sqrt(2.0))]) == []


def test_notch_d45_passes_at_r_plus_04() -> None:
    assert dfam.check_holes([_hole(radius_mm=2.25, peak_mm=2.65)]) == []


def test_notch_d45_old_2r_apex_fails_too_big() -> None:
    # 60 deg cone on small hole: apex 2r = 4.5 > 1.5 * 2.65
    assert _rules(dfam.check_holes([_hole(radius_mm=2.25, peak_mm=4.5)])) == ["teardrop"]


def test_notch_within_1_5x_passes_just_above_fails() -> None:
    assert dfam.check_holes([_hole(radius_mm=2.25, peak_mm=2.65 * 1.4)]) == []
    assert _rules(dfam.check_holes([_hole(radius_mm=2.25, peak_mm=2.65 * 1.5 + 0.01)])) == ["teardrop"]


def test_full_point_d10_r_sqrt2_passes() -> None:
    assert dfam.check_holes([_hole(radius_mm=5.0, peak_mm=5.0 * math.sqrt(2.0))]) == []


def test_full_point_d10_notch_peak_fails() -> None:
    assert _rules(dfam.check_holes([_hole(radius_mm=5.0, peak_mm=5.4)])) == ["teardrop"]


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
    out = dfam.check_holes([_hole(on_bed=True, radius_mm=radius, peak_mm=dfam.teardrop_required_peak_mm(radius))])
    assert _rules(out) == expected


def test_first_layer_not_checked_off_bed() -> None:
    out = dfam.check_holes([_hole(on_bed=False, radius_mm=0.5, peak_mm=dfam.teardrop_required_peak_mm(0.5))])
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


@pytest.mark.parametrize(
    ("length", "level"),
    [
        (1.4, "WARN"),  # short bed segment between dovetail tongue chamfers: left sharp on purpose
        (3.0, "FAIL"),  # BED_SHORT_EDGE_MM boundary: rule is length < 3.0
        (5.0, "FAIL"),
    ],
)
def test_bed_sharp_short_edge_warns(length: float, level: str) -> None:
    out = dfam.check_edges([_edge(kind="bed", treatment="sharp", size_mm=0.0, room_mm=5.0, length_mm=length)])
    assert _rules(out) == ["bed_chamfer"]
    assert out[0].level == level
    if level == "WARN":
        assert "left sharp" in out[0].msg


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
# pin_base_chamfer: concave base of a convex pin (sharp / chamfer or fillet < 0.3 FAIL)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("treatment", "size", "expected"),
    [
        ("sharp", 0.0, ["pin_base_chamfer"]),
        ("chamfer", 0.29, ["pin_base_chamfer"]),
        ("chamfer", 0.3, []),
        ("fillet", 0.1, ["pin_base_chamfer"]),
        ("fillet", 0.3, []),
    ],
)
def test_pin_base_matrix(treatment: str, size: float, expected: list[str]) -> None:
    out = dfam.check_edges([_edge(kind="pin_base", treatment=treatment, size_mm=size)])
    assert _rules(out) == expected
    assert all(f.level == "FAIL" for f in out)


def test_pin_base_skip_opt_out() -> None:
    assert "pin_base_chamfer" in dfam.SKIPPABLE_RULES
    assert (
        dfam.format_skip({"pin_base_chamfer": "print on fillet-free pin"})
        == "pin_base_chamfer:print on fillet-free pin"
    )
    edges = [_edge(kind="pin_base", treatment="sharp", size_mm=0.0)]
    assert _rules(dfam.check_all(_body(), [], edges, {})) == ["pin_base_chamfer"]
    out = dfam.check_all(_body(), [], edges, {"pin_base_chamfer": "pin base sits on a boss"})
    assert _rules(out) == ["dfam_skip"]
    assert out[0].level == "WARN"


def test_pin_base_grip_fin_exempt() -> None:
    # FAIL on old code: pin_base not in the on_grip_fin skip, sharp fin root at shaft -> pin_base_chamfer FAIL
    assert dfam.check_edges([_edge(kind="pin_base", treatment="sharp", size_mm=0.0, on_grip_fin=True)]) == []


@pytest.mark.parametrize(
    ("slit_width", "expected"),
    [
        (0.1, []),
        (dfam.SLIT_MAX_MM, []),
        (0.25, ["pin_base_chamfer"]),
        (None, ["pin_base_chamfer"]),
    ],
)
def test_pin_base_slit_floor_exempt(slit_width: float | None, expected: list[str]) -> None:
    # FAIL on old code: EdgeFact had no slit_width_mm (TypeError), and 0.1 mm slit floor FAILed as sharp pin base
    out = dfam.check_edges([_edge(kind="pin_base", treatment="sharp", size_mm=0.0, slit_width_mm=slit_width)])
    assert _rules(out) == expected


# ---------------------------------------------------------------------------
# grip_fin_sliver: grip_fit min fin tangential width over lead-in band < GRIP_FIN_SECTION_MIN_MM (0.4) = FAIL.
# FAIL on old code: rule did not exist (no finding for 0.39).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("section", "expected"),
    [
        (0.8, []),
        (0.4, []),
        (0.39, ["grip_fin_sliver"]),
        (0.0, ["grip_fin_sliver"]),
        (None, []),  # no grip fin on body: rule silent
    ],
)
def test_grip_fin_sliver_matrix(section: float | None, expected: list[str]) -> None:
    out = dfam.check_body_facts(_body(grip_fin_section_mm=section))
    assert _rules(out) == expected
    assert all(f.level == "FAIL" for f in out)


def test_grip_fin_sliver_skip_opt_out() -> None:
    assert "grip_fin_sliver" in dfam.SKIPPABLE_RULES
    assert dfam.GRIP_FIN_SECTION_MIN_MM == 0.4
    assert _rules(dfam.check_all(_body(grip_fin_section_mm=0.2), [], [], {})) == ["grip_fin_sliver"]
    out = dfam.check_all(
        _body(grip_fin_section_mm=0.2), [], [], {"grip_fin_sliver": "print on a 0.3 mm fin on purpose"}
    )
    assert _rules(out) == ["dfam_skip"]
    assert out[0].level == "WARN"


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
