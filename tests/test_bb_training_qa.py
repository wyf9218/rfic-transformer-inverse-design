"""Independent bounded QA regressions; no real dataset or training execution."""

from types import SimpleNamespace

import pytest
import torch

from research.broadband56_nn import __main__ as cli
from research.broadband56_nn import training


def test_cli_preexisting_output_does_not_mutate_historical_run(tmp_path):
    output = tmp_path / "completed_historical_run"
    output.mkdir()
    original = output / "TRAINING_RECEIPT.json"
    original.write_text('{"status":"PRETRAINED"}\n', encoding="utf-8")
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(FileExistsError):
        cli.main(["train-forward", "--data", str(tmp_path / "unused_data"),
                  "--contract", str(tmp_path / "unused_contract.json"),
                  "--out", str(output), "--kind", "F1", "--device", "cpu"])
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


def test_strict_invalid_physical_requests_still_pay_failure_cost(monkeypatch):
    raw_s = torch.zeros(2, 56, 32)
    target = torch.ones(2, 56, 4)
    prediction = {"y_safe": target.clone(), "valid": torch.ones_like(target, dtype=torch.bool),
                  "strict_lumped_valid": torch.zeros(2, 56, dtype=torch.bool),
                  "invalid_penalty": torch.zeros(2, 56)}
    monkeypatch.setattr(training, "extract_physical", lambda *_: prediction)
    bundle = SimpleNamespace(norm={"s_scale": [1.] * 32, "y_scale": [1.] * 4},
                             frequency=torch.arange(5, 61) * 1e9)
    decoder = SimpleNamespace(feasibility=lambda geometry: {"penalty": torch.zeros(len(geometry))})
    spec = {"s_mask": torch.zeros_like(raw_s, dtype=torch.bool),
            "y_mask": torch.ones_like(target, dtype=torch.bool),
            "s_target": raw_s, "y_target": target,
            "y_relation": torch.zeros_like(target, dtype=torch.long)}
    loss = training.requested_loss(raw_s, torch.zeros(2, 10), spec, bundle, decoder, {"port_contract": {}})
    assert torch.isfinite(loss) and loss >= 1
