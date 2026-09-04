from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
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
from rfic_transformer_inverse_design.core import (
    FoundryLayoutSpec,
    PowerLine8PortSpec,
    default_run_config,
)
from rfic_transformer_inverse_design.layout import export_transformer_layout
from rfic_transformer_inverse_design.layout import foundry_audit as foundry_audit_module


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_broadband56_v2_cadence_streamout_batch.py"
CONTRACT = ROOT / "docs" / "research" / "FOUNDRY_LAYOUT_AUDIT_CONTRACT.json"


def test_cadence_batch_produces_audit_before_pass_partition(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, candidate_count=2, delegate_fail_indexes={1})
    result = _run(fixture)
    assert result.returncode == 0, result.stderr

    out = Path(fixture["out_dir"])
    receipt = json.loads(
        (out / "CADENCE_STREAMOUT_BATCH_ROLE_RECEIPT.json").read_text()
    )
    assert receipt["overall_status"] == "PASS"
    assert receipt["submitted_count"] == 2
    assert receipt["cadence_pass_count"] == 1
    assert receipt["cadence_fail_count"] == 1
    assert receipt["downstream_calibre_authorized"] is True

    passed = _read_csv(out / "CADENCE_PASS_CANDIDATE_QUEUE.csv")
    evidence = _read_csv(out / "CADENCE_STREAMOUT_DELEGATE_EVIDENCE_INDEX.csv")
    assert [row["candidate_id_sha256"] for row in passed] == [fixture["first_sha"]]
    audit_path = Path(evidence[0]["source_foundry_layout_audit_path"])
    assert audit_path.is_file() and audit_path.stat().st_size > 0
    audit = json.loads(audit_path.read_text())
    assert audit["audit_boundary"] == (
        "POST_CADENCE_STREAMOUT_PRE_CADENCE_PASS_PARTITION"
    )
    assert audit["overall_status"] == "PASS"
    assert audit["gds_sha256"] == evidence[0]["source_gds_sha256"]

    copied_gds = list(
        (out / "cadence_pass_dataset").glob(
            "evaluations/*/streamout/transformer_layout_cadpins.gds"
        )
    )
    assert len(copied_gds) == 1
    source_gds = Path(evidence[0]["source_gds_path"])
    assert copied_gds[0].stat().st_ino == source_gds.stat().st_ino
    assert _sha(copied_gds[0]) == _sha(source_gds)


def test_zero_cadence_pass_is_terminal_upstream_failure(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, candidate_count=1, invalid_source_audit=True)
    result = _run(fixture)

    assert result.returncode == 2
    out = Path(fixture["out_dir"])
    receipt = json.loads(
        (out / "CADENCE_STREAMOUT_BATCH_ROLE_RECEIPT.json").read_text()
    )
    failures = _read_csv(out / "CADENCE_STREAMOUT_FAILURE_INDEX.csv")
    assert receipt["overall_status"] == "FAIL"
    assert receipt["cadence_pass_count"] == 0
    assert receipt["cadence_fail_count"] == 1
    assert receipt["downstream_calibre_authorized"] is False
    assert receipt["upstream_terminal_failure"]["calibre_invocation_forbidden"] is True
    assert "source foundry-layout audit is not PASS" in failures[0]["error"]
    assert not (out / "roles" / "05_calibre_runner").exists()


def test_zero_cadence_pass_is_terminal_partition_for_production(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, candidate_count=1, invalid_source_audit=True)
    result = _run(fixture, stage="PILOT_32")

    assert result.returncode == 0, result.stderr
    out = Path(fixture["out_dir"])
    receipt = json.loads(
        (out / "CADENCE_STREAMOUT_BATCH_ROLE_RECEIPT.json").read_text()
    )
    failures = _read_csv(out / "CADENCE_STREAMOUT_FAILURE_INDEX.csv")
    assert receipt["overall_status"] == "PASS"
    assert receipt["decision"] == (
        "TERMINAL_PARTITION_CANDIDATE_BOUND_CADENCE_STREAMOUT"
    )
    assert receipt["cadence_pass_count"] == 0
    assert receipt["cadence_fail_count"] == 1
    assert receipt["all_candidates_rejected_before_calibre"] is True
    assert receipt["downstream_calibre_authorized"] is True
    assert receipt["upstream_terminal_failure"] is None
    assert len(failures) == 1
    assert _read_csv(out / "CADENCE_PASS_CANDIDATE_QUEUE.csv") == []


