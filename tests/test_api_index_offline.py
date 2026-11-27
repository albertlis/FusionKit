"""Helper index docs/fusion_kit_api.md + per-module cards docs/api/<module>.md must list every public fusion_kit function and no stale ones.

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
API = ROOT / "docs" / "api"
ROW = re.compile(r"^\| ([a-z_]+\.[A-Za-z_]\w*) \|")
HEADER = "| helper | signature | replaces raw adsk |"
CARD_REF = re.compile(r"docs/api/([a-z_]+)\.md \((\d+) helpers\)")
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


def cards() -> list[Path]:
    return sorted(API.glob("*.md"))


def rows_of(card: Path) -> list[str]:
    return [line for line in card.read_text(encoding="utf-8").splitlines() if ROW.match(line)]


def doc_rows() -> list[str]:
    return [line for card in cards() for line in rows_of(card)]


def row_name(line: str) -> str:
    match = ROW.match(line)
    assert match is not None
    return match.group(1)


def documented_rows() -> list[str]:
    return [row_name(line) for line in doc_rows()]


@functools.cache
def def_line(module: str, name: str) -> int:
    tree = ast.parse((PKG / f"{module}.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node.lineno
    raise AssertionError(f"{module}.{name} not defined in {module}.py")


def test_cards_exist() -> None:
    assert cards(), f"no module cards in {API}"


def test_every_card_has_hook_header() -> None:
    bad = [card.name for card in cards() if HEADER not in card.read_text(encoding="utf-8").splitlines()]
    assert not bad, f"cards without hook header line: {bad}"


def test_index_has_no_rows() -> None:
    rows = [line for line in DOC.read_text(encoding="utf-8").splitlines() if ROW.match(line)]
    assert not rows, f"helper rows belong in docs/api/<module>.md, not {DOC.name}: {len(rows)} found"


def test_row_belongs_to_its_module_card() -> None:
    wrong = [
        f"{card.name}:{row_name(line)}"
        for card in cards()
        for line in rows_of(card)
        if row_name(line).split(".", 1)[0] != card.stem
    ]
    assert not wrong, f"rows in the wrong module card: {wrong}"


def test_every_public_function_documented() -> None:
    missing = public_functions() - set(documented_rows())
    assert not missing, f"public fusion_kit functions missing from docs/api/: {sorted(missing)}"


def test_every_module_with_public_defs_has_card() -> None:
    modules = {name.split(".", 1)[0] for name in public_functions()}
    missing = modules - {card.stem for card in cards()}
    assert not missing, f"modules with public defs and no docs/api card: {sorted(missing)}"


def test_no_stale_rows() -> None:
    stale = set(documented_rows()) - public_functions()
    assert not stale, f"rows in docs/api/ with no public function: {sorted(stale)}"


def test_no_duplicate_rows() -> None:
    rows = documented_rows()
    dups = sorted({r for r in rows if rows.count(r) > 1})
    assert not dups, f"duplicate rows: {dups}"


def test_index_lists_every_card() -> None:
    listed = {name for name, _ in CARD_REF.findall(DOC.read_text(encoding="utf-8"))}
    on_disk = {card.stem for card in cards()}
    assert listed == on_disk, (
        f"index vs cards: missing from index {sorted(on_disk - listed)}, index-only {sorted(listed - on_disk)}"
    )


def test_index_helper_counts_match_cards() -> None:
    counts = {name: int(n) for name, n in CARD_REF.findall(DOC.read_text(encoding="utf-8"))}
    actual = {card.stem: len(rows_of(card)) for card in cards()}
    assert counts == actual, f"index helper counts != card rows: {counts} vs {actual}"


def test_row_line_refs_match_defs() -> None:
    stale: list[str] = []
    for line in doc_rows():
        mod, name = row_name(line).split(".", 1)
        ref = LINE_REF.search(line)
        if ref is None or ref.group(1) != mod or int(ref.group(2)) != def_line(mod, name):
            stale.append(row_name(line))
    assert not stale, f"stale ' — {{module}}.py:LINE' refs in docs/api/: {stale}"


def test_row_length_max_160() -> None:
    long_rows = [row_name(line) for line in doc_rows() if len(line) > ROW_MAX and row_name(line) not in TOKEN_HEAVY]
    assert not long_rows, f"rows over {ROW_MAX} chars (not token-heavy): {long_rows}"


def test_graph_card_third_column_has_no_raw_tokens() -> None:
    """Defect 3: graph helpers replace no raw adsk token. A backticked token in col3 makes the nudge hook suggest the
    helper for ordinary bodies / healthState code (TOKEN in hooks/fusion_kit_nudge.py)."""
    tokened = [
        f"{row_name(line)}: {cells[2]}"
        for line in rows_of(API / "graph.md")
        if "`" in (cells := [c.strip() for c in line.strip().strip("|").split("|")])[2]
    ]
    assert not tokened, f"graph card rows with raw tokens in col3: {tokened}"
