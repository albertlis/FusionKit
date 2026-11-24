# fusion_kit API index

Before raw adsk in execute/parts: find helper here. Missing → raw adsk in `parts/helpers.py` + open a GitHub issue using the *Missing helper* template.
Always run via `fusion_kit.loader.run_part(entry)`, never `import fusion_kit` in execute code.
Column 3 = raw adsk tokens the helper wraps (empty = none), dotted allowed. Read by `hooks/fusion_kit_nudge.py`. `!` prefix (`!addNewComponent`) = exact 1:1 replacement: first hit per session+file+token is denied once, retry passes. Plain token = nudge only.
Rows = every public module-level def (no leading `_`). Col 2 ends ` — mod.py:LINE` = def line (test-checked). Prose lives in the def docstring. Enforced by `tests/test_api_index_offline.py`.

## build_check
| helper | signature | replaces raw adsk |
|---|---|---|
| build_check.build_and_check | build_and_check(...) — build_check.py:233 |  |
| build_check.format_check | format_check(bodies, summary) — build_check.py:120 |  |

## component
| helper | signature | replaces raw adsk |
|---|---|---|
| component.new_comp | new_comp(root, name) — component.py:7 | `!addNewComponent` |
| component.new_occ | new_occ(root, name) — component.py:12 | `!addNewComponent` |
| component.active_design | active_design() — component.py:19 | `Design.cast` |

## dfam
| helper | signature | replaces raw adsk |
|---|---|---|
| dfam_rules.teardrop_peak_mm | teardrop_peak_mm(...) — dfam_rules.py:46 |  |
| dfam_rules.teardrop_required_peak_mm | teardrop_required_peak_mm(...) — dfam_rules.py:54 |  |
| dfam_rules.teardrop_notch | teardrop_notch(r_mm) — dfam_rules.py:50 |  |
| dfam_rules.check_holes | check_holes(holes) — dfam_rules.py:62 |  |
| dfam_rules.check_edges | check_edges(edges) — dfam_rules.py:97 |  |
| dfam_rules.check_body_facts | check_body_facts(body_fact) — dfam_rules.py:190 |  |
| dfam_rules.check_all | check_all(...) — dfam_rules.py:237 |  |
| dfam_rules.summarize_findings | summarize_findings(findings) — dfam_rules.py:251 |  |
| dfam_body.parse_skip | parse_skip(value) — dfam_body.py:18 |  |
| dfam_body.format_skip | format_skip(rules) — dfam_body.py:60 |  |
| dfam_body.set_skip | set_skip(body, rules) — dfam_body.py:89 | `attributes.add` |
| dfam_facts.facts_from_body | facts_from_body(body, tags) — dfam_facts.py:70 | `Cylinder.cast`, `Plane.cast`, `Line3D.cast` |
| dfam_body.skip_from_body | skip_from_body(body) — dfam_body.py:83 | `attributes.itemByName` |
| dfam_body.dfam_check | dfam_check(body, fail_as_warn) — dfam_body.py:99 |  |
| dfam_rules.chamfer_leg_mm | chamfer_leg_mm(...) — dfam_rules.py:257 |  |
| dfam_rules.bed_leg_min_mm | bed_leg_min_mm(req_mm) — dfam_rules.py:266 |  |
| dfam_rules.bed_wall_ratio | bed_wall_ratio(...) — dfam_rules.py:275 |  |
| dfam_rules.bed_chamfer_feasible | bed_chamfer_feasible(phi_deg, angle_deg) — dfam_rules.py:292 |  |
| dfam_rules.bed_footprint_mm | bed_footprint_mm(...) — dfam_rules.py:301 |  |
| dfam_geom.is_full_cylinder | is_full_cylinder(span_deg) — dfam_geom.py:208 |  |
| dfam_warp.in_warp_groove | in_warp_groove(...) — dfam_warp.py:237 |  |
| dfam_warp.in_groove_rect | in_groove_rect(...) — dfam_warp.py:227 |  |
| dfam_warp.warp_family_mm | warp_family_mm(body) — dfam_warp.py:290 |  |
| dfam_warp.warp_frames | warp_frames(...) — dfam_warp.py:250 |  |
| dfam_warp.in_warp_frames | in_warp_frames(...) — dfam_warp.py:279 |  |
| dfam_bed.bed_axis | bed_axis(bed) — dfam_bed.py:17 |  |
| dfam_bed.bed_from_body | bed_from_body(body) — dfam_bed.py:27 | `attributes.itemByName` |
| dfam_bed.set_bed | set_bed(body, bed) — dfam_bed.py:39 | `attributes.add` |
| dfam_bed.bed_rotation_axes | bed_rotation_axes(bed) — dfam_bed.py:49 |  |
| dfam_bed.bed_to_minus_z_matrix | bed_to_minus_z_matrix(bed) — dfam_bed.py:82 | `Matrix3D.setWithCoordinateSystem`, `Vector3D.create` |
| dfam_bed.print_frame_body | print_frame_body(body) — dfam_bed.py:241 | `TemporaryBRepManager.get`, `copy`, `transform` |
| dfam_geom.annulus_width_mm | annulus_width_mm(face) — dfam_geom.py:169 | `BRepFace.loops`, `BRepEdge.geometry` |
| dfam_bed.print_frame_with_tags | print_frame_with_tags(body) — dfam_bed.py:195 | `TemporaryBRepManager.get`, `copy`, `transform`, `attributes.itemByName` |
| dfam_bed.rotate_point | rotate_point(p, axes) — dfam_bed.py:94 |  |
| dfam_bed.match_faces | match_faces(orig, copy, tol) — dfam_bed.py:141 |  |

