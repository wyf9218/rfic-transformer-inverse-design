"""Synthetic contract/routing tests only; these do not validate real GDS."""
import csv
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from research.broadband56_nn import frequency_research_gds_audit as audit


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return audit.pin(path)


def csv_write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def repin(packet):
    path, request, rows = packet
    records_path = Path(request["source_pins"]["eleven_records"]["path"])
    records_path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    request["source_pins"]["eleven_records"] = audit.pin(records_path)
    write(path, request)
    return path


@pytest.fixture
def packet(tmp_path, monkeypatch):
    context = dict(request_id="synthetic-HELDOUT_TRIPLE_AUDIT-000003", frequency_ghz=7,
                   model_id="f07-SYNTHETIC", dataset_scope="FORMAL_10K", q_proxy=12,
                   target_source="HELDOUT_TRIPLE_AUDIT")
    geometry = [300., 300., 200., 200., 6., 50., 50., 0., 200., 200.]
    records = []
    for q in range(10, 21):
        row = dict(**context, q_target=q, candidate_id=f'{context["request_id"]}-q{q:02d}',
            geometry_fields=list(audit.GEOMETRY_FIELDS), grid_geometry=geometry,
            target=[1., .7, q, .2], grid_proxy=[1., .7, q + .1, .2],
            grid_proxy_score=.1 * abs(q - 12), proxy_preselected=q == 12,
            evidence_source="SELF_PROXY", parameter_identity_not_actual_gds=True,
            emx_status="NOT_RUN", actual_response=None, analytic_grid=q in (10, 12, 20))
        row["parameter_geometry_hash"] = audit._parameter_hash(row)
        records.append(row)
    freeze = dict(schema="frequency_qscan_freeze.v1", status="FROZEN_BEFORE_QSCAN_AND_NEW_EMX",
        frequency_ghz=7, model_id=context["model_id"],
        config=dict(frequency_ghz=7, model_id=context["model_id"], dataset_scope="FORMAL_10K",
                    study_id="synthetic", label_mode="STRICT_LUMPED"),
        protocol=dict(q_values=list(range(10, 21)), q_scalar="min(Qp,Qs)"))
    sources = {"qscan_freeze": write(tmp_path / "freeze.json", freeze),
               "eleven_records": {"path": str(tmp_path / "eleven.jsonl")}}
    runtime = {"configuration": write(tmp_path / "config.yaml", {"synthetic": True}),
               "foundry_contract": write(tmp_path / "contract.json", {"synthetic": True}),
               "core_sources": {name: write(tmp_path / "cores" / (name + ".py"), {"synthetic": True})
                                for name in audit.CORE_MODULES}}
    # Only pytest substitutes these reference bytes; the CLI has no override.
    monkeypatch.setattr(audit, "CONFIG_SHA256", runtime["configuration"]["sha256"])
    monkeypatch.setattr(audit, "FOUNDRY_CONTRACT_SHA256", runtime["foundry_contract"]["sha256"])
    monkeypatch.setattr(audit, "PROVEN_CORE_SHA256", {k: p["sha256"] for k, p in runtime["core_sources"].items()})
    routes = {r["candidate_id"]: f"parallel_shards/shard_{n:03d}"
              for n, r in enumerate(r for r in records if r["analytic_grid"])}
    request = dict(schema=audit.REQUEST_SCHEMA, request=context, source_pins=sources,
        runtime=runtime, cadence={"root": str(tmp_path / "cadence"), "routes": routes},
        production_campaign_membership=False)
    packet = tmp_path / "request.json", request, records
    repin(packet)
    return packet


