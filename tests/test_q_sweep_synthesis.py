from __future__ import annotations

import hashlib
import json
import shlex
import sys
from pathlib import Path

import numpy as np
import pytest

import rfic_transformer_inverse_design.synthesis.q_sweep as q_sweep_module
from rfic_transformer_inverse_design.synthesis.frozen_mlp import (
    FrozenTandemMLP,
    GEOMETRY_COLUMNS,
    MLPBatchPrediction,
)
from rfic_transformer_inverse_design.synthesis.q_sweep import (
    PhysicalTarget3,
    _validate_gds_binary,
    _validated_physical_backend_argv,
    execute_q_sweep,
    run_q_sweep,
)


@pytest.fixture(autouse=True)
def _allow_fixture_backend_only_for_ingestion_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fixture backend tests ingest gates; production command trust is separate."""

    monkeypatch.setattr(
        q_sweep_module,
        "_validated_physical_backend_argv",
        lambda command: shlex.split(command),
    )
from rfic_transformer_inverse_design.synthesis.q_sweep_gui import (
    _model_provenance,
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


def test_physical_backend_command_rejects_unapproved_script() -> None:
    with pytest.raises(ValueError, match="physical backend"):
        _validated_physical_backend_argv(f"{sys.executable} /tmp/fake_backend.py")


def test_gds_artifact_must_be_parseable_and_nonempty(tmp_path: Path) -> None:
    fake = tmp_path / "fake.gds"
    fake.write_bytes(b"GDS-looking bytes are not a GDS library")

    with pytest.raises(ValueError, match="invalid GDS artifact"):
        _validate_gds_binary(fake, "demo_q10")


def _write_physical_backend(
    path: Path,
    *,
    s4p_mode: str = "valid",
    feature_delta: float = 0.0,
    include_evidence_manifest: bool = True,
    include_terminal_receipt: bool = True,
    duplicate_result_row: bool = False,
) -> None:
    script = r'''
import argparse, hashlib, json, gdstk, numpy as np
from pathlib import Path
import rfic_transformer_inverse_design.execution.zeus_cadence as approved_emx_wrapper
import rfic_transformer_inverse_design.layout.export as approved_exporter
import rfic_transformer_inverse_design.synthesis.q_sweep_physical_backend as approved_backend
from rfic_transformer_inverse_design.network_analysis import z_to_s
from rfic_transformer_inverse_design.sim.base import SParameterResult
from rfic_transformer_inverse_design.synthesis.q_sweep import _extract_s4p_features_15ghz

MODE = __MODE__
FEATURE_DELTA = __FEATURE_DELTA__
INCLUDE_EVIDENCE_MANIFEST = __INCLUDE_EVIDENCE_MANIFEST__
INCLUDE_TERMINAL_RECEIPT = __INCLUDE_TERMINAL_RECEIPT__
DUPLICATE_RESULT_ROW = __DUPLICATE_RESULT_ROW__

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write_s4p(path, q, target):
    if MODE == "text":
        path.write_text("! not a parseable Touchstone file\n")
        return None
    frequencies = np.linspace(5.0e9, 60.0e9, 111)
    if MODE == "wrong_grid":
        frequencies = frequencies + 1000.0
    lp_nh = float(target["Lp_nH"]) + abs(q - 17) * 0.1
    ls_nh = float(target["Ls_nH"])
    qp = float(q)
    qs = float(q) + 0.25
    k_abs = float(target["K_abs"])
    transform = np.asarray(((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)))
    common = np.asarray(((1.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.0, 1.0)))
    z_single = np.empty((len(frequencies), 4, 4), dtype=np.complex128)
    for index, frequency_hz in enumerate(frequencies):
        omega = 2.0 * np.pi * frequency_hz
        lp_h = lp_nh * 1.0e-9
        ls_h = ls_nh * 1.0e-9
        mutual_h = k_abs * np.sqrt(lp_h * ls_h)
        z_diff = np.asarray(
            (
                (omega * lp_h / qp + 1j * omega * lp_h, 1j * omega * mutual_h),
                (1j * omega * mutual_h, omega * ls_h / qs + 1j * omega * ls_h),
            ),
            dtype=np.complex128,
        )
        z_common = np.diag((50.0 + 0j, 50.0 + 0j))
        z_single[index] = (
            0.25 * transform @ z_diff @ transform.T
            + 0.25 * common @ z_common @ common.T
        )
    SParameterResult(
        freqs_hz=frequencies,
        s_matrix=z_to_s(z_single, z0=50.0),
        reference_impedance_ohm=50.0,
    ).to_touchstone(path)
    return {
        "Lp_nH": lp_nh,
        "Ls_nH": ls_nh,
        "Qp": qp,
        "Qs": qs,
        "K_abs": k_abs,
    }

p=argparse.ArgumentParser(); p.add_argument('--request-json'); p.add_argument('--out-dir')
a=p.parse_args(); request_path=Path(a.request_json).resolve(); request=json.loads(request_path.read_text()); root=Path(a.out_dir).resolve()
config=root/'fixture_config.json'; process=root/'fixture_process.proc'
config.write_text('{"fixture":"ingestion-only"}\n'); process.write_text('fixture process\n')
approved_sources = {
  'backend': Path(approved_backend.__file__).resolve(),
  'structured_exporter': Path(approved_exporter.__file__).resolve(),
  'emx_wrapper': Path(approved_emx_wrapper.__file__).resolve(),
}
source_hashes={name:sha256(path) for name,path in approved_sources.items()}
rows=[]; candidate_receipts=[]
for item in request['candidates']:
    q=int(item['q_target']); folder=root/item['candidate_id']; folder.mkdir(parents=True)
    gds=folder/'layout.gds'; s4p=folder/'emx.s4p'
    geometry_json=folder/'geometry.json'; layout_manifest=folder/'layout_manifest.json'; evaluation_summary=folder/'summary.json'
    geometry_json.write_text(json.dumps(item['geometry_um'],sort_keys=True))
    layout_manifest.write_text(json.dumps({'fixture':True,'gds':str(gds)}))
    evaluation_summary.write_text(json.dumps({'fixture':True,'s4p':str(s4p)}))
    lib=gdstk.Library(); cell=lib.new_cell('CANDIDATE'); cell.add(gdstk.rectangle((0, 0), (q, q), layer=74)); lib.write_gds(gds)
    target=item['target_features']
    intended = write_s4p(s4p, q, target)
    if MODE == "valid":
        extracted = _extract_s4p_features_15ghz(s4p, item['candidate_id'])
        features = {name: extracted[name] for name in ('Lp_nH', 'Ls_nH', 'Qp', 'Qs', 'K_abs')}
    else:
        features = intended or {
            'Lp_nH': target['Lp_nH'], 'Ls_nH': target['Ls_nH'],
            'Qp': q, 'Qs': q, 'K_abs': target['K_abs'],
        }
    features['Lp_nH'] = float(features['Lp_nH']) + FEATURE_DELTA
    artifact_record = {
        'gds': str(gds.relative_to(root)),
        's4p': str(s4p.relative_to(root)),
    }
    if INCLUDE_EVIDENCE_MANIFEST:
        evidence = folder/'evidence_manifest.json'
        evidence.write_text(json.dumps({
          'schema':'rfic_q_sweep_candidate_physical_evidence.v1',
          'candidate_id':item['candidate_id'],
          'q_target':q,
          'geometry_sha256':item['geometry_sha256'],
          'artifacts':{
            'gds':{'path':str(gds.relative_to(root)),'sha256':sha256(gds)},
            's4p':{'path':str(s4p.relative_to(root)),'sha256':sha256(s4p)},
          },
          'top_cell':'CANDIDATE',
          'structured_exporter':{
            'id':'rfic_transformer_inverse_design.layout.export.export_transformer_layout',
            'source_sha256':source_hashes['structured_exporter'],
          },
          'process_sha256':sha256(process),
          'config_sha256':sha256(config),
          'backend_sha256':source_hashes['backend'],
          'emx_wrapper_sha256':source_hashes['emx_wrapper'],
          'ports':['P001','P002','P003','P004'],
          'differential_pairs':[['P001','P002'],['P003','P004']],
          'ground_mode':'single_ended_shield_grounded',
          'frequency_grid':{
            'start_hz':5.0e9,'stop_hz':60.0e9,
            'step_hz':0.5e9,'point_count':111,
          },
          'feature_extraction_hz':15.0e9,
        }))
        artifact_record['evidence_manifest'] = str(evidence.relative_to(root))
    row={'candidate_id':item['candidate_id'],'q_target':q,'geometry_sha256':item['geometry_sha256'],
      'features_15ghz':features,
      # Synthetic fixture evidence for ingestion tests only, never native proof.
      'operating_point_evidence':{'candidate_id':item['candidate_id'],'frequency_hz':15e9,
        'physical15':{'lp_nh':features['Lp_nH'],'ls_nh':features['Ls_nH'],
          'qmin':min(features['Qp'],features['Qs']),'k_abs':features['K_abs']},
        'descriptor_valid':True,'strict_valid':False,'below_half_srf':False,
        's_z_roundtrip_abs_max':1e-12,'passivity_sigma_max':.99,'reciprocity_abs_max':0.},
      'physical_gate_evidence':{'geometry_sha256':item['geometry_sha256'],
        'gds_sha256':sha256(gds),'s4p_sha256':sha256(s4p),
        'current_structure_compatible':True,'drc_zero_blocking':True,
        'original_GDS_DRC_EMX_binding':True},
      'artifacts':artifact_record}
    rows.append(row)
    if INCLUDE_EVIDENCE_MANIFEST:
        candidate_receipts.append({
          'candidate_id':item['candidate_id'],'q_target':q,
          'geometry_sha256':item['geometry_sha256'],'status':'PASS','error':None,
          'artifacts':{
            **artifact_record,
            'gds_sha256':sha256(gds),'s4p_sha256':sha256(s4p),
            'evidence_manifest_sha256':sha256(evidence),
            'geometry_json':str(geometry_json.relative_to(root)),
            'geometry_json_sha256':sha256(geometry_json),
            'layout_manifest':str(layout_manifest.relative_to(root)),
            'layout_manifest_sha256':sha256(layout_manifest),
            'evaluation_summary':str(evaluation_summary.relative_to(root)),
            'evaluation_summary_sha256':sha256(evaluation_summary),
          },
          'emx_command_sha256':hashlib.sha256(b'["fixture-no-emx"]').hexdigest(),
          'emx_command_argument_count':1,
        })
results_payload={
  'schema':'rfic_q_sweep_physical_results.v1','label_source':'FRESH_REAL_EMX',
  'request_sha256':sha256(request_path),'config_sha256':sha256(config),
  'process_sha256':sha256(process),'model_identity':request.get('model_identity') or {},
  'results':rows,
}
if DUPLICATE_RESULT_ROW:
    results_payload['results'].append(dict(rows[0]))
results_path=root/'physical_results.json'; results_path.write_text(json.dumps(results_payload))
receipt={
  'schema':'rfic_q_sweep_physical_backend_receipt.v1','overall_status':'PASS',
  'decision':'FRESH_REAL_EMX_Q_SWEEP_READY','requested_label_source':'FRESH_REAL_EMX',
  'evidence_source':'FRESH_REAL_EMX','started_utc':'fixture','completed_utc':'fixture',
  'request_json':str(request_path),'request_sha256':sha256(request_path),
  'config':str(config),'config_sha256':sha256(config),
  'process_file':str(process),'process_sha256':sha256(process),
  'runtime_sources':{name:{'path':str(path),'sha256':source_hashes[name]} for name,path in approved_sources.items()},
  'model_identity':request.get('model_identity') or {},'q_values':list(range(10,21)),
  'candidate_count':11,'pass_count':11,'fail_count':0,
  'candidate_evidence_manifest_count':len(candidate_receipts),
  'preflight_checks':[{'name':'fixture_ingestion_contract','status':'PASS','detail':'synthetic; never real EMX'}],
  'candidates':candidate_receipts,'failure_type':None,'failure_message':None,
  'physical_results':str(results_path),'physical_results_sha256':sha256(results_path),
  'backend_receipt':str(root/'backend_receipt.json'),
}
if INCLUDE_TERMINAL_RECEIPT:
    (root/'backend_receipt.json').write_text(json.dumps(receipt))
'''
    path.write_text(
        script.replace("__MODE__", repr(s4p_mode)).replace(
            "__FEATURE_DELTA__", repr(float(feature_delta))
        ).replace(
            "__INCLUDE_EVIDENCE_MANIFEST__", repr(include_evidence_manifest)
        ).replace(
            "__INCLUDE_TERMINAL_RECEIPT__", repr(include_terminal_receipt)
        ).replace(
            "__DUPLICATE_RESULT_ROW__", repr(duplicate_result_row)
        ),
        encoding="utf-8",
    )


def test_physical_backend_must_bind_all_candidates_and_selects_fresh_emx(
    tmp_path: Path,
) -> None:
    backend = tmp_path / "backend.py"
    _write_physical_backend(backend)
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
    assert Path(result.selected_artifacts["evidence_manifest"]).is_file()
    assert len(result.selected_artifacts["evidence_manifest_sha256"]) == 64
    for name in (
        "structured_exporter_source_sha256",
        "process_sha256",
        "config_sha256",
        "backend_sha256",
        "emx_wrapper_sha256",
        "backend_receipt_sha256",
        "physical_results_sha256",
    ):
        assert len(result.selected_artifacts[name]) == 64
    assert Path(result.selected_artifacts["backend_receipt"]).is_file()
    assert len(result.candidates) == 11
    selected = next(item for item in result.candidates if item.q_target == 17)
    assert selected.fresh_emx_features_15ghz["Lp_nH"] == pytest.approx(1.15)
    assert selected.fresh_emx_features_15ghz["Ls_nH"] == pytest.approx(1.40)
    assert selected.fresh_emx_features_15ghz["Qp"] == pytest.approx(17.0)
    assert selected.fresh_emx_features_15ghz["Qs"] == pytest.approx(17.25)
    assert selected.fresh_emx_features_15ghz["K_abs"] == pytest.approx(0.76)
    request = json.loads((output / "physical_backend_request.json").read_text())
    assert request["model_identity"]["model_id"] == "dummy"
    assert request["model_identity"]["contract_payload_sha256"] == ""
    assert request["model_identity"]["architecture"] == {}
    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["model_identity"] == request["model_identity"]


def test_physical_backend_exit_zero_without_terminal_receipt_is_rejected(
    tmp_path: Path,
) -> None:
    backend = tmp_path / "backend_without_receipt.py"
    _write_physical_backend(backend, include_terminal_receipt=False)

    with pytest.raises(ValueError, match="terminal receipt is missing"):
        execute_q_sweep(
            model=DummyModel(),
            target=PhysicalTarget3("demo", 1.15, 1.40, 0.76),
            output_dir=tmp_path / "physical_without_receipt",
            mode="physical",
            physical_backend_command=f"{sys.executable} {backend}",
        )


def test_physical_backend_duplicate_result_row_is_rejected(tmp_path: Path) -> None:
    backend = tmp_path / "backend_duplicate.py"
    _write_physical_backend(backend, duplicate_result_row=True)

    with pytest.raises(ValueError, match="exactly eleven result objects"):
        execute_q_sweep(
            model=DummyModel(),
            target=PhysicalTarget3("demo", 1.15, 1.40, 0.76),
            output_dir=tmp_path / "physical_duplicate",
            mode="physical",
            physical_backend_command=f"{sys.executable} {backend}",
        )


def test_physical_backend_rejects_missing_evidence_manifest(
    tmp_path: Path,
) -> None:
    backend = tmp_path / "backend_missing_evidence.py"
    _write_physical_backend(backend, include_evidence_manifest=False)
    output = tmp_path / "physical_missing_evidence"

    with pytest.raises(ValueError, match="candidate_evidence_manifest_count"):
        execute_q_sweep(
            model=DummyModel(),
            target=PhysicalTarget3("demo", 1.15, 1.40, 0.76),
            output_dir=output,
            mode="physical",
            physical_backend_command=f"{sys.executable} {backend}",
        )

    failure = json.loads((output / "run_manifest.json").read_text())
    assert failure["overall_status"] == "FAIL"
    assert failure["evidence_source"] == "FROZEN_FORWARD_PROXY_DIAGNOSTIC_ONLY"


def test_physical_backend_rejects_non_touchstone_s4p(tmp_path: Path) -> None:
    backend = tmp_path / "backend_text.py"
    _write_physical_backend(backend, s4p_mode="text")

    with pytest.raises(ValueError, match="invalid S4P artifact"):
        execute_q_sweep(
            model=DummyModel(),
            target=PhysicalTarget3("demo", 1.15, 1.40, 0.76),
            output_dir=tmp_path / "physical_text",
            mode="physical",
            physical_backend_command=f"{sys.executable} {backend}",
        )


def test_physical_backend_rejects_wrong_s4p_grid(tmp_path: Path) -> None:
    backend = tmp_path / "backend_grid.py"
    _write_physical_backend(backend, s4p_mode="wrong_grid")

    with pytest.raises(ValueError, match="S4P frequency grid mismatch"):
        execute_q_sweep(
            model=DummyModel(),
            target=PhysicalTarget3("demo", 1.15, 1.40, 0.76),
            output_dir=tmp_path / "physical_grid",
            mode="physical",
            physical_backend_command=f"{sys.executable} {backend}",
        )


def test_physical_backend_rejects_feature_mismatch(tmp_path: Path) -> None:
    backend = tmp_path / "backend_mismatch.py"
    _write_physical_backend(backend, feature_delta=0.01)

    with pytest.raises(ValueError, match="backend feature mismatch"):
        execute_q_sweep(
            model=DummyModel(),
            target=PhysicalTarget3("demo", 1.15, 1.40, 0.76),
            output_dir=tmp_path / "physical_mismatch",
            mode="physical",
            physical_backend_command=f"{sys.executable} {backend}",
        )


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
    assert model.runtime_checkpoint_attestation is not None
    assert model.runtime_checkpoint_attestation["status"] == (
        "FULL_INVERSE_AND_FORWARD_WEIGHTS_HASH_VERIFIED"
    )
    assert not model.arrays["inverse_weights"][0].flags.writeable
    assert _model_provenance(model, contract_path)["runtime_checkpoint_status"] == (
        "FULL_INVERSE_AND_FORWARD_WEIGHTS_HASH_VERIFIED"
    )
    prediction = model.predict(np.asarray([[1.5, 1.6, 12.0, 0.5]]))
    np.testing.assert_allclose(prediction.geometry[0], np.arange(10.0))
    np.testing.assert_allclose(prediction.proxy_features[0], [1.5, 1.6, 12.0, 0.5])


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
