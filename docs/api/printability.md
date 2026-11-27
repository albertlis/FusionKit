# printability

Helper card for `fusion_kit/printability.py`. Index: `docs/fusion_kit_api.md`.

| helper | signature | replaces raw adsk |
|---|---|---|
| printability.rot_x | rot_x(deg) — printability.py:157 |  |
| printability.rot_y | rot_y(deg) — printability.py:162 |  |
| printability.rot_z | rot_z(deg) — printability.py:167 |  |
| printability.compose | compose(*mats) — printability.py:183 |  |
| printability.rotate_mesh | rotate_mesh(mesh, rot) — printability.py:199 |  |
| printability.weld | weld(mesh, tol_mm) — printability.py:206 |  |
| printability.check | check(...) — printability.py:558 |  |
| printability.sweep | sweep(...) — printability.py:815 |  |
| printability.clear_rays | clear_rays(...) — printability.py:894 |  |
| printability.march_thickness | march_thickness(...) — printability.py:967 |  |
| printability.mesh_from_body | mesh_from_body(body, quality) — printability.py:1027 | `createMeshCalculator` |
| printability.check_body | check_body(...) — printability.py:1528 | `_brep_slit_ceiling`, `_brep_short_bridge`, `CANTILEVER_MAX_MM`, `_brep_short_cantilever` |
| printability.wall_thickness_mm | wall_thickness_mm(...) — printability.py:1597 | `pointContainment` |
