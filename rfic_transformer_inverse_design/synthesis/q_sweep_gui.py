"""Local three-input web application for exact-Q MLP synthesis."""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import mimetypes
import os
import re
import threading
import uuid
import webbrowser
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .frozen_mlp import FrozenTandemMLP
from .physical_evidence import validate_physical_evidence_manifest
from .q_sweep import (
    FEATURE_NAMES,
    LAYOUT_EVIDENCE_CLASS,
    Q_SWEEP_VALUES,
    PhysicalTarget3,
    _validated_layout_backend_argv,
    _validated_physical_backend_argv,
    execute_q_sweep,
)
from .layout_evidence import validate_layout_evidence_manifest


DESIGN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    job_id: str
    target: PhysicalTarget3
    output_dir: Path
    status: str = "QUEUED"
    created_utc: str = field(default_factory=_utc_now)
    completed_utc: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    artifacts: dict[str, Path] = field(default_factory=dict)
    mode: str | None = None
    model_provenance: dict[str, Any] = field(default_factory=dict)
    started_utc: str | None = None
    history_restored: bool = False
    model: Any = field(default=None, repr=False)
    physical_backend_command: str | None = field(default=None, repr=False)
    layout_backend_command: str | None = field(default=None, repr=False)
    proxy_candidates_sha256: str | None = None


