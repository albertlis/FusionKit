"""PreToolUse guard (matcher Agent): deny ONCE per session a Fusion brief without the docs ref.

A brief that mentions Fusion (fusion_mcp_execute, adsk, fusion_kit, /parts/, fusion)
and does not reference docs/fusion_kit_api.md is denied once per session, with a reason
to add the file to FILES: (or paste the helper list). The retry in the same session
passes. Docs lookup = same as fusion_kit_nudge: CLAUDE_PLUGIN_ROOT first, then the repo
this hook file sits in. No docs found -> nothing to point at -> allow. Any error -> allow:
a guard must not block an agent spawn on its own bug. State: one marker file per session
in the temp dir (jira-guard pattern).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

TRIGGERS = ("fusion_mcp_execute", "adsk", "fusion_kit", "/parts/", "fusion")
DOCS_REF = "fusion_kit_api.md"
DOCS_REL = Path("docs") / DOCS_REF
REASON = (
    "Fusion brief without docs/fusion_kit_api.md — add it to FILES: (agent must read the helper index) "
    "or paste the index. Retry will pass."
)


def docs_candidates() -> list[Path]:
    """Index lookup order: plugin root (CLAUDE_PLUGIN_ROOT), then the repo this hook file sits in."""
    out: list[Path] = []
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root:
        out.append(Path(root) / DOCS_REL)
    out.append(Path(__file__).resolve().parent.parent / DOCS_REL)
    return out


def needs_docs(prompt: str) -> bool:
    low = prompt.lower().replace("\\", "/")
    return any(t in low for t in TRIGGERS) and DOCS_REF not in low


def state_file(session: str, state_dir: Path) -> Path:
    safe = re.sub(r"[^\w-]", "_", session)[:120]
    return state_dir / f"claude-fusion-brief-guard-{safe}"


def decide(prompt: str, session: str, docs_exist: bool, state_dir: Path) -> str | None:
    """Deny reason, or None to allow. Denies once per session."""
    if not docs_exist or not needs_docs(prompt):
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
        if not isinstance(data, dict) or data.get("tool_name") != "Agent":
            return 0
        tool_input = data.get("tool_input")
        prompt = tool_input.get("prompt") if isinstance(tool_input, dict) else None
        if not isinstance(prompt, str):
            return 0
        reason = decide(
            prompt,
            str(data.get("session_id") or "nosession"),
            any(p.is_file() for p in docs_candidates()),
            Path(tempfile.gettempdir()),
        )
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
    except Exception:  # noqa: BLE001 - fail-open by design: never block an agent spawn on a guard bug
        return 0
    return 0


P = "Write a part via fusion_mcp_execute"

CASES: tuple[tuple[str, str, bool, str | None], ...] = (
    # (name, prompt, docs file present, expected: REASON = deny, None = allow)
    ("hit: fusion_mcp_execute, no docs ref", P, True, REASON),
    ("hit: parts path", "Build my_part/parts/body.py with dimensions", True, REASON),
    ("hit: windows parts path", r"Build C:\proj\my_part\parts\body.py", True, REASON),
    ("hit: uppercase FUSION_KIT", "Import FUSION_KIT in the script", True, REASON),
    ("no-hit: plain python", "Sum the columns of a CSV", True, None),
    ("docs ref present", "Read docs/fusion_kit_api.md, then build my_part/parts/body.py", True, None),
    ("docs file missing -> allow", P, False, None),
)


def run_main(
    payload_text: str, plugin_root: Path | None, script: Path = Path(__file__)
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    if plugin_root is not None:
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
    return subprocess.run(
        [sys.executable, str(script)],
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
        state = Path(tmp)
        for name, prompt, docs, want in CASES:
            got = decide(prompt, f"sel-{name}", docs, state)
            check(name, got == want, f"got {got!a}")
        first = decide(P, "flow", True, state)
        second = decide(P, "flow", True, state)
        check(
            "deny then allow (retry passes)", first == REASON and second is None, f"first {first!a} second {second!a}"
        )
        check(
            "once per session: other prompt same session allowed",
            decide("Other brief fusion_kit", "flow", True, state) is None,
        )
        check("new session denied again", decide(P, "flow-2", True, state) == REASON)
        check("state write failure -> allow", decide(P, "flow-3", True, state / "missing") is None)

        plugin = Path(tmp) / "plugin"
        (plugin / "docs").mkdir(parents=True)
        (plugin / "docs" / "fusion_kit_api.md").write_text("spis", encoding="utf-8")
        # copy of the hook with no docs beside it -> no index reachable
        bare_hook = Path(tmp) / "bare" / "hooks" / "fusion_kit_brief_guard.py"
        bare_hook.parent.mkdir(parents=True)
        shutil.copy(Path(__file__).resolve(), bare_hook)
        sid = f"sel-e2e-{uuid.uuid4().hex}"
        payload = json.dumps({"tool_name": "Agent", "session_id": sid, "tool_input": {"prompt": P}})
        try:
            r1 = run_main(payload, plugin)
            hso: object = json.loads(r1.stdout).get("hookSpecificOutput", {}) if r1.stdout.strip() else {}
            ok1 = (
                isinstance(hso, dict)
                and hso.get("permissionDecision") == "deny"
                and hso.get("permissionDecisionReason") == REASON
            )
            check(
                "e2e first call via CLAUDE_PLUGIN_ROOT -> deny JSON", r1.returncode == 0 and ok1, ascii(r1.stdout)[:200]
            )
            r2 = run_main(payload, plugin)
            check("e2e retry -> silent allow", r2.returncode == 0 and r2.stdout.strip() == "", ascii(r2.stdout)[:200])
            sid2 = f"sel-e2e-nodocs-{uuid.uuid4().hex}"
            r3 = run_main(
                json.dumps({"tool_name": "Agent", "session_id": sid2, "tool_input": {"prompt": P}}), None, bare_hook
            )
            check(
                "e2e no index reachable -> silent",
                r3.returncode == 0 and r3.stdout.strip() == "",
                ascii(r3.stdout)[:200],
            )
            r4 = run_main("not json", plugin)
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
