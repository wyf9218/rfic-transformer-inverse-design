from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import canonical_geometry_sha256


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_broadband56_v2_bound_queue_builder.py"
SPEC = importlib.util.spec_from_file_location("bound_queue_builder", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return path


def _fixture(tmp_path: Path) -> list[str]:
    config = tmp_path / "config.yaml"
    config.write_text("foundry_layout:\n  enabled: true\n", encoding="utf-8")
    approval = _write_json(
        tmp_path / "approval.json",
        {
            "schema": MODULE.CORRECTED_APPROVAL_SCHEMA,
            "overall_status": "PASS",
            "decision": MODULE.CORRECTED_APPROVAL_DECISION,
            "authorization_scope": MODULE.CORRECTED_APPROVAL_SCOPE,
            "restore_corrected_foundry_layout_contract_authorized": True,
            "one_corrected_rescue_golden_authorized": True,
            "nn_training_authorized": False,
            "verified_bound_files": {
                "corrected_private_configuration": MODULE._file_record(config)
            },
        },
    )
    geometry = {name: float(index + 1) for index, name in enumerate(MODULE.GEOMETRY_FIELDS)}
    geometry_sha = canonical_geometry_sha256(geometry)
    source = _write_json(
        tmp_path / "source.json",
        {
            "schema": MODULE.SAFE_ANCHOR_SOURCE_SCHEMA,
            "overall_status": "PASS",
            "decision": "USE_GEOMETRY_PARAMETERS_ONLY_REGENERATE_WITH_CURRENT_FROZEN_GENERATOR",
            "campaign_id": MODULE.CAMPAIGN_ID,
            "historical_candidate_id": "anchor",
            "current_canonical_geometry_sha256": geometry_sha,
            "geometry_vector_order": list(MODULE.GEOMETRY_FIELDS),
            "geometry": geometry,
            "analytical_gate": {"status": "PASS", "topology_mode": "1t1t"},
            "historical_gds_reused": False,
            "historical_labels_reused": False,
        },
    )
    queue = tmp_path / "queue.csv"
    row = {
        "campaign_id": MODULE.CAMPAIGN_ID,
        "campaign_phase": "PHASE_A",
        "acquisition_source": MODULE.SAFE_ANCHOR_SOURCE,
        "campaign_contract_fingerprint": MODULE.CONTRACT_FINGERPRINT,
        "analytical_status": "PASS",
        "topology_status": "PASS",
        "top_metal_drc_status": "PASS",
        **{
            name: geometry_sha
            for name in (
                "candidate_id_sha256",
                "candidate_geometry_identity_sha256",
                "geometry_id",
                "geometry_sha256",
                "geometry_fingerprint_sha256",
            )
        },
        **{f"geom__{name}": value for name, value in geometry.items()},
    }
    with queue.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    delegate = tmp_path / "delegate.py"
    delegate.write_text("raise SystemExit(99)\n", encoding="utf-8")
    return [
        "--delegate-script",
        str(delegate),
        "--delegate-sha256",
        MODULE._sha256(delegate),
        "--safe-anchor-source-receipt",
        str(source),
        "--safe-anchor-source-sha256",
        MODULE._sha256(source),
        "--safe-anchor-queue",
        str(queue),
        "--safe-anchor-queue-sha256",
        MODULE._sha256(queue),
        "--corrected-approval-receipt",
        str(approval),
        "--corrected-approval-sha256",
        MODULE._sha256(approval),
        "--corrected-config-sha256",
        MODULE._sha256(config),
        "--expected-safe-anchor-geometry-sha256",
        geometry_sha,
        "--expected-safe-anchor-id",
        "anchor",
        "--contract",
        str(tmp_path / "unused-contract.json"),
        "--config",
        str(config),
        "--out-dir",
        str(tmp_path / "out"),
        "--count",
        "1",
        "--phase",
        "PHASE_A",
        "--stage",
        "GOLDEN",
        "--current-accepted",
        "0",
    ]


def test_materializes_exact_geometry_only_golden(tmp_path: Path) -> None:
    argv = _fixture(tmp_path)
    assert MODULE.main(argv) == 0
    receipt = json.loads((tmp_path / "out" / MODULE.OUTPUT_NAME).read_text())
    assert receipt["overall_status"] == "PASS"
    assert receipt["safe_anchor_geometry_sha256"] == argv[argv.index("--expected-safe-anchor-geometry-sha256") + 1]
    assert receipt["source_contract"] == MODULE.SOURCE_CONTRACT
    assert receipt["historical_gds_reused"] is False
    assert receipt["historical_s4p_reused"] is False
    assert receipt["proxy_or_physical_labels_present"] is False
    assert MODULE._sha256(tmp_path / "out" / MODULE.QUEUE_NAME) == MODULE._sha256(
        tmp_path / "queue.csv"
    )


def test_pilot_delegate_always_excludes_exact_historical_anchor(tmp_path, monkeypatch):
    private, argv = MODULE._parse_private_args(_fixture(tmp_path))
    argv[argv.index("--stage") + 1] = "PILOT_32"
    observed = []
    def run(command, **kwargs):
        observed.append((command, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(MODULE.subprocess, "run", run)
    assert MODULE._run_delegate(private, argv) == 0
    command, kwargs = observed[0]
    assert command[-2:] == ["--exclude-geometry-csv", str(tmp_path / "queue.csv")]
    assert command[2:-2] == argv
    assert kwargs["shell"] is False


def test_pilot_delegate_rejects_changed_anchor_before_child(tmp_path, monkeypatch):
    private, argv = MODULE._parse_private_args(_fixture(tmp_path))
    (tmp_path / "queue.csv").write_text("changed")
    def forbidden(*args, **kwargs):
        raise AssertionError("must reject before delegation")
    monkeypatch.setattr(MODULE.subprocess, "run", forbidden)
    with pytest.raises(MODULE.BoundQueueError, match="exclusion queue SHA"):
        MODULE._run_delegate(private, argv)


def test_rejects_approval_config_identity_drift(tmp_path: Path) -> None:
    argv = _fixture(tmp_path)
    config = Path(argv[argv.index("--config") + 1])
    config.write_text("foundry_layout:\n  enabled: false\n", encoding="utf-8")
    argv[argv.index("--corrected-config-sha256") + 1] = MODULE._sha256(config)
    assert MODULE.main(argv) == 2
    assert not (tmp_path / "out").exists()
