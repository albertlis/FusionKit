"""Pure build graph: node model, rule flags, SKQ_WARN attachment, text render, diff, JSON round-trip.

Stdlib only at import time. collect / run need the Fusion runtime: adsk and fusion_kit.timeline load inside them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import adsk.fusion

LINE_MAX = 160
GRAPH_LINE_CAP = 30  # write_graph summary: detail lines after the GRAPH line; overflow -> '… +N more'
_NDIGITS = 4
_UNNAMED = re.compile(r"[A-Z][A-Za-z]+\d+")
_AUTO_PARAM = re.compile(r"d\d+")  # Fusion auto id of a model parameter: renumbers on rebuild, never a diff key
_HARDCODED = re.compile(r"-?[\d.]+\s*(?:mm|cm|deg)?")
_ZERO_ANGLE = re.compile(r"-?(?:0+(?:\.0*)?|\.0+)\s*(?:deg|rad)?")  # default taper angle Fusion adds to every extrude
_SKQ_PREFIXES = ("SKQ_WARN ", "SKQ_INFO ", "SKQ_FAIL ")
_CROSS_RULE = "dup_sketch_cross_comp"
# Fusion display units. Fusion stores lengths in cm and angles in radians whatever the display unit (gotchas.md).
LENGTH_UNITS = frozenset({"mm", "cm", "m", "in", "ft", "yd", "um", "mil"})
ANGLE_UNITS = frozenset({"deg", "rad"})
_CM_TO_MM = 10.0
# One model parameter: (name, mm / deg value or None when unknown, expression, Fusion unit; "" = unitless).
ParamRow = tuple[str, float | None, str, str]
# Features that never take a sketch as input (see _unused_sketch). Any other feature after a sketch may consume it.
_NO_SKETCH_INPUT = frozenset(
    {
        "sketch",
        "plane",
        "axis",
        "occurrence",
        "fillet",
        "chamfer",
        "shell",
        "draft",
        "mirror",
        "rectangularpattern",
        "circularpattern",
        "move",
        "scale",
        "combine",
    }
)


@dataclass
class GraphNode:
    """One timeline item. params: one ParamRow per model parameter the feature created."""

    idx: int
    kind: str
    label: str
    params: list[ParamRow]
    inputs: list[str]
    outputs: list[str]
    healthy: bool = True
    suppressed: bool = False
    message: str = ""
    src: str = ""
    flags: list[str] = field(default_factory=list)


def flag(nodes: list[GraphNode]) -> None:
    """Add rule flags to node.flags in place. Idempotent: a flag already present is not added again."""
    counts: dict[str, int] = {}
    for node in nodes:
        counts[node.label] = counts.get(node.label, 0) + 1
    for node in nodes:
        hits: list[str] = []
        if not node.healthy:
            hits.append("unhealthy")
        if node.suppressed:
            hits.append("suppressed")
        if counts[node.label] > 1:
            hits.append("dup_label")
        if _unused_sketch(node, nodes):
            hits.append("unused_sketch")
        if any(_is_hardcoded(name, expr, unit) for name, _value, expr, unit in node.params):
            hits.append("hardcoded")
        if _UNNAMED.fullmatch(node.label) is not None:
            hits.append("unnamed")
        for name in hits:
            if name not in node.flags:
                node.flags.append(name)


def attach_skq(nodes: list[GraphNode], lines: list[str]) -> None:
    """Attach flag skq:<rule> to nodes named in SKQ_WARN / SKQ_INFO / SKQ_FAIL lines. Unknown names ignored.

    Cross form 'SKQ_WARN dup_sketch_cross_comp: a, b same profile -> ...' attaches to every listed name.
    Idempotent.
    """
    by_label: dict[str, list[GraphNode]] = {}
    for node in nodes:
        by_label.setdefault(node.label, []).append(node)
    for line in lines:
        prefix = next((p for p in _SKQ_PREFIXES if line.startswith(p)), None)
        if prefix is None:
            continue
        rule, sep, tail = line[len(prefix) :].partition(": ")
        if not sep:
            continue
        if rule == _CROSS_RULE and " same profile" in tail:
            names = [part.strip() for part in tail.split(" same profile", 1)[0].split(",")]
        else:
            names = [tail.split(" ", 1)[0]]
        tag = f"skq:{rule}"
        for name in names:
            for node in by_label.get(name, []):
                if tag not in node.flags:
                    node.flags.append(tag)


def render(nodes: list[GraphNode], title: str) -> str:
    """Header, one line per node, then one '!! ' line grouping flags by name. Lines cut to LINE_MAX with '…'."""
    lines = [f"GRAPH {title}  items={len(nodes)}  flags={_flag_total(nodes)}"]
    lines.extend(_node_line(node) for node in nodes)
    summary = _flag_summary(nodes)
    if summary:
        lines.append(f"!! {summary}")
    return "\n".join(_cut(line) for line in lines)


def _diff_keys(nodes: list[GraphNode]) -> list[str]:
    """Match key per node: its label, or kind + occurrence for unnamed default names (Extrude7 renumbers on insert)."""
    unnamed_seen: dict[str, int] = {}
    keys: list[str] = []
    for node in nodes:
        if _UNNAMED.fullmatch(node.label) is None:
            keys.append(node.label)
        else:
            k = unnamed_seen.get(node.kind, 0)
            unnamed_seen[node.kind] = k + 1
            keys.append(f"{node.kind}#{k}")
    return keys


def diff(prev: list[GraphNode], cur: list[GraphNode]) -> list[str]:
    """Change lines matched by _diff_keys: '+ label', '- label', '~ label param old -> new', '~ label health ok -> fail'.

    Order: cur order (per node: param changes, then health), then removed nodes in prev order.
    Mm values compared rounded to 4 dp; a missing or None value shows as '?'.
    """
    prev_keys = _diff_keys(prev)
    cur_keys = _diff_keys(cur)
    prev_by: dict[str, list[GraphNode]] = {}
    for key, node in zip(prev_keys, prev):
        prev_by.setdefault(key, []).append(node)
    cur_seen: dict[str, int] = {}
    out: list[str] = []
    for key, node in zip(cur_keys, cur):
        k = cur_seen.get(key, 0)
        cur_seen[key] = k + 1
        olds = prev_by.get(key, [])
        if k >= len(olds):
            out.append(f"+ {node.label}")
            continue
        out.extend(_param_changes(olds[k], node))
        if olds[k].healthy != node.healthy:
            was = "ok" if olds[k].healthy else "fail"
            now = "ok" if node.healthy else "fail"
            out.append(f"~ {node.label} health {was} -> {now}")
    prev_seen: dict[str, int] = {}
    for key, node in zip(prev_keys, prev):
        k = prev_seen.get(key, 0)
        prev_seen[key] = k + 1
        if k >= cur_seen.get(key, 0):
            out.append(f"- {node.label}")
    return out


def to_json(nodes: list[GraphNode]) -> str:
    """JSON array of node dicts. from_json(to_json(x)) == x."""
    return json.dumps([asdict(node) for node in nodes])


def from_json(text: str) -> list[GraphNode]:
    """Inverse of to_json: params restored from JSON lists to tuples. Malformed or old-schema text raises."""
    out: list[GraphNode] = []
    for raw in json.loads(text):
        raw["params"] = [tuple(param) for param in raw["params"]]
        out.append(GraphNode(**raw))
    return out


def write_graph(nodes: list[GraphNode], directory: str, name: str, skq_lines: list[str], key: str = "") -> list[str]:
    """Flag nodes, attach SKQ lines, write fk_graph_<stem>.txt (render) and .json (to_json) into directory.

    stem = <safe name>_<8 hex of sha256(normcase(abspath(key)) or name)>: key = build identity (entry script path; backslash and slash spellings give the same stem), so two designs with
    the same or only non-ASCII names do not overwrite or diff against each other. An existing .json there is the
    previous build: its diff against nodes is listed. Returns summary lines:
    'GRAPH <txt path> items=N flags=M changed=K', then the '!!' flag line(s) and the diff lines, at most
    GRAPH_LINE_CAP of them, then '… +X more'. Missing directory raises (open fails; nothing is swallowed).

    Previous .json unreadable (truncated, garbage, other schema): deliberate degradation, not a swallow. The new files
    are still written, no diff is listed, and the summary carries 'GRAPH prev unreadable (<ExcType>) -> no diff'.
    Only json.JSONDecodeError, TypeError, KeyError, ValueError are caught, and only while loading that previous file.
    Files are written to <path>.tmp and os.replace'd, so an interrupted write never leaves a half-written .json.
    """
    flag(nodes)
    attach_skq(nodes, skq_lines)
    ident = os.path.normcase(os.path.abspath(key.replace("\\", "/"))) if key else name  # one spelling per path
    digest = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:8]
    stem = os.path.join(directory, f"fk_graph_{_safe_name(name)}_{digest}")
    txt_path, json_path = stem + ".txt", stem + ".json"
    changes: list[str] = []
    unreadable: str | None = None
    if os.path.exists(json_path):
        try:
            with open(json_path, encoding="utf-8") as f:
                prev = from_json(f.read())
        except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
            unreadable = type(exc).__name__
        else:
            changes = diff(prev, nodes)
    text = render(nodes, name)
    try:  # json first: it is the diff base. On failure: leftover .tmp removed, exception re-raised (caller prints GRAPH failed)
        _write_atomic(json_path, to_json(nodes))
        _write_atomic(txt_path, text + "\n")
    finally:
        for leftover in (json_path + ".tmp", txt_path + ".tmp"):
            if os.path.exists(leftover):
                os.remove(leftover)
    out = [f"GRAPH {txt_path} items={len(nodes)} flags={_flag_total(nodes)} changed={len(changes)}"]
    if unreadable is not None:
        out.append(f"GRAPH prev unreadable ({unreadable}) -> no diff")
    detail = [line for line in text.splitlines() if line.startswith("!! ")] + changes
    out.extend(detail[:GRAPH_LINE_CAP])
    if len(detail) > GRAPH_LINE_CAP:
        out.append(f"… +{len(detail) - GRAPH_LINE_CAP} more")
    return out


def _write_atomic(path: str, text: str) -> None:
    """Write <path>.tmp in the same directory, then os.replace it over path."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _safe_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "design"


