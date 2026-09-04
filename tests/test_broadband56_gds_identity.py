from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import gdstk
import pytest

import rfic_transformer_inverse_design.campaigns.broadband56_gds_identity as identity_module
from rfic_transformer_inverse_design.campaigns.broadband56_gds_identity import (
    AUDIT_SCHEMA,
    GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM,
    GdsIdentityError,
    audit_gds_physical_identity,
    gds_structural_identity,
    gds_timestamp_normalized_sha256,
)


def test_arbitrary_positive_batch_passes_and_writes_hash_closed_index(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, offsets=(0.0, 20.0))

    result = _run(fixture)

    assert result["overall_status"] == "PASS"
    assert result["expected_count"] == 2
    assert result["pass_count"] == 2
    assert result["checks"]["physical_identity_hashes_unique"] is True
    out_dir = fixture["out_dir"]
    summary = json.loads(
        (out_dir / "GDS_PHYSICAL_IDENTITY_AUDIT_SUMMARY.json").read_text()
    )
    assert summary["schema"] == AUDIT_SCHEMA
    assert summary["simulator_action_taken"] is False
    assert (out_dir / "gds_physical_identity_audited_index.csv").is_file()
    assert (out_dir / "SHA256SUMS.txt").is_file()


def test_rejects_input_hash_drift_before_official_output(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, offsets=(0.0,))

    with pytest.raises(GdsIdentityError, match="candidate CSV SHA-256 mismatch"):
        audit_gds_physical_identity(
            candidate_csv=fixture["candidate_csv"],
            dataset_dir=fixture["dataset_dir"],
            input_index_csv=fixture["index_csv"],
            out_dir=fixture["out_dir"],
            expected_count=1,
            expected_candidate_sha256="f" * 64,
            expected_dataset_rows_sha256=fixture["dataset_rows_sha256"],
            expected_index_sha256=fixture["index_sha256"],
        )

    assert not fixture["out_dir"].exists()


def test_direct_to_cadence_polygon_drift_fails_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, offsets=(0.0,))
    cadence_gds = fixture["cadence_gds_paths"][0]
    _write_gds(cadence_gds, offset=2.0, cadence=True)
    _refresh_index_and_audit(fixture, row_index=0)

    result = _run(fixture)

    assert result["overall_status"] == "FAIL"
    assert result["pass_count"] == 0
    record = json.loads(
        next((fixture["out_dir"] / "records").glob("*.json")).read_text()
    )
    assert record["checks"]["direct_and_cadence_layer_unions_equal"] is False
    assert record["automatic_calibre_authorized"] is False


def test_equivalent_polygon_partition_passes_physical_identity(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, offsets=(0.0,))
    cadence_gds = fixture["cadence_gds_paths"][0]
    _write_gds(cadence_gds, offset=0.0, cadence=True, partitioned=True)
    _refresh_index_and_audit(fixture, row_index=0)

    result = _run(fixture)

    assert result["overall_status"] == "PASS"
    record = json.loads(
        next((fixture["out_dir"] / "records").glob("*.json")).read_text()
    )
    assert record["checks"]["direct_and_cadence_layer_unions_equal"] is True
    assert record["checks"][
        "direct_and_cadence_physical_structures_equal"
    ] is True
    assert record["diagnostics"][
        "direct_and_cadence_polygon_multisets_equal"
    ] is False


def test_contained_duplicate_pin_polygon_passes_physical_identity(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, offsets=(0.0,))
    cadence_gds = fixture["cadence_gds_paths"][0]
    _write_gds(cadence_gds, offset=0.0, cadence=True, contained_overlay=True)
    _refresh_index_and_audit(fixture, row_index=0)

    result = _run(fixture)

    assert result["overall_status"] == "PASS"
    record = json.loads(
        next((fixture["out_dir"] / "records").glob("*.json")).read_text()
    )
    assert record["checks"]["direct_and_cadence_layer_unions_equal"] is True
    assert record["diagnostics"][
        "direct_and_cadence_polygon_multisets_equal"
    ] is False


def test_duplicate_physical_gds_is_not_counted_as_two_geometries(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, offsets=(0.0, 0.0))

    result = _run(fixture)

    assert result["overall_status"] == "FAIL"
    assert result["pass_count"] == 2
    assert result["checks"]["physical_identity_hashes_unique"] is False


def test_cadence_pin_offset_above_five_nm_fails_structural_audit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "transformer_layout_cadpins.gds"
    _write_gds(path, offset=0.0, cadence=True, pin_offset_um=0.006)

    identity = gds_structural_identity(path)

    assert identity["overall_status"] == "FAIL"
    assert identity["checks"]["cadence_pin_labels_match_base_layer_and_within_5nm"] is False


