"""Read-only identity gate for reusing completed seven-study evaluations.

This checks existing evidence, not prediction quality. It never loads a data
Bundle, instantiates a model, performs inference, regenerates a panel, or writes
any receipt. A self-consistent old output is insufficient: current data, model
lineage, evaluator sources, target protocol and sealed-test release must match.
"""
from __future__ import annotations

from pathlib import Path
import re
from types import SimpleNamespace

from . import seven_evaluation as seven
from .io import canonical_sha, read_json, sha256


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _pin_file(pin, label, *, base=None, inside=None):
    _require(isinstance(pin, dict) and isinstance(pin.get("path"), str), label + " missing file pin")
    _require(re.fullmatch(r"[0-9a-f]{64}", str(pin.get("sha256", ""))) is not None,
             label + " invalid SHA-256")
    path = Path(pin["path"])
    if not path.is_absolute() and base is not None:
        path = Path(base) / path
    path = path.resolve()
    if inside is not None:
        _require(path.is_relative_to(Path(inside).resolve()), label + " escapes evidence directory")
    _require(path.is_file() and sha256(path) == pin["sha256"], label + " file missing or SHA mismatch")
    if "bytes" in pin:
        _require(path.stat().st_size == pin["bytes"], label + " byte count mismatch")
    return path


def _same_pin(pin, expected, label, *, base=None, inside=None):
    _require(isinstance(pin, dict) and pin.get("sha256") == expected["sha256"], label + " identity mismatch")
    return _pin_file(pin, label, base=base, inside=inside)


def _manifest(root):
    """Require the exact file set, not merely all declared files being present."""
    manifest_path = root / "ARTIFACT_MANIFEST.json"
    sums_path = root / "SHA256SUMS.txt"
    document = read_json(manifest_path)
    entries = document.get("files")
    _require(isinstance(entries, list), "artifact manifest missing files list")
    expected = {}
    for entry in entries:
        name = entry.get("path")
        _require(isinstance(name, str) and not Path(name).is_absolute() and
                 name == Path(name).as_posix() and ".." not in Path(name).parts and name not in expected,
                 "artifact manifest has invalid or duplicate path")
        _require(name not in ("ARTIFACT_MANIFEST.json", "SHA256SUMS.txt"), "artifact manifest self-reference")
        _pin_file(entry, "artifact manifest " + name, base=root, inside=root)
        expected[name] = entry["sha256"]
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    _require(not any(p.is_symlink() for p in root.rglob("*")), "artifact manifest contains symlink")
    _require(actual == set(expected) | {"ARTIFACT_MANIFEST.json", "SHA256SUMS.txt"},
             "artifact manifest file set has extra or missing files")
    recorded = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        _require(match is not None, "SHA256SUMS has malformed entry")
        digest, name = match.groups()
        _require(name not in recorded, "SHA256SUMS duplicate path")
        recorded[name] = digest
    _require(recorded == {**expected, "ARTIFACT_MANIFEST.json": sha256(manifest_path)},
             "SHA256SUMS differs from exact artifact manifest")
    return {"artifact_manifest": seven._pin(manifest_path), "sha256sums": seven._pin(sums_path),
            "artifact_count": len(expected)}


def _common_targets(path, identity, split, protocol, data_root):
    plan = read_json(path)
    _require(plan.get("schema") == "bb_seven_common_15ghz_targets.v1" and plan.get("status") == "FROZEN",
             "common target schema/status mismatch")
    _require(plan.get("split") == split and all(plan.get(k) == v for k, v in identity.items()),
             "common target split/data/normalizer mismatch")
    _require(plan.get("evaluation_protocol_sha256") == protocol["sha256"], "common target protocol mismatch")
    ids = plan.get("target_ids", [])
    n = plan.get("target_count")
    _require(isinstance(n, int) and not isinstance(n, bool) and n > 0 and len(ids) == n and
             len(set(ids)) == n and canonical_sha(ids) == plan.get("target_id_order_sha256"),
             "common target IDs/count/order mismatch")
    _require(plan.get("task") == "PHYSICAL_15GHZ" and plan.get("frequency_hz") == 15_000_000_000 and
             plan.get("frequency_index") == 10 and plan.get("feature_order") == list(seven.FEATURES) and
             plan.get("condition_mask") == [[True] * 4 for _ in range(n)] and plan.get("relation") == [0] * 4,
             "common target feature/mask/relation mismatch")
    _require(len(plan.get("source_indices", [])) == n and len(plan.get("targets", [])) == n and
             all(isinstance(row, list) and len(row) == 4 for row in plan["targets"]), "common target shape mismatch")
    _require(plan.get("channel_scale") == read_json(data_root / "normalizer.json")["y_scale"],
             "common target shared train-only scale mismatch")
    _require(plan.get("source_split_geometries") == n + plan.get("source_label_ineligible_geometries", -1),
             "common target original source denominator mismatch")
    return plan


