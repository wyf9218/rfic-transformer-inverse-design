"""Independent selected-only evidence tests; every byte is SYNTHETIC, not EMX.

The caller supplies a previously validated context, as the public API requires.
These fixtures exercise the real evidence validator, including its entire local
pin graph, without patching it or loading models, datasets, or native tools.
"""
from copy import deepcopy
import csv
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.broadband56_nn import eucap15_selected_evidence as evidence


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def pin(path):
    raw = Path(path).read_bytes()
    return {"path": str(path), "sha256": sha(raw), "bytes": len(raw)}


def cleaned(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, list):
        return [cleaned(v) for v in value]
    if isinstance(value, dict):
        return {k: cleaned(v) for k, v in value.items()}
    return value


class SyntheticEvidence:
    """A small complete native-shaped chain, deliberately not a real solve."""

    def __init__(self, root, *, actual=None, below_half_srf=True, physics=True):
        self.root = root.resolve()
        self.paths = {}
        self.json_order = []
        self.request_id = "SYNTHETIC-request-000000"
        self.model_id = "SYNTHETIC-formal10k-reference"
        self.q = 13
        self.wanted = [1.0, 1.5, 13.0, 0.5]
        self.proxy = [1.01, 1.49, 13.1, 0.51]
        self.scale = [2.5, 2.5, 20.0, 0.8]
        self.tau = [0.1, 0.1, 1.0, 0.04]
        actual = [1.02, 1.48, 13.2, 0.52] if actual is None else actual
        self.native = self.root / "SYNTHETIC_NATIVE_NOT_EXECUTED"
        self.blob("gds", self.native / "geometry.gds", b"SYNTHETIC GDS NOT A REAL LAYOUT\n")
        self.blob("ports", self.native / "ports.json", b'{"synthetic":true}\n')
        self.blob("config", self.root / "private_config.yaml", b"SYNTHETIC TEST CONFIG ONLY\n")
        self.blob("calibre_index", self.native / "calibre_index.csv", b"SYNTHETIC DRC INDEX ONLY\n")
        self.blob("s4p", self.native / "solve" / "synthetic.s4p", b"! SYNTHETIC HASH PLACEHOLDER, NOT SIMULATED\n")
        self.blob("log", self.native / "solve" / "solver.log", b"SYNTHETIC: NO SOLVER WAS EXECUTED\n")
        self.command = ["SYNTHETIC_EMX_NEVER_EXECUTED", "--gds", self.paths["gds"].as_posix()]
        self.put("command", self.native / "solve" / "emx_command.json", self.command)
        self.put("reference", self.root / "REFERENCE.json", {
            "schema": "eucap15_reference_identity.v1", "reference_label": "FORMAL10K_REFERENCE",
            "status": "REFERENCE_LOADED_NOT_FINAL", "source_snapshot_geometries": 10000,
            "frequency_ghz": 15, "model_id": self.model_id, "label_mode": "STRICT_LUMPED",
            "synthetic_fixture": True,
        })
        self.records = []
        for q in range(10, 21):
            self.records.append({
                "request_id": self.request_id, "candidate_id": f"SYNTHETIC-candidate-q{q}",
                "q_target": q, "q_proxy": self.q, "proxy_preselected": q == self.q,
                "analytic_grid": True, "model_id": self.model_id, "frequency_ghz": 15,
                "dataset_scope": "FORMAL_10K", "target_source": "DEVELOPMENT_UNIFORM_TRIPLE",
                "evidence_source": "SELF_PROXY", "emx_status": "NOT_RUN", "actual_response": None,
                "target": [1.0, 1.5, float(q), 0.5], "grid_proxy": self.proxy,
                "candidate_geometry_identity_sha256": sha(f"SYNTHETIC geometry {q}".encode()),
            })
        self.blob("records", self.root / "eleven_records.jsonl", b"".join(
            (json.dumps(r, sort_keys=True) + "\n").encode() for r in self.records))
        chosen = self.records[self.q - 10]
        self.candidate_id = chosen["candidate_id"]
        self.geometry_sha = chosen["candidate_geometry_identity_sha256"]
        self.candidate_sha = sha(self.candidate_id.encode())
        self.put("freeze", self.root / "PILOT_FREEZE.json", {
            "protocol": {"score_scale": self.scale, "absolute_hit_tolerances": self.tau},
            "executed_legacy_tolerance_float64": self.tau, "synthetic_fixture": True,
        })
        self.item = {
            "request_id": self.request_id, "candidate_id": self.candidate_id, "q_proxy": self.q,
            "source_records": self.p("records"), "record_line_number": self.q - 9,
            "source_record_canonical_sha256": canonical(chosen), "selected_analytic_pass": True,
            "selected_target": self.wanted, "selected_grid_proxy": self.proxy,
            "candidate_geometry_identity_sha256": self.geometry_sha,
        }
        manifest = {
            "schema": "eucap15_selected_candidate_handoff.v1", "N_requests": 64,
            "N_proxy_candidates": 704, "research_phase": "DEVELOPMENT_PILOT_NOT_FINAL10K",
            "frequency_ghz": 15, "dataset_scope": "FORMAL_10K", "label_mode": "STRICT_LUMPED",
            "model_id": self.model_id,
            "requests": [self.item] + [{"request_id": f"SYNTHETIC-request-{i:06d}",
                "candidate_id": f"SYNTHETIC-other-selected-{i}"} for i in range(1, 64)],
        }
        self.put("pilot", self.root / "SUBMISSION_MANIFEST.json", manifest)
        context_fields = {
            "candidate_id": self.candidate_id, "model_id": self.model_id, "dataset_scope": "FORMAL_10K",
            "frequency_ghz": 15, "q_requested": self.q, "q_proxy": self.q, "q_emx": None,
        }
        selected_fields = {
            "physical_selection": "Q_PROXY_ONLY", "selected_manifest": self.p("pilot"),
            "reference": self.p("reference"), "original_request_denominator": 64,
            "unselected_physical_status": "NOT_REQUESTED_MAIN_PILOT",
        }
        audited = []
        for record in self.records:
            if record["candidate_id"] == self.candidate_id:
                audited.append({"candidate_id": self.candidate_id, "status": "PASS",
                    "candidate_id_sha256": self.candidate_sha,
                    "candidate_geometry_identity_sha256": self.geometry_sha,
                    "gds": self.p("gds"), "port_manifest": self.p("ports")})
            else:
                audited.append({"candidate_id": record["candidate_id"],
                    "status": "NOT_REQUESTED_MAIN_PILOT", "audit_attempted": False,
                    "cadence_routed": False, "calibre_eligible": False})
        statuses = [{"candidate_id": r["candidate_id"], "status": r["status"]} for r in audited]
        self.put("audit", self.native / "REQUEST_GDS_AUDIT.json", {
            "schema": "eucap15_selected_request_gds_audit.v1", "physical_selection": "Q_PROXY_ONLY",
            "selected_candidate_id": self.candidate_id, "N_selected": 1, "N_audit_attempted": 1,
            "source_pins": {"eleven_records": self.p("records"), "qscan_freeze": self.p("freeze"),
                "selected_manifest": self.p("pilot"), "reference": self.p("reference"),
                "private_config": self.p("config")}, "records": audited,
        })
        self.put("drc", self.native / "calibre_summary.json", {
            "overall_status": "PASS", "blocking_drc_violation_count": 0,
            "drc_scope": "foundry_macro_ip_back_end", "candidate_id_sha256": self.candidate_sha,
            "candidate_geometry_identity_sha256": self.geometry_sha,
            "gds_path": self.p("gds")["path"], "gds_sha256": self.p("gds")["sha256"],
            "checks": {"foundry_drc_pass": True, "same_gds": True},
        })
        self.put("request", self.native / "REQUEST.json", {
            **{k: v for k, v in context_fields.items() if k != "q_emx"},
            "schema": "eucap15_selected_emx_request.v1", "request_id": self.request_id,
            "target_source": "DEVELOPMENT_UNIFORM_TRIPLE", "production_campaign_membership": False,
            "records": self.p("records"), "qscan_freeze": self.p("freeze"),
            "selected_manifest": self.p("pilot"), "gds_audit": self.p("audit"),
            "calibre_index": self.p("calibre_index"), "private_config": self.p("config"),
        })
        self.put("proof", self.native / "PREFLIGHT.json", {
            **context_fields, **selected_fields, "schema": "eucap15_selected_emx_preflight.v1",
            "status": "PASS", "request_id": self.request_id, "candidate_id_sha256": self.candidate_sha,
            "geometry_sha256": self.geometry_sha, "original_record": chosen,
            "protocol": self.read("freeze")["protocol"], "executed_hit_tolerances": self.tau,
            "frequency_grid_hz": [n * 10**9 for n in range(5, 61)],
            "port_order": ["P001", "P002", "P003", "P004"], "port_permutation": [0, 1, 3, 2],
            "reference_ohm": 50, "full11_physical_optimum": "NOT_EVALUATED_SINGLE_PRESELECTED_CANDIDATE",
            "production_membership": False, "output": str(self.native), "request": self.p("request"),
            "gds": self.p("gds"), "port_manifest": self.p("ports"), "calibre": self.p("drc"),
            "source_pins": [self.p(k) for k in ("request", "records", "freeze", "audit", "calibre_index",
                "config", "pilot", "reference", "gds", "ports", "drc")],
            "command": self.command, "original_candidate_statuses": statuses,
        })
        self.put("solver", self.native / "SOLVER_RECEIPT.json", {
            "schema": "frequency_research_fresh_solver.v1", "status": "PASS",
            "candidate_id": self.candidate_id, "preflight": self.p("proof"),
            "source_gds_before": self.p("gds"), "source_gds_after": self.p("gds"),
            "real_emx": True, "production_modified": False, "q_emx": None,
            "touchstone": self.p("s4p"), "artifacts": [self.p(k) for k in ("s4p", "log", "command")],
        })
        finite = all(math.isfinite(a) for a in actual)
        row_base = dict(zip(("lp_nh", "ls_nh", "qmin", "k_abs"), actual))
        row_base.update({"qp": actual[2], "qs": actual[2] + 1.0, "signed_k": -actual[3]})
        row_base.update({"finite_values": finite, "positive_primary_resistance": True,
            "positive_secondary_resistance": True, "positive_primary_inductive_reactance": True,
            "positive_secondary_inductive_reactance": True, "below_half_srf": below_half_srf,
            "broadband_descriptor_valid": finite, "strict_lumped_valid": finite and below_half_srf,
            "passivity_status": "PASS" if physics else "FAIL", "reciprocity_status": "PASS"})
        self.rows = [{"frequency_hz": n * 10**9, **row_base} for n in range(5, 61)]
        self.paths["csv"] = self.native / "features" / "features_56.csv"
        self.write_csv(self.rows)
        errors = [a - t for a, t in zip(actual, self.wanted)]
        hits = [math.isfinite(e) and abs(e) <= t for e, t in zip(errors, self.tau)]
        valid = bool(finite and below_half_srf and physics)
        self.put("feature", self.native / "features" / "FEATURE_RECEIPT.json", {
            **context_fields, **selected_fields, "schema": "eucap15_selected_fresh_features.v1",
            "status": "PASS_EXTRACTION", "target": self.wanted, "proxy_self": self.proxy,
            "score_scale": self.scale, "absolute_hit_tolerances": self.tau,
            "q_optimum_status": "NOT_EVALUATED_MAIN_PRESELECTED_CANDIDATE", "production_membership": False,
            "preflight": self.p("proof"), "solver_receipt": self.p("solver"),
            "actual_fresh_emx": cleaned(actual), "emx_minus_target": cleaned(errors),
            "emx_minus_proxy": cleaned([a - p for a, p in zip(actual, self.proxy)]),
            "normalized_response_score": math.sqrt(sum((e / s)**2 for e, s in zip(errors, self.scale))/4) if finite else None,
            "within_tolerance": hits, "joint_response_hit": all(hits), "descriptor_valid": finite,
            "strict_lumped_valid": finite and below_half_srf, "physics_qa_pass": physics,
            "valid_for_strict_comparison": valid, "strict_joint_hit": all(hits) and valid,
            "target_relative_signed_percent": cleaned([100 * e / t for e, t in zip(errors, self.wanted)]),
            "target_relative_absolute_percent": cleaned([100 * abs(e) / t for e, t in zip(errors, self.wanted)]),
            "original_frequency_row": cleaned(self.rows[10]), "original_candidate_statuses": statuses,
            "original_56_summary": {"port_count": 4, "frequency_points": 56,
                "frequency_start_hz": 5 * 10**9, "frequency_stop_hz": 60 * 10**9, "frequency_step_hz": 10**9},
        })
        self.put("manifest", self.native / "features" / "MANIFEST.json", {
            "artifacts": [self.p("feature"), self.p("csv")], "inputs_unchanged": True,
        })
        self.ctx = SimpleNamespace(manifest_pin=self.p("pilot"), manifest=self.read("pilot"),
            freeze_pin=self.p("freeze"), freeze=self.read("freeze"), reference_pin=self.p("reference"),
            path_map={}, selected=deepcopy(chosen), request=deepcopy(self.item))
        self.item = deepcopy(self.item)

    def blob(self, key, path, raw):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.paths[key] = path

    def put(self, key, path, value):
        self.blob(key, path, (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode())
        if key not in self.json_order:
            self.json_order.append(key)

    def p(self, key):
        return pin(self.paths[key])

    def read(self, key):
        return json.loads(self.paths[key].read_text())

    def write_csv(self, rows, fieldnames=None):
        path = self.paths["csv"]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames or list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def repin_after(self, key):
        """Close upstream pins after tampering; semantic attacks must not only hit stale hashes."""
        def replace(value):
            if isinstance(value, dict):
                if {"path", "sha256", "bytes"} <= value.keys():
                    return {**value, **pin(Path(value["path"]))}
                return {k: replace(v) for k, v in value.items()}
            if isinstance(value, list):
                return [replace(v) for v in value]
            return value
        if key in self.json_order:
            later = self.json_order[self.json_order.index(key) + 1:]
        else:
            later = self.json_order
        for name in later:
            self.put(name, self.paths[name], replace(self.read(name)))

    def edit(self, key, change):
        value = self.read(key)
        change(value)
        self.put(key, self.paths[key], value)
        self.repin_after(key)

    def entry(self):
        return {"request_id": self.request_id, "feature_manifest": self.p("manifest")}

    def inspect(self):
        return evidence.inspect_features(self.entry(), self.item, self.ctx)


@pytest.fixture
def chain(tmp_path):
    return SyntheticEvidence(tmp_path)


def test_complete_strict_chain_is_readonly_and_never_claims_full11(chain):
    before = {k: p.read_bytes() for k, p in chain.paths.items()}
    result = chain.inspect()
    assert result["status"] == "STRICT_VALID"
    assert result["actual"] == [1.02, 1.48, 13.2, 0.52]
    assert result["valid_for_strict_comparison"] is True
    assert result["strict_joint_hit"] is True
    assert result["geometry_sha"] == chain.geometry_sha
    assert result["touchstone_sha"] == chain.p("s4p")["sha256"]
    assert "q_emx" not in result
    expected = {chain.p(k)["path"] for k in chain.paths}
    assert {p["path"] for p in result["evidence_pins"]} == expected
    assert before == {k: p.read_bytes() for k, p in chain.paths.items()}


def test_context_selected_from_other_request_cannot_replace_this_item(chain):
    chain.ctx.selected = {"candidate_id": "OTHER-CANDIDATE", "q_target": 20}
    chain.ctx.request = {"request_id": "OTHER-REQUEST"}
    assert chain.inspect()["actual"] == [1.02, 1.48, 13.2, 0.52]


@pytest.mark.parametrize("kwargs", [{"below_half_srf": False}, {"physics": False},
    {"actual": [math.nan, 1.5, 13.0, 0.5]}, {"actual": [1.0, math.inf, 13.0, 0.5]},
    {"actual": [1.0, 1.5, -math.inf, 0.5]}])
def test_invalid_or_nonfinite_native_result_is_retained_not_promoted(tmp_path, kwargs):
    c = SyntheticEvidence(tmp_path, **kwargs)
    result = c.inspect()
    assert result["status"] == "EMX_INVALID"
    assert result["valid_for_strict_comparison"] is False
    assert result["strict_joint_hit"] is False
    assert result["actual"] == c.read("feature")["actual_fresh_emx"]
    assert result["touchstone_sha"] == c.p("s4p")["sha256"]


def test_finite_strict_miss_is_not_failed_extraction(tmp_path):
    c = SyntheticEvidence(tmp_path, actual=[1.0, 1.5, 13.0, 0.56])
    assert c.inspect()["status"] == "STRICT_VALID"
    assert c.inspect()["strict_joint_hit"] is False


@pytest.mark.parametrize("field,value", [
    ("q_requested", 14), ("q_proxy", 14), ("q_emx", 13), ("target", [1.0, 1.5, 14.0, 0.5]),
    ("proxy_self", [1.0, 1.5, 13.0, 0.5]), ("model_id", "SYNTHETIC_OTHER_MODEL"),
    ("dataset_scope", "DEVELOPMENT_5K"), ("frequency_ghz", 14),
    ("candidate_id", "SYNTHETIC_OTHER_CANDIDATE"), ("original_request_denominator", 62),
    ("schema", "frequency_research_fresh_features.v1"), ("physical_selection", "FULL11"),
    ("q_optimum_status", "Q_EMX_OPTIMAL"), ("production_membership", True),
    ("actual_fresh_emx", [1.0, 1.5, 13.0, 0.5]), ("emx_minus_target", [0.0] * 4),
    ("emx_minus_proxy", [0.0] * 4), ("normalized_response_score", 0.0),
    ("target_relative_signed_percent", [0.0] * 4), ("target_relative_absolute_percent", [0.0] * 4),
    ("within_tolerance", [False] * 4), ("strict_joint_hit", False),
    ("valid_for_strict_comparison", 1), ("absolute_hit_tolerances", [9.0] * 4),
])
def test_feature_identity_and_saved_numerical_claim_tampering_rejected(chain, field, value):
    chain.edit("feature", lambda obj: obj.__setitem__(field, value))
    with pytest.raises(evidence.SelectedEvidenceError):
        chain.inspect()


@pytest.mark.parametrize("field,value", [
    ("request_id", "OTHER_REQUEST"), ("geometry_sha256", "0" * 64),
    ("candidate_id_sha256", "0" * 64), ("q_emx", 13), ("q_requested", 10),
    ("frequency_grid_hz", list(range(56))), ("port_order", ["P002", "P001", "P003", "P004"]),
    ("port_permutation", [0, 1, 2, 3]), ("reference_ohm", 75),
    ("full11_physical_optimum", "FULL11_PASSED"), ("schema", "frequency_research_emx_preflight.v1"),
])
def test_preflight_drift_rejected_with_reclosed_hashes(chain, field, value):
    chain.edit("proof", lambda obj: obj.__setitem__(field, value))
    with pytest.raises(evidence.SelectedEvidenceError):
        chain.inspect()


@pytest.mark.parametrize("key", ["reference", "selected_manifest"])
def test_wrong_existing_reference_or_selected_manifest_is_not_accepted(chain, key):
    alternate = chain.root / f"OTHER_{key}.json"
    alternate.write_text('{"synthetic_other_identity":true}\n')
    chain.edit("feature", lambda obj: obj.__setitem__(key, pin(alternate)))
    with pytest.raises(evidence.SelectedEvidenceError):
        chain.inspect()


@pytest.mark.parametrize("field,value", [("drc_scope", "synthetic_nonfoundry"),
    ("gds_sha256", "0" * 64), ("gds_path", "/SYNTHETIC/OTHER.gds"),
    ("candidate_geometry_identity_sha256", "0" * 64), ("blocking_drc_violation_count", 1),
    ("checks", {"foundry_drc_pass": False}), ("checks", {})])
def test_drc_scope_same_gds_and_failure_identity_rejected(chain, field, value):
    chain.edit("drc", lambda obj: obj.__setitem__(field, value))
    with pytest.raises(evidence.SelectedEvidenceError):
        chain.inspect()


def test_source_pin_omission_rejected(chain):
    chain.edit("proof", lambda obj: obj.__setitem__("source_pins", [p for p in obj["source_pins"]
        if p["path"] != str(chain.paths["config"])]))
    with pytest.raises(evidence.SelectedEvidenceError, match="omitted"):
        chain.inspect()


def test_duplicate_source_pin_rejected(chain):
    chain.edit("proof", lambda obj: obj["source_pins"].append(deepcopy(obj["source_pins"][0])))
    with pytest.raises(evidence.SelectedEvidenceError, match="duplicate"):
        chain.inspect()


@pytest.mark.parametrize("change", [
    lambda obj: obj.__setitem__("N_audit_attempted", 11),
    lambda obj: obj.__setitem__("selected_candidate_id", "OTHER"),
    lambda obj: obj["records"][0].__setitem__("status", "PASS"),
    lambda obj: obj["records"][0].__setitem__("cadence_routed", True),
    lambda obj: obj["records"].append(deepcopy(obj["records"][0])),
    lambda obj: obj["records"][3].__setitem__("candidate_geometry_identity_sha256", "0" * 64),
])
def test_single_selected_native_audit_and_all_eleven_accounting(chain, change):
    chain.edit("audit", change)
    with pytest.raises(evidence.SelectedEvidenceError):
        chain.inspect()


@pytest.mark.parametrize("field,value", [("status", "FAIL"), ("real_emx", False),
    ("production_modified", True), ("q_emx", 13), ("candidate_id", "OTHER")])
def test_solver_false_terminal_or_identity_rejected(chain, field, value):
    chain.edit("solver", lambda obj: obj.__setitem__(field, value))
    with pytest.raises(evidence.SelectedEvidenceError):
        chain.inspect()


def test_solver_before_after_gds_mismatch_rejected(chain):
    chain.edit("solver", lambda obj: obj.__setitem__("source_gds_after", chain.p("s4p")))
    with pytest.raises(evidence.SelectedEvidenceError):
        chain.inspect()


def test_solver_command_is_exact_original_argv_array(chain):
    chain.put("command", chain.paths["command"], ["SYNTHETIC_DIFFERENT_ARGV"])
    chain.repin_after("command")
    with pytest.raises(evidence.SelectedEvidenceError, match="command"):
        chain.inspect()


@pytest.mark.parametrize("kind", ["duplicate", "missing_touchstone", "missing_command", "outside"])
def test_solver_artifact_closure_cannot_be_faked(chain, kind):
    def change(obj):
        if kind == "duplicate":
            obj["artifacts"].append(deepcopy(obj["artifacts"][0]))
        elif kind == "missing_touchstone":
            obj["artifacts"] = [p for p in obj["artifacts"] if not p["path"].endswith(".s4p")]
        elif kind == "missing_command":
            obj["artifacts"] = [p for p in obj["artifacts"] if not p["path"].endswith("emx_command.json")]
        else:
            obj["artifacts"].append(chain.p("gds"))
    chain.edit("solver", change)
    with pytest.raises(evidence.SelectedEvidenceError):
        chain.inspect()


@pytest.mark.parametrize("kind", ["short", "duplicate_frequency", "wrong_frequency", "duplicate_column"])
def test_exact56_frequency_and_columns_required(chain, kind):
    rows = deepcopy(chain.rows)
    fields = None
    if kind == "short":
        rows.pop()
    elif kind == "duplicate_frequency":
        rows[11]["frequency_hz"] = rows[10]["frequency_hz"]
    elif kind == "wrong_frequency":
        rows[10]["frequency_hz"] = 15 * 10**9 + 1
    else:
        fields = list(rows[0]) + ["qmin"]
    chain.write_csv(rows, fields)
    chain.repin_after("csv")
    with pytest.raises(evidence.SelectedEvidenceError):
        chain.inspect()


@pytest.mark.parametrize("field,value", [("lp_nh", 9.0), ("frequency_hz", 16 * 10**9),
    ("below_half_srf", False), ("strict_lumped_valid", "NOT_A_BOOLEAN")])
def test_original_frequency_row_must_match_retained_csv(chain, field, value):
    chain.edit("feature", lambda obj: obj["original_frequency_row"].__setitem__(field, value))
    with pytest.raises(evidence.SelectedEvidenceError):
        chain.inspect()


@pytest.mark.parametrize("predicate", ["finite_values", "below_half_srf", "positive_primary_resistance"])
def test_descriptor_and_strict_cannot_disagree_with_original_predicates(chain, predicate):
    chain.rows[10][predicate] = False
    chain.write_csv(chain.rows)
    chain.repin_after("csv")
    chain.edit("feature", lambda obj: obj["original_frequency_row"].__setitem__(predicate, False))
    with pytest.raises(evidence.SelectedEvidenceError, match="predicate"):
        chain.inspect()


@pytest.mark.parametrize("field,value", [("qmin", 12.0), ("qp", math.nan),
    ("qs", math.inf), ("k_abs", 0.53), ("signed_k", -0.53)])
def test_qmin_and_absolute_k_must_follow_original_extractor_fields(chain, field, value):
    # Preserve exact CSV/original-row correspondence, so the derived-feature
    # consistency gate, not a stale source hash, must reject the contradiction.
    chain.rows[10][field] = value
    chain.write_csv(chain.rows)
    chain.repin_after("csv")
    chain.edit("feature", lambda obj: obj["original_frequency_row"].__setitem__(field, cleaned(value)))
    with pytest.raises(evidence.SelectedEvidenceError):
        chain.inspect()


def test_positive_or_negative_original_signed_k_yields_same_absolute_k(chain):
    assert chain.rows[10]["signed_k"] < 0
    assert chain.inspect()["status"] == "STRICT_VALID"
    chain.rows[10]["signed_k"] *= -1
    chain.write_csv(chain.rows)
    chain.repin_after("csv")
    chain.edit("feature", lambda obj: obj["original_frequency_row"].__setitem__(
        "signed_k", chain.rows[10]["signed_k"]))
    assert chain.inspect()["actual"][3] == 0.52


def test_low_q_percent_does_not_make_k_residual_or_strict_invalid_a_hit(tmp_path):
    c = SyntheticEvidence(tmp_path, actual=[1.0, 1.5, 13.01, 0.56], below_half_srf=False)
    assert c.inspect()["status"] == "EMX_INVALID"
    c.edit("feature", lambda obj: obj.__setitem__("strict_joint_hit", True))
    with pytest.raises(evidence.SelectedEvidenceError, match="strict_joint_hit"):
        c.inspect()


@pytest.mark.parametrize("kind", ["hash", "size", "relative", "symlink", "missing", "extra_entry"])
def test_manifest_pin_and_exact_publication_entry_required(chain, kind):
    entry = chain.entry()
    if kind == "hash":
        entry["feature_manifest"]["sha256"] = "0" * 64
    elif kind == "size":
        entry["feature_manifest"]["bytes"] += 1
    elif kind == "relative":
        entry["feature_manifest"]["path"] = "features/MANIFEST.json"
    elif kind == "symlink":
        link = chain.root / "manifest_link.json"
        link.symlink_to(chain.paths["manifest"])
        entry["feature_manifest"]["path"] = str(link)
    elif kind == "missing":
        entry["feature_manifest"]["path"] = str(chain.root / "missing" / "MANIFEST.json")
    else:
        entry["q_emx"] = 13
    with pytest.raises(evidence.SelectedEvidenceError):
        evidence.inspect_features(entry, chain.item, chain.ctx)


@pytest.mark.parametrize("kind", ["duplicate", "missing", "inputs_changed"])
def test_feature_manifest_must_close_exact_two_artifacts(chain, kind):
    def change(obj):
        if kind == "duplicate":
            obj["artifacts"].append(deepcopy(obj["artifacts"][0]))
        elif kind == "missing":
            obj["artifacts"].pop()
        else:
            obj["inputs_unchanged"] = False
    chain.edit("manifest", change)
    with pytest.raises(evidence.SelectedEvidenceError):
        chain.inspect()


@pytest.mark.parametrize("raw_member", ['"duplicate":1,"duplicate":1', '"bad":NaN',
    '"bad":Infinity', '"bad":-Infinity', '"bad":1e999'])
def test_reclosed_json_duplicate_keys_and_nonstandard_numbers_rejected(chain, raw_member):
    path = chain.paths["feature"]
    raw = path.read_text().rstrip()
    path.write_text(raw[:-1] + "," + raw_member + "}\n")
    chain.repin_after("feature")
    with pytest.raises(evidence.SelectedEvidenceError):
        chain.inspect()


def test_any_original_source_changed_after_context_validation_rejected(chain):
    chain.paths["records"].write_bytes(chain.paths["records"].read_bytes() + b"\n")
    with pytest.raises(evidence.SelectedEvidenceError, match="SHA/size"):
        chain.inspect()


@pytest.mark.parametrize("field,value", [("q_proxy", 10), ("selected_analytic_pass", False),
    ("candidate_id", "SYNTHETIC-candidate-q10"), ("record_line_number", 1),
    ("request_id", "SYNTHETIC-request-000001")])
def test_caller_cannot_substitute_preselected_item_or_cross_request(chain, field, value):
    item = deepcopy(chain.item)
    item[field] = value
    with pytest.raises(evidence.SelectedEvidenceError):
        evidence.inspect_features(chain.entry(), item, chain.ctx)


def test_duplicate_original_request_membership_rejected(chain):
    chain.ctx.manifest["requests"][1] = deepcopy(chain.item)
    chain.put("pilot", chain.paths["pilot"], chain.ctx.manifest)
    chain.ctx.manifest_pin = chain.p("pilot")
    with pytest.raises(evidence.SelectedEvidenceError, match="unique"):
        chain.inspect()


def test_analytic_failed_preselection_cannot_acquire_synthetic_physical_success(chain):
    # Rebind this deliberately synthetic context; even an internally consistent
    # handoff flag cannot promote the original analytic failure or pick another Q.
    chain.item["selected_analytic_pass"] = False
    chain.ctx.manifest["requests"][0] = deepcopy(chain.item)
    chain.put("pilot", chain.paths["pilot"], chain.ctx.manifest)
    chain.ctx.manifest_pin = chain.p("pilot")
    with pytest.raises(evidence.SelectedEvidenceError, match="analytic eligibility"):
        chain.inspect()
