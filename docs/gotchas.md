# Fusion API gotchas

Pitfalls for Autodesk Fusion API scripts (run through the Fusion MCP `execute` tool or an add-in). Items were verified against live Fusion behaviour. FusionKit encodes many of them: where a bullet names a helper, use the helper instead of raw adsk. Helper index: `docs/fusion_kit_api.md`.

## Units & parameters

- **Internal length unit is cm.** Cause: the API works in centimetres, not millimetres. Fix: express values as strings with units via `ValueInput.createByString`, or set `dim.parameter.expression`.
- **`unitsManager.evaluateExpression(expr, "mm")` returns cm.** Cause: the result is in internal units despite the `"mm"` argument. Fix: multiply the result by 10 to get millimetres.
- **User Parameter expressions reject `max()` and `min()`.** Symptom: `Expression is invalid.` Fix: compute the value in Python and write the result as a number or an expression without these functions.
- **Expression `round()` is floor(x + 0.5).** Symptom: `round(2.5)` = 3, not banker's rounding. Fix: account for this when computing expected values.
- **Live parameter change on profiles chosen in Python.** Cause: profiles picked by index from a Python filter are re-picked by index on rebuild, so Fusion selects different faces. Symptom: extrudes hit unintended faces (measured: clipped features came out unclipped and full-length, solid slabs appeared). Fix: do not change such parameters live; edit the parameter source and rebuild the whole part.
- **`userParameters.itemByName(name)` returns null for an unknown name.** Symptom: `'NoneType' object has no attribute ...` on `.expression` / `.value`. Fix: check the name against the Parameters dialog; FusionKit `user_params` tests `is None` before use.
- **Deleting a User Parameter that is still referenced fails.** Symptom: Fusion refuses the delete. Fix: delete the dependent feature or body first.
- **Parametric = live dialog edit; Python reads of params are build-only.** Cause: `sketch.mm("name")`, `eval_fresh`, `itemByName(...).value`, and any `if` or `range` on those values freeze the value at build. Editing the parameter in the Parameters dialog then changes nothing (found by the user: print_orientation, end_stop_enabled). Edge values can also break the model (found by the user: bottom_flange_width = 1 mm, chamfers wider than the flange, failed features), and a count of 0 can cut with no target body (screw_hole_count = 0). Fix: pass expression strings to builders, and declare `min`/`max` (or `values`) as the 4th PARAMS item. `build_and_check(..., params=PARAMS)` then sweeps every declared range live (`DFAM_FAIL param_sweep`). `param_lint` reports `BUILD_ONLY_PARAM` reads; opt out only with `# param-lint: build-only <reason>`.

- **Feature model params come in several rows; angles are radians (measured, build graph smoke).** A plain `extrude(..., "wall_len")` (measured) created `d5` = 10 mm with expression `wall_len` and `d6` = 0.0 deg (taper). A fillet of 1 mm creates `d7` (`role` TangencyWeight, unitless, value 1, expression `1`) and `d8` (`role` Radius, unit `mm`, `.value` 0.1 cm = 1 mm). Sketch dimensions are not feature params: the sketch row shows only `profiles`. Fix: `timeline.item_params` converts with `_mm_value` (cm to mm, rad to deg).

## Sketches

- **Sketch on `yZConstructionPlane` has swapped axes.** Measured mapping: sketch x = model −Z, sketch y = model +Y. Fix: map coordinates with `sketch.to_sketch(s, x_mm, y_mm, z_mm)`, and verify positions with `body.pointContainment`. A print that echoes the input values proves nothing.
- **Sketch on a BRepFace (`new_sketch(comp, face)`) gets a projected holed outline.** Cause: Fusion auto-projects the face outline, so the profiles are the holed outline plus the inner circle. Symptom: `profile(sk)` with `outer=True` picks the holed outline, and the cut removes the body (measured: side walls removed). Fix: use `features.profile(sk, outer=False)` for the inner feature, and `sketch.to_sketch` for model-to-sketch points.
- **Over-constrained sketch (`VCS_SKETCH_OVER_CONSTRAINTS`).** Cause: dimensions on exactly shared coordinates, or horizontal/vertical inference on coincident points. Fix: add per-vertex jitter with `sketch.param_poly`, and delete the inferred constraints.
- **`param_poly` / `fix_point`: vertex sharing an exact coordinate.** Symptom: `VCS_SKETCH_OVER_CONSTRAINTS` when a vertex shares an exact x or y coordinate with the previous vertex. Cause: unknown. Fix: avoid shared coordinates; offset the vertex (a 0.3 mm overshoot was used, code since removed).
- **Unsigned sketch dims keep the mirrored solution.** Symptom: a point dimensioned to a model coordinate lands on the wrong side of a sketch axis (world Z sign flipped) after an earlier solve dragged it across that axis. Cause: `fix_point` dims are unsigned, so the side comes from the point's current position. Fix: `fix_point` moves the point onto the target's side of each wrong-sign axis (`SketchPoint.move`, target + `RECT_JITTER` 0.04 cm) before any dim is added. Same-side axes and target-0 axes are not moved (exact target coords -> `VCS_SKETCH_OVER_CONSTRAINTS`). Verify the side with `body.pointContainment`.
- **Center-point slot geometry.** Cause: `addCenterPointSlot` takes the arc centre at the play position, and the overall length is 2 × play + width. Fix: size the slot from that rule; `holes.slot_sketch` wraps the call.

