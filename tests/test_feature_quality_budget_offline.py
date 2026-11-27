"""Offline tests: feature_quality.check over a 308-entry fake timeline (the live budget overrun was at ~300 entries).

No Fusion runtime: SimpleNamespace fakes, same shape as test_finish_order_offline.py / test_extrude_offset_offline.py.
Wall-clock time is measured by the benchmark, not asserted here (timing asserts are flaky).
"""

from types import SimpleNamespace as NS

from fusion_kit import feature_quality as fq

N = 308
NORMALS = [(0, 0, 1), (0, 0, -1), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0)]
ATTRS = NS(itemByName=lambda group, name: None)


def _plane(o: tuple[float, float, float], n: tuple[float, float, float]) -> NS:
    return NS(objectType="adsk::core::Plane", origin=NS(x=o[0], y=o[1], z=o[2]), normal=NS(x=n[0], y=n[1], z=n[2]))


def _body(i: int) -> NS:
    faces = []
    for f in range(6):
        o = (0.1 * (i % 50) + 0.01 * f, 0.2 * (i // 50), 0.3 * f)
        faces.append(NS(entityToken=f"b{i}_f{f}", geometry=_plane(o, NORMALS[f]), area=1.0 + 0.1 * f))
    box = NS(minPoint=NS(x=-1.0, y=-1.0, z=-1.0), maxPoint=NS(x=5.0, y=5.0, z=5.0))
    return NS(entityToken=f"b{i}", name=f"b{i}", faces=faces, boundingBox=box)


def _extrude(i: int, body: NS, operation: int) -> NS:
    prof = NS(
        objectType="adsk::fusion::Profile",
        entityToken=f"prof{i}",
        plane=NS(origin=NS(x=0.0, y=0.0, z=0.1 * (i % 40)), normal=NS(x=0.0, y=0.0, z=1.0)),
        profileLoops=[],
        parentSketch=NS(geometricConstraints=[]),
        areaProperties=lambda: NS(area=1.0 + 0.01 * (i % 7)),
    )
    extent = NS(objectType="adsk::fusion::DistanceExtentDefinition", distance=NS(value=0.2 + 0.05 * (i % 4)))
    return NS(
        objectType="adsk::fusion::ExtrudeFeature",
        isSuppressed=False,
        operation=operation,
        profile=[prof],
        extentOne=extent,
        extentTwo=None,
        startFaces=[],
        endFaces=[],
        sideFaces=[],
        bodies=[body],
        attributes=ATTRS,
        isThinExtrude=False,
        edgeSets=[],
    )


def _finish(otype: str, body: NS, i: int) -> NS:
    if otype == "FilletFeature":
        es = NS(objectType="adsk::fusion::ConstantRadiusFilletEdgeSet", radius=NS(expression=f"r{i % 3}"))
    else:
        es = NS(objectType="adsk::fusion::EqualDistanceChamferEdgeSet", distance=NS(expression=f"c{i % 2}"))
    return NS(objectType=f"adsk::fusion::{otype}", isSuppressed=False, attributes=ATTRS, bodies=[body], edgeSets=[es])


def _cplane(i: int) -> NS:
    expr = "h/2" if i % 8 == 3 else f"d{i}"
    defn = NS(objectType="adsk::fusion::ConstructionPlaneOffsetDefinition", offset=NS(expression=expr))
    return NS(
        objectType="adsk::fusion::ConstructionPlane",
        isSuppressed=False,
        attributes=ATTRS,
        geometry=_plane((0.0, 0.0, 0.05 * i), (0.0, 0.0, 1.0)),
        parentComponent=NS(entityToken="comp"),
        definition=defn,
        bodies=[],
    )


def _design(n: int) -> NS:
    rows = []
    for i in range(n):
        kind = i % 5
        if kind in (0, 1):
            ent = _extrude(i, _body(i), 0 if kind == 0 else 1)
        elif kind == 2:
            ent = _finish("FilletFeature", _body(i), i)
        elif kind == 3:
            ent = _finish("ChamferFeature", _body(i), i)
        else:
            ent = _cplane(i)
        rows.append((f"f{i}", ent))
    return NS(timeline=NS(count=len(rows), item=lambda k: NS(name=rows[k][0], isGroup=False, entity=rows[k][1])))


def test_all_entries_checked_no_skip_line() -> None:
    lines = fq.check(_design(N), over_budget=lambda: False)
    assert not [line for line in lines if line.startswith("SKIP")]
    assert [line for line in lines if line.startswith("SKQ_WARN")]  # sanity: rules fire on this timeline


def test_budget_hit_reports_exact_unchecked_count() -> None:
    calls = {"n": 0}

    def over_after_300() -> bool:
        calls["n"] += 1
        return calls["n"] > 300  # polled once per entry: entries 0..299 checked, 300..307 not

    lines = fq.check(_design(N), over_budget=over_after_300)
    assert lines[-1] == "SKIP fq budget: 8 of 308 entries not checked"
