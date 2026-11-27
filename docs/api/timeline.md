# timeline

Helper card for `fusion_kit/timeline.py`. Index: `docs/fusion_kit_api.md`.

| helper | signature | replaces raw adsk |
|---|---|---|
| timeline.check_label | check_label(label) — timeline.py:27 |  |
| timeline.set_label | set_label(entity, label) — timeline.py:59 |  |
| timeline.source_of | source_of(entity) — timeline.py:71 |  |
| timeline.label_stats | label_stats(design) — timeline.py:85 |  |
| timeline.param_index | param_index(design) — timeline.py:125 | `modelParameters`, `createdBy`, `value`, `unit`, `expression` (one walk; reuse in loops) |
| timeline.item_params | item_params(design, feature, index=None) — timeline.py:139 | `modelParameters`, `createdBy`, `unit`, `value`, `expression` |
| timeline.dump | dump(design) — timeline.py:187 |  |
| timeline.run | run(_context) — timeline.py:225 |  |
