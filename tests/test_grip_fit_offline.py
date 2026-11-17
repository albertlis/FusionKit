"""Offline tests for fusion_kit.grip_fit: fin corner expressions (evaluated with expr.evaluate), kind guard,
lead-in cone points/slope and fin face tag rule. No Fusion runtime: adsk stubs come from conftest.py. Expressions are
evaluated offline; mm units.
"""

import math
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import fusion_kit.grip_fit as gf_mod
from fusion_kit.dfam import DFAM_GROUP, GRIP_FIN_ATTR, GRIP_FIN_SECTION_ATTR, GRIP_FIN_SECTION_MIN_MM
from fusion_kit.expr import evaluate
from fusion_kit.grip_fit import (
    EntrySide,
    GripKind,
    axis_frame,
    default_entry,
    fin_band_mm,
    fin_corners,
    fin_section_min_mm,
    grip_fit,
    is_grip_fin_end_face,
    is_grip_fin_face,
    lead_in_corners,
    lead_in_trims_body,
    model_point,
    relief_profile_corners,
    relief_roof_rise,
)


def _vals(corners: list[tuple[str, str]], names: dict[str, float] | None = None) -> list[tuple[float, float]]:
    env = names or {}
    return [(evaluate(a, env), evaluate(b, env)) for a, b in corners]


def test_relief_roof_rise_shaft_hand_calc() -> None:
    # shaft d 8, relief 0.8: r_out = 4 + 0.5, r_in = 3.2 -> rise = 1.3 * tan(46 deg)
    rise = evaluate(relief_roof_rise("shaft", "8 mm", "0.8 mm"), {})
    assert rise == pytest.approx(1.3 * math.tan(math.radians(46.0)), abs=1e-9)


def test_relief_roof_rise_hole_hand_calc() -> None:
    # hole d 8, relief 0.8: r_in = 4, r_out = 4.8 -> rise = 0.8 * tan(46 deg)
    rise = evaluate(relief_roof_rise("hole", "8 mm", "0.8 mm"), {})
    assert rise == pytest.approx(0.8 * math.tan(math.radians(46.0)), abs=1e-9)


def test_relief_roof_rise_bad_kind_raises() -> None:
    bad_kind: Any = "bore"
    with pytest.raises(ValueError, match="not in"):
        relief_roof_rise(bad_kind, "8 mm", "0.8 mm")


def test_relief_profile_roof_end_is_start_plus_depth_plus_rise() -> None:
    # roof far end (corner 3, along) must equal start + depth + relief_roof_rise
    rise = evaluate(relief_roof_rise("shaft", "8 mm", "0.8 mm"), {})
    corners = relief_profile_corners("shaft", "8 mm", "0.8 mm", "2 mm", "4.65 mm")
    roof_end = _vals(corners)[3][1]
    assert roof_end == pytest.approx(2 + 4.65 + rise, abs=1e-9)


def test_relief_pocket_sized_from_rise_leaves_root() -> None:
    # side_wall case: zone 7 mm from xt, roof end must sit 1 mm short of shoulder face (xs = xt + 7)
    rise = evaluate(relief_roof_rise("shaft", "8 mm", "0.8 mm"), {})
    depth = 7 - 1 - rise
    roof_end = _vals(relief_profile_corners("shaft", "8 mm", "0.8 mm", "0 mm", f"{depth} mm"))[3][1]
    assert roof_end == pytest.approx(7 - 1, abs=1e-9)


def test_fin_corners_bad_kind_raises() -> None:
    bad_kind: Any = "bore"  # untyped on purpose: runtime guard, not the Literal type
    with pytest.raises(ValueError, match="not in"):
        fin_corners(bad_kind, "22 mm", "0.6 mm", "0.8 mm", "1.2 mm", "45 deg")


def test_grip_fit_bad_kind_raises_before_fusion() -> None:
    comp: Any = MagicMock(name="comp")
    body: Any = MagicMock(name="body")
    bad_kind: Any = "bore"
    with pytest.raises(ValueError, match="not in"):
        grip_fit(comp, body, ("0 mm", "0 mm"), "0 mm", "10 mm", "22 mm", bad_kind, label="seat")
    comp.features.assert_not_called()


def test_hole_tip_radius_is_nominal_minus_interference() -> None:
    v = _vals(fin_corners("hole", "22 mm", "0.6 mm", "0.8 mm", "1.2 mm", "45 deg"))
    tip_minus, tip_plus = v[0], v[1]
    assert math.isclose(tip_minus[0], 10.4, abs_tol=1e-9)  # D/2 - 0.6
    assert math.isclose(tip_plus[0], 10.4, abs_tol=1e-9)
    assert math.isclose(tip_minus[1], -0.4, abs_tol=1e-9)
    assert math.isclose(tip_plus[1], 0.4, abs_tol=1e-9)


def test_hole_root_embedded_beyond_relief_edge() -> None:
    v = _vals(fin_corners("hole", "22 mm", "0.6 mm", "0.8 mm", "1.2 mm", "45 deg"))
    relief_edge = 11.0 + 1.2
    assert min(v[2][0], v[3][0]) > relief_edge  # root corners in solid wall


