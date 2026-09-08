"""Synthetic train-only Q coverage tests; no model or real prepared data."""
import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from research.broadband56_nn import frequency_q_train_support as module
from research.broadband56_nn.data import FREQUENCY_HZ, Y_COLUMNS, SPLIT_NAMES, _split_for_hash


@pytest.fixture
def bundle():
    hashes = np.array([hashlib.sha256(f"q-support-synthetic-{i}".encode()).hexdigest() for i in range(60)])
    split = np.array([_split_for_hash(h, 17) for h in hashes], dtype=np.int8)
    y = np.ones((60, 56, 4), dtype=np.float64)
    y[:, :, 2] = 12.0
    arrays = dict(geometry_ids=np.array([f"g{i}" for i in range(60)]), geometry_sha256=hashes,
        geometry=np.ones((60, 10)), frequency_hz=FREQUENCY_HZ.copy(), split=split, y=y,
        y_valid=np.ones_like(y, dtype=bool), strict_lumped_valid=np.ones((60, 56), dtype=bool),
        broadband_descriptor_valid=np.ones((60, 56), dtype=bool))
    manifest = dict(unique_geometries=60, geometry_fields=[f"g{i}" for i in range(10)],
        target_columns=list(Y_COLUMNS), split_counts={name:int((split == i).sum()) for i,name in enumerate(SPLIT_NAMES)})
    splits = dict(seed=17, by_geometry_sha256={h:SPLIT_NAMES[int(s)] for h,s in zip(hashes, split)})
    return SimpleNamespace(arrays=arrays, manifest=manifest, splits=splits)


def test_half_open_edges_tails_and_full_denominator(bundle):
    train = np.flatnonzero(bundle.arrays["split"] == 0)
    bundle.arrays["y"][train[:7], :, 2] = np.array([9.49, 9.5, 10.49, 10.5, 20.499, 20.5, 21.0])[:, None]
    result = module.count_support(bundle)
    assert len(result["frequencies"]) == 16 and len(result["cells"]) == 176
    f = result["frequencies"][0]
    cells = result["cells"][:11]
    assert f["below_window_n"] == 1 and f["above_or_equal_window_n"] == 2
    assert cells[0]["local_train_n"] == 2 and cells[1]["local_train_n"] == 1 and cells[-1]["local_train_n"] == 1
    assert sum(c["local_train_n"] for c in cells) + 3 == f["eligible_train_n"] == len(train)
    assert cells[0]["fraction_of_eligible_train"] == 2 / len(train)
    assert all(c["count_status"] == ("EMPTY" if c["local_train_n"] == 0 else "COUNT_ONLY") for c in cells)


def test_heldout_values_cannot_change_distribution_or_mask_input(bundle):
    original = module.count_support(bundle)
    bundle.arrays["y"][bundle.arrays["split"] != 0] = np.nan
    with patch.object(module.profile_api, "frequency_mask", wraps=module.profile_api.frequency_mask) as mask:
        assert module.count_support(bundle) == original
    assert all(call.args[0].arrays["y"].shape[0] == int((bundle.arrays["split"] == 0).sum()) for call in mask.call_args_list)


def test_original_strict_validity_all_four_mask_and_finite_required(bundle):
    train = np.flatnonzero(bundle.arrays["split"] == 0)
    bundle.arrays["strict_lumped_valid"][train[0], 0] = False
    bundle.arrays["y_valid"][train[1], 0, 1] = False
    bundle.arrays["y"][train[2], 0, 3] = np.nan
    result = module.count_support(bundle)
    assert result["frequencies"][0]["eligible_train_n"] == len(train) - 3
    assert result["frequencies"][1]["eligible_train_n"] == len(train)


def test_nominal_range_separate_from_positive_neighborhood_count(bundle):
    bundle.arrays["y"][:, :, 2] = 10.2
    row = module.count_support(bundle)["cells"][0]
    assert row["local_train_n"] > 0 and row["count_status"] == "COUNT_ONLY"
    assert row["nominal_q_in_marginal_range"] is False


