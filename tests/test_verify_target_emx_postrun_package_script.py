from tests.rfic_transformer_inverse_design.shared import *

import hashlib
import importlib.util
import sys
import tarfile
import warnings
from io import BytesIO
from unittest import mock

from PIL import Image, ImageDraw


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "verify_target_emx_postrun_package.py"
    spec = importlib.util.spec_from_file_location("verify_target_emx_postrun_package_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _valid_png_bytes(width: int = 900, height: int = 600, *, byte_count: int = 4096) -> bytes:
    image = Image.new("RGB", (width, height), (246, 247, 249))
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 13):
        draw.line((x, 0, width - 1 - (x % width), height - 1), fill=((x * 3) % 255, 80, 180), width=2)
    for y in range(0, height, 17):
        draw.line((0, y, width - 1, height - 1 - (y % height)), fill=(180, (y * 5) % 255, 40), width=1)
    draw.rectangle((40, 40, min(width - 40, 260), min(height - 40, 160)), outline=(20, 80, 160), width=3)
    buffer = BytesIO()
    image.save(buffer, format="PNG", compress_level=4)
    data = buffer.getvalue()
    if len(data) < byte_count:
        buffer = BytesIO()
        image.save(buffer, format="PNG", compress_level=0)
        data = buffer.getvalue()
    return data


