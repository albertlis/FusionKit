# features_edges

Helper card for `fusion_kit/features_edges.py`. Index: `docs/fusion_kit_api.md`.

| helper | signature | replaces raw adsk |
|---|---|---|
| features_edges.faces_by_normal | faces_by_normal(...) — features_edges.py:27 |  |
| features_edges.convex_edges | convex_edges(...) — features_edges.py:159 | `pointContainment` |
| features_edges.edges_at_xy | edges_at_xy(...) — features_edges.py:205 | `edges`, `boundingBox` |
| features_edges.planar_faces | planar_faces(...) — features_edges.py:226 |  |
| features_edges.fillet | fillet(...) — features_edges.py:263 | `filletFeatures`, `addConstantRadiusEdgeSet` |
| features_edges.chamfer | chamfer(...) — features_edges.py:285 | `chamferFeatures`, `addEqualDistanceChamferEdgeSet`, `addTwoDistancesChamferEdgeSet` |
| features_edges.fillet_groups | fillet_groups(...) — features_edges.py:312 | `filletFeatures`, `addConstantRadiusEdgeSet` |
| features_edges.chamfer_groups | chamfer_groups(...) — features_edges.py:335 | `chamferFeatures`, `addEqualDistanceChamferEdgeSet` |
| features_edges.chamfer_greedy | chamfer_greedy(...) — features_edges.py:377 |  |
| features_edges.select_edges | select_edges(...) — features_edges.py:485 | `edges.item`, `startVertex`, `endVertex` |
