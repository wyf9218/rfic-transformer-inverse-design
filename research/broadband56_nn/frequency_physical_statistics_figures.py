"""Incremental static figures from frozen local physical statistics only.

No model, CAD, solver, SSH, or running-observer imports. Request figures are
content-addressed for reuse; a published receipt is the only completion marker.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
import os
from pathlib import Path

import numpy as np

from .io import canonical_sha, read_json, save_json, sha256, utc_now

FEATURES = ("Lp_nH", "Ls_nH", "Qmin", "K_abs")
LABELS = ("Lp [nH]", "Ls [nH]", "Qmin [dimensionless]", "|k| [dimensionless]")
SCALE = (2.5, 2.5, 20., .8)
TAU = (.125, .125, 1., .04)
FREQUENCIES = tuple(range(5, 21))
FORMAL = "FORMAL_10K"
DEVELOPMENT = "DEVELOPMENT_5K_NOT_FORMAL_10K"
BLUE, ORANGE, INK, LIGHT = "#2864DC", "#D97718", "#24292F", "#E4E7EB"
FIRST15 = "qscan15_development5k_20260908_v1-HELDOUT_TRIPLE_AUDIT-000000"
SIGNATURE_FIELDS = ("request_id", "candidate_id", "frequency_ghz", "model_id", "dataset_scope", "target_source", "label_mode",
    "q_target", "q_proxy", "selected_before_emx", "stage", "solved", "strict_valid", "joint_hit", "target", "proxy",
    "actual", "emx_minus_target", "emx_minus_frozen_proxy", "target_relative_absolute_percent",
    "proxy_score", "emx_score", "score_scale", "absolute_tolerances")


def require(value, message):
    if not value:
        raise ValueError(message)


def pin(path):
    path = Path(path).resolve(strict=True)
    return dict(path=str(path), sha256=sha256(path), bytes=path.stat().st_size)


def verify(record):
    found = pin(record["path"])
    require(found["sha256"] == record["sha256"] and found["bytes"] == record["bytes"], "source pin mismatch: " + found["path"])
    return Path(found["path"])


def csv_rows(path):
    with Path(path).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def number(value):
    if value in (None, "", "None", "null", "NA", "NOT_AVAILABLE"):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def truth(value):
    return value is True or value == "True" or value == "true" or value == "1"


def request_signature(rows):
    def source_hashes(value):
        if isinstance(value,dict):
            return ([value["sha256"]] if "sha256" in value else []) + [h for v in value.values() for h in source_hashes(v)]
        if isinstance(value,list):return [h for v in value for h in source_hashes(v)]
        return []
    return canonical_sha([dict({k:r[k] for k in SIGNATURE_FIELDS},source_hashes=sorted(source_hashes(r.get("source_pins",{}))))
                          for r in sorted(rows,key=lambda r:r["q_target"])])


def stage_bucket(row):
    stage = row["stage"].upper()
    if row["strict_valid"] is True:
        return "Strict valid"
    if row["solved"]:
        return "EMX invalid"
    if "PENDING" in stage:
        return "Pending"
    if "ANALYTIC" in stage:
        return "Analytic fail"
    if "CALIBRE" in stage or "DRC" in stage:
        return "Calibre fail"
    if "GDS" in stage or "CADENCE" in stage:
        return "GDS/Cadence fail"
    return "Other not solved"


def load_snapshot(root):
    root = Path(root).resolve(strict=True)
    receipt_path = root / "STATS_RECEIPT.json"
    receipt = read_json(receipt_path)
    require(receipt["schema"] == "frequency_physical_statistics_receipt.v1" and receipt["status"] == "PUBLISHED",
            "published complete statistics receipt required")
    manifest = read_json(verify(receipt["manifest"]))
    verify(receipt["summary"]); verify(receipt["sha256sums"])
    files = {Path(p["path"]).name: p for p in manifest["artifacts"]}
    names = ("CANDIDATE_ROWS.json", "REQUEST_STATUS.csv", "FREQUENCY_STATUS.csv", "METRICS.csv", "SELECTION_COMPARISON.csv", "SUMMARY.json")
    for name in names:
        require(name in files and verify(files[name]) == root / name, "statistics artifact missing or outside snapshot: " + name)
    candidate_data = read_json(root / "CANDIDATE_ROWS.json")
    require(candidate_data["schema"] == "frequency_physical_candidate_rows.v1" and candidate_data["feature_order"] == list(FEATURES),
            "wrong candidate schema or feature order")
    candidates = candidate_data["rows"]
    require(Counter(r["frequency_ghz"] for r in candidates) == Counter({f:220 for f in FREQUENCIES}), "exact16 routes with220 slots each required")
    require(all(r["dataset_scope"] == (DEVELOPMENT if r["frequency_ghz"] == 15 else FORMAL) for r in candidates), "frozen physical scope routing changed")
    requests = csv_rows(root / "REQUEST_STATUS.csv")
    grouped = defaultdict(list)
    for row in candidates:
        grouped[row["request_id"]].append(row)
    require(len(requests) == 320 and len(candidates) == 3520 and len(grouped) == 320, "original320/3520 frame required")
    require(len({r["request_id"] for r in requests}) == 320, "duplicate request summary")
    require(set(grouped) == {r["request_id"] for r in requests}, "request/candidate identity disagreement")
    for request in requests:
        group = sorted(grouped[request["request_id"]], key=lambda r: r["q_target"])
        require([r["q_target"] for r in group] == list(range(10, 21)), "missing or duplicate original Q slot")
        require(len({r["candidate_id"] for r in group}) == 11, "duplicate candidate identity")
        require(all(str(r[k]) == str(request[k]) for r in group for k in ("frequency_ghz", "model_id", "dataset_scope", "q_proxy")),
                "candidate/request model context differs")
        require(all(r["score_scale"] == list(SCALE) and np.allclose(r["absolute_tolerances"], TAU, rtol=0, atol=1e-15) for r in group),
                "changed score or absolute tolerance contract")
        require(all(r["selected_before_emx"] == (r["q_target"] == r["q_proxy"]) for r in group), "selected identity differs")
        if number(request.get("q_emx")) is not None:
            require(all(r["strict_valid"] is True for r in group), "q_emx requires all11 original strict-valid candidates")
        for r in group:
            if not r["solved"]:
                require(r["actual"] is None and r["emx_score"] is None and r["target_relative_absolute_percent"] is None,
                        "unsolved slots cannot contain numeric EMX or zero-filled error")
    for scope in (FORMAL, DEVELOPMENT):
        for feature in ("target_source", "label_mode"):
            require(len({r[feature] for r in candidates if r["dataset_scope"] == scope}) <= 1, "do not pool different target sources or label policies")
    return dict(root=root, receipt=receipt, source_pin=pin(receipt_path), files=files, candidates=candidates,
                requests=requests, grouped=dict(grouped), metrics=csv_rows(root / "METRICS.csv"),
                selection=csv_rows(root / "SELECTION_COMPARISON.csv"), summary=read_json(root / "SUMMARY.json"))


def plot_style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
        "axes.spines.right": False, "axes.edgecolor": INK, "text.color": INK, "axes.labelcolor": INK,
        "svg.hashsalt": "frequency-physical-statistics-v1", "pdf.fonttype": 42})
    return plt


def header(fig, title, subtitle, source_sha, footer):
    fig.suptitle(title, x=.065, y=.98, ha="left", fontsize=18, fontweight="bold")
    fig.text(.065, .939, subtitle, va="top", fontsize=10)
    fig.text(.065, .043, footer, va="bottom", fontsize=9)
    fig.text(.065, .015, "Frozen statistics receipt SHA-256 " + source_sha, fontsize=8)


def export(fig, out, stem):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    # Figure-level text cannot be rescued by a tight crop; inspect actual page.
    for text in fig.texts:
        box = text.get_window_extent(renderer)
        require(fig.bbox.contains(*box.get_points()[0]) and fig.bbox.contains(*box.get_points()[1]), "figure header/footer overflow")
    result = []
    for suffix in ("png", "svg", "pdf"):
        path = out / (stem + "." + suffix)
        require(not path.exists(), "figure exists: " + str(path))
        fig.savefig(path, dpi=300, facecolor="white")
        result.append(pin(path))
    plot_style().close(fig)
    return result


def route_status(data, out):
    plt = plot_style()
    fig, axes = plt.subplots(1, 2, figsize=(18, 10), gridspec_kw={"width_ratios": [1, 2]})
    fig.subplots_adjust(left=.065, right=.98, top=.84, bottom=.15, wspace=.20)
    labels = [f"{f} GHz" + ("  D5K*" if f == 15 else "  F10K") for f in FREQUENCIES]
    by_frequency = {f: [r for r in data["candidates"] if r["frequency_ghz"] == f] for f in FREQUENCIES}
    counts = [Counter(stage_bucket(r) for r in by_frequency[f]) for f in FREQUENCIES]
    accounted = [sum(r["status"] == "ACCOUNTED" for r in data["requests"] if int(r["frequency_ghz"]) == f) for f in FREQUENCIES]
    y = np.arange(16)
    axes[0].barh(y, accounted, color=BLUE, label="Accounted request")
    axes[0].barh(y, 20-np.asarray(accounted), left=accounted, color=LIGHT, edgecolor=INK, hatch="..", label="Pending request")
    for i, n in enumerate(accounted): axes[0].text(20.3, i, f"{n}/20", va="center", fontsize=9)
    axes[0].set_xlim(0, 23)
    axes[0].set_yticks(y, labels)
    axes[0].set_xlabel("Requests; original N=20 per route")
    axes[0].legend(loc="lower left", bbox_to_anchor=(0, 1.015), frameon=False, fontsize=9)
    keys = ("Strict valid", "EMX invalid", "Analytic fail", "GDS/Cadence fail", "Calibre fail", "Other not solved", "Pending")
    colors = (BLUE, "white", ORANGE, "white", "#F4CAA5", "#AEB4BC", LIGHT)
    hatches = ("", "xx", "//", "//", "xx", "++", "..")
    left = np.zeros(16)
    for key, color, hatch in zip(keys, colors, hatches):
        values = np.array([count[key] for count in counts])
        axes[1].barh(y, values, left=left, color=color, edgecolor=INK, linewidth=.45, hatch=hatch, label=key)
        left += values
    require(all(left == 220), "candidate state bars must retain220 original slots per route")
    axes[1].set_yticks(y, [""]*16)
    axes[1].set_xlim(0, 220)
    axes[1].set_xlabel("Candidate slots; original N=220 per route")
    axes[1].legend(loc="lower left", bbox_to_anchor=(0, 1.015), ncol=3, frameon=False, fontsize=9)
    for ax in axes:
        ax.invert_yaxis(); ax.grid(axis="x", alpha=.2); ax.set_axisbelow(True)
    header(fig, "Physical request coverage and candidate gate states", "16 routes / 320 original requests / 3,520 original Q slots. An accounted request can contain failed candidates.",
           data["source_pin"]["sha256"], "*15 GHz uses development5K; other routes use formal10K. Analytic failures include frozen pre-dispatch gates across all320 requests.\nAn unaccounted request can already have analytic failures; Pending is neither success nor failure. Descriptive coverage only.")
    return export(fig, out, "route_status")


def metric_group(data, scope, estimand, comparison, feature):
    rows = [r for r in data["metrics"] if r["dataset_scope"] == scope and r["estimand"] == estimand
            and r["comparison"] == comparison and r["validity"] == "strict_valid" and r["feature"] == feature]
    require(len({int(r["frequency_ghz"]) for r in rows}) == len(rows), "metric group pools distinct models")
    return {int(r["frequency_ghz"]): r for r in rows}


def mae_p95(data, out, scope, estimand):
    plt = plot_style()
    fig, axes = plt.subplots(4, 2, figsize=(18, 15))
    fig.subplots_adjust(left=.075, right=.975, top=.89, bottom=.13, hspace=.48, wspace=.22)
    for i, feature in enumerate(FEATURES):
        for j, comparison in enumerate(("emx_minus_target", "emx_minus_frozen_proxy")):
            ax = axes[i, j]
            lookup = metric_group(data, scope, estimand, comparison, feature)
            frequencies = FREQUENCIES if scope == FORMAL else (15,)
            xs = np.arange(len(frequencies))
            for offset, field, marker, fill, label in ((-.12,"mae","o",BLUE,"MAE"),(.12,"abs_error_p95","s","white","P95 |error|")):
                points = [(x, number(lookup.get(f, {}).get(field))) for x, f in zip(xs, frequencies)]
                good = [(x,v) for x,v in points if v is not None]
                if good: ax.scatter([x+offset for x,v in good], [v for x,v in good], marker=marker, facecolor=fill, edgecolor=BLUE, s=35, label=label)
                if field == "mae":
                    for x, f in zip(xs, frequencies):
                        record=lookup.get(f,{})
                        value,lower,upper=(number(record.get(k)) for k in ("mae","mae_ci_low","mae_ci_high"))
                        if value is not None and lower is not None and upper is not None:
                            ax.vlines(x+offset,lower,upper,color=BLUE,linewidth=1.1)
            for x, f in zip(xs, frequencies):
                if number(lookup.get(f, {}).get("mae")) is None:
                    ax.axvspan(x-.35, x+.35, color=LIGHT)
            ticklabels = []
            for f in frequencies:
                r = lookup.get(f, {})
                n = int(number(r.get("n")) or 0)
                nr = int(number(r.get("n_request_groups")) or 0)
                ticklabels.append(f"{f}\nn={n}\nR={nr}" if r else f"{f}\nN/S")
            ax.set_xticks(xs, ticklabels, fontsize=8)
            ax.set_ylim(bottom=0); ax.set_xlim(-.6, len(xs)-.4)
            ax.set_ylabel(LABELS[i]); ax.grid(axis="y", alpha=.25); ax.set_axisbelow(True)
            ax.set_title(("EMX - target" if j == 0 else "EMX - frozen proxy") + " | " + FEATURES[i], loc="left", fontsize=11)
            if i == 0 and j == 0: ax.legend(loc="upper left", fontsize=8, frameon=False)
    header(fig, f"Physical residual MAE / P95 — {scope} — {estimand}", "Physical units; n = strict-valid rows, R = distinct request groups. Different supports and training budgets; no interpolated frequency curves.",
           data["source_pin"]["sha256"], "MAE error bars use saved95% request-cluster bootstrapCI where available; R<2: noCI, R2..9: PROVISIONAL. P95 dot is an absolute-error percentile.\nOriginal frame per route:20 requests /220 Q slots. Values are conditional on strict validity; failures/pending remain in the status panel.\nN/S = not in this scope; missing is not zero. Fixed observed requests only; no population accuracy or causal frequency/model ranking.")
    return export(fig, out, ("formal" if scope == FORMAL else "development15") + "_" + estimand + "_mae_p95")


def heatmap(data, out):
    from matplotlib.colors import LinearSegmentedColormap
    plt = plot_style()
    fig, axes = plt.subplots(2, 2, figsize=(21, 9))
    fig.subplots_adjust(left=.065, right=.91, top=.84, bottom=.16, wspace=.16, hspace=.55)
    cmap = LinearSegmentedColormap.from_list("physical_blue", ["#FFFFFF", "#91B5F1", BLUE])
    cmap.set_bad(LIGHT)
    for i, estimand in enumerate(("selected_q_proxy", "all_candidates")):
        for j, comparison in enumerate(("emx_minus_target", "emx_minus_frozen_proxy")):
            ax = axes[i,j]; array = np.full((4,16), np.nan)
            for k, feature in enumerate(FEATURES):
                lookup = metric_group(data, FORMAL, estimand, comparison, feature)
                for n,f in enumerate(FREQUENCIES):
                    value = number(lookup.get(f, {}).get("mae"))
                    if value is not None: array[k,n] = value/SCALE[k]
            im = ax.imshow(np.ma.masked_invalid(array), cmap=cmap, vmin=0, vmax=.10, aspect="auto")
            for k in range(4):
                for n,f in enumerate(FREQUENCIES):
                    value = array[k,n]
                    label = (f"{value:.3f}" + ("+" if value>.10 else "")) if np.isfinite(value) else ("N/S" if f==15 else "—")
                    ax.text(n,k,label,ha="center",va="center",fontsize=8,color="white" if value>.065 else INK)
            support=metric_group(data, FORMAL, estimand, comparison, FEATURES[0])
            # Strict-valid four-feature rows share support; never hide a feature-specific denominator.
            for feature in FEATURES[1:]:
                other=metric_group(data, FORMAL, estimand, comparison, feature)
                require(all((r.get("n"),r.get("n_request_groups"))==(other[f].get("n"),other[f].get("n_request_groups")) for f,r in support.items()),
                        "heatmap feature support differs; requires per-cell denominators")
            ticks=[f"{f}\nn={int(number(support[f].get('n')) or 0)}\nR={int(number(support[f].get('n_request_groups')) or 0)}" if f in support else f"{f}\nN/S" for f in FREQUENCIES]
            ax.set_xticks(range(16), ticks,fontsize=8);ax.set_yticks(range(4),FEATURES)
            ax.set_title(estimand + " | " + comparison, loc="left", fontsize=11)
    cax = fig.add_axes([.925,.25,.012,.5])
    fig.colorbar(im, cax=cax, extend="max", label="MAE / fixed declared span (dimensionless)")
    header(fig, "Formal10K physical MAE on one fixed normalized scale", "4 features ×16 frequencies per panel. Fixed spans [2.5 nH,2.5 nH,20,0.8]; color range0..0.10 for every panel and snapshot.",
           data["source_pin"]["sha256"], "+ marks values above the fixed color maximum. — = no strict-valid metric; N/S =15 GHz not scoped. n = valid rows, R = request groups.\nOriginal per-route frame20 requests /220 Q slots. Do not substitute development5K data; no IID or causal ranking.")
    return export(fig,out,"formal_normalized_mae_heatmap")


def request_q_plots(data, rows, request, out, *, score_only=False):
    plt = plot_style(); rows = sorted(rows,key=lambda r:r["q_target"])
    f, q_proxy, q_emx = rows[0]["frequency_ghz"], rows[0]["q_proxy"], number(request.get("q_emx"))
    title_scope="DEVELOPMENT5K" if rows[0]["dataset_scope"]==DEVELOPMENT else rows[0]["dataset_scope"]
    title_suffix=f"{f} GHz | {title_scope} | {rows[0]['model_id']}"
    valid=sum(r["strict_valid"] is True for r in rows)
    subtitle=f"{rows[0]['request_id']}\nOriginal11 slots; strict-valid {valid}/11; q_proxy={q_proxy}; q_emx="+(str(int(q_emx)) if q_emx is not None else "UNAVAILABLE (full11 gate unmet)")
    if rows[0]["dataset_scope"]==DEVELOPMENT:subtitle+="\nDEVELOPMENT5K is not the formal10K model; this evidence is never pooled into the formal15 column."
    footer="Score = sqrt(mean(((response-target(Q))/[2.5,2.5,20,0.8])²)). q_proxy was fixed before EMX; failure slots are not replaced.\nOne fixed request, not model/population accuracy; no PVT, mesh-convergence or full-chip claim."
    fig,ax=plt.subplots(figsize=(16,8));fig.subplots_adjust(left=.08,right=.975,top=.78,bottom=.18)
    qs=[r["q_target"] for r in rows]
    for field,color,marker,label in (("proxy_score",BLUE,"o","Frozen grid proxy"),("emx_score",ORANGE,"s","Fresh EMX (strict-valid)")):
        values=[number(r[field]) if field=="proxy_score" or r["strict_valid"] is True else None for r in rows]
        ax.plot(qs,[np.nan if v is None else v for v in values],color=color,marker=marker,linewidth=1.5,label=label)
    for r in rows:
        if r["strict_valid"] is not True:
            ax.axvspan(r["q_target"]-.32,r["q_target"]+.32,color=LIGHT,zorder=0)
            ax.text(r["q_target"],.03,stage_bucket(r).replace(" ","\n"),transform=ax.get_xaxis_transform(),ha="center",va="bottom",fontsize=8)
    ax.axvline(q_proxy,color=INK,linestyle="--",linewidth=1,label=f"preselected q_proxy={q_proxy}")
    if q_emx is not None:ax.axvline(q_emx,color=ORANGE,linestyle=":",linewidth=1.4,label=f"full11 q_emx={int(q_emx)}")
    ax.set_xticks(qs);ax.set_xlim(9.5,20.5);ax.set_ylim(bottom=0)
    ax.set_xlabel("Original target Q");ax.set_ylabel("Symmetric fixed-span score (lower is better)")
    ax.legend(loc="lower left",bbox_to_anchor=(0,1.04),ncol=3,frameon=False);ax.grid(axis="y",alpha=.25)
    header(fig,"Q scan: proxy and fresh EMX — "+title_suffix,subtitle,data["source_pin"]["sha256"],footer)
    exports=export(fig,out,"proxy_emx_score_by_q")
    if score_only:return exports
    fig,axes=plt.subplots(2,2,figsize=(17,11));fig.subplots_adjust(left=.075,right=.975,top=.81,bottom=.17,wspace=.2,hspace=.37)
    for i,ax in enumerate(axes.flat):
        for r in rows:
            q=r["q_target"];pct=r["target_relative_absolute_percent"]
            value=number(pct[i]) if pct is not None and r["strict_valid"] is True else None
            if value is None:
                ax.axvspan(q-.32,q+.32,color=LIGHT)
                unavailable="Percent\nundefined" if r["strict_valid"] is True else stage_bucket(r).replace(" ","\n")
                ax.text(q,.07,unavailable,transform=ax.get_xaxis_transform(),ha="center",va="bottom",fontsize=7)
            else:
                ax.bar(q,value,color=BLUE,edgecolor=INK,linewidth=.5,hatch="///" if q==q_proxy else "",width=.64)
                ax.annotate(f"{value:.3g}%",(q,value),xytext=(0,4),textcoords="offset points",ha="center",fontsize=8)
        ax.set_ylim(bottom=0);lo,hi=ax.get_ylim();ax.set_ylim(0,max(hi,.1)*1.22)
        ax.set_xticks(qs);ax.set_xlim(9.5,20.5);ax.set_title(LABELS[i],loc="left")
        ax.set_xlabel("Original target Q");ax.set_ylabel("Absolute target-relative error (%)")
        ax.grid(axis="y",alpha=.22);ax.set_axisbelow(True)
    header(fig,"All11 candidate target-relative errors — "+title_suffix,subtitle,data["source_pin"]["sha256"],
        "Each panel has its own percentage axis; numeric % labels show every valid candidate. Hatched bar = preselected q_proxy.\nHit thresholds are [0.125 nH,0.125 nH,1,0.04], NOT target-relative5%. Fail/invalid/null slots have no numeric bar.")
    return exports+export(fig,out,"target_percent_by_q")


def legacy_reuse(path, data):
    receipt=read_json(path)
    require(receipt["schema"]=="first15_completed_real_emx_evidence_delivery.v1" and receipt["status"]=="COMPLETE", "explicit first15 reuse receipt required")
    rid=receipt["request_id"];require(rid==FIRST15 and rid in data["grouped"],"legacy first15 identity differs")
    original=csv_rows(verify(receipt["chart_data"]))
    rows=sorted(data["grouped"][rid],key=lambda r:r["q_target"])
    require(len(original)==11,"legacy reuse requires original11 slots")
    for old,new in zip(original,rows):
        require(int(old["q"])==new["q_target"] and old["candidate_id"]==new["candidate_id"] and old["model_id"]==new["model_id"] and old["dataset_scope"]==new["dataset_scope"],"legacy candidate identity differs")
        for i,feature in enumerate(FEATURES):
            for prefix,key in (("target_","target"),("proxy_","proxy"),("fresh_emx_","actual")):
                old_value=number(old[prefix+feature]);new_value=None if new[key] is None else number(new[key][i])
                require((old_value is None and new_value is None) or (old_value is not None and new_value is not None and math.isclose(old_value,new_value,rel_tol=1e-10,abs_tol=1e-12)),"legacy figure values differ")
    exports=[pin(verify(p)) for p in receipt["selected_exports"]]
    verify(receipt["visual_qa"])
    return {rid:dict(request_id=rid,signature=request_signature(rows),exports=exports,reused_from=pin(path),
        visual_qa=receipt["visual_qa"],format_note="Historical PNG/SVG reused unchanged; no PDF was originally exported and none regenerated.")}


def previous_reuse(path, data):
    receipt=read_json(path)
    require(receipt["schema"]=="frequency_physical_statistics_figures_receipt.v1" and receipt["status"]=="COMPLETE", "complete previous figure receipt required")
    require(receipt["statistics"]["sha256"] != data["source_pin"]["sha256"], "this exact statistics snapshot already has figures")
    found={}
    for entry in receipt["request_figures"]:
        rid=entry["request_id"]
        if rid in data["grouped"]:
            require(entry["signature"]==request_signature(data["grouped"][rid]),"previous immutable request content changed")
            for p in entry["exports"]:verify(p)
            found[rid]=dict(entry,reused_from=pin(path))
    return found


def selection_plots(data, out):
    """Draw only an actually available common complete11 comparison."""
    plt=plot_style();exports=[]
    for scope in (FORMAL,DEVELOPMENT):
        eligible=[r for r in data["selection"] if r["dataset_scope"]==scope and number(r.get("N_common_complete_requests")) and r["status"]=="AVAILABLE_DESCRIPTIVE"]
        if not eligible:continue
        frequencies=sorted({int(r["frequency_ghz"]) for r in eligible})
        fig,axes=plt.subplots(2,2,figsize=(16,10));fig.subplots_adjust(left=.075,right=.975,top=.83,bottom=.16,wspace=.2,hspace=.4)
        for i,ax in enumerate(axes.flat):
            for strategy,dx,color,marker,face in (("fixed_q15",-.2,BLUE,"o","white"),("q_proxy",0,BLUE,"o",BLUE),("q_emx",.2,ORANGE,"s",ORANGE)):
                values={int(r["frequency_ghz"]):r for r in eligible if r["strategy"]==strategy and r["feature"]==FEATURES[i]}
                for x,f in enumerate(frequencies):
                    require(f in values,"common comparison missing a strategy/feature")
                    ax.scatter(x+dx,number(values[f]["mae"]),color=color,marker=marker,facecolors=face,s=48,label=strategy if x==0 else None)
            n={int(r["frequency_ghz"]):int(r["N_common_complete_requests"]) for r in eligible}
            ax.set_xticks(range(len(frequencies)),[f"{f} GHz\nR={n[f]}" for f in frequencies]);ax.set_ylim(bottom=0)
            ax.set_ylabel("MAE "+LABELS[i]);ax.set_title(FEATURES[i],loc="left");ax.grid(axis="y",alpha=.2)
            if i==0:ax.legend(frameon=False)
        header(fig,"Complete11 selection comparison — "+scope,"All three strategies use exactly the same strict-valid original11 requests; R is the shared request denominator.",
            data["source_pin"]["sha256"],"q_emx is the retrospective normalized-score oracle, not necessarily the best individual feature. No survival-only optimum or cross-scope pooling.\nSmall fixed request samples are descriptive; exact MAE/RMSE/Bias/quantiles and request-clusterCI remain in SELECTION_COMPARISON.csv.")
        exports+=export(fig,out,("formal" if scope==FORMAL else "development15")+"_complete11_selection")
    return exports


def distribution_plots(data, out):
    """Simple bounded display transformations, not a new statistical analysis."""
    plt=plot_style();exports=[];availability=[]
    for f in FREQUENCIES:
        allrows=[r for r in data["candidates"] if r["frequency_ghz"]==f]
        rows=[r for r in allrows if r["strict_valid"] is True]
        n=len({r["request_id"] for r in rows});scope=allrows[0]["dataset_scope"]
        varying=all(len({r["target"][i] for r in rows})>=3 for i in range(4))
        entry=dict(frequency_ghz=f,dataset_scope=scope,N_strict_valid_requests=n,
            CDF="NOT_AVAILABLE_NEEDS_5_REQUESTS" if n<5 else "RENDERED_CONDITIONAL_STRICT_VALID_ECDF",
            attainment="NOT_AVAILABLE_NEEDS_5_REQUESTS" if n<5 else "RENDERED_DESCRIPTIVE_ATTAINMENT",
            density="NOT_AVAILABLE_NEEDS_20_REQUESTS_AND_TARGET_VARIATION" if n<20 or not varying else "RENDERED_UNSMOOTHED_BIN_COUNTS",
            reason="Distinct request support, never11-candidate inflation. Exact rows retained for sparse cases.")
        availability.append(entry)
        if n>=5:
            fig,axes=plt.subplots(2,2,figsize=(16,10));fig.subplots_adjust(left=.075,right=.975,top=.83,bottom=.18,wspace=.2,hspace=.35)
            for i,ax in enumerate(axes.flat):
                for selected,color,label,style in ((False,BLUE,"all strict-valid candidates","-"),(True,ORANGE,"strict-valid q_proxy","--")):
                    subset=[r for r in rows if not selected or r["selected_before_emx"]]
                    errors=sorted(abs(r["emx_minus_target"][i]) for r in subset if number(r["emx_minus_target"][i]) is not None)
                    if errors:
                        # Start at zero and preserve duplicate observations: an exact empirical step, no smoothing.
                        ax.step([0]+errors,[0]+list(np.arange(1,len(errors)+1)/len(errors)),where="post",color=color,linestyle=style,label=f"{label}; n={len(errors)}")
                    else:
                        ax.text(.02,.82,"strict-valid q_proxy: n=0; no curve",transform=ax.transAxes,fontsize=9)
                ax.set_ylim(0,1.02);ax.set_xlim(left=0);ax.set_xlabel("Absolute residual "+LABELS[i]);ax.set_ylabel("Conditional empirical cumulative fraction")
                ax.set_title(FEATURES[i],loc="left");ax.grid(alpha=.2)
                if i==0:ax.legend(frameon=False,fontsize=8,loc="lower right")
            pending=sum(stage_bucket(r)=="Pending" for r in allrows)
            other=len(allrows)-len(rows)-pending
            selected_valid=sum(r["selected_before_emx"] for r in rows)
            header(fig,f"Conditional strict-valid residual ECDF — {f} GHz — {scope}",f"R={n} distinct requests contribute valid candidates. Each curve uses its own observed valid denominator; not the original-frame attainment.",
                data["source_pin"]["sha256"],f"Original220 slots: strict-valid={len(rows)}, failed/invalid={other}, pending={pending}. Preselected valid={selected_valid}/20 original requests.\nExcluded failure/pending slots are not zero errors. Curves describe observed strict-valid residuals, not whole-frame success or population accuracy.\nQ candidates within a request are correlated; no IID assumption, distribution confidence band, smoothing or cross-scope pooling.")
            exports+=export(fig,out,f"f{f:02d}_conditional_strict_valid_ecdf")
            fig,axes=plt.subplots(2,2,figsize=(16,10));fig.subplots_adjust(left=.075,right=.975,top=.83,bottom=.16,wspace=.2,hspace=.35)
            for i,ax in enumerate(axes.flat):
                for selected,color,label,style in ((False,BLUE,"all candidates /220 original slots","-"),(True,ORANGE,"q_proxy /20 original requests","--")):
                    subset=[r for r in rows if not selected or r["selected_before_emx"]]
                    errors=sorted(abs(r["emx_minus_target"][i]) for r in subset if number(r["emx_minus_target"][i]) is not None)
                    ax.step(errors,np.arange(1,len(errors)+1)/(20 if selected else 220),where="post",color=color,linestyle=style,label=label)
                ax.set_ylim(0,1);ax.set_xlim(left=0);ax.set_xlabel("Absolute residual "+LABELS[i]);ax.set_ylabel("Fraction of original denominator")
                ax.set_title(FEATURES[i],loc="left");ax.grid(alpha=.2)
                if i==0:ax.legend(frameon=False,fontsize=8)
            header(fig,f"Residual attainment — {f} GHz — {scope}",f"Strict-valid source requests R={n}; failed and pending slots retain original220/20 denominators. Curves need not reach1.",
                data["source_pin"]["sha256"],"This is finite-frame residual attainment, not an IID conditional populationCDF; no smoothing or distribution confidence band.\nTarget-relative percentages are not used. Cross-frequency/model causal inference is not supported.")
            exports+=export(fig,out,f"f{f:02d}_residual_attainment")
        if n>=20 and varying:
            fig,axes=plt.subplots(2,2,figsize=(16,11));fig.subplots_adjust(left=.075,right=.95,top=.83,bottom=.15,wspace=.3,hspace=.35)
            for i,ax in enumerate(axes.flat):
                good=[r for r in rows if number(r["actual"][i]) is not None]
                x=[r["target"][i] for r in good];y=[r["actual"][i] for r in good]
                im=ax.hexbin(x,y,gridsize=12,mincnt=1,cmap="Blues",linewidths=.2)
                lo,hi=min(x+y),max(x+y);ax.plot([lo,hi],[lo,hi],color=INK,linestyle="--",linewidth=.8)
                ax.set_xlabel("Target "+LABELS[i]);ax.set_ylabel("Actual EMX "+LABELS[i]);ax.set_title(FEATURES[i]+f" | n={len(good)}",loc="left")
                fig.colorbar(im,ax=ax,label="Observed candidate count")
            header(fig,f"Target and actual response bin counts — {f} GHz — {scope}",f"Unsmoothed strict-valid candidate counts; R={n} distinct fixed requests. Shared request candidates are correlated.",
                data["source_pin"]["sha256"],"All-candidate descriptive support, not selected-design populationaccuracy; dashed line is target=actual. No fitted density or regression/R².\nFailures/pending excluded from numeric bins only and retained in the status panel/original220 denominator.")
            exports+=export(fig,out,f"f{f:02d}_target_actual_counts")
    return exports,availability


def build(stats, out, previous=None, reuse_request_figures=None):
    data=load_snapshot(stats);out=Path(out).resolve();require(not out.exists(),"no-clobber figures output already exists")
    reuse=previous_reuse(previous,data) if previous else {}
    if reuse_request_figures:reuse.update(legacy_reuse(reuse_request_figures,data))
    out.mkdir(parents=True)
    save_json(out/"INPUTS.json",dict(statistics=data["source_pin"],table_pins=[data["files"][name] for name in data["files"]],renderer=pin(__file__)))
    exports=route_status(data,out)
    for estimand in ("selected_q_proxy","all_candidates"):
        exports+=mae_p95(data,out,FORMAL,estimand)
    save_json(out/"DEVELOPMENT15_METRICS_TABLE.json",dict(source=data["files"]["METRICS.csv"],
        rows=[r for r in data["metrics"] if r["dataset_scope"]==DEVELOPMENT],
        reason="Only one development frequency: exact physical-unit metrics table plus original first15 Q figures; no underpowered one-point frequency chart."))
    exports+=heatmap(data,out)
    request_figures=[]
    for request in data["requests"]:
        rid=request["request_id"];rows=data["grouped"][rid]
        if request["status"] != "ACCOUNTED":continue
        if rid in reuse:
            request_figures.append(reuse[rid]);continue
        require(rid!=FIRST15,"first15 already has figures; pass its explicit reuse receipt instead of redrawing")
        target=out/"requests"/rid;target.mkdir(parents=True)
        save_json(target/"SOURCE_ROWS.json",dict(request=request,rows=rows))
        pins=request_q_plots(data,rows,request,target)
        request_figures.append(dict(request_id=rid,signature=request_signature(rows),exports=pins,source=pin(target/"SOURCE_ROWS.json"),reused_from=None))
    extra,availability=distribution_plots(data,out);exports+=extra
    exports+=selection_plots(data,out)
    save_json(out/"DISTRIBUTION_AVAILABILITY.json",availability)
    save_json(out/"SELECTION_COMPARISON_TABLE.json",dict(source=data["files"]["SELECTION_COMPARISON.csv"],rows=data["selection"],
        rule="Common original11 strict-valid set only. n=0 is NOT_AVAILABLE, never a survivor optimum."))
    save_json(out/"FIGURE_CONTRACT.json",dict(schema="frequency_physical_statistics_figures_contract.v1",source=data["source_pin"],
        features=FEATURES,score_scale=SCALE,absolute_tolerances=TAU,scope="formal/development separate",palette="blue/orange+neutral; explicit hatches/openmarkers",
        denominator="320requests/3520logicalcandidate slots; selected_q_proxy distinct from all_candidates",formats=["PNG300dpi","SVG","PDF"],
        inference_or_simulation=False,automated_layout_check="figure-level text within page; manual visualQA is a separate receipt",availability=availability))
    for p in data["files"].values():verify(p)
    verify(data["source_pin"])
    files=[pin(p) for p in sorted(out.rglob("*")) if p.is_file()]
    marker=out/".FIGURES_RECEIPT.pending.json"
    save_json(marker,dict(schema="frequency_physical_statistics_figures_receipt.v1",status="COMPLETE",created_utc=utc_now(),
        statistics=data["source_pin"],renderer=pin(__file__),aggregate_exports=exports,request_figures=request_figures,artifacts=files,
        N_original_requests=320,N_original_candidates=3520,N_accounted_requests=len(request_figures),
        visual_qa="NOT_RUN_AUTOMATIC_RENDER_ONLY; manual review must be reported separately",source_data_modified=False,model_calls=False,remote_calls=False))
    os.link(marker,out/"FIGURES_RECEIPT.json")
    marker.unlink()
    return pin(out/"FIGURES_RECEIPT.json")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats",required=True);parser.add_argument("--out",required=True)
    parser.add_argument("--previous");parser.add_argument("--reuse-request-figures")
    args=parser.parse_args()
    print(json.dumps(build(args.stats,args.out,args.previous,args.reuse_request_figures),sort_keys=True))


if __name__=="__main__":main()