def test_shaft_tip_radius_is_nominal_plus_interference() -> None:
    v = _vals(fin_corners("shaft", "8 mm", "0.6 mm", "0.8 mm", "1.2 mm", "45 deg"))
    assert math.isclose(v[0][0], 4.6, abs_tol=1e-9)  # d/2 + 0.6
    assert math.isclose(v[1][0], 4.6, abs_tol=1e-9)


def test_shaft_root_embedded_inside_core() -> None:
    v = _vals(fin_corners("shaft", "8 mm", "0.6 mm", "0.8 mm", "1.2 mm", "45 deg"))
    core_edge = 4.0 - 1.2
    assert max(v[2][0], v[3][0]) < core_edge  # root corners inside core


@pytest.mark.parametrize("kind", ["hole", "shaft"])
def test_fin_thickness_at_tip_and_root(kind: GripKind) -> None:
    v = _vals(fin_corners(kind, "22 mm", "0.6 mm", "0.8 mm", "1.2 mm", "45 deg"))
    assert math.isclose(math.dist(v[0], v[1]), 0.8, abs_tol=1e-9)
    assert math.isclose(math.dist(v[2], v[3]), 0.8, abs_tol=1e-9)


@pytest.mark.parametrize("kind,diameter,tip_r", [("hole", "22 mm", 10.4), ("shaft", "8 mm", 4.6)])
def test_lean_angle_from_radial(kind: GripKind, diameter: str, tip_r: float) -> None:
    v = _vals(fin_corners(kind, diameter, "0.6 mm", "0.8 mm", "1.2 mm", "35 deg"))
    mid_root = ((v[2][0] + v[3][0]) / 2, (v[2][1] + v[3][1]) / 2)
    dr = abs(mid_root[0] - tip_r)
    dt = abs(mid_root[1])
    assert math.isclose(math.degrees(math.atan2(dt, dr)), 35.0, abs_tol=1e-6)


def test_lean_zero_is_radial_rib() -> None:
    v = _vals(fin_corners("hole", "22 mm", "0.6 mm", "0.8 mm", "1.2 mm", "0 deg"))
    assert math.isclose((v[2][1] + v[3][1]) / 2, 0.0, abs_tol=1e-9)


def test_parametric_names_match_literals() -> None:
    lit = _vals(fin_corners("hole", "22 mm", "0.6 mm", "0.8 mm", "1.2 mm", "45 deg"))
    par = _vals(
        fin_corners("hole", "seat_d", "fin_interf", "fin_t", "relief_depth", "fin_lean"),
        {"seat_d": 22.0, "fin_interf": 0.6, "fin_t": 0.8, "relief_depth": 1.2, "fin_lean": math.radians(45.0)},
    )
    flat_lit = [x for p in lit for x in p]
    flat_par = [x for p in par for x in p]
    assert flat_par == pytest.approx(flat_lit, abs=1e-9)


# --- lead-in cone: points, slope, direction -------------------------------------------------------------------


def _lead(kind: GripKind, diameter: str, start: str, depth: str, lead: str = "1 mm") -> list[tuple[float, float]]:
    return _vals(lead_in_corners(kind, diameter, "0.6 mm", lead, start, depth))


def test_lead_in_hole_points() -> None:
    v = _lead("hole", "22 mm", "0 mm", "10 mm")  # tip r = 10.4, lead 1 mm, H = 1.5 mm
    assert math.isclose(v[0][0], 10.4, abs_tol=1e-9) and math.isclose(v[0][1], 0.0, abs_tol=1e-9)  # tip @ entry
    assert math.isclose(v[1][0], 11.4, abs_tol=1e-9) and math.isclose(v[1][1], 0.0, abs_tol=1e-9)  # outer @ entry
    assert math.isclose(v[2][0], 10.4, abs_tol=1e-9) and math.isclose(v[2][1], 1.5, abs_tol=1e-9)  # tip @ far


def test_lead_in_hole_cone_slope_steeper_than_45() -> None:
    v = _lead("hole", "22 mm", "0 mm", "10 mm")
    dr = abs(v[1][0] - v[2][0])
    dz = abs(v[2][1] - v[1][1])
    angle = math.degrees(math.atan2(dz, dr))
    assert angle > 45.0  # overhang check: must stay > 45 deg from horizontal
    assert math.isclose(angle, math.degrees(math.atan(1.5)), abs_tol=1e-9)  # atan((1+0.5)/1)


def test_lead_in_hole_direction_wider_at_entry() -> None:
    v = _lead("hole", "22 mm", "2 mm", "10 mm")
    assert v[1][0] > v[0][0]  # entry radius larger than tip radius (opens outward toward entry)
    assert math.isclose(v[1][1], 2.0, abs_tol=1e-9)  # entry = start (bed side)
    assert v[2][1] > v[1][1]  # cone rises into the fin


