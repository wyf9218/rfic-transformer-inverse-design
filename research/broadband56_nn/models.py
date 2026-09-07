"""Broadband56 v2 architectures; no dataset, simulation or geometry-policy IO.

Forward outputs are normalized S channels. The caller owns train-only S scaling,
the contract-derived geometry mapper, and specification token construction. In
particular, unknown target *values* must be removed before token construction;
``condition_any`` is an aggregation/attention mask, not a substitute for that.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import torch
from torch import Tensor, nn


FREQUENCY_COUNT = 56
S_CHANNELS = 32
DEFAULT_TOKEN_DIM = 137
PACKAGE_MAPPING = {
    "BB01": ("F1", "I1"),
    "BB02": ("F2", "I1"),
    "BB03": ("F3", "I1"),
    "BB04": ("F2", "I2"),
    "BB05": ("F2", "I3"),
    "BB06": ("F2", "I4"),
}


def frequency_encoding(frequency_hz: Tensor) -> Tensor:
    """Return [2u-1, sin(2πku), cos(2πku), k=1..8], u=(GHz-5)/55.

    The sin/cos entries are interleaved in increasing k order. Actual Hz are
    required, and no frequency interpolation or additional EM labels is implied.
    """
    if frequency_hz.ndim not in (1, 2):
        raise ValueError("frequency_hz must have shape [J] or [B,J]")
    if not torch.is_floating_point(frequency_hz):
        raise TypeError("frequency_hz must be a floating-point tensor")
    u = (frequency_hz / 1e9 - 5.0) / 55.0
    k = torch.arange(1, 9, dtype=u.dtype, device=u.device)
    phase = (2.0 * torch.pi) * u.unsqueeze(-1) * k
    paired = torch.stack((torch.sin(phase), torch.cos(phase)), dim=-1)
    return torch.cat(((2.0 * u - 1.0).unsqueeze(-1), paired.flatten(-2)), dim=-1)


def _frequency_grid(geometry: Tensor, frequency_hz: Tensor, geometry_dim: int) -> Tensor:
    if geometry.ndim != 2 or geometry.shape[-1] != geometry_dim:
        raise ValueError(f"geometry must have shape [B,{geometry_dim}]")
    if frequency_hz.ndim == 1 and frequency_hz.shape[0] == FREQUENCY_COUNT:
        frequency_hz = frequency_hz.unsqueeze(0).expand(geometry.shape[0], -1)
    elif frequency_hz.shape != (geometry.shape[0], FREQUENCY_COUNT):
        raise ValueError("frequency_hz must have shape [56] or [B,56]")
    return frequency_hz.to(device=geometry.device, dtype=geometry.dtype)


def _mlp(widths: list[int], activation: type[nn.Module]) -> nn.Sequential:
    layers: list[nn.Module] = []
    for index, (left, right) in enumerate(zip(widths[:-1], widths[1:])):
        layers.append(nn.Linear(left, right))
        if index < len(widths) - 2:
            layers.append(activation())
    return nn.Sequential(*layers)


class Architecture(nn.Module):
    """Model metadata is serialized alongside weights, never inferred from names."""

    def __init__(self, geometry_dim: int, config: dict[str, Any]) -> None:
        super().__init__()
        if not isinstance(geometry_dim, int) or geometry_dim <= 0:
            raise ValueError("geometry_dim must be a positive contract-derived integer")
        self.geometry_dim = geometry_dim
        self.config = {
            "schema": "broadband56_architecture_v2",
            "geometry_dim": geometry_dim,
            "frequency_count": FREQUENCY_COUNT,
            **config,
        }


class PointConditionalForward(Architecture):
    def __init__(self, geometry_dim: int) -> None:
        super().__init__(geometry_dim, {
            "kind": "F1", "widths": [geometry_dim + 1, 256, 256, 256, 32],
            "activation": "ReLU", "frequency_representation": "2*((f_Hz/1e9-5)/55)-1",
            "dropout": 0.0, "output": "normalized_S_real_imag_32_unclipped",
        })
        self.network = _mlp([geometry_dim + 1, 256, 256, 256, 32], nn.ReLU)

    def forward(self, geometry: Tensor, frequency_hz: Tensor) -> Tensor:
        frequency_hz = _frequency_grid(geometry, frequency_hz, self.geometry_dim)
        frequency = (2.0 * ((frequency_hz / 1e9 - 5.0) / 55.0) - 1.0).unsqueeze(-1)
        geometry = geometry.unsqueeze(1).expand(-1, FREQUENCY_COUNT, -1)
        return self.network(torch.cat((geometry, frequency), dim=-1))


class PreNormResidual(nn.Module):
    """Two main-trunk Linear layers: h + W2(SiLU(W1(LN(h))))."""

    def __init__(self, width: int) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(width)
        self.linear1 = nn.Linear(width, width)
        self.activation = nn.SiLU()
        self.linear2 = nn.Linear(width, width)

    def forward(self, hidden: Tensor) -> Tensor:
        return hidden + self.linear2(self.activation(self.linear1(self.norm(hidden))))


class FiLMResidual(nn.Module):
    """h + W2(SiLU(W1((1+gamma(z))*LN(h)+beta(z))))."""

    def __init__(self, width: int = 256, geometry_width: int = 512) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(width)
        self.film = nn.Linear(geometry_width, 2 * width)
        self.linear1 = nn.Linear(width, width)
        self.activation = nn.SiLU()
        self.linear2 = nn.Linear(width, width)
        nn.init.zeros_(self.film.weight)
        nn.init.zeros_(self.film.bias)

    def forward(self, hidden: Tensor, geometry_hidden: Tensor) -> Tensor:
        gamma, beta = self.film(geometry_hidden).chunk(2, dim=-1)
        modulated = (1.0 + gamma.unsqueeze(1)) * self.norm(hidden) + beta.unsqueeze(1)
        return hidden + self.linear2(self.activation(self.linear1(modulated)))


FOURIER_CONFIG = {
    "dimension": 17, "u": "(f_Hz/1e9-5)/55", "coordinate": "2*u-1",
    "pairs": "interleaved_sin_cos", "harmonics": list(range(1, 9)),
}


class FiLMForward(Architecture):
    def __init__(self, geometry_dim: int) -> None:
        super().__init__(geometry_dim, {
            "kind": "F2", "geometry_encoder": [geometry_dim, 256, 512],
            "geometry_residual_blocks": 4, "geometry_residual_width": 512,
            "geometry_residual_main_linear_layers": 8,
            "frequency_encoder": [17, 128, 256], "frequency_encoding": FOURIER_CONFIG,
            "decoder_residual_blocks": 4, "decoder_residual_width": 256,
            "decoder_main_linear_layers": 8, "output_linear": [256, 32],
            "activation": "SiLU", "normalization": "LayerNorm_pre_norm",
            "residual": "h + W2(SiLU(W1(LN(h))))",
            "film_residual": "h + W2(SiLU(W1((1+gamma(z))*LN(h)+beta(z))))",
            "film_initialization": "zero_gamma_beta_weights_and_biases",
            "dropout": 0.0, "batch_norm": False,
            "output": "normalized_S_real_imag_32_unclipped",
        })
        self.geometry_encoder = _mlp([geometry_dim, 256, 512], nn.SiLU)
        self.geometry_blocks = nn.Sequential(*(PreNormResidual(512) for _ in range(4)))
        self.frequency_encoder = _mlp([17, 128, 256], nn.SiLU)
        self.decoder_blocks = nn.ModuleList(FiLMResidual() for _ in range(4))
        self.output = nn.Linear(256, 32)

    def forward(self, geometry: Tensor, frequency_hz: Tensor) -> Tensor:
        frequency_hz = _frequency_grid(geometry, frequency_hz, self.geometry_dim)
        geometry_hidden = self.geometry_blocks(self.geometry_encoder(geometry))
        hidden = self.frequency_encoder(frequency_encoding(frequency_hz))
        for block in self.decoder_blocks:
            hidden = block(hidden, geometry_hidden)
        return self.output(hidden)


class BranchTrunkForward(Architecture):
    def __init__(self, geometry_dim: int, rank: int = 64) -> None:
        if rank != 64:
            raise ValueError("The frozen primary F3 contract has rank 64; ablations need new configs")
        super().__init__(geometry_dim, {
            "kind": "F3", "branch": [geometry_dim, 512, 512, 512, 32 * rank],
            "trunk": [17, 256, 256, 256, rank], "rank": rank,
            "frequency_encoding": FOURIER_CONFIG, "activation": "SiLU",
            "contraction": "einsum('bcr,bjr->bjc') + channel_bias",
            "dropout": 0.0, "output": "normalized_S_real_imag_32_unclipped",
        })
        self.rank = rank
        self.branch = _mlp([geometry_dim, 512, 512, 512, S_CHANNELS * rank], nn.SiLU)
        self.trunk = _mlp([17, 256, 256, 256, rank], nn.SiLU)
        self.bias = nn.Parameter(torch.zeros(S_CHANNELS))

    def forward(self, geometry: Tensor, frequency_hz: Tensor) -> Tensor:
        frequency_hz = _frequency_grid(geometry, frequency_hz, self.geometry_dim)
        branch = self.branch(geometry).reshape(geometry.shape[0], S_CHANNELS, self.rank)
        trunk = self.trunk(frequency_encoding(frequency_hz))
        return torch.einsum("bcr,bjr->bjc", branch, trunk) + self.bias


def _validate_inverse(tokens: Tensor, condition_any: Tensor, token_dim: int) -> None:
    if tokens.ndim != 3 or tokens.shape[1:] != (FREQUENCY_COUNT, token_dim):
        raise ValueError(f"tokens must have shape [B,56,{token_dim}]")
    if condition_any.shape != tokens.shape[:2] or condition_any.dtype != torch.bool:
        raise ValueError("condition_any must be bool with shape [B,56]")
    if condition_any.device != tokens.device:
        raise ValueError("condition_any and tokens must be on the same device")
    if not bool(condition_any.any(dim=1).all()):
        raise ValueError("All-empty specification is not a valid inverse design request")


def masked_pooling(hidden: Tensor, condition_any: Tensor) -> Tensor:
    """Pool only requested frequency locations; reject empty and preserve gradients."""
    if hidden.ndim != 3 or condition_any.shape != hidden.shape[:2]:
        raise ValueError("hidden [B,J,H] and condition_any [B,J] shapes must agree")
    if condition_any.dtype != torch.bool:
        raise TypeError("condition_any must be bool")
    count = condition_any.sum(dim=1, keepdim=True)
    if not bool((count > 0).all()):
        raise ValueError("All-empty specification cannot be pooled")
    selected = torch.where(condition_any.unsqueeze(-1), hidden, torch.zeros_like(hidden))
    return selected.sum(dim=1) / count.to(hidden.dtype)


class SpectrumMLPInverse(Architecture):
    def __init__(self, geometry_dim: int, token_dim: int) -> None:
        super().__init__(geometry_dim, {
            "kind": "I1", "token_dim": token_dim,
            "widths": [FREQUENCY_COUNT * token_dim, 256, 256, 256, geometry_dim],
            "activation": "ReLU", "dropout": 0.0,
            "output": "geometry_logits_common_mapper_external",
            "masked_target_sanitization": "required_before_token_construction",
        })
        self.token_dim = token_dim
        self.network = _mlp([FREQUENCY_COUNT * token_dim, 256, 256, 256, geometry_dim], nn.ReLU)

    def forward(self, tokens: Tensor, condition_any: Tensor) -> Tensor:
        _validate_inverse(tokens, condition_any, self.token_dim)
        return self.network(tokens.flatten(1))


class ResidualConv1D(nn.Module):
    """Two kernel-3 convolutions, pre-norm/SiLU, no temporal downsampling."""

    def __init__(self, input_channels: int, output_channels: int) -> None:
        super().__init__()
        self.norm1 = nn.LayerNorm(input_channels)
        self.norm2 = nn.LayerNorm(output_channels)
        self.conv1 = nn.Conv1d(input_channels, output_channels, 3, stride=1, padding=1)
        self.conv2 = nn.Conv1d(output_channels, output_channels, 3, stride=1, padding=1)
        self.shortcut = (nn.Identity() if input_channels == output_channels
                         else nn.Linear(input_channels, output_channels))
        self.activation = nn.SiLU()

    def forward(self, hidden: Tensor) -> Tensor:
        residual = self.shortcut(hidden)
        hidden = self.conv1(self.activation(self.norm1(hidden)).transpose(1, 2)).transpose(1, 2)
        hidden = self.conv2(self.activation(self.norm2(hidden)).transpose(1, 2)).transpose(1, 2)
        return residual + hidden


class SpectrumCNNInverse(Architecture):
    def __init__(self, kind: str, geometry_dim: int, token_dim: int) -> None:
        width, depth, feedforward = {"I2": (256, 0, 0), "I3": (256, 4, 1024),
                                    "I4": (384, 6, 1536)}[kind]
        super().__init__(geometry_dim, {
            "kind": kind, "token_dim": token_dim, "token_projection": [token_dim, 128],
            "cnn_blocks": [[128, 128], [128, 256]], "cnn_linear_convolutions_per_block": 2,
            "cnn_kernel": 3, "cnn_stride": 1, "cnn_padding": 1,
            "cnn_downsampling": False, "cnn_activation": "SiLU",
            "cnn_normalization": "LayerNorm_pre_norm_per_token",
            "cnn_projection_shortcut": "Linear_per_token_when_width_changes",
            "attention_input_projection": [256, width] if width != 256 else None,
            "transformer_depth": depth, "transformer_d_model": width if depth else None,
            "transformer_heads": 8 if depth else None,
            "transformer_feedforward": feedforward if depth else None,
            "transformer_pre_norm": True if depth else None,
            "transformer_activation": "GELU" if depth else None,
            "transformer_dropout": 0.05 if depth else 0.0,
            "transformer_initialization": "independent_Xavier_uniform_linear_and_attention_weights_zero_biases" if depth else None,
            "causal_attention": False, "attention_key_padding": "not_condition_any",
            "pooling": "masked_mean_over_condition_any",
            "head": [width, 128, geometry_dim], "head_activation": "SiLU",
            "output": "geometry_logits_common_mapper_external",
            "masked_target_sanitization": "required_before_token_construction",
        })
        self.token_dim = token_dim
        self.projection = nn.Linear(token_dim, 128)
        self.cnn = nn.Sequential(ResidualConv1D(128, 128), ResidualConv1D(128, 256))
        self.attention_projection = nn.Identity() if width == 256 else nn.Linear(256, width)
        self.transformer: nn.TransformerEncoder | None = None
        if depth:
            layer = nn.TransformerEncoderLayer(
                d_model=width, nhead=8, dim_feedforward=feedforward, dropout=0.05,
                activation="gelu", batch_first=True, norm_first=True,
            )
            self.transformer = nn.TransformerEncoder(layer, num_layers=depth, enable_nested_tensor=False)
            # TransformerEncoder clones one initialized layer. Independently initialize
            # every cloned layer so depth is not a stack of initially identical maps.
            for encoder_layer in self.transformer.layers:
                for module in encoder_layer.modules():
                    if isinstance(module, nn.Linear):
                        nn.init.xavier_uniform_(module.weight)
                        if module.bias is not None:
                            nn.init.zeros_(module.bias)
                nn.init.xavier_uniform_(encoder_layer.self_attn.in_proj_weight)
                nn.init.zeros_(encoder_layer.self_attn.in_proj_bias)
        self.head = _mlp([width, 128, geometry_dim], nn.SiLU)

    def forward(self, tokens: Tensor, condition_any: Tensor) -> Tensor:
        _validate_inverse(tokens, condition_any, self.token_dim)
        hidden = self.attention_projection(self.cnn(self.projection(tokens)))
        if self.transformer is not None:
            hidden = self.transformer(hidden, src_key_padding_mask=~condition_any)
        return self.head(masked_pooling(hidden, condition_any))


def build_forward(kind: str, geometry_dim: int) -> Architecture:
    constructors = {"F1": PointConditionalForward, "F2": FiLMForward, "F3": BranchTrunkForward}
    if kind not in constructors:
        raise ValueError(f"Unknown forward architecture: {kind}")
    return constructors[kind](geometry_dim)


def build_inverse(kind: str, geometry_dim: int, token_dim: int = DEFAULT_TOKEN_DIM) -> Architecture:
    if not isinstance(token_dim, int) or token_dim <= 0:
        raise ValueError("token_dim must be a positive integer derived from the common schema")
    if kind == "I1":
        return SpectrumMLPInverse(geometry_dim, token_dim)
    if kind not in {"I2", "I3", "I4"}:
        raise ValueError(f"Unknown inverse architecture: {kind}")
    return SpectrumCNNInverse(kind, geometry_dim, token_dim)


def parameter_counts(model: nn.Module) -> dict[str, int]:
    return {"total": sum(parameter.numel() for parameter in model.parameters()),
            "trainable": sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)}


def architecture_config(model: Architecture) -> dict[str, Any]:
    config = deepcopy(model.config)
    config["parameter_counts"] = parameter_counts(model)
    return config
