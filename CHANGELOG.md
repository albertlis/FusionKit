# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `sketch` opt-in constraint helpers (default behaviour unchanged): `param_poly(..., mode="segments")` (axis-parallel edges: H/V link + one length dim each, redundant lengths skipped, first vertex only anchored; diagonal edge = ValueError), `circle(..., concentric_to=<SketchCircle|SketchArc>)` (addConcentric, centre from ref, x/y/z unused), `polar_point(s, centre_point, radius_expr, angle_expr, *, radius_mm, angle_deg)` (radial distance dim + angular dim vs construction reference line).
- `features_edges.fillet_groups(comp, groups, *, tangent_chain=False, label)` / `chamfer_groups(comp, groups, *, label)`: one fillet / chamfer feature, one edge set per group `[(edges, radius_or_distance), ...]`.
- `feature_quality` WARN rules `finishing_not_last` (fillet/chamfer followed by extrude/revolve/combine/pattern/mirror on the same body; warp-labeled exempt) and `finishing_split` (2nd fillet or 2nd chamfer on one body).
- `feature_quality` WARN rule `warp_not_last`: non-warp fillet / chamfer / extrude / revolve / combine / pattern / mirror on a body after a warp relief groove on the same body (grooves last, after bed chamfer). Groove = `WARP_GROOVE_ATTR` tag, fallback name contains `warp`. `feature_quality.is_warp_groove(name, ent)`.
- `param_sweep` (new module): `sweep_params(design, spec)` sets each User Parameter that declares a range to its ends (or its `values`) in the live document, reads timeline health and body geometry, and restores the original expression (finally). Flags `DFAM_FAIL param_sweep` lines for a rejected expression, an unhealthy feature, a body count change, or no effect on geometry (BUILD-ONLY param).
- `param_lint` (new module): `lint_entry` flags `BUILD_ONLY_PARAM` reads (`sketch.mm`, `eval_fresh`, `itemByName(...).value`) in the entry file and `parts/`. Opt-out per line: `# param-lint: build-only <reason>`.
- `user_params`: a spec item may carry a 4th element `SweepRange` (`min` + `max`, or `values`, plus optional `no_geometry`). New helpers `entry_fields`, `entry_sweep`.
- `sketch_quality` WARN rule `dup_sketch_cross_comp` (design-wide via `check()`; same-component via `check_sketches` / report()): sketches whose profile curves match (sorted kinds + lengths/radii rounded 0.01 mm) in >= 2 sketches -> one line per group; single-circle groups need >= 3 members; skip via `skq_skip`. New helpers `curve_sig`, `cross_dup_lines`.
- `feature_quality` WARN rule `sketch_extrude_is_offset`: single-profile extrude whose profile is coplanar with an earlier planar body face and has the same area (+/-1%) -> extrude from face / offset face. Helper `rule_sketch_extrude_is_offset`. Only faces of the body the extrude modifies count (join / cut / intersect); NewBody extrudes skipped.
- `feature_quality` WARN rules `finish_ops_order` (body-modifying feature: extrude, revolve, hole, pattern, mirror, combine, sweep, loft, shell, thread, split body, after a fillet / chamfer on the same body; warp-labeled exempt; opt-out `finish_ops_order`) and `finish_ops_split` (two fillets or two chamfers on one body with the same radius / distance expression -> one feature; bed chamfers and different expressions exempt; opt-out `finish_ops_split`).

- `build_check` opt-in `not_printed` body flag: `mark_not_printed(body, reason)` / `not_printed_from_body(body)` (body attribute `print` / `not_printed`). A flagged body (bolt, installed shim, bearing, assembly copy) skips the DfAM and print checks and prints `SKIP not_printed <body>: <reason>`. Unflagged bodies unchanged.
- Build graph (`fusion_kit/graph.py`, new module): one node per timeline item (kind, params, inputs / outputs, health, suppressed, `src`) with rule flags `unhealthy`, `suppressed`, `dup_label`, `unused_sketch`, `hardcoded`, `unnamed`, `skq:<rule>`. `collect(design)`, `render`, `diff` (vs previous build), `to_json` / `from_json`, `run(_context)` (read-only dump of the active design). `build_and_check` writes `fk_graph_<name>.txt` / `.json` next to the build log and prints `GRAPH <path> items=N flags=M changed=K` plus flag and diff lines.
- `timeline.set_label` records `fk` / `src` = `<script>.py:<line>` of the build-script call; `timeline.source_of(entity)` reads it.
- Offline tests only; live Fusion run not yet verified.

### Changed

