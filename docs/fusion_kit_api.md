# fusion_kit API index

Before raw adsk in execute/parts: find helper here. Missing → raw adsk in `parts/helpers.py` + open a GitHub issue using the *Missing helper* template.
Always run via `fusion_kit.loader.run_part(entry)`, never `import fusion_kit` in execute code.
Column 3 = raw adsk tokens the helper wraps (empty = none). Read by `hooks/fusion_kit_nudge.py`.
Rows = every public module-level def (no leading `_`). Enforced by `tests/test_api_index_offline.py`.

## build_check
| helper | signature | replaces raw adsk |
|---|---|---|
| build_check.build_and_check | build_and_check(entry_path, *, print_frames=None, interference=True, screenshot=None) — run_part(entry, "build") with stdout captured (printed only on error: last 20 lines, exception re-raised) + report_data (dfam=False) + dfam + check_body per body (print_frames {comp name: Rot}, default IDENTITY) + interference over root occurrences + save_screenshot; prints and returns compact keyword lines (one per body, summary, max 3 detail lines per body) | |
| build_check.format_check | format_check(bodies, summary) -> compact text from BodyCheck / Summary. Pure | |

## component
| helper | signature | replaces raw adsk |
|---|---|---|
| component.new_comp | new_comp(root, name) | |
| component.new_occ | new_occ(root, name) | `addNewComponent` |

## dfam
| helper | signature | replaces raw adsk |
|---|---|---|
| dfam.teardrop_peak_mm | teardrop_peak_mm(r_mm, wall_deg) | |
| dfam.check_holes | check_holes(holes) | |
| dfam.check_edges | check_edges(edges) | |
| dfam.check_body_facts | check_body_facts(body_fact) | |
| dfam.check_all | check_all(body, holes, edges, skip) | |
| dfam.summarize_findings | summarize_findings(findings) | |
| dfam.parse_skip | parse_skip(value) | |
| dfam.format_skip | format_skip(rules) | |
| dfam.set_skip | set_skip(body, rules) | `attributes.add` |
| dfam.facts_from_body | facts_from_body(body, tags=None) — tags = print-frame tempId sets from print_frame_with_tags; None = read attrs from body | `Cylinder.cast`, `Plane.cast`, `Line3D.cast` |
| dfam.skip_from_body | skip_from_body(body) | `attributes.itemByName` |
| dfam.dfam_check | dfam_check(body, fail_as_warn=False) | |
| dfam.chamfer_leg_mm | chamfer_leg_mm(width_mm, n_chamfer, n_adjacent) | |
| dfam.bed_leg_min_mm | bed_leg_min_mm(req_mm) | |
| dfam.bed_wall_ratio | bed_wall_ratio(phi_deg, angle_deg) -> bed leg / wall leg = sin(phi)/tan(angle)+cos(phi). Pure | |
| dfam.bed_footprint_mm | bed_footprint_mm(width_mm, alpha_deg, phi_deg) -> bed footprint of a chamfer face = w*sin(phi+alpha)/sin(phi). Pure | |
| dfam.is_full_cylinder | is_full_cylinder(span_deg) | |
| dfam.in_warp_groove | in_warp_groove(point_mm, grooves, bed_z_mm) | |
| dfam.in_groove_rect | in_groove_rect(point_mm, r, bed_z_mm) | |
| dfam.warp_family_mm | warp_family_mm(body) | |
| dfam.bed_axis | bed_axis(bed) -> (axis index 0/1/2 for x/y/z, outward sign); ValueError unless bed in -z, +z, -x, +x, -y, +y | |
| dfam.bed_from_body | bed_from_body(body) — stored bed, absent = "-z" | `attributes.itemByName` |
| dfam.set_bed | set_bed(body, bed) — validates via bed_axis, writes ("dfam", "bed") | `attributes.add` |
| dfam.bed_rotation_axes | bed_rotation_axes(bed) -> images of x, y, z under the rotation putting the bed face on -Z (proper, det +1); "-z" = identity. Pure | |
| dfam.bed_to_minus_z_matrix | bed_to_minus_z_matrix(bed) -> Matrix3D (rotation, origin 0) for TemporaryBRepManager.transform | `Matrix3D.setWithCoordinateSystem`, `Vector3D.create` (via _frame_matrix) |
| dfam.print_frame_body | print_frame_body(body) -> body itself for bed "-z"; else TEMPORARY copy rotated to bed -> -Z and shifted to min Z = 0 (= print_frame_with_tags(body)[0]). Original untouched; attrs (dfam/bed, dfam_skip) must be read from the ORIGINAL | `TemporaryBRepManager.get`, `copy`, `transform` |
| dfam.annulus_width_mm | annulus_width_mm(face) -> width mm of a planar 2-loop circle annulus (|r1 - r2|, shared centre); None if not an annulus. Used for pin_base slit floor (<= SLIT_MAX_MM 0.2) and printability slit ceiling | `BRepFace.loops`, `BRepEdge.geometry` (center/radius duck-typed via _circle_of) |
| dfam.print_frame_with_tags | print_frame_with_tags(body) -> (print-frame body, tags, unmapped). tags = {"mating": frozenset, "grip_fin": frozenset} of COPY tempIds, from original tagged faces matched by match_faces; None for bed "-z". unmapped = ["<tag> face <i>"] for tagged faces that did not map | `TemporaryBRepManager.get`, `copy`, `transform`, `attributes.itemByName` (via _has_attr) |
| dfam.rotate_point | rotate_point(p, axes) -> image of point p under rotation with columns axes (bed_rotation_axes). Pure | |
| dfam.match_faces | match_faces(orig, copy, tol=SIG_TOL_CM) -> {orig index: copy index}; face signature = (vertex centroid cm, area cm2), equal within tol; same index preferred, unique unused match otherwise, ambiguous = unmapped. Pure | |

