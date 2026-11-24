"""Sketch probe: run named steps on one sketch, one PROBE line per step, optional rollback.

Per step: `PROBE <name> ok|FAIL [<err>] constraints=<n> dims=<n> fully=<bool>`.
FAIL adds `PROBE <name> SHARED <axis>=<v>mm pts=...` for points sharing an exact x or y with other points
(known VCS_SKETCH_OVER_CONSTRAINTS cause). Steps keep running after a FAIL.
"""

from collections.abc import Callable, Sequence

import adsk.fusion

ProbeStep = tuple[str, Callable[[adsk.fusion.Sketch], object]]


def _state(sketch: adsk.fusion.Sketch) -> str:
    return (
        f"constraints={sketch.geometricConstraints.count} dims={sketch.sketchDimensions.count}"
        f" fully={sketch.isFullyConstrained}"
    )


def _shared_lines(name: str, sketch: adsk.fusion.Sketch) -> list[str]:
    points = [(p.geometry.x, p.geometry.y) for p in sketch.sketchPoints]
    lines: list[str] = []
    for axis, index in (("x", 0), ("y", 1)):
        groups: dict[float, list[tuple[float, float]]] = {}
        for pt in points:
            groups.setdefault(pt[index], []).append(pt)
        for value, members in groups.items():
            if len(members) > 1:
                coords = " ".join(f"({x * 10:.3f},{y * 10:.3f})" for x, y in members)
                lines.append(f"PROBE {name} SHARED {axis}={value * 10:.3f}mm pts={coords}")
    return lines


def probe_sketch(sketch: adsk.fusion.Sketch, steps: Sequence[ProbeStep], rollback: bool = True) -> str:
    """Run each (name, step(sketch)) in order, printing one PROBE line per step; return the log.

    rollback=True raises RuntimeError("PROBE ROLLBACK" + log) so execute undoes every Fusion change.
    rollback=False returns the log text (offline tests).
    """
    log: list[str] = []
    for name, step in steps:
        try:
            step(sketch)
            log.append(f"PROBE {name} ok {_state(sketch)}")
        except Exception as err:
            log.append(f"PROBE {name} FAIL {err} {_state(sketch)}")
            log.extend(_shared_lines(name, sketch))
    text = "\n".join(log)
    print(text)
    if rollback:
        raise RuntimeError(f"PROBE ROLLBACK\n{text}")
    return text
