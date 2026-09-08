"""Standalone v2 selection-figure correction; never loaded by the running consumer.

Reuse the published statistics reader/style/export primitives. Only glyph
clipping and explicit request-level uncertainty labels differ from the v1 plot.
No inference, solver invocation, numerical metric recomputation or hot replacement.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import frequency_physical_statistics_figures as base
from .io import save_json, utc_now

CONTRACT = {
    "question": "Compare three selection strategies on exactly the same complete11 requests.",
    "takeaway": "Descriptive same-request errors, not a population or individual-feature champion.",
    "family": "Comparison", "variant": "four small-multiple dot plots",
    "surface": "standalone scientific PNG/SVG/PDF",
    "palette_policy": "hard two-root cap; original blue/orange plus neutral",
    "noncolor_encoding": "open circle / filled circle / filled square",
    "zero_axis": "MAE axis starts at zero; marker glyphs are not clipped at that boundary",
    "uncertainty": "Explicit per-frequency R and source CI status; R1 is NOT_ESTIMABLE",
    "sample_policy": "Only original11 strict-valid common requests; never acquire extra data for aesthetics",
    "qa_surface": "actual exported PNG and rasterized PDF",
    "runtime_integration": "NOT_INSTALLED; standalone only; running v1 untouched",
}
STRATEGIES = (
    ("fixed_q15", -.2, base.BLUE, "o", "white"),
    ("q_proxy", 0, base.BLUE, "o", base.BLUE),
    ("q_emx", .2, base.ORANGE, "s", base.ORANGE),
)


def common_rows(data, scope):
    rows = [r for r in data["selection"] if r["dataset_scope"] == scope
            and r["status"] == "AVAILABLE_DESCRIPTIVE"
            and base.number(r.get("N_common_complete_requests"))]
    frequencies = sorted({int(r["frequency_ghz"]) for r in rows})
    for frequency in frequencies:
        group = [r for r in rows if int(r["frequency_ghz"]) == frequency]
        keys = [(r["strategy"], r["feature"]) for r in group]
        expected = {(s[0], f) for s in STRATEGIES for f in base.FEATURES}
        base.require(len(keys) == len(set(keys)) and set(keys) == expected,
                     "exact three-strategy four-feature rows required")
        for field in ("model_id", "target_source", "label_mode", "protocol_sha256",
                      "N_common_complete_requests", "n_request_groups", "ci_status"):
            base.require(len({str(r[field]) for r in group}) == 1, "common context differs: " + field)
        n = base.number(group[0]["N_common_complete_requests"])
        base.require(n is not None and n >= 1 and n == int(n), "positive integer common request count required")
        base.require(base.number(group[0]["n_request_groups"]) == n, "request cluster denominator differs")
        if n == 1:
            base.require(group[0]["ci_status"] == "NOT_ESTIMABLE", "R1 CI must be NOT_ESTIMABLE")
        for row in group:
            value = base.number(row["mae"])
            base.require(value is not None and value >= 0, "finite nonnegative MAE required")
    return rows, frequencies


def make_figure(data, scope):
    rows, frequencies = common_rows(data, scope)
    if not rows:
        return None
    plt = base.plot_style()
    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    fig.subplots_adjust(left=.075, right=.975, top=.79, bottom=.16, wspace=.2, hspace=.48)
    groups = {f: next(r for r in rows if int(r["frequency_ghz"]) == f) for f in frequencies}
    for i, ax in enumerate(axes.flat):
        for strategy, dx, color, marker, face in STRATEGIES:
            values = {int(r["frequency_ghz"]): r for r in rows
                      if r["strategy"] == strategy and r["feature"] == base.FEATURES[i]}
            for x, frequency in enumerate(frequencies):
                ax.scatter(x + dx, base.number(values[frequency]["mae"]),
                           color=color, marker=marker, facecolors=face, s=48,
                           label=strategy if x == 0 else None, clip_on=False, zorder=4)
        ax.set_xticks(range(len(frequencies)), [
            f"{f} GHz | R={int(float(groups[f]['N_common_complete_requests']))}"
            + "\nCI: " + groups[f]["ci_status"] for f in frequencies])
        ax.set_xlim(-.65, len(frequencies) - .35)
        ax.set_ylim(bottom=0)
        lo, hi = ax.get_ylim()
        ax.set_ylim(0, max(hi, 1e-12) * 1.08)
        ax.set_ylabel("MAE " + base.LABELS[i])
        ax.set_title(base.FEATURES[i], loc="left")
        ax.grid(axis="y", alpha=.2)
        ax.set_axisbelow(True)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(.065, .875),
               ncol=3, frameon=False)
    base.header(fig, "Complete11 selection comparison — " + scope,
                "Common strict-valid original11 requests only. R=1: CI NOT_ESTIMABLE; single-request MAE is absolute error.",
                data["source_pin"]["sha256"],
                "q_emx is the retrospective fixed-span score minimum, not necessarily the best individual feature.\n"
                "No survivor-only optimum, cross-scope pooling or population claim. Exact values and CI statuses remain in source CSV.")
    return fig


def build(stats, out):
    data = base.load_snapshot(stats)
    out = Path(out).resolve()
    base.require(not out.exists(), "no-clobber output already exists")
    out.mkdir(parents=True)
    save_json(out / "CHART_CONTRACT.json", CONTRACT)
    try:
        exports = []
        for scope in (base.FORMAL, base.DEVELOPMENT):
            fig = make_figure(data, scope)
            if fig is not None:
                name = ("formal" if scope == base.FORMAL else "development15") + "_complete11_selection_v2"
                exports.extend(base.export(fig, out, name))
        receipt = {
            "schema": "frequency_selection_figures_v2_receipt.v1",
            "created_utc": utc_now(), "status": "RENDERED_MANUAL_VISUAL_QA_REQUIRED",
            "statistics": data["source_pin"], "selection_csv": data["files"]["SELECTION_COMPARISON.csv"],
            "source": base.pin(__file__), "unchanged_v1_helpers": base.pin(base.__file__),
            "contract": base.pin(out / "CHART_CONTRACT.json"), "exports": exports,
            "runtime_integration": "NOT_INSTALLED_STANDALONE_ONLY",
            "metrics_recomputed": False, "model_or_simulator_calls": 0,
        }
        save_json(out / "RENDER_RECEIPT.json", receipt)
        return receipt
    except Exception as exc:
        save_json(out / "FAILURE.json", {"created_utc": utc_now(), "status": "FAIL_PRESERVED",
                                       "error": type(exc).__name__ + ": " + str(exc)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    receipt = build(args.stats, args.out)
    print(receipt["status"], len(receipt["exports"]))


if __name__ == "__main__":
    main()
