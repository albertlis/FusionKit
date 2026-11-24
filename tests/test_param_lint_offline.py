"""param_lint: build-only user-parameter reads found by ast. Pure source strings + tmp_path package layout."""

from pathlib import Path

from fusion_kit.param_lint import DYNAMIC, format_lint, lint_entry, lint_source


def _names(src: str) -> list[tuple[str, int]]:
    return [(h.name, h.line) for h in lint_source(src, "p.py")]


def test_sketch_mm_read_flagged_with_line() -> None:
    src = 'def run(_c):\n    s = 1\n    x = sketch.mm("plate_length") / 2\n'
    assert _names(src) == [("plate_length", 3)]


def test_bare_mm_and_eval_fresh_flagged() -> None:
    src = 'x = mm("gap")\ny = eval_fresh(design, "wall_t * 2", "mm")\n'
    assert _names(src) == [("gap", 1), ("wall_t", 2)]


def test_unit_words_dropped_names_kept() -> None:
    assert _names('v = mm("bottom_flange_width + 2 mm")\n') == [("bottom_flange_width", 1)]


def test_non_literal_argument_is_dynamic() -> None:
    assert _names("v = mm(name_var)\n") == [(DYNAMIC, 1)]


def test_dynamic_name_only_selection_reason_opts_out() -> None:
    # <dynamic> may be a ranged param -> only `selection` reason is accepted
    assert _names("v = mm(name_var)  # param-lint: build-only validation\n") == [(DYNAMIC, 1)]
    assert _names("v = mm(name_var)  # param-lint: build-only selection (feature tracks topology)\n") == []


def test_itemByName_value_flagged() -> None:
    assert _names('v = up.itemByName("end_stop_enabled").value\n') == [("end_stop_enabled", 1)]


def test_live_expression_uses_not_flagged() -> None:
    src = (
        'up.itemByName("gap").expression = "2 mm"\n'
        'sketch.dim(s, a, b, "plate_length", "horizontal")\n'
        'sketch.anchor(s, p, h="boss_edge_offset")\n'
        "mm_total = 3\n"
    )
    assert _names(src) == []


def test_opt_out_with_reason_suppresses() -> None:
    src = 'v = mm("print_orientation")  # param-lint: build-only frame chosen at build, rebuild on change\n'
    assert _names(src) == []


def test_opt_out_without_reason_still_flagged() -> None:
    src = 'v = mm("print_orientation")  # param-lint: build-only\n'
    assert _names(src) == [("print_orientation", 1)]


def test_format_lint_line_exact() -> None:
    hits = lint_source('v = mm("gap")\n', "C:/p/x.py")
    assert format_lint(hits) == ["BUILD_ONLY_PARAM gap C:/p/x.py:1"]


def test_lint_entry_scans_entry_and_parts_only(tmp_path: Path) -> None:
    pkg = tmp_path / "roller"
    (pkg / "parts").mkdir(parents=True)
    entry = pkg / "roller.py"
    entry.write_text('def run(_c):\n    mm("wall")\n', encoding="utf-8")
    (pkg / "parts" / "beam.py").write_text('x = mm("beam_len")\n', encoding="utf-8")
    (pkg / "other.py").write_text('x = mm("not_scanned")\n', encoding="utf-8")
    hits = lint_entry(str(entry))
    assert [(h.name, Path(h.file).name, h.line) for h in hits] == [
        ("wall", "roller.py", 2),
        ("beam_len", "beam.py", 1),
    ]


def test_ranged_param_opt_out_needs_selection_reason() -> None:
    ranged = frozenset({"top_wall_thickness"})
    validation = 'v = mm("top_wall_thickness")  # param-lint: build-only validation (raises at build)\n'
    assert [h.name for h in lint_source(validation, "p.py", ranged)] == ["top_wall_thickness"]
    selection = 'v = mm("top_wall_thickness")  # param-lint: build-only selection (feature tracks topology)\n'
    assert lint_source(selection, "p.py", ranged) == []
    assert lint_source(validation, "p.py") == []  # no sweep range: any reason still opts out


def test_lint_entry_reads_ranged_names_from_parts_params(tmp_path: Path) -> None:
    pkg = tmp_path / "holder"
    (pkg / "parts").mkdir(parents=True)
    entry = pkg / "holder.py"
    entry.write_text(
        'x = mm("wall")  # param-lint: build-only validation\ny = mm("gap")  # param-lint: build-only validation\n',
        encoding="utf-8",
    )
    (pkg / "parts" / "params.py").write_text(
        "PARAMS = {\n"
        '    "wall": ("4 mm", "mm", "w", SweepRange(min="1 mm", max="9 mm")),\n'
        '    "gap": ("2 mm", "mm", "g"),\n'
        "}\n",
        encoding="utf-8",
    )
    assert [(h.name, h.line) for h in lint_entry(str(entry))] == [("wall", 1)]


def test_seed_reason_opts_out_on_dynamic_and_ranged() -> None:
    seed = "# param-lint: build-only seed (float seeds sketch geometry, dims snap it)"
    assert _names(f"v = mm(name_var)  {seed}\n") == []
    ranged = frozenset({"top_wall_thickness"})
    assert lint_source(f'v = mm("top_wall_thickness")  {seed}\n', "p.py", ranged) == []


def test_reason_word_tolerates_punctuation_prefix() -> None:
    ranged = frozenset({"top_wall_thickness"})
    paren = 'v = mm("top_wall_thickness")  # param-lint: build-only (selection) edge pick\n'
    assert lint_source(paren, "p.py", ranged) == []
    dash = 'v = mm("top_wall_thickness")  # param-lint: build-only - seed float start value\n'
    assert lint_source(dash, "p.py", ranged) == []
    paren_validation = 'v = mm("top_wall_thickness")  # param-lint: build-only (validation)\n'
    assert [h.name for h in lint_source(paren_validation, "p.py", ranged)] == ["top_wall_thickness"]


def test_list_spec_with_sweep_range_counted_as_ranged(tmp_path: Path) -> None:
    pkg = tmp_path / "bracket"
    (pkg / "parts").mkdir(parents=True)
    entry = pkg / "bracket.py"
    entry.write_text('x = mm("wall")  # param-lint: build-only validation\n', encoding="utf-8")
    (pkg / "parts" / "params.py").write_text(
        'PARAMS = {\n    "wall": ["4 mm", "mm", "w", SweepRange(min="1 mm", max="9 mm")],\n}\n',
        encoding="utf-8",
    )
    assert [(h.name, h.line) for h in lint_entry(str(entry))] == [("wall", 1)]
