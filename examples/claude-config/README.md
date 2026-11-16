# Claude config examples

Example setup for the workflow in [docs/workflow.md](../../docs/workflow.md). Copy what you need. The plugin does not load anything in this folder. The plugin wires its own hooks from [hooks/hooks.json](../../hooks/hooks.json), so do not copy those into your settings.

## Files

| File | Where it goes | Notes |
|---|---|---|
| `agents/SimpleTask.md` | `~/.claude/agents/` (all projects) or `<repo>/.claude/agents/` (one repo) | Default Haiku worker |
| `agents/TestRunner.md` | same | Haiku, runs commands and reports |
| `agents/Adversary.md` | same | Opus, read-only reviewer |
| `agents/Structure.md` | same | Opus, owns CONVENTIONS.md |
| `settings.hooks.example.json` | merge its `hooks` key into `~/.claude/settings.json` | Keep your other keys. See Hooks below |
| `STATE.template.md` | copy to the repo root as `STATE.md` | Working state. Our state-hook creates a skeleton at stop if the file is missing |

The plugin ships `CadSimplifier` itself. Do not copy it here.

## Agent frontmatter

Fields used in the examples:

- `name`, `description`: the description tells the main session when to call the agent.
- `model`: `haiku` or `opus` alias. The frontmatter decides the model.
- `effort`: `low` or `high`, where we set it.
- `tools`: the tools the agent may use. This is an example list. MCP tool names depend on your setup, so trim the list to the tools you have.

Never pass `model` when you call Agent. A `model` in the call silently replaces the frontmatter model, so a Haiku worker can run on a different model with no error. To change the model, edit the agent file.

Each body is short on purpose. The brief carries the task (FILES, KNOWN, DONE WHEN), and the agent file carries the role.

## Hooks

`settings.hooks.example.json` references eight scripts in `~/.claude/`:

- `git-guard.py`, `agent-guard.py`, `verify-nudge.py`, `review-nudge.py`, `state-hook.py`, `batch-nudge.py`, `structure-guard.py`, `structure-nudge.py`

These scripts are not part of this plugin. You write your own. [docs/workflow.md](../../docs/workflow.md) lists what each one does, under "Hooks". Delete the entries for scripts you do not write. A hook pointing at a missing script is an error.

The flags in the example (`--hook`, `--reset`, `--session`, `--guard`, `--stop`, `--commit`) are the conventions of our own scripts. Yours may take none of them.

The example uses `~/.claude/...` paths and no user names, so the same file works on any machine. JSON has no comments, which is why the explanation lives here.

## Why the hooks matter

A ban in a prompt is a request. A hook is a control. The hook can deny a call or block a stop, which a prompt cannot. The table in [docs/workflow.md](../../docs/workflow.md) maps each practice to its hook.

## Checks

- The JSON parses: `uv run python -c "import json;json.load(open('examples/claude-config/settings.hooks.example.json'))"`, run from the FusionKit root.
- After you write your own hook scripts, run each one's `--selftest` if it has one.
