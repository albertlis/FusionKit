# How we work with Claude on CAD

This page describes how we drive Fusion 360 from Claude Code with the FusionKit plugin. It covers who does what, how we write briefs, the build loop, and which checks are hard controls and which are only reminders.

It is not the Fusion API reference. For that, start with [fusion_kit_api.md](fusion_kit_api.md) and [gotchas.md](gotchas.md).

Nothing here stops every mistake. The model is wrong sometimes, Fusion is wrong sometimes, and the checks catch most of it. The workflow is there so that we see the rest.

## The team

The main session runs on Opus. It plans, writes briefs, reads results and makes the decisions. It edits code itself only for one or two lines. Anything bigger goes to a worker.

| Role | Model | Job |
|---|---|---|
| Main session | Opus | Plans, writes briefs, checks evidence, decides |
| `SimpleTask` | Haiku | Default worker. One small task per call, about 5 minutes |
| `TestRunner` | Haiku | Runs the test or type-check command and reports tersely |
| `Adversary` | Opus | Attacks a finished chunk of work. Read-only |
| `Structure` | Opus | Owns `CONVENTIONS.md`. Writes nothing else |
| `SketchReviewer` | Opus | Reads sketches and the features that use them; names constraint and extent fixes. Read-only |
| `CadSimplifier` | Opus | Reads a Fusion timeline and names steps that can go. Read-only |
| `Senior` | Opus | Writer of last resort. Only after two failed `SimpleTask` attempts |

The plugin ships `SketchReviewer` (in [agents/SketchReviewer.md](../agents/SketchReviewer.md)) and `CadSimplifier` (in [agents/CadSimplifier.md](../agents/CadSimplifier.md)). The other roles are our own agent files. Example copies are in [examples/claude-config/agents/](../examples/claude-config/agents/).

Why many small workers: a worker with a sharp brief succeeds more often than one big run, and a small failed task costs little. When a worker fails, we narrow the brief and retry before we escalate. Our rule: ten small tasks before one big one.

Never pass `model` when you call Agent. The agent file's frontmatter decides the model. A `model` in the call silently replaces it, so a Haiku worker can end up on a different model with no error. If you need another model, change the agent file.

## Example: a hole change on the demo bracket

The [demo bracket](../examples/demo_bracket/README.md) is a 90 x 60 x 5 mm plate with four mounting holes, an upright wall with a gable-roof slot, two gussets and a bed chamfer. The holes come from one `mounting_hole_grid` feature: one hole, then `features.pattern_grid`.

Say we want six holes instead of four. The main session does not open the source files. It writes a brief:

```
FILES:
- examples/demo_bracket/parts/body.py: mounting_hole_grid (holes.hole_simple + features.pattern_grid)
- docs/fusion_kit_api.md: read before scripting
KNOWN:
- Hole count is a pattern quantity parameter, not one feature per hole
- Holes are cut last; a later join closes them
- Label names stay as they are; timeline_unnamed must stay 0
DONE WHEN:
- uvx basedpyright examples/demo_bracket/parts/body.py: 0 errors
- one execute of the build step prints report() with timeline_unnamed=0 and no DFAM_FAIL
```

This brief is illustrative. It was not run. A worker makes the edit, runs the checks and reports their output. The main session reads the numbers. It does not accept the word "done".

## Briefs

Every agent brief has three sections.

- `FILES:` absolute paths with the symbol, one per line. If you do not know the file, write `unknown` and say why.
- `KNOWN:` established facts: signatures, decisions, and what was tried with its result. A clean-context agent starts from zero. Without KNOWN it rediscovers the repo. One early agent spent about 70k tokens scanning before we added a context pack.
- `DONE WHEN:` an exact command or measurement that must pass. "Looks right" is not a DONE WHEN.

Geometry briefs for Fusion add two sections:

- `PARAMS:` name = expression, with units. Example: `hole_count = 6`, `hole_pitch_mm = 20 mm`.
- `GEOMETRY:` one line per feature: label, plane, profile, operation, extent expression. A repeated or symmetric feature is written as a seed plus a pattern (quantity and spacing as parameters), or as a mirror. Six holes are one row, not six rows.

The `DONE WHEN` of a geometry brief names the numbers the build must print: bodies, bounding box, volume, and interference = 0 where it applies. Add one line that a parameter change is re-measured.

The Fusion report must end with a `FUSION_KIT GAPS:` line, or `none`.

What the guard checks: `fusion_kit_brief_guard` runs before each Agent call. If the brief mentions Fusion and does not reference `docs/fusion_kit_api.md`, the call is denied once per session. Add the file to FILES and retry; the retry passes. The guard checks only that reference. It does not check the PARAMS, GEOMETRY or DONE WHEN sections. We check those by reading the brief before we send it.

## Before you model

- Get the mating dimensions from the user first: plug, board, cable, pins, clearances. Never invent fit values.
- Two or more layout options: list them and let the user pick before any geometry.
- Parameters before geometry. Every dimension is a User Parameter with an expression, not a typed number.