- `fusion-kit` skill and `CadSimplifier` agent: read the build graph (`fk_graph_<name>.txt`, `graph.run`) before opening part source; `CadSimplifier` dumps `graph.py` instead of `timeline.py`.
- `features.bed_chamfer`: per-loop orientation. All loops still go into ONE feature; if that fails, each bed loop (outer + inner) is tested alone in both `isFlipped` values. Loops with no valid orientation are left sharp and printed as `BED_CHAMFER_LOOP_SKIP centre_mm=(x, y, z) reason`; the feature is rebuilt from the passing loops (orientation keeping most loops). Loops valid ONLY in the other orientation get a second feature labelled `<label>_flip` (no longer left sharp); if that second feature fails as a whole, its loops are printed as skipped (`combined isFlipped=... chamfer failed`). RuntimeError only when no loop passes. Loop helpers `_bed_outer_edges` / `_bed_inner_edges` / `_bed_loop_edges` replaced by `features_bed._bed_loops`. Per-loop fallback runs only with `inner_loops`, `exclude_points_mm` or `strict` (default calls keep the old single-feature behaviour). A `chamferFeatures.add()` RuntimeError counts as a failed loop test. Skipped inner loops are recorded in body attr `dfam/bed_loop_skip` (mm centres); `dfam_check` treats their rim as a bed edge (FAIL `bed_chamfer` unless `dfam_skip`).
- `features.bed_chamfer(..., exclude_points_mm=[(x, y, z), ...])`: inner loop whose bbox (off the bed normal) contains a global mm point is dropped silently (deliberate sharp bore). Outer loops never excluded. Printed as `BED_CHAMFER_EXCLUDE`.
- `features.bed_chamfer(..., strict=True)`: raise RuntimeError, no feature created, if any loop has no valid orientation in either `isFlipped` or its `<label>_flip` feature fails. Loops valid only in the other orientation are accepted (second feature).
- `WARP_GROOVE_ATTR` moved to `dfam_types` (re-imported by `features_warp`, same name / value `"warp_groove"`).
- `warp_relief_grooves` literal families (no `length_exprs` entry): one sketch `<label>_sketch` with all groove rects + one cut `<label>` over `profiles()`, instead of seed cut + pattern per family. Usual two-family part: 6 timeline items -> 2, same geometry, `WARP_GROOVE_ATTR` kept on both. Live families (`length_exprs`) keep seed + `pattern_rectangular`. Return list: live features first, then the literal cut. `_relief_family` is live-only (ValueError without `length_expr`); new private `_literal_grooves`, `_depth_text`.
- `build_and_check(..., params=None, sweep=False)`: sweep is off by default; run `sweep=True` at the done-gate. The summary line ends with `sweep=<N>p/<F>F`, `sweep=none` (no `params` given) or `sweep=off`. Sweep lines and `BUILD_ONLY_PARAM` lines follow the interference lines.
- `build_and_check(..., budget_s=30.0)`: budget counts CHECK time only (clock starts after the build; `None` = no budget). Checked after each finished stage, before each comp and before interference. Exceeded stages print `SKIP <stage> budget` (a comp not started: `SKIP comp <name> budget`), show `skipped`, summary ends `budget=exceeded`. A stage not in `checks` shows `off`.
- `build_and_check(..., log_path=None)`: build log file. Every `START <stage>` (written before the stage, so a hang shows where), `TIME` and `SKIP` line is appended + flushed as it happens. Default: env `FUSIONKIT_LOG`, else `<tempdir>/fusion_kit_build_check.log`. Each call appends a `RUN <iso time>` header instead of truncating; the file keeps the last `LOG_KEEP_RUNS` (5) runs, oldest trimmed at the start of a call (per-run `TIME` deltas stay comparable); `log_path=""` disables. Interference `START` line carries `pairs=<n occurrence pairs>`. param_sweep probe `TIME` lines stay on stdout only.
- `expr.derive` accepts 4-item spec entries.
- `build_and_check` prints `TIME <stage> <s>` lines (also when a stage raises). `param_sweep` prints `TIME baseline` and `TIME probe:<name>=<value> <s> cache_hits=N`.
- `param_sweep` caches body verdicts by geometry fingerprint and frame; baseline measured once per sweep.
- `dfam.teardrop_required_peak_mm` / `holes.teardrop_hole`, Ø ≤ 8 mm: required peak = min(r + 0.4, r√2). Flat-topped notch only where r + 0.4 < r√2 (Ø > ~1.93 mm), else full 45° point.
- `printability.check_body`: bed-adjacent exemption only for faces ≥ `BED_ADJACENT_MIN_ANGLE_DEG` (44°).
- `printability.check`: theta test runs before groove test; `_slit_walls` precomputed once per check.
- `dfam._bridge_collinear` and groove rect build: faster (was O(M²) for merge). `dfam._groove_rects` recess-gap pairs come from a sweep over boxes sorted on one axis (`_gap_pairs`).
- `param_sweep`: warp grooves are suppressed on probes of parameters that no groove expression refers to; their baseline is measured with grooves suppressed too. The verdict cache key includes the suppression state.
- `printability.check_body`: per-body face geometry cache (`_GeomCache`, 5 mm snap grid over 1 mm-expanded face boxes); slit and groove tests use XY buckets; `_min_span` projects on the convex hull only (`_hull_idx`).
- `features.convex_edges`: planar-planar creases decided from coedge topology (no `pointContainment`); containment probe is fallback only, counted in `CONVEX_STATS`.
- Measured live by the maintainer (holder build, pitch 30): build 47 s -> 17 s; `check_body` 11-27 s -> 0.8 s per body.

