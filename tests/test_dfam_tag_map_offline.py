"""Offline tests: print-frame face tag mapping (dfam.rotate_point / dfam.match_faces). Pure floats, no Fusion runtime.

Tags (mating, grip_fin) are read from the original body; the print-frame copy takes no attributes, so each tagged
original face is mapped to its copy face by rotated vertex centroid + area (tol SIG_TOL_CM, cm).
"""

import pytest

from fusion_kit import dfam

ALL_BEDS = ["-z", "+z", "-x", "+x", "-y", "+y"]
SIGS = [
    ((0.0, 0.0, 0.0), 4.0),
    ((1.0, 0.2, 0.0), 2.5),
    ((0.3, 0.9, 0.7), 1.25),
    ((-0.5, 0.1, 0.4), 6.0),
]


def _outward(bed: str) -> dfam.Vec:
    axis, sign = dfam.bed_axis(bed)
    return (
        float(sign) if axis == 0 else 0.0,
        float(sign) if axis == 1 else 0.0,
        float(sign) if axis == 2 else 0.0,
    )


def test_rotate_point_identity_for_minus_z() -> None:
    assert dfam.rotate_point((1.0, 2.0, 3.0), dfam.bed_rotation_axes("-z")) == (1.0, 2.0, 3.0)


def test_rotate_point_maps_outward_bed_normal_to_minus_z() -> None:
    for bed in ALL_BEDS:
        out = dfam.rotate_point(_outward(bed), dfam.bed_rotation_axes(bed))
        assert [round(c, 12) + 0.0 for c in out] == [0.0, 0.0, -1.0], bed


def test_rotate_point_preserves_length() -> None:
    p = (0.3, -1.2, 2.5)
    for bed in ALL_BEDS:
        q = dfam.rotate_point(p, dfam.bed_rotation_axes(bed))
        assert sum(c * c for c in q) == pytest.approx(sum(c * c for c in p))


def test_match_faces_identity_order() -> None:
    assert dfam.match_faces(SIGS, list(SIGS)) == {0: 0, 1: 1, 2: 2, 3: 3}


def test_match_faces_permuted_copy_maps_by_signature() -> None:
    copy = list(reversed(SIGS))
    assert dfam.match_faces(SIGS, copy) == {0: 3, 1: 2, 2: 1, 3: 0}


def test_match_faces_after_rigid_rotation_on_copy() -> None:
    # copy = originals rotated by a bed matrix, order shuffled: mapping must still find every face
    for bed in ALL_BEDS:
        axes = dfam.bed_rotation_axes(bed)
        rotated = [(dfam.rotate_point(c, axes), a) for c, a in SIGS]
        order = [2, 0, 3, 1]
        copy = [rotated[i] for i in order]
        mapping = dfam.match_faces(
            rotated, copy
        )  # originals rotated into the print frame, as print_frame_with_tags does
        assert mapping == {i: order.index(i) for i in range(4)}, bed


def test_match_faces_tolerance_edges() -> None:
    within = [((0.0, 0.0, 5e-5), 4.0 + 5e-5)]  # centroid and area both inside 1e-4 cm
    assert dfam.match_faces(SIGS[:1], within) == {0: 0}
    outside = [((0.0, 0.0, 2e-4), 4.0)]  # centroid 2e-4 cm off
    assert dfam.match_faces(SIGS[:1], outside) == {}
    area_off = [((0.0, 0.0, 0.0), 4.0 + 2e-4)]  # area 2e-4 cm2 off
    assert dfam.match_faces(SIGS[:1], area_off) == {}


def test_match_faces_unmatched_face_absent() -> None:
    copy = [SIGS[1], SIGS[2], SIGS[3]]  # face 0 missing from copy
    assert dfam.match_faces(SIGS, copy) == {1: 0, 2: 1, 3: 2}


def test_match_faces_ambiguous_copy_not_mapped() -> None:
    # two original faces with the same signature, one copy face: the copy face matches 2 originals -> none mapped
    assert dfam.match_faces([SIGS[1], SIGS[1]], [SIGS[1]]) == {}
