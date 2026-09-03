"""Transformer layout export and geometry helpers."""

from .builders import (
    BridgeEndpointStack,
    BridgePadStage,
    BridgeCornerAnchors,
    CenterTappedInductorGeometry,
    InductorLayoutSpec,
    InductorTerminals,
    LayerPolygonGroup,
    _build_center_tapped_inductor,
    _build_winding,
    _signal_label_point,
)
from .checks import TransformerGdstkCheckResult, _generic_same_layer_spacing_checks, run_transformer_gdstk_checks
from .export import export_transformer_layout
from .foundry_audit import (
    FoundryLayoutAuditError,
    load_and_validate_foundry_layout_audit,
    produce_foundry_layout_audit,
    validate_foundry_layout_audit,
)

__all__ = [
    "BridgeCornerAnchors",
    "BridgeEndpointStack",
    "BridgePadStage",
    "CenterTappedInductorGeometry",
    "InductorLayoutSpec",
    "InductorTerminals",
    "LayerPolygonGroup",
    "TransformerGdstkCheckResult",
    "FoundryLayoutAuditError",
    "export_transformer_layout",
    "load_and_validate_foundry_layout_audit",
    "produce_foundry_layout_audit",
    "run_transformer_gdstk_checks",
    "validate_foundry_layout_audit",
]
