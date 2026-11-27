# dfam_bed

Helper card for `fusion_kit/dfam_bed.py`. Index: `docs/fusion_kit_api.md`.

| helper | signature | replaces raw adsk |
|---|---|---|
| dfam_bed.bed_axis | bed_axis(bed) — dfam_bed.py:26 |  |
| dfam_bed.bed_from_body | bed_from_body(body) — dfam_bed.py:36 | `attributes.itemByName` |
| dfam_bed.set_bed | set_bed(body, bed) — dfam_bed.py:48 | `attributes.add` |
| dfam_bed.bed_rotation_axes | bed_rotation_axes(bed) — dfam_bed.py:128 |  |
| dfam_bed.bed_to_minus_z_matrix | bed_to_minus_z_matrix(bed) — dfam_bed.py:161 | `Matrix3D.setWithCoordinateSystem`, `Vector3D.create` |
| dfam_bed.print_frame_body | print_frame_body(body) — dfam_bed.py:320 | `TemporaryBRepManager.get`, `copy`, `transform` |
| dfam_bed.print_frame_with_tags | print_frame_with_tags(body) — dfam_bed.py:274 | `TemporaryBRepManager.get`, `copy`, `transform`, `attributes.itemByName` |
| dfam_bed.rotate_point | rotate_point(p, axes) — dfam_bed.py:173 |  |
| dfam_bed.match_faces | match_faces(orig, copy, tol) — dfam_bed.py:220 |  |
| dfam_bed.add_bed_loop_skips | add_bed_loop_skips(body, centres) — writes dfam/bed_loop_skip (mm centres, merged) — dfam_bed.py:82 |  |
| dfam_bed.format_loop_centres | format_loop_centres(centres) — centres -> "x,y,z;..." attr value — dfam_bed.py:61 |  |
| dfam_bed.loop_centres_from_body | loop_centres_from_body(body) — dfam/bed_loop_skip -> centres (original frame mm) — dfam_bed.py:76 |  |
| dfam_bed.loop_tokens_at_centres | loop_tokens_at_centres(loops, centres) — tempIds of loops whose centre matches — dfam_bed.py:119 |  |
| dfam_bed.parse_loop_centres | parse_loop_centres(value) — attr value -> centres — dfam_bed.py:66 |  |
| dfam_bed.print_frame_centre_mm | print_frame_centre_mm(c_mm, bed, lo_mm, hi_mm) — original mm point -> print frame — dfam_bed.py:93 |  |
| dfam_bed.print_frame_loop_centres | print_frame_loop_centres(body, bed) — recorded centres -> print-frame centres — dfam_bed.py:110 |  |
