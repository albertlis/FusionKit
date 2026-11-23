"""DFAM (design-for-additive-manufacturing) guard for fusion_kit.report() output.

report() prints `DFAM_FAIL <rule>: <msg>` / `DFAM_WARN ...` lines and a `dfam=<n>F/<n>W` token.
Assumes fusion_kit's report layout: a `<comp>: bodies=...` header per component, followed by its lines.

PostToolUse (matcher `mcp__autodesk-fusion__fusion_mcp_execute|mcp__fusion-cad__execute`):
- result contains `DFAM_FAIL` and script calls `run_part(` -> FAIL state per comp. Report is split into
  sections: header `<comp>: bodies=...` plus the lines below it (up to the next header). DFAM_FAIL and
  `dfam=` lines of a section belong to that comp (key = comp name). Lines before the first header ->
  key = first argument of `run_part(` (fallback "unknown"). additionalContext lists the FAILs plus a
  pointer to docs/dfam_guidelines.md. Each comp entry with FAIL is replaced and set OPEN (re-arm).
- result contains `DFAM_FAIL` and script has NO `run_part(` (legacy, raw adsk) -> additionalContext only, no state.
- comp section with `dfam=0F` (no FAIL) and script has `run_part(` -> clears ONLY that comp's entry.
  Green result without `run_part(` (legacy) clears nothing.
Marker = one JSON file per session: {part: {"state": "OPEN"|"BLOCKED", "lines": [...]}}.
Stop: every OPEN part -> one decision block listing each part's lines (state -> BLOCKED). Next stop passes.
New DFAM_FAIL of that part after a block -> OPEN -> block allowed again.
stop_hook_active -> pass without changing the marker. Bad stdin or error -> silent, exit 0.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

EXECUTE_TOOLS = frozenset(
    {
        "mcp__autodesk-fusion__fusion_mcp_execute",
        "mcp__fusion-cad__execute",
        # plugin-bundled MCP servers: mcp__plugin_<plugin>_<server>__<tool>
        "mcp__plugin_fusion-kit_autodesk-fusion__fusion_mcp_execute",
        "mcp__plugin_fusion-kit_fusion-cad__execute",
    }
)
BUILD_CALL = "run_part("
FAIL_TOKEN = re.compile(r"(?:DFAM|SKQ)_FAIL\b")  # SKQ_FAIL (sketch_quality) blocks like DFAM_FAIL
FAIL_LINE = re.compile(r"(?:DFAM|SKQ)_FAIL[^\n\\\"]*")
GREEN_TOKEN = re.compile(r"dfam=0F\b")
PART_ARG = re.compile(r"run_part\(\s*[\"']?([^\"',)\s]+)")
UNKNOWN_PART = "unknown"
LINE_SPLIT = re.compile(r"\\n|\r?\n")  # real newlines, or literal \n when response was json.dumps'd
COMP_HEADER = re.compile(r"^(?P<comp>[^\n:]+): bodies=")  # report() line: `<comp.name>: bodies=...`
OPEN = "OPEN"
BLOCKED = "BLOCKED"
MAX_LINES = 20


def docs_dir() -> Path:
    """CLAUDE_PLUGIN_ROOT/docs when set, else the repo this hook file sits in (hooks/../docs)."""
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    return Path(root) / "docs" if root else Path(__file__).resolve().parent.parent / "docs"


DFAM_DOC = docs_dir() / "dfam_guidelines.md"
TAIL = f"Fix via fusion_kit builders, see {DFAM_DOC}; opt-out only with dfam_skip attr + reason."
CTX_PREFIX = "[dfam-guard] DFAM_FAIL in fusion_kit report:\n"
LEGACY_PREFIX = "[dfam-guard] DFAM_FAIL in script without run_part() (legacy raw adsk) - warn only, not blocking:\n"
REASON_PREFIX = "[dfam-guard] DFAM_FAIL unresolved after last fusion_kit build (block once per change):\n"


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


def response_text(response: object) -> str:
    return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)


def fail_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in FAIL_LINE.findall(text):
        line = raw.strip()
        if line and line not in out:
            out.append(line)
    return out[:MAX_LINES]


def state_file(session: str, state_dir: Path) -> Path:
    safe = re.sub(r"[^\w-]", "_", session)[:120]
    return state_dir / f"claude-dfam-guard-{safe}"


def bullets(lines: list[str]) -> str:
    return "".join(f"- {line}\n" for line in lines)


def part_key(script: str) -> str:
    """First argument of run_part(...) = part key. Caller guarantees BUILD_CALL in script."""
    m = PART_ARG.search(script)
    return m.group(1) if m else UNKNOWN_PART


def segments(text: str) -> list[tuple[str | None, str]]:
    """(comp, body) per report section. Body runs from `<comp>: bodies=` header to next header.
    Lines before first header -> comp None (caller falls back to run_part literal key)."""
    out: list[tuple[str | None, list[str]]] = [(None, [])]
    for raw in LINE_SPLIT.split(text):
        line = raw.strip()
        m = COMP_HEADER.match(line)
        if m:
            out.append((m.group("comp").strip(), []))
        out[-1][1].append(line)
    return [(comp, "\n".join(body)) for comp, body in out]


def load_state(marker: Path) -> dict[str, tuple[str, list[str]]]:
    """{part: (OPEN|BLOCKED, lines)}. Missing, unreadable or non-JSON marker -> empty."""
    try:
        raw: object = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    state: dict[str, tuple[str, list[str]]] = {}
    for part, entry in raw.items():
        if isinstance(entry, dict) and isinstance(entry.get("state"), str) and isinstance(entry.get("lines"), list):
            state[str(part)] = (str(entry["state"]), [str(x) for x in entry["lines"]])
    return state


def save_state(marker: Path, state: dict[str, tuple[str, list[str]]]) -> None:
    if not state:
        marker.unlink(missing_ok=True)
        return
    data = {part: {"state": st, "lines": lines} for part, (st, lines) in state.items()}
    marker.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def on_post_tool(payload: dict[str, object], state_dir: Path) -> str | None:
    """additionalContext text, or None. Writes/clears the per-part state as side effect."""
    if payload.get("tool_name") not in EXECUTE_TOOLS:
        return None
    marker = state_file(str(payload.get("session_id") or "nosession"), state_dir)
    tool_input = payload.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    text = response_text(payload.get("tool_response"))
    script = execute_script(tool_input)
    if script is None or BUILD_CALL not in script:
        lines = fail_lines(text) if FAIL_TOKEN.search(text) else []
        return LEGACY_PREFIX + bullets(lines) if lines else None
    fallback = part_key(script)
    state = load_state(marker)
    shown: list[str] = []
    changed = False
    for comp, body in segments(text):
        key = fallback if comp is None else comp
        lines = fail_lines(body)
        if lines:
            state[key] = (OPEN, lines)
            shown += lines
            changed = True
        elif GREEN_TOKEN.search(body) and state.pop(key, None) is not None:
            changed = True
    if changed:
        save_state(marker, state)
    return CTX_PREFIX + bullets(shown) + TAIL if shown else None


def on_stop(payload: dict[str, object], state_dir: Path) -> str | None:
    """Block reason, or None to pass. Every OPEN part blocks once in one reason, then turns BLOCKED."""
    if payload.get("stop_hook_active"):
        return None
    marker = state_file(str(payload.get("session_id") or "nosession"), state_dir)
    state = load_state(marker)
    open_parts = [part for part, (st, _) in state.items() if st == OPEN]
    if not open_parts:
        return None
    body = ""
    for part in open_parts:
        lines = state[part][1]
        body += f"{part}:\n" + bullets(lines)
        state[part] = (BLOCKED, lines)
    save_state(marker, state)
    return REASON_PREFIX + body + TAIL


def main() -> int:
    try:
        data: object = json.load(sys.stdin)
        if not isinstance(data, dict):
            return 0
        state_dir = Path(tempfile.gettempdir())
        if "tool_name" in data:
            ctx = on_post_tool(data, state_dir)
            if ctx is not None:
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": ctx}}))
            return 0
        reason = on_stop(data, state_dir)
        if reason is not None:
            print(json.dumps({"decision": "block", "reason": reason}))
    except Exception:  # noqa: BLE001 - fail-open by design: never block a session on a hook bug
        return 0
    return 0


FAIL_A = "DFAM_FAIL hole_min_wall: wall 0.4mm < 0.8mm"
FAIL_B = "DFAM_FAIL overhang: face n_z=0.2 at 38deg < 45deg"
WARN = "DFAM_WARN thin_rib: 1.1mm"
FAIL_RESP = f"bodies=1 bbox_mm=40.0x30.0x10.0 timeline_unnamed=0\n{FAIL_A}\n{WARN}\ndfam=1F/1W"
FAIL2_RESP = f"bodies=1 timeline_unnamed=0\n{FAIL_A}\n{FAIL_B}\ndfam=2F/0W"
FAIL_B_RESP = f"bodies=1 timeline_unnamed=0\n{FAIL_B}\ndfam=1F/0W"
GREEN_DFAM = "bodies=2 bbox_mm=40.0x30.0x10.0 timeline_unnamed=0 dfam=0F/2W"
RED_NO_TOKEN = "bodies=1 timeline_unnamed=0"
BUILD_SCRIPT = 'from fusion_kit.loader import run_part\ndef run(_context):\n    run_part(entry, "build")\n'
LEGACY_SCRIPT = 'import adsk.core\ndef run(_context):\n    print("raw")\n'
EXEC = "mcp__autodesk-fusion__fusion_mcp_execute"
CAD = "mcp__fusion-cad__execute"
A_FAIL = f"A: bodies=1 dfam=1F/0W\n{FAIL_A}"
B_FAIL = f"B: bodies=1 dfam=1F/0W\n{FAIL_B}"
A_GREEN = "A: bodies=2 dfam=0F/0W"
B_GREEN = "B: bodies=2 dfam=0F/0W"
A_FAIL_B_GREEN = f"{A_FAIL}\n{B_GREEN}"
A_B_FAIL = f"{A_FAIL}\n{B_FAIL}"


def build_for(part: str) -> str:
    return f'from fusion_kit.loader import run_part\ndef run(_context):\n    run_part("{part}", "build")\n'


def ex(script: str, response: str, sid: str = "s1", as_string: bool = False, tool: str = EXEC) -> dict[str, object]:
    obj: object = json.dumps({"script": script}) if as_string else {"script": script}
    return {"tool_name": tool, "session_id": sid, "tool_input": {"object": obj}, "tool_response": response}


def stop(sid: str = "s1", active: bool = False) -> dict[str, object]:
    return {"hook_event_name": "Stop", "session_id": sid, "stop_hook_active": active}


def replay(events: list[dict[str, object]], state_dir: Path) -> list[str | None]:
    out: list[str | None] = []
    for ev in events:
        if "tool_name" in ev:
            out.append(on_post_tool(ev, state_dir))
        else:
            out.append(on_stop(ev, state_dir))
    return out


def states(events: list[dict[str, object]], sid: str = "s1") -> dict[str, str]:
    """Per-part state after replay: {part: OPEN|BLOCKED}."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        replay(events, d)
        return {part: st for part, (st, _) in load_state(state_file(sid, d)).items()}