## Features

- **Join after a hole cut closes the hole.** Cause: a join (boss or spigot) fills the previously cut hole. Fix: cut holes last in the build order.
- **`chamferFeatures` on a concave edge is a silent no-op.** Symptom: the call succeeds and nothing changes. Fix: add the material with a diamond-profile extrude (gusset) instead.
- **Chamfer chain ends at a sub-chamfer ledge (`ASM_BL_CAP_COMPLEX`, `ASM_BL_END_TOO_CMPLX`).** Cause: the chain ends at a ledge narrower than the chamfer (measured: 0.12 mm step). Fix: change the geometry so the chain end has no such ledge, for example one parameter for both widths. A smaller chamfer size does not fix it.
- **Bed chamfer over thin-wall bed loops fails with `ASM_BL_UNFIN_SHEET`.** Cause: thin walls (measured at 1.6 mm) at the bed loop. Fix: `features.bed_chamfer(..., only_largest_bed_face=True)`, which chamfers only the outer loop of the largest bed face.
- **Mirror or pattern of a cut with coplanar faces fails with `ASM_PATTERN_FEATURES_NO_PASTE_INT_EDGES`.** Fix: put all instances as profiles in ONE sketch and cut once.
- **Clipping many thin patterned bodies.** Symptom: extrude-intersect skips bodies; combine-intersect of a joined multi-lump body fails with `FEATURE_FAILED_TO_CREATE`. Fix: build the bodies pre-clipped (sketch the outline and strips, pick the profiles) instead of clipping afterwards.
- **Extrude cut or intersect with `participantBodies` skips a body (measured).** Fix: trim with a `combine` intersect against a tool body, or join first and then cut outside the boxes.
- **`pattern.bodies` may contain repeated seeds.** Cause: the same seed can appear more than once. Behaviour: `features.combine` dedupes tool bodies by `entityToken` (first wins, target dropped, order kept; ValueError if none are left). Without dedupe the call fails with `One of the input arguments is invalid` (localized Fusion message; Polish UI text: `Jeden z argumentów wejściowych jest niewłaściwy`).
- **Seed pattern over two interleaved grids needs an integer pitch ratio.** Cause: with a non-integer ratio every cell is cut differently and the pattern misses. Fix: link the pitches with an integer-multiple expression, for example `pitch_b = k * pitch_a`.
- **Rectangular pattern: unset direction-two gives 3 copies per instance by default.** Symptom: quantity 4 produces 12 bodies. Fix: set direction-two quantity to 1. The `features.pattern_rectangular` and `features.pattern_grid` helpers do this.
- **Pattern quantity 1 on a cut fails with "cannot cut any instances".** Cause: a pattern of quantity 1 has no instances to cut.
- **Pattern along a sketch line on a downslope.** Fix: use a negative pitch.
- **`ConstructionPlane.setByAngle` rounds the angle to 0.1°.** Symptom: small gaps silently close. Fix: quantize angle parameters to 0.1°.
- **Rotating a body about an axis inside a sub-component fails with `3 : Invalid entity`.** Fix: use `features.rotate_body_about_x` or `features.rotate_body_about_y` with the axis in the root component. Positive angle follows the right-hand rule (measured).
- **Orphan root construction (broken references): `deleteMe()` returns True but the object stays (measured twice).** Fix: fetch the referenced objects first (`itemByName` returns None at marker 0), set `timeline.markerPosition = 0`, call `deleteMe()`, then restore the marker to `timeline.count`. `TimelineObject` has no `deleteMe`.

