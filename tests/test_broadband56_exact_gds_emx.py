from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import gdstk

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    TARGET_ACCEPTED_GEOMETRIES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
from rfic_transformer_inverse_design.campaigns.broadband56_exact_gds_emx import (
    CALIBRE_ZERO_BLOCKING_PASS_DECISION,
    CALIBRE_ZERO_BLOCKING_RECEIPT_SCHEMA,
    EXACT_GDS_EMX_FAILURE_NAME,
    EXACT_GDS_EMX_RECEIPT_NAME,
    ExactAuditedGdsEmxError,
    run_exact_audited_gds_fresh_emx,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (
    ATTEMPT_REPLENISHMENT_CONTRACT,
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_TEMPLATE = (
    REPO_ROOT
    / "configs"
    / "mars_s4p_grounded_powerline_broadband56_balanced200k_v2_template.yaml"
)
CANDIDATE_ID = "1" * 64
GEOMETRY_ID = "2" * 64


class Broadband56ExactGdsEmxTest(unittest.TestCase):
    def test_runs_emx_on_exact_zero_blocking_gds_without_copying_it(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmpdir:
            fixture = _build_fixture(Path(tmpdir))
            original_gds_sha = _sha256(fixture["gds"])

            result = _run_fixture(fixture, run_emx_fn=_fake_emx)

            self.assertEqual(result["overall_status"], "PASS")
            self.assertEqual(_sha256(fixture["gds"]), original_gds_sha)
            receipt = json.loads(
                Path(result["receipt_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(receipt["overall_status"], "PASS")
            self.assertEqual(receipt["source_exact_gds"]["sha256"], original_gds_sha)
            self.assertEqual(receipt["emx_output"]["num_ports"], 4)
            self.assertEqual(receipt["emx_output"]["num_frequency_points"], 56)
            self.assertFalse(receipt["cadence_executed_by_this_runner"])
            self.assertFalse(receipt["calibre_executed_by_this_runner"])
            self.assertFalse(receipt["gds_generated_or_copied_by_this_runner"])
            self.assertTrue(receipt["fresh_real_emx_executed"])
            self.assertEqual(list(fixture["out_dir"].rglob("*.gds")), [])

    def test_rejects_nonzero_calibre_blocking_count_before_emx(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmpdir:
            fixture = _build_fixture(Path(tmpdir))
            receipt = json.loads(
                fixture["calibre_receipt"].read_text(encoding="utf-8")
            )
            receipt["calibre_blocking_violations"] = 1
            fixture["calibre_receipt"].write_text(
                json.dumps(receipt, indent=2), encoding="utf-8"
            )
            fixture["calibre_receipt_sha256"] = _sha256(
                fixture["calibre_receipt"]
            )
            calls: list[str] = []

            def _must_not_run(**kwargs):
                calls.append("emx")
                raise AssertionError("EMX must not run")

            with self.assertRaisesRegex(
                ExactAuditedGdsEmxError, "zero_blocking"
            ):
                _run_fixture(fixture, run_emx_fn=_must_not_run)

            self.assertEqual(calls, [])
            self.assertFalse(fixture["out_dir"].exists())

    def test_rejects_non_full_campaign_authorization_before_emx(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmpdir:
            fixture = _build_fixture(Path(tmpdir))
            receipt = json.loads(
                fixture["authorization_receipt"].read_text(encoding="utf-8")
            )
            receipt["emx_authorized_within_current_stage"] = False
            fixture["authorization_receipt"].write_text(
                json.dumps(receipt, indent=2), encoding="utf-8"
            )
            fixture["authorization_receipt_sha256"] = _sha256(
                fixture["authorization_receipt"]
            )
            calls: list[str] = []

            def _must_not_run(**kwargs):
                calls.append("emx")
                raise AssertionError("EMX must not run")

            with self.assertRaisesRegex(
                ExactAuditedGdsEmxError, "emx_authorized"
            ):
                _run_fixture(fixture, run_emx_fn=_must_not_run)

            self.assertEqual(calls, [])
            self.assertFalse(fixture["out_dir"].exists())

    def test_rejects_manifest_port_order_drift_before_emx(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmpdir:
            fixture = _build_fixture(Path(tmpdir))
            manifest = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
            manifest["ports"][0]["name"] = "P004"
            fixture["manifest"].write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
            fixture["manifest_sha256"] = _sha256(fixture["manifest"])
            _refresh_calibre_receipt(fixture)
            calls: list[str] = []

            def _must_not_run(**kwargs):
                calls.append("emx")
                raise AssertionError("EMX must not run")

            with self.assertRaisesRegex(
                ExactAuditedGdsEmxError, "port_names_exact"
            ):
                _run_fixture(fixture, run_emx_fn=_must_not_run)

            self.assertEqual(calls, [])

    def test_rejects_calibre_receipt_bound_to_another_gds_before_emx(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmpdir:
            fixture = _build_fixture(Path(tmpdir))
            other_gds = Path(tmpdir) / "other.gds"
            other_gds.write_bytes(fixture["gds"].read_bytes())
            receipt = json.loads(
                fixture["calibre_receipt"].read_text(encoding="utf-8")
            )
            receipt["gds_path"] = str(other_gds)
            fixture["calibre_receipt"].write_text(
                json.dumps(receipt, indent=2), encoding="utf-8"
            )
            fixture["calibre_receipt_sha256"] = _sha256(
                fixture["calibre_receipt"]
            )
            calls: list[str] = []

            def _must_not_run(**kwargs):
                calls.append("emx")
                raise AssertionError("EMX must not run")

            with self.assertRaisesRegex(ExactAuditedGdsEmxError, "gds_path"):
                _run_fixture(fixture, run_emx_fn=_must_not_run)

            self.assertEqual(calls, [])

    def test_rejects_gds_identity_drift_after_emx(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmpdir:
            fixture = _build_fixture(Path(tmpdir))

            def _mutating_emx(**kwargs):
                payload = _fake_emx(**kwargs)
                Path(kwargs["layout"].gds_path).write_bytes(b"changed after Calibre")
                return payload

            with self.assertRaisesRegex(
                ExactAuditedGdsEmxError, "SHA-256 mismatch"
            ):
                _run_fixture(fixture, run_emx_fn=_mutating_emx)

            self.assertTrue(
                (fixture["out_dir"] / EXACT_GDS_EMX_FAILURE_NAME).is_file()
            )
            self.assertFalse(
                (fixture["out_dir"] / EXACT_GDS_EMX_RECEIPT_NAME).exists()
            )

    def test_rejects_touchstone_with_wrong_frequency_grid(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmpdir:
            fixture = _build_fixture(Path(tmpdir))

            def _wrong_grid_emx(**kwargs):
                return _fake_emx(**kwargs, frequencies_hz=FREQUENCY_GRID_HZ[:-1])

            with self.assertRaisesRegex(
                ExactAuditedGdsEmxError, "frequency_count_exact_56"
            ):
                _run_fixture(fixture, run_emx_fn=_wrong_grid_emx)

            self.assertTrue(
                (fixture["out_dir"] / EXACT_GDS_EMX_FAILURE_NAME).is_file()
            )

    def test_rejects_emx_command_bound_to_another_gds(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmpdir:
            fixture = _build_fixture(Path(tmpdir))

            def _wrong_command_emx(**kwargs):
                payload = _fake_emx(**kwargs)
                command_path = Path(kwargs["work_dir"]) / "emx" / "emx_command.json"
                command_path.write_text(
                    json.dumps(["emx", "/tmp/not-the-audited-input.gds"]),
                    encoding="utf-8",
                )
                return payload

            with self.assertRaisesRegex(
                ExactAuditedGdsEmxError, "not bound to exactly one"
            ):
                _run_fixture(fixture, run_emx_fn=_wrong_command_emx)

    def test_refuses_existing_output_directory_before_emx(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmpdir:
            fixture = _build_fixture(Path(tmpdir))
            fixture["out_dir"].mkdir()
            calls: list[str] = []

            def _must_not_run(**kwargs):
                calls.append("emx")
                raise AssertionError("EMX must not run")

            with self.assertRaisesRegex(
                ExactAuditedGdsEmxError, "refusing existing output directory"
            ):
                _run_fixture(fixture, run_emx_fn=_must_not_run)

            self.assertEqual(calls, [])


def _build_fixture(root: Path) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    config = root / "private_56pt.yaml"
    config.write_bytes(CONFIG_TEMPLATE.read_bytes())

    gds = root / "candidate_cadence.gds"
    library = gdstk.Library()
    cell = library.new_cell("TRANSFORMER")
    cell.add(gdstk.rectangle((0.0, 0.0), (20.0, 20.0), layer=81, datatype=0))
    library.write_gds(str(gds))

    manifest = root / "transformer_layout.layout.json"
    manifest.write_text(
        json.dumps(
            {
                "layout_path": str(root / "pre_cadence.gds"),
                "top_cell": "TRANSFORMER",
                "ports": [
                    {
                        "name": name,
                        "signal_labels": [name],
                        "ground_labels": ["GND"],
                        "internal_size_um": [4.0, 4.0],
                    }
                    for name in ("P001", "P002", "P003", "P004")
                ],
                "metal_layer": 81,
                "metal_datatype": 0,
                "ground_layer": 68,
                "ground_datatype": 20,
                "label_layer": 81,
                "label_datatype": 0,
                "cadence_pin_purpose": 51,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    calibre_report = root / "calibre_drc_summary.txt"
    calibre_report.write_text("TOTAL RESULTS GENERATED = 0\n", encoding="utf-8")
    calibre_receipt = root / "CALIBRE_ZERO_BLOCKING_RECEIPT.json"
    calibre_receipt.write_text(
        json.dumps(
            {
                "schema": CALIBRE_ZERO_BLOCKING_RECEIPT_SCHEMA,
                "overall_status": "PASS",
                "decision": CALIBRE_ZERO_BLOCKING_PASS_DECISION,
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "candidate_id_sha256": CANDIDATE_ID,
                "geometry_identity_sha256": GEOMETRY_ID,
                "config_path": str(config),
                "config_size_bytes": config.stat().st_size,
                "config_sha256": _sha256(config),
                "gds_path": str(gds),
                "gds_size_bytes": gds.stat().st_size,
                "gds_sha256": _sha256(gds),
                "manifest_path": str(manifest),
                "manifest_size_bytes": manifest.stat().st_size,
                "manifest_sha256": _sha256(manifest),
                "top_cell": "TRANSFORMER",
                "cadence_streamout_complete": True,
                "calibre_executed": True,
                "calibre_blocking_violations": 0,
                "calibre_report_path": str(calibre_report),
                "calibre_report_size_bytes": calibre_report.stat().st_size,
                "calibre_report_sha256": _sha256(calibre_report),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    authorization_receipt = root / "FULL_CAMPAIGN_AUTHORIZATION_RECEIPT.json"
    authorization_receipt.write_text(
        json.dumps(
            {
                "schema": FULL_CAMPAIGN_APPROVAL_SCHEMA,
                "overall_status": "PASS",
                "decision": FULL_CAMPAIGN_PASS_DECISION,
                "authorization_scope": FULL_CAMPAIGN_APPROVAL_SCOPE,
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "approved_by": "Test project owner",
                "emx_authorized_within_current_stage": True,
                "campaign_200k_authorized": True,
                "accepted_geometry_target": TARGET_ACCEPTED_GEOMETRIES,
                "replenished_attempt_rounds_authorized": True,
                "attempt_replenishment_contract": ATTEMPT_REPLENISHMENT_CONTRACT,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "config": config,
        "config_sha256": _sha256(config),
        "gds": gds,
        "gds_sha256": _sha256(gds),
        "manifest": manifest,
        "manifest_sha256": _sha256(manifest),
        "calibre_receipt": calibre_receipt,
        "calibre_receipt_sha256": _sha256(calibre_receipt),
        "authorization_receipt": authorization_receipt,
        "authorization_receipt_sha256": _sha256(authorization_receipt),
        "out_dir": root / "fresh_emx_run",
    }


def _run_fixture(fixture: dict[str, object], *, run_emx_fn):
    return run_exact_audited_gds_fresh_emx(
        config_path=fixture["config"],
        expected_config_sha256=fixture["config_sha256"],
        gds_path=fixture["gds"],
        expected_gds_sha256=fixture["gds_sha256"],
        manifest_path=fixture["manifest"],
        expected_manifest_sha256=fixture["manifest_sha256"],
        calibre_receipt_path=fixture["calibre_receipt"],
        expected_calibre_receipt_sha256=fixture["calibre_receipt_sha256"],
        full_campaign_receipt_path=fixture["authorization_receipt"],
        expected_full_campaign_receipt_sha256=fixture[
            "authorization_receipt_sha256"
        ],
        candidate_id_sha256=CANDIDATE_ID,
        geometry_identity_sha256=GEOMETRY_ID,
        out_dir=fixture["out_dir"],
        run_emx_fn=run_emx_fn,
    )


def _refresh_calibre_receipt(fixture: dict[str, object]) -> None:
    receipt = json.loads(fixture["calibre_receipt"].read_text(encoding="utf-8"))
    receipt["manifest_size_bytes"] = fixture["manifest"].stat().st_size
    receipt["manifest_sha256"] = fixture["manifest_sha256"]
    fixture["calibre_receipt"].write_text(
        json.dumps(receipt, indent=2), encoding="utf-8"
    )
    fixture["calibre_receipt_sha256"] = _sha256(fixture["calibre_receipt"])


def _fake_emx(*, run_config, work_dir, layout, manifest, frequencies_hz=None):
    emx_dir = Path(work_dir) / "emx"
    emx_dir.mkdir(parents=True, exist_ok=True)
    touchstone = emx_dir / "emx.s4p"
    _write_s4p(
        touchstone,
        FREQUENCY_GRID_HZ if frequencies_hz is None else frequencies_hz,
    )
    command = [
        str(run_config.emx.emx_binary),
        str(layout.gds_path),
        str(layout.top_cell),
        str(run_config.emx.emx_process_file),
    ]
    (emx_dir / "emx_command.json").write_text(
        json.dumps(command), encoding="utf-8"
    )
    return {
        "touchstone_path": str(touchstone),
        "raw_touchstone_path": str(touchstone),
        "command": command,
        "num_freqs": len(tuple(frequencies_hz or FREQUENCY_GRID_HZ)),
        "raw_num_ports": len(manifest.ports),
        "effective_num_ports": len(manifest.ports),
    }


def _write_s4p(path: Path, frequencies_hz) -> None:
    with path.open("w", encoding="ascii") as handle:
        handle.write("! exact-grid synthetic test fixture; EMX is mocked\n")
        handle.write("# GHz S RI R 50\n")
        for frequency_hz in frequencies_hz:
            values = [f"{float(frequency_hz) / 1e9:.12g}"]
            for row in range(4):
                for col in range(4):
                    real = 0.05 if row == col else 0.01
                    values.extend([f"{real:.12e}", "0.000000000000e+00"])
            handle.write(" ".join(values) + "\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
