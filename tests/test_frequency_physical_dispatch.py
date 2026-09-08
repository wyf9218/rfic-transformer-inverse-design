"""Independent synthetic dispatcher tests; subprocesses are always stubbed."""
import copy
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from research.broadband56_nn import frequency_physical_dispatch as dispatch
from research.broadband56_nn.frequency_research_emx import ResearchEmxError


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return dispatch.pin(path)


@pytest.fixture
def request_rows():
    job = dict(request_id="synthetic-request", frequency_ghz=7, model_id="f07-synthetic",
               dataset_scope="FORMAL_10K", q_proxy=14, target_source="HELDOUT_TRIPLE_AUDIT")
    rows = [dict(**job, candidate_id=f"synthetic-request-q{q}", q_target=q, analytic_grid=True) for q in range(10, 21)]
    features = [dict(**job, candidate_id=r["candidate_id"], q_requested=r["q_target"],
        valid_for_strict_comparison=True, normalized_response_score=abs(r["q_target"] - 14),
        strict_joint_hit=r["q_target"] == 14, target_relative_absolute_percent=[1, 2, 3, 4]) for r in rows]
    return job, rows, features


def test_eleven_valid_only_optimum_and_tie_smaller_q(request_rows):
    job, rows, features = request_rows
    features[3]["normalized_response_score"] = 0
    result = dispatch.summarize_request(job, rows, features)
    assert result["q_emx"] == 13 and result["selected_q_agrees_with_emx"] is False
    assert result["N_logical"] == result["N_strict_valid"] == 11
    assert result["q_proxy"] == 14 and result["q_proxy_strict_joint_hit"] is True


def test_partial_solutions_no_best_survivor_and_qproxy_not_run(request_rows):
    job, rows, features = request_rows
    rows[-1]["analytic_grid"] = False
    result = dispatch.summarize_request(job, rows, features[:4])
    assert result["q_emx"] is None and result["N_not_solved"] == 7
    assert result["N_analytic_fail"] == 1 and result["q_proxy_real_validation"] == "NOT_RUN"
    assert result["q_proxy_score"] is result["selected_q_agrees_with_emx"] is None


def test_eleven_solved_one_invalid_is_not_eleven_valid(request_rows):
    job, rows, features = request_rows
    features[0]["valid_for_strict_comparison"] = False
    result = dispatch.summarize_request(job, rows, features)
    assert result["N_solved"] == 11 and result["N_strict_valid"] == 10 and result["q_emx"] is None


@pytest.mark.parametrize("mutation", [lambda rows: rows.append(dict(rows[-1])),
    lambda rows: rows.__setitem__(0, dict(rows[1])), lambda rows: rows.reverse()])
def test_original_eleven_grid_is_required(request_rows, mutation):
    job, rows, features = request_rows
    mutation(rows)
    with pytest.raises(ResearchEmxError):
        dispatch.summarize_request(job, rows, features)


@pytest.mark.parametrize("field,value", [("candidate_id", "foreign"), ("frequency_ghz", 8),
    ("model_id", "wrong"), ("dataset_scope", "DEVELOPMENT_5K_NOT_FORMAL_10K"), ("q_proxy", 13),
    ("request_id", "foreign")])
def test_feature_context_mismatch_rejected(request_rows, field, value):
    job, rows, features = request_rows
    features[0][field] = value
    with pytest.raises(ResearchEmxError):
        dispatch.summarize_request(job, rows, features)


def test_optional_legacy_feature_request_id_absence_is_supported(request_rows):
    job, rows, features = request_rows
    for row in features:
        row.pop("request_id")
    assert dispatch.summarize_request(job, rows, features)["q_emx"] == 14


def test_analytic_failed_feature_and_duplicate_feature_rejected(request_rows):
    job, rows, features = request_rows
    with pytest.raises(ResearchEmxError, match="Duplicate"):
        dispatch.summarize_request(job, rows, features + [features[0]])
    rows[0]["analytic_grid"] = False
    with pytest.raises(ResearchEmxError):
        dispatch.summarize_request(job, rows, features)


