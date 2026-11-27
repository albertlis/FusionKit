# sketch

Helper card for `fusion_kit/sketch.py`. Index: `docs/fusion_kit_api.md`.

| helper | signature | replaces raw adsk |
|---|---|---|
| sketch.new_sketch | new_sketch(...) — sketch.py:40 | `sketches.add` |
| sketch.to_sketch | to_sketch(s, x_mm, y_mm, z_mm) — sketch.py:66 | `modelToSketchSpace` |
| sketch.circle | circle(s, x, y, r, z, diameter, *, concentric_to) — sketch.py:76 | `addByCenterRadius`, `addDiameterDimension`, `addConcentric` |
| sketch.fillet_corner | fillet_corner(...) — sketch.py:119 | `addFillet`, `addRadialDimension` |
| sketch.poly | poly(s, pts, *, lock_axes) — sketch.py:143 | `addByTwoPoints`, `addVertical`, `addHorizontal` |
| sketch.rect | rect(s, a, b) — sketch.py:179 | `addByTwoPoints`, `addHorizontal`, `addVertical` |
| sketch.dim | dim(...) — sketch.py:208 | `addDistanceDimension` |
| sketch.project_point | project_point(s, entity) — sketch.py:226 | `project2` |
| sketch.dim_from | dim_from(...) — sketch.py:235 |  |
| sketch.polar_point | polar_point(s, centre_point, r, a, *, radius_mm, angle_deg) — sketch.py:260 | `addAngularDimension`, `addHorizontal` |
| sketch.anchor | anchor(s, p, h, v, ref) — sketch.py:305 | `addCoincident`, `addVerticalPoints`, `addHorizontalPoints` |
| sketch.mm | mm(expr) — sketch.py:367 | `evaluateExpression` |
| sketch.fix_point | fix_point(...) — sketch.py:425 |  |
| sketch.model_axis_to_sketch | model_axis_to_sketch(s, model_axis) — sketch.py:513 |  |
| sketch.param_poly | param_poly(..., mode="vertices"/"segments") — sketch.py:599 | `deleteMe`, `addHorizontal`, `addVertical`, `addDistanceDimension` |
| sketch.fixed_poly | fixed_poly(s, pts) — sketch.py:671 | `isFixed` |
| sketch.rect_two_corners | rect_two_corners(...) — sketch.py:694 |  |
| sketch.rect_xy | rect_xy(s, x0, y0, x1, y1) — sketch.py:706 |  |
| sketch.rect_xy_sketch | rect_xy_sketch(...) — sketch.py:711 |  |
| sketch.deferred | deferred(s) — sketch.py:745 | `isComputeDeferred` |
| sketch.offset_curves | offset_curves(...) — sketch.py:718 | `createOffsetInput`, `addOffset2`, `ValueInput.createByString` |
| sketch.center_on_origin | center_on_origin(...) — sketch.py:733 | `addByTwoPoints`, `addMidPoint`, `isConstruction` |
