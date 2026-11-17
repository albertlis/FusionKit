"""Hook: [CadSimplifier] nudge, once per part, only after a green fusion_kit build.

Layout assumed (fusion_kit projects): Fusion parts live in `<pkg>/parts/*.py`, or as
`<pkg>/<pkg>.py` directly under the repo root. Repo root = CLAUDE_PROJECT_DIR
(fallback: cwd; a plugin install has no repo-relative path).

Mechanics: per-session state file, one entry per part (key = package name).
  dirty  -> part changed, no green report since
  ready  -> last build of the part green: bodies=, timeline_unnamed=0, dfam=0F
  nudged -> Stop blocked once for this part; sticky until a [CadSimplifier] call

PostToolUse (matcher `.*autodesk-fusion__fusion_mcp_execute|.*fusion-cad__execute|Agent|Write|Edit`):
- Write/Edit of a part file -> dirty (ready demoted; nudged stays nudged).
- execute whose script has `run_part(` and `"build"`: green response -> ready (unless nudged);
  any other build response -> dirty (unless nudged).
- Agent with subagent_type `CadSimplifier` -> state cleared (all parts, nudged included).
Stop: any part ready -> decision block once; those parts -> nudged.
stop_hook_active -> pass, same ready->nudged transition.
Error or bad stdin -> silent, exit 0 (hook never blocks work on its own bug).
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

EXECUTE = "mcp__autodesk-fusion__fusion_mcp_execute"
# Plugin-bundled MCP servers get scoped names: mcp__plugin_<plugin>_<server>__<tool>
PLUGIN_EXECUTE = "mcp__plugin_fusion-kit_autodesk-fusion__fusion_mcp_execute"
CAD_EXECUTE = "mcp__fusion-cad__execute"
PLUGIN_CAD_EXECUTE = "mcp__plugin_fusion-kit_fusion-cad__execute"
EXECUTE_TOOLS = frozenset({EXECUTE, PLUGIN_EXECUTE, CAD_EXECUTE, PLUGIN_CAD_EXECUTE})
AGENT = "Agent"
EDIT_TOOLS = frozenset({"Write", "Edit"})
SIMPLIFIER = "CadSimplifier"
PARTS_GLOB = "*/parts/*.py"
BUILD_CALL = "run_part("
BUILD_NAME = re.compile(r"""["']build["']""")
# run_part(<entry path>, ...): entry = `<pkg>/<pkg>.py`, so Path.stem = package name.
PART_ARG = re.compile(r"run_part\(\s*(?:[rR](?=[\"']))?[\"']?([^\"',)\s]+)")
UNKNOWN_PART = "unknown"
GREEN_BODIES = "bodies="
GREEN_UNNAMED = re.compile(r"timeline_unnamed=0\b")
GREEN_DFAM = re.compile(r"dfam=0F\b")
DIRTY = "dirty"
READY = "ready"
NUDGED = "nudged"
REASON = (
    "[cad-simplify-nudge] Fusion part changed without [CadSimplifier]. "
    "Consider [CadSimplifier] after a green build. Trivial change -> one sentence why, then finish."
)


def project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(os.getcwd())


def execute_script(tool_input: dict[str, object]) -> str | None:
    direct = tool_input.get("script")
    if isinstance(direct, str):
        return direct
    obj: object = tool_input.get("object")
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError:
            return None
    if isinstance(obj, dict) and isinstance(obj.get("script"), str):
        return obj["script"]
    return None


def is_build(script: str) -> bool:
    return BUILD_CALL in script and BUILD_NAME.search(script) is not None


def is_green(response: object) -> bool:
    text = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
    return GREEN_BODIES in text and GREEN_UNNAMED.search(text) is not None and GREEN_DFAM.search(text) is not None


def part_key(script: str) -> str:
    """Package name of the built part (caller guarantees BUILD_CALL in script)."""
    m = PART_ARG.search(script)
    return Path(m.group(1)).stem if m else UNKNOWN_PART


def part_of(file_path: str, project: Path) -> str | None:
    """Package name for a Fusion part file inside repo only: `*/parts/*.py` or `<pkg>/<pkg>.py`."""
    try:
        rel = Path(file_path).resolve().relative_to(project.resolve())
    except (ValueError, OSError):
        return None
    if fnmatch.fnmatch(rel.as_posix(), PARTS_GLOB):
        return rel.parts[0]
    if len(rel.parts) == 2 and rel.parts[1] == f"{rel.parts[0]}.py":
        return rel.parts[0]
    return None


def state_file(session: str, state_dir: Path) -> Path:
    safe = re.sub(r"[^\w-]", "_", session)[:120]
    return state_dir / f"claude-cad-simplify-nudge-{safe}"


def load_state(marker: Path) -> dict[str, str]:
    try:
        data: object = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def save_state(marker: Path, state: dict[str, str]) -> None:
    if state:
        marker.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    else:
        marker.unlink(missing_ok=True)


def demote(state: dict[str, str], part: str) -> None:
    """Part changed or build not green: back to dirty. Nudged stays until [CadSimplifier]."""
    if state.get(part) != NUDGED:
        state[part] = DIRTY


def on_post_tool(payload: dict[str, object], state_dir: Path, project: Path) -> None:
    marker = state_file(str(payload.get("session_id") or "nosession"), state_dir)
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    name = payload.get("tool_name")
    state = load_state(marker)
    if name in EDIT_TOOLS:
        file_path = tool_input.get("file_path")
        part = part_of(file_path, project) if isinstance(file_path, str) else None
        if part is None:
            return
        demote(state, part)
    elif name in EXECUTE_TOOLS:
        script = execute_script(tool_input)
        if script is None or not is_build(script):
            return
        part = part_key(script)
        if is_green(payload.get("tool_response")):
            if state.get(part) != NUDGED:
                state[part] = READY
        else:
            demote(state, part)
    elif name == AGENT and tool_input.get("subagent_type") == SIMPLIFIER:
        state = {}
    else:
        return
    save_state(marker, state)


def on_stop(payload: dict[str, object], state_dir: Path) -> str | None:
    """Block reason, or None to pass. Ready parts turn nudged: block at most once per part."""
    marker = state_file(str(payload.get("session_id") or "nosession"), state_dir)
    state = load_state(marker)
    ready = [part for part, status in state.items() if status == READY]
    if not ready:
        return None
    for part in ready:
        state[part] = NUDGED
    save_state(marker, state)
    if payload.get("stop_hook_active"):
        return None
    return REASON


def main() -> int:
    try:
        data: object = json.load(sys.stdin)
        if not isinstance(data, dict):
            return 0
        state_dir = Path(tempfile.gettempdir())
        if "tool_name" in data:
            on_post_tool(data, state_dir, project_dir())
            return 0
        reason = on_stop(data, state_dir)
        if reason is not None:
            print(json.dumps({"decision": "block", "reason": reason}))
    except Exception:  # noqa: BLE001 - fail-open by design: never block a session on a hook bug
        return 0
    return 0


def build_script(entry: str) -> str:
    return f'from fusion_kit.loader import run_part\nrun_part("{entry}", "build")\n'


PKG = "my_part"
OTHER = "other_part"
BUILD_SCRIPT = build_script(f"C:/proj/{PKG}/{PKG}.py")
OTHER_BUILD_SCRIPT = build_script(f"C:/proj/{OTHER}/{OTHER}.py")
GREEN = "bodies=2 bbox_mm=40.0x30.0x10.0 volume_cm3=12.3 timeline_unnamed=0 timeline_numbered=0 dfam=0F/2W"
DFAM_RED = "bodies=2 bbox_mm=40.0x30.0x10.0 timeline_unnamed=0 dfam=1F/2W\nDFAM_FAIL teardrop: x"
RED = "NameError: run"
NOT_BUILD = 'print("hello")'


def ex(script: str, response: str, sid: str = "s1", as_string: bool = False, tool: str = EXECUTE) -> dict[str, object]:
    obj: object = json.dumps({"script": script}) if as_string else {"script": script}
    return {"tool_name": tool, "session_id": sid, "tool_input": {"object": obj}, "tool_response": response}


def edit(file_path: str, sid: str = "s1", tool: str = "Edit") -> dict[str, object]:
    return {"tool_name": tool, "session_id": sid, "tool_input": {"file_path": file_path}, "tool_response": "ok"}


def agent(sub: str, sid: str = "s1") -> dict[str, object]:
    return {
        "tool_name": AGENT,
        "session_id": sid,
        "tool_input": {"subagent_type": sub, "prompt": "x"},
        "tool_response": "ok",
    }


def stop(sid: str = "s1", active: bool = False) -> dict[str, object]:
    return {"hook_event_name": "Stop", "session_id": sid, "stop_hook_active": active}


def replay(events: list[dict[str, object]], state_dir: Path, project: Path) -> list[str | None]:
    out: list[str | None] = []
    for ev in events:
        if "tool_name" in ev:
            on_post_tool(ev, state_dir, project)
        else:
            out.append(on_stop(ev, state_dir))
    return out


def cases(project: Path) -> tuple[tuple[str, list[dict[str, object]], list[str | None]], ...]:
    part = str(project / PKG / "parts" / "body.py")
    pkg_file = str(project / PKG / f"{PKG}.py")
    nested_pkg = str(project / PKG / "sub" / f"{PKG}.py")
    lib = str(project / "fusion_kit" / "features.py")
    outside = str(Path(tempfile.gettempdir()) / "other" / "parts" / "x.py")
    return (
        # (name, events in order, expected result of each Stop event)
        ("build green -> stop blocks", [ex(BUILD_SCRIPT, GREEN), stop()], [REASON]),
        ("build + CadSimplifier -> stop passes", [ex(BUILD_SCRIPT, GREEN), agent(SIMPLIFIER), stop()], [None]),
        ("second stop after block passes", [ex(BUILD_SCRIPT, GREEN), stop(), stop()], [REASON, None]),
        (
            "second green build of nudged part -> stop silent",
            [ex(BUILD_SCRIPT, GREEN), stop(), ex(BUILD_SCRIPT, GREEN), stop(), stop()],
            [REASON, None, None],
        ),
        (
            "nudged part: edit + green rebuild -> stop silent",
            [ex(BUILD_SCRIPT, GREEN), stop(), edit(part), ex(BUILD_SCRIPT, GREEN), stop()],
            [REASON, None],
        ),
        ("stop_hook_active passes and clears", [ex(BUILD_SCRIPT, GREEN), stop(active=True), stop()], [None, None]),
        ("non-build execute -> stop passes", [ex(NOT_BUILD, GREEN), stop()], [None]),
        ("build red (no timeline_unnamed=0) -> stop passes", [ex(BUILD_SCRIPT, RED), stop()], [None]),
        (
            "build with timeline_unnamed=2 -> stop passes",
            [ex(BUILD_SCRIPT, "bodies=1 timeline_unnamed=2 dfam=0F/0W"), stop()],
            [None],
        ),
        ("build with dfam=1F (non-green report) -> stop passes", [ex(BUILD_SCRIPT, DFAM_RED), stop()], [None]),
        (
            "green then dfam=1F rebuild -> stop passes",
            [ex(BUILD_SCRIPT, GREEN), ex(BUILD_SCRIPT, DFAM_RED), stop()],
            [None],
        ),
        ("object as JSON string build detected", [ex(BUILD_SCRIPT, GREEN, as_string=True), stop()], [REASON]),
        (
            "fusion-cad execute build green -> stop blocks",
            [ex(BUILD_SCRIPT, GREEN, tool=CAD_EXECUTE), stop()],
            [REASON],
        ),
        (
            "plugin fusion-cad execute build green -> stop blocks",
            [ex(BUILD_SCRIPT, GREEN, tool=PLUGIN_CAD_EXECUTE), stop()],
            [REASON],
        ),
        ("other Agent does not clear state", [ex(BUILD_SCRIPT, GREEN), agent("Explore"), stop()], [REASON]),
        ("state is per session", [ex(BUILD_SCRIPT, GREEN, sid="a"), stop(sid="b"), stop(sid="a")], [None, REASON]),
        (
            "two parts -> each blocks once",
            [ex(BUILD_SCRIPT, GREEN), stop(), ex(OTHER_BUILD_SCRIPT, GREEN), stop(), stop()],
            [REASON, REASON, None],
        ),
        ("Edit parts alone, no green build -> stop passes", [edit(part), stop()], [None]),
        ("Edit parts then green build -> stop blocks", [edit(part), ex(BUILD_SCRIPT, GREEN), stop()], [REASON]),
        ("green build then Edit parts -> stop passes", [ex(BUILD_SCRIPT, GREEN), edit(part), stop()], [None]),
        (
            "Write parts then green build -> stop blocks",
            [edit(part, tool="Write"), ex(BUILD_SCRIPT, GREEN), stop()],
            [REASON],
        ),
        (
            "Edit <pkg>/<pkg>.py then green build -> stop blocks",
            [edit(pkg_file), ex(BUILD_SCRIPT, GREEN), stop()],
            [REASON],
        ),
        ("Edit nested <pkg>/sub/<pkg>.py -> stop passes", [edit(nested_pkg), stop()], [None]),
        ("Edit fusion_kit/features.py -> stop passes", [edit(lib), stop()], [None]),
        ("Edit outside repo -> stop passes", [edit(outside), stop()], [None]),
        (
            "Edit parts then build then CadSimplifier -> stop passes",
            [edit(part), ex(BUILD_SCRIPT, GREEN), agent(SIMPLIFIER), stop()],
            [None],
        ),
        ("Edit parts after CadSimplifier, no build -> stop passes", [agent(SIMPLIFIER), edit(part), stop()], [None]),
        (
            "CadSimplifier resets nudged part; edit + green rebuild -> fires again",
            [ex(BUILD_SCRIPT, GREEN), stop(), agent(SIMPLIFIER), edit(part), ex(BUILD_SCRIPT, GREEN), stop()],
            [REASON, REASON],
        ),
        (
            "CadSimplifier resets nudged part; green rebuild -> fires again",
            [ex(BUILD_SCRIPT, GREEN), stop(), agent(SIMPLIFIER), ex(BUILD_SCRIPT, GREEN), stop()],
            [REASON, REASON],
        ),
    )


def run_main(payload_text: str, project: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project)}
    return subprocess.run(
        [sys.executable, __file__],
        input=payload_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )


