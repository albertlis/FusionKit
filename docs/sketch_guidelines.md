# Sketch guidelines (Fusion 360, fusion_kit)

## Scope and status

- Applies to every sketch that feeds a feature in a fusion_kit part.
- Reviewed live, read-only, by `agents/SketchReviewer.md` (dump of sketches and features). Its recipes name fusion_kit helpers; each row states `present` or GAP.
- Automatic `SKQ_*` lines are printed by `report()` and `build_check` (`fusion_kit/sketch_quality.py`, `fusion_kit/feature_quality.py`). The SKQ list below is the rule set those modules implement.
- Sources are web reports (`S1`–`S10` and the Fusion help links below). They are advice, not measured values. Where a rule is our own, it is marked **FusionKit rule**.

## Rules (ranked by consequence)

| # | Rule | Sources | Exceptions |
|---|---|---|---|
| 1 | Fully define a sketch before a feature uses it. | S3, S4, Fusion help (fully define) | Diagnostic sketches only. |
| 2 | Constrain with relations first; dimension only design intent. | S1, S4, S5, S6 | The value that is a User Parameter of the design. |
| 3 | Do not dimension geometry that is already constrained (over-dimensioning). | S4, S1 | None. |
| 4 | Use symmetry, equal, midpoint and concentric instead of repeated or mirrored dimensions. | S1, S3 | None. |
| 5 | Position geometry against other geometry, not absolute coordinates. | S3 | Anchor to the origin (one coincident). |
| 6 | A through cut is "Through all", not a guessed depth. | S9, S10 | Depth set by a real face: "To object" with offset. |
| 7 | Pattern and mirror as features, not inside a sketch. Matches CLAUDE.md design intent. | S9 | None (FusionKit rule). |
| 8 | Fillets and chamfers are 3D features at the end of the build. Inside a sketch only when functional. | S1, S2, Autodesk forum (URL not recorded) | See exceptions below. |
| 9 | Keep sketches simple: one intent per sketch. | S1 | None. |
| 10 | Project only the edges that drive the sketch. A symmetry axis is a construction line. | S4 | None. |
| 11 | One sketch, many features, instead of duplicate sketches. | S4 | Instances that differ in shape (measured). |
| 12 | Name every timeline item. | S3 (intent), FusionKit rule (`timeline_unnamed` must be 0) | None. |

Weak or no source (own rules, marked):
- Avoid "Fix" on sketch points (Autodesk Fix blog, S-fix, weak source; the blog itself says Fix makes changes harder). FusionKit rule: `fix_constraint`.
- Constraints over dimension-formulas (`w/2`, `pitch*2`): FusionKit rule, no Autodesk source. The user's rule.
- Curvature (G2), polygon and offset recipes: FusionKit rules.

## Exceptions: functional sketch fillets

Rule 8 allows an arc in a sketch only when one of these holds:
- the arc centre or radius is a dimension anchor the design needs (S1 concept: anchor to a real point);
- the arc is part of a profile for loft or sweep (the fillet must be in the path or section);
- a concentric radius is a design value that must be shared by several arcs.

Otherwise: the sketch corner stays sharp and the fillet is `features.fillet(tangent_chain=True)` on the 3D edges after the feature.

## SKQ rule list

