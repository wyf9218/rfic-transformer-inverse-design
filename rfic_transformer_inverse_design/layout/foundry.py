"""Current-foundry layout rules shared by bridge and ground-stitch vias."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FoundryViaArrayRule:
    size_um: float
    spacing_um: float
    enclosure_um: float
    columns: int
    rows: int


def foundry_via_array_rule(via_number: int) -> FoundryViaArrayRule:
    """Return the audited TSMC65 via-array rule for one adjacent-metal step."""

    rules = {
        # Keep one manufacturing-grid step above the 0.13 um VIAx.S.2 minimum.
        5: FoundryViaArrayRule(0.10, 0.14, 0.04, 3, 3),
        6: FoundryViaArrayRule(0.10, 0.14, 0.04, 3, 3),
        7: FoundryViaArrayRule(0.36, 0.54, 0.08, 2, 2),
        # M9.EN.1 requires 0.30 um enclosure of VIA8 by M9.
        8: FoundryViaArrayRule(0.36, 0.54, 0.30, 2, 2),
        # RV.W.1.WB and AP.EN.1.WB require a 3 um RV with 1.5 um
        # enclosure. Keep one 5 nm manufacturing-grid step of margin.
        9: FoundryViaArrayRule(3.00, 3.00, 1.505, 1, 1),
    }
    try:
        return rules[int(via_number)]
    except KeyError as exc:
        raise ValueError(
            f"no current-foundry via rule is defined for via{via_number}"
        ) from exc


def foundry_via_array_rules_for_process(
    proc_info,
) -> tuple[tuple[int, FoundryViaArrayRule], ...]:
    """Map each foundry via rule to its GDS layer in the active process."""

    seen_layers: dict[int, FoundryViaArrayRule] = {}
    for via_number in range(5, 10):
        layer = proc_info.gds_layer_for_via_number(via_number)
        if layer is None:
            continue
        rule = foundry_via_array_rule(via_number)
        existing = seen_layers.get(int(layer))
        if existing is not None and existing != rule:
            raise ValueError(
                f"GDS layer {layer} maps to conflicting current-foundry via rules"
            )
        seen_layers[int(layer)] = rule
    return tuple(sorted(seen_layers.items()))
