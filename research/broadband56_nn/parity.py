"""Train-only, source-CSV-backed numerical parity receipt for the physical layer."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
import torch

from .data import PHYSICAL_COLUMNS, S_COLUMNS
from .io import read_json, save_json, sha256, utc_now
from .physics import extract_physical


def verify(data: str | Path, contract: str | Path, out: str | Path,
           count: int = 64) -> dict:
    """Verify selected training geometries against copied production CSV labels.

    No neural model or sealed test responses are evaluated. The source CSV,
    prepared data, manifest, frozen runtime contract and implementation hashes
    are retained in the receipt. Undefined production descriptors must agree
    as NaN, not be silently omitted. Both validity levels must match exactly.
    """
    data, contract, out = Path(data).resolve(), Path(contract).resolve(), Path(out).resolve()
    if data.is_dir():
        data = data / "dataset.npz"
    if out.exists():
        raise FileExistsError(f"refusing to overwrite existing parity evidence: {out}")
    if count < 1:
        raise ValueError("count must be positive")
    manifest_path = data.parent / "data_manifest.json"
    receipt = {"schema": "bb_physical_extractor_parity.v1", "created_utc": utc_now(),
               "status": "FAIL", "data_path": str(data), "data_sha": sha256(data),
               "contract_path": str(contract), "contract_sha": sha256(contract),
               "data_manifest_path": str(manifest_path),
               "data_manifest_sha": sha256(manifest_path),
               "sample_rule": f"first min({count},N_train) source-order training geometries; all 56 frequencies",
               "sample_split": "train", "sealed_test_evaluated": False,
               "model_evaluated": False, "real_emx_validation": "NOT_RUN",
               "rtol": 1e-7, "atol": 1e-8, "requested_count": count}
    try:
        metadata, configuration = read_json(manifest_path), read_json(contract)
        if metadata["status"] != "PASS" or metadata["artifacts"]["dataset.npz"]["sha256"] != receipt["data_sha"]:
            raise ValueError("prepared data does not match PASS manifest")
        if configuration["port_contract"] != metadata["port_contract"]:
            # Metadata may contain additional provenance fields. Scientific
            # port ordering and z0 must still match the actual invocation.
            for key in ("ports", "port_order", "reference_impedance_ohm"):
                if configuration["port_contract"].get(key) != metadata["port_contract"].get(key):
                    raise ValueError(f"runtime and source port contracts differ: {key}")
        root = Path(__file__).resolve().parents[2]
        sources = [Path(__file__).resolve(), Path(__file__).with_name("physics.py"),
                   root / "rfic_transformer_inverse_design/network_analysis.py",
                   root / "rfic_transformer_inverse_design/analysis/extraction.py",
                   root / "rfic_transformer_inverse_design/campaigns/broadband56_s4p_qa.py"]
        receipt["implementation_sources"] = [{"path": str(path), "sha256": sha256(path)} for path in sources]
        source_pin = metadata["sources"]["long_features"]
        csv_path = Path(source_pin["path"])
        if not csv_path.is_absolute():
            csv_path = Path(metadata["source_manifest"]["path"]).parent / csv_path
        if sha256(csv_path) != source_pin["sha256"]:
            raise ValueError("production CSV source hash changed")
        receipt["production_csv_source"] = {"path": str(csv_path), "sha256": source_pin["sha256"]}
        with np.load(data, allow_pickle=False) as archive:
            indices = np.flatnonzero(archive["split"] == 0)[:count]
            if len(indices) < 1:
                raise ValueError("no training geometries available")
            ids = archive["geometry_ids"][indices].tolist()
            frequencies = archive["frequency_hz"].copy()
            s = archive["s"][indices].copy()
            stored_physical = archive["physical_features"][indices].copy()
            stored_broad = archive["broadband_descriptor_valid"][indices].copy()
            stored_strict = archive["strict_lumped_valid"][indices].copy()
        if s.shape != (len(indices), 56, 32) or not np.array_equal(frequencies, np.arange(5, 61) * 1_000_000_000):
            raise ValueError("prepared S data does not have exact full Broadband56 shape/grid")
        receipt["geometry_indices"] = indices.tolist()
        receipt["geometry_ids"] = ids
        receipt["geometries_checked"] = len(ids)
        receipt["frequency_rows_checked"] = len(ids) * 56
        source_physical = np.empty_like(stored_physical)
        source_broad, source_strict = np.empty_like(stored_broad), np.empty_like(stored_strict)
        seen = np.zeros((len(ids), 56), dtype=bool)
        selected = {identifier: index for index, identifier in enumerate(ids)}
        with csv_path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                if row["geometry_id"] not in selected:
                    continue
                i = selected[row["geometry_id"]]
                frequency = int(row["frequency_hz"])
                j = (frequency - 5_000_000_000) // 1_000_000_000
                if not 0 <= j < 56 or frequencies[j] != frequency or seen[i, j]:
                    raise ValueError("duplicate/off-grid selected CSV source row")
                source_s = np.array([float(row[name]) for name in S_COLUMNS])
                if not np.array_equal(source_s, s[i, j]):
                    raise ValueError("prepared S channels differ from actual production CSV")
                source_physical[i, j] = [float(row[name]) for name in PHYSICAL_COLUMNS]
                source_broad[i, j] = row["broadband_descriptor_valid"].lower() == "true"
                source_strict[i, j] = row["strict_lumped_valid"].lower() == "true"
                seen[i, j] = True
                if seen.all():
                    break
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
            errors[name] = {"finite_values_compared": int(finite.sum()),
                            "undefined_values_matched": int(np.isnan(reference).sum()),
                            "max_absolute_error": float(np.abs(calculated[finite] - reference[finite]).max()) if finite.any() else None}
        broad = result["broadband_descriptor_valid"].numpy()
        strict = result["strict_lumped_valid"].numpy()
        np.testing.assert_array_equal(broad, source_broad)
        np.testing.assert_array_equal(strict, source_strict)
        receipt.update(status="PASS", physical_feature_errors=errors,
                       broadband_mask_exact_match=True, strict_mask_exact_match=True,
                       source_arrays_exact_match=True,
                       finite_solve_rows=int(result["s_parameter_valid"].sum()),
                       invalid_prediction_request_safety="COVERED_BY_SEPARATE_SYNTHETIC_TESTS",
                       note="This proves extractor consistency on observed training S; it does not validate inverse-generated geometries.")
        if sha256(data) != receipt["data_sha"] or sha256(contract) != receipt["contract_sha"]:
            raise ValueError("data or contract changed during parity verification")
        if any(sha256(pin["path"]) != pin["sha256"] for pin in receipt["implementation_sources"]):
            raise ValueError("implementation source changed during parity verification")
    except Exception as error:
        receipt["status"] = "FAIL"
        receipt["error"] = f"{type(error).__name__}: {error}"
        save_json(out, receipt)
        raise
    save_json(out, receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--count", type=int, default=64)
    args = parser.parse_args()
    receipt = verify(args.data, args.contract, args.out, args.count)
    print(f"PHYSICAL_EXTRACTOR_PARITY={receipt['status']} TRAIN_GEOMETRIES={receipt['geometries_checked']} RECEIPT={Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
