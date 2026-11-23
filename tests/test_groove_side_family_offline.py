"""Offline: warp-groove family on a side face (secondary print position, bed=-x) exempts its rim edges.

Pure core only (no adsk). Faces in Fusion units (cm) like BRepFace.boundingBox; body box in mm.
Geometry (mm): body X 0..20, Y 0..70, Z 0..90. Family cut into the X=0 face (bed -x): two grooves running along Z,
0.8 mm wide, centres y=5.0 and y=34.94. Groove rim = X=0 edge of a groove wall: vertical, sharp, outside the bed band.
"""

from types import SimpleNamespace
from typing import Any

from fusion_kit.dfam import EdgeFact, check_edges, in_warp_frames, warp_frames

LO: tuple[float, float, float] = (0.0, 0.0, 0.0)
HI: tuple[float, float, float] = (20.0, 70.0, 90.0)


def _face(lo: tuple[float, float, float], hi: tuple[float, float, float]) -> Any:
    """Planar face, corners given in mm, stored in cm. No inner loops."""
    lo_cm = SimpleNamespace(x=lo[0] / 10, y=lo[1] / 10, z=lo[2] / 10)
    hi_cm = SimpleNamespace(x=hi[0] / 10, y=hi[1] / 10, z=hi[2] / 10)
    return SimpleNamespace(boundingBox=SimpleNamespace(minPoint=lo_cm, maxPoint=hi_cm), loops=[])


# X=0 face split into 3 strips by two Z-running grooves: gaps y 4.6..5.4 and 34.54..35.34 (0.8 mm)
SIDE_X0: dict[str, list[Any]] = {
    "-x": [
        _face((0.0, 0.0, 0.0), (0.0, 4.6, 90.0)),
        _face((0.0, 5.4, 0.0), (0.0, 34.54, 90.0)),
        _face((0.0, 35.34, 0.0), (0.0, 70.0, 90.0)),
    ]
}
# one groove only: lone gap is not a family
SIDE_ONE: dict[str, list[Any]] = {
    "-x": [_face((0.0, 0.0, 0.0), (0.0, 4.6, 90.0)), _face((0.0, 5.4, 0.0), (0.0, 70.0, 90.0))]
}


def _rim(in_groove: bool) -> EdgeFact:
    """Vertical sharp edge (groove rim / body corner), 10 mm room."""
    return EdgeFact(label="e", kind="vertical", treatment="sharp", size_mm=0.0, room_mm=10.0, in_warp_groove=in_groove)


def test_side_family_rim_edge_not_vertical_corner() -> None:
    # reported edge: edge@(0.0,35.3,77.2) on the groove wall at y=35.34, bed -x
    frames = warp_frames(LO, HI, SIDE_X0)
    flag = in_warp_frames((0.0, 35.3, 77.2), frames)
    assert flag is True
    assert check_edges([_rim(flag)]) == []


def test_side_family_rim_edge_other_groove_exempt() -> None:
    frames = warp_frames(LO, HI, SIDE_X0)
    assert in_warp_frames((0.0, 4.6, 45.0), frames) is True  # rim of groove y 4.6..5.4
    assert in_warp_frames((0.0, 5.4, 16.7), frames) is True  # other wall of same groove
    assert check_edges([_rim(in_warp_frames((0.0, 5.4, 16.7), frames))]) == []


def test_side_family_body_corner_still_fails() -> None:
    # outer corner of the X=0 face, away from any groove: still FAIL
    frames = warp_frames(LO, HI, SIDE_X0)
    flag = in_warp_frames((0.0, 60.0, 45.0), frames)
    assert flag is False
    assert [f.rule for f in check_edges([_rim(flag)])] == ["vertical_corner"]


def test_side_family_band_height_not_exempt() -> None:
    # same y/z as a groove rim but 10 mm inside the body (x = 10): outside the bed band
    frames = warp_frames(LO, HI, SIDE_X0)
    assert in_warp_frames((10.0, 35.3, 77.2), frames) is False


def test_lone_side_groove_not_exempt() -> None:
    frames = warp_frames(LO, HI, SIDE_ONE)
    assert frames == []
    assert in_warp_frames((0.0, 5.0, 45.0), frames) is False


def test_no_side_faces_no_frames() -> None:
    assert warp_frames(LO, HI, {}) == []