## expr
| helper | signature | replaces raw adsk |
|---|---|---|
| expr.trig | trig(fn, angle) — expr.py:93 |  |
| expr.deg | deg(d) — expr.py:98 |  |
| expr.near | near(a, b) — expr.py:103 |  |
| expr.as_str | as_str(x) — expr.py:108 |  |
| expr.evaluate | evaluate(expr, names) — expr.py:139 |  |
| expr.derive | derive(spec, names) — expr.py:150 |  |
| expr.abs_expr | abs_expr(d) — expr.py:158 |  |
| expr.min_expr | min_expr(a, b) — expr.py:163 |  |
| expr.max_expr | max_expr(a, b) — expr.py:169 |  |

## feature_quality
| helper | signature | replaces raw adsk |
|---|---|---|
| feature_quality.check | check(design) — feature_quality.py:433 |  |
| feature_quality.rule_extent_distance_matches_body | rule_extent_distance_matches_body(...) — feature_quality.py:216 |  |
| feature_quality.rule_cut_blind_through | rule_cut_blind_through(...) — feature_quality.py:236 |  |
| feature_quality.rule_twin_extrudes | rule_twin_extrudes(...) — feature_quality.py:256 |  |
| feature_quality.rule_hole_as_extrude | rule_hole_as_extrude(...) — feature_quality.py:280 |  |
| feature_quality.rule_plane_offset_expr | rule_plane_offset_expr(...) — feature_quality.py:328 |  |
| feature_quality.rule_edge_by_edge | rule_edge_by_edge(entries) — feature_quality.py:374 |  |
| feature_quality.rule_thin_as_offset | rule_thin_as_offset(...) — feature_quality.py:401 |  |

