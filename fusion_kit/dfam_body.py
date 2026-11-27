"""DfAM body entry: dfam_check, dfam_skip grammar (parse/format) and body attribute read/write."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from fusion_kit.dfam_bed import bed_from_body, print_frame_loop_centres, print_frame_with_tags
from fusion_kit.dfam_facts import _grip_fin_section, facts_from_body
from fusion_kit.dfam_rules import check_all
from fusion_kit.dfam_types import DFAM_GROUP, SKIP_ATTR, Finding

if TYPE_CHECKING:  # annotations only; runtime adsk import stays lazy inside functions
    import adsk.core
    import adsk.fusion


def parse_skip(value: str) -> dict[str, str]:
    """Parse dfam_skip attr '<rule>:<reason>[;<rule>:<reason>...]'. Empty entries ignored.

    Raises ValueError on missing rule/reason or a rule given twice. Reason may contain ':'.
    """
    out: dict[str, str] = {}
    for part in value.split(";"):
        part = part.strip()
        if not part:
            continue
        rule, sep, reason = part.partition(":")
        rule, reason = rule.strip(), reason.strip()
        if not sep or not rule or not reason:
            raise ValueError(f"dfam_skip entry needs <rule>:<reason>, got {part!r}")
        if rule in out:
            raise ValueError(f"dfam_skip rule {rule!r} given twice")
        out[rule] = reason
    return out


# Rule ids check_all can emit as findings (ADVISORY is never fed through check_all, so not skippable).
SKIPPABLE_RULES: frozenset[str] = frozenset(
    {
        "teardrop",
        "seam_notch",
        "first_layer",
        "counterbore_down",
        "relief_slot",
        "vertical_corner",
        "bed_chamfer",
        "joint_chamfer",
        "pin_base_chamfer",
        "warp_grooves",
        "min_wall",
        "petg_wall",
        "slender_fin",
        "cavity",
        "grip_fin_sliver",
    }
)


def format_skip(rules: dict[str, str]) -> str:
    """Build dfam_skip value '<rule>:<reason>; ...' from {rule: reason}. Pure.

    Raises ValueError: unknown rule; reason empty, padded with spaces, containing ';' or line breaks.
    ':' inside reason is fine (parse_skip splits on the first ':'). Result round-trips via parse_skip.
    """
    entries: list[str] = []
    for rule, reason in rules.items():
        if rule not in SKIPPABLE_RULES:
            raise ValueError(f"dfam skip: unknown rule {rule!r}; known: {sorted(SKIPPABLE_RULES)}")
        if not reason or reason != reason.strip():
            raise ValueError(f"dfam skip {rule!r}: reason must be non-empty, no surrounding spaces, got {reason!r}")
        if ";" in reason:
            raise ValueError(f"dfam skip {rule!r}: reason may not contain ';' (rule separator), got {reason!r}")
        if "\r" in reason or "\n" in reason:
            raise ValueError(f"dfam skip {rule!r}: reason may not contain line breaks, got {reason!r}")
        entries.append(f"{rule}:{reason}")
    value = "; ".join(entries)
    if parse_skip(value) != rules:
        raise ValueError(f"dfam skip does not round-trip: {value!r}")
    return value


def skip_from_body(body: adsk.fusion.BRepBody) -> dict[str, str]:
    """dfam_skip attribute ("dfam", "dfam_skip") -> {rule: reason}. Absent -> {}."""
    attr = body.attributes.itemByName(DFAM_GROUP, SKIP_ATTR)
    return {} if attr is None else parse_skip(attr.value)


def set_skip(body: adsk.fusion.BRepBody, rules: dict[str, str]) -> str:
    """Write dfam_skip attribute ("dfam", "dfam_skip") from {rule: reason} via format_skip.

    Attributes.add upserts (same group+name -> value replaced). Validation runs before any write. Returns value.
    """
    value = format_skip(rules)
    body.attributes.add(DFAM_GROUP, SKIP_ATTR, value)
    return value


def dfam_check(body: adsk.fusion.BRepBody, fail_as_warn: bool = False) -> list[Finding]:
    """All DfAM findings for one body. fail_as_warn=True: FAIL downgraded to WARN (rule names kept).

    Geometry is read in PRINT frame (print_frame_body, bed from dfam/bed); non-'-z' beds tag messages "[print frame ...]".
    """
    bed = bed_from_body(body)
    frame, tags, unmapped = print_frame_with_tags(body)
    body_fact, holes, edges = facts_from_body(frame, tags, bed_loop_centres=print_frame_loop_centres(body, bed))
    section = _grip_fin_section(body)  # attr lives on the ORIGINAL body, not the print-frame copy
    if section is not None:
        body_fact = replace(body_fact, grip_fin_section_mm=section)
    found = check_all(body_fact, holes, edges, skip_from_body(body))
    if bed != "-z":
        found = [replace(f, msg=f"{f.msg} [print frame, bed {bed}]") for f in found]
        if unmapped:
            found.append(
                Finding(
                    "bed_frame",
                    "WARN",
                    f"bed {bed}: tagged faces not mapped onto print-frame copy ({'; '.join(unmapped)}); their exemptions are not applied",
                )
            )
    if fail_as_warn:
        return [replace(f, level="WARN") if f.level == "FAIL" else f for f in found]
    return found
