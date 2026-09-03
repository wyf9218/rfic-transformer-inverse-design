from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    canonical_geometry_sha256,
)
from rfic_transformer_inverse_design.layout import foundry_audit as audit_module
from rfic_transformer_inverse_design.layout.foundry_audit import (
    AUDIT_SCHEMA,
    FoundryLayoutAuditError,
    load_and_validate_foundry_layout_audit,
    produce_foundry_layout_audit,
    validate_foundry_layout_audit,
)
from tests.test_run_broadband56_v2_cadence_streamout_batch import (
    _build_real_foundry_layout,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "research" / "FOUNDRY_LAYOUT_AUDIT_CONTRACT.json"


@pytest.fixture(scope="module")
def valid_audit(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("foundry-audit")
    layout_dir, geometry = _build_real_foundry_layout(root)
    config = root / "config.yaml"
    config.write_text(
        "emx:\n"
        "  foundry_layout:\n"
        "    enabled: true\n"
        "    manufacturing_grid_um: 0.005\n"
        "    power_line_stitch_pad_depth_um: 6.0\n"
        "    shield_strap_width_um: 10.0\n"
        "    shield_strap_pitch_um: 20.0\n",
        encoding="utf-8",
    )
    geometry_sha = canonical_geometry_sha256(geometry)
    candidate = {
        "candidate_id": "fixture-candidate",
        "candidate_id_sha256": geometry_sha,
        "candidate_geometry_identity_sha256": geometry_sha,
        "geometry_sha256": geometry_sha,
        **{f"geom__{name}": value for name, value in geometry.items()},
    }
    gds = layout_dir / "transformer_layout.gds"
    output = layout_dir / "foundry_layout_audit.json"
    forbidden = mock.Mock(side_effect=AssertionError("external process launch"))
    with (
        mock.patch.object(subprocess, "run", forbidden),
        mock.patch.object(subprocess, "Popen", forbidden),
        mock.patch.object(os, "system", forbidden),
    ):
        audit = produce_foundry_layout_audit(
            gds_path=gds,
            source_audit_path=layout_dir / "foundry_layout_source_audit.json",
            power_line_audit_path=layout_dir / "power_line_8port_geometry.json",
            config_path=config,
            contract_path=CONTRACT,
            candidate=candidate,
            stage_id="GOLDEN",
            output_path=output,
        )

    forbidden.assert_not_called()
    assert output.is_file() and output.stat().st_size > 0
    return {
        "audit": audit,
        "audit_path": output,
        "candidate": candidate,
        "config": config,
        "gds": gds,
        "geometry_sha": geometry_sha,
        "contract_sha": _sha(CONTRACT),
        "config_sha": _sha(config),
        "gds_sha": _sha(gds),
    }


def test_audit_binds_exact_gds_sha256(valid_audit: dict[str, Any]) -> None:
    audit = valid_audit["audit"]
    assert audit["gds_sha256"] == valid_audit["gds_sha"]
    assert audit["gds"]["sha256"] == valid_audit["gds_sha"]


def test_audit_binds_exact_geometry_sha256(valid_audit: dict[str, Any]) -> None:
    audit = valid_audit["audit"]
    assert audit["geometry_sha256"] == valid_audit["geometry_sha"]
    assert canonical_geometry_sha256(audit["geometry_vector"]) == valid_audit[
        "geometry_sha"
    ]


def test_audit_binds_exact_configuration_sha256(
    valid_audit: dict[str, Any],
) -> None:
    assert valid_audit["audit"]["private_configuration"]["sha256"] == valid_audit[
        "config_sha"
    ]


def test_missing_audit_fails_closed(
    valid_audit: dict[str, Any], tmp_path: Path
) -> None:
    with pytest.raises(FoundryLayoutAuditError, match="missing or empty"):
        _load(tmp_path / "missing.json", valid_audit)


def test_empty_audit_fails_closed(
    valid_audit: dict[str, Any], tmp_path: Path
) -> None:
    path = tmp_path / "empty.json"
    path.touch()
    with pytest.raises(FoundryLayoutAuditError, match="missing or empty"):
        _load(path, valid_audit)


def test_malformed_audit_fails_closed(
    valid_audit: dict[str, Any], tmp_path: Path
) -> None:
    path = tmp_path / "malformed.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(FoundryLayoutAuditError, match="cannot be parsed"):
        _load(path, valid_audit)


def test_wrong_schema_fails_closed(valid_audit: dict[str, Any]) -> None:
    audit = copy.deepcopy(valid_audit["audit"])
    audit["schema"] = "wrong.schema"
    with pytest.raises(FoundryLayoutAuditError, match="schema mismatch"):
        _validate(audit, valid_audit)


def test_overall_fail_does_not_validate_as_pass(valid_audit: dict[str, Any]) -> None:
    audit = copy.deepcopy(valid_audit["audit"])
    check = "actual_gds_present_and_nonempty"
    audit["checks"][check] = False
    audit["overall_status"] = "FAIL"
    audit["failure_reasons"] = [check]
    with pytest.raises(FoundryLayoutAuditError, match="overall_status is not PASS"):
        _validate(audit, valid_audit)


def test_gds_hash_mismatch_fails_closed(valid_audit: dict[str, Any]) -> None:
    with pytest.raises(FoundryLayoutAuditError, match="gds.sha256 mismatch"):
        _validate(valid_audit["audit"], valid_audit, gds_sha="0" * 64)


def test_geometry_hash_mismatch_fails_closed(valid_audit: dict[str, Any]) -> None:
    with pytest.raises(FoundryLayoutAuditError, match="geometry_sha256 mismatch"):
        _validate(valid_audit["audit"], valid_audit, geometry_sha="1" * 64)


def test_configuration_hash_mismatch_fails_closed(
    valid_audit: dict[str, Any],
) -> None:
    with pytest.raises(
        FoundryLayoutAuditError, match="private_configuration.sha256 mismatch"
    ):
        _validate(valid_audit["audit"], valid_audit, config_sha="2" * 64)


def test_stale_candidate_and_stage_fail_closed(valid_audit: dict[str, Any]) -> None:
    with pytest.raises(FoundryLayoutAuditError) as caught:
        _validate(
            valid_audit["audit"],
            valid_audit,
            stage_id="PILOT_32",
            candidate_sha="3" * 64,
        )
    assert "stage_id mismatch" in str(caught.value)
    assert "candidate_id_sha256 mismatch" in str(caught.value)


def test_atomic_write_interruption_leaves_no_final_audit(
    valid_audit: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "foundry_layout_audit.json"
    payload = copy.deepcopy(valid_audit["audit"])

    def interrupt(_source: Path, _destination: Path) -> None:
        raise OSError("simulated atomic rename interruption")

    monkeypatch.setattr(os, "replace", interrupt)
    with pytest.raises(OSError, match="simulated atomic rename interruption"):
        audit_module._atomic_write_audit(
            output,
            payload,
            expected={
                "stage_id": "GOLDEN",
                "candidate_id_sha256": valid_audit["geometry_sha"],
                "geometry_sha256": valid_audit["geometry_sha"],
                "config_sha256": valid_audit["config_sha"],
                "gds_sha256": valid_audit["gds_sha"],
                "contract_sha256": valid_audit["contract_sha"],
            },
            require_pass=True,
        )
    assert not output.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_actual_checks_are_required_and_producer_launches_no_external_process(
    valid_audit: dict[str, Any],
) -> None:
    audit = valid_audit["audit"]
    assert audit["schema"] == AUDIT_SCHEMA
    assert audit["overall_status"] == "PASS"
    assert audit["failure_reasons"] == []
    assert all(value is True for value in audit["checks"].values())
    _validate(audit, valid_audit, verify_files=True)


def test_multiple_actual_failures_write_atomic_fail_receipt(
    valid_audit: dict[str, Any], tmp_path: Path
) -> None:
    import gdstk

    library = gdstk.read_gds(valid_audit["gds"])
    for cell in library.cells:
        for polygon in cell.polygons:
            polygon.translate(0.001, 0.0)
    changed_gds = tmp_path / "off_grid.gds"
    library.write_gds(changed_gds)
    output = tmp_path / "foundry_layout_audit.json"
    original = valid_audit["audit"]
    audit = produce_foundry_layout_audit(
        gds_path=changed_gds,
        source_audit_path=Path(original["source_layout_audit"]["path"]),
        power_line_audit_path=Path(original["source_power_line_audit"]["path"]),
        config_path=valid_audit["config"],
        contract_path=CONTRACT,
        candidate=valid_audit["candidate"],
        stage_id="GOLDEN",
        output_path=output,
    )
    assert output.is_file() and output.stat().st_size > 0
    assert audit["overall_status"] == "FAIL"
    assert len(audit["failure_reasons"]) > 1
    assert audit["failure_reasons"] == sorted(audit["failure_reasons"])
    validate_foundry_layout_audit(
        audit,
        expected_stage_id="GOLDEN",
        expected_candidate_id_sha256=valid_audit["geometry_sha"],
        expected_geometry_sha256=valid_audit["geometry_sha"],
        expected_config_sha256=valid_audit["config_sha"],
        expected_gds_sha256=_sha(changed_gds),
        expected_contract_sha256=valid_audit["contract_sha"],
        require_pass=False,
        verify_files=True,
    )


def _load(path: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    return load_and_validate_foundry_layout_audit(
        path,
        expected_stage_id="GOLDEN",
        expected_candidate_id_sha256=fixture["geometry_sha"],
        expected_geometry_sha256=fixture["geometry_sha"],
        expected_config_sha256=fixture["config_sha"],
        expected_gds_sha256=fixture["gds_sha"],
        expected_contract_sha256=fixture["contract_sha"],
        require_pass=True,
        verify_files=True,
    )


def _validate(
    audit: dict[str, Any],
    fixture: dict[str, Any],
    *,
    stage_id: str = "GOLDEN",
    candidate_sha: str | None = None,
    geometry_sha: str | None = None,
    config_sha: str | None = None,
    gds_sha: str | None = None,
    verify_files: bool = False,
) -> None:
    validate_foundry_layout_audit(
        audit,
        expected_stage_id=stage_id,
        expected_candidate_id_sha256=candidate_sha or fixture["geometry_sha"],
        expected_geometry_sha256=geometry_sha or fixture["geometry_sha"],
        expected_config_sha256=config_sha or fixture["config_sha"],
        expected_gds_sha256=gds_sha or fixture["gds_sha"],
        expected_contract_sha256=fixture["contract_sha"],
        require_pass=True,
        verify_files=verify_files,
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