## features_* (split from features.py; `fusion_kit.features` re-exports all names)
| helper | signature | replaces raw adsk |
|---|---|---|
| features_common.collection | collection(items) — features_common.py:41 | `ObjectCollection` |
| features_extrude.profile | profile(sketch, outer) — features_extrude.py:34 | `profileLoops`, `areaProperties` |
| features_extrude.profiles | profiles(sketch, predicate) — features_extrude.py:74 |  |
| features_extrude.extrude | extrude(...) — features_extrude.py:83 | `extrudeFeatures`, `setSymmetricExtent`, `setOneSideExtent`, `setTwoSidesExtent`, `ToEntityExtentDefinition`, `ThroughAllExtentDefinition`, `FromEntityStartDefinition` |
| features_extrude.revolve | revolve(...) — features_extrude.py:199 | `revolveFeatures`, `setAngleExtent`, `setOneSideToExtent` |
| features_pattern.pattern_circular | pattern_circular(...) — features_pattern.py:47 | `circularPatternFeatures` |
| features_pattern.pattern_rectangular | pattern_rectangular(...) — features_pattern.py:84 | `rectangularPatternFeatures`, `setDirectionTwo` |
| features_axes.axis_plane | axis_plane(...) — features_axes.py:30 | `setByOffset` |
| features_pattern.pattern_grid | pattern_grid(...) — features_pattern.py:130 | `rectangularPatternFeatures`, `setDirectionTwo` |
| features_pattern.combine | combine(...) — features_pattern.py:191 | `combineFeatures` |
| features_axes.move_body | move_body(...) — features_axes.py:138 | `moveFeatures`, `defineAsTranslateXYZ` |
| features_extrude.shell | shell(...) — features_extrude.py:241 | `shellFeatures`, `ShellFeatureInput.insideThickness` |
| features_extrude.plane_midplane | plane_midplane(...) — features_extrude.py:264 | `constructionPlanes`, `setByTwoPlanes` |
| features_extrude.axis_by_cylinder | axis_by_cylinder(...) — features_extrude.py:286 | `constructionAxes`, `setByCircularFace` |
| features_axes.rotate_body | rotate_body(...) — features_axes.py:157 | `moveFeatures`, `defineAsRotate` |
| features_extrude.single_body | single_body(comp, prefix) — features_extrude.py:308 |  |
| features_axes.axis_y_through | axis_y_through(...) — features_axes.py:87 | `setByTwoPlanes` |
| features_axes.axis_x_through | axis_x_through(...) — features_axes.py:102 | `setByTwoPlanes` |
| features_axes.rotate_body_about_y | rotate_body_about_y(...) — features_axes.py:202 |  |
| features_axes.rotate_body_about_x | rotate_body_about_x(...) — features_axes.py:219 |  |
| features_common.face_normal | face_normal(face, point) — features_common.py:71 | `getNormalAtPoint` |
| features_edges.faces_by_normal | faces_by_normal(...) — features_edges.py:27 |  |
| features_edges.convex_edges | convex_edges(...) — features_edges.py:159 | `pointContainment` |
| features_edges.edges_at_xy | edges_at_xy(...) — features_edges.py:205 | `edges`, `boundingBox` |
| features_axes.offset_plane | offset_plane(...) — features_axes.py:236 | `setByOffset` |
| features_edges.planar_faces | planar_faces(...) — features_edges.py:226 |  |
| features_edges.fillet | fillet(...) — features_edges.py:263 | `filletFeatures`, `addConstantRadiusEdgeSet` |
| features_edges.chamfer | chamfer(...) — features_edges.py:285 | `chamferFeatures`, `addEqualDistanceChamferEdgeSet`, `addTwoDistancesChamferEdgeSet` |
| features_edges.chamfer_greedy | chamfer_greedy(...) — features_edges.py:332 |  |
| features_pattern.mirror | mirror(...) — features_pattern.py:218 | `mirrorFeatures` |
| features_pattern.mirror_bodies | mirror_bodies(...) — features_pattern.py:249 | `mirrorFeatures`, `createInput`, `bodies` |
| features_bed.bed_chamfer | bed_chamfer(...) — features_bed.py:92 |  |
| features_edges.select_edges | select_edges(...) — features_edges.py:440 | `edges.item`, `startVertex`, `endVertex` |
| features_warp.groove_positions | groove_positions(...) — features_warp.py:54 |  |
| features_warp.warp_relief_grooves | warp_relief_grooves(...) — features_warp.py:262 | `axis_plane`, `extrude`, `patterns`; *_mm optional w/ *_expr |
| features_axes.axis_z_through | axis_z_through(...) — features_axes.py:117 | `setByTwoPlanes` |