def physical_fixture(packet):
    request = audit.load_request(packet[0])
    for candidate in request.candidates:
        if not candidate["analytic_grid"]:
            continue
        shard = request.routes[candidate["candidate_id"]]
        write(shard / "candidate_queue_dataset_summary.json", dict(overall_status="PASS",
            cadence_streamout_only=True, run_emx=False))
        geometry = dict(zip(audit.GEOMETRY_FIELDS, candidate["original_record"]["grid_geometry"]))
        row = {"queue__" + k: candidate[k] for k in ("candidate_id", "candidate_id_sha256",
                                                    "candidate_geometry_identity_sha256")}
        row.update(evaluation="synthetic-evaluation", **{"geom__" + k: v for k, v in geometry.items()})
        csv_write(shard / "dataset_rows.csv", [row])
        evaluation = shard / "evaluations" / "synthetic-evaluation"
        geometry.update(primary_turns=1, secondary_turns=1,
            **{k: 6. for k in ("primary_width_um", "secondary_width_um", "primary_vdd_bar_width_um", "secondary_vdd_bar_width_um")})
        metrics = {f"{w}_winding_centerline_{a}_deg": v for w in ("primary", "secondary")
                   for a, v in (("min_internal_angle", 135), ("max_internal_angle", 135),
                                ("min_terminal_angle", 90), ("max_terminal_angle", 90))}
        metrics.update({k: 6. for k in ("power_line_8port_bridge_width_um",
            "power_line_8port_primary_bridge_width_um", "power_line_8port_secondary_bridge_width_um")})
        write(evaluation / "summary.json", dict(geometry=geometry, ok=True, error=None,
            touchstone_path=None, geometry_check=dict(ok=True, metrics=metrics)))
        write(evaluation / "layout/foundry_layout_source_audit.json",
              dict(grid_canonicalization=dict(overall_status="PASS", max_relative_area_change=0)))
        write(evaluation / "layout/power_line_8port_geometry.json", dict(touchstone_mode="signal_4_grounded_aux"))
        write(evaluation / "layout/transformer_layout.layout.json", dict(synthetic=True))
        for name in ("layout/transformer_layout.gds", "streamout/transformer_layout_cadpins.gds"):
            path = evaluation / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"SYNTHETIC_NOT_A_GDS")
    return request


@pytest.fixture
def backend(monkeypatch):
    calls = []

    def actual(**kwargs):
        calls.append(kwargs["gds_path"])
        return dict(checks={"all_polygon_edges_horizontal_vertical_or_45_degree": True,
                            "all_polygon_vertices_on_manufacturing_grid": True},
                    **{k: dict(overall_status="PASS") for k in
                       ("ground_frame", "via_and_landing", "power_line_bridge_connections")})

    value = SimpleNamespace(cfg=None, contract=None,
        foundry=SimpleNamespace(_validate_source_audit=lambda *a, **k: None,
            _validate_power_line_audit_shape=lambda *a: None, _audit_actual_gds=actual),
        ports=SimpleNamespace(measure_port_ground_metrics=lambda **k: dict(power_line_check="PASS",
            metrics={"power_line_8port_port_ground_overlap_verified_port_count": 8},
            via_stack_check={"overall_status": "PASS"})),
        identity=SimpleNamespace(gds_structural_identity=lambda p: dict(overall_status="PASS",
            layer_union_sha256="SYNTHETIC", label_pin_set_sha256="SYNTHETIC", structural_sha256="SYNTHETIC"),
            gds_timestamp_normalized_sha256=lambda p: "SYNTHETIC_NORMALIZED_NOT_GDS",
            GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM="SYNTHETIC_TEST_ONLY"),
        adapter=SimpleNamespace(from_vector=lambda x: x, field_order=lambda: audit.GEOMETRY_FIELDS,
                                search_space=SimpleNamespace(validate=lambda g: [])))
    monkeypatch.setattr(audit, "_load_backend", lambda _: value)
    return value, calls


def test_any_analytic_subset_preserves_all_eleven_and_pass_pins(packet, backend, tmp_path):
    physical_fixture(packet)
    result = audit.audit_request(packet[0], tmp_path / "output")
    assert result["status"] == "PASS"
    assert (result["N_logical"], result["N_analytic_fail"], result["N_audit_pass"], result["N_audit_fail"]) == (11, 8, 3, 0)
    assert [r["q_target"] for r in result["records"] if r["status"] == "PASS"] == [10, 12, 20]
    assert len(backend[1]) == 3
    assert result["REAL_EMX_VALIDATION"] == "NOT_RUN" and result["q_emx"] is None
    assert result["production_campaign_membership"] is result["repairs_performed"] is False
    assert result["source_pins"]["private_config"] == packet[1]["runtime"]["configuration"]
    with Path(result["calibre_input"]["path"]).open() as stream:
        rows = list(csv.DictReader(stream))
    assert tuple(rows[0]) == audit.CALIBRE_FIELDS and len(rows) == 3
    for record in result["records"]:
        assert record["original_record"] == packet[2][record["q_target"] - 10]
        if record["status"] == "ANALYTIC_FAIL":
            assert record["audit_attempted"] is record["cadence_routed"] is record["calibre_eligible"] is False
        else:
            geometry_audit = audit.read_json(record["geometry_audit"]["path"])
            assert record["port_manifest"] in geometry_audit["original_artifacts"]
            assert record["gds"] in geometry_audit["original_artifacts"]
            for name in ("geometry_audit", "port_manifest", "gds"):
                audit.verify_pin(record[name])
    with pytest.raises(FileExistsError):
        audit.audit_request(packet[0], tmp_path / "output")