def test_lead_in_shaft_points_and_direction() -> None:
    v = _lead("shaft", "8 mm", "0 mm", "10 mm")  # tip r = 4.6, entry = top z = 10
    assert math.isclose(v[0][0], 4.6, abs_tol=1e-9) and math.isclose(v[0][1], 10.0, abs_tol=1e-9)
    assert math.isclose(v[1][0], 3.6, abs_tol=1e-9) and math.isclose(v[1][1], 10.0, abs_tol=1e-9)  # narrower at top
    assert math.isclose(v[2][0], 4.6, abs_tol=1e-9) and math.isclose(v[2][1], 8.5, abs_tol=1e-9)  # tip @ z_top - H
    assert v[1][0] < v[0][0]  # shaft: radius narrows toward the tip


def test_lead_in_shaft_entry_follows_start_plus_depth() -> None:
    v = _lead("shaft", "8 mm", "2 mm", "10 mm")
    assert math.isclose(v[1][1], 12.0, abs_tol=1e-9)  # entry = start + depth


@pytest.mark.parametrize("kind,diameter", [("hole", "22 mm"), ("shaft", "8 mm")])
@pytest.mark.parametrize("lead", ["0.5 mm", "1 mm", "2 mm"])
def test_lead_in_slope_formula_and_overhang_margin(kind: GripKind, diameter: str, lead: str) -> None:
    v = _lead(kind, diameter, "0 mm", "10 mm", lead)
    lead_v = float(lead.split()[0])
    dr = abs(v[1][0] - v[2][0])
    dz = abs(v[2][1] - v[1][1])
    assert math.isclose(dr, lead_v, abs_tol=1e-9)
    assert math.isclose(dz, lead_v + 0.5, abs_tol=1e-9)
    assert math.degrees(math.atan2(dz, dr)) > 45.0


def test_lead_in_parametric_matches_literal() -> None:
    lit = _lead("hole", "22 mm", "0 mm", "10 mm")
    par = _vals(
        lead_in_corners("hole", "seat_d", "fin_interf", "lead_v", "zone_z", "zone_h"),
        {"seat_d": 22.0, "fin_interf": 0.6, "lead_v": 1.0, "zone_z": 0.0, "zone_h": 10.0},
    )
    assert [x for p in par for x in p] == pytest.approx([x for p in lit for x in p], abs=1e-9)


def test_lead_in_bad_kind_raises() -> None:
    bad_kind: Any = "bore"
    with pytest.raises(ValueError, match="not in"):
        lead_in_corners(bad_kind, "22 mm", "0.6 mm", "1 mm", "0 mm", "10 mm")


# --- fin face tag rule ------------------------------------------------------------------------------------------


def test_fin_band_hole_and_shaft() -> None:
    lo, hi = fin_band_mm("hole", 22.0, 0.6, 1.2)  # tip 10.4 .. relief edge 12.2, +/- 0.05
    assert math.isclose(lo, 10.35, abs_tol=1e-9) and math.isclose(hi, 12.25, abs_tol=1e-9)
    lo, hi = fin_band_mm("shaft", 8.0, 0.6, 1.2)  # relief edge 2.8 .. tip 4.6
    assert math.isclose(lo, 2.75, abs_tol=1e-9) and math.isclose(hi, 4.65, abs_tol=1e-9)


def test_is_grip_fin_face_vertical_in_band_and_zone() -> None:
    band, zone = (10.35, 12.25), (0.0, 10.0)
    assert is_grip_fin_face(0.0, 11.0, 5.0, band, zone)  # fin side wall
    assert not is_grip_fin_face(1.0, 11.0, 5.0, band, zone)  # horizontal top/bottom
    assert not is_grip_fin_face(0.7, 11.0, 5.0, band, zone)  # tilted (lead-in cone region)
    assert not is_grip_fin_face(0.0, 13.0, 5.0, band, zone)  # beyond relief edge (wall)
    assert not is_grip_fin_face(0.0, 11.0, 11.0, band, zone)  # above grip zone


def test_is_grip_fin_end_face_axial_cap_in_band_and_zone() -> None:
    # FAIL on old code: is_grip_fin_end_face did not exist; far-end cap (normal along axis) was never tagged
    band, zone = (10.35, 12.25), (0.0, 10.0)
    assert is_grip_fin_end_face(1.0, 11.0, 10.0, band, zone)  # far end cap at zone end
    assert is_grip_fin_end_face(-1.0, 11.0, 0.0, band, zone)  # entry cap, normal sign ignored
    assert not is_grip_fin_end_face(0.0, 11.0, 10.0, band, zone)  # side wall: not an end cap
    assert not is_grip_fin_end_face(0.7, 11.0, 10.0, band, zone)  # tilted (lead-in cone region)
    assert not is_grip_fin_end_face(1.0, 13.0, 10.0, band, zone)  # beyond relief edge
    assert not is_grip_fin_end_face(1.0, 11.0, 11.0, band, zone)  # above grip zone


def test_dfam_attr_contract() -> None:
    assert (DFAM_GROUP, GRIP_FIN_ATTR) == ("dfam", "grip_fin")


# --- axis / entry / extrude-start mapping (grip_fit axis support) --------------------------------------------


