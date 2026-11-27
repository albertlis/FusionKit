"""Offline tests: bed -> print frame (dfam.bed_rotation_axes / bed_to_minus_z_matrix / print_frame_body), dfam message
tag, warp-groove seed mapping on non-z beds (features._relief_family / warp_relief_grooves).

No Fusion runtime: adsk stubs from conftest.py. TemporaryBRepManager and axis helpers are monkeypatched.
"""

import sys
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from fusion_kit import dfam, dfam_bed, dfam_body, features, features_axes, features_warp

_CORE: Any = sys.modules["adsk.core"]
_FUSION: Any = sys.modules["adsk.fusion"]
ALL_BEDS = ["-z", "+z", "-x", "+x", "-y", "+y"]


def _apply(cols: tuple[Any, Any, Any], v: Sequence[float]) -> list[float]:
    # R v = sum_k v[k] * (column k)
    return [sum(v[k] * cols[k][r] for k in range(3)) for r in range(3)]


@pytest.mark.parametrize("bed", ALL_BEDS)
def test_bed_normal_maps_to_minus_z(bed: str) -> None:
    axis, sign = dfam.bed_axis(bed)
    n = tuple(float(sign) if k == axis else 0.0 for k in range(3))
    out = _apply(dfam.bed_rotation_axes(bed), n)
    assert [round(c, 12) for c in out] == [0.0, 0.0, -1.0]


@pytest.mark.parametrize("bed", ALL_BEDS)
def test_rotation_is_proper(bed: str) -> None:
    cols = dfam.bed_rotation_axes(bed)
    for k in range(3):
        assert sum(c * c for c in cols[k]) == pytest.approx(1.0)  # unit columns
        for m in range(k + 1, 3):
            assert sum(a * b for a, b in zip(cols[k], cols[m])) == pytest.approx(0.0)  # orthogonal
    det = (
        cols[0][0] * (cols[1][1] * cols[2][2] - cols[2][1] * cols[1][2])
        - cols[1][0] * (cols[0][1] * cols[2][2] - cols[2][1] * cols[0][2])
        + cols[2][0] * (cols[0][1] * cols[1][2] - cols[1][1] * cols[0][2])
    )
    assert det == pytest.approx(1.0)


def test_minus_z_bed_is_identity() -> None:
    assert dfam.bed_rotation_axes("-z") == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def test_rotation_rejects_unknown_bed() -> None:
    with pytest.raises(ValueError, match="bed"):
        dfam.bed_rotation_axes("+q")


def _record_frames(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, Any]]:
    # the adsk Matrix3D construction is one module-level function; patch it on our own module (no adsk stub edits)
    calls: list[tuple[Any, Any]] = []

    def fake(origin: Any, axes: Any) -> str:
        calls.append((origin, axes))
        return f"M{len(calls)}"

    monkeypatch.setattr(dfam_bed, "_frame_matrix", fake)
    return calls


def test_matrix_uses_rotation_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_frames(monkeypatch)
    assert dfam.bed_to_minus_z_matrix("+x") == "M1"
    assert calls == [((0.0, 0.0, 0.0), dfam.bed_rotation_axes("+x"))]


def _tmp_body(min_z: float) -> Any:
    tmp: Any = MagicMock(name="tmp_body")
    tmp.boundingBox.minPoint.z = min_z
    return tmp


def test_print_frame_minus_z_is_same_body(monkeypatch: pytest.MonkeyPatch) -> None:
    body: Any = MagicMock()
    body.attributes.itemByName.return_value = None
    mgr_get = MagicMock(name="get")
    monkeypatch.setattr(_FUSION.TemporaryBRepManager, "get", mgr_get)
    assert dfam.print_frame_body(body) is body
    mgr_get.assert_not_called()


def test_print_frame_plus_x_copies_rotates_shifts(monkeypatch: pytest.MonkeyPatch) -> None:
    body: Any = MagicMock()
    body.attributes.itemByName.return_value = SimpleNamespace(value="+x")
    tmp = _tmp_body(min_z=2.0)  # after rotation the body's min z is 2.0 mm-equivalent (cm units in Fusion)
    mgr: Any = MagicMock(name="mgr")
    mgr.copy.return_value = tmp
    monkeypatch.setattr(_FUSION, "TemporaryBRepManager", SimpleNamespace(get=lambda: mgr))
    calls = _record_frames(monkeypatch)

    out = dfam.print_frame_body(body)

    assert out is tmp
    mgr.copy.assert_called_once_with(body)
    assert mgr.transform.call_count == 2
    assert mgr.transform.call_args_list[0].args == (tmp, "M1")  # rotation for bed +x
    assert mgr.transform.call_args_list[1].args == (tmp, "M2")  # shift min z 2.0 -> 0
    assert calls[0] == ((0.0, 0.0, 0.0), dfam.bed_rotation_axes("+x"))
    assert calls[1] == ((0.0, 0.0, -2.0), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))


