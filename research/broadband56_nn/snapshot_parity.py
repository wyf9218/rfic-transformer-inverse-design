"""Train-only physical parity against frozen cumulative or incremental CSVs.

This research adapter does not create a synthetic cumulative producer receipt,
change source files, evaluate a neural network, or read sealed-test metrics.
It retains the original parity receipt contract consumed by training/delivery.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
import torch

from .data import PHYSICAL_COLUMNS, S_COLUMNS
from .io import read_json, save_json, sha256, utc_now
from .physics import extract_physical


def _bound_path(pin, base):
    path = Path(pin["path"])
    if not path.is_absolute():
        path = base / path
    path = path.resolve()
    if not path.is_file() or sha256(path) != pin["sha256"]:
        raise ValueError(f"source SHA mismatch: {path}")
    expected_size = pin.get("size_bytes", pin.get("bytes"))
    if expected_size is not None and path.stat().st_size != expected_size:
        raise ValueError(f"source size mismatch: {path}")
    return path


def _boolean(value):
    if value.lower() not in ("true", "false"):
        raise ValueError("production validity flag must be explicit true/false")
    return value.lower() == "true"


def _shard_order(role):
    if role == "long_features":
        return (-2,)
    if role == "base/long_features":
        return (-1,)
    parts = role.split("/")
    if len(parts) != 3 or parts[0] != "increment" or not parts[1].isdigit():
        raise ValueError(f"unsupported long-feature shard role: {role}")
    return (int(parts[1]),)


def verify(data: str | Path, contract: str | Path, out: str | Path, count: int = 64) -> dict:
    """Check first ``count`` training geometries, plus one per uncovered shard.

    Every source shard is SHA-verified before and after verification. Numeric
    comparisons only use training geometries; all their 56 frequencies and both
    validity masks are retained, including undefined raw descriptors. Additional
    shard witnesses are deterministic and explicitly recorded, not model scores.
    """
    data, contract, out = Path(data).resolve(), Path(contract).resolve(), Path(out).resolve()
    if data.is_dir():
        data = data / "dataset.npz"
    if out.exists():
        raise FileExistsError(f"refusing to overwrite existing parity evidence: {out}")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("count must be a positive integer")
    manifest_path = data.parent / "data_manifest.json"
    receipt = {"schema": "bb_physical_extractor_parity.v1", "adapter": "frozen_multishard_train_only.v1",
               "created_utc": utc_now(), "status": "FAIL", "data_path": str(data), "data_sha": sha256(data),
               "contract_path": str(contract), "contract_sha": sha256(contract),
               "data_manifest_path": str(manifest_path), "data_manifest_sha": sha256(manifest_path),
               "sample_rule": f"first min({count},N_train) source-order training geometries, plus first training geometry per otherwise uncovered source shard; all 56 frequencies",
               "requested_count": count, "sample_split": "train", "sealed_test_evaluated": False,
               "model_evaluated": False, "real_emx_validation": "NOT_RUN", "source_files_modified": False,
               "rtol": 1e-7, "atol": 1e-8}
    try:
        metadata, configuration = read_json(manifest_path), read_json(contract)
        if metadata.get("status") != "PASS" or metadata["artifacts"]["dataset.npz"]["sha256"] != receipt["data_sha"]:
            raise ValueError("prepared data does not match PASS manifest")
        for key in ("ports", "port_order", "reference_impedance_ohm", "mode", "internal_permutation", "channel_order"):
            if configuration["port_contract"].get(key) != metadata["port_contract"].get(key):
                raise ValueError(f"runtime and source port contracts differ: {key}")
        identity_pins = [{"path": str(data), "sha256": receipt["data_sha"]},
                         {"path": str(contract), "sha256": receipt["contract_sha"]},
                         {"path": str(manifest_path), "sha256": receipt["data_manifest_sha"]}]
        source_manifest = _bound_path(metadata["source_manifest"], manifest_path.parent)
        identity_pins.append({"path": str(source_manifest), "sha256": metadata["source_manifest"]["sha256"]})
        roles = sorted((role for role in metadata["sources"] if role == "long_features" or role.endswith("/long_features")), key=_shard_order)
        if not roles or ("long_features" in roles and len(roles) != 1):
            raise ValueError("missing or mixed cumulative/incremental source layout")
        if roles[0] != "long_features":
            if roles[0] != "base/long_features" or roles[1:] != [f"increment/{i}/long_features" for i in range(len(roles)-1)]:
                raise ValueError("increment source shard sequence is not contiguous")
            if "selection_manifest" not in metadata:
                raise ValueError("increment sources require a frozen selection manifest")
        if "selection_manifest" in metadata:
            selection_path = _bound_path(metadata["selection_manifest"], manifest_path.parent)
            selection = read_json(selection_path)
            if selection.get("status") != "READY_FOR_10K" or selection["evidence"]["source_files"] != metadata["sources"]:
                raise ValueError("prepared source pins differ from frozen selection")
            identity_pins.append({"path": str(selection_path), "sha256": metadata["selection_manifest"]["sha256"]})
            receipt["selection_manifest"] = identity_pins[-1]
        shards = []
        for role in roles:
            pin = metadata["sources"][role]
            path = _bound_path(pin, source_manifest.parent)
            shards.append({"role": role, "path": str(path), "sha256": pin["sha256"],
                           "size_bytes": path.stat().st_size})
        receipt["production_csv_sources"] = shards
        root = Path(__file__).resolve().parents[2]
        implementations = [Path(__file__), Path(__file__).with_name("physics.py"), Path(__file__).with_name("data.py"),
                           root / "rfic_transformer_inverse_design/network_analysis.py",
                           root / "rfic_transformer_inverse_design/analysis/extraction.py",
                           root / "rfic_transformer_inverse_design/campaigns/broadband56_s4p_qa.py"]
        receipt["implementation_sources"] = [{"path": str(path.resolve()), "sha256": sha256(path)} for path in implementations]
        with np.load(data, allow_pickle=False) as archive:
            all_ids = archive["geometry_ids"].astype(str).tolist()
            if len(set(all_ids)) != len(all_ids):
                raise ValueError("prepared geometry IDs are not unique")
            train = np.flatnonzero(archive["split"] == 0).tolist()
            if not train:
                raise ValueError("no training geometries available")
            train_by_id = {all_ids[i]: i for i in train}
            owner = {}
            for shard_index, shard in enumerate(shards):
                members = set()
                with Path(shard["path"]).open(newline="", encoding="utf-8") as stream:
                    for row in csv.DictReader(stream):
                        gid = row["geometry_id"]
                        if gid not in train_by_id:
                            continue
                        if gid in owner and owner[gid] != shard_index:
                            raise ValueError("training geometry appears in multiple producer shards")
                        owner[gid] = shard_index
                        members.add(train_by_id[gid])
                shard["training_geometry_indices"] = sorted(members)
            if set(owner) != set(train_by_id):
                raise ValueError("source shards omit a prepared training geometry")
            chosen = set(train[:count])
            supplemental = []
            for shard in shards:
                members = shard["training_geometry_indices"]
                if members and not chosen.intersection(members):
                    chosen.add(members[0])
                    supplemental.append(members[0])
                shard["training_geometries_available"] = len(members)
                shard["training_geometries_checked"] = len(chosen.intersection(members))
                shard["sampled_training_geometry_indices"] = sorted(chosen.intersection(members))
                del shard["training_geometry_indices"]
            indices = np.asarray(sorted(chosen), dtype=int)
            ids = [all_ids[i] for i in indices]
            frequencies, s = archive["frequency_hz"].copy(), archive["s"][indices].copy()
            stored_physical = archive["physical_features"][indices].copy()
            stored_broad = archive["broadband_descriptor_valid"][indices].copy()
            stored_strict = archive["strict_lumped_valid"][indices].copy()
        if s.shape != (len(indices), 56, 32) or not np.array_equal(frequencies, np.arange(5, 61) * 1_000_000_000):
            raise ValueError("prepared S data lacks exact full Broadband56 shape/grid")
        receipt.update(geometry_indices=indices.tolist(), geometry_ids=ids, geometries_checked=len(ids),
                       supplemental_shard_geometry_indices=supplemental, frequency_rows_checked=len(ids) * 56)
        source_physical = np.empty_like(stored_physical)
        source_broad, source_strict = np.empty_like(stored_broad), np.empty_like(stored_strict)
        seen = np.zeros((len(ids), 56), dtype=bool)
        selected = {gid: i for i, gid in enumerate(ids)}
        for shard in shards:
            with Path(shard["path"]).open(newline="", encoding="utf-8") as stream:
                for row in csv.DictReader(stream):
                    if row["geometry_id"] not in selected:
                        continue
                    i, frequency = selected[row["geometry_id"]], int(row["frequency_hz"])
                    j = (frequency - 5_000_000_000) // 1_000_000_000
                    if not 0 <= j < 56 or frequencies[j] != frequency or seen[i, j]:
                        raise ValueError("duplicate/off-grid selected CSV source row")
                    if not np.array_equal(np.asarray([float(row[name]) for name in S_COLUMNS]), s[i, j]):
                        raise ValueError("prepared S channels differ from production CSV")
                    source_physical[i, j] = [float(row[name]) for name in PHYSICAL_COLUMNS]
                    source_broad[i, j] = _boolean(row["broadband_descriptor_valid"])
                    source_strict[i, j] = _boolean(row["strict_lumped_valid"])
                    seen[i, j] = True
        if not seen.all():
            raise ValueError("source CSV is missing selected training response rows")
        np.testing.assert_array_equal(source_physical, stored_physical)
        np.testing.assert_array_equal(source_broad, stored_broad)
        np.testing.assert_array_equal(source_strict, stored_strict)
        torch.set_num_threads(2)
        result = extract_physical(torch.from_numpy(s), torch.from_numpy(frequencies), configuration["port_contract"])
        y = result["y"].numpy()
        actual = np.stack([y[..., 0], y[..., 1], result["qp"].numpy(), result["qs"].numpy(),
                           y[..., 2], result["k_signed"].numpy(), y[..., 3]], axis=-1)
        errors = {}
        for index, name in enumerate(PHYSICAL_COLUMNS):
            reference, calculated = source_physical[..., index], actual[..., index]
            np.testing.assert_allclose(calculated, reference, rtol=1e-7, atol=1e-8, equal_nan=True, err_msg=name)
            finite = np.isfinite(reference) & np.isfinite(calculated)
            errors[name] = {"finite_values_compared": int(finite.sum()), "undefined_values_matched": int(np.isnan(reference).sum()),
                            "max_absolute_error": float(np.abs(calculated[finite] - reference[finite]).max()) if finite.any() else None}
        np.testing.assert_array_equal(result["broadband_descriptor_valid"].numpy(), source_broad)
        np.testing.assert_array_equal(result["strict_lumped_valid"].numpy(), source_strict)
        for pin in identity_pins + shards + receipt["implementation_sources"]:
            _bound_path(pin, data.parent)
        receipt.update(status="PASS", physical_feature_errors=errors, broadband_mask_exact_match=True,
                       strict_mask_exact_match=True, source_arrays_exact_match=True,
                       finite_solve_rows=int(result["s_parameter_valid"].sum()),
                       invalid_prediction_request_safety="COVERED_BY_SEPARATE_SYNTHETIC_TESTS",
                       note="Observed train-S extractor consistency only; not validation of inverse-generated geometries or a model accuracy result.")
    except Exception as error:
        receipt.update(status="FAIL", error=f"{type(error).__name__}: {error}")
        save_json(out, receipt)
        raise
    save_json(out, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--count", type=int, default=64)
    args = parser.parse_args()
    receipt = verify(args.data, args.contract, args.out, args.count)
    print(f"PHYSICAL_EXTRACTOR_PARITY={receipt['status']} TRAIN_GEOMETRIES={receipt['geometries_checked']}")


if __name__ == "__main__":
    main()