def _has_profiles(node: GraphNode) -> bool:
    return any(name == "profiles" and value is not None and value > 0 for name, value, _expr, _unit in node.params)


def _unused_sketch(node: GraphNode, nodes: list[GraphNode]) -> bool:
    """True when a sketch with profiles has no recorded consumer and no later feature could still consume it.

    A sketch consumed only by a suppressed feature counts as used (intended: suppression is temporary).

    Recorded consumers: `inputs` of other nodes. collect fills them from the `profile` of every feature (extrude,
    revolve, sweep ...) and from the loft sections (LoftFeature.loftSections). Sweep path is not read
    (SweepFeature.path raises in Fusion), so a sweep counts as unreadable. Conservative fallback: a later feature
    whose inputs are not read (hole, emboss, coil, split face, sweep, and any extrude / revolve / loft with no
    readable input) keeps the sketch unflagged. Later _NO_SKETCH_INPUT features and later extrude / revolve / loft
    with readable inputs do not.
    """
    if node.kind != "sketch" or not _has_profiles(node):
        return False
    if any(node.label in other.inputs for other in nodes if other is not node):
        return False
    return not any(
        other.idx > node.idx
        and other.kind not in _NO_SKETCH_INPUT
        and not (other.kind in ("extrude", "revolve", "loft") and other.inputs)
        for other in nodes
    )


