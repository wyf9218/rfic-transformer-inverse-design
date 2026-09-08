"""Compact uncertainty labels for standalone complete11 plots; no data changes.

The frozen v2 artists, numerical values, denominators and axes are reused.
Only tick text and its decoding key change. Not installed in the live consumer.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import frequency_physical_selection_figures_v2 as v2
from .io import save_json, utc_now

base = v2.base
FROZEN_V2_SHA = "5e5da74e5bdb2e22dde310f3c72e510c26714a75773cf97416eccdbec940ce4e"
FROZEN_BASE_SHA = "fefc8b4ca0acad73dabbc14622bd33fb400810f3bb9359ac07f138a1677a5e19"
CI_CODES = {"NOT_ESTIMABLE": "N/E", "PROVISIONAL_SMALL_REQUEST_N": "P",
            "DESCRIPTIVE_REQUEST_BOOTSTRAP": "D"}
CI_KEY = ("CI N/E: not estimable (R<2); P: provisional (R2–9); "
          "D: descriptive (R≥10). Exact intervals remain in source CSV.")
CONTRACT = {**v2.CONTRACT,
            "uncertainty": "Compact exact CI status codes plus explicit decoding key; same source intervals.",
            "layout_change": "Tick text only; original scatter offsets, axes and data unchanged.",
            "ci_codes": CI_CODES}


def ci_code(count, status):
    n = base.number(count)
    base.require(n is not None and n >= 1 and n == int(n), "positive integer request count required")
    expected = ("NOT_ESTIMABLE" if n < 2 else "PROVISIONAL_SMALL_REQUEST_N"
                if n < 10 else "DESCRIPTIVE_REQUEST_BOOTSTRAP")
    base.require(status == expected, "CI status disagrees with request count")
    return CI_CODES[status]


def make_figure(data, scope):
    rows, frequencies = v2.common_rows(data, scope)
    if not rows:
        return None
    groups = {f: next(r for r in rows if int(r["frequency_ghz"]) == f) for f in frequencies}
    labels = []
    for f in frequencies:
        group = groups[f]
        n = group["N_common_complete_requests"]
        code = ci_code(n, group["ci_status"])
        separator = " | " if len(frequencies) <= 4 else "\n"
        labels.append(f"{f} GHz\nR={int(float(n))}{separator}CI {code}")
    fig = v2.make_figure(data, scope)
    for ax in fig.axes:
        ax.set_xticks(range(len(frequencies)), labels, fontsize=10 if len(frequencies) <= 4 else 8)
    fig.text(.075, .105, CI_KEY, fontsize=8.5, ha="left")
    return fig


def build(stats, out):
    v2_pin, base_pin = base.pin(v2.__file__), base.pin(base.__file__)
    base.require(v2_pin["sha256"] == FROZEN_V2_SHA, "v2 helper identity differs")
    base.require(base_pin["sha256"] == FROZEN_BASE_SHA, "base helper identity differs")
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
                stem = ("formal" if scope == base.FORMAL else "development15") + "_complete11_selection_v3"
                exports.extend(base.export(fig, out, stem))
        receipt = {
            "schema": "frequency_selection_figures_v3_receipt.v1", "created_utc": utc_now(),
            "status": "RENDERED_INDEPENDENT_VISUAL_QA_REQUIRED",
            "statistics": data["source_pin"], "selection_csv": data["files"]["SELECTION_COMPARISON.csv"],
            "source": base.pin(__file__), "unchanged_v2_helper": v2_pin,
            "unchanged_base_helper": base_pin, "contract": base.pin(out / "CHART_CONTRACT.json"),
            "exports": exports, "ci_codes": CI_CODES, "metrics_recomputed": False,
            "model_or_simulator_calls": 0, "runtime_integration": "NOT_INSTALLED_STANDALONE_ONLY"}
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
