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
SCRIPT = ROOT / "scripts" / "run_broadband56_v2_cadence_streamout_batch.py"


def test_cadence_batch_preserves_pass_and_failure(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, candidate_ids=("a" * 64, "f" * 64))
    result = _run(fixture)
    assert result.returncode == 0, result.stderr

    out = fixture["out_dir"]
    receipt = json.loads(
        (out / "CADENCE_STREAMOUT_BATCH_ROLE_RECEIPT.json").read_text()
    )
    assert receipt["overall_status"] == "PASS"
    assert receipt["submitted_count"] == 2
    assert receipt["cadence_pass_count"] == 1
    assert receipt["cadence_fail_count"] == 1
    assert receipt["candidate_failures_counted_as_accepted"] is False
    assert receipt["simulator_action_taken"] is True

    passed = _read_csv(out / "CADENCE_PASS_CANDIDATE_QUEUE.csv")
    failed = _read_csv(out / "CADENCE_STREAMOUT_FAILURE_INDEX.csv")
    evidence = _read_csv(out / "CADENCE_STREAMOUT_DELEGATE_EVIDENCE_INDEX.csv")
    dataset = _read_csv(out / "cadence_pass_dataset" / "dataset_rows.csv")
    assert [row["candidate_id_sha256"] for row in passed] == ["a" * 64]
    assert [row["candidate_id_sha256"] for row in failed] == ["f" * 64]
    assert len(evidence) == 2
    assert len(dataset) == 1
    copied_gds = list(
        (out / "cadence_pass_dataset").glob(
            "evaluations/*/streamout/transformer_layout_cadpins.gds"
        )
    )
    assert len(copied_gds) == 1
    source_gds = Path(evidence[0]["source_gds_path"])
    assert copied_gds[0].stat().st_ino == source_gds.stat().st_ino
    assert _sha(copied_gds[0]) == _sha(source_gds)


def test_cadence_batch_rejects_duplicate_geometry_before_delegate(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, candidate_ids=("a" * 64, "b" * 64))
    rows = _read_csv(fixture["candidate_queue"])
    rows[1]["geometry_sha256"] = rows[0]["geometry_sha256"]
    _write_csv(fixture["candidate_queue"], rows)
    receipt = json.loads(fixture["input_receipt"].read_text())
    receipt["candidate_queue"] = _record(fixture["candidate_queue"])
    fixture["input_receipt"].write_text(json.dumps(receipt))

    result = _run(fixture)
    assert result.returncode == 2
    assert "duplicated" in result.stderr
    assert not fixture["out_dir"].exists()


def test_cadence_batch_is_no_clobber(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, candidate_ids=("a" * 64,))
    first = _run(fixture)
    assert first.returncode == 0, first.stderr
    receipt_path = fixture["out_dir"] / "CADENCE_STREAMOUT_BATCH_ROLE_RECEIPT.json"
    before = receipt_path.read_bytes()

    second = _run(fixture)
    assert second.returncode == 2
    assert "no-clobber" in second.stderr
    assert receipt_path.read_bytes() == before


