# DfAM guidelines (PETG, Fusion 360 / fusion_kit)

## Scope and output

- Assumptions: PETG, 0.4 mm nozzle, 0.2 mm layers. All values in mm unless stated.
- `report(comp)` (`fusion_kit/report.py`) runs `dfam_check` on every body. It appends `dfam=<nF>F/<nW>W` to the summary line and prints one line per finding: `DFAM_FAIL <rule>: <msg>` or `DFAM_WARN <rule>: <msg>`. `report(comp, dfam=False)` disables the check.
- FAIL rules block Stop through `hooks/dfam_guard.py`, once per new FAIL, for scripts that build parts through `fusion_kit.loader.run_part`. Scripts without `run_part` (legacy raw adsk) are warn-only. `dfam_check(body, fail_as_warn=True)` downgrades FAIL to WARN.
- WARN rules are printed only.
- Opt-out: body attribute group `dfam`, name `dfam_skip`, value `<rule>:<reason>[; <rule>:<reason>...]`. Set with `dfam.set_skip(body, {rule: reason})`, read with `dfam.skip_from_body(body)`. A skipped rule that would fire is reported as `DFAM_WARN dfam_skip: <rule> skipped: <reason>`. Only rules in `SKIPPABLE_RULES` (`fusion_kit/dfam.py`) can be skipped; advisory rules cannot. Reason: non-empty, no surrounding spaces, no `;`, no line breaks. Example: `teardrop:no horizontal hole here`.
- Source column: a URL, or `FusionKit default (empirical, configurable)` where no public source matches. Where a public source differs, its value is stated as well.
- Status: rule values not yet verified on a live print.

## FAIL rules (block Stop)

