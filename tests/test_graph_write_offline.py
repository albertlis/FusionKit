"""graph.write_graph: writes fk_graph_<name>.txt / .json, diffs against the previous .json, caps the summary. Offline."""

import os
import re
from pathlib import Path

import pytest

from fusion_kit.graph import GraphNode, from_json, write_graph


def _nodes(width: float = 5.0) -> list[GraphNode]:
    return [
        GraphNode(
            idx=0, kind="sketch", label="base_sketch", params=[("profiles", 1.0, "1", "")], inputs=[], outputs=[]
        ),
        GraphNode(
            idx=1,
            kind="extrude",
            label="base",
            params=[("distance", width, "w", "mm")],
            inputs=["base_sketch"],
            outputs=["body"],
        ),
    ]


def test_writes_txt_and_json_and_first_call_has_no_diff(tmp_path: Path) -> None:
    summary = write_graph(_nodes(), str(tmp_path), "Frame", [])
    txt_path = next(tmp_path.glob("fk_graph_frame_*.txt"))
    txt = txt_path.read_text(encoding="utf-8")
    assert txt.splitlines()[0].startswith("GRAPH Frame  items=2  flags=")
    restored = from_json(next(tmp_path.glob("fk_graph_frame_*.json")).read_text(encoding="utf-8"))
    assert [node.label for node in restored] == ["base_sketch", "base"]
    assert summary[0].startswith(f"GRAPH {os.path.join(str(tmp_path), txt_path.name)} items=2 ")
    assert summary[0].endswith("changed=0")
    assert not [line for line in summary if line.startswith("~ ")]


def test_second_call_diffs_changed_param(tmp_path: Path) -> None:
    write_graph(_nodes(5.0), str(tmp_path), "Frame", [])
    summary = write_graph(_nodes(6.0), str(tmp_path), "Frame", [])
    assert summary[0].endswith("changed=1")
    assert "~ base distance 5.0 -> 6.0" in summary


def test_summary_capped_at_30_lines_with_more_marker(tmp_path: Path) -> None:
    params = [(f"p{k}", 1.0, "w", "mm") for k in range(40)]
    before = [GraphNode(idx=0, kind="extrude", label="base", params=params, inputs=[], outputs=[])]
    after = [
        GraphNode(
            idx=0,
            kind="extrude",
            label="base",
            params=[(name, 2.0, expr, unit) for name, _value, expr, unit in params],
            inputs=[],
            outputs=[],
        )
    ]
    write_graph(before, str(tmp_path), "Frame", [])
    summary = write_graph(after, str(tmp_path), "Frame", [])
    assert summary[0].endswith("changed=40")
    assert len(summary) == 32  # GRAPH line + 30 detail lines + '… +10 more'
    assert summary[1] == "~ base p0 1.0 -> 2.0"
    assert summary[31] == "… +10 more"


def test_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        write_graph(_nodes(), str(tmp_path / "missing"), "Frame", [])


def test_name_sanitised_to_lowercase_alnum_underscore(tmp_path: Path) -> None:
    write_graph(_nodes(), str(tmp_path), "My Frame-2!", [])
    names = sorted(p.name for p in tmp_path.iterdir())
    pattern = r"fk_graph_my_frame_2_[0-9a-f]{8}\.(json|txt)"
    assert len(names) == 2 and all(re.fullmatch(pattern, n) for n in names), names


def test_same_name_different_key_do_not_share_files(tmp_path: Path) -> None:
    write_graph(_nodes(5.0), str(tmp_path), "Frame", [], key="a/frame.py")
    summary = write_graph(_nodes(6.0), str(tmp_path), "Frame", [], key="b/frame.py")
    assert summary[0].endswith("changed=0"), summary  # not diffed against the other design's .json
    assert len(list(tmp_path.glob("fk_graph_*.json"))) == 2


def test_same_key_diffs_against_its_own_previous_build(tmp_path: Path) -> None:
    write_graph(_nodes(5.0), str(tmp_path), "Frame", [], key="a/frame.py")
    summary = write_graph(_nodes(6.0), str(tmp_path), "Frame", [], key="a/frame.py")
    assert summary[0].endswith("changed=1"), summary


def test_non_ascii_names_do_not_collide(tmp_path: Path) -> None:
    # Non-ASCII letters drop out of the safe name: "Żółć" and "ąęśćź" both give "design", "Żółw" and "Ęw" both "w".
    for name in ("Żółć", "ąęśćź", "Żółw", "Ęw"):
        write_graph(_nodes(5.0), str(tmp_path), name, [])
    assert len(list(tmp_path.iterdir())) == 8  # four designs, txt + json each
    summary = write_graph(_nodes(5.0), str(tmp_path), "Żółć", [])
    assert summary[0].endswith("changed=0"), summary


def test_skq_line_attached_and_listed_in_flag_line(tmp_path: Path) -> None:
    skq = ["SKQ_FAIL underconstrained: base_sketch not fully constrained"]
    summary = write_graph(_nodes(), str(tmp_path), "Frame", skq)
    assert any(line.startswith("!! ") and "skq:underconstrained: base_sketch" in line for line in summary)


@pytest.mark.parametrize(
    ("previous", "exc_name"),
    [
        ("", "JSONDecodeError"),  # truncated to 0 bytes
        ("{not json", "JSONDecodeError"),  # garbage
        ('[{"idx": 0}]', "KeyError"),  # old schema: required field missing
        (
            '[{"idx": 0, "kind": "x", "label": "b", "params": [], "inputs": [], "outputs": [], "legacy": 1}]',
            "TypeError",  # old schema: unknown field
        ),
    ],
)
def test_unreadable_previous_json_writes_new_files_and_says_so(tmp_path: Path, previous: str, exc_name: str) -> None:
    write_graph(_nodes(5.0), str(tmp_path), "Frame", [])
    json_path = next(tmp_path.glob("fk_graph_frame_*.json"))
    json_path.write_text(previous, encoding="utf-8")
    summary = write_graph(_nodes(6.0), str(tmp_path), "Frame", [])
    assert f"GRAPH prev unreadable ({exc_name}) -> no diff" in summary, summary
    assert summary[0].endswith("changed=0"), summary
    restored = from_json(json_path.read_text(encoding="utf-8"))
    assert [node.label for node in restored] == ["base_sketch", "base"]
    assert restored[1].params[0][1] == 6.0
    assert not list(tmp_path.glob("*.tmp"))


def test_key_spellings_share_one_stem(tmp_path: Path) -> None:
    """Defect 2: 'C:/p/f.py' and 'C:\\p\\f.py' are the same build; one stem, the second call diffs against the first."""
    write_graph(_nodes(5.0), str(tmp_path), "Frame", [], key="C:/p/f.py")
    summary = write_graph(_nodes(6.0), str(tmp_path), "Frame", [], key="C:\\p\\f.py")
    assert summary[0].endswith("changed=1"), summary
    assert len(list(tmp_path.glob("fk_graph_*.json"))) == 1


def test_write_failure_leaves_no_tmp_and_reraises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Defect 1: os.replace fails -> leftover .tmp removed, PermissionError re-raised (caller prints the line)."""

    def locked_replace(src: str, dst: str) -> None:
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(os, "replace", locked_replace)
    with pytest.raises(PermissionError):
        write_graph(_nodes(), str(tmp_path), "Frame", [])
    assert not list(tmp_path.glob("*.tmp"))