## expr
| helper | signature | replaces raw adsk |
|---|---|---|
| expr.trig | trig(fn, angle) | |
| expr.deg | deg(d) | |
| expr.near | near(a, b) | |
| expr.as_str | as_str(x) | |
| expr.evaluate | evaluate(expr, names) | |
| expr.derive | derive(spec, names) | |

## features
| helper | signature | replaces raw adsk |
|---|---|---|
| features.collection | collection(items) | `ObjectCollection` |
| features.profile | profile(sketch, outer=True) | `profileLoops`, `areaProperties` |
| features.profiles | profiles(sketch, predicate=None) | |
| features.extrude | extrude(comp, prof, distance, op="new", ..., label) | `extrudeFeatures`, `setSymmetricExtent`, `setOneSideExtent` |
| features.revolve | revolve(comp, prof, axis, op, angle, *, bodies=None, label) | `revolveFeatures`, `setAngleExtent` |
| features.pattern_circular | pattern_circular(comp, entities, axis, qty, total, label) | `circularPatternFeatures` |
| features.pattern_rectangular | pattern_rectangular(comp, entities, axis, qty, distance, distance_type, label) | `rectangularPatternFeatures`, `setDirectionTwo` |
| features.axis_plane | axis_plane(comp, axis, expr, value_mm, label) | `setByOffset` |
| features.pattern_grid | pattern_grid(comp, entities, dir_one, qty_one, spacing_one, dir_two, qty_two, spacing_two, label) | `rectangularPatternFeatures`, `setDirectionTwo` |
| features.combine | combine(comp, target, tools, op, keep_tools, label) — tools deduped by entityToken, target dropped; ValueError if none left | `combineFeatures` |
| features.move_body | move_body(comp, bodies, dx, dy, dz, label) | `moveFeatures`, `defineAsTranslateXYZ` |
| features.rotate_body | rotate_body(comp, bodies, axis, angle, label) | `moveFeatures`, `defineAsRotate` |
| features.single_body | single_body(comp, prefix="") | |
| features.axis_y_through | axis_y_through(comp, x, z, label) | `setByTwoPlanes` |
| features.axis_x_through | axis_x_through(comp, y, z, label) | `setByTwoPlanes` |
| features.rotate_body_about_y | rotate_body_about_y(comp, bodies, x, z, angle, label) | |
| features.rotate_body_about_x | rotate_body_about_x(comp, bodies, y, z, angle, label) | |
| features.face_normal | face_normal(face, point=None) | `getNormalAtPoint` |
| features.faces_by_normal | faces_by_normal(body, normal, min_dot=0.6) | |
| features.convex_edges | convex_edges(body, edges, probe_mm=0.1) | `pointContainment` |
| features.edges_at_xy | edges_at_xy(body, x_mm, y_mm, tol_mm=0.05) | `edges`, `boundingBox` |
| features.offset_plane | offset_plane(comp, base, distance, label) | `setByOffset` |
| features.planar_faces | planar_faces(body, axis, at_mm, normal_sign=None, tol_mm=0.01) | |
| features.fillet | fillet(comp, edges, radius, tangent_chain, label) | `filletFeatures`, `addConstantRadiusEdgeSet` |
| features.chamfer | chamfer(comp, edges, distance, tangent_chain, label, distance2=None) | `chamferFeatures`, `addEqualDistanceChamferEdgeSet`, `addTwoDistancesChamferEdgeSet` |
| features.chamfer_greedy | chamfer_greedy(comp, edges, distance, *, label, min_len_mm=0.0) — probe each edge alone (RuntimeError = refused, shorter than min_len_mm skipped), ONE chamfer over accepted; None if none | |
| features.mirror | mirror(comp, feats, plane, compute, label) | `mirrorFeatures` |
| features.mirror_bodies | mirror_bodies(comp, bodies, plane, label) — returns mirrored bodies | `mirrorFeatures`, `createInput`, `bodies` |
| features.bed_chamfer | bed_chamfer(comp, body, distance, label, only_largest_bed_face=False, angle_deg=46.0, bed="-z", min_edge_mm=0.0, inner_loops=False) — bed = bed face dir ("-z" min Z default, "+x" X-max, ...); stored via set_bed; one two-distance edge set per interior-dihedral group: `distance` = 90-deg-equivalent wall leg, bed footprint = distance / tan(angle_deg) at any phi, wall leg = distance * bed_wall_ratio(90)/bed_wall_ratio(phi) (105 = dovetail wall); `angle_deg` = chamfer face angle FROM HORIZONTAL, must be in (45, 90) exclusive else ValueError (46 -> n_z 0.695; 45 FAILS). isFlipped tried False then True; accept only if face angle acos(\|n_z\|) = angle_deg ±0.5 and \|n_z\| < 0.707 (Fusion order undocumented); `min_edge_mm` skips bed edges shorter than mm; `inner_loops=True` also chamfers inner loops (holes/windows) of the bed face, same dihedral groups and min_edge_mm (default False = holes sharp, fits) |
| features.select_edges | select_edges(body, circle_r, axis, convex_top, tol_mm, on_plane, face_normal, min_dot) | |
| features.groove_positions | groove_positions(length_mm, pitch_mm=30.0) | |
| features.warp_relief_grooves | warp_relief_grooves(comp, body, *, label, width_mm=0.8, depth_mm=0.3, pitch_mm=30.0, bed="-z") — bed = print bed face (BedDir); two families = in-plane axes; stores set_bed. Seed rect in model coords (_seed_rect); cut depth sign from plane normal; live smoke tests/smoke/fusion_smoke_bed_orientation.py | `axis_plane`, `extrude`, `patterns` |
| features.axis_z_through | axis_z_through(comp, x, y, label) | `setByTwoPlanes` |

