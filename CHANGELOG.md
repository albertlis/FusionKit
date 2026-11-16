# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-16

### Added

- `fusion_kit` helper library for Autodesk Fusion scripts (Python 3.12, stdlib and `adsk` only): `component`, `dfam`, `enums`, `expr`, `features`, `holes`, `joints`, `loader`, `printability`, `report`, `sketch`, `steps`, `timeline`, `user_params`, `view`.
- Claude Code plugin:
  - `fusion-kit` skill (`skills/fusion-kit/SKILL.md`).
  - `CadSimplifier` agent (read-only timeline review).
  - Four hooks (`hooks/hooks.json`): `fusion_kit_nudge`, `fusion_kit_brief_guard`, `dfam_guard`, `cad_simplify_nudge`.
  - MCP configuration (`.mcp.json`): official Autodesk Fusion MCP and optional fusion-cad MCP.
- Documentation: helper index (`docs/fusion_kit_api.md`), verified Fusion behaviour (`docs/gotchas.md`), DfAM rules (`docs/dfam_guidelines.md`).
- Offline test suite (`tests/`), live smoke scripts (`tests/smoke/`), CI workflow (`.github/workflows/ci.yml`).
- `dfam.in_warp_groove(point_mm, grooves, bed_z_mm)`: shared groove predicate. Only the qualifying family (`_warp_family`, ≥ 2 parallel grooves) counts. `EdgeFact.in_warp_groove` (default False) set by `facts_from_body`.
- `dfam.in_groove_rect(point_mm, r, bed_z_mm)`: one groove rect at a point (band, width, grown footprint). `dfam.warp_family_mm(body)`: qualifying groove family of a body, mm, body frame.
- `printability.check(exempt_rects=...)` / `sweep(exempt_rects=...)` / `check_body`: down-facing triangle wholly inside one qualifying groove rect is not overhang. Default None = no exemption. `check_body` passes `warp_family_mm` for identity orientation only. Replaces `groove_bridge_exempt` (removed).

### Changed

- `features.bed_chamfer`: two-distance chamfer (`addTwoDistancesChamferEdgeSet`) with new `angle_deg=46.0` (chamfer face angle from horizontal). `distance` stays the wall leg; bed leg = distance / tan(angle_deg). Previously equal-distance 45 deg, which fails the DfAM `bed_chamfer` limit (n_z 0.7071). isFlipped tried False then True; an orientation is accepted only if the chamfer face angle acos(|n_z|) equals `angle_deg` within 0.5 deg AND |n_z| < 0.707 (a wrong orientation with |n_z| < 0.707 passed for angle < 45: 30 requested -> 60 deg face). `angle_deg` outside (45, 90) exclusive raises ValueError (45 gives |n_z| 0.7071 and fails; equal-legs mode removed). RuntimeError if neither orientation matches. Live Fusion check pending.

### Fixed

