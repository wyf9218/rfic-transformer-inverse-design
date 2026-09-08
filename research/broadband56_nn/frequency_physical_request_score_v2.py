"""Standalone, presentation-only repair of two frozen request score figures.

Not imported by the incremental consumer. No model/statistics/native-stage calls.
The v1 constructor runs with an isolated globals mapping and captured export;
only the resulting figure artists are changed. Original v1 files stay intact.
"""
from __future__ import annotations

import argparse
import copy
import math
from pathlib import Path
from types import FunctionType

import numpy as np

from . import frequency_physical_statistics_figures as v1
from .io import read_json, save_json, utc_now

BASELINE_SHA = "fefc8b4ca0acad73dabbc14622bd33fb400810f3bb9359ac07f138a1677a5e19"
REQUEST_IDS = tuple(f"f{f}_qscan-HELDOUT_TRIPLE_AUDIT-000000" for f in (16, 17))


def validate_source(source, entry):
    rows, request = source["rows"], source["request"]
    v1.require(request["request_id"] == entry["request_id"], "request identity differs")
    v1.require(request["status"] == "ACCOUNTED", "only completed request sources")
    v1.require(len(rows) == 11 and sorted(r["q_target"] for r in rows) == list(range(10, 21)), "original11 slots required")
    v1.require(len({r["candidate_id"] for r in rows}) == 11, "duplicate candidate identity")
    v1.require(v1.request_signature(rows) == entry["signature"], "request signature differs")
    for row in rows:
        v1.require(all(str(row[k]) == str(request[k]) for k in
                       ("request_id", "frequency_ghz", "model_id", "dataset_scope", "q_proxy")), "source context differs")
        v1.require(row["selected_before_emx"] == (row["q_target"] == row["q_proxy"]), "preselection differs")
        v1.require(row["score_scale"] == list(v1.SCALE) and
                   np.allclose(row["absolute_tolerances"], v1.TAU, rtol=0, atol=1e-15), "score/tolerance differs")
        if not row["solved"]:
            v1.require(row["actual"] is None and row["emx_score"] is None, "unsolved numeric EMX")
    if v1.number(request.get("q_emx")) is not None:
        v1.require(all(r["strict_valid"] is True for r in rows) and request["full11_gate"] == "PASS", "full11 gate unmet")


def construct_score(source, statistics_pin):
    """Construct v1 score artists without mutating v1 globals or exporting v1."""
    captured = []

    def capture(fig, out, stem):
        v1.require(stem == "proxy_emx_score_by_q" and not captured, "score-only capture required")
        captured.append(fig)
        return []

    original = v1.request_q_plots
    isolated = dict(original.__globals__, export=capture)
    constructor = FunctionType(original.__code__, isolated, original.__name__,
                               original.__defaults__, original.__closure__)
    constructor.__kwdefaults__ = dict(original.__kwdefaults__ or {})
    frozen = copy.deepcopy(source)
    constructor({"source_pin": statistics_pin}, frozen["rows"], frozen["request"], None, score_only=True)
    v1.require(len(captured) == 1 and frozen == source, "constructor modified source or emitted extra figures")
    return captured[0]


def repair_artists(fig):
    """Separate status text from quantitative marks; preserve all line data."""
    v1.require(len(fig.axes) == 1, "expected one v1 score axis")
    ax = fig.axes[0]
    originals = [(line, np.asarray(line.get_xdata()).copy(), np.asarray(line.get_ydata()).copy()) for line in ax.lines]
    failed = [(float(text.get_position()[0]), text.get_text()) for text in ax.texts]
    for text in list(ax.texts):
        text.remove()
    ax.set_position([.08, .32, .895, .46])
    _, upper = ax.get_ylim()
    v1.require(math.isfinite(upper) and upper > 0, "nonpositive score plotting domain")
    ax.set_ylim(-.035 * upper, upper)
    ax.set_yticks([tick for tick in ax.get_yticks() if 0 <= tick <= upper])
    ax.axhline(0, color=v1.LIGHT, linewidth=.7, zorder=0)
    strip = fig.add_axes([.08, .14, .895, .075], frameon=False)
    strip.set_xlim(ax.get_xlim()); strip.set_ylim(0, 1)
    strip.set_xticks([]); strip.set_yticks([])
    for q, text in failed:
        strip.axvspan(q-.32, q+.32, color=v1.LIGHT, zorder=0)
        strip.text(q, .5, f"Q{int(q)}\n" + text, ha="center", va="center", fontsize=8,
                   transform=strip.get_xaxis_transform())
    fig.text(.08, .228, "Non-strict slots (aligned with original Q; no EMX score mark)", fontsize=9)
    fig.text(.08, .112, "Layout v2 only: blank margin below zero keeps markers intact; scores are nonnegative and are not percentages.", fontsize=8)
    # The original subtitle and provenance/score definition stay present.
    subtitle = next(text for text in fig.texts if "Original11 slots" in text.get_text())
    subtitle.set_text(subtitle.get_text() + "\nR=1 fixed request; CI NOT_ESTIMABLE; descriptive only.")
    for line, x, y in originals:
        v1.require(np.array_equal(x, np.asarray(line.get_xdata()), equal_nan=True) and
                   np.array_equal(y, np.asarray(line.get_ydata()), equal_nan=True), "line data changed")
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    # The status strip is disjoint from both score trajectories and q lines.
    for text in strip.texts:
        box = text.get_window_extent(renderer)
        v1.require(strip.bbox.contains(*box.get_points()[0]) and strip.bbox.contains(*box.get_points()[1]), "status label overflow")
    n_marks = 0
    for line, _, _ in originals:
        if line.get_marker() not in ("o", "s"):
            continue
        radius = (line.get_markersize() + line.get_markeredgewidth()) * fig.dpi / 144
        for xy in line.get_xydata():
            if not np.isfinite(xy).all():
                continue
            x, y = ax.transData.transform(xy)
            v1.require(ax.bbox.x0+radius < x < ax.bbox.x1-radius and
                       ax.bbox.y0+radius < y < ax.bbox.y1-radius, "marker clipped by axes")
            n_marks += 1
    return dict(status="ARTIST_DATA_AND_BOUNDS_PASS", N_original_slots=11,
                N_score_marks=n_marks, failure_q_slots=[int(q) for q, _ in failed],
                line_data_changed=False, score_recomputed=False,
                visual_qa="AWAITING_INDEPENDENT_VISUAL_ACCEPTANCE")