## grip_fit
| helper | signature | replaces raw adsk |
|---|---|---|
| grip_fit.fin_corners | fin_corners(kind, diameter, interference, fin_thickness, relief, lean) — pure string assembly, (radial, tangential) mm expressions | |
| grip_fit.fin_ramp_corners | fin_ramp_corners(kind, diameter, interference, relief, entry_along, entry) — pure string assembly, (radial, along) mm expressions of the fin entry ramp triangle: root (r_root, z_e), overcut (r_end, z_e), cone end (r_end, z_e ± (span+0.2)·tan(FIN_RAMP_ANGLE=46 deg)); z_e = fin entry plane = entry face (low: start, high: start+depth); sign + low, − high; hole r_root D/2+relief+0.6, r_end D/2-i-0.2; shaft r_root d/2-relief-0.6, r_end d/2+i+0.2 | |
| grip_fit.relief_profile_corners | relief_profile_corners(kind, diameter, relief, start, depth, roof_angle="46 deg", entry="low") — pure string assembly, (radial, along) mm expressions of the relief quad; entry high = mirror (open end at start+depth, roof far end at start-rise): hole ring D/2..D/2+relief, shaft groove d/2-relief..d/2+0.5; outer wall to start+depth, conical roof (tan(roof_angle)) rising inward to D/2 (hole) / core (shaft); no flat ceiling | |
| grip_fit.grip_fit | (ValueError before Fusion if fin_count > max_fin_count; check skipped when an input is a user-parameter name) grip_fit(comp, body, centre, start, depth, diameter, kind="hole", *, axis="z", entry=None, interference, fin_thickness, fin_count, relief, lean, lead_in="1 mm", label) — relief revolve cut (trapezoid, 46 deg roof, no flat ceiling) + seed fin + circular pattern + fin entry ramp revolve cut (fin bodies only, 46 deg cone, fin_ramp_corners) + one join + lead-in revolve cut (tip trim at entry; zone = [start, start+depth] along axis; fin span = full zone: entry low: start..start+depth+relief_roof_rise+0.05 mm (far end inside solid above roof); entry high: start-relief_roof_rise-0.05..start+depth (entry face); writes body attr dfam/grip_fin_min_section (fin_section_min_mm); entry low/high, default hole low, shaft high; slope > 45 deg) + fin face tag (`DFAM_GROUP`/`GRIP_FIN_ATTR` from `fusion_kit.dfam`, planar vertical faces in radial band); kind "hole" or "shaft" | `extrudeFeatures`, `circularPatternFeatures`, `combineFeatures`, `revolveFeatures` (lead-in), `attributes.add` (tag, raw adsk) |
| grip_fit.relief_roof_rise | relief_roof_rise(kind, diameter, relief, roof_angle="46 deg") -> mm expression of the conical roof rise (r_out - r_in) * tan(roof_angle), same constants as relief_profile_corners. Far roof end = start + depth + rise (entry low) | start - rise (entry high); size a pocket as depth = wanted_end - start - rise (low). Pure string assembly | |
| grip_fit.lead_in_corners | lead_in_corners(kind, diameter, interference, lead_in, start, depth, entry=None) — pure string assembly, (radial, along) mm expressions of the lead-in triangle | |
| grip_fit.lead_in_trims_body | lead_in_trims_body(lead_mm, interference_mm, relief_mm) -> bool — True when lead > interference + relief (triangle reaches solid core/seat); grip_fit creates the lead-in cut only then. Pure floats | |
| grip_fit.axis_frame | axis_frame(axis) -> (along, radial, tangential) coordinate indices; x -> (0,1,2), y -> (1,2,0), z -> (2,0,1); ValueError unless x/y/z. Pure | |
| grip_fit.default_entry | default_entry(kind) -> "low" (hole) / "high" (shaft). Pure | |
| grip_fit.model_point | model_point(axis, radial, tangential, along) -> (x, y, z) model-coord expressions. Pure string assembly | |
| grip_fit.fin_band_mm | fin_band_mm(kind, diameter_mm, interference_mm, relief_mm) -> (lo, hi) mm radial band of fin faces (tip..relief edge, +/-0.05). Pure floats | |
| grip_fit.fin_gap_mm | fin_gap_mm(n_fins, kind, diameter_mm, relief_mm, fin_thickness_mm, interference_mm, lean_rad) -> min arc gap (mm) between neighbour fin footprints over the fin band in the relief zone (shaft d/2-relief..d/2+interference, hole d/2-interference..d/2+relief; 41 samples); lean offset included. Pure floats | |
| grip_fit.fin_footprint_rad | fin_footprint_rad(kind, diameter_mm, relief_mm, fin_thickness_mm, interference_mm, lean_rad, rho_mm) -> (lo, hi) polar angle (rad, seed ray 0) of the fin quad on circle rho_mm; single arc, else ValueError. Pure floats | |
| grip_fit.max_fin_count | max_fin_count(diameter_mm, kind, relief_mm, fin_thickness_mm, interference_mm, lean_rad) -> largest fin count with gap >= FIN_GAP_MM (0.4 mm); 0 if none. Pure floats | |
| grip_fit.is_grip_fin_face | is_grip_fin_face(normal_z, radial_mm, z_mm, band, zone) -> bool. Fin face tag rule (vertical, in band, in grip zone). Pure floats | |
| grip_fit.is_grip_fin_end_face | is_grip_fin_end_face(normal_axis, radial_mm, z_mm, band, zone) -> bool. Fin axial end cap tag rule (planar, normal along axis, in band, in zone +/- FIN_FACE_TOL_MM). Far-end cap meets peg at pin_base edge. Pure floats | |
| grip_fit.fin_section_min_mm | fin_section_min_mm(kind, diameter_mm, relief_mm, fin_thickness_mm, interference_mm, lean_rad, lead_mm) -> min tangential fin width (mm) over the lead-in radial band (tip .. lead-in outer radius; 41 samples, ends inset). Lean 0 = fin thickness. ValueError on bad kind / lead <= 0. Feeds dfam grip_fin_sliver (< 0.4 mm). Pure floats | |

