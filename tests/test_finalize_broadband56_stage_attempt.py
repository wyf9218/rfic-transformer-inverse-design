from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path

from rfic_transformer_inverse_design.campaigns.broadband56_stage_progress import (
    ATTEMPT_FAILURE_ACCOUNTING_FIELDS,
    validate_stage_progress_receipt,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "finalize_broadband56_stage_attempt.py"
SPEC = importlib.util.spec_from_file_location("stage_attempt_finalizer", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _evidence(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha(path),
    }


def _write_prior_golden(campaign_root: Path, tmp_path: Path) -> None:
    prior = tmp_path / "prior_golden"
    geometry = "f" * 64
    attempt = _write_csv(
        prior / "attempt.csv",
        ["attempt_id", "geometry_sha256", "terminal_stage"],
        [{"attempt_id": "golden", "geometry_sha256": geometry, "terminal_stage": "ACCEPTED"}],
    )
    accepted = _write_csv(
        prior / "accepted.csv", ["geometry_sha256"], [{"geometry_sha256": geometry}]
    )
    rejected = _write_csv(prior / "rejected.csv", ["geometry_sha256"], [])
    exact = _write_csv(
        prior / "exact.csv",
        ["candidate_id_sha256", "geometry_sha256"],
        [{"candidate_id_sha256": geometry, "geometry_sha256": geometry}],
    )
    s4p = _write_csv(
        prior / "s4p.csv", ["geometry_sha256"], [{"geometry_sha256": geometry}]
    )
    features = _write_csv(
        prior / "features.csv",
        ["geometry_sha256", "frequency_hz"],
        [
            {
                "geometry_sha256": geometry,
                "frequency_hz": str(5_000_000_000 + point * 1_000_000_000),
            }
            for point in range(56)
        ],
    )
    funnel = _write_csv(
        prior / "funnel.csv",
        ["stage", "count"],
        [
            {
                "stage": field,
                "count": 1 if field in {"raw_geometry_candidates", "accepted_geometries"} else 0,
            }
            for field in ATTEMPT_FAILURE_ACCOUNTING_FIELDS
        ],
    )
    raw_receipt = prior / "RAW_PRODUCTS_RECEIPT.json"
    raw_receipt.write_text(
        json.dumps({"overall_status": "PASS", "outputs": {"long_features": _evidence(features)}})
        + "\n",
        encoding="utf-8",
    )
    stage_dir = campaign_root / "stages" / "000001_golden"
    stage_dir.mkdir(parents=True)
    stage_receipt = {
        "overall_status": "PASS",
        "stage": "GOLDEN",
        "accepted_unique_geometries": 1,
        "artifacts": {
            "attempt_ledger": _evidence(attempt),
            "accepted_geometry_index": _evidence(accepted),
            "rejected_geometry_index": _evidence(rejected),
            "exact_gds_emx_receipt_index": _evidence(exact),
            "s4p_artifact_index": _evidence(s4p),
            "failure_funnel": _evidence(funnel),
            "raw_products_receipt": _evidence(raw_receipt),
        },
    }
    (stage_dir / "STAGE_RECEIPT.json").write_text(
        json.dumps(stage_receipt) + "\n", encoding="utf-8"
    )


def _args(tmp_path: Path, *, accepted: int, raw: int, stage: str = "PILOT_32") -> argparse.Namespace:
    campaign_root = tmp_path / "campaign"
    (campaign_root / "stages").mkdir(parents=True)
    if stage == "PILOT_32":
        _write_prior_golden(campaign_root, tmp_path)
    backend = tmp_path / "backend.json"
    backend.write_text("{}\n", encoding="utf-8")
    authorization = tmp_path / "authorization.json"
    authorization.write_text("{}\n", encoding="utf-8")
    accepted_hashes = [f"{index + 1:064x}" for index in range(accepted)]
    rejected_hashes = [f"{accepted + index + 1:064x}" for index in range(raw - accepted)]
    attempt_rows = [
        {"attempt_id": f"a{index}", "geometry_sha256": value, "terminal_stage": "ACCEPTED"}
        for index, value in enumerate(accepted_hashes)
    ] + [
        {"attempt_id": f"r{index}", "geometry_sha256": value, "terminal_stage": "ANALYTICAL_FAILURE"}
        for index, value in enumerate(rejected_hashes)
    ]
    accepted_rows = [{"geometry_sha256": value} for value in accepted_hashes]
    rejected_rows = [{"geometry_sha256": value} for value in rejected_hashes]
    exact_rows = [
        {"candidate_id_sha256": value, "geometry_sha256": value}
        for value in accepted_hashes
    ]
    s4p_rows = [{"geometry_sha256": value} for value in accepted_hashes]
    feature_rows = [
        {"geometry_sha256": value, "frequency_hz": str(5_000_000_000 + point * 1_000_000_000)}
        for value in accepted_hashes
        for point in range(56)
    ]
    paths = {
        "attempt_ledger": _write_csv(
            tmp_path / "attempt.csv",
            ["attempt_id", "geometry_sha256", "terminal_stage"],
            attempt_rows,
        ),
        "accepted_geometry_increment": _write_csv(
            tmp_path / "accepted.csv", ["geometry_sha256"], accepted_rows
        ),
        "rejected_geometry_increment": _write_csv(
            tmp_path / "rejected.csv", ["geometry_sha256"], rejected_rows
        ),
        "exact_gds_emx_receipt_index": _write_csv(
            tmp_path / "exact.csv",
            ["candidate_id_sha256", "geometry_sha256"],
            exact_rows,
        ),
        "s4p_artifact_index": _write_csv(
            tmp_path / "s4p.csv", ["geometry_sha256"], s4p_rows
        ),
        "long_features": _write_csv(
            tmp_path / "features.csv",
            ["geometry_sha256", "frequency_hz"],
            feature_rows,
        ),
    }
    funnel = {field: 0 for field in ATTEMPT_FAILURE_ACCOUNTING_FIELDS}
    funnel["raw_geometry_candidates"] = raw
    funnel["accepted_geometries"] = accepted
    funnel["analytical_failures"] = raw - accepted
    paths["failure_funnel"] = _write_csv(
        tmp_path / "funnel.csv",
        ["stage", "count"],
        [{"stage": field, "count": funnel[field]} for field in ATTEMPT_FAILURE_ACCOUNTING_FIELDS],
    )
    return argparse.Namespace(
        stage=stage,
        campaign_root=str(campaign_root),
        backend_identity_manifest=str(backend),
        full_campaign_receipt=str(authorization),
        **{field: str(path) for field, path in paths.items()},
        out_dir=str(tmp_path / "out"),
        simulator_action_taken=False,
    )


def test_shortfall_writes_valid_nonterminal_progress_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(MODULE, "validate_stage_receipt_chain", lambda *args, **kwargs: [])
    args = _args(tmp_path, accepted=20, raw=24)
    out_dir = Path(args.out_dir)

    result = MODULE.finalize_stage_attempt(args, out_dir=out_dir)

    assert result["decision"] == "CONTINUE_SAMPLING"
    progress_path = out_dir / MODULE.PROGRESS_RECEIPT_NAME
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["accepted_before"] == 1
    assert progress["accepted_after"] == 21
    assert progress["remaining_after"] == 11
    assert validate_stage_progress_receipt(
        progress,
        stage="PILOT_32",
        attempt_index=1,
        accepted_before=1,
        prior_progress_receipt_sha256=None,
        backend_manifest_sha256=_sha(Path(args.backend_identity_manifest)),
        authorization_receipt_sha256=_sha(Path(args.full_campaign_receipt)),
        verify_artifacts=True,
        artifact_root=progress_path.parent,
    ) == []
    assert result["cumulative_stage_inputs"] is None


def test_exact_frozen_boundary_binds_cumulative_inputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(MODULE, "validate_stage_receipt_chain", lambda *args, **kwargs: [])
    monkeypatch.setattr(MODULE, "FROZEN_INTERMEDIATE_ACCEPTED_BOUNDARIES", (21,))
    args = _args(tmp_path, accepted=20, raw=24)
    out_dir = Path(args.out_dir)

    result = MODULE.finalize_stage_attempt(args, out_dir=out_dir)

    progress = json.loads(
        (out_dir / MODULE.PROGRESS_RECEIPT_NAME).read_text(encoding="utf-8")
    )
    cumulative = progress["round_cumulative_inputs"]
    assert result["decision"] == "CONTINUE_SAMPLING"
    assert cumulative is not None
    assert set(cumulative) == set(MODULE.STAGE_PROGRESS_ARTIFACT_FIELDS)
    for record in cumulative.values():
        path = Path(record["path"])
        assert path.is_file()
        assert _sha(path) == record["sha256"]


def test_exact_target_writes_cumulative_inputs_without_progress(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(MODULE, "validate_stage_receipt_chain", lambda *args, **kwargs: [])
    args = _args(tmp_path, accepted=31, raw=31)
    out_dir = Path(args.out_dir)

    result = MODULE.finalize_stage_attempt(args, out_dir=out_dir)

    assert result["decision"] == "STAGE_TARGET_REACHED"
    assert result["accepted_after"] == 32
    assert not (out_dir / MODULE.PROGRESS_RECEIPT_NAME).exists()
    cumulative = result["cumulative_stage_inputs"]
    assert cumulative is not None
    for record in cumulative.values():
        assert Path(record["path"]).is_file()
        assert _sha(Path(record["path"])) == record["sha256"]
    accepted_rows = list(
        csv.DictReader(Path(cumulative["accepted_geometry_increment"]["path"]).open(newline="", encoding="utf-8"))
    )
    feature_rows = list(
        csv.DictReader(Path(cumulative["long_features"]["path"]).open(newline="", encoding="utf-8"))
    )
    assert len(accepted_rows) == 32
    assert len(feature_rows) == 32 * 56


def test_overshoot_fails_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(MODULE, "validate_stage_receipt_chain", lambda *args, **kwargs: [])
    args = _args(tmp_path, accepted=32, raw=32)

    try:
        MODULE.finalize_stage_attempt(args, out_dir=Path(args.out_dir))
    except MODULE.StageAttemptFinalizationError as exc:
        assert "overshoots" in str(exc)
    else:
        raise AssertionError("overshoot must fail")


def test_feature_grain_mismatch_fails_before_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(MODULE, "validate_stage_receipt_chain", lambda *args, **kwargs: [])
    args = _args(tmp_path, accepted=2, raw=2)
    features = Path(args.long_features)
    rows = list(csv.DictReader(features.open(newline="", encoding="utf-8")))
    _write_csv(features, ["geometry_sha256", "frequency_hz"], rows[:-1])

    try:
        MODULE.finalize_stage_attempt(args, out_dir=Path(args.out_dir))
    except MODULE.StageAttemptFinalizationError as exc:
        assert "accepted_count times 56" in str(exc)
    else:
        raise AssertionError("wrong feature grain must fail")