- `dfam._groove_rects` / `printability.check_body`: groove ceilings at crossings (0.8 x 0.8 squares where a warp groove meets a perpendicular one) were overhang after the body-edge fix. Bed-gap pieces stopped at each crossing, so the crossing square was in no rect. Collinear pieces of one groove line are now merged (`_bridge_collinear` / `_merge_collinear`) when the gap between them is ≤ `WARP_BRIDGE_GAP_MAX_MM` (1.0 mm + pads): the gap is the perpendicular groove. Body-edge end inset unchanged: a collinear notch 10 mm past a strip end is still not exempt. Family count is per merged groove line. `dfam._warp_family` now requires ≥ 2 distinct parallel centre lines per direction (cross-axis centre within `WARP_EDGE_PAD_MM` = same line): a dashed slot (two pieces of one line, 15 mm apart) no longer qualifies.
- `dfam._groove_rects(bed, span_mm)` / `warp_family_mm` / `facts_from_body`: recess-gap groove strip ends extended to the body footprint only within `WARP_END_INSET_MAX_MM` (2.0 mm) of it. Previously every rect ran to the body bbox, so a collinear bed notch anywhere along the groove line was exempt. Bed faces stop at the bed chamfer / corner inset, so ceiling end segments at the plate edge fell outside every rect and were counted as overhang (`check_body` ok=False on demo_bracket).
- `dfam.facts_from_body`: bed edge against a tangent non-planar face (Torus / BSpline fillet, also on curved bed edges) is a fillet, not a smooth seam: FAIL `bed_chamfer` "needs chamfer, not fillet". Radius = Torus `minorRadius` (0.0 = unknown, kind still fillet). Previously dropped silently.
- `dfam._cone_width_mm`: width = area / mean(L_bed, L_top), L_bed = summed length of the face's edges at bed z (split bed circle = several arcs), L_top = summed length of edges wholly above it. No top edge -> None, caller falls back to bed edge length (old behaviour). Previously `arcs[0]` only.
- `dfam.check_edges` bed_chamfer: size = BED leg only (`_bed_leg_mm`, neighbours with |n_z| > `AXIS_PARALLEL`) for planar and cone chamfers. Limit = `bed_leg_min_mm(req)` = req / tan(`BED_CHAMFER_ANGLE_DEG`) − `BED_CHAMFER_TOL_MM` (46°, 0.02 mm); `features.bed_chamfer` default `angle_deg` = `BED_CHAMFER_ANGLE_DEG`. Previously max(bed, wall) leg let a steep chamfer (bed 0.2 mm, wall 1.0 mm) pass. 46° 1 mm (bed 0.966) and 45° 1 mm still pass.
- `printability.check_body`: tessellated overhang facet (< min angle) is dropped only when the centroid snaps to a B-rep face (nearest trimmed face per `evaluator.isParameterOnFace`, within `BREP_SNAP_TOL_MM` 0.1 mm), all 3 vertices lie ON that same face (a boundary vertex shared with an adjacent face counts; the vertex's own nearest face is not compared), and that face's angle is >= min angle. A sliver with a vertex off the steep face stays flagged. `face_normal` ValueError (degenerate point/apex) = facet stays flagged, no crash. Untrimmed-only projections no longer match a neighbouring face. No candidate = facet stays overhang (`_brep_overhang_deg`, via `overhang_ok` callback on `check`; default None = unchanged). Identity orientation only. Fixes ok=False from chordal facets at 44.93 deg on 46 deg chamfer corners (demo_bracket, 8 facets, 4.5 mm2).
- `dfam.facts_from_body`: bed edge between bed and an inclined cone face (|n_z| in (`CONE_TILT_TOL`, `AXIS_PARALLEL`)) is a chamfer, not sharp. Fixes false `DFAM_FAIL bed_chamfer ... bed edge sharp` on corners chamfered after a vertical fillet. Size = `_cone_width_mm` (cone area / mean of bed arc and opposite arc) x bed leg factor. Vertical cylinder neighbour unchanged (still sharp FAIL).
- `dfam.check_edges`: `bed_chamfer` and `vertical_corner` skip edges in a warp-groove footprint (0.8 mm groove rims and crossing corners were FAIL, impossible to satisfy). Outer corners unchanged. Exemption needs a qualifying parallel family: lone blind slot, single 0.8 mm gap or two pads 0.8 mm apart are FAIL again.
- `printability.check`: groove ceilings (0.8 mm wide, 0.3 mm above bed) no longer count as overhang area, only when inside a qualifying groove rect. Triangle-bbox heuristic (`_narrow_low_bridge`) removed: it dropped tessellation slivers of large ceilings (20x20 ceiling z 0.3: 192 vs 200 mm2) and ignored rotation.
- `warp_relief_grooves`: relief-family seed pattern now uses `IDENTICAL_PATTERN` (default `OPTIMIZED` truncated the body once the other family existed).
