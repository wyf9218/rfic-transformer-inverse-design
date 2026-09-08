"""Standalone marker-clipping repair for saved physical MAE/P95 panels.

Only scatter clipping/layering differs from the frozen v1 panel. Statistics,
scope, intervals, zero baseline and annotations are unchanged. No model,
inference, training, CAD, solver or remote call occurs. Never edits live v1.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np

from . import frequency_physical_statistics_figures as v1


V1_SHA256 = "fefc8b4ca0acad73dabbc14622bd33fb400810f3bb9359ac07f138a1677a5e19"
ESTIMANDS = ("selected_q_proxy", "all_candidates")
SCOPES = (v1.FORMAL, v1.DEVELOPMENT)


def make_figure(data, scope, estimand):
    """Render saved metrics, preserving v1 exactly except marker visibility."""
    v1.require(scope in SCOPES, "unsupported dataset scope")
    v1.require(estimand in ESTIMANDS, "unsupported estimand")
    plt = v1.plot_style()
    fig, axes = plt.subplots(4, 2, figsize=(18, 15))
    fig.subplots_adjust(left=.075, right=.975, top=.89, bottom=.13, hspace=.48, wspace=.22)
    for i, feature in enumerate(v1.FEATURES):
        for j, comparison in enumerate(("emx_minus_target", "emx_minus_frozen_proxy")):
            ax = axes[i, j]
            lookup = v1.metric_group(data, scope, estimand, comparison, feature)
            frequencies = v1.FREQUENCIES if scope == v1.FORMAL else (15,)
            xs = np.arange(len(frequencies))
            for offset, field, marker, fill, label in (
                (-.12, "mae", "o", v1.BLUE, "MAE"),
                (.12, "abs_error_p95", "s", "white", "P95 |error|"),
            ):
                points = [(x, v1.number(lookup.get(f, {}).get(field))) for x, f in zip(xs, frequencies)]
                good = [(x, value) for x, value in points if value is not None]
                if good:
                    ax.scatter([x + offset for x, value in good], [value for x, value in good],
                               marker=marker, facecolor=fill, edgecolor=v1.BLUE, s=35, label=label,
                               clip_on=False, zorder=4)
                if field == "mae":
                    for x, f in zip(xs, frequencies):
                        record = lookup.get(f, {})
                        value, lower, upper = (v1.number(record.get(k)) for k in ("mae", "mae_ci_low", "mae_ci_high"))
                        if value is not None and lower is not None and upper is not None:
                            ax.vlines(x + offset, lower, upper, color=v1.BLUE, linewidth=1.1)
            for x, f in zip(xs, frequencies):
                if v1.number(lookup.get(f, {}).get("mae")) is None:
                    ax.axvspan(x - .35, x + .35, color=v1.LIGHT)
            ticklabels = []
            for f in frequencies:
                record = lookup.get(f, {})
                n = int(v1.number(record.get("n")) or 0)
                nr = int(v1.number(record.get("n_request_groups")) or 0)
                ticklabels.append(f"{f}\nn={n}\nR={nr}" if record else f"{f}\nN/S")
            ax.set_xticks(xs, ticklabels, fontsize=8)
            ax.set_ylim(bottom=0)
            ax.set_xlim(-.6, len(xs) - .4)
            ax.set_ylabel(v1.LABELS[i])
            ax.grid(axis="y", alpha=.25)
            ax.set_axisbelow(True)
            ax.set_title(("EMX - target" if j == 0 else "EMX - frozen proxy") + " | " + v1.FEATURES[i],
                         loc="left", fontsize=11)
            if i == 0 and j == 0:
                ax.legend(loc="upper left", fontsize=8, frameon=False)
    v1.header(fig, f"Physical residual MAE / P95 — {scope} — {estimand}",
              "Physical units; n = strict-valid rows, R = distinct request groups. Different supports and training budgets; no interpolated frequency curves.",
              data["source_pin"]["sha256"],
              "MAE error bars use saved95% request-cluster bootstrapCI where available; R<2: noCI, R2..9: PROVISIONAL. P95 dot is an absolute-error percentile.\n"
              "Original frame per route:20 requests /220 Q slots. Values are conditional on strict validity; failures/pending remain in the status panel.\n"
              "N/S = not in this scope; missing is not zero. Fixed observed requests only; no population accuracy or causal frequency/model ranking.")
    return fig


def build(stats, out, *, scope=v1.FORMAL, estimand="selected_q_proxy"):
    """Publish one isolated panel to a new directory, retaining any failure."""
    out = Path(out).resolve()
    v1.require(not out.exists(), "no-clobber output already exists")
    v1.require(scope in SCOPES and estimand in ESTIMANDS, "unsupported scope or estimand")
    source = v1.pin(v1.__file__)
    v1.require(source["sha256"] == V1_SHA256, "frozen v1 implementation changed")
    renderer = v1.pin(__file__)
    data = v1.load_snapshot(stats)
    out.mkdir(parents=True, exist_ok=False)
    try:
        v1.save_json(out / "INPUTS.json", dict(statistics=data["source_pin"],
            table_pins=list(data["files"].values()), renderer=renderer, reused_v1=source))
        v1.save_json(out / "FIGURE_CONTRACT.json", dict(
            schema="frequency_physical_metric_panels_contract.v2", scope=scope, estimand=estimand,
            question="What are the saved strict-valid physical-unit MAE and P95 residuals at each measured frequency?",
            takeaway="Near-zero markers remain fully visible without changing saved numerical values or the zero baseline.",
            family="faceted dot and saved interval", renderer="Matplotlib static PNG300dpi/SVG/PDF",
            palette="same frozen v1 blue/open squares/neutral unavailable bands",
            inputs="METRICS.csv from hash-verified published statistics snapshot",
            numerical_recomputation=False, geometry_or_frequency_interpolation=False,
            changed_artist_properties={"scatter.clip_on": False, "scatter.zorder": 4},
            unchanged="values, missingness, scope, counts, intervals, axis limits, titles and caveats",
            visual_qa="NOT_RUN; independent inspection of final PNG/PDF required"))
        fig = make_figure(data, scope, estimand)
        stem = ("formal" if scope == v1.FORMAL else "development15") + "_" + estimand + "_mae_p95"
        exports = v1.export(fig, out, stem)
        for record in data["files"].values():
            v1.verify(record)
        for record in (data["source_pin"], source, renderer):
            v1.verify(record)
        artifacts = [v1.pin(p) for p in sorted(out.iterdir()) if p.is_file()]
        sums = out / "SHA256SUMS"
        with sums.open("x", encoding="utf-8") as stream:
            stream.writelines(record["sha256"] + "  " + Path(record["path"]).name + "\n" for record in artifacts)
        pending = out / ".FIGURES_RECEIPT.pending.json"
        v1.save_json(pending, dict(schema="frequency_physical_metric_panels_receipt.v2", status="COMPLETE",
            created_utc=v1.utc_now(), statistics=data["source_pin"], source_metrics=data["files"]["METRICS.csv"],
            renderer=renderer, reused_v1=source, scope=scope, estimand=estimand, exports=exports,
            artifacts=artifacts, sha256sums=v1.pin(sums), source_data_modified=False,
            inference_calls=0, model_calls=0, remote_calls=0,
            visual_qa="NOT_RUN_AUTOMATIC_RENDER_ONLY"))
        os.link(pending, out / "FIGURES_RECEIPT.json")
        pending.unlink()
        return v1.pin(out / "FIGURES_RECEIPT.json")
    except Exception as error:
        failure = out / "FAILED_RENDER.json"
        if not failure.exists():
            v1.save_json(failure, dict(status="FAILED", created_utc=v1.utc_now(),
                error=type(error).__name__ + ": " + str(error), statistics=data["source_pin"],
                renderer=renderer, existing_artifacts_preserved=True))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--scope", choices=SCOPES, default=v1.FORMAL)
    parser.add_argument("--estimand", choices=ESTIMANDS, default="selected_q_proxy")
    args = parser.parse_args()
    print(json.dumps(build(args.stats, args.out, scope=args.scope, estimand=args.estimand), sort_keys=True))


if __name__ == "__main__":
    main()
