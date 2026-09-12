"""Only new public path-configuration guards; no old kernel or data replay."""
import pytest

from research.broadband56_nn.eucap15_geometry_proposals import configured_inputs


def sample():
    item={"path":"/synthetic/not_read.json","sha256":"a"*64}
    return {"schema":"eucap15_geometry_proposal_public_intent.v2",
        "inputs":{key:dict(item) for key in ("contract","splits","current_source_rows",
                    "exclusion_metadata","prior_members31","prior_proposals64")},
        "sources":{"production_geometry_helpers":dict(item)}}


def test_explicit_six_source_pins_do_not_assume_repository_private_data():
    cfg=sample()
    assert configured_inputs(cfg) is cfg["inputs"]


def test_old_frozen_intent_schema_not_silently_reinterpreted():
    cfg=sample();cfg["schema"]="eucap15_production_doe_neighborhood_intent.v1"
    with pytest.raises(ValueError,match="public-v2"):
        configured_inputs(cfg)


def test_missing_private_source_is_not_defaulted():
    cfg=sample();del cfg["inputs"]["current_source_rows"]
    with pytest.raises(ValueError,match="Exact six"):
        configured_inputs(cfg)


def test_relative_source_path_or_missing_helper_is_rejected():
    cfg=sample();cfg["inputs"]["contract"]["path"]="contract.json"
    with pytest.raises(ValueError,match="Absolute source"):
        configured_inputs(cfg)
    cfg=sample();cfg["sources"]={}
    with pytest.raises(ValueError,match="helper"):
        configured_inputs(cfg)
