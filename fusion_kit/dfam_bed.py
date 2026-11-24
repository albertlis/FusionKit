"""DfAM print bed: bed attribute, rotation to -Z, print-frame copy and face matching."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from fusion_kit.dfam_geom import _has_attr
from fusion_kit.dfam_limits import SIG_TOL_CM
from fusion_kit.dfam_types import BED_ATTR, BED_DIRS, DFAM_GROUP, GRIP_FIN_ATTR, MATING_ATTR, BedDir, Vec

if TYPE_CHECKING:  # annotations only; runtime adsk import stays lazy inside functions
    import adsk.core
    import adsk.fusion


def bed_axis(bed: str) -> tuple[int, int]:
    """Bed direction -> (axis index 0|1|2 for x|y|z, outward sign). '+x' = bed is the X-max face. Else ValueError.

    -> (axis index 0/1/2 for x/y/z, outward sign); ValueError unless bed in -z, +z, -x, +x, -y, +y
    """
    if bed not in BED_DIRS:
        raise ValueError(f"bed must be one of {BED_DIRS}, got {bed!r}")
    return "xyz".index(bed[1]), (1 if bed[0] == "+" else -1)


def bed_from_body(body: adsk.fusion.BRepBody) -> str:
    """Bed direction stored in attribute ("dfam", "bed"). Absent -> "-z".

    stored bed, absent = "-z"
    """
    attr = body.attributes.itemByName(DFAM_GROUP, BED_ATTR)
    if attr is None:
        return "-z"
    bed_axis(attr.value)  # validates stored value
    return attr.value


def set_bed(body: adsk.fusion.BRepBody, bed: BedDir) -> BedDir:
    """Write bed direction to attribute ("dfam", "bed") after bed_axis validation. Returns bed.

    validates via bed_axis, writes ("dfam", "bed")
    """
    bed_axis(bed)
    body.attributes.add(DFAM_GROUP, BED_ATTR, bed)
    return bed


def bed_rotation_axes(bed: str) -> tuple[Vec, Vec, Vec]:
    """Rotation R that puts the bed face on -Z, as images of body x, y, z (columns of R). Proper, det +1.

    Outward bed normal (bed_axis sign * e_axis) maps to (0, 0, -1). '-z' = identity. Pure.

    -> images of x, y, z under the rotation putting the bed face on -Z (proper, det +1); "-z" =
    identity. Pure
    """
    bed_axis(bed)  # validates
    table: dict[str, tuple[Vec, Vec, Vec]] = {
        "-z": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        "+z": ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0)),
        "+x": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),
        "-x": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)),
        "+y": ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
        "-y": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
    }
    return table[bed]


_IDENTITY_AXES: tuple[Vec, Vec, Vec] = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _frame_matrix(origin: Vec, axes: tuple[Vec, Vec, Vec]) -> adsk.core.Matrix3D:
    """Matrix3D for a frame: origin + images of x, y, z (Matrix3D.setWithCoordinateSystem). Only adsk call site here."""
    from adsk import core

    ex, ey, ez = (core.Vector3D.create(*c) for c in axes)
    m = core.Matrix3D.create()
    m.setWithCoordinateSystem(core.Point3D.create(*origin), ex, ey, ez)
    return m


def bed_to_minus_z_matrix(bed: str) -> adsk.core.Matrix3D:
    """Matrix3D (rotation only, origin 0) for TemporaryBRepManager.transform: bed face -> min Z (bed_rotation_axes).

    -> Matrix3D (rotation, origin 0) for TemporaryBRepManager.transform
    `Matrix3D.setWithCoordinateSystem`, `Vector3D.create` (via _frame_matrix)
    """
    return _frame_matrix((0.0, 0.0, 0.0), bed_rotation_axes(bed))


FaceSig = tuple[Vec, float]  # (vertex centroid cm, area cm2)


def rotate_point(p: Vec, axes: tuple[Vec, Vec, Vec]) -> Vec:
    """Image of p under the rotation whose columns are axes (images of body x, y, z), e.g. bed_rotation_axes(bed). Pure.

    -> image of point p under rotation with columns axes (bed_rotation_axes). Pure
    """
    return (
        p[0] * axes[0][0] + p[1] * axes[1][0] + p[2] * axes[2][0],
        p[0] * axes[0][1] + p[1] * axes[1][1] + p[2] * axes[2][1],
        p[0] * axes[0][2] + p[1] * axes[1][2] + p[2] * axes[2][2],
    )


def _sig_close(a: FaceSig, b: FaceSig, tol: float) -> bool:
    (ac, aa), (bc, ba) = a, b
    return abs(aa - ba) <= tol and all(abs(ac[k] - bc[k]) <= tol for k in range(3))


_CellKey = tuple[int, int, int, int]


def _sig_cell(s: FaceSig, width: float) -> _CellKey:
    (cx, cy, cz), area = s
    return (math.floor(cx / width), math.floor(cy / width), math.floor(cz / width), math.floor(area / width))


_CELL_NEIGHBOURS = [(a, b, c, d) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1) for d in (-1, 0, 1)]


def _cell_index(sigs: list[FaceSig], width: float) -> dict[_CellKey, list[int]]:
    out: dict[_CellKey, list[int]] = {}
    for j, s in enumerate(sigs):
        out.setdefault(_sig_cell(s, width), []).append(j)
    return out


def _close_to(s: FaceSig, sigs: list[FaceSig], index: dict[_CellKey, list[int]], width: float, tol: float) -> list[int]:
    """Ascending j with _sig_close(s, sigs[j], tol). Scans the 3^4 neighbour cells of s only (same predicate, exact)."""
    k = _sig_cell(s, width)
    hits = [
        j
        for d in _CELL_NEIGHBOURS
        for j in index.get((k[0] + d[0], k[1] + d[1], k[2] + d[2], k[3] + d[3]), [])
        if _sig_close(s, sigs[j], tol)
    ]
    return sorted(hits)


def match_faces(orig: list[FaceSig], copy: list[FaceSig], tol: float = SIG_TOL_CM) -> dict[int, int]:
    """Original face index -> copy face index, for faces whose signatures agree within tol. Pure.

    Same index wins when it matches (live: TemporaryBRepManager.copy keeps face order). Else the single copy face
    within tol. A copy face matched by more than one original face is not mapped (ambiguous). Missing key = not mapped.

    -> {orig index: copy index}; face signature = (vertex centroid cm, area cm2), equal within tol;
    same index preferred, unique unused match otherwise, ambiguous = unmapped. Pure
    """

    if tol <= 0:
        raise ValueError(f"match_faces: tol must be > 0, got {tol!r}")
    width = 2.0 * tol  # |delta| <= tol moves a value at most half a cell: every close pair sits in neighbouring cells
    copy_index = _cell_index(copy, width)
    orig_index = _cell_index(orig, width)

    def unique(j: int) -> bool:
        return len(_close_to(copy[j], orig, orig_index, width, tol)) == 1

    out: dict[int, int] = {}
    used: set[int] = set()
    for i, o in enumerate(orig):
        cand = [j for j in _close_to(o, copy, copy_index, width, tol) if j not in used]
        if i in cand and unique(i):
            j = i
        elif len(cand) == 1 and unique(cand[0]):
            j = cand[0]
        else:
            continue
        out[i] = j
        used.add(j)
    return out


def _face_sig(face: adsk.fusion.BRepFace) -> FaceSig:
    """(vertex centroid cm, area cm2). The vertex centroid commutes with rigid motion: compare original vs copy."""
    pts: dict[int, tuple[float, float, float]] = {}
    for e in face.edges:
        for v in (e.startVertex, e.endVertex):
            if v is not None:
                g = v.geometry
                pts[v.tempId] = (g.x, g.y, g.z)
    if not pts:  # no vertices (closed smooth face): point on face stands in
        p = face.pointOnFace
        pts[-1] = (p.x, p.y, p.z)
    n = len(pts)
    centre = (
        sum(p[0] for p in pts.values()) / n,
        sum(p[1] for p in pts.values()) / n,
        sum(p[2] for p in pts.values()) / n,
    )
    return centre, face.area


def print_frame_with_tags(
    body: adsk.fusion.BRepBody,
) -> tuple[adsk.fusion.BRepBody, dict[str, frozenset[int]] | None, list[str]]:
    """(print-frame body, tags, unmapped). bed '-z': (body, None, []) = tags read from the body itself.

    Other beds: TEMPORARY copy rotated by bed_to_minus_z_matrix, faces matched to the original by rotated signature
    (match_faces, before the z shift; shift moves both sides alike), then shifted so min Z = 0. tags: name ->
    tempId set on the copy for every mating / grip_fin tagged original face that mapped. unmapped: "<tag> face <i>".

    -> (print-frame body, tags, unmapped). tags = {"mating": frozenset, "grip_fin": frozenset} of
    COPY tempIds, from original tagged faces matched by match_faces; None for bed "-z". unmapped =
    ["<tag> face <i>"] for tagged faces that did not map `TemporaryBRepManager.get`, `copy`,
    `transform`, `attributes.itemByName` (via _has_attr)
    """
    from adsk import fusion

    bed = bed_from_body(body)
    if bed == "-z":
        return body, None, []
    mgr = fusion.TemporaryBRepManager.get()
    tmp = mgr.copy(body)
    mgr.transform(tmp, bed_to_minus_z_matrix(bed))
    rot = bed_rotation_axes(bed)
    orig_sigs: list[FaceSig] = []
    src = list(body.faces)
    for f in src:
        c, a = _face_sig(f)
        orig_sigs.append((rotate_point(c, rot), a))
    pair = match_faces(orig_sigs, [_face_sig(f) for f in tmp.faces])
    mgr.transform(tmp, _frame_matrix((0.0, 0.0, -tmp.boundingBox.minPoint.z), _IDENTITY_AXES))
    copy_faces = list(tmp.faces)
    tags: dict[str, frozenset[int]] = {}
    unmapped: list[str] = []
    for name in (MATING_ATTR, GRIP_FIN_ATTR):
        ids: set[int] = set()
        for i, f in enumerate(src):
            if not _has_attr(f, name):
                continue
            if i in pair:
                ids.add(copy_faces[pair[i]].tempId)
            else:
                unmapped.append(f"{name} face {i}")
        tags[name] = frozenset(ids)
    return tmp, tags, unmapped


def print_frame_body(body: adsk.fusion.BRepBody) -> adsk.fusion.BRepBody:
    """Body in PRINT frame: bed face on min Z (floor at z = 0). bed '-z' -> body itself, no copy.

    Other beds: TEMPORARY copy (see print_frame_with_tags). Original body untouched. Copy is not in the design; callers read
    dfam/bed, dfam_skip from the ORIGINAL.

    -> body itself for bed "-z"; else TEMPORARY copy rotated to bed -> -Z and shifted to min Z = 0
    (= print_frame_with_tags(body)[0]). Original untouched; attrs (dfam/bed, dfam_skip) must be read
    from the ORIGINAL
    """
    return print_frame_with_tags(body)[0]
