"""Synthetic receipt/dispatch tests only. Exporters are stubs; no plot/model calls."""
import csv
import fcntl
import json
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.broadband56_nn import frequency_physical_export_batch as batch


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return batch.pin(path)


def trio(folder, stem):
    folder.mkdir(parents=True, exist_ok=True)
    items = []
    for suffix in ("png", "svg", "pdf"):
        path = folder / (stem + "." + suffix)
        path.write_text("synthetic export " + suffix)
        items.append(batch.pin(path))
    return items


class Frame:
    def __init__(self, root):
        self.root = root; self.out = root / "batch"; self.inputs = root / "inputs"
        self.inputs.mkdir()
        self.rid = "synthetic-f06-request-000002"
        self.status = [{"request_id": self.rid, "status": "ACCOUNTED", "q_proxy": "10", "q_emx": ""}]
        self.status += [{"request_id": f"pending-{i}", "status": "PENDING", "q_proxy": "10", "q_emx": ""}
                        for i in range(319)]
        status_path = self.inputs / "REQUEST_STATUS.csv"
        with status_path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.status[0])); writer.writeheader(); writer.writerows(self.status)
        summary = put(self.inputs / "SUMMARY.json", dict(schema="frequency_physical_statistics.v1",
            status="PARTIAL_SNAPSHOT", N_planned_requests=320, N_accounted_requests=1))
        files = [summary, batch.pin(status_path)]
        manifest = put(self.inputs / "MANIFEST.json", dict(schema="frequency_physical_statistics_manifest.v1", artifacts=files))
        sums = self.inputs / "SHA256SUMS"
        sums.write_text("".join(p["sha256"] + "  " + Path(p["path"]).name + "\n" for p in files + [manifest]))
        self.stats = put(self.inputs / "STATS_RECEIPT.json", dict(schema="frequency_physical_statistics_receipt.v1",
            status="PUBLISHED", N_accounted_requests=1, N_original_candidates=3520,
            summary=summary, manifest=manifest, sha256sums=batch.pin(sums)))
        self.rows = [dict(request_id=self.rid, q_target=q, stage="GDS_FAIL" if q == 11 else "SOLVED_STRICT_VALID")
                     for q in range(10, 21)]
        self.source = put(self.inputs / "SOURCE_ROWS.json", dict(request=self.status[0], rows=self.rows))
        self.entry = dict(request_id=self.rid, signature="a"*64, source=self.source,
                          exports=trio(self.inputs / "originals", "proxy_emx_score_by_q") +
                                  trio(self.inputs / "originals", "target_percent_by_q"))
        self.figures = put(self.inputs / "FIGURES_RECEIPT.json", dict(
            schema="frequency_physical_statistics_figures_receipt.v1", status="COMPLETE",
            statistics=self.stats, N_accounted_requests=1, N_original_requests=320, N_original_candidates=3520,
            request_figures=[self.entry], aggregate_exports=[]))
        publication = put(self.inputs / "PUBLICATION.json", dict(schema="frequency_physical_capture_publication.v1", status="PUBLISHED"))
        self.snapshot = put(self.inputs / "SNAPSHOT_RECEIPT.json", dict(status="PUBLISHED", remote_modified=False,
            statistics=self.stats, figures=self.figures, publication=publication))
        config = put(self.inputs / "CONFIG.json", {"synthetic": True})
        self.final_path = self.inputs / "FINAL_RECEIPT.json"
        self.final = dict(status="PARTIAL", end_reason="DEADLINE_PARTIAL", N_accounted_requests=1,
            N_original_requests=320, N_original_candidates=3520, N_pending_requests=319,
            simulation_or_training_started=False, remote_modified=False,
            latest_snapshot=self.snapshot, configuration=config, error=None)
        put(self.final_path, self.final)
        self.qa_path = self.inputs / "OLD_QA.json"; put(self.qa_path, {"status": "NO-GO", "figures": []})
        self.contract_path = self.inputs / "CONTRACT.json"

    def contract(self, kinds=("score",)):
        folder = Path(batch.__file__).parent
        put(self.contract_path, dict(schema="frequency_physical_request_figure_contract.v3", no_clobber=True,
            figures_receipt=self.figures, original_visual_qa=batch.pin(self.qa_path),
            v1_renderer_sha256=batch.pin(folder / "frequency_physical_statistics_figures.py")["sha256"],
            v2_helper_sha256=batch.pin(folder / "frequency_physical_request_score_v2.py")["sha256"],
            jobs=[dict(request_id=self.rid, kind=kind) for kind in kinds]))
        return self.contract_path

    def approved(self, kind="score"):
        exports = {Path(p["path"]).suffix[1:]: p for p in trio(self.inputs / "approved", kind)}
        figure = dict(request_id=self.rid, kind=kind, source=self.source, signature=self.entry["signature"],
                      status="GO", exports=exports)
        qa = put(self.inputs / "VISUAL_GO.json", dict(status="GO", figures=[figure]))
        return put(self.inputs / "APPROVED_EXPORTS.json", dict(status="GO", visual_go=qa,
            exports=[{k: figure[k] for k in ("request_id", "kind", "source", "signature")} | exports]))["path"]

    def plan(self, **kwargs):
        return batch.plan(self.final_path, self.out, **kwargs)