## grip_fit
| helper | signature | replaces raw adsk |
|---|---|---|
| grip_fit.fin_corners | fin_corners(...) — grip_fit.py:102 |  |
| grip_fit.fin_ramp_corners | fin_ramp_corners(...) — grip_fit.py:407 |  |
| grip_fit.relief_profile_corners | relief_profile_corners(...) — grip_fit.py:450 |  |
| grip_fit.grip_fit | grip_fit(...) — grip_fit.py:679 | `extrudeFeatures`, `circularPatternFeatures`, `combineFeatures`, `revolveFeatures`, `attributes.add` |
| grip_fit.relief_roof_rise | relief_roof_rise(...) — grip_fit.py:495 |  |
| grip_fit.lead_in_corners | lead_in_corners(...) — grip_fit.py:348 |  |
| grip_fit.lead_in_trims_body | lead_in_trims_body(...) — grip_fit.py:392 |  |
| grip_fit.axis_frame | axis_frame(axis) — grip_fit.py:58 |  |
| grip_fit.default_entry | default_entry(kind) — grip_fit.py:72 |  |
| grip_fit.model_point | model_point(...) — grip_fit.py:91 |  |
| grip_fit.fin_band_mm | fin_band_mm(...) — grip_fit.py:516 |  |
| grip_fit.fin_gap_mm | fin_gap_mm(...) — grip_fit.py:177 |  |
| grip_fit.fin_footprint_rad | fin_footprint_rad(...) — grip_fit.py:217 |  |
| grip_fit.max_fin_count | max_fin_count(...) — grip_fit.py:259 |  |
| grip_fit.is_grip_fin_face | is_grip_fin_face(...) — grip_fit.py:533 |  |
| grip_fit.is_grip_fin_end_face | is_grip_fin_end_face(...) — grip_fit.py:554 |  |
| grip_fit.fin_section_min_mm | fin_section_min_mm(...) — grip_fit.py:318 |  |

## holes
| helper | signature | replaces raw adsk |
|---|---|---|
| holes.slot_sketch | slot_sketch(...) — holes.py:34 | `addCenterPointSlot` |
| holes.slot_profile | slot_profile(...) — holes.py:61 | `profileLoops`, `areaProperties` |
| holes.chamfer_reach_mm | chamfer_reach_mm(...) — holes.py:79 |  |
| holes.cut_screw_slot | cut_screw_slot(...) — holes.py:87 |  |
| holes.cut_screw_slot_live | cut_screw_slot_live(...) — holes.py:153 | `addCenterPointSlot` |
| holes.countersink_depth_mm | countersink_depth_mm(...) — holes.py:214 |  |
| holes.hole_simple | hole_simple(...) — holes.py:246 | `holeFeatures`, `createSimpleInput` |
| holes.hole_counterbore | hole_counterbore(...) — holes.py:259 | `holeFeatures`, `createCounterboreInput` |
| holes.hole_countersink | hole_countersink(...) — holes.py:281 | `holeFeatures`, `createCountersinkInput` |
| holes.teardrop_points | teardrop_points(...) — holes.py:324 |  |
| holes.teardrop_flat_points | teardrop_flat_points(...) — holes.py:351 |  |
| holes.seam_notch_points | seam_notch_points(...) — holes.py:389 |  |
| holes.sacrificial_depth | sacrificial_depth(...) — holes.py:416 |  |
| holes.teardrop_hole | teardrop_hole(...) — holes.py:424 |  |
| holes.teardrop_hole_live | teardrop_hole_live(...) — holes.py:473 | `addByTwoPoints`, `addByCenterRadius`, `addDiameterDimension` |
| holes.seam_notch | seam_notch(...) — holes.py:538 |  |

## joints
| helper | signature | replaces raw adsk |
|---|---|---|
| joints.dovetail_profile | dovetail_profile(...) — joints.py:29 |  |
| joints.mark_mating | mark_mating(faces) — joints.py:78 | `attributes.add` |
| joints.dovetail | dovetail(...) — joints.py:112 | `sketches.add`, `sketchLines`, `addByTwoPoints`, `attributes.add` |

## loader
| helper | signature | replaces raw adsk |
|---|---|---|
| loader.run_part | run_part(...) — loader.py:44 |  |

## param_lint
| helper | signature | replaces raw adsk |
|---|---|---|
| param_lint.lint_source | lint_source(source, filename, ranged) — param_lint.py:186 |  |
| param_lint.lint_entry | lint_entry(entry_path) — param_lint.py:208 |  |
| param_lint.format_lint | format_lint(hits) — param_lint.py:223 |  |