def test_cadence_batch_rejects_duplicate_geometry_before_delegate(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, candidate_count=2)
    candidate_queue = Path(fixture["candidate_queue"])
    rows = _read_csv(candidate_queue)
    rows[1]["geometry_sha256"] = rows[0]["geometry_sha256"]
    _write_csv(candidate_queue, rows)
    input_receipt = Path(fixture["input_receipt"])
    receipt = json.loads(input_receipt.read_text())
    receipt["candidate_queue"] = _record(candidate_queue)
    input_receipt.write_text(json.dumps(receipt))

    result = _run(fixture)
    assert result.returncode == 2
    assert "duplicated" in result.stderr
    assert not Path(fixture["out_dir"]).exists()


def test_cadence_batch_is_no_clobber(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, candidate_count=1)
    first = _run(fixture)
    assert first.returncode == 0, first.stderr
    receipt_path = (
        Path(fixture["out_dir"]) / "CADENCE_STREAMOUT_BATCH_ROLE_RECEIPT.json"
    )
    before = receipt_path.read_bytes()

    second = _run(fixture)
    assert second.returncode == 2
    assert "no-clobber" in second.stderr
    assert receipt_path.read_bytes() == before


def _fixture(
    root: Path,
    *,
    candidate_count: int,
    delegate_fail_indexes: set[int] | None = None,
    invalid_source_audit: bool = False,
) -> dict[str, Path | str]:
    template_dir, geometry = _build_real_foundry_layout(root)
    if invalid_source_audit:
        source_path = template_dir / "foundry_layout_source_audit.json"
        source = json.loads(source_path.read_text())
        source["overall_status"] = "FAIL"
        source_path.write_text(json.dumps(source))

    config = root / "config.yaml"
    config.write_text(
        "emx:\n"
        "  foundry_layout:\n"
        "    enabled: true\n"
        "    manufacturing_grid_um: 0.005\n"
        "    power_line_stitch_pad_depth_um: 6.0\n"
        "    shield_strap_width_um: 10.0\n"
        "    shield_strap_pitch_um: 20.0\n"
    )
    delegate = root / "fake_cadence_delegate.py"
    delegate.write_text(
        _fake_delegate_source(delegate_fail_indexes or set()), encoding="utf-8"
    )
    rows: list[dict[str, str]] = []
    first_sha = ""
    for index in range(candidate_count):
        values = dict(geometry)
        values["offset_um"] += float(index)
        digest = canonical_geometry_sha256(values)
        if index == 0:
            first_sha = digest
        rows.append(
            {
                "candidate_id": f"candidate_{index + 1}",
                "candidate_id_sha256": digest,
                "candidate_geometry_identity_sha256": digest,
                "campaign_id": CAMPAIGN_ID,
                "campaign_contract_fingerprint": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "campaign_phase": "PHASE_A",
                "acquisition_source": "base_space_filling",
                "geometry_sha256": digest,
                "analytical_status": "PASS",
                "topology_status": "PASS",
                **{f"geom__{name}": str(value) for name, value in values.items()},
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
                    "foundry_layout_audit_producer": _record(
                        Path(foundry_audit_module.__file__).resolve()
                    ),
                },
                "runtime_identities": {
                    "python_executable": _record(Path(sys.executable).resolve()),
                    "private_configuration": _record(config),
                    "foundry_layout_audit_contract": _record(CONTRACT),
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
        "first_sha": first_sha,
    }


def _build_real_foundry_layout(
    root: Path, *, fractional_geometry: bool = False
) -> tuple[Path, dict[str, float]]:
    cfg = default_run_config("1t1t")
    cfg = replace(
        cfg,
        emx=replace(
            cfg.emx,
            port_mode="single_ended_shield_grounded",
            differential_port_pairs=((0, 1), (2, 3)),
            power_line_8port=PowerLine8PortSpec(
                enabled=True,
                touchstone_mode="signal_4_grounded_aux",
                bridge_width_um=10.0,
                port_map=("P001", "P002", "P003", "P004"),
                role_labels=(
                    ("primary_top", "P001"),
                    ("primary_bottom", "P002"),
                    ("secondary_top", "P003"),
                    ("secondary_bottom", "P004"),
                    ("left_power_top", "P005"),
                    ("left_power_bottom", "P006"),
                    ("right_power_top", "P007"),
                    ("right_power_bottom", "P008"),
                ),
            ),
            foundry_layout=FoundryLayoutSpec(enabled=True),
        ),
        bounds=replace(
            cfg.bounds,
            primary=replace(
                cfg.bounds.primary,
                center_tap=True,
                vdd_bar=replace(
                    cfg.bounds.primary.vdd_bar,
                    enabled=True,
                    bar_layer=cfg.emx.ap_layer,
                    width_um=10.0,
                    offset_um=12.0,
                ),
            ),
            secondary=replace(
                cfg.bounds.secondary,
                center_tap=True,
                vdd_bar=replace(
                    cfg.bounds.secondary.vdd_bar,
                    enabled=True,
                    bar_layer=cfg.emx.m9_layer,
                    width_um=10.0,
                    offset_um=12.0,
                ),
            ),
        ),
    )
    geometry = cfg.bounds.midpoint()
    if fractional_geometry:
        from rfic_transformer_inverse_design.core.adapter import TransformerOptimizationAdapter

        adapter = TransformerOptimizationAdapter(cfg.bounds)
        offsets = {
            "primary_outer_width_um": 0.123456,
            "primary_outer_height_um": 0.234567,
            "secondary_outer_width_um": 0.345678,
            "secondary_outer_height_um": 0.456789,
            "primary_width_um": 0.012345,
            "secondary_width_um": 0.012345,
            "offset_um": 0.123456,
        }
        geometry = adapter.from_vector(
            [
                value + offsets.get(name, 0.0)
                for name, value in zip(adapter.field_order(), adapter.to_vector(geometry))
            ]
        )
    template_dir = root / "template_layout"
    export_transformer_layout(geometry, cfg, template_dir, validate_geometry=False)
    values = {
        "primary_outer_width_um": geometry.primary.outer_width_um,
        "primary_outer_height_um": geometry.primary.outer_height_um,
        "secondary_outer_width_um": geometry.secondary.outer_width_um,
        "secondary_outer_height_um": geometry.secondary.outer_height_um,
        "line_width_um": geometry.primary.trace_width_um,
        "primary_terminal_y_span_um": geometry.primary.terminal_y_span_um,
        "secondary_terminal_y_span_um": geometry.secondary.terminal_y_span_um,
        "offset_um": geometry.offset_um,
        "primary_feed_extension_um": geometry.primary.feed_extension_um,
        "secondary_feed_extension_um": geometry.secondary.feed_extension_um,
    }
    return template_dir, values


def _run(
    fixture: dict[str, Path | str], *, stage: str = "GOLDEN"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--stage",
            stage,
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


def _fake_delegate_source(fail_indexes: set[int]) -> str:
    return f'''import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

if "--expected-config-sha256" in sys.argv:
    raise SystemExit("unsupported config hash argument reached legacy delegate")
p = argparse.ArgumentParser()
p.add_argument("--candidate-csv", required=True)
p.add_argument("--out-dir", required=True)
args, _ = p.parse_known_args()
out = Path(args.out_dir)
out.mkdir(parents=True)
template = Path(__file__).resolve().parent / "template_layout"
with Path(args.candidate_csv).open(newline="", encoding="utf-8") as h:
    rows = list(csv.DictReader(h))
shards = []
fail_indexes = {sorted(fail_indexes)!r}
for index, row in enumerate(rows):
    shard = out / "parallel_shards" / f"shard_{{index:03d}}"
    shard.mkdir(parents=True)
    success = index not in fail_indexes
    dataset = shard / "dataset_rows.csv"
    fields = ["queue__candidate_id_sha256", "ok", "evaluation"]
    evaluation = f"eval_{{index:03d}}"
    with dataset.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        w.writerow({{
            "queue__candidate_id_sha256": row["candidate_id_sha256"],
            "ok": "true" if success else "false",
            "evaluation": evaluation,
        }})
    if success:
        evaluation_dir = shard / "evaluations" / evaluation
        (evaluation_dir / "streamout").mkdir(parents=True)
        (evaluation_dir / "layout").mkdir()
        shutil.copyfile(
            template / "transformer_layout.gds",
            evaluation_dir / "streamout" / "transformer_layout_cadpins.gds",
        )
        for name in (
            "foundry_layout_source_audit.json",
            "power_line_8port_geometry.json",
        ):
            shutil.copyfile(template / name, evaluation_dir / "layout" / name)
        power_line = json.loads((template / "power_line_8port_geometry.json").read_text())
        (evaluation_dir / "summary.json").write_text(json.dumps({{
            "ok": True, "geometry_check": {{"ok": True, "errors": [], "metrics": {{}},
            "power_line_8port_geometry_audit": power_line}}
        }}))
    shard_summary = {{
        "overall_status": "PASS" if success else "FAIL",
        "run_emx": False,
        "create_only": False,
        "cadence_streamout_only": True,
        "cadence_streamout_output_contract": {{
            "checked": True,
            "valid_candidate_bound_gds_count": 1 if success else 0,
            "touchstone_file_count": 0,
        }},
    }}
    summary_path = shard / "candidate_queue_dataset_summary.json"
    summary_path.write_text(json.dumps(shard_summary))
    shards.append({{
        "index": index,
        "returncode": 0,
        "overall_status": shard_summary["overall_status"],
        "out_dir": str(shard),
        "summary_path": str(summary_path),
        "dataset_rows_csv": str(dataset),
    }})
parent = {{
    "overall_status": "PASS" if all(x["overall_status"] == "PASS" for x in shards) else "FAIL",
    "input_row_count": len(rows),
    "shard_count": len(shards),
    "shards": shards,
}}
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
