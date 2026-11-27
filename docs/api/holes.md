# holes

Helper card for `fusion_kit/holes.py`. Index: `docs/fusion_kit_api.md`.

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
