# Contributing to FusionKit

FusionKit is a helper library for Autodesk Fusion scripts, plus a Claude Code plugin. [CLAUDE.md](CLAUDE.md) holds the repo map and the rules this repo follows.

## Development setup

Python 3.12 (the pytest job in CI runs 3.12 and 3.14 on Ubuntu and Windows) and [uv](https://docs.astral.sh/uv/).

```
uv sync --group dev
uv run pytest -q
uv run ruff check .
```

Static check against the Autodesk stubs (optional). Create the junction once; it is gitignored, do not commit it. Never edit files inside the junction:

```
mklink /J .fusion_defs "%APPDATA%\Autodesk\Autodesk Fusion 360\API\Python\defs"
uvx basedpyright fusion_kit
```

Hook selftests (CI runs all four on Ubuntu, Python 3.12):

```
python hooks/cad_simplify_nudge.py --selftest
python hooks/dfam_guard.py --selftest
python hooks/fusion_kit_brief_guard.py --selftest
python hooks/fusion_kit_nudge.py --selftest
```

Plugin manifest check:

```
claude plugin validate .
```

(the root CLAUDE.md warning is expected: it is the contributor guide, not plugin context)

## Tests

- Offline tests (`tests/`) run without Fusion. `tests/conftest.py` stubs `adsk` with `MagicMock`. A test must be able to fail on the bug it targets.
- Live smoke scripts (`tests/smoke/`) run inside Fusion through the Fusion MCP. pytest does not collect them. If a change touches geometry, run the relevant smoke script and report the result in the PR. If you could not run it, say so. The offline tests cannot show that Fusion accepts a generated operation.

## Style

- ruff: line length 120, target py312 (`pyproject.toml`).
- Type hints: `list`, `dict`, `X | None`. Not `List`, `Optional`, `Union`. No `# type: ignore` and no `cast(Any)`.
- Public helpers take mm; Fusion internals are cm. Convert at the boundary and say so in the docstring.
- No try/except that swallows errors inside helpers.
- Timeline items created by builders take `label=` (snake_case role; sketches end in `_sketch`).

## Helper checklist

For a new or changed public helper (module-level def without a leading `_`):

1. Row in `docs/fusion_kit_api.md` with signature and the raw adsk tokens it wraps. `tests/test_api_index_offline.py` enforces the format.
2. Offline test in `tests/` that fails on the bug it targets.
3. Live smoke test in `tests/smoke/` if the helper touches geometry.
4. Verified Fusion behaviour: entry in `docs/gotchas.md` with a "measured" note. Record only what you measured.
5. Keep the module index in `fusion_kit/__init__.py` current.

## Issues

Use the templates in `.github/ISSUE_TEMPLATE/`:

- Bug report.
- Feature request.
- Missing helper: a Fusion operation that needs a raw adsk workaround. Describe the operation, the problem and the workaround. Remove personal names, project names and file paths.

## Pull requests

1. For non-trivial changes, open an issue first.
2. Branch from `main`.
3. Keep the change focused: one helper or one fix per PR where possible.
4. Before opening the PR, run locally: ruff, pytest, the four hook selftests, `claude plugin validate .`.
5. PR description: what changed, how you verified it, and whether live smoke ran in Fusion (state the Fusion version if it did).
6. No hand-written changelog entry. release-please generates `CHANGELOG.md` from commit and PR titles, so write a clear PR title.

## Commits and releases

Commits follow [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`. Add `!` after the type for a breaking change (`refactor!: ...`). PRs are squash-merged, so the PR title becomes the commit message and CI checks it. release-please reads those commits on `main` and keeps a release PR open with the version bump and changelog. Merging that release PR creates the `vX.Y.Z` tag and the GitHub release. Do not bump versions by hand.

## License

By contributing, you agree that your contribution is licensed under the FusionKit License 1.0 ([LICENSE](LICENSE)). Noncommercial purposes are permitted purposes under its terms. Its Attribution section requires projects built with FusionKit to credit it.
