from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_broadband56_v2_calibre_zero_blocking_batch.py"


def test_batch_preserves_pass_and_failure_partition(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, candidate_ids=("a" * 64, "f" * 64))
    result = _run(fixture)
    assert result.returncode == 0, result.stderr

    out = fixture["out_dir"]
    receipt = json.loads(
        (out / "CALIBRE_ZERO_BLOCKING_BATCH_ROLE_RECEIPT.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["overall_status"] == "PASS"
    assert receipt["submitted_count"] == 2
    assert receipt["terminal_count"] == 2
    assert receipt["receipt_pass_count"] == 1
    assert receipt["receipt_fail_count"] == 1
    assert receipt["failed_candidates_counted_as_accepted"] is False
    assert receipt["simulator_action_taken"] is False

    passed = _read_csv(out / "CALIBRE_ZERO_BLOCKING_PASS_INDEX.csv")
    failed = _read_csv(out / "CALIBRE_ZERO_BLOCKING_FAILURE_INDEX.csv")
    evidence = _read_csv(out / "CALIBRE_ZERO_BLOCKING_EVIDENCE_INDEX.csv")
    assert [row["candidate_id_sha256"] for row in passed] == ["a" * 64]
    assert Path(passed[0]["calibre_receipt_path"]).is_file()
    assert [row["candidate_id_sha256"] for row in failed] == ["f" * 64]
    assert failed[0]["terminal_stage"] == "calibre_zero_blocking_receipt"
    assert len(evidence) == 2
    assert {row["overall_status"] for row in evidence} == {"PASS", "FAIL"}


def test_batch_rejects_duplicate_geometry_before_delegate(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, candidate_ids=("a" * 64, "b" * 64))
    rows = _read_csv(fixture["input_index"])
    rows[1]["geometry_sha256"] = rows[0]["geometry_sha256"]
    rows[1]["candidate_geometry_identity_sha256"] = rows[0]["geometry_sha256"]
    _write_csv(fixture["input_index"], rows)
    input_receipt = json.loads(
        fixture["input_receipt"].read_text(encoding="utf-8")
    )
    input_receipt["pass_index"] = _file_record(fixture["input_index"])
    fixture["input_receipt"].write_text(
        json.dumps(input_receipt), encoding="utf-8"
    )

    result = _run(fixture)
    assert result.returncode == 2
    assert "duplicated" in result.stderr
    assert not fixture["out_dir"].exists()


def test_batch_is_no_clobber(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, candidate_ids=("a" * 64,))
    first = _run(fixture)
    assert first.returncode == 0, first.stderr
    receipt_path = (
        fixture["out_dir"] / "CALIBRE_ZERO_BLOCKING_BATCH_ROLE_RECEIPT.json"
    )
    before = receipt_path.read_bytes()

    second = _run(fixture)
    assert second.returncode == 2
    assert "no-clobber" in second.stderr
    assert receipt_path.read_bytes() == before


def test_batch_propagates_an_empty_upstream_partition(tmp_path: Path) -> None:
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
        (
            fixture["out_dir"]
            / "CALIBRE_ZERO_BLOCKING_BATCH_ROLE_RECEIPT.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["submitted_count"] == 0
    assert receipt["receipt_pass_count"] == 0
    assert receipt["receipt_fail_count"] == 0
    assert receipt["simulator_action_taken"] is False


def _fixture(root: Path, *, candidate_ids: tuple[str, ...]) -> dict:
    config = root / "config.yaml"
    config.write_text("frequency_points: 56\n", encoding="utf-8")
    delegate = root / "fake_zero_blocking_builder.py"
    delegate.write_text(_fake_delegate_source(), encoding="utf-8")

    rows = []
    for index, candidate_id in enumerate(candidate_ids, start=1):
        geometry_sha = f"{index:x}" * 64
        geometry_sha = geometry_sha[:64]
        gds = root / f"candidate_{index}.gds"
        manifest = root / f"candidate_{index}.layout.json"
        summary = root / f"candidate_{index}.drc.json"
        gds.write_bytes(f"gds-{index}".encode("ascii"))
        manifest.write_text(
            json.dumps({"top_cell": "TRANSFORMER"}), encoding="utf-8"
        )
        summary.write_text(
            json.dumps({"overall_status": "PASS"}), encoding="utf-8"
        )
        rows.append(
            {
                "geometry_id": f"geometry_{index}",
                "geometry_sha256": geometry_sha,
                "candidate_id_sha256": candidate_id,
                "candidate_geometry_identity_sha256": geometry_sha,
                "campaign_phase": "PHASE_A",
                "acquisition_source": "base_space_filling",
                "campaign_contract_fingerprint": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "gds_path": str(gds),
                "gds_sha256": _sha256(gds),
                "manifest_path": str(manifest),
                "manifest_sha256": _sha256(manifest),
                "drc_summary_path": str(summary),
                "drc_summary_sha256": _sha256(summary),
                "blocking_drc_violation_count": "0",
                "overall_status": "PASS",
            }
        )
    input_index = root / "calibre_pass_index.csv"
    _write_csv(input_index, rows)
    input_receipt = root / "CALIBRE_BATCH_ROLE_RECEIPT.json"
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
                    "calibre_zero_blocking_receipt_builder": _file_record(SCRIPT),
                    "calibre_zero_blocking_single_receipt_builder": _file_record(
                        delegate
                    ),
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
    authorization = root / "FULL_CAMPAIGN_AUTHORIZATION_RECEIPT.json"
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
            }
        ),
        encoding="utf-8",
    )
    return {
        "config": config,
        "delegate": delegate,
        "input_index": input_index,
        "input_receipt": input_receipt,
        "backend_manifest": backend_manifest,
        "authorization": authorization,
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


def _fake_delegate_source() -> str:
    return '''import argparse
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--config", required=True)
p.add_argument("--gds", required=True)
p.add_argument("--manifest", required=True)
p.add_argument("--candidate-id-sha256", required=True)
p.add_argument("--geometry-identity-sha256", required=True)
p.add_argument("--out-dir", required=True)
args, _ = p.parse_known_args()
if args.candidate_id_sha256.startswith("f"):
    raise SystemExit(2)
out = Path(args.out_dir)
out.mkdir(parents=True)
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
receipt = {
    "schema": "rfic_transformer.broadband56_v2_calibre_zero_blocking_receipt.v1",
    "overall_status": "PASS",
    "decision": "USE_EXACT_ZERO_BLOCKING_GDS_FOR_FRESH_EMX",
    "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
    "contract_fingerprint_sha256": "f86a00efbf7756b7421b863bbb16c340db6b423640f63a3257d46c1af49eb55e",
    "candidate_id_sha256": args.candidate_id_sha256,
    "geometry_identity_sha256": args.geometry_identity_sha256,
    "config_sha256": sha(args.config),
    "gds_sha256": sha(args.gds),
    "manifest_sha256": sha(args.manifest),
    "calibre_blocking_violations": 0,
    "source_files_unchanged": True,
    "simulator_action_taken": False,
}
(out / "CALIBRE_ZERO_BLOCKING_RECEIPT.json").write_text(
    json.dumps(receipt), encoding="utf-8"
)
'''


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _empty_csv_keep_header(path: Path) -> None:
    with path.open(newline="", encoding="utf-8") as handle:
        fields = list(csv.DictReader(handle).fieldnames or [])
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=fields).writeheader()


def _file_record(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
