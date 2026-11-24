"""docs/fusion_kit_api.md must list every public fusion_kit function and no stale ones.

Offline: parses source with ast (no adsk import).
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "fusion_kit"
DOC = ROOT / "docs" / "fusion_kit_api.md"
ROW = re.compile(r"^\| ([a-z_]+\.[A-Za-z_]\w*) \|")
HEADER = "| helper | signature | replaces raw adsk |"
LINE_REF = re.compile(r" — ([a-z_]+)\.py:(\d+) \|")
ROW_MAX = 160
# col3 tokens alone exceed ROW_MAX; tokens stay byte-identical (hook parses them), so these rows may run long.
TOKEN_HEAVY = frozenset({"features_extrude.extrude", "param_sweep.sweep_params", "sketch_quality.sketch_facts"})


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


def doc_rows() -> list[str]:
    return [line for line in DOC.read_text(encoding="utf-8").splitlines() if ROW.match(line)]


def row_name(line: str) -> str:
    match = ROW.match(line)
    assert match is not None
    return match.group(1)


@functools.cache
def def_line(module: str, name: str) -> int:
    tree = ast.parse((PKG / f"{module}.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node.lineno
    raise AssertionError(f"{module}.{name} not defined in {module}.py")


def test_row_line_refs_match_defs() -> None:
    stale: list[str] = []
    for line in doc_rows():
        mod, name = row_name(line).split(".", 1)
        ref = LINE_REF.search(line)
        if ref is None or ref.group(1) != mod or int(ref.group(2)) != def_line(mod, name):
            stale.append(row_name(line))
    assert not stale, f"stale ' — {{module}}.py:LINE' refs in {DOC.name}: {stale}"


def test_row_length_max_160() -> None:
    long_rows = [row_name(line) for line in doc_rows() if len(line) > ROW_MAX and row_name(line) not in TOKEN_HEAVY]
    assert not long_rows, f"rows over {ROW_MAX} chars (not token-heavy): {long_rows}"