def selftest() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "proj"
        project.mkdir()
        for idx, (name, events, want) in enumerate(cases(project)):
            state = Path(tmp) / f"state-{idx}"  # fresh state per case: nudged state must not leak
            state.mkdir()
            got = replay(events, state, project)
            check(name, got == want, f"got {got!a}")

    sid = f"sel-e2e-{uuid.uuid4().hex}"
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "proj"
        project.mkdir()
        try:
            part = str(project / PKG / "parts" / "body.py")
            r = run_main(json.dumps(ex(BUILD_SCRIPT, GREEN, sid=sid)), project)
            check(
                "e2e PostToolUse build -> silent exit 0",
                r.returncode == 0 and r.stdout.strip() == "",
                ascii(r.stdout)[:200] + ascii(r.stderr)[:200],
            )
            r = run_main(json.dumps(stop(sid=sid)), project)
            out: object = json.loads(r.stdout) if r.stdout.strip() else {}
            check(
                "e2e Stop -> decision block JSON",
                r.returncode == 0
                and isinstance(out, dict)
                and out.get("decision") == "block"
                and out.get("reason") == REASON,
                ascii(r.stdout)[:200],
            )
            r = run_main(json.dumps(stop(sid=sid)), project)
            check("e2e second Stop -> silent", r.returncode == 0 and r.stdout.strip() == "", ascii(r.stdout)[:200])
            r = run_main(json.dumps(edit(part, sid=sid)), project)
            check(
                "e2e PostToolUse Edit parts -> silent exit 0",
                r.returncode == 0 and r.stdout.strip() == "",
                ascii(r.stdout)[:200] + ascii(r.stderr)[:200],
            )
            r = run_main(json.dumps(stop(sid=sid)), project)
            check(
                "e2e Stop after Edit of nudged part -> silent",
                r.returncode == 0 and r.stdout.strip() == "",
                ascii(r.stdout)[:200],
            )
            r = run_main("not json", project)
            check("e2e bad stdin -> silent exit 0", r.returncode == 0 and r.stdout.strip() == "", ascii(r.stdout)[:200])
        finally:
            state_file(sid, Path(tempfile.gettempdir())).unlink(missing_ok=True)

    for name, ok, detail in results:
        print(f"PASS {name}" if ok else f"FAIL {name}: {detail}")
    passed = sum(ok for _, ok, _ in results)
    print(f"{passed}/{len(results)} PASS")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