def cases() -> tuple[tuple[str, list[dict[str, object]], list[str | tuple[str, ...] | None]], ...]:
    # (name, events in order, per event: None = silent, str / tuple of str = substring(s) the output must contain)
    return (
        (
            "fail + run_part -> ctx lists FAIL, stop blocks",
            [ex(BUILD_SCRIPT, FAIL_RESP), stop()],
            [FAIL_A, "DFAM_FAIL hole_min_wall"],
        ),
        (
            "fail then green 0F -> marker cleared, stop passes",
            [ex(BUILD_SCRIPT, FAIL_RESP), ex(BUILD_SCRIPT, GREEN_DFAM), stop()],
            [FAIL_A, None, None],
        ),
        (
            "legacy script no run_part -> warn only, stop passes",
            [ex(LEGACY_SCRIPT, FAIL_RESP), stop()],
            ["DFAM_FAIL hole_min_wall", None],
        ),
        (
            "stop blocks once, second stop passes",
            [ex(BUILD_SCRIPT, FAIL_RESP), stop(), stop()],
            [FAIL_A, "DFAM_FAIL hole_min_wall", None],
        ),
        (
            "stop_hook_active passes without consuming block",
            [ex(BUILD_SCRIPT, FAIL_RESP), stop(active=True), stop()],
            [FAIL_A, None, "DFAM_FAIL hole_min_wall"],
        ),
        (
            "new fail after block re-arms block",
            [ex(BUILD_SCRIPT, FAIL_RESP), stop(), ex(BUILD_SCRIPT, FAIL2_RESP), stop()],
            [FAIL_A, "DFAM_FAIL hole_min_wall", FAIL_B, "DFAM_FAIL overhang"],
        ),
        ("green 0F without prior fail -> silent, stop passes", [ex(BUILD_SCRIPT, GREEN_DFAM), stop()], [None, None]),
        ("no dfam token -> silent, stop passes", [ex(BUILD_SCRIPT, RED_NO_TOKEN), stop()], [None, None]),
        (
            "object as JSON string fail detected",
            [ex(BUILD_SCRIPT, FAIL_RESP, as_string=True), stop()],
            [FAIL_A, "DFAM_FAIL hole_min_wall"],
        ),
        (
            "fusion-cad execute fail -> stop blocks",
            [ex(BUILD_SCRIPT, FAIL_RESP, tool=CAD), stop()],
            [FAIL_A, "DFAM_FAIL hole_min_wall"],
        ),
        (
            "other tool with DFAM text -> silent, stop passes",
            [ex(BUILD_SCRIPT, FAIL_RESP, tool="Bash"), stop()],
            [None, None],
        ),
        (
            "marker is per session",
            [ex(BUILD_SCRIPT, FAIL_RESP, sid="a"), stop(sid="b"), stop(sid="a")],
            [FAIL_A, None, "DFAM_FAIL hole_min_wall"],
        ),
        (
            "A FAIL, legacy script green 0F -> A still blocks",
            [ex(build_for("part_a"), FAIL_RESP), ex(LEGACY_SCRIPT, GREEN_DFAM), stop()],
            [FAIL_A, None, FAIL_A],
        ),
        (
            "A FAIL, run_part(B) green 0F -> A still blocks with A lines",
            [ex(build_for("part_a"), FAIL_RESP), ex(build_for("part_b"), GREEN_DFAM), stop()],
            [FAIL_A, None, FAIL_A],
        ),
        (
            "A FAIL + B FAIL -> one block lists both parts",
            [ex(build_for("part_a"), FAIL_RESP), ex(build_for("part_b"), FAIL_B_RESP), stop()],
            [FAIL_A, FAIL_B, (FAIL_A, FAIL_B)],
        ),
        (
            "A FAIL then A green 0F -> A cleared, stop passes",
            [ex(build_for("part_a"), FAIL_RESP), ex(build_for("part_a"), GREEN_DFAM), stop()],
            [FAIL_A, None, None],
        ),
        ("stop without marker passes", [stop()], [None]),
        (
            "A + B FAIL in one report -> one block names both comps",
            [ex(BUILD_SCRIPT, A_B_FAIL), stop()],
            [(FAIL_A, FAIL_B), ("A:\n", "B:\n", FAIL_A, FAIL_B)],
        ),
        (
            "comp A FAIL then comp A 0F -> A cleared, stop passes",
            [ex(BUILD_SCRIPT, A_FAIL), stop(), ex(BUILD_SCRIPT, A_GREEN), stop()],
            [FAIL_A, "A:\n", None, None],
        ),
    )