## param_sweep
| helper | signature | replaces raw adsk |
|---|---|---|
| param_sweep.candidates | candidates(original, opts) — param_sweep.py:111 |  |
| param_sweep.unhealthy_names | unhealthy_names(items) — param_sweep.py:128 |  |
| param_sweep.geometry | geometry(bodies) — param_sweep.py:136 |  |
| param_sweep.same_geometry | same_geometry(a, b) — param_sweep.py:154 |  |
| param_sweep.judge | judge(...) — param_sweep.py:211 |  |
| param_sweep.new_fails | new_fails(baseline, probe) — param_sweep.py:177 |  |
| param_sweep.format_sweep | format_sweep(rows) — param_sweep.py:259 |  |
| param_sweep.sweep_token | sweep_token(rows) — param_sweep.py:269 |  |
| param_sweep.sweep_params | sweep_params(...) — param_sweep.py:541 | `userParameters`, `expression`, `timeline`, `healthState`, `isSuppressed`, `isGroup`, `allComponents`, `physicalProperties` |
| param_sweep.body_verdict | body_verdict(body, up_rot) — param_sweep.py:377 | `dfam_check`, `check_body` |

## printability
| helper | signature | replaces raw adsk |
|---|---|---|
| printability.rot_x | rot_x(deg) — printability.py:157 |  |
| printability.rot_y | rot_y(deg) — printability.py:162 |  |
| printability.rot_z | rot_z(deg) — printability.py:167 |  |
| printability.compose | compose(*mats) — printability.py:183 |  |
| printability.rotate_mesh | rotate_mesh(mesh, rot) — printability.py:199 |  |
| printability.weld | weld(mesh, tol_mm) — printability.py:206 |  |
| printability.check | check(...) — printability.py:537 |  |
| printability.sweep | sweep(...) — printability.py:794 |  |
| printability.clear_rays | clear_rays(...) — printability.py:873 |  |
| printability.march_thickness | march_thickness(...) — printability.py:946 |  |
| printability.mesh_from_body | mesh_from_body(body, quality) — printability.py:1006 | `createMeshCalculator` |
| printability.check_body | check_body(...) — printability.py:1507 | `_brep_slit_ceiling`, `_brep_short_bridge`, `CANTILEVER_MAX_MM`, `_brep_short_cantilever` |
| printability.wall_thickness_mm | wall_thickness_mm(...) — printability.py:1576 | `pointContainment` |

## probe
| helper | signature | replaces raw adsk |
|---|---|---|
| probe.probe_sketch | probe_sketch(sketch, steps, rollback=True) — probe.py:36 |  |

## report
| helper | signature | replaces raw adsk |
|---|---|---|
| report.report | report(comp, dfam) — report.py:33 | `healthState`, `isFullyConstrained`, `isSuppressed` |
| report.report_data | report_data(comp, dfam) — report.py:69 | `healthState`, `isFullyConstrained` |
| report.format_report | format_report(data) — report.py:120 |  |
| report.world_bbox | world_bbox(occ) — report.py:144 |  |
| report.inside | inside(body, x_mm, y_mm, z_mm) — report.py:158 | `pointContainment` |
| report.inside_runs | inside_runs(...) — report.py:196 | `pointContainment` |
| report.merge_runs | merge_runs(zs, flags) — report.py:168 |  |
| report.interference | interference(design, items) — report.py:231 | `createInterferenceInput`, `analyzeInterference` |
| report.overhangs | overhangs(...) — report.py:276 | `parametricRange`, `isParameterOnFace`, `getNormalAtParameter` |

