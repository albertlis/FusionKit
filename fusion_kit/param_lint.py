"""Static lint: user-parameter values read in Python at build time (BUILD-ONLY params).

A part that reads a parameter with sketch.mm("name"), eval_fresh(...) or itemByName("name").value freezes the
value into geometry at build. Editing that parameter in the Fusion dialog then does nothing until a rebuild
(docs/gotchas.md: parametric = live dialog edit).

Flagged reads print `BUILD_ONLY_PARAM <name> <file>:<line>`. Not blocking (no DFAM_FAIL).
Opt-out: `# param-lint: build-only <reason>` on the flagged line; the reason is required.
Pure: lint_source takes text. lint_entry reads the entry file and its parts/ package.

Sketch-quality rules share the same API and output shape `<RULE> <name> <file>:<line>`. Not blocking.
SKETCH_ARC: arc built in sketch code (addFillet, addByThreePoints, addByCenterStartSweep, fillet_corner) -> 3D fillet.
DIM_EXPR_MATH: dim(...) expression or `.expression =` string with arithmetic -> constraint instead.
FIX_POINT: `.isFixed = True` or fixed_poly(...) -> no fixed points.
THROUGH_GUESS (WARN): extrude op cut with literal distance, no through_all/to -> through-all or to-object.
Opt-out: `# sketch-lint: <RULE> <reason>` on the flagged line; the reason is required.
"""

import ast
import re
from dataclasses import dataclass
from pathlib import Path

_READ_FUNCS = frozenset({"mm", "eval_fresh"})  # sketch.mm / user_params.eval_fresh, bare or attribute call
_UNIT_WORDS = frozenset({"mm", "cm", "m", "in", "ft", "deg", "rad"})
_IDENT = re.compile(r"[A-Za-z_]\w*")
_OPT_OUT = re.compile(r"#\s*param-lint:\s*build-only\s+\S")
DYNAMIC = "<dynamic>"  # name when the argument is not a string literal
BUILD_ONLY = "BUILD_ONLY_PARAM"
_ARC_FUNCS = frozenset(
    {"addFillet", "addByThreePoints", "addByCenterStartSweep", "fillet_corner"}
)  # sketch arc builders
_FIX_FUNCS = frozenset({"fixed_poly"})  # helper pinning every point of a polygon
_ARITH = re.compile(r"[\w)\]]\s*[*/+\-]\s*[\w(.+\-]")  # operator with a left operand: leading minus is not arithmetic
_SCI = re.compile(r"\d[eE][-+]?\d")  # 1e-3 literal, not arithmetic


@dataclass(frozen=True)
class LintHit:
    name: str  # user parameter name(s) read, comma-joined; DYNAMIC when unknown
    file: str
    line: int
    rule: str = BUILD_ONLY  # BUILD_ONLY_PARAM or a sketch-quality rule name


def _func_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _names_in(node: ast.expr | None) -> str:
    """Parameter names read by a mm()/eval_fresh() argument: identifiers of a string literal minus unit words."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        names = [t for t in _IDENT.findall(node.value) if t not in _UNIT_WORDS]
        return ",".join(names) if names else DYNAMIC
    return DYNAMIC


def _hit_name(node: ast.AST) -> str | None:
    """Name read by node when node is a build-time parameter read, else None."""
    if isinstance(node, ast.Call) and _func_name(node) in _READ_FUNCS:
        arg_index = 1 if _func_name(node) == "eval_fresh" else 0  # eval_fresh(design, expr, units)
        return _names_in(node.args[arg_index] if len(node.args) > arg_index else None)
    if isinstance(node, ast.Attribute) and node.attr == "value" and isinstance(node.value, ast.Call):
        inner = node.value
        if _func_name(inner) == "itemByName":
            arg = inner.args[0] if inner.args else None
            return _names_in(arg) if isinstance(arg, ast.Constant) else DYNAMIC
    return None


def _is_literal(node: ast.expr | None) -> bool:
    """Number or string literal, optionally with a unary sign (distance -2)."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        node = node.operand
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float | str)
        and not isinstance(node.value, bool)
    )