## The build loop

For a CAD change the loop is the same every time:

1. Build in steps. One `execute` per step, timed with `time.perf_counter()`. A timed-out call keeps Fusion busy, so the next call times out too. Do not retry. Wait until Fusion is idle, then inspect the document.
2. Read `report()`. It prints bodies, bounding box, volume, constrained sketches, `timeline_unnamed` (must be 0), `timeline_numbered`, the DfAM counts, and one line per `DFAM_FAIL` or `DFAM_WARN`. Read the numbers, not only the colour of the output.
3. Run `SketchReviewer` on the finished part, after `report()` is green. It reads the sketches and the features that use them (read-only) and returns ranked constraint and extent fixes. Rules: [sketch_guidelines.md](sketch_guidelines.md).
4. Run `printability.check_body(body, up_rot, min_angle_deg=45)` in print orientation. Check the seed occurrence of each component. A rotated twin of the same component gives a false overhang FAIL.
5. Run `CadSimplifier` on the finished part. It reads the timeline (read-only) and returns a ranked recipe. Hand the recipe to a worker, rebuild, re-measure.
6. Run `Adversary` on the finished chunk, after the checks are green.
7. Fix, re-run live, re-measure. Quote numbers from the last run only.
8. Take one screenshot at the end. `save_screenshot` writes a 600x400 PNG with +Z up.
9. Ask for a photo or screenshot review against the physical fit. Done means it fits, not that the checks are green.

Done gate: `build_and_check(..., params=PARAMS, sweep=True)` once, when the part is otherwise done. Expect `sweep=<N>p/0F`. Per build the sweep stays off (`sweep=off`), so iteration is fast. `TIME <stage> <s>` lines show the cost of each stage. `SKIP <stage> budget` or `budget=exceeded` means that stage did not run: it is not a pass. Raise `budget_s` or narrow `checks`, then re-run.

Green is not proof. Lint and type checks say the code is well formed. The report says the model has the numbers we expected. Only a run that exercises the change, or a measurement of the real model, says it works. We quote that output in the reply. If we cannot run it, we say so.

### Example: a DfAM fail on the bed chamfer

The overhang check fails a downward face with n_z above 0.707. A 45-degree bed chamfer has n_z 0.7071, so it fails. A 44-degree chamfer (n_z 0.719) fails as well. See [gotchas.md](gotchas.md), item 7.

`report()` prints a `DFAM_FAIL bed_chamfer` line. The `dfam_guard` hook also blocks the stop once. The fix is the default `features.bed_chamfer` angle of 46 degrees (n_z 0.695). Rebuild, re-run `report()`, and check that the FAIL line is gone. The DfAM rules are in [dfam_guidelines.md](dfam_guidelines.md).

### Example: a pattern candidate from CadSimplifier

Suppose `timeline_numbered` shows `hole_1` to `hole_6`, each with the same expressions. `CadSimplifier` flags them as one seed plus `pattern_grid`, with the quantity as a parameter. The worker rebuilds the holes that way, and the main session re-measures the plate. A pattern exception is allowed only when measured. A code comment states the reason.

## Batching and parallel calls

- Independent calls go in one message. Two edits to two different files go in one message.
- Go sequential only when the second call needs the first call's effect. Example: edit, then measure. A test run before the edit proves nothing about the edit.
- Every turn re-reads the whole context. At a 230k window, four parallel calls cost about $0.115 and the same four in sequence about $0.46 (our figures).
- Long jobs get an explicit `timeout`, and `run_in_background` for anything long. A background command without a timeout dies at its default limit with no result.
- `batch-nudge` (ours) reminds after two single-call turns in a row. It is advice, not a block.

## Git

- No commit, branch, push, reset, revert, merge, rebase, tag or stash without permission in the current message.
- "Implement X" is not permission to commit. Permission from an earlier message does not carry forward.
- `git-guard` (ours) denies these before they run. The deliberate bypass is a sentinel comment on that one command segment, used only when the user has said so in that message.

## Hooks: a ban in a prompt is a request, a hook is a control

Prompt text gets ignored, especially late in a long session. A hook runs outside the model. It can deny a call, block a stop, or print a reminder. We use hooks for rules where a break costs real time or real material. Prompt text covers the rest.

There are two sources of hooks:

- Shipped with this plugin, wired in `hooks/hooks.json`. These are the first four rows of the table.
- Ours, kept in our own `~/.claude/` folder. We do not ship them. The table describes what they do. [settings.hooks.example.json](../examples/claude-config/settings.hooks.example.json) wires them. See the [examples README](../examples/claude-config/README.md).

