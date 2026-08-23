#!/usr/bin/env python3
"""Build the formal MARS56 1M production/checkpoint contract.

The contract is deliberately separate from the evidence.  It defines what must
exist before the 1M goal can be marked complete: 10 formal 100k datasets, a
strict per-100k physical/model checkpoint for each dataset, and cumulative
100k/200k/.../1000k checkpoints with Lp/Ls/Q/|K| uniformity evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_BASE = "/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256"
DEFAULT_PROJECT = "/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705"


def main() -> int:
    args = _parse_args()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    expected_chunks = int(args.expected_chunks)
    expected_per_chunk = int(args.expected_per_chunk)
    total = expected_chunks * expected_per_chunk
    contract = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "contract_name": "mars56_s4p_1m_physical_feature_uniformity_contract",
        "overall_status": "CONTRACT_ONLY_NOT_EVIDENCE",
        "base": str(args.base),
        "remote_project": str(args.remote_project),
        "expected_chunks": expected_chunks,
        "expected_per_chunk": expected_per_chunk,
        "expected_total_rows": total,
        "touchstone_contract": {
            "extension": ".s4p",
            "ports": 4,
            "frequency_start_ghz": 5.0,
            "frequency_stop_ghz": 60.0,
            "frequency_step_ghz": 0.5,
            "frequency_points": 111,
            "port_mode": "single_ended_shield_grounded",
            "cadence_pin_purpose": 51,
        },
        "queue_contract": {
            "pre_emx_provenance_audit": "audit_mars56_s4p_candidate_queue_provenance.py",
            "source_selection_required": True,
            "reject_bootstrap_source": "geometry_space_filling_no_physical_labels",
            "required_predicted_physical_features": ["Lp", "Ls", "Q", "K"],
            "required_target_bin_physical_features": ["Lp", "Ls", "Q", "K"],
        },
        "physical_feature_checkpoint_contract": {
            "target_ghz": 15.0,
            "lp_nh_range": [0.5, 3.0],
            "ls_nh_range": [0.5, 3.0],
            "q_range": [5.0, 25.0],
            "k_abs_range": [0.0, 0.8],
            "bins": 10,
            "pair_bins": 10,
            "four_d_bins": 4,
            "min_four_d_occupied_fraction": 0.50,
            "min_four_d_normalized_entropy": 0.80,
            "max_four_d_nonzero_bin_imbalance": 4.0,
            "require_plots": True,
            "required_steps": [
                "stable_index",
                "response_features",
                "enrichment",
                "uniformity",
                "uniformity_manifest",
                "training",
                "model",
                "traceability",
            ],
            "required_artifacts": [
                "mars56_s4p_physical_checkpoint_pipeline_summary.json",
                "physical_feature_uniformity_summary.json",
                "physical_feature_uniformity_manifest.json",
                "physical_feature_marginal_histograms.png",
                "physical_feature_pair_scatter.png",
                "physical_feature_pair_occupancy_heatmaps.png",
                "physical_feature_inverse_training_manifest.json",
                "physical_feature_inverse_checkpoint_test_summary.json",
                "physical_checkpoint_traceability_summary.json",
            ],
            "traceability_required_row_fields": [
                "stable_manifest_rows",
                "response_feature_rows",
                "enriched_rows",
                "training_rows",
            ],
        },
        "formal_100k_chunks": [
            _formal_chunk(args.base, index, expected_per_chunk)
            for index in range(1, expected_chunks + 1)
        ],
        "cumulative_checkpoints": [
            _cumulative_checkpoint(args.base, index, expected_per_chunk)
            for index in range(1, expected_chunks + 1)
        ],
        "completion_rule": {
            "formal_chunk_pass_count_required": expected_chunks,
            "cumulative_checkpoint_pass_count_required": expected_chunks,
            "total_nonempty_s4p_required": total,
            "evidence_index_required": True,
            "evidence_index_json": f"{args.base}/status/100k_checkpoint_evidence_index_20260707/mars56_100k_checkpoint_evidence_index.json",
            "evidence_index_markdown": f"{args.base}/status/100k_checkpoint_evidence_index_20260707/mars56_100k_checkpoint_evidence_index.md",
        },
        "local_source_shas": _local_source_shas(Path(args.local_root).expanduser().resolve()) if args.local_root else {},
        "boundary": (
            "This file is the production contract. It is not evidence that the "
            "remote data exists or that checkpoints passed."
        ),
    }

    json_path = out_dir / "mars56_1m_production_plan_contract.json"
    md_path = out_dir / "mars56_1m_production_plan_contract.md"
    json_path.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(_render_markdown(contract), encoding="utf-8")

    print(f"contract_json={json_path}")
    print(f"contract_md={md_path}")
    print("PRODUCTION_PLAN_CONTRACT_STATUS=CONTRACT_WRITTEN_NOT_EVIDENCE")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--remote-project", default=DEFAULT_PROJECT)
    parser.add_argument("--expected-chunks", type=int, default=10)
    parser.add_argument("--expected-per-chunk", type=int, default=100000)
    parser.add_argument("--local-root", help="Optional local repo/workspace root for source SHA recording")
    return parser.parse_args()


def _formal_chunk(base: str, index: int, expected_per_chunk: int) -> dict[str, Any]:
    tag = f"chunk_{index:03d}_100k_after_chunk08_pass"
    dataset = f"{base}/datasets/{tag}"
    return {
        "index": index,
        "tag": tag,
        "dataset": dataset,
        "expected_rows": expected_per_chunk,
        "dataset_summary": f"{dataset}/parallel_candidate_queue_dataset_summary.json",
        "checkpoint_root": f"{base}/model_tests/{tag}",
        "required_state": "dataset_summary_PASS_and_checkpoint_proof_PASS",
    }


def _cumulative_checkpoint(base: str, index: int, expected_per_chunk: int) -> dict[str, Any]:
    expected_rows = index * expected_per_chunk
    tag = f"cumulative_{index * 100:04d}k_after_chunk08_pass"
    return {
        "index": index,
        "tag": tag,
        "expected_rows": expected_rows,
        "checkpoint_root": f"{base}/cumulative_model_tests/{tag}",
        "required_state": "checkpoint_proof_PASS",
    }


def _local_source_shas(root: Path) -> dict[str, Any]:
    files = [
        "rfic-transformer-inverse-design/scripts/run_mars56_s4p_100k_chunk_from_queue.sh",
        "rfic-transformer-inverse-design/scripts/audit_mars56_s4p_candidate_queue_provenance.py",
        "rfic-transformer-inverse-design/scripts/run_mars56_s4p_physical_checkpoint_pipeline.sh",
        "rfic-transformer-inverse-design/scripts/audit_physical_feature_uniformity.py",
        "rfic-transformer-inverse-design/scripts/audit_physical_checkpoint_traceability.py",
        "rfic-transformer-inverse-design/scripts/audit_mars56_s4p_million_chunk_checkpoint.py",
    ]
    out: dict[str, Any] = {}
    for rel in files:
        path = root / rel
        out[rel] = _file_source(path)
    return out


def _file_source(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return out
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    out.update({"size_bytes": path.stat().st_size, "sha256": digest.hexdigest()})
    return out


def _render_markdown(contract: dict[str, Any]) -> str:
    lines = [
        "# MARS56 1M Production Plan Contract",
        "",
        f"Generated UTC: `{contract['generated_utc']}`",
        "",
        "## Scope",
        "",
        f"- Expected chunks: `{contract['expected_chunks']}`",
        f"- Expected rows per chunk: `{contract['expected_per_chunk']}`",
        f"- Expected total rows: `{contract['expected_total_rows']}`",
        f"- Base: `{contract['base']}`",
        "",
        "## Formal 100k Chunks",
        "",
        "| Index | Tag | Expected rows | Required state |",
        "| --- | --- | ---: | --- |",
    ]
    for chunk in contract["formal_100k_chunks"]:
        lines.append(f"| {chunk['index']} | `{chunk['tag']}` | {chunk['expected_rows']} | `{chunk['required_state']}` |")
    lines.extend(
        [
            "",
            "## Cumulative Checkpoints",
            "",
            "| Index | Tag | Expected rows | Required state |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for item in contract["cumulative_checkpoints"]:
        lines.append(f"| {item['index']} | `{item['tag']}` | {item['expected_rows']} | `{item['required_state']}` |")
    lines.extend(
        [
            "",
            "## Physical Feature Gate",
            "",
            "- Features: `Lp`, `Ls`, `Q`, `|K|`",
            "- Uniformity: marginal, pairwise, and 4D occupancy gates",
            "- Required plots: marginal histograms, pair scatter, pair occupancy heatmaps",
            "",
            "## Boundary",
            "",
            contract["boundary"],
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