def test_dfam_check_tags_messages_on_non_z_bed(monkeypatch: pytest.MonkeyPatch) -> None:
    body: Any = MagicMock()
    body.attributes.itemByName.side_effect = lambda grp, name: SimpleNamespace(value="+x") if name == "bed" else None
    frame_body: Any = object()  # stands in for the print-frame copy
    facts_arg: list[Any] = []

    def fake_facts(b: Any, tags: Any = None, bed_loop_centres: Any = None) -> tuple[str, list[Any], list[Any]]:
        facts_arg.append(b)
        return ("fact", [], [])

    monkeypatch.setattr(dfam_body, "print_frame_with_tags", lambda b: (frame_body, {}, []))
    monkeypatch.setattr(dfam_body, "facts_from_body", fake_facts)
    monkeypatch.setattr(
        dfam_body, "check_all", lambda fact, holes, edges, skip: [dfam.Finding("bed_chamfer", "FAIL", "e")]
    )
    monkeypatch.setattr(dfam_body, "skip_from_body", lambda b: {})
    out = dfam.dfam_check(body)
    assert facts_arg == [frame_body]  # facts read from the print-frame body
    assert [f.msg for f in out] == ["e [print frame, bed +x]"]


def test_dfam_check_plain_z_bed_untagged(monkeypatch: pytest.MonkeyPatch) -> None:
    body: Any = MagicMock()
    body.attributes.itemByName.return_value = None
    monkeypatch.setattr(dfam_body, "facts_from_body", lambda b, tags=None, bed_loop_centres=None: ("fact", [], []))
    monkeypatch.setattr(
        dfam_body, "check_all", lambda fact, holes, edges, skip: [dfam.Finding("bed_chamfer", "FAIL", "e")]
    )
    monkeypatch.setattr(dfam_body, "skip_from_body", lambda b: {})
    assert [f.msg for f in dfam.dfam_check(body)] == ["e"]


def test_seed_rect_plus_x_passes_model_points(monkeypatch: pytest.MonkeyPatch) -> None:
    # bed '+x' (bed plane x = 7 mm): corners are model points (7, y, z); fix_point gets the same model point
    lines = [MagicMock(name=f"l{i}") for i in range(4)]
    rect_args: list[tuple[Any, Any]] = []

    def fake_rect(s: Any, a: Any, b: Any) -> list[MagicMock]:
        rect_args.append((a, b))
        return lines

    fix = MagicMock(name="fix_point")
    monkeypatch.setattr(features_warp, "rect", fake_rect)
    monkeypatch.setattr(features_warp, "fix_point", fix)
    features._seed_rect(MagicMock(name="sketch"), 19.6, -1.0, 20.4, 36.0, bed_dir="+x", bed_mm=7.0)
    assert rect_args == [((7.0, 19.6, -1.0), (7.0, 20.4, 36.0))]
    near, far = fix.call_args_list
    assert near.args[2][0] == "7.000000 mm"
    assert [float(v.split()[0]) for v in near.args[2][1:]] == pytest.approx([19.6, -1.0])
    assert [float(v.split()[0]) for v in far.args[2][1:]] == pytest.approx([20.4, 36.0])


# ---------------------------------------------------------------------------
# _literal_grooves on bed '+x': seed corners in sketch coords, sign-flipped sketch x
# ---------------------------------------------------------------------------
def _plate_body() -> Any:
    # cm: x 0..0.7 (7 mm, thin), y 0..10 (100 mm span), z 0..3.5 (35 mm span); bed = X-max face
    return SimpleNamespace(
        boundingBox=SimpleNamespace(
            minPoint=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            maxPoint=SimpleNamespace(x=0.7, y=10.0, z=3.5),
        )
    )


def _literal_calls(monkeypatch: pytest.MonkeyPatch, along: Any) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
    """_literal_grooves on the plate body, bed '+x' (bed plane x = 7 mm): (seed rect args, kwargs) per groove rect."""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(features_warp, "_seed_rect", lambda sk, *c, **kw: calls.append((c, kw)))
    features_warp._literal_grooves(
        MagicMock(name="sketch"),
        _plate_body(),
        along,
        width_mm=0.8,
        pitch_mm=30.0,
        bed_dir="+x",
        bed_mm=7.0,
        width_expr=None,
        run_expr=None,
    )
    return calls


def test_literal_plus_x_maps_corners_to_sketch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _literal_calls(monkeypatch, "y")
    assert len(calls) == 3  # 100 mm span -> centres -30, 0, 30
    x0, y0, x1, y1 = calls[0][0]
    # bed '+x': in-plane model axes y (lower index, spacing: 19.6..20.4, centre 20 = 50 - 30) and z (run:
    # -1.0..36.0 mm with 1 mm overrun)
    assert (x0, x1) == pytest.approx((19.6, 20.4))
    assert (y0, y1) == pytest.approx((-1.0, 36.0))


def test_depth_text_sign_flips_cut() -> None:
    assert features_warp._depth_text(-1, 0.3, None) == "-0.300000 mm"


