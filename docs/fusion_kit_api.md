# fusion_kit API index

Before raw adsk in execute/parts: find helper here. Missing → raw adsk in `parts/helpers.py` + open a GitHub issue using the *Missing helper* template.
Always run via `fusion_kit.loader.run_part(entry)`, never `import fusion_kit` in execute code.
Column 3 = raw adsk tokens the helper wraps (empty = none), dotted allowed. Read by `hooks/fusion_kit_nudge.py`. `!` prefix (`!addNewComponent`) = exact 1:1 replacement: first hit per session+file+token is denied once, retry passes. Plain token = nudge only.
Rows = every public module-level def (no leading `_`), stored in the module card `docs/api/<module>.md`, never here. Col 2 ends ` — mod.py:LINE` = def line (test-checked). Prose lives in the def docstring. Enforced by `tests/test_api_index_offline.py`.

Read `docs/api/<module>.md` for the module you use, not the whole set.

Modules (purpose — card — helper count):

- build_check — One execute = build + all verification, compact keyword output — docs/api/build_check.md (4 helpers)
- component — Component and occurrence creation helpers — docs/api/component.md (3 helpers)
- dfam_bed — DfAM print bed: bed attribute, rotation to -Z, print-frame copy and face matching — docs/api/dfam_bed.md (16 helpers)
- dfam_body — DfAM body entry: dfam_check, dfam_skip grammar (parse/format) and body attribute read/write — docs/api/dfam_body.md (5 helpers)
- dfam_facts — DfAM adsk adapter: BRepBody -> HoleFact / EdgeFact / BodyFact via facts_from_body — docs/api/dfam_facts.md (1 helpers)
- dfam_geom — DfAM BRep helpers: edge, face, annulus and cone classification on Fusion geometry — docs/api/dfam_geom.md (2 helpers)
- dfam_rules — DfAM pure rule checks on plain facts (no adsk, runs offline) — docs/api/dfam_rules.md (13 helpers)
- dfam_warp — DfAM warp-relief groove geometry: groove rects, families, print-frame warp zones — docs/api/dfam_warp.md (5 helpers)
- expr — Expression-carrying values for Fusion parametric scripts — docs/api/expr.md (9 helpers)
- feature_quality — Feature-quality rules: timeline smells that a more specific Fusion feature replaces — docs/api/feature_quality.md (10 helpers)
- features_axes — Construction axes/planes and body transforms (move, rotate). Split from fusion_kit.features — docs/api/features_axes.md (9 helpers)
- features_bed — Print-bed elephant-foot chamfer (bed_chamfer). Split from fusion_kit.features — docs/api/features_bed.md (1 helpers)
- features_common — Shared primitives for fusion_kit.features_*: ops enums, ValueInput, collections, face normal,... — docs/api/features_common.md (2 helpers)
- features_edges — Edge and face selection, convex edges, fillet/chamfer. Split from fusion_kit.features — docs/api/features_edges.md (10 helpers)
- features_extrude — Profiles, extrude, revolve, shell and construction planes/axes from profiles. Split from... — docs/api/features_extrude.md (8 helpers)
- features_pattern — Circular, rectangular and grid patterns, mirror, combine. Split from fusion_kit.features — docs/api/features_pattern.md (6 helpers)
- features_warp — Warp relief grooves on the print bed. Split from fusion_kit.features — docs/api/features_warp.md (2 helpers)
- graph — Pure build graph: node model, rule flags, SKQ_WARN attachment, text render, diff, JSON round-trip — docs/api/graph.md (9 helpers)
- grip_fit — Press-fit grip fins: thin bending fins for a bore (kind="hole") or peg (kind="shaft"), axis X, Y... — docs/api/grip_fit.md (17 helpers)
- holes — Screw hole helper: round clearance hole, or slot (play > 0), with 45 deg countersink on the... — docs/api/holes.md (16 helpers)
- joints — Dovetail slide joint: pure profile geometry + Fusion adapter — docs/api/joints.md (3 helpers)
- loader — Entry-point runner for Fusion 360 part scripts — docs/api/loader.md (1 helpers)
- param_lint — Static lint: user-parameter values read in Python at build time (BUILD-ONLY params) — docs/api/param_lint.md (3 helpers)
- param_sweep — Live parametric robustness: set each swept User Parameter to its range ends and check the model — docs/api/param_sweep.md (10 helpers)
- printability — FDM printability checker: pure mesh core + Fusion adapter — docs/api/printability.md (13 helpers)
- probe — Sketch probe: run named steps on one sketch, one PROBE line per step, optional rollback — docs/api/probe.md (1 helpers)
- report — Diagnostic report and measurement helpers for Fusion 360 scripts — docs/api/report.md (10 helpers)
- sketch — Fully-constrained sketch helpers for Fusion 360 — docs/api/sketch.md (22 helpers)
- sketch_quality — Sketch quality lines for report() and build_check: SKQ_FAIL / SKQ_WARN — docs/api/sketch_quality.md (10 helpers)
- steps — Timed step runner for split Fusion builds: one execute call per context, steps run in the given... — docs/api/steps.md (1 helpers)
- timeline — Timeline labels and a compact timeline dump (input for the CadSimplifier review) — docs/api/timeline.md (8 helpers)
- user_params — User Parameter utilities for Fusion 360 parametric scripts — docs/api/user_params.md (5 helpers)
- view — Viewport screenshot to PNG file + named camera orientations. fusion_mcp_read screenshot returns... — docs/api/view.md (2 helpers)
