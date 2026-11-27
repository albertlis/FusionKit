# DfAM guidelines (PETG, Fusion 360 / fusion_kit)

## Scope and output

- Assumptions: PETG, 0.4 mm nozzle, 0.2 mm layers. All values in mm unless stated.
- `report(comp)` (`fusion_kit/report.py`) runs `dfam_check` on every body. It appends `dfam=<nF>F/<nW>W` to the summary line and prints one line per finding: `DFAM_FAIL <rule>: <msg>` or `DFAM_WARN <rule>: <msg>`. `report(comp, dfam=False)` disables the check.
- FAIL rules block Stop through `hooks/dfam_guard.py`, once per new FAIL, for scripts that build parts through `fusion_kit.loader.run_part`. Scripts without `run_part` (legacy raw adsk) are warn-only. `dfam_check(body, fail_as_warn=True)` downgrades FAIL to WARN.
- WARN rules are printed only.
- Opt-out: body attribute group `dfam`, name `dfam_skip`, value `<rule>:<reason>[; <rule>:<reason>...]`. Set with `dfam.set_skip(body, {rule: reason})`, read with `dfam.skip_from_body(body)`. A skipped rule that would fire is reported as `DFAM_WARN dfam_skip: <rule> skipped: <reason>`. Only rules in `SKIPPABLE_RULES` (`fusion_kit/dfam.py`) can be skipped; advisory rules cannot. Reason: non-empty, no surrounding spaces, no `;`, no line breaks. Example: `teardrop:no horizontal hole here`.
- Source column: a URL, or `FusionKit default (empirical, configurable)` where no public source matches. Where a public source differs, its value is stated as well.
- Status: rule values not yet verified on a live print.
- Sketch shape rules (fully constrained sketches, extents, SKQ list): [sketch_guidelines.md](sketch_guidelines.md).

## FAIL rules (block Stop)

