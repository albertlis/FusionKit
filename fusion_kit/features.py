"""Fusion 360 feature creation helpers.

docs/gotchas.md gotchas encoded here
--------------------------------
- extrude / revolve: participantBodies = list(comp.bRepBodies) ALWAYS set for non-new ops.
  Default cut/join hits bodies of ALL components otherwise.
- profile(): uses profileLoops.count, never max(area) — concentric-circle trap:
  max area picks the inner disc, not the outer annulus.
- All distance / angle arguments accept str expression OR Ex (expr.e used for createByString).
"""

import math
import re
from collections.abc import Callable, Iterable, Sequence
from itertools import pairwise
from typing import Literal, Protocol

import adsk.core
import adsk.fusion

from fusion_kit._vec import _cross, _unit_strict
from fusion_kit.dfam import BED_CHAMFER_ANGLE_DEG, DFAM_GROUP, BedDir, bed_axis, set_bed
from fusion_kit.enums import (
    IDENTICAL_PATTERN,
    OP_CUT,
    OP_INTERSECT,
    OP_JOIN,
    OP_NEW,
    OPTIMIZED_PATTERN,
    PATTERN_SPACING,
    POINT_INSIDE,
    POINT_ON,
    POSITIVE_EXTENT,
)
from fusion_kit.expr import Ex, as_str
from fusion_kit.features_axes import (
    _axis_by_planes,
    _body_coll,
    _plane_mm,
    _rotate_about_root_axis,
    axis_plane,
    axis_x_through,
    axis_y_through,
    axis_z_through,
    move_body,
    offset_plane,
    rotate_body,
    rotate_body_about_x,
    rotate_body_about_y,
)
from fusion_kit.features_bed import (
    _BED_FLAT_TOL_CM,
    _BED_MAX_NZ,
    _bed_dihedral_deg,
    _bed_loops,
    bed_chamfer,
)

# ruff: noqa: F401 (re-export facade, explicit names)
from fusion_kit.features_common import (
    _OPS,
    P3,
    CombineOp,
    Op,
    _axis_index,
    _delete_previous,
    _mm,
    _owned_by,
    _root_label,
    _vi,
    _xyz,
    collection,
    face_normal,
)
from fusion_kit.features_edges import (
    CONVEX_STATS,
    _coedge_dir,
    _in_face_dir,
    _in_inner_loop,
    _is_planar,
    _probe_convex,
    _signed,
    _split_probe,
    _topo_convex,
    _xy_hit,
    chamfer,
    chamfer_greedy,
    chamfer_groups,
    convex_edges,
    edges_at_xy,
    faces_by_normal,
    fillet,
    fillet_groups,
    planar_faces,
    select_edges,
)
from fusion_kit.features_extrude import (
    _within,
    axis_by_cylinder,
    extrude,
    plane_midplane,
    profile,
    profiles,
    revolve,
    shell,
    single_body,
)
from fusion_kit.features_pattern import (
    _CUT_COPLANAR_HINT,
    _count,
    _dedupe_tools,
    _has_cut,
    _reraise_if_cut,
    _Tokened,
    combine,
    mirror,
    mirror_bodies,
    pattern_circular,
    pattern_grid,
    pattern_rectangular,
)
from fusion_kit.features_warp import (
    _AXES,
    _GROOVE_MIN_SPAN_MM,
    _GROOVE_OVERRUN_MM,
    GROOVE_EDGE_MIN_MM,
    WARP_GROOVE_ATTR,
    _grooves_ok,
    _relief_family,
    _seed_rect,
    _spaced_centres,
    _tag_warp_groove,
    groove_positions,
    warp_relief_grooves,
)
from fusion_kit.sketch import fix_point, new_sketch, rect
from fusion_kit.timeline import check_label, set_label
