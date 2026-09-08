"""Static, source-pinned Q-scan figures; no inference, training or EMX.

One record per request is the denominator. Candidate failures remain visible.
Only completed SELF_PROXY scans with no fresh EMX are accepted by this renderer.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import shutil

import numpy as np

from .io import read_json, save_json, sha256, utc_now

FEATURES = ("Lp_nH", "Ls_nH", "Qmin", "K_abs")
LABELS = ("Lp [nH]", "Ls [nH]", "Qmin [dimensionless]", "|K| [dimensionless]")
SCALE = np.asarray([2.5, 2.5, 20., .8])
TAU = .05 * SCALE
SOURCES = ("HELDOUT_TRIPLE_AUDIT", "RANDOM_LHS_TRIPLE")
SHORT = dict(zip(SOURCES, ("H: held-out triples", "C: randomized LHS triples")))
BLUE, ORANGE, INK, GRID = "#2864DC", "#D97718", "#24292F", "#E4E7EB"


def pin(path):
    path = Path(path).resolve(strict=True)
    return dict(path=str(path), sha256=sha256(path), bytes=path.stat().st_size)


def verify(value):
    observed = pin(value["path"])
    if observed["sha256"] != value["sha256"] or observed["bytes"] != value["bytes"]:
        raise ValueError(f"source pin mismatch: {value['path']}")
    return Path(observed["path"])


def jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def fixed_cdf(errors, denominator):
    """Finite residual attainment on the original N; missing is never imputed."""
    values = np.asarray(errors, dtype=float)
    if denominator <= 0 or len(values) > denominator or np.any(values < 0):
        raise ValueError("nonnegative errors and original non-shrinking denominator required")
    values = np.sort(values[np.isfinite(values)])
    return values, np.arange(1, len(values) + 1) / denominator


def check_request(request, rows, freeze):
    """Recompute every candidate score and selected request without a model."""
    q_values = freeze["protocol"]["q_values"]
    if sorted(r["q_target"] for r in rows) != q_values:
        raise ValueError("missing or duplicate logical Q candidates")
    rows = sorted(rows, key=lambda r: r["q_target"])
    scores = []
    for row in rows:
        if (row["request_id"] != request["request_id"] or row["target_source"] != request["target_source"]
                or row["model_id"] != freeze["model_id"] or row["frequency_ghz"] != freeze["frequency_ghz"]
                or row["dataset_scope"] != freeze["config"]["dataset_scope"]):
            raise ValueError("candidate identity differs")
        if (row["evidence_source"] != "SELF_PROXY" or row["emx_status"] != "NOT_RUN"
                or row["actual_response"] is not None or row["unique_emx_solve"]):
            raise ValueError("fresh EMX/source mixing is outside this renderer")
        target = np.asarray([float(request["lp_nh"]), float(request["ls_nh"]), row["q_target"], float(request["k_abs"])])
        if not np.array_equal(np.asarray(row["target"], float), target):
            raise ValueError("candidate target differs from frozen request")
        response = np.asarray(row["grid_proxy"], float)
        if response.shape != (4,):
            raise ValueError("four proxy features required")
        value = float(np.sqrt(np.mean(((response-target)/SCALE)**2)))
        stored = row["grid_proxy_score"]
        if np.isfinite(value) != (stored is not None and np.isfinite(stored)):
            raise ValueError("nonfinite score disagreement")
        if np.isfinite(value) and not np.isclose(value, stored, rtol=1e-12, atol=1e-14):
            raise ValueError("candidate score differs from fixed-scale recomputation")
        finite = np.isfinite(response).all()
        if (row["inference_status"] == "FINITE") != finite:
            raise ValueError("inference status disagrees with finite proxy values")
        if row["within_tolerance"] != list(np.abs(response-target) <= TAU):
            raise ValueError("absolute tolerance flags differ")
        scores.append(value)
    finite = [j for j, score in enumerate(scores) if np.isfinite(score)]
    best = min(finite, key=lambda j: (scores[j], q_values[j])) if finite else None
    q_proxy = q_values[best] if len(finite) == len(q_values) else None
    if (request["q_proxy"] != q_proxy or request["N_proxy_finite"] != len(finite)
            or request["full_proxy_scan"] != (len(finite) == len(q_values))):
        raise ValueError("request Q selection differs")
    if any(r["proxy_preselected"] != (r["q_target"] == q_proxy) or r["q_proxy"] != q_proxy for r in rows):
        raise ValueError("candidate selection flags differ")
    if (request["q_emx"] is not None or request["best_available_emx"] is not None
            or request["complete_fresh_emx_candidates"] != 0 or request["independent_solves"] != 0
            or request["REAL_EMX_VALIDATION"] != "NOT_RUN"):
        raise ValueError("request physical state is not NOT_RUN")
    chosen = rows[best] if q_proxy is not None else None
    error = np.asarray(chosen["grid_proxy"])-np.asarray(chosen["target"]) if chosen else None
    if chosen:
        if not np.allclose(error, request["selected_error"], rtol=1e-12, atol=1e-14):
            raise ValueError("selected residual differs")
        if (request["selected_support"] != chosen["support_status"]
                or request["selected_analytic_pass"] != chosen["analytic_grid"]
                or request["selected_joint_response_hit"] != all(chosen["within_tolerance"])):
            raise ValueError("selected status differs")
    elif (request["selected_error"] is not None or request["selected_analytic_pass"]
          or request["selected_joint_response_hit"]):
        raise ValueError("failed request contains a selected result")
    result = {k: request[k] for k in ("request_id", "target_source", "source_geometry_id", "source_geometry_sha256",
        "lp_nh", "ls_nh", "k_abs", "request_order", "q_proxy", "full_proxy_scan", "selected_support",
        "selected_analytic_pass", "selected_joint_response_hit", "preselected_emx")}
    result.update(selected_score=scores[best] if chosen else None,
                  end_to_end_proxy_hit=bool(chosen and request["selected_analytic_pass"] and request["selected_joint_response_hit"]),
                  analytic_candidate_failures=sum(not r["analytic_grid"] for r in rows),
                  nonfinite_or_rejected_candidates=len(rows)-len(finite),
                  evidence_source="SELF_PROXY", emx_status="NOT_RUN", q_emx=None)
    result.update({"error__"+name: float(error[j]) if error is not None else None for j, name in enumerate(FEATURES)})
    return result


def group_summary(rows):
    result = []
    for source in SOURCES:
        group = [r for r in rows if r["target_source"] == source]
        if not group:
            raise ValueError("both source cohorts required")
        features = {}
        for name in FEATURES:
            e = np.asarray([r["error__"+name] for r in group if r["error__"+name] is not None], float)
            features[name] = dict(N_finite=len(e), MAE=float(np.abs(e).mean()) if len(e) else None,
                RMSE=float(np.sqrt(np.mean(e*e))) if len(e) else None,
                P95=float(np.quantile(np.abs(e), .95)) if len(e) else None)
        result.append(dict(target_source=source, N_requested=len(group), N_complete_proxy=sum(r["full_proxy_scan"] for r in group),
            N_failed_proxy=sum(not r["full_proxy_scan"] for r in group),
            analytic_pass=sum(r["selected_analytic_pass"] for r in group),
            joint_response_hit=sum(r["selected_joint_response_hit"] for r in group),
            end_to_end_proxy_hit=sum(r["end_to_end_proxy_hit"] for r in group),
            q_proxy_distribution=dict(Counter(str(r["q_proxy"]) for r in group)), features=features))
    return result


def load_qscan(root):
    """Read pinned shards in bounded batches and retain only plot-level tables."""
    root = Path(root).resolve(strict=True)
    summary_path = root/"QSCAN_SUMMARY.json"
    summary = read_json(summary_path)
    if summary.get("schema") != "frequency_qscan_summary.v1" or summary.get("status") != "PROXY_COMPLETE_EMX_NOT_RUN":
        raise ValueError("completed proxy-only Q scan required")
    if summary["REAL_EMX_VALIDATION"] != "NOT_RUN":
        raise ValueError("mixed physical evidence")
    freeze_path = verify(summary["freeze"])
    if freeze_path != root/"QSCAN_FREEZE.json":
        raise ValueError("summary points to a different freeze")
    freeze = read_json(freeze_path)
    protocol = freeze["protocol"]
    if (protocol["q_values"] != list(range(10, 21)) or not np.array_equal(protocol["score_scale"], SCALE)
            or not np.allclose(protocol["absolute_tolerances"], TAU, rtol=0, atol=1e-15)):
        raise ValueError("unsupported frozen Q grid/score/tolerance contract")
    if (summary["frequency_ghz"] != freeze["frequency_ghz"] or summary["model_id"] != freeze["model_id"]
            or summary["dataset_scope"] != freeze["config"]["dataset_scope"]):
        raise ValueError("summary/freeze model identity differs")
    sources = [pin(summary_path), pin(freeze_path)]
    # Models are hashed as bytes only. Historical implementation pins are recorded,
    # not reinterpreted using current source that may have legitimately advanced.
    for group in (freeze["identity"], freeze["artifacts"]):
        for value in group.values():
            if isinstance(value, dict) and "path" in value:
                sources.append(pin(verify(value)))
    with verify(freeze["artifacts"]["requests.csv"]).open(newline="", encoding="utf-8") as stream:
        frozen_requests = list(csv.DictReader(stream))
    ids = [r["request_id"] for r in frozen_requests]
    if len(set(ids)) != len(ids) or len(ids) != summary["N_requests"]:
        raise ValueError("frozen request identity/denominator differs")
    request_map = {r["request_id"]: r for r in frozen_requests}
    heldout = [r for r in frozen_requests if r["target_source"] == SOURCES[0]]
    first_id = min(heldout, key=lambda r: int(r["request_order"]))["request_id"]
    compact, first, seen, n_candidates = [], [], set(), 0
    for receipt_pin in summary["shards"]:
        receipt_path = verify(receipt_pin)
        receipt = read_json(receipt_path)
        if receipt["status"] != "COMPLETE" or receipt["freeze"] != summary["freeze"]:
            raise ValueError("incomplete or foreign shard")
        sources.append(pin(receipt_path))
        for value in receipt["artifacts"].values():
            sources.append(pin(verify(value)))
        requests = jsonl(receipt["artifacts"]["requests.jsonl"]["path"])
        candidates = jsonl(receipt["artifacts"]["candidates.jsonl"]["path"])
        if [r["request_id"] for r in requests] != receipt["request_ids"]:
            raise ValueError("shard request order differs")
        by_request = defaultdict(list)
        for row in candidates:
            by_request[row["request_id"]].append(row)
        if set(by_request) != set(receipt["request_ids"]):
            raise ValueError("foreign/missing candidate request")
        n_candidates += len(candidates)
        for request in requests:
            rid = request["request_id"]
            if rid in seen or rid not in request_map:
                raise ValueError("duplicate or foreign request in shards")
            seen.add(rid)
            if any(str(request.get(k)) != str(v) for k, v in request_map[rid].items()):
                raise ValueError("request differs from frozen source")
            compact.append(check_request(request, by_request[rid], freeze))
            if rid == first_id:
                first = sorted(by_request[rid], key=lambda r: r["q_target"])
    if seen != set(ids) or n_candidates != len(ids)*11 or n_candidates != summary["N_logical_candidates"]:
        raise ValueError("final fixed request/candidate denominator differs")
    compact.sort(key=lambda r: (SOURCES.index(r["target_source"]), int(r["request_order"])))
    groups = group_summary(compact)
    for computed in groups:
        matches = [g for g in summary["groups"] if g["target_source"] == computed["target_source"] and g["selected_support"] == "ALL_REQUESTS"]
        if len(matches) != 1:
            raise ValueError("ambiguous source summary")
        saved = matches[0]
        for key, value in computed.items():
            if key != "features" and saved[key] != value:
                raise ValueError(f"source summary mismatch: {key}")
        for f in FEATURES:
            for key, value in computed["features"][f].items():
                if value is None:
                    if saved["features"][f][key] is not None:
                        raise ValueError("source finite feature denominator differs")
                elif not np.isclose(saved["features"][f][key], value, rtol=1e-12, atol=1e-14):
                    raise ValueError(f"source feature statistic differs: {f}/{key}")
    return freeze, summary, compact, first, groups, sources


def target_cells(rows, xedges, yedges):
    """Fixed unsmoothed cells; retain original cell N and every failure count."""
    xedges, yedges = np.asarray(xedges, float), np.asarray(yedges, float)
    if (not np.isfinite(xedges).all() or not np.isfinite(yedges).all()
            or np.any(np.diff(xedges) <= 0) or np.any(np.diff(yedges) <= 0)):
        raise ValueError("finite increasing bin edges required")
    groups = defaultdict(list)
    outside = 0
    for row in rows:
        x, y = float(row["lp_nh"]), float(row["ls_nh"])
        if not (xedges[0] <= x <= xedges[-1] and yedges[0] <= y <= yedges[-1]):
            outside += 1
            continue
        i = min(int(np.searchsorted(xedges, x, side="right")-1), len(xedges)-2)
        j = min(int(np.searchsorted(yedges, y, side="right")-1), len(yedges)-2)
        groups[i, j].append(row)
    cells = []
    for i in range(len(xedges)-1):
        for j in range(len(yedges)-1):
            cell = groups[i, j]
            scores = [r["selected_score"] for r in cell if r["selected_score"] is not None]
            n = len(cell)
            selected = sum(r["q_proxy"] is not None for r in cell)
            extrapolated = sum(r["selected_support"] == "EXTRAPOLATION" for r in cell)
            cells.append(dict(x_index=i, y_index=j, lp_low=xedges[i], lp_high=xedges[i+1],
                ls_low=yedges[j], ls_high=yedges[j+1], N_requested=n, N_selected=selected,
                N_finite_score=len(scores), N_unselected=n-selected, N_extrapolated=extrapolated,
                N_analytic_failed=sum(r["q_proxy"] is not None and not r["selected_analytic_pass"] for r in cell),
                mean_score_available=float(np.mean(scores)) if scores else None,
                selected_extrapolation_fraction=extrapolated/n if n else None,
                sparse_fewer_than_10=n < 10))
    return dict(N_requested=len(rows), N_outside=outside, xedges=xedges.tolist(), yedges=yedges.tolist(), cells=cells)


def chart_contract(freeze, summary, sources, xedges, yedges):
    return dict(schema="frequency_qscan_chart_contract.v1", created_utc=utc_now(),
        renderer="Matplotlib static SVG/PDF/300dpi PNG", surface="standalone academic research figures",
        dataset_scope=summary["dataset_scope"], source_geometries=freeze["source_geometries"],
        actual_frequency_exposure=freeze["frequency_exposure"], model_id=summary["model_id"],
        frequency_ghz=summary["frequency_ghz"], evidence_source="SELF_PROXY", REAL_EMX_VALIDATION="NOT_RUN",
        N_requests=summary["N_requests"], N_logical_candidates=summary["N_logical_candidates"],
        grain="one request per source group; 11 Q candidates are paired alternatives, not independent requests",
        independent_variable="Q target for within-request score scan; other plots are descriptive finite-frame summaries",
        controls="same frozen model pair, split, seed, 10-D grid geometry decoder and scoring recipe",
        claim_boundary="No architecture/data-size causal claim, physical accuracy or EMX optimum; source cohorts are different target distributions",
        error_definition="symmetric grid proxy minus target(q); Q=min(Qp,Qs), NOT one-sided shortfall",
        score_scale=SCALE.tolist(), absolute_tolerances=TAU.tolist(),
        tolerance_definition="5% of fixed declared spans; NOT per-target relative 5%",
        uncertainty="NOT_ESTIMATED: one randomized LHS and fixed held-out request frame; not IID",
        failure_policy="numeric residuals of analytic failures retained; absent residuals excluded from x values but retained in fixed N",
        palette_policy="hard two-root cap: blue/orange plus neutral; density and error use single blue root",
        palette=dict(blue=BLUE, orange=ORANGE, ink=INK, grid=GRID),
        non_color_encoding="source facets, solid/dashed CDF, open/hatched failure bars, x failure markers, labels and counts",
        branding="third-party academic research; no OpenAI blossom", sources=sources,
        bin_policy="8 equal-width cells on frozen train-only p01/p99 Lp/Ls window; outside counts retained; no smoothing; post-result descriptive partition",
        xedges=xedges.tolist(), yedges=yedges.tolist(),
        charts=[
            dict(id="first_request_q_scores", family="paired ordered dot/line plus empty physical panel", footprint_inches=[12,7],
                 question="How does the first frozen held-out request score at each of the 11 Q candidates?",
                 takeaway="Q proxy winner is a self-consistency selection; analytic failures remain and no physical winner exists.",
                 sufficiency="exact 11 saved candidate rows; nonfinite/rejected values shown in status strip; no interpolation"),
            dict(id="selected_q_distribution", family="stacked bar with source facets", footprint_inches=[12,7],
                 question="Which Q is selected for each target-source request frame?",
                 takeaway="Source-specific counts retain selected analytic failures and unselected requests.",
                 sufficiency="all original source requests, 11 Q bins plus unselected category"),
            dict(id="selected_residual_cdf", family="four-feature ECDF small multiples", footprint_inches=[12,8.8],
                 question="What fraction of the original source frame attains each selected absolute residual?",
                 takeaway="Four physical-unit residual distributions include finite analytic failures; denominator never shrinks.",
                 sufficiency="all selected saved residuals; missing stays missing and curve endpoint remains finite/N"),
            dict(id="target_space_support", family="unsmoothed 2D heatmap with source facets", footprint_inches=[15,10],
                 question="Where do request density, selected proxy error and extrapolation occur in Lp/Ls target space?",
                 takeaway="Each cell carries N; sparse regions and outside-window requests remain explicit; marginal support is not joint feasibility.",
                 sufficiency="8 by 8 fixed train-window bins; empty cells missing, no smoothing or IID interval")],
        final_qa="inspect all four exported PNGs and all four PDF pages; renderer cannot certify human visual QA")


def _csv(path, rows):
    with Path(path).open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _plotting():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family":"DejaVu Sans", "font.size":10, "axes.titlesize":11,
        "figure.facecolor":"white", "axes.facecolor":"white", "text.color":INK,
        "axes.labelcolor":INK, "xtick.color":INK, "ytick.color":INK, "axes.edgecolor":INK,
        "svg.fonttype":"none", "pdf.fonttype":42, "savefig.facecolor":"white"})
    return plt


def _header(fig, title, subtitle, footnote):
    fig.suptitle(title, x=.065, ha="left", fontsize=17, fontweight="bold", y=.985)
    fig.text(.065, .935, subtitle, ha="left", va="top", fontsize=10)
    fig.text(.065, .023, footnote, ha="left", va="bottom", fontsize=8.5, linespacing=1.5)


def _axes(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color=GRID, linewidth=.6, zorder=0)


def _export(fig, out, name):
    result = []
    for ext in ("svg", "pdf", "png"):
        path = out/(name+"."+ext)
        if path.exists():
            raise FileExistsError(path)
        fig.savefig(path, dpi=300, metadata={"Creator":"frequency_qscan_figures"})
        result.append(pin(path))
    return result


def _draw_first(plt, first, row, context):
    fig, axes = plt.subplots(1, 2, figsize=(12, 7), gridspec_kw={"width_ratios":[1.6, 1]})
    fig.subplots_adjust(left=.09, right=.96, top=.79, bottom=.25, wspace=.3)
    _header(fig, "Q-candidate scores for the first held-out request", context+"\n"
        f"Lp={float(row['lp_nh']):.6f} nH; Ls={float(row['ls_nh']):.6f} nH; |K|={float(row['k_abs']):.6f}; 11 candidates / 1 request",
        "Score = RMS[(grid proxy − target(Q)) / (2.5 nH, 2.5 nH, 20, 0.8)]; lower is closer. Q=min(Qp,Qs).\n"
        "Selection uses all finite proxy scores; analytic failures remain eligible in the saved protocol.\n"
        "Physical optimum requires 11/11 valid exact fresh EMX candidates. Current fresh EMX: 0/11; q_emx absent.")
    ax = axes[0]
    q = np.asarray([r["q_target"] for r in first])
    score = np.asarray([r["grid_proxy_score"] for r in first], float)
    ax.plot(q, score, color=BLUE, marker="o", linewidth=1.5, markersize=5, label="finite SELF_PROXY")
    fails = np.asarray([not r["analytic_grid"] for r in first]) & np.isfinite(score)
    ax.scatter(q[fails], score[fails], color=ORANGE, marker="x", s=80, linewidths=2, zorder=5, label="analytic FAIL")
    missing = ~np.isfinite(score)
    if missing.any():
        ax.scatter(q[missing], np.full(missing.sum(), -.10), transform=ax.get_xaxis_transform(),
                   marker="x", color=INK, clip_on=False, label="rejected / nonfinite")
    if row["q_proxy"] is not None:
        pos = next(j for j, r in enumerate(first) if r["q_target"] == row["q_proxy"])
        ax.scatter([q[pos]], [score[pos]], marker="*", s=190, color=BLUE, edgecolor=INK, zorder=6)
        ax.annotate(f"q_proxy={q[pos]}", (q[pos], score[pos]), xytext=(0, 28), textcoords="offset points", ha="center",
                    arrowprops={"arrowstyle":"-", "color":INK}, fontsize=11)
    ax.set(xlabel="Candidate Q target [dimensionless]", ylabel="SELF_PROXY score [dimensionless]", xticks=q, ylim=(0, max(.01, np.nanmax(score)*1.25) if np.isfinite(score).any() else 1))
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    _axes(ax)
    axes[1].set(xticks=[], yticks=[], xlabel="Fresh EMX score [dimensionless]")
    for spine in axes[1].spines.values():
        spine.set_color(GRID)
    axes[1].text(.5, .61, "NOT_RUN", transform=axes[1].transAxes, ha="center", fontsize=23, color=INK)
    axes[1].text(.5, .39, "0 / 11 physical responses\nNo numeric EMX scores\nNo q_emx selection", transform=axes[1].transAxes, ha="center", fontsize=12, linespacing=1.7)
    return fig


def _draw_distribution(plt, rows, groups, context):
    from matplotlib.patches import Patch
    fig, axes = plt.subplots(1, 2, figsize=(12, 7))
    fig.subplots_adjust(left=.08, right=.97, top=.78, bottom=.25, wspace=.24)
    _header(fig, "Selected Q distribution by target source", context+"\nOne selection per request; counts and percentages use the original source-specific N",
        "Filled: selected geometry passes the analytic check. Hatched: selected geometry fails; failures stay in the denominator.\n"
        "Q selection measures SELF_PROXY agreement only. Target cohorts differ; a single randomized LHS has no IID interval.\n"
        "Marginal support does not establish joint feasibility. REAL_EMX_VALIDATION=NOT_RUN for all requests.")
    common_max = max(max(g["q_proxy_distribution"].values())/g["N_requested"]*100 for g in groups)
    for ax, source, group in zip(axes, SOURCES, groups):
        selected = [r for r in rows if r["target_source"] == source]
        qvalues = list(range(10, 21)) + [None]
        total = np.asarray([sum(r["q_proxy"] == q for r in selected) for q in qvalues])
        failed = np.asarray([sum(r["q_proxy"] == q and not r["selected_analytic_pass"] for r in selected) if q is not None else 0 for q in qvalues])
        bottom = total-failed
        x = np.arange(len(qvalues))
        ax.bar(x[:-1], bottom[:-1]/len(selected)*100, color=BLUE, edgecolor=INK, linewidth=.4, label="analytic PASS")
        ax.bar(x[-1:], bottom[-1:]/len(selected)*100, color=GRID, edgecolor=INK, linewidth=.4, label="unselected")
        mask = failed > 0
        ax.bar(x[mask], failed[mask]/len(selected)*100, bottom=bottom[mask]/len(selected)*100, facecolor="white", edgecolor=ORANGE, hatch="///", label="analytic FAIL")
        for j, value in enumerate(total):
            ax.text(j, value/len(selected)*100+.7, f"{value:,}", ha="center", va="bottom", fontsize=8, rotation=90 if value >= 1000 else 0)
        ax.set(title=f"{SHORT[source]} | N={len(selected):,}", ylabel="Share of original requests [%]", xlabel="Selected Q target [dimensionless]",
               xticks=x, xticklabels=[str(q) if q is not None else "None" for q in qvalues], ylim=(0, common_max*1.3+3))
        _axes(ax)
        ax.text(.01, .97, f"Proxy incomplete: {group['N_failed_proxy']:,}; selected analytic FAIL: {int(failed.sum()):,}",
                transform=ax.transAxes, va="top", fontsize=8.8)
    # Empty BarContainers lose their styling in Matplotlib's legend handler.
    # Explicit handles keep the failure hatch truthful even when its count is zero.
    axes[1].legend(handles=[Patch(facecolor=BLUE, edgecolor=INK, label="analytic PASS"),
                           Patch(facecolor=GRID, edgecolor=INK, label="unselected"),
                           Patch(facecolor="white", edgecolor=ORANGE, hatch="///", label="analytic FAIL")],
                   loc="upper right", bbox_to_anchor=(1, -.18), frameon=False, fontsize=8.5, ncols=3)
    return fig


def _draw_cdf(plt, rows, groups, context):
    fig, axes = plt.subplots(2, 2, figsize=(12, 8.8))
    fig.subplots_adjust(left=.08, right=.97, top=.82, bottom=.22, hspace=.4, wspace=.22)
    _header(fig, "Selected-candidate absolute residual distributions", context+"\nSELF_PROXY; H and C keep separate, fixed request denominators; physical-unit x axes use symmetric-log scaling",
        "Absolute tolerances: Lp/Ls 0.125 nH; Qmin 1; |K| 0.04. These are 5% of fixed spans, not target-relative percentages.\n"
        f"Finite residuals of analytic failures are retained: H={groups[0]['N_complete_proxy']-groups[0]['analytic_pass']:,}; C={groups[1]['N_complete_proxy']-groups[1]['analytic_pass']:,}. "
        f"Missing selections retained in N: H={groups[0]['N_failed_proxy']:,}; C={groups[1]['N_failed_proxy']:,}.\n"
        "Q residual is symmetric about selected Q target. ECDF endpoints are finite/N; no imputation, IID interval or fresh-EMX accuracy claim.")
    for j, (ax, name, label, tolerance) in enumerate(zip(axes.flat, FEATURES, LABELS, TAU)):
        xmax = tolerance
        for source, group, color, linestyle in zip(SOURCES, groups, (BLUE, ORANGE), ("-", "--")):
            e = [abs(r["error__"+name]) if r["error__"+name] is not None else np.nan for r in rows if r["target_source"] == source]
            x, y = fixed_cdf(e, group["N_requested"])
            ax.step(np.r_[0, x], np.r_[0, y*100], where="post", color=color, linestyle=linestyle, linewidth=1.6,
                    label=f"{source[0] if source == SOURCES[0] else 'C'}: N={group['N_requested']:,}; finite={len(x):,}")
            if len(x):
                xmax = max(xmax, x[-1])
        ax.axvline(tolerance, color=INK, linestyle=":", linewidth=1)
        ax.text(tolerance, 103, f"τ={tolerance:g}", ha="center", fontsize=9)
        ax.set_xscale("symlog", linthresh=tolerance/20)
        ax.set(xlim=(0, xmax*1.15), ylim=(0, 110), yticks=[0, 25, 50, 75, 100], xlabel="Absolute residual: "+label,
               ylabel="Attained / original N [%]")
        _axes(ax)
        if j == 0:
            ax.legend(loc="lower right", frameon=False, fontsize=9)
    return fig


def _draw_space(plt, cells_by_source, context):
    from matplotlib.colors import LinearSegmentedColormap
    blue = LinearSegmentedColormap.from_list("qscan_blue", ["#FFFFFF", BLUE])
    orange = LinearSegmentedColormap.from_list("qscan_orange", ["#FFFFFF", ORANGE])
    fig, axes = plt.subplots(2, 3, figsize=(15, 10))
    fig.subplots_adjust(left=.065, right=.925, top=.80, bottom=.20, hspace=.43, wspace=.36)
    _header(fig, "Lp–Ls target-space density, error and selected support", context+"\n8 × 8 cells over the frozen train-only 1st–99th percentile window; H/C facets; empty cells blank; no smoothing",
        "Density labels: original cell N. Error labels: finite selected scores / cell N; mean score includes selected analytic failures.\n"
        "Support labels: extrapolated selections / cell N. In-range marginal support leaves joint feasibility UNKNOWN; sparse cells N<10 are descriptive.\n"
        "Color scales shared between source rows. Outside-window counts are retained in row labels and source tables. No spatial or IID confidence interval.")
    specs = [("N_requested", "Request density", blue, "requests / cell"),
             ("mean_score_available", "Mean selected SELF_PROXY score", blue, "fixed-scale score"),
             ("selected_extrapolation_fraction", "Selected extrapolation support", orange, "extrapolated / cell N")]
    for col, (key, title, cmap, colorbar_label) in enumerate(specs):
        all_values = [c[key] for data in cells_by_source.values() for c in data["cells"] if c[key] is not None]
        vmax = max(all_values) if all_values else 1
        vmax = max(vmax, 1e-9)
        for row, source in enumerate(SOURCES):
            data = cells_by_source[source]
            matrix = np.full((8, 8), np.nan)
            for cell in data["cells"]:
                if cell["N_requested"]:
                    matrix[cell["y_index"], cell["x_index"]] = cell[key] if cell[key] is not None else np.nan
            ax = axes[row, col]
            m = ax.pcolormesh(data["xedges"], data["yedges"], np.ma.masked_invalid(matrix), cmap=cmap, vmin=0, vmax=vmax,
                             edgecolors=GRID, linewidth=.35)
            for cell in data["cells"]:
                n = cell["N_requested"]
                if not n:
                    continue
                text = str(n) if col == 0 else f"{cell['N_finite_score']}/{n}" if col == 1 else f"{cell['N_extrapolated']}/{n}"
                ax.text((cell["lp_low"]+cell["lp_high"])/2, (cell["ls_low"]+cell["ls_high"])/2, text,
                        ha="center", va="center", fontsize=6.1, color=INK,
                        bbox={"facecolor":"white", "alpha":.82, "edgecolor":"none", "pad":.2})
            ax.set(title=(title+"\n" if row == 0 else "")+f"{SHORT[source]} | N={data['N_requested']:,}; outside={data['N_outside']:,}",
                   xlabel="Target Lp [nH]", ylabel="Target Ls [nH]")
            cb = fig.colorbar(m, ax=ax, fraction=.042, pad=.02)
            cb.set_label(colorbar_label, fontsize=8)
            cb.ax.tick_params(labelsize=8)
    return fig


def render_qscan_figures(qscan_dir, out_dir):
    out = Path(out_dir).resolve()
    if out.exists():
        raise FileExistsError(out)
    freeze, summary, rows, first, groups, sources = load_qscan(qscan_dir)
    xedges = np.linspace(freeze["train_window"]["p01"][0], freeze["train_window"]["p99"][0], 9)
    yedges = np.linspace(freeze["train_window"]["p01"][1], freeze["train_window"]["p99"][1], 9)
    cells = {s: target_cells([r for r in rows if r["target_source"] == s], xedges, yedges) for s in SOURCES}
    out.mkdir(parents=True, exist_ok=False)
    try:
        with Path(__file__).open("rb") as src, (out/"renderer_source.py").open("xb") as dest:
            shutil.copyfileobj(src, dest)
        save_json(out/"CHART_CONTRACTS.json", chart_contract(freeze, summary, sources, xedges, yedges))
        _csv(out/"selected_requests.csv", rows)
        first_table = []
        for record in first:
            row = {k: record[k] for k in ("request_id", "candidate_id", "q_target", "grid_proxy_score", "q_proxy",
                   "proxy_preselected", "analytic_grid", "support_status", "inference_status", "emx_status", "parameter_geometry_hash")}
            row.update(emx_score=None, q_emx=None)
            for j, feature in enumerate(FEATURES):
                row["target__"+feature] = record["target"][j]
                row["grid_proxy__"+feature] = record["grid_proxy"][j]
            first_table.append(row)
        _csv(out/"first_request_candidates.csv", first_table)
        _csv(out/"target_space_cells.csv", [dict(target_source=s, **c) for s, d in cells.items() for c in d["cells"]])
        save_json(out/"PLOT_DATA.json", dict(groups=groups, first_request_id=first[0]["request_id"], first_request_candidates=first,
            target_space=cells, original_summary=pin(Path(qscan_dir)/"QSCAN_SUMMARY.json")))
        context = (f"{summary['frequency_ghz']:g} GHz | {summary['dataset_scope']} | SELF_PROXY | fresh EMX: NOT_RUN\n"
                   f"Source geometries {freeze['source_geometries']:,}; eligible train/validation/test "
                   f"{freeze['frequency_exposure']['train']['eligible_geometries']:,}/"
                   f"{freeze['frequency_exposure']['validation']['eligible_geometries']:,}/"
                   f"{freeze['frequency_exposure']['test']['eligible_geometries']:,}; seed {freeze['train_seed']}")
        plt = _plotting()
        artifacts = []
        for name, draw, args in (
            ("first_request_q_scores", _draw_first, (first, rows[0], context)),
            ("selected_q_distribution", _draw_distribution, (rows, groups, context)),
            ("selected_residual_cdf", _draw_cdf, (rows, groups, context)),
            ("target_space_support", _draw_space, (cells, context))):
            fig = draw(plt, *args)
            artifacts.extend(_export(fig, out, name))
            plt.close(fig)
        # Detect source changes throughout rendering, without invoking model code.
        for value in sources:
            verify(value)
        receipt = dict(schema="frequency_qscan_figure_receipt.v1", status="RENDER_COMPLETE_VISUAL_QA_PENDING", created_utc=utc_now(),
            figures=artifacts, sources=sources, data_files=[pin(out/n) for n in ("CHART_CONTRACTS.json", "selected_requests.csv", "first_request_candidates.csv", "target_space_cells.csv", "PLOT_DATA.json", "renderer_source.py")],
            code=pin(out/"renderer_source.py"), N_requests=len(rows), N_logical_candidates=summary["N_logical_candidates"],
            groups=groups, numerics_qa="PASS: source pins, exact requests/candidates, score/selection/residual/tolerance recomputation and saved summary reconciliation",
            model_prediction_calls=0, training_calls=0, physical_calls=0, REAL_EMX_VALIDATION="NOT_RUN")
        save_json(out/"FIGURE_RECEIPT.json", receipt)
        return receipt
    except Exception as exc:
        save_json(out/"FAILURE_RECEIPT.json", dict(status="FAIL", created_utc=utc_now(), error_type=type(exc).__name__, error=str(exc),
                  next_legal_entry="Keep this failed directory; fix renderer and choose a new no-clobber output version."))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qscan", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    receipt = render_qscan_figures(args.qscan, args.out)
    print(json.dumps({k:receipt[k] for k in ("status", "N_requests", "N_logical_candidates", "REAL_EMX_VALIDATION")}))


if __name__ == "__main__":
    main()