def _configuration(summary, request, records, identity, split, panel_kind, target_path):
    gate = summary.get("configuration_freeze")
    _require(isinstance(gate, dict) and gate == request.get("configuration_freeze"),
             "configuration freeze request/summary mismatch")
    if split == "validation":
        _require(gate.get("test_access") is False, "validation configuration cannot claim test access")
        if "file" in gate:
            path = _pin_file(gate["file"], "configuration freeze")
            expected = seven.broadband.verify_configuration_freeze(path, records,
                SimpleNamespace(data_sha=identity["data_sha"], norm_sha=identity["normalizer_sha"]), split)
            _require(gate == expected, "validation configuration freeze mismatch")
        else:
            expected = "VALIDATION_ONLY" if panel_kind == "common15" else "VALIDATION_ONLY_NO_TEST_RELEASE"
            _require(gate.get("status") == expected, "validation configuration status mismatch")
        return gate
    _require(summary.get("best_validation_candidate") == "NOT_DETERMINED" and
             gate.get("test_access") is True, "test selection or configuration release mismatch")
    field = "receipt" if panel_kind == "common15" else "file"
    path = _pin_file(gate.get(field), "configuration freeze")
    if panel_kind == "common15":
        expected = seven._verify_freeze(path, records, identity, target_path, split)
    else:
        expected = seven.broadband.verify_configuration_freeze(path, records,
            SimpleNamespace(data_sha=identity["data_sha"], norm_sha=identity["normalizer_sha"]), split)
    _require(gate == expected, "configuration freeze exact gate mismatch")
    return gate


def _common(root, summary, request, records, identity, registry, split, target_path):
    _require(target_path is not None, "common15 requires current target plan path")
    target_path = Path(target_path).resolve()
    protocol = seven.seven_protocol_identity()
    expected = seven._checkpoint_shas(records)
    _require(summary.get("schema") == "bb_seven_evaluation_summary.v1" and
             request.get("schema") == "bb_seven_evaluation_request.v1", "common evaluation schema mismatch")
    _require(summary.get("checkpoints") == expected and request.get("checkpoints") == expected,
             "common evaluation checkpoint set differs from active registry")
    _require(all(summary.get(k) == v and request.get(k) == v for k, v in identity.items()),
             "common evaluation data/split/normalizer identity mismatch")
    _require(summary.get("evaluation_protocol_sha256") == protocol["sha256"] and
             request.get("evaluation_protocol") == protocol, "common evaluation current source/protocol mismatch")
    _same_pin(request.get("registry"), seven._pin(registry), "active registry")
    _same_pin(summary.get("evaluation_request"), seven._pin(root / "EVALUATION_REQUEST.json"),
              "evaluation request", inside=root)
    for document in (summary, request):
        _same_pin(document.get("target_plan"), seven._pin(target_path), "common target plan")
    return protocol