def test_axis_frame_right_handed_cycle() -> None:
    assert axis_frame("x") == (0, 1, 2)  # along x, radial y, tangential z
    assert axis_frame("y") == (1, 2, 0)  # along y, radial z, tangential x
    assert axis_frame("z") == (2, 0, 1)  # along z, radial x, tangential y (old layout)


def test_axis_frame_bad_axis_raises() -> None:
    bad_axis: Any = "w"
    with pytest.raises(ValueError, match="not in"):
        axis_frame(bad_axis)


def test_model_point_z_is_old_layout() -> None:
    assert model_point("z", "cx + 1", "cy + 2", "st") == ("cx + 1", "cy + 2", "st")


def test_model_point_x_and_y_put_parts_on_model_axes() -> None:
    assert model_point("x", "R", "T", "A") == ("A", "R", "T")
    assert model_point("y", "R", "T", "A") == ("T", "A", "R")


def test_model_point_x_numeric_fin_tip() -> None:
    # X-axis hole, centre (y, z) = (0, 0): tip corner 0 = (radial 10.4, tangential -0.4) -> model (x=0, y=10.4, z=-0.4)
    (ra, ta), *_ = fin_corners("hole", "22 mm", "0.6 mm", "0.8 mm", "1.2 mm", "45 deg")
    px, py, pz = model_point("x", f"(0 mm) + {ra}", f"(0 mm) + {ta}", "0 mm")
    assert [evaluate(e, {}) for e in (px, py, pz)] == pytest.approx([0.0, 10.4, -0.4], abs=1e-9)


def test_default_entry_hole_low_shaft_high() -> None:
    assert default_entry("hole") == "low"
    assert default_entry("shaft") == "high"


def test_lead_in_entry_high_hole_moves_z_only() -> None:
    v = _vals(lead_in_corners("hole", "22 mm", "0.6 mm", "1 mm", "0 mm", "10 mm", "high"))
    assert math.isclose(v[0][1], 10.0, abs_tol=1e-9)  # entry at start + depth
    assert math.isclose(v[2][1], 8.5, abs_tol=1e-9)  # far = entry - H
    assert math.isclose(v[1][0], 11.4, abs_tol=1e-9)  # hole still wider at entry


def test_lead_in_entry_low_shaft_narrow_at_entry() -> None:
    v = _vals(lead_in_corners("shaft", "8 mm", "0.6 mm", "1 mm", "0 mm", "10 mm", "low"))
    assert math.isclose(v[0][1], 0.0, abs_tol=1e-9)  # entry at start
    assert math.isclose(v[2][1], 1.5, abs_tol=1e-9)  # far = entry + H
    assert math.isclose(v[1][0], 3.6, abs_tol=1e-9)  # shaft still narrower at entry


def test_lead_in_bad_entry_raises() -> None:
    bad_entry: Any = "mid"
    with pytest.raises(ValueError, match="not in"):
        lead_in_corners("hole", "22 mm", "0.6 mm", "1 mm", "0 mm", "10 mm", bad_entry)


def test_grip_fit_bad_axis_and_entry_raise_before_fusion() -> None:
    comp: Any = MagicMock(name="comp")
    body: Any = MagicMock(name="body")
    bad_axis: Any = "w"
    with pytest.raises(ValueError, match="not in"):
        grip_fit(comp, body, ("0 mm", "0 mm"), "0 mm", "10 mm", "22 mm", "hole", axis=bad_axis, label="seat")
    bad_entry: Any = "mid"
    with pytest.raises(ValueError, match="not in"):
        grip_fit(comp, body, ("0 mm", "0 mm"), "0 mm", "10 mm", "22 mm", "hole", entry=bad_entry, label="seat")
    comp.features.assert_not_called()


def test_normal_sign_per_axis() -> None:
    plane: Any = MagicMock(name="plane")
    plane.geometry.normal = SimpleNamespace(x=-1.0, y=0.0, z=0.0)
    assert gf_mod._normal_sign(plane, "x") == -1
    plane.geometry.normal = SimpleNamespace(x=0.0, y=1.0, z=0.0)
    assert gf_mod._normal_sign(plane, "y") == 1
    plane.geometry.normal = SimpleNamespace(x=0.0, y=0.0, z=1.0)
    assert gf_mod._normal_sign(plane, "z") == 1


def test_extrude_zone_minus_normal_starts_offset_plus_normal_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_extrude(comp: Any, prof: Any, distance: Any, **kw: Any) -> str:
        seen.update(kw)
        seen["distance"] = distance
        return "feat"

    monkeypatch.setattr(gf_mod.features, "extrude", fake_extrude)
    comp: Any = MagicMock(name="comp")
    prof: Any = MagicMock(name="prof")
    gf_mod._extrude_zone(comp, prof, "10 mm", -1, op="new", label="x")
    assert seen["start_offset"] == "-(10 mm)" and seen["distance"] == "10 mm"
    gf_mod._extrude_zone(comp, prof, "10 mm", 1, op="new", label="x")
    assert seen["start_offset"] is None


# --- relief pocket profile: conical roof, no flat ceiling (revolve cut) ----------------------------------------------

