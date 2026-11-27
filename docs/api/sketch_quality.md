# sketch_quality

Helper card for `fusion_kit/sketch_quality.py`. Index: `docs/fusion_kit_api.md`.

| helper | signature | replaces raw adsk |
|---|---|---|
| sketch_quality.half_operand | half_operand(expr) — sketch_quality.py:116 |  |
| sketch_quality.pattern_multiple | pattern_multiple(expr) — sketch_quality.py:131 |  |
| sketch_quality.check_facts | check_facts(f, skip) — sketch_quality.py:319 |  |
| sketch_quality.skq_token | skq_token(lines) — sketch_quality.py:344 |  |
| sketch_quality.sketch_facts | sketch_facts(sk) — sketch_quality.py:418 | `profiles`, `profileLoops`, `profileCurves`, `sketchCurves`, `sketchPoints`, `geometricConstraints`, `sketchDimensions`, `isFullyConstrained`, `isFixed`, `isReference`, `objectType` |
| sketch_quality.skip_of | skip_of(sk) — sketch_quality.py:459 | `attributes.itemByName` |
| sketch_quality.check_sketches | check_sketches(sketches, over_budget=None) — sketch_quality.py:501 | over_budget() per sketch -> SKIP skq budget |
| sketch_quality.check | check(design, over_budget=None) — sketch_quality.py:512 | `allComponents` |
| sketch_quality.curve_sig | curve_sig(curve) — sketch_quality.py:354 | `objectType`, `length`, `radius` |
| sketch_quality.cross_dup_lines | cross_dup_lines(items) — sketch_quality.py:375 |  |
