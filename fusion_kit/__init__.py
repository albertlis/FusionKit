"""fusion_kit — shared Fusion 360 scripting helpers.

Module index (main helpers per module; every public def with its signature is in docs/fusion_kit_api.md)
-------
component    new_comp, new_occ.
dfam         dfam_check (FDM rule scan per body), check_all, summarize_findings, set_skip, skip_from_body.
expr         Ex (parametric length), trig, deg, near, as_str, evaluate, derive.
features     collection, profile, extrude, revolve, pattern_circular, pattern_rectangular, pattern_grid,
             combine (join/cut/intersect), face_normal, fillet, chamfer, chamfer_greedy, bed_chamfer,
             select_edges, offset_plane, planar_faces, mirror_bodies, warp_relief_grooves.
             Types: Op (extrude/revolve, incl. "new"), CombineOp (join/cut/intersect).
holes        hole_simple, hole_counterbore, hole_countersink (native HoleFeature), cut_screw_slot,
             cut_screw_slot_live, teardrop_hole, seam_notch, sacrificial_depth.
joints       dovetail, dovetail_profile, mark_mating.
loader       run_part (module-cache purge + exec entry).
printability check_body, check, sweep, mesh_from_body, wall_thickness_mm, clear_rays.
report       report (one-line diagnostic), world_bbox (root-frame bbox of an occurrence), inside, inside_runs,
             interference (pairwise overlap, nominal pose), overhangs (FDM downward-face scan).
sketch       new_sketch, to_sketch, circle, poly, rect, dim, anchor (ref= = far-reference dims), RECT_JITTER,
             param_poly, fixed_poly, deferred.
steps        run_steps.
timeline     set_label, check_label, label_stats, dump.
user_params  ensure_params, snapshot_geo (User Parameter utilities).
view         set_orientation, save_screenshot (viewport -> PNG file, optional eye/target mm camera).
enums        typed adsk enum constants: OP_NEW / OP_JOIN / OP_CUT / OP_INTERSECT (FeatureOperations),
             POSITIVE_EXTENT / NEGATIVE_EXTENT (ExtentDirections), PATTERN_SPACING / PATTERN_EXTENT,
             OPTIMIZED_PATTERN / IDENTICAL_PATTERN / ADJUST_PATTERN, ALIGNED_DIM / HORIZONTAL_DIM / VERTICAL_DIM,
             POINT_INSIDE / POINT_ON / POINT_OUTSIDE (PointContainment), NORMAL_QUALITY_MESH,
             DOC_TYPE_DESIGN, PARAMETRIC_DESIGN, ISO_TOP_RIGHT.

Units
-----
Helpers take mm (sketch x/y/z/r, extrude/fillet/chamfer expressions as str/Ex, select_edges circle_r,
on_plane value and tol_mm). Fusion internals are cm: .value and evaluateExpression return cm.
"""