RELIEF_ROOF_MIN_DEG = 45.5  # overhang check fails at exactly 45 deg; roof walls must be steeper than this


def _capture_grip_fit(
    monkeypatch: pytest.MonkeyPatch,
    kind: GripKind,
    diameter: str,
    lean: str = "45 deg",
    entry: EntrySide = "low",
    body: Any = None,
    lead: str = "1 mm",
) -> tuple[dict[str, list[tuple[str, str, str]]], dict[str, tuple[Any, str, Any]], dict[str, Any]]:
    """Run grip_fit with every Fusion call stubbed. Returns (sketch label -> param_poly points, revolve label -> (profile, op, bodies))."""
    sketches: dict[str, list[tuple[str, str, str]]] = {}
    revolves: dict[str, tuple[Any, str, Any]] = {}
    extrudes: dict[str, Any] = {}

    def fake_sketch(comp: Any, plane: Any, *, label: str) -> MagicMock:
        sk: MagicMock = MagicMock(name=label)
        sk.label = label
        return sk

    def fake_poly(sketch: Any, pts: list[tuple[str, str, str]]) -> None:
        sketches[sketch.label] = pts

    def fake_profile(sketch: Any, outer: bool = False) -> SimpleNamespace:
        return SimpleNamespace(sketch=sketch.label)

    def fake_revolve(comp: Any, prof: Any, axis: Any, op: str, **kw: Any) -> None:
        revolves[kw["label"]] = (prof, op, kw.get("bodies"))

    def fake_extrude(comp: Any, prof: Any, distance: Any, **kw: Any) -> SimpleNamespace:
        extrudes[kw["label"]] = distance
        return SimpleNamespace(bodies=[])

    def fake_plane(*args: Any, **kw: Any) -> SimpleNamespace:
        return SimpleNamespace(geometry=SimpleNamespace(normal=SimpleNamespace(x=0.0, y=0.0, z=1.0)))

    def fake_pattern(comp: Any, entities: Any, axis: Any, qty: Any, **kw: Any) -> SimpleNamespace:
        return SimpleNamespace(bodies=[MagicMock(name="fin_body")])  # live: pattern always yields fin bodies

    monkeypatch.setattr(gf_mod, "mm", lambda expr: evaluate(expr, {}))  # offline: no Fusion design / user params
    monkeypatch.setattr(gf_mod, "new_sketch", fake_sketch)
    monkeypatch.setattr(gf_mod, "param_poly", fake_poly)
    monkeypatch.setattr(gf_mod, "_axis_through", lambda *a, **k: "axis")
    monkeypatch.setattr(gf_mod.features, "axis_plane", fake_plane)
    monkeypatch.setattr(gf_mod.features, "profile", fake_profile)
    monkeypatch.setattr(gf_mod.features, "revolve", fake_revolve)
    monkeypatch.setattr(gf_mod.features, "extrude", fake_extrude)
    monkeypatch.setattr(gf_mod.features, "pattern_circular", fake_pattern)
    monkeypatch.setattr(gf_mod.features, "combine", lambda *a, **k: MagicMock(name="join"))
    comp: Any = MagicMock(name="comp")
    body = body if body is not None else MagicMock(name="body")
    gf_mod.grip_fit(
        comp,
        body,
        ("0 mm", "0 mm"),
        "0 mm",
        "10 mm",
        diameter,
        kind,
        entry=entry,
        fin_count="8",
        lean=lean,
        lead_in=lead,
        label="seat",
    )
    return sketches, revolves, extrudes


# shaft lean 45 deg raises in fin_footprint_rad (pre-existing, unrelated to relief): lean 0 used for shaft here
@pytest.mark.parametrize("kind,diameter,lean", [("hole", "22 mm", "45 deg"), ("shaft", "8 mm", "0 deg")])
def test_relief_profile_roof_is_steep_cone_not_flat(
    monkeypatch: pytest.MonkeyPatch, kind: GripKind, diameter: str, lean: str
) -> None:
    sketches, revolves, _extrudes = _capture_grip_fit(monkeypatch, kind, diameter, lean)
    assert "seat_relief" in revolves and revolves["seat_relief"][1] == "cut", "relief is not a revolve cut"
    assert "seat_relief_sketch" in sketches, "relief profile is not one polygon"
    # axis z: model_point -> (x = radial, y = tangential, z = along)
    pts = [(evaluate(x, {}), evaluate(z, {})) for x, _y, z in sketches["seat_relief_sketch"]]
    z_end = 10.0  # start 0 + depth 10
    d_mm = evaluate(diameter, {})
    r_in_expected = d_mm / 2 if kind == "hole" else d_mm / 2 - 1.2  # hole ring starts at bore wall D/2
    r_min = min(r for r, _ in pts)
    assert r_min == pytest.approx(r_in_expected, abs=1e-9), (
        f"inner radius {r_min:.3f} mm, need {r_in_expected:.3f} mm (hole: ring at D/2, apex on axis cuts core)"
    )
    for k in range(len(pts)):
        (r1, z1), (r2, z2) = pts[k], pts[(k + 1) % len(pts)]
        dr, dz = r2 - r1, z2 - z1
        if abs(z1) < 1e-9 and abs(z2) < 1e-9:
            continue  # entry plane (open end, not a roof)
        if abs(dr) < 1e-9:
            continue  # cylindrical wall parallel to the axis
        assert abs(dz) > 1e-9, f"flat segment perpendicular to axis at z={z1:.3f} mm, profile {pts}"
        angle = math.degrees(math.atan2(abs(dz), abs(dr)))
        assert angle >= RELIEF_ROOF_MIN_DEG, (
            f"roof segment {angle:.2f} deg from horizontal, need >= {RELIEF_ROOF_MIN_DEG}"
        )
    rise = max(z for _, z in pts) - z_end
    assert rise >= 1.2 - 1e-9, f"roof adds {rise:.3f} mm axially, need >= relief depth 1.2 mm"