def _is_hardcoded(name: str, expr: str, unit: str) -> bool:
    """A dimension typed as a literal. Unitless params (profiles, pattern counts) are counts, never hardcoded.

    A zero angle (unit deg / rad) is Fusion's default taper, not a typed dimension: not hardcoded.
    """
    if not unit or name.startswith("_") or _HARDCODED.fullmatch(expr) is None:
        return False
    return not (unit in ANGLE_UNITS and _ZERO_ANGLE.fullmatch(expr) is not None)


def _status(node: GraphNode) -> str:
    if not node.healthy:
        return "FAIL"
    if node.suppressed:
        return "SUPP"
    if node.flags:
        return "WARN"
    return "OK"


def _param_str(name: str, value: float | None, expr: str, unit: str) -> str:
    suffix = "mm" if unit in LENGTH_UNITS else "deg" if unit in ANGLE_UNITS else ""  # value is already mm / deg
    text = f"{name}={'?' if value is None else f'{round(value, _NDIGITS)}{suffix}'}"
    if expr.strip() and _HARDCODED.fullmatch(expr.strip()) is None:
        text += f"(={expr})"
    return text


def _node_line(node: GraphNode) -> str:
    params = " ".join(_param_str(name, value, expr, unit) for name, value, expr, unit in node.params)
    inputs = f"← {', '.join(node.inputs)}" if node.inputs else ""
    outputs = f"→ {', '.join(node.outputs)}" if node.outputs else ""
    tokens = [f"#{node.idx}", node.kind, node.label, params, inputs, outputs, _status(node), node.src]
    if node.message:
        tokens.append(f'"{node.message}"')
    return " ".join(token for token in tokens if token)