class JobStore:
    """Single active job store; EMX jobs are intentionally serialized per GUI."""

    def __init__(
        self,
        *,
        model: FrozenTandemMLP,
        output_root: Path,
        mode: str,
        physical_backend_command: str | None,
        layout_backend_command: str | None = None,
        model_contract_path: str | Path | None = None,
        model_profile: str = "deployed100k",
    ) -> None:
        self.model = model
        self.output_root = output_root.expanduser().resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.physical_backend_command = physical_backend_command
        self.layout_backend_command = layout_backend_command
        self.model_provenance = _model_provenance(model, model_contract_path)
        self.model_profile = str(getattr(model, "adapter_profile", model_profile))
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._metadata_root = self.output_root / ".gui_jobs"
        self._metadata_root.mkdir(exist_ok=True)
        if self._metadata_root.is_symlink():
            raise ValueError("GUI history directory must not be a symlink")
        self.history_warnings: list[str] = []
        self._loaded_models: dict[str, tuple[Any, dict[str, Any], str]] = {}
        self._active_model_key = self._register_model(
            model, self.model_provenance, self.model_profile
        )
        self._restore_history()
        self._discover_legacy_history()

    def _register_model(self, model: Any, provenance: dict[str, Any], profile: str) -> str:
        identity = json.dumps(provenance, sort_keys=True, ensure_ascii=False)
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        self._loaded_models[key] = (model, copy.deepcopy(provenance), profile)
        return key

    def _persist(self, job: Job) -> None:
        """Persist UI metadata beside runs, never precreate or alter a run directory."""

        payload = {
            "schema": "rfic_q_sweep_gui_job.v1",
            "job_id": job.job_id,
            "target": {
                "design_id": job.target.design_id,
                "lp_nh": job.target.lp_nh,
                "ls_nh": job.target.ls_nh,
                "k_abs": job.target.k_abs,
            },
            "output_dir": str(job.output_dir),
            "status": job.status,
            "mode": job.mode or self.mode,
            "model_provenance": job.model_provenance,
            "created_utc": job.created_utc,
            "started_utc": job.started_utc,
            "completed_utc": job.completed_utc,
            "error": job.error,
            "result": job.result,
            "proxy_candidates_sha256": job.proxy_candidates_sha256,
            "artifacts": {name: str(path) for name, path in job.artifacts.items()},
            "logs": [str(path) for path in _job_logs(job)],
        }
        destination = self._metadata_root / f"{job.job_id}.json"
        temporary = self._metadata_root / f".{job.job_id}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            directory_fd = os.open(self._metadata_root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _restore_history(self) -> None:
        for path in sorted(self._metadata_root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("schema") != "rfic_q_sweep_gui_job.v1":
                    raise ValueError("unsupported history schema")
                job_id = str(data["job_id"])
                if not DESIGN_ID_PATTERN.fullmatch(job_id) or path.stem != job_id:
                    raise ValueError("history job identity mismatch")
                root = Path(data["output_dir"]).resolve()
                if root != (self.output_root / job_id).resolve() or not root.is_relative_to(self.output_root):
                    raise ValueError("history output is not this job directory")
                mode = data["mode"]
                if mode not in {"proxy", "layout", "physical"}:
                    raise ValueError("invalid history mode")
                job = Job(
                    job_id=job_id, target=_target_from_payload(data["target"]),
                    output_dir=root, mode=mode,
                    status=str(data["status"]),
                    model_provenance=copy.deepcopy(data.get("model_provenance") or {}),
                    created_utc=data["created_utc"], started_utc=data.get("started_utc"),
                    completed_utc=data.get("completed_utc"), error=data.get("error"),
                    result=data.get("result"), history_restored=True,
                    proxy_candidates_sha256=data.get("proxy_candidates_sha256"),
                )
                if job.status in {"RUNNING", "QUEUED"}:
                    job.status = "INTERRUPTED"
                    job.error = "GUI 上次退出时任务未记载终态；仅恢复记录，未重新运行。"
                    # Completion time is unknown, not the time the GUI was reopened.
                    job.completed_utc = None
                elif job.status not in {"SUCCEEDED", "FAILED", "INTERRUPTED"}:
                    raise ValueError("unknown history terminal state")
                for name, value in (data.get("artifacts") or {}).items():
                    artifact = Path(value).resolve()
                    if artifact.is_relative_to(root) and artifact.is_file():
                        job.artifacts[name] = artifact
                self._jobs[job_id] = job
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self.history_warnings.append(f"{path.name}: {type(exc).__name__}")

    def list_jobs(self) -> dict[str, Any]:
        with self._lock:
            jobs = []
            for job in sorted(self._jobs.values(), key=lambda item: item.created_utc, reverse=True):
                jobs.append({
                    "job_id": job.job_id, "status": job.status,
                    "mode": job.mode or self.mode, "created_utc": job.created_utc,
                    "completed_utc": job.completed_utc, "error": job.error,
                    "target": {"design_id": job.target.design_id, "Lp_nH": job.target.lp_nh,
                               "Ls_nH": job.target.ls_nh, "K_abs": job.target.k_abs},
                    "selected_q": (job.result or {}).get("selected_q"),
                    "model_provenance": job.model_provenance,
                    "history_restored": job.history_restored,
                })
            return {"jobs": jobs, "history_warnings": list(self.history_warnings)}

    def _discover_legacy_history(self) -> None:
        """Expose existing completed sweeps without writing into their frozen outputs."""

        for root in sorted(self.output_root.iterdir()):
            if root.name.startswith(".") or root.name in self._jobs or not root.is_dir() or root.is_symlink():
                continue
            manifest_path, selection_path = root / "run_manifest.json", root / "selection.json"
            if not manifest_path.is_file() or not selection_path.is_file():
                continue
            try:
                if not DESIGN_ID_PATTERN.fullmatch(root.name):
                    raise ValueError("unsupported historical job ID")
                if any(path.is_symlink() for path in (manifest_path, selection_path)):
                    raise ValueError("historical authority is a symlink")
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                result = json.loads(selection_path.read_text(encoding="utf-8"))
                if manifest.get("schema") != "rfic_q_sweep_run_manifest.v1" or manifest.get("overall_status") != "PASS":
                    continue
                mode = manifest["mode"]
                if mode not in {"proxy", "layout", "physical"} or result.get("mode") != mode:
                    raise ValueError("historical mode mismatch")
                values = result["target_three_features"]
                target = _target_from_payload({"design_id": result["design_id"],
                                              "lp_nh": values["Lp_nH"],
                                              "ls_nh": values["Ls_nH"],
                                              "k_abs": values["K_abs"]})
                identity = manifest.get("model_identity") or {}
                declared = identity.get("artifact_expected_sha256") or {}
                provenance = {
                    "model_id": identity.get("model_id") or manifest.get("model_id") or "UNKNOWN",
                    "model_seed": identity.get("model_seed", manifest.get("model_seed")),
                    "architecture": identity.get("architecture") or {},
                    "runtime_checkpoint_status": "HISTORICAL_MANIFEST_ONLY_NOT_RELOADED",
                    "rows": {},
                    "sha256": {name: {"value": declared.get(name), "source": "historical_run_manifest"}
                               for name in ("summary", "weights", "training_table")},
                }
                job = Job(job_id=root.name, target=target, output_dir=root,
                          status="SUCCEEDED", mode=mode,
                          created_utc=manifest["started_utc"], started_utc=manifest["started_utc"],
                          completed_utc=manifest.get("completed_utc"), result=result,
                          model_provenance=provenance, history_restored=True,
                          artifacts=_collect_artifacts(root, result))
                self._jobs[job.job_id] = job
            except (OSError, TypeError, ValueError, KeyError) as exc:
                self.history_warnings.append(f"{root.name}: {type(exc).__name__}")

    def switch_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if any(job.status in {"QUEUED", "RUNNING"} for job in self._jobs.values()):
                raise RuntimeError("任务正在运行，完成后才能切换模型。")
            key = payload.get("loaded_model_key")
            if key is not None:
                if key not in self._loaded_models:
                    raise ValueError("未找到已加载模型。")
            else:
                from .model_adapter import load_model

                profile = str(payload.get("profile") or "deployed100k")
                directory = payload.get("model_dir")
                if not isinstance(directory, str) or not directory.strip():
                    raise ValueError("请提供完整模型包目录。")
                contract_path = payload.get("contract_path") or None
                model = load_model(directory, profile=profile, contract_path=contract_path)
                provenance = _model_provenance(model, contract_path)
                if provenance["runtime_checkpoint_status"] != "FULL_INVERSE_AND_FORWARD_WEIGHTS_HASH_VERIFIED":
                    raise ValueError("模型包完整身份未通过验证。")
                key = self._register_model(model, provenance, str(getattr(model, "adapter_profile", profile)))
            self.model, provenance, self.model_profile = self._loaded_models[str(key)]
            self.model_provenance = copy.deepcopy(provenance)
            self._active_model_key = str(key)
            return self.public_context()

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = _target_from_payload(payload)
        with self._lock:
            active = next(
                (
                    job
                    for job in self._jobs.values()
                    if job.status in {"QUEUED", "RUNNING"}
                ),
                None,
            )
            if active is not None:
                raise RuntimeError("已有任务正在运行，请等待完成。")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            job_id = f"{stamp}_{uuid.uuid4().hex[:8]}"
            job = Job(
                job_id=job_id,
                target=target,
                output_dir=self.output_root / job_id,
                mode=self.mode,
                model_provenance=copy.deepcopy(self.model_provenance),
                model=self.model,
                physical_backend_command=self.physical_backend_command,
                layout_backend_command=self.layout_backend_command,
            )
            self._persist(job)
            self._jobs[job_id] = job
            threading.Thread(
                target=self._run,
                args=(job_id,),
                name=f"q-sweep-{job_id}",
                daemon=True,
            ).start()
            return self.public_record(job_id)

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "RUNNING"
            job.started_utc = _utc_now()
        try:
            self._persist(job)
            result = execute_q_sweep(
                model=job.model,
                target=job.target,
                output_dir=job.output_dir,
                mode=job.mode or self.mode,
                physical_backend_command=job.physical_backend_command,
                layout_backend_command=job.layout_backend_command,
            )
            result_record = result.record()
            proxy_path = job.output_dir / "proxy_candidates.json"
            if proxy_path.is_file() and proxy_path.resolve().is_relative_to(job.output_dir.resolve()):
                job.proxy_candidates_sha256 = _sha256(proxy_path)
            result_record["physical_q_metadata_status"] = (
                _attach_physical_q_components(job.output_dir, result_record)
            )
            with self._lock:
                job.result = result_record
                job.status = "SUCCEEDED"
                job.artifacts = _collect_artifacts(job.output_dir, result_record)
        except Exception as exc:
            with self._lock:
                job.status = "FAILED"
                job.error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                job.completed_utc = _utc_now()
                try:
                    self._persist(job)
                except OSError as exc:
                    job.error = (job.error or "") + f" 历史记录保存失败：{type(exc).__name__}"

    def public_record(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            result = job.result or {}
            mode = job.mode or self.mode
            raw_candidates = result.get("candidates") or []
            public_candidates = _public_candidates(raw_candidates)
            proxy_comparison = _same_run_proxy_features(job, public_candidates)
            for candidate in public_candidates:
                candidate["proxy_features_from_same_run"] = proxy_comparison.get(candidate.get("candidate_id"))
                candidate["proxy_features_source_sha256"] = (
                    job.proxy_candidates_sha256 if candidate["proxy_features_from_same_run"] is not None else None
                )
                candidate["proxy_features_status"] = (
                    "HASH_AND_CANDIDATE_BOUND_SAVED_PROXY" if candidate["proxy_features_from_same_run"] is not None
                    else "UNAVAILABLE_NO_VERIFIED_SAVED_PROXY"
                )
            error_summary = _error_summary(public_candidates, result.get("selected_q"))
            gds_by_q: dict[str, dict[str, Any]] = {}
            raw_by_q = {
                int(candidate["q_target"]): candidate
                for candidate in raw_candidates
                if isinstance(candidate, dict)
                and _safe_int(candidate.get("q_target")) in Q_SWEEP_VALUES
            }
            batch_download_ready = (
                _physical_batch_download_ready(job, result)
                if mode == "physical"
                else _layout_batch_download_ready(job, result)
            )
            for candidate in public_candidates:
                q_value = int(candidate["q_target"])
                artifact_name = f"gds_q{q_value:02d}"
                path = job.artifacts.get(artifact_name)
                artifact_hashes = candidate.get("artifact_sha256") or {}
                evidence = candidate.get("artifact_evidence") or {}
                declared_gds_sha = str(artifact_hashes.get("gds") or "").lower()
                if (
                    not batch_download_ready
                    or
                    path is None
                    or not path.is_file()
                    or not path.resolve().is_relative_to(job.output_dir.resolve())
                    or mode not in {"layout", "physical"}
                    or result.get("evidence_source")
                    not in {LAYOUT_EVIDENCE_CLASS, "FRESH_REAL_EMX"}
                    or candidate.get("evidence_source")
                    != result.get("evidence_source")
                    or not re.fullmatch(r"[0-9a-f]{64}", declared_gds_sha)
                    or _sha256(path) != declared_gds_sha
                    or (
                        mode == "physical"
                        and evidence.get("physical_evidence_class")
                        != "CANDIDATE_BOUND_GDS_S4P_CONTRACT"
                    )
                    or (
                        mode == "layout"
                        and evidence.get("layout_evidence_class")
                        != LAYOUT_EVIDENCE_CLASS
                    )
                    or raw_by_q.get(q_value) is None
                    or not (
                        _physical_candidate_artifacts_intact(job, raw_by_q[q_value])
                        if mode == "physical"
                        else _layout_candidate_artifacts_intact(
                            job, raw_by_q[q_value]
                        )
                    )
                ):
                    continue
                gds_by_q[str(q_value)] = {
                    "url": f"/api/jobs/{job_id}/artifacts/{artifact_name}",
                    "filename": self.artifact_filename(job_id, artifact_name),
                    "gds_sha256": declared_gds_sha,
                    "geometry_sha256": candidate.get("geometry_sha256"),
                    "candidate_id": candidate.get("candidate_id"),
                }
            evidence_readiness = _evidence_readiness(
                mode=mode,
                job_status=job.status,
                result=result,
                candidates=public_candidates,
                gds_by_q=gds_by_q,
            )
            progress = _job_progress(job, mode)
            artifacts = {
                name: f"/api/jobs/{job_id}/artifacts/{name}"
                for name in job.artifacts if name == "preview"
            }
            bundle_url = None
            if len(gds_by_q) == len(Q_SWEEP_VALUES):
                bundle_url = f"/api/jobs/{job_id}/artifacts/gds_zip"
                artifacts["gds_zip"] = bundle_url
            return {
                "job_id": job.job_id,
                "status": job.status,
                "mode": mode,
                "created_utc": job.created_utc,
                "started_utc": job.started_utc,
                "completed_utc": job.completed_utc,
                "elapsed_seconds": _job_elapsed(job),
                "stage": progress["stage"],
                "progress": progress,
                "completed_candidate_count": len(public_candidates) if job.status == "SUCCEEDED" else None,
                "history_restored": job.history_restored,
                "error": job.error,
                "target": {
                    "design_id": job.target.design_id,
                    "Lp_nH": job.target.lp_nh,
                    "Ls_nH": job.target.ls_nh,
                    "K_abs": job.target.k_abs,
                },
                "selected_q": result.get("selected_q"),
                "selection_score": result.get("selection_score"),
                "evidence_source": result.get("evidence_source"),
                "metric_source": result.get("metric_source"),
                "selection_source": result.get("selection_source"),
                "artifact_evidence_class": result.get(
                    "artifact_evidence_class"
                ),
                "scientific_boundary": result.get("scientific_boundary"),
                "model_provenance": job.model_provenance or (
                    {"model_id": "UNKNOWN", "runtime_checkpoint_status": "RUNTIME_CHECKPOINT_NOT_VERIFIED"}
                    if job.history_restored else self.model_provenance
                ),
                "evidence_readiness": evidence_readiness,
                "error_summary": error_summary,
                "candidates": public_candidates,
                "gds_by_q": gds_by_q,
                "artifacts": artifacts,
                "gds_bundle_url": bundle_url,
                "logs": [{"name": path.name,
                          "url": f"/api/jobs/{job_id}/artifacts/log_{path.name}"}
                         for path in _job_logs(job)],
            }

    def public_context(self) -> dict[str, Any]:
        """Return immutable, path-free model and configured-mode metadata."""

        return {
            "mode": self.mode,
            "model_profile": self.model_profile,
            "available_profiles": ["deployed100k", "real10k", "custom"],
            "loaded_models": [
                {"key": key, "profile": profile,
                 "model_id": provenance.get("model_id"),
                 "active": key == self._active_model_key}
                for key, (_, provenance, profile) in self._loaded_models.items()
            ],
            "model_provenance": self.model_provenance,
            "evidence_readiness": _evidence_readiness(
                mode=self.mode,
                job_status="NOT_STARTED",
                result={},
                candidates=[],
                gds_by_q={},
            ),
        }

    def artifact(self, job_id: str, name: str) -> Path:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if name in {"gds", "s4p"}:
                raise PermissionError("Use a candidate-specific, evidence-checked artifact")
            if name.startswith("log_"):
                matches = [path for path in _job_logs(job) if f"log_{path.name}" == name]
                if not matches:
                    raise FileNotFoundError(name)
                return matches[0]
            path = job.artifacts.get(name)
            if path is None or not path.is_file():
                raise FileNotFoundError(name)
            if not path.resolve().is_relative_to(job.output_dir.resolve()):
                raise PermissionError("artifact escaped the job directory")
            match = re.fullmatch(r"(gds|s4p)_q(\d{2})", name)
            if match:
                kind, q_value = match.group(1), int(match.group(2))
                result = job.result or {}
                candidates = result.get("candidates") or []
                candidate = next(
                    (
                        item
                        for item in candidates
                        if isinstance(item, dict)
                        and _safe_int(item.get("q_target")) == q_value
                    ),
                    None,
                )
                if (
                    job.status != "SUCCEEDED"
                    or candidate is None
                    or (kind == "s4p" and (job.mode or self.mode) != "physical")
                    or not (
                        _physical_batch_download_ready(job, result)
                        if (job.mode or self.mode) == "physical"
                        else _layout_batch_download_ready(job, result)
                    )
                    or not (
                        _physical_candidate_artifacts_intact(job, candidate)
                        if (job.mode or self.mode) == "physical"
                        else _layout_candidate_artifacts_intact(job, candidate)
                    )
                    or Path(str((candidate.get("artifacts") or {}).get(kind))).resolve()
                    != path.resolve()
                ):
                    raise PermissionError("Candidate evidence is no longer download-ready")
            return path

    def gds_bundle(self, job_id: str) -> tuple[bytes, str]:
        """Build a download in memory from the same complete-batch gate as individual GDS."""

        with self._lock:
            record = self.public_record(job_id)
            entries = record["gds_by_q"]
            if set(entries) != {str(q) for q in Q_SWEEP_VALUES}:
                raise PermissionError("完整 11 候选 GDS 证据门尚未通过。")
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for q_value in Q_SWEEP_VALUES:
                    name = f"gds_q{q_value:02d}"
                    path = self.artifact(job_id, name)
                    payload = path.read_bytes()
                    if hashlib.sha256(payload).hexdigest() != entries[str(q_value)]["gds_sha256"]:
                        raise PermissionError("GDS 在下载期间发生变化。")
                    archive.writestr(self.artifact_filename(job_id, name), payload)
            # No historical output is written; the final recheck also catches changes
            # to a different candidate's receipt while earlier files were read.
            if self.public_record(job_id)["gds_by_q"] != entries:
                raise PermissionError("GDS 批次证据在下载期间发生变化。")
            job = self._jobs[job_id]
            return buffer.getvalue(), f"{job.target.design_id}_q10-20_gds.zip"

    def artifact_filename(self, job_id: str, name: str) -> str:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if name.startswith("log_"):
                return self.artifact(job_id, name).name
            match = re.fullmatch(r"(gds|s4p)_q(\d{2})", name)
            if match:
                return f"{job.target.design_id}_q{match.group(2)}.{match.group(1)}"
            path = job.artifacts.get(name)
            if path is None:
                raise FileNotFoundError(name)
            return path.name


def _job_elapsed(job: Job) -> float | None:
    if not job.started_utc or job.status == "INTERRUPTED":
        return None
    try:
        start = datetime.fromisoformat(job.started_utc)
        end = datetime.fromisoformat(job.completed_utc) if job.completed_utc else datetime.now(timezone.utc)
        return max(0.0, (end - start).total_seconds())
    except (TypeError, ValueError):
        return None


def _same_run_proxy_features(job: Job, candidates: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Read the completion-pinned proxy snapshot without inference or historical backfill."""

    if not job.proxy_candidates_sha256:
        return {}
    path = job.output_dir / "proxy_candidates.json"
    try:
        if not path.is_file() or not path.resolve().is_relative_to(job.output_dir.resolve()):
            return {}
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != job.proxy_candidates_sha256:
            return {}
        saved = json.loads(payload)
        if saved.get("evidence_source") != "FROZEN_FORWARD_PROXY_DIAGNOSTIC":
            return {}
        rows = saved.get("candidates") or []
        if len(rows) != len(Q_SWEEP_VALUES) or len(candidates) != len(Q_SWEEP_VALUES):
            return {}
        by_id = {row["candidate_id"]: row for row in rows}
        if len(by_id) != len(rows):
            return {}
        bound = {}
        for candidate in candidates:
            row = by_id.get(candidate["candidate_id"])
            if row is None or any(row.get(key) != candidate.get(key) for key in (
                "q_target", "geometry_sha256", "geometry_um", "target_features",
            )):
                return {}
            observed = row["observed_features"]
            values = {name: float(observed[name]) for name in FEATURE_NAMES}
            if not all(math.isfinite(value) for value in values.values()):
                return {}
            bound[candidate["candidate_id"]] = values
        return bound
    except (OSError, KeyError, TypeError, ValueError):
        return {}


def _job_logs(job: Job) -> list[Path]:
    names = ("layout_backend.stdout.log", "layout_backend.stderr.log",
             "physical_backend.stdout.log", "physical_backend.stderr.log")
    root = job.output_dir.resolve()
    return [root / name for name in names
            if (root / name).is_file() and (root / name).resolve().is_relative_to(root)]


def _job_progress(job: Job, mode: str) -> dict[str, Any]:
    root = job.output_dir.resolve()
    proxy_count = 0
    proxy_path = root / "proxy_candidates.json"
    try:
        if proxy_path.is_file() and proxy_path.resolve().is_relative_to(root):
            proxy = json.loads(proxy_path.read_text(encoding="utf-8"))
            proxy_count = len(proxy.get("candidates") or [])
    except (OSError, TypeError, ValueError):
        pass
    backend_root = root / ("layout_backend" if mode == "layout" else "physical_backend")
    observed = {"gds": 0, "s4p": 0}
    if backend_root.is_dir() and backend_root.resolve().is_relative_to(root):
        for suffix in observed:
            observed[suffix] = sum(
                1 for path in backend_root.rglob(f"*.{suffix}")
                if path.is_file() and path.resolve().is_relative_to(root)
            )
    stage = {
        "QUEUED": "queued", "SUCCEEDED": "complete", "FAILED": "failed",
        "INTERRUPTED": "interrupted",
    }.get(job.status, "model_inference")
    if job.status == "RUNNING" and proxy_count:
        stage = "finalizing" if mode == "proxy" else f"{mode}_backend"
    return {
        "stage": stage, "candidate_count": len(Q_SWEEP_VALUES),
        "proxy_candidates_completed": proxy_count,
        "gds_files_observed": observed["gds"], "s4p_files_observed": observed["s4p"],
        "evidence_basis": "proxy_candidates.json and observed backend files; file counts are not validated candidate counts",
    }


def _model_provenance(
    model: Any, contract_path: str | Path | None = None
) -> dict[str, Any]:
    """Build path-free model identity, denominator, and hash metadata."""

    contract = getattr(model, "contract", {})
    summary = getattr(model, "summary", {})
    if not isinstance(contract, dict):
        contract = {}
    if not isinstance(summary, dict):
        summary = {}

    contract_file = (
        Path(contract_path).expanduser().resolve()
        if contract_path is not None
        else Path(getattr(model, "contract_path", Path(__file__).with_name("real10k_model_contract.json"))).resolve()
    )
    contract_sha256: str | None = None
    contract_sha_source = "unavailable"
    try:
        on_disk_contract = json.loads(contract_file.read_text(encoding="utf-8"))
        if isinstance(on_disk_contract, dict) and on_disk_contract == contract:
            contract_sha256 = _sha256(contract_file)
            contract_sha_source = "exact_runtime_contract_file"
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass

    artifacts = contract.get("artifacts") or {}
    if not isinstance(artifacts, dict):
        artifacts = {}
    runtime_attestation = getattr(model, "runtime_checkpoint_attestation", None)
    if not isinstance(runtime_attestation, dict):
        runtime_attestation = {}
    summary_artifact = artifacts.get("summary")
    weights_artifact = artifacts.get("weights")
    declared_summary_sha = str(
        (summary_artifact.get("sha256") if isinstance(summary_artifact, dict) else "")
        or ""
    ).lower()
    declared_weights_sha = str(
        (weights_artifact.get("sha256") if isinstance(weights_artifact, dict) else "")
        or ""
    ).lower()
    checkpoint_verified = (
        isinstance(model, FrozenTandemMLP)
        and runtime_attestation.get("schema")
        == "rfic_frozen_tandem_mlp_runtime_attestation.v1"
        and runtime_attestation.get("status")
        == "FULL_INVERSE_AND_FORWARD_WEIGHTS_HASH_VERIFIED"
        and runtime_attestation.get("contract_sha256") == contract_sha256
        and runtime_attestation.get("summary_sha256") == declared_summary_sha
        and runtime_attestation.get("weights_sha256") == declared_weights_sha
        and runtime_attestation.get("arrays_write_protected") is True
    )
    loader_verification = (
        "runtime_attestation_hash_bound_at_model_load"
        if checkpoint_verified
        else "declared_metadata_not_runtime_verified"
    )
    architecture = contract.get("architecture") or {}
    if not isinstance(architecture, dict):
        architecture = {}

    split_source = "evaluation_isolation"
    split_block = summary.get(split_source) or {}
    split_counts = split_block.get("row_counts", {}) if isinstance(split_block, dict) else {}
    if not split_counts:
        split_source = "split_audit"
        split_block = summary.get(split_source) or {}
        split_counts = split_block.get("row_counts", {}) if isinstance(split_block, dict) else {}
    if not isinstance(split_counts, dict):
        split_counts = {}
    contract_rows = contract.get("row_counts") or {}
    if not isinstance(contract_rows, dict):
        contract_rows = {}

    source_value, source_name = _first_row_count(
        (summary.get("training_count"), "summary.training_count"),
        (contract.get("training_label_count"), "contract.training_label_count"),
        (contract_rows.get("source"), "contract.row_counts.source"),
    )
    train_value, train_name = _first_row_count(
        (split_counts.get("train"), f"summary.{split_source}.row_counts.train"),
        (summary.get("train_rows"), "summary.train_rows"),
        (contract_rows.get("train"), "contract.row_counts.train"),
    )
    validation_value, validation_name = _first_row_count(
        (
            split_counts.get("validation"),
            f"summary.{split_source}.row_counts.validation",
        ),
        (summary.get("validation_rows"), "summary.validation_rows"),
        (contract_rows.get("validation"), "contract.row_counts.validation"),
    )
    test_value, test_name = _first_row_count(
        (split_counts.get("test"), f"summary.{split_source}.row_counts.test"),
        (summary.get("test_rows"), "summary.test_rows"),
        (contract_rows.get("test"), "contract.row_counts.test"),
    )
    rows = {
        "source_rows": _row_record(source_value, source_name),
        "gradient_training_rows": _row_record(train_value, train_name),
        "validation_rows": _row_record(validation_value, validation_name),
        "test_rows": _row_record(test_value, test_name),
    }
    known_split = (train_value, validation_value, test_value)
    rows["split_sum_matches_source"] = (
        source_value == sum(int(value) for value in known_split)
        if source_value is not None and all(value is not None for value in known_split)
        else None
    )
    rows["definition"] = (
        "Source-table rows are distinct from rows used for gradient updates; "
        "validation rows select checkpoints and test rows follow the saved isolation contract."
    )

    return {
        "model_id": _safe_public_text(getattr(model, "model_id", None)),
        "display_name": _safe_public_text(contract.get("display_name")),
        "model_profile": _safe_public_text(getattr(model, "adapter_profile", None)),
        "model_seed": _safe_int(getattr(model, "model_seed", None)),
        "target_frequency_ghz": _safe_float(
            getattr(model, "target_frequency_ghz", None)
        ),
        "declared_support_lower": list(contract.get("declared_support_lower") or []),
        "declared_support_upper": list(contract.get("declared_support_upper") or []),
        "q_target_semantics": _safe_public_text(contract.get("q_target_semantics")),
        "q_training_semantics": _safe_public_text(contract.get("q_target_semantics")),
        "q_training_margin": contract.get("q_training_margin_physical"),
        "q_training_margin_physical": contract.get("q_training_margin_physical"),
        "inference_q_input_transform": contract.get("inference_q_input_transform"),
        "selection_q_semantics": "exact_symmetric",
        "q_training_definition": _safe_public_text(
            contract.get("q_training_definition")
        ),
        "evaluation_mode": _safe_public_text(summary.get("evaluation_mode")),
        "quality_status": _safe_public_text(
            summary.get("quality_status") or summary.get("overall_status")
        ),
        "runtime_checkpoint_status": (
            "FULL_INVERSE_AND_FORWARD_WEIGHTS_HASH_VERIFIED"
            if checkpoint_verified
            else "RUNTIME_CHECKPOINT_NOT_VERIFIED"
        ),
        "architecture": {
            "inverse_mlp": [
                int(value) for value in (architecture.get("inverse_mlp") or [])
            ],
            "forward_surrogate": [
                int(value)
                for value in (architecture.get("forward_surrogate") or [])
            ],
            "hidden_activation": _safe_public_text(
                architecture.get("hidden_activation")
            ),
            "geometry_projection": _safe_public_text(
                architecture.get("geometry_projection")
            ),
        },
        "rows": rows,
        "sha256": {
            "contract": {
                "value": contract_sha256,
                "source": contract_sha_source,
            },
            "summary": _artifact_hash_record(
                artifacts.get("summary"), loader_verification
            ),
            "weights": _artifact_hash_record(
                artifacts.get("weights"), loader_verification
            ),
            "source_table": _artifact_hash_record(
                artifacts.get("training_table"), "declared_by_contract"
            ),
        },
    }


def _first_row_count(*candidates: tuple[Any, str]) -> tuple[int | None, str]:
    for value, source in candidates:
        parsed = _safe_int(value)
        if parsed is not None and parsed >= 0:
            return parsed, source
    return None, "unavailable"


def _row_record(value: int | None, source: str) -> dict[str, Any]:
    return {"value": value, "source": source}


def _artifact_hash_record(value: Any, verification: str) -> dict[str, Any]:
    record = value if isinstance(value, dict) else {}
    declared = str(record.get("sha256") or "").lower()
    valid = bool(re.fullmatch(r"[0-9a-f]{64}", declared))
    return {
        "value": declared if valid else None,
        "source": verification if valid else "unavailable",
    }


def _safe_public_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:256] if text else None


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _target_from_payload(payload: dict[str, Any]) -> PhysicalTarget3:
    design_id = str(payload.get("design_id") or "design").strip()
    if not DESIGN_ID_PATTERN.fullmatch(design_id):
        raise ValueError("设计名只能包含字母、数字、点、下划线或短横线。")
    labels = {"lp_nh": "Lp", "ls_nh": "Ls", "k_abs": "|K|"}
    values: dict[str, float] = {}
    for key, label in labels.items():
        try:
            values[key] = float(payload[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{label} 必须是数值。") from exc
    target = PhysicalTarget3(design_id=design_id, **values)
    target.validate()
    return target


def _collect_artifacts(root: Path, result: dict[str, Any]) -> dict[str, Path]:
    candidates_name = {
        "physical": "physical_candidates.csv",
        "layout": "layout_candidates.csv",
    }.get(str(result.get("mode")), "proxy_candidates.csv")
    candidates = {
        "selection": root / "selection.json",
        "manifest": root / "run_manifest.json",
        "candidates": root / candidates_name,
        "preview": Path(str((result.get("selected_artifacts") or {}).get("preview") or "")),
    }
    if result.get("mode") in {"layout", "physical"}:
        for kind in ("gds", "s4p"):
            value = (result.get("selected_artifacts") or {}).get(kind)
            if value:
                candidates[kind] = Path(str(value))
    for candidate in result.get("candidates") or []:
        q_value = int(candidate["q_target"])
        if (
            result.get("mode") not in {"layout", "physical"}
            or candidate.get("evidence_source")
            not in {LAYOUT_EVIDENCE_CLASS, "FRESH_REAL_EMX"}
            or q_value not in Q_SWEEP_VALUES
        ):
            continue
        artifact = candidate.get("artifacts") or {}
        for kind in ("gds", "s4p"):
            value = artifact.get(kind)
            if value:
                candidates[f"{kind}_q{q_value:02d}"] = Path(str(value))
    return {
        name: path.resolve()
        for name, path in candidates.items()
        if path.is_file() and path.resolve().is_relative_to(root.resolve())
    }


def _layout_batch_download_ready(job: Job, result: dict[str, Any]) -> bool:
    """Require one intact 11/11 structured-GDS batch before any download."""

    candidates = result.get("candidates")
    if (
        job.status != "SUCCEEDED"
        or result.get("mode") != "layout"
        or result.get("evidence_source") != LAYOUT_EVIDENCE_CLASS
        or result.get("metric_source") != "FROZEN_FORWARD_PROXY_DIAGNOSTIC"
        or result.get("selection_source") != "FROZEN_FORWARD_PROXY_DIAGNOSTIC"
        or result.get("artifact_evidence_class") != LAYOUT_EVIDENCE_CLASS
        or result.get("physical_q_metadata_status") != "NOT_APPLICABLE_PROXY"
        or not isinstance(candidates, list)
        or len(candidates) != len(Q_SWEEP_VALUES)
    ):
        return False
    q_values = [
        _safe_int(item.get("q_target")) if isinstance(item, dict) else None
        for item in candidates
    ]
    if None in q_values or set(q_values) != set(Q_SWEEP_VALUES):
        return False
    receipt_hashes: set[str] = set()
    result_hashes: set[str] = set()
    for candidate in candidates:
        if (
            not isinstance(candidate, dict)
            or candidate.get("evidence_source") != LAYOUT_EVIDENCE_CLASS
            or candidate.get("observation_source")
            != "FROZEN_FORWARD_PROXY_DIAGNOSTIC"
            or candidate.get("artifact_evidence_class") != LAYOUT_EVIDENCE_CLASS
            or not _layout_candidate_artifacts_intact(job, candidate)
        ):
            return False
        artifacts = candidate.get("artifacts") or {}
        receipt_hashes.add(str(artifacts.get("layout_backend_receipt_sha256") or ""))
        result_hashes.add(str(artifacts.get("layout_results_sha256") or ""))
    return len(receipt_hashes) == 1 and len(result_hashes) == 1


def _layout_candidate_artifacts_intact(job: Job, candidate: dict[str, Any]) -> bool:
    """Recheck GDS-only paths, hashes, evidence, receipt, and sources per GET."""

    try:
        artifacts = candidate.get("artifacts")
        if (
            not isinstance(artifacts, dict)
            or candidate.get("observation_source")
            != "FROZEN_FORWARD_PROXY_DIAGNOSTIC"
            or candidate.get("artifact_evidence_class") != LAYOUT_EVIDENCE_CLASS
            or artifacts.get("layout_evidence_class") != LAYOUT_EVIDENCE_CLASS
            or artifacts.get("emx_status") != "NOT_RUN"
            or artifacts.get("calibre_drc") != "NOT_RUN"
            or artifacts.get("hfss_correlation") != "NOT_RUN"
        ):
            return False
        layout_root = (job.output_dir / "layout_backend").resolve(strict=True)
        if not layout_root.is_dir():
            return False
        path_fields = {
            "gds": "gds_sha256",
            "evidence_manifest": "evidence_manifest_sha256",
            "layout_backend_receipt": "layout_backend_receipt_sha256",
            "layout_results": "layout_results_sha256",
            "geometry_json": "geometry_json_sha256",
            "layout_manifest": "layout_manifest_sha256",
            "evaluation_summary": "evaluation_summary_sha256",
        }
        paths: dict[str, Path] = {}
        digests: dict[str, str] = {}
        for name, digest_name in path_fields.items():
            raw = artifacts.get(name)
            if not isinstance(raw, str) or not raw:
                return False
            path = Path(raw).expanduser().resolve(strict=True)
            digest = _valid_sha256_text(artifacts.get(digest_name))
            if (
                digest is None
                or not path.is_relative_to(layout_root)
                or not path.is_file()
                or _sha256(path) != digest
            ):
                return False
            paths[name] = path
            digests[digest_name] = digest
        if paths["layout_backend_receipt"] != layout_root / "layout_backend_receipt.json":
            return False
        if paths["layout_results"] != layout_root / "layout_results.json":
            return False

        candidate_id = str(candidate.get("candidate_id") or "")
        q_target = _safe_int(candidate.get("q_target"))
        geometry_sha256 = _valid_sha256_text(candidate.get("geometry_sha256"))
        if not candidate_id or q_target not in Q_SWEEP_VALUES or geometry_sha256 is None:
            return False
        evidence = validate_layout_evidence_manifest(
            paths["evidence_manifest"],
            root=layout_root,
            expected_candidate_id=candidate_id,
            expected_q_target=int(q_target),
            expected_geometry_sha256=geometry_sha256,
        )
        if (
            Path(evidence["artifacts"]["gds"]["path"]).resolve() != paths["gds"]
            or evidence["artifacts"]["gds"]["sha256"] != digests["gds_sha256"]
            or any(
                Path(evidence["artifacts"][name]["path"]).resolve() != paths[name]
                or evidence["artifacts"][name]["sha256"]
                != digests[f"{name}_sha256"]
                for name in (
                    "geometry_json",
                    "layout_manifest",
                    "evaluation_summary",
                )
            )
        ):
            return False

        receipt = json.loads(paths["layout_backend_receipt"].read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or any(
            receipt.get(name) != value
            for name, value in {
                "schema": "rfic_q_sweep_layout_backend_receipt.v1",
                "overall_status": "PASS",
                "decision": "STRUCTURED_GDS_Q_SWEEP_READY",
                "evidence_source": LAYOUT_EVIDENCE_CLASS,
                "emx_status": "NOT_RUN",
                "s4p_generated_count": 0,
                "calibre_drc": "NOT_RUN",
                "hfss_correlation": "NOT_RUN",
            }.items()
        ):
            return False
        if (
            receipt.get("candidate_count") != len(Q_SWEEP_VALUES)
            or receipt.get("pass_count") != len(Q_SWEEP_VALUES)
            or receipt.get("fail_count") != 0
            or receipt.get("candidate_evidence_manifest_count") != len(Q_SWEEP_VALUES)
            or receipt.get("q_values") != list(Q_SWEEP_VALUES)
            or Path(str(receipt.get("layout_backend_receipt") or "")).resolve()
            != paths["layout_backend_receipt"]
            or Path(str(receipt.get("layout_results") or "")).resolve()
            != paths["layout_results"]
            or _valid_sha256_text(receipt.get("layout_results_sha256"))
            != digests["layout_results_sha256"]
        ):
            return False
        request_path = Path(str(receipt.get("request_json") or "")).resolve(strict=True)
        request_sha = _valid_sha256_text(receipt.get("request_sha256"))
        if (
            request_path != (job.output_dir / "layout_backend_request.json").resolve(strict=True)
            or request_sha is None
            or _sha256(request_path) != request_sha
        ):
            return False
        layout_results = json.loads(paths["layout_results"].read_text(encoding="utf-8"))
        if (
            not isinstance(layout_results, dict)
            or layout_results.get("schema") != "rfic_q_sweep_layout_results.v1"
            or layout_results.get("evidence_source") != LAYOUT_EVIDENCE_CLASS
            or layout_results.get("emx_status") != "NOT_RUN"
            or not isinstance(layout_results.get("results"), list)
            or len(layout_results["results"]) != len(Q_SWEEP_VALUES)
        ):
            return False

        receipt_candidates = receipt.get("candidates")
        if not isinstance(receipt_candidates, list) or len(receipt_candidates) != len(
            Q_SWEEP_VALUES
        ):
            return False
        receipt_candidate = next(
            (
                item
                for item in receipt_candidates
                if isinstance(item, dict) and item.get("candidate_id") == candidate_id
            ),
            None,
        )
        receipt_artifacts = (
            receipt_candidate.get("artifacts")
            if isinstance(receipt_candidate, dict)
            else None
        )
        if (
            not isinstance(receipt_candidate, dict)
            or receipt_candidate.get("status") != "PASS"
            or receipt_candidate.get("emx_status") != "NOT_RUN"
            or receipt_candidate.get("q_target") != q_target
            or receipt_candidate.get("geometry_sha256") != geometry_sha256
            or not isinstance(receipt_artifacts, dict)
            or any(
                receipt_artifacts.get(name) != digests[name]
                for name in (
                    "gds_sha256",
                    "evidence_manifest_sha256",
                    "geometry_json_sha256",
                    "layout_manifest_sha256",
                    "evaluation_summary_sha256",
                )
            )
        ):
            return False
        for name in (
            "gds",
            "evidence_manifest",
            "geometry_json",
            "layout_manifest",
            "evaluation_summary",
        ):
            raw = Path(str(receipt_artifacts.get(name) or ""))
            receipt_path = (raw if raw.is_absolute() else layout_root / raw).resolve(
                strict=True
            )
            if receipt_path != paths[name]:
                return False

        approved_sources = {
            "layout_backend": Path(__file__).with_name(
                "q_sweep_layout_backend.py"
            ).resolve(),
            "structured_exporter": Path(__file__).resolve().parents[1]
            / "layout"
            / "export.py",
            "evaluator": Path(__file__).resolve().parents[1]
            / "execution"
            / "evaluator.py",
            "request_mapping_preflight": Path(__file__).with_name(
                "q_sweep_physical_backend.py"
            ).resolve(),
            "layout_checks": Path(__file__).resolve().parents[1]
            / "layout"
            / "checks.py",
        }
        runtime_sources = receipt.get("runtime_sources")
        if not isinstance(runtime_sources, dict) or set(runtime_sources) != set(
            approved_sources
        ):
            return False
        for name, approved_path in approved_sources.items():
            approved_path = approved_path.resolve(strict=True)
            record = runtime_sources.get(name)
            if (
                not isinstance(record, dict)
                or Path(str(record.get("path") or "")).resolve(strict=True)
                != approved_path
                or _valid_sha256_text(record.get("sha256")) != _sha256(approved_path)
            ):
                return False
        provenance = {
            "layout_backend_sha256": runtime_sources["layout_backend"]["sha256"],
            "structured_exporter_source_sha256": runtime_sources[
                "structured_exporter"
            ]["sha256"],
            "evaluator_sha256": runtime_sources["evaluator"]["sha256"],
            "config_sha256": receipt.get("config_sha256"),
            "process_sha256": receipt.get("process_sha256"),
        }
        evidence_provenance = {
            "layout_backend_sha256": evidence.get("layout_backend_sha256"),
            "structured_exporter_source_sha256": evidence[
                "structured_exporter"
            ]["source_sha256"],
            "evaluator_sha256": evidence.get("evaluator_sha256"),
            "config_sha256": evidence.get("config_sha256"),
            "process_sha256": evidence.get("process_sha256"),
        }
        if any(
            _valid_sha256_text(artifacts.get(name)) != _valid_sha256_text(digest)
            or _valid_sha256_text(evidence_provenance.get(name))
            != _valid_sha256_text(digest)
            for name, digest in provenance.items()
        ):
            return False
        for path_name, digest_name in (
            ("config", "config_sha256"),
            ("process_file", "process_sha256"),
        ):
            bound_path = Path(str(receipt.get(path_name) or "")).resolve(strict=True)
            bound_digest = _valid_sha256_text(receipt.get(digest_name))
            if (
                bound_digest is None
                or not bound_path.is_file()
                or _sha256(bound_path) != bound_digest
                or artifacts.get(digest_name) != bound_digest
            ):
                return False
        return True
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return False


def _physical_batch_download_ready(job: Job, result: dict[str, Any]) -> bool:
    """Require one intact 11/11 attested batch before exposing any GDS."""

    candidates = result.get("candidates")
    if (
        job.status != "SUCCEEDED"
        or result.get("mode") != "physical"
        or result.get("evidence_source") != "FRESH_REAL_EMX"
        or result.get("physical_q_metadata_status") != "BOUND_11_OF_11"
        or not isinstance(candidates, list)
        or len(candidates) != len(Q_SWEEP_VALUES)
    ):
        return False
    q_values = [_safe_int(item.get("q_target")) if isinstance(item, dict) else None for item in candidates]
    if None in q_values or set(q_values) != set(Q_SWEEP_VALUES):
        return False
    receipt_hashes: set[str] = set()
    result_hashes: set[str] = set()
    for candidate in candidates:
        if (
            not isinstance(candidate, dict)
            or candidate.get("evidence_source") != "FRESH_REAL_EMX"
            or not _physical_candidate_artifacts_intact(job, candidate)
        ):
            return False
        artifacts = candidate.get("artifacts") or {}
        receipt_hashes.add(str(artifacts.get("backend_receipt_sha256") or ""))
        result_hashes.add(str(artifacts.get("physical_results_sha256") or ""))
    return len(receipt_hashes) == 1 and len(result_hashes) == 1


def _physical_candidate_artifacts_intact(job: Job, candidate: dict[str, Any]) -> bool:
    """Re-run the path, SHA, receipt, and manifest gates at download time."""

    try:
        artifacts = candidate.get("artifacts")
        if not isinstance(artifacts, dict):
            return False
        if artifacts.get("physical_evidence_class") != "CANDIDATE_BOUND_GDS_S4P_CONTRACT":
            return False
        physical_root = (job.output_dir / "physical_backend").resolve(strict=True)
        if not physical_root.is_dir():
            return False
        path_fields = {
            "gds": "gds_sha256",
            "s4p": "s4p_sha256",
            "evidence_manifest": "evidence_manifest_sha256",
            "backend_receipt": "backend_receipt_sha256",
            "physical_results": "physical_results_sha256",
            "geometry_json": "geometry_json_sha256",
            "layout_manifest": "layout_manifest_sha256",
            "evaluation_summary": "evaluation_summary_sha256",
        }
        paths: dict[str, Path] = {}
        digests: dict[str, str] = {}
        for name, digest_name in path_fields.items():
            raw = artifacts.get(name)
            if not isinstance(raw, str) or not raw:
                return False
            path = Path(raw).expanduser().resolve(strict=True)
            digest = _valid_sha256_text(artifacts.get(digest_name))
            if (
                digest is None
                or not path.is_relative_to(physical_root)
                or not path.is_file()
                or _sha256(path) != digest
            ):
                return False
            paths[name] = path
            digests[digest_name] = digest
        if paths["backend_receipt"] != physical_root / "backend_receipt.json":
            return False
        if paths["physical_results"] != physical_root / "physical_results.json":
            return False

        candidate_id = str(candidate.get("candidate_id") or "")
        q_target = _safe_int(candidate.get("q_target"))
        geometry_sha256 = _valid_sha256_text(candidate.get("geometry_sha256"))
        if not candidate_id or q_target not in Q_SWEEP_VALUES or geometry_sha256 is None:
            return False
        evidence = validate_physical_evidence_manifest(
            paths["evidence_manifest"],
            root=physical_root,
            expected_candidate_id=candidate_id,
            expected_q_target=int(q_target),
            expected_geometry_sha256=geometry_sha256,
        )
        for kind in ("gds", "s4p"):
            if (
                Path(str(evidence["artifacts"][kind]["path"])).resolve()
                != paths[kind]
                or evidence["artifacts"][kind]["sha256"]
                != digests[f"{kind}_sha256"]
            ):
                return False

        receipt = json.loads(paths["backend_receipt"].read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or any(
            receipt.get(name) != value
            for name, value in {
                "schema": "rfic_q_sweep_physical_backend_receipt.v1",
                "overall_status": "PASS",
                "decision": "FRESH_REAL_EMX_Q_SWEEP_READY",
                "evidence_source": "FRESH_REAL_EMX",
            }.items()
        ):
            return False
        if (
            receipt.get("candidate_count") != len(Q_SWEEP_VALUES)
            or receipt.get("pass_count") != len(Q_SWEEP_VALUES)
            or receipt.get("fail_count") != 0
            or receipt.get("candidate_evidence_manifest_count") != len(Q_SWEEP_VALUES)
            or receipt.get("q_values") != list(Q_SWEEP_VALUES)
            or _valid_sha256_text(receipt.get("physical_results_sha256"))
            != digests["physical_results_sha256"]
            or Path(str(receipt.get("backend_receipt") or "")).resolve()
            != paths["backend_receipt"]
            or Path(str(receipt.get("physical_results") or "")).resolve()
            != paths["physical_results"]
        ):
            return False
        receipt_candidates = receipt.get("candidates")
        if not isinstance(receipt_candidates, list) or len(receipt_candidates) != len(
            Q_SWEEP_VALUES
        ):
            return False
        receipt_candidate = next(
            (
                item
                for item in receipt_candidates
                if isinstance(item, dict) and item.get("candidate_id") == candidate_id
            ),
            None,
        )
        receipt_artifacts = (
            receipt_candidate.get("artifacts")
            if isinstance(receipt_candidate, dict)
            else None
        )
        if (
            not isinstance(receipt_candidate, dict)
            or receipt_candidate.get("status") != "PASS"
            or receipt_candidate.get("q_target") != q_target
            or receipt_candidate.get("geometry_sha256") != geometry_sha256
            or not isinstance(receipt_artifacts, dict)
            or _valid_sha256_text(receipt_candidate.get("emx_command_sha256"))
            != _valid_sha256_text(artifacts.get("emx_command_sha256"))
            or not isinstance(receipt_candidate.get("emx_command_argument_count"), int)
            or receipt_candidate.get("emx_command_argument_count", 0) <= 0
            or any(
                receipt_artifacts.get(name) != digests[name]
                for name in (
                    "gds_sha256",
                    "s4p_sha256",
                    "evidence_manifest_sha256",
                    "geometry_json_sha256",
                    "layout_manifest_sha256",
                    "evaluation_summary_sha256",
                )
            )
        ):
            return False
        for name in (
            "gds",
            "s4p",
            "evidence_manifest",
            "geometry_json",
            "layout_manifest",
            "evaluation_summary",
        ):
            raw_receipt_path = Path(str(receipt_artifacts.get(name) or ""))
            receipt_artifact_path = (
                raw_receipt_path
                if raw_receipt_path.is_absolute()
                else physical_root / raw_receipt_path
            ).resolve(strict=True)
            if receipt_artifact_path != paths[name]:
                return False

        approved_sources = {
            "backend": Path(__file__).with_name("q_sweep_physical_backend.py").resolve(),
            "structured_exporter": Path(__file__).resolve().parents[1]
            / "layout"
            / "export.py",
            "emx_wrapper": Path(__file__).resolve().parents[1]
            / "execution"
            / "zeus_cadence.py",
        }
        runtime_sources = receipt.get("runtime_sources")
        if not isinstance(runtime_sources, dict) or set(runtime_sources) != set(
            approved_sources
        ):
            return False
        for name, approved_path in approved_sources.items():
            record = runtime_sources.get(name)
            approved_path = approved_path.resolve(strict=True)
            if (
                not isinstance(record, dict)
                or Path(str(record.get("path") or "")).resolve(strict=True)
                != approved_path
                or _valid_sha256_text(record.get("sha256")) != _sha256(approved_path)
            ):
                return False
        provenance = {
            "backend_sha256": runtime_sources["backend"]["sha256"],
            "structured_exporter_source_sha256": runtime_sources[
                "structured_exporter"
            ]["sha256"],
            "emx_wrapper_sha256": runtime_sources["emx_wrapper"]["sha256"],
            "config_sha256": receipt.get("config_sha256"),
            "process_sha256": receipt.get("process_sha256"),
        }
        evidence_provenance = {
            "backend_sha256": evidence.get("backend_sha256"),
            "structured_exporter_source_sha256": (
                evidence.get("structured_exporter") or {}
            ).get("source_sha256"),
            "emx_wrapper_sha256": evidence.get("emx_wrapper_sha256"),
            "config_sha256": evidence.get("config_sha256"),
            "process_sha256": evidence.get("process_sha256"),
        }
        if any(
            _valid_sha256_text(artifacts.get(name)) != _valid_sha256_text(digest)
            or _valid_sha256_text(evidence_provenance.get(name))
            != _valid_sha256_text(digest)
            for name, digest in provenance.items()
        ):
            return False
        for path_name, digest_name in (
            ("config", "config_sha256"),
            ("process_file", "process_sha256"),
        ):
            bound_path = Path(str(receipt.get(path_name) or "")).resolve(strict=True)
            bound_digest = _valid_sha256_text(receipt.get(digest_name))
            if bound_digest is None or not bound_path.is_file() or _sha256(bound_path) != bound_digest:
                return False
            if artifacts.get(digest_name) != bound_digest:
                return False
        return True
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return False


def _attach_physical_q_components(root: Path, result: dict[str, Any]) -> str:
    """Attach only the immutable Qp/Qs values recomputed from accepted S4P data."""

    if result.get("mode") != "physical":
        return "NOT_APPLICABLE_PROXY"
    candidates = result.get("candidates") or []
    if not isinstance(candidates, list) or len(candidates) != len(Q_SWEEP_VALUES):
        return "UNAVAILABLE_UNBOUND_S4P_FEATURES"
    q_grid = [
        _safe_int(item.get("q_target")) if isinstance(item, dict) else None
        for item in candidates
    ]
    if None in q_grid or set(q_grid) != set(Q_SWEEP_VALUES):
        return "UNAVAILABLE_UNBOUND_S4P_FEATURES"
    bound: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("evidence_source") != "FRESH_REAL_EMX":
            return "UNAVAILABLE_UNBOUND_S4P_FEATURES"
        features = candidate.get("fresh_emx_features_15ghz") or {}
        artifacts = candidate.get("artifacts") or {}
        if not isinstance(features, dict) or not isinstance(artifacts, dict):
            return "UNAVAILABLE_UNBOUND_S4P_FEATURES"
        qp = _safe_float(features.get("Qp"))
        qs = _safe_float(features.get("Qs"))
        declared_qmin = _safe_float(features.get("Qmin"))
        observed = _safe_float((candidate.get("observed_features") or {}).get("Q_scalar"))
        s4p_sha = _valid_sha256_text(artifacts.get("s4p_sha256"))
        evidence_sha = _valid_sha256_text(artifacts.get("evidence_manifest_sha256"))
        receipt_sha = _valid_sha256_text(artifacts.get("backend_receipt_sha256"))
        if (
            qp is None
            or qs is None
            or declared_qmin is None
            or observed is None
            or qp <= 0.0
            or qs <= 0.0
            or s4p_sha is None
            or evidence_sha is None
            or receipt_sha is None
            or artifacts.get("physical_evidence_class")
            != "CANDIDATE_BOUND_GDS_S4P_CONTRACT"
        ):
            return "UNAVAILABLE_UNBOUND_S4P_FEATURES"
        qmin = min(qp, qs)
        if not (
            math.isclose(qmin, declared_qmin, rel_tol=1.0e-10, abs_tol=1.0e-12)
            and math.isclose(qmin, observed, rel_tol=1.0e-10, abs_tol=1.0e-12)
        ):
            return "UNAVAILABLE_QMIN_MISMATCH"
        bound.append(
            (
                candidate,
                {
                    "Qp": qp,
                    "Qs": qs,
                    "Qmin": qmin,
                    "definition": "Qmin=min(Qp, Qs)",
                    "source": "S4P_RECOMPUTED_AND_TERMINAL_RECEIPT_BOUND",
                    "source_sha256": s4p_sha,
                    "evidence_manifest_sha256": evidence_sha,
                    "backend_receipt_sha256": receipt_sha,
                },
            )
        )
    for candidate, components in bound:
        candidate["physical_q_components"] = components
    return f"BOUND_{len(bound)}_OF_{len(candidates)}"


def _valid_sha256_text(value: Any) -> str | None:
    digest = str(value or "").lower()
    return digest if re.fullmatch(r"[0-9a-f]{64}", digest) else None


def _public_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove server-local paths while retaining hashes needed for traceability."""

    public: list[dict[str, Any]] = []
    for raw in candidates:
        candidate = dict(raw)
        q_components = candidate.pop("physical_q_components", None)
        artifacts = dict(candidate.pop("artifacts", {}) or {})
        candidate["artifact_sha256"] = {
            kind: artifacts.get(f"{kind}_sha256")
            for kind in ("gds", "s4p", "preview")
            if artifacts.get(f"{kind}_sha256")
        }
        candidate["artifact_evidence"] = {
            "physical_evidence_class": artifacts.get("physical_evidence_class"),
            "layout_evidence_class": artifacts.get("layout_evidence_class"),
            "evidence_manifest_sha256": artifacts.get(
                "evidence_manifest_sha256"
            ),
            "backend_receipt_sha256": artifacts.get("backend_receipt_sha256"),
            "physical_results_sha256": artifacts.get("physical_results_sha256"),
            "layout_backend_receipt_sha256": artifacts.get(
                "layout_backend_receipt_sha256"
            ),
            "layout_results_sha256": artifacts.get("layout_results_sha256"),
            "config_sha256": artifacts.get("config_sha256"),
            "process_sha256": artifacts.get("process_sha256"),
            "backend_sha256": artifacts.get("backend_sha256"),
            "layout_backend_sha256": artifacts.get("layout_backend_sha256"),
            "structured_exporter_source_sha256": artifacts.get(
                "structured_exporter_source_sha256"
            ),
            "emx_wrapper_sha256": artifacts.get("emx_wrapper_sha256"),
            "evaluator_sha256": artifacts.get("evaluator_sha256"),
            "emx_status": artifacts.get("emx_status"),
            "calibre_drc": artifacts.get("calibre_drc"),
            "hfss_correlation": artifacts.get("hfss_correlation"),
        }
        errors = candidate.get("per_feature_percent_error") or {}
        values = [abs(float(errors[name])) for name in FEATURE_NAMES]
        candidate["mean_absolute_percent_error"] = sum(values) / len(values)
        observed = candidate.get("observed_features") or {}
        evidence_source = str(candidate.get("evidence_source") or "")
        is_physical = evidence_source == "FRESH_REAL_EMX"
        is_layout = evidence_source == LAYOUT_EVIDENCE_CLASS
        components = q_components if isinstance(q_components, dict) else {}
        qp = _safe_float(components.get("Qp"))
        qs = _safe_float(components.get("Qs"))
        reported = _safe_float(observed.get("Q_scalar"))
        candidate["q_metadata"] = {
            "reported_value": reported,
            "reported_label": "Qmin" if is_physical else "frozen_forward_proxy_Q",
            "definition": (
                "Qmin=min(Qp, Qs)"
                if is_physical
                else (
                    "Frozen-forward proxy reconstruction; structured GDS exists but "
                    "EMX/Qp/Qs are NOT_RUN."
                    if is_layout
                    else "Frozen-forward proxy reconstruction; not measured Qp/Qs."
                )
            ),
            "Qp": qp if is_physical else None,
            "Qs": qs if is_physical else None,
            "components_status": (
                "BOUND_FRESH_REAL_EMX"
                if is_physical and qp is not None and qs is not None
                else (
                    "UNAVAILABLE_IN_PUBLIC_RESULT"
                    if is_physical
                    else "NOT_APPLICABLE_PROXY"
                )
            ),
            "source_sha256": (
                components.get("source_sha256")
                if is_physical and qp is not None and qs is not None
                else None
            ),
        }
        public.append(candidate)
    return public


def _error_summary(
    candidates: list[dict[str, Any]], selected_q: Any
) -> dict[str, float | int | str | None]:
    """Summarize the declared four-feature relative-error denominator."""

    if not candidates:
        return {
            "candidate_count": 0,
            "selected_q": None,
            "selected_total_error_percent_rmse": None,
            "mean_total_error_percent_rmse": None,
            "definition": "RMS of the four per-feature percentage errors; mean is over all Q candidates.",
        }
    totals = [float(item["relative_percent_rmse"]) for item in candidates]
    selected = next(
        (item for item in candidates if int(item["q_target"]) == int(selected_q)),
        None,
    )
    return {
        "candidate_count": len(candidates),
        "selected_q": None if selected is None else int(selected["q_target"]),
        "selected_total_error_percent_rmse": (
            None if selected is None else float(selected["relative_percent_rmse"])
        ),
        "mean_total_error_percent_rmse": sum(totals) / len(totals),
        "definition": "RMS of the four per-feature percentage errors; mean is over all Q candidates.",
    }


def _evidence_readiness(
    *,
    mode: str,
    job_status: str,
    result: dict[str, Any],
    candidates: list[dict[str, Any]],
    gds_by_q: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Describe readiness without treating one physical gate as another."""

    evidence_source = str(result.get("evidence_source") or "")
    fresh_count = sum(
        1 for candidate in candidates if candidate.get("evidence_source") == "FRESH_REAL_EMX"
    )
    q_component_count = sum(
        1
        for candidate in candidates
        if (candidate.get("q_metadata") or {}).get("components_status")
        == "BOUND_FRESH_REAL_EMX"
    )
    complete_fresh = (
        mode == "physical"
        and evidence_source == "FRESH_REAL_EMX"
        and len(candidates) == len(Q_SWEEP_VALUES)
        and fresh_count == len(Q_SWEEP_VALUES)
        and q_component_count == len(Q_SWEEP_VALUES)
        and len(gds_by_q) == len(Q_SWEEP_VALUES)
    )
    complete_layout = (
        mode == "layout"
        and evidence_source == LAYOUT_EVIDENCE_CLASS
        and len(candidates) == len(Q_SWEEP_VALUES)
        and fresh_count == 0
        and q_component_count == 0
        and len(gds_by_q) == len(Q_SWEEP_VALUES)
    )
    if complete_fresh:
        selection_readiness = "FRESH_REAL_EMX_11_OF_11"
        display_label = "Fresh real-EMX：11/11 候选完成"
    elif complete_layout:
        selection_readiness = "STRUCTURED_GDS_11_OF_11"
        display_label = "结构化二进制 GDS：11/11（EMX/DRC 未运行）"
    elif evidence_source == "FROZEN_FORWARD_PROXY_DIAGNOSTIC" or mode == "proxy":
        selection_readiness = "FROZEN_FORWARD_PROXY_DIAGNOSTIC_ONLY"
        display_label = "Frozen forward proxy：仅诊断，不是物理证据"
    elif job_status in {"QUEUED", "RUNNING", "NOT_STARTED"}:
        selection_readiness = "PHYSICAL_BACKEND_NOT_COMPLETE"
        display_label = (
            "Layout 模式已配置，结构化 GDS 尚未完成"
            if mode == "layout"
            else "Physical 模式已配置，fresh real-EMX 尚未完成"
        )
    else:
        selection_readiness = "PHYSICAL_EVIDENCE_NOT_READY"
        display_label = "物理证据未就绪"
    return {
        "configured_mode": mode,
        "job_status": job_status,
        "selection_readiness": selection_readiness,
        "display_label": display_label,
        "evidence_source": evidence_source or None,
        "candidate_count": len(candidates),
        "fresh_real_emx_candidate_count": fresh_count,
        "q_components_bound_count": q_component_count,
        "gds_download_ready_count": len(gds_by_q),
        "structured_gds_candidate_count": (
            len(candidates) if evidence_source == LAYOUT_EVIDENCE_CLASS else 0
        ),
        "expected_candidate_count": len(Q_SWEEP_VALUES),
        "calibre_drc": "NOT_REPORTED_BY_Q_SWEEP",
        "hfss_correlation": "NOT_REPORTED_BY_Q_SWEEP",
        "proxy_is_physical_evidence": False,
    }


class GuiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: JobStore) -> None:
        self.store = store
        super().__init__(address, GuiHandler)


class GuiHandler(BaseHTTPRequestHandler):
    server: GuiServer

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._bytes(
                HTTPStatus.OK,
                _index_html(self.server.store.mode).encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        if parsed.path == "/api/context":
            self._json(HTTPStatus.OK, self.server.store.public_context())
            return
        if parsed.path == "/api/jobs":
            self._json(HTTPStatus.OK, self.server.store.list_jobs())
            return
        match = re.fullmatch(r"/api/jobs/([^/]+)", parsed.path)
        if match:
            try:
                self._json(HTTPStatus.OK, self.server.store.public_record(match.group(1)))
            except KeyError:
                self._json(HTTPStatus.NOT_FOUND, {"error": "job not found"})
            return
        match = re.fullmatch(r"/api/jobs/([^/]+)/artifacts/([^/]+)", parsed.path)
        if match:
            try:
                if match.group(2) == "gds_zip":
                    payload, filename = self.server.store.gds_bundle(match.group(1))
                    self._bytes(HTTPStatus.OK, payload, "application/zip", filename=filename)
                    return
                path = self.server.store.artifact(match.group(1), match.group(2))
                media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                filename = self.server.store.artifact_filename(
                    match.group(1), match.group(2)
                )
                self._bytes(HTTPStatus.OK, path.read_bytes(), media, filename=filename)
            except (KeyError, FileNotFoundError, PermissionError):
                self._json(HTTPStatus.NOT_FOUND, {"error": "artifact not found"})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route not in {"/api/jobs", "/api/model"}:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            if route == "/api/model":
                record = self.server.store.switch_model(payload)
                self._json(HTTPStatus.OK, record)
            else:
                record = self.server.store.create(payload)
                self._json(HTTPStatus.ACCEPTED, record)
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self._bytes(
            status,
            (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _bytes(
        self,
        status: HTTPStatus,
        payload: bytes,
        content_type: str,
        *,
        filename: str | None = None,
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Three-input RFIC inverse-synthesis GUI")
    parser.add_argument("--model-dir", default=os.environ.get("RFIC_Q_SWEEP_MODEL_DIR"))
    parser.add_argument("--model-contract", default=None)
    parser.add_argument("--model-profile", choices=("deployed100k", "real10k", "custom"),
                        default=os.environ.get("RFIC_Q_SWEEP_MODEL_PROFILE", "deployed100k"))
    parser.add_argument(
        "--output-root",
        default=os.environ.get("RFIC_Q_SWEEP_OUTPUT_ROOT", "q_sweep_gui_runs"),
    )
    parser.add_argument(
        "--mode",
        choices=("proxy", "layout", "physical"),
        default=os.environ.get("RFIC_Q_SWEEP_MODE", "proxy"),
    )
    parser.add_argument(
        "--layout-backend-command",
        default=os.environ.get("RFIC_Q_SWEEP_LAYOUT_BACKEND"),
    )
    parser.add_argument(
        "--physical-backend-command",
        default=os.environ.get("RFIC_Q_SWEEP_PHYSICAL_BACKEND"),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.model_dir:
        raise SystemExit("--model-dir or RFIC_Q_SWEEP_MODEL_DIR is required")
    if args.mode == "physical" and not args.physical_backend_command:
        raise SystemExit(
            "physical mode requires --physical-backend-command or "
            "RFIC_Q_SWEEP_PHYSICAL_BACKEND"
        )
    if args.mode == "layout" and not args.layout_backend_command:
        raise SystemExit(
            "layout mode requires --layout-backend-command or "
            "RFIC_Q_SWEEP_LAYOUT_BACKEND"
        )
    if args.mode == "physical":
        try:
            _validated_physical_backend_argv(args.physical_backend_command)
        except ValueError as exc:
            raise SystemExit(f"invalid physical backend command: {exc}") from exc
    if args.mode == "layout":
        try:
            _validated_layout_backend_argv(args.layout_backend_command)
        except ValueError as exc:
            raise SystemExit(f"invalid layout backend command: {exc}") from exc
    from .model_adapter import load_model

    model = load_model(args.model_dir, profile=args.model_profile, contract_path=args.model_contract)
    store = JobStore(
        model=model,
        output_root=Path(args.output_root),
        mode=args.mode,
        physical_backend_command=args.physical_backend_command,
        layout_backend_command=args.layout_backend_command,
        model_contract_path=args.model_contract,
        model_profile=args.model_profile,
    )
    server = GuiServer((args.host, args.port), store)
    url = f"http://{args.host}:{server.server_address[1]}/"
    print(json.dumps({"status": "READY", "url": url, "mode": args.mode}))
    if args.open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _index_html(mode: str) -> str:
    return Path(__file__).with_name("q_sweep_ui.html").read_text(encoding="utf-8").replace("__MODE__", mode).replace(
        "__MODE_LABEL__",
        (
            "Fresh real-EMX"
            if mode == "physical"
            else ("Structured GDS · EMX 未运行" if mode == "layout" else "Frozen proxy")
        ),
    )




if __name__ == "__main__":
    raise SystemExit(main())