def test_empty_train_keeps_null_fractions_and_no_range(bundle):
    bundle.arrays["strict_lumped_valid"][:] = False
    result = module.count_support(bundle)
    assert all(c["local_train_n"] == 0 and c["count_status"] == "EMPTY"
               and c["fraction_of_eligible_train"] is None and c["nominal_q_in_marginal_range"] is None
               for c in result["cells"])


def metadata(result):
    models = {}
    rows = []
    for row in result["frequencies"]:
        f, n = row["frequency_ghz"], row["eligible_train_n"]
        models[f] = dict(model_id=f"synthetic{f}", frequency_ghz=f, label_mode="STRICT_LUMPED", formal_10k=True,
            source_snapshot_geometries=10000, dataset_sha256="a"*64,
            roles={role:dict(eligible_rows={"train":{"eligible_geometries":n}}) for role in ("forward","inverse")},
            support={side:row[side] for side in ("train_min","train_max")})
        distribution = {name:{"min":row["train_min"][i],"max":row["train_max"][i]} for i,name in enumerate(Y_COLUMNS)}
        rows.append(dict(frequency_ghz=f, label_modes={"STRICT_LUMPED":dict(splits={"train":{"eligible":n}},train_distribution=distribution)}))
    profile = dict(schema="bb_frequency_data_profile.v1", snapshot_unique_geometries=10000,
        source_identity={"dataset":{"sha256":"a"*64}},rows=rows)
    return profile, models


@pytest.mark.parametrize("change", ["count", "range", "development", "dataset", "frequency"])
def test_crosscheck_rejects_incompatible_registered_metadata(bundle, change):
    result = module.count_support(bundle)
    profile, models = metadata(result)
    assert len(module.crosscheck(result, profile, models, "a"*64)) == 16
    models = copy.deepcopy(models)
    if change == "count": models[20]["roles"]["forward"]["eligible_rows"]["train"]["eligible_geometries"] += 1
    if change == "range": models[20]["support"]["train_max"][2] += .01
    if change == "development": models[20]["source_snapshot_geometries"] = 5000
    if change == "dataset": models[20]["dataset_sha256"] = "b"*64
    if change == "frequency": models[20]["frequency_ghz"] = 19
    with pytest.raises(ValueError): module.crosscheck(result, profile, models, "a"*64)


def test_exact_contract_rejected_before_dataset_load(tmp_path):
    contract = tmp_path / "contract.json"
    contract.write_text('{"schema":"synthetic_wrong_contract"}')
    with patch.object(module.profile_api,"load_profile_bundle") as loader:
        with pytest.raises(ValueError, match="exact frozen"):
            module._load_inputs(contract, "unused", "unused")
        loader.assert_not_called()


def test_no_clobber_before_any_input_read(tmp_path):
    with patch.object(module,"_load_inputs") as loader:
        with pytest.raises(ValueError, match="no-clobber"):
            module.build("unused", "unused", "unused", tmp_path)
        loader.assert_not_called()


def test_failure_is_retained_without_complete_receipt(tmp_path):
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"source":{"data_root":str(tmp_path / "immutable")}}))
    out = tmp_path / "out"
    with patch.object(module,"_load_inputs",side_effect=ValueError("synthetic admission failure")):
        with pytest.raises(ValueError,match="synthetic admission"):
            module.build(contract,"unused","unused",out)
    assert (out / "Q_SUPPORT_FAILED.json").is_file()
    assert not (out / "Q_SUPPORT_RECEIPT.json").exists()


def test_heatmap_exact_counts_and_noncolor_out_flag_without_adequacy(bundle):
    bundle.arrays["y"][:, :, 2] = 10.2
    result = module.count_support(bundle)
    fig = module.make_figure(result,"a"*64,"b"*64)
    try:
        ax = fig.axes[0]
        assert len(ax.texts) == 176
        assert ax.texts[0].get_text() == f"{result['frequencies'][0]['eligible_train_n']}\nOUT"
        assert all("n=" in tick.get_text() for tick in ax.get_yticklabels())
        assert "NOT an error tolerance" in " ".join(text.get_text() for text in fig.texts)
        np.testing.assert_array_equal(ax.images[0].get_array(), np.array([c["local_train_n"] for c in result["cells"]]).reshape(16,11))
    finally:
        import matplotlib.pyplot as plt
        plt.close(fig)
