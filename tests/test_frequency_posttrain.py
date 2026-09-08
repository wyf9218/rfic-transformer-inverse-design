"""Mocked native post-training stages; no model/data/EMX work is performed."""
from pathlib import Path

import pytest

from research.broadband56_nn import frequency_posttrain as post
from research.broadband56_nn.io import save_json, sha256, canonical_sha


def write(path, value):
    save_json(path, value)
    return path


def index(root):
    with (root / "SHA256SUMS.txt").open("x") as stream:
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.name != "SHA256SUMS.txt":
                stream.write(sha256(p)+"  "+str(p.relative_to(root))+"\n")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "study"; root.mkdir()
    data = tmp_path / "data"; data.mkdir()
    manifest = write(data / "data_manifest.json", {"artifacts": {"dataset.npz": {"sha256": "a"*64}}})
    recipe = write(tmp_path / "recipe.json", {"synthetic": True})
    request = {"schema": "frequency_study_request.v1", "out": str(root),
        "posttrain": "LOAD_RESUME_PROFILE_EVALUATE_PLOT_PACKAGE", "contract_path": str(recipe),
        "legacy_replay_receipt": str(recipe), "legacy_replay_sha256": sha256(recipe),
        "train": {"frequency_ghz": 15, "label_mode": "STRICT_LUMPED", "device": "cpu",
                  "deadline_utc": "2099-01-01T00:00:00Z"}}
    request_path = write(root / "request.json", request)
    roles = {}
    for role in ("forward", "inverse"):
        path = write(root / role / "TRAINING_RECEIPT.json", {"synthetic": True})
        checkpoint = path.parent / "checkpoint.pt"; checkpoint.write_bytes(b"synthetic_not_a_model")
        write(path.parent / "history.json", {"synthetic": True})
        roles[role] = {"receipt": post.pin(path), "best": post.pin(checkpoint), "last": post.pin(checkpoint)}
    pair = {"schema": "frequency_pair_receipt.v1", "status": "TRAINED_BUDGET_OR_EARLY_STOP",
        "frequency_ghz": 15, "label_mode": "STRICT_LUMPED", "request": post.pin(request_path),
        "roles": roles, "data_root": str(data)}
    write(root / "PAIR_RECEIPT.json", pair)
    calls = []
    protocol = {"schema": "synthetic_protocol"}
    monkeypatch.setattr(post, "protocol_identity", lambda: protocol)
    identity = {"dataset": {"sha256": "a"*64}, "frequency_ghz": 15, "label_mode": "STRICT_LUMPED",
        "protocol_sha256": canonical_sha(protocol), **{r+"_checkpoint": roles[r]["best"] for r in roles}}

    def acceptance(data, f, i, out, *args, **kw):
        calls.append("acceptance")
        assert kw["deadline_utc"] == request["train"]["deadline_utc"]
        assert kw["training_budget_sha256"] == sha256(request_path)
        assert kw["lock_fds"] == (123, 456)
        results = {r: {"status": "PASS", "checks": {k: True for k in post.ACCEPTANCE_CHECKS},
            **{"original_"+s+"_sha256": roles[r][s]["sha256"] for s in ("best", "last")}} for r in roles}
        write(out / "BB00_LOAD_RESUME_RECEIPT.json", {"schema": "bb00_load_resume_proof.v1", "status": "PASS",
            "results": results, "original_pins": {}, "data_sha": "a"*64,
            "original_checkpoint_bytes_unchanged": True, "effective_deadline_utc": kw["deadline_utc"],
            "training_budget_sha256": kw["training_budget_sha256"]})

    def profile(data, out, **kw):
        calls.append("profile")
        write(out / "frequency_data_profile.json", {"synthetic": True})
        write(out / "PROFILE_RECEIPT.json", {"schema": "bb_frequency_profile_receipt.v1",
            "status": "PASS_DATA_PROFILE_ONLY", "source_dataset_sha256": "a"*64,
            "source_data_manifest_sha256": sha256(manifest)})
        index(out)

    def evaluate(data, f, i, out, **kw):
        calls.append(kw["split"])
        write(out / "EVALUATION_SUMMARY.json", {"schema": "frequency_evaluation_summary.v1",
            "status": "COMPLETE_DESCRIPTIVE_EVALUATION", "identity": identity,
            "split": kw["split"], "artifacts": {},
            "configuration_freeze": post.pin(kw["configuration_freeze"]) if "configuration_freeze" in kw else None})
        index(out)

    def freeze(data, f, i, out, **kw):
        calls.append("freeze")
        write(out, {"status": "FROZEN", "identity": identity,
                    "validation_summary": post.pin(kw["validation_summary"])})

    def figures(out, schema):
        calls.append(schema)
        chart = write(out / "CHART_CONTRACTS.json", {"synthetic": True})
        write(out / "FIGURE_MANIFEST.json", {"schema": schema, "status": "EXPORTED_PENDING_VISUAL_QA",
            "files": [], "chart_contract": post.pin(chart)})
        index(out)

    def package(pair, profile, evaluation, acceptance, out):
        calls.append("package")
        write(out / "PACKAGE_RECEIPT.json", {"schema": "frequency_package_receipt.v1",
            "status": "PORTABLE_INFERENCE_PACKAGE_READY", "inputs": {
                "pair": post.pin(pair), "profile": post.pin(profile), "acceptance": post.pin(acceptance)}})
        index(out)

    monkeypatch.setattr(post, "verify_bb00_load_resume", acceptance)
    monkeypatch.setattr(post, "profile_prepared_data", profile)
    monkeypatch.setattr(post, "evaluate_frequency", evaluate)
    monkeypatch.setattr(post, "freeze_frequency_evaluation", freeze)
    monkeypatch.setattr(post, "render_frequency_figures", lambda ev, f, i, out: figures(out, "frequency_figures.v1"))
    monkeypatch.setattr(post, "render_frequency_profile", lambda pr, out, **kw: figures(out, "frequency_profile_figures.v1"))
    monkeypatch.setattr(post, "package_model", package)
    return request, root, calls


