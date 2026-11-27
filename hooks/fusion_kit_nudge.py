"""PreToolUse nudge + deny-once: raw adsk in Fusion part code -> fusion_kit helper.

Targets: `mcp__autodesk-fusion__fusion_mcp_execute` and `mcp__fusion-cad__execute` (script text) and Write/Edit on
`*/parts/*.py` and mechanism entries `<pkg>/<pkg>.py` (content / new_string). `parts/helpers.py` is
exempt: raw adsk is the
documented escape there (CLAUDE.md). Source: docs/fusion_kit_api.md, rows
`| helper | signature | replaces raw adsk |`, third cell = backticked raw tokens (dotted allowed).
Token prefix `!` (`!addNewComponent`) = exact 1:1 replacement: first hit per (session, target, token) is a
PreToolUse deny; the retry passes (nudge only). Plain token = additionalContext nudge, never denied.
Nothing inside the FusionKit repo itself is denied (fusion_kit/ implements the helpers).
Index lookup: CLAUDE_PLUGIN_ROOT first, then the repo this hook file sits in.
Token found (word boundary) and its helper not already called in the same text ->
one additionalContext block. Missing docs or any error -> silent exit 0.
"""

from __future__ import annotations

import fnmatch
import hashlib
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
TOKEN = re.compile(r"`(!?[A-Za-z_][\w.]*)`")
STRICT = "!"
HELPER = re.compile(r"[A-Za-z_][\w.]*")
DOCS_REL = Path("docs") / "fusion_kit_api.md"
API_DIR = "api"  # per-module cards docs/api/<module>.md hold the rows
REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_NORM = str(REPO_ROOT).replace("\\", "/").lower()
MARK_PREFIX = "claude-fusion-nudge-deny-"
HEADER = "FUSION_KIT: raw adsk in code, a helper exists in fusion_kit:"
DENY_HEAD = "FUSION_KIT: raw adsk with an exact fusion_kit replacement. Use the helper; the retry passes."
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
    out.append(REPO_ROOT / DOCS_REL)
    return out


def parse_index(text: str) -> tuple[dict[str, list[str]], frozenset[str]]:
    """raw adsk token -> helper names (fusion_kit. prefix stripped), and the strict (`!`) tokens."""
    out: dict[str, list[str]] = {}
    strict: set[str] = set()
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
        for cell_token in TOKEN.findall(cells[2]):
            token = cell_token.removeprefix(STRICT)
            if cell_token.startswith(STRICT):
                strict.add(token)
            helpers = out.setdefault(token, [])
            if helper not in helpers:
                helpers.append(helper)
    return out, frozenset(strict)


def parse_table(text: str) -> dict[str, list[str]]:
    return parse_index(text)[0]


