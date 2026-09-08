"""Synthetic standalone-wrapper tests; no Calibre/native subprocess executes."""
from contextlib import contextmanager
import csv
import json
from pathlib import Path
import sys

import pytest

from research.broadband56_nn import frequency_research_calibre as calibre
from research.broadband56_nn.frequency_research_emx import ResearchEmxError
from rfic_transformer_inverse_design.campaigns import broadband56_gds_identity as identity


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return calibre.pin(path)


def csv_write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return calibre.pin(path)


@pytest.fixture
def packet(tmp_path, monkeypatch):
    source = tmp_path / "synthetic_gds_hash.py"
    source.write_text("def gds_timestamp_normalized_sha256(path):\n    return 'SYNTHETIC_NORMALIZED'\n")
    gds = tmp_path / "synthetic.gds"
    gds.write_bytes(b"SYNTHETIC_NOT_A_GDS")
    rows = [dict(candidate_id_sha256=f"{i:064x}", candidate_geometry_identity_sha256=f"{i+2:064x}",
        gds_path=str(gds), gds_sha256=calibre.pin(gds)["sha256"],
        gds_timestamp_normalized_sha256="SYNTHETIC_NORMALIZED",
        gds_timestamp_normalization_algorithm="SYNTHETIC", geometry_audit_path=str(tmp_path / "audit.json"),
        top_cell="TRANSFORMER") for i in range(2)]
    script = tmp_path / "synthetic_standalone.py"
    script.write_text("raise RuntimeError('THIS STUB MUST NEVER EXECUTE')\n")
    request = dict(schema="frequency_research_calibre_request.v1", input_index=csv_write(tmp_path / "input.csv", rows),
        script=calibre.pin(script), gds_hash_source=calibre.pin(source), runtime_sources=[],
        repo=str(Path(calibre.__file__).resolve().parents[2]), out=str(tmp_path / "out"),
        global_lock_path=str(tmp_path / "global.lock"))
    path = tmp_path / "request.json"
    write(path, request)
    monkeypatch.setattr(identity, "gds_timestamp_normalized_sha256", lambda p: "SYNTHETIC_NORMALIZED")
    monkeypatch.setitem(sys.modules, "rfic_transformer_inverse_design.layout.gds_hash", None)
    return path, request, rows


@pytest.fixture
def native_stub(packet, monkeypatch):
    calls = {"lease": [], "runpy": [], "subprocess": []}

    @contextmanager
    def lease(path, inherited_fd):
        calls["lease"].append((path, inherited_fd))
        yield 777

    def run(*args, **kwargs):
        calls["subprocess"].append((args, kwargs))
        return None

    def native(script, run_name):
        calls["runpy"].append((script, run_name, list(sys.argv)))
        calibre.subprocess.run(["SYNTHETIC_NEVER_EXECUTED"], pass_fds=(888,))
        output = Path(packet[1]["out"])
        csv_write(output / "drc_index.csv", [dict(row, overall_status="PASS") for row in packet[2]])
        write(output / "tsmc65_calibre_macro_drc_batch_summary.json", {"synthetic": True})

    monkeypatch.setattr(calibre, "global_lease", lease)
    monkeypatch.setattr(calibre.subprocess, "run", run)
    monkeypatch.setattr(calibre.runpy, "run_path", native)
    return calls