## holes
| helper | signature | replaces raw adsk |
|---|---|---|
| holes.slot_sketch | slot_sketch(sketch, center, direction, width_mm, play_mm) | `addCenterPointSlot` |
| holes.slot_profile | slot_profile(sketch, width_mm, play_mm) | `profileLoops`, `areaProperties` |
| holes.chamfer_reach_mm | chamfer_reach_mm(play_mm, d_mm) | |
| holes.cut_screw_slot | cut_screw_slot(comp, sketch, center, direction, d_mm, play_mm, head_d_mm, body, entry_plane, depth_mm, plane_outward, *, cs_plane, label) | |
| holes.cut_screw_slot_live | cut_screw_slot_live(comp, plane, place, center, direction, body, plane_outward, width, play, head, depth, width_mm, play_mm, head_mm, label) | `addCenterPointSlot` |
| holes.countersink_depth_mm | countersink_depth_mm(d_mm, head_d_mm) | |
| holes.hole_simple | hole_simple(comp, points, diameter, depth=None, *, label, through_dir) | `holeFeatures`, `createSimpleInput` |
| holes.hole_counterbore | hole_counterbore(comp, points, diameter, cb_diameter, cb_depth, depth=None, *, label, through_dir) | `holeFeatures`, `createCounterboreInput` |
| holes.hole_countersink | hole_countersink(comp, points, diameter, cs_diameter, cs_angle="90 deg", depth=None, *, label, through_dir) | `holeFeatures`, `createCountersinkInput` |
| holes.teardrop_points | teardrop_points(cx, cy, r_mm, wall_deg=60.0, up=(0.0, 1.0)) | |
| holes.seam_notch_points | seam_notch_points(cx, cy, r_mm, notch_mm=0.3, dir_deg=90.0) | |
| holes.sacrificial_depth | sacrificial_depth(cb_depth_mm, layer_mm=0.2) | |
| holes.teardrop_hole | teardrop_hole(comp, sketch, center_mm, r_mm, body, depth_mm, *, label, wall_deg=60.0, up=(0.0, 1.0), normal="y") | |
| holes.seam_notch | seam_notch(comp, sketch, center_mm, r_mm, body, depth_mm, *, label, notch_mm=0.3, dir_deg=90.0, normal="y") | |

