# CLAUDE.md

Guidance for Claude Code working ON the FusionKit repository. To build Fusion models WITH FusionKit, read `skills/fusion-kit/SKILL.md`.

## What this repo is

- `fusion_kit/`: helper library for Autodesk Fusion scripts. Fusion runs it with its embedded Python, so it uses stdlib + `adsk` only. No third-party imports.
- Claude Code plugin: skill, `CadSimplifier` agent, four hooks, MCP config.
- Public repo. No personal data, private project names, user names or machine paths in code, docs, tests or hooks.

## Repo map

- `fusion_kit/`: helper modules. Module index in `fusion_kit/__init__.py`: component, dfam, enums, expr, features, holes, joints, loader, printability, report, sketch, steps, timeline, user_params, view.
- `docs/fusion_kit_api.md`: helper index (one row per public def). Format enforced by `tests/test_api_index_offline.py`.
- `docs/gotchas.md`: verified Fusion behaviour. Each entry carries a "measured" note.
- `docs/dfam_guidelines.md`: FDM/PETG DfAM rules used by the `dfam` module and `hooks/dfam_guard.py`.
- `tests/`: offline tests. `tests/conftest.py` installs `MagicMock` stubs for `adsk.core` and `adsk.fusion` when `adsk` is not importable.
- `tests/smoke/`: live scripts that run INSIDE Fusion through the MCP. pytest ignores them (`addopts` in `pyproject.toml`).
- `hooks/`: `fusion_kit_nudge.py`, `fusion_kit_brief_guard.py`, `dfam_guard.py`, `cad_simplify_nudge.py`, wired in `hooks/hooks.json`. Each supports `--selftest`.
- `skills/fusion-kit/SKILL.md`: skill for Fusion modelling sessions.
- `agents/CadSimplifier.md`: read-only timeline review agent.
- `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.mcp.json`: plugin manifest, marketplace entry, MCP servers.
- `.github/`: CI (`workflows/ci.yml`) and issue templates.
- `CONTRIBUTING.md`, `CHANGELOG.md`, `CITATION.cff`, `PRIVACY.md`, `LICENSE`.

## Commands

```
uv sync --group dev
uv run pytest -q
uv run ruff check .
uvx basedpyright fusion_kit
python hooks/<name>.py --selftest
claude plugin validate .
```

(the root CLAUDE.md warning is expected: it is the contributor guide, not plugin context)

`uvx basedpyright fusion_kit` needs the Autodesk stubs junction, created once on Windows (`.fusion_defs` is gitignored, never commit it):

```
mklink /J .fusion_defs "%APPDATA%\Autodesk\Autodesk Fusion 360\API\Python\defs"
```

CI runs `ruff check`, `pytest` and the four hook selftests (`cad_simplify_nudge`, `dfam_guard`, `fusion_kit_brief_guard`, `fusion_kit_nudge`).

## Rules for changes

- Public helpers take mm. Fusion internals are cm (`.value` and `evaluateExpression` return cm). Convert at the boundary.
- Every new or changed public helper: row in `docs/fusion_kit_api.md`, an offline test that can fail, and a live smoke test in `tests/smoke/` when it touches geometry.
- `label=` is mandatory on timeline items created by builders: snake_case role, sketches end in `_sketch`. Raw entities get `timeline.set_label`.
- No try/except that swallows errors in helpers. An exception is the debug output.
- Verified Fusion behaviour goes to `docs/gotchas.md` with a "measured" note. Unverified behaviour does not go there.
- Style: ruff (line-length 120, target py312). Type hints use `list` and `X | None`, not `List` and `Union`.

## Live testing in Fusion

- Scripts run through `fusion_kit.loader.run_part(entry)`, which purges cached `fusion_kit` modules. Never `import fusion_kit` directly in execute code.
- The execute script and the entry file both define `def run(_context)`.
- Any exception inside `run()` makes MCP roll back the whole call.
- MCP call limit is about 900 s. Split long builds into several calls.
- Fusion must be open with the API server enabled (Preferences > General > API).

## MCP

- Official Autodesk Fusion MCP, built into Fusion (Preferences > General > API), endpoint `http://127.0.0.1:27182/mcp`. Tools: `fusion_mcp_execute` (scripts; `readOnly: true` for inspection), `fusion_mcp_read` (screenshot, document, apiDocumentation; read before guessing an API), `fusion_mcp_update` (undo, redo).
- fusion-cad MCP (`https://github.com/Mfrostbutter/fusion-cad-mcp`, MIT, optional): typed operations plus `measure`, `bounding_box`, `volume`, `interference_check`, `audit_feature_health`, `drive_joint`, `find_gotcha`, `find_pattern`, `find_api`. Used for verification. FusionKit does not depend on it.