| Rule | Applies to | Requirement | Helper | Source |
|---|---|---|---|---|
| `teardrop` | Horizontal hole (axis not vertical) | Ø ≤ 8 mm: required peak = min(r + 0.4, r√2); measured peak ≥ 0.98 × req and ≤ 1.5 × req (full 60° cone on a small hole FAILs). Shape: flat-topped notch (top at r + 0.4, 45° flanks, apex clipped flat) where r + 0.4 < r√2 (Ø > ~1.93 mm); full 45° point otherwise. Ø > 8 mm: full 45° point, peak ≥ 0.98 × r√2. Constants `TEARDROP_NOTCH_MAX_D_MM` 8.0, `TEARDROP_NOTCH_MM` 0.4, `TEARDROP_NOTCH_MAX_RATIO` 1.5; required peak = `dfam.teardrop_required_peak_mm(r)`. | `holes.teardrop_hole` | FusionKit default (empirical, configurable). Public sources differ: 30° roof from horizontal in PLA community test (https://makerworld.com/en/models/2085522-teardrop-hole-tests); 45° common rule (https://www.luisllamas.es/en/3d-printing-design-45-degree-rule-no-supports/); size threshold 8–10 mm (https://www.dfamacademy.com/guides/fdm-overhang-angles), not implemented here. |
| `seam_notch` | Vertical hole | V notch 0.3 mm. Notch direction parameter, default +Y (`dir_deg=90`). | `holes.seam_notch` | FusionKit default (empirical, configurable). No public source found. |
| `bed_chamfer` | Edge on bed face | Chamfer ≥ 1.0 mm, size = BED footprint (planar and cone; exact at any interior dihedral via bed_footprint_mm; wall leg ignored), limit = `bed_leg_min_mm(req)` = req / tan 46° − 0.02 mm (`BED_CHAMFER_TOL_MM`): ≥ 0.946 mm bed leg for 1 mm wall; steep chamfer (bed 0.2 mm, wall 1.0 mm) FAILs; 46° 1 mm (bed 0.966) and 45° 1 mm pass; ≥ 0.5 mm when bed edge / wall room < 3 mm; < 0.4 mm always FAIL. Fillet or sharp edge FAIL; sharp edge < 3.0 mm (`BED_SHORT_EDGE_MM`, `EdgeFact.length_mm`) WARN instead, because `bed_chamfer(min_edge_mm=3.0)` skips such edges on purpose (chamfer would leave a ~0-footprint sliver; elephant foot over < 3 mm negligible); length unknown (None) = FAIL. Face 46° from horizontal (`bed_chamfer` default `angle_deg=46`; bed footprint = 1 mm / tan 46° ≈ 0.966 mm at any dihedral (builder scales wall leg by 1/bed_wall_ratio)), not 45°: 45° gives n_z = 0.7071 > 0.707 overhang limit; 44° gives n_z = 0.719 and also fails. | `features.bed_chamfer` | FusionKit default (empirical, configurable). Public sources differ: 0.2 mm elephant-foot compensation (https://help.prusa3d.com/article/elephant-foot-compensation_114487); 0.2–0.4 mm in a community blog (URL not recorded). 45° is the common chamfer angle; 46° face (|n_z| 0.695) is a FusionKit choice. |
| `warp_grooves` | Bed face with side > 35 mm, no grooves | Grooves 0.8 mm wide × 0.6 mm deep, pitch 30 mm. Pitch is a helper default and is not checked. | `features.warp_relief_grooves` | FusionKit default (empirical, configurable). Only anecdotal public source: https://forum.bambulab.com/t/tips-warping-of-large-flat-piece/151789. No measured source found. |
| `vertical_corner` | Vertical (Z-parallel) edges | Sharp corner FAIL. Fillet < 3.0 mm FAIL (use chamfer). Chamfer of any size, or fillet ≥ 3.0 mm, passes. | `features.fillet` / `features.chamfer` | FusionKit default (empirical, configurable) for 3.0 mm. Concept: rounded corners distribute stress (https://support.ultimaker.com/s/article/1667337954399). |
| `joint_chamfer` | Mating edges of dovetail / slot joints | Sharp edge FAIL. Chamfer < 0.3 mm FAIL. | `joints.dovetail` (`edge_chamfer_mm=0.3`) | FusionKit default (empirical, configurable) for 0.3 mm. Clearance 0.20 mm per side (`clearance_mm=0.2`, not checked by any rule): https://grandpacad.com/en/tools/tolerance-fit-calculator (+0.40 mm diameter = 0.20 mm per side). |
| `pin_base_chamfer` | Concave base of a convex pin / boss: circular edge, convex cylinder meets plane normal to its axis. Pin tip rim and hole walls excluded (not pin bases). Exempt: grip fin edges (`on_grip_fin`, fin root at the shaft); base on a slit floor <= `SLIT_MAX_MM` 0.2 mm wide (`slit_width_mm` = annulus floor width, `annulus_width_mm`: two concentric circles, r1 - r2; slit inner wall is not a pin). | Sharp base FAIL. Chamfer or fillet < 0.3 mm FAIL (`PIN_BASE_CHAMFER_MIN_MM`). Adapter sees sharp base only: chamfer / fillet at base not measured (tangent / cone seams skipped), so only the sharp case is live-checked. | - | FusionKit default (empirical, configurable) for 0.3 mm, user rule 2026-11-17 (stress build-up at pin base). No public source recorded. |
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
| `grip_fin_sliver` | Press-fit grip fins (`grip_fit`) | FAIL when the min fin tangential width over the lead-in band (tip .. lead-in outer radius) is < 0.4 mm (knife edge at entry). Value from body attr `dfam` / `grip_fin_min_section`, written by `grip_fit`. Skippable. | `grip_fit.fin_section_min_mm` | FusionKit default (empirical, 0.4 mm = `GRIP_FIN_SECTION_MIN_MM`). |

## Docs-only (no code rule)

| Rule | Applies to | Requirement | Helper | Source |
|---|---|---|---|---|
| `hex_hole` | Hex hole | Vertex up; alternative to teardrop. | - | FusionKit default (empirical, configurable). |
| `counterbore_bridge` | Counterbore step | Bridge of 2 layers; alternative to sacrificial layer. | - | Concept: https://github.com/Finn2708/CounterboreBridging |

## Press-fit grip fins (code rule: grip_fin_sliver)

Helper: `features` grip fit (`fusion_kit/grip_fit.py`). Use only for a vertical axis (fins are vertical walls, print Z = part axis, no supports).

- Use grip fit when the printer tolerance is not trusted enough for a plain press fit (plain bore/peg = nominal ± print error). Fins give interference that bends instead of crushing the bearing.
- Kind `hole`: bore for an outer race; fins on the wall, tips at D/2 - interference. Kind `shaft`: peg for an inner race; fins from the core, tips at d/2 + interference.
- Relief: radial pocket behind each fin (default 1.2 mm). Fins bend into it; without relief the fin is a rigid rib (preload, see `crush_ribs`).
- Lean: fin angle from radial (default 45 deg). Lean lets the fin bend tangentially; lean 0 = rigid rib.
- Fin thickness: vertical-wall rules conflict. 0.8 mm is inside the `petg_wall` FAIL-free band (<= 0.9) but below `min_wall` (1.0 mm FAIL). 1.0-1.6 mm FAILs `petg_wall`. >= 1.6 mm passes both but is stiff. Live smoke (0.8 mm, 16 fins): `min_wall` did NOT fire; `bed_chamfer` FAILs on fin feet (sharp bed edges at z = grip start) and `vertical_corner` FAILs on fin vertical corners (sharp, 4 per fin). Both need a chamfer on the fin foot / corners, or a `dfam_skip` with reason; neither is applied by the helper.
- Exemption (code): fin side, tip and axial end-cap faces carry attribute group `dfam`, name `grip_fin` (end caps: `grip_fit.is_grip_fin_end_face`, planar normal to the grip axis in the fin band and zone; the far-end cap meets the peg at a `pin_base` edge). An edge is exempt from `vertical_corner`, `bed_chamfer` and `pin_base_chamfer` when at least one adjacent face carries it (`EdgeFact.on_grip_fin`, `dfam.GRIP_FIN_ATTR`). Kinds `vertical`, `bed`, `pin_base`; `joint_chamfer` (mating) and all other rules unchanged. Tags are written at build time: live model needs a rebuild to pick up end-cap tags. Why: a 0.8 mm flexing fin cannot take a 3 mm fillet or a 1 mm bed chamfer leg; the radial tip is handled by the lead-in cone, fin side bed edges stay sharp by design. Thickness is not measured (`min_wall_mm` None), so a thickness-based exemption is impossible; the face tag is the only key.
- Interference tuning: default 0.6 mm radial. Too little = loose; too much = fin root stress / bearing crack. Tune by a printed test piece, not by the model.
- Fin count default 16. Pattern is seed + `pattern_circular`; fins joined once with `combine`.
- Root embedded in solid beyond the relief edge (overlap 0.6 mm default) so fins fuse with the wall/core.
- Fin span = full zone: fins start at zone start (entry low) / end at entry face start + depth (entry high). Fin entry end is ramped (`fin_ramp_corners` revolve cut on fin bodies only, root at entry face): cone at 46 deg from horizontal, tip starts later (entry low) / ends earlier (entry high) by (relief + 0.6 + interference) * tan46. No flat down-facing fin foot, no knife-edge flap. Lead-in cone clips fin tips only when lead_in + 0.5 > tan46 * (relief + 0.6 + interference) (~1.99 mm at defaults); the solid-reach guard (lead_in > interference + relief) fires earlier, so that cut can leave a cone face on the fin tip (not verified live). Not yet checked by live `check_body` (no Fusion in this run).
- `grip_fin_sliver` (code): `grip_fit` writes the analytic min fin tangential width over the lead-in band to body attr `dfam` / `grip_fin_min_section` (mm, 4 dp; keeps the min over several fits). Rule FAILs below 0.4 mm. Lean must be a literal angle for the write (user-parameter lean: skipped, no attr, no check).
- Status: rule values not yet verified on a live print.

## Peg anchoring (docs-only, no code rule)

Printed peg standing on a plate (peg axis = print Z): layers are perpendicular to the peg, so a bending load peels them at the root. Root = weakest point.

- A peg merely unioned onto the plate face is bonded only by the plate's top skin. Anchor it.
- Anchor: continue the peg (or its shoulder, whichever is wider) DOWN into the plate, separated from the plate by a thin annular slit (default 0.1 mm, parameter). Slicer prints the anchor's own perimeters down through the plate, fused into infill / perimeters on both sides of the slit, so the peg shell is continuous from deep in the plate to the tip.
- Anchor depth = min(peg_length, plate_thickness - floor_skin), floor_skin default 0.8 mm (4 layers at 0.2 mm). Never past the far plate face minus the floor skin.
- Slit open at the plate surface the peg grows from (no ceiling, no overhang). Use the wider of peg / shoulder diameter as the anchor so the slit is not covered by the shoulder. Slot ceiling <= 0.2 mm between facing walls is not overhang in `printability.check` (`SLIT_MAX_MM`); wider bridges still are.
- Short bridge: flat down-facing B-rep face (planar; 1 loop, or concentric annulus) whose shortest in-plane span <= 1.0 mm (`SHORT_BRIDGE_MAX_MM`) is not overhang ONLY if supported: both long sides (edges at both ends of the min-width span) border a face going below the bridge plane (bbox minZ < bridge z - 0.1 mm); annulus: every edge of both loops supported. Cantilever (unsupported) stays flagged. PETG bridges <= 1 mm (user P25, 2026-11-17). Wider bridges and wide rings (> 1 mm radial) still are (`_brep_short_bridge`). Short cantilever (P26, 2026-11-17): flat down-facing 1-loop face with reach <= 1.0 mm (`CANTILEVER_MAX_MM`) from one supported edge's line is not overhang (`_brep_short_cantilever`); longer reach or no supported edge still is.
- Root transition: shoulder or chamfer, no sharp inner corner.
- Peg core after any grip-fit relief: >= 5-6 mm section. Below that, fins weaken the peg more than the anchor helps.
- Slit < slicer slice closing radius (Prusa default 0.049 mm) gets closed. Keep slit >= 0.1 mm.
- Implementation pattern: one extrude cut of a ring (two concentric circles, one profile) from the peg face down by the depth expression. Multiple pegs: seed + mirror / pattern.
- Status: rule values not yet verified on a live print. No code rule; no helper.

## Values not checked by code

- Warp groove pitch (30 mm) and depth: helper defaults only. The rule fires when a bed side is > 35 mm and the bed shows fewer than 2 parallel grooves; a gap counts as a groove only when its width is within ±25 % of `WARP_GROOVE_WIDTH_MM` (0.8 mm → 0.6–1.0 mm), or as a slot-like inner loop (aspect > 4).
- `JOINT_CLEARANCE_MM` (0.2): exported, used by no rule. Clearance is a `joints.dovetail` parameter.
- `first_layer` text case: guidance only.
- Teardrop size threshold (8–10 mm in the cited guide): not implemented; every horizontal hole is checked.
- Warp-groove exemption (`dfam.in_warp_groove`, shared predicate): a bed rim edge (`bed_chamfer`) or vertical corner (`vertical_corner`) lying in a warp-groove footprint (gap width 0.6–1.0 mm, same ±25 % as above; blind slot loops too) at z ≤ bed + 0.5 mm is not FAIL. Recess-gap strip ends extend to the body footprint only when within `WARP_END_INSET_MAX_MM` (2.0 mm) of it (plate edge / bed chamfer zone); a collinear notch 10 mm past a strip end stays FAIL. Only a qualifying family counts: ≥ 2 parallel grooves on the bed (`_warp_family`). A lone slot or a single gap / two pads 0.8 mm apart never exempt. A 0.8 mm groove cannot take a 1 mm chamfer or 3 mm fillet. Outer corners and edges above the band still FAIL. Not checked by any rule: groove depth 0.3 mm, pitch 30 mm.
- Groove ceiling exemption (`printability.check(exempt_rects=...)`, default None = none; `check_body` passes `dfam.warp_family_mm(body)` for identity orientation only; `sweep` passes it to the identity candidate only): a down-facing triangle with all 3 vertices inside ONE qualifying groove rect (`dfam.in_groove_rect`: z ≤ bed + 0.5 mm, rect width 0.6–1.0 mm, footprint grown by 0.05 mm) is not an overhang. Triangle bbox is never used. Rotated orientations keep the groove ceiling as overhang (rects are design frame). Hanging-edge and floating-point checks are not changed by this exemption.

## Warp groove order (feature_quality, WARN `warp_not_last`)

- Grooves last: warp relief grooves are the last features of a body, after all features incl. bed chamfer. Any later fillet / chamfer / extrude / revolve / combine / pattern / mirror on that body WARNs at that feature. Groove = `WARP_GROOVE_ATTR` tag (`features.warp_relief_grooves`), fallback name contains `warp`.

## Bed orientation (print frame)

- Body attribute `("dfam", "bed")`: `-z` (absent = default), `+z`, `-x`, `+x`, `-y`, `+y`. `+x` = bed is the X-max face (outward normal +X). Written by `features.bed_chamfer(bed=)` and `features.warp_relief_grooves(bed=)` via `dfam.set_bed`; read by `dfam.bed_from_body`.
- Rules run in PRINT frame: `dfam.print_frame_body(body)` makes a TEMPORARY copy (`TemporaryBRepManager.copy`), rotates the bed face onto -Z (`dfam.bed_rotation_axes`, `bed_to_minus_z_matrix`) and shifts min Z to 0. `-z` bodies are used as they are (no copy). The original body is never changed.
- Findings on a non-`-z` body end with `[print frame, bed <dir>]`; edge/hole positions in those messages are print-frame coordinates, not model coordinates.
- Limits, measured live: temporary body faces take no attributes (`_has_attr` returns False there). Tags are carried as data: `print_frame_with_tags` maps each tagged original face to its copy face (live: `TemporaryBRepManager.copy` keeps face order, 10/10 faces matched by index; `match_faces` takes the same index when the signature agrees, else the unique copy face, signature = vertex centroid + area within 1e-4 cm). `dfam_check` adds `WARN bed_frame` only for tagged faces that did not map. Temporary body `physicalProperties.volume` reads 0: use topology / bbox, not volume, on the copy.
- `printability.check_body` uses the same print frame; `up_rot` is applied on top of it. Groove-ceiling exemption (`warp_family_mm`) applies only with identity `up_rot`, as before.

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