def _arg(call: ast.Call, index: int, keyword: str) -> ast.expr | None:
    """Positional argument at index, else keyword argument named keyword, else None."""
    if len(call.args) > index:
        return call.args[index]
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return None


def _has_arith(text: str) -> bool:
    return _ARITH.search(_SCI.sub("0", text)) is not None


def _sketch_hits(node: ast.AST) -> list[tuple[str, str, int]]:
    """(rule, name, line) of sketch-quality findings rooted at node. Opt-out not applied."""
    hits: list[tuple[str, str, int]] = []
    if isinstance(node, ast.Call):
        fname = _func_name(node)
        if fname in _ARC_FUNCS:
            hits.append(("SKETCH_ARC", fname, node.lineno))
        if fname in _FIX_FUNCS:
            hits.append(("FIX_POINT", fname, node.lineno))
        if fname == "dim":
            expr = _arg(node, 3, "expr")  # dim(s, a, b, expr, orientation)
            if isinstance(expr, ast.Constant) and isinstance(expr.value, str) and _has_arith(expr.value):
                hits.append(("DIM_EXPR_MATH", expr.value, expr.lineno))
        if fname == "extrude":
            op = _arg(node, 3, "op")  # extrude(comp, prof, distance, op, ...)
            distance = _arg(node, 2, "distance")
            no_extent_kw = not any(kw.arg in ("through_all", "to") for kw in node.keywords)
            if isinstance(op, ast.Constant) and op.value == "cut" and _is_literal(distance) and no_extent_kw:
                hits.append(("THROUGH_GUESS", "extrude", node.lineno))
    elif isinstance(node, ast.Assign):
        value = node.value
        for target in node.targets:
            if not isinstance(target, ast.Attribute):
                continue
            if target.attr == "isFixed" and isinstance(value, ast.Constant) and value.value is True:
                hits.append(("FIX_POINT", "isFixed", node.lineno))
            if (
                target.attr == "expression"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and _has_arith(value.value)
            ):
                hits.append(("DIM_EXPR_MATH", value.value, value.lineno))
    return hits


def _node_hits(node: ast.AST) -> list[tuple[str, str, int]]:
    """(rule, name, line) of every finding rooted at node. Opt-out not applied."""
    name = _hit_name(node)
    if name is not None:
        return [(BUILD_ONLY, name, getattr(node, "lineno", 0))]
    return _sketch_hits(node)


def _opted_out(text: str, rule: str) -> bool:
    if rule == BUILD_ONLY:
        return _OPT_OUT.search(text) is not None
    return re.search(rf"#\s*sketch-lint:\s*{rule}\s+\S", text) is not None


def lint_source(source: str, filename: str) -> list[LintHit]:
    """Lint findings in one source text, opt-out lines removed (per rule). Sorted by line."""
    lines = source.splitlines()
    tree = ast.parse(source, filename=filename)
    hits: list[LintHit] = []
    for node in ast.walk(tree):
        for rule, name, line in _node_hits(node):
            text = lines[line - 1] if 0 < line <= len(lines) else ""
            if _opted_out(text, rule):
                continue
            hits.append(LintHit(name=name, file=filename, line=line, rule=rule))
    return sorted(hits, key=lambda h: h.line)


def lint_entry(entry_path: str) -> list[LintHit]:
    """lint_source over the entry file and every .py under <entry dir>/parts/ (sorted). Missing file -> OSError."""
    entry = Path(entry_path)
    files = [entry]
    parts = entry.parent / "parts"
    if parts.is_dir():
        files.extend(sorted(parts.rglob("*.py")))
    hits: list[LintHit] = []
    for path in files:
        hits.extend(lint_source(path.read_text(encoding="utf-8"), str(path)))
    return hits


def format_lint(hits: list[LintHit]) -> list[str]:
    """One line per hit: '<RULE> <name> <file>:<line>' (RULE = BUILD_ONLY_PARAM or a sketch rule). Pure."""
    return [f"{h.rule} {h.name} {h.file}:{h.line}" for h in hits]