## sketch
| helper | signature | replaces raw adsk |
|---|---|---|
| sketch.new_sketch | new_sketch(...) — sketch.py:39 | `sketches.add` |
| sketch.to_sketch | to_sketch(s, x_mm, y_mm, z_mm) — sketch.py:65 | `modelToSketchSpace` |
| sketch.circle | circle(s, x, y, r, z, diameter) — sketch.py:75 | `addByCenterRadius`, `addDiameterDimension`; x/y/z also expr str/Ex |
| sketch.fillet_corner | fillet_corner(...) — sketch.py:108 | `addFillet`, `addRadialDimension` |
| sketch.poly | poly(s, pts, *, lock_axes) — sketch.py:132 | `addByTwoPoints`, `addVertical`, `addHorizontal` |
| sketch.rect | rect(s, a, b) — sketch.py:168 | `addByTwoPoints`, `addHorizontal`, `addVertical` |
| sketch.dim | dim(...) — sketch.py:197 | `addDistanceDimension` |
| sketch.project_point | project_point(s, entity) — sketch.py:215 | `project2` |
| sketch.dim_from | dim_from(...) — sketch.py:224 |  |
| sketch.anchor | anchor(s, p, h, v, ref) — sketch.py:254 | `addCoincident`, `addVerticalPoints`, `addHorizontalPoints` |
| sketch.mm | mm(expr) — sketch.py:316 | `evaluateExpression` |
| sketch.fix_point | fix_point(...) — sketch.py:374 |  |
| sketch.model_axis_to_sketch | model_axis_to_sketch(s, model_axis) — sketch.py:462 |  |
| sketch.param_poly | param_poly(...) — sketch.py:471 | `deleteMe`, `addHorizontal`, `addVertical` |
| sketch.fixed_poly | fixed_poly(s, pts) — sketch.py:531 | `isFixed` |
| sketch.rect_two_corners | rect_two_corners(...) — sketch.py:554 |  |
| sketch.rect_xy | rect_xy(s, x0, y0, x1, y1) — sketch.py:566 |  |
| sketch.rect_xy_sketch | rect_xy_sketch(...) — sketch.py:571 |  |
| sketch.deferred | deferred(s) — sketch.py:605 | `isComputeDeferred` |
| sketch.offset_curves | offset_curves(...) — sketch.py:578 | `createOffsetInput`, `addOffset2`, `ValueInput.createByString` |
| sketch.center_on_origin | center_on_origin(...) — sketch.py:593 | `addByTwoPoints`, `addMidPoint`, `isConstruction` |

## sketch_quality
| helper | signature | replaces raw adsk |
|---|---|---|
| sketch_quality.half_operand | half_operand(expr) — sketch_quality.py:108 |  |
| sketch_quality.pattern_multiple | pattern_multiple(expr) — sketch_quality.py:123 |  |
| sketch_quality.check_facts | check_facts(f, skip) — sketch_quality.py:306 |  |
| sketch_quality.skq_token | skq_token(lines) — sketch_quality.py:331 |  |
| sketch_quality.sketch_facts | sketch_facts(sk) — sketch_quality.py:362 | `profiles`, `profileLoops`, `profileCurves`, `sketchCurves`, `sketchPoints`, `geometricConstraints`, `sketchDimensions`, `isFullyConstrained`, `isFixed`, `isReference`, `objectType` |
| sketch_quality.skip_of | skip_of(sk) — sketch_quality.py:393 | `attributes.itemByName` |
| sketch_quality.check_sketches | check_sketches(sketches) — sketch_quality.py:415 |  |
| sketch_quality.check | check(design) — sketch_quality.py:428 | `allComponents` |

## steps
| helper | signature | replaces raw adsk |
|---|---|---|
| steps.run_steps | run_steps(steps, context) — steps.py:9 |  |

## timeline
| helper | signature | replaces raw adsk |
|---|---|---|
| timeline.check_label | check_label(label) — timeline.py:16 |  |
| timeline.set_label | set_label(entity, label) — timeline.py:23 |  |
| timeline.label_stats | label_stats(design) — timeline.py:37 |  |
| timeline.dump | dump(design) — timeline.py:73 | `healthState` |
| timeline.run | run(_context) — timeline.py:111 |  |

## user_params
| helper | signature | replaces raw adsk |
|---|---|---|
| user_params.ensure_params | ensure_params(...) — user_params.py:98 | `userParameters` |
| user_params.snapshot_geo | snapshot_geo(design) — user_params.py:147 | `userParameters` |
| user_params.eval_fresh | eval_fresh(...) — user_params.py:162 | `evaluateExpression`, `userParameters` |
| user_params.entry_fields | entry_fields(entry) — user_params.py:36 |  |
| user_params.entry_sweep | entry_sweep(entry) — user_params.py:46 |  |

## view
| helper | signature | replaces raw adsk |
|---|---|---|
| view.set_orientation | set_orientation(name) — view.py:31 | `viewOrientation` |
| view.save_screenshot | save_screenshot(...) — view.py:58 | `saveAsImageFile` |
