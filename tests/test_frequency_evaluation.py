"""Numerical and static-export synthetic checks; no real dataset/model execution."""
import csv
import json
from pathlib import Path

import numpy as np
import pytest

from research.broadband56_nn import frequency_evaluation as ev
from research.broadband56_nn.io import save_json, canonical_sha


def test_forward_physical_metrics_and_r2():
    truth=np.arange(80,dtype=float).reshape(20,4)/10+1
    metrics=ev.regression_metrics(truth,truth)
    for row in metrics.values():
        assert row["mae"]==row["rmse"]==0
        assert row["r2"]==1 and row["fixed_denominator"]==20


def test_r2_constant_not_fabricated():
    truth=np.ones((20,4))
    for row in ev.regression_metrics(truth,truth).values():
        assert row["r2"] is None
        assert row["r2_status"].startswith("NOT_APPLICABLE")


def test_failed_prediction_retains_full_denominator_and_tail():
    truth=np.ones((20,4));prediction=truth.copy();prediction[-2:]=np.nan
    result=ev.inverse_metrics(truth,prediction,np.ones(20,dtype=bool))
    assert result["joint_hit_count"]==18
    assert result["joint_hit_denominator"]==20
    assert result["joint_hit_rate"]==.9
    assert result["max_declared_span_error_p95"] is None
    assert result["conditional_evaluable_max_error_p95"]==0
    assert result["features"]["Lp_nH"]["mae"] is None
    assert result["features"]["Lp_nH"]["evaluable_denominator"]==18


def test_analytically_infeasible_response_cannot_count_as_hit():
    truth=np.ones((2,4));feasible=np.array([True,False])
    result=ev.inverse_metrics(truth,truth,feasible)
    assert result["joint_hit_rate"]==.5 and result["target_failure_count"]==1


def test_declared_span_is_not_target_relative_percentage():
    truth=np.ones((2,4))*.01
    prediction=truth+np.asarray(ev.SPANS)*.04
    result=ev.inverse_metrics(truth,prediction,np.ones(2,dtype=bool))
    assert result["joint_hit_rate"]==1
    assert np.max(np.abs((prediction-truth)/truth))>1
    assert "NOT target-relative" in ev.protocol_identity()["tolerance_definition"]


@pytest.mark.parametrize("fault",["float_mask","wrong_shape","nonfinite_target"])
def test_invalid_metric_contracts_rejected(fault):
    target=np.ones((3,4));prediction=target.copy()
    with pytest.raises(ValueError):
        if fault=="float_mask":ev.regression_metrics(target,prediction,np.ones((3,4)))
        elif fault=="wrong_shape":ev.regression_metrics(target,prediction[:2])
        else:
            target[0,0]=np.nan
            ev.regression_metrics(target,prediction)


def test_test_gate_precedes_any_output_or_bundle_loading(tmp_path,monkeypatch):
    monkeypatch.setattr(ev,"_identity",lambda *a:{"identity":"synthetic"})
    with pytest.raises(ValueError,match="sealed test"):
        ev.evaluate_frequency("NOT_READ","NOT_READ","NOT_READ",tmp_path/"out",split="test")
    assert not (tmp_path/"out").exists()


def test_freeze_exact_identity_and_no_clobber(tmp_path,monkeypatch):
    identity={"identity":"synthetic"}
    monkeypatch.setattr(ev,"_identity",lambda *a:identity)
    path=tmp_path/"freeze.json"
    ev.freeze_frequency_evaluation(None,None,None,path)
    assert ev._verify_test_freeze(path,identity)["sha256"]==ev.pin(path)["sha256"]
    with pytest.raises(ValueError):ev._verify_test_freeze(path,{"other":"identity"})
    with pytest.raises(FileExistsError):ev.freeze_frequency_evaluation(None,None,None,path)