def test_no_clobber_rejects_second_run(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, offsets=(0.0,))
    assert _run(fixture)["overall_status"] == "PASS"

    with pytest.raises(GdsIdentityError, match="refusing existing output"):
        _run(fixture)


def test_candidate_csv_drift_during_audit_fails_final_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, offsets=(0.0,))
    original = identity_module.gds_structural_identity
    mutated = False

    def mutate_candidate_csv(path: Path) -> dict[str, object]:
        nonlocal mutated
        result = original(path)
        if not mutated:
            candidate_csv = fixture["candidate_csv"]
            candidate_csv.write_bytes(candidate_csv.read_bytes() + b"\n")
            mutated = True
        return result

    monkeypatch.setattr(
        identity_module, "gds_structural_identity", mutate_candidate_csv
    )

    result = _run(fixture)

    assert result["overall_status"] == "FAIL"
    assert result["checks"]["candidate_csv_sha_remains_expected"] is False


def test_cadence_gds_drift_during_structural_audit_fails_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, offsets=(0.0,))
    original = identity_module.gds_structural_identity
    cadence_gds = fixture["cadence_gds_paths"][0]
    mutated = False

    def mutate_cadence_gds(path: Path) -> dict[str, object]:
        nonlocal mutated
        result = original(path)
        if path == cadence_gds and not mutated:
            path.write_bytes(path.read_bytes() + b"\x00\x00")
            mutated = True
        return result

    monkeypatch.setattr(
        identity_module, "gds_structural_identity", mutate_cadence_gds
    )

    result = _run(fixture)

    assert result["overall_status"] == "FAIL"
    record = json.loads(
        next((fixture["out_dir"] / "records").glob("*.json")).read_text()
    )
    assert record["checks"][
        "cadence_gds_unchanged_through_structural_audit"
    ] is False