def _blank_png_bytes(width: int = 900, height: int = 600) -> bytes:
    image = Image.new("RGB", (width, height), (255, 255, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG", compress_level=0)
    return buffer.getvalue()


def _tiny_png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00"
        b"\x1b\xb6\xeeV"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _metrics_csv_text(columns: tuple[str, ...]) -> str:
    rows = [",".join(columns)]
    for index in range(451):
        freq_ghz = 5.0 + 0.1 * index
        values = {
            "freq_hz": f"{freq_ghz * 1.0e9:.1f}",
            "freq_ghz": f"{freq_ghz:.1f}",
            "lp_nh": f"{1.0 + 0.001 * index:.6g}",
            "ls_nh": f"{1.2 + 0.001 * index:.6g}",
            "m_nh": f"{0.55 + 0.0001 * index:.6g}",
            "k": f"{0.5 + 0.0001 * index:.6g}",
            "qp": f"{10.0 + 0.01 * index:.6g}",
            "qs": f"{12.0 + 0.01 * index:.6g}",
            "cm_single_primary_ff": f"{30.0 + 0.01 * index:.6g}",
        }
        rows.append(",".join(values[column] for column in columns))
    return "\n".join(rows) + "\n"


def _ordered_port_pairs() -> list[str]:
    pairs: list[str] = []
    for a in range(1, 5):
        for b in range(1, 5):
            if b == a:
                continue
            remaining = [port for port in range(1, 5) if port not in (a, b)]
            for c, d in ((remaining[0], remaining[1]), (remaining[1], remaining[0])):
                pairs.append(f"{a},{b}:{c},{d}")
    return pairs


def _port_pair_sensitivity_csv_text(*, approved_status: str = "PASS", approved_error: float = 0.0) -> str:
    rows = [
        "port_pairs,overall_status,pass_count,metric_count,max_percent_error,mean_percent_error",
    ]
    for pair in _ordered_port_pairs():
        if pair == "1,2:3,4":
            rows.append(f'"{pair}",{approved_status},{6 if approved_status == "PASS" else 5},6,{approved_error},0.0')
        else:
            rows.append(f'"{pair}",FAIL,0,6,100.0,50.0')
    return "\n".join(rows) + "\n"


def _write_fixture_validation_dir(root: Path, *, emx_sha: str, emx_first_status: str = "PASS") -> Path:
    validation = root / "validation_20260613"
    (validation / "touchstone_physical_gate").mkdir(parents=True)
    (validation / "emx_first_validation_gate_20260613").mkdir(parents=True)
    (validation / "emx_wideband.s4p.sha256").write_text(f"{emx_sha}  /mars/path/emx.s4p\n", encoding="utf-8")
    _write_json(
        validation / "touchstone_physical_gate" / "touchstone_transformer_audit_summary.json",
        {
            "overall_status": "PASS",
            "port_count": 4,
            "frequency": {
                "start_hz": 5_000_000_000.0,
                "stop_hz": 50_000_000_000.0,
                "step_hz": 100_000_000.0,
                "points": 451,
            },
            "arguments": {
                "min_target_abs_k": 0.05,
                "min_window_abs_k": 0.05,
            },
            "checks": [
                {"status": "PASS", "name": "port count", "detail": "ports=4"},
                {"status": "PASS", "name": "source identity", "detail": "expected=EMX"},
                {"status": "PASS", "name": "differential Z finiteness", "detail": "finite"},
                {"status": "PASS", "name": "differential Z reciprocity", "detail": "ok"},
                {"status": "PASS", "name": "differential Z positive-realness", "detail": "ok"},
                {"status": "PASS", "name": "ADS-equivalent metric finiteness", "detail": "finite"},
                {"status": "PASS", "name": "target-frequency transformer metrics", "detail": "ok"},
                {"status": "PASS", "name": "positive metric window", "detail": "ok"},
                {"status": "PASS", "name": "smooth transformer metric window", "detail": "ok"},
            ],
        },
    )
    (validation / "touchstone_physical_gate" / "touchstone_transformer_audit_report.md").write_text("# report\n", encoding="utf-8")
    (validation / "touchstone_physical_gate" / "touchstone_transformer_metrics.csv").write_text(
        _metrics_csv_text(("freq_hz", "freq_ghz", "lp_nh", "ls_nh", "m_nh", "k", "qp", "qs")),
        encoding="utf-8",
    )
    (validation / "touchstone_physical_gate" / "touchstone_ads_equivalent_metrics.png").write_bytes(_valid_png_bytes())
    emx_first_check_names = (
        "source identity",
        "source provenance header",
        "S-matrix shape",
        "frequency row count",
        "finite numeric values",
        "frequency monotonicity",
        "reciprocity",
        "passivity",
        "differential Z finiteness",
        "differential Z reciprocity",
        "differential Z positive-realness",
        "final ADS sweep coverage",
        "ADS no-extrapolation plot grid",
        "target frequency availability",
        "ADS photo anchor",
        "basic numeric physics sanity",
        "physical metric window",
        "smooth transformer metric window",
        "approved port-pair photo alignment",
        "any port-pair photo alignment",
    )
    _write_json(
        validation / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_summary.json",
        {
            "overall_status": emx_first_status,
            "decision": "ACCEPT_AS_GOLDEN_EMX_REFERENCE" if emx_first_status == "PASS" else "DO_NOT_USE_AS_GOLDEN_EMX_REFERENCE",
            "frequency_ghz": {"start": 5.0, "stop": 50.0, "step": 0.1, "points": 451},
            "physical_curve_gate": {
                "physical_window_start_ghz": 5.0,
                "physical_window_stop_ghz": 30.0,
                "shape_window_start_ghz": 5.0,
                "shape_window_stop_ghz": 30.0,
                "min_target_abs_k": 0.05,
                "min_window_abs_k": 0.05,
                "max_target_abs_k": 0.98,
                "max_shape_spike_ratio": 4.0,
                "max_shape_relative_step": 0.25,
            },
            "checks": [{"status": emx_first_status, "name": name, "detail": "ok"} for name in emx_first_check_names],
            "port_pair_sensitivity": {
                "best": {"port_pairs": "1,2:3,4", "overall_status": "PASS", "max_percent_error": 0.0},
                "default": {"port_pairs": "1,2:3,4", "overall_status": "PASS", "max_percent_error": 0.0},
                "pass_count": 1,
            },
            "artifacts": {
                "port_pair_csv": str(
                    validation
                    / "emx_first_validation_gate_20260613"
                    / "emx_first_validation_gate_port_pair_sensitivity.csv"
                )
            },
        },
    )
    (validation / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_report.md").write_text("# report\n", encoding="utf-8")
    (validation / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_metrics.csv").write_text(
        _metrics_csv_text(("freq_hz", "freq_ghz", "lp_nh", "ls_nh", "k", "qp", "qs", "cm_single_primary_ff")),
        encoding="utf-8",
    )
    (validation / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_ads_style_metrics.png").write_bytes(_valid_png_bytes())
    (validation / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_core_metrics.png").write_bytes(_valid_png_bytes())
    (validation / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_port_pair_sensitivity.csv").write_text(
        _port_pair_sensitivity_csv_text(),
        encoding="utf-8",
    )
    (validation / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_port_pair_sensitivity.png").write_bytes(
        _valid_png_bytes()
    )
    return validation


def _write_tarball(root: Path, validation: Path) -> tuple[Path, Path]:
    tarball = root / "validation_20260613_transfer.tar.gz"
    with tarfile.open(tarball, "w:gz") as archive:
        archive.add(validation, arcname=validation.name)
    sha_record = root / "validation_20260613_transfer.tar.gz.sha256"
    sha_record.write_text(f"{_sha256(tarball)}  {tarball.name}\n", encoding="utf-8")
    return tarball, sha_record


class VerifyTargetEmxPostrunPackageScriptTest(TransformerToolboxTestBase):
    def test_accepts_validation_package_and_matching_local_emx(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx_s4p = root / "emx.s4p"
            emx_s4p.write_bytes(b"real emx bytes")
            validation = _write_fixture_validation_dir(root, emx_sha=_sha256(emx_s4p))
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--emx-s4p",
                    str(emx_s4p),
                    "--require-emx-s4p",
                    "--out-dir",
                    str(root / "out"),
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["local EMX S4P SHA"]["status"], "PASS")
            self.assertEqual(checks["post-run validation artifact content"]["status"], "PASS")
            bundle = summary["accepted_emx_reference_bundle"]
            self.assertEqual(bundle["status"], "READY_FOR_HFSS")
            self.assertEqual(bundle["emx_s4p"]["path"], str(emx_s4p.resolve()))
            self.assertEqual(bundle["emx_s4p"]["sha256"], _sha256(emx_s4p))
            self.assertEqual(bundle["mars_emx_sha256"], _sha256(emx_s4p))
            self.assertIn("emx_first_core_plot", bundle["artifacts"])
            self.assertTrue(bundle["artifacts"]["emx_first_core_plot"]["exists"])
            self.assertIn("port_pair_sensitivity_csv", bundle["artifacts"])

    def test_keeps_evidence_only_when_local_emx_is_not_supplied(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(["--tarball", str(tarball), "--sha-record", str(sha_record), "--out-dir", str(root / "out")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(summary["decision"], "VALIDATION_EVIDENCE_TRANSFERRED_NO_LOCAL_EMX")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["local EMX S4P SHA"]["status"], "WARN")
            self.assertEqual(summary["accepted_emx_reference_bundle"]["status"], "NOT_READY")
            self.assertIsNone(summary["accepted_emx_reference_bundle"]["emx_s4p"])

    def test_rejects_failed_emx_first_gate(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64, emx_first_status="FAIL")
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "DO_NOT_IMPORT_TARGET_EMX_REFERENCE")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["EMX-first gate status"]["status"], "FAIL")

    def test_rejects_emx_first_summary_without_required_photo_anchor_check(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            summary_path = validation / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_summary.json"
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            data["checks"] = [item for item in data["checks"] if item["name"] != "ADS photo anchor"]
            summary_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["EMX-first gate required physics/photo/port checks"]["status"], "FAIL")
            self.assertIn("ADS photo anchor", checks["EMX-first gate required physics/photo/port checks"]["detail"])

    def test_rejects_emx_first_summary_without_curve_window_argument_evidence(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            summary_path = validation / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_summary.json"
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            data["physical_curve_gate"].pop("min_window_abs_k")
            summary_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["EMX-first gate curve-window arguments"]["status"], "FAIL")
            self.assertIn("min_window_abs_k", checks["EMX-first gate curve-window arguments"]["detail"])

    def test_rejects_touchstone_summary_without_coupling_argument_evidence(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            summary_path = validation / "touchstone_physical_gate" / "touchstone_transformer_audit_summary.json"
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            data["arguments"].pop("min_target_abs_k")
            summary_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["Touchstone physical gate coupling arguments"]["status"], "FAIL")

    def test_rejects_touchstone_summary_without_required_physics_checks(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            summary_path = validation / "touchstone_physical_gate" / "touchstone_transformer_audit_summary.json"
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            data["checks"] = [item for item in data["checks"] if item["name"] != "positive metric window"]
            summary_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["Touchstone physical gate required physics checks"]["status"], "FAIL")

    def test_rejects_invalid_mars_emx_sha_record(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="not-a-sha")
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            self.assertEqual(summary["decision"], "DO_NOT_IMPORT_TARGET_EMX_REFERENCE")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["MARS-recorded EMX S4P SHA"]["status"], "FAIL")
            self.assertIn("invalid SHA256 digest", checks["MARS-recorded EMX S4P SHA"]["detail"])

    def test_rejects_empty_postrun_artifact_file(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            (validation / "touchstone_physical_gate" / "touchstone_ads_equivalent_metrics.png").write_bytes(b"")
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["post-run validation artifacts"]["status"], "FAIL")
            self.assertIn("empty=", checks["post-run validation artifacts"]["detail"])

    def test_rejects_missing_emx_first_core_metric_figure(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            emx_s4p = root / "emx.s4p"
            emx_s4p.write_bytes(b"real emx bytes")
            validation = _write_fixture_validation_dir(root, emx_sha=_sha256(emx_s4p))
            (validation / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_core_metrics.png").unlink()
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--emx-s4p",
                    str(emx_s4p),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["post-run validation artifacts"]["status"], "FAIL")
            self.assertIn("emx_first_validation_gate_core_metrics.png", checks["post-run validation artifacts"]["detail"])

    def test_rejects_postrun_artifact_with_invalid_png_content(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            (validation / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_ads_style_metrics.png").write_bytes(
                b"\x89PNG\r\n\x1a\nplot"
            )
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["post-run validation artifact content"]["status"], "FAIL")
            self.assertIn("missing PNG IHDR chunk", checks["post-run validation artifact content"]["detail"])

    def test_rejects_postrun_artifact_with_tiny_placeholder_png(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            (validation / "touchstone_physical_gate" / "touchstone_ads_equivalent_metrics.png").write_bytes(_tiny_png_bytes())
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["post-run validation artifact content"]["status"], "FAIL")
            self.assertIn("PNG dimensions 1x1 below minimum", checks["post-run validation artifact content"]["detail"])

    def test_rejects_postrun_artifact_with_blank_placeholder_png(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            (validation / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_core_metrics.png").write_bytes(
                _blank_png_bytes()
            )
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["post-run validation artifact content"]["status"], "FAIL")
            self.assertIn("blank or nearly constant PNG", checks["post-run validation artifact content"]["detail"])

    def test_rejects_postrun_artifact_with_missing_csv_columns(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            (validation / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_metrics.csv").write_text(
                "freq_hz,lp_nh\n15000000000,1.0\n",
                encoding="utf-8",
            )
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["post-run validation artifact content"]["status"], "FAIL")
            self.assertIn("missing columns", checks["post-run validation artifact content"]["detail"])

    def test_rejects_metrics_csv_with_stale_frequency_grid(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            (validation / "touchstone_physical_gate" / "touchstone_transformer_metrics.csv").write_text(
                "freq_hz,freq_ghz,lp_nh,ls_nh,m_nh,k,qp,qs\n"
                "15000000000,15,1.0,1.2,0.55,0.5,10,12\n",
                encoding="utf-8",
            )
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["post-run validation artifact content"]["status"], "FAIL")
            self.assertIn("frequency points expected 451, got 1", checks["post-run validation artifact content"]["detail"])

    def test_rejects_metrics_csv_with_nonfinite_physics_value(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            metrics_path = validation / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_metrics.csv"
            rows = metrics_path.read_text(encoding="utf-8").splitlines()
            header = rows[0].split(",")
            k_index = header.index("k")
            first_values = rows[1].split(",")
            first_values[k_index] = "nan"
            rows[1] = ",".join(first_values)
            metrics_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["post-run validation artifact content"]["status"], "FAIL")
            self.assertIn("non-finite numeric value for k", checks["post-run validation artifact content"]["detail"])

    def test_rejects_missing_emx_first_port_pair_sensitivity_artifact(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            (validation / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_port_pair_sensitivity.png").unlink()
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["post-run validation artifacts"]["status"], "FAIL")
            self.assertIn("emx_first_validation_gate_port_pair_sensitivity.png", checks["post-run validation artifacts"]["detail"])

    def test_rejects_port_pair_csv_without_passing_approved_pair(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            (
                validation
                / "emx_first_validation_gate_20260613"
                / "emx_first_validation_gate_port_pair_sensitivity.csv"
            ).write_text(_port_pair_sensitivity_csv_text(approved_status="FAIL", approved_error=6.0), encoding="utf-8")
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["EMX-first port-pair sensitivity CSV gate"]["status"], "FAIL")
            self.assertIn("approved port pair 1,2:3,4 status='FAIL'", checks["EMX-first port-pair sensitivity CSV gate"]["detail"])

    def test_rejects_port_pair_csv_with_missing_pair_coverage(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            rows = _port_pair_sensitivity_csv_text().splitlines()
            (
                validation
                / "emx_first_validation_gate_20260613"
                / "emx_first_validation_gate_port_pair_sensitivity.csv"
            ).write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")
            tarball, sha_record = _write_tarball(root, validation)

            status = mod.main(
                [
                    "--tarball",
                    str(tarball),
                    "--sha-record",
                    str(sha_record),
                    "--out-dir",
                    str(root / "out"),
                    "--no-fail-exit",
                ]
            )

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "FAIL")
            checks = {item["name"]: item for item in summary["checks"]}
            self.assertEqual(checks["EMX-first port-pair sensitivity CSV gate"]["status"], "FAIL")
            self.assertIn("port-pair row count expected 24, got 23", checks["EMX-first port-pair sensitivity CSV gate"]["detail"])

    def test_extracts_validation_package_when_tarfile_filter_argument_is_unavailable(self) -> None:
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            validation = _write_fixture_validation_dir(root, emx_sha="a" * 64)
            tarball, sha_record = _write_tarball(root, validation)
            original_extractall = tarfile.TarFile.extractall
            calls: list[object] = []

            def fake_extractall(self, path=".", members=None, *, numeric_owner=False, filter=None):
                calls.append(filter)
                if filter is not None:
                    raise TypeError("extractall() got an unexpected keyword argument 'filter'")
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    return original_extractall(self, path, members, numeric_owner=numeric_owner)

            with mock.patch.object(tarfile.TarFile, "extractall", fake_extractall):
                status = mod.main(["--tarball", str(tarball), "--sha-record", str(sha_record), "--out-dir", str(root / "out")])

            self.assertEqual(status, 0)
            summary = json.loads((root / "out" / "target_emx_postrun_import_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["overall_status"], "PASS")
            self.assertEqual(calls.count("data"), 1)
            self.assertEqual(calls.count(None), 1)