def test_relocate_exact_original_pin_and_bytes(tmp_path):
    original = write(tmp_path / "original.json", {"synthetic": True})
    payload = tmp_path / "payload"
    copied = write(payload / "copied.json", {"synthetic": True})
    item = dict(original=original, relative_path="copied.json", sha256=copied["sha256"], bytes=copied["bytes"])
    assert dispatch.relocate(original, {"relocations": [item]}, payload) == copied
    with pytest.raises(ResearchEmxError, match="missing/duplicated"):
        dispatch.relocate(original, {"relocations": [item, item]}, payload)
    for relative in ("../original.json", "/absolute"):
        with pytest.raises(ResearchEmxError, match="Unsafe"):
            dispatch.relocate(original, {"relocations": [dict(item, relative_path=relative)]}, payload)
    write(payload / "copied.json", {"changed": True})
    with pytest.raises(ResearchEmxError, match="byte identity"):
        dispatch.relocate(original, {"relocations": [item]}, payload)


def test_generated_bindings_reused_only_if_exact(tmp_path):
    path = tmp_path / "binding.json"
    first = dispatch.frozen_json(path, {"original": True})
    assert dispatch.frozen_json(path, {"original": True}) == first
    with pytest.raises(ResearchEmxError, match="differs"):
        dispatch.frozen_json(path, {"original": False})
    assert dispatch.read_json(path) == {"original": True}


@pytest.fixture
def runner(tmp_path, monkeypatch):
    value = object.__new__(dispatch.Dispatcher)
    value.config_pin = write(tmp_path / "config.json", {"synthetic": True})
    value.config = {"source_pins": [], "repo": str(tmp_path), "out": str(tmp_path / "queue")}
    value.out = Path(value.config["out"])
    value.environment = {"PYTHONDONTWRITEBYTECODE": "1"}
    value.fd, value.queue_fd = 101, 102
    value.admit = lambda: {"SYNTHETIC": True}
    value.event = lambda *a, **k: None
    monkeypatch.setattr(dispatch.subprocess, "Popen", lambda *a, **k: pytest.fail("unexpected native process"))
    return value


def test_partial_native_output_is_never_relaunched(runner, tmp_path):
    output = tmp_path / "native"
    output.mkdir()
    with pytest.raises(ResearchEmxError, match="duplicate dispatch refused"):
        runner.process(tmp_path, "stage", ["synthetic"], output, output / "done.json")


def test_successful_stage_terminal_is_reused_and_inherited_lease_normalized(runner, tmp_path, monkeypatch):
    output, completion = tmp_path / "native", tmp_path / "native/done.json"
    calls = []

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        write(completion, {"synthetic": True})
        return SimpleNamespace(pid=99999999, wait=lambda: 0)

    monkeypatch.setattr(dispatch.subprocess, "Popen", popen)
    command = ["synthetic", "--inherited-global-lease-fd", "101"]
    first = runner.process(tmp_path, "stage", command, output, completion)
    second = runner.process(tmp_path, "stage", ["synthetic", "--inherited-global-lease-fd", "333"], output, completion)
    assert first == second and len(calls) == 1
    assert calls[0][0][:3] == ["nice", "-n", "19"]
    assert calls[0][1]["pass_fds"] == (101, 102)
    write(completion, {"tampered": True})
    with pytest.raises(ResearchEmxError):
        runner.process(tmp_path, "stage", command, output, completion)
    assert len(calls) == 1


def test_failed_native_terminal_and_unreceipted_log_are_not_retried(runner, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch.subprocess, "Popen", lambda *a, **k: (
        calls.append(a) or SimpleNamespace(pid=99999999, wait=lambda: 2)))
    output = tmp_path / "native"
    with pytest.raises(ResearchEmxError, match="did not finish"):
        runner.process(tmp_path, "stage", ["synthetic"], output, output / "done.json")
    assert dispatch.read_json(tmp_path / "stage_PROCESS.json")["returncode"] == 2
    with pytest.raises(ResearchEmxError, match="Previous process failed"):
        runner.process(tmp_path, "stage", ["synthetic"], output, output / "done.json")
    (tmp_path / "unreceipted.log").write_text("SYNTHETIC partial log")
    with pytest.raises(ResearchEmxError, match="unreceipted"):
        runner.process(tmp_path, "unreceipted", ["synthetic"], output, output / "done.json")
    assert len(calls) == 1


def test_terminal_queue_reuses_without_global_lease_or_native_calls(runner, tmp_path, monkeypatch):
    terminal = {"status": "FINITE_ROUNDROBIN_COMPLETE", "config": runner.config_pin, "N_requests": 320}
    write(runner.out / "TERMINAL.json", terminal)
    monkeypatch.setattr(dispatch, "global_lease", lambda *a: pytest.fail("no lease needed for terminal reuse"))
    assert runner.run() == terminal


