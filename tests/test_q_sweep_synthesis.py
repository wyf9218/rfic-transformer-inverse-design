from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from rfic_transformer_inverse_design.synthesis.frozen_mlp import (
    FrozenTandemMLP,
    GEOMETRY_COLUMNS,
    MLPBatchPrediction,
)
from rfic_transformer_inverse_design.synthesis.q_sweep import (
    PhysicalTarget3,
    execute_q_sweep,
    run_q_sweep,
)
from rfic_transformer_inverse_design.synthesis.q_sweep_gui import (
    _target_from_payload,
)


class DummyModel:
    model_id = "dummy"
    model_seed = 7
    target_frequency_ghz = 15.0

    def predict(self, targets: np.ndarray) -> MLPBatchPrediction:
        rows = np.asarray(targets, dtype=float)
        geometry = np.column_stack(
            [rows[:, 2] + float(index) for index in range(10)]
        )
        proxy = rows.copy()
        proxy[:, 0] += np.abs(rows[:, 2] - 14.0) * 0.1
        return MLPBatchPrediction(geometry=geometry, proxy_features=proxy)


def test_q_sweep_uses_exact_grid_and_deterministic_best() -> None:
    result = run_q_sweep(
        DummyModel(),
        PhysicalTarget3("demo", 1.15, 1.40, 0.76),
    )
    assert result.q_values == tuple(range(10, 21))
    assert len(result.candidates) == 11
    assert result.selected_q == 14
    assert result.evidence_source == "FROZEN_FORWARD_PROXY_DIAGNOSTIC"
    assert "provisional" in result.scientific_boundary


def test_q_sweep_rejects_a_changed_q_grid() -> None:
    with pytest.raises(ValueError, match="fixed to integers 10 through 20"):
        run_q_sweep(
            DummyModel(),
            PhysicalTarget3("demo", 1.15, 1.40, 0.76),
            q_values=(10, 11),
        )


def test_proxy_run_writes_preview_but_not_gds(tmp_path: Path) -> None:
    output = tmp_path / "proxy_run"
    result = execute_q_sweep(
        model=DummyModel(),
        target=PhysicalTarget3("demo", 1.15, 1.40, 0.76),
        output_dir=output,
    )
    assert result.selected_q == 14
    assert (output / "deliverables/demo_selected_structure.png").is_file()
    assert (output / "proxy_candidates.csv").is_file()
    assert "gds" not in result.selected_artifacts
    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["evidence_source"] == "FROZEN_FORWARD_PROXY_DIAGNOSTIC"


def test_physical_mode_fails_closed_without_backend(tmp_path: Path) -> None:
    output = tmp_path / "physical_missing"
    with pytest.raises(RuntimeError, match="physical mode requires"):
        execute_q_sweep(
            model=DummyModel(),
            target=PhysicalTarget3("demo", 1.15, 1.40, 0.76),
            output_dir=output,
            mode="physical",
        )
    failure = json.loads((output / "run_manifest.json").read_text())
    assert failure["overall_status"] == "FAIL"
    assert failure["evidence_source"] == "FROZEN_FORWARD_PROXY_DIAGNOSTIC_ONLY"


