"""Production-general physical identity audit for broadband56 Cadence GDS.

The audit binds each canonical candidate and geometry identity to one Cadence
stream-out GDS, then compares its flattened physical representation with the
direct-layout GDS from the same evaluation directory. Polygon partitioning is
canonicalized as a per-layer Boolean union before it contributes to the
physical hash. Base layout label markers also contribute to the hash. Cadence
OA display-pin labels are checked separately and may move by at most one 5 nm
grid step.

This module performs no simulator action and has no subprocess capability.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
AUDIT_SCHEMA = "rfic_transformer.broadband56_v2_gds_physical_identity_audit.v1"
STRUCTURAL_SCHEMA = "flattened_gds_layer_union_label_pin_physical_identity_pm_v2"
GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM = (
    "gdsii-record-sha256-zero-bgnlib-bgnstr-timestamps-v1"
)
TOP_CELL = "TRANSFORMER"
PM_PER_M = 1.0e12
PM_ROUNDING_TOLERANCE = 1.0e-3
MAX_CADENCE_PIN_LABEL_OFFSET_PM = 5_000
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
EVALUATION_KEY_PATTERN = re.compile(r"[0-9a-f]{16}")
_BGNLIB = 0x01
_BGNSTR = 0x05
_ENDLIB = 0x04
_INT2 = 0x02
_TIMESTAMP_PAYLOAD_BYTES = 24

REQUIRED_PORT_LABELS = (
    "P001",
    "P002",
    "P003",
    "P004",
    "P001_G",
    "P002_G",
    "P003_G",
    "P004_G",
    "P005",
    "P006",
    "P007",
    "P008",
)
CADENCE_OA_PIN_LABELS = (
    "P001",
    "P002",
    "P003",
    "P004",
    "P001_G",
    "P002_G",
    "P003_G",
    "P004_G",
)
REQUIRED_CANDIDATE_FIELDS = {
    "candidate_id",
    "candidate_id_sha256",
    "candidate_geometry_identity_sha256",
}
REQUIRED_DATASET_FIELDS = {
    "evaluation",
    "queue__candidate_id",
    "queue__candidate_id_sha256",
    "queue__candidate_geometry_identity_sha256",
}
REQUIRED_INDEX_FIELDS = {
    "candidate_id",
    "candidate_id_sha256",
    "candidate_geometry_identity_sha256",
    "gds_path",
    "gds_sha256",
    "gds_timestamp_normalized_sha256",
    "geometry_audit_path",
    "geometry_audit_sha256",
    "overall_status",
}


class GdsIdentityError(RuntimeError):
    """Raised when physical identity cannot be established fail closed."""


def audit_gds_physical_identity(
    *,
    candidate_csv: Path,
    dataset_dir: Path,
    input_index_csv: Path,
    out_dir: Path,
    expected_count: int,
    expected_candidate_sha256: str,
    expected_dataset_rows_sha256: str,
    expected_index_sha256: str,
) -> dict[str, Any]:
    """Write one no-clobber audit and return its terminal summary."""

    if expected_count < 1:
        raise GdsIdentityError("expected_count must be positive")
    candidate_path = _regular_file(candidate_csv, "candidate CSV")
    dataset_root = _directory(dataset_dir, "dataset directory")
    dataset_rows_path = _regular_file(
        dataset_root / "dataset_rows.csv", "dataset rows CSV"
    )
    index_path = _regular_file(input_index_csv, "candidate GDS index CSV")
    output = _absolute(out_dir)
    if output.exists():
        raise GdsIdentityError(f"refusing existing output directory: {output}")
    expected_candidate_sha = _sha256_value(
        expected_candidate_sha256, "expected candidate CSV SHA-256"
    )
    expected_dataset_rows_sha = _sha256_value(
        expected_dataset_rows_sha256, "expected dataset rows CSV SHA-256"
    )
    expected_index_sha = _sha256_value(
        expected_index_sha256, "expected candidate GDS index CSV SHA-256"
    )
    _require_sha(candidate_path, expected_candidate_sha, "candidate CSV")
    _require_sha(
        dataset_rows_path, expected_dataset_rows_sha, "dataset rows CSV"
    )
    _require_sha(index_path, expected_index_sha, "candidate GDS index CSV")

    candidate_rows, candidate_fields = _read_csv(candidate_path)
    dataset_rows, dataset_fields = _read_csv(dataset_rows_path)
    index_rows, index_fields = _read_csv(index_path)
    preflight = {
        "candidate_columns_complete": REQUIRED_CANDIDATE_FIELDS.issubset(
            candidate_fields
        ),
        "dataset_columns_complete": REQUIRED_DATASET_FIELDS.issubset(
            dataset_fields
        ),
        "index_columns_complete": REQUIRED_INDEX_FIELDS.issubset(index_fields),
        "candidate_count_matches_expected": len(candidate_rows) == expected_count,
        "dataset_count_matches_expected": len(dataset_rows) == expected_count,
        "index_count_matches_expected": len(index_rows) == expected_count,
    }
    failed_preflight = [name for name, passed in preflight.items() if not passed]
    if failed_preflight:
        raise GdsIdentityError(
            "GDS identity preflight failed: " + ",".join(failed_preflight)
        )

    candidate_by_id = _unique_rows(
        candidate_rows, "candidate_id_sha256", "candidate CSV"
    )
    dataset_by_id = _unique_rows(
        dataset_rows, "queue__candidate_id_sha256", "dataset rows CSV"
    )
    index_by_id = _unique_rows(
        index_rows, "candidate_id_sha256", "candidate GDS index CSV"
    )
    expected_ids = set(candidate_by_id)
    if set(dataset_by_id) != expected_ids or set(index_by_id) != expected_ids:
        raise GdsIdentityError("candidate, dataset, and GDS index identity sets differ")

    output.mkdir(parents=True, mode=0o700)
    records_dir = output / "records"
    records_dir.mkdir()
    records: list[dict[str, Any]] = []
    audited_rows: list[dict[str, Any]] = []
    for candidate_id in sorted(expected_ids):
        candidate = candidate_by_id[candidate_id]
        dataset = dataset_by_id[candidate_id]
        index = index_by_id[candidate_id]
        try:
            record = _audit_candidate(
                candidate=candidate,
                dataset=dataset,
                index=index,
                dataset_dir=dataset_root,
                input_index_csv=index_path,
            )
        except Exception as exc:  # Per-candidate failures remain evidence.
            record = {
                "schema": AUDIT_SCHEMA,
                "overall_status": "FAIL",
                "candidate_id": str(candidate.get("candidate_id") or ""),
                "candidate_id_sha256": candidate_id,
                "candidate_geometry_identity_sha256": str(
                    candidate.get("candidate_geometry_identity_sha256") or ""
                ).lower(),
                "error": f"{type(exc).__name__}: {exc}",
                "checks": {"candidate_physical_identity_completed": False},
                "failed_checks": ["candidate_physical_identity_completed"],
                "simulator_action_taken": False,
            }
        record_path = records_dir / f"{candidate_id}_gds_identity.json"
        _write_json_exclusive(record_path, record)
        record_sha = _sha256(record_path)
        records.append(record)
        audited_rows.append(
            {
                **index,
                "gds_physical_identity_status": record["overall_status"],
                "candidate_physical_identity_sha256": str(
                    record.get("candidate_physical_identity_sha256") or ""
                ),
                "gds_physical_identity_audit_path": str(record_path),
                "gds_physical_identity_audit_sha256": record_sha,
            }
        )

    physical_hashes = [
        str(record.get("candidate_physical_identity_sha256") or "")
        for record in records
    ]
    checks = {
        **preflight,
        "identity_sets_match_exactly": set(dataset_by_id)
        == set(index_by_id)
        == expected_ids,
        "every_candidate_physical_identity_passes": bool(records)
        and all(record.get("overall_status") == "PASS" for record in records),
        "physical_identity_hashes_valid": bool(physical_hashes)
        and all(_is_sha256(value) for value in physical_hashes),
        "physical_identity_hashes_unique": len(set(physical_hashes))
        == expected_count,
        "candidate_csv_sha_remains_expected": _stable_sha256(candidate_path)
        == expected_candidate_sha,
        "dataset_rows_csv_sha_remains_expected": _stable_sha256(
            dataset_rows_path
        )
        == expected_dataset_rows_sha,
        "input_gds_index_sha_remains_expected": _stable_sha256(index_path)
        == expected_index_sha,
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    audited_index_path = output / "gds_physical_identity_audited_index.csv"
    _write_csv_exclusive(audited_index_path, audited_rows)
    summary = {
        "schema": AUDIT_SCHEMA,
        "overall_status": status,
        "decision": (
            "GDS_PHYSICAL_IDENTITY_READY_FOR_CALIBRE"
            if status == "PASS"
            else "DO_NOT_RUN_CALIBRE_GDS_PHYSICAL_IDENTITY_FAILED"
        ),
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "expected_count": expected_count,
        "pass_count": sum(
            record.get("overall_status") == "PASS" for record in records
        ),
        "fail_count": sum(
            record.get("overall_status") != "PASS" for record in records
        ),
        "candidate_csv": _file_record(candidate_path),
        "dataset_rows_csv": _file_record(dataset_rows_path),
        "input_gds_index_csv": _file_record(index_path),
        "audited_gds_index_csv": _file_record(audited_index_path),
        "checks": checks,
        "structural_identity_contract": {
            "schema": STRUCTURAL_SCHEMA,
            "top_cell": TOP_CELL,
            "required_port_labels": list(REQUIRED_PORT_LABELS),
            "cadence_oa_pin_labels": list(CADENCE_OA_PIN_LABELS),
            "maximum_cadence_pin_label_offset_pm": (
                MAX_CADENCE_PIN_LABEL_OFFSET_PM
            ),
            "direct_and_cadence_layer_unions_must_match": True,
            "direct_and_cadence_base_label_sets_must_match": True,
            "candidate_physical_hashes_must_be_unique": True,
        },
        "automatic_calibre_authorized": False,
        "automatic_emx_authorized": False,
        "simulator_action_taken": False,
    }
    summary_path = output / "GDS_PHYSICAL_IDENTITY_AUDIT_SUMMARY.json"
    _write_json_exclusive(summary_path, summary)
    _write_sha256s(output)
    return {**summary, "summary_path": str(summary_path)}


def gds_structural_identity(path: Path) -> dict[str, Any]:
    """Return a deterministic, cell-order-independent physical identity."""

    gds_path = _regular_file(path, "GDS")
    try:
        import gdstk
    except ImportError as exc:  # pragma: no cover - production environment gate.
        raise GdsIdentityError("gdstk is required for GDS identity audit") from exc

    library = gdstk.read_gds(str(gds_path))
    top_cells = library.top_level()
    if len(top_cells) != 1 or top_cells[0].name != TOP_CELL:
        raise GdsIdentityError(
            f"GDS top cell must be exactly {TOP_CELL}: "
            f"{[cell.name for cell in top_cells]}"
        )
    top = top_cells[0]
    polygons = top.get_polygons(
        apply_repetitions=True,
        include_paths=True,
        depth=None,
    )
    labels = top.get_labels(apply_repetitions=True, depth=None)
    polygon_records = sorted(
        _polygon_record(polygon, unit_m=float(library.unit))
        for polygon in polygons
    )
    layer_union_records = _layer_union_polygon_records(
        polygons,
        unit_m=float(library.unit),
        precision_m=float(library.precision),
    )
    base_label_records = [
        _label_marker_record(label, unit_m=float(library.unit))
        for label in labels
        if _is_base_layout_label(label)
    ]
    cadence_pin_records = [
        _label_marker_record(label, unit_m=float(library.unit))
        for label in labels
        if not _is_base_layout_label(label)
    ]
    base_label_set = sorted(set(base_label_records))
    base_by_text: dict[str, set[tuple[Any, ...]]] = defaultdict(set)
    for record in base_label_set:
        base_by_text[str(record[0])].add(record)
    pin_by_text: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for record in cadence_pin_records:
        pin_by_text[str(record[0])].append(record)
    cadence_streamout = gds_path.name == "transformer_layout_cadpins.gds"
    pin_offsets_valid = all(
        _pin_marker_matches_base(pin, base_by_text.get(text, set()))
        for text, pins in pin_by_text.items()
        for pin in pins
    )
    checks = {
        "library_unit_finite_positive": math.isfinite(float(library.unit))
        and float(library.unit) > 0.0,
        "library_precision_finite_positive": math.isfinite(
            float(library.precision)
        )
        and float(library.precision) > 0.0,
        "flattened_polygons_present": bool(polygon_records),
        "required_port_label_texts_present": set(REQUIRED_PORT_LABELS).issubset(
            base_by_text
        ),
        "each_layout_label_text_has_one_base_marker": bool(base_by_text)
        and all(len(records) == 1 for records in base_by_text.values()),
        "base_layout_label_records_are_unique": len(base_label_records)
        == len(base_label_set),
        "cadence_pin_label_texts_exact_or_direct_not_applicable": (
            set(pin_by_text) == set(CADENCE_OA_PIN_LABELS)
            and all(len(pin_by_text[text]) == 1 for text in CADENCE_OA_PIN_LABELS)
            if cadence_streamout
            else not cadence_pin_records
        ),
        "cadence_pin_labels_match_base_layer_and_within_5nm": pin_offsets_valid,
    }
    polygon_hash = _json_sha256(
        {
            "schema": STRUCTURAL_SCHEMA,
            "kind": "polygon_multiset",
            "records": polygon_records,
        }
    )
    layer_union_hash = _json_sha256(
        {
            "schema": STRUCTURAL_SCHEMA,
            "kind": "layer_union",
            "records": layer_union_records,
        }
    )
    label_hash = _json_sha256(
        {
            "schema": STRUCTURAL_SCHEMA,
            "kind": "base_layout_label_pin_marker_set",
            "records": base_label_set,
        }
    )
    structural_hash = _json_sha256(
        {
            "schema": STRUCTURAL_SCHEMA,
            "layer_union_sha256": layer_union_hash,
            "label_pin_set_sha256": label_hash,
        }
    )
    return {
        "overall_status": "PASS" if all(checks.values()) else "FAIL",
        "structural_schema": STRUCTURAL_SCHEMA,
        "structural_sha256": structural_hash,
        "polygon_multiset_sha256": polygon_hash,
        "layer_union_sha256": layer_union_hash,
        "label_pin_set_sha256": label_hash,
        "polygon_count": len(polygon_records),
        "layer_union_polygon_count": len(layer_union_records),
        "base_layout_label_marker_count": len(base_label_set),
        "cadence_oa_pin_label_marker_count": len(cadence_pin_records),
        "cadence_oa_pin_label_max_offset_pm": _maximum_pin_offset_pm(
            pin_by_text, base_by_text
        ),
        "checks": checks,
    }


def gds_timestamp_normalized_sha256(path: Path) -> str:
    """Hash GDSII bytes while zeroing only BGNLIB/BGNSTR timestamps."""

    data = _regular_file(path, "GDS").read_bytes()
    digest = hashlib.sha256()
    offset = 0
    record_count = 0
    timestamp_count = 0
    saw_endlib = False
    while offset < len(data):
        if saw_endlib:
            padding = data[offset:]
            if any(padding):
                raise GdsIdentityError("nonzero bytes follow the GDSII ENDLIB")
            digest.update(padding)
            offset = len(data)
            break
        if len(data) - offset < 4:
            raise GdsIdentityError("truncated GDSII record header")
        length = int.from_bytes(data[offset : offset + 2], "big")
        if length < 4 or length % 2:
            raise GdsIdentityError("invalid GDSII record length")
        end = offset + length
        if end > len(data):
            raise GdsIdentityError("truncated GDSII record payload")
        record_type = data[offset + 2]
        data_type = data[offset + 3]
        payload = data[offset + 4 : end]
        digest.update(data[offset : offset + 4])
        if record_type in {_BGNLIB, _BGNSTR}:
            if data_type != _INT2 or len(payload) != _TIMESTAMP_PAYLOAD_BYTES:
                raise GdsIdentityError("invalid GDSII timestamp record")
            digest.update(bytes(_TIMESTAMP_PAYLOAD_BYTES))
            timestamp_count += 1
        else:
            digest.update(payload)
        if record_type == _ENDLIB:
            if data_type != 0 or payload:
                raise GdsIdentityError("invalid GDSII ENDLIB record")
            saw_endlib = True
        offset = end
        record_count += 1
    if record_count == 0 or timestamp_count < 2 or not saw_endlib:
        raise GdsIdentityError("incomplete GDSII record stream")
    return digest.hexdigest()


def _audit_candidate(
    *,
    candidate: Mapping[str, str],
    dataset: Mapping[str, str],
    index: Mapping[str, str],
    dataset_dir: Path,
    input_index_csv: Path,
) -> dict[str, Any]:
    candidate_name = str(candidate.get("candidate_id") or "")
    candidate_id = _sha256_value(
        candidate.get("candidate_id_sha256"), "candidate_id_sha256"
    )
    geometry_id = _sha256_value(
        candidate.get("candidate_geometry_identity_sha256"),
        "candidate_geometry_identity_sha256",
    )
    evaluation = str(dataset.get("evaluation") or "")
    evaluation_dir = _find_evaluation_dir(dataset_dir, evaluation)
    cadence_gds = _declared_file(index.get("gds_path"), "Cadence GDS")
    expected_cadence_gds = (
        evaluation_dir / "streamout" / "transformer_layout_cadpins.gds"
    ).resolve()
    direct_gds = _regular_file(
        evaluation_dir / "layout" / "transformer_layout.gds",
        "direct-layout GDS",
    )
    geometry_audit_path = _geometry_audit_path(
        input_index_csv=input_index_csv,
        candidate_id_sha256=candidate_id,
        raw=index.get("geometry_audit_path"),
    )
    geometry_audit = _json_object(geometry_audit_path, "geometry audit")
    cadence_sha = _stable_sha256(cadence_gds)
    direct_sha = _stable_sha256(direct_gds)
    cadence_normalized_sha = gds_timestamp_normalized_sha256(cadence_gds)
    direct_normalized_sha = gds_timestamp_normalized_sha256(direct_gds)
    cadence_identity = gds_structural_identity(cadence_gds)
    direct_identity = gds_structural_identity(direct_gds)
    cadence_post_audit_sha = _stable_sha256(cadence_gds)
    direct_post_audit_sha = _stable_sha256(direct_gds)
    normalized_algorithm = str(
        index.get("gds_timestamp_normalization_algorithm")
        or index.get("gds_timestamp_normalized_sha256_algorithm")
        or ""
    )
    checks = {
        "candidate_id_nonempty": bool(candidate_name),
        "candidate_name_matches_index_and_dataset": candidate_name
        == str(index.get("candidate_id") or "")
        == str(dataset.get("queue__candidate_id") or ""),
        "candidate_sha_matches_index_and_dataset": candidate_id
        == str(index.get("candidate_id_sha256") or "").lower()
        == str(dataset.get("queue__candidate_id_sha256") or "").lower(),
        "geometry_sha_matches_index_and_dataset": geometry_id
        == str(index.get("candidate_geometry_identity_sha256") or "").lower()
        == str(
            dataset.get("queue__candidate_geometry_identity_sha256") or ""
        ).lower(),
        "index_status_pass": str(index.get("overall_status") or "").upper()
        == "PASS",
        "cadence_gds_is_exact_evaluation_streamout": cadence_gds
        == expected_cadence_gds,
        "cadence_raw_sha_matches_index": cadence_sha
        == str(index.get("gds_sha256") or "").lower(),
        "cadence_gds_unchanged_through_structural_audit": (
            cadence_post_audit_sha == cadence_sha
        ),
        "direct_gds_unchanged_through_structural_audit": (
            direct_post_audit_sha == direct_sha
        ),
        "cadence_normalized_sha_matches_index": cadence_normalized_sha
        == str(index.get("gds_timestamp_normalized_sha256") or "").lower(),
        "cadence_normalization_algorithm_matches": normalized_algorithm
        == GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM,
        "geometry_audit_sha_matches_index": _stable_sha256(geometry_audit_path)
        == str(index.get("geometry_audit_sha256") or "").lower(),
        "geometry_audit_status_pass": geometry_audit.get("overall_status")
        == "PASS",
        "geometry_audit_candidate_matches": str(
            geometry_audit.get("candidate_id_sha256") or ""
        ).lower()
        == candidate_id,
        "geometry_audit_geometry_matches": str(
            geometry_audit.get("candidate_geometry_identity_sha256") or ""
        ).lower()
        == geometry_id,
        "geometry_audit_gds_matches": str(
            geometry_audit.get("gds_sha256") or ""
        ).lower()
        == cadence_sha,
        "direct_structural_audit_pass": direct_identity["overall_status"]
        == "PASS",
        "cadence_structural_audit_pass": cadence_identity["overall_status"]
        == "PASS",
        "direct_and_cadence_layer_unions_equal": direct_identity[
            "layer_union_sha256"
        ]
        == cadence_identity["layer_union_sha256"],
        "direct_and_cadence_base_label_sets_equal": direct_identity[
            "label_pin_set_sha256"
        ]
        == cadence_identity["label_pin_set_sha256"],
        "direct_and_cadence_physical_structures_equal": direct_identity[
            "structural_sha256"
        ]
        == cadence_identity["structural_sha256"],
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    return {
        "schema": AUDIT_SCHEMA,
        "overall_status": status,
        "candidate_id": candidate_name,
        "candidate_id_sha256": candidate_id,
        "candidate_geometry_identity_sha256": geometry_id,
        "evaluation_cache_key": evaluation,
        "direct_gds_path": str(direct_gds),
        "direct_gds_sha256": direct_sha,
        "direct_gds_post_audit_sha256": direct_post_audit_sha,
        "direct_gds_timestamp_normalized_sha256": direct_normalized_sha,
        "cadence_gds_path": str(cadence_gds),
        "cadence_gds_sha256": cadence_sha,
        "cadence_gds_post_audit_sha256": cadence_post_audit_sha,
        "cadence_gds_timestamp_normalized_sha256": cadence_normalized_sha,
        "direct_structural_sha256": direct_identity["structural_sha256"],
        "cadence_structural_sha256": cadence_identity["structural_sha256"],
        "candidate_physical_identity_sha256": cadence_identity[
            "structural_sha256"
        ],
        "direct_structure": direct_identity,
        "cadence_structure": cadence_identity,
        "diagnostics": {
            "direct_and_cadence_polygon_multisets_equal": direct_identity[
                "polygon_multiset_sha256"
            ]
            == cadence_identity["polygon_multiset_sha256"],
        },
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "automatic_calibre_authorized": False,
        "automatic_emx_authorized": False,
        "simulator_action_taken": False,
    }


def _polygon_record(polygon: Any, *, unit_m: float) -> tuple[Any, ...]:
    points = tuple(
        (
            _physical_pm(float(point[0]), unit_m=unit_m),
            _physical_pm(float(point[1]), unit_m=unit_m),
        )
        for point in polygon.points
    )
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    points = _remove_redundant_ring_points(points)
    if len(points) < 3:
        raise GdsIdentityError("flattened polygon has fewer than three vertices")
    return (int(polygon.layer), int(polygon.datatype), _canonical_ring(points))


def _layer_union_polygon_records(
    polygons: Iterable[Any],
    *,
    unit_m: float,
    precision_m: float,
) -> list[tuple[Any, ...]]:
    """Canonicalize polygon partitioning without tolerating physical drift."""

    try:
        import gdstk
    except ImportError as exc:  # pragma: no cover - production environment gate.
        raise GdsIdentityError("gdstk is required for GDS identity audit") from exc

    if not math.isfinite(precision_m) or precision_m <= 0.0:
        raise GdsIdentityError("GDS precision must be finite and positive")
    if not math.isfinite(unit_m) or unit_m <= 0.0:
        raise GdsIdentityError("GDS unit must be finite and positive")

    grouped: dict[tuple[int, int], list[Any]] = defaultdict(list)
    for polygon in polygons:
        grouped[(int(polygon.layer), int(polygon.datatype))].append(polygon)

    precision_user_units = precision_m / unit_m
    records: list[tuple[Any, ...]] = []
    for (layer, datatype), group in sorted(grouped.items()):
        union = gdstk.boolean(
            group,
            [],
            "or",
            precision=precision_user_units,
            layer=layer,
            datatype=datatype,
        )
        if not union:
            raise GdsIdentityError(
                f"layer union unexpectedly empty: layer={layer}, datatype={datatype}"
            )
        records.extend(
            _polygon_record(polygon, unit_m=unit_m) for polygon in union
        )
    return sorted(records)


def _remove_redundant_ring_points(
    points: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], ...]:
    clean = list(points)
    changed = True
    while changed and len(clean) >= 3:
        changed = False
        output: list[tuple[int, int]] = []
        size = len(clean)
        for index, current in enumerate(clean):
            previous = clean[(index - 1) % size]
            following = clean[(index + 1) % size]
            if current == previous or current == following:
                changed = True
                continue
            cross = (
                (current[0] - previous[0]) * (following[1] - current[1])
                - (current[1] - previous[1]) * (following[0] - current[0])
            )
            between = (
                min(previous[0], following[0])
                <= current[0]
                <= max(previous[0], following[0])
                and min(previous[1], following[1])
                <= current[1]
                <= max(previous[1], following[1])
            )
            if cross == 0 and between:
                changed = True
                continue
            output.append(current)
        clean = output
    return tuple(clean)


def _canonical_ring(
    points: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], ...]:
    forward = [points[index:] + points[:index] for index in range(len(points))]
    reversed_points = tuple(reversed(points))
    reverse = [
        reversed_points[index:] + reversed_points[:index]
        for index in range(len(reversed_points))
    ]
    return min((*forward, *reverse))


def _label_marker_record(label: Any, *, unit_m: float) -> tuple[Any, ...]:
    return (
        str(label.text),
        int(label.layer),
        int(label.texttype),
        _physical_pm(float(label.origin[0]), unit_m=unit_m),
        _physical_pm(float(label.origin[1]), unit_m=unit_m),
    )


def _is_base_layout_label(label: Any) -> bool:
    rotation = 0.0 if label.rotation is None else float(label.rotation)
    magnification = 1.0 if label.magnification is None else float(
        label.magnification
    )
    return (
        math.isfinite(rotation)
        and math.isfinite(magnification)
        and math.isclose(rotation, 0.0, rel_tol=0.0, abs_tol=1.0e-12)
        and math.isclose(magnification, 1.0, rel_tol=0.0, abs_tol=1.0e-12)
        and not bool(label.x_reflection)
    )


def _pin_marker_matches_base(
    pin: tuple[Any, ...], base_records: set[tuple[Any, ...]]
) -> bool:
    if len(base_records) != 1:
        return False
    base = next(iter(base_records))
    return (
        pin[:3] == base[:3]
        and abs(int(pin[3]) - int(base[3])) <= MAX_CADENCE_PIN_LABEL_OFFSET_PM
        and abs(int(pin[4]) - int(base[4])) <= MAX_CADENCE_PIN_LABEL_OFFSET_PM
    )


def _maximum_pin_offset_pm(
    pin_by_text: Mapping[str, list[tuple[Any, ...]]],
    base_by_text: Mapping[str, set[tuple[Any, ...]]],
) -> int:
    offsets: list[int] = []
    for text, pins in pin_by_text.items():
        bases = base_by_text.get(text, set())
        if len(bases) != 1:
            continue
        base = next(iter(bases))
        for pin in pins:
            offsets.append(
                max(
                    abs(int(pin[3]) - int(base[3])),
                    abs(int(pin[4]) - int(base[4])),
                )
            )
    return max(offsets, default=0)


def _physical_pm(value: float, *, unit_m: float) -> int:
    scaled = float(value) * float(unit_m) * PM_PER_M
    if not math.isfinite(scaled):
        raise GdsIdentityError("non-finite physical coordinate")
    rounded = int(round(scaled))
    if abs(scaled - rounded) > PM_ROUNDING_TOLERANCE:
        raise GdsIdentityError("physical coordinate is not integral in pm")
    return rounded


def _find_evaluation_dir(dataset_dir: Path, evaluation: str) -> Path:
    if EVALUATION_KEY_PATTERN.fullmatch(evaluation) is None:
        raise GdsIdentityError(f"invalid evaluation cache key: {evaluation!r}")
    matches = [
        path.parent.resolve()
        for path in dataset_dir.glob(f"**/evaluations/{evaluation}/summary.json")
    ]
    unique = sorted(set(matches))
    if len(unique) != 1:
        raise GdsIdentityError(
            f"expected one evaluation directory for {evaluation}, found {len(unique)}"
        )
    try:
        unique[0].relative_to(dataset_dir.resolve())
    except ValueError as exc:
        raise GdsIdentityError("evaluation directory escapes dataset root") from exc
    return unique[0]


def _geometry_audit_path(
    *, input_index_csv: Path, candidate_id_sha256: str, raw: Any
) -> Path:
    declared = _declared_file(raw, "candidate-bound geometry audit")
    parent = (input_index_csv.parent / "candidate_bound_geometry_audits").resolve()
    allowed_names = {
        f"{candidate_id_sha256}.json",
        f"{candidate_id_sha256[:16]}_geometry_audit.json",
    }
    if declared.parent != parent or declared.name not in allowed_names:
        raise GdsIdentityError(
            "geometry audit is not the exact candidate-bound producer path"
        )
    return declared


def _unique_rows(
    rows: Iterable[dict[str, str]], field: str, label: str
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        key = _sha256_value(row.get(field), f"{label}.{field}")
        if key in result:
            raise GdsIdentityError(f"{label} contains duplicate {field}: {key}")
        result[key] = row
    return result


def _read_csv(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = [dict(row) for row in reader]
        fields = set(reader.fieldnames or [])
    if not rows:
        raise GdsIdentityError(f"CSV is empty: {path}")
    return rows, fields


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GdsIdentityError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise GdsIdentityError(f"{label} JSON root is not an object")
    return value


def _regular_file(path: Path, label: str) -> Path:
    absolute = _absolute(path)
    if not absolute.is_file() or absolute.stat().st_size <= 0:
        raise GdsIdentityError(f"{label} is missing or empty: {absolute}")
    return absolute


def _directory(path: Path, label: str) -> Path:
    absolute = _absolute(path)
    if not absolute.is_dir():
        raise GdsIdentityError(f"{label} is missing: {absolute}")
    return absolute


def _declared_file(raw: Any, label: str) -> Path:
    value = str(raw or "").strip()
    path = Path(value).expanduser()
    if not value or not path.is_absolute():
        raise GdsIdentityError(f"{label} path must be absolute")
    return _regular_file(path, label)


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def _require_sha(path: Path, expected: Any, label: str) -> None:
    expected_sha = _sha256_value(expected, f"expected {label} SHA-256")
    actual = _stable_sha256(path)
    if actual != expected_sha:
        raise GdsIdentityError(
            f"{label} SHA-256 mismatch: expected={expected_sha}, actual={actual}"
        )


def _stable_sha256(path: Path) -> str:
    before = path.stat()
    digest = _sha256(path)
    after = path.stat()
    if _stat_identity(before) != _stat_identity(after):
        raise GdsIdentityError(f"file changed while hashing: {path}")
    return digest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_value(value: Any, label: str) -> str:
    digest = str(value or "").strip().lower()
    if SHA256_PATTERN.fullmatch(digest) is None:
        raise GdsIdentityError(f"{label} is not SHA-256")
    return digest


def _is_sha256(value: Any) -> bool:
    return SHA256_PATTERN.fullmatch(str(value or "")) is not None


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": _stable_sha256(path),
    }


def _write_json_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


def _write_csv_exclusive(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_sha256s(root: Path) -> None:
    records = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name == "SHA256SUMS.txt":
            continue
        records.append(f"{_sha256(path)}  {path.relative_to(root)}")
    with (root / "SHA256SUMS.txt").open("x", encoding="utf-8") as handle:
        handle.write("\n".join(records) + "\n")


__all__ = [
    "AUDIT_SCHEMA",
    "GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM",
    "GdsIdentityError",
    "STRUCTURAL_SCHEMA",
    "audit_gds_physical_identity",
    "gds_structural_identity",
    "gds_timestamp_normalized_sha256",
]