| Rule | Applies to | Requirement | Helper | Source |
|---|---|---|---|---|
| `teardrop` | Horizontal hole (axis not vertical) | Teardrop peak ≥ 0.98 × 1.155 r (r / sin 60°; roof 30° from horizontal). Applies to every horizontal hole; no size threshold. A 45° roof would need 1.414 r. | `holes.teardrop_hole` | FusionKit default (empirical, configurable). Public sources differ: 30° roof from horizontal in PLA community test (https://makerworld.com/en/models/2085522-teardrop-hole-tests); 45° common rule (https://www.luisllamas.es/en/3d-printing-design-45-degree-rule-no-supports/); size threshold 8–10 mm (https://www.dfamacademy.com/guides/fdm-overhang-angles), not implemented here. |
| `seam_notch` | Vertical hole | V notch 0.3 mm. Notch direction parameter, default +Y (`dir_deg=90`). | `holes.seam_notch` | FusionKit default (empirical, configurable). No public source found. |
| `bed_chamfer` | Edge on bed face | Chamfer ≥ 1.0 mm, size = BED leg only (planar and cone; wall leg ignored), limit = `bed_leg_min_mm(req)` = req / tan 46° − 0.02 mm (`BED_CHAMFER_TOL_MM`): ≥ 0.946 mm bed leg for 1 mm wall; steep chamfer (bed 0.2 mm, wall 1.0 mm) FAILs; 46° 1 mm (bed 0.966) and 45° 1 mm pass; ≥ 0.5 mm when bed edge / wall room < 3 mm; < 0.4 mm always FAIL. Fillet or sharp edge FAIL. Face 46° from horizontal (`bed_chamfer` default `angle_deg=46`; bed leg = wall leg / tan 46° ≈ 0.966 × wall leg), not 45°: 45° gives n_z = 0.7071 > 0.707 overhang limit; 44° gives n_z = 0.719 and also fails. | `features.bed_chamfer` | FusionKit default (empirical, configurable). Public sources differ: 0.2 mm elephant-foot compensation (https://help.prusa3d.com/article/elephant-foot-compensation_114487); 0.2–0.4 mm in a community blog (URL not recorded). 45° is the common chamfer angle; 46° face (|n_z| 0.695) is a FusionKit choice. |
| `warp_grooves` | Bed face with side > 35 mm, no grooves | Grooves 0.8 mm wide × 0.3 mm deep, pitch 30 mm. Pitch is a helper default and is not checked. | `features.warp_relief_grooves` | FusionKit default (empirical, configurable). Only anecdotal public source: https://forum.bambulab.com/t/tips-warping-of-large-flat-piece/151789. No measured source found. |
| `vertical_corner` | Vertical (Z-parallel) edges | Sharp corner FAIL. Fillet < 3.0 mm FAIL (use chamfer). Chamfer of any size, or fillet ≥ 3.0 mm, passes. | `features.fillet` / `features.chamfer` | FusionKit default (empirical, configurable) for 3.0 mm. Concept: rounded corners distribute stress (https://support.ultimaker.com/s/article/1667337954399). |
| `joint_chamfer` | Mating edges of dovetail / slot joints | Sharp edge FAIL. Chamfer < 0.3 mm FAIL. | `joints.dovetail` (`edge_chamfer_mm=0.3`) | FusionKit default (empirical, configurable) for 0.3 mm. Clearance 0.20 mm per side (`clearance_mm=0.2`, not checked by any rule): https://grandpacad.com/en/tools/tolerance-fit-calculator (+0.40 mm diameter = 0.20 mm per side). |
| `min_wall` | Any wall | Wall < 1.0 mm FAIL. | - | PETG minimum 1.0 mm, recommended 1.6 mm: https://www.rigidprints.co.uk/design-guide/wall-thickness |
| `petg_wall` | Vertical walls (only vertical walls are measured) | Wall strictly between 0.9 and 1.6 mm FAIL: two-perimeter band (2 × 0.45 mm line width). | - | Upper 1.6 mm: https://www.rigidprints.co.uk/design-guide/wall-thickness. Lower: 0.86 mm for two perimeters (https://help.prusa3d.com/article/layers-and-perimeters_1748); FusionKit uses 0.9 (close match). |
| `first_layer` | Holes / details on bed face | Bed hole diameter < 2.0 mm FAIL. Text on bed face: guidance only, not checked. Fix: recess 0.2–0.4 mm deep, or move to side. | - | FusionKit default (empirical, configurable) for 2.0 mm and recess depth. First layer 0.20 mm: https://help.prusa3d.com/article/layers-and-perimeters_1748 |
| `counterbore_down` | Counterbore step facing the bed | Unsupported bed step FAIL. Fix: sacrificial layer (0.2 mm) or stepped bridge. | `holes.sacrificial_depth` (`layer_mm=0.2`) | Concept: sacrificial layer-high cuts (https://github.com/Finn2708/CounterboreBridging). 0.2 mm PETG layer: https://www.sovol3d.com/blogs/news/essential-petg-print-settings |

## WARN rules (printed only)

| Rule | Applies to | Requirement | Helper | Source |
|---|---|---|---|---|
| `slender_fin` | Height / min footprint side > 4.0 | Fin 0.8–1.2 mm thick, gap 0.5–1.0 mm, tines 0.2h × 0.4w, taper to model. | - | FusionKit default (empirical, configurable). |
| `relief_slot` | Side screw hole without relief | 1 mm slots on both sides. | - | FusionKit default (empirical, configurable). |
| `cavity` | Pockets or enclosed voids (`BodyFact.has_cavity`) | Avoid. If unavoidable: chunky walls, infill 15–25%, verify escape hole / support. | - | FusionKit default (empirical, configurable). |

## Advisory rules (reference only)

The seven `ADVISORY` entries in `fusion_kit/dfam.py` are not emitted by `report()` or `check_all()`: no DFAM line is printed for them. They are checked only through the listed helper, where one exists.

| Rule | Applies to | Requirement | Helper | Source |
|---|---|---|---|---|
| `orient_45` | Overhangs in print orientation | Max 45° overhang in print orientation (`up_rot`). Box on edge: 45° plus 1–2 mm foot chamfer. | `printability.check_body(min_angle_deg=45)` | 45° common rule: https://www.luisllamas.es/en/3d-printing-design-45-degree-rule-no-supports/ |
| `fin_support` | Flat overhang or tall slender fin | Support rows 5–8 mm under; top 0.2 mm below surface. | - | FusionKit default (empirical, configurable). |
| `micro_feature` | Round holes ~2 mm | Ring of 0.5–1 mm slots @ 2 mm. | - | FusionKit default (empirical, configurable). |
| `thread_flat` | Threads | Trapezoid or rounded profile; flat cut at top and bottom, horizontal. | - | FusionKit default (empirical, configurable). |
| `crush_ribs` | Grip fins / crush ribs | Slit pins and end chamfer. Check fit and print direction. | - | FusionKit default (empirical, configurable). |
| `side_slot` | Side slots | 0.4–0.6 mm under surface. Check support and orientation. | - | FusionKit default (empirical, configurable). |
| `texture` | Fuzzy skin / surface texture | Surface only; no load-bearing features. | - | FusionKit default (empirical, configurable). |

## Docs-only (no code rule)

| Rule | Applies to | Requirement | Helper | Source |
|---|---|---|---|---|
| `hex_hole` | Hex hole | Vertex up; alternative to teardrop. | - | FusionKit default (empirical, configurable). |
| `counterbore_bridge` | Counterbore step | Bridge of 2 layers; alternative to sacrificial layer. | - | Concept: https://github.com/Finn2708/CounterboreBridging |

## Values not checked by code

- Warp groove pitch (30 mm) and depth: helper defaults only. The rule fires when a bed side is > 35 mm and the bed shows fewer than 2 parallel grooves; a gap counts as a groove only when its width is within ±25 % of `WARP_GROOVE_WIDTH_MM` (0.8 mm → 0.6–1.0 mm), or as a slot-like inner loop (aspect > 4).
- `JOINT_CLEARANCE_MM` (0.2): exported, used by no rule. Clearance is a `joints.dovetail` parameter.
- `first_layer` text case: guidance only.
- Teardrop size threshold (8–10 mm in the cited guide): not implemented; every horizontal hole is checked.
- Warp-groove exemption (`dfam.in_warp_groove`, shared predicate): a bed rim edge (`bed_chamfer`) or vertical corner (`vertical_corner`) lying in a warp-groove footprint (gap width 0.6–1.0 mm, same ±25 % as above; blind slot loops too) at z ≤ bed + 0.5 mm is not FAIL. Recess-gap strip ends extend to the body footprint only when within `WARP_END_INSET_MAX_MM` (2.0 mm) of it (plate edge / bed chamfer zone); a collinear notch 10 mm past a strip end stays FAIL. Only a qualifying family counts: ≥ 2 parallel grooves on the bed (`_warp_family`). A lone slot or a single gap / two pads 0.8 mm apart never exempt. A 0.8 mm groove cannot take a 1 mm chamfer or 3 mm fillet. Outer corners and edges above the band still FAIL. Not checked by any rule: groove depth 0.3 mm, pitch 30 mm.
- Groove ceiling exemption (`printability.check(exempt_rects=...)`, default None = none; `check_body` passes `dfam.warp_family_mm(body)` for identity orientation only; `sweep` passes it to the identity candidate only): a down-facing triangle with all 3 vertices inside ONE qualifying groove rect (`dfam.in_groove_rect`: z ≤ bed + 0.5 mm, rect width 0.6–1.0 mm, footprint grown by 0.05 mm) is not an overhang. Triangle bbox is never used. Rotated orientations keep the groove ceiling as overhang (rects are design frame). Hanging-edge and floating-point checks are not changed by this exemption.

## Sources

1. https://makerworld.com/en/models/2085522-teardrop-hole-tests: PLA community teardrop test, 30° roof.
2. https://www.luisllamas.es/en/3d-printing-design-45-degree-rule-no-supports/: 45° common overhang rule.
3. https://www.dfamacademy.com/guides/fdm-overhang-angles: teardrop size threshold 8–10 mm (concept).
4. https://help.prusa3d.com/article/elephant-foot-compensation_114487: elephant-foot compensation 0.2 mm.
5. https://forum.bambulab.com/t/tips-warping-of-large-flat-piece/151789: warp relief (anecdotal).
6. https://support.ultimaker.com/s/article/1667337954399: rounded vertical corners (concept).
7. https://grandpacad.com/en/tools/tolerance-fit-calculator: clearance 0.20 mm per side.
8. https://www.rigidprints.co.uk/design-guide/wall-thickness: PETG minimum wall 1.0 mm, recommended 1.6 mm.
9. https://help.prusa3d.com/article/layers-and-perimeters_1748: 0.20 mm first layer; two perimeters ≈ 0.86 mm.
10. https://github.com/Finn2708/CounterboreBridging: counterbore sacrificial layer (concept).
11. https://www.sovol3d.com/blogs/news/essential-petg-print-settings: 0.2 mm PETG layer.
