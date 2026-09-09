"""Pure synthetic mirror/label tests: NOT full-chain QA and NOT real EMX.

No model, S-parameter extraction, external tool, live export or native process.
"""
from copy import deepcopy
import csv
import hashlib
import io
import math

import pytest

from research.broadband56_nn import eucap15_acquisition_evidence as evidence


def pin_bytes(path, raw):
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def mirror_fixture(tmp_path, raw=b'{"synthetic":true}\n'):
    root = tmp_path.resolve()
    original = root / "SYNTHETIC_NATIVE_ABSENT" / "artifact.json"
    local = root / "mirror.opaque"
    local.write_bytes(raw)
    return pin_bytes(original, raw), local, {str(original): str(local)}


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def labels_fixture(*, source="SPARSE_TARGETED", actual=None, target=None, half_srf=True, physics=True):
    actual = [1., 1.5, 13., .5] if actual is None else list(actual)
    targeted = source == "SPARSE_TARGETED"
    wanted = ([1., 1.5, 13., .5] if target is None else list(target)) if targeted else None
    proxy = [1.01, 1.49, 13.1, .49] if targeted else None
    proposal = {"source": source, "target": wanted, "proxy": proxy, "q_proxy": 13 if targeted else None}
    finite = all(math.isfinite(a) for a in actual)
    row = dict(zip(("lp_nh", "ls_nh", "qmin", "k_abs"), actual))
    row.update(qp=actual[2], qs=actual[2]+1, signed_k=-actual[3],
        finite_values=finite, positive_primary_resistance=True, positive_secondary_resistance=True,
        positive_primary_inductive_reactance=True, positive_secondary_inductive_reactance=True,
        broadband_descriptor_valid=finite, strict_lumped_valid=finite and half_srf, below_half_srf=half_srf,
        passivity_status="PASS" if physics else "FAIL", reciprocity_status="PASS")
    rows = [{"frequency_hz": f*10**9, **row} for f in range(5,61)]
    valid = bool(finite and half_srf and physics)
    errors = [a-t for a,t in zip(actual,wanted)] if targeted else None
    proxy_errors = [a-p for a,p in zip(actual,proxy)] if targeted else None
    hits = [math.isfinite(e) and abs(e)<=t for e,t in zip(errors,[.125,.125,1.,.04])] if targeted else None
    score = math.sqrt(sum((e/s)**2 for e,s in zip(errors,[2.5,2.5,20.,.8]))/4) if finite and targeted else None
    feature = clean(dict(original_frequency_row=rows[10], original_56_summary=dict(port_count=4,
        frequency_points=56,frequency_start_hz=5*10**9,frequency_stop_hz=60*10**9,frequency_step_hz=10**9),
        actual_fresh_emx=actual,target=wanted,proxy_self=proxy,emx_minus_target=errors,emx_minus_proxy=proxy_errors,
        normalized_response_score=score,within_tolerance=hits,joint_response_hit=all(hits) if targeted else None,
        descriptor_valid=finite,strict_lumped_valid=finite and half_srf,physics_qa_pass=physics,
        valid_for_strict_comparison=valid,strict_joint_hit=bool(all(hits) and valid) if targeted else None,
        target_relative_signed_percent=[100*e/t for e,t in zip(errors,wanted)] if targeted else None,
        target_relative_absolute_percent=[100*abs(e)/t for e,t in zip(errors,wanted)] if targeted else None,
        target_errors_defined=targeted,core15_eligible=bool(valid and .5<=actual[0]<=2 and .5<=actual[1]<=2 and .2<=actual[3]<=.85),
        q10_to20_supported=bool(finite and 10<=actual[2]<=20)))
    return proposal, feature, rows


def csv_bytes(rows, *, fields=None):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0]), lineterminator="\n")
    writer.writeheader(); writer.writerows(rows)
    return stream.getvalue().encode()


def test_mirror_exact_identity_and_caller_map_snapshot(tmp_path):
    expected, local, mapping = mirror_fixture(tmp_path)
    reader = evidence.MirrorReader(mapping)
    mapping[expected["path"]] = str(tmp_path.resolve()/"must_not_be_used")
    assert reader.document(expected) == {"synthetic": True}
    assert reader.mapped_pin(expected["path"]) == expected
    assert reader.evidence[expected["path"]] == {"original": expected, "resolved": dict(expected,path=str(local))}
    reader.recheck()


