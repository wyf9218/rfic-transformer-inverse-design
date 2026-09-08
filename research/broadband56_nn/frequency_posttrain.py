"""Ordinary strict 5..20 GHz post-training completion with existing tools.

Completed stages are hash-checked and reused. Existing incomplete directories
are never retried, replaced or assigned a new attempt number. The caller holds
the study/device leases; diagnostic acceptance inherits those descriptors.
"""
from pathlib import Path

from .io import canonical_sha, read_json, sha256, utc_now
from .study_once import atomic_json, pin, verify_pin
from .bb00_delivery import verify_bb00_load_resume
from .frequency_profile import profile_prepared_data
from .frequency_evaluation import freeze_frequency_evaluation, evaluate_frequency, protocol_identity
from .frequency_figures import render_frequency_figures, render_frequency_profile
from .frequency_package import package_model, ACCEPTANCE_CHECKS
from .frequency_audit_hook import prepare_requested_audit


def _files(root):
    result = []
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            raise ValueError("post-training evidence may not contain symlinks")
        if path.is_file():
            result.append(pin(path))
    return result


def _verify_index(root):
    index = Path(root) / "SHA256SUMS.txt"
    if not index.is_file():
        raise ValueError("completed stage lacks SHA256SUMS")
    recorded = set()
    for line in index.read_text().splitlines():
        digest, relative = line.split("  ", 1)
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts or relative in recorded:
            raise ValueError("stage index escapes its evidence directory")
        recorded.add(relative)
        verify_pin({"path": str(Path(root)/rel), "sha256": digest})
    actual = {str(Path(item["path"]).relative_to(Path(root).resolve())) for item in _files(root)
              if Path(item["path"]) != index.resolve()}
    if recorded != actual:
        raise ValueError("stage SHA256SUMS does not cover the complete artifact set")