def test_completed_unreceipted_cadence_is_adopted_without_native_rerun(runner, tmp_path):
    output, completion = tmp_path / "native", tmp_path / "native/complete.json"
    command = ["SYNTHETIC_CADENCE_NEVER_EXECUTED"]
    intent = dict(config=runner.config_pin, command=command, output=str(output), completion=str(completion))
    write(tmp_path / "cadence_INTENT.json", intent)
    write(completion, {"overall_status": "PASS"})
    (tmp_path / "cadence.log").write_text("SYNTHETIC completed child")
    result = runner.process(tmp_path, "cadence", command, output, completion)
    assert result["recovery"]["status"] == "COMPLETE_CHILD_TERMINAL_ADOPTED_NO_NATIVE_RERUN"
    assert dispatch.read_json(tmp_path / "cadence_PROCESS.json") == result


def test_owned_but_incomplete_native_output_is_not_adopted_or_rerun(runner, tmp_path):
    output, completion = tmp_path / "native", tmp_path / "native/complete.json"
    output.mkdir()
    command = ["SYNTHETIC_NEVER_EXECUTED"]
    write(tmp_path / "cadence_INTENT.json", dict(config=runner.config_pin, command=command,
        output=str(output), completion=str(completion)))
    with pytest.raises(ResearchEmxError, match="Partial native output"):
        runner.process(tmp_path, "cadence", command, output, completion)


def test_existing_emx_solver_recovery_is_extract_only(runner, tmp_path, monkeypatch):
    output, completion = tmp_path / "native", tmp_path / "native/features/FEATURE_RECEIPT.json"
    command = ["python", "-B", "-m", "synthetic", "run", "--request", "request.json",
               "--output", str(output), "--inherited-global-lease-fd", "101"]
    semantic = list(command)
    semantic[-1] = "<inherited-global-lease-fd>"
    write(tmp_path / "emx_q10_INTENT.json", dict(config=runner.config_pin, command=semantic,
        output=str(output), completion=str(completion)))
    write(output / "SOLVER_RECEIPT.json", {"SYNTHETIC_EXISTING_SOLVER": True})
    (tmp_path / "emx_q10.log").write_text("SYNTHETIC previous solver")
    commands = []

    def extract(command, **kwargs):
        commands.append(command)
        write(completion, {"SYNTHETIC_FEATURES": True})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(dispatch.subprocess, "run", extract)
    result = runner.process(tmp_path, "emx_q10", command, output, completion)
    assert result["recovery"]["status"] == "EXISTING_SOLVER_REUSED_EXTRACTION_ONLY"
    assert len(commands) == 1 and "extract" in commands[0] and "run" not in commands[0]
    assert "--inherited-global-lease-fd" not in commands[0]


@pytest.fixture
def queue_config(tmp_path):
    source = write(tmp_path / "payload/record.json", {"synthetic": True})
    original = {**source, "path": "/original/record.json"}
    relocation = {"relocations": [dict(original=original, relative_path="record.json",
                                      sha256=source["sha256"], bytes=source["bytes"])]}
    jobs = [dict(request_id=f"synthetic-{n}", dispatch_order=n, round_index=n // 16,
        frequency_ghz=dispatch.ORDER[n % 16], original_request_order=n // 16,
        N_logical_candidates=11, N_analytic_pass=9, N_analytic_failed=2,
        route="REUSE_EXISTING_FIRST15_GDS_CALIBRE" if n == 0 else "INDEPENDENT_RESEARCH",
        pending_receipt=original, candidate_records=original, candidate_csv=original) for n in range(320)]
    plan = dict(frequency_order=dispatch.ORDER, jobs=jobs, max_requests_per_frequency=20, max_global_emx_concurrency=4)
    config = dict(schema="frequency_physical_finite_dispatch.v1", max_global_solvers=1, cadence_jobs=1,
        production_modified=False, resource_budget={"cpu_per_solver": 2, "max_global_solvers": 1}, source_pins=[],
        payload_root=str(tmp_path / "payload"), relocation_manifest=write(tmp_path / "relocation.json", relocation),
        dispatch_manifest=write(tmp_path / "plan.json", plan))
    return config, plan


