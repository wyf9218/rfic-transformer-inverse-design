import pytest
from research.broadband56_nn.eucap15_author_sources import history_rows, request_rows, load_json, export

def test_history_missing_validation_remains_empty():
    rows = history_rows([{"step":1,"train_loss":.2}, {"step":2,"validation_loss":.3}], "forward")
    assert rows[0]["validation_loss"] is None
    assert rows[1]["train_loss"] is None
    assert len(rows) == 2
    assert rows[1]["loss_comparability"] == "WITHIN_ROLE_ONLY_NOT_PHYSICAL_UNITS"

def test_history_duplicate_step_rejected():
    with pytest.raises(ValueError, match="non-increasing"):
        history_rows([{"step":1}, {"step":1}], "inverse")

def base_row():
    return dict(request_id="synthetic", candidate_id="synthetic-q10", q_proxy="10",
                state="ANALYTIC_FAIL", strict_joint_hit="", failure_detail="synthetic failure",
                touchstone_sha="", target="[1,1,10,0.5]", grid_proxy="[1,1,10,0.5]", actual="")

def test_failure_retained_without_emx_imputation():
    row = request_rows([base_row()])[0]
    assert row["N_original"] == 64
    assert row["state"] == "ANALYTIC_FAIL"
    assert row["emx_Qmin"] is None
    assert row["target_Qmin"] == 10

def test_non_strict_descriptor_retained_but_excluded_from_strict_plot():
    row = base_row()
    row["state"] = "EMX_INVALID"
    row["actual"] = "[1,1,10,0.5]"
    result = request_rows([row])[0]
    assert result["emx_Qmin"] == 10
    assert result["strict_plot_eligible"] is False
    assert result["actual_interpretation"] == "DESCRIPTOR_OR_MISSING_NOT_STRICT"

def test_duplicate_request_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        request_rows([base_row(), base_row()])

def test_json_duplicate_key_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        load_json('{"a":1,"a":2}')

def test_existing_output_is_never_overwritten(tmp_path):
    spec = tmp_path/"spec.json"
    spec.write_text('{"schema":"eucap15_author_source_spec.v1","private_author_package":true,"sources":[]}')
    out = tmp_path/"already"
    out.mkdir()
    marker = out/"keep"
    marker.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        export(spec, out)
    assert marker.read_bytes() == b"original"
