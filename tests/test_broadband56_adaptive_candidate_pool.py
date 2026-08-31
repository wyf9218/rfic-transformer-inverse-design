from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.api import (
    TransformerOptimizationAdapter,
    load_run_config,
)
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
    GEOMETRY_FIELDS,
    canonical_geometry_bounds,
    canonical_geometry_sha256,
    contract_fingerprint,
)
from rfic_transformer_inverse_design.campaigns.broadband56_geometry_coverage import (
    geometry_bounds_payload,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "configs" / "broadband56_real_emx_balanced200k_tsmc65_v2.json"
TEMPLATE = ROOT / "configs" / "mars_s4p_grounded_powerline_broadband56_balanced200k_v2_template.yaml"


class _RoundSpecFixture:
    phase = "PHASE_B"
    phase_round_index = 1
    global_round_index = 1
    accepted_start = 3
    accepted_target = 8
    batch_size = 5
    round_id = "phase_b_round_01_000003_000008"

    def as_dict(self) -> dict[str, object]:
        return {
            "round_id": self.round_id,
            "phase": self.phase,
            "phase_round_index": self.phase_round_index,
            "global_round_index": self.global_round_index,
            "accepted_start": self.accepted_start,
            "accepted_target": self.accepted_target,
            "batch_size": self.batch_size,
            "source_quotas": {
                "underfilled_response_repair": 3,
                "ensemble_uncertainty": 1,
                "maximin_geometry_exploration": 1,
            },
            "fallback_source_quotas": {"maximin_geometry_exploration": 5},
        }


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_evidence(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "exists": True,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    assert rows
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_round_fixture(root: Path) -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    fingerprint = str(contract.get("contract_fingerprint_sha256") or contract_fingerprint(contract))
    phase_a_builder = _load_script("build_broadband56_phase_a_queue")
    seed_dir = root / "accepted_seed_rows"
    assert (
        phase_a_builder.main(
            [
                "--contract",
                str(CONTRACT),
                "--config",
                str(TEMPLATE),
                "--out-dir",
                str(seed_dir),
                "--count",
                "3",
                "--seed",
                "2026082803",
            ]
        )
        == 0
    )
    with (seed_dir / "broadband56_candidate_queue.csv").open(
        newline="",
        encoding="utf-8",
    ) as handle:
        seed_rows = list(csv.DictReader(handle))

    accepted_rows: list[dict[str, object]] = []
    for row in seed_rows:
        accepted = dict(row)
        accepted["campaign_contract_fingerprint"] = fingerprint
        accepted["calibre_blocking_violations"] = 0
        for field in (
            "analytical_status",
            "topology_status",
            "cadence_gds_status",
            "calibre_status",
            "emx_status",
            "s4p_status",
            "feature_extraction_status",
        ):
            accepted[field] = "PASS"
        accepted_rows.append(accepted)
    accepted_path = root / "accepted_geometries.csv"
    _write_csv(accepted_path, accepted_rows)

    config = load_run_config(TEMPLATE)
    adapter = TransformerOptimizationAdapter(config.bounds)
    bounds = canonical_geometry_bounds(adapter)
    bounds_path = root / "GEOMETRY_BOUNDS_FROZEN.json"
    _write_json(
        bounds_path,
        geometry_bounds_payload(
            bounds=bounds,
            contract_fingerprint_sha256=fingerprint,
        ),
    )

    round_spec = _RoundSpecFixture()
    round_dir = root / "round"
    round_dir.mkdir()
    round_contract_path = round_dir / "ADAPTIVE_ROUND_CONTRACT.json"
    _write_json(
        round_contract_path,
        {
            "overall_status": "PASS",
            "decision": "USE_FALLBACK_MAXIMIN",
            "campaign_id": CAMPAIGN_ID,
            "campaign_contract_fingerprint": fingerprint,
            "acquisition_mode": "FALLBACK_MAXIMIN",
            "round": round_spec.as_dict(),
            "current_accepted": round_spec.accepted_start,
            "raw_selection_count": round_spec.batch_size,
            "preceding_real_emx_audit": {
                "accepted_geometries_path": str(accepted_path.resolve()),
                "accepted_geometries_sha256": _sha256(accepted_path),
                "geometry_bounds_path": str(bounds_path.resolve()),
                "geometry_bounds_sha256": _sha256(bounds_path),
            },
        },
    )
    round_receipt_path = round_dir / "ADAPTIVE_ROUND_RECEIPT.json"
    _write_json(
        round_receipt_path,
        {
            "overall_status": "PASS",
            "decision": "STAGE_ADAPTIVE_CANDIDATES",
            "campaign_id": CAMPAIGN_ID,
            "campaign_contract_fingerprint": fingerprint,
            "outputs": {
                "adaptive_round_contract": _file_evidence(round_contract_path),
            },
        },
    )
    return {
        "fingerprint": fingerprint,
        "round_dir": round_dir,
        "round_contract_path": round_contract_path,
        "accepted_path": accepted_path,
        "accepted_hashes": {str(row["geometry_sha256"]) for row in accepted_rows},
    }


def _patch_small_round(module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "ADAPTIVE_BATCH_SIZE", 5)
    monkeypatch.setattr(module, "MINIMUM_CANDIDATE_POOL_FACTOR", 2)
    monkeypatch.setattr(module, "adaptive_round_spec", lambda count: _RoundSpecFixture())


def test_adaptive_candidate_pool_is_exact_unique_disjoint_and_geometry_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _write_round_fixture(tmp_path)
    module = _load_script("build_broadband56_adaptive_candidate_pool")
    _patch_small_round(module, monkeypatch)
    out_dir = tmp_path / "candidate_pool_output"

    exit_code = module.main(
        [
            "--contract",
            str(CONTRACT),
            "--config",
            str(TEMPLATE),
            "--round-dir",
            str(fixture["round_dir"]),
            "--out-dir",
            str(out_dir),
            "--count",
            "10",
            "--seed",
            "2026082810",
        ]
    )

    assert exit_code == 0
    pool_path = out_dir / "broadband56_adaptive_candidate_pool.csv"
    with pool_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 10
    assert len({row["geometry_sha256"] for row in rows}) == 10
    assert not ({row["geometry_sha256"] for row in rows} & fixture["accepted_hashes"])
    assert all(row["campaign_phase"] == "PHASE_B" for row in rows)
    assert all(row["label_status"] == "UNEVALUATED_GEOMETRY_ONLY" for row in rows)
    assert all(row["predictions_are_labels"] == "false" for row in rows)
    assert all(row["candidate_generation_mode"] == "sobol_normalized_10d_adaptive_pool" for row in rows)
    assert not any(column.startswith(("pred__", "unc__")) for column in rows[0])
    for row in rows:
        geometry = {name: row[f"geom__{name}"] for name in GEOMETRY_FIELDS}
        assert row["geometry_sha256"] == canonical_geometry_sha256(geometry)
        assert row["candidate_id_sha256"] == row["geometry_sha256"]
        assert row["candidate_geometry_identity_sha256"] == row["geometry_sha256"]
        assert row["candidate_identity_schema"] == "canonical_10d_geometry_sha256_alias_v1"
        assert row["analytical_status"] == "PASS"
        assert row["topology_status"] == "PASS"
        assert row["top_metal_drc_status"] == "PASS"
    summary = json.loads(
        (out_dir / "ADAPTIVE_CANDIDATE_POOL_SUMMARY.json").read_text(encoding="utf-8")
    )
    assert summary["overall_status"] == "PASS"
    assert summary["candidate_count"] == 10
    assert summary["accepted_geometry_count"] == 3


def test_adaptive_candidate_pool_fails_closed_on_round_hash_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _write_round_fixture(tmp_path)
    round_contract_path = fixture["round_contract_path"]
    round_contract = json.loads(round_contract_path.read_text(encoding="utf-8"))
    round_contract["tampered_after_receipt"] = True
    _write_json(round_contract_path, round_contract)
    module = _load_script("build_broadband56_adaptive_candidate_pool")
    _patch_small_round(module, monkeypatch)
    out_dir = tmp_path / "rejected_pool"

    exit_code = module.main(
        [
            "--contract",
            str(CONTRACT),
            "--config",
            str(TEMPLATE),
            "--round-dir",
            str(fixture["round_dir"]),
            "--out-dir",
            str(out_dir),
            "--count",
            "10",
            "--seed",
            "2026082810",
        ]
    )

    assert exit_code == 2
    assert not (out_dir / "broadband56_adaptive_candidate_pool.csv").exists()
    receipt = json.loads(
        (out_dir / "ADAPTIVE_CANDIDATE_POOL_RECEIPT.json").read_text(encoding="utf-8")
    )
    assert receipt["overall_status"] == "FAIL"
    assert any(
        check["name"] == "adaptive_round_contract_hash_bound" and not check["pass"]
        for check in receipt["checks"]
    )