def finalize_pair(request, root, lock_fds=()):
    """Finish one existing pair; never renew its deadline or claim visual QA."""
    study = Path(root).resolve()
    frequency = request["train"]["frequency_ghz"]
    label_mode = request["train"]["label_mode"]
    if (request.get("posttrain") != "LOAD_RESUME_PROFILE_EVALUATE_PLOT_PACKAGE" or
            type(frequency) is not int or not 5 <= frequency <= 20 or label_mode != "STRICT_LUMPED"):
        raise ValueError("post-training automation requires strict integer frequency 5..20 GHz")
    if Path(request["out"]).resolve() != study:
        raise ValueError("post-training root differs from the existing study")
    pair_path = study / "PAIR_RECEIPT.json"
    pair = read_json(pair_path)
    if (pair.get("schema") != "frequency_pair_receipt.v1" or
            pair.get("status") != "TRAINED_BUDGET_OR_EARLY_STOP" or
            pair.get("frequency_ghz") != frequency or pair.get("label_mode") != label_mode):
        raise ValueError("qualified existing trained pair required")
    if read_json(verify_pin(pair["request"])) != request:
        raise ValueError("pair is not bound to the same immutable request")
    for role in ("forward", "inverse"):
        for name in ("receipt", "best", "last"):
            verify_pin(pair["roles"][role][name])
    data = Path(pair["data_root"])
    data_manifest = read_json(data / "data_manifest.json")
    dataset_sha = data_manifest["artifacts"]["dataset.npz"]["sha256"]
    identity = {"pair": pin(pair_path), "request": pair["request"],
        "data_manifest": pin(data / "data_manifest.json"), "dataset_sha256": dataset_sha,
        "posttrain_source_sha256": sha256(__file__), "deadline_utc": request["train"]["deadline_utc"]}
    audit_request = study / "LARGE_EVALUATION_REQUEST.json"
    if audit_request.exists():
        identity["large_evaluation_request"] = pin(audit_request)
        identity["pretest_audit_hook"] = pin(Path(__file__).with_name("frequency_audit_hook.py"))
    output = study / "posttrain"
    final = output / "POSTTRAIN_RECEIPT.json"
    if final.exists():
        result = read_json(final)
        if result.get("identity") != identity or result.get("status") != "ARTIFACTS_READY_VISUAL_QA_PENDING":
            raise ValueError("post-training completion identity differs")
        for item in result["artifacts"]:
            verify_pin(item)
        return result
    output.mkdir(parents=True, exist_ok=True)
    failed = output / "POSTTRAIN_FAILED.json"
    if failed.exists():
        raise ValueError("previous post-training failure requires explicit inspection; no automatic retry")
    binding = output / "POSTTRAIN_INPUT.json"
    progress_path = output / "POSTTRAIN_PROGRESS.json"
    if binding.exists():
        if read_json(binding) != identity:
            raise ValueError("existing post-training inputs changed")
    else:
        if any(output.iterdir()):
            raise ValueError("existing unbound post-training outputs require reconciliation")
        atomic_json(binding, identity, immutable=True)
    progress = read_json(progress_path) if progress_path.exists() else {"identity": identity, "stages": {}}
    if progress["identity"] != identity:
        raise ValueError("post-training progress belongs to another pair")

    def stage(name, directory, receipt_name, schema, status, call, validate, *, indexed=True):
        receipt_path = directory / receipt_name
        if name in progress["stages"]:
            for item in progress["stages"][name]["artifacts"]:
                verify_pin(item)
        elif directory.exists():
            # A function may finish before the parent journals its completion.
            # Reuse only a complete native receipt and its own verified index.
            if not receipt_path.is_file():
                raise ValueError("incomplete existing stage; no retry: " + name)
        else:
            call()
        value = read_json(receipt_path)
        if value.get("schema") != schema or value.get("status") != status:
            raise ValueError("stage lacks qualified completion: " + name)
        if indexed:
            _verify_index(directory)
        validate(value)
        if name not in progress["stages"]:
            progress["stages"][name] = {"receipt": pin(receipt_path), "artifacts": _files(directory)}
            atomic_json(progress_path, progress)
        return receipt_path

    def acceptance_valid(value):
        if (value.get("data_sha") != dataset_sha or value.get("original_checkpoint_bytes_unchanged") is not True or
                value.get("frequency_ghz", 15) != frequency or value.get("label_mode", "STRICT_LUMPED") != label_mode or
                value.get("effective_deadline_utc") != identity["deadline_utc"] or
                value.get("training_budget_sha256") != pair["request"]["sha256"]):
            raise ValueError("load/resume proof data or original deadline differs")
        for role in ("forward", "inverse"):
            result = value["results"][role]
            if (result.get("status") != "PASS" or not ACCEPTANCE_CHECKS.issubset(result["checks"]) or
                    not all(v is True for v in result["checks"].values()) or
                    any(result["original_"+s+"_sha256"] != pair["roles"][role][s]["sha256"] for s in ("best", "last"))):
                raise ValueError("load/resume role/checkpoint proof differs")
        for path, digest in value["original_pins"].items():
            verify_pin({"path": path, "sha256": digest})

    forward = pair["roles"]["forward"]["best"]["path"]
    inverse = pair["roles"]["inverse"]["best"]["path"]

    def evaluation_valid(value, split):
        observed = value["identity"]
        if (value["split"] != split or observed["dataset"]["sha256"] != dataset_sha or
                observed["frequency_ghz"] != frequency or observed["label_mode"] != label_mode or
                observed["protocol_sha256"] != canonical_sha(protocol_identity()) or
                any(observed[r+"_checkpoint"]["sha256"] != pair["roles"][r]["best"]["sha256"]
                    for r in ("forward", "inverse"))):
            raise ValueError("evaluation is not the exact completed pair/protocol")
        for item in value["artifacts"].values():
            verify_pin(item)
        if split == "test" and value["configuration_freeze"] != pin(freeze_path):
            raise ValueError("test result is not bound to this freeze")

    def figures_valid(value):
        for item in value["files"]:
            verify_pin(item)
        verify_pin(value["chart_contract"])

    try:
        acceptance_dir = output / "acceptance"
        acceptance = stage("acceptance", acceptance_dir, "BB00_LOAD_RESUME_RECEIPT.json", "bb00_load_resume_proof.v1", "PASS",
            lambda: verify_bb00_load_resume(data, pair["roles"]["forward"]["receipt"]["path"],
                pair["roles"]["inverse"]["receipt"]["path"], acceptance_dir, request["contract_path"],
                request["legacy_replay_receipt"], request["legacy_replay_sha256"], request["train"]["device"],
                lock_fds=lock_fds, deadline_utc=identity["deadline_utc"], training_budget_sha256=pair["request"]["sha256"],
                frequency_ghz=frequency, label_mode=label_mode),
            acceptance_valid, indexed=False)
        profile_dir = output / "profile"
        def profile_valid(value):
            if value["source_dataset_sha256"] != dataset_sha or value["source_data_manifest_sha256"] != identity["data_manifest"]["sha256"]:
                raise ValueError("frequency profile snapshot differs")
        stage("profile", profile_dir, "PROFILE_RECEIPT.json", "bb_frequency_profile_receipt.v1", "PASS_DATA_PROFILE_ONLY",
              lambda: profile_prepared_data(data, profile_dir, extractor_source=request.get("extractor_source")), profile_valid)
        profile = profile_dir / "frequency_data_profile.json"
        # User-authorized supplement: exact IDs/window/model/tolerance first,
        # then existing scoring. This does not change training or production.
        prepare_requested_audit(study, pair, profile)
        evaluation = output / "evaluation"
        validation_dir = evaluation / "validation"
        validation = stage("validation", validation_dir, "EVALUATION_SUMMARY.json", "frequency_evaluation_summary.v1", "COMPLETE_DESCRIPTIVE_EVALUATION",
            lambda: evaluate_frequency(data, forward, inverse, validation_dir, split="validation", device=request["train"]["device"],
                frequency_ghz=frequency, label_mode=label_mode),
            lambda value: evaluation_valid(value, "validation"))
        freeze_path = evaluation / "TEST_CONFIGURATION_FREEZE.json"
        if not freeze_path.exists():
            freeze_frequency_evaluation(data, forward, inverse, freeze_path, validation_summary=validation,
                frequency_ghz=frequency, label_mode=label_mode)
        freeze = read_json(freeze_path)
        if (freeze.get("status") != "FROZEN" or freeze["identity"] != read_json(validation)["identity"] or
                freeze["validation_summary"] != pin(validation)):
            raise ValueError("existing test configuration freeze differs")
        test_dir = evaluation / "test"
        stage("test", test_dir, "EVALUATION_SUMMARY.json", "frequency_evaluation_summary.v1", "COMPLETE_DESCRIPTIVE_EVALUATION",
            lambda: evaluate_frequency(data, forward, inverse, test_dir, split="test", device=request["train"]["device"],
                frequency_ghz=frequency, label_mode=label_mode,
                configuration_freeze=freeze_path), lambda value: evaluation_valid(value, "test"))
        figures = output / "figures"
        histories = [str(Path(pair["roles"][r]["receipt"]["path"]).parent / "history.json") for r in ("forward", "inverse")]
        stage("model_figures", figures / "model", "FIGURE_MANIFEST.json", "frequency_figures.v1", "EXPORTED_PENDING_VISUAL_QA",
              lambda: render_frequency_figures(test_dir, *histories, figures / "model"), figures_valid)
        stage("profile_figures", figures / "profile", "FIGURE_MANIFEST.json", "frequency_profile_figures.v1", "EXPORTED_PENDING_VISUAL_QA",
              lambda: render_frequency_profile(profile, figures / "profile", expected_sha256=sha256(profile)), figures_valid)
        def package_valid(value):
            if (value["inputs"]["pair"] != pin(pair_path) or value["inputs"]["profile"] != pin(profile) or
                    value["inputs"]["acceptance"] != pin(acceptance)):
                raise ValueError("portable package sources differ")
        stage("package", output / "package", "PACKAGE_RECEIPT.json", "frequency_package_receipt.v1", "PORTABLE_INFERENCE_PACKAGE_READY",
              lambda: package_model(pair_path, profile, evaluation, acceptance, output / "package"), package_valid)
        result = {"schema": "frequency_posttrain_receipt.v1", "status": "ARTIFACTS_READY_VISUAL_QA_PENDING",
            "identity": identity, "stages": progress["stages"], "artifacts": _files(output),
            "visual_qa": "NOT_RUN_REQUIRES_HUMAN_OR_INDEPENDENT_IMAGE_INSPECTION",
            "REAL_EMX_VALIDATION": "NOT_RUN", "budget_renewed": False, "created_utc": utc_now()}
        atomic_json(final, result, immutable=True)
        return result
    except Exception as error:
        if not failed.exists():
            atomic_json(failed, {"schema": "frequency_posttrain_failure.v1", "status": "FAILED_NO_AUTOMATIC_RETRY",
                "identity": identity, "error": type(error).__name__+": "+str(error),
                "completed_stages": list(progress["stages"]), "created_utc": utc_now()}, immutable=True)
        raise