def synthetic_evaluation(root):
    root.mkdir()
    truth=np.arange(80,dtype=float).reshape(20,4)/40+1
    prediction=truth+np.asarray(ev.SPANS)*.01
    ids=[f"synthetic-{i:03d}" for i in range(20)]
    rows=[];inverse=[]
    for i,target_id in enumerate(ids):
        row={"target_id":target_id}
        for j,name in enumerate(ev.FEATURES):
            row["truth__"+name]=truth[i,j];row["prediction__"+name]=prediction[i,j]
        rows.append(row)
        for mode in ("continuous","grid"):
            item={"target_id":target_id,"mode":mode}
            item.update({"error__"+name:prediction[i,j]-truth[i,j] for j,name in enumerate(ev.FEATURES)})
            inverse.append(item)
    ev._csv(root/"forward_predictions.csv",rows);ev._csv(root/"inverse_predictions.csv",inverse)
    identity={"dataset":{"sha256":"SYNTHETIC_ONLY"},"forward_checkpoint":{"sha256":"forward"},"inverse_checkpoint":{"sha256":"inverse"}}
    summary={"schema":"frequency_evaluation_summary.v1","status":"COMPLETE_DESCRIPTIVE_EVALUATION",
        "real_emx_validation":"NOT_RUN","data_evidence":"SYNTHETIC_TEST_ONLY","target_count":20,
        "target_id_order_sha256":canonical_sha(ids),"source_snapshot_geometries":20,"source_split_geometries":20,
        "frequency_ghz":15,"label_mode":"STRICT_LUMPED","split":"validation","geometry_dimension":10,
        "identity":identity,"forward":{"features":ev.regression_metrics(truth,prediction)},
        "inverse":{mode:ev.inverse_metrics(truth,prediction,np.ones(20,dtype=bool)) for mode in ("continuous","grid")},
        "model_metadata":{role:{"step":8} for role in ("forward","inverse")},
        "artifacts":{name:ev.pin(root/name) for name in ("forward_predictions.csv","inverse_predictions.csv")}}
    save_json(root/"EVALUATION_SUMMARY.json",summary)
    history_paths=[]
    for role in ("forward","inverse"):
        h=root/role;h.mkdir()
        save_json(h/"history.json",[{"step":i,"train_loss":1/i,"validation_loss":1.1/i if i%2==0 else None} for i in range(1,17)])
        save_json(h/"TRAINING_RECEIPT.json",{"role":role,"data_sha":"SYNTHETIC_ONLY","best_sha256":role,"completed_step":16})
        history_paths.append(h/"history.json")
    return history_paths


def test_static_exports_use_saved_csv_and_do_not_claim_visual_qa(tmp_path):
    pytest.importorskip("matplotlib")
    from research.broadband56_nn.frequency_figures import render_frequency_figures
    h=synthetic_evaluation(tmp_path/"evaluation")
    result=render_frequency_figures(tmp_path/"evaluation",*h,tmp_path/"figures")
    assert result["status"]=="EXPORTED_PENDING_VISUAL_QA"
    assert len(result["files"])==15
    assert {Path(p["path"]).suffix for p in result["files"]}=={".svg",".pdf",".png"}
    assert all(ev.pin(p["path"])==p for p in result["files"])
    assert "SYNTHETIC TEST ONLY" in (tmp_path/"figures/forward_scatter.svg").read_text()


def test_corrupt_chart_source_rejected_before_output(tmp_path):
    from research.broadband56_nn.frequency_figures import render_frequency_figures
    h=synthetic_evaluation(tmp_path/"evaluation")
    with (tmp_path/"evaluation/forward_predictions.csv").open("a") as stream:stream.write("corruption\n")
    with pytest.raises(ValueError,match="identity changed"):
        render_frequency_figures(tmp_path/"evaluation",*h,tmp_path/"figures")
    assert not (tmp_path/"figures").exists()


def synthetic_profile(path):
    rows=[]
    for frequency in range(5,61):
        modes={}
        for mode in ("STRICT_LUMPED","POINTWISE_DESCRIPTOR_EXPERIMENTAL"):
            eligible=0 if mode=="STRICT_LUMPED" and frequency>20 else 10
            modes[mode]={"eligible_count":eligible,
                "splits":{name:{"eligible":int(eligible*fraction)} for name,fraction in (("train",.6),("validation",.2),("test",.2))},
                "train_distribution":{name:({"min":1,"p5":1.1,"p50":1.5,"p95":1.9,"max":2} if eligible else None)
                    for name in ("lp_nh","ls_nh","qmin","k_abs")}}
        rows.append({"frequency_ghz":frequency,"total_unique_geometries":10,"label_modes":modes})
    save_json(path,{"schema":"bb_frequency_data_profile.v1","rows":rows})


def test_profile_preserves_missing_frequency_extent(tmp_path,monkeypatch):
    pytest.importorskip("matplotlib")
    from research.broadband56_nn import frequency_figures as figures
    source=tmp_path/"synthetic_profile.json";synthetic_profile(source)
    captured={}
    def capture(fig,out,name):
        captured[name]=[axis.get_xlim() for axis in fig.axes]
        return []
    monkeypatch.setattr(figures,"_export",capture)
    result=figures.render_frequency_profile(source,tmp_path/"figures",expected_sha256=ev.pin(source)["sha256"])
    assert result["status"]=="EXPORTED_PENDING_VISUAL_QA"
    assert captured["frequency_train_ranges"]==[(4.,61.)]*8
    assert len(captured["frequency_label_counts"])==2
    assert result["source"]==ev.pin(source)


def test_profile_wrong_pin_rejected_before_output(tmp_path):
    from research.broadband56_nn.frequency_figures import render_frequency_profile
    source=tmp_path/"synthetic_profile.json";synthetic_profile(source)
    with pytest.raises(ValueError,match="SHA differs"):
        render_frequency_profile(source,tmp_path/"figures",expected_sha256="0"*64)
    assert not (tmp_path/"figures").exists()
