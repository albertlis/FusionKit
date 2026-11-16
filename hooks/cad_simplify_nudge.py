"""Hook: [CadSimplifier] after a Fusion part change (build or file edit).

Layout assumed (fusion_kit projects): Fusion parts live in `<pkg>/parts/*.py`, or as
`<pkg>/<pkg>.py` directly under the repo root. Repo root = CLAUDE_PROJECT_DIR
(fallback: cwd; a plugin install has no repo-relative path).

Mechanics: trigger -> marker per session -> Stop blocks once.

PostToolUse (matcher `mcp__autodesk-fusion__fusion_mcp_execute|Agent|Write|Edit`):
- execute whose script has `run_part(` and `"build"`, and whose result has `bodies=` and `timeline_unnamed=0`
  -> marker (green build).
- Write/Edit of a part file: `*/parts/*.py` or `<pkg>/<pkg>.py` under the repo root -> marker.
- Agent with subagent_type `CadSimplifier` -> marker cleared (cap resets after the call).
Stop: marker set -> decision block once (marker cleared on block, so the second stop passes).
stop_hook_active -> pass. Error or bad stdin -> silent, exit 0 (hook never blocks work on its own bug).
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
EXECUTE_TOOLS = frozenset({EXECUTE, PLUGIN_EXECUTE})
AGENT = "Agent"
EDIT_TOOLS = frozenset({"Write", "Edit"})
SIMPLIFIER = "CadSimplifier"
PARTS_GLOB = "*/parts/*.py"
BUILD_CALL = "run_part("
BUILD_NAME = re.compile(r"""["']build["']""")
GREEN_BODIES = "bodies="
GREEN_UNNAMED = re.compile(r"timeline_unnamed=0\b")
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
    return GREEN_BODIES in text and GREEN_UNNAMED.search(text) is not None


def is_fusion_part(file_path: str, project: Path) -> bool:
    """Inside repo only: `*/parts/*.py`, or `<pakiet>/<pakiet>.py` directly under the repo root."""
    try:
        rel = Path(file_path).resolve().relative_to(project.resolve())
    except (ValueError, OSError):
        return False
    if fnmatch.fnmatch(rel.as_posix(), PARTS_GLOB):
        return True
    return len(rel.parts) == 2 and rel.parts[1] == f"{rel.parts[0]}.py"


def state_file(session: str, state_dir: Path) -> Path:
    safe = re.sub(r"[^\w-]", "_", session)[:120]
    return state_dir / f"claude-cad-simplify-nudge-{safe}"


def on_post_tool(payload: dict[str, object], state_dir: Path, project: Path) -> None:
    marker = state_file(str(payload.get("session_id") or "nosession"), state_dir)
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    name = payload.get("tool_name")
    if name in EDIT_TOOLS:
        file_path = tool_input.get("file_path")
        if isinstance(file_path, str) and is_fusion_part(file_path, project):
            marker.write_text("1", encoding="utf-8")
    elif name in EXECUTE_TOOLS:
        script = execute_script(tool_input)
        if script is not None and is_build(script) and is_green(payload.get("tool_response")):
            marker.write_text("1", encoding="utf-8")
    elif name == AGENT and tool_input.get("subagent_type") == SIMPLIFIER:
        marker.unlink(missing_ok=True)


def on_stop(payload: dict[str, object], state_dir: Path) -> str | None:
    """Block reason, or None to pass. Marker cleared first: block at most once per change."""
    marker = state_file(str(payload.get("session_id") or "nosession"), state_dir)
    if not marker.exists():
        return None
    marker.unlink(missing_ok=True)
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


GREEN = "bodies=2 bbox_mm=40.0x30.0x10.0 volume_cm3=12.3 timeline_unnamed=0 timeline_numbered=0"
RED = "NameError: run"
BUILD_SCRIPT = 'from fusion_kit.loader import run_part\ndef run(_context):\n    run_part(entry, "build")\n'
NOT_BUILD = 'print("hello")'
PKG = "my_part"


def ex(script: str, response: str, sid: str = "s1", as_string: bool = False) -> dict[str, object]:
    obj: object = json.dumps({"script": script}) if as_string else {"script": script}
    return {"tool_name": EXECUTE, "session_id": sid, "tool_input": {"object": obj}, "tool_response": response}


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
        ("stop_hook_active passes and clears", [ex(BUILD_SCRIPT, GREEN), stop(active=True), stop()], [None, None]),
        ("non-build execute -> stop passes", [ex(NOT_BUILD, GREEN), stop()], [None]),
        ("build red (no timeline_unnamed=0) -> stop passes", [ex(BUILD_SCRIPT, RED), stop()], [None]),
        (
            "build with timeline_unnamed=2 -> stop passes",
            [ex(BUILD_SCRIPT, "bodies=1 timeline_unnamed=2"), stop()],
            [None],
        ),
        ("object as JSON string build detected", [ex(BUILD_SCRIPT, GREEN, as_string=True), stop()], [REASON]),
        ("other Agent does not clear marker", [ex(BUILD_SCRIPT, GREEN), agent("Explore"), stop()], [REASON]),
        ("marker is per session", [ex(BUILD_SCRIPT, GREEN, sid="a"), stop(sid="b"), stop(sid="a")], [None, REASON]),
        ("Edit parts/*.py -> stop blocks", [edit(part), stop()], [REASON]),
        ("Write parts/*.py -> stop blocks", [edit(part, tool="Write"), stop()], [REASON]),
        ("Edit <pkg>/<pkg>.py -> stop blocks", [edit(pkg_file), stop()], [REASON]),
        ("Edit nested <pkg>/sub/<pkg>.py -> stop passes", [edit(nested_pkg), stop()], [None]),
        ("Edit fusion_kit/features.py -> stop passes", [edit(lib), stop()], [None]),
        ("Edit outside repo -> stop passes", [edit(outside), stop()], [None]),
        ("Edit parts then CadSimplifier -> stop passes", [edit(part), agent(SIMPLIFIER), stop()], [None]),
        (
            "Edit parts then build then CadSimplifier -> stop passes",
            [edit(part), ex(BUILD_SCRIPT, GREEN), agent(SIMPLIFIER), stop()],
            [None],
        ),
        ("Edit parts after CadSimplifier -> stop blocks", [agent(SIMPLIFIER), edit(part), stop()], [REASON]),
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
        state = Path(tmp) / "state"
        state.mkdir()
        project = Path(tmp) / "proj"
        project.mkdir()
        for name, events, want in cases(project):
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
            out = json.loads(r.stdout) if r.stdout.strip() else {}
            check(
                "e2e Stop after Edit parts -> decision block",
                r.returncode == 0 and isinstance(out, dict) and out.get("decision") == "block",
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
