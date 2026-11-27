# features_extrude

Helper card for `fusion_kit/features_extrude.py`. Index: `docs/fusion_kit_api.md`.

| helper | signature | replaces raw adsk |
|---|---|---|
| features_extrude.profile | profile(sketch, outer) — features_extrude.py:34 | `profileLoops`, `areaProperties` |
| features_extrude.profiles | profiles(sketch, predicate) — features_extrude.py:74 |  |
| features_extrude.extrude | extrude(...) — features_extrude.py:83 | `extrudeFeatures`, `setSymmetricExtent`, `setOneSideExtent`, `setTwoSidesExtent`, `ToEntityExtentDefinition`, `ThroughAllExtentDefinition`, `FromEntityStartDefinition` |
| features_extrude.revolve | revolve(...) — features_extrude.py:199 | `revolveFeatures`, `setAngleExtent`, `setOneSideToExtent` |
| features_extrude.shell | shell(...) — features_extrude.py:241 | `shellFeatures`, `ShellFeatureInput.insideThickness` |
| features_extrude.plane_midplane | plane_midplane(...) — features_extrude.py:264 | `constructionPlanes`, `setByTwoPlanes` |
| features_extrude.axis_by_cylinder | axis_by_cylinder(...) — features_extrude.py:286 | `constructionAxes`, `setByCircularFace` |
| features_extrude.single_body | single_body(comp, prefix) — features_extrude.py:308 |  |
