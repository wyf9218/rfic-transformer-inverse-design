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
from rfic_transformer_inverse_design.campaigns.broadband56_gds_identity import (
    GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_broadband56_v2_calibre_batch.py"


def test_calibre_batch_preserves_pass_and_failure(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _run(fixture)
    assert result.returncode == 0, result.stderr

    out = fixture["out_dir"]
    receipt = json.loads((out / "CALIBRE_BATCH_ROLE_RECEIPT.json").read_text())
    assert receipt["overall_status"] == "PASS"
    assert receipt["submitted_count"] == 2
    assert receipt["calibre_pass_count"] == 1
    assert receipt["calibre_fail_count"] == 1
    assert receipt["failed_candidates_counted_as_accepted"] is False
    assert receipt["simulator_action_taken"] is True
    delegate_input = Path(receipt["delegate_input_index"]["path"])
    assert delegate_input.name == "CALIBRE_DELEGATE_INPUT_INDEX.csv"
    delegate_rows = _read_csv(delegate_input)
    assert all(
        row["gds_timestamp_normalization_algorithm"]
        == GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM
        for row in delegate_rows
    )
    assert "gds_timestamp_normalization_algorithm" not in _csv_fields(
        fixture["input_index"]
    )

    passed = _read_csv(out / "CALIBRE_PASS_INDEX.csv")
    failed = _read_csv(out / "CALIBRE_FAILURE_INDEX.csv")
    evidence = _read_csv(out / "CALIBRE_EVIDENCE_INDEX.csv")
    assert [row["candidate_id_sha256"] for row in passed] == ["a" * 64]
    assert [row["candidate_id_sha256"] for row in failed] == ["f" * 64]
    assert {row["overall_status"] for row in evidence} == {"PASS", "FAIL"}
    assert passed[0]["gds_physical_identity_status"] == "PASS"
    assert passed[0]["blocking_drc_violation_count"] == "0"


def test_calibre_batch_is_no_clobber(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    first = _run(fixture)
    assert first.returncode == 0, first.stderr
    receipt = fixture["out_dir"] / "CALIBRE_BATCH_ROLE_RECEIPT.json"
    before = receipt.read_bytes()

    second = _run(fixture)
    assert second.returncode == 2
    assert "no-clobber" in second.stderr
    assert receipt.read_bytes() == before


def test_calibre_batch_propagates_an_empty_gds_partition(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _empty_csv_keep_header(fixture["input_index"])
    input_receipt = json.loads(fixture["input_receipt"].read_text())
    input_receipt["pass_index"] = _record(fixture["input_index"])
    fixture["input_receipt"].write_text(json.dumps(input_receipt))

    result = _run(fixture)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(
        (fixture["out_dir"] / "CALIBRE_BATCH_ROLE_RECEIPT.json").read_text()
    )
    assert receipt["submitted_count"] == 0
    assert receipt["calibre_pass_count"] == 0
    assert receipt["calibre_fail_count"] == 0
    assert receipt["simulator_action_taken"] is False


def _fixture(root: Path) -> dict[str, Path]:
    input_index = root / "gds_pass_index.csv"
    _write_csv(
        input_index,
        [
            {
                "candidate_id_sha256": "a" * 64,
                "candidate_geometry_identity_sha256": "1" * 64,
                "gds_physical_identity_status": "PASS",
                "gds_path": "/tmp/a.gds",
                "gds_timestamp_normalized_sha256": "b" * 64,
                "gds_timestamp_normalized_sha256_algorithm": (
                    GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM
                ),
                "geometry_audit_path": "/tmp/a_geometry_audit.json",
            },
            {
                "candidate_id_sha256": "f" * 64,
                "candidate_geometry_identity_sha256": "2" * 64,
                "gds_physical_identity_status": "PASS",
                "gds_path": "/tmp/f.gds",
                "gds_timestamp_normalized_sha256": "c" * 64,
                "gds_timestamp_normalized_sha256_algorithm": (
                    GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM
                ),
                "geometry_audit_path": "/tmp/f_geometry_audit.json",
            },
        ],
    )
    input_receipt = root / "GDS_PHYSICAL_IDENTITY_BATCH_ROLE_RECEIPT.json"
    input_receipt.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "stage": "GOLDEN",
                "pass_index": _record(input_index),
            }
        )
    )
    delegate = root / "fake_calibre_delegate.py"
    delegate.write_text(_fake_delegate_source())
    archive = root / "foundry.tar.gz"
    archive.write_bytes(b"foundry-archive")
    deck = root / "deck.svrf"
    deck.write_text("foundry-deck")
    guide = root / "guide.txt"
    guide.write_text("foundry-guide")
    manifest = root / "PRIVATE_BACKEND_IDENTITY_MANIFEST.json"
    manifest.write_text(
        json.dumps(
            {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "script_identities": {
                    "calibre_runner": _record(SCRIPT),
                    "calibre_batch_delegate": _record(delegate),
                },
                "runtime_identities": {
                    "python_executable": _record(Path(sys.executable).resolve()),
                    "calibre_foundry_archive": _record(archive),
                    "calibre_rule_deck": _record(deck),
                    "calibre_user_guide": _record(guide),
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
                "calibre_authorized_within_current_stage": True,
            }
        )
    )
    return {
        "input_index": input_index,
        "input_receipt": input_receipt,
        "delegate": delegate,
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
            "--full-campaign-receipt",
            str(fixture["authorization"]),
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
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--input-index-csv", required=True)
p.add_argument("--out-dir", required=True)
p.add_argument("--foundry-archive", required=True)
p.add_argument("--expected-archive-sha256", required=True)
p.add_argument("--expected-deck-sha256", required=True)
p.add_argument("--expected-user-guide-sha256", required=True)
args, _ = p.parse_known_args()
out = Path(args.out_dir)
out.mkdir(parents=True)
with Path(args.input_index_csv).open(newline="", encoding="utf-8") as h:
    reader = csv.DictReader(h)
    source_fields = set(reader.fieldnames or [])
    source = list(reader)
required_fields = {
    "candidate_id_sha256",
    "candidate_geometry_identity_sha256",
    "gds_path",
    "gds_timestamp_normalized_sha256",
    "gds_timestamp_normalization_algorithm",
    "geometry_audit_path",
}
if not required_fields.issubset(source_fields):
    raise SystemExit(9)
if any(
    row["gds_timestamp_normalization_algorithm"]
    != "gdsii-record-sha256-zero-bgnlib-bgnstr-timestamps-v1"
    for row in source
):
    raise SystemExit(10)
rows = []
for row in source:
    success = not row["candidate_id_sha256"].startswith("f")
    candidate_dir = out / "candidates" / row["candidate_id_sha256"][:16]
    candidate_dir.mkdir(parents=True)
    summary_path = candidate_dir / "drc_summary.json"
    if success:
        summary = {
            "overall_status": "PASS",
            "candidate_id_sha256": row["candidate_id_sha256"],
            "candidate_geometry_identity_sha256": row["candidate_geometry_identity_sha256"],
        }
        summary_path.write_text(json.dumps(summary))
        summary_sha = hashlib.sha256(summary_path.read_bytes()).hexdigest()
    else:
        summary_sha = ""
    rows.append({
        "candidate_id_sha256": row["candidate_id_sha256"],
        "candidate_geometry_identity_sha256": row["candidate_geometry_identity_sha256"],
        "drc_summary_path": str(summary_path) if success else "",
        "drc_summary_sha256": summary_sha,
        "drc_violation_count": "0" if success else "4",
        "blocking_drc_violation_count": "0" if success else "4",
        "documented_warning_count": "0",
        "overall_status": "PASS" if success else "FAIL",
        "error": "" if success else "synthetic blocking violations",
    })
index = out / "drc_index.csv"
with index.open("w", newline="", encoding="utf-8") as h:
    w = csv.DictWriter(h, fieldnames=list(rows[0]))
    w.writeheader(); w.writerows(rows)
pass_count = sum(row["overall_status"] == "PASS" for row in rows)
archive = Path(args.foundry_archive)
summary = {
    "schema": "tsmc65_calibre_macro_ip_back_end_drc_batch_v1",
    "overall_status": "PASS" if pass_count == len(rows) else "FAIL",
    "candidate_count": len(rows),
    "pass_count": pass_count,
    "fail_count": len(rows) - pass_count,
    "foundry_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    "foundry_source_deck_sha256": args.expected_deck_sha256,
    "foundry_user_guide_sha256": args.expected_user_guide_sha256,
    "drc_index_csv": str(index),
    "drc_index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
}
(out / "tsmc65_calibre_macro_drc_batch_summary.json").write_text(json.dumps(summary))
'''


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _csv_fields(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle).fieldnames or [])


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


def _record(path: Path) -> dict[str, str | int]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha(path),
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
