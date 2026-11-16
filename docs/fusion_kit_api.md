# fusion_kit API index

Before raw adsk in execute/parts: find helper here. Missing → raw adsk in `parts/helpers.py` + open a GitHub issue using the *Missing helper* template.
Always run via `fusion_kit.loader.run_part(entry)`, never `import fusion_kit` in execute code.
Column 3 = raw adsk tokens the helper wraps (empty = none). Read by `hooks/fusion_kit_nudge.py`.
Rows = every public module-level def (no leading `_`). Enforced by `tests/test_api_index_offline.py`.

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
| dfam.facts_from_body | facts_from_body(body) | `Cylinder.cast`, `Plane.cast`, `Line3D.cast` |
| dfam.skip_from_body | skip_from_body(body) | `attributes.itemByName` |
| dfam.dfam_check | dfam_check(body, fail_as_warn=False) | |
| dfam.chamfer_leg_mm | chamfer_leg_mm(width_mm, n_chamfer, n_adjacent) | |
| dfam.bed_leg_min_mm | bed_leg_min_mm(req_mm) | |
| dfam.is_full_cylinder | is_full_cylinder(span_deg) | |
| dfam.in_warp_groove | in_warp_groove(point_mm, grooves, bed_z_mm) | |
| dfam.in_groove_rect | in_groove_rect(point_mm, r, bed_z_mm) | |
| dfam.warp_family_mm | warp_family_mm(body) | |

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
| features.bed_chamfer | bed_chamfer(comp, body, distance, label, only_largest_bed_face=False, angle_deg=46.0) | two-distance: `distance` = wall leg, bed leg = distance / tan(angle_deg); `angle_deg` = chamfer face angle FROM HORIZONTAL, must be in (45, 90) exclusive else ValueError (46 -> n_z 0.695; 45 FAILS). isFlipped tried False then True; accept only if face angle acos(\|n_z\|) = angle_deg ±0.5 and \|n_z\| < 0.707 (Fusion order undocumented) |
| features.select_edges | select_edges(body, circle_r, axis, convex_top, tol_mm, on_plane, face_normal, min_dot) | |
| features.groove_positions | groove_positions(length_mm, pitch_mm=30.0) | |
| features.warp_relief_grooves | warp_relief_grooves(comp, body, *, label, width_mm=0.8, depth_mm=0.3, pitch_mm=30.0) | |

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
| printability.check_body | check_body(body, up_rot, min_angle_deg=45.0, ...) | |
| printability.wall_thickness_mm | wall_thickness_mm(body, point_mm, direction, max_mm=50.0, resolution_mm=0.05) | `pointContainment` |

## report
| helper | signature | replaces raw adsk |
|---|---|---|
| report.report | report(comp) | `healthState`, `isFullyConstrained` |
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

## view
| helper | signature | replaces raw adsk |
|---|---|---|
| view.set_orientation | set_orientation(name) | `viewOrientation` |
| view.save_screenshot | save_screenshot(path, eye_mm=None, target_mm=None, width=600, height=400, hide=None, fit=True) | `saveAsImageFile` |
