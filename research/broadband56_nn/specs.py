"""One leakage-resistant 137-field token interface for all inverse models."""
from __future__ import annotations

import numpy as np
import torch
from torch.nn import functional as F
from .models import frequency_encoding

TOKEN_DIM = 137
TASK_MODES = ("full", "band", "multi", "single")


def make_spec(s, y, y_valid, frequency_hz, rng, *, task=None, mode=None,
              relation=0, tolerance_fraction=0.05):
    """Each specification comes from one geometry. Hidden validity is not input."""
    b, nf, _ = s.shape
    if task not in (None, "SPECTRUM", "PHYSICAL") or mode not in (None, *TASK_MODES):
        raise ValueError("unknown specification task/mode")
    if relation not in (0, 1, 2) or not np.isfinite(tolerance_fraction) or tolerance_fraction < 0:
        raise ValueError("invalid relation/tolerance")
    sm = torch.zeros_like(s, dtype=torch.bool)
    ym = torch.zeros_like(y, dtype=torch.bool)
    names = []
    for row in range(b):
        row_task = task or ("SPECTRUM" if rng.random() < 0.5 else "PHYSICAL")
        row_mode = mode or TASK_MODES[int(rng.integers(4))]
        available = (torch.ones(nf, dtype=torch.bool) if row_task == "SPECTRUM"
                     else y_valid[row].any(-1).cpu()).nonzero().flatten().numpy()
        if not len(available):
            if task == "PHYSICAL":
                raise ValueError("requested PHYSICAL row has no valid target")
            row_task, available = "SPECTRUM", np.arange(nf)
        if row_mode == "single":
            chosen = rng.choice(available, size=1, replace=False)
        elif row_mode == "multi":
            chosen = np.sort(rng.choice(available, size=min(4, len(available)), replace=False))
        elif row_mode == "band":
            width = int(rng.integers(4, min(17, nf + 1)))
            start = int(rng.integers(nf - width + 1))
            chosen = available[(available >= start) & (available < start + width)]
            if not len(chosen):
                chosen = rng.choice(available, size=1, replace=False)
        else:
            chosen = available
        ix = torch.as_tensor(chosen, dtype=torch.long, device=s.device)
        if row_task == "SPECTRUM":
            sm[row, ix] = True
        else:
            ym[row, ix] = y_valid[row, ix]
        names.append({"task": row_task, "mode": row_mode,
                      "frequency_indices": chosen.tolist()})
    return {"s_target": s, "s_mask": sm, "y_target": y, "y_mask": ym,
            "frequency_hz": frequency_hz,
            "s_tolerance": torch.full_like(s, tolerance_fraction),
            "y_tolerance": torch.full_like(y, tolerance_fraction),
            "y_relation": torch.full_like(y, relation, dtype=torch.long),
            "description": names}


def tokenize(spec, normalizer):
    """Clear values, tolerances and relation codes BEFORE any learned operator."""
    s, y = spec["s_target"], spec["y_target"]
    sm, ym = spec["s_mask"].bool(), spec["y_mask"].bool()
    any_condition = sm.any(-1) | ym.any(-1)
    if not bool(any_condition.any(-1).all()):
        raise ValueError("all-empty specification")
    def tensor(key):
        return torch.as_tensor(normalizer[key], dtype=s.dtype, device=s.device)
    for key in ("s_scale", "y_scale"):
        if not bool(torch.isfinite(tensor(key)).all() and (tensor(key) > 0).all()):
            raise ValueError("normalizer scale must be positive and finite")
    # where is applied before arithmetic, not NaN * zero after arithmetic.
    safe_s = torch.where(sm, s, tensor("s_mean"))
    safe_y = torch.where(ym, y, tensor("y_mean"))
    st = (safe_s - tensor("s_mean")) / tensor("s_scale")
    yt = (safe_y - tensor("y_mean")) / tensor("y_scale")
    if not bool(torch.isfinite(st).all() and torch.isfinite(yt).all()):
        raise ValueError("nonfinite requested target")
    sr = torch.where(sm, spec["s_tolerance"], 0)
    yr = torch.where(ym, spec["y_tolerance"], 0)
    if not bool(torch.isfinite(sr).all() and torch.isfinite(yr).all() and
                (sr >= 0).all() and (yr >= 0).all()):
        raise ValueError("requested tolerances must be finite and nonnegative")
    relation = torch.where(ym, spec["y_relation"], 0)
    if not bool(((relation >= 0) & (relation <= 2)).all()):
        raise ValueError("unknown relation")
    relations = F.one_hot(relation, num_classes=3).to(s.dtype) * ym[..., None]
    freq = frequency_encoding(spec["frequency_hz"].to(s.device, s.dtype))
    if freq.ndim == 2:
        freq = freq.unsqueeze(0).expand(s.shape[0], -1, -1)
    tokens = torch.cat((freq, st, sm.to(s.dtype), sr, yt, ym.to(s.dtype), yr,
                        relations.flatten(-2)), dim=-1)
    assert tokens.shape[-1] == TOKEN_DIM
    return tokens, any_condition


def spectrum_loss(prediction, spec, scale):
    mask = spec["s_mask"]
    scale = torch.as_tensor(scale, dtype=prediction.dtype, device=prediction.device)
    if not bool(torch.isfinite(prediction[mask]).all()):
        raise ValueError("nonfinite requested S prediction")
    target = torch.where(mask, spec["s_target"], 0)
    error = (torch.where(mask, prediction, 0) - target) / scale
    # Primary training EQ is symmetric normalized MSE; tolerance is evaluation only.
    per = torch.where(mask, error.square(), 0).sum((1, 2)) / mask.sum((1, 2)).clamp_min(1)
    return per


def forward_loss(prediction, target, scale):
    scale = torch.as_tensor(scale, dtype=prediction.dtype, device=prediction.device)
    residual = (prediction - target) / scale
    main = residual.square().mean((1, 2)).mean()
    delta = (residual[:, 1:] - residual[:, :-1]).square().mean((1, 2)).mean()
    return main + 0.1 * delta, {"s_mse": main.detach(), "difference_mse": delta.detach()}
