# Conventions

## Layout
- `fusion_kit/` — library. One domain per module, flat (no subpackages). Private module = `_name.py`.
- `tests/` — offline pytest (`test_*`), no Fusion needed. `tests/smoke/` — live scripts run inside Fusion.
- `hooks/` — Claude Code hook scripts + `hooks.json` wiring.
- `skills/<skill-name>/SKILL.md`, `agents/<AgentName>.md` — plugin content.
- `docs/` — `fusion_kit_api.md` (helper index), `api/<module>.md` (per-module helper cards), `gotchas.md`, `dfam_guidelines.md`.
- `.claude-plugin/`, `.github/` — packaging and issue templates only.

## Naming
- Module: snake_case domain noun, plural for collections — `holes.py`, `features.py`, `printability.py`.
- Public function: verb_noun — `check_holes`, `set_skip`, `parse_skip`. Factories `new_<noun>` — `new_comp`.
- Predicate: `is_`/`has_` — `is_full_cylinder`.
- Lengths in mm: suffix `_mm` on params and returns — `teardrop_peak_mm(r_mm)`. Angles `_deg`. Internal cm never public.
- Timeline builders: keyword `label=` mandatory. Label = snake_case role — `side_wall`; sketches end `_sketch`.
- Private helper: `_name` — `_span_deg`. Short names (`_dot`, `_sub`) allowed only for vector math.
- Class: PascalCase noun — `Finding`, `HoleFact`. Fact dataclasses end `Fact`.
- Constant: UPPER_SNAKE — `POINT_INSIDE` (enums.py).
- User Parameter: full English words — `side_wall_thickness`, not `wall_t`.
- Offline test: `tests/test_<module|helper>_offline.py`. Smoke: `tests/smoke/fusion_smoke_<topic>.py`.
- Hook: `<topic>_<nudge|guard>.py` — nudge = advises, guard = denies. Each hook has `--selftest`.
- Docs: snake_case `.md`; plugin-mandated names (`SKILL.md`) exempt.

## Vocabulary
- comp — `adsk.fusion.Component` — component_obj, cmp
- occ — `Occurrence` — instance, inst
- body — `BRepBody` — solid, b (outside 3-line scope)
- label — timeline item name — tag, title
- finding — one DfAM result — issue, violation
- skip — DfAM opt-out rule map — ignore, exclude, waiver
- gap — missing fusion_kit op — todo, missing
- graph — build graph, one `GraphNode` per timeline item (`graph.py`) — tree, dag, map
- skq — sketch_quality finding line prefix (`SKQ_WARN`) — sq, sketchq
- Allowed abbreviations: comp, occ, expr, param, mm, deg, dfam, bbox, skq.

## Banned names
`utils`, `utils2`, `helper(s)` (in library), `misc`, `common`, `core`, `base`, `old_*`, `tmp_*`, `final*`, `*_v2`, `legacy`; bare `data`/`info`/`manager`/`handler`/`process`; unit-less length params (`width` → `width_mm`).

## Where new code goes
- New op used by >=2 mechanisms → existing domain module; new module only for a new domain.
- New public helper → also a row in `docs/api/<module>.md` + offline test.
- New Fusion quirk → `docs/gotchas.md`. New print rule → `docs/dfam_guidelines.md` + `dfam.py`.
- Shared vector math → `fusion_kit/_vec.py` (not copied per module).
- New hook → `hooks/` + entry in `hooks.json`.