def _fixture(root: Path, *, offsets: tuple[float, ...]) -> dict[str, object]:
    candidate_rows: list[dict[str, str]] = []
    dataset_rows: list[dict[str, str]] = []
    index_rows: list[dict[str, str]] = []
    dataset_dir = root / "dataset"
    index_dir = root / "index"
    audit_dir = index_dir / "candidate_bound_geometry_audits"
    audit_dir.mkdir(parents=True)
    cadence_paths: list[Path] = []
    audit_paths: list[Path] = []
    for index, offset in enumerate(offsets, start=1):
        candidate_id = f"candidate_{index:03d}"
        candidate_sha = f"{index:064x}"
        geometry_sha = f"{index + 100:064x}"
        evaluation = f"{index:016x}"
        evaluation_dir = dataset_dir / "evaluations" / evaluation
        direct_gds = evaluation_dir / "layout" / "transformer_layout.gds"
        cadence_gds = (
            evaluation_dir / "streamout" / "transformer_layout_cadpins.gds"
        )
        _write_gds(direct_gds, offset=offset, cadence=False)
        _write_gds(cadence_gds, offset=offset, cadence=True)
        (evaluation_dir / "summary.json").write_text("{}\n", encoding="utf-8")
        geometry_audit = audit_dir / f"{candidate_sha}.json"
        geometry_audit.write_text(
            json.dumps(
                {
                    "overall_status": "PASS",
                    "candidate_id_sha256": candidate_sha,
                    "candidate_geometry_identity_sha256": geometry_sha,
                    "gds_sha256": _sha(cadence_gds),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        candidate_rows.append(
            {
                "candidate_id": candidate_id,
                "candidate_id_sha256": candidate_sha,
                "candidate_geometry_identity_sha256": geometry_sha,
            }
        )
        dataset_rows.append(
            {
                "evaluation": evaluation,
                "queue__candidate_id": candidate_id,
                "queue__candidate_id_sha256": candidate_sha,
                "queue__candidate_geometry_identity_sha256": geometry_sha,
            }
        )
        index_rows.append(
            {
                "candidate_id": candidate_id,
                "candidate_id_sha256": candidate_sha,
                "candidate_geometry_identity_sha256": geometry_sha,
                "gds_path": str(cadence_gds.resolve()),
                "gds_sha256": _sha(cadence_gds),
                "gds_timestamp_normalized_sha256": (
                    gds_timestamp_normalized_sha256(cadence_gds)
                ),
                "gds_timestamp_normalization_algorithm": (
                    GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM
                ),
                "geometry_audit_path": str(geometry_audit.resolve()),
                "geometry_audit_sha256": _sha(geometry_audit),
                "overall_status": "PASS",
            }
        )
        cadence_paths.append(cadence_gds)
        audit_paths.append(geometry_audit)
    candidate_csv = root / "candidates.csv"
    dataset_rows_csv = dataset_dir / "dataset_rows.csv"
    index_csv = index_dir / "candidate_bound_cadence_gds_index.csv"
    _write_csv(candidate_csv, candidate_rows)
    _write_csv(dataset_rows_csv, dataset_rows)
    _write_csv(index_csv, index_rows)
    return {
        "candidate_csv": candidate_csv,
        "candidate_sha256": _sha(candidate_csv),
        "dataset_dir": dataset_dir,
        "dataset_rows_csv": dataset_rows_csv,
        "dataset_rows_sha256": _sha(dataset_rows_csv),
        "index_csv": index_csv,
        "index_rows": index_rows,
        "index_sha256": _sha(index_csv),
        "cadence_gds_paths": cadence_paths,
        "geometry_audit_paths": audit_paths,
        "out_dir": root / "audit_out",
        "expected_count": len(offsets),
    }


def _run(fixture: dict[str, object]) -> dict[str, object]:
    return audit_gds_physical_identity(
        candidate_csv=fixture["candidate_csv"],
        dataset_dir=fixture["dataset_dir"],
        input_index_csv=fixture["index_csv"],
        out_dir=fixture["out_dir"],
        expected_count=fixture["expected_count"],
        expected_candidate_sha256=fixture["candidate_sha256"],
        expected_dataset_rows_sha256=fixture["dataset_rows_sha256"],
        expected_index_sha256=fixture["index_sha256"],
    )


def _refresh_index_and_audit(
    fixture: dict[str, object], *, row_index: int
) -> None:
    rows = fixture["index_rows"]
    cadence_gds = fixture["cadence_gds_paths"][row_index]
    geometry_audit = fixture["geometry_audit_paths"][row_index]
    rows[row_index]["gds_sha256"] = _sha(cadence_gds)
    rows[row_index]["gds_timestamp_normalized_sha256"] = (
        gds_timestamp_normalized_sha256(cadence_gds)
    )
    audit = json.loads(geometry_audit.read_text(encoding="utf-8"))
    audit["gds_sha256"] = _sha(cadence_gds)
    geometry_audit.write_text(json.dumps(audit) + "\n", encoding="utf-8")
    rows[row_index]["geometry_audit_sha256"] = _sha(geometry_audit)
    _write_csv(fixture["index_csv"], rows)
    fixture["index_sha256"] = _sha(fixture["index_csv"])


def _write_gds(
    path: Path,
    *,
    offset: float,
    cadence: bool,
    pin_offset_um: float = 0.001,
    partitioned: bool = False,
    contained_overlay: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    library = gdstk.Library(unit=1.0e-6, precision=1.0e-9)
    cell = gdstk.Cell("TRANSFORMER")
    if partitioned:
        cell.add(
            gdstk.rectangle(
                (offset, 0.0),
                (offset + 5.0, 10.0),
                layer=35,
                datatype=0,
            ),
            gdstk.rectangle(
                (offset + 5.0, 0.0),
                (offset + 10.0, 10.0),
                layer=35,
                datatype=0,
            ),
        )
    else:
        cell.add(
            gdstk.rectangle(
                (offset, 0.0),
                (offset + 10.0, 10.0),
                layer=35,
                datatype=0,
            )
        )
    if contained_overlay:
        cell.add(
            gdstk.rectangle(
                (offset + 1.0, 1.0),
                (offset + 9.0, 9.0),
                layer=35,
                datatype=0,
            )
        )
    layers = {
        "P001": 126,
        "P002": 126,
        "P003": 139,
        "P004": 139,
        "P001_G": 135,
        "P002_G": 135,
        "P003_G": 135,
        "P004_G": 135,
        "P005": 135,
        "P006": 135,
        "P007": 135,
        "P008": 135,
    }
    origins: dict[str, tuple[float, float]] = {}
    for label_index, (text, layer) in enumerate(layers.items()):
        origin = (offset + 0.5 + label_index * 0.1, 1.0)
        origins[text] = origin
        cell.add(gdstk.Label(text, origin, layer=layer, texttype=0))
    if cadence:
        for text in (
            "P001",
            "P002",
            "P003",
            "P004",
            "P001_G",
            "P002_G",
            "P003_G",
            "P004_G",
        ):
            origin = origins[text]
            cell.add(
                gdstk.Label(
                    text,
                    (origin[0] + pin_offset_um, origin[1]),
                    layer=layers[text],
                    texttype=0,
                    rotation=2.0 * math.pi,
                )
            )
    library.add(cell)
    library.write_gds(path)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
