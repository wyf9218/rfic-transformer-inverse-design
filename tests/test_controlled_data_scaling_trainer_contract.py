from __future__ import annotations

import hashlib
import importlib.util
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "train_physical_feature_tandem_inverse.py"
SPEC = importlib.util.spec_from_file_location("controlled_tandem_trainer", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
TRAINER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRAINER)

BUILDER_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_controlled_data_scaling_split.py"
BUILDER_SPEC = importlib.util.spec_from_file_location("controlled_scaling_builder", BUILDER_SCRIPT)
assert BUILDER_SPEC is not None and BUILDER_SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(BUILDER_SPEC)
BUILDER_SPEC.loader.exec_module(BUILDER)


INPUT_COLUMNS = [
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
]
GEOMETRY_COLUMNS = [
    "geom__primary_outer_width_um",
    "geom__primary_outer_height_um",
    "geom__secondary_outer_width_um",
    "geom__secondary_outer_height_um",
    "geom__line_width_um",
    "geom__primary_terminal_y_span_um",
    "geom__secondary_terminal_y_span_um",
    "geom__offset_um",
    "geom__primary_feed_extension_um",
    "geom__secondary_feed_extension_um",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_external_declared_normalization_is_identical_across_train_subsets(tmp_path: Path) -> None:
    contract_path = tmp_path / "normalization.json"
    contract_path.write_text(
        json.dumps(
            {
                "schema": "declared_midpoint_half_range_normalization_v1",
                "input_columns": INPUT_COLUMNS,
                "geometry_columns": GEOMETRY_COLUMNS,
                "input_lower": [0.5, 0.5, 5.0, 0.0],
                "input_upper": [3.0, 3.0, 25.0, 0.8],
                "geometry_lower": [160.0, 160.0, 160.0, 160.0, 3.0, 20.0, 20.0, -90.0, 100.0, 100.0],
                "geometry_upper": [520.0, 520.0, 520.0, 520.0, 12.0, 90.0, 90.0, 90.0, 320.0, 320.0],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    x = np.asarray(
        [
            [0.5, 3.0, 5.0, 0.0],
            [3.0, 0.5, 25.0, 0.8],
            [1.75, 1.75, 15.0, 0.4],
            [1.0, 2.0, 10.0, 0.2],
        ],
        dtype=float,
    )
    y_lower = np.asarray([160.0, 160.0, 160.0, 160.0, 3.0, 20.0, 20.0, -90.0, 100.0, 100.0])
    y_upper = np.asarray([520.0, 520.0, 520.0, 520.0, 12.0, 90.0, 90.0, 90.0, 320.0, 320.0])
    y = np.vstack([y_lower, y_upper, 0.5 * (y_lower + y_upper), 0.75 * y_lower + 0.25 * y_upper])
    matrix = {
        "x": x,
        "y": y,
        "split_x": x,
        "source_indices": np.arange(len(x)),
        "source_evaluations": [f"row_{index}" for index in range(len(x))],
        "source_geometry_identities": [hashlib.sha256(str(index).encode()).hexdigest() for index in range(len(x))],
    }
    common = {
        "floor": 1.0e-12,
        "fixed_contract_path": str(contract_path),
        "expected_fixed_contract_sha256": _sha256(contract_path),
        "input_columns": INPUT_COLUMNS,
        "geometry_columns": GEOMETRY_COLUMNS,
    }
    first = TRAINER._normalize(
        matrix,
        {"train": np.asarray([0, 2]), "validation": np.asarray([1]), "test": np.asarray([3])},
        **common,
    )
    second = TRAINER._normalize(
        matrix,
        {"train": np.asarray([1, 3]), "validation": np.asarray([0]), "test": np.asarray([2])},
        **common,
    )
    for key in ("x_mean", "x_scale", "feature_lower", "feature_upper", "y_mean", "y_scale", "geometry_lower", "geometry_upper"):
        np.testing.assert_array_equal(first["normalization"][key], second["normalization"][key])
    np.testing.assert_allclose(first["normalization"]["geometry_lower"], -1.0)
    np.testing.assert_allclose(first["normalization"]["geometry_upper"], 1.0)
    assert first["normalization_contract"]["train_arm_specific_statistics_used"] is False
    assert first["normalization_contract"]["large_arm_empirical_statistics_used"] is False


def test_continuous_permutation_stream_emits_only_full_batches_and_uniform_exposure() -> None:
    train_indices = np.arange(7, dtype=int)
    data = {
        "training_batch_sampler_state": {
            "family": "row_uniform",
            "train_indices": train_indices,
        }
    }
    state = TRAINER._init_continuous_permutation_batch_state(data)
    batches = TRAINER._training_batches(
        data,
        4,
        np.random.default_rng(2026082201),
        continuous_state=state,
        exact_batch_count=5,
    )
    assert [len(batch) for batch in batches] == [4, 4, 4, 4, 4]
    stream = np.concatenate(batches)
    assert len(stream) == 20
    assert set(stream[:7]) == set(train_indices)
    assert set(stream[7:14]) == set(train_indices)
    counts = np.bincount(stream, minlength=len(train_indices))
    assert int(np.max(counts) - np.min(counts)) <= 1
    assert int(state["emitted_row_draws"]) == 20


def test_fixed_common_holdout_manifest_keeps_identical_identity_sets_across_arms(tmp_path: Path) -> None:
    identities = [hashlib.sha256(f"geometry-{index}".encode()).hexdigest() for index in range(8)]
    manifest_path = tmp_path / "holdout.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "fixed_common_holdout_geometry_identity_v1",
                "identity_kind": "canonical_geometry_sha256",
                "validation_geometry_identities": [identities[1], identities[5]],
                "test_geometry_identities": [identities[2], identities[6]],
                "selection_method": "stable_hash_within_physical_cell_x_source_batch",
                "stratification": ["physical_cell_4d", "source_batch"],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        fixed_common_holdout_manifest_json=str(manifest_path),
        fixed_common_holdout_manifest_sha256=_sha256(manifest_path),
    )

    def matrix(selected: list[int]) -> dict[str, object]:
        return {
            "count": len(selected),
            "source_geometry_identities": [identities[index] for index in selected],
        }

    large_order = list(range(8))
    small_order = [6, 5, 2, 1, 7, 0]
    large_split, large_audit = TRAINER._split_indices_from_common_holdout_manifest(
        matrix(large_order), args
    )
    small_split, small_audit = TRAINER._split_indices_from_common_holdout_manifest(
        matrix(small_order), args
    )

    def selected_identities(order: list[int], split: dict[str, np.ndarray], name: str) -> set[str]:
        loaded = [identities[index] for index in order]
        return {loaded[int(index)] for index in split[name]}

    for name in ("validation", "test"):
        assert selected_identities(large_order, large_split, name) == selected_identities(
            small_order, small_split, name
        )
    assert len(large_split["train"]) == 4
    assert len(small_split["train"]) == 2
    assert large_audit["fixed_common_holdout_manifest"]["sha256"] == small_audit[
        "fixed_common_holdout_manifest"
    ]["sha256"]


def test_materializer_builds_exact_nested_arms_without_identity_overlap(tmp_path: Path) -> None:
    base_path = tmp_path / "base.csv"
    increment_path = tmp_path / "increment.csv"
    geometry_columns = list(GEOMETRY_COLUMNS)

    def geometry(index: int) -> dict[str, str]:
        values = [
            160.0 + index,
            170.0 + index,
            180.0 + index,
            190.0 + index,
            3.0 + (index % 80) / 10.0,
            20.0 + (index % 60),
            21.0 + (index % 60),
            -90.0 + (index % 180),
            100.0 + (index % 200),
            101.0 + (index % 200),
        ]
        return {column: format(value, ".17g") for column, value in zip(geometry_columns, values)}

    def features(index: int) -> tuple[float, float, float, float]:
        cell = index % 4
        return (
            0.75 + 0.625 * cell,
            0.8 + 0.6 * cell,
            7.0 + 5.0 * cell,
            0.1 + 0.2 * cell,
        )

    base_fields = [
        "evaluation",
        "touchstone_path",
        "touchstone_sha256",
        "lp_nh_center",
        "ls_nh_center",
        "q_center",
        "k_abs_center",
        *geometry_columns,
    ]
    with base_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=base_fields)
        writer.writeheader()
        for index in range(100):
            lp, ls, q, k = features(index)
            writer.writerow(
                {
                    "evaluation": f"base-{index}",
                    "touchstone_path": f"/real/base-{index}.s4p",
                    "touchstone_sha256": hashlib.sha256(f"base-touchstone-{index}".encode()).hexdigest(),
                    "lp_nh_center": lp,
                    "ls_nh_center": ls,
                    "q_center": q,
                    "k_abs_center": k,
                    **geometry(index),
                }
            )
    increment_fields = [
        "evaluation",
        "touchstone_path",
        "touchstone_sha256",
        *INPUT_COLUMNS,
        *geometry_columns,
    ]
    with increment_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=increment_fields)
        writer.writeheader()
        for offset in range(120):
            index = 100 + offset
            lp, ls, q, k = features(index)
            writer.writerow(
                {
                    "evaluation": f"increment-{offset}",
                    "touchstone_path": f"/real/increment-{offset}.s4p",
                    "touchstone_sha256": hashlib.sha256(f"increment-touchstone-{offset}".encode()).hexdigest(),
                    INPUT_COLUMNS[0]: lp,
                    INPUT_COLUMNS[1]: ls,
                    INPUT_COLUMNS[2]: q,
                    INPUT_COLUMNS[3]: k,
                    **geometry(index),
                }
            )

    out_dir = tmp_path / "materialized"
    result = BUILDER.main(
        [
            "--base-pool-csv",
            str(base_path),
            "--base-pool-sha256",
            _sha256(base_path),
            "--increment-training-csv",
            str(increment_path),
            "--increment-training-sha256",
            _sha256(increment_path),
            "--out-dir",
            str(out_dir),
            "--expected-base-rows",
            "100",
            "--expected-increment-rows",
            "120",
            "--expected-accepted-rows",
            "220",
            "--expected-increment-range-rejects",
            "0",
            "--holdout-each",
            "10",
            "--large-train-count",
            "200",
            "--small-train-count",
            "100",
            "--min-holdout-occupied-cells",
            "2",
            "--subset-seeds",
            "11,12,13,14,15",
        ]
    )
    assert result == 0
    summary = json.loads(
        (out_dir / "controlled_data_scaling_materialization_summary.json").read_text(encoding="utf-8")
    )
    assert summary["overall_status"] == "PASS"
    assert summary["split_contract"]["large_train_count"] == 200
    assert summary["split_contract"]["validation_count"] == 10
    assert summary["split_contract"]["test_count"] == 10
    assert len(summary["arms"]) == 6
    for arm in summary["arms"][1:]:
        assert arm["training_row_count"] == 100
        assert arm["validation_row_count"] == 10
        assert arm["test_row_count"] == 10