def _flag_summary(nodes: list[GraphNode]) -> str:
    groups: dict[str, list[str]] = {}
    for node in nodes:
        for name in node.flags:
            groups.setdefault(name, []).append(node.label)
    return " | ".join(f"{name}: {', '.join(labels)}" for name, labels in groups.items())


def _cut(line: str) -> str:
    return line if len(line) <= LINE_MAX else line[: LINE_MAX - 1] + "…"


def _flag_total(nodes: list[GraphNode]) -> int:
    return sum(len(node.flags) for node in nodes)


def _param_values(node: GraphNode) -> dict[tuple[str, str], tuple[str, float | None]]:
    """Stable key -> (label, value) per param. User names key by name; auto ids (d<digits>) key by position p<i>."""
    out: dict[tuple[str, str], tuple[str, float | None]] = {}
    for i, (name, value, _expr, _unit) in enumerate(node.params):
        if _AUTO_PARAM.fullmatch(name) is None:
            out[("name", name)] = (name, value)
        else:
            out[("pos", str(i))] = (f"p{i}", value)
    return out


def _param_changes(old: GraphNode, new: GraphNode) -> list[str]:
    before = _param_values(old)
    after = _param_values(new)
    out: list[str] = []
    for key in dict.fromkeys([*after, *before]):
        label = after[key][0] if key in after else before[key][0]
        was = before[key][1] if key in before else None
        now = after[key][1] if key in after else None
        was = None if was is None else round(was, _NDIGITS)
        now = None if now is None else round(now, _NDIGITS)
        if was != now:
            out.append(f"~ {new.label} {label} {'?' if was is None else was} -> {'?' if now is None else now}")
    return out


def collect(design: adsk.fusion.Design) -> list[GraphNode]:
    """One GraphNode per non-group timeline item of design. Fusion runtime only (adsk imported inside)."""
    import adsk.fusion

    from fusion_kit.timeline import _loft_sketches, _parent_sketches, item_params, param_index, source_of

    tl = design.timeline
    index = param_index(design)  # one walk over the model parameters for the whole build
    nodes: list[GraphNode] = []
    for i in range(tl.count):
        it = tl.item(i)
        if it.isGroup:
            continue
        ent = it.entity
        if ent is None:  # broken reference: keep the row, nothing else readable
            nodes.append(
                GraphNode(
                    idx=i,
                    kind="unknown",
                    label=it.name,
                    params=[],
                    inputs=[],
                    outputs=[],
                    healthy=False,
                    suppressed=bool(it.isSuppressed),
                )
            )
            continue
        kind = ent.objectType.split("::")[-1].removesuffix("Feature").removeprefix("Construction").lower()
        feat = adsk.fusion.Feature.cast(ent)
        sk = adsk.fusion.Sketch.cast(ent)
        params: list[ParamRow] = []
        inputs: list[str] = []
        outputs: list[str] = []
        if feat is not None:
            params = item_params(design, feat, index)
            # profile of any feature (extrude, revolve, sweep ...) and loft sections; dedupe keeps order.
            # Sweep path NOT read: SweepFeature.path raises in Fusion (docs/gotchas.md), see _unused_sketch.
            inputs = list(dict.fromkeys(_parent_sketches(ent) + _loft_sketches(ent)))
            bodies = getattr(feat, "bodies", None)  # optional API: not every feature exposes bodies
            if bodies is not None:
                outputs = [bodies.item(j).name for j in range(bodies.count)]
        if sk is not None:
            params.append(("profiles", float(sk.profiles.count), str(sk.profiles.count), ""))
        plane = adsk.fusion.ConstructionPlane.cast(ent)
        axis = adsk.fusion.ConstructionAxis.cast(ent)
        tagged = next((e for e in (feat, sk, plane, axis) if e is not None), None)  # entity that carries the attribute
        tag = source_of(tagged) if tagged is not None else None
        nodes.append(
            GraphNode(
                idx=i,
                kind=kind,
                label=it.name,
                params=params,
                inputs=inputs,
                outputs=outputs,
                # suppressed items report SuppressedFeatureHealthState (measured: 3), not an error: not unhealthy
                healthy=bool(it.isSuppressed)
                or it.healthState == adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState,
                suppressed=bool(it.isSuppressed),
                message=getattr(it, "errorOrWarningMessage", ""),  # optional API, same reason as bodies
                src=tag or "",
            )
        )
    return nodes


def run(_context: str) -> None:
    """Entry for loader.run_part (read-only execute): print the active design's build graph with rule flags."""
    from fusion_kit.component import active_design

    design = active_design()
    nodes = collect(design)
    flag(nodes)
    print(render(nodes, design.rootComponent.name))
