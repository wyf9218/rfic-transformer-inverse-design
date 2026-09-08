"""Explicit standalone score/percent layout repair from published request pins.

No consumer integration or automatic dispatch. A frozen contract authorizes each
request/kind. Original line/bar values and artifacts are never replaced.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from types import FunctionType

from . import frequency_physical_request_score_v2 as v2
from .io import read_json, save_json, utc_now

v1 = v2.v1
V2_SHA = "d1022dd4b056312ad831e50a9b17d22ed38708637ebb51edf16e11e6d7340d15"
STEMS = {"score": "proxy_emx_score_by_q", "percent": "target_percent_by_q"}


def authorized(contract, request_id, kind):
    v1.require(contract["schema"] == "frequency_physical_request_figure_contract.v3" and
               contract["no_clobber"] is True, "wrong no-clobber chart contract")
    v1.require(kind in STEMS, "kind must be score or percent")
    matches = [job for job in contract["jobs"] if job == dict(request_id=request_id, kind=kind)]
    v1.require(len(matches) == 1, "request/kind not uniquely authorized by frozen contract")


def construct_percent(source, statistics_pin):
    """Capture only percent artists; the unwanted score figure is never drawn/exported."""
    captured = []

    def capture(fig, out, stem):
        if stem == STEMS["percent"]:
            v1.require(not captured, "duplicate percent figure")
            captured.append(fig)
        else:
            v1.require(stem == STEMS["score"], "unexpected constructor output")
            v1.plot_style().close(fig)
        return []

    original = v1.request_q_plots
    constructor = FunctionType(original.__code__, dict(original.__globals__, export=capture),
                               original.__name__, original.__defaults__, original.__closure__)
    constructor.__kwdefaults__ = dict(original.__kwdefaults__ or {})
    frozen = copy.deepcopy(source)
    constructor({"source_pin": statistics_pin}, frozen["rows"], frozen["request"], None, score_only=False)
    v1.require(len(captured) == 1 and frozen == source, "source mutation or wrong figure count")
    return captured[0]


def repair_percent(fig):
    """Wrap only the existing GDS/Cadence failure text at its original position."""
    v1.require(len(fig.axes) == 4, "four feature panels required")
    patches = [(p, p.get_path().vertices.copy(), p.get_transform().get_matrix().copy())
               for ax in fig.axes for p in ax.patches]
    original_text = [(t, t.get_text(), t.get_position()) for ax in fig.axes for t in ax.texts]
    changed = []
    for ax in fig.axes:
        for text in ax.texts:
            if text.get_text() == "GDS/Cadence\nfail":
                text.set_text("GDS/\nCadence\nfail")
                changed.append((ax, text))
    v1.require(changed, "no matching GDS/Cadence label to repair")
    fig.canvas.draw(); renderer = fig.canvas.get_renderer()
    for ax, text in changed:
        box = text.get_window_extent(renderer)
        q = text.get_position()[0]
        left = ax.transData.transform((q-.48, 0))[0]
        right = ax.transData.transform((q+.48, 0))[0]
        v1.require(left < box.x0 and box.x1 < right and ax.bbox.y0 < box.y0 < box.y1 < ax.bbox.y1,
                   "wrapped failure label outside own Q column or panel")
    import numpy as np
    for patch, vertices, matrix in patches:
        v1.require(np.array_equal(vertices, patch.get_path().vertices) and
                   np.array_equal(matrix, patch.get_transform().get_matrix()), "bar/region geometry changed")
    for text, content, position in original_text:
        expected = "GDS/\nCadence\nfail" if content == "GDS/Cadence\nfail" else content
        v1.require(text.get_text() == expected and text.get_position() == position, "unrelated text changed")
    return dict(status="TEXT_AND_BAR_GEOMETRY_PASS", N_wrapped_failure_labels=len(changed),
                positions_changed=False, bar_geometry_changed=False, numerical_annotations_changed=False,
                percentage_recomputed=False, visual_qa="AWAITING_INDEPENDENT_VISUAL_ACCEPTANCE")


def build(contract_path, request_id, kind, out):
    out = Path(out).resolve()
    v1.require(not out.exists(), "output exists; no-clobber")
    pins = []

    def checked(record):
        path = v1.verify(record); pins.append(record)
        return path

    contract_pin = v1.pin(contract_path); contract = read_json(checked(contract_pin))
    authorized(contract, request_id, kind)
    baseline, helper = v1.pin(v1.__file__), v1.pin(v2.__file__)
    v1.require(baseline["sha256"] == v2.BASELINE_SHA == contract["v1_renderer_sha256"], "v1 source changed")
    v1.require(helper["sha256"] == V2_SHA == contract["v2_helper_sha256"], "v2 helper changed")
    checked(baseline); checked(helper); checked(contract["original_visual_qa"])
    original_receipt = read_json(checked(contract["figures_receipt"]))
    v1.require(original_receipt["schema"] == "frequency_physical_statistics_figures_receipt.v1" and
               original_receipt["status"] == "COMPLETE", "complete published figures required")
    statistics = read_json(checked(original_receipt["statistics"]))
    v1.require(statistics["status"] == "PUBLISHED", "published statistics required")
    entries = [e for e in original_receipt["request_figures"] if e["request_id"] == request_id]
    v1.require(len(entries) == 1, "missing or duplicate request")
    entry = entries[0]; source = read_json(checked(entry["source"]))
    v2.validate_source(source, entry)
    if source["request"]["full11_gate"] == "PASS":
        v1.require(v1.number(source["request"]["q_emx"]) is not None, "full11 pass without q_emx")
    original_pins = {tuple(sorted(r["source_pins"]["original_records"].items())) for r in source["rows"]}
    v1.require(len(original_pins) == 1, "original eleven source differs")
    original_records = [json.loads(line) for line in
                        checked(dict(next(iter(original_pins)))).read_text().splitlines()]
    by_id = {row["candidate_id"]: row for row in original_records}
    v1.require(len(original_records) == len(by_id) == 11, "original eleven identity differs")
    for row in source["rows"]:
        frozen = by_id[row["candidate_id"]]
        v1.require(all(row[k] == frozen[k] for k in ("candidate_id", "request_id", "model_id", "dataset_scope", "frequency_ghz", "q_target", "q_proxy", "target")),
                   "original candidate context changed")
        v1.require(row["proxy"] == frozen["grid_proxy"] and row["proxy_score"] == frozen["grid_proxy_score"] and
                   row["selected_before_emx"] == frozen["proxy_preselected"], "original proxy/preselection changed")
    original_exports = [p for p in entry["exports"] if Path(p["path"]).stem == STEMS[kind]]
    v1.require(sorted(Path(p["path"]).suffix for p in original_exports) == [".pdf", ".png", ".svg"], "original export trio required")
    for p in original_exports: checked(p)
    implementation = v1.pin(__file__); checked(implementation)
    out.mkdir(parents=True, exist_ok=False)
    try:
        if kind == "score":
            fig = v2.construct_score(source, original_receipt["statistics"])
            structural = v2.repair_artists(fig)
        else:
            fig = construct_percent(source, original_receipt["statistics"])
            structural = repair_percent(fig)
        exports = v1.export(fig, out, STEMS[kind] + "_v3")
        for p in pins: v1.verify(p)
        save_json(out/"MANIFEST.json", dict(source_pins=pins, artifacts=exports))
        result = dict(schema="frequency_physical_request_figure_repair_receipt.v3",
            status="EXPORTED_AWAITING_INDEPENDENT_VISUAL_ACCEPTANCE", created_utc=utc_now(),
            request_id=request_id, kind=kind, source=entry["source"], signature=entry["signature"],
            q_proxy=source["request"]["q_proxy"], q_emx=source["request"]["q_emx"] or None,
            implementation=implementation, v1_renderer=baseline, v2_helper=helper, contract=contract_pin,
            statistics=original_receipt["statistics"], original_exports=original_exports, exports=exports,
            structural_qa=structural, manifest=v1.pin(out/"MANIFEST.json"), no_clobber=True,
            source_or_numerical_values_modified=False, old_failures_preserved=True,
            consumer_or_existing_source_modified=False, model_calls=0, remote_calls=0)
        save_json(out/"REPAIR_RECEIPT.json", result)
        with (out/"SHA256SUMS").open("x", encoding="utf-8") as stream:
            for p in sorted(p for p in out.iterdir() if p.is_file() and p.name != "SHA256SUMS"):
                stream.write(v1.pin(p)["sha256"] + "  " + p.name + "\n")
        return v1.pin(out/"REPAIR_RECEIPT.json")
    except Exception as exc:
        save_json(out/"FAILURE_RECEIPT.json", dict(status="FAIL_PRESERVED", error_type=type(exc).__name__,
                  error=str(exc), created_utc=utc_now(), contract=contract_pin, request_id=request_id, kind=kind))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--kind", choices=tuple(STEMS), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    import json
    print(json.dumps(build(args.contract, args.request_id, args.kind, args.out), indent=2))


if __name__ == "__main__":
    main()
