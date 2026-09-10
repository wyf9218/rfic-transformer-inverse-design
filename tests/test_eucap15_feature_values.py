"""Synthetic label arithmetic only; no native evidence or model execution."""
from copy import deepcopy
import csv
import io
import math

import pytest

from research.broadband56_nn.eucap15_feature_values import validate_feature_values
from research.broadband56_nn.eucap15_selected_evidence import SelectedEvidenceError
from tests.fixtures.eucap15_selected_evidence_fixture import SyntheticEvidence


@pytest.fixture
def saved(tmp_path):
    chain = SyntheticEvidence(tmp_path)
    return chain.read("feature"), chain.paths["csv"].read_bytes(), {
        "wanted": chain.wanted, "proxy": chain.proxy, "scale": chain.scale, "tau": chain.tau}


def rewrite(raw, change):
    reader = csv.DictReader(io.StringIO(raw.decode(), newline=""))
    rows = list(reader)
    change(rows)
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=reader.fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


def test_strict_and_input_immutability_without_native_io(saved, monkeypatch):
    feature, raw, args = saved
    before = deepcopy(feature), raw, deepcopy(args)
    def forbidden(*a, **kw):
        raise AssertionError("pure validator must not open a file")
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("pathlib.Path.open", forbidden)
    result = validate_feature_values(feature, raw, **args)
    assert result == {"actual": [1.02, 1.48, 13.2, .52],
        "valid_for_strict_comparison": True, "strict_joint_hit": True, "status": "STRICT_VALID"}
    assert (feature, raw, args) == before


@pytest.mark.parametrize("kwargs, valid, hit", [
    ({"below_half_srf": False}, False, False), ({"physics": False}, False, False),
    ({"actual": [1.3, 1.48, 13.2, .52]}, True, False),
    ({"actual": [math.nan, 1.48, 13.2, .52]}, False, False),
    ({"actual": [1.02, 1.48, math.nan, .52]}, False, False),
    ({"actual": [1.02, 1.48, 13.2, math.inf]}, False, False),
])
def test_invalid_values_preserved_not_promoted(tmp_path, kwargs, valid, hit):
    chain = SyntheticEvidence(tmp_path, **kwargs)
    feature = chain.read("feature")
    result = validate_feature_values(feature, chain.paths["csv"].read_bytes(),
        wanted=chain.wanted, proxy=chain.proxy, scale=chain.scale, tau=chain.tau)
    assert result["actual"] == feature["actual_fresh_emx"]
    assert result["valid_for_strict_comparison"] is valid
    assert result["strict_joint_hit"] is hit
    assert result["status"] == ("STRICT_VALID" if valid else "EMX_INVALID")


@pytest.mark.parametrize("name", ["wanted", "proxy", "scale", "tau"])
@pytest.mark.parametrize("bad", [[1., 2., 3.], [True, 1., 1., 1.],
    [float("nan"), 1., 1., 1.], [float("inf"), 1., 1., 1.]])
def test_four_finite_numeric_arguments_required(saved, name, bad):
    feature, raw, args = saved
    args[name] = bad
    with pytest.raises(SelectedEvidenceError):
        validate_feature_values(feature, raw, **args)


@pytest.mark.parametrize("name", ["wanted", "scale", "tau"])
@pytest.mark.parametrize("value", [0., -1.])
def test_positive_contract_arguments_required(saved, name, value):
    feature, raw, args = saved
    args[name][0] = value
    with pytest.raises(SelectedEvidenceError):
        validate_feature_values(feature, raw, **args)


@pytest.mark.parametrize("key", ["actual_fresh_emx", "emx_minus_target", "emx_minus_proxy",
    "normalized_response_score", "within_tolerance", "joint_response_hit", "descriptor_valid",
    "strict_lumped_valid", "physics_qa_pass", "valid_for_strict_comparison", "strict_joint_hit",
    "target_relative_signed_percent", "target_relative_absolute_percent", "target", "proxy_self",
    "score_scale", "absolute_hit_tolerances"])
def test_saved_claim_drift_rejected(saved, key):
    feature, raw, args = saved
    value = feature[key]
    if isinstance(value, bool):
        feature[key] = not value
    elif isinstance(value, list):
        value[0] = not value[0] if isinstance(value[0], bool) else value[0] + .01
    else:
        feature[key] = value + .01
    with pytest.raises(SelectedEvidenceError):
        validate_feature_values(feature, raw, **args)


@pytest.mark.parametrize("kind", ["row_missing", "duplicate_frequency", "wrong_start", "duplicate_column",
    "missing_cell", "extra_cell", "bad_utf8", "not_bytes", "summary_drift", "original_drift"])
def test_original56_shape_and_original15_identity(saved, kind):
    feature, raw, args = saved
    if kind == "row_missing":
        raw = rewrite(raw, lambda rows: rows.pop())
    elif kind in ("duplicate_frequency", "wrong_start"):
        raw = rewrite(raw, lambda rows: rows[0].update(frequency_hz="6000000000"))
    elif kind == "duplicate_column":
        lines = raw.decode().splitlines()
        fields = lines[0].split(",")
        fields[-1] = fields[-2]
        lines[0] = ",".join(fields)
        raw = ("\n".join(lines) + "\n").encode()
    elif kind in ("missing_cell", "extra_cell"):
        lines = raw.decode().splitlines()
        lines[1] = lines[1].rsplit(",", 1)[0] if kind == "missing_cell" else lines[1] + ",unexpected"
        raw = ("\n".join(lines) + "\n").encode()
    elif kind == "bad_utf8":
        raw = b"\xff"
    elif kind == "not_bytes":
        raw = raw.decode()
    elif kind == "summary_drift":
        feature["original_56_summary"]["frequency_points"] = 55
    else:
        feature["original_frequency_row"]["lp_nh"] += .1
    with pytest.raises(SelectedEvidenceError):
        validate_feature_values(feature, raw, **args)


@pytest.mark.parametrize("name, value", [("qp", 5.), ("qs", 5.), ("signed_k", -.8),
    ("positive_primary_resistance", False), ("below_half_srf", False),
    ("reciprocity_status", "FAIL")])
def test_coherent_csv_original_row_cannot_hide_derived_drift(saved, name, value):
    feature, raw, args = saved
    feature["original_frequency_row"][name] = value
    raw = rewrite(raw, lambda rows: rows[10].update({name: str(value)}))
    with pytest.raises(SelectedEvidenceError):
        validate_feature_values(feature, raw, **args)


def test_signed_k_positive_and_csv_line_ending_are_not_changed_labels(saved):
    feature, raw, args = saved
    feature["original_frequency_row"]["signed_k"] = .52
    raw = rewrite(raw, lambda rows: rows[10].update(signed_k="0.52"))
    assert validate_feature_values(feature, raw, **args)["status"] == "STRICT_VALID"
    assert validate_feature_values(feature, raw.replace(b"\n", b"\r\n"), **args)["status"] == "STRICT_VALID"


def test_missing_claim_and_bool_substitution_fail_closed(saved):
    feature, raw, args = saved
    feature["strict_joint_hit"] = 1
    with pytest.raises(SelectedEvidenceError):
        validate_feature_values(feature, raw, **args)
    del feature["strict_joint_hit"]
    with pytest.raises(SelectedEvidenceError):
        validate_feature_values(feature, raw, **args)