def _fixture(root: Path, *, candidate_ids: tuple[str, ...]) -> dict[str, Path]:
    config = root / "config.yaml"
    config.write_text("frozen: true\n")
    delegate = root / "fake_cadence_delegate.py"
    delegate.write_text(_fake_delegate_source())
    rows = []
    for index, candidate_id in enumerate(candidate_ids, start=1):
        rows.append(
            {
                "candidate_id": f"candidate_{index}",
                "candidate_id_sha256": candidate_id,
                "candidate_geometry_identity_sha256": candidate_id,
                "campaign_id": CAMPAIGN_ID,
                "campaign_contract_fingerprint": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "campaign_phase": "PHASE_A",
                "acquisition_source": "base_space_filling",
                "geometry_sha256": f"{index:x}" * 64,
                "analytical_status": "PASS",
                "topology_status": "PASS",
                "geom__primary_outer_width_um": str(200 + index),
            }
        )
    candidate_queue = root / "candidate_queue.csv"
    _write_csv(candidate_queue, rows)
    input_receipt = root / "ACQUISITION_RECEIPT.json"
    input_receipt.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "candidate_queue": _record(candidate_queue),
            }
        )
    )
    manifest = root / "PRIVATE_BACKEND_IDENTITY_MANIFEST.json"
    manifest.write_text(
        json.dumps(
            {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "script_identities": {
                    "cadence_streamout_runner": _record(SCRIPT),
                    "cadence_streamout_delegate": _record(delegate),
                },
                "runtime_identities": {
                    "python_executable": _record(Path(sys.executable).resolve()),
                    "private_configuration": _record(config),
                },
            }
        )
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
                "backend_identity_manifest": {"sha256": _sha(manifest)},
                "cadence_authorized_within_current_stage": True,
            }
        )
    )
    return {
        "config": config,
        "delegate": delegate,
        "candidate_queue": candidate_queue,
        "input_receipt": input_receipt,
        "manifest": manifest,
        "authorization": authorization,
        "out_dir": root / "out",
    }


def _run(fixture: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--stage",
            "GOLDEN",
            "--input-role-receipt",
            str(fixture["input_receipt"]),
            "--backend-identity-manifest",
            str(fixture["manifest"]),
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
    return r'''import argparse
import csv
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--candidate-csv", required=True)
p.add_argument("--out-dir", required=True)
args, _ = p.parse_known_args()
out = Path(args.out_dir)
out.mkdir(parents=True)
with Path(args.candidate_csv).open(newline="", encoding="utf-8") as h:
    rows = list(csv.DictReader(h))
shards = []
for index, row in enumerate(rows):
    shard = out / "parallel_shards" / f"shard_{index:03d}"
    shard.mkdir(parents=True)
    success = not row["candidate_id_sha256"].startswith("f")
    dataset = shard / "dataset_rows.csv"
    fields = ["queue__candidate_id_sha256", "ok", "evaluation"]
    evaluation = f"eval_{index:03d}"
    with dataset.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "queue__candidate_id_sha256": row["candidate_id_sha256"],
            "ok": "true" if success else "false",
            "evaluation": evaluation,
        })
    if success:
        evaluation_dir = shard / "evaluations" / evaluation
        (evaluation_dir / "streamout").mkdir(parents=True)
        (evaluation_dir / "streamout" / "transformer_layout_cadpins.gds").write_bytes(
            f"gds-{index}".encode("ascii")
        )
        (evaluation_dir / "summary.json").write_text('{"overall_status":"PASS"}')
        (evaluation_dir / "layout").mkdir()
        (evaluation_dir / "layout" / "geometry.json").write_text("{}")
    shard_summary = {
        "overall_status": "PASS" if success else "FAIL",
        "run_emx": False,
        "create_only": False,
        "cadence_streamout_only": True,
        "cadence_streamout_output_contract": {
            "checked": True,
            "valid_candidate_bound_gds_count": 1 if success else 0,
            "touchstone_file_count": 0,
        },
    }
    summary_path = shard / "candidate_queue_dataset_summary.json"
    summary_path.write_text(json.dumps(shard_summary))
    shards.append({
        "index": index,
        "returncode": 0,
        "overall_status": shard_summary["overall_status"],
        "out_dir": str(shard),
        "summary_path": str(summary_path),
        "dataset_rows_csv": str(dataset),
    })
parent = {
    "overall_status": "PASS" if all(x["overall_status"] == "PASS" for x in shards) else "FAIL",
    "input_row_count": len(rows),
    "shard_count": len(shards),
    "shards": shards,
}
(out / "parallel_candidate_queue_dataset_summary.json").write_text(json.dumps(parent))
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


def _record(path: Path) -> dict[str, str | int]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha(path),
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