| ID | Level | Check | Replacement |
|---|---|---|---|
| `underconstrained` | WARN | `not isFullyConstrained` | Rule 1: add relations, then dimensions for intent |
| `sketch_fillet` | WARN | Arc tangent to 2 lines at a corner, not used as a dimension anchor or profile | 3D fillet (`features.fillet`) |
| `expr_dim` | WARN | Dimension expression with arithmetic or a literal, where a constraint fits | Midpoint / symmetric / pattern (see SketchReviewer 2a) |
| `dim_ratio` | WARN | More dimensions than geometric constraints (over-dimensioned), one line per sketch | Rule 3; point-coordinate dims → horizontal/vertical/coincident/collinear constraints |
| `fix_constraint` | WARN | Fixed points (`isFixed`) | `sketch.anchor` + H/V dims |
| `extent_distance_matches_body` | WARN | Distance extent >= 1.0 mm (`SHALLOW_DEPTH_MM`) ending on an existing face (±0.01 mm, `EXTENT_TOL_MM`) | To object (+ offset) |
| `cut_blind_through` | WARN | Cut >= 1.0 mm (`SHALLOW_DEPTH_MM`) with Distance overshooting the body bbox by more than 0.01 mm (`EXTENT_TOL_MM`) | Through all |
| `twin_extrudes` | WARN | Two extrudes from one profile in opposite directions | Symmetric, or Two sides |
| `sketch_pattern` | WARN | Rectangular or circular pattern constraints in a sketch | Pattern feature (rule 7) |
| `dup_dim_expr` | WARN | Same normalised expression (no whitespace, `-(-X)` → X, outer parens stripped) in two or more dimensions; one line per sketch | horizontal/vertical/collinear for shared coordinates; equal/symmetry for equal lengths |
| `pattern_expr` | WARN | >= 2 dimensions `name*n` / `n*name` on one name with >= 2 distinct integers n (per-instance copies) | One seed + pattern feature (rule 7) |
| `projected_unused` | WARN | Projected curve not used by any constraint or profile | Remove projection (rule 10) |
| `hole_as_extrude` | WARN | Cut extrude of one circle profile (one loop, one circle) | Hole: `holes.hole_simple` / `holes.hole_counterbore` |
| `plane_offset_expr` | WARN | Offset expression divides by 2 AND plane sits midway (±0.01 mm) between two other parallel reference planes with distinct offsets (origin plane at 0 for axis normals; earlier construction planes of the same component). Single origin plane + `/2` alone = no line | Midplane: `features.plane_midplane` |
| `edge_by_edge` | WARN | Consecutive single-edge fillets / chamfers with identical expression | One edge set + tangent chain |
| `thin_as_offset` | WARN | Profile holds 2+ curves of an OffsetConstraint, extrude not thin | Thin extrude |

Static lint (non-blocking, `param_lint.py`, same output form as `BUILD_ONLY_PARAM`): `SKETCH_ARC`, `DIM_EXPR_MATH`, `FIX_POINT`, `THROUGH_GUESS` (WARN).

Opt-out: attribute `skq` / `skq_skip` = `<rule>:<reason>` on the sketch (sketch rules) or feature (feature rules), printed as `SKQ_WARN skq_skip`; lint line `# sketch-lint: <rule> <reason>`. Reason required.

SKQ lines are WARN (advisory), none block Stop.

## Sources

- S1 https://www.onshape.com/en/resource-center/tech-tips/how-to-avoid-3-common-cad-sketching-mistakes
- S2 https://www.reddit.com/r/Onshape/comments/1bfhezx/is_it_better_to_draw_fillets_into_the_sketch/
- S3 https://www.autodesk.com/products/fusion-360/blog/intent-driven-design-guide-autodesk-fusion/
- S4 https://sekkei-tech.com/2026/06/05/catia-sketcher-best-practices/
- S5 https://community.ptc.com/3d-part-assembly-design-327/creo-sketch-mode-best-practices-are-they-documented-129648
- S6 https://help.autodesk.com/cloudhelp/2023/ENU/Inventor-Help/files/GUID-FAF69614-E8F2-4763-975C-552E0BEA1DD1.htm
- S7 https://help.autodesk.com/view/fusion360/ENU/?guid=SLD-EXTRUDE-SOLID
- S8 https://help.autodesk.com/view/fusion360/ENU/?guid=SFC-EXTRUDE
- S9 https://www.engineersrule.com/choose-feature-pattern-sketch-pattern/
- S10 https://community.ptc.com/3d-part-assembly-design-327/make-cut-through-all-the-default-169632
- Fusion help, fully define: https://help.autodesk.com/cloudhelp/ENU/Fusion-Sketch/files/SKT-FULLY-DEFINE-CONSTRAIN-SKETCH.htm
- Autodesk Fix blog (weak source): https://www.autodesk.com/products/fusion-360/blog/mastering-sketch-constraints-autodesk-fusion-tutorial/
