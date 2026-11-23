"""param_lint sketch-quality rules (SKETCH_ARC, DIM_EXPR_MATH, FIX_POINT, THROUGH_GUESS). Pure source strings."""

from fusion_kit.param_lint import format_lint, lint_source


def _rules(src: str) -> list[tuple[str, str, int]]:
    return [(h.rule, h.name, h.line) for h in lint_source(src, "p.py")]


# SKETCH_ARC
def test_lint_sketch_arc_add_fillet_hit() -> None:
    assert _rules("sk.sketchArcs.addFillet(a, b, c, r)\n") == [("SKETCH_ARC", "addFillet", 1)]


def test_lint_sketch_arc_three_points_and_sweep_hit() -> None:
    src = "arcs.addByThreePoints(p1, p2, p3)\narcs.addByCenterStartSweep(c, s, e)\n"
    assert _rules(src) == [("SKETCH_ARC", "addByThreePoints", 1), ("SKETCH_ARC", "addByCenterStartSweep", 2)]


def test_lint_sketch_arc_fillet_corner_bare_hit() -> None:
    assert _rules("fillet_corner(s, a, b, 2)\n") == [("SKETCH_ARC", "fillet_corner", 1)]


def test_lint_sketch_arc_line_no_hit() -> None:
    assert _rules("sk.sketchCurves.sketchLines.addByTwoPoints(a, b)\n") == []


def test_lint_sketch_arc_opt_out_with_reason() -> None:
    src = "arcs.addByThreePoints(p1, p2, p3)  # sketch-lint: SKETCH_ARC loft guide profile, not a corner\n"
    assert _rules(src) == []


def test_lint_sketch_arc_opt_out_without_reason_still_flagged() -> None:
    assert _rules("arcs.addByThreePoints(p1, p2, p3)  # sketch-lint: SKETCH_ARC\n") == [
        ("SKETCH_ARC", "addByThreePoints", 1)
    ]


def test_lint_sketch_arc_opt_out_other_rule_does_not_suppress() -> None:
    src = "arcs.addByThreePoints(p1, p2, p3)  # sketch-lint: FIX_POINT wrong rule name\n"
    assert _rules(src) == [("SKETCH_ARC", "addByThreePoints", 1)]


# DIM_EXPR_MATH
def test_lint_dim_expr_math_positional_hit() -> None:
    assert _rules('sketch.dim(s, a, b, "pitch*2")\n') == [("DIM_EXPR_MATH", "pitch*2", 1)]


def test_lint_dim_expr_math_keyword_hit() -> None:
    assert _rules('sketch.dim(s, a, b, expr="w/2-t")\n') == [("DIM_EXPR_MATH", "w/2-t", 1)]


def test_lint_dim_expr_math_assign_expression_hit() -> None:
    assert _rules('d.parameter.expression = "pitch * 2"\n') == [("DIM_EXPR_MATH", "pitch * 2", 1)]


def test_lint_dim_expr_math_leading_minus_no_hit() -> None:
    assert _rules('sketch.dim(s, a, b, "-5 mm")\nd.parameter.expression = "-w"\n') == []


def test_lint_dim_expr_math_plain_name_and_unit_no_hit() -> None:
    src = 'sketch.dim(s, a, b, "plate_length")\nd.parameter.expression = "2 mm"\nd.parameter.expression = "1e-3 mm"\n'
    assert _rules(src) == []


def test_lint_dim_expr_math_non_literal_no_hit() -> None:
    assert _rules("sketch.dim(s, a, b, expr_var)\n") == []


def test_lint_dim_expr_math_opt_out_with_reason() -> None:
    src = 'sketch.dim(s, a, b, "pitch*2")  # sketch-lint: DIM_EXPR_MATH spacing is a user-visible ratio\n'
    assert _rules(src) == []


# FIX_POINT
def test_lint_fix_point_is_fixed_true_hit() -> None:
    assert _rules("pt.isFixed = True\n") == [("FIX_POINT", "isFixed", 1)]


def test_lint_fix_point_is_fixed_false_no_hit() -> None:
    assert _rules("pt.isFixed = False\n") == []


def test_lint_fix_point_fixed_poly_hit() -> None:
    assert _rules("fixed_poly(s, pts)\n") == [("FIX_POINT", "fixed_poly", 1)]


def test_lint_fix_point_opt_out_with_reason() -> None:
    src = "pt.isFixed = True  # sketch-lint: FIX_POINT datum origin, nothing can move it\n"
    assert _rules(src) == []


# THROUGH_GUESS (WARN)
def test_lint_through_guess_numeric_distance_hit() -> None:
    src = 'features.extrude(comp, prof, 5, op="cut", label="x")\n'
    assert _rules(src) == [("THROUGH_GUESS", "extrude", 1)]


def test_lint_through_guess_positional_op_string_distance_hit() -> None:
    src = 'features.extrude(comp, prof, "wall_t", "cut", label="x")\n'
    assert _rules(src) == [("THROUGH_GUESS", "extrude", 1)]


def test_lint_through_guess_negative_distance_hit() -> None:
    assert _rules('features.extrude(comp, prof, -2, op="cut", label="x")\n') == [("THROUGH_GUESS", "extrude", 1)]


def test_lint_through_guess_through_all_kw_no_hit() -> None:
    src = 'features.extrude(comp, prof, 5, op="cut", through_all=True, label="x")\n'
    assert _rules(src) == []


def test_lint_through_guess_to_kw_no_hit() -> None:
    src = 'features.extrude(comp, prof, 5, op="cut", to=face, label="x")\n'
    assert _rules(src) == []


def test_lint_through_guess_non_cut_or_variable_distance_no_hit() -> None:
    src = 'features.extrude(comp, prof, 5, op="new", label="x")\nfeatures.extrude(comp, prof, dist_var, op="cut", label="x")\n'
    assert _rules(src) == []


def test_lint_through_guess_opt_out_with_reason() -> None:
    src = 'features.extrude(comp, prof, 5, op="cut", label="x")  # sketch-lint: THROUGH_GUESS blind pocket by design\n'
    assert _rules(src) == []


# format
def test_lint_format_sketch_rule_line_exact() -> None:
    hits = lint_source("sk.addFillet(a, b, c, r)\npt.isFixed = True\n", "C:/p/x.py")
    assert format_lint(hits) == ["SKETCH_ARC addFillet C:/p/x.py:1", "FIX_POINT isFixed C:/p/x.py:2"]
