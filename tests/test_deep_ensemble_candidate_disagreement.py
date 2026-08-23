from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import json
import sys

import numpy as np
import pytest


FEATURES = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_deep_ensemble_candidate_disagreement.py"
    spec = importlib.util.spec_from_file_location("deep_ensemble_candidate_disagreement_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _member(path: Path, member: int, *, mismatch: bool = False) -> None:
    fields = ["candidate_id", "geom__g0", "geom__g1"] + [f"pred_{feature}" for feature in FEATURES]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index in range(12):
            row = {
                "candidate_id": f"candidate_{index:03d}",
                "geom__g0": index + (0.5 if mismatch and index == 3 else 0.0),
                "geom__g1": 2.0 * index,
            }
            for axis, feature in enumerate(FEATURES):
                row[f"pred_{feature}"] = 1.0 + axis + 0.1 * index + 0.01 * member
            writer.writerow(row)


def test_combines_five_members_with_exact_geometry_contract(tmp_path):
    module = _load()
    members = []
    for member in range(5):
        path = tmp_path / f"member_{member}.csv"
        _member(path, member)
        members.append(path)
    out_dir = tmp_path / "out"
    argv = []
    for path in members:
        argv.extend(["--member-csv", str(path)])
    argv.extend(["--out-dir", str(out_dir), "--min-candidates", "10"])
    assert module.main(argv) == 0

    summary = json.loads((out_dir / "deep_ensemble_candidate_prediction_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["member_count"] == 5
    assert summary["checks"]["candidate_geometry_sha_match"] is True
    rows = list(csv.DictReader((out_dir / "deep_ensemble_candidate_predictions.csv").open()))
    assert len(rows) == 12
    assert float(rows[0]["pred_lp_nh_center"]) == pytest.approx(1.02)
    assert float(rows[0]["pred_uncertainty_lp_nh_center"]) == pytest.approx(np.std([1.0, 1.01, 1.02, 1.03, 1.04]))
    assert rows[0]["pred_source"] == "deep_ensemble_disagreement_for_candidate_priority_only"


def test_rejects_member_geometry_mismatch(tmp_path):
    module = _load()
    argv = []
    for member in range(5):
        path = tmp_path / f"member_{member}.csv"
        _member(path, member, mismatch=member == 4)
        argv.extend(["--member-csv", str(path)])
    out_dir = tmp_path / "out"
    argv.extend(["--out-dir", str(out_dir)])
    assert module.main(argv) == 2
    summary = json.loads((out_dir / "deep_ensemble_candidate_prediction_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["candidate_geometry_sha_match"] is False


def test_rejects_implicit_row_number_identity(tmp_path):
    module = _load()
    argv = []
    for member in range(5):
        path = tmp_path / f"member_{member}.csv"
        _member(path, member)
        rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
        fields = [field for field in rows[0] if field != "candidate_id"]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                row.pop("candidate_id")
                writer.writerow(row)
        argv.extend(["--member-csv", str(path)])
    out_dir = tmp_path / "out"
    argv.extend(["--out-dir", str(out_dir)])
    assert module.main(argv) == 2
    summary = json.loads((out_dir / "deep_ensemble_candidate_prediction_summary.json").read_text())
    assert summary["checks"]["id_column_present"] is False