## joints
| helper | signature | replaces raw adsk |
|---|---|---|
| joints.dovetail_profile | dovetail_profile(width_mm, depth_mm, angle_deg=15.0, clearance_mm=0.2) | |
| joints.mark_mating | mark_mating(faces) | `attributes.add` |
| joints.dovetail | dovetail(comp, sketch, body_male, body_female, origin_mm, width_mm, depth_mm, length_mm, *, label, angle_deg=15.0, clearance_mm=0.2, edge_chamfer_mm=0.3) | `sketches.add`, `sketchLines`, `addByTwoPoints`, `attributes.add` |

## loader
| helper | signature | replaces raw adsk |
|---|---|---|
| loader.run_part | run_part(entry_path, context="") | |

## printability
| helper | signature | replaces raw adsk |
|---|---|---|
| printability.rot_x | rot_x(deg) | |
| printability.rot_y | rot_y(deg) | |
| printability.rot_z | rot_z(deg) | |
| printability.compose | compose(*mats) | |
| printability.rotate_mesh | rotate_mesh(mesh, rot) | |
| printability.weld | weld(mesh, tol_mm=1e-4) | |
| printability.check | check(mesh, min_angle_deg=45.0, ...) | |
| printability.sweep | sweep(mesh, candidates, min_angle_deg=45.0, ...) | |
| printability.clear_rays | clear_rays(mesh, origins, direction, length_mm) | |
| printability.march_thickness | march_thickness(is_inside, point_mm, direction, max_mm, resolution_mm) | |
| printability.mesh_from_body | mesh_from_body(body, quality=None) | `createMeshCalculator` |
| printability.check_body | check_body(body, up_rot, min_angle_deg=45.0, ...) | Slot ceiling <= SLIT_MAX_MM (0.2 mm) between facing walls is not overhang (slicer closes it); B-rep flat annulus (2 concentric loops) of width <= SLIT_MAX_MM exempt (`_brep_slit_ceiling`). Flat down-facing B-rep bridge (1 planar loop, or annulus) with min in-plane width <= SHORT_BRIDGE_MAX_MM (1.0 mm) exempt ONLY if supported: both long sides (edges at both ends of the min-width span) border a face going below the bridge plane (bbox minZ < bridge z - 0.1 mm); annulus: every edge of both loops supported. Cantilever (unsupported) stays flagged (`_brep_short_bridge`; user P25 2026-11-17). Short cantilever (P26): planar down-facing 1-loop face with reach <= 1.0 mm (`CANTILEVER_MAX_MM`) from one supported edge's line exempt (`_brep_short_cantilever`). |
| printability.wall_thickness_mm | wall_thickness_mm(body, point_mm, direction, max_mm=50.0, resolution_mm=0.05) | `pointContainment` |

