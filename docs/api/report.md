# report

Helper card for `fusion_kit/report.py`. Index: `docs/fusion_kit_api.md`.

| helper | signature | replaces raw adsk |
|---|---|---|
| report.report | report(comp, dfam) — report.py:33 |  |
| report.design_stats | design_stats(design) — report.py:80 |  |
| report.report_data | report_data(comp, dfam, *, stats, skq) — report.py:98 |  |
| report.format_report | format_report(data) — report.py:146 |  |
| report.world_bbox | world_bbox(occ) — report.py:170 |  |
| report.inside | inside(body, x_mm, y_mm, z_mm) — report.py:184 | `pointContainment` |
| report.inside_runs | inside_runs(...) — report.py:222 | `pointContainment` |
| report.merge_runs | merge_runs(zs, flags) — report.py:194 |  |
| report.interference | interference(design, items) — report.py:257 | `createInterferenceInput`, `analyzeInterference` |
| report.overhangs | overhangs(...) — report.py:302 | `parametricRange`, `isParameterOnFace`, `getNormalAtParameter` |