- **Live user-param change shows in the build graph diff (measured).** `userParameters.itemByName("wall_len").expression = "12 mm"` then `collect` gave `d5` 12.0 mm on the extrude row, and `graph.diff` printed `~ block_extrude d5 10.0 -> 12.0`. Fix: none needed; the diff is the check that a parametric chain rebuilt.
- **`axis_z_through` leaves three timeline rows (measured).** Rows `<label>_plane_x`, `<label>_plane_y` (kind `plane`) and `<label>` (kind `axis`). `ConstructionAxis.attributes.add` for the `src` tag worked (measured: `fusion_smoke_graph.py:80` on all three). `new_comp` adds an occurrence row `block:1` with no `src` (measured).
- **`SweepFeature.path` getter raises (measured).** `sweep.path` gave `RuntimeError: 3 : Didn't roll editing feature back.` on a sweep from `createPath(line, True)` and also `createPath(line, False)`, with the sweep last in the timeline, with a later plane, and inside an occurrence component (unsaved new document). `sweep.profile` reads fine (type `Profile`, `parentSketch.name` = sketch name). Fix: `graph.collect` does not read the sweep path; `_unused_sketch` treats a sweep as unreadable, so earlier sketches stay unflagged.
- **`LoftFeature.loftSections` is readable (measured).** `loftSections.count` = 2, `loftSections.item(i).entity` = `Profile`, `Profile.parentSketch.name` = sketch name. `timeline._loft_sketches` reads them (smoke `fusion_smoke_graph_sweep_loft.py`).
- **Path sketch has no profiles (measured).** A sketch holding only an open line renders `profiles=0.0`, so `unused_sketch` never fires on it.
- **Sweep path from a sketch-space `Point3D.create(0, 0, 2)` fails (measured).** `sweepFeatures.add` raised `ASM_PATH_TANGENT`. Fix: convert model points with `sketch.to_sketch` (`modelToSketchSpace`).

## Bodies & geometry queries

- **`BRepFace` has no `startVertex`; only `BRepEdge` does.** Symptom: `AttributeError` on a face. Fix: `edge.startVertex` / `edge.endVertex` for edge ends; `face.pointOnFace` for a point on the face. Stub check: `startVertex` on `BRepEdge` only (`adsk/fusion.py`).
- **`bRepBodies.item(0)` is not the main body (measured: the back plate came first).** Fix: pass bodies by reference.
- **`face.geometry.normal` ignores face orientation.** Symptom: floor normals point the wrong way. Fix: use `evaluator.getNormalAtPoint` or `features.face_normal`.
- **`evaluator.parametricRange()` returns a `BoundingBox2D`, not a tuple.** Fix: read the box's min and max accessors.
- **PointContainment enum names.** Cause: the enum members are `PointInsidePointContainment`, `On` and `Outside`; `...SolidPointContainment` does not exist. Fix: use the typed `POINT_*` constants from fusion_kit `enums`.
- **Overhang threshold.** Cause: a downward-facing face with n_z > 0.707 counts as overhang (45° limit). An exact 45° chamfer evaluates to 0.7071 and trips the check. Fix: give the chamfer face 46° from horizontal (n_z 0.695; `features.bed_chamfer` default). 44° from horizontal gives n_z 0.719 and FAILS. Check in the print orientation, and flip the part if it is printed inverted.
- **Silent loss of bodies or features.** Fix: run `report.report(comp)` on each component. It prints bodies, lumps, bounding box and timeline health.
- **Fusion attributes are not carried through mirror or join.** Symptom: an attribute on a seed body (e.g. the dfam `grip_fin` face tag) is gone after `MirrorFeature` or `combine` join, so the DfAM exemption no longer matches (reported: 8 false `vertical_corner` FAILs). Fix: tag after the final join, or run the tagging builder (`grip_fit`) on the final joined body.

## Assemblies & joints

- **`contactSets.add([occ, ...])` takes a Python list, not an `ObjectCollection`.** Fix: pass a Python list. Set `isContactAnalysisEnabled = True` before `isContactSetAnalysis`.
- **`analyzeInterference` checks the nominal pose only.** Fix: for moving parts, rotate the occurrence with `occ.transform2 = m` (built with `Matrix3D.setToRotation`) at each travel limit and re-check. `transform` is absent from the stubs; `transform2` was verified live. `report.interference` wraps the check.
- **Printability check on a second occurrence of the same component gives a false overhang FAIL.** Measured: a rotated twin with the seed's up-rotation flag set to false reported 4921 mm² of overhang. Fix: run the printability check on the seed occurrence only, once per component.
- **Suppressed timeline item reports `healthState` 3, not an error (measured).** `TimelineObject.isSuppressed = True` on an extrude gave `healthState=3` (`SuppressedFeatureHealthState`, measured as the enum value 3), `isSuppressed=True`, `errorOrWarningMessage=''`. Fix: `graph.collect` treats a suppressed item as healthy (status SUPP, no `unhealthy` flag). Before the fix it rendered FAIL.
- **Fillet too large for the body fails with `healthState` 2 (measured).** Fillet radius `fil_r` changed live from 1 mm to 30 mm on a 20 x 10 x 10 mm block: `healthState=2` (`ErrorFeatureHealthState`, enum value 2), `errorOrWarningMessage` = `"  Nie można utworzyć zaokrąglenia/fazowania o żądanym rozmiarze..."` (Polish UI text, `Obliczenia nie powiodły się` at the end). The fillet row stays in the timeline with the message.
- **Healthy extrude gets the `hardcoded` flag and renders WARN (measured).** Measured on both extrudes of the run (`d7` on `block_extrude`, `d11` on `side_extrude`): a model parameter `taperAngleOne` with `expression='0.0 deg'`, `unit='deg'`, which the user never typed. `graph._is_hardcoded` matches the literal and flags it, so `block_extrude` shows `WARN` with `healthState=0`, `errorOrWarningMessage=''`. Status mapping is correct (any flag = WARN); the flag rule is what fires.