@pytest.mark.parametrize("kind", ["sha", "bytes", "missing", "malformed_sha", "bool_size"])
def test_mirror_rejects_bad_pins_or_missing_exact_map(tmp_path, kind):
    expected, local, mapping = mirror_fixture(tmp_path)
    if kind == "sha": expected["sha256"] = "0"*64
    elif kind == "bytes": expected["bytes"] += 1
    elif kind == "missing": mapping = {}
    elif kind == "malformed_sha": expected["sha256"] = "G"*64
    else: expected["bytes"] = True
    with pytest.raises(ValueError):
        evidence.MirrorReader(mapping).read(expected)


@pytest.mark.parametrize("kind", ["file", "parent"])
def test_mirror_symlink_rejected(tmp_path, kind):
    expected, local, mapping = mirror_fixture(tmp_path)
    link = tmp_path.resolve()/"link"
    link.symlink_to(local if kind == "file" else local.parent, target_is_directory=kind == "parent")
    mapping[expected["path"]] = str(link if kind == "file" else link/local.name)
    with pytest.raises(ValueError,match="symlink"):
        evidence.MirrorReader(mapping).read(expected)


def test_mirror_conflicting_pin_for_same_original_rejected(tmp_path):
    expected, local, mapping = mirror_fixture(tmp_path)
    reader=evidence.MirrorReader(mapping);reader.read(expected)
    other=b'{"synthetic":false}\n';local.write_bytes(other)
    with pytest.raises(ValueError,match="Conflicting source pin"):
        reader.read(pin_bytes(expected["path"],other))


def test_mirror_recheck_rejects_later_mutation(tmp_path):
    expected, local, mapping=mirror_fixture(tmp_path)
    reader=evidence.MirrorReader(mapping);reader.read(expected);local.write_bytes(b"changed\n")
    with pytest.raises(ValueError,match="SHA/size"):
        reader.recheck()


def test_mirror_mid_read_mutation_rejected(tmp_path,monkeypatch):
    expected,local,mapping=mirror_fixture(tmp_path)
    real_pin=evidence.pin
    def mutate_then_pin(path):
        local.write_bytes(b"changed during read\n")
        return real_pin(path)
    monkeypatch.setattr(evidence,"pin",mutate_then_pin)
    with pytest.raises(ValueError,match="changed during read"):
        evidence.MirrorReader(mapping).read(expected)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":1e999}'])
def test_mirror_strict_json(tmp_path,raw):
    expected,_,mapping=mirror_fixture(tmp_path,raw)
    with pytest.raises(ValueError):
        evidence.MirrorReader(mapping).document(expected)


def test_targeted_labels_exact_and_readonly():
    proposal,feature,rows=labels_fixture(actual=[1.02,1.48,13.2,.52])
    before=deepcopy((proposal,feature,rows))
    result=evidence.verify_labels(feature,proposal,csv_bytes(rows))
    assert result['strict_valid'] and result['core_eligible'] and result['strict_joint_hit']
    assert result['actual']==[1.02,1.48,13.2,.52] and result['target_errors_defined']
    assert (proposal,feature,rows)==before


@pytest.mark.parametrize("source",["GEOMETRY_DOE","EXPLORATION"])
def test_untargeted_valid_labels_keep_all_target_errors_null(source):
    proposal,feature,rows=labels_fixture(source=source)
    result=evidence.verify_labels(feature,proposal,csv_bytes(rows))
    assert result['strict_valid'] and result['core_eligible']
    assert result['strict_joint_hit'] is result['emx_minus_target'] is result['emx_minus_proxy'] is None
    assert result['target_errors_defined'] is False


@pytest.mark.parametrize("field",["target","proxy","q_proxy"])
def test_doe_cannot_acquire_invented_target_or_q(field):
    proposal,feature,rows=labels_fixture(source='GEOMETRY_DOE')
    proposal[field]=13 if field=='q_proxy' else [1.,1.5,13.,.5]
    with pytest.raises(ValueError,match='no target'):
        evidence.verify_labels(feature,proposal,csv_bytes(rows))