## report
| helper | signature | replaces raw adsk |
|---|---|---|
| report.report | report(comp, dfam=True) | `healthState`, `isFullyConstrained` |
| report.report_data | report_data(comp, dfam=True) -> ReportData (structured facts, prints nothing; findings None when dfam=False) | `healthState`, `isFullyConstrained` |
| report.format_report | format_report(data) -> report() text from ReportData. Pure | |
| report.world_bbox | world_bbox(occ) | |
| report.inside | inside(body, x_mm, y_mm, z_mm) | `pointContainment` |
| report.inside_runs | inside_runs(body, x_mm, y_mm, z0_mm, z1_mm, step_mm=0.1) | `pointContainment` |
| report.merge_runs | merge_runs(zs, flags) | |
| report.interference | interference(design, items) | `createInterferenceInput`, `analyzeInterference` |
| report.overhangs | overhangs(body, limit=0.707, bed_tol_mm=0.01, max_hole_d_mm=8.0) | `parametricRange`, `isParameterOnFace`, `getNormalAtParameter` |

## sketch
| helper | signature | replaces raw adsk |
|---|---|---|
| sketch.new_sketch | new_sketch(comp, plane="xy", label) | |
| sketch.to_sketch | to_sketch(s, x_mm, y_mm, z_mm) | `modelToSketchSpace` |
| sketch.circle | circle(s, x, y, r, z=0.0, diameter=None) | `addByCenterRadius`, `addDiameterDimension` |
| sketch.fillet_corner | fillet_corner(s, line_a, line_b, corner, radius_expr, radius_mm) | `addFillet`, `addRadialDimension` |
| sketch.poly | poly(s, pts, lock_axes=False) | `addByTwoPoints`, `addVertical`, `addHorizontal` |
| sketch.rect | rect(s, a, b) | `addByTwoPoints`, `addHorizontal`, `addVertical` |
| sketch.dim | dim(s, a, b, expr, orientation) | `addDistanceDimension` |
| sketch.project_point | project_point(s, entity) | `project2` |
| sketch.dim_from | dim_from(s, ref, ref_mm, p, offsets) | |
| sketch.anchor | anchor(s, p, h=None, v=None, ref=None) | `addCoincident`, `addVerticalPoints`, `addHorizontalPoints` |
| sketch.mm | mm(expr) | `evaluateExpression` |
| sketch.fix_point | fix_point(s, sp, p) | |
| sketch.param_poly | param_poly(s, pts, jitter_mm=0.05) | `deleteMe` |
| sketch.fixed_poly | fixed_poly(s, pts) | `isFixed` |
| sketch.rect_two_corners | rect_two_corners(s, a, b, side_h, side_v) | |
| sketch.rect_xy | rect_xy(s, x0, y0, x1, y1) | |
| sketch.rect_xy_sketch | rect_xy_sketch(comp, label, x0, y0, x1, y1) | |
| sketch.deferred | deferred(s) | `isComputeDeferred` |

## steps
| helper | signature | replaces raw adsk |
|---|---|---|
| steps.run_steps | run_steps(steps, context) | |

## timeline
| helper | signature | replaces raw adsk |
|---|---|---|
| timeline.check_label | check_label(label) | |
| timeline.set_label | set_label(entity, label) | |
| timeline.label_stats | label_stats(design) | |
| timeline.dump | dump(design) | `healthState` |
| timeline.run | run(_context) | |

## user_params
| helper | signature | replaces raw adsk |
|---|---|---|
| user_params.ensure_params | ensure_params(design, spec, overwrite=True, prune=False) | `userParameters` |
| user_params.snapshot_geo | snapshot_geo(design) | `userParameters` |
| user_params.eval_fresh | eval_fresh(design, expr, units) -> internal units (cm/rad). Derived user params re-evaluated from `.expression`, never stale `.value` (used by `sketch.mm`) | `evaluateExpression`, `userParameters` |

## view
| helper | signature | replaces raw adsk |
|---|---|---|
| view.set_orientation | set_orientation(name) | `viewOrientation` |
| view.save_screenshot | save_screenshot(path, eye_mm=None, target_mm=None, width=600, height=400, hide=None, fit=True) | `saveAsImageFile` |