def run_main(payload_text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, __file__],
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

    def matches(got: str | None, want: str | tuple[str, ...] | None) -> bool:
        if want is None:
            return got is None
        if got is None:
            return False
        subs = (want,) if isinstance(want, str) else want
        return all(sub in got for sub in subs)

    for name, events, want in cases():
        with tempfile.TemporaryDirectory() as tmp:  # fresh state per case: no leakage between cases
            got = replay(events, Path(tmp))
        ok = len(got) == len(want) and all(matches(g, w) for g, w in zip(got, want))
        check(name, ok, f"got {got!a}")

    state_cases: list[tuple[str, list[dict[str, object]], dict[str, str]]] = [
        ("A FAIL + B 0F in one report -> A OPEN, no B entry", [ex(BUILD_SCRIPT, A_FAIL_B_GREEN)], {"A": OPEN}),
        (
            "B FAIL, then A FAIL + B 0F -> A OPEN, B cleared",
            [ex(BUILD_SCRIPT, B_FAIL), ex(BUILD_SCRIPT, A_FAIL_B_GREEN)],
            {"A": OPEN},
        ),
        ("A FAIL + B FAIL in one report -> both OPEN", [ex(BUILD_SCRIPT, A_B_FAIL)], {"A": OPEN, "B": OPEN}),
        (
            "run_part(entry) A FAIL, then B 0F, comp names differ -> A isolated",
            [ex(BUILD_SCRIPT, A_FAIL), ex(BUILD_SCRIPT, B_GREEN)],
            {"A": OPEN},
        ),
        (
            "run_part(entry) B FAIL, then A 0F, comp names differ -> B isolated",
            [ex(BUILD_SCRIPT, B_FAIL), ex(BUILD_SCRIPT, A_GREEN)],
            {"B": OPEN},
        ),
        ("no comp header -> key = run_part literal", [ex(build_for("part_a"), FAIL_RESP)], {"part_a": OPEN}),
        ("comp 0F without prior FAIL -> no state", [ex(BUILD_SCRIPT, B_GREEN)], {}),
        ("legacy script with comp FAIL -> no state", [ex(LEGACY_SCRIPT, A_FAIL)], {}),
    ]
    for name, events, want_state in state_cases:
        got_state = states(events)
        check(name, got_state == want_state, f"got {got_state!a}")

    sid = f"sel-e2e-{uuid.uuid4().hex}"
    try:
        r = run_main(json.dumps(ex(BUILD_SCRIPT, FAIL_RESP, sid=sid)))
        out: object = json.loads(r.stdout) if r.stdout.strip() else {}
        hso = out.get("hookSpecificOutput", {}) if isinstance(out, dict) else {}
        check(
            "e2e PostToolUse fail -> hookSpecificOutput additionalContext",
            r.returncode == 0
            and isinstance(hso, dict)
            and hso.get("hookEventName") == "PostToolUse"
            and "DFAM_FAIL hole_min_wall" in str(hso.get("additionalContext", "")),
            ascii(r.stdout)[:200] + ascii(r.stderr)[:200],
        )
        r = run_main(json.dumps(stop(sid=sid)))
        out = json.loads(r.stdout) if r.stdout.strip() else {}
        check(
            "e2e Stop -> decision block JSON",
            r.returncode == 0
            and isinstance(out, dict)
            and out.get("decision") == "block"
            and "DFAM_FAIL hole_min_wall" in str(out.get("reason")),
            ascii(r.stdout)[:200],
        )
        r = run_main(json.dumps(stop(sid=sid)))
        check("e2e second Stop -> silent", r.returncode == 0 and r.stdout.strip() == "", ascii(r.stdout)[:200])
        r = run_main("not json")
        check(
            "e2e bad stdin -> silent exit 0",
            r.returncode == 0 and r.stdout.strip() == "",
            ascii(r.stdout)[:200] + ascii(r.stderr)[:200],
        )
    finally:
        state_file(sid, Path(tempfile.gettempdir())).unlink(missing_ok=True)

    for name, ok, detail in results:
        print(f"PASS {name}" if ok else f"FAIL {name}: {detail}")
    passed = sum(ok for _, ok, _ in results)
    print(f"{passed}/{len(results)} PASS")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