FIN_ROOF_OVERLAP_MM_SPEC = 0.05  # spec: fin far end overlaps solid above the roof by 0.05 mm so the join fuses


# fin extrude length must reach the roof end: start 0 + depth 10 + roof rise + overlap (fin end face inside solid)
@pytest.mark.parametrize("kind,diameter,lean", [("hole", "22 mm", "45 deg"), ("shaft", "8 mm", "0 deg")])
def test_fin_extrude_reaches_roof_end_plus_overlap(
    monkeypatch: pytest.MonkeyPatch, kind: GripKind, diameter: str, lean: str
) -> None:
    _sketches, _revolves, extrudes = _capture_grip_fit(monkeypatch, kind, diameter, lean)
    fin_len = evaluate(extrudes["seat_fin"], {})
    fin_start = 0.0  # entry low: fins start at zone start (full-length span, start 0)
    fin_end = fin_start + fin_len
    rise = evaluate(relief_roof_rise(kind, diameter, "1.2 mm"), {})
    expected = 10.0 + rise + FIN_ROOF_OVERLAP_MM_SPEC
    assert fin_end == pytest.approx(expected, abs=1e-9), (
        f"fin ends {fin_end:.3f} mm, need depth 10 + roof rise {rise:.3f} + overlap {FIN_ROOF_OVERLAP_MM_SPEC}"
        f" = {expected:.3f} mm: fin face hangs in air under the roof"
    )


@pytest.mark.parametrize("kind,diameter", [("hole", "22 mm"), ("shaft", "8 mm")])
def test_relief_profile_entry_low_is_default(kind: GripKind, diameter: str) -> None:
    # low entry must keep the original profile byte-for-byte (side_wall, roller low seat)
    low = relief_profile_corners(kind, diameter, "1.2 mm", "2 mm", "4.65 mm", entry="low")
    assert low == relief_profile_corners(kind, diameter, "1.2 mm", "2 mm", "4.65 mm")


@pytest.mark.parametrize("kind,diameter", [("hole", "22 mm"), ("shaft", "8 mm")])
def test_relief_profile_entry_high_roof_before_start(kind: GripKind, diameter: str) -> None:
    # entry high: open end at start + depth = 6.65; roof far end at start - rise (away from entry, before start)
    rise = evaluate(relief_roof_rise(kind, diameter, "1.2 mm"), {})
    vals = _vals(relief_profile_corners(kind, diameter, "1.2 mm", "2 mm", "4.65 mm", entry="high"))
    assert [z for _r, z in vals] == pytest.approx([6.65, 6.65, 2.0, 2 - rise], abs=1e-9)
    assert vals[0][0] == pytest.approx(vals[3][0], abs=1e-9), "roof does not end on the bore/core radius"
    assert vals[1][0] == pytest.approx(vals[2][0], abs=1e-9), "outer wall radius mismatch"


# entry high (start 0, depth 10): entry face z=10; relief roof and fin far end on the start side (z < 0)
@pytest.mark.parametrize("kind,diameter,lean", [("hole", "22 mm", "45 deg"), ("shaft", "8 mm", "0 deg")])
def test_entry_high_relief_and_fins_lie_before_start(
    monkeypatch: pytest.MonkeyPatch, kind: GripKind, diameter: str, lean: str
) -> None:
    sketches, _revolves, extrudes = _capture_grip_fit(monkeypatch, kind, diameter, lean, entry="high")
    rise = evaluate(relief_roof_rise(kind, diameter, "1.2 mm"), {})
    relief_z = [evaluate(z, {}) for _x, _y, z in sketches["seat_relief_sketch"]]
    assert max(relief_z) == pytest.approx(10.0, abs=1e-9), f"relief pokes past entry face: z max {max(relief_z):.3f}"
    assert min(relief_z) == pytest.approx(-rise, abs=1e-9), f"roof far end z {min(relief_z):.3f}, need {-rise:.3f}"
    fin_z = [evaluate(z, {}) for _x, _y, z in sketches["seat_fin_sketch"]]
    assert min(fin_z) == pytest.approx(-rise - FIN_ROOF_OVERLAP_MM_SPEC, abs=1e-9), (
        "fin sketch not at start - rise - overlap"
    )
    assert max(fin_z) == pytest.approx(min(fin_z), abs=1e-9), "fin sketch not on one plane"
    fin_len = evaluate(extrudes["seat_fin"], {})
    assert min(fin_z) + fin_len == pytest.approx(10.0, abs=1e-9), "fin span does not end at entry face (start + depth)"


