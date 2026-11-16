<div align="center">

<img src="docs/img/hero.png" alt="Demo bracket built with FusionKit: plate with mounting holes, gable-slot wall, gussets and fillets" width="720">

# FusionKit

**Reliable, parametric Autodesk Fusion modelling for scripts and AI agents**

[![CI](https://github.com/albertlis/FusionKit/actions/workflows/ci.yml/badge.svg)](https://github.com/albertlis/FusionKit/actions/workflows/ci.yml)
![license](https://img.shields.io/badge/license-FusionKit%201.0-blue)
![Python 3.12 | 3.14](https://img.shields.io/badge/python-3.12%20%7C%203.14-blue)
![Autodesk Fusion](https://img.shields.io/badge/Autodesk%20Fusion-API-orange)
![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-purple)

</div>

FusionKit is a small helper library that sits between your code and the Autodesk Fusion API (`adsk`). It turns the raw API into typed, mm-based helpers that build parametric models, catch silent geometry loss, and check parts for FDM printability. It ships as a plain Python package and as a Claude Code plugin, so an AI agent drives Fusion through the same guard rails a careful engineer would use.

FusionKit runs inside Fusion's embedded Python. It depends only on the standard library and `adsk`.

Fusion ships Python 3.14 (tested on 3.14.7); offline tests run on 3.12 and 3.14.

## Why FusionKit

The raw Fusion API works, but it has sharp edges that cost hours when you hit them for the first time. FusionKit encodes the fixes once.

| The problem with the raw API | What FusionKit does |
|---|---|
| **Module cache across runs.** Fusion keeps `sys.modules` between `execute` calls, so an edited helper silently runs its old code. | `loader.run_part` purges `fusion_kit.*` and the part package before every run. |
| **Sketch constraint jitter.** Dimensions on exactly shared coordinates trigger over-constraint errors and inferred H/V constraints. | `sketch.rect` and `sketch.param_poly` offset vertices safely and dimension them explicitly. |
| **Internal units are cm.** `evaluateExpression(expr, "mm")` also returns cm, and `.value` is cm. | Every public helper takes mm and converts at the boundary. |
| **Silent geometry loss.** A join after a hole cut closes the hole, and a chamfer on a concave edge does nothing without an error. | `report.report` prints bodies, lumps, bounding box and volume, and `convex_edges` and `chamfer_greedy` probe edges before they fail quietly. |
| **Timeline noise.** Features named by Fusion (`Extrude3`) make the timeline unreadable and the design hard to edit. | `label=` is mandatory on every builder. `report` prints `timeline_unnamed`, which must be 0. |
| **Enum and expression pitfalls.** Typed enums are easy to misspell, and `round()` in expressions is not what Python does. | `enums` holds typed constants, and `expr` builds expressions with known behaviour. |
| **3D-printing failures found late.** Overhangs and thin walls show up after printing. | `dfam` and `printability` run FDM (PETG) rule checks on every body, in print orientation. |

The rules behind these helpers are not guesses. [`docs/gotchas.md`](docs/gotchas.md) lists **47 verified Fusion behaviours**, and the DfAM rules are documented in [`docs/dfam_guidelines.md`](docs/dfam_guidelines.md).

## Features

**Parametric building blocks**

- `sketch`: create sketches on named planes or faces, add dimensioned rectangles and circles, anchor points to the origin, and keep profiles fully constrained.
- `features`: extrude, revolve, fillet, chamfer, combine (join, cut, intersect), mirror, rectangular, circular and grid patterns, and offset planes. Profiles are selected by geometry, not by index.
- `holes`: simple, counterbore and countersink holes, screw slots with clearance, teardrop holes for horizontal bores, and seam notches.
- `joints`: dovetail profiles and joints, with clearances and mating-face marking.
- `user_params` and `expr`: create User Parameters from a single spec dictionary, and build parametric lengths and expressions that stay live.

**Script infrastructure**

- `loader`: runs a part entry file with a clean module cache.
- `steps`: runs a build as a sequence of steps, each in its own `execute` call, so long builds stay under the MCP timeout.
- `timeline`: labels timeline items, checks label rules, and dumps timeline health.
- `component` and `view`: create sub-components, set view orientation, and save a 600x400 PNG with a fixed camera.
- `enums`: typed constants for Fusion enums (feature operations, extents, pattern types, point containment).

**Verification and manufacturing**

- `report`: one-line self-check per component, plus world-space bounding boxes, point and interior probes, pairwise interference, and overhang scans.
- `printability`: orientation sweeps, overhang and bed-contact checks, wall thickness, and line-of-sight ray checks on a triangle mesh of the body.
- `dfam`: FDM rule scan per body (teardrop holes, bed chamfers, warp grooves, vertical corners, minimum walls, PETG wall thickness and more). FAIL findings block the Claude Code Stop hook. A body can opt out of a rule with the `dfam_skip` attribute and a written reason.

Every public helper is listed with its signature in [`docs/fusion_kit_api.md`](docs/fusion_kit_api.md). Each row there is enforced by an offline test.

## Quick start

FusionKit needs Fusion open with its MCP server enabled. Both quick starts assume that.

1. Open Autodesk Fusion. It works on the free Personal plan.
2. Go to **Preferences > General > API** and enable the Fusion MCP server. Its endpoint is `127.0.0.1:27182/mcp` (plain HTTP, local only).

### A. Claude Code plugin

In Claude Code, add the marketplace and install the plugin:

```
/plugin marketplace add albertlis/FusionKit
/plugin install fusion-kit@fusionkit
```

The plugin wires up the Fusion MCP server (`.mcp.json`), the `fusion-kit` skill, the `CadSimplifier` review agent and four hooks. The skill finds FusionKit through the `FUSIONKIT_PATH` environment variable. If it is not set, the skill uses the plugin root. The plugin cache path changes between plugin versions, so setting the variable persistently is the safer choice. On Windows:

```
setx FUSIONKIT_PATH "C:\path\to\FusionKit"
```

Restart Fusion afterwards, because a running process does not see new environment variables.

For the optional `fusion-cad` MCP server, see [Recommended MCP stack](#recommended-mcp-stack).

### B. Manual install

```bash
git clone https://github.com/albertlis/FusionKit.git
cd FusionKit
uv sync --group dev
```

Set `FUSIONKIT_PATH` to the FusionKit root (the directory that contains `fusion_kit/`). Then start every Fusion script through the loader:

```python
import os
import sys


def run(_context):
    fk = os.environ.get("FUSIONKIT_PATH", r"C:\path\to\FusionKit")
    for m in [m for m in sys.modules if m == "fusion_kit" or m.startswith("fusion_kit.")]:
        del sys.modules[m]
    sys.path.insert(0, fk)
    from fusion_kit.loader import run_part

    run_part(r"C:\path\to\my_bracket\my_bracket.py", "")
```

Send this through the Fusion MCP `execute` tool, or paste it into Fusion's **Scripts and Add-Ins** dialog. Do not `import fusion_kit` at the top level of an `execute` script. The module cache then keeps stale helpers between runs.

Entry files must define `def run(_context)`. The loader expects the layout `<project>/<pkg>/<pkg>.py`, with optional `parts/` and `anim/` subfolders.

## Example

A 60 x 30 x 4 mm plate with two bosses, patterned along the plate edge. Each entry is parametric, so changing `plate_length` in Fusion rebuilds the part. The file is `my_bracket/my_bracket.py`, run through `run_part` as shown above. Because the loader adds the FusionKit root to `sys.path` before it executes the entry file, the top-level `fusion_kit` imports below are safe.

```python
import adsk.core
import adsk.fusion

from fusion_kit import component, features, report, sketch, user_params

PARAMS = {
    "plate_length": ("60 mm", "mm", "plate length along X"),
    "plate_width": ("30 mm", "mm", "plate width along Y"),
    "plate_thickness": ("4 mm", "mm", "plate thickness along Z"),
    "boss_diameter": ("8 mm", "mm", "boss diameter"),
    "boss_height": ("3 mm", "mm", "boss height above the plate"),
    "boss_edge_offset": ("10 mm", "mm", "boss centre to plate end"),
}


def run(_context: str) -> None:
    design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
    user_params.ensure_params(design, PARAMS)
    comp = component.new_comp(design.rootComponent, "bracket")

    # Plate: rectangle dimensioned by parameters, anchored to the origin, extruded.
    plate_sk = sketch.new_sketch(comp, "xy", label="plate_sketch")
    bottom, right, _top, _left = sketch.rect(plate_sk, (0, 0, 0), (60, 30, 0))
    sketch.anchor(plate_sk, bottom.startSketchPoint)
    sketch.dim(plate_sk, bottom.startSketchPoint, bottom.endSketchPoint, "plate_length")
    sketch.dim(plate_sk, right.startSketchPoint, right.endSketchPoint, "plate_width")
    features.extrude(comp, features.profile(plate_sk), "plate_thickness", label="plate")

    # Boss: one seed, starting on the plate top (start_offset), joined to the plate.
    boss_sk = sketch.new_sketch(comp, "xy", label="boss_sketch")
    boss = sketch.circle(boss_sk, 10, 15, 4, diameter="boss_diameter")
    sketch.anchor(boss_sk, boss.centerSketchPoint, h="boss_edge_offset", v="plate_width / 2")
    boss_feat = features.extrude(
        comp,
        features.profile(boss_sk),
        "boss_height",
        op="join",
        start_offset="plate_thickness",
        label="boss",
    )

    # Repeat along the plate edge: qty 2, spacing = length - 2 * edge offset puts the second boss at the opposite end.
    features.pattern_rectangular(
        comp,
        [boss_feat],
        bottom,
        "2",
        "plate_length - 2 * boss_edge_offset",
        label="boss_row",
    )

    report.report(comp)
```

Two notes on the example:

- Every dimension is a User Parameter. The helpers take expression strings, so the model stays editable from the Fusion parameters dialog.
- `label=` names each timeline item (`plate`, `boss`, `boss_row`, `plate_sketch`). That is why `report` shows zero unnamed items.

## Demo

[`examples/demo_bracket/`](examples/demo_bracket/README.md) is a printable PETG bracket built only from FusionKit helpers: a 90 x 60 x 5 mm plate with four mounting holes, a 40 mm wall with a gable-roof slot, two gussets, and the DfAM treatment for each feature.

<div align="center">
<img src="docs/img/underside.png" alt="Underside of the demo bracket: warp relief grooves and mounting holes" width="720">

<sub>Underside: 0.8 x 0.3 mm warp relief grooves on a 30 mm pitch, and the four mounting holes.</sub>
</div>

What it shows:

- **Pattern holes:** one mounting hole, cut last to avoid closing it, repeated with `features.pattern_grid`.
- **Fillets and chamfers:** r 3 mm fillets on the outer vertical corners, 0.5 mm chamfers on the slot corners.
- **Bed chamfer:** 46 degree chamfer on the bed edge, the steepest angle the DfAM rule allows.
- **Warp relief grooves:** 0.8 x 0.3 mm grooves in the bed face to reduce warping of the large flat base.
- **Gable-roof slot:** a pointed 46.8 degree roof, so the slot has no flat ceiling.
- **Self-check:** `report` ends with no DfAM findings, and `printability.check_body(min_angle_deg=45)` passes.

Sample `report()` output from a live run:

```
demo_bracket: bodies=1 lumps=1 bbox=90.0x60.0x40.0mm vol=42.139cm3 sketches=7/7 constrained design_timeline_unhealthy=0 timeline_unnamed=0 timeline_numbered=0 dfam=0F/0W
```

To run it, use the bootstrap from [Manual install](#b-manual-install) with `examples/demo_bracket/demo_bracket.py` as the entry. The [demo README](examples/demo_bracket/README.md#run) has the exact snippet and the step names for partial runs.

## What report() prints

`report.report(comp)` ends every part script. It prints one line per component, then one line per DfAM finding. The sample below is illustrative. The field names and formats come from `fusion_kit/report.py` and `hooks/dfam_guard.py`, but the values are invented.

```
bracket: bodies=1 lumps=1 bbox=60.0x30.0x4.0mm vol=6.240cm3 sketches=2/2 constrained design_timeline_unhealthy=0 timeline_unnamed=0 timeline_numbered=0 dfam=0F/1W
DFAM_WARN <rule>: <message about one body>
```

What to check:

- `bodies` and `lumps`: a body count that drops after a join or cut means geometry was lost.
- `bbox` and `vol`: compare against the numbers you expect from the parameters.
- `sketches=N/Mconstrained`: every sketch should be fully constrained.
- `design_timeline_unhealthy=0`: no feature has failed.
- `timeline_unnamed=0`: must be zero. `timeline_numbered` lists `x_1..x_n` names, which often mark a pattern that should have been a seed. Justify it or replace it.
- `dfam=<nF>F/<nW>W` and the `DFAM_FAIL` and `DFAM_WARN` lines: each finding names the rule. A FAIL blocks the Claude Code Stop hook until it is fixed or opted out.

Read the output. A green line with a FAIL below it is not a pass.

## Recommended MCP stack

FusionKit runs scripts through Fusion's own MCP server. A second server is optional.

| Server | What it is for | Required |
|---|---|---|
| **Autodesk Fusion MCP** (`autodesk-fusion`, `127.0.0.1:27182/mcp`) | Built into Fusion, enabled under **Preferences > General > API**. Gives `fusion_mcp_execute` for scripts and document operations, `fusion_mcp_read` for screenshots, documents and API docs, and `fusion_mcp_update` for undo and redo. | Yes |
| **fusion-cad-mcp** ([github.com/Mfrostbutter/fusion-cad-mcp](https://github.com/Mfrostbutter/fusion-cad-mcp), MIT) | A typed tool layer in front of the Fusion MCP server. Adds measurement tools (`measure`, `bounding_box`, `volume`, `mass`), `interference_check`, `drive_joint` for moving parts, `audit_feature_health`, and knowledge lookups (`find_gotcha`, `find_pattern`, `find_api`). Optional, but the skill and `CadSimplifier` use it when present. | No |

Install `fusion-cad-mcp` by following its own README. The plugin's `.mcp.json` registers it as the server `fusion-cad`, which runs the command `fusion-cad-mcp`.

## Plugin contents

The Claude Code plugin (`.claude-plugin/plugin.json`, marketplace `fusionkit`) contains the following.

**Skill**

- `skills/fusion-kit/SKILL.md`: the setup check, the script bootstrap, the project layout, the build workflow, design-intent rules, tool routing, the eight most important gotchas and the missing-helper process.

**Agent**

- `agents/CadSimplifier.md`: a read-only design-intent reviewer. Its frontmatter sets `name: CadSimplifier`, `effort: low`, a read-only tool list and `omitClaudeMd: true`. It reads the live timeline with one read-only `execute` call, looks for repeated features, symmetric halves and per-instance formulas, and returns a ranked recipe. It does not change the design.

**Hooks** (`hooks/hooks.json`)

- `fusion_kit_nudge.py` (PreToolUse on Fusion `execute` and on `Write`/`Edit` of part files): flags raw `adsk` calls that already have a `fusion_kit` helper. It never blocks.
- `fusion_kit_brief_guard.py` (PreToolUse on `Agent`): denies, once per session, a sub-agent brief that concerns Fusion but does not reference `docs/fusion_kit_api.md`.
- `dfam_guard.py` (PostToolUse on `execute` and Stop): parses `report()` output and blocks Stop while DfAM FAIL findings remain.
- `cad_simplify_nudge.py` (PostToolUse on `execute`, `Agent`, `Write` and `Edit`, and Stop): reminds you to run `CadSimplifier` after a part's `report()` is green and before a commit.

**MCP configuration**

- `.mcp.json`: the `autodesk-fusion` HTTP server and the optional `fusion-cad` stdio server.

## Type checking with Autodesk stubs

Fusion's Python stubs catch calls to API members that do not exist. They are not part of this repository. Create a gitignored junction once, pointing at the stubs that Fusion installs:

```
mklink /J .fusion_defs "%APPDATA%\Autodesk\Autodesk Fusion 360\API\Python\defs"
```

Then run the static check before each `execute`:

```
uvx basedpyright fusion_kit
```

The junction is read-only in practice: never edit files inside it. FusionKit's own code uses `list`, `dict` and `X | None` type hints, and does not use `# type: ignore` or `cast(Any)`.

## Testing

Offline tests run without Fusion. `tests/conftest.py` stubs the `adsk` package with `MagicMock`, so the helpers' logic is tested without geometry. A test must be able to fail on the bug it targets.

```
uv sync --group dev
uv run pytest -q
uv run ruff check .
```

Live smoke scripts in `tests/smoke/` run inside Fusion through the MCP server. pytest does not collect them. A change that touches geometry needs a smoke run in Fusion, and the result must be reported. The offline tests cannot show that Fusion accepts a generated operation.

CI (`.github/workflows/ci.yml`) runs ruff and pytest on Ubuntu and Windows with Python 3.12 and 3.14, and it runs the four hook self-tests:

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

## Documentation

- [`docs/fusion_kit_api.md`](docs/fusion_kit_api.md): helper index. Every public helper, its signature and the raw `adsk` calls it wraps. Read it before writing a part.
- [`docs/gotchas.md`](docs/gotchas.md): 47 verified Fusion behaviours, grouped by units and parameters, sketches, features, bodies and geometry, assemblies and joints, MCP execution and documents, and performance.
- [`docs/dfam_guidelines.md`](docs/dfam_guidelines.md): FDM and PETG rules, which are FAIL and which are WARN, and their sources.
- [`CONVENTIONS.md`](CONVENTIONS.md): repository layout, naming and vocabulary.
- [`CLAUDE.md`](CLAUDE.md): the repository map and the rules this repo follows.

## Roadmap

These are plans, not commitments.

- More DfAM rules, and better coverage of the rules that currently only appear as reference notes.
- Assembly and joint helpers beyond the current dovetail, built on the same verified-behaviour process.
- Installation without a Python package index: updates arrive through plugin updates.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) first. In short:

1. For non-trivial changes, open an issue first. Use the bug, feature or missing-helper template.
2. Branch from `main`, and keep each pull request to one helper or one fix.
3. Before you open a pull request, run `uv run ruff check .`, `uv run pytest -q`, the four hook self-tests and `claude plugin validate .`.
4. A new public helper needs a row in `docs/fusion_kit_api.md`, an offline test, a live smoke test if it touches geometry, and a measured entry in `docs/gotchas.md`.
5. Add an entry under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md).

Contributions are licensed under the FusionKit License 1.0 (see below).

## License and attribution

FusionKit is licensed under the **FusionKit License 1.0** (source-available, noncommercial, attribution required). The full text is in [LICENSE](LICENSE).

In short:

- Noncommercial use, personal use, research and use by charitable, educational and public organizations are permitted purposes.
- The Attribution section requires that any project, design, model, file, publication, video or repository you create with FusionKit displays a clear credit reading **Built with FusionKit (https://github.com/albertlis/FusionKit)**. For source repositories, put it in the README.

Add this line to the README of any project that uses FusionKit:

```markdown
Built with FusionKit (https://github.com/albertlis/FusionKit)
```

To show the credit as a badge, paste this:

```markdown
[![Built with FusionKit](https://img.shields.io/badge/Built%20with-FusionKit-blue)](https://github.com/albertlis/FusionKit)
```

## Acknowledgements

- The Autodesk Fusion API and its embedded Python environment.
- [fusion-cad-mcp](https://github.com/Mfrostbutter/fusion-cad-mcp) by Mfrostbutter, the optional typed tool layer described above.
- The Claude Code plugin system, which carries the skill, agent and hooks.

## Disclaimer

FusionKit is an independent project. It is not affiliated with, endorsed by or sponsored by Autodesk, Inc. Autodesk and Fusion are trademarks of Autodesk, Inc. (Fusion 360 is also a trademark of Autodesk.) The software is provided as is, without warranty. Check every printed or machined part against its real requirements before you rely on it.
