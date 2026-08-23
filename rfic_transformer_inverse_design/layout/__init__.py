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

__all__ = [
    "BridgeCornerAnchors",
    "BridgeEndpointStack",
    "BridgePadStage",
    "CenterTappedInductorGeometry",
    "InductorLayoutSpec",
    "InductorTerminals",
    "LayerPolygonGroup",
    "TransformerGdstkCheckResult",
    "export_transformer_layout",
    "run_transformer_gdstk_checks",
]