# --- fin span = full zone; min fin section recorded (grip_fin_sliver) ----------------------------------------
# Full-length span (user decision): fins reach the entry face; the entry-end ramp cuts them, no lead-in sliver.


@pytest.mark.parametrize(
    "kind,diameter,lean,entry",
    [
        ("hole", "22 mm", "45 deg", "low"),
        ("hole", "22 mm", "45 deg", "high"),
        ("shaft", "8 mm", "0 deg", "low"),
        ("shaft", "8 mm", "0 deg", "high"),
    ],
)
def test_fin_span_full_length(
    monkeypatch: pytest.MonkeyPatch, kind: GripKind, diameter: str, lean: str, entry: EntrySide
) -> None:
    sketches, _revolves, extrudes = _capture_grip_fit(monkeypatch, kind, diameter, lean, entry=entry)
    fin_st = evaluate(sketches["seat_fin_sketch"][0][2], {})
    fin_end = fin_st + evaluate(extrudes["seat_fin"], {})
    if entry == "low":
        assert fin_st == pytest.approx(0.0, abs=1e-9), f"low entry: fin starts {fin_st:.3f}, need zone start 0"
    else:
        assert fin_end == pytest.approx(10.0, abs=1e-9), f"high entry: fin ends {fin_end:.3f}, need entry face 10"


FIN_RAMP_MIN_DEG = 45.5  # overhang bar, same as RELIEF_ROOF_MIN_DEG
FIN_RAMP_OVERCUT_SPEC_MM = 0.2  # spec: ramp cut line runs 0.2 mm past the fin tip (no tangential contact with tip face)


# fin entry end is a cone at 46 deg from horizontal, root at entry plane, tip starts later (low) / ends earlier (high).
# z_e = entry face: low start, high start + depth. Ramp cut on fin bodies only.
@pytest.mark.parametrize("kind,diameter,lean", [("hole", "22 mm", "45 deg"), ("shaft", "8 mm", "0 deg")])
@pytest.mark.parametrize("entry", ["low", "high"])
def test_fin_entry_ramp_is_cone_root_to_tip(
    monkeypatch: pytest.MonkeyPatch, kind: GripKind, diameter: str, lean: str, entry: EntrySide
) -> None:
    sketches, revolves, _extrudes = _capture_grip_fit(monkeypatch, kind, diameter, lean, entry=entry)
    assert "seat_fin_ramp" in revolves, "fin entry end is a flat foot: no fin ramp cut"
    assert revolves["seat_fin_ramp"][1] == "cut"
    assert revolves["seat_fin_ramp"][2], "fin ramp cut has no fin bodies to target"
    pts = [(evaluate(x, {}), evaluate(z, {})) for x, _y, z in sketches["seat_fin_ramp_sketch"]]
    assert len(pts) == 3, f"ramp is a triangle, got {len(pts)} points"
    d = evaluate(diameter, {})
    rel, i, ov = 1.2, 0.6, 0.6  # grip_fit defaults: relief, interference, ROOT_OVERLAP_MM
    if kind == "hole":
        r_root, r_end = d / 2 + rel + ov, d / 2 - i - FIN_RAMP_OVERCUT_SPEC_MM
        r_tip = d / 2 - i
    else:
        r_root, r_end = d / 2 - rel - ov, d / 2 + i + FIN_RAMP_OVERCUT_SPEC_MM
        r_tip = d / 2 + i
    z_e = 0.0 if entry == "low" else 10.0  # entry face: low start, high start + depth
    s = 1 if entry == "low" else -1
    tan46 = math.tan(math.radians(46.0))
    span = rel + ov + i + FIN_RAMP_OVERCUT_SPEC_MM
    expected = [(r_root, z_e), (r_end, z_e), (r_end, z_e + s * span * tan46)]
    for er, ez in expected:
        assert any(abs(r - er) < 1e-9 and abs(z - ez) < 1e-9 for r, z in pts), (
            f"corner ({er:.4f}, {ez:.4f}) missing in {pts}"
        )
    (r1, z1), (r2, z2) = expected[0], expected[2]
    z_at_tip = z1 + (z2 - z1) * (r_tip - r1) / (r2 - r1)
    assert z_at_tip == pytest.approx(z_e + s * (rel + ov + i) * tan46, abs=1e-9), (
        f"cone at tip radius z={z_at_tip:.4f}: tip must start {(rel + ov + i) * tan46:.3f} mm past root (entry side)"
    )
    angle = math.degrees(math.atan2(abs(z2 - z1), abs(r2 - r1)))
    assert angle >= FIN_RAMP_MIN_DEG, f"entry cone {angle:.2f} deg from horizontal, need >= {FIN_RAMP_MIN_DEG}"
    assert angle == pytest.approx(46.0, abs=1e-9)