def test_literal_rejects_normal_axis() -> None:
    # family along x is normal to bed '+x': no rect, clear error before any Fusion call
    with pytest.raises(ValueError, match="normal"):
        features_warp._literal_grooves(
            MagicMock(),
            _plate_body(),
            "x",
            width_mm=0.8,
            pitch_mm=30.0,
            bed_dir="+x",
            bed_mm=7.0,
            width_expr=None,
            run_expr=None,
        )


def test_warp_relief_grooves_plus_x_plumbing(monkeypatch: pytest.MonkeyPatch) -> None:
    body: Any = _plate_body()
    body.attributes = MagicMock()
    body.attributes.itemByName.return_value = None
    plane = MagicMock(name="plane")
    plane.geometry.normal = SimpleNamespace(x=1.0, y=0.0, z=0.0)  # yZ base normal +X: cut must go -X
    axis_calls: list[tuple[Any, ...]] = []

    def fake_axis_plane(comp: Any, axis: str, expr: str, value_mm: float, *, label: str) -> Any:
        axis_calls.append((axis, expr, value_mm, label))
        return plane

    monkeypatch.setattr(features_warp, "axis_plane", fake_axis_plane)
    monkeypatch.setattr(features_warp, "check_label", lambda label: None)
    lit_calls: list[tuple[str, str]] = []

    def fake_literal(s: Any, b: Any, along: str, **kw: Any) -> None:
        lit_calls.append((along, kw["bed_dir"]))

    monkeypatch.setattr(features_warp, "_literal_grooves", fake_literal)
    monkeypatch.setattr(features_warp, "new_sketch", lambda comp, plane, *, label: SimpleNamespace(label=label))
    monkeypatch.setattr(features_warp, "profiles", lambda sk: ["p"])
    cut_calls: list[tuple[Any, ...]] = []

    def fake_extrude(comp: Any, prof: Any, distance: str, **kw: Any) -> str:
        cut_calls.append((prof, distance, kw["op"], kw["label"]))
        return "cut"

    monkeypatch.setattr(features_warp, "extrude", fake_extrude)
    out = features.warp_relief_grooves(MagicMock(name="comp"), body, label="warp", bed="+x")
    assert axis_calls == [("x", "7.000000 mm", 7.0, "warp_bed")]
    assert lit_calls == [("y", "+x")]  # z span 35 mm <= 35: no groove, no rects
    assert cut_calls == [(["p"], "-0.600000 mm", "cut", "warp_y")]  # depth_sign -1 (plane normal +X), default depth 0.6
    assert out == ["cut"]
    body.attributes.add.assert_called_once_with("dfam", "bed", "+x")


def test_has_attr_false_on_temporary_face() -> None:
    face: Any = MagicMock(name="temp_face")
    face.attributes.itemByName.side_effect = RuntimeError("object does not support attributes")
    assert dfam._has_attr(face, "mating") is False


def test_dfam_check_warns_only_when_tagged_face_unmapped(monkeypatch: pytest.MonkeyPatch) -> None:
    body: Any = MagicMock()
    body.attributes.itemByName.side_effect = lambda grp, name: SimpleNamespace(value="+x") if name == "bed" else None
    tags_seen: list[Any] = []

    def fake_facts(b: Any, tags: Any = None, bed_loop_centres: Any = None) -> tuple[str, list[Any], list[Any]]:
        tags_seen.append(tags)
        return ("fact", [], [])

    monkeypatch.setattr(
        dfam_body, "print_frame_with_tags", lambda b: (object(), {"grip_fin": frozenset({7})}, ["grip_fin face 3"])
    )
    monkeypatch.setattr(dfam_body, "facts_from_body", fake_facts)
    monkeypatch.setattr(dfam_body, "check_all", lambda fact, holes, edges, skip: [])
    monkeypatch.setattr(dfam_body, "skip_from_body", lambda b: {})
    out = dfam.dfam_check(body)
    assert tags_seen == [{"grip_fin": frozenset({7})}]  # mapped tags reach facts_from_body
    assert [(f.rule, f.level) for f in out] == [("bed_frame", "WARN")]
    assert "grip_fin face 3" in out[0].msg


def test_dfam_check_no_bed_frame_warn_when_all_tags_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    body: Any = MagicMock()
    body.attributes.itemByName.side_effect = lambda grp, name: SimpleNamespace(value="+x") if name == "bed" else None
    monkeypatch.setattr(dfam_body, "print_frame_with_tags", lambda b: (object(), {"mating": frozenset({1})}, []))
    monkeypatch.setattr(dfam_body, "facts_from_body", lambda b, tags=None, bed_loop_centres=None: ("fact", [], []))
    monkeypatch.setattr(dfam_body, "check_all", lambda fact, holes, edges, skip: [])
    monkeypatch.setattr(dfam_body, "skip_from_body", lambda b: {})
    assert dfam.dfam_check(body) == []
