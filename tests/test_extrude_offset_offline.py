"""Offline tests: feature_quality rule sketch_extrude_is_offset (profile == earlier planar body face -> extrude from face).

No Fusion runtime: duck-typed SimpleNamespace fakes. Fusion lengths are cm, areas cm2; expectations in mm where stated.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from fusion_kit import feature_quality as fq

HIT = "profile == face -> extrude from face / offset face (no sketch)"
JOIN_OP, CUT_OP, INTERSECT_OP, NEW_BODY_OP = 0, 1, 2, 3  # adsk FeatureOperations (stub fusion.py:666-669)


def P(x: float, y: float, z: float) -> Any:
    return SimpleNamespace(x=x, y=y, z=z)


def _plane_geom(z_cm: float, normal: tuple[float, float, float] = (0.0, 0.0, 1.0), kind: str = "Plane") -> Any:
    return SimpleNamespace(objectType=f"adsk::core::{kind}", origin=P(0.0, 0.0, z_cm), normal=P(*normal))


def _face(token: str, z_cm: float, area_cm2: float, **kw: Any) -> Any:
    return SimpleNamespace(entityToken=token, geometry=_plane_geom(z_cm, **kw), area=area_cm2)


def _base(z_cm: float = 1.0, area_cm2: float = 4.0, token: str = "body") -> Any:
    body = SimpleNamespace(entityToken=token, faces=[_face(f"{token}_top", z_cm, area_cm2)])
    return SimpleNamespace(objectType="adsk::fusion::BaseFeature", isSuppressed=False, bodies=[body])


def _profile(z_cm: float = 1.0, area_cm2: float = 4.0, token: str = "p", otype: str = "Profile") -> Any:
    return SimpleNamespace(
        objectType=f"adsk::fusion::{otype}",
        entityToken=token,
        plane=SimpleNamespace(origin=P(0.0, 0.0, z_cm), normal=P(0.0, 0.0, 1.0)),
        profileLoops=[],
        areaProperties=lambda: SimpleNamespace(area=area_cm2),
    )


def _extrude(
    profiles: list[Any],
    own_tokens: tuple[str, ...] = (),
    otype: str = "ExtrudeFeature",
    operation: int = JOIN_OP,
    bodies: list[Any] | None = None,
) -> Any:
    return SimpleNamespace(
        objectType=f"adsk::fusion::{otype}",
        isSuppressed=False,
        operation=operation,
        profile=profiles,
        startFaces=[SimpleNamespace(entityToken=t) for t in own_tokens],
        endFaces=[],
        sideFaces=[],
        bodies=[SimpleNamespace(entityToken="body")] if bodies is None else bodies,
    )


def _hit(ext: Any, earlier: Any) -> str | None:
    return fq.rule_sketch_extrude_is_offset(ext, [("Base", earlier)])


# --- registration ---------------------------------------------------------------------------------


def test_rule_registered_in_rules_and_single_rules() -> None:
    assert "sketch_extrude_is_offset" in fq.RULES
    assert "sketch_extrude_is_offset" in dict(fq.SINGLE_RULES)


# --- hit / no-hit ---------------------------------------------------------------------------------


def test_hit_profile_equals_face() -> None:
    assert _hit(_extrude([_profile(1.0, 4.0)]), _base(1.0, 4.0)) == HIT


def test_hit_area_within_one_percent() -> None:
    assert _hit(_extrude([_profile(1.0, 4.03)]), _base(1.0, 4.0)) == HIT  # 0.75 %


def test_no_hit_area_off_by_five_percent() -> None:
    assert _hit(_extrude([_profile(1.0, 4.2)]), _base(1.0, 4.0)) is None  # 5 %


def test_no_hit_profile_not_coplanar() -> None:
    assert _hit(_extrude([_profile(1.2, 4.0)]), _base(1.0, 4.0)) is None  # 2 mm above the face plane


def test_no_hit_face_not_parallel() -> None:
    base = SimpleNamespace(
        objectType="adsk::fusion::BaseFeature",
        isSuppressed=False,
        bodies=[SimpleNamespace(entityToken="b", faces=[_face("side", 1.0, 4.0, normal=(1.0, 0.0, 0.0))])],
    )
    assert _hit(_extrude([_profile(1.0, 4.0)]), base) is None


def test_no_hit_face_not_planar() -> None:
    base = SimpleNamespace(
        objectType="adsk::fusion::BaseFeature",
        isSuppressed=False,
        bodies=[
            SimpleNamespace(
                entityToken="body",
                faces=[SimpleNamespace(entityToken="cyl", geometry=_plane_geom(1.0, kind="Cylinder"), area=4.0)],
            )
        ],
    )
    assert _hit(_extrude([_profile(1.0, 4.0)]), base) is None


def test_no_hit_two_profiles() -> None:
    ext = _extrude([_profile(1.0, 4.0, "p1"), _profile(1.0, 4.0, "p2")])
    assert _hit(ext, _base(1.0, 4.0)) is None


def test_no_hit_profile_is_brep_face() -> None:
    ext = _extrude([_profile(1.0, 4.0, otype="BRepFace")])
    assert _hit(ext, _base(1.0, 4.0)) is None


def test_no_hit_own_face_excluded() -> None:
    ext = _extrude([_profile(1.0, 4.0)], own_tokens=("body_top",))
    assert _hit(ext, _base(1.0, 4.0)) is None


def test_no_hit_not_an_extrude() -> None:
    ext = _extrude([_profile(1.0, 4.0)], otype="FilletFeature")
    assert _hit(ext, _base(1.0, 4.0)) is None


def test_no_hit_new_body_extrude_beside_coplanar_sibling() -> None:
    # shim_*_body FP: NewBody extrude in a comp where an earlier sibling body has a coplanar equal face -> skipped
    ext = _extrude([_profile(1.0, 4.0)], operation=NEW_BODY_OP, bodies=[SimpleNamespace(entityToken="shim")])
    assert _hit(ext, _base(1.0, 4.0)) is None


def test_no_hit_join_onto_other_body() -> None:
    # coplanar equal face lives on body "body"; this extrude modifies body "other" only
    ext = _extrude([_profile(1.0, 4.0)], operation=JOIN_OP, bodies=[SimpleNamespace(entityToken="other")])
    assert _hit(ext, _base(1.0, 4.0)) is None


@pytest.mark.parametrize("operation", [JOIN_OP, CUT_OP, INTERSECT_OP])
def test_hit_target_body_any_modify_op(operation: int) -> None:
    assert _hit(_extrude([_profile(1.0, 4.0)], operation=operation), _base(1.0, 4.0)) == HIT


def test_no_hit_extrude_without_bodies() -> None:
    assert _hit(_extrude([_profile(1.0, 4.0)], bodies=[]), _base(1.0, 4.0)) is None


@pytest.mark.parametrize("area_b", [3.99, 4.0, 4.04])
def test_tolerance_edges_one_percent(area_b: float) -> None:
    expected = HIT if abs(area_b - 4.0) <= 0.01 * 4.0 else None
    assert _hit(_extrude([_profile(1.0, area_b)]), _base(1.0, 4.0)) == expected
