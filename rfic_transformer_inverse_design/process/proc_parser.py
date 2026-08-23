"""Lightweight EMX `.proc` parser for layer-name and thickness lookup."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Literal

from ..paths import bundled_proc_dir, resolve_local_path

_ASSUME_RE = re.compile(r"^assume\s+(.+)$", flags=re.IGNORECASE)
_DEFINE_RE = re.compile(r"^define\s+([A-Za-z0-9_]+)\s*=\s*(.+?)\s*$")
_LAYER_RE = re.compile(
    r"^layer\s+([0-9.]+|infinity)\s+([0-9.]+)(?:\s+conductivity\s+([-+]?[0-9.]+(?:[eE][-+]?[0-9]+)?)\s+\S+)?(?:\s*#\s*(.*))?$",
    flags=re.IGNORECASE,
)
_POSITION_RE = re.compile(r"^position\s+([-+]?[0-9.]+(?:[eE][-+]?[0-9]+)?)\s*$", flags=re.IGNORECASE)
_GDS_TOKEN_RE = re.compile(r"l(\d+)t(\d+)", flags=re.IGNORECASE)
_METAL_NAME_RE = re.compile(r"metal(\d+)$", flags=re.IGNORECASE)
_VIA_NAME_RE = re.compile(r"via(\d+)$", flags=re.IGNORECASE)

@dataclass(frozen=True)
class ProcLayerDefinition:
    """Named logical layer declared in a `.proc` file."""

    name: str
    expression: str
    category: Literal["metal", "via", "other"]
    gds_layers: tuple[int, ...]
    gds_layer_datatypes: tuple[tuple[int, int], ...]
    line_no: int


@dataclass(frozen=True)
class ProcConductor:
    """Physical conductor entry from the process stack."""

    name: str
    thickness_um: float
    z_bottom_um: float
    z_top_um: float
    sheet_resistance_expr: str
    line_no: int
    gds_layers: tuple[int, ...]
    gds_layer_datatypes: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class ProcDielectricLayer:
    """Finite dielectric slab entry from the process stack."""

    name: str
    thickness_um: float
    z_bottom_um: float
    z_top_um: float
    epsilon_r: float
    line_no: int
    conductivity_s_per_m: float = 0.0


@dataclass(frozen=True)
class ProcGdsPair:
    """Concrete raw GDS layer/datatype pair used for export."""

    layer: int
    datatype: int
    role: Literal["drawing", "pin", "single"]
    logical_name: str


@dataclass(frozen=True)
class ProcFileInfo:
    """Parsed view of a `.proc` file focused on stackup lookup."""

    path: Path
    assumptions: tuple[str, ...]
    layer_definitions: tuple[ProcLayerDefinition, ...]
    conductors: tuple[ProcConductor, ...]
    dielectrics: tuple[ProcDielectricLayer, ...]

    @staticmethod
    def _role_for_pair(
        name: str,
        pair: tuple[int, int],
        all_pairs: tuple[tuple[int, int], ...],
    ) -> Literal["drawing", "pin", "single"]:
        if len(all_pairs) <= 1:
            return "single"
        metal_number = _metal_number_from_name(name)
        if metal_number is None:
            return "single"
        min_layer = min(int(layer) for layer, _datatype in all_pairs)
        max_layer = max(int(layer) for layer, _datatype in all_pairs)
        layer = int(pair[0])
        if layer == min_layer:
            return "drawing"
        if layer == max_layer:
            return "pin"
        return "single"

    def _definition_for_gds_layer(self, layer: int) -> ProcLayerDefinition | None:
        matches = self.layer_definitions_for_gds_layer(layer)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        target = int(layer)
        for match in matches:
            pairs = tuple((int(gds_layer), int(datatype)) for gds_layer, datatype in match.gds_layer_datatypes)
            if not pairs:
                continue
            if any(int(gds_layer) == target for gds_layer, _datatype in pairs):
                return match
        return matches[0]

    def gds_pairs_for_layer(self, layer: int) -> tuple[ProcGdsPair, ...]:
        definition = self._definition_for_gds_layer(layer)
        if definition is None or not definition.gds_layer_datatypes:
            return ()
        return tuple(
            ProcGdsPair(
                layer=int(gds_layer),
                datatype=int(datatype),
                role=self._role_for_pair(definition.name, (int(gds_layer), int(datatype)), definition.gds_layer_datatypes),
                logical_name=definition.name,
            )
            for gds_layer, datatype in definition.gds_layer_datatypes
        )

    def preferred_draw_pair_for_layer(self, layer: int) -> ProcGdsPair | None:
        pairs = self.gds_pairs_for_layer(layer)
        if not pairs:
            return None
        drawing_pairs = [pair for pair in pairs if pair.role == "drawing"]
        if drawing_pairs:
            return min(drawing_pairs, key=lambda pair: pair.layer)
        return min(pairs, key=lambda pair: pair.layer)

    def preferred_pin_pair_for_layer(self, layer: int) -> ProcGdsPair | None:
        pairs = self.gds_pairs_for_layer(layer)
        if not pairs:
            return None
        pin_pairs = [pair for pair in pairs if pair.role == "pin"]
        if pin_pairs:
            return max(pin_pairs, key=lambda pair: pair.layer)
        single_pairs = [pair for pair in pairs if pair.role == "single"]
        if single_pairs:
            return max(single_pairs, key=lambda pair: pair.layer)
        return max(pairs, key=lambda pair: pair.layer)

    def layer_definitions_for_gds_layer(self, layer: int) -> tuple[ProcLayerDefinition, ...]:
        target = int(layer)
        return tuple(defn for defn in self.layer_definitions if target in defn.gds_layers)

    def conductors_for_gds_layer(self, layer: int) -> tuple[ProcConductor, ...]:
        target = int(layer)
        return tuple(conductor for conductor in self.conductors if target in conductor.gds_layers)

    def conductor_named(self, name: str) -> ProcConductor | None:
        target = str(name).strip().lower()
        for conductor in self.conductors:
            if conductor.name.lower() == target:
                return conductor
        return None

    def summary_for_gds_layer(self, layer: int) -> str:
        defs = self.layer_definitions_for_gds_layer(layer)
        conductors = self.conductors_for_gds_layer(layer)
        parts: list[str] = []
        if conductors:
            parts.append(
                ", ".join(
                    f"{conductor.name} ({conductor.thickness_um:.3f} um conductor)"
                    for conductor in conductors
                )
            )
        if defs:
            remaining = [defn.name for defn in defs if defn.name.lower() not in {c.name.lower() for c in conductors}]
            if remaining:
                parts.append(", ".join(f"{name} ({self._category_for_name(name, defs)} definition)" for name in remaining))
        pair_roles = []
        for pair in self.gds_pairs_for_layer(layer):
            if pair.layer != int(layer):
                continue
            if pair.role != "single":
                pair_roles.append(f"role={pair.role}")
            pair_roles.append(f"datatype={pair.datatype}")
        if pair_roles:
            parts.append(", ".join(pair_roles))
        if not parts:
            return "no proc mapping found"
        return "; ".join(parts)

    def display_label_for_gds_layer(self, layer: int) -> str:
        target = int(layer)
        conductors = self.conductors_for_gds_layer(target)
        defs = self.layer_definitions_for_gds_layer(target)
        if conductors:
            name = conductors[0].name
        elif defs:
            name = defs[0].name
        else:
            return f"raw [{target}]"
        aliases = " / ".join(_aliases_for_name(name))
        pair = next((item for item in self.gds_pairs_for_layer(target) if int(item.layer) == target), None)
        if pair is None or pair.role == "single":
            return f"{aliases} [{target}]"
        return f"{aliases} {pair.role} [{target}]"

    def selectable_layer_options(self, extra_layers: tuple[int, ...] = ()) -> tuple[tuple[str, int], ...]:
        layers: set[int] = set(extra_layers)
        for definition in self.layer_definitions:
            for layer in definition.gds_layers:
                layers.add(int(layer))
        return tuple((self.display_label_for_gds_layer(layer), int(layer)) for layer in sorted(layers))

    def selectable_metal_options(self, extra_layers: tuple[int, ...] = ()) -> tuple[tuple[str, int], ...]:
        layers: set[int] = set(extra_layers)
        for conductor in self.conductors:
            if _metal_number_from_name(conductor.name) is None:
                continue
            preferred = self.preferred_draw_pair_for_layer(conductor.gds_layers[0]) if conductor.gds_layers else None
            if preferred is not None:
                layers.add(int(preferred.layer))
        return tuple((self.display_label_for_gds_layer(layer), int(layer)) for layer in sorted(layers))

    def metal_number_for_gds_layer(self, layer: int) -> int | None:
        target = int(layer)
        for conductor in self.conductors_for_gds_layer(target):
            metal_number = _metal_number_from_name(conductor.name)
            if metal_number is not None:
                return metal_number
        for definition in self.layer_definitions_for_gds_layer(target):
            metal_number = _metal_number_from_name(definition.name)
            if metal_number is not None:
                return metal_number
        return None

    def gds_layer_for_metal_number(self, metal_number: int) -> int | None:
        target = int(metal_number)
        for conductor in self.conductors:
            if _metal_number_from_name(conductor.name) == target and conductor.gds_layers:
                preferred_pair = self.preferred_draw_pair_for_layer(conductor.gds_layers[0])
                if preferred_pair is not None:
                    return int(preferred_pair.layer)
                preferred = _preferred_gds_layer_from_pairs(conductor.gds_layer_datatypes)
                if preferred is not None:
                    return preferred
                return int(max(conductor.gds_layers))
        for definition in self.layer_definitions:
            if _metal_number_from_name(definition.name) == target and definition.gds_layers:
                preferred_pair = self.preferred_draw_pair_for_layer(definition.gds_layers[0])
                if preferred_pair is not None:
                    return int(preferred_pair.layer)
                preferred = _preferred_gds_layer_from_pairs(definition.gds_layer_datatypes)
                if preferred is not None:
                    return preferred
                return int(max(definition.gds_layers))
        return None

    def gds_layer_for_via_number(self, via_number: int) -> int | None:
        target = int(via_number)
        for definition in self.layer_definitions:
            if _via_number_from_name(definition.name) == target and definition.gds_layers:
                return int(definition.gds_layers[0])
        return None

    @staticmethod
    def _category_for_name(name: str, defs: tuple[ProcLayerDefinition, ...]) -> str:
        target = str(name).strip().lower()
        for defn in defs:
            if defn.name.lower() == target:
                return defn.category
        return "other"


def _definition_category(name: str) -> Literal["metal", "via", "other"]:
    lowered = str(name).strip().lower()
    if lowered.startswith("metal"):
        return "metal"
    if lowered.startswith("via"):
        return "via"
    return "other"


def _aliases_for_name(name: str) -> tuple[str, ...]:
    raw = str(name).strip()
    lowered = raw.lower()
    metal_match = _METAL_NAME_RE.fullmatch(lowered)
    if metal_match:
        return (f"M{metal_match.group(1)}", raw)
    via_match = _VIA_NAME_RE.fullmatch(lowered)
    if via_match:
        return (f"V{via_match.group(1)}", raw)
    return (raw,)


def _metal_number_from_name(name: str) -> int | None:
    match = _METAL_NAME_RE.fullmatch(str(name).strip().lower())
    if match is None:
        return None
    return int(match.group(1))


def _via_number_from_name(name: str) -> int | None:
    match = _VIA_NAME_RE.fullmatch(str(name).strip().lower())
    if match is None:
        return None
    return int(match.group(1))


def _preferred_gds_layer_from_pairs(gds_pairs: tuple[tuple[int, int], ...]) -> int | None:
    if not gds_pairs:
        return None
    datatype0_layers = [int(layer) for layer, datatype in gds_pairs if int(datatype) == 0]
    if datatype0_layers:
        return max(datatype0_layers)
    return max(int(layer) for layer, _datatype in gds_pairs)


@dataclass(frozen=True)
class InferredBridgeRoute:
    bridge_layer: int
    bridge_via_layer: int
    bridge_lower_layer: int | None = None
    bridge_lower_via_layer: int | None = None


def infer_bridge_route_layers(
    proc_info: ProcFileInfo,
    *,
    coil_layer: int,
    bridge_layer: int,
) -> InferredBridgeRoute:
    coil_metal = proc_info.metal_number_for_gds_layer(int(coil_layer))
    target_metal = proc_info.metal_number_for_gds_layer(int(bridge_layer))
    if coil_metal is None:
        raise ValueError(f"coil layer {coil_layer} is not a recognized metal layer in the proc file")
    if target_metal is None:
        raise ValueError(f"bridge layer {bridge_layer} is not a recognized metal layer in the proc file")
    if coil_metal == target_metal:
        raise ValueError("coil layer and bridge layer must be different metals for a crossover route")

    step = 1 if target_metal > coil_metal else -1
    traversed_metals = list(range(coil_metal + step, target_metal + step, step))
    if len(traversed_metals) > 2:
        raise ValueError(
            f"unsupported bridge route from metal{coil_metal} to metal{target_metal}; "
            "only one or two metal-step routes are currently supported"
        )

    first_metal = traversed_metals[0]
    first_layer = proc_info.gds_layer_for_metal_number(first_metal)
    first_via = proc_info.gds_layer_for_via_number(min(coil_metal, first_metal))
    if first_layer is None or first_via is None:
        raise ValueError(f"could not infer first bridge step between metal{coil_metal} and metal{first_metal}")

    if len(traversed_metals) == 1:
        return InferredBridgeRoute(
            bridge_layer=int(first_layer),
            bridge_via_layer=int(first_via),
        )

    second_metal = traversed_metals[1]
    second_layer = proc_info.gds_layer_for_metal_number(second_metal)
    second_via = proc_info.gds_layer_for_via_number(min(first_metal, second_metal))
    if second_layer is None or second_via is None:
        raise ValueError(f"could not infer second bridge step between metal{first_metal} and metal{second_metal}")

    return InferredBridgeRoute(
        bridge_layer=int(first_layer),
        bridge_via_layer=int(first_via),
        bridge_lower_layer=int(second_layer),
        bridge_lower_via_layer=int(second_via),
    )


def _resolve_local_repo_path(path: str | Path) -> Path:
    return resolve_local_path(path, extra_roots=(bundled_proc_dir(),))


@lru_cache(maxsize=32)
def parse_proc_file(path: str | Path) -> ProcFileInfo:
    """Parse a `.proc` file and expose logical-layer and thickness information."""

    resolved = _resolve_local_repo_path(path)
    text = resolved.read_text(encoding="utf-8")
    assumptions: list[str] = []
    layer_definitions: list[ProcLayerDefinition] = []
    conductors: list[ProcConductor] = []
    dielectrics: list[ProcDielectricLayer] = []
    definition_map: dict[str, ProcLayerDefinition] = {}

    lines = text.splitlines()
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assume_match = _ASSUME_RE.match(stripped)
        if assume_match:
            assumptions.append(assume_match.group(1).strip())
            continue
        define_match = _DEFINE_RE.match(stripped)
        if not define_match:
            continue
        name = define_match.group(1).strip()
        expression = define_match.group(2).strip()
        gds_pairs = tuple((int(layer), int(datatype)) for layer, datatype in _GDS_TOKEN_RE.findall(expression))
        definition = ProcLayerDefinition(
            name=name,
            expression=expression,
            category=_definition_category(name),
            gds_layers=tuple(layer for layer, _datatype in gds_pairs),
            gds_layer_datatypes=gds_pairs,
            line_no=line_no,
        )
        layer_definitions.append(definition)
        definition_map[name.lower()] = definition

    stack_height_um = 0.0
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        layer_match = _LAYER_RE.match(stripped)
        if layer_match:
            thickness_token = layer_match.group(1)
            if thickness_token.lower() != "infinity":
                thickness_um = float(thickness_token)
                dielectrics.append(
                    ProcDielectricLayer(
                        name=(layer_match.group(4) or "").strip() or f"layer_{line_no}",
                        thickness_um=thickness_um,
                        z_bottom_um=stack_height_um,
                        z_top_um=stack_height_um + thickness_um,
                        epsilon_r=float(layer_match.group(2)),
                        line_no=line_no,
                        conductivity_s_per_m=float(layer_match.group(3) or 0.0),
                    )
                )
                stack_height_um += thickness_um
            continue
        position_match = _POSITION_RE.match(stripped)
        if position_match:
            stack_height_um += float(position_match.group(1))
            continue
        if not stripped.lower().startswith("conductor "):
            continue
        parts = stripped.split()
        if len(parts) < 4:
            continue
        name = parts[3].strip()
        definition = definition_map.get(name.lower())
        thickness_um = float(parts[1])
        conductors.append(
            ProcConductor(
                name=name,
                thickness_um=thickness_um,
                z_bottom_um=stack_height_um,
                z_top_um=stack_height_um + thickness_um,
                sheet_resistance_expr=parts[2],
                line_no=line_no,
                gds_layers=() if definition is None else definition.gds_layers,
                gds_layer_datatypes=() if definition is None else definition.gds_layer_datatypes,
            )
        )
        stack_height_um += thickness_um

    return ProcFileInfo(
        path=resolved,
        assumptions=tuple(assumptions),
        layer_definitions=tuple(layer_definitions),
        conductors=tuple(conductors),
        dielectrics=tuple(dielectrics),
    )