@pytest.mark.parametrize("field,value", [("request_id", "wrong"), ("frequency_ghz", 8),
    ("model_id", "wrong"), ("dataset_scope", "DEVELOPMENT_5K_NOT_FORMAL_10K"), ("q_proxy", 11)])
def test_record_context_mismatch_rejected_before_backend(packet, backend, field, value):
    packet[2][3][field] = value
    with pytest.raises(audit.AuditInputError, match="record/context mismatch"):
        audit.load_request(repin(packet))
    assert not backend[1]


@pytest.mark.parametrize("field", ["frequency_ghz", "model_id"])
def test_freeze_identity_mismatch(packet, field):
    path = Path(packet[1]["source_pins"]["qscan_freeze"]["path"])
    freeze = audit.read_json(path)
    freeze[field] = 8 if field == "frequency_ghz" else "wrong"
    packet[1]["source_pins"]["qscan_freeze"] = write(path, freeze)
    with pytest.raises(audit.AuditInputError, match="freeze/request mismatch"):
        audit.load_request(repin(packet))


@pytest.mark.parametrize("mutation,match", [
    (lambda rows: rows.pop(), "eleven"),
    (lambda rows: rows.__setitem__(1, rows[0]), "eleven"),
    (lambda rows: rows[0].update(candidate_id="foreign"), "candidate identity"),
    (lambda rows: rows[0].update(parameter_geometry_hash="wrong"), "parameter geometry hash"),
    (lambda rows: rows[0].update(analytic_grid="True"), "analytic_grid"),
    (lambda rows: rows[0].update(geometry_fields=list(reversed(audit.GEOMETRY_FIELDS))), "geometry field"),
    (lambda rows: rows[0].update(emx_status="PASS"), "predate fresh EMX"),
])
def test_metadata_failures(packet, mutation, match):
    mutation(packet[2])
    with pytest.raises(audit.AuditInputError, match=match):
        audit.load_request(repin(packet))


def test_pin_tamper_preserves_request_failure(packet, tmp_path):
    Path(packet[1]["source_pins"]["eleven_records"]["path"]).write_text("tampered")
    with pytest.raises(audit.AuditInputError, match="pin mismatch"):
        audit.audit_request(packet[0], tmp_path / "failed")
    assert audit.read_json(tmp_path / "failed/REQUEST_FAILURE.json")["status"] == "FAIL"


@pytest.mark.parametrize("role", ["configuration", "foundry_contract", "gds_identity"])
def test_repinning_changed_physical_contract_does_not_authorize_it(packet, role):
    runtime = packet[1]["runtime"]
    container = runtime if role in runtime else runtime["core_sources"]
    path = Path(container[role]["path"])
    container[role] = write(path, {"changed": True})
    with pytest.raises(audit.AuditInputError, match="differs from proven"):
        audit.load_request(repin(packet))


@pytest.mark.parametrize("route", ["../outside", "/outside"])
def test_route_escape_rejected(packet, route):
    routes = packet[1]["cadence"]["routes"]
    routes[next(iter(routes))] = route
    with pytest.raises(audit.AuditInputError, match="unsafe relative"):
        audit.load_request(repin(packet))


def test_analytic_failed_candidate_cannot_be_routed(packet):
    packet[1]["cadence"]["routes"][packet[2][1]["candidate_id"]] = "parallel_shards/shard_003"
    with pytest.raises(audit.AuditInputError, match="exactly analytic-pass"):
        audit.load_request(repin(packet))


def test_duplicate_shard_rejected(packet):
    routes = packet[1]["cadence"]["routes"]
    names = list(routes)
    routes[names[1]] = routes[names[0]]
    with pytest.raises(audit.AuditInputError, match="duplicate candidate shard"):
        audit.load_request(repin(packet))


