# Demo bracket

Small FDM-printable bracket built with FusionKit: a 90 x 60 x 5 mm plate with four mounting holes, a 40 mm upright wall with a gable-roof slot, two triangular gussets, 3 mm filleted outer vertical corners, 0.5 mm chamfered slot corners, a bed chamfer and anti-warp relief grooves.

## What it shows

| Feature | Helpers |
|---|---|
| User parameters with expressions | `user_params.ensure_params` |
| Fully constrained sketch rectangle | `sketch.rect_xy_sketch`, `sketch.rect_two_corners` |
| Dimensioned, positioned circle | `sketch.circle`, `sketch.anchor` |
| Triangle with live dimensions | `sketch.param_poly` |
| Gable-roof slot profile (pointed roof, no flat ceiling) | `sketch.param_poly` |
| Extrude new / join / cut / symmetric | `features.extrude`, `features.profile` |
| Construction planes from expressions | `features.axis_plane` |
| Seed + mirror | `features.mirror_bodies`, `features.combine` |
| Vertical corner fillet r 3 mm (outer corners of plate and wall ends) | `features.fillet`, `features.select_edges`, `features.edges_at_xy` |
| Vertical corner chamfer 0.5 mm (wall slot corners) | `features.chamfer`, `features.select_edges`, `features.edges_at_xy` |
| Simple hole + two-direction pattern | `holes.hole_simple`, `features.pattern_grid` |
| Bed chamfer | `features.bed_chamfer` |
| Warp relief grooves | `features.warp_relief_grooves` |
| Self-check and printability | `report.report`, `report.inside`, `printability.check_body` |

Holes are cut last, because a join after a hole cut closes the hole.

## Features (timeline labels)

`plate` > `wall` (with `outer_corner_fillet`) > `gusset_sketch`, `gusset`, `gusset_mirror`, `join_gussets` > `slot_sketch` (gable roof) > `wall_slot` > `slot_corner_chamfer` > `bed_chamfer` > `warp_grooves` > `mounting_hole_grid`.

## Run

1. Open Fusion, enable the API server (Preferences > General > API), and create a new design document. Do not run it on a document you need to keep.
2. Execute through the Fusion MCP `execute` tool, with the absolute path to this file:

```python
import sys


def run(_context):
    sys.path.insert(0, r"<FusionKit root>")
    from fusion_kit.loader import run_part

    run_part(r"<FusionKit root>\examples\demo_bracket\demo_bracket.py", "")
```

Context is a comma-separated list of step names (`plate, wall, gussets, slot, bed_chamfer, grooves, holes, check`). Empty runs all steps.

Built with FusionKit (https://github.com/albertlis/FusionKit)
