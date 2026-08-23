#!/usr/bin/env python3
"""Two-panel smoke test for the preregistered zero-safe v2 report generator.

This fixture validates schema/gate/report mechanics only.  It is synthetic and
is not evidence about EMX accuracy or the real 10,000-target experiment.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent.parent
GENERATOR = HERE / "build_historical_200k_fixed10k_fresh_emx_statistics_v2_zero_safe.py"
PREREG = (
    WORKSPACE
    / "reports"
    / "historical_200k_fixed10k_mars_physical_20260822"
    / "statistics_v2_preregistration_20260822"
    / "fresh_emx_statistics_v2_preregistration.json"
)
PREREG_SHA = "1f139ee13352d7df63d99b790987041fad538540c9c2cc05babcac5fe958cb55"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_generator():
    spec = importlib.util.spec_from_file_location("statistics_v2_zero_safe_smoke", GENERATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import v2 generator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    assert _sha(PREREG) == PREREG_SHA
    with tempfile.TemporaryDirectory(prefix="fresh-emx-v2-zero-safe-smoke-") as raw:
        root = Path(raw)
        rows_path = root / "v1_rows.csv"
        summary_path = root / "v1_summary.json"
        manifest_path = root / "v1_manifest.json"
        out_dir = root / "out"
        fields = [
            "target_id",
            "panel",
            "inside_historical_training_contract",
            "source_row_index",
            "candidate_id_sha256",
            "candidate_geometry_identity_sha256",
            "touchstone_sha256",
            "gds_timestamp_normalized_sha256",
            *(f"target__{name}" for name in ("lp_nh", "ls_nh", "qmin", "k_abs")),
            *(f"proxy__{name}" for name in ("lp_nh", "ls_nh", "qmin", "k_abs")),
            *(f"real_emx__{name}" for name in ("lp_nh", "ls_nh", "qmin", "k_abs")),
            "all_report_gates_pass",
        ]
        rows = []
        for index, panel in enumerate(("legacy_k_le_0p8", "extension_k_gt_0p8")):
            rows.append(
                {
                    "target_id": f"target_{index}",
                    "panel": panel,
                    "inside_historical_training_contract": str(index == 0).lower(),
                    "source_row_index": index,
                    "candidate_id_sha256": hashlib.sha256(
                        f"candidate{index}".encode()
                    ).hexdigest(),
                    "candidate_geometry_identity_sha256": hashlib.sha256(
                        f"geometry{index}".encode()
                    ).hexdigest(),
                    "touchstone_sha256": hashlib.sha256(f"s4p{index}".encode()).hexdigest(),
                    "gds_timestamp_normalized_sha256": hashlib.sha256(
                        f"gds{index}".encode()
                    ).hexdigest(),
                    "target__lp_nh": 1.0,
                    "target__ls_nh": 1.2,
                    "target__qmin": 10.0,
                    "target__k_abs": 0.0 if index == 0 else 0.9,
                    "proxy__lp_nh": 1.05,
                    "proxy__ls_nh": 1.15,
                    "proxy__qmin": 11.0,
                    "proxy__k_abs": 0.05 if index == 0 else 0.85,
                    "real_emx__lp_nh": 1.10,
                    "real_emx__ls_nh": 1.10,
                    "real_emx__qmin": 9.0 if index == 0 else 12.0,
                    "real_emx__k_abs": 0.10 if index == 0 else 0.80,
                    "all_report_gates_pass": "false",
                }
            )
        with rows_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        summary_path.write_text(
            json.dumps(
                {
                    "schema": (
                        "historical_200k_fixed10k_fresh_real_emx_statistics_zero_safe_v3"
                    ),
                    "overall_status": "PASS",
                    "funnel": {
                        "original_target_denominator": 10000,
                        "analytical_preflight_pass": 7926,
                        "cadence_streamout_pass": 7373,
                        "zero_blocking_calibre_pass": 7298,
                        "fresh_real_emx_evaluated": 2,
                    },
                    "artifacts": {
                        "fresh_emx_evaluated_rows": {
                            "sha256": _sha(rows_path),
                            "row_count": 2,
                        }
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        manifest_path.write_text(
            json.dumps({"artifacts": {"fixture": {"sha256": "synthetic"}}}) + "\n",
            encoding="utf-8",
        )
        generator = _load_generator()
        result = generator.main(
            [
                "--v1-summary",
                str(summary_path),
                "--v1-evaluated-rows",
                str(rows_path),
                "--v1-manifest",
                str(manifest_path),
                "--preregistration-json",
                str(PREREG),
                "--expected-v1-summary-sha256",
                _sha(summary_path),
                "--expected-v1-evaluated-rows-sha256",
                _sha(rows_path),
                "--expected-v1-manifest-sha256",
                _sha(manifest_path),
                "--expected-preregistration-sha256",
                PREREG_SHA,
                "--expected-count",
                "2",
                "--out-dir",
                str(out_dir),
            ]
        )
        assert result == 0
        summary = json.loads(
            (out_dir / "historical_200k_fresh_emx_statistics_v2_methodology_summary.json")
            .read_text(encoding="utf-8")
        )
        assert summary["overall_status"] == "PASS"
        assert summary["row_count"] == 2
        assert all(summary["checks"].values())
        assert summary["method_preregistration_sha256"] == PREREG_SHA
        assert summary["histogram_contract"]["result_dependent_clipping_or_p99_axis"] is False
        assert summary["metrics"]["overall"]["features"]["k_abs"]["target_vs_emx"][
            "role_warning"
        ] == "DENOMINATOR_SENSITIVE_DIAGNOSTIC_ONLY"
    print("V2_ZERO_SAFE_FULL_SMOKE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