def test_exact_standalone_arguments_and_inherited_lease_without_simulator(packet, native_stub):
    before_argv = sys.argv
    before_run = calibre.subprocess.run
    calibre.run(packet[0], 123)
    script = packet[1]["script"]["path"]
    assert native_stub["runpy"] == [(script, "__main__", [script,
        "--input-index-csv", packet[1]["input_index"]["path"], "--out-dir", packet[1]["out"],
        "--maximum-candidates", "2", "--calibre-module", "mentor/old/2025", "--nice-level", "19"])]
    assert native_stub["lease"] == [(packet[1]["global_lock_path"], 123)]
    assert set(native_stub["subprocess"][0][1]["pass_fds"]) == {777, 888}
    assert sys.argv is before_argv and calibre.subprocess.run is before_run
    receipt = calibre.read_json(Path(packet[1]["out"]) / "RESEARCH_WRAPPER_RECEIPT.json")
    assert receipt["N_candidates"] == receipt["N_pass"] == 2
    assert receipt["production_modified"] is receipt["solver_started"] is False
    with pytest.raises(ResearchEmxError, match="never repeat"):
        calibre.run(packet[0], 123)
    assert len(native_stub["runpy"]) == 1


def test_existing_partial_output_no_launch(packet, native_stub):
    Path(packet[1]["out"]).mkdir()
    with pytest.raises(ResearchEmxError, match="never repeat"):
        calibre.run(packet[0], 123)
    assert not native_stub["runpy"] and not native_stub["lease"]


@pytest.mark.parametrize("role", ["input_index", "script", "gds_hash_source"])
def test_input_pin_tamper_rejected_before_native(packet, native_stub, role):
    Path(packet[1][role]["path"]).write_text("SYNTHETIC_TAMPER")
    with pytest.raises(ResearchEmxError, match="mismatch"):
        calibre.run(packet[0], 123)
    assert not native_stub["runpy"]


def test_actual_raw_gds_change_rejected_before_native(packet, native_stub):
    Path(packet[2][0]["gds_path"]).write_bytes(b"SYNTHETIC_CHANGED")
    with pytest.raises(ResearchEmxError, match="GDS changed"):
        calibre.run(packet[0], 123)
    assert not native_stub["runpy"]


def test_normalized_identity_change_rejected(packet, native_stub, monkeypatch):
    monkeypatch.setattr(identity, "gds_timestamp_normalized_sha256", lambda p: "OTHER")
    with pytest.raises(ResearchEmxError, match="normalized GDS"):
        calibre.run(packet[0], 123)
    assert not native_stub["runpy"]


def test_incomplete_or_foreign_native_candidate_evidence_rejected(packet, native_stub, monkeypatch):
    def incomplete(*a, **k):
        output = Path(packet[1]["out"])
        csv_write(output / "drc_index.csv", [dict(packet[2][0], overall_status="PASS")])
    monkeypatch.setattr(calibre.runpy, "run_path", incomplete)
    with pytest.raises(ResearchEmxError, match="Incomplete DRC"):
        calibre.run(packet[0], 123)
    assert not (Path(packet[1]["out"]) / "RESEARCH_WRAPPER_RECEIPT.json").exists()


def test_native_failure_exit_one_still_accounts_original_candidates(packet, native_stub, monkeypatch):
    native = calibre.runpy.run_path

    def failed(*a, **k):
        native(*a, **k)
        rows = [dict(packet[2][0], overall_status="FAIL"), dict(packet[2][1], overall_status="PASS")]
        csv_write(Path(packet[1]["out"]) / "drc_index.csv", rows)
        raise SystemExit(1)

    monkeypatch.setattr(calibre.runpy, "run_path", failed)
    calibre.run(packet[0], 123)
    receipt = calibre.read_json(Path(packet[1]["out"]) / "RESEARCH_WRAPPER_RECEIPT.json")
    assert receipt["native_returncode"] == 1 and receipt["N_candidates"] == 2 and receipt["N_pass"] == 1


def test_unexpected_exit_preserves_outputs_and_restores_process_globals(packet, native_stub, monkeypatch):
    before_argv, before_run = sys.argv, calibre.subprocess.run

    def failed(*a, **k):
        raise SystemExit(2)

    monkeypatch.setattr(calibre.runpy, "run_path", failed)
    with pytest.raises(ResearchEmxError, match="Unexpected standalone"):
        calibre.run(packet[0], 123)
    assert sys.argv is before_argv and calibre.subprocess.run is before_run
