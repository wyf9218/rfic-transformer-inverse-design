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
SCRIPT = ROOT / "scripts" / "run_broadband56_v2_gds_physical_identity_batch.py"


def test_gds_identity_batch_preserves_terminal_partition(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _run(fixture)
    assert result.returncode == 0, result.stderr

    out = fixture["out_dir"]
    receipt = json.loads(
        (out / "GDS_PHYSICAL_IDENTITY_BATCH_ROLE_RECEIPT.json").read_text()
    )
    assert receipt["overall_status"] == "PASS"
    assert receipt["submitted_count"] == 2
    assert receipt["identity_pass_count"] == 1
    assert receipt["identity_fail_count"] == 1
    assert receipt["failed_candidates_counted_as_accepted"] is False
    assert receipt["delegate_return_code"] == 2
    assert receipt["simulator_action_taken"] is False

    passed = _read_csv(out / "GDS_PHYSICAL_IDENTITY_PASS_INDEX.csv")
    failed = _read_csv(out / "GDS_PHYSICAL_IDENTITY_FAILURE_INDEX.csv")
    evidence = _read_csv(out / "GDS_PHYSICAL_IDENTITY_EVIDENCE_INDEX.csv")
    assert [row["candidate_id_sha256"] for row in passed] == ["a" * 64]
    assert [row["candidate_id_sha256"] for row in failed] == ["f" * 64]
    assert {row["overall_status"] for row in evidence} == {"PASS", "FAIL"}


def test_gds_identity_batch_is_no_clobber(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    first = _run(fixture)
    assert first.returncode == 0, first.stderr
    receipt = fixture["out_dir"] / "GDS_PHYSICAL_IDENTITY_BATCH_ROLE_RECEIPT.json"
    before = receipt.read_bytes()

    second = _run(fixture)
    assert second.returncode == 2
    assert "no-clobber" in second.stderr
    assert receipt.read_bytes() == before


def test_gds_identity_batch_propagates_an_empty_cadence_partition(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    for path in (
        fixture["candidate"],
        fixture["dataset"],
        fixture["index"],
    ):
        _empty_csv_keep_header(path)
    input_receipt = json.loads(fixture["input_receipt"].read_text())
    input_receipt["expected_count"] = 0
    input_receipt["candidate_csv"] = _record(fixture["candidate"])
    input_receipt["dataset_rows_csv"] = _record(fixture["dataset"])
    input_receipt["candidate_bound_index"] = _record(fixture["index"])
    fixture["input_receipt"].write_text(json.dumps(input_receipt))

    result = _run(fixture)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(
        (
            fixture["out_dir"]
            / "GDS_PHYSICAL_IDENTITY_BATCH_ROLE_RECEIPT.json"
        ).read_text()
    )
    assert receipt["submitted_count"] == 0
    assert receipt["identity_pass_count"] == 0
    assert receipt["identity_fail_count"] == 0
    assert receipt["simulator_action_taken"] is False


def _fixture(root: Path) -> dict[str, Path]:
    candidate = root / "candidate.csv"
    dataset_dir = root / "dataset"
    dataset_dir.mkdir()
    dataset = dataset_dir / "dataset_rows.csv"
    index = root / "candidate_bound_index.csv"
    candidate_rows = []
    index_rows = []
    for position, candidate_sha in enumerate(("a" * 64, "f" * 64), start=1):
        geometry_sha = str(position) * 64
        evaluation_dir = dataset_dir / "evaluations" / f"evaluation_{position}"
        streamout_dir = evaluation_dir / "streamout"
        layout_dir = evaluation_dir / "layout"
        streamout_dir.mkdir(parents=True)
        layout_dir.mkdir()
        gds = streamout_dir / "transformer_layout_cadpins.gds"
        manifest_path = layout_dir / "transformer_layout.layout.json"
        gds.write_bytes(f"gds-{position}".encode("ascii"))
        manifest_path.write_text(
            json.dumps({"top_cell": "TRANSFORMER"}), encoding="utf-8"
        )
        candidate_rows.append(
            {
                "candidate_id": f"candidate_{position}",
                "candidate_id_sha256": candidate_sha,
                "candidate_geometry_identity_sha256": geometry_sha,
                "campaign_id": CAMPAIGN_ID,
                "campaign_contract_fingerprint": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "campaign_phase": "PHASE_A",
                "acquisition_source": "base_space_filling",
                "geometry_id": f"geometry_{position}",
                "geometry_sha256": geometry_sha,
            }
        )
        index_rows.append(
            {
                "candidate_id_sha256": candidate_sha,
                "candidate_geometry_identity_sha256": geometry_sha,
                "gds_path": str(gds),
                "gds_sha256": _sha(gds),
                "layout_manifest_sha256": _sha(manifest_path),
                "geometry_audit_path": f"/tmp/{position}.json",
            }
        )
    _write_csv(candidate, candidate_rows)
    _write_csv(
        dataset,
        [
            {"queue__candidate_id_sha256": "a" * 64},
            {"queue__candidate_id_sha256": "f" * 64},
        ],
    )
    _write_csv(index, index_rows)
    input_receipt = root / "CANDIDATE_GDS_INDEX_SUMMARY.json"
    input_receipt.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "expected_count": 2,
                "candidate_csv": _record(candidate),
                "dataset_rows_csv": _record(dataset),
                "dataset_dir": str(dataset_dir),
                "candidate_bound_index": _record(index),
            }
        )
    )
    delegate = root / "fake_gds_identity_delegate.py"
    delegate.write_text(_fake_delegate_source())
    module = root / "gds_identity_module.py"
    module.write_text("VALUE = 1\n")
    manifest = root / "PRIVATE_BACKEND_IDENTITY_MANIFEST.json"
    manifest.write_text(
        json.dumps(
            {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "script_identities": {
                    "gds_physical_identity_auditor": _record(SCRIPT),
                    "gds_physical_identity_delegate": _record(delegate),
                    "gds_physical_identity_module": _record(module),
                },
                "runtime_identities": {
                    "python_executable": _record(Path(sys.executable).resolve())
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
            }
        )
    )
    return {
        "candidate": candidate,
        "dataset": dataset,
        "index": index,
        "input_receipt": input_receipt,
        "delegate": delegate,
        "module": module,
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
    return f'''import argparse
import csv
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--input-index-csv", required=True)
p.add_argument("--out-dir", required=True)
args, _ = p.parse_known_args()
out = Path(args.out_dir)
(out / "records").mkdir(parents=True)
with Path(args.input_index_csv).open(newline="", encoding="utf-8") as h:
    source = list(csv.DictReader(h))
rows = []
pass_count = 0
for row in source:
    success = not row["candidate_id_sha256"].startswith("f")
    audit = {{
        "overall_status": "PASS" if success else "FAIL",
        "candidate_id_sha256": row["candidate_id_sha256"],
        "candidate_geometry_identity_sha256": row["candidate_geometry_identity_sha256"],
        "candidate_physical_identity_sha256": "3" * 64 if success else "",
        "error": "synthetic mismatch" if not success else "",
    }}
    audit_path = out / "records" / f"{{row['candidate_id_sha256']}}_gds_identity.json"
    audit_path.write_text(json.dumps(audit))
    digest = hashlib.sha256(audit_path.read_bytes()).hexdigest()
    rows.append({{**row,
        "gds_physical_identity_status": audit["overall_status"],
        "candidate_physical_identity_sha256": audit["candidate_physical_identity_sha256"],
        "gds_physical_identity_audit_path": str(audit_path),
        "gds_physical_identity_audit_sha256": digest,
    }})
    pass_count += int(success)
index = out / "gds_physical_identity_audited_index.csv"
with index.open("w", newline="", encoding="utf-8") as h:
    w = csv.DictWriter(h, fieldnames=list(rows[0]))
    w.writeheader(); w.writerows(rows)
summary = {{
    "overall_status": "PASS" if pass_count == len(rows) else "FAIL",
    "campaign_id": "{CAMPAIGN_ID}",
    "contract_fingerprint_sha256": "{SCIENTIFIC_CONTRACT_FINGERPRINT}",
    "expected_count": len(rows),
    "pass_count": pass_count,
    "fail_count": len(rows) - pass_count,
    "audited_gds_index_csv": {{
        "path": str(index),
        "size_bytes": index.stat().st_size,
        "sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
    }},
}}
(out / "GDS_PHYSICAL_IDENTITY_AUDIT_SUMMARY.json").write_text(json.dumps(summary))
raise SystemExit(0 if pass_count == len(rows) else 2)
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


def _record(path: Path) -> dict[str, str | int]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha(path),
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