def read_part(path: Path, label: str) -> str | None:
    """One doc file. Unreadable or not UTF-8 -> stderr warning line and None: only this file is skipped."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"fusion_kit_nudge: skipped {label}: {type(exc).__name__}", file=sys.stderr)
        return None


def load_index(paths: list[Path]) -> tuple[dict[str, list[str]], frozenset[str]] | None:
    """Index plus every module card in the sibling api/ dir: rows live in the cards. Bad file -> skipped, others load."""
    for path in paths:
        text = read_part(path, DOCS_REL.as_posix())
        if text is None:
            continue
        for card in sorted((path.parent / API_DIR).glob("*.md")):
            part = read_part(card, f"docs/{API_DIR}/{card.name}")
            if part is not None:
                text += "\n" + part
        index = parse_index(text)
        if index[0]:
            return index
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


def strict_hits(text: str, table: dict[str, list[str]], strict: frozenset[str]) -> list[str]:
    """Strict (`!`) tokens found in text with at least one helper not already called. Sorted."""
    return [
        t
        for t in sorted(strict)
        if re.search(rf"\b{re.escape(t)}\b", text) and any(not uses(text, h) for h in table[t])
    ]


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


def checked_path(norm: str) -> bool:
    """Lowercase forward-slash path: parts/*.py except helpers.py, or mechanism entry <pkg>/<pkg>.py."""
    if fnmatch.fnmatch(norm, PARTS_GLOB):
        return norm.rsplit("/", 1)[-1] != EXEMPT_NAME
    folder, _, name = norm.rpartition("/")
    pkg = folder.rpartition("/")[2]
    return bool(pkg) and name == f"{pkg}.py"


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
    if not checked_path(norm):
        return None
    field = tool_input.get("content") if tool_name == "Write" else tool_input.get("new_string")
    return field if isinstance(field, str) else None


def in_fk_repo(tool_name: str, tool_input: dict[str, object]) -> bool:
    """File lives inside the FusionKit repo (fusion_kit/ implements the helpers): never denied."""
    path = tool_input.get("file_path") if tool_name in EDIT_TOOLS else None
    return isinstance(path, str) and path.replace("\\", "/").lower().startswith(REPO_NORM + "/")


def target_key(tool_name: str, tool_input: dict[str, object]) -> str:
    path = tool_input.get("file_path") if tool_name in EDIT_TOOLS else None
    return path.replace("\\", "/").lower() if isinstance(path, str) else "execute"


def marker_file(session: str, target: str, token: str, state_dir: Path) -> Path:
    digest = hashlib.sha1(f"{target}|{token}".encode()).hexdigest()[:16]
    safe = re.sub(r"[^\w-]", "_", session)[:120]
    return state_dir / f"{MARK_PREFIX}{safe}-{digest}"


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


def decide(
    payload: dict[str, object], table: dict[str, list[str]], strict: frozenset[str], state_dir: Path
) -> dict[str, object] | None:
    """Hook output: deny once per (session, target, strict token) outside the FusionKit repo, else nudge, else None."""
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return None
    text = target_text(tool_name, tool_input)
    if not text or not hit_lines(text, table):
        return None
    if not in_fk_repo(tool_name, tool_input):
        session = str(payload.get("session_id") or "nosession")
        target = target_key(tool_name, tool_input)
        for token in strict_hits(text, table, strict):
            marker = marker_file(session, target, token, state_dir)
            if marker.exists():
                continue
            try:
                marker.write_text("1", encoding="utf-8")
            except OSError:
                continue  # cannot remember the deny -> no deny, never deny in a loop
            helpers = [FK_PREFIX + h for h in table[token] if not uses(text, h)]
            reason = "\n".join([DENY_HEAD, f"- {token} → " + " / ".join(helpers), FOOTER])
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
    msg = nudge(payload, table)
    if msg is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}


def main() -> int:
    try:
        payload: object = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        index = load_index(docs_candidates())
        if index is None:
            return 0
        out = decide(payload, index[0], index[1], Path(tempfile.gettempdir()))
        if out is not None:
            print(json.dumps(out))
    except Exception:  # noqa: BLE001 - fail-open by design: never block a tool call
        return 0
    return 0


DOCS_TEXT = """# fusion_kit API

| helper | signature | replaces raw adsk |
|---|---|---|
| report.inside | inside(body, point_mm) -> bool | `pointContainment` |
| features.normal_at | normal_at(face, point) | `getNormalAtPoint`, `pointContainment` |
| component.new_occ | new_occ(root, name) | `!addNewComponent` |
| features.select_edges | select_edges(body) | `startVertex` |
| dfam.facts_from_body | facts_from_body(body) | `Cylinder.cast` |
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
        "mechanism entry <pkg>/<pkg>.py hit",
        "Write",
        {"file_path": "C:/proj/my_pkg/my_pkg.py", "content": "f.getNormalAtPoint(p)"},
        ["- getNormalAtPoint → fusion_kit.features.normal_at"],
    ),
    (
        "other top-level py in package ignored",
        "Write",
        {"file_path": "C:/proj/my_pkg/other.py", "content": "f.getNormalAtPoint(p)"},
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
    (
        "dotted token hit (was dropped by old token regex)",
        EXECUTE,
        {"script": "c = adsk.fusion.Cylinder.cast(f)"},
        ["- Cylinder.cast → fusion_kit.dfam.facts_from_body"],
    ),
    (
        "plain token hit (edge loop)",
        EXECUTE,
        {"script": "v = e.startVertex.geometry"},
        ["- startVertex → fusion_kit.features.select_edges"],
    ),
    (
        "strict token hit (execute) still nudge-listed",
        EXECUTE,
        {"script": "occ = root.occurrences.addNewComponent(m)"},
        ["- addNewComponent → fusion_kit.component.new_occ"],
    ),
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


def _hso(out: dict[str, object] | None) -> dict[str, object]:
    if out is None:
        return {}
    hso = out.get("hookSpecificOutput")
    return hso if isinstance(hso, dict) else {}


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
        table, strict = parse_index(DOCS_TEXT)
        check("table parsed", table.get("pointContainment") == ["report.inside", "features.normal_at"], ascii(table))
        check("strict token parsed", strict == frozenset({"addNewComponent"}), ascii(strict))
        for name, tool, tool_input, expected in CASES:
            msg = nudge({"tool_name": tool, "tool_input": tool_input}, table)
            got = [] if msg is None else [ln for ln in msg.splitlines() if ln.startswith("- ")]
            check(name, got == expected, f"got {got!a}")

        state = Path(tmp) / "state"
        state.mkdir()
        parts_body = str(REPO_ROOT.parent / "Projektowanie3D" / "my_part" / "parts" / "body.py")
        strict_write = {
            "tool_name": "Write",
            "session_id": "sel-deny-a",
            "tool_input": {"file_path": parts_body, "content": "occ = root.occurrences.addNewComponent(m)"},
        }
        first = _hso(decide(strict_write, table, strict, state))
        second = _hso(decide(strict_write, table, strict, state))
        check(
            "(a) strict Write parts -> deny first, retry allowed (nudge)",
            first.get("permissionDecision") == "deny"
            and "addNewComponent" in str(first.get("permissionDecisionReason", ""))
            and second.get("permissionDecision") is None
            and "additionalContext" in second,
            f"first {ascii(first)[:160]} second {ascii(second)[:160]}",
        )
        other_session = {**strict_write, "session_id": "sel-deny-b"}
        check(
            "deny is per session: other session denied again",
            _hso(decide(other_session, table, strict, state)).get("permissionDecision") == "deny",
        )
        other_file = {
            **strict_write,
            "session_id": "sel-deny-c",
            "tool_input": {"file_path": parts_body + ".other.py", "content": "addNewComponent(m)"},
        }
        check(
            "deny is per file: other file in same session denied",
            _hso(decide(other_file, table, strict, state)).get("permissionDecision") == "deny",
        )
        plain = {
            "tool_name": "Write",
            "session_id": "sel-plain",
            "tool_input": {"file_path": parts_body, "content": "v = e.startVertex.geometry"},
        }
        check(
            "(b) plain token -> nudge, never deny",
            _hso(decide(plain, table, strict, state)).get("permissionDecision") is None
            and "additionalContext" in _hso(decide(plain, table, strict, state)),
        )
        in_repo = {
            "tool_name": "Write",
            "session_id": "sel-repo",
            "tool_input": {
                "file_path": str(REPO_ROOT / "fusion_kit" / "parts" / "x.py"),
                "content": "occ = root.occurrences.addNewComponent(m)",
            },
        }
        repo_hso = _hso(decide(in_repo, table, strict, state))
        check(
            "(c) file under FusionKit repo -> no deny (nudge only)",
            repo_hso.get("permissionDecision") is None and "additionalContext" in repo_hso,
            ascii(repo_hso)[:160],
        )
        check("missing docs -> no index", load_index([Path(tmp) / "absent.md"]) is None)
        real = load_index([REPO_ROOT / DOCS_REL])
        check(
            "real index parses (strict addNewComponent, dotted Cylinder.cast)",
            real is not None
            and "addNewComponent" in real[1]
            and "Cylinder.cast" in real[0]
            and "startVertex" in real[0],
            ascii(real)[:200],
        )
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
        r = run_main("not json", plugin)
        check("e2e bad stdin -> silent", r.returncode == 0 and r.stdout.strip() == "", ascii(r.stdout)[:200])
        r = run_main(hit, None, bare_hook)
        check("e2e no index reachable -> silent", r.returncode == 0 and r.stdout.strip() == "", ascii(r.stdout)[:200])

        sid = f"sel-e2e-{os.getpid()}"
        e2e = json.dumps(
            {
                "tool_name": "Write",
                "session_id": sid,
                "tool_input": {"file_path": parts_body, "content": "root.occurrences.addNewComponent(m)"},
            }
        )
        real_state = Path(tempfile.gettempdir())
        marker = marker_file(sid, target_key("Write", {"file_path": parts_body}), "addNewComponent", real_state)
        try:
            r1 = run_main(e2e, plugin)
            d1 = _hso(json.loads(r1.stdout)) if r1.stdout.strip() else {}
            check(
                "e2e first strict Write -> deny JSON",
                r1.returncode == 0 and d1.get("permissionDecision") == "deny",
                ascii(r1.stdout)[:200],
            )
            r2 = run_main(e2e, plugin)
            d2 = _hso(json.loads(r2.stdout)) if r2.stdout.strip() else {}
            check(
                "e2e retry -> nudge, no deny",
                r2.returncode == 0 and d2.get("permissionDecision") is None and "additionalContext" in d2,
                ascii(r2.stdout)[:200],
            )
        finally:
            marker.unlink(missing_ok=True)

    for name, ok, detail in results:
        print(f"PASS {name}" if ok else f"FAIL {name}: {detail}")
    passed = sum(ok for _, ok, _ in results)
    print(f"{passed}/{len(results)} PASS")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
