"""docs/fusion_kit_api.md must list every public fusion_kit function and no stale ones.

Offline: parses source with ast (no adsk import).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "fusion_kit"
DOC = ROOT / "docs" / "fusion_kit_api.md"
ROW = re.compile(r"^\| ([a-z_]+\.[A-Za-z_]\w*) \|")
HEADER = "| helper | signature | replaces raw adsk |"


def public_functions() -> set[str]:
    found: set[str] = set()
    for path in sorted(PKG.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                found.add(f"{path.stem}.{node.name}")
    return found


def documented_rows() -> list[str]:
    rows: list[str] = []
    for line in DOC.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if match is not None:
            rows.append(match.group(1))
    return rows


def test_doc_has_hook_header() -> None:
    assert HEADER in DOC.read_text(encoding="utf-8").splitlines()


def test_every_public_function_documented() -> None:
    missing = public_functions() - set(documented_rows())
    assert not missing, f"public fusion_kit functions missing from {DOC.name}: {sorted(missing)}"


def test_no_stale_rows() -> None:
    stale = set(documented_rows()) - public_functions()
    assert not stale, f"rows in {DOC.name} with no public function: {sorted(stale)}"


def test_no_duplicate_rows() -> None:
    rows = documented_rows()
    dups = sorted({r for r in rows if rows.count(r) > 1})
    assert not dups, f"duplicate rows: {dups}"