@pytest.mark.parametrize("kind,diameter", [("hole", "22 mm"), ("shaft", "8 mm")])
def test_grip_fit_writes_min_section_attr(monkeypatch: pytest.MonkeyPatch, kind: GripKind, diameter: str) -> None:
    body = MagicMock(name="body")
    body.attributes.itemByName.return_value = None  # no earlier grip fit on this body
    _capture_grip_fit(monkeypatch, kind, diameter, "0 deg", body=body)  # lean 0: rectangle, section = fin 0.8 mm
    body.attributes.add.assert_called_once()
    group, name, value = body.attributes.add.call_args.args
    assert (group, name) == (DFAM_GROUP, GRIP_FIN_SECTION_ATTR)
    assert float(value) == pytest.approx(0.8, abs=1e-4)


def test_fin_section_lean_zero_is_fin_thickness() -> None:
    # rigid rib (lean 0): quad is a rectangle, tangential width = fin thickness on every radial line
    assert fin_section_min_mm("hole", 22.0, 1.2, 0.8, 0.6, 0.0, 1.0) == pytest.approx(0.8, abs=1e-9)
    assert fin_section_min_mm("shaft", 8.0, 1.2, 0.8, 0.6, 0.0, 1.0) == pytest.approx(0.8, abs=1e-9)


def test_fin_section_thin_fin_below_min() -> None:
    section = fin_section_min_mm("hole", 22.0, 1.2, 0.3, 0.6, 0.0, 1.0)
    assert section == pytest.approx(0.3, abs=1e-9)
    assert section < GRIP_FIN_SECTION_MIN_MM  # thin fin -> grip_fin_sliver FAIL (test_dfam_offline)


def test_fin_section_default_lean_positive() -> None:
    assert fin_section_min_mm("hole", 22.0, 1.2, 0.8, 0.6, math.radians(45.0), 1.0) > 0.0
    assert fin_section_min_mm("shaft", 8.0, 1.2, 0.8, 0.6, math.radians(45.0), 1.0) > 0.0


# --- lead-in cut: created only when it has solid to trim (no zero-intersection revolve) ------------------------------
# Fins run the full zone; the entry ramp shapes them. The lead-in triangle meets solid only when
# lead > interference + relief. Default (1 vs 0.6 + 1.2) lies inside the relief pocket -> "Brak bryły docelowej".


def test_lead_in_trims_body_threshold() -> None:
    assert not lead_in_trims_body(1.0, 0.6, 1.2)  # default: triangle inside relief pocket
    assert not lead_in_trims_body(1.5, 0.5, 1.0)  # boundary: reach == relief, touches a face only
    assert lead_in_trims_body(1.6, 0.5, 1.0)  # reaches past relief pocket -> cuts solid


def test_lead_in_fin_clip_threshold_above_wall() -> None:
    # Lead-in cone (height H = lead + 0.5) clips fin tips only when H > tan46 * (relief + root overlap + i), the ramp
    # reach at the tip. That lead must lie above the solid-reach threshold (lead = i + relief), so lead_in_trims_body
    # alone decides whether the cut exists and no cut can clip fins without also reaching solid.
    tan46 = math.tan(math.radians(46.0))
    ov = evaluate(gf_mod.ROOT_OVERLAP_MM, {})
    for i, rel in [(0.3, 0.8), (0.6, 1.2), (0.9, 1.5), (0.6, 2.0)]:
        fin_clip_lead = tan46 * (rel + ov + i) - 0.5
        assert fin_clip_lead > i + rel, f"fin clip lead {fin_clip_lead:.3f} <= solid lead {i + rel:.3f}"


@pytest.mark.parametrize("kind,diameter,lean", [("hole", "22 mm", "45 deg"), ("shaft", "8 mm", "0 deg")])
def test_lead_in_cut_skipped_at_default_params(
    monkeypatch: pytest.MonkeyPatch, kind: GripKind, diameter: str, lean: str
) -> None:
    sketches, revolves, _extrudes = _capture_grip_fit(monkeypatch, kind, diameter, lean)
    assert "seat_lead_in" not in revolves, "lead-in cut created with no solid to trim"
    assert "seat_lead_sketch" not in sketches
    assert "seat_relief" in revolves and "seat_fin_sketch" in sketches  # rest of the build still produced


@pytest.mark.parametrize("kind,diameter,lean", [("hole", "22 mm", "45 deg"), ("shaft", "8 mm", "0 deg")])
def test_lead_in_cut_targets_joined_body_when_it_trims_solid(
    monkeypatch: pytest.MonkeyPatch, kind: GripKind, diameter: str, lean: str
) -> None:
    body: Any = MagicMock(name="body")
    _sketches, revolves, _extrudes = _capture_grip_fit(monkeypatch, kind, diameter, lean, body=body, lead="2 mm")
    assert "seat_lead_in" in revolves, "lead 2 mm reaches solid: lead-in cut must exist"
    _prof, op, bodies = revolves["seat_lead_in"]
    assert op == "cut"
    assert bodies == [body], "lead-in cut must target the joined part body"
