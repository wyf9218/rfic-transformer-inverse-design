"""Exact descriptive integer-Q training coverage; no model or remote calls.

Read the frozen prepared dataset once with the existing profile loader. Reuse
the existing strict mask on the train slice only. Never fit a normalizer or
read held-out label values into a distribution. Positive counts are COUNT_ONLY,
not an adequacy claim; integer-Q envelope membership is a separate quantity.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from . import frequency_profile as profile_api
from .io import read_json, save_json, sha256, utc_now


CONTRACT_SHA256 = "9cd0eb8c3d6f44c48448aa75893f360d7d18a90ae992a1fd394456192a183620"
FREQUENCIES = tuple(range(5, 21))
Q_VALUES = tuple(range(10, 21))
BLUE, INK = "#2864DC", "#24292F"


def require(value, message):
    if not value:
        raise ValueError(message)


def pin(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def verify(record):
    actual = pin(record["path"])
    require(actual["sha256"] == record["sha256"], "source SHA changed: " + actual["path"])
    require("bytes" not in record or actual["bytes"] == record["bytes"], "source size changed")
    return Path(actual["path"])


def _stat(path):
    value = Path(path).stat()
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def count_support(bundle):
    """Pure count computation, including tails and exact original eligibility."""
    profile_api._validate(bundle)
    arrays = bundle.arrays
    train = arrays["split"] == 0
    # Strict-mask code sees train labels only, not held-out numerical values.
    train_bundle = SimpleNamespace(arrays={
        "frequency_hz": arrays["frequency_hz"],
        **{name: arrays[name][train] for name in
           ("y", "y_valid", "strict_lumped_valid", "broadband_descriptor_valid")},
    })
    cells, frequencies = [], []
    for f in FREQUENCIES:
        mask = profile_api.frequency_mask(train_bundle, f, "STRICT_LUMPED")
        j = profile_api._frequency_index(train_bundle, f)
        values = train_bundle.arrays["y"][mask, j].astype(np.float64)
        q_values = values[:, 2]
        n = len(q_values)
        lo = float(q_values.min()) if n else None
        hi = float(q_values.max()) if n else None
        below = int((q_values < 9.5).sum())
        above = int((q_values >= 20.5).sum())
        counts = [int(((q_values >= q - .5) & (q_values < q + .5)).sum()) for q in Q_VALUES]
        require(sum(counts) + below + above == n, "11 bins plus tails must equal full eligible train n")
        frequencies.append(dict(frequency_ghz=f, eligible_train_n=n,
            q_train_min=lo, q_train_max=hi, below_window_n=below, above_or_equal_window_n=above,
            window_n=sum(counts), bins_plus_tails_n=sum(counts) + below + above,
            train_min=values.min(axis=0).tolist() if n else [None] * 4,
            train_max=values.max(axis=0).tolist() if n else [None] * 4))
        for q, local_n in zip(Q_VALUES, counts):
            cells.append(dict(frequency_ghz=f, q=q, eligible_train_n=n,
                neighborhood_lower_inclusive=q - .5, neighborhood_upper_exclusive=q + .5,
                local_train_n=local_n, fraction_of_eligible_train=local_n / n if n else None,
                count_status="EMPTY" if local_n == 0 else "COUNT_ONLY",
                nominal_q_in_marginal_range=(lo <= q <= hi) if n else None,
                q_train_min=lo, q_train_max=hi,
                below_window_n=below, above_or_equal_window_n=above))
    return {"frequencies": frequencies, "cells": cells}


def crosscheck(result, profile, models, dataset_sha):
    """Compare saved profile and registered package metadata, never weights."""
    require(profile.get("schema") == "bb_frequency_data_profile.v1"
            and profile.get("snapshot_unique_geometries") == 10000, "formal10K profile required")
    require(profile["source_identity"]["dataset"]["sha256"] == dataset_sha, "profile dataset differs")
    require(set(models) == set(FREQUENCIES), "exact16 registered frequencies required")
    lookup = {int(row["frequency_ghz"]): row for row in profile["rows"]}
    checks = []
    for row in result["frequencies"]:
        f, n = row["frequency_ghz"], row["eligible_train_n"]
        saved = lookup[f]["label_modes"]["STRICT_LUMPED"]
        model = models[f]
        require(model["frequency_ghz"] == f and model["label_mode"] == "STRICT_LUMPED"
                and model["source_snapshot_geometries"] == 10000 and model["formal_10k"] is True
                and model["dataset_sha256"] == dataset_sha, "registered model not exact formal10K strict frequency")
        require(saved["splits"]["train"]["eligible"] == n, "profile eligible train n differs")
        for role in ("forward", "inverse"):
            require(model["roles"][role]["eligible_rows"]["train"]["eligible_geometries"] == n,
                    "model eligible train n differs")
        for side in ("min", "max"):
            observed = row["train_" + side]
            prior = [saved["train_distribution"][name][side] for name in profile_api.Y_COLUMNS]
            require(observed == prior == model["support"]["train_" + side],
                    "actual/profile/model train range differs")
        checks.append(dict(frequency_ghz=f, eligible_train_n=n, model_id=model["model_id"],
            profile_train_count="EXACT_MATCH", model_F_I_train_count="EXACT_MATCH",
            all_four_train_min_max="EXACT_MATCH"))
    return checks


def _load_inputs(contract_path, profile_path, registry_path):
    contract_pin = pin(contract_path)
    require(contract_pin["sha256"] == CONTRACT_SHA256, "exact frozen Q coverage contract required")
    contract = read_json(contract_path)
    require(contract["frequencies_ghz"] == list(FREQUENCIES) and contract["q_values"] == list(Q_VALUES)
            and contract["split"] == "train" and contract["label_mode"] == "STRICT_LUMPED",
            "unsupported frozen support contract")
    data_root = Path(contract["source"]["data_root"]).resolve(strict=True)
    dataset = data_root / "dataset.npz"
    before = _stat(dataset)
    bundle = profile_api.load_profile_bundle(data_root)
    require(_stat(dataset) == before, "prepared dataset changed while loading")
    require(bundle.manifest["unique_geometries"] == 10000, "development5K is not formal10K")
    require(bundle.manifest_sha == contract["source"]["data_manifest_sha256"], "prepared manifest differs")
    for name in ("dataset", "splits"):
        expected = contract["source"][name]
        stored = bundle.manifest["artifacts"][expected["path"]]
        require(stored["sha256"] == expected["sha256"]
                and (data_root / expected["path"]).stat().st_size == expected["size_bytes"],
                "contract and verified prepared source differ")
    expected_source = contract["source"]["source_manifest"]
    require(str(bundle.source_manifest_path.resolve()) == expected_source["path"]
            and bundle.manifest["source_manifest"]["sha256"] == expected_source["sha256"],
            "source manifest identity differs")
    pins = [contract_pin, pin(profile_path), pin(registry_path), pin(data_root / "data_manifest.json")]
    saved_profile, registry = read_json(profile_path), read_json(registry_path)
    require(saved_profile["source_identity"]["data_manifest_sha256"] == bundle.manifest_sha,
            "profile prepared manifest differs")
    entries = registry["models"]
    require(sorted(entry["frequency_ghz"] for entry in entries) == list(FREQUENCIES),
            "registry must contain exactly16 unique frequencies")
    models = {}
    for entry in entries:
        index_path = verify(entry["model_index"])
        pins.append(pin(index_path))
        index = read_json(index_path)
        require(index.get("schema") == "frequency_model_index.v1", "registered package index schema differs")
        selected = [m for m in index["models"].values()
                    if m["frequency_ghz"] == entry["frequency_ghz"] and m["label_mode"] == "STRICT_LUMPED"]
        require(len(selected) == 1, "one exact strict frequency model required")
        models[entry["frequency_ghz"]] = selected[0]
    return bundle, saved_profile, models, contract, pins, before


def make_figure(result, contract_sha, dataset_sha):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
        "text.color": INK, "axes.labelcolor": INK, "pdf.fonttype": 42,
        "svg.hashsalt": "frequency-q-train-support-v1"})
    counts = np.array([cell["local_train_n"] for cell in result["cells"]]).reshape(16, 11)
    maximum = max(1, int(counts.max()))
    cmap = LinearSegmentedColormap.from_list("support_blue", ["#FFFFFF", "#BCD0F4", BLUE])
    fig, ax = plt.subplots(figsize=(15.5, 11.8))
    fig.subplots_adjust(left=.17, right=.86, top=.84, bottom=.19)
    image = ax.imshow(counts, cmap=cmap, vmin=0, vmax=maximum, aspect="auto")
    ax.set_xticks(np.arange(11), [str(q) for q in Q_VALUES])
    ax.set_yticks(np.arange(16), [f"{row['frequency_ghz']} GHz   n={row['eligible_train_n']:,}"
                                 for row in result["frequencies"]])
    ax.set_xlabel("Integer Q target; descriptive neighborhood [q - 0.5, q + 0.5)", labelpad=10)
    ax.set_xticks(np.arange(-.5, 11, 1), minor=True)
    ax.set_yticks(np.arange(-.5, 16, 1), minor=True)
    ax.grid(which="minor", color="#D8DDE5", linewidth=.6)
    ax.tick_params(which="minor", bottom=False, left=False)
    for cell in result["cells"]:
        i, j = cell["frequency_ghz"] - 5, cell["q"] - 10
        label = str(cell["local_train_n"])
        if cell["nominal_q_in_marginal_range"] is False:
            label += "\nOUT"
        elif cell["nominal_q_in_marginal_range"] is None:
            label += "\nNO TRAIN"
        ax.text(j, i, label, ha="center", va="center", fontsize=10,
                color="white" if cell["local_train_n"] > .65 * maximum else INK)
    colorbar = fig.colorbar(image, ax=ax, fraction=.035, pad=.028)
    colorbar.set_label("Unique strict-valid train geometries in local Q neighborhood", labelpad=12)
    fig.suptitle("Marginal Q coverage in the frozen 5–20 GHz training subsets", x=.06, y=.967,
                 ha="left", fontsize=18, fontweight="bold")
    fig.text(.06, .921, "FORMAL 10K snapshot | STRICT_LUMPED | shared geometry-hash train split | Q = min(Qp, Qs)", fontsize=11)
    fig.text(.06, .887, "Exact local counts; row n is the full eligible train denominator, including both tails outside [9.5, 20.5).", fontsize=10)
    fig.text(.06, .112,
        "OUT: integer q lies outside observed train min/max; independent of the local-bin count. 0 = EMPTY neighborhood.\n"
        "Positive counts are COUNT_ONLY, not adequate support. No conditioning on Lp, Ls or |k|; joint reachability remains UNKNOWN.\n"
        "Neighborhood width 1 is descriptive density, NOT an error tolerance or changed eligibility gate. No arbitrary sparse threshold or CI.",
        fontsize=10, va="center", linespacing=1.5)
    fig.text(.06, .044, "Contract SHA-256 " + contract_sha, fontsize=8)
    fig.text(.06, .023, "Prepared dataset SHA-256 " + dataset_sha, fontsize=8)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for text in fig.texts:
        box = text.get_window_extent(renderer)
        require(fig.bbox.contains(*box.get_points()[0]) and fig.bbox.contains(*box.get_points()[1]),
                "figure header/footer overflow")
    return fig


def build(contract_path, profile_path, registry_path, out):
    output = Path(out).resolve()
    require(not output.exists(), "no-clobber support output already exists")
    contract = read_json(contract_path)
    data_root = Path(contract["source"]["data_root"]).resolve()
    require(output != data_root and data_root not in output.parents, "output must be outside immutable data")
    output.mkdir(parents=True, exist_ok=False)
    try:
        bundle, saved_profile, models, contract, sources, dataset_stat = _load_inputs(contract_path, profile_path, registry_path)
        implementation = pin(__file__)
        profiler = pin(profile_api.__file__)
        inputs = dict(contract=sources[0], metadata_sources=sources[1:], renderer=implementation,
            existing_profile_implementation=profiler, prepared_sources=contract["source"],
            checkpoint_bytes_read=False, production_access=False, heldout_values_in_distribution=False)
        save_json(output / "INPUTS.json", inputs)
        result = count_support(bundle)
        checks = crosscheck(result, saved_profile, models, contract["source"]["dataset"]["sha256"])
        save_json(output / "Q_TRAIN_SUPPORT.json", dict(schema="frequency_q_integer_train_support.v1",
            created_utc=utc_now(), data_scope=contract["data_scope"], split="train", label_mode="STRICT_LUMPED",
            contract=sources[0], input_pins=sources[1:], **result,
            crosschecks=checks, model_or_support_policy_changed=False,
            interpretation="Marginal descriptive counts only; no arbitrary adequacy threshold or joint feasibility claim."))
        with (output / "Q_TRAIN_SUPPORT.csv").open("x", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(result["cells"][0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(result["cells"])
        with (output / "FREQUENCY_DENOMINATORS.csv").open("x", encoding="utf-8", newline="") as stream:
            fields = [key for key in result["frequencies"][0] if key not in ("train_min", "train_max")]
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
            writer.writeheader()
            writer.writerows(result["frequencies"])
        fig = make_figure(result, sources[0]["sha256"], contract["source"]["dataset"]["sha256"])
        exports = []
        try:
            for extension in ("png", "svg", "pdf"):
                path = output / ("q_train_support_5to20." + extension)
                fig.savefig(path, dpi=300, facecolor="white")
                exports.append(pin(path))
        finally:
            import matplotlib.pyplot as plt
            plt.close(fig)
        for source in sources + [implementation, profiler]:
            verify(source)
        require(_stat(bundle.root / "dataset.npz") == dataset_stat, "prepared dataset changed during support build")
        save_json(output / "CROSSCHECKS.json", dict(status="EXACT_MATCH", rows=checks,
            note="Profile and all16 registered model train n/min/max matched without loading weights."))
        artifacts = [pin(p) for p in sorted(output.iterdir()) if p.is_file()]
        sums = output / "SHA256SUMS"
        with sums.open("x", encoding="utf-8") as stream:
            stream.writelines(record["sha256"] + "  " + Path(record["path"]).name + "\n" for record in artifacts)
        receipt = dict(schema="frequency_q_integer_train_support_receipt.v1", status="COMPLETE_PENDING_INDEPENDENT_QA",
            created_utc=utc_now(), contract=sources[0], implementation=implementation, artifacts=artifacts,
            sha256sums=pin(sums), exports=exports, frequency_count=16, q_count=11, cell_count=176,
            snapshot_geometries=10000, train_values_only=True, source_data_modified=False,
            normalizer_fit=False, training_calls=0, inference_calls=0, remote_calls=0,
            dataset_hash_verification="existing loader verified exact frozen dataset SHA; file identity/size/mtime stable after build",
            scientific_change=False, numerical_qa="AUTHOR_CROSSCHECK_ONLY_INDEPENDENT_QA_REQUIRED",
            visual_qa="NOT_RUN_AUTOMATIC_RENDER_ONLY_INDEPENDENT_QA_REQUIRED")
        marker = output / ".Q_SUPPORT_RECEIPT.pending.json"
        save_json(marker, receipt)
        os.link(marker, output / "Q_SUPPORT_RECEIPT.json")
        marker.unlink()
        return pin(output / "Q_SUPPORT_RECEIPT.json")
    except Exception as error:
        save_json(output / "Q_SUPPORT_FAILED.json", dict(status="FAILED", created_utc=utc_now(),
            error=type(error).__name__ + ": " + str(error), prior_artifacts_retained=True))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.contract, args.profile, args.registry, args.out), sort_keys=True))


if __name__ == "__main__":
    main()
