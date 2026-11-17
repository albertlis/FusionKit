"""PreToolUse guard: deny ONCE per session a small Fusion probe script run from the main session.

Targets `mcp__autodesk-fusion__fusion_mcp_execute` and `mcp__fusion-cad__execute` (plugin-scoped
names end the same way; the hooks.json matcher is the regex filter). Only script execution is
gated (featureType absent or "script"). Never gated:
- subagent payloads (`agent_id` present): agents run the probes;
- scripts calling `build_and_check(` or `run_part(`: the build path;
- scripts with >=3 `print(` calls: a grouped probe.
Anything else -> deny once per session (marker file, fusion_kit_brief_guard pattern); the retry
in the same session passes. Any error -> allow: a guard must not block a tool on its own bug.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

EXECUTE_SUFFIXES = ("autodesk-fusion__fusion_mcp_execute", "fusion-cad__execute")
BUILD_CALLS = ("build_and_check(", "run_part(")
PRINT_CALL = re.compile(r"(?<![\w.])print\(")
MIN_PRINTS = 3
REASON = (
    "SONDA FUSION z sesji głównej: zgrupuj >=3 pomiary w jednym skrypcie albo deleguj [SimpleTask]. "
    "Build -> build_and_check. Ponów, jeśli celowo."
)


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


def is_probe(script: str) -> bool:
    """Small probe: no build call and fewer than MIN_PRINTS print calls."""
    if any(call in script for call in BUILD_CALLS):
        return False
    return len(PRINT_CALL.findall(script)) < MIN_PRINTS


def state_file(session: str, state_dir: Path) -> Path:
    safe = re.sub(r"[^\w-]", "_", session)[:120]
    return state_dir / f"claude-fusion-probe-guard-{safe}"


def decide(payload: dict[str, object], session: str, state_dir: Path) -> str | None:
    """Deny reason, or None to allow. Denies once per session."""
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_name, str) or not tool_name.endswith(EXECUTE_SUFFIXES):
        return None
    if payload.get("agent_id") or not isinstance(tool_input, dict):
        return None
    if tool_input.get("featureType") not in (None, "script"):
        return None
    script = execute_script(tool_input)
    if script is None or not is_probe(script):
        return None
    marker = state_file(session, state_dir)
    if marker.exists():
        return None
    try:
        marker.write_text("1", encoding="utf-8")
    except OSError:
        return None  # cannot remember the deny -> allow, never deny in a loop
    return REASON


def main() -> int:
    try:
        data: object = json.load(sys.stdin)
        if not isinstance(data, dict):
            return 0
        reason = decide(data, str(data.get("session_id") or "nosession"), Path(tempfile.gettempdir()))
        if reason is not None:
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": reason,
                        }
                    }
                )
            )
    except Exception:  # noqa: BLE001 - fail-open by design: never block a tool call on a guard bug
        return 0
    return 0


EXEC = "mcp__autodesk-fusion__fusion_mcp_execute"
CAD = "mcp__fusion-cad__execute"


def payload(
    script: str, *, tool: str = EXEC, session: str = "sel", agent: str | None = None, **extra: object
) -> dict[str, object]:
    tool_input: dict[str, object] = {"script": script, "featureType": "script", **extra}
    out: dict[str, object] = {"tool_name": tool, "session_id": session, "tool_input": tool_input}
    if agent is not None:
        out["agent_id"] = agent
    return out


CASES: tuple[tuple[str, dict[str, object], bool], ...] = (
    # (name, payload, expected: True = deny, False = allow)
    ("main 1-print probe -> deny", payload("print(body.volume)"), True),
    ("build_and_check -> allow", payload("build_and_check('x/parts/body.py')"), False),
    ("run_part -> allow", payload("run_part(entry)\nprint(1)"), False),
    ("3 prints -> allow", payload("print(a)\nprint(b)\nprint(c)"), False),
    ("2 prints -> deny", payload("print(a)\nprint(b)"), True),
    ("pprint / print_x / obj.print not counted", payload("pprint(1)\nprint_x(2)\nobj.print(3)"), True),
    ("subagent 1-print -> allow", payload("print(1)", agent="a1"), False),
    ("fusion-cad 1-print -> deny", payload("print(1)", tool=CAD), True),
    ("featureType document -> allow", payload("print(1)", featureType="document"), False),
    ("readOnly 1-print -> deny", payload("print(1)", readOnly=True), True),
    (
        "script in object JSON string -> deny",
        {"tool_name": EXEC, "session_id": "sel", "tool_input": {"object": '{"script": "print(1)"}'}},
        True,
    ),
    (
        "no script field -> allow",
        {"tool_name": EXEC, "session_id": "sel", "tool_input": {"featureType": "script"}},
        False,
    ),
    ("other tool ignored", {"tool_name": "Bash", "session_id": "sel", "tool_input": {"command": "print(1)"}}, False),
)


def run_main(payload_text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        input=payload_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def selftest() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        for name, pl, want_deny in CASES:
            got = decide(pl, f"sel-{name}", state)
            check(name, (got == REASON) == want_deny, f"got {got!a}")
        first = decide(payload("print(1)"), "flow", state)
        second = decide(payload("print(1)"), "flow", state)
        check(
            "deny then allow (retry passes)", first == REASON and second is None, f"first {first!a} second {second!a}"
        )
        check(
            "once per session: other probe same session allowed",
            decide(payload("print(2)"), "flow", state) is None,
        )
        check("new session denied again", decide(payload("print(1)"), "flow-2", state) == REASON)
        check("state write failure -> allow", decide(payload("print(1)"), "flow-3", state / "missing") is None)

    sid = f"sel-e2e-{uuid.uuid4().hex}"
    try:
        r1 = run_main(json.dumps(payload("print(1)", session=sid)))
        hso: object = json.loads(r1.stdout).get("hookSpecificOutput", {}) if r1.stdout.strip() else {}
        ok1 = (
            isinstance(hso, dict)
            and hso.get("permissionDecision") == "deny"
            and hso.get("permissionDecisionReason") == REASON
        )
        check("e2e first call -> deny JSON", r1.returncode == 0 and ok1, ascii(r1.stdout)[:200])
        r2 = run_main(json.dumps(payload("print(1)", session=sid)))
        check("e2e retry -> silent allow", r2.returncode == 0 and r2.stdout.strip() == "", ascii(r2.stdout)[:200])
        r3 = run_main(json.dumps(payload("print(1)", session=f"{sid}-sub", agent="a1")))
        check("e2e subagent -> silent allow", r3.returncode == 0 and r3.stdout.strip() == "", ascii(r3.stdout)[:200])
        r4 = run_main("not json")
        check("e2e bad stdin -> silent", r4.returncode == 0 and r4.stdout.strip() == "", ascii(r4.stdout)[:200])
    finally:
        state_file(sid, Path(tempfile.gettempdir())).unlink(missing_ok=True)

    for name, ok, detail in results:
        print(f"PASS {name}" if ok else f"FAIL {name}: {detail}")
    passed = sum(ok for _, ok, _ in results)
    print(f"{passed}/{len(results)} PASS")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