### Removed

- `sketch.tangent`, `sketch.equal`, `sketch.parallel`, `sketch.perpendicular`, `sketch.midpoint`, `sketch.concentric`, `sketch.collinear`, `sketch.symmetric`, `sketch.smooth`, `sketch.polygon` wrappers. Use `s.geometricConstraints.addX` directly (raw adsk).
- `holes.teardrop_hole(notch_max_d_mm=...)` param. Threshold fixed at `TEARDROP_NOTCH_MAX_D_MM` (8.0).

### Fixed

- `warp_relief_grooves`: live seed starts from the run low-edge expression.
- `features.convex_edges`: false "not convex" on thin 45° chamfers.
- `features.bed_chamfer`: a `chamferFeatures.add()` RuntimeError now counts as a failed attempt in every mode (was caught only in per-loop fallback, so default calls escaped with the raw ASM error and skipped the isFlipped retry). Default mode: flip retry runs; if both fail, RuntimeError keeps `bed_chamfer <label>: no orientation ...` and appends `(add() raised: <text>)`.

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
- `dfam._cone_width_mm`: width = area / mean(L_bed, L_top), L_bed = summed length of the face's edges at bed z (split bed circle = several arcs), L_top = summed length of edges lying at the face's max z (side/slant/mid-height edges excluded; fixes width 1.05 -> 1.41 mm on pawl chamfer cone). No top edge -> None, caller falls back to bed edge length (old behaviour). Previously `arcs[0]` only.
- `dfam.check_edges` bed_chamfer: size = BED leg only (`_bed_leg_mm`, neighbours with |n_z| > `AXIS_PARALLEL`) for planar and cone chamfers. Limit = `bed_leg_min_mm(req)` = req / tan(`BED_CHAMFER_ANGLE_DEG`) − `BED_CHAMFER_TOL_MM` (46°, 0.02 mm); `features.bed_chamfer` default `angle_deg` = `BED_CHAMFER_ANGLE_DEG`. Previously max(bed, wall) leg let a steep chamfer (bed 0.2 mm, wall 1.0 mm) pass. 46° 1 mm (bed 0.966) and 45° 1 mm still pass.
- `printability.check_body`: tessellated overhang facet (< min angle) is dropped only when the centroid snaps to a B-rep face (nearest trimmed face per `evaluator.isParameterOnFace`, within `BREP_SNAP_TOL_MM` 0.1 mm), all 3 vertices lie ON that same face (a boundary vertex shared with an adjacent face counts; the vertex's own nearest face is not compared), and that face's angle is >= min angle. A sliver with a vertex off the steep face stays flagged. `face_normal` ValueError (degenerate point/apex) = facet stays flagged, no crash. Untrimmed-only projections no longer match a neighbouring face. No candidate = facet stays overhang (`_brep_overhang_deg`, via `overhang_ok` callback on `check`; default None = unchanged). Identity orientation only. Fixes ok=False from chordal facets at 44.93 deg on 46 deg chamfer corners (demo_bracket, 8 facets, 4.5 mm2).
- `dfam.facts_from_body`: bed edge between bed and an inclined cone face (|n_z| in (`CONE_TILT_TOL`, `AXIS_PARALLEL`)) is a chamfer, not sharp. Fixes false `DFAM_FAIL bed_chamfer ... bed edge sharp` on corners chamfered after a vertical fillet. Size = `_cone_width_mm` (cone area / mean of bed arc and opposite arc) x bed leg factor. Vertical cylinder neighbour unchanged (still sharp FAIL).
- `dfam.check_edges`: `bed_chamfer` and `vertical_corner` skip edges in a warp-groove footprint (0.8 mm groove rims and crossing corners were FAIL, impossible to satisfy). Outer corners unchanged. Exemption needs a qualifying parallel family: lone blind slot, single 0.8 mm gap or two pads 0.8 mm apart are FAIL again.
- `printability.check`: groove ceilings (0.8 mm wide, 0.3 mm above bed) no longer count as overhang area, only when inside a qualifying groove rect. Triangle-bbox heuristic (`_narrow_low_bridge`) removed: it dropped tessellation slivers of large ceilings (20x20 ceiling z 0.3: 192 vs 200 mm2) and ignored rotation.
- `warp_relief_grooves`: relief-family seed pattern now uses `IDENTICAL_PATTERN` (default `OPTIMIZED` truncated the body once the other family existed).
