"""Reproducible static advisor figures from pinned single-frequency CSV/JSON.

No model inference, fitting or data generation. Outputs are white-background
SVG/PDF/300dpi PNG. A chart contract is written before rendering; actual visual
review is deliberately NOT claimed by the renderer.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
import textwrap

import numpy as np

from .io import canonical_sha, read_json, save_json, sha256, utc_now
from .frequency_evaluation import FEATURES, UNITS, SPANS, pin, verify_pin, regression_metrics

BLUE = "#2864DC"
ORANGE = "#D97718"
INK = "#24292F"
GRID = "#E4E7EB"


def _plotting():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family":"DejaVu Sans", "font.size":10, "axes.titlesize":11,
        "figure.facecolor":"white", "axes.facecolor":"white", "text.color":INK,
        "axes.labelcolor":INK,"xtick.color":INK,"ytick.color":INK,"axes.edgecolor":INK,
        "svg.fonttype":"none","pdf.fonttype":42,"savefig.facecolor":"white"})
    return plt


def _rows(path):
    with Path(path).open(newline="",encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _values(rows, prefix):
    return np.asarray([[float(row[prefix+name]) if row[prefix+name] else np.nan
                        for name in FEATURES] for row in rows],dtype=float)


def _history(path, role, summary):
    path=Path(path)
    values=read_json(path)
    receipt_path=path.parent/"TRAINING_RECEIPT.json"
    receipt=read_json(receipt_path)
    checkpoint=summary["identity"][role+"_checkpoint"]
    if receipt.get("role")!=role or checkpoint["sha256"] not in (receipt.get("best_sha256"),receipt.get("last_sha256")):
        raise ValueError("history is not bound to the evaluated role/checkpoint")
    if receipt.get("data_sha")!=summary["identity"]["dataset"]["sha256"]:
        raise ValueError("history data identity differs")
    steps=[row["step"] for row in values]
    if not steps or steps!=sorted(set(steps)):
        raise ValueError("learning history requires unique increasing real update indices")
    if steps[-1]!=receipt.get("completed_step"):
        raise ValueError("learning history does not reach its terminal receipt")
    return values, {"history":pin(path),"training_receipt":pin(receipt_path)}


def _header(fig,title,subtitle,footnote):
    fig.suptitle(title,x=.065,ha="left",fontsize=16,fontweight="bold",y=.985)
    fig.text(.065,.938,subtitle,ha="left",va="top",fontsize=10,color=INK)
    wrapped="\n".join(textwrap.fill(line,width=int(fig.get_figwidth()*12)) for line in footnote.splitlines())
    fig.text(.065,.025,wrapped,ha="left",va="bottom",fontsize=8,color=INK)


def _axes(ax):
    ax.spines[["top","right"]].set_visible(False)
    ax.grid(axis="y",color=GRID,linewidth=.6,zorder=0)


def _export(fig,out,name):
    files=[]
    for extension in ("svg","pdf","png"):
        path=out/(name+"."+extension)
        if path.exists(): raise FileExistsError(path)
        fig.savefig(path,dpi=300,facecolor="white",metadata={"Creator":"frequency_figures"})
        files.append(pin(path))
    return files


def _contracts(summary, sources):
    count=summary["target_count"]
    base={"renderer":"matplotlib static SVG/PDF/300dpi PNG", "surface":"standalone academic advisor figures",
        "palette_policy":"hard two-root cap: blue/orange plus neutral", "palette":{"blue":BLUE,"orange":ORANGE,"ink":INK,"grid":GRID},
        "non_color_encoding":"continuous circle/solid vs grid open-square/dashed; ideal and zero references dark-neutral dotted",
        "grain":"one eligible held-out unique geometry at one integer GHz; inverse two output modes are paired, not two independent samples",
        "fixed_target_denominator":count,"sources":sources,"source_snapshot_geometries":summary["source_snapshot_geometries"],
        "claim_boundary":"Forward uses held-out EM labels; inverse SELF_PROXY only. REAL_EMX_VALIDATION=NOT_RUN; no convergence or physical winner claim.",
        "branding":"Third-party academic research; no OpenAI branding or decorative blossom",
        "final_qa_surface":"exported PNG and PDF; independent visual inspection remains required"}
    return {"schema":"frequency_chart_contracts.v1","created_utc":utc_now(),"common":base,"charts":[
        {"id":"learning_curves","family":"line small multiples","question":"What objective values were actually recorded during forward/inverse updates?",
         "takeaway":"Recorded objectives and checkpoint selection, not evidence of convergence.","sufficiency":"All recorded updates retained; fewer than eight values use markers without connecting lines; missing validation is not interpolated.","footprint_inches":[11,8]},
        {"id":"forward_scatter","family":"scatter small multiples","question":"How do forward predictions compare with actual held-out EM labels?",
         "takeaway":"Four physical-unit prediction relationships with MAE/RMSE/R2.","sufficiency":f"{count} requested observations; nonfinite predictions explicitly counted; fewer than eight finite pairs shown as labeled observations.","footprint_inches":[11,8]},
        {"id":"inverse_residuals","family":"histogram small multiples","question":"What are the paired continuous/grid SELF_PROXY residual distributions?",
         "takeaway":"Residuals retain fixed-denominator misses and the declared-span tolerance.","sufficiency":f"Two paired modes on the same {count} target IDs; only finite residuals histogrammed, omissions annotated.","footprint_inches":[11,8]},
        {"id":"inverse_feasibility_hits","family":"grouped bar","question":"How do analytical feasibility and fixed-span joint hit fractions change under grid export?",
         "takeaway":"Descriptive grid effect, not fresh-EMX accuracy.","sufficiency":"Exact counts/fixed original denominator; bar axis0..100%.","footprint_inches":[9,5.8]},
        {"id":"architecture_routing","family":"code-native flow diagram","question":"How does selected integer frequency route through its independent tandem pair?",
         "takeaway":"Frequency selects the pair and is not a fifth MLP input; forward is frozen for inverse training.","sufficiency":"Actual evaluated architecture metadata; not a quantitative performance plot.","footprint_inches":[12,6]}]}


def render_frequency_figures(evaluation_dir, forward_history, inverse_history, out_dir):
    """Validate pinned saved inputs, then render without loading any model."""
    evaluation_dir=Path(evaluation_dir)
    summary_path=evaluation_dir/"EVALUATION_SUMMARY.json"
    summary=read_json(summary_path)
    if summary.get("schema")!="frequency_evaluation_summary.v1" or summary.get("status")!="COMPLETE_DESCRIPTIVE_EVALUATION":
        raise ValueError("completed frequency evaluation required")
    if summary.get("real_emx_validation")!="NOT_RUN":
        raise ValueError("this renderer only represents the declared no-fresh-EMX evaluation")
    for value in summary["artifacts"].values(): verify_pin(value)
    forward_rows=_rows(verify_pin(summary["artifacts"]["forward_predictions.csv"]))
    inverse_rows=_rows(verify_pin(summary["artifacts"]["inverse_predictions.csv"]))
    ids=[row["target_id"] for row in forward_rows]
    if len(ids)!=summary["target_count"] or len(set(ids))!=len(ids) or canonical_sha(ids)!=summary["target_id_order_sha256"]:
        raise ValueError("forward chart target identity/denominator differs")
    truth,prediction=_values(forward_rows,"truth__"),_values(forward_rows,"prediction__")
    if regression_metrics(truth,prediction)!=summary["forward"]["features"]:
        raise ValueError("forward chart statistics disagree with saved predictions")
    modes={name:[row for row in inverse_rows if row["mode"]==name] for name in ("continuous","grid")}
    if len(inverse_rows)!=2*len(ids) or any([row["target_id"] for row in rows]!=ids for rows in modes.values()):
        raise ValueError("inverse modes do not share the original ordered target frame")
    histories={}; history_pins={}
    for role,path in (("forward",forward_history),("inverse",inverse_history)):
        histories[role],history_pins[role]=_history(path,role,summary)
    sources={"evaluation_summary":pin(summary_path),"evaluation_artifacts":summary["artifacts"],"learning_history":history_pins}
    contracts=_contracts(summary,sources)
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=False)
    save_json(out/"CHART_CONTRACTS.json",contracts)
    plt=_plotting(); exported=[]
    f=summary["frequency_ghz"]; n=summary["target_count"]
    subtitle=f"{f} GHz | {summary['label_mode']} | {summary['split']} eligible n={n:,} / split n={summary['source_split_geometries']:,} | snapshot {summary['source_snapshot_geometries']:,} geometries"
    if summary.get("data_evidence")=="SYNTHETIC_TEST_ONLY": subtitle="SYNTHETIC TEST ONLY | "+subtitle
    source_note="Source EVALUATION_SUMMARY SHA-256 "+sources["evaluation_summary"]["sha256"][:16]+"…; complete source pins in CHART_CONTRACTS.json."
    fig,axes=plt.subplots(2,2,figsize=(11,8))
    for column,role in enumerate(("forward","inverse")):
        for row,key in enumerate(("train_loss","validation_loss")):
            ax=axes[row,column];records=[record for record in histories[role] if record.get(key) is not None]
            x=np.asarray([r["step"] for r in records]);y=np.asarray([r[key] for r in records],float)
            finite=np.isfinite(y)
            ax.plot(x[finite],y[finite],color=BLUE if role=="forward" else ORANGE,
                    marker="o" if role=="forward" else "s",markersize=3,linewidth=1.3,
                    linestyle="-" if len(records)>=8 else "None")
            ax.set(title=role.title()+" — "+key.replace("_"," "),xlabel="Optimizer updates",ylabel="Recorded objective (native units)")
            if not finite.any(): ax.text(.5,.5,"No finite recorded values",transform=ax.transAxes,ha="center")
            if key=="validation_loss": ax.axvline(summary["model_metadata"][role]["step"],color=INK,linestyle=":",linewidth=1,label="Evaluated checkpoint")
            _axes(ax)
    _header(fig,f"{f} GHz recorded learning objectives",subtitle,
        "Separate native train/validation objectives; inverse training weight can vary. No smoothing or inferred convergence.\nDotted validation lines mark evaluated checkpoints. "+source_note)
    fig.subplots_adjust(left=.09,right=.97,top=.855,bottom=.14,hspace=.48,wspace=.3)
    exported+=_export(fig,out,"learning_curves");plt.close(fig)
    fig,axes=plt.subplots(2,2,figsize=(11,8))
    for j,ax in enumerate(axes.flat):
        ok=np.isfinite(prediction[:,j]);x,y=truth[ok,j],prediction[ok,j]
        ax.scatter(x,y,s=13,color=BLUE,alpha=.6,marker="o",edgecolors="none")
        if len(x):
            low=min(x.min(),y.min());high=max(x.max(),y.max());pad=max((high-low)*.05,1e-5)
            ax.plot([low-pad,high+pad],[low-pad,high+pad],color=INK,linestyle=":",linewidth=1)
            ax.set_xlim(low-pad,high+pad);ax.set_ylim(low-pad,high+pad)
        metrics=summary["forward"]["features"][FEATURES[j]]
        fmt=lambda value:"NA" if value is None else f"{value:.4g}"
        ax.set(title=f"{FEATURES[j]} | finite {ok.sum()}/{n}",xlabel=f"Held-out EM label ({UNITS[j]})",ylabel=f"Forward prediction ({UNITS[j]})")
        ax.text(.025,.97,f"MAE {fmt(metrics['mae'])}  RMSE {fmt(metrics['rmse'])}\nR² {fmt(metrics['r2'])}",transform=ax.transAxes,va="top",fontsize=9)
        _axes(ax)
    _header(fig,f"{f} GHz forward predictions versus held-out EM labels",subtitle,
        "Truth is the held-out reference geometry's EM label, not fresh EMX of inverse-generated geometry.\n"+source_note)
    fig.subplots_adjust(left=.1,right=.97,top=.855,bottom=.14,hspace=.47,wspace=.35)
    exported+=_export(fig,out,"forward_scatter");plt.close(fig)
    fig,axes=plt.subplots(2,2,figsize=(11,8))
    residuals={name:_values(rows,"error__") for name,rows in modes.items()}
    for j,ax in enumerate(axes.flat):
        all_finite=np.concatenate([v[np.isfinite(v[:,j]),j] for v in residuals.values()])
        tolerance=.05*SPANS[j]
        bound=max(float(np.abs(all_finite).max()) if len(all_finite) else tolerance,tolerance)*1.05
        bins=np.linspace(-bound,bound,31)
        for name,color,style in (("continuous",BLUE,"-"),("grid",ORANGE,"--")):
            values=residuals[name][:,j];ok=np.isfinite(values)
            ax.hist(values[ok],bins=bins,histtype="step",linewidth=1.4,color=color,linestyle=style,label=f"{name}: finite {ok.sum()}/{n}")
        ax.axvline(0,color=INK,linestyle=":",linewidth=1)
        for sign in (-1,1):ax.axvline(sign*tolerance,color=INK,linestyle="--",linewidth=.9)
        ax.set(title=f"{FEATURES[j]} residual",xlabel=f"SELF_PROXY response − target ({UNITS[j]})",ylabel="Target count")
        ax.legend(fontsize=8);_axes(ax)
    _header(fig,f"{f} GHz inverse SELF_PROXY residual distributions",subtitle,
        "Dashed limits = ±0.05×declared spans [2.5 nH, 2.5 nH, 20, 0.8]; NOT target-relative 5%.\nFinite histograms do not drop failures from hit denominators. REAL_EMX_VALIDATION=NOT_RUN. "+source_note)
    fig.subplots_adjust(left=.09,right=.97,top=.855,bottom=.15,hspace=.45,wspace=.3)
    exported+=_export(fig,out,"inverse_residuals");plt.close(fig)
    fig,ax=plt.subplots(figsize=(9,5.8));x=np.arange(2)
    for name,offset,color,hatch in (("continuous",-.18,BLUE,None),("grid",.18,ORANGE,"//")):
        metrics=summary["inverse"][name]
        values=[metrics["analytical_pass_rate"]*100,metrics["joint_hit_rate"]*100]
        bars=ax.bar(x+offset,values,.34,label=name,color=color,edgecolor=INK,linewidth=.6,hatch=hatch)
        ax.bar_label(bars,labels=[f"{v:.1f}%" for v in values],padding=4,fontsize=10)
    ax.set_xticks(x,["Analytical geometry pass","Joint SELF_PROXY hit"]);ax.set_ylim(0,112);ax.set_ylabel(f"Percent of all {n:,} eligible targets")
    ax.legend(loc="lower center",bbox_to_anchor=(.5,1.01),ncol=2,frameon=False);_axes(ax)
    _header(fig,f"{f} GHz analytical feasibility and joint proxy hits",subtitle,
        "Joint hit: all four absolute errors ≤ 5% of declared spans AND analytical feasibility.\nAnalytical pass is not DRC/manufacturing acceptance. REAL_EMX_VALIDATION=NOT_RUN.\n"+source_note)
    fig.subplots_adjust(left=.1,right=.97,top=.78,bottom=.22)
    exported+=_export(fig,out,"inverse_feasibility_hits");plt.close(fig)
    fig,ax=plt.subplots(figsize=(12,6));ax.set_xlim(0,12);ax.set_ylim(0,6);ax.axis("off")
    from matplotlib.patches import FancyBboxPatch
    d=summary["geometry_dimension"]
    boxes=[(.2,3.5,2,1.2,"Four target values\nLp, Ls, min(Qp,Qs), |K|",BLUE),
        (2.9,3.5,2.6,1.2,f"Inverse_f\n4 → 256 × 3 → {d}\ntanh-approx GELU",ORANGE),
        (6.1,3.5,2.0,1.2,f"One geometry\n{d} fields (µm)\nbounded map",BLUE),
        (8.7,3.5,3,1.2,f"Frozen Forward_f\n{d} → 256 × 3 → 4\nSELF_PROXY diagnostic",BLUE),
        (.5,.8,4.8,1.5,"Forward training\nReal geometry → Forward_f → four EM labels\nValidation selects checkpoint; test is report-only",BLUE),
        (6.0,.8,5.3,1.5,"Inverse tandem training\nFour targets → Inverse_f → frozen Forward_f\nGradient flows through F to I; F weights stay fixed",ORANGE)]
    for x0,y0,w,h,text,color in boxes:
        ax.add_patch(FancyBboxPatch((x0,y0),w,h,boxstyle="round,pad=0.05,rounding_size=0.08",facecolor="white",edgecolor=color,linewidth=1.5))
        ax.text(x0+w/2,y0+h/2,text,ha="center",va="center",fontsize=10,color=INK)
    for left,right in ((2.25,2.83),(5.55,6.03),(8.16,8.63)):
        ax.annotate("",xy=(right,4.1),xytext=(left,4.1),arrowprops={"arrowstyle":"->","color":INK,"lw":1.2})
    ax.text(7.3,5.45,"Integer GHz + label mode: select exact I_f / F_f pair",ha="center",fontsize=10,color=INK)
    for x in (4.2,10.2):
        ax.annotate("",xy=(x,4.8),xytext=(7.3,5.25),arrowprops={"arrowstyle":"->","color":INK,"lw":1.1,"linestyle":"--"})
    ax.text(11.65,5.15,"Dashed = routing only",ha="right",fontsize=8,color=INK)
    ax.text(6,2.8,"Targets: [Lp (nH), Ls (nH), min(Qp,Qs), |K|] — symmetric matching",ha="center",fontsize=11)
    _header(fig,"Frequency-indexed tandem MLP: training and model routing",
        f"Independent model pairs; frequency selects a model and is NOT an additional MLP input | evaluated {f} GHz",
        "No cross-frequency interpolation, hidden spectrum input or joint 56-frequency target. Fresh EMX of generated geometry: NOT_RUN.\n"+source_note)
    fig.subplots_adjust(left=.035,right=.98,top=.86,bottom=.12)
    exported+=_export(fig,out,"architecture_routing");plt.close(fig)
    result={"schema":"frequency_figures.v1","status":"EXPORTED_PENDING_VISUAL_QA","created_utc":utc_now(),
        "chart_contract":pin(out/"CHART_CONTRACTS.json"),"sources":sources,"files":exported,
        "visual_qa":"NOT_PERFORMED_BY_RENDERER; inspect actual exported PNG/PDF before advisor handoff",
        "real_emx_validation":"NOT_RUN","renderer_source":pin(__file__)}
    save_json(out/"FIGURE_MANIFEST.json",result)
    with (out/"SHA256SUMS.txt").open("x") as stream:
        for file in sorted(out.iterdir()):
            if file.is_file() and file.name!="SHA256SUMS.txt":stream.write(sha256(file)+"  "+file.name+"\n")
    return result


def render_frequency_profile(profile_json,out_dir,*,expected_sha256):
    """Draw all 56 committed-data profile rows; never infer unmeasured labels."""
    source=pin(profile_json)
    if source["sha256"]!=expected_sha256:raise ValueError("frequency profile SHA differs")
    profile=read_json(profile_json)
    rows=profile.get("rows",[])
    if profile.get("schema")!="bb_frequency_data_profile.v1" or [row["frequency_ghz"] for row in rows]!=list(range(5,61)):
        raise ValueError("complete, ordered, source-bound 56-frequency profile required")
    totals={row["total_unique_geometries"] for row in rows}
    if len(totals)!=1:raise ValueError("frequency rows do not share a source geometry snapshot")
    total=totals.pop();modes=("STRICT_LUMPED","POINTWISE_DESCRIPTOR_EXPERIMENTAL")
    for row in rows:
        for mode in modes:
            label=row["label_modes"][mode]
            if sum(label["splits"][split]["eligible"] for split in ("train","validation","test"))!=label["eligible_count"]:
                raise ValueError("profile split eligibility counts do not reconcile")
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=False)
    contract={"schema":"frequency_profile_chart_contract.v1","source":source,
        "source_snapshot_geometries":total,"grain":"unique geometry by integer frequency; shared geometry split across all frequencies",
        "evidence":"committed snapshot label inventory and train-only physical distributions; NOT trained-model accuracy",
        "charts":[{"id":"frequency_label_counts","family":"line small multiples","question":"How many labels remain usable within the fixed train/validation/test groups at each frequency?","sufficiency":"All 56 measured frequencies, both original label modes, no interpolation", "footprint_inches":[12,6.5]},
            {"id":"frequency_train_ranges","family":"dot-and-interval small multiples","question":"What are the actual train-only four-feature distributions under the two explicit label modes?","sufficiency":"56 slots retained; missing distributions appear as gaps, not zero or neighboring estimates","footprint_inches":[13,8]}],
        "palette_policy":"blue/orange plus neutral; split markers and dashes; train median blue, p5..p95 orange, min..max dark-neutral",
        "caveats":["Strict labels above30GHz cannot be proven by the original5..60GHz SRF scan; this is not proof that high-frequency S parameters are invalid.",
            "Descriptor is POINTWISE_DESCRIPTOR_EXPERIMENTAL, not a silent strict-mask substitute.",
            "Observed train min/max, percentiles, normalizer scales and configured support windows are different concepts. A bounding box is not joint realizability.",
            "Shared geometry split; frequency rows are not independent geometries; no test-driven support fitting."],
        "visual_qa":"NOT_PERFORMED_BY_RENDERER"}
    save_json(out/"CHART_CONTRACTS.json",contract)
    plt=_plotting();exports=[];freq=np.asarray([row["frequency_ghz"] for row in rows])
    fig,axes=plt.subplots(1,2,figsize=(12,6.5),sharey=True)
    for mode,ax,title in zip(modes,axes,("STRICT_LUMPED","POINTWISE_DESCRIPTOR_EXPERIMENTAL")):
        for split,color,marker,style in (("train",BLUE,"o","-"),("validation",ORANGE,"s","--"),("test",INK,"^",":")):
            counts=[row["label_modes"][mode]["splits"][split]["eligible"] for row in rows]
            ax.plot(freq,counts,color=color,marker=marker,markersize=3,markevery=5,linestyle=style,linewidth=1.3,label=split)
        ax.set(title=title,xlabel="Integer frequency (GHz)",ylabel="Eligible unique geometries")
        ax.set_ylim(bottom=0);ax.legend();_axes(ax)
    _header(fig,"Frequency-specific label counts in fixed geometry groups",
        f"Committed snapshot: {total:,} unique geometries | 56 measured frequency slots | counts are labels, not trained models",
        "Train/validation/test geometry groups stay fixed across frequency. Strict and descriptor modes remain separate.\nProfile SHA-256 "+source["sha256"][:20]+"…; full provenance in CHART_CONTRACTS.json.")
    fig.subplots_adjust(left=.085,right=.97,top=.82,bottom=.19,wspace=.25)
    exports+=_export(fig,out,"frequency_label_counts");plt.close(fig)
    fig,axes=plt.subplots(2,4,figsize=(13,8))
    for i,mode in enumerate(modes):
        for j,name in enumerate(("lp_nh","ls_nh","qmin","k_abs")):
            ax=axes[i,j]
            def series(key):
                values=[]
                for row in rows:
                    stats=row["label_modes"][mode].get("train_distribution") or {}
                    value=(stats.get(name) or {}).get(key)
                    values.append(float(value) if value is not None else np.nan)
                return np.asarray(values)
            low,high,p5,p95,median=(series(key) for key in ("min","max","p5","p95","p50"))
            ax.vlines(freq,low,high,color=INK,linewidth=.6,alpha=.55,label="min..max")
            ax.vlines(freq,p5,p95,color=ORANGE,linewidth=1.5,label="p5..p95")
            ax.plot(freq,median,color=BLUE,marker="o",markersize=2,linestyle="None",label="median")
            mode_short="Strict" if i==0 else "Descriptor (experimental)"
            ax.set(title=mode_short+" — "+FEATURES[j],xlabel="Frequency (GHz)",ylabel=UNITS[j])
            ax.set_xlim(4,61)
            ax.title.set_fontsize(9)
            if not np.isfinite(median).any():ax.text(.5,.5,"NO ELIGIBLE TRAIN LABELS",transform=ax.transAxes,ha="center",fontsize=8)
            if i==0 and j==0:ax.legend(fontsize=8)
            _axes(ax)
    _header(fig,"Frequency-specific train-only physical feature ranges",
        f"Source snapshot {total:,} unique geometries | median, p5..p95, min..max within the original train group",
        "Missing strict-label frequencies stay blank. Marginal ranges are not normalizer scales, configured windows or proven joint support.\nProfile SHA-256 "+source["sha256"][:20]+"…; no model prediction or test-tuned support is used.")
    fig.subplots_adjust(left=.07,right=.98,top=.85,bottom=.15,hspace=.44,wspace=.36)
    exports+=_export(fig,out,"frequency_train_ranges");plt.close(fig)
    result={"schema":"frequency_profile_figures.v1","status":"EXPORTED_PENDING_VISUAL_QA","created_utc":utc_now(),
        "source":source,"files":exports,"chart_contract":pin(out/"CHART_CONTRACTS.json"),"renderer_source":pin(__file__),
        "real_emx_validation":"NOT_RUN","visual_qa":"NOT_PERFORMED_BY_RENDERER"}
    save_json(out/"FIGURE_MANIFEST.json",result)
    with (out/"SHA256SUMS.txt").open("x") as stream:
        for file in sorted(out.iterdir()):
            if file.is_file() and file.name!="SHA256SUMS.txt":stream.write(sha256(file)+"  "+file.name+"\n")
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("evaluation","forward-history","inverse-history","out"):parser.add_argument("--"+name,required=True)
    args=parser.parse_args(argv)
    render_frequency_figures(args.evaluation,args.forward_history,args.inverse_history,args.out)


if __name__=="__main__":main()
