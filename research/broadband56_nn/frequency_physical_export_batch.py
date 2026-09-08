"""Finite, post-terminal export orchestration. No collector or scientific work.

Default plan mode is read-only. Run calls only explicitly selected existing
renderers, never statistics.build, a model, a simulator, or a remote command.
Export completion is not visual acceptance. Existing failures are sticky.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path

PLAN_SCHEMA = "frequency_physical_export_batch_plan.v1"
RECEIPT_SCHEMA = "frequency_physical_export_batch_receipt.v1"
FORMAL = "FORMAL_10K"
DEVELOPMENT = "DEVELOPMENT_5K_NOT_FORMAL_10K"
AGGREGATES = {
    "formal-selected": (FORMAL, "selected_q_proxy"),
    "formal-all": (FORMAL, "all_candidates"),
    "development-selected": (DEVELOPMENT, "selected_q_proxy"),
    "development-all": (DEVELOPMENT, "all_candidates"),
    "selection": None,
}
STEMS = {
    "proxy_emx_score_by_q": "score", "target_percent_by_q": "percent",
    "proxy_vs_fresh_score_by_q": "score", "fresh_emx_percent_by_q": "percent",
}
MODULES = {
    "request": "frequency_physical_request_figures_v3",
    "metric": "frequency_physical_metric_panels_v2",
    "selection": "frequency_physical_selection_figures_v3",
}
AUTHOR_RECEIPTS = {"request": "REPAIR_RECEIPT.json", "metric": "FIGURES_RECEIPT.json",
                   "selection": "RENDER_RECEIPT.json"}


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def pin(path):
    path = Path(path).resolve(strict=True)
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return dict(path=str(path), sha256=h.hexdigest(), bytes=path.stat().st_size)


def verify(item):
    require(pin(item["path"]) == item, "pin changed: " + item["path"])
    return Path(item["path"])


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n"); stream.flush(); os.fsync(stream.fileno())


def checked_document(item):
    document = read(verify(item))
    verify(item)
    return document


def verify_nested_pins(value):
    if isinstance(value, dict):
        if {"path", "sha256", "bytes"} <= set(value):
            verify({k: value[k] for k in ("path", "sha256", "bytes")})
        else:
            for child in value.values(): verify_nested_pins(child)
    elif isinstance(value, list):
        for child in value: verify_nested_pins(child)


def export_map(items):
    result = {}
    for item in items:
        verify(item)
        suffix = Path(item["path"]).suffix.lstrip(".")
        require(suffix in ("png", "svg", "pdf") and suffix not in result,
                "duplicate or unsupported export format")
        result[suffix] = item
    require("png" in result, "PNG export required")
    return result


def _inputs(final_path):
    final_pin = pin(final_path); final = checked_document(final_pin)
    require(Path(final_path).name == "FINAL_RECEIPT.json", "terminal FINAL_RECEIPT required")
    n = final.get("N_accounted_requests")
    require(final.get("status") in ("COMPLETE", "PARTIAL") and type(n) is int and 0 <= n <= 320,
            "invalid terminal status/count")
    require(final.get("N_original_requests") == 320 and final.get("N_original_candidates") == 3520
            and final.get("N_pending_requests") == 320-n, "original320/3520 denominator differs")
    require(final.get("simulation_or_training_started") is False and final.get("remote_modified") is False,
            "not the reporting-only terminal contract")
    if final["status"] == "COMPLETE":
        require(n == 320 and final.get("end_reason") == "ALL320_ACCOUNTED" and final.get("error") is None,
                "false all320 completion")
    else:
        require(final.get("end_reason") in ("DEADLINE_PARTIAL", "PHYSICAL_QUEUE_ENDED_PARTIAL",
                                             "STOPPED_WITH_EVIDENCE_ERROR"), "unknown partial terminal reason")
    require(final.get("latest_snapshot") is not None, "no published terminal snapshot; nothing to export")
    snapshot = checked_document(final["latest_snapshot"])
    require(snapshot.get("status") == "PUBLISHED" and snapshot.get("remote_modified") is False,
            "snapshot not published/read-only")
    publication = checked_document(snapshot["publication"])
    require(publication.get("schema") == "frequency_physical_capture_publication.v1" and
            publication.get("status") == "PUBLISHED", "capture publication is not closed")
    verify(final["configuration"])
    stats = checked_document(snapshot["statistics"]); figures = checked_document(snapshot["figures"])
    require(stats.get("schema") == "frequency_physical_statistics_receipt.v1" and stats.get("status") == "PUBLISHED",
            "statistics not published")
    require(figures.get("schema") == "frequency_physical_statistics_figures_receipt.v1" and
            figures.get("status") == "COMPLETE" and figures.get("statistics") == snapshot["statistics"],
            "figure/statistics chain differs")
    require(stats.get("N_accounted_requests") == figures.get("N_accounted_requests") == n and
            stats.get("N_original_candidates") == figures.get("N_original_candidates") == 3520 and
            figures.get("N_original_requests") == 320, "terminal snapshot counts differ")
    manifest = checked_document(stats["manifest"]); summary = checked_document(stats["summary"])
    verify(stats["sha256sums"])
    require(manifest.get("schema") == "frequency_physical_statistics_manifest.v1", "statistics manifest schema differs")
    require(summary.get("schema") == "frequency_physical_statistics.v1" and
            summary.get("status") == ("COMPLETE_SNAPSHOT" if n == 320 else "PARTIAL_SNAPSHOT") and
            summary.get("N_planned_requests") == 320 and summary.get("N_accounted_requests") == n,
            "statistics summary differs")
    files = {}
    for item in manifest["artifacts"]:
        verify(item); name = Path(item["path"]).name
        require(name not in files, "duplicate statistics artifact")
        files[name] = item
    require(files.get("SUMMARY.json") == stats["summary"], "summary not in statistics manifest")
    indexed = {}
    for line in verify(stats["sha256sums"]).read_text().splitlines():
        checksum, name = line.split("  ", 1)
        require(Path(name).name == name and name not in indexed, "unsafe/duplicate statistics checksum name")
        indexed[name] = checksum
    require(indexed == {**{k: p["sha256"] for k, p in files.items()},
                        "MANIFEST.json": stats["manifest"]["sha256"]}, "statistics checksum index differs")
    with verify(files["REQUEST_STATUS.csv"]).open(newline="") as stream:
        requests = list(csv.DictReader(stream))
    require(len(requests) == 320 and len({r["request_id"] for r in requests}) == 320,
            "original320 request identities differ")
    accounted = {r["request_id"]: r for r in requests if r["status"] == "ACCOUNTED"}
    entries = figures["request_figures"]
    require(len(entries) == len(accounted) == n and
            {e["request_id"] for e in entries} == set(accounted), "accounted request figure set differs")
    sources = [final_pin, final["configuration"], final["latest_snapshot"], snapshot["publication"], snapshot["statistics"], snapshot["figures"],
               stats["manifest"], stats["summary"], stats["sha256sums"], *files.values()]
    originals = []
    for entry in entries:
        source = entry.get("source")
        require(isinstance(entry.get("signature"), str) and len(entry["signature"]) == 64,
                "missing request signature")
        if source:
            data = checked_document(source); sources.append(source)
            require(data["request"] == accounted[entry["request_id"]] and len(data["rows"]) == 11 and
                    sorted(r["q_target"] for r in data["rows"]) == list(range(10, 21)),
                    "original request metadata/eleven slots differ")
        else:
            require(entry["request_id"] == "qscan15_development5k_20260908_v1-HELDOUT_TRIPLE_AUDIT-000000"
                    and entry.get("reused_from") and entry.get("visual_qa"), "only explicit legacy first15 lacks source rows")
            for key in ("reused_from", "visual_qa"):
                verify(entry[key]); sources.append(entry[key])
        groups = {}
        for item in entry["exports"]:
            stem = Path(item["path"]).stem
            require(stem in STEMS, "unknown original request figure stem")
            groups.setdefault(STEMS[stem], []).append(item)
        require(set(groups) == {"score", "percent"}, "two original request kinds required")
        for kind, exports in groups.items():
            exports = export_map(exports)
            sources.extend(exports.values())
            originals.append(dict(request_id=entry["request_id"], kind=kind, signature=entry["signature"],
                                  source=source, request_metadata=accounted[entry["request_id"]],
                                  exports=exports, status="ORIGINAL_UNREVIEWED"))
    aggregates = []
    for item in figures["aggregate_exports"]:
        verify(item); sources.append(item)
        aggregates.append(dict(export=item, status="ORIGINAL_UNREVIEWED"))
    return final, snapshot, files, originals, aggregates, sources


def _approved(paths, originals, sources):
    lookup = {(r["request_id"], r["kind"]): r for r in originals}; result = {}
    for path in paths:
        index_pin = pin(path); index = checked_document(index_pin)
        require(index.get("status") == "GO" and isinstance(index.get("exports"), list), "approved index must declare GO exports")
        qa_pin = index["visual_go"]; qa = checked_document(qa_pin)
        sources.extend((index_pin, qa_pin))
        for item in index["exports"]:
            key = (item["request_id"], item["kind"])
            require(key in lookup and key not in result, "unknown/duplicate approved request kind")
            original = lookup[key]
            require(item.get("source") == original["source"] and item["signature"] == original["signature"],
                    "approved request/source signature differs")
            exports = {k: item[k] for k in ("png", "svg", "pdf") if k in item}
            export_map(list(exports.values()))
            matches = [p for p in qa.get("figures", []) if p.get("request_id") == key[0] and p.get("kind") == key[1]
                       and p.get("status") in ("GO", "PASS") and p.get("source") == original["source"]
                       and p.get("signature") == original["signature"]]
            require(len(matches) == 1, "QA lacks exact approved figure")
            qa_exports = matches[0].get("exports", {k: matches[0][k] for k in ("png", "svg", "pdf") if k in matches[0]})
            require(exports == qa_exports, "approved exports not bound to exact QA")
            sources.extend(exports.values())
            result[key] = dict(original, status="REUSED_APPROVED", exports=exports,
                               original_exports=original["exports"], approved_index=index_pin, visual_qa=qa_pin)
    return result


def plan(final_path, out, *, request_contract=None, approved_indexes=(), aggregates=()):
    """Read-only finite plan. No directories, lock files, imports of plotters or writes."""
    out = Path(out).resolve()
    final, snapshot, files, originals, aggregate_originals, sources = _inputs(final_path)
    approved = _approved(approved_indexes, originals, sources)
    original_by_key = {(r["request_id"], r["kind"]): r for r in originals}
    jobs = []; contract_pin = None
    if request_contract:
        contract_pin = pin(request_contract); contract = checked_document(contract_pin); sources.append(contract_pin)
        require(contract.get("schema") == "frequency_physical_request_figure_contract.v3" and
                contract.get("no_clobber") is True and contract.get("figures_receipt") == snapshot["figures"],
                "request contract must bind terminal published figures")
        verify(contract["original_visual_qa"]); sources.append(contract["original_visual_qa"])
        helpers = Path(__file__).resolve().parent
        require(contract.get("v1_renderer_sha256") == pin(helpers / "frequency_physical_statistics_figures.py")["sha256"]
                and contract.get("v2_helper_sha256") == pin(helpers / "frequency_physical_request_score_v2.py")["sha256"],
                "request contract helper source locks differ")
        seen = set()
        for item in contract["jobs"]:
            require(set(item) == {"request_id", "kind"}, "wrong request job fields")
            key = (item["request_id"], item["kind"])
            require(key in original_by_key and key not in seen, "unknown/duplicate requested job")
            seen.add(key)
            if key in approved:
                continue
            original = original_by_key[key]
            require(original["source"] is not None, "reuse legacy first15; do not render it again")
            if item["kind"] == "percent":
                require(any(r["stage"] == "GDS_FAIL" for r in checked_document(original["source"])["rows"]),
                        "percent repair needs existing GDS/Cadence failure label")
            jobs.append(dict(type="request", **item, original=original, contract=contract_pin))
    require(len(aggregates) == len(set(aggregates)) and all(a in AGGREGATES for a in aggregates),
            "duplicate/unknown aggregate selection")
    for name in aggregates:
        values = AGGREGATES[name]
        jobs.append(dict(type="selection", aggregate=name) if values is None else
                    dict(type="metric", aggregate=name, scope=values[0], estimand=values[1]))
    implementation_dir = Path(__file__).resolve().parent
    required = {"frequency_physical_export_batch.py", "frequency_physical_statistics_figures.py", "io.py", "__init__.py"}
    for job in jobs:
        required.add(MODULES[job["type"]] + ".py")
        if job["type"] == "request": required.add("frequency_physical_request_score_v2.py")
        if job["type"] == "selection": required.add("frequency_physical_selection_figures_v2.py")
    implementation = [pin(implementation_dir / name) for name in sorted(required)]
    sources += implementation
    for job in jobs:
        job["id"] = job["type"] + "-" + digest(job)[:20]
    require(all(not Path(p["path"]).is_relative_to(out) for p in sources), "output contains an input; choose independent output")
    return dict(schema=PLAN_SCHEMA, output=str(out), final=pin(final_path), snapshot=final["latest_snapshot"],
                statistics=snapshot["statistics"], figures=snapshot["figures"],
                physical_terminal_status=final["status"], physical_end_reason=final["end_reason"],
                N_accounted_requests=final["N_accounted_requests"], N_original_requests=320, N_original_candidates=3520,
                physical_frame_complete=final["status"] == "COMPLETE", implementation=implementation,
                source_pins=list({digest(p): p for p in sources}.values()), jobs=jobs,
                request_exports=[approved.get((r["request_id"], r["kind"]), r) for r in originals],
                original_aggregate_exports=aggregate_originals,
                final_visual_qa="NOT_GRANTED_BY_BATCH", model_calls=0, remote_calls=0, statistics_build_calls=0)


def _execute(job, plan_value, destination):
    module = importlib.import_module("." + MODULES[job["type"]], __package__)
    if job["type"] == "request":
        module.build(job["contract"]["path"], job["request_id"], job["kind"], destination)
    elif job["type"] == "metric":
        module.build(Path(plan_value["statistics"]["path"]).parent, destination,
                     scope=job["scope"], estimand=job["estimand"])
    else:
        module.build(Path(plan_value["statistics"]["path"]).parent, destination)


def _author_result(job, plan_value, destination):
    require(not any((destination / n).exists() for n in
                    ("FAILURE_RECEIPT.json", "FAILED_RENDER.json", "FAILURE.json")), "author failure retained; no retry")
    receipt_pin = pin(destination / AUTHOR_RECEIPTS[job["type"]]); receipt = checked_document(receipt_pin)
    verify_nested_pins(receipt)
    expected = {
        "request": ("frequency_physical_request_figure_repair_receipt.v3", "EXPORTED_AWAITING_INDEPENDENT_VISUAL_ACCEPTANCE"),
        "metric": ("frequency_physical_metric_panels_receipt.v2", "COMPLETE"),
        "selection": ("frequency_selection_figures_v3_receipt.v1", "RENDERED_INDEPENDENT_VISUAL_QA_REQUIRED"),
    }[job["type"]]
    require((receipt.get("schema"), receipt.get("status")) == expected and
            receipt.get("statistics") == plan_value["statistics"], "author receipt schema/status/statistics differs")
    renderer_key = {"request": "implementation", "metric": "renderer", "selection": "source"}[job["type"]]
    expected_renderer = next(p for p in plan_value["implementation"] if Path(p["path"]).stem == MODULES[job["type"]])
    require(receipt.get(renderer_key) == expected_renderer, "author renderer identity differs")
    if job["type"] == "request":
        require(all(receipt.get(k) == job[k] for k in ("request_id", "kind", "contract")) and
                all(receipt.get(k) == job["original"][k] for k in ("source", "signature")), "author request identity differs")
        require(receipt.get("source_or_numerical_values_modified") is False and
                receipt.get("consumer_or_existing_source_modified") is False and
                receipt.get("model_calls") == 0 and receipt.get("remote_calls") == 0,
                "request author changed science/runtime")
    elif job["type"] == "metric":
        require(receipt.get("scope") == job["scope"] and receipt.get("estimand") == job["estimand"], "author metric route differs")
        require(receipt.get("source_data_modified") is False and receipt.get("model_calls") == 0 and
                receipt.get("remote_calls") == 0, "metric author changed science/runtime")
    else:
        require(receipt.get("metrics_recomputed") is False and receipt.get("model_or_simulator_calls") == 0,
                "selection author changed science/runtime")
    exports = receipt["exports"]
    require(isinstance(exports, list), "author exports list required")
    groups = {}
    for item in exports:
        path = verify(item)
        require(path.is_relative_to(destination.resolve()), "author export outside job directory")
        groups.setdefault(path.stem, []).append(item)
    for group in groups.values():
        require(set(export_map(group)) == {"png", "svg", "pdf"}, "complete export trio required")
    require(len(groups) == 1 if job["type"] != "selection" else len(groups) <= 2,
            "wrong author figure count")
    return dict(author_receipt=receipt_pin, exports=exports,
                status="EXPORTED_AWAITING_INDEPENDENT_VISUAL_ACCEPTANCE")


def run(plan_value, *, executor=None):
    """Run finitely or adopt exact completed receipts. Unknown partials never rerun."""
    require(plan_value.get("schema") == PLAN_SCHEMA, "wrong batch plan")
    for item in plan_value["source_pins"]: verify(item)
    out = Path(plan_value["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    lock = out.parent / ("." + out.name + ".export.lock")
    require(not lock.is_symlink(), "lock symlink not allowed")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc: raise ValueError("batch already locked; no duplicate") from exc
        plan_path = out / "PLAN.json"
        if out.exists():
            require(plan_path.is_file() and read(plan_path) == plan_value, "existing output has no exact plan; preserve")
        else:
            out.mkdir(); write(plan_path, plan_value)
        require(not (out / "FAILURE_RECEIPT.json").exists(), "batch failure retained; no retry")
        if (out / "BATCH_RECEIPT.json").exists():
            terminal = read(out / "BATCH_RECEIPT.json")
            require(terminal.get("schema") == RECEIPT_SCHEMA and
                    terminal.get("status") == "COMPLETE_FINITE_EXPORT_BATCH" and terminal.get("plan") == pin(plan_path),
                    "existing batch terminal identity differs")
            require([p["path"] for p in terminal["jobs"]] ==
                    [str(out / j["id"] / "JOB_RECEIPT.json") for j in plan_value["jobs"]],
                    "existing terminal job set differs")
            verify_nested_pins(terminal)
        if (out / "EXPORT_INDEX.json").exists():
            # An index is written only after all jobs closed. Missing referenced
            # artifacts must fail before a deleted job directory can look fresh.
            verify_nested_pins(read(out / "EXPORT_INDEX.json"))
        fingerprint = digest(plan_value); results = []
        for job in plan_value["jobs"]:
            folder = out / job["id"]; destination = folder / "render"
            intent = dict(plan_sha256=fingerprint, job=job)
            fresh = not folder.exists()
            if fresh:
                folder.mkdir(); write(folder / "INTENT.json", intent)
            else:
                require((folder / "INTENT.json").is_file() and read(folder / "INTENT.json") == intent,
                        "partial job has no exact intent; preserve")
            require(not (folder / "FAILURE.json").exists(), "job failure retained; no retry")
            try:
                saved = folder / "JOB_RECEIPT.json"
                if not saved.exists() and fresh:
                    (executor or _execute)(job, plan_value, destination)
                # A preexisting intent without a qualified author receipt is ambiguous.
                # _author_result fails instead of re-invoking the exporter.
                result = _author_result(job, plan_value, destination)
                journal = dict(schema="frequency_export_batch_job.v1", **intent, **result)
                # Job completion means author exports exist, never visual GO.
                journal["status"] = "COMPLETE_AUTHOR_EXPORTS_NOT_VISUAL_GO"
                if saved.exists(): require(read(saved) == journal, "completed job receipt changed")
                else: write(saved, journal)
                results.append(dict(job=job, receipt=pin(saved), **result))
            except Exception as exc:
                if not (folder / "FAILURE.json").exists():
                    write(folder / "FAILURE.json", dict(status="FAIL_PRESERVED_NO_RETRY", **intent,
                          error=type(exc).__name__ + ": " + str(exc)))
                raise
        for item in plan_value["source_pins"]: verify(item)
        request_exports = {(r["request_id"], r["kind"]): r for r in plan_value["request_exports"]}
        aggregate_exports = list(plan_value["original_aggregate_exports"])
        for result in results:
            job = result["job"]
            if job["type"] == "request":
                key = (job["request_id"], job["kind"])
                request_exports[key] = dict(job["original"], status=result["status"],
                    exports=export_map(result["exports"]), original_exports=job["original"]["exports"],
                    author_receipt=result["author_receipt"], job_receipt=result["receipt"])
            else:
                aggregate_exports.append(dict(aggregate=job["aggregate"], exports=result["exports"],
                    status=result["status"], author_receipt=result["author_receipt"], job_receipt=result["receipt"]))
        index = dict(schema="frequency_physical_export_index.v1", plan=pin(plan_path),
                     physical_terminal_status=plan_value["physical_terminal_status"],
                     physical_frame_complete=plan_value["physical_frame_complete"],
                     N_accounted_requests=plan_value["N_accounted_requests"], N_original_requests=320,
                     N_original_candidates=3520, request_exports=list(request_exports.values()),
                     aggregate_exports=aggregate_exports, grants_visual_GO=False)
        index_path = out / "EXPORT_INDEX.json"
        if index_path.exists(): require(read(index_path) == index, "partial final index differs; preserve")
        else: write(index_path, index)
        receipt = dict(schema=RECEIPT_SCHEMA, status="COMPLETE_FINITE_EXPORT_BATCH", plan=pin(plan_path),
                       export_index=pin(index_path), jobs=[r["receipt"] for r in results],
                       physical_terminal_status=plan_value["physical_terminal_status"],
                       physical_frame_complete=plan_value["physical_frame_complete"],
                       no_visual_GO_granted=True, remote_calls=0, model_calls=0, statistics_build_calls=0)
        final_path = out / "BATCH_RECEIPT.json"
        if final_path.exists(): require(read(final_path) == receipt, "batch receipt changed")
        else: write(final_path, receipt)
        return pin(final_path)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--final", required=True); parser.add_argument("--out", required=True)
    parser.add_argument("--mode", choices=("plan", "run"), default="plan")
    parser.add_argument("--request-contract")
    parser.add_argument("--approved-export-index", action="append", default=[])
    parser.add_argument("--aggregate", action="append", choices=tuple(AGGREGATES), default=[])
    args = parser.parse_args()
    value = plan(args.final, args.out, request_contract=args.request_contract,
                 approved_indexes=args.approved_export_index, aggregates=args.aggregate)
    print(json.dumps(value if args.mode == "plan" else run(value), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