def build(contract_path, out):
    out = Path(out).resolve()
    v1.require(not out.exists(), "output exists; no-clobber")
    pins = []

    def checked(record):
        path = v1.verify(record); pins.append(record)
        return path

    contract_pin = v1.pin(contract_path)
    contract = read_json(checked(contract_pin))
    v1.require(contract["schema"] == "frequency_physical_request_score_contract.v2" and contract["no_clobber"] is True,
               "wrong chart contract")
    v1.require(tuple(contract["request_ids"]) == REQUEST_IDS, "only two explicitly scoped requests")
    baseline = v1.pin(v1.__file__)
    v1.require(baseline["sha256"] == BASELINE_SHA == contract["baseline_renderer_sha256"], "frozen v1 renderer changed")
    checked(baseline); checked(contract["prior_visual_qa"])
    receipt = read_json(checked(contract["figures_receipt"]))
    v1.require(receipt["schema"] == "frequency_physical_statistics_figures_receipt.v1" and receipt["status"] == "COMPLETE", "complete v1 figures receipt required")
    statistics = read_json(checked(receipt["statistics"]))
    v1.require(statistics["status"] == "PUBLISHED", "published statistics required")
    selections = []
    for rid in REQUEST_IDS:
        entries = [entry for entry in receipt["request_figures"] if entry["request_id"] == rid]
        v1.require(len(entries) == 1, "missing or duplicate request figure")
        entry = entries[0]; source = read_json(checked(entry["source"]))
        validate_source(source, entry)
        old_exports = [p for p in entry["exports"] if Path(p["path"]).stem == "proxy_emx_score_by_q"]
        v1.require(sorted(Path(p["path"]).suffix for p in old_exports) == [".pdf", ".png", ".svg"], "original score export trio required")
        for p in old_exports:
            checked(p)
        selections.append((entry, source, old_exports))
    out.mkdir(parents=True, exist_ok=False)
    implementation = v1.pin(__file__); checked(implementation)
    exports = []
    for entry, source, old_exports in selections:
        destination = out / entry["request_id"]; destination.mkdir()
        fig = construct_score(source, receipt["statistics"])
        structural = repair_artists(fig)
        rendered = v1.export(fig, destination, "proxy_emx_score_by_q_v2")
        exports.append(dict(request_id=entry["request_id"], signature=entry["signature"],
                            original_source=entry["source"], original_failed_exports=old_exports,
                            exports=rendered, structural_qa=structural,
                            q_proxy=source["request"]["q_proxy"], q_emx=source["request"]["q_emx"] or None))
    for p in pins:
        v1.verify(p)
    manifest = out / "MANIFEST.json"
    save_json(manifest, dict(source_pins=pins, artifacts=[p for entry in exports for p in entry["exports"]]))
    delivery = dict(schema="frequency_physical_request_score_repair_receipt.v2", status="EXPORTED_AWAITING_INDEPENDENT_VISUAL_ACCEPTANCE",
                    created_utc=utc_now(), implementation=implementation, baseline_renderer=baseline,
                    contract=contract_pin, original_figures=contract["figures_receipt"], statistics=receipt["statistics"],
                    requests=exports, manifest=v1.pin(manifest), no_clobber=True,
                    source_data_modified=False, score_recomputed=False, model_calls=0, remote_calls=0,
                    consumer_or_v1_modified=False, old_failures_preserved=True)
    receipt_path = out / "REPAIR_RECEIPT.json"; save_json(receipt_path, delivery)
    artifacts = sorted(p for p in out.rglob("*") if p.is_file())
    with (out / "SHA256SUMS").open("x", encoding="utf-8") as stream:
        for p in artifacts:
            stream.write(v1.pin(p)["sha256"] + "  " + str(p.relative_to(out)) + "\n")
    return v1.pin(receipt_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    import json
    print(json.dumps(build(args.contract, args.out), indent=2))


if __name__ == "__main__":
    main()