def test_complete_once_and_hash_checked_reuse(setup):
    request, root, calls = setup
    result = post.finalize_pair(request, root, (123, 456))
    assert result["status"] == "ARTIFACTS_READY_VISUAL_QA_PENDING"
    assert result["budget_renewed"] is False and result["REAL_EMX_VALIDATION"] == "NOT_RUN"
    assert calls == ["acceptance", "profile", "validation", "freeze", "test", "frequency_figures.v1",
                     "frequency_profile_figures.v1", "package"]
    before = list(calls)
    assert post.finalize_pair(request, root, (123, 456)) == result
    assert calls == before


def test_incomplete_existing_stage_never_retries(setup):
    request, root, calls = setup
    # First mocked profile exits like parent death, after acceptance was saved.
    output = root / "posttrain"
    original = post.profile_prepared_data
    def crash(*args, **kwargs):
        Path(args[1]).mkdir()
        raise SystemExit("simulated parent interruption")
    post.profile_prepared_data = crash
    try:
        with pytest.raises(SystemExit):
            post.finalize_pair(request, root, (123, 456))
    finally:
        post.profile_prepared_data = original
    with pytest.raises(ValueError, match="incomplete existing stage"):
        post.finalize_pair(request, root, (123, 456))
    assert calls == ["acceptance"] and (output / "POSTTRAIN_FAILED.json").is_file()
    with pytest.raises(ValueError, match="previous post-training failure"):
        post.finalize_pair(request, root, (123, 456))
    assert calls == ["acceptance"]


def test_completed_acceptance_survives_parent_interruption(setup, monkeypatch):
    request, root, calls = setup
    original = post.profile_prepared_data
    monkeypatch.setattr(post, "profile_prepared_data", lambda *a, **k: (_ for _ in ()).throw(SystemExit("crash before output")))
    with pytest.raises(SystemExit):
        post.finalize_pair(request, root, (123, 456))
    monkeypatch.setattr(post, "profile_prepared_data", original)
    result = post.finalize_pair(request, root, (123, 456))
    assert result["status"] == "ARTIFACTS_READY_VISUAL_QA_PENDING" and calls.count("acceptance") == 1


def test_changed_completed_artifact_is_not_recomputed(setup):
    request, root, calls = setup
    post.finalize_pair(request, root, (123, 456))
    target = root / "posttrain/profile/frequency_data_profile.json"
    target.write_text("tampered")
    before = list(calls)
    with pytest.raises(ValueError, match="identity changed"):
        post.finalize_pair(request, root, (123, 456))
    assert calls == before


def test_other_frequency_cannot_use_15ghz_acceptance(setup):
    request, root, calls = setup
    request["train"]["frequency_ghz"] = 10
    with pytest.raises(ValueError, match="strict 15 GHz"):
        post.finalize_pair(request, root)
    assert calls == [] and not (root / "posttrain").exists()


def test_incomplete_stage_hash_index_is_not_accepted(setup, monkeypatch):
    request, root, calls = setup
    original = post.profile_prepared_data
    def incomplete(*args, **kwargs):
        original(*args, **kwargs)
        (Path(args[1]) / "SHA256SUMS.txt").write_text("")
    monkeypatch.setattr(post, "profile_prepared_data", incomplete)
    with pytest.raises(ValueError, match="complete artifact set"):
        post.finalize_pair(request, root, (123, 456))
    assert calls == ["acceptance", "profile"]