def test_frozen_queue_order_and_bounded_resource_contract(queue_config):
    config, plan = queue_config
    assert dispatch.validate(config)[0] == plan
    four = copy.deepcopy(config)
    four["max_global_solvers"] = four["resource_budget"]["max_global_solvers"] = 4
    assert dispatch.validate(four)[0] == plan
    for key, value in (("max_global_solvers", 0), ("max_global_solvers", 5), ("cadence_jobs", 2), ("production_modified", True)):
        bad = copy.deepcopy(config)
        bad[key] = value
        with pytest.raises(ResearchEmxError):
            dispatch.validate(bad)


@pytest.mark.parametrize("mutation", [lambda p: p["jobs"].__setitem__(1, p["jobs"][2]),
    lambda p: p["jobs"][17].update(round_index=0),
    lambda p: p["jobs"][20].update(N_analytic_pass=10),
    lambda p: p["jobs"][0].update(route="REGENERATE_FIRST15")])
def test_queue_semantic_corruption_not_fixed_by_repin(queue_config, mutation):
    config, plan = queue_config
    mutation(plan)
    config["dispatch_manifest"] = write(Path(config["dispatch_manifest"]["path"]), plan)
    with pytest.raises(ResearchEmxError):
        dispatch.validate(config)


@pytest.mark.parametrize("workers", [0, 5, True, 1.5])
def test_bounded_pool_rejects_unsupported_concurrency(workers):
    with pytest.raises(ResearchEmxError):
        dispatch.bounded_map(lambda x: x, [1], workers)


def test_bounded_pool_runs_each_item_once_under_four_active():
    state = {"active": 0, "maximum": 0, "seen": []}
    lock = threading.Lock()

    def task(item):
        with lock:
            state["active"] += 1
            state["maximum"] = max(state["maximum"], state["active"])
            state["seen"].append(item)
        time.sleep(.003)  # Bounded local test only; no simulator or remote wait.
        with lock:
            state["active"] -= 1
        return item * 2

    assert sorted(dispatch.bounded_map(task, range(12), 4)) == [n * 2 for n in range(12)]
    assert 1 <= state["maximum"] <= 4 and state["active"] == 0
    assert sorted(state["seen"]) == list(range(12))


def test_pool_failure_cancels_not_started_and_never_submits_replacements(monkeypatch):
    submitted = []

    class Future:
        def __init__(self, function, item):
            self.function, self.item, self.cancelled = function, item, False

        def result(self):
            return self.function(self.item)

        def cancel(self):
            self.cancelled = True
            return True

    class Pool:
        def __init__(self, max_workers):
            assert max_workers == 2

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, function, item):
            future = Future(function, item)
            submitted.append(future)
            return future

    monkeypatch.setattr(dispatch, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(dispatch, "wait", lambda active, **k: ({submitted[0]}, active - {submitted[0]}))

    def fail(item):
        raise RuntimeError("SYNTHETIC_CHILD_FAILURE")

    with pytest.raises(RuntimeError, match="SYNTHETIC_CHILD_FAILURE"):
        dispatch.bounded_map(fail, range(20), 2)
    assert len(submitted) == 2 and submitted[1].cancelled is True


def test_four_worker_resource_reserves_not_single_worker_threshold(tmp_path, monkeypatch):
    config = dict(out=str(tmp_path), max_global_solvers=4,
        resource_budget={"min_memory_available_bytes": 8 * 1024**3, "min_disk_free_bytes": 20 * 1024**3})
    observed = {"memory": 32 * 1024**3, "disk": 80 * 1024**3, "load": 0, "cpu": 16}
    original_read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda p, *a, **k:
        f'MemAvailable: {observed["memory"] // 1024} kB\n' if str(p) == "/proc/meminfo" else original_read(p, *a, **k))
    monkeypatch.setattr(dispatch.os, "cpu_count", lambda: observed["cpu"])
    monkeypatch.setattr(dispatch.os, "getloadavg", lambda: (observed["load"], 0, 0))
    monkeypatch.setattr(dispatch.shutil, "disk_usage", lambda p: SimpleNamespace(free=observed["disk"]))
    assert dispatch.resources(config)[0] is True
    for key, value in (("memory", 32 * 1024**3 - 1024), ("disk", 80 * 1024**3 - 1), ("load", 8), ("cpu", 7)):
        saved = observed[key]
        observed[key] = value
        assert dispatch.resources(config)[0] is False
        observed[key] = saved
