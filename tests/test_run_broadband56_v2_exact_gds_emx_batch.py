from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
    TARGET_ACCEPTED_GEOMETRIES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
from rfic_transformer_inverse_design.campaigns.broadband56_exact_gds_emx import (
    EXACT_GDS_EMX_FAILURE_NAME,
    EXACT_GDS_EMX_PASS_DECISION,
    EXACT_GDS_EMX_RECEIPT_NAME,
    EXACT_GDS_EMX_RECEIPT_SCHEMA,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (
    ATTEMPT_REPLENISHMENT_CONTRACT,
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_broadband56_v2_exact_gds_emx_batch.py"


def test_batch_preserves_pass_and_failure_terminal_partition(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, candidate_ids=("a" * 64, "f" * 64))
    result = _run(fixture)
    assert result.returncode == 0, result.stderr

    out = fixture["out_dir"]
    receipt = json.loads(
        (out / "EXACT_GDS_EMX_BATCH_ROLE_RECEIPT.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["overall_status"] == "PASS"
    assert receipt["submitted_count"] == 2
    assert receipt["terminal_count"] == 2
    assert receipt["emx_pass_count"] == 1
    assert receipt["emx_fail_count"] == 1
    assert receipt["failed_candidates_counted_as_accepted"] is False
    assert receipt["simulator_action_taken"] is True

    pass_rows = _read_csv(out / "EXACT_GDS_EMX_RECEIPT_INDEX.csv")
    failure_rows = _read_csv(out / "EXACT_GDS_EMX_FAILURE_INDEX.csv")
    evidence_rows = _read_csv(out / "EXACT_GDS_EMX_DELEGATE_EVIDENCE_INDEX.csv")
    assert [row["accepted_sequence"] for row in pass_rows] == ["1"]
    assert [row["candidate_id_sha256"] for row in pass_rows] == ["a" * 64]
    assert [row["candidate_id_sha256"] for row in failure_rows] == ["f" * 64]
    assert failure_rows[0]["terminal_stage"] == "EMX_FAILURE"
    assert len(evidence_rows) == 2
    assert {row["overall_status"] for row in evidence_rows} == {"PASS", "FAIL"}
    assert all(len(row["command_argv_sha256"]) == 64 for row in evidence_rows)
    assert all(Path(row["delegate_result_path"]).is_file() for row in evidence_rows)


def test_batch_treats_mismatched_pass_receipt_as_candidate_failure(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, candidate_ids=("b" * 64,))
    result = _run(fixture)
    assert result.returncode == 0, result.stderr

    out = fixture["out_dir"]
    receipt = json.loads(
        (out / "EXACT_GDS_EMX_BATCH_ROLE_RECEIPT.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["emx_pass_count"] == 0
    assert receipt["emx_fail_count"] == 1
    failure = _read_csv(out / "EXACT_GDS_EMX_FAILURE_INDEX.csv")[0]
    assert "candidate_id" in failure["error"]


def test_batch_rejects_duplicate_geometry_before_launch(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, candidate_ids=("a" * 64, "c" * 64))
    rows = _read_csv(fixture["input_index"])
    rows[1]["geometry_sha256"] = rows[0]["geometry_sha256"]
    _write_csv(fixture["input_index"], rows)
    receipt = json.loads(fixture["input_receipt"].read_text(encoding="utf-8"))
    receipt["pass_index"] = _file_record(fixture["input_index"])
    fixture["input_receipt"].write_text(
        json.dumps(receipt), encoding="utf-8"
    )

    result = _run(fixture)
    assert result.returncode == 2
    assert "geometry identities are not unique" in result.stderr
    assert not fixture["out_dir"].exists()


def test_batch_is_no_clobber(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, candidate_ids=("a" * 64,))
    first = _run(fixture)
    assert first.returncode == 0, first.stderr
    receipt_path = fixture["out_dir"] / "EXACT_GDS_EMX_BATCH_ROLE_RECEIPT.json"
    before = receipt_path.read_bytes()

    second = _run(fixture)
    assert second.returncode == 2
    assert "no-clobber" in second.stderr
    assert receipt_path.read_bytes() == before


def test_batch_propagates_an_empty_upstream_partition_without_emx(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, candidate_ids=("a" * 64,))
    _empty_csv_keep_header(fixture["input_index"])
    input_receipt = json.loads(
        fixture["input_receipt"].read_text(encoding="utf-8")
    )
    input_receipt["pass_index"] = _file_record(fixture["input_index"])
    fixture["input_receipt"].write_text(
        json.dumps(input_receipt), encoding="utf-8"
    )

    result = _run(fixture)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(
        (fixture["out_dir"] / "EXACT_GDS_EMX_BATCH_ROLE_RECEIPT.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["submitted_count"] == 0
    assert receipt["emx_pass_count"] == 0
    assert receipt["emx_fail_count"] == 0
    assert receipt["simulator_action_taken"] is False


def _fixture(root: Path, *, candidate_ids: tuple[str, ...]) -> dict:
    config = root / "config.yaml"
    config.write_text("frozen: true\n", encoding="utf-8")
    authorization = root / "FULL_CAMPAIGN_AUTHORIZATION_RECEIPT.json"
    module = root / "exact_module.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    runner = root / "fake_exact_runner.py"
    runner.write_text(_fake_runner_source(), encoding="utf-8")

    rows = []
    for index, candidate_id in enumerate(candidate_ids, start=1):
        geometry_sha = f"{index:x}" * 64
        geometry_sha = geometry_sha[:64]
        gds = root / f"candidate_{index}.gds"
        manifest = root / f"candidate_{index}.layout.json"
        calibre = root / f"candidate_{index}.calibre.json"
        gds.write_bytes(f"gds-{index}".encode("ascii"))
        manifest.write_text('{"top_cell":"TRANSFORMER"}\n', encoding="utf-8")
        calibre.write_text('{"overall_status":"PASS"}\n', encoding="utf-8")
        rows.append(
            {
                "geometry_id": f"geometry_{index}",
                "geometry_sha256": geometry_sha,
                "candidate_id_sha256": candidate_id,
                "campaign_phase": "GOLDEN",
                "acquisition_source": "base_space_filling",
                "campaign_contract_fingerprint": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "gds_path": str(gds),
                "gds_sha256": _sha256(gds),
                "manifest_path": str(manifest),
                "manifest_sha256": _sha256(manifest),
                "calibre_receipt_path": str(calibre),
                "calibre_receipt_sha256": _sha256(calibre),
            }
        )
    input_index = root / "calibre_pass_index.csv"
    _write_csv(input_index, rows)
    input_receipt = root / "CALIBRE_ZERO_BLOCKING_BATCH_ROLE_RECEIPT.json"
    input_receipt.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "stage": "GOLDEN",
                "pass_index": _file_record(input_index),
            }
        ),
        encoding="utf-8",
    )
    backend_manifest = root / "PRIVATE_BACKEND_IDENTITY_MANIFEST.json"
    backend_manifest.write_text(
        json.dumps(
            {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "script_identities": {
                    "exact_audited_gds_emx_runner": _file_record(SCRIPT),
                    "exact_audited_gds_emx_single_runner": _file_record(runner),
                    "exact_audited_gds_emx_module": _file_record(module),
                },
                "runtime_identities": {
                    "python_executable": _file_record(
                        Path(sys.executable).resolve()
                    ),
                    "private_configuration": _file_record(config),
                },
            }
        ),
        encoding="utf-8",
    )
    authorization.write_text(
        json.dumps(
            {
                "schema": FULL_CAMPAIGN_APPROVAL_SCHEMA,
                "overall_status": "PASS",
                "decision": FULL_CAMPAIGN_PASS_DECISION,
                "authorization_scope": FULL_CAMPAIGN_APPROVAL_SCOPE,
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "backend_identity_manifest": _file_record(backend_manifest),
                "approved_by": "test owner",
                "emx_authorized_within_current_stage": True,
                "campaign_200k_authorized": True,
                "accepted_geometry_target": TARGET_ACCEPTED_GEOMETRIES,
                "replenished_attempt_rounds_authorized": True,
                "attempt_replenishment_contract": ATTEMPT_REPLENISHMENT_CONTRACT,
            }
        ),
        encoding="utf-8",
    )
    input_payload = json.loads(input_receipt.read_text(encoding="utf-8"))
    input_payload["backend_identity_manifest"] = _file_record(backend_manifest)
    input_payload["full_campaign_authorization_receipt"] = _file_record(
        authorization
    )
    input_receipt.write_text(json.dumps(input_payload), encoding="utf-8")
    return {
        "config": config,
        "authorization": authorization,
        "module": module,
        "runner": runner,
        "input_index": input_index,
        "input_receipt": input_receipt,
        "backend_manifest": backend_manifest,
        "out_dir": root / "batch_out",
    }


def _run(fixture: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--stage",
            "GOLDEN",
            "--input-role-receipt",
            str(fixture["input_receipt"]),
            "--backend-identity-manifest",
            str(fixture["backend_manifest"]),
            "--config",
            str(fixture["config"]),
            "--full-campaign-receipt",
            str(fixture["authorization"]),
            "--max-concurrency",
            "2",
            "--out-dir",
            str(fixture["out_dir"]),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _fake_runner_source() -> str:
    return f'''import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--candidate-id-sha256", required=True)
p.add_argument("--geometry-identity-sha256", required=True)
p.add_argument("--out-dir", required=True)
args, _ = p.parse_known_args()
out = Path(args.out_dir)
out.mkdir(parents=True)
if args.candidate_id_sha256.startswith("f"):
    (out / "{EXACT_GDS_EMX_FAILURE_NAME}").write_text(
        json.dumps({{"overall_status": "FAIL", "error": "synthetic test failure"}}),
        encoding="utf-8",
    )
    raise SystemExit(2)
candidate = "0" * 64 if args.candidate_id_sha256.startswith("b") else args.candidate_id_sha256
receipt = {{
    "schema": "{EXACT_GDS_EMX_RECEIPT_SCHEMA}",
    "overall_status": "PASS",
    "decision": "{EXACT_GDS_EMX_PASS_DECISION}",
    "campaign_id": "{CAMPAIGN_ID}",
    "contract_fingerprint_sha256": "{SCIENTIFIC_CONTRACT_FINGERPRINT}",
    "candidate_id_sha256": candidate,
    "geometry_identity_sha256": args.geometry_identity_sha256,
    "fresh_real_emx_executed": True,
    "proxy_or_historical_label_used": False,
    "simulator_action_taken": True,
}}
(out / "{EXACT_GDS_EMX_RECEIPT_NAME}").write_text(
    json.dumps(receipt), encoding="utf-8"
)
'''


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _empty_csv_keep_header(path: Path) -> None:
    with path.open(newline="", encoding="utf-8") as handle:
        fields = list(csv.DictReader(handle).fieldnames or [])
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=fields).writeheader()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_record(path: Path) -> dict[str, str | int]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }
