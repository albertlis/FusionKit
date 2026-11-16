"""Demo bracket (FusionKit example).

Plate with 2x2 mounting holes, upright wall with a slot, two gussets, bed chamfer and warp relief grooves.
Built only with FusionKit helpers.

Works in the ACTIVE document. Context: comma-separated step names, empty = all steps in order.
Run: fusion_kit.loader.run_part(<absolute path to this file>, "")
"""

from collections.abc import Callable

import adsk.core
import adsk.fusion
import parts.body as _body
import parts.params as _params

from fusion_kit.enums import PARAMETRIC_DESIGN
from fusion_kit.printability import check_body
from fusion_kit.report import inside, report
from fusion_kit.steps import run_steps
from fusion_kit.user_params import ensure_params


def _design() -> adsk.fusion.Design:
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        raise RuntimeError("open a new empty design first")
    return design


def run(_context: str) -> None:
    design = _design()
    root = design.rootComponent
    design.designType = PARAMETRIC_DESIGN
    ensure_params(design, _params.PARAMS, prune=False)
    for i in range(root.occurrences.count - 1, -1, -1):
        name = root.occurrences.item(i).component.name
        if name == _body.COMP_NAME or name.startswith(_body.COMP_NAME + " ("):
            root.occurrences.item(i).deleteMe()

    comp = _body.new_part(root)
    state: dict[str, adsk.fusion.BRepBody] = {}

    def check() -> None:
        final = _body.only_body(comp)
        report(comp)
        # Groove at X = 45 (centre of 90 mm span, pitch 30 -> groove at 45) must be outside; plate beside it inside.
        print("probe groove centre (45, 20, 0.15) inside:", inside(final, 45.0, 20.0, 0.15))
        print("probe plate beside groove (50, 20, 0.15) inside:", inside(final, 50.0, 20.0, 0.15))
        print("check_body min_angle 45 (print orientation = identity):", check_body(final, min_angle_deg=45.0))

    steps: dict[str, Callable[[], object]] = {
        "plate": lambda: state.update(body=_body.plate(comp)),
        "wall": lambda: state.update(body=_body.wall(comp, state["body"])),
        "gussets": lambda: state.update(body=_body.gussets(comp, state["body"])),
        "slot": lambda: state.update(body=_body.slot(comp, state["body"])),
        "bed_chamfer": lambda: state.update(body=_body.bed_chamfer(comp, state["body"])),
        "grooves": lambda: _body.warp_grooves(comp, state["body"]),
        "holes": lambda: _body.mounting_holes(comp),
        "check": check,
    }
    timings = run_steps(steps, _context or ",".join(steps))
    print("step seconds:", timings)
