"""Static evidence-only figures for the four-panel frequency evaluation.

This module reads saved CSV/JSON, never a model or a remote system. Its default
inverse figure stage is grid; raw predictions remain in records and rate charts.
No output overwrite, interpolation of missing models, or inferred physical truth.
Scope is FOUR_TARGET_ONE_SHOT, never the later three-target Q-scan experiment.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import textwrap
from collections import defaultdict
from pathlib import Path

import numpy as np

from .io import read_json, save_json, sha256, utc_now

FEATURES = ("Lp_nH", "Ls_nH", "Qmin", "K_abs")
UNITS = ("nH", "nH", "dimensionless", "dimensionless")
TARGET_COLUMNS = ("target_lp_nh", "target_ls_nh", "target_q_scalar", "target_k_abs")
BLUE, GOLD, INK, GREY = "#2864DC", "#B88B19", "#24292F", "#E4E7EB"
MISSING = "#D9DDE2"
GROUP_KEYS = ("panel", "evaluation_source", "geometry_stage", "frequency_ghz", "label_mode")


def pin(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def rows(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def number(value):
    try:
        result = float(value)
    except (ValueError, TypeError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def boolean(value):
    return value is True or str(value).lower() in ("true", "1", "pass")


def group_key(row):
    return tuple(int(float(row[k])) if k == "frequency_ghz" else str(row[k]) for k in GROUP_KEYS)


def fixed_attainment(values, n_requested):
    """Return a finite-error step function, with all failures in its denominator."""
    values = np.asarray(values, float)
    if n_requested < len(values) or n_requested <= 0:
        raise ValueError("attainment requires the original positive requested denominator")
    finite = np.sort(values[np.isfinite(values)])
    if np.any(finite < 0):
        raise ValueError("absolute-error attainment cannot contain negative errors")
    return finite, np.arange(1, len(finite) + 1, dtype=float) / n_requested


def normalized_matrix(groups, panel, source, stage, mode):
    matrix = np.full((4, 56), np.nan)
    seen = set()
    for group in groups:
        if (group.get("panel"), group.get("evaluation_source"), group.get("geometry_stage"), group.get("label_mode")) != (panel, source, stage, mode):
            continue
        frequency = int(group["frequency_ghz"])
        if frequency not in range(5, 61) or frequency in seen:
            raise ValueError("duplicate or out-of-contract frequency group")
        seen.add(frequency)
        for j, feature in enumerate(FEATURES):
            matrix[j, frequency - 5] = number(group.get("features", {}).get(feature, {}).get("MAE_normalized"))
    return matrix


def target_cells(x, y, r_max, hit, x_edges, y_edges, *, sparse_n=10):
    """No smoothing, no clipping: outside-window counts remain explicit."""
    x, y, r_max, hit = (np.asarray(value) for value in (x, y, r_max, hit))
    xe, ye = np.asarray(x_edges, float), np.asarray(y_edges, float)
    if len(xe) < 2 or len(ye) < 2 or not np.all(np.diff(xe) > 0) or not np.all(np.diff(ye) > 0):
        raise ValueError("target-space edges must be frozen and strictly increasing")
    inside = np.isfinite(x) & np.isfinite(y) & (x >= xe[0]) & (x <= xe[-1]) & (y >= ye[0]) & (y <= ye[-1])
    xi = np.minimum(np.searchsorted(xe, x, side="right") - 1, len(xe) - 2)
    yi = np.minimum(np.searchsorted(ye, y, side="right") - 1, len(ye) - 2)
    result = []
    for a in range(len(xe) - 1):
        for b in range(len(ye) - 1):
            selected = inside & (xi == a) & (yi == b)
            n = int(selected.sum())
            finite = selected & np.isfinite(r_max)
            result.append({"x_index": a, "y_index": b, "x_low": float(xe[a]), "x_high": float(xe[a + 1]),
                "y_low": float(ye[b]), "y_high": float(ye[b + 1]), "N_requested": n,
                "N_finite": int(finite.sum()), "N_hit": int(np.sum(hit[selected])),
                "mean_r_max_available": float(np.mean(r_max[finite])) if finite.any() else None,
                "hit_rate_fixed_denominator": float(np.sum(hit[selected]) / n) if n else None,
                "sparse_unreliable": 0 < n < sparse_n})
    return {"cells": result, "N_requested": len(x), "N_in_window": int(inside.sum()),
            "N_outside_window_or_nonfinite": int((~inside).sum()), "sparse_threshold": sparse_n}


def _plotting():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
        "figure.facecolor": "white", "axes.facecolor": "white", "text.color": INK,
        "axes.labelcolor": INK, "axes.edgecolor": INK, "xtick.color": INK, "ytick.color": INK,
        "svg.fonttype": "none", "pdf.fonttype": 42, "savefig.facecolor": "white"})
    cmap = LinearSegmentedColormap.from_list("declared_blue", ["#EFF4FC", BLUE])
    cmap.set_bad(MISSING)
    return plt, cmap


def _header(fig, title, subtitle, note):
    import textwrap
    fig.suptitle(title, x=.07, ha="left", y=.98, fontsize=16, fontweight="bold")
    subtitle = "FOUR_TARGET_ONE_SHOT | " + subtitle
    fig.text(.07, .93, textwrap.fill(subtitle, int(fig.get_figwidth() * 11)), ha="left", va="top", fontsize=10)
    fig.text(.07, .024, "\n".join(textwrap.fill(s, int(fig.get_figwidth() * 12)) for s in note.splitlines()),
             ha="left", va="bottom", fontsize=8)


def _style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color=GREY, linewidth=.55, zorder=0)


def _write_csv(path, records):
    records = list(records)
    fields = list(dict.fromkeys(key for row in records for key in row))
    with Path(path).open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(records)


def _export(fig, out, name, data, caption, metadata):
    data_path = out / (name + ".json")
    save_json(data_path, {"schema": "frequency_large_chart_data.v1", "caption": caption,
                        "metadata": metadata, "data": data})
    files = [pin(data_path)]
    for extension in ("svg", "pdf", "png"):
        path = out / (name + "." + extension)
        if path.exists():
            raise FileExistsError(path)
        fig.savefig(path, dpi=300, metadata={"Creator": "frequency_large_figures"})
        files.append(pin(path))
    return {"chart_id": name, "caption": caption, "metadata": metadata, "files": files}


def _group_arrays(records, expected_n):
    grouped = defaultdict(dict)
    for record in records:
        target, feature = record["target_id"], record["feature"]
        if feature not in FEATURES or feature in grouped[target]:
            raise ValueError("duplicate target-feature or unknown feature in chart input")
        grouped[target][feature] = record
    if len(grouped) != expected_n or any(set(row) != set(FEATURES) for row in grouped.values()):
        raise ValueError("chart input must retain all four records for every requested target")
    ids = sorted(grouped)
    values = {key: np.asarray([[number(grouped[target][feature].get(key)) for feature in FEATURES] for target in ids])
              for key in ("reference_or_target", "predicted", "error_abs", "error_over_tolerance")}
    declared_finite = np.asarray([[boolean(grouped[target][feature].get("prediction_finite")) for feature in FEATURES] for target in ids])
    if np.any(np.isfinite(values["error_abs"]) & ~declared_finite):
        raise ValueError("nonfinite prediction status carries a finite chart error")
    values["finite_four"] = np.all(np.isfinite(values["error_over_tolerance"]) & declared_finite, axis=1)
    values["r_max"] = np.where(values["finite_four"], np.max(values["error_over_tolerance"], axis=1), np.nan)
    physical_valid = np.asarray([[boolean(grouped[target][feature].get("prediction_physical_valid", True)) for feature in FEATURES] for target in ids])
    source_valid = np.asarray([[boolean(grouped[target][feature].get("source_label_valid", True)) for feature in FEATURES] for target in ids])
    values["physical_valid_four"] = np.all(physical_valid, axis=1)
    values["source_valid_four"] = np.all(source_valid, axis=1)
    values["hit"] = values["finite_four"] & values["physical_valid_four"] & values["source_valid_four"] & (values["r_max"] <= 1)
    values["target_ids"] = ids
    return values


def _frozen_edges(freeze):
    edges = freeze.get("target_space_bin_edges")
    if not isinstance(edges, dict):
        raise ValueError("target_space_bin_edges must be fixed in CONFIGURATION_FREEZE before scoring")
    return {feature: edges[feature] for feature in FEATURES}


def _metadata(group, sources):
    config = read_json(sources["CONFIGURATION_FREEZE.json"]["path"])
    return {k: group.get(k) for k in (*GROUP_KEYS, "N_requested", "N_completed", "N_finite", "N_pending", "N_failed",
                                   "train_seed", "model_id", "dataset_id", "normalizer_id")} | {
        "frozen_context": {"request": config.get("config", config), "identity": config.get("identity"), "protocol": config.get("protocol")},
        "inference_mode": "FOUR_TARGET_ONE_SHOT",
        "q_scan_status": "NOT_THIS_EXPERIMENT",
        "prediction_physical_valid_meaning": "numerical/sign sanity only; not generated-candidate strict-label proof",
        "source_pins": sources, "error_scale": "frozen train-only evaluation scale; not per-target relative percentage",
        "tolerance": "frozen tau; ALL_FOUR_HIT iff finite physically-valid and source-valid all four and max(abs(error)/tau)<=1",
        "uncertainty": "fixed saved design; no LHS binomial confidence interval"}


def _verify_prediction_manifest(root):
    path = root / "prediction_manifest.json"
    manifest = read_json(path)
    if manifest.get("schema") != "frequency_large_prediction_manifest.v1":
        raise ValueError("completed prediction identity manifest required")
    bindings = dict(manifest["artifacts"])
    bindings["CONFIGURATION_FREEZE.json"] = manifest["frozen_inputs"]
    bindings["target_manifest.csv"] = manifest["target_manifest"]
    for name in ("prediction_records.csv", "summary.json", "frequency_status.csv", "CONFIGURATION_FREEZE.json", "target_manifest.csv"):
        source = bindings[name]
        if Path(source["path"]).resolve() != (root / name).resolve() or sha256(root / name) != source["sha256"]:
            raise ValueError("saved prediction manifest identity mismatch: " + name)
    return pin(path)


def render_large_figures(evaluation_root, out_dir):
    root, out = Path(evaluation_root), Path(out_dir)
    paths = {name: root / name for name in ("CONFIGURATION_FREEZE.json", "target_manifest.csv", "prediction_records.csv", "summary.json", "frequency_status.csv")}
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    sources = {name: pin(path) for name, path in paths.items()}
    sources["prediction_manifest.json"] = _verify_prediction_manifest(root)
    freeze, summary = read_json(paths["CONFIGURATION_FREEZE.json"]), read_json(paths["summary.json"])
    groups = [dict(group, frequency_ghz=int(float(group["frequency_ghz"]))) for group in summary["groups"]]
    predictions, status = rows(paths["prediction_records.csv"]), rows(paths["frequency_status.csv"])
    target_rows = rows(paths["target_manifest.csv"])
    target_lookup = {(row["panel"], row["target_id"]): row for row in target_rows}
    if len(target_lookup) != len(target_rows):
        raise ValueError("duplicate panel-target identity")
    status_keys = {(int(float(row["frequency_ghz"])), row["label_mode"]) for row in status}
    modes = sorted({row["label_mode"] for row in status})
    if not modes or len(status_keys) != len(status) or any((f, mode) not in status_keys for mode in modes for f in range(5, 61)):
        raise ValueError("frequency status requires every 5..60 GHz cell per label mode exactly once")
    pred_groups = defaultdict(list)
    for record in predictions:
        pred_groups[group_key(record)].append(record)
    primary = [g for g in groups if g.get("features") and g.get("geometry_stage") in ("reference", "grid", "actual_gds")]
    if len({group_key(g) for g in primary}) != len(primary):
        raise ValueError("multiple models/seeds at one chart cell require a separately named report")
    arrays = {group_key(g): _group_arrays(pred_groups[group_key(g)], int(g["N_requested"])) for g in primary}
    for g in primary:
        if int(arrays[group_key(g)]["hit"].sum()) != int(g["ALL_FOUR_HIT_count"]):
            raise ValueError("saved ALL_FOUR_HIT differs from four-feature AND in chart records")
    edges = _frozen_edges(freeze)
    out.mkdir(parents=True, exist_ok=False)
    contract_path = Path(__file__).parents[2] / "docs/research/FREQUENCY_LARGE_EVALUATION_CHART_CONTRACT.md"
    # Preserve the pre-code chart contract and exact renderer with the export.
    shutil.copyfile(contract_path, out / "CHART_CONTRACT.md")
    shutil.copyfile(__file__, out / "frequency_large_figures.py")
    save_json(out / "CHART_CONTEXT.json", {"schema": "frequency_large_chart_context.v1", "created_utc": utc_now(),
        "sources": sources, "configuration": freeze, "primary_inverse_stage": "grid",
        "inference_mode": "FOUR_TARGET_ONE_SHOT; NOT three-target Q-scan",
        "raw_stage_policy": "all raw rows retained in input and included in separate rate marks; detailed distribution figures use predeclared grid stage",
        "palette": {"blue": BLUE, "gold": GOLD, "missing": MISSING}, "no_confidence_interval_for_single_lhs": True})
    plt, cmap = _plotting()
    charts = []
    source_note = "Source summary SHA-256 " + sources["summary.json"]["sha256"][:16] + "…; full identity pins in chart JSON."
    # Full-frequency population and a separate categorical model-status strip.
    for mode in modes:
        selected = sorted((r for r in status if r["label_mode"] == mode), key=lambda r: int(float(r["frequency_ghz"])))
        frequencies = [int(float(r["frequency_ghz"])) for r in selected]
        fig, axes = plt.subplots(2, 1, figsize=(13, 7.6), gridspec_kw={"height_ratios": [4, 1]})
        for key, label, color, style in (("n_accepted_geometries", "All accepted geometries", INK, ":"),
            ("n_descriptor_valid", "Descriptor mask valid", BLUE, "-"), ("n_strict_valid", "Strict mask valid", GOLD, "--"),
            ("n_test_eligible", "Eligible test geometries in this label policy", INK, "-.")):
            axes[0].plot(frequencies, [number(r[key]) for r in selected], label=label, color=color, linestyle=style, linewidth=1.5)
        evaluated = [g for g in primary if g["panel"] == "A" and g["label_mode"] == mode]
        if evaluated:
            axes[0].scatter([g["frequency_ghz"] for g in evaluated], [g["N_requested"] for g in evaluated],
                            marker="o", facecolor="white", edgecolor=BLUE, s=55, label="Actual A evaluated N", zorder=4)
        axes[0].set(xlim=(4.5, 60.5), ylim=(0, None), ylabel="Unique geometries / actual A requests")
        axes[0].legend(ncol=2, frameon=False, fontsize=9, loc="lower right", bbox_to_anchor=(1, 1.01)); _style(axes[0])
        labels = list(dict.fromkeys(r["model_status"] for r in selected))
        for i, label in enumerate(labels):
            x = [f for f, r in zip(frequencies, selected) if r["model_status"] == label]
            axes[1].scatter(x, [i] * len(x), marker="s", s=35, color=BLUE if "TRAINED" in label and "NOT_" not in label else INK)
        display_labels = [textwrap.fill(label.replace("_", " "), 20) for label in labels]
        axes[1].set(yticks=range(len(labels)), yticklabels=display_labels, ylim=(-.5, len(labels) - .5), xlim=(4.5, 60.5), xlabel="Integer frequency (GHz)")
        axes[1].tick_params(axis="y", labelsize=8); axes[1].set_xticks([5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]); _style(axes[1])
        caption = "Source mask counts do not mean that models are trained. Eligible test labels and actual A evaluations are distinct. Untrained frequencies are not interpolated."
        _header(fig, "Frequency label availability and model status", mode + " | full 5–60 GHz grid | model status shown separately", caption + "\n" + source_note)
        fig.subplots_adjust(left=.14, right=.97, top=.77, bottom=.17, hspace=.38)
        charts.append(_export(fig, out, "frequency_counts_" + mode, selected, caption, {"label_mode": mode, "sources": sources})); plt.close(fig)
    # Matrix and physical marks preserve panel / source / stage separation.
    families = sorted({(g["panel"], g["evaluation_source"], g["geometry_stage"], g["label_mode"]) for g in primary})
    for panel, source, stage, mode in families:
        subset = sorted([g for g in primary if (g["panel"], g["evaluation_source"], g["geometry_stage"], g["label_mode"]) == (panel, source, stage, mode)], key=lambda g: g["frequency_ghz"])
        prefix = f"{panel}_{source}_{stage}_{mode}"
        matrix = normalized_matrix(groups, panel, source, stage, mode)
        fig, ax = plt.subplots(figsize=(13, 5.5))
        finite = matrix[np.isfinite(matrix)]
        image = ax.imshow(np.ma.masked_invalid(matrix), cmap=cmap, aspect="auto", origin="upper", extent=(4.5, 60.5, 3.5, -.5), vmin=0, vmax=max(float(finite.max()) if len(finite) else 1, 1e-12))
        ax.set(yticks=range(4), yticklabels=FEATURES, xticks=[5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60], xlabel="Integer frequency (GHz)")
        fig.colorbar(image, ax=ax, label="MAE / frozen train scale")
        for g in subset:
            for j in range(4):
                value = matrix[j, int(g["frequency_ghz"]) - 5]
                if np.isfinite(value):
                    ax.text(g["frequency_ghz"], j, f"{value:.3g}", ha="center", va="center", fontsize=7, rotation=90,
                            color="white" if len(finite) and value > .55 * finite.max() else INK)
        caption = "Grey = not evaluated / no finite metric, never zero. Normalization is by a frozen training scale, not by each target. Panel-specific source and grid are not physical validation."
        ns = ", ".join(f"{g['frequency_ghz']}GHz N={g['N_requested']}" for g in subset)
        _header(fig, "Four-feature normalized MAE by frequency", f"Panel {panel} | {source} | {stage} | {mode} | {ns}", caption + "\n" + source_note)
        fig.subplots_adjust(left=.1, right=.94, top=.82, bottom=.19)
        charts.append(_export(fig, out, prefix + "_normalized_mae", {"frequencies": list(range(5, 61)), "features": FEATURES,
            "values": [[None if not np.isfinite(v) else float(v) for v in row] for row in matrix], "groups": subset}, caption, _metadata(subset[0], sources))); plt.close(fig)
        fig, axes = plt.subplots(2, 2, figsize=(12, 8))
        for j, ax in enumerate(axes.flat):
            for key, label, color, marker, style in (("MAE_available", "MAE", BLUE, "o", "-"), ("P50_abs", "P50", GOLD, "s", "--"),
                ("P90_abs", "P90", INK, "^", ":"), ("P95_abs", "P95", BLUE, "D", "-.")):
                x = [int(g["frequency_ghz"]) for g in subset]
                y = [number(g["features"][FEATURES[j]].get(key)) for g in subset]
                # Missing cells break lines; a single frequency remains a marker.
                full = np.full(56, np.nan)
                for f, value in zip(x, y): full[f - 5] = value
                ax.plot(range(5, 61), full, color=color, marker=marker, markersize=5, fillstyle="none" if key == "P95_abs" else "full", linestyle=style, linewidth=1, label=label)
            ax.set(title=FEATURES[j], xlabel="Frequency (GHz)", ylabel="Absolute error (" + UNITS[j] + ")", xlim=(4.5, 60.5), ylim=(0, None))
            ax.legend(frameon=False, fontsize=8, ncol=2); _style(ax)
        caption = "Available-case physical errors use separate units. Missing frequencies break curves; a single evaluated frequency is a marker. P95 is a quantile, not the maximum."
        ns = ", ".join(f"{g['frequency_ghz']}GHz N={g['N_requested']}" for g in subset)
        _header(fig, "Physical-unit error levels by frequency", f"Panel {panel} | {source} | {stage} | {mode} | {ns}", caption + "\n" + source_note)
        fig.subplots_adjust(left=.1, right=.97, top=.83, bottom=.14, hspace=.5, wspace=.32)
        charts.append(_export(fig, out, prefix + "_physical_errors", subset, caption, _metadata(subset[0], sources))); plt.close(fig)
    # Fixed-denominator distribution and truth/target relationship per measured frequency.
    for group in primary:
        key = group_key(group); a = arrays[key]; n = int(group["N_requested"])
        panel, source, stage, f, mode = key
        prefix = f"{panel}_{source}_{stage}_{mode}_f{f}"
        meta = _metadata(group, sources)
        subtitle = f"Panel {panel} | {f} GHz | {source} | {stage} | {mode} | N_requested={n:,}"
        fig, ax = plt.subplots(figsize=(10, 6))
        x, y = fixed_attainment(a["r_max"], n)
        if len(x): ax.step(np.r_[0, x], np.r_[0, y], where="post", color=BLUE, linewidth=1.5)
        ax.axvline(1, color=INK, linestyle=":", linewidth=1, label="All-four tolerance threshold r_max = 1")
        ax.set(xlabel="r_max = max of four absolute errors / respective tolerance", ylabel="Requests at or below threshold / N_requested", ylim=(0, 1.03), xlim=(0, max(1.1, float(x[-1]) * 1.02 if len(x) else 1.1)))
        ax.legend(frameon=False, fontsize=9); _style(ax)
        caption = f"Fixed denominator {n}; finite four-feature predictions {int(a['finite_four'].sum())}. Missing errors are not imputed. ALL_FOUR_HIT also requires source validity and numerical/sign sanity, not generated strict-label proof; geometry is separate."
        _header(fig, "All-four target error attainment", subtitle, caption + "\n" + source_note)
        fig.subplots_adjust(left=.1, right=.97, top=.83, bottom=.19)
        charts.append(_export(fig, out, prefix + "_rmax_attainment", {"r_max": x.tolist(), "attainment": y.tolist(), "N_requested": n}, caption, meta)); plt.close(fig)
        fig, axes = plt.subplots(2, 2, figsize=(12, 8)); cdf_data = {}
        for j, ax in enumerate(axes.flat):
            x, y = fixed_attainment(a["error_abs"][:, j], n)
            if len(x): ax.step(np.r_[0, x], np.r_[0, y], where="post", color=BLUE, linewidth=1.4)
            ax.set(title=f"{FEATURES[j]} | finite {len(x)}/{n}", xlabel="Absolute error (" + UNITS[j] + ")", ylabel="Fraction of all requested", ylim=(0, 1.03), xlim=(0, max(float(x[-1]) * 1.03 if len(x) else 1, 1e-12))); _style(ax)
            cdf_data[FEATURES[j]] = {"absolute_error": x.tolist(), "attainment": y.tolist(), "N_requested": n, "N_finite": len(x)}
        caption = "Empirical fixed-denominator attainment functions; they need not reach one. Finite-only conditional CDF is not substituted. No single-LHS binomial uncertainty band."
        _header(fig, "Four-feature absolute-error attainment", subtitle, caption + "\n" + source_note)
        fig.subplots_adjust(left=.1, right=.97, top=.83, bottom=.14, hspace=.48, wspace=.32)
        charts.append(_export(fig, out, prefix + "_feature_attainment", cdf_data, caption, meta)); plt.close(fig)
        fig, axes = plt.subplots(2, 2, figsize=(12, 8)); scatter_data = []
        for j, ax in enumerate(axes.flat):
            valid = np.isfinite(a["reference_or_target"][:, j]) & np.isfinite(a["predicted"][:, j])
            x, y = a["reference_or_target"][valid, j], a["predicted"][valid, j]
            if len(x) >= 50:
                h = ax.hexbin(x, y, gridsize=32, mincnt=1, cmap=cmap, linewidths=0)
                fig.colorbar(h, ax=ax, label="Requests / hexbin", fraction=.047, pad=.03)
            elif len(x): ax.scatter(x, y, s=18, facecolors="none", edgecolors=BLUE)
            if len(x):
                low, high = min(x.min(), y.min()), max(x.max(), y.max()); pad = max((high - low) * .04, 1e-7)
                ax.plot([low - pad, high + pad], [low - pad, high + pad], color=INK, linestyle=":", linewidth=1)
                ax.set_xlim(low - pad, high + pad); ax.set_ylim(low - pad, high + pad)
            ax.set(title=f"{FEATURES[j]} | finite {len(x)}/{n}", xlabel=("Held-out EM truth" if panel == "A" else "Target") + " (" + UNITS[j] + ")", ylabel=("Forward prediction" if panel == "A" else source) + " (" + UNITS[j] + ")"); _style(ax)
            for i, target_id in enumerate(a["target_ids"]):
                scatter_data.append({"target_id": target_id, "feature": FEATURES[j], "unit": UNITS[j],
                    "reference_or_target": float(a["reference_or_target"][i, j]) if np.isfinite(a["reference_or_target"][i, j]) else None,
                    "prediction": float(a["predicted"][i, j]) if np.isfinite(a["predicted"][i, j]) else None})
        caption = "All finite pairs retained, with fixed requested denominators shown. Dotted identity is a visual reference, not a physical validation claim. Below 50 pairs use scatter rather than density."
        _header(fig, "Truth–prediction density" if panel == "A" else "Target–" + source + " density", subtitle, caption + "\n" + source_note)
        fig.subplots_adjust(left=.09, right=.95, top=.83, bottom=.14, hspace=.5, wspace=.48)
        charts.append(_export(fig, out, prefix + "_truth_prediction", scatter_data, caption, meta)); plt.close(fig)
        if panel not in ("B", "C") or stage != "grid": continue
        target_values = np.asarray([[number(target_lookup[(panel, target_id)][column]) for column in TARGET_COLUMNS] for target_id in a["target_ids"]])
        if not np.array_equal(target_values, a["reference_or_target"]):
            raise ValueError("target-space manifest differs from prediction target values")
        fig, axes = plt.subplots(2, 3, figsize=(14, 9)); cell_data = {}
        from matplotlib.patches import Rectangle
        for row_index, (j, k) in enumerate(((0, 1), (2, 3))):
            # Constant train-window dimensions are declared, not assigned invented spread.
            if len(set(edges[FEATURES[j]])) < 2 or len(set(edges[FEATURES[k]])) < 2:
                for ax in axes[row_index]: ax.text(.5, .5, "Degenerate frozen train window\n2D cell plot NOT_DEFINED", transform=ax.transAxes, ha="center"); ax.axis("off")
                cell_data[FEATURES[j] + "_" + FEATURES[k]] = {"status": "DEGENERATE_TRAIN_WINDOW"}
                continue
            cell = target_cells(target_values[:, j], target_values[:, k], a["r_max"], a["hit"], edges[FEATURES[j]], edges[FEATURES[k]])
            cell_data[FEATURES[j] + "_" + FEATURES[k]] = cell
            nx, ny = len(edges[FEATURES[j]]) - 1, len(edges[FEATURES[k]]) - 1
            for column, (metric, title) in enumerate((("N_requested", "Requested cell count"), ("mean_r_max_available", "Mean r_max (finite only)"), ("hit_rate_fixed_denominator", "All-four hit fraction"))):
                ax = axes[row_index, column]; values = np.full((ny, nx), np.nan)
                for c in cell["cells"]:
                    if c["N_requested"] and c[metric] is not None: values[c["y_index"], c["x_index"]] = c[metric]
                h = ax.pcolormesh(edges[FEATURES[j]], edges[FEATURES[k]], np.ma.masked_invalid(values), cmap=cmap, shading="flat", vmin=0, vmax=1 if column == 2 else None)
                fig.colorbar(h, ax=ax, fraction=.047, pad=.03)
                for c in cell["cells"]:
                    if c["sparse_unreliable"]:
                        ax.add_patch(Rectangle((c["x_low"], c["y_low"]), c["x_high"] - c["x_low"], c["y_high"] - c["y_low"], fill=False, hatch="xx", linewidth=0, edgecolor=INK))
                    if column == 0 and c["N_requested"]:
                        ax.text((c["x_low"] + c["x_high"]) / 2, (c["y_low"] + c["y_high"]) / 2, str(c["N_requested"]), ha="center", va="center", fontsize=6)
                ax.set(title=title, xlabel=FEATURES[j] + " (" + UNITS[j] + ")", ylabel=FEATURES[k] + " (" + UNITS[k] + ")")
            axes[row_index, 0].text(0, -.25, f"In frozen window {cell['N_in_window']}/{n}; outside/nonfinite {cell['N_outside_window_or_nonfinite']}", transform=axes[row_index, 0].transAxes, fontsize=8)
        caption = "Frozen train-window bin edges; no smoothing or clipping. Empty cells grey; crossed cells N<10 are unreliable. Available mean error uses finite count; cell hit fraction uses all cell requests. B may lie outside this window; outside counts remain explicit. C feasibility UNKNOWN."
        _header(fig, "Target-space coverage and SELF_PROXY reliability", subtitle, caption + "\n" + source_note)
        fig.subplots_adjust(left=.07, right=.96, top=.83, bottom=.18, hspace=.62, wspace=.46)
        charts.append(_export(fig, out, prefix + "_target_space", cell_data, caption, meta)); plt.close(fig)
    # Response and analytic pass are separate, plus pending physical audit denominator.
    usable = [g for g in groups if g.get("panel") in ("A", "B", "C") and g.get("features")]
    physical = [g for g in groups if g.get("panel") == "D"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 7))
    labels = [f"{g['panel']} {g['frequency_ghz']}GHz\n{g['geometry_stage']} N={g['N_requested']}" for g in usable]
    response = [number(g.get("ALL_FOUR_HIT_rate")) for g in usable]
    axes[0].barh(range(len(usable)), response, color=BLUE, edgecolor=INK, linewidth=.5)
    axes[0].set(yticks=range(len(usable)), yticklabels=labels, xlim=(0, 1.12), title="ALL_FOUR_HIT (response only)", xlabel="Fraction of all requested")
    for i, g in enumerate(usable): axes[0].text(response[i] + .015, i, f"{g['ALL_FOUR_HIT_count']}/{g['N_requested']}", va="center", fontsize=8)
    inverse = [g for g in usable if g["panel"] in ("B", "C")]
    geometry = [number(g.get("geometry_pass_rate")) for g in inverse]
    axes[1].barh(range(len(inverse)), geometry, color=GOLD, edgecolor=INK, hatch="//", linewidth=.5)
    axes[1].set(yticks=range(len(inverse)), yticklabels=[f"{g['panel']} {g['frequency_ghz']}GHz\n{g['geometry_stage']} N={g['N_requested']}" for g in inverse], xlim=(0, 1.12), title="Analytic geometry pass", xlabel="Fraction of all requested")
    for i, g in enumerate(inverse):
        if np.isfinite(geometry[i]): axes[1].text(geometry[i] + .015, i, f"{g.get('geometry_pass_count')}/{g['N_requested']}", va="center", fontsize=8)
    axes[2].axis("off")
    lines = ["GENERATED_FRESH_EMX audit", ""]
    for g in physical:
        lines += [f"Selection {g.get('selection_group', '?')} | {g.get('frequency_ghz')} GHz",
            f"Selected {g.get('N_requested', 0)}; solved {g.get('N_emx_solved', 0)}",
            f"Pending {g.get('N_pending', 0)}; failed {g.get('N_failed', 0)}", ""]
    if not physical: lines += ["No saved physical audit group"]
    lines += ["Pending is not physical failure.", "No EMX success percentage", "is inferred from SELF_PROXY."]
    axes[2].text(.03, .97, "\n".join(lines), transform=axes[2].transAxes, va="top", fontsize=11)
    for ax in axes[:2]: _style(ax); ax.tick_params(axis="y", labelsize=8)
    caption = "Response AND requires four finite, source-valid, sign-sane predictions within tolerance, not generated strict-label proof. Analytic geometry and fresh physical completion are separate; analytic checks are not layout/Calibre acceptance. No LHS confidence interval."
    _header(fig, "Response hits, geometry checks and physical audit progress", "Panels remain separate | raw/grid are paired stages, not independent samples", caption + "\n" + source_note)
    fig.subplots_adjust(left=.1, right=.97, top=.82, bottom=.19, wspace=.6)
    charts.append(_export(fig, out, "response_geometry_emx_status", {"response_geometry_groups": usable, "physical_groups": physical}, caption, {"sources": sources})); plt.close(fig)
    for source in sources.values():
        if sha256(source["path"]) != source["sha256"]:
            raise ValueError("a source changed while rendering; partial exports are not accepted")
    manifest = {"schema": "frequency_large_figure_manifest.v1", "status": "EXPORTED_PENDING_VISUAL_QA", "created_utc": utc_now(),
        "sources": sources, "renderer": pin(out / "frequency_large_figures.py"), "chart_contract": pin(out / "CHART_CONTRACT.md"),
        "chart_count": len(charts), "figure_exports": len(charts) * 3, "charts": charts,
        "visual_qa": "NOT_PERFORMED_BY_RENDERER", "real_emx_validation": summary.get("real_emx_validation", "NOT_RUN"),
        "inference_mode": "FOUR_TARGET_ONE_SHOT", "q_scan_status": "NOT_THIS_EXPERIMENT"}
    save_json(out / "FIGURE_MANIFEST.json", manifest)
    with (out / "SHA256SUMS.txt").open("x") as stream:
        for path in sorted(out.iterdir()):
            if path.is_file() and path.name != "SHA256SUMS.txt": stream.write(sha256(path) + "  " + path.name + "\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-root", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    result = render_large_figures(args.evaluation_root, args.out)
    print(json.dumps({k: result[k] for k in ("status", "chart_count", "figure_exports")}))


if __name__ == "__main__":
    main()