def test_physical_backend_must_bind_all_candidates_and_selects_fresh_emx(
    tmp_path: Path,
) -> None:
    backend = tmp_path / "backend.py"
    backend.write_text(
        """
import argparse, json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--request-json'); p.add_argument('--out-dir')
a=p.parse_args(); request=json.loads(Path(a.request_json).read_text()); root=Path(a.out_dir)
rows=[]
for item in request['candidates']:
    q=int(item['q_target']); folder=root/item['candidate_id']; folder.mkdir(parents=True)
    gds=folder/'layout.gds'; s4p=folder/'emx.s4p'; gds.write_bytes(b'GDS'+bytes([q])); s4p.write_text('! fresh emx')
    target=item['target_features']
    rows.append({'candidate_id':item['candidate_id'],'q_target':q,'geometry_sha256':item['geometry_sha256'],
      'features_15ghz':{'Lp_nH':target['Lp_nH']+abs(q-17)*0.1,'Ls_nH':target['Ls_nH'],'Qp':q,'Qs':q,'K_abs':target['K_abs']},
      'artifacts':{'gds':str(gds.relative_to(root)),'s4p':str(s4p.relative_to(root))}})
(root/'physical_results.json').write_text(json.dumps({'schema':'rfic_q_sweep_physical_results.v1','label_source':'FRESH_REAL_EMX','results':rows}))
""",
        encoding="utf-8",
    )
    output = tmp_path / "physical_run"
    result = execute_q_sweep(
        model=DummyModel(),
        target=PhysicalTarget3("demo", 1.15, 1.40, 0.76),
        output_dir=output,
        mode="physical",
        physical_backend_command=f"{sys.executable} {backend}",
    )
    assert result.selected_q == 17
    assert result.evidence_source == "FRESH_REAL_EMX"
    assert Path(result.selected_artifacts["gds"]).is_file()
    assert Path(result.selected_artifacts["s4p"]).is_file()
    assert len(result.candidates) == 11


def test_gui_payload_has_three_physical_inputs_and_no_q_input() -> None:
    target = _target_from_payload(
        {"design_id": "demo", "lp_nh": 1.15, "ls_nh": 1.40, "k_abs": 0.76}
    )
    assert target == PhysicalTarget3("demo", 1.15, 1.40, 0.76)


def test_hash_bound_loader_and_numpy_runtime(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    summary = {
        "input_columns": [
            "input__lp_nh_center",
            "input__ls_nh_center",
            "input__q_center",
            "input__k_abs_center",
        ],
        "geometry_columns": list(GEOMETRY_COLUMNS),
    }
    summary_path = model_dir / "summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    weights_path = model_dir / "weights.npz"
    np.savez(
        weights_path,
        forward_weight_0=np.zeros((10, 5)),
        forward_bias_0=np.zeros(5),
        forward_weight_1=np.zeros((5, 4)),
        forward_bias_1=np.zeros(4),
        inverse_weight_0=np.zeros((4, 5)),
        inverse_bias_0=np.zeros(5),
        inverse_weight_1=np.zeros((5, 10)),
        inverse_bias_1=np.zeros(10),
        normalization__x_mean=np.asarray([1.5, 1.6, 12.0, 0.5]),
        normalization__x_scale=np.ones(4),
        normalization__y_mean=np.arange(10.0),
        normalization__y_scale=np.ones(10),
        normalization__geometry_lower=-np.ones(10),
        normalization__geometry_upper=np.ones(10),
    )
    contract = {
        "schema": "rfic_frozen_tandem_mlp_public_contract.v1",
        "model_id": "synthetic",
        "model_seed": 1,
        "target_frequency_ghz": 15.0,
        "input_columns": summary["input_columns"],
        "geometry_columns": list(GEOMETRY_COLUMNS),
        "declared_support_lower": [0.5, 0.5, 5.0, 0.0],
        "declared_support_upper": [3.0, 3.0, 25.0, 0.8],
        "architecture": {
            "inverse_mlp": [4, 5, 10],
            "forward_surrogate": [10, 5, 4],
        },
        "artifacts": {
            "summary": {"filename": "summary.json", "sha256": _sha(summary_path)},
            "weights": {"filename": "weights.npz", "sha256": _sha(weights_path)},
        },
    }
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    model = FrozenTandemMLP.load(model_dir, contract_path=contract_path)
    prediction = model.predict(np.asarray([[1.5, 1.6, 12.0, 0.5]]))
    np.testing.assert_allclose(prediction.geometry[0], np.arange(10.0))
    np.testing.assert_allclose(prediction.proxy_features[0], [1.5, 1.6, 12.0, 0.5])


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
