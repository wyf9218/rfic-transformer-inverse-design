from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import gdstk

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
    GEOMETRY_FIELDS,
    canonical_geometry_sha256,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
)
from rfic_transformer_inverse_design.campaigns import broadband56_gds_identity


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_broadband56_v2_candidate_gds_index_batch.py"


def test_candidate_gds_index_binds_exact_cadence_artifact(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _run(fixture)
    assert result.returncode == 0, result.stderr

    out = fixture["out_dir"]
    receipt = json.loads(
        (out / "CANDIDATE_GDS_INDEX_BATCH_ROLE_RECEIPT.json").read_text()
    )
    assert receipt["overall_status"] == "PASS"
    assert receipt["expected_count"] == 1
    assert receipt["simulator_action_taken"] is False
    index = _read_csv(out / "candidate_bound_cadence_gds_index.csv")
    assert len(index) == 1
    assert index[0]["candidate_id_sha256"] == fixture["geometry_sha"]
    assert Path(index[0]["gds_path"]).name == "transformer_layout_cadpins.gds"
    audit = json.loads(Path(index[0]["geometry_audit_path"]).read_text())
    assert audit["overall_status"] == "PASS"
    assert audit["gds_sha256"] == index[0]["gds_sha256"]


def test_candidate_gds_index_rejects_dataset_identity_drift(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    rows = _read_csv(fixture["dataset_rows"])
    rows[0]["queue__candidate_geometry_identity_sha256"] = "f" * 64
    _write_csv(fixture["dataset_rows"], rows)
    _refresh_cadence_receipt(fixture)

    result = _run(fixture)
    assert result.returncode == 2
    assert "dataset row candidate or geometry mismatch" in result.stderr
    assert not (
        fixture["out_dir"] / "CANDIDATE_GDS_INDEX_BATCH_ROLE_RECEIPT.json"
    ).exists()


def test_candidate_gds_index_is_no_clobber(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    first = _run(fixture)
    assert first.returncode == 0, first.stderr
    receipt = fixture["out_dir"] / "CANDIDATE_GDS_INDEX_BATCH_ROLE_RECEIPT.json"
    before = receipt.read_bytes()

    second = _run(fixture)
    assert second.returncode == 2
    assert "no-clobber" in second.stderr
    assert receipt.read_bytes() == before


def test_candidate_gds_index_propagates_an_empty_cadence_partition(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    _empty_csv_keep_header(Path(fixture["candidate"]))
    _empty_csv_keep_header(Path(fixture["dataset_rows"]))
    cadence_receipt = json.loads(
        Path(fixture["cadence_receipt"]).read_text(encoding="utf-8")
    )
    cadence_receipt["cadence_pass_count"] = 0
    cadence_receipt["pass_candidate_queue"] = _record(
        Path(fixture["candidate"])
    )
    cadence_receipt["pass_dataset_rows"] = _record(
        Path(fixture["dataset_rows"])
    )
    Path(fixture["cadence_receipt"]).write_text(
        json.dumps(cadence_receipt), encoding="utf-8"
    )

    result = _run(fixture)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(
        (
            Path(fixture["out_dir"])
            / "CANDIDATE_GDS_INDEX_BATCH_ROLE_RECEIPT.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["expected_count"] == 0
    assert receipt["simulator_action_taken"] is False
    assert _read_csv(
        Path(fixture["out_dir"]) / "candidate_bound_cadence_gds_index.csv"
    ) == []


def test_candidate_runner_metadata_includes_provenance_hashes() -> None:
    source = (ROOT / "scripts" / "run_candidate_queue_dataset.py").read_text()
    assert '"candidate_id_sha256"' in source
    assert '"candidate_geometry_identity_sha256"' in source


def _fixture(root: Path) -> dict[str, Path | str]:
    values = {
        "primary_outer_width_um": 260.0,
        "primary_outer_height_um": 262.0,
        "secondary_outer_width_um": 240.0,
        "secondary_outer_height_um": 242.0,
        "line_width_um": 6.5,
        "primary_terminal_y_span_um": 100.0,
        "secondary_terminal_y_span_um": 102.0,
        "offset_um": 3.0,
        "primary_feed_extension_um": 150.0,
        "secondary_feed_extension_um": 152.0,
    }
    geometry_sha = canonical_geometry_sha256(values)
    candidate_id = f"b56v2_{geometry_sha[:16]}"
    candidate = root / "candidate.csv"
    candidate_row = {
        "candidate_id": candidate_id,
        "candidate_id_sha256": geometry_sha,
        "candidate_geometry_identity_sha256": geometry_sha,
        "campaign_id": CAMPAIGN_ID,
        "campaign_contract_fingerprint": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "campaign_phase": "GOLDEN",
        "acquisition_source": "base_space_filling",
        "geometry_id": geometry_sha,
        "geometry_sha256": geometry_sha,
        **{f"geom__{name}": values[name] for name in GEOMETRY_FIELDS},
    }
    _write_csv(candidate, [candidate_row])

    dataset_dir = root / "dataset"
    evaluation = "evaluation_001"
    evaluation_dir = dataset_dir / "evaluations" / evaluation
    layout_dir = evaluation_dir / "layout"
    streamout_dir = evaluation_dir / "streamout"
    layout_dir.mkdir(parents=True)
    streamout_dir.mkdir()
    direct_gds = layout_dir / "transformer_layout.gds"
    cadence_gds = streamout_dir / "transformer_layout_cadpins.gds"
    library = gdstk.Library()
    cell = library.new_cell("TRANSFORMER")
    cell.add(gdstk.rectangle((0, 0), (10, 10), layer=126, datatype=0))
    library.write_gds(str(direct_gds))
    shutil.copy2(direct_gds, cadence_gds)
    (layout_dir / "transformer_layout.layout.json").write_text(
        json.dumps({"top_cell": "TRANSFORMER"}), encoding="utf-8"
    )
    (evaluation_dir / "summary.json").write_text(
        json.dumps({"overall_status": "PASS", "geometry": values}),
        encoding="utf-8",
    )
    dataset_rows = dataset_dir / "dataset_rows.csv"
    dataset_row = {
        "evaluation": evaluation,
        "ok": "true",
        "queue__candidate_id": candidate_id,
        "queue__candidate_id_sha256": geometry_sha,
        "queue__candidate_geometry_identity_sha256": geometry_sha,
        **{f"geom__{name}": values[name] for name in GEOMETRY_FIELDS},
    }
    _write_csv(dataset_rows, [dataset_row])

    manifest = root / "PRIVATE_BACKEND_IDENTITY_MANIFEST.json"
    manifest.write_text(
        json.dumps(
            {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "script_identities": {
                    "candidate_gds_index_builder": _record(SCRIPT),
                    "gds_physical_identity_module": _record(
                        Path(broadband56_gds_identity.__file__).resolve()
                    ),
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
                "backend_identity_manifest": {"sha256": _sha(manifest)},
            }
        ),
        encoding="utf-8",
    )
    cadence_receipt = root / "CADENCE_STREAMOUT_BATCH_ROLE_RECEIPT.json"
    fixture: dict[str, Path | str] = {
        "candidate": candidate,
        "dataset_dir": dataset_dir,
        "dataset_rows": dataset_rows,
        "manifest": manifest,
        "authorization": authorization,
        "cadence_receipt": cadence_receipt,
        "out_dir": root / "out",
        "geometry_sha": geometry_sha,
    }
    _refresh_cadence_receipt(fixture)
    return fixture


def _refresh_cadence_receipt(fixture: dict[str, Path | str]) -> None:
    cadence_receipt = Path(fixture["cadence_receipt"])
    cadence_receipt.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "stage": "GOLDEN",
                "cadence_pass_count": 1,
                "backend_identity_manifest": _record(Path(fixture["manifest"])),
                "full_campaign_authorization_receipt": _record(
                    Path(fixture["authorization"])
                ),
                "pass_candidate_queue": _record(Path(fixture["candidate"])),
                "pass_dataset_rows": _record(Path(fixture["dataset_rows"])),
                "pass_dataset_dir": str(Path(fixture["dataset_dir"]).resolve()),
            }
        ),
        encoding="utf-8",
    )


def _run(fixture: dict[str, Path | str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--stage",
            "GOLDEN",
            "--input-role-receipt",
            str(fixture["cadence_receipt"]),
            "--backend-identity-manifest",
            str(fixture["manifest"]),
            "--full-campaign-receipt",
            str(fixture["authorization"]),
            "--out-dir",
            str(fixture["out_dir"]),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _empty_csv_keep_header(path: Path) -> None:
    with path.open(newline="", encoding="utf-8") as handle:
        fields = list(csv.DictReader(handle).fieldnames or [])
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=fields).writeheader()


def _record(path: Path) -> dict[str, object]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha(resolved),
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
