"""Optional exact private-delegate fixtures. No PDK or simulator is executed."""
import ast
import csv
import hashlib
import importlib.util
import json
import os
import threading
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns import broadband56_dispatch as dispatch
from tests.test_run_broadband56_v2_calibre_batch import _load_runner_module


def load_delegate():
    path = os.environ.get("BROADBAND56_TEST_CALIBRE_DELEGATE")
    if not path:
        pytest.skip("exact private scheduling delegate not supplied")
    spec = importlib.util.spec_from_file_location("_private_calibre_refill_fixture", path)
    module = importlib.util.module_from_spec(spec)
    with _load_runner_module()._legacy_gds_hash_compat_module():
        spec.loader.exec_module(module)
    return module


def test_private_non_main_functions_and_scientific_constants_are_unchanged():
    old = os.environ.get("BROADBAND56_TEST_CALIBRE_ORIGINAL")
    new = os.environ.get("BROADBAND56_TEST_CALIBRE_DELEGATE")
    if not old or not new:
        pytest.skip("exact original and replacement delegate required")
    def non_main(path):
        return [ast.dump(n, include_attributes=False) for n in ast.parse(Path(path).read_text()).body
                if not (isinstance(n, ast.FunctionDef) and n.name == "main")]
    assert non_main(old) == non_main(new)
    from rfic_transformer_inverse_design.campaigns.broadband56_golden_stage import GOLDEN_COMPATIBLE_SCHEDULER_REBINDS
    assert ("script_identities", "calibre_batch_delegate",
            hashlib.sha256(Path(old).read_bytes()).hexdigest(),
            hashlib.sha256(Path(new).read_bytes()).hexdigest()) in GOLDEN_COMPATIBLE_SCHEDULER_REBINDS


@pytest.mark.parametrize("fail_candidate", [False, True])
def test_private_delegate_uses_refill_and_keeps_input_order(tmp_path, monkeypatch, fail_candidate):
    module = load_delegate()
    rows = [{name: "fixture" for name in module.REQUIRED_INDEX_FIELDS} for _ in range(96)]
    for index, row in enumerate(rows):
        row.update(candidate_id_sha256=hashlib.sha256(str(index).encode()).hexdigest(),
                   candidate_geometry_identity_sha256=hashlib.sha256(f"geometry{index}".encode()).hexdigest())
    source = tmp_path/"input.csv"
    with source.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=module.REQUIRED_INDEX_FIELDS)
        writer.writeheader(); writer.writerows(rows)
    archive = tmp_path/"fixture.archive"; archive.write_bytes(b"not a PDK")
    deck = b"1P9M PROCESS WITH 6X1Z1U METAL OPTION\nT-N65-CL-DR-001\nVER 2.6_2\n"
    guide = b"fixture only"
    monkeypatch.setattr(module, "_extract_foundry_inputs", lambda *_: (deck, guide))
    monkeypatch.setattr(module, "_calibre_version", lambda _: "fixture-not-Calibre")
    monkeypatch.setenv("BROADBAND56_MAX_CONCURRENCY", "48")
    monkeypatch.setattr(dispatch, "stage_admission", lambda capacity: (lambda: 48))
    later = threading.Event()
    ids = [r["candidate_id_sha256"] for r in rows]
    def run(*, row, **_):
        index = ids.index(row["candidate_id_sha256"])
        if index == 0:
            assert later.wait(3), "refill stopped behind slow candidate"
        if index == 49:
            later.set()
        if fail_candidate and index == 3:
            raise RuntimeError("candidate failed, fixture only")
        return {"candidate_id_sha256": row["candidate_id_sha256"],
                "candidate_geometry_identity_sha256": row["candidate_geometry_identity_sha256"],
                "overall_status": "PASS", "drc_violation_count": 0,
                "blocking_drc_violation_count": 0, "documented_warning_count": 0}
    monkeypatch.setattr(module, "_run_candidate", run)
    out = tmp_path/"out"
    digest = lambda b: hashlib.sha256(b).hexdigest()
    rc = module.main(["--input-index-csv", str(source), "--out-dir", str(out),
          "--foundry-archive", str(archive), "--expected-archive-sha256", digest(archive.read_bytes()),
          "--expected-deck-sha256", digest(deck), "--expected-user-guide-sha256", digest(guide)])
    assert rc == int(fail_candidate)
    summary = json.loads((out/"tsmc65_calibre_macro_drc_batch_summary.json").read_text())
    assert summary["pass_count"] == 96 - int(fail_candidate)
    assert summary["fail_count"] == int(fail_candidate)
    with (out/"drc_index.csv").open() as f:
        results = list(csv.DictReader(f))
    assert [r["candidate_id_sha256"] for r in results] == ids
    receipt = json.loads((out/"dispatch/DISPATCH_RECEIPT.json").read_text())
    assert receipt["executor_capacity"] == receipt["peak_inflight_delegates"] == 48
    assert receipt["accepted_increment"] == 0