def verify_existing_evaluation(out, data_manifest_path, registry_path, split,
                               panel_kind, target_plan_path=None):
    """Return a PASS identity receipt dict, or raise; never modify or infer.

    ``registry_path`` is the current bb_seven_registry.v1 twelve-state registry
    for either panel; broadband compares its exact ten-state subset. Paths may
    be relocated only when their pinned bytes remain identical. ``common15``
    requires the current frozen target-plan path. Broadband uses its immutable
    internal PANEL_MANIFEST, optionally bound to ``target_plan_path`` as well.
    """
    _require(split in ("validation", "test"), "invalid split")
    _require(panel_kind in ("common15", "broadband"), "invalid panel_kind")
    root, manifest_path, registry = (Path(p).resolve() for p in (out, data_manifest_path, registry_path))
    _require(manifest_path.name == "data_manifest.json", "data manifest filename mismatch")
    seals = _manifest(root)
    identity = seven._data_identity(manifest_path.parent)
    _require(sha256(manifest_path) == identity["data_manifest_sha256"], "data manifest identity mismatch")
    records = seven._registry(registry, identity)
    summary, request = (read_json(root / name) for name in ("EVALUATION_SUMMARY.json", "EVALUATION_REQUEST.json"))
    _require(summary.get("status") == "COMPLETE_PROXY_EVALUATION", "evaluation not COMPLETE")
    _require(summary.get("split") == split and request.get("split") == split, "evaluation split mismatch")
    _require(all(summary.get(k) == identity[k] and request.get(k) == identity[k] for k in ("data_sha", "normalizer_sha")),
             "evaluation data/normalizer mismatch")
    _require(summary.get("test_used_to_select_model") is False and summary.get("real_emx_validation") == "NOT_RUN" and
             summary.get("physical_winner") == "NOT_ESTABLISHED", "evaluation evidence/selection scope mismatch")
    if panel_kind == "common15":
        protocol = _common(root, summary, request, records, identity, registry, split, target_plan_path)
        plan = _common_targets(target_plan_path, identity, split, protocol, manifest_path.parent)
        _require(read_json(root / "common_15ghz_specs.json") == plan, "copied common target plan mismatch")
        _require(summary.get("target_count") == plan["target_count"] and
                 summary.get("selection_criterion") == seven.SELECTION, "common target count/selection mismatch")
        _require(set(summary.get("packages", {})) == set(seven.PACKAGES) and
                 set(summary.get("forward_models", {})) == set(seven.FORWARDS), "common model set mismatch")
        for name in seven.PACKAGES:
            report = read_json(_pin_file(summary["packages"][name]["metrics"], name + " metrics", inside=root))
            own = "BB00_FORWARD" if name == "BB00" else seven.PACKAGE_MAPPING[name][0]
            for key, model_name in (("selected_checkpoint", name), ("own_forward", own), ("common_forward", "FREF")):
                _same_pin(report.get(key), records[model_name]["checkpoint"], name + " " + key)
            _same_pin(report.get("target_plan"), seven._pin(target_plan_path), name + " target plan")
            _require(report.get("target_id_order_sha256") == plan["target_id_order_sha256"] and
                     report.get("target_count") == plan["target_count"], name + " target order/count mismatch")
        for name, report in summary["forward_models"].items():
            _same_pin(report.get("checkpoint"), records[name]["checkpoint"], name + " checkpoint")
            _require(report.get("source_target_ids_sha256") == plan["target_id_order_sha256"], name + " target order mismatch")
        target_pin = seven._pin(target_plan_path)
    else:
        records = {k: v for k, v in records.items() if not k.startswith("BB00")}
        protocol = seven.broadband.evaluation_protocol_identity()
        _require(summary.get("schema") == "bb_evaluation_summary.v1", "broadband evaluation schema mismatch")
        _require(summary.get("evaluation_protocol") == protocol and request.get("evaluation_protocol") == protocol,
                 "broadband evaluation current source/protocol mismatch")
        _same_pin(request.get("data_manifest"), seven._pin(manifest_path), "source data manifest")
        source = request.get("source_runs", {})
        _require(set(source) == set(records), "broadband source checkpoint set mismatch")
        for name, record in records.items():
            for field in ("checkpoint", "receipt"):
                _same_pin(source[name].get(field), record[field], name + " source " + field)
        panel_path = _pin_file(summary.get("panel_manifest"), "broadband target panel", inside=root)
        _same_pin(request.get("panel_manifest"), seven._pin(panel_path), "broadband request target panel", inside=root)
        if target_plan_path is not None:
            _same_pin(summary["panel_manifest"], seven._pin(target_plan_path), "current broadband target plan", inside=root)
        plan = read_json(panel_path)
        _require(plan.get("schema") == "bb_fixed_evaluation_specs.v1" and plan.get("split") == split and
                 plan.get("data_sha") == identity["data_sha"] and plan.get("normalizer_sha") == identity["normalizer_sha"] and
                 plan.get("data_manifest_sha") == identity["data_manifest_sha256"] and
                 plan.get("evaluation_protocol") == protocol and plan.get("seed") == seven.broadband.PANEL_SEED,
                 "broadband frozen target data/normalizer/protocol mismatch")
        names = [task.lower() + "_" + mode for task, mode in seven.broadband.PANEL_DEFINITIONS]
        _require([p.get("name") for p in plan.get("panels", [])] == names, "broadband target panel names/order mismatch")
        _require(set(summary.get("forward_models", {})) == set(seven.broadband.FORWARD_IDS) and
                 set(summary.get("packages", {})) == set(seven.PACKAGE_MAPPING), "broadband model set mismatch")
        for name in seven.broadband.FORWARD_IDS:
            report = read_json(_pin_file(summary["forward_models"][name]["metrics"], name + " metrics", inside=root))
            _same_pin(report.get("checkpoint"), records[name]["checkpoint"], name + " checkpoint")
        for name in seven.PACKAGE_MAPPING:
            report = read_json(_pin_file(summary["packages"][name]["metrics"], name + " metrics", inside=root))
            for key, model_name in (("selected_checkpoint", name), ("own_forward", seven.PACKAGE_MAPPING[name][0]),
                                    ("common_forward", "FREF")):
                _same_pin(report.get(key), records[model_name]["checkpoint"], name + " " + key)
            _same_pin(report.get("panel_manifest"), seven._pin(panel_path), name + " target panel", inside=root)
        target_pin = seven._pin(panel_path)
    gate = _configuration(summary, request, records, identity, split, panel_kind, target_plan_path)
    return {"schema": "bb_existing_evaluation_reuse_check.v1", "status": "PASS", "reuse_allowed": True,
            "split": split, "panel_kind": panel_kind, **seals, **identity,
            "registry": seven._pin(registry), "checkpoints": seven._checkpoint_shas(records),
            "checkpoint_count": len(records), "evaluation_protocol_sha256": protocol["sha256"],
            "target_plan": target_pin, "configuration_freeze": gate,
            "evaluation_summary": seven._pin(root / "EVALUATION_SUMMARY.json"),
            "model_inference_performed": False, "new_metrics_computed": False, "files_modified": False}