@pytest.mark.parametrize("identity_field", ["candidate_id", "candidate_id_sha256", "candidate_geometry_identity_sha256"])
def test_queue_identity_failure_does_not_block_other_candidates(packet, backend, tmp_path, identity_field):
    request = physical_fixture(packet)
    path = next(iter(request.routes.values())) / "dataset_rows.csv"
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    rows[0]["queue__" + identity_field] = "foreign"
    csv_write(path, rows)
    result = audit.audit_request(packet[0], tmp_path / "out")
    assert (result["status"], result["N_audit_pass"], result["N_audit_fail"]) == ("PARTIAL_FAIL", 2, 1)
    assert len(backend[1]) == 2
    assert result["records"][0]["status"] == "FAIL"
    assert result["records"][-1]["status"] == "PASS"


def test_structural_mismatch_is_not_repaired_or_indexed(packet, backend, tmp_path):
    physical_fixture(packet)
    backend[0].identity.gds_structural_identity = lambda p: dict(overall_status="PASS",
        layer_union_sha256=p.name, label_pin_set_sha256="x", structural_sha256="x")
    result = audit.audit_request(packet[0], tmp_path / "out")
    assert result["N_audit_fail"] == 3 and "calibre_input" not in result
    assert result["records"][0]["failed_checks"] == ["topology_pass"]


def test_summary_geometry_mismatch_skips_actual_audit(packet, backend, tmp_path):
    request = physical_fixture(packet)
    path = next(iter(request.routes.values())) / "evaluations/synthetic-evaluation/summary.json"
    summary = audit.read_json(path)
    summary["geometry"][audit.GEOMETRY_FIELDS[0]] += 1
    write(path, summary)
    result = audit.audit_request(packet[0], tmp_path / "out")
    assert len(backend[1]) == 2 and result["N_audit_fail"] == 1


def test_missing_shard_continues_without_replacement(packet, backend, tmp_path):
    result = audit.audit_request(packet[0], tmp_path / "out")
    assert result["N_audit_fail"] == 3 and result["N_analytic_fail"] == 8
    assert "calibre_input" not in result and not backend[1]


def test_all_analytic_fail_never_loads_backend(packet, monkeypatch, tmp_path):
    for row in packet[2]:
        row["analytic_grid"] = False
    packet[1]["cadence"]["routes"] = {}
    repin(packet)
    monkeypatch.setattr(audit, "_load_backend", lambda _: pytest.fail("backend must not be loaded"))
    result = audit.audit_request(packet[0], tmp_path / "out")
    assert result["N_analytic_fail"] == 11 and result["N_audit_attempted"] == 0
    assert result["status"] == "NO_ELIGIBLE_CANDIDATES"
    assert "calibre_input" not in result


def test_original_mutation_prevents_candidate_pass(packet, backend, tmp_path):
    physical_fixture(packet)
    old = backend[0].identity.gds_timestamp_normalized_sha256

    def mutate(path):
        path.write_bytes(b"SYNTHETIC_MUTATION_TEST")
        return old(path)

    backend[0].identity.gds_timestamp_normalized_sha256 = mutate
    result = audit.audit_request(packet[0], tmp_path / "out")
    assert result["N_audit_fail"] == 3 and "calibre_input" not in result
    assert all("original artifacts changed" in r["error"] for r in result["records"] if r["status"] == "FAIL")
    assert any(Path(p["path"]).name == "INPUT_BINDING.json" for p in result["records"][0]["partial_evidence"])


def test_input_output_overlap_rejected_before_write(packet):
    out = Path(packet[1]["cadence"]["root"]) / "audit"
    with pytest.raises(audit.AuditInputError, match="Cadence source"):
        audit.audit_request(packet[0], out)
    assert not out.exists()


def test_validate_only_no_output_or_backend(packet, monkeypatch, capsys):
    monkeypatch.setattr(audit, "_load_backend", lambda _: pytest.fail("metadata validation must not load backend"))
    audit.main(["--request", str(packet[0]), "--validate-only"])
    assert "METADATA_VALIDATED_GDS_NOT_AUDITED" in capsys.readouterr().out


def test_optimized_python_rejected(packet):
    result = subprocess.run([sys.executable, "-B", "-O", "-m", audit.__name__, "--request", str(packet[0]),
                             "--validate-only"], capture_output=True, text=True)
    assert result.returncode != 0 and "Python optimization is forbidden" in result.stderr