## MCP execution & documents

- **Fusion keeps `sys.modules` across script runs (module cache).** Symptom: stale code runs (measured: an old `select_edges` ran after a change). Fix: always launch via `fusion_kit.loader.run_part(entry)`, which purges `fusion_kit.*`, `parts.*` and the part package. Never `import fusion_kit` directly in execute code.
- Switching FusionKit path: Fusion caches fusion_kit modules in sys.modules across runs; purge fusion_kit* before import (bootstrap snippet) or restart Fusion.
- **Import bootstrap.** Symptom: top-level import fails with `ModuleNotFoundError: fusion_kit`. Fix: inside `run()`, call `sys.path.insert(0, os.environ["FUSIONKIT_PATH"])`, then `from fusion_kit.loader import run_part`, after purging fusion_kit* from sys.modules (see 'Switching FusionKit path').
- **Monkeypatch after the loader purge.** Cause: `loader.run_part` purges `fusion_kit*` from `sys.modules`, so a patch applied before the purge is lost. Fix: apply the patch after the purge, replicating `run_part` by hand.
- **Execute script must define `def run(_context)`.** Symptom: top-level code without `run` fails with `NameError: run`. Behaviour: any exception inside `run()` (including that NameError) makes MCP roll back ALL changes of that call.
- **Probe by raising.** Cause: an exception at the end of an execute script rolls back everything the script did. Fix: build, measure, then `raise RuntimeError(log)`; the log comes back in the error text.
- **Execute call timeouts.** Measured: a single call of 533 s completed. The harness MCP timeout is 900 s. Fix: split long builds and checks into separate `run(context)` calls. A timed-out call keeps Fusion busy, so later calls also time out. Do not retry; wait until Fusion is idle.
- **Read-only diagnostic scripts.** Fix: pass `readOnly: true` to execute. Such scripts run even while a command dialog is open.
- **"command dialog is open" blocks execute.** Cause: a Fusion command is open in the UI. Fix: close it (Esc) in Fusion.
- **`app.data.activeProject` under MCP fails with `InternalValidationError : group`.** Fix: get the project from a known document's `dataFile.parentProject`. Match documents by `dataFile.name` plus `parentProject.id`, never by name alone.
- **`doc.saveAs()` on a new document under MCP returns True but does not save.** Symptom: `isSaved` stays False, and `doc.dataFile` raises `3 : Failed to get temporary file` (polled for 120 s). Fix: check `dataFile` inside a try block after `saveAs`; save manually from the Fusion UI.
- **MCP `ECONNREFUSED`.** Cause: the Fusion MCP server has died. Fix: restart Fusion.
- **Do not edit files under `.fusion_defs`.** Cause: it is a junction to the Autodesk install. It exists so basedpyright can check API calls against the stubs.

## Performance

- **Slow build: time each step.** Fix: one execute per build step, timed with `time.perf_counter()`. Measured: expression-driven sketch dimensions take about 0.4 s each (64 dimensions took 46 s via `sketch.param_poly`). For code-picked or rebuild-only sketches, use `sketch.fixed_poly` and defer computation with `sketch.deferred` (`isComputeDeferred`).
- **Slow build: read `TIME` / `STEP` lines, do not cProfile.** `build_and_check` prints `TIME build|dfam:<body>|check:<body>|interf|sweep|skq|total <s>` live, not in the returned text. `steps.run_steps` prints `STEP <name> <s> s` per step and `STEPS total <s> s`. Sweep prints `TIME probe:<name>=<expr> <s> cache_hits=N`.
- **`sweep=True` is slow: done-gate only.** Each probe is a live `userParameters.itemByName(n).expression` edit plus measure. Keep `sweep=off` per build (`workflow.md`).
- **Chamfer probes are expensive on heavy bodies.** Measured: each add-and-delete probe takes about 5 s. Fix: keep probe count low.
- **Many `join` extrudes onto a heavy body push a build past 300 s.** Cause: each join recomputes all previous features. Fix: extrude each piece as a NewBody and join once at the end with `features.combine`.
