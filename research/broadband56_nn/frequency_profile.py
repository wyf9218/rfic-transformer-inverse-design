"""Read-only, train-distribution profiling of a prepared Broadband56 snapshot.

The array interface accepts the existing training.Bundle without running it.
The standalone loader is NumPy-only and never imports a trainer or re-extracts
labels. CSV has exactly one row per frequency; two label modes remain distinct.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from .data import FREQUENCY_HZ, SPLIT_NAMES, Y_COLUMNS, _check_pin, _split_for_hash
from .io import read_json, save_json, sha256, utc_now


LABEL_MODES = ("STRICT_LUMPED", "POINTWISE_DESCRIPTOR_EXPERIMENTAL")
UNITS = ("nH", "nH", "dimensionless", "dimensionless")
QUANTILES = (0, 1, 5, 50, 95, 99, 100)
QUANTILE_NAMES = ("min", "p1", "p5", "p50", "p95", "p99", "max")
ARRAY_KEYS = ("geometry_ids", "geometry_sha256", "geometry", "frequency_hz", "y",
              "y_valid", "broadband_descriptor_valid", "strict_lumped_valid", "split")


def load_profile_bundle(data_root):
    """Verify existing prepared pins; read only arrays needed for profiling."""
    root = Path(data_root).resolve()
    manifest = read_json(root / "data_manifest.json")
    if manifest.get("schema") != "bb_data_manifest.v1" or manifest.get("status") != "PASS":
        raise ValueError("PASS prepared Broadband56 manifest required")
    for name in ("dataset.npz", "normalizer.json", "splits.json", "geometry_provenance.json"):
        _check_pin(manifest["artifacts"][name], root)
    source = _check_pin(manifest["source_manifest"], root)
    with np.load(root / "dataset.npz", allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in ARRAY_KEYS}
    return SimpleNamespace(root=root, arrays=arrays, manifest=manifest,
                           manifest_sha=sha256(root / "data_manifest.json"),
                           source_manifest_path=source,
                           splits=read_json(root / "splits.json"))


def _frequency_index(bundle, frequency_ghz):
    if isinstance(frequency_ghz, bool) or not isinstance(frequency_ghz, (int, np.integer)):
        raise ValueError("frequency is an exact integer GHz router, not an interpolated input")
    matches = np.flatnonzero(bundle.arrays["frequency_hz"] == int(frequency_ghz) * 10**9)
    if len(matches) != 1 or not 5 <= int(frequency_ghz) <= 60:
        raise ValueError("frequency must be present exactly once on the 5..60 GHz grid")
    return int(matches[0])


def frequency_mask(bundle, frequency_ghz, label_mode="STRICT_LUMPED"):
    """Exact per-frequency label eligibility, never prediction-based filtering."""
    if label_mode not in LABEL_MODES:
        raise ValueError("explicit supported label mode required")
    index = _frequency_index(bundle, frequency_ghz)
    arrays = bundle.arrays
    finite = np.isfinite(arrays["y"][:, index]).all(axis=1)
    if label_mode == "STRICT_LUMPED":
        return arrays["strict_lumped_valid"][:, index] & arrays["y_valid"][:, index].all(axis=1) & finite
    return arrays["broadband_descriptor_valid"][:, index] & finite


def _validate(bundle):
    arrays, manifest = bundle.arrays, bundle.manifest
    if tuple(manifest["target_columns"]) != Y_COLUMNS:
        raise ValueError("four physical target columns must retain exact contract order")
    if not np.array_equal(arrays["frequency_hz"], FREQUENCY_HZ):
        raise ValueError("complete exact 56-frequency grid required")
    count = len(arrays["geometry_ids"])
    if count != manifest["unique_geometries"] or count < 1:
        raise ValueError("manifest geometry count mismatch")
    for key in ("geometry_ids", "geometry_sha256"):
        if arrays[key].shape != (count,) or len(np.unique(arrays[key])) != count:
            raise ValueError("unique geometry ID/hash required")
    dimension = len(manifest["geometry_fields"])
    if arrays["geometry"].shape != (count, dimension) or not np.isfinite(arrays["geometry"]).all():
        raise ValueError("geometry fields/shape/finite values differ from actual contract")
    if arrays["y"].shape != (count, 56, 4):
        raise ValueError("expected geometry by 56 by four target tensor")
    for key, shape in (("y_valid", (count, 56, 4)),
                       ("broadband_descriptor_valid", (count, 56)),
                       ("strict_lumped_valid", (count, 56))):
        if arrays[key].shape != shape or arrays[key].dtype != np.dtype(bool):
            raise ValueError("validity masks must retain boolean values and exact shapes")
    if np.any(arrays["strict_lumped_valid"] & ~arrays["broadband_descriptor_valid"]):
        raise ValueError("strict domain cannot exceed original descriptor domain")
    if arrays["split"].shape != (count,) or not np.isin(arrays["split"], [0, 1, 2]).all():
        raise ValueError("existing global geometry split required")
    splits = getattr(bundle, "splits", None)
    if splits is None:
        splits = read_json(Path(bundle.root) / "splits.json")
    expected = np.array([_split_for_hash(str(h), splits["seed"]) for h in arrays["geometry_sha256"]])
    if not np.array_equal(expected, arrays["split"]):
        raise ValueError("stored geometry split differs from frozen 60/20/20 hash rule")
    for i, name in enumerate(SPLIT_NAMES):
        if manifest["split_counts"][name] != int((arrays["split"] == i).sum()):
            raise ValueError("global split count mismatch")
        members = [str(h) for h in arrays["geometry_sha256"][arrays["split"] == i]]
        if any(splits["by_geometry_sha256"].get(h) != name for h in members):
            raise ValueError("split membership manifest differs from prepared array")
    return splits


def _distribution(values):
    if not len(values):
        return {name: {**{q: None for q in QUANTILE_NAMES}, "mean": None, "std_population": None}
                for name in Y_COLUMNS}
    quantiles = np.percentile(values, QUANTILES, axis=0, method="linear")
    return {name: {**{q: float(quantiles[j, i]) for j, q in enumerate(QUANTILE_NAMES)},
                   "mean": float(values[:, i].mean()),
                   "std_population": float(values[:, i].std(ddof=0))}
            for i, name in enumerate(Y_COLUMNS)}


def _joint_coverage(values):
    """Observed train occupancy, not a claim that its rectangular hull is feasible."""
    if not len(values):
        return {"status": "NO_TRAIN_LABELS", "fit_split": "train", "n": 0,
                "occupied_cells": 0, "possible_cells": 256, "occupancy_fraction": None,
                "bins_per_feature": 4, "bin_edges": None, "cell_counts": []}
    lower, upper = values.min(axis=0), values.max(axis=0)
    span = upper - lower
    scaled = np.divide(values - lower, span, out=np.zeros_like(values), where=span > 0)
    cells = np.minimum(np.floor(scaled * 4).astype(np.int64), 3)
    keys, counts = np.unique(cells, axis=0, return_counts=True)
    possible = 4 ** int((span > 0).sum())
    return {"status": "EMPIRICAL_TRAIN_OCCUPANCY_ONLY", "fit_split": "train", "n": len(values),
            "bins_per_feature": 4, "binning": "equal-width train min/max; constant axes have one bin",
            "bin_edges": {name: np.linspace(lower[i], upper[i], 5).tolist() for i, name in enumerate(Y_COLUMNS)},
            "constant_features": [name for i, name in enumerate(Y_COLUMNS) if span[i] == 0],
            "occupied_cells": len(keys), "possible_cells": possible,
            "occupancy_fraction": len(keys) / possible,
            "cell_counts": [{"cell": key.tolist(), "count": int(n)} for key, n in zip(keys, counts)],
            "all_combinations_in_bounding_box_are_feasible": False}


def profile_bundle(bundle):
    """Return all 56 data slots; only train labels determine distribution summaries."""
    splits = _validate(bundle)
    arrays = bundle.arrays
    rows = []
    for f in range(5, 61):
        j = _frequency_index(bundle, f)
        targets = arrays["y"][:, j]
        finite = np.isfinite(targets).all(axis=1)
        row = {"frequency_ghz": f, "frequency_hz": f * 10**9,
               "total_unique_geometries": len(targets),
               "descriptor_valid_count": int(arrays["broadband_descriptor_valid"][:, j].sum()),
               "strict_valid_count": int(arrays["strict_lumped_valid"][:, j].sum()),
               "four_targets_simultaneously_finite_count": int(finite.sum()), "label_modes": {}}
        for mode in LABEL_MODES:
            domain = arrays["strict_lumped_valid" if mode == "STRICT_LUMPED" else "broadband_descriptor_valid"][:, j]
            eligible = frequency_mask(bundle, f, mode)
            statistics = {}
            for code, name in enumerate(SPLIT_NAMES):
                group = arrays["split"] == code
                reasons = {
                    "domain_invalid": int((group & ~domain).sum()),
                    "domain_valid_nonfinite_target": int((group & domain & ~finite).sum()),
                    "domain_valid_finite_but_target_mask_invalid": int((group & domain & finite & ~eligible).sum())}
                total, available = int(group.sum()), int((group & eligible).sum())
                if sum(reasons.values()) + available != total:
                    raise ValueError("eligibility/exclusion denominator did not reconcile")
                statistics[name] = {"total": total, "eligible": available, "excluded": total - available,
                                    "exclusion_reasons_disjoint": reasons}
            train_values = targets[(arrays["split"] == 0) & eligible].astype(np.float64)
            total = int(eligible.sum())
            status = "NO_STRICT_LABELS" if mode == "STRICT_LUMPED" and total == 0 else (
                "INSUFFICIENT_DATA" if any(statistics[name]["eligible"] < 2 for name in SPLIT_NAMES) else "PROVISIONAL")
            row["label_modes"][mode] = {"status": status, "eligible_count": total,
                "eligibility_fraction": total / len(targets), "splits": statistics,
                "train_distribution": _distribution(train_values),
                "train_joint_coverage": _joint_coverage(train_values),
                "nonfinite_per_target_counts_overlapping": {name: int((~np.isfinite(targets[:, i])).sum()) for i, name in enumerate(Y_COLUMNS)}}
        rows.append(row)
    return {"schema": "bb_frequency_data_profile.v1", "status": "PROFILE_COMPLETE_NO_TRAINING_CLAIM",
            "created_utc": utc_now(), "rows": rows, "frequency_slots": 56,
            "snapshot_unique_geometries": len(arrays["geometry_ids"]),
            "snapshot_role": "DEVELOPMENT_LT10K" if len(arrays["geometry_ids"]) < 10000 else "SNAPSHOT_PROFILE_ONLY",
            "source_table_frequency_rows": len(arrays["geometry_ids"]) * 56,
            "actual_gradient_training_geometries": "NOT_MEASURED_BY_DATA_PROFILE",
            "label_definitions": {"columns": list(Y_COLUMNS), "display_columns": ["Lp_nH", "Ls_nH", "Q_scalar=min(Qp,Qs)", "K_abs"],
                "units": list(UNITS), "STRICT_LUMPED": "original strict mask AND all four original y_valid flags AND four finite targets",
                "POINTWISE_DESCRIPTOR_EXPERIMENTAL": "original descriptor mask AND four finite targets; strict mask retained, never substituted"},
            "geometry": {"fields": bundle.manifest["geometry_fields"], "dimension": bundle.manifest["geometry_dim"], "units": bundle.manifest["geometry_units"]},
            "port_contract": bundle.manifest["port_contract"],
            "contract_fingerprint_sha256": bundle.manifest["contract_fingerprint_sha256"],
            "split": {"seed": splits["seed"], "method": splits["method"], "requested_fractions": [0.6, 0.2, 0.2],
                      "counts": bundle.manifest["split_counts"], "frequency_independent": True},
            "concepts": {"configuration_coverage_window": "Only frozen geometry bounds are bound here; no four-target Cartesian feasibility window is invented",
                         "configuration_geometry_bounds": bundle.manifest.get("geometry_bounds"),
                         "actual_distribution": "Per-frequency, per-mode eligible train labels only; exact linear quantiles",
                         "normalizer_scale": "NOT_FITTED_BY_PROFILER; train population std is descriptive, not an inherited broadband scale",
                         "model_support_domain": "NOT_ESTABLISHED_BY_DATA_PROFILE; observed train min/max hull is not a realizability guarantee"},
            "test_access": "Only existing split membership, finite/domain flags and sample counts; no test value distribution or model metrics used for tuning",
            "exclusion_policy": "Disjoint domain-invalid, domain-valid nonfinite, and finite target-mask-invalid reasons; no predicted-error filtering",
            "status_policy": "NO_STRICT_LABELS for zero strict labels; INSUFFICIENT_DATA if any split has fewer than two eligible labels; otherwise PROVISIONAL, never evidence of trained/validated support",
            "real_emx_validation_of_new_geometries": "NOT_RUN"}


def _flatten(row):
    flat = {key: value for key, value in row.items() if key != "label_modes"}
    for mode, value in row["label_modes"].items():
        prefix = "strict" if mode == "STRICT_LUMPED" else "descriptor"
        flat.update({f"{prefix}_status": value["status"], f"{prefix}_eligible_count": value["eligible_count"]})
        for split, counts in value["splits"].items():
            for key in ("total", "eligible", "excluded"):
                flat[f"{prefix}_{split}_{key}"] = counts[key]
            for reason, count in counts["exclusion_reasons_disjoint"].items():
                flat[f"{prefix}_{split}_{reason}"] = count
        for feature, stats in value["train_distribution"].items():
            for key, value_ in stats.items():
                flat[f"{prefix}_train_{feature}_{key}"] = value_
        for key in ("occupied_cells", "possible_cells", "occupancy_fraction"):
            flat[f"{prefix}_train_joint_{key}"] = value["train_joint_coverage"][key]
    return flat


def profile_prepared_data(data_root, out, *, extractor_source=None):
    """Create one versioned report directory; never alter a prepared snapshot."""
    source_root = Path(data_root).resolve()
    output = Path(out).resolve()
    if output == source_root or source_root in output.parents:
        raise ValueError("profile output must be outside the immutable prepared data directory")
    output.mkdir(parents=True, exist_ok=False)
    try:
        bundle = load_profile_bundle(data_root)
        profile = profile_bundle(bundle)
        profile["source_identity"] = {"data_root": str(bundle.root), "data_manifest_sha256": bundle.manifest_sha,
            "dataset": bundle.manifest["artifacts"]["dataset.npz"], "splits": bundle.manifest["artifacts"]["splits.json"],
            "source_manifest": bundle.manifest["source_manifest"], "raw_source_pins": bundle.manifest["sources"],
            "identity_scope": "Prepared artifact and source-manifest hashes rechecked; original large raw CSV bytes were not re-read or re-extracted"}
        profile["extractor"] = {"observed_srf_rule": "For each winding, 2*f < observed SRF; censored-above-60GHz uses 2*f <= 60GHz; strict requires descriptor-valid AND both windings below half SRF",
            "descriptor_rule": "finite S/Z/derived fields and positive primary/secondary differential resistance and inductive reactance",
            "high_frequency_interpretation": "The 5..60GHz scan cannot prove strict validity for 31..60GHz; this does not invalidate finite S parameters or license relabeling descriptor data as strict",
            "raw_label_masks_recomputed": False, "production_executable_byte_identity_proven": False,
            "inspected_source": None, "version_identity": "SOURCE_SHA_WHEN_SUPPLIED; data masks remain the authority"}
        if extractor_source is not None:
            source = Path(extractor_source).resolve()
            profile["extractor"]["inspected_source"] = {"path": str(source), "sha256": sha256(source)}
        save_json(output / "frequency_data_profile.json", profile)
        flat = [_flatten(row) for row in profile["rows"]]
        with (output / "frequency_data_profile.csv").open("x", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(flat[0]))
            writer.writeheader()
            writer.writerows(flat)
        receipt = {"schema": "bb_frequency_profile_receipt.v1", "status": "PASS_DATA_PROFILE_ONLY",
            "created_utc": utc_now(), "source_data_manifest_sha256": bundle.manifest_sha,
            "source_dataset_sha256": bundle.manifest["artifacts"]["dataset.npz"]["sha256"],
            "profiler_source_sha256": sha256(__file__), "frequencies": 56,
            "unique_geometries": profile["snapshot_unique_geometries"],
            "fifteen_ghz": profile["rows"][10], "model_training": False,
            "artifacts": {name: {"path": name, "sha256": sha256(output / name)} for name in ("frequency_data_profile.json", "frequency_data_profile.csv")}}
        save_json(output / "PROFILE_RECEIPT.json", receipt)
        with (output / "SHA256SUMS.txt").open("x", encoding="utf-8") as stream:
            for name in ("frequency_data_profile.json", "frequency_data_profile.csv", "PROFILE_RECEIPT.json"):
                stream.write(f"{sha256(output / name)}  {name}\n")
        return receipt
    except Exception as exc:
        save_json(output / "PROFILE_FAILED.json", {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}", "input": str(data_root)})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--extractor-source")
    args = parser.parse_args(argv)
    result = profile_prepared_data(args.data, args.out, extractor_source=args.extractor_source)
    print(f"{result['status']}: {result['unique_geometries']} unique geometries; 56 frequency slots")


if __name__ == "__main__":
    main()
