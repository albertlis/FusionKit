"""PreToolUse nudge (never blocks): raw adsk in Fusion part code -> fusion_kit helper.

Targets: `mcp__autodesk-fusion__fusion_mcp_execute` and `mcp__fusion-cad__execute` (script text) and Write/Edit on
`*/parts/*.py` (content / new_string). `parts/helpers.py` is exempt: raw adsk is the
documented escape there (CLAUDE.md). Source: docs/fusion_kit_api.md, rows
`| helper | signature | replaces raw adsk |`, third cell = backticked raw tokens.
Index lookup: CLAUDE_PLUGIN_ROOT first, then the repo this hook file sits in.
Token found (word boundary) and its helper not already called in the same text ->
one additionalContext block. Missing docs or any error -> silent exit 0.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXECUTE = "mcp__autodesk-fusion__fusion_mcp_execute"
FUSION_CAD_EXECUTE = "mcp__fusion-cad__execute"
# Plugin-bundled MCP servers get scoped names: mcp__plugin_<plugin>_<server>__<tool>
PLUGIN_EXECUTE = "mcp__plugin_fusion-kit_autodesk-fusion__fusion_mcp_execute"
PLUGIN_FUSION_CAD_EXECUTE = "mcp__plugin_fusion-kit_fusion-cad__execute"
EXECUTE_TOOLS = frozenset({EXECUTE, FUSION_CAD_EXECUTE, PLUGIN_EXECUTE, PLUGIN_FUSION_CAD_EXECUTE})
EDIT_TOOLS = frozenset({"Write", "Edit"})
PARTS_GLOB = "*/parts/*.py"
EXEMPT_NAME = "helpers.py"
FK_PREFIX = "fusion_kit."
TABLE_HEAD = ["helper", "signature", "replaces raw adsk"]
TOKEN = re.compile(r"`([A-Za-z_]\w*)`")
HELPER = re.compile(r"[A-Za-z_][\w.]*")
DOCS_REL = Path("docs") / "fusion_kit_api.md"
HEADER = "FUSION_KIT: raw adsk in code, a helper exists in fusion_kit:"
FOOTER = (
    "Index: docs/fusion_kit_api.md. No helper -> raw adsk in parts/helpers.py, then open a GitHub issue "
    "(gap template) at https://github.com/albertlis/FusionKit/issues"
)


def docs_candidates() -> list[Path]:
    """Index lookup order: plugin root (CLAUDE_PLUGIN_ROOT), then the repo this hook file sits in."""
    out: list[Path] = []
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root:
        out.append(Path(root) / DOCS_REL)
    out.append(Path(__file__).resolve().parent.parent / DOCS_REL)
    return out


def parse_table(text: str) -> dict[str, list[str]]:
    """raw adsk token -> helper names (fusion_kit. prefix stripped)."""
    out: dict[str, list[str]] = {}
    in_table = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            in_table = False
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not in_table:
            in_table = [c.lower() for c in cells[:3]] == TABLE_HEAD
            continue
        # separator row (---) or empty row: nothing but - : and spaces
        if len(cells) < 3 or not set("".join(cells)) - set("-: "):
            continue
        name = HELPER.match(cells[0].strip("`"))
        if name is None:
            continue
        helper = name.group(0).removeprefix(FK_PREFIX)
        for token in TOKEN.findall(cells[2]):
            helpers = out.setdefault(token, [])
            if helper not in helpers:
                helpers.append(helper)
    return out


def load_table(paths: list[Path]) -> dict[str, list[str]] | None:
    for path in paths:
        try:
            table = parse_table(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if table:
            return table
    return None


def uses(text: str, helper: str) -> bool:
    """Helper already called in text: `report.inside(` or bare `inside(`."""
    last = helper.rsplit(".", 1)[-1]
    return re.search(rf"(?<!\w){re.escape(last)}\s*\(", text) is not None


def hit_lines(text: str, table: dict[str, list[str]]) -> list[str]:
    lines: list[str] = []
    for token in sorted(table):
        if not re.search(rf"\b{re.escape(token)}\b", text):
            continue
        helpers = [h for h in table[token] if not uses(text, h)]
        if helpers:
            lines.append(f"- {token} → " + " / ".join(FK_PREFIX + h for h in helpers))
    return lines


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


def target_text(tool_name: str, tool_input: dict[str, object]) -> str | None:
    """Code to inspect: execute script, or new text of a parts/*.py write."""
    if tool_name in EXECUTE_TOOLS:
        return execute_script(tool_input)
    if tool_name not in EDIT_TOOLS:
        return None
    path = tool_input.get("file_path")
    if not isinstance(path, str):
        return None
    norm = path.replace("\\", "/").lower()
    if not fnmatch.fnmatch(norm, PARTS_GLOB) or norm.rsplit("/", 1)[-1] == EXEMPT_NAME:
        return None
    field = tool_input.get("content") if tool_name == "Write" else tool_input.get("new_string")
    return field if isinstance(field, str) else None


def nudge(payload: dict[str, object], table: dict[str, list[str]]) -> str | None:
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return None
    text = target_text(tool_name, tool_input)
    if not text:
        return None
    lines = hit_lines(text, table)
    if not lines:
        return None
    return "\n".join([HEADER, *lines, FOOTER])


def main() -> int:
    try:
        payload: object = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        table = load_table(docs_candidates())
        if table is None:
            return 0
        msg = nudge(payload, table)
        if msg is not None:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}))
    except Exception:  # noqa: BLE001 - fail-open by design: never block a tool call
        return 0
    return 0


DOCS_TEXT = """# fusion_kit API

| helper | signature | replaces raw adsk |
|---|---|---|
| report.inside | inside(body, point_mm) -> bool | `pointContainment` |
| features.normal_at | normal_at(face, point) | `getNormalAtPoint`, `pointContainment` |
| enums.Plane | enum | |
| sketch.anchor | anchor(sk, corner, h, v) | `isFullyConstrained` |
"""

CASES: tuple[tuple[str, str, dict[str, object], list[str]], ...] = (
    # (name, tool, input, exact hit lines expected; [] = silent)
    (
        "execute script hit",
        EXECUTE,
        {"script": "b.pointContainment(p)"},
        ["- pointContainment → fusion_kit.report.inside / fusion_kit.features.normal_at"],
    ),
    (
        "execute object.script hit",
        EXECUTE,
        {"object": {"script": "n = f.getNormalAtPoint(p)"}},
        ["- getNormalAtPoint → fusion_kit.features.normal_at"],
    ),
    (
        "execute object as JSON string",
        EXECUTE,
        {"object": '{"script": "f.getNormalAtPoint(p)"}'},
        ["- getNormalAtPoint → fusion_kit.features.normal_at"],
    ),
    (
        "fusion-cad execute script hit",
        FUSION_CAD_EXECUTE,
        {"script": "b.pointContainment(p)"},
        ["- pointContainment → fusion_kit.report.inside / fusion_kit.features.normal_at"],
    ),
    ("no-hit plain python", EXECUTE, {"script": "print(1)"}, []),
    ("word boundary", EXECUTE, {"script": "b.pointContainmentX(p)"}, []),
    (
        "helper already called",
        EXECUTE,
        {"script": "from fusion_kit.sketch import anchor\nanchor(sk, 0, 1, 1)\nsk.isFullyConstrained"},
        [],
    ),
    (
        "one of two helpers already called",
        EXECUTE,
        {"script": "report.inside(b, p)\nb.pointContainment(p)"},
        ["- pointContainment → fusion_kit.features.normal_at"],
    ),
    (
        "Write parts hit",
        "Write",
        {"file_path": r"C:\proj\my_part\parts\body.py", "content": "f.getNormalAtPoint(p)"},
        ["- getNormalAtPoint → fusion_kit.features.normal_at"],
    ),
    (
        "Edit parts new_string hit",
        "Edit",
        {"file_path": "C:/proj/X/parts/body.py", "old_string": "a", "new_string": "b.isFullyConstrained"},
        ["- isFullyConstrained → fusion_kit.sketch.anchor"],
    ),
    (
        "Edit old_string ignored",
        "Edit",
        {"file_path": "C:/proj/X/parts/body.py", "old_string": "pointContainment", "new_string": "pass"},
        [],
    ),
    (
        "non-parts py ignored",
        "Write",
        {"file_path": "C:/proj/fusion_kit/features.py", "content": "pointContainment"},
        [],
    ),
    ("helpers.py exempt", "Write", {"file_path": "C:/proj/X/parts/helpers.py", "content": "pointContainment"}, []),
    ("other tool ignored", "Bash", {"command": "pointContainment"}, []),
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
        plugin = Path(tmp) / "plugin"
        (plugin / "docs").mkdir(parents=True)
        (plugin / "docs" / "fusion_kit_api.md").write_text(DOCS_TEXT, encoding="utf-8")
        # copy of the hook with no docs beside it -> no index reachable
        bare_hook = Path(tmp) / "bare" / "hooks" / "fusion_kit_nudge.py"
        bare_hook.parent.mkdir(parents=True)
        shutil.copy(Path(__file__).resolve(), bare_hook)
        table = parse_table(DOCS_TEXT)
        check("table parsed", table.get("pointContainment") == ["report.inside", "features.normal_at"], ascii(table))
        for name, tool, tool_input, expected in CASES:
            msg = nudge({"tool_name": tool, "tool_input": tool_input}, table)
            got = [] if msg is None else [ln for ln in msg.splitlines() if ln.startswith("- ")]
            check(name, got == expected, f"got {got!a}")
        check("missing docs -> no table", load_table([Path(tmp) / "absent.md"]) is None)
        check("real index parses", load_table([Path(__file__).resolve().parent.parent / DOCS_REL]) is not None)
        hit = json.dumps({"tool_name": EXECUTE, "tool_input": {"script": "b.pointContainment(p)"}})
        r = run_main(hit, plugin)
        hso: object = json.loads(r.stdout).get("hookSpecificOutput", {}) if r.stdout.strip() else {}
        ctx = hso.get("additionalContext", "") if isinstance(hso, dict) else ""
        check(
            "e2e hit via CLAUDE_PLUGIN_ROOT -> additionalContext, no permissionDecision",
            r.returncode == 0
            and "- pointContainment" in ctx
            and isinstance(hso, dict)
            and "permissionDecision" not in hso,
            ascii(r.stdout)[:200],
        )
        r = run_main(hit, None, bare_hook)
        check("e2e no index reachable -> silent", r.returncode == 0 and r.stdout.strip() == "", ascii(r.stdout)[:200])
        r = run_main("not json", plugin)
        check("e2e bad stdin -> silent", r.returncode == 0 and r.stdout.strip() == "", ascii(r.stdout)[:200])

    for name, ok, detail in results:
        print(f"PASS {name}" if ok else f"FAIL {name}: {detail}")
    passed = sum(ok for _, ok, _ in results)
    print(f"{passed}/{len(results)} PASS")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