class ExportStub:
    def __init__(self, *, bad_statistics=False, empty_selection=False):
        self.calls = 0; self.bad_statistics = bad_statistics; self.empty_selection = empty_selection

    def __call__(self, job, plan, destination):
        self.calls += 1
        destination.mkdir(parents=True)
        exports = [] if self.empty_selection else trio(destination, "synthetic")
        renderer = next(p for p in plan["implementation"] if Path(p["path"]).stem == batch.MODULES[job["type"]])
        stats = plan["final"] if self.bad_statistics else plan["statistics"]
        common = dict(statistics=stats, exports=exports)
        if job["type"] == "request":
            receipt = dict(schema="frequency_physical_request_figure_repair_receipt.v3",
                status="EXPORTED_AWAITING_INDEPENDENT_VISUAL_ACCEPTANCE", implementation=renderer,
                request_id=job["request_id"], kind=job["kind"], contract=job["contract"],
                source=job["original"]["source"], signature=job["original"]["signature"],
                source_or_numerical_values_modified=False, consumer_or_existing_source_modified=False,
                model_calls=0, remote_calls=0, **common)
        elif job["type"] == "metric":
            receipt = dict(schema="frequency_physical_metric_panels_receipt.v2", status="COMPLETE", renderer=renderer,
                scope=job["scope"], estimand=job["estimand"], source_data_modified=False, model_calls=0, remote_calls=0, **common)
        else:
            receipt = dict(schema="frequency_selection_figures_v3_receipt.v1",
                status="RENDERED_INDEPENDENT_VISUAL_QA_REQUIRED", source=renderer,
                metrics_recomputed=False, model_or_simulator_calls=0, **common)
        put(destination / batch.AUTHOR_RECEIPTS[job["type"]], receipt)


class ExportBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name); self.frame = Frame(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_plan_read_only_without_render_import(self):
        with patch.object(batch.importlib, "import_module", side_effect=AssertionError("plot import")):
            value = self.frame.plan()
        self.assertEqual(value["jobs"], [])
        self.assertFalse(self.frame.out.exists())
        self.assertFalse((self.root / ".batch.export.lock").exists())
        self.assertFalse(value["physical_frame_complete"])
        self.assertEqual({x["status"] for x in value["request_exports"]}, {"ORIGINAL_UNREVIEWED"})

    def test_false_complete_rejected(self):
        put(self.frame.final_path, dict(self.frame.final, status="COMPLETE", end_reason="ALL320_ACCOUNTED"))
        with self.assertRaisesRegex(ValueError, "false all320"):
            self.frame.plan()

    def test_no_published_snapshot_rejected(self):
        put(self.frame.final_path, dict(self.frame.final, latest_snapshot=None))
        with self.assertRaisesRegex(ValueError, "no published"):
            self.frame.plan()

    def test_snapshot_hash_change_rejected(self):
        Path(self.frame.snapshot["path"]).write_text("{}")
        with self.assertRaisesRegex(ValueError, "pin changed"):
            self.frame.plan()

    def test_source_hash_change_after_plan_rejected_before_output(self):
        value = self.frame.plan()
        Path(self.frame.source["path"]).write_text("{}")
        with self.assertRaisesRegex(ValueError, "pin changed"):
            batch.run(value)
        self.assertFalse(self.frame.out.exists())

    def test_denominator_changed_rejected(self):
        put(self.frame.final_path, dict(self.frame.final, N_pending_requests=0))
        with self.assertRaisesRegex(ValueError, "denominator"):
            self.frame.plan()

    def test_contract_duplicate_jobs_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate requested"):
            self.frame.plan(request_contract=self.frame.contract(("score", "score")))

    def test_contract_wrong_terminal_snapshot_rejected(self):
        path = self.frame.contract(); c = batch.read(path); c["figures_receipt"] = self.frame.stats; put(path, c)
        with self.assertRaisesRegex(ValueError, "bind terminal"):
            self.frame.plan(request_contract=path)

    def test_only_explicit_jobs_run_and_duplicate_run_adopts_receipts(self):
        value = self.frame.plan(request_contract=self.frame.contract(), aggregates=["formal-selected", "selection"])
        stub = ExportStub(); receipt = batch.run(value, executor=stub)
        again = batch.run(value, executor=stub)
        self.assertEqual(stub.calls, 3); self.assertEqual(receipt, again)
        index = batch.read(self.frame.out / "EXPORT_INDEX.json")
        self.assertEqual([r["status"] for r in index["request_exports"]],
                         ["EXPORTED_AWAITING_INDEPENDENT_VISUAL_ACCEPTANCE", "ORIGINAL_UNREVIEWED"])
        self.assertFalse(index["physical_frame_complete"]); self.assertFalse(index["grants_visual_GO"])

    def test_approved_exact_qa_reused_without_export(self):
        approved = self.frame.approved()
        value = self.frame.plan(request_contract=self.frame.contract(), approved_indexes=[approved])
        self.assertEqual(value["jobs"], [])
        stub = ExportStub(); batch.run(value, executor=stub)
        self.assertEqual(stub.calls, 0)
        self.assertEqual(batch.read(self.frame.out / "EXPORT_INDEX.json")["request_exports"][0]["status"], "REUSED_APPROVED")

    def test_approved_signature_mismatch_rejected(self):
        path = self.frame.approved(); index = batch.read(path); index["exports"][0]["signature"] = "b"*64; put(Path(path), index)
        with self.assertRaisesRegex(ValueError, "signature differs"):
            self.frame.plan(approved_indexes=[path])

    def test_index_go_without_exact_qa_rejected(self):
        path = self.frame.approved(); index = batch.read(path)
        index["visual_go"] = put(self.frame.inputs / "NOT_REVIEWED.json", {"status": "GO", "figures": []}); put(Path(path), index)
        with self.assertRaisesRegex(ValueError, "QA lacks exact"):
            self.frame.plan(approved_indexes=[path])

    def _interrupted(self, value):
        self.frame.out.mkdir(); put(self.frame.out / "PLAN.json", value)
        job = value["jobs"][0]; folder = self.frame.out / job["id"]; folder.mkdir()
        put(folder / "INTENT.json", dict(plan_sha256=batch.digest(value), job=job))
        return job, folder

    def test_complete_author_receipt_recovers_without_new_export(self):
        value = self.frame.plan(request_contract=self.frame.contract())
        job, folder = self._interrupted(value); stub = ExportStub(); stub(job, value, folder / "render")
        batch.run(value, executor=lambda *args: self.fail("repeated export"))
        self.assertTrue((folder / "JOB_RECEIPT.json").exists()); self.assertEqual(stub.calls, 1)

    def test_partial_artifact_is_sticky_failure_without_retry(self):
        value = self.frame.plan(request_contract=self.frame.contract())
        job, folder = self._interrupted(value); partial = folder / "render" / "partial.png"
        partial.parent.mkdir(); partial.write_bytes(b"original failure bytes")
        stub = ExportStub()
        for _ in range(2):
            with self.assertRaises((FileNotFoundError, ValueError)):
                batch.run(value, executor=stub)
        self.assertEqual(stub.calls, 0); self.assertEqual(partial.read_bytes(), b"original failure bytes")
        self.assertTrue((folder / "FAILURE.json").exists())

    def test_intent_without_receipt_not_silently_retried(self):
        value = self.frame.plan(request_contract=self.frame.contract())
        self._interrupted(value)
        with self.assertRaises(FileNotFoundError):
            batch.run(value, executor=lambda *args: self.fail("ambiguous intent repeated"))

    def test_wrong_author_stats_fail_and_preserve(self):
        value = self.frame.plan(request_contract=self.frame.contract()); stub = ExportStub(bad_statistics=True)
        with self.assertRaisesRegex(ValueError, "statistics differs"):
            batch.run(value, executor=stub)
        with self.assertRaisesRegex(ValueError, "failure retained"):
            batch.run(value, executor=stub)
        self.assertEqual(stub.calls, 1)

    def test_completed_export_tamper_does_not_reexport(self):
        value = self.frame.plan(request_contract=self.frame.contract()); stub = ExportStub(); batch.run(value, executor=stub)
        artifact = self.frame.out / value["jobs"][0]["id"] / "render" / "synthetic.png"
        artifact.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "pin changed"):
            batch.run(value, executor=stub)
        self.assertEqual(stub.calls, 1); self.assertEqual(artifact.read_bytes(), b"tampered")

    def test_deleted_completed_job_cannot_look_fresh(self):
        value = self.frame.plan(request_contract=self.frame.contract()); stub = ExportStub(); batch.run(value, executor=stub)
        shutil.rmtree(self.frame.out / value["jobs"][0]["id"])
        with self.assertRaises(FileNotFoundError):
            batch.run(value, executor=stub)
        self.assertEqual(stub.calls, 1)

    def test_existing_unknown_output_not_touched(self):
        value = self.frame.plan(); self.frame.out.mkdir(); (self.frame.out / "unknown").write_bytes(b"keep")
        with self.assertRaisesRegex(ValueError, "no exact plan"):
            batch.run(value)
        self.assertEqual((self.frame.out / "unknown").read_bytes(), b"keep")

    def test_nonblocking_lock_prevents_duplicate(self):
        value = self.frame.plan()
        with (self.root / ".batch.export.lock").open("w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "already locked"):
                batch.run(value)
        self.assertFalse(self.frame.out.exists())

    def test_selection_with_no_common_complete11_exports_no_fake_figure(self):
        value = self.frame.plan(aggregates=["selection"])
        batch.run(value, executor=ExportStub(empty_selection=True))
        index = batch.read(self.frame.out / "EXPORT_INDEX.json")
        self.assertEqual(index["aggregate_exports"][-1]["exports"], [])
        self.assertFalse(index["grants_visual_GO"])

    def test_unknown_or_duplicate_aggregate_rejected(self):
        for names in (["unknown"], ["selection", "selection"]):
            with self.assertRaisesRegex(ValueError, "aggregate selection"):
                self.frame.plan(aggregates=names)


if __name__ == "__main__":
    unittest.main()
