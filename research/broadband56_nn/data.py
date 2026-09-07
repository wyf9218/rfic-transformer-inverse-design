"""Hash-bound, geometry-grouped preparation of accepted Broadband56 research data.

This module never reads a growing campaign CSV implicitly. All input files are
explicit local pins, and production acceptance receipts are required. It does
not modify production data, invoke an EM solver, or evaluate any model.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


FREQUENCY_HZ = np.arange(5, 61, dtype=np.int64) * 1_000_000_000
S_COLUMNS = tuple(f"s{r}{c}_{part}" for r in range(1, 5) for c in range(1, 5) for part in ("re", "im"))
Y_COLUMNS = ("lp_nh", "ls_nh", "qmin", "k_abs")
PHYSICAL_COLUMNS = ("lp_nh", "ls_nh", "qp", "qs", "qmin", "signed_k", "k_abs")
SPLIT_NAMES = ("train", "validation", "test")


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, document: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")


def _check_pin(pin: dict[str, Any], base: Path) -> Path:
    path = Path(pin["path"])
    if not path.is_absolute():
        path = base / path
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"input is not a regular, non-symlink file: {path}")
    before = path.stat()
    digest = sha256(path)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError(f"input changed while hashing: {path}")
    if digest != pin["sha256"] or ("size_bytes" in pin and after.st_size != pin["size_bytes"]):
        raise ValueError(f"input SHA/size mismatch: {path}")
    return path


def _true(value: str) -> bool:
    if value not in ("true", "false"):
        raise ValueError(f"invalid validity flag {value!r}")
    return value == "true"


def _split_for_hash(geometry_sha: str, seed: int) -> int:
    # Fixed SHA thresholds preserve every old geometry's group under growth.
    number = int.from_bytes(hashlib.sha256(f"bb56-split-v1:{seed}:{geometry_sha}".encode()).digest()[:8], "big")
    if number * 10 < 6 * (1 << 64):
        return 0
    return 1 if number * 10 < 8 * (1 << 64) else 2


def _fit_scale(values: np.ndarray, valid: np.ndarray | None = None, floor: float = 1e-6) -> tuple[list[float], list[float]]:
    flattened = values.reshape(-1, values.shape[-1]).astype(np.float64)
    mask = np.isfinite(flattened) if valid is None else valid.reshape(flattened.shape) & np.isfinite(flattened)
    means, scales = [], []
    for column in range(flattened.shape[1]):
        selected = flattened[mask[:, column], column]
        if not len(selected):
            raise ValueError(f"no valid training labels for channel {column}")
        means.append(float(selected.mean()))
        scales.append(max(float(selected.std(ddof=0)), floor))
    return means, scales


def prepare_data(
    source_manifest_path: str | Path,
    out_dir: str | Path,
    previous_splits: str | Path | None = None,
    seed: int = 17,
) -> dict[str, Any]:
    """Freeze an accepted local snapshot into NPZ plus train-only normalization.

    Manifest schema ``bb_source_manifest.v1`` contains ``files`` pins named
    accepted_geometries, long_features, checkpoint_receipt,
    raw_products_receipt, geometry_bounds; additional pinned files are allowed.
    Pins contain path, sha256 and optional size_bytes. Public examples must use
    synthetic data. The output directory is create-once, including on failure.
    """
    source_path, output = Path(source_manifest_path).resolve(), Path(out_dir)
    output.mkdir(parents=True, exist_ok=False)
    try:
        return _prepare(source_path, output, previous_splits, seed)
    except Exception as exc:
        _write_json(output / "PREPARATION_FAILED.json", {"status": "FAIL", "error": str(exc), "source_manifest": str(source_path)})
        raise


def _prepare(source_path: Path, output: Path, previous_splits: str | Path | None, seed: int) -> dict[str, Any]:
    source = _json(source_path)
    if source.get("schema") != "bb_source_manifest.v1":
        raise ValueError("unsupported source manifest schema")
    port_contract = source.get("port_contract", {})
    if port_contract.get("port_order") != ["P001", "P002", "P003", "P004"] or port_contract.get("reference_impedance_ohm") != 50.0:
        raise ValueError("source must explicitly declare the verified four-port / 50-ohm contract")
    pins = source["files"]
    required = {"accepted_geometries", "long_features", "checkpoint_receipt", "raw_products_receipt", "geometry_bounds"}
    if not required.issubset(pins):
        raise ValueError(f"missing source files: {sorted(required - pins.keys())}")
    files = {key: _check_pin(pin, source_path.parent) for key, pin in pins.items()}
    original_stats = {key: (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) for key, p in files.items()}
    checkpoint, products, bounds = (_json(files[key]) for key in ("checkpoint_receipt", "raw_products_receipt", "geometry_bounds"))
    if checkpoint.get("overall_status") != "PASS" or checkpoint.get("decision") != "USE_CHECKPOINT":
        raise ValueError("snapshot lacks a PASS USE_CHECKPOINT receipt")
    if products.get("overall_status") != "PASS" or products.get("decision") != "USE_AS_FRESH_REAL_EMX_RAW_PRODUCTS":
        raise ValueError("raw products lack formal fresh-real-EMX acceptance")
    if not products.get("checks") or not all(value is True for value in products["checks"].values()):
        raise ValueError("raw product acceptance checks are incomplete")
    contract = checkpoint["contract_fingerprint_sha256"]
    if products.get("contract_fingerprint_sha256") != contract or bounds.get("contract_fingerprint_sha256") != contract:
        raise ValueError("source scientific contracts differ")
    if source.get("contract_fingerprint_sha256", contract) != contract:
        raise ValueError("manifest scientific contract differs")
    for role in ("accepted_geometries", "long_features", "geometry_bounds"):
        if checkpoint["inputs"][role]["sha256"] != pins[role]["sha256"]:
            raise ValueError(f"checkpoint does not bind {role}")
    for role in ("accepted_geometries", "long_features"):
        if products["outputs"][role]["sha256"] != pins[role]["sha256"]:
            raise ValueError(f"raw product receipt does not bind {role}")
    expected = int(checkpoint["expected_accepted"])
    if products["counts"]["accepted_geometries"] != expected or products["counts"]["geometry_frequency_rows"] != expected * 56:
        raise ValueError("acceptance receipt counts differ")
    fields = list(bounds["geometry_coverage_contract"]["field_order"])
    if len(set(fields)) != len(fields) or not fields or set(fields) != set(bounds["field_bounds_um"]):
        raise ValueError("geometry field contract is inconsistent")
    lower = np.array([bounds["field_bounds_um"][name][0] for name in fields])
    upper = np.array([bounds["field_bounds_um"][name][1] for name in fields])
    ids, hashes, geometry_rows, provenance = [], [], [], []
    gate_names = ("duplicate_status", "geometry_bounds_status", "analytical_status", "topology_status", "cadence_gds_status", "calibre_status", "emx_status", "s4p_status", "s_to_z_status", "feature_extraction_status")
    with files["accepted_geometries"].open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["campaign_contract_fingerprint"] != contract or any(row[name] != "PASS" for name in gate_names):
                raise ValueError("accepted geometry has wrong contract or failed gates")
            if int(row["accepted_sequence"]) != len(ids) + 1 or int(row["calibre_blocking_violations"]) != 0:
                raise ValueError("accepted sequence / DRC evidence is inconsistent")
            ids.append(row["geometry_id"])
            hashes.append(row["geometry_sha256"])
            geometry_rows.append([float(row[f"geom__{name}"]) for name in fields])
            provenance.append({name: row[name] for name in ("geometry_id", "geometry_sha256", "accepted_sequence", "campaign_phase", "acquisition_source")})
    if len(ids) != expected or len(set(ids)) != expected or len(set(hashes)) != expected:
        raise ValueError("geometry count or canonical uniqueness failed")
    geometry = np.asarray(geometry_rows, dtype=np.float64)
    if not np.isfinite(geometry).all() or (geometry < lower).any() or (geometry > upper).any():
        raise ValueError("nonfinite or out-of-contract geometry")
    if len({tuple(row) for row in geometry.tolist()}) != expected:
        raise ValueError("duplicate geometry coordinates under different identities")
    index = {name: i for i, name in enumerate(ids)}
    s = np.empty((expected, 56, 32), dtype=np.float64)
    physical = np.empty((expected, 56, len(PHYSICAL_COLUMNS)), dtype=np.float64)
    descriptor_valid = np.zeros((expected, 56), dtype=bool)
    strict_valid = np.zeros_like(descriptor_valid)
    seen = np.zeros_like(descriptor_valid)
    s4p_shas: dict[str, str] = {}
    with files["long_features"].open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            i = index.get(row["geometry_id"])
            if i is None or row["geometry_sha256"] != hashes[i] or row["campaign_contract_fingerprint"] != contract:
                raise ValueError("long feature row is not bound to accepted geometry")
            f = int(row["frequency_hz"])
            j = (f - 5_000_000_000) // 1_000_000_000
            if not 0 <= j < 56 or FREQUENCY_HZ[j] != f or seen[i, j]:
                raise ValueError("duplicate or off-grid frequency row")
            if int(row["accepted_sequence"]) != i + 1:
                raise ValueError("long feature accepted sequence mismatch")
            previous = s4p_shas.setdefault(ids[i], row["s4p_sha256"])
            if previous != row["s4p_sha256"]:
                raise ValueError("one geometry uses inconsistent S4P identities")
            s[i, j] = [float(row[name]) for name in S_COLUMNS]
            physical[i, j] = [float(row[name]) for name in PHYSICAL_COLUMNS]
            descriptor_valid[i, j] = _true(row["broadband_descriptor_valid"])
            strict_valid[i, j] = _true(row["strict_lumped_valid"])
            if strict_valid[i, j] and not descriptor_valid[i, j]:
                raise ValueError("strict validity cannot exceed descriptor validity")
            seen[i, j] = True
    if not seen.all() or not np.isfinite(s).all():
        raise ValueError("incomplete or nonfinite full S tensor")
    if len(set(s4p_shas.values())) != expected:
        raise ValueError("same S4P exported under multiple geometry identities")
    physical_valid = descriptor_valid[..., None] & np.isfinite(physical)
    if np.any(descriptor_valid[..., None] & ~np.isfinite(physical)):
        raise ValueError("physical descriptor marked valid despite nonfinite features")
    y_indices = [PHYSICAL_COLUMNS.index(name) for name in Y_COLUMNS]
    y = physical[:, :, y_indices]
    y_valid = strict_valid[..., None] & np.isfinite(y)
    # Raw physical values remain unaltered, including invalid finite / NaN values.
    # Consumers must mask before arithmetic, never compute 0 * NaN.
    split = np.array([_split_for_hash(digest, seed) for digest in hashes], dtype=np.int8)
    previous_pin = None
    if previous_splits is not None:
        previous_path = Path(previous_splits)
        old = _json(previous_path)
        if old.get("seed") != seed or old.get("contract_fingerprint_sha256") != contract:
            raise ValueError("old split seed/scientific contract differs")
        old_members = old["by_geometry_sha256"]
        for i, digest in enumerate(hashes):
            if digest in old_members:
                split[i] = SPLIT_NAMES.index(old_members[digest])
        old_id_map = old.get("geometry_id_to_sha256", {})
        if any(identifier in old_id_map and old_id_map[identifier] != digest for identifier, digest in zip(ids, hashes)):
            raise ValueError("old geometry ID changed canonical identity")
        previous_pin = {"path": str(previous_path.resolve()), "sha256": sha256(previous_path)}
    split_counts = {name: int((split == i).sum()) for i, name in enumerate(SPLIT_NAMES)}
    if any(count == 0 for count in split_counts.values()):
        raise ValueError("empty train/validation/test split; snapshot too small for frozen hash rule")
    train = split == 0
    g_min, g_max = geometry[train].min(0), geometry[train].max(0)
    if np.any(g_max <= g_min):
        raise ValueError("constant training geometry dimension cannot be normalized")
    s_mean, s_scale = _fit_scale(s[train])
    y_mean, y_scale = _fit_scale(y[train], y_valid[train])
    normalizer = {"schema": "bb_normalizer.v1", "fit_split": "train", "training_geometries": split_counts["train"], "scale_floor": 1e-6, "g_min": g_min.tolist(), "g_max": g_max.tolist(), "s_mean": s_mean, "s_scale": s_scale, "y_mean": y_mean, "y_scale": y_scale, "geometry_fields": fields, "field_names": fields, "s_columns": list(S_COLUMNS), "y_columns": list(Y_COLUMNS), "y_units": ["nH", "nH", "dimensionless", "dimensionless"], "physical_mask_domain": "strict_lumped_valid AND finite_feature", "contract_bounds_um": {"lower": lower.tolist(), "upper": upper.tolist()}}
    splits = {"schema": "bb_splits.v1", "seed": seed, "contract_fingerprint_sha256": contract, "method": "sha256 bb56-split-v1:seed:canonical_geometry_sha256 first64bits thresholds 0.6/0.8", "requested_fractions": [0.6, 0.2, 0.2], "counts": split_counts, "by_geometry_sha256": dict(zip(hashes, (SPLIT_NAMES[int(v)] for v in split))), "geometry_id_to_sha256": dict(zip(ids, hashes)), "ids": {name: [ids[j] for j in np.flatnonzero(split == i)] for i, name in enumerate(SPLIT_NAMES)}, "previous_splits": previous_pin}
    for key, path in files.items():
        now = path.stat()
        if (now.st_ino, now.st_size, now.st_mtime_ns) != original_stats[key]:
            raise ValueError(f"source changed during preparation: {key}")
    np.savez_compressed(output / "dataset.npz", geometry_ids=np.asarray(ids), geometry_sha256=np.asarray(hashes), geometry=geometry, frequency_hz=FREQUENCY_HZ, s=s, s_valid=np.ones_like(s, dtype=bool), y=y, y_valid=y_valid, physical_features=physical, physical_valid=physical_valid, broadband_descriptor_valid=descriptor_valid, strict_lumped_valid=strict_valid, split=split)
    _write_json(output / "normalizer.json", normalizer)
    _write_json(output / "splits.json", splits)
    _write_json(output / "geometry_provenance.json", {"rows": [{**row, "s4p_sha256": s4p_shas[row["geometry_id"]]} for row in provenance]})
    artifacts = {name: {"path": name, "sha256": sha256(output / name), "size_bytes": (output / name).stat().st_size} for name in ("dataset.npz", "normalizer.json", "splits.json", "geometry_provenance.json")}
    manifest = {"schema": "bb_data_manifest.v1", "status": "PASS", "source_manifest": {"path": str(source_path), "sha256": sha256(source_path)}, "sources": pins, "contract_fingerprint_sha256": contract, "unique_geometries": expected, "frequency_rows": expected * 56, "geometry_dim": len(fields), "geometry_fields": fields, "geometry_field_order": fields, "geometry_bounds": bounds["field_bounds_um"], "geometry_bounds_source": pins["geometry_bounds"], "geometry_units": "um", "frequency_hz": FREQUENCY_HZ.tolist(), "s_channels": list(S_COLUMNS), "physical_columns": list(PHYSICAL_COLUMNS), "target_columns": list(Y_COLUMNS), "split_counts": split_counts, "normalizer_fit_split": "train", "s_validity": "All finite full four-port S retained, including lumped-invalid frequencies", "physical_validity": "Main y_valid uses strict_lumped_valid AND finite_feature; physical_valid uses broadband_descriptor_valid AND finite_feature. Both raw domain masks retained.", "test_usage": "Sealed: input preparation only; no model evaluation or test-derived scaling", "port_contract": port_contract, "artifacts": artifacts}
    _write_json(output / "data_manifest.json", manifest)
    with (output / "SHA256SUMS.txt").open("x", encoding="utf-8") as handle:
        for name in (*artifacts, "data_manifest.json"):
            handle.write(f"{sha256(output / name)}  {name}\n")
    return manifest