| Practice | Hook event and matcher | What it does | Ships? |
|---|---|---|---|
| Use helpers, not raw adsk, in part code | PreToolUse: `fusion_mcp_execute`, Write, Edit | `fusion_kit_nudge`: names the matching helper from the index. Advice only, never blocks | yes |
| Fusion briefs cite the helper index | PreToolUse: Agent | `fusion_kit_brief_guard`: denies once per session a Fusion brief without `docs/fusion_kit_api.md`. Retry passes | yes |
| DfAM FAIL blocks the stop | PostToolUse: `fusion_mcp_execute`, `fusion-cad` execute. Stop | `dfam_guard`: records `DFAM_FAIL` per part and blocks the stop once. A new FAIL re-arms it | yes |
| Simplifier review after a build | PostToolUse: `fusion_mcp_execute`, `fusion-cad` execute, Agent, Write, Edit. Stop | `cad_simplify_nudge`: after a green build or a part-file edit, blocks the stop once until `CadSimplifier` has run | yes |
| No destructive git without permission | PreToolUse: Bash, PowerShell | `git-guard`: denies commit, push, reset and the rest. Per-segment sentinel for deliberate use | no |
| Briefs have sections and a valid roster | PreToolUse: Agent, Task. SessionStart: reset | `agent-guard`: checks the roster, the FILES / KNOWN / DONE WHEN sections, and multi-task Haiku briefs. Denies a bad brief | no |
| Verify before "works" and before commit | Stop. PreToolUse: Bash, PowerShell (`--commit`) | `verify-nudge`: nudges once per edit if code changed after the last run. Same check runs before a commit | no |
| Review changed Python | Stop | `review-nudge`: nudges a review. The cap resets after an `Adversary` call | no |
| Batch independent calls | PostToolBatch | `batch-nudge`: nudges after two single-call turns in a row | no |
| Working state in STATE.md | SessionStart (startup, resume). PreToolUse: Write, Edit. Stop | `state-hook`: injects STATE.md at session start (size capped). Guards its size limits. At stop, creates a skeleton if missing and nudges an update | no |
| Layout and names from CONVENTIONS.md | PreToolUse: Write, Edit | `structure-guard`: denies the first new code file in a repo without CONVENTIONS.md, once per session. `Structure` may write only CONVENTIONS.md | no |
| Structure gate | Stop | `structure-nudge`: nudges a `Structure` pass after three or more new code files | no |

The shipped hooks fail open. If one hits an internal error, it lets the action through and stays silent. Each hook has a `--selftest` flag (see [CONVENTIONS.md](../CONVENTIONS.md)). Run it after you edit a hook.

## When something fails

- A tool error stops the step. Report the exact error text. Do not swap in a guess or a different tool quietly. `ECONNREFUSED` on the Fusion endpoint means Fusion stopped. Ask the user to restart it.
- A worker FAIL, or a DONE WHEN that fails twice: narrow the brief (smaller FILES, sharper DONE WHEN) and retry the worker.
- Two failed worker attempts: `Senior` may take the task. Design trade-offs go to `arch`, not to a writer.
- A Fusion call that times out: inspect the document before any retry. Undo with `fusion_mcp_update` if the state is bad.
- A failed script can leave an unsaved document open. Tell the user, or close it with `doc.close(False)` in a diagnostic script.

## Review rounds

- Review each finished chunk once, after its checks are green. A chunk is one part, one phase or one file set.
- Do not send the same unchanged code to `Adversary` again. A second round on the same code finds less, and it can damage code that was correct.
- Review what is done now and the rest when it lands. Do not wait for the end of a large task. A late review finds defects that were already built on.

## Memory and working state

- Work in progress goes to `STATE.md` in the repo root. Sections: Goal, Stages, Now, To do, Open decisions, Decisions made (with the why), Files, Blockers, Done. Edit one section at a time; do not rewrite the file. Template: [STATE.template.md](../examples/claude-config/STATE.template.md).
- Memory is for facts that will still be true in a week. Versions, paths and run status are not. Work in progress never goes to memory.
- Write memory last, one topic per file. The memory index loads every session, so each line costs tokens every time.

## Conventions and the Structure gate

FusionKit's [CONVENTIONS.md](../CONVENTIONS.md) is the layout reference for this repo. `fusion_kit/` is a flat library with one domain per module. `tests/`, `hooks/`, `skills/`, `agents/` and `docs/` hold the rest. Naming rules include a `_mm` suffix on lengths, a `_deg` suffix on angles, full English User Parameter names, snake_case timeline labels with sketches ending in `_sketch`, and a banned-name list (`utils`, `helper` in the library, `misc`, `common`, `tmp_*`, `final*`).

For your own Fusion project, give the repo its own CONVENTIONS.md and let `Structure` own it. A new module or a public rename goes to `Structure` first. It decides names and returns renames as `NEXT:` lines. A worker applies them. The worker does not choose names.

## Where to start

- [Demo bracket README](../examples/demo_bracket/README.md): a small, complete build with the helpers named.
- [fusion_kit_api.md](fusion_kit_api.md): the helper index. Read it before you write any part code.
- [gotchas.md](gotchas.md) and [dfam_guidelines.md](dfam_guidelines.md): the rules that bite.
- [SKILL.md](../skills/fusion-kit/SKILL.md): the setup check and the short workflow the plugin loads.
- [examples/claude-config/README.md](../examples/claude-config/README.md): agent files, hook wiring and the STATE template.