@pytest.mark.parametrize("kind",["length","frequency","duplicate_columns","original_row"])
def test_exact56_integrity_rejections(kind):
    proposal,feature,rows=labels_fixture();fields=None
    if kind=='length':rows=rows[:-1]
    elif kind=='frequency':rows[0]['frequency_hz']=6*10**9
    elif kind=='duplicate_columns':fields=[*rows[0],next(iter(rows[0]))]
    else:rows[10]['lp_nh']+=.01
    with pytest.raises(ValueError):
        evidence.verify_labels(feature,proposal,csv_bytes(rows,fields=fields))


@pytest.mark.parametrize("field,value,match",[
    ('qmin',14.,'Qmin'),('signed_k',-.6,'Absolute k'),
    ('positive_primary_resistance',False,'Descriptor'),('below_half_srf',False,'SRF/strict')])
def test_semantic_predicate_rejections_not_just_stale_original_row(field,value,match):
    proposal,feature,rows=labels_fixture()
    rows[10][field]=value;feature['original_frequency_row']=deepcopy(rows[10])
    with pytest.raises(ValueError,match=match):
        evidence.verify_labels(feature,proposal,csv_bytes(rows))


@pytest.mark.parametrize("field",['emx_minus_target','emx_minus_proxy','target_relative_absolute_percent','normalized_response_score'])
def test_saved_error_or_score_mismatch_rejected(field):
    proposal,feature,rows=labels_fixture()
    if isinstance(feature[field],list):feature[field][0]+=.001
    else:feature[field]+=.001
    with pytest.raises(ValueError,match='Reconciled15'):
        evidence.verify_labels(feature,proposal,csv_bytes(rows))


def test_new_exact_point_zero_four_not_original64_legacy_float():
    proposal,feature,rows=labels_fixture(actual=[1.,1.5,13.,.25],target=[1.,1.5,13.,.21])
    delta=.25-.21
    assert delta>.04 and delta<=.04000000000000001
    result=evidence.verify_labels(feature,proposal,csv_bytes(rows))
    assert result['strict_valid'] and result['strict_joint_hit'] is False
    feature['within_tolerance'][-1]=True;feature['joint_response_hit']=feature['strict_joint_hit']=True
    with pytest.raises(ValueError,match='within_tolerance'):
        evidence.verify_labels(feature,proposal,csv_bytes(rows))


@pytest.mark.parametrize("actual,core,q_support",[
    ([.5,.5,10.,.2],True,True),([2.,2.,20.,.85],True,True),
    ([math.nextafter(.5,-math.inf),1.,13.,.5],False,True),
    ([1.,math.nextafter(2.,math.inf),13.,.5],False,True),
    ([1.,1.5,13.,math.nextafter(.85,math.inf)],False,True),
    ([1.,1.5,9.,.5],True,False)])
def test_core_domain_inclusive_edges_and_q_is_separate(actual,core,q_support):
    proposal,feature,rows=labels_fixture(source='GEOMETRY_DOE',actual=actual)
    result=evidence.verify_labels(feature,proposal,csv_bytes(rows))
    assert result['strict_valid'] is True and result['core_eligible'] is core
    assert result['q10_to20_supported'] is q_support


@pytest.mark.parametrize("half_srf,physics",[(False,True),(True,False)])
def test_invalid_never_promoted_by_q_support_or_exact_target(half_srf,physics):
    proposal,feature,rows=labels_fixture(half_srf=half_srf,physics=physics)
    result=evidence.verify_labels(feature,proposal,csv_bytes(rows))
    assert result['q10_to20_supported'] is True
    assert result['strict_valid'] is result['core_eligible'] is result['strict_joint_hit'] is False


def test_nonfinite_csv_keeps_json_null_and_invalid():
    proposal,feature,rows=labels_fixture(actual=[1.,1.5,math.nan,.5])
    result=evidence.verify_labels(feature,proposal,csv_bytes(rows))
    assert result['actual'][2] is None
    assert not result['strict_valid'] and not result['core_eligible'] and not result['strict_joint_hit']
