"""Exact Q=10..20 sweep around one frozen three-feature target."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .frozen_mlp import FrozenTandemMLP, GEOMETRY_COLUMNS
from ..analysis.operating_point15 import LABEL_POLICY, classify as operating_point_classify
from .layout_evidence import (
    LAYOUT_EVIDENCE_CLASS,
    validate_layout_evidence_manifest,
)
from .physical_evidence import validate_physical_evidence_manifest


Q_SWEEP_VALUES = tuple(range(10, 21))
FEATURE_NAMES = ("Lp_nH", "Ls_nH", "Q_scalar", "K_abs")
DECLARED_FEATURE_SPANS = np.asarray((2.5, 2.5, 20.0, 0.8), dtype=float)
FRESH_EMX_FEATURE_NAMES = (
    "frequency_hz",
    "Lp_nH",
    "Ls_nH",
    "Qp",
    "Qs",
    "Qmin",
    "K_abs",
    "K_signed",
    "reference_impedance_min_ohm",
    "reference_impedance_max_ohm",
)
EXPECTED_S4P_FREQUENCIES_HZ = np.linspace(5.0e9, 60.0e9, 111)
S4P_FREQUENCY_TOLERANCE_HZ = 1.0
BACKEND_FEATURE_RELATIVE_TOLERANCE = 1.0e-9
BACKEND_FEATURE_ABSOLUTE_TOLERANCE = 1.0e-12
PHYSICAL_BACKEND_MODULE = (
    "rfic_transformer_inverse_design.synthesis.q_sweep_physical_backend"
)
PHYSICAL_BACKEND_RECEIPT_SCHEMA = "rfic_q_sweep_physical_backend_receipt.v1"
PHYSICAL_BACKEND_RECEIPT_FILENAME = "backend_receipt.json"
LAYOUT_BACKEND_MODULE = (
    "rfic_transformer_inverse_design.synthesis.q_sweep_layout_backend"
)
LAYOUT_BACKEND_RECEIPT_SCHEMA = "rfic_q_sweep_layout_backend_receipt.v1"
LAYOUT_BACKEND_RECEIPT_FILENAME = "layout_backend_receipt.json"
LAYOUT_RESULTS_SCHEMA = "rfic_q_sweep_layout_results.v1"
LAYOUT_RESULTS_FILENAME = "layout_results.json"
LAYOUT_REQUEST_SCHEMA = "rfic_q_sweep_layout_request.v1"


@dataclass(frozen=True)
class PhysicalTarget3:
    """The three user-entered physical features held fixed during the Q sweep."""

    design_id: str
    lp_nh: float
    ls_nh: float
    k_abs: float

    def validate(self) -> None:
        allowed = set(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
        )
        if not self.design_id or any(char not in allowed for char in self.design_id):
            raise ValueError(
                "design_id must contain only letters, digits, underscore, dot, or dash"
            )
        values = np.asarray((self.lp_nh, self.ls_nh, self.k_abs), dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("Lp, Ls, and |K| must be finite")
        if not 0.5 <= self.lp_nh <= 3.0:
            raise ValueError("Lp must be inside the frozen support [0.5, 3.0] nH")
        if not 0.5 <= self.ls_nh <= 3.0:
            raise ValueError("Ls must be inside the frozen support [0.5, 3.0] nH")
        if not 0.0 < self.k_abs <= 0.8:
            raise ValueError("|K| must be inside the frozen support (0, 0.8]")

    def vector_for_q(self, q_value: int) -> np.ndarray:
        return np.asarray(
            (self.lp_nh, self.ls_nh, float(q_value), self.k_abs), dtype=float
        )


@dataclass(frozen=True)
class CandidateMetrics:
    """One Q candidate and either proxy or fresh-real-EM observations."""

    candidate_id: str
    q_target: int
    target_features: dict[str, float]
    observed_features: dict[str, float]
    fresh_emx_features_15ghz: dict[str, float]
    geometry_um: dict[str, float]
    geometry_sha256: str
    per_feature_abs_error: dict[str, float]
    per_feature_percent_error: dict[str, float]
    declared_range_normalized_rmse: float
    relative_percent_rmse: float
    evidence_source: str
    observation_source: str
    artifact_evidence_class: str
    artifacts: dict[str, str]


@dataclass(frozen=True)
class QSweepResult:
    """Complete eleven-candidate sweep and deterministic selection."""

    design_id: str
    mode: str
    target_three_features: dict[str, float]
    q_values: tuple[int, ...]
    selected_candidate_id: str
    selected_q: int
    selection_score: float
    evidence_source: str
    metric_source: str
    selection_source: str
    artifact_evidence_class: str
    candidates: tuple[CandidateMetrics, ...]
    selected_artifacts: dict[str, str]
    scientific_boundary: str

    def record(self) -> dict[str, Any]:
        value = asdict(self)
        value['label_policy'] = LABEL_POLICY
        value['srf_role'] = 'OPTIONAL_DIAGNOSTIC_NOT_A_GATE'
        value["q_values"] = list(self.q_values)
        value["candidates"] = [asdict(item) for item in self.candidates]
        return value


def run_q_sweep(
    model: FrozenTandemMLP,
    target: PhysicalTarget3,
    *,
    q_values: Iterable[int] = Q_SWEEP_VALUES,
) -> QSweepResult:
    """Run the frozen MLP and rank all exact-Q candidates through its proxy."""

    target.validate()
    q_grid = _validate_q_grid(q_values)
    targets = np.vstack([target.vector_for_q(q_value) for q_value in q_grid])
    prediction = model.predict(targets)
    candidates: list[CandidateMetrics] = []
    for index, q_value in enumerate(q_grid):
        geometry = {
            name.removeprefix("geom__"): float(value)
            for name, value in zip(GEOMETRY_COLUMNS, prediction.geometry[index])
        }
        candidate_id = f"{target.design_id}_q{q_value:02d}"
        candidates.append(
            _candidate_metrics(
                candidate_id=candidate_id,
                q_target=q_value,
                target_features=targets[index],
                observed_features=prediction.proxy_features[index],
                fresh_emx_features_15ghz={},
                geometry=geometry,
                evidence_source="FROZEN_FORWARD_PROXY_DIAGNOSTIC",
                artifacts={},
            )
        )
    selected = min(
        candidates,
        key=lambda item: (item.declared_range_normalized_rmse, item.q_target),
    )
    return QSweepResult(
        design_id=target.design_id,
        mode="proxy",
        target_three_features={
            "Lp_nH": float(target.lp_nh),
            "Ls_nH": float(target.ls_nh),
            "K_abs": float(target.k_abs),
        },
        q_values=q_grid,
        selected_candidate_id=selected.candidate_id,
        selected_q=selected.q_target,
        selection_score=selected.declared_range_normalized_rmse,
        evidence_source="FROZEN_FORWARD_PROXY_DIAGNOSTIC",
        metric_source="FROZEN_FORWARD_PROXY_DIAGNOSTIC",
        selection_source="FROZEN_FORWARD_PROXY_DIAGNOSTIC",
        artifact_evidence_class="NONE",
        candidates=tuple(candidates),
        selected_artifacts={},
        scientific_boundary=(
            "The selected Q minimizes declared-range-normalized error only on the "
            "frozen forward surrogate. It is provisional until all eleven GDS "
            "candidates receive fresh real-EMX evaluation."
        ),
    )


def execute_q_sweep(
    *,
    model: FrozenTandemMLP,
    target: PhysicalTarget3,
    output_dir: str | Path,
    mode: str = "proxy",
    physical_backend_command: str | None = None,
    layout_backend_command: str | None = None,
) -> QSweepResult:
    """Create a no-clobber run and execute proxy, layout, or physical mode."""

    if mode not in {"proxy", "layout", "physical"}:
        raise ValueError("mode must be 'proxy', 'layout', or 'physical'")
    root = Path(output_dir).expanduser().resolve()
    _prepare_no_clobber_directory(root)
    started_utc = _utc_now()
    model_identity = _model_identity(model)
    proxy = run_q_sweep(model, target)
    _write_candidates_csv(root / "proxy_candidates.csv", proxy.candidates)
    _write_json(root / "proxy_candidates.json", proxy.record())
    request_path = root / (
        "layout_backend_request.json"
        if mode == "layout"
        else "physical_backend_request.json"
    )
    _write_backend_request(request_path, model, proxy, mode=mode)

    if mode in {"layout", "physical"}:
        try:
            if mode == "physical":
                if not physical_backend_command:
                    raise RuntimeError(
                        "physical mode requires RFIC_Q_SWEEP_PHYSICAL_BACKEND or "
                        "--physical-backend-command; proxy ranking cannot be reported "
                        "as physical validation"
                    )
                result = _run_physical_backend(
                    proxy=proxy,
                    root=root,
                    command=physical_backend_command,
                )
            else:
                if not layout_backend_command:
                    raise RuntimeError(
                        "layout mode requires RFIC_Q_SWEEP_LAYOUT_BACKEND or "
                        "--layout-backend-command; a PNG preview is not a GDS"
                    )
                result = _run_layout_backend(
                    proxy=proxy,
                    root=root,
                    command=layout_backend_command,
                )
        except Exception as exc:
            _write_json(
                root / "run_manifest.json",
                {
                    "schema": "rfic_q_sweep_run_manifest.v1",
                    "overall_status": "FAIL",
                    "started_utc": started_utc,
                    "completed_utc": _utc_now(),
                    "mode": mode,
                    "model_id": model.model_id,
                    "model_seed": model.model_seed,
                    "model_identity": model_identity,
                    "q_values": list(Q_SWEEP_VALUES),
                    "candidate_count": len(proxy.candidates),
                    "failure_type": type(exc).__name__,
                    "failure_message": str(exc),
                    "evidence_source": "FROZEN_FORWARD_PROXY_DIAGNOSTIC_ONLY",
                    "scientific_boundary": (
                        "The requested backend stage was not completed. Proxy candidates "
                        "must not be reported as GDS, fresh EMX, DRC, or HFSS evidence."
                    ),
                },
            )
            raise
        candidates_name = (
            "physical_candidates.csv"
            if mode == "physical"
            else "layout_candidates.csv"
        )
        _write_candidates_csv(root / candidates_name, result.candidates)
    else:
        result = proxy

    selected = next(
        item
        for item in result.candidates
        if item.candidate_id == result.selected_candidate_id
    )
    deliverables = root / "deliverables"
    deliverables.mkdir(exist_ok=False)
    preview_path = deliverables / f"{target.design_id}_selected_structure.png"
    selected_artifacts = dict(result.selected_artifacts)
    source_preview = selected_artifacts.get("preview")
    if source_preview:
        shutil.copy2(source_preview, preview_path)
    else:
        render_geometry_preview(
            selected.geometry_um,
            preview_path,
            title=f"{target.design_id}: selected Q={selected.q_target}",
        )
    selected_artifacts["preview"] = str(preview_path)

    if mode == "physical":
        selected_artifacts = _copy_selected_physical_artifacts(
            selected,
            deliverables,
            target.design_id,
            selected_artifacts,
        )
    result = QSweepResult(
        design_id=result.design_id,
        mode=result.mode,
        target_three_features=result.target_three_features,
        q_values=result.q_values,
        selected_candidate_id=result.selected_candidate_id,
        selected_q=result.selected_q,
        selection_score=result.selection_score,
        evidence_source=result.evidence_source,
        metric_source=result.metric_source,
        selection_source=result.selection_source,
        artifact_evidence_class=result.artifact_evidence_class,
        candidates=result.candidates,
        selected_artifacts=selected_artifacts,
        scientific_boundary=result.scientific_boundary,
    )
    _write_json(root / "selection.json", result.record())
    _write_json(root / "selected_geometry.json", selected.geometry_um)
    manifest = {
        "schema": "rfic_q_sweep_run_manifest.v1",
        "overall_status": "PASS",
        "started_utc": started_utc,
        "completed_utc": _utc_now(),
        "mode": mode,
        "model_id": model.model_id,
        "model_seed": model.model_seed,
        "model_identity": model_identity,
        "q_values": list(Q_SWEEP_VALUES),
        "candidate_count": len(result.candidates),
        "selected_candidate_id": result.selected_candidate_id,
        "selected_q": result.selected_q,
        "selection_score": result.selection_score,
        "evidence_source": result.evidence_source,
        "selected_artifacts": selected_artifacts,
        "scientific_boundary": result.scientific_boundary,
    }
    _write_json(root / "run_manifest.json", manifest)
    return result


def render_geometry_preview(
    geometry_um: dict[str, float],
    output_path: str | Path,
    *,
    title: str,
) -> None:
    """Render a geometry-coordinate preview; this is not a GDS or DRC result."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output = Path(output_path)
    primary = _octagon(
        float(geometry_um["primary_outer_width_um"]),
        float(geometry_um["primary_outer_height_um"]),
        0.0,
    )
    offset = float(geometry_um["offset_um"])
    secondary = _octagon(
        float(geometry_um["secondary_outer_width_um"]),
        float(geometry_um["secondary_outer_height_um"]),
        offset,
    )
    line_width = float(geometry_um["line_width_um"])
    primary_feed = float(geometry_um["primary_feed_extension_um"])
    secondary_feed = float(geometry_um["secondary_feed_extension_um"])
    primary_span = float(geometry_um["primary_terminal_y_span_um"])
    secondary_span = float(geometry_um["secondary_terminal_y_span_um"])
    fig, ax = plt.subplots(figsize=(9.2, 5.4), dpi=180)
    ax.set_facecolor("#f7f8fa")
    for points, color, label in (
        (primary, "#1769d2", "Primary / M10"),
        (secondary, "#e85b0c", "Secondary / M9"),
    ):
        ax.plot(
            points[:, 0],
            points[:, 1],
            color=color,
            linewidth=max(2.0, line_width * 0.45),
            solid_joinstyle="miter",
            label=label,
        )
    primary_x = -0.5 * float(geometry_um["primary_outer_width_um"])
    secondary_x = offset + 0.5 * float(
        geometry_um["secondary_outer_width_um"]
    )
    for y in (-0.5 * primary_span, 0.5 * primary_span):
        ax.plot(
            (primary_x - primary_feed, primary_x),
            (y, y),
            color="#1769d2",
            linewidth=max(2.0, line_width * 0.45),
        )
    for y in (-0.5 * secondary_span, 0.5 * secondary_span):
        ax.plot(
            (secondary_x, secondary_x + secondary_feed),
            (y, y),
            color="#e85b0c",
            linewidth=max(2.0, line_width * 0.45),
        )
    all_x = np.concatenate((primary[:, 0], secondary[:, 0]))
    all_y = np.concatenate((primary[:, 1], secondary[:, 1]))
    margin = 0.18 * max(float(np.ptp(all_x)), float(np.ptp(all_y)))
    ax.set_xlim(float(all_x.min() - primary_feed - margin), float(all_x.max() + secondary_feed + margin))
    ax.set_ylim(float(all_y.min() - margin), float(all_y.max() + margin))
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, color="#dce1e8", linewidth=0.6)
    ax.set_xlabel("x (um)")
    ax.set_ylabel("y (um)")
    ax.set_title(title, fontsize=14, weight="bold")
    ax.legend(loc="upper right", frameon=True)
    ax.text(
        0.01,
        0.01,
        "10-D coordinate preview; not GDS/DRC/EM evidence",
        transform=ax.transAxes,
        fontsize=8,
        color="#5a6575",
    )
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def _validated_physical_backend_argv(command: str) -> list[str]:
    """Allow only this checkout's hash-audited backend module in physical mode."""

    return _validated_packaged_backend_argv(
        command,
        module=PHYSICAL_BACKEND_MODULE,
        label="physical",
    )


def _validated_layout_backend_argv(command: str) -> list[str]:
    """Allow only this checkout's packaged GDS-only backend in layout mode."""

    return _validated_packaged_backend_argv(
        command,
        module=LAYOUT_BACKEND_MODULE,
        label="layout",
    )


def _validated_packaged_backend_argv(
    command: str,
    *,
    module: str,
    label: str,
) -> list[str]:
    """Validate a same-runtime packaged backend plus SHA-pinned config inputs."""

    argv = shlex.split(command)
    if len(argv) < 3:
        raise ValueError(f"{label} backend command is incomplete")
    executable = shutil.which(argv[0]) or argv[0]
    try:
        executable_path = Path(executable).expanduser().resolve(strict=True)
        runtime_path = Path(sys.executable).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(
            f"{label} backend Python executable is not verifiable: {exc}"
        ) from exc
    if executable_path != runtime_path:
        raise ValueError(
            f"{label} backend must use the same hash-audited Python runtime as the GUI"
        )
    if argv[1:3] != ["-m", module]:
        raise ValueError(
            f"{label} backend must invoke its packaged q-sweep backend module"
        )
    for forbidden in ("--request-json", "--out-dir"):
        if forbidden in argv[3:]:
            raise ValueError(f"{forbidden} is owned by the GUI and must not be pre-supplied")
    for required in ("--config", "--config-sha256", "--process-sha256"):
        if argv.count(required) != 1:
            raise ValueError(f"{label} backend command requires exactly one {required}")
        index = argv.index(required)
        if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
            raise ValueError(f"{label} backend command is missing the value for {required}")
    return argv


def _approved_runtime_source_paths() -> dict[str, Path]:
    package_root = Path(__file__).resolve().parents[1]
    return {
        "backend": Path(__file__).with_name("q_sweep_physical_backend.py").resolve(),
        "structured_exporter": (package_root / "layout" / "export.py").resolve(),
        "emx_wrapper": (package_root / "execution" / "zeus_cadence.py").resolve(),
    }


def _validated_backend_receipt(
    *,
    root: Path,
    request_path: Path,
    result_path: Path,
    proxy: QSweepResult,
) -> dict[str, Any]:
    """Validate the terminal run receipt and every file/hash it authorizes."""

    receipt_path = (root / PHYSICAL_BACKEND_RECEIPT_FILENAME).resolve()
    if not receipt_path.is_relative_to(root) or not receipt_path.is_file():
        raise ValueError("physical backend terminal receipt is missing or unsafe")
    receipt = _read_json(receipt_path)
    required_literals = {
        "schema": PHYSICAL_BACKEND_RECEIPT_SCHEMA,
        "overall_status": "PASS",
        "decision": "FRESH_REAL_EMX_Q_SWEEP_READY",
        "requested_label_source": "FRESH_REAL_EMX",
        "evidence_source": "FRESH_REAL_EMX",
    }
    for name, expected_value in required_literals.items():
        if receipt.get(name) != expected_value:
            raise ValueError(
                f"physical backend receipt {name} must be {expected_value!r}"
            )
    for name, expected_value in (
        ("candidate_count", len(Q_SWEEP_VALUES)),
        ("pass_count", len(Q_SWEEP_VALUES)),
        ("fail_count", 0),
        ("candidate_evidence_manifest_count", len(Q_SWEEP_VALUES)),
    ):
        value = receipt.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value != expected_value:
            raise ValueError(
                f"physical backend receipt {name} must equal {expected_value}"
            )
    if receipt.get("q_values") != list(Q_SWEEP_VALUES):
        raise ValueError("physical backend receipt Q grid is not exactly 10 through 20")
    if receipt.get("failure_type") is not None or receipt.get("failure_message") is not None:
        raise ValueError("PASS physical backend receipt contains a failure")
    checks = receipt.get("preflight_checks")
    if not isinstance(checks, list) or not checks or any(
        not isinstance(item, dict) or item.get("status") != "PASS" for item in checks
    ):
        raise ValueError("physical backend receipt does not contain an all-PASS preflight")

    request = _read_json(request_path)
    request_sha256 = _sha256(request_path)
    if receipt.get("request_sha256") != request_sha256:
        raise ValueError("physical backend receipt request SHA-256 mismatch")
    if receipt.get("model_identity") != (request.get("model_identity") or {}):
        raise ValueError("physical backend receipt model identity mismatch")
    if _resolved_receipt_file(receipt.get("request_json"), "request JSON") != request_path.resolve():
        raise ValueError("physical backend receipt points to a different request")
    if _resolved_receipt_file(
        receipt.get("backend_receipt"), "backend receipt"
    ) != receipt_path:
        raise ValueError("physical backend receipt path does not self-bind")
    if _resolved_receipt_file(
        receipt.get("physical_results"), "physical results"
    ) != result_path.resolve():
        raise ValueError("physical backend receipt points to different physical results")

    physical_results_sha256 = _required_sha256(
        receipt.get("physical_results_sha256"), "physical_results_sha256"
    )
    if not result_path.is_file() or _sha256(result_path) != physical_results_sha256:
        raise ValueError("physical results SHA-256 does not match terminal receipt")

    config_path = _resolved_receipt_file(receipt.get("config"), "config")
    process_path = _resolved_receipt_file(receipt.get("process_file"), "process file")
    config_sha256 = _required_sha256(receipt.get("config_sha256"), "config_sha256")
    process_sha256 = _required_sha256(receipt.get("process_sha256"), "process_sha256")
    if _sha256(config_path) != config_sha256:
        raise ValueError("physical backend config changed or has the wrong SHA-256")
    if _sha256(process_path) != process_sha256:
        raise ValueError("physical backend process file changed or has the wrong SHA-256")

    runtime_sources = receipt.get("runtime_sources")
    approved_sources = _approved_runtime_source_paths()
    if not isinstance(runtime_sources, dict) or set(runtime_sources) != set(approved_sources):
        raise ValueError("physical backend runtime source set is incomplete")
    source_sha256: dict[str, str] = {}
    verified_files: dict[Path, str] = {
        request_path.resolve(): request_sha256,
        receipt_path: _sha256(receipt_path),
        result_path.resolve(): physical_results_sha256,
        config_path: config_sha256,
        process_path: process_sha256,
    }
    for name, approved_path in approved_sources.items():
        record = runtime_sources.get(name)
        if not isinstance(record, dict):
            raise ValueError(f"physical backend runtime source {name} is invalid")
        actual_path = _resolved_receipt_file(record.get("path"), f"runtime source {name}")
        if actual_path != approved_path or not approved_path.is_file():
            raise ValueError(f"physical backend runtime source {name} is not approved")
        digest = _required_sha256(record.get("sha256"), f"runtime source {name}")
        if _sha256(approved_path) != digest:
            raise ValueError(f"physical backend runtime source {name} SHA-256 mismatch")
        source_sha256[name] = digest
        verified_files[approved_path] = digest

    expected_candidates = {
        item.candidate_id: (item.q_target, item.geometry_sha256)
        for item in proxy.candidates
    }
    records = receipt.get("candidates")
    if not isinstance(records, list) or len(records) != len(expected_candidates):
        raise ValueError("physical backend receipt must contain exactly eleven candidates")
    candidate_artifacts: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("physical backend candidate receipt must be an object")
        candidate_id = str(record.get("candidate_id") or "")
        q_target = record.get("q_target")
        geometry_sha256 = str(record.get("geometry_sha256") or "")
        if (
            candidate_id not in expected_candidates
            or candidate_id in seen
            or record.get("status") != "PASS"
            or isinstance(q_target, bool)
            or not isinstance(q_target, int)
            or (q_target, geometry_sha256) != expected_candidates[candidate_id]
            or record.get("error") is not None
        ):
            raise ValueError(f"invalid terminal candidate receipt for {candidate_id!r}")
        artifact_record = record.get("artifacts")
        if not isinstance(artifact_record, dict):
            raise ValueError(f"candidate receipt artifacts are missing for {candidate_id}")
        emx_command_sha256 = _required_sha256(
            record.get("emx_command_sha256"),
            f"{candidate_id} emx_command_sha256",
        )
        command_count = record.get("emx_command_argument_count")
        if (
            isinstance(command_count, bool)
            or not isinstance(command_count, int)
            or command_count <= 0
        ):
            raise ValueError(f"candidate receipt EMX command is missing for {candidate_id}")
        bound: dict[str, str] = {}
        for kind, digest_name in (
            ("gds", "gds_sha256"),
            ("s4p", "s4p_sha256"),
            ("evidence_manifest", "evidence_manifest_sha256"),
            ("geometry_json", "geometry_json_sha256"),
            ("layout_manifest", "layout_manifest_sha256"),
            ("evaluation_summary", "evaluation_summary_sha256"),
        ):
            artifact_path = _contained_receipt_file(
                root, artifact_record.get(kind), f"{candidate_id} {kind}"
            )
            digest = _required_sha256(
                artifact_record.get(digest_name), f"{candidate_id} {digest_name}"
            )
            if _sha256(artifact_path) != digest:
                raise ValueError(f"candidate receipt {kind} SHA-256 mismatch for {candidate_id}")
            bound[kind] = str(artifact_path)
            bound[digest_name] = digest
            verified_files[artifact_path] = digest
        bound["emx_command_sha256"] = emx_command_sha256
        bound["emx_command_argument_count"] = str(command_count)
        candidate_artifacts[candidate_id] = bound
        seen.add(candidate_id)
    if seen != set(expected_candidates):
        raise ValueError("physical backend receipt candidates are incomplete")

    return {
        "path": receipt_path,
        "sha256": verified_files[receipt_path],
        "request_sha256": request_sha256,
        "physical_results_path": result_path.resolve(),
        "physical_results_sha256": physical_results_sha256,
        "config_sha256": config_sha256,
        "process_sha256": process_sha256,
        "source_sha256": source_sha256,
        "model_identity": request.get("model_identity") or {},
        "candidate_artifacts": candidate_artifacts,
        "verified_files": verified_files,
    }


def _bind_candidate_to_backend_receipt(
    *, artifacts: dict[str, str], candidate_id: str, receipt: dict[str, Any]
) -> None:
    expected = receipt["candidate_artifacts"][candidate_id]
    for kind in ("gds", "s4p", "evidence_manifest"):
        if Path(artifacts[kind]).resolve() != Path(expected[kind]).resolve():
            raise ValueError(f"terminal receipt {kind} path mismatch for {candidate_id}")
        if artifacts[f"{kind}_sha256"] != expected[f"{kind}_sha256"]:
            raise ValueError(f"terminal receipt {kind} SHA-256 mismatch for {candidate_id}")
    for kind in ("geometry_json", "layout_manifest", "evaluation_summary"):
        artifacts[kind] = expected[kind]
        artifacts[f"{kind}_sha256"] = expected[f"{kind}_sha256"]
    artifacts["emx_command_sha256"] = expected["emx_command_sha256"]
    artifacts["emx_command_argument_count"] = expected[
        "emx_command_argument_count"
    ]
    provenance = {
        "config_sha256": receipt["config_sha256"],
        "process_sha256": receipt["process_sha256"],
        "backend_sha256": receipt["source_sha256"]["backend"],
        "structured_exporter_source_sha256": receipt["source_sha256"][
            "structured_exporter"
        ],
        "emx_wrapper_sha256": receipt["source_sha256"]["emx_wrapper"],
    }
    for name, expected_digest in provenance.items():
        if artifacts.get(name) != expected_digest:
            raise ValueError(f"terminal receipt provenance mismatch for {candidate_id}: {name}")


def _verify_backend_attestation_unchanged(receipt: dict[str, Any]) -> None:
    changed = [
        str(path)
        for path, expected_digest in receipt["verified_files"].items()
        if not path.is_file() or _sha256(path) != expected_digest
    ]
    if changed:
        raise ValueError(
            "physical backend evidence changed during GUI ingestion: " + ", ".join(changed)
        )


def _resolved_receipt_file(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"physical backend receipt is missing {label}")
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"physical backend receipt {label} is missing: {exc}") from exc
    if not path.is_file():
        raise ValueError(f"physical backend receipt {label} is not a file")
    return path


def _contained_receipt_file(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"physical backend receipt is missing {label}")
    raw = Path(value)
    try:
        path = (raw if raw.is_absolute() else root / raw).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"physical backend receipt {label} is missing: {exc}") from exc
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"physical backend receipt {label} escaped its output root")
    return path


def _required_sha256(value: Any, label: str) -> str:
    digest = str(value or "").lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{label} must be a complete SHA-256")
    return digest


def _approved_layout_runtime_source_paths() -> dict[str, Path]:
    package_root = Path(__file__).resolve().parents[1]
    return {
        "layout_backend": Path(__file__).with_name(
            "q_sweep_layout_backend.py"
        ).resolve(),
        "structured_exporter": (package_root / "layout" / "export.py").resolve(),
        "evaluator": (package_root / "execution" / "evaluator.py").resolve(),
        "request_mapping_preflight": Path(__file__).with_name(
            "q_sweep_physical_backend.py"
        ).resolve(),
        "layout_checks": (package_root / "layout" / "checks.py").resolve(),
    }


def _validated_layout_backend_receipt(
    *,
    root: Path,
    request_path: Path,
    result_path: Path,
    proxy: QSweepResult,
) -> dict[str, Any]:
    """Validate the complete 11/11 GDS-only terminal receipt."""

    receipt_path = (root / LAYOUT_BACKEND_RECEIPT_FILENAME).resolve()
    if not receipt_path.is_relative_to(root) or not receipt_path.is_file():
        raise ValueError("layout backend terminal receipt is missing or unsafe")
    receipt = _read_json(receipt_path)
    literals = {
        "schema": LAYOUT_BACKEND_RECEIPT_SCHEMA,
        "overall_status": "PASS",
        "decision": "STRUCTURED_GDS_Q_SWEEP_READY",
        "requested_label_source": LAYOUT_EVIDENCE_CLASS,
        "evidence_source": LAYOUT_EVIDENCE_CLASS,
        "emx_status": "NOT_RUN",
        "calibre_drc": "NOT_RUN",
        "hfss_correlation": "NOT_RUN",
    }
    for name, expected in literals.items():
        if receipt.get(name) != expected:
            raise ValueError(f"layout backend receipt {name} must be {expected!r}")
    for name, expected in (
        ("candidate_count", len(Q_SWEEP_VALUES)),
        ("pass_count", len(Q_SWEEP_VALUES)),
        ("fail_count", 0),
        ("candidate_evidence_manifest_count", len(Q_SWEEP_VALUES)),
        ("s4p_generated_count", 0),
    ):
        value = receipt.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value != expected:
            raise ValueError(f"layout backend receipt {name} must equal {expected}")
    if receipt.get("q_values") != list(Q_SWEEP_VALUES):
        raise ValueError("layout backend receipt Q grid is not exactly 10 through 20")
    if receipt.get("failure_type") is not None or receipt.get("failure_message") is not None:
        raise ValueError("PASS layout backend receipt contains a failure")
    checks = receipt.get("preflight_checks")
    if not isinstance(checks, list) or not checks or any(
        not isinstance(item, dict) or item.get("status") != "PASS" for item in checks
    ):
        raise ValueError("layout backend receipt lacks an all-PASS preflight")

    request = _read_json(request_path)
    request_sha256 = _sha256(request_path)
    if receipt.get("request_sha256") != request_sha256:
        raise ValueError("layout backend request SHA-256 mismatch")
    if receipt.get("model_identity") != (request.get("model_identity") or {}):
        raise ValueError("layout backend model identity mismatch")
    if _resolved_receipt_file(receipt.get("request_json"), "layout request") != request_path.resolve():
        raise ValueError("layout receipt points to a different request")
    if _resolved_receipt_file(
        receipt.get("layout_backend_receipt"), "layout backend receipt"
    ) != receipt_path:
        raise ValueError("layout receipt path does not self-bind")
    if _resolved_receipt_file(
        receipt.get("layout_results"), "layout results"
    ) != result_path.resolve():
        raise ValueError("layout receipt points to different results")
    results_sha256 = _required_sha256(
        receipt.get("layout_results_sha256"), "layout_results_sha256"
    )
    if not result_path.is_file() or _sha256(result_path) != results_sha256:
        raise ValueError("layout results SHA-256 mismatch")

    config_path = _resolved_receipt_file(receipt.get("config"), "layout config")
    process_path = _resolved_receipt_file(
        receipt.get("process_file"), "layout process file"
    )
    config_sha256 = _required_sha256(receipt.get("config_sha256"), "config_sha256")
    process_sha256 = _required_sha256(receipt.get("process_sha256"), "process_sha256")
    if _sha256(config_path) != config_sha256 or _sha256(process_path) != process_sha256:
        raise ValueError("layout config or process file changed")

    approved_sources = _approved_layout_runtime_source_paths()
    runtime_sources = receipt.get("runtime_sources")
    if not isinstance(runtime_sources, dict) or set(runtime_sources) != set(approved_sources):
        raise ValueError("layout backend runtime source set is incomplete")
    source_sha256: dict[str, str] = {}
    verified_files: dict[Path, str] = {
        request_path.resolve(): request_sha256,
        receipt_path: _sha256(receipt_path),
        result_path.resolve(): results_sha256,
        config_path: config_sha256,
        process_path: process_sha256,
    }
    for name, approved_path in approved_sources.items():
        record = runtime_sources.get(name)
        if not isinstance(record, dict):
            raise ValueError(f"layout runtime source {name} is invalid")
        actual_path = _resolved_receipt_file(record.get("path"), f"layout source {name}")
        digest = _required_sha256(record.get("sha256"), f"layout source {name}")
        if actual_path != approved_path or _sha256(approved_path) != digest:
            raise ValueError(f"layout runtime source {name} is not current")
        source_sha256[name] = digest
        verified_files[approved_path] = digest

    expected_candidates = {
        item.candidate_id: (item.q_target, item.geometry_sha256)
        for item in proxy.candidates
    }
    records = receipt.get("candidates")
    if not isinstance(records, list) or len(records) != len(expected_candidates):
        raise ValueError("layout receipt must contain exactly eleven candidates")
    candidate_artifacts: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("layout candidate receipt must be an object")
        candidate_id = str(record.get("candidate_id") or "")
        q_target = record.get("q_target")
        geometry_sha256 = str(record.get("geometry_sha256") or "")
        if (
            candidate_id not in expected_candidates
            or candidate_id in seen
            or record.get("status") != "PASS"
            or record.get("emx_status") != "NOT_RUN"
            or record.get("error") is not None
            or isinstance(q_target, bool)
            or not isinstance(q_target, int)
            or (q_target, geometry_sha256) != expected_candidates[candidate_id]
        ):
            raise ValueError(f"invalid layout receipt for {candidate_id!r}")
        artifacts = record.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ValueError(f"layout artifacts are missing for {candidate_id}")
        bound: dict[str, str] = {}
        for kind, digest_name in (
            ("gds", "gds_sha256"),
            ("evidence_manifest", "evidence_manifest_sha256"),
            ("geometry_json", "geometry_json_sha256"),
            ("layout_manifest", "layout_manifest_sha256"),
            ("evaluation_summary", "evaluation_summary_sha256"),
        ):
            artifact_path = _contained_receipt_file(
                root, artifacts.get(kind), f"{candidate_id} layout {kind}"
            )
            digest = _required_sha256(
                artifacts.get(digest_name), f"{candidate_id} {digest_name}"
            )
            if _sha256(artifact_path) != digest:
                raise ValueError(f"layout {kind} SHA-256 mismatch for {candidate_id}")
            bound[kind] = str(artifact_path)
            bound[digest_name] = digest
            verified_files[artifact_path] = digest
        candidate_artifacts[candidate_id] = bound
        seen.add(candidate_id)
    if seen != set(expected_candidates):
        raise ValueError("layout receipt candidates are incomplete")
    return {
        "path": receipt_path,
        "sha256": verified_files[receipt_path],
        "request_sha256": request_sha256,
        "layout_results_path": result_path.resolve(),
        "layout_results_sha256": results_sha256,
        "config_sha256": config_sha256,
        "process_sha256": process_sha256,
        "source_sha256": source_sha256,
        "model_identity": request.get("model_identity") or {},
        "candidate_artifacts": candidate_artifacts,
        "verified_files": verified_files,
    }


def _run_layout_backend(
    *,
    proxy: QSweepResult,
    root: Path,
    command: str,
) -> QSweepResult:
    """Run and ingest a complete structured-GDS-only batch."""

    layout_root = root / "layout_backend"
    layout_root.mkdir(exist_ok=False)
    request = root / "layout_backend_request.json"
    backend_argv = _validated_layout_backend_argv(command)
    process = subprocess.run(
        backend_argv + ["--request-json", str(request), "--out-dir", str(layout_root)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    (root / "layout_backend.stdout.log").write_text(process.stdout, encoding="utf-8")
    (root / "layout_backend.stderr.log").write_text(process.stderr, encoding="utf-8")
    if process.returncode != 0:
        raise RuntimeError(
            f"layout backend failed with exit code {process.returncode}: "
            f"{process.stderr[-1200:]}"
        )
    result_path = layout_root / LAYOUT_RESULTS_FILENAME
    receipt = _validated_layout_backend_receipt(
        root=layout_root,
        request_path=request,
        result_path=result_path,
        proxy=proxy,
    )
    payload = _read_json(result_path)
    if (
        payload.get("schema") != LAYOUT_RESULTS_SCHEMA
        or payload.get("evidence_source") != LAYOUT_EVIDENCE_CLASS
        or payload.get("emx_status") != "NOT_RUN"
        or payload.get("calibre_drc") != "NOT_RUN"
        or payload.get("hfss_correlation") != "NOT_RUN"
    ):
        raise ValueError("layout results have an invalid evidence boundary")
    for name in ("request_sha256", "config_sha256", "process_sha256"):
        if str(payload.get(name) or "").lower() != str(receipt[name]).lower():
            raise ValueError(f"layout results {name} does not match terminal receipt")
    if payload.get("model_identity") != receipt["model_identity"]:
        raise ValueError("layout results model identity mismatch")
    rows = payload.get("results")
    if not isinstance(rows, list) or len(rows) != len(Q_SWEEP_VALUES):
        raise ValueError("layout backend must return exactly eleven results")
    expected = {item.candidate_id: item for item in proxy.candidates}
    returned = [str(row.get("candidate_id")) for row in rows if isinstance(row, dict)]
    if len(returned) != len(rows) or len(set(returned)) != len(rows) or set(returned) != set(expected):
        raise ValueError("layout backend did not return exact 11/11 candidates")

    candidates: list[CandidateMetrics] = []
    for row in rows:
        candidate_id = str(row["candidate_id"])
        proxy_item = expected[candidate_id]
        if (
            row.get("q_target") != proxy_item.q_target
            or row.get("geometry_sha256") != proxy_item.geometry_sha256
        ):
            raise ValueError(f"layout candidate identity mismatch for {candidate_id}")
        raw_artifacts = row.get("artifacts")
        if not isinstance(raw_artifacts, dict):
            raise ValueError(f"layout artifacts missing for {candidate_id}")
        gds_value = str(raw_artifacts.get("gds") or "")
        gds_path = (
            (layout_root / gds_value).resolve()
            if not Path(gds_value).is_absolute()
            else Path(gds_value).resolve()
        )
        if not gds_path.is_relative_to(layout_root) or not gds_path.is_file():
            raise ValueError(f"unsafe layout GDS for {candidate_id}")
        _validate_gds_binary(gds_path, candidate_id)
        evidence_value = str(raw_artifacts.get("evidence_manifest") or "")
        evidence_path = (
            (layout_root / evidence_value).resolve()
            if not Path(evidence_value).is_absolute()
            else Path(evidence_value).resolve()
        )
        if not evidence_path.is_relative_to(layout_root) or not evidence_path.is_file():
            raise ValueError(f"unsafe layout evidence for {candidate_id}")
        evidence = validate_layout_evidence_manifest(
            evidence_path,
            root=layout_root,
            expected_candidate_id=candidate_id,
            expected_q_target=proxy_item.q_target,
            expected_geometry_sha256=proxy_item.geometry_sha256,
        )
        receipt_artifacts = receipt["candidate_artifacts"][candidate_id]
        if (
            Path(receipt_artifacts["gds"]).resolve() != gds_path
            or receipt_artifacts["gds_sha256"] != _sha256(gds_path)
            or Path(receipt_artifacts["evidence_manifest"]).resolve() != evidence_path
            or receipt_artifacts["evidence_manifest_sha256"]
            != evidence["evidence_manifest_sha256"]
        ):
            raise ValueError(f"layout receipt artifact mismatch for {candidate_id}")
        evidence_provenance = {
            "config_sha256": evidence["config_sha256"],
            "process_sha256": evidence["process_sha256"],
            "layout_backend_sha256": evidence["layout_backend_sha256"],
            "structured_exporter_source_sha256": evidence["structured_exporter"][
                "source_sha256"
            ],
            "evaluator_sha256": evidence["evaluator_sha256"],
        }
        expected_provenance = {
            "config_sha256": receipt["config_sha256"],
            "process_sha256": receipt["process_sha256"],
            "layout_backend_sha256": receipt["source_sha256"]["layout_backend"],
            "structured_exporter_source_sha256": receipt["source_sha256"][
                "structured_exporter"
            ],
            "evaluator_sha256": receipt["source_sha256"]["evaluator"],
        }
        if evidence_provenance != expected_provenance:
            raise ValueError(f"layout evidence provenance mismatch for {candidate_id}")
        artifacts = {
            "gds": str(gds_path),
            "gds_sha256": _sha256(gds_path),
            "evidence_manifest": str(evidence_path),
            "evidence_manifest_sha256": evidence["evidence_manifest_sha256"],
            "layout_evidence_validation_schema": evidence["schema"],
            "layout_evidence_class": evidence["evidence_class"],
            **expected_provenance,
            "geometry_json": receipt_artifacts["geometry_json"],
            "geometry_json_sha256": receipt_artifacts["geometry_json_sha256"],
            "layout_manifest": receipt_artifacts["layout_manifest"],
            "layout_manifest_sha256": receipt_artifacts["layout_manifest_sha256"],
            "evaluation_summary": receipt_artifacts["evaluation_summary"],
            "evaluation_summary_sha256": receipt_artifacts[
                "evaluation_summary_sha256"
            ],
            "layout_backend_receipt": str(receipt["path"]),
            "layout_backend_receipt_sha256": str(receipt["sha256"]),
            "layout_results": str(receipt["layout_results_path"]),
            "layout_results_sha256": str(receipt["layout_results_sha256"]),
            "emx_status": "NOT_RUN",
            "calibre_drc": "NOT_RUN",
            "hfss_correlation": "NOT_RUN",
        }
        preview_value = raw_artifacts.get("preview")
        if preview_value:
            preview_path = (layout_root / str(preview_value)).resolve()
            if preview_path.is_relative_to(layout_root) and preview_path.is_file():
                artifacts["preview"] = str(preview_path)
                artifacts["preview_sha256"] = _sha256(preview_path)
        candidates.append(
            _candidate_metrics(
                candidate_id=candidate_id,
                q_target=proxy_item.q_target,
                target_features=np.asarray(
                    [proxy_item.target_features[name] for name in FEATURE_NAMES],
                    dtype=float,
                ),
                observed_features=np.asarray(
                    [proxy_item.observed_features[name] for name in FEATURE_NAMES],
                    dtype=float,
                ),
                fresh_emx_features_15ghz={},
                geometry=proxy_item.geometry_um,
                evidence_source=LAYOUT_EVIDENCE_CLASS,
                artifacts=artifacts,
            )
        )
    candidates.sort(key=lambda item: item.q_target)
    _verify_backend_attestation_unchanged(receipt)
    selected = min(
        candidates,
        key=lambda item: (item.declared_range_normalized_rmse, item.q_target),
    )
    return QSweepResult(
        design_id=proxy.design_id,
        mode="layout",
        target_three_features=proxy.target_three_features,
        q_values=proxy.q_values,
        selected_candidate_id=selected.candidate_id,
        selected_q=selected.q_target,
        selection_score=selected.declared_range_normalized_rmse,
        evidence_source=LAYOUT_EVIDENCE_CLASS,
        metric_source="FROZEN_FORWARD_PROXY_DIAGNOSTIC",
        selection_source="FROZEN_FORWARD_PROXY_DIAGNOSTIC",
        artifact_evidence_class=LAYOUT_EVIDENCE_CLASS,
        candidates=tuple(candidates),
        selected_artifacts=selected.artifacts,
        scientific_boundary=(
            "All eleven candidates have request-bound parseable structured binary GDS "
            "exports. Error values and selected Q remain frozen-forward proxy diagnostics; "
            "fresh EMX, S4P, Calibre DRC, HFSS, and measurement are NOT_RUN."
        ),
    )


def _run_physical_backend(
    *,
    proxy: QSweepResult,
    root: Path,
    command: str,
) -> QSweepResult:
    physical_root = root / "physical_backend"
    physical_root.mkdir(exist_ok=False)
    request = root / "physical_backend_request.json"
    backend_argv = _validated_physical_backend_argv(command)
    process = subprocess.run(
        backend_argv
        + ["--request-json", str(request), "--out-dir", str(physical_root)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    (root / "physical_backend.stdout.log").write_text(
        process.stdout, encoding="utf-8"
    )
    (root / "physical_backend.stderr.log").write_text(
        process.stderr, encoding="utf-8"
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"physical backend failed with exit code {process.returncode}: "
            f"{process.stderr[-1200:]}"
        )
    result_path = physical_root / "physical_results.json"
    receipt = _validated_backend_receipt(
        root=physical_root,
        request_path=request,
        result_path=result_path,
        proxy=proxy,
    )
    payload = _read_json(result_path)
    if payload.get("schema") != "rfic_q_sweep_physical_results.v1":
        raise ValueError("physical backend returned an unsupported schema")
    if payload.get("label_source") != "FRESH_REAL_EMX":
        raise ValueError("physical backend label_source must be FRESH_REAL_EMX")
    rows = payload.get("results")
    if not isinstance(rows, list):
        raise ValueError("physical backend results must be a list")
    if len(rows) != len(Q_SWEEP_VALUES) or any(
        not isinstance(row, dict) for row in rows
    ):
        raise ValueError("physical backend must return exactly eleven result objects")
    expected = {item.candidate_id: item for item in proxy.candidates}
    returned_ids = [str(row.get("candidate_id")) for row in rows]
    if len(set(returned_ids)) != len(returned_ids) or set(returned_ids) != set(expected):
        raise ValueError("physical backend must return exactly all eleven candidates")
    for name in ("request_sha256", "config_sha256", "process_sha256"):
        if str(payload.get(name) or "").lower() != str(receipt[name]).lower():
            raise ValueError(f"physical results {name} does not match terminal receipt")
    if payload.get("model_identity") != receipt["model_identity"]:
        raise ValueError("physical results model identity does not match terminal receipt")
    candidates: list[CandidateMetrics] = []
    for row in rows:
        candidate_id = str(row["candidate_id"])
        proxy_item = expected[candidate_id]
        if int(row.get("q_target")) != proxy_item.q_target:
            raise ValueError(f"Q mismatch for {candidate_id}")
        if str(row.get("geometry_sha256")) != proxy_item.geometry_sha256:
            raise ValueError(f"geometry hash mismatch for {candidate_id}")
        feature_record = row.get("features_15ghz") or {}
        if not isinstance(feature_record, dict):
            raise ValueError(f"features_15ghz must be an object for {candidate_id}")
        artifacts = _validated_backend_artifacts(
            physical_root,
            row.get("artifacts") or {},
            candidate_id,
        )
        fresh_features = _extract_s4p_features_15ghz(
            Path(artifacts["s4p"]), candidate_id
        )
        _validate_backend_feature_match(
            feature_record,
            fresh_features,
            candidate_id,
        )
        artifacts = _bind_physical_evidence(
            root=physical_root,
            artifacts=artifacts,
            candidate_id=candidate_id,
            q_target=proxy_item.q_target,
            geometry_sha256=proxy_item.geometry_sha256,
        )
        _bind_candidate_to_backend_receipt(
            artifacts=artifacts,
            candidate_id=candidate_id,
            receipt=receipt,
        )
        artifacts.update(
            {
                "backend_receipt": str(receipt["path"]),
                "backend_receipt_sha256": str(receipt["sha256"]),
                "physical_results": str(receipt["physical_results_path"]),
                "physical_results_sha256": str(receipt["physical_results_sha256"]),
            }
        )
        physical = np.asarray(
            (
                fresh_features["Lp_nH"],
                fresh_features["Ls_nH"],
                fresh_features["Qmin"],
                fresh_features["K_abs"],
            ),
            dtype=float,
        )
        if not np.isfinite(physical).all():
            raise ValueError(f"invalid fresh-EMX features for {candidate_id}")
        _operating_point_backend_gate(row, physical, artifacts)
        target_vector = np.asarray(
            [proxy_item.target_features[name] for name in FEATURE_NAMES], dtype=float
        )
        candidates.append(
            _candidate_metrics(
                candidate_id=candidate_id,
                q_target=proxy_item.q_target,
                target_features=target_vector,
                observed_features=physical,
                fresh_emx_features_15ghz=fresh_features,
                geometry=proxy_item.geometry_um,
                evidence_source="FRESH_REAL_EMX",
                artifacts=artifacts,
            )
        )
    candidates.sort(key=lambda item: item.q_target)
    _verify_backend_attestation_unchanged(receipt)
    selected = min(
        candidates,
        key=lambda item: (item.declared_range_normalized_rmse, item.q_target),
    )
    return QSweepResult(
        design_id=proxy.design_id,
        mode="physical",
        target_three_features=proxy.target_three_features,
        q_values=proxy.q_values,
        selected_candidate_id=selected.candidate_id,
        selected_q=selected.q_target,
        selection_score=selected.declared_range_normalized_rmse,
        evidence_source="FRESH_REAL_EMX",
        metric_source="FRESH_REAL_EMX",
        selection_source="FRESH_REAL_EMX",
        artifact_evidence_class="CANDIDATE_BOUND_GDS_S4P_CONTRACT",
        candidates=tuple(candidates),
        selected_artifacts=selected.artifacts,
        scientific_boundary=(
            "The selected candidate minimizes the fixed declared-range-normalized "
            "four-feature score across eleven fresh real-EMX evaluations at 15 GHz. "
            "This full-Q diagnostic is not the final preselected-q_proxy experiment. "
            "15GHz operating-point descriptors use no half-SRF gate. "
            "Foundry DRC and independent HFSS correlation remain separate gates."
        ),
    )


def _operating_point_backend_gate(row, physical, artifacts):
    """Require saved numerical QA and candidate-bound physical gate evidence.

    Missing SRF/old strict fields are irrelevant. Missing actual QA/provenance
    is not equivalent to physical invalidity and must be supplied by backend.
    """
    feature = row.get('operating_point_evidence')
    if not isinstance(feature, dict):
        raise ValueError('MISSING_OPERATING_POINT_QA_EVIDENCE: backend must return the original audited15GHz feature receipt')
    if feature.get('candidate_id') != row['candidate_id']:
        raise ValueError('operating-point candidate identity mismatch')
    verdict = operating_point_classify(feature)
    wanted = [verdict['physical15'][k] for k in ('lp_nh','ls_nh','qmin','k_abs')]
    if not verdict['operating_point_valid'] or not np.allclose(physical, wanted, rtol=0, atol=1e-12):
        raise ValueError('OPERATING_POINT_NUMERICAL_REJECTION_OR_FEATURE_MISMATCH')
    proof = row.get('physical_gate_evidence', {})
    if (proof.get('geometry_sha256') != row['geometry_sha256'] or
            proof.get('gds_sha256') != artifacts['gds_sha256'] or proof.get('s4p_sha256') != artifacts['s4p_sha256'] or
            proof.get('current_structure_compatible') is not True or proof.get('drc_zero_blocking') is not True or
            proof.get('original_GDS_DRC_EMX_binding') is not True):
        raise ValueError('MISSING_OR_INCOMPATIBLE_GDS_DRC_EMX_BINDING')
    return verdict


def _candidate_metrics(
    *,
    candidate_id: str,
    q_target: int,
    target_features: np.ndarray,
    observed_features: np.ndarray,
    fresh_emx_features_15ghz: dict[str, float],
    geometry: dict[str, float],
    evidence_source: str,
    artifacts: dict[str, str],
) -> CandidateMetrics:
    target = np.asarray(target_features, dtype=float)
    observed = np.asarray(observed_features, dtype=float)
    absolute = np.abs(observed - target)
    percent = 100.0 * absolute / np.maximum(np.abs(target), 1.0e-12)
    score = float(np.sqrt(np.mean((absolute / DECLARED_FEATURE_SPANS) ** 2)))
    percent_rmse = float(np.sqrt(np.mean(percent**2)))
    observation_source = (
        "FRESH_REAL_EMX"
        if evidence_source == "FRESH_REAL_EMX"
        else "FROZEN_FORWARD_PROXY_DIAGNOSTIC"
    )
    artifact_evidence_class = {
        "FRESH_REAL_EMX": "CANDIDATE_BOUND_GDS_S4P_CONTRACT",
        LAYOUT_EVIDENCE_CLASS: LAYOUT_EVIDENCE_CLASS,
    }.get(evidence_source, "NONE")
    return CandidateMetrics(
        candidate_id=candidate_id,
        q_target=int(q_target),
        target_features={name: float(value) for name, value in zip(FEATURE_NAMES, target)},
        observed_features={name: float(value) for name, value in zip(FEATURE_NAMES, observed)},
        fresh_emx_features_15ghz={
            key: float(value) for key, value in fresh_emx_features_15ghz.items()
        },
        geometry_um={key: float(value) for key, value in geometry.items()},
        geometry_sha256=_geometry_sha256(geometry),
        per_feature_abs_error={name: float(value) for name, value in zip(FEATURE_NAMES, absolute)},
        per_feature_percent_error={name: float(value) for name, value in zip(FEATURE_NAMES, percent)},
        declared_range_normalized_rmse=score,
        relative_percent_rmse=percent_rmse,
        evidence_source=evidence_source,
        observation_source=observation_source,
        artifact_evidence_class=artifact_evidence_class,
        artifacts=dict(artifacts),
    )


def _validate_q_grid(values: Iterable[int]) -> tuple[int, ...]:
    grid = tuple(int(value) for value in values)
    if grid != Q_SWEEP_VALUES:
        raise ValueError("the production Q grid is fixed to integers 10 through 20")
    return grid


def _octagon(width: float, height: float, center_x: float) -> np.ndarray:
    half_w = 0.5 * width
    half_h = 0.5 * height
    chamfer = (math.sqrt(2.0) - 1.0) * min(half_w, half_h)
    points = np.asarray(
        (
            (-half_w + chamfer, half_h),
            (half_w - chamfer, half_h),
            (half_w, half_h - chamfer),
            (half_w, -half_h + chamfer),
            (half_w - chamfer, -half_h),
            (-half_w + chamfer, -half_h),
            (-half_w, -half_h + chamfer),
            (-half_w, half_h - chamfer),
            (-half_w + chamfer, half_h),
        ),
        dtype=float,
    )
    points[:, 0] += center_x
    return points


def _write_backend_request(
    path: Path,
    model: FrozenTandemMLP,
    result: QSweepResult,
    *,
    mode: str = "physical",
) -> None:
    layout_only = mode == "layout"
    payload = {
        "schema": (
            LAYOUT_REQUEST_SCHEMA
            if layout_only
            else "rfic_q_sweep_physical_request.v1"
        ),
        "model_id": model.model_id,
        "model_seed": model.model_seed,
        "model_identity": _model_identity(model),
        "target_frequency_ghz": model.target_frequency_ghz,
        "q_values": list(result.q_values),
        "required_label_source": (
            LAYOUT_EVIDENCE_CLASS if layout_only else "FRESH_REAL_EMX"
        ),
        "required_outputs": (
            ["GDS", "geometry_json", "layout_manifest", "evaluation_summary"]
            if layout_only
            else ["GDS", "S4P", "Lp", "Ls", "Qp", "Qs", "K_abs"]
        ),
        "emx_requested": not layout_only,
        "label_policy": LABEL_POLICY,
        "srf_required": False,
        "q_proxy": result.selected_q,
        "selection_scope": "FULL11Q_DIAGNOSTIC_NOT_FINAL_PRESELECTED_Q_PROXY_VALIDATION",
        "required_evidence": [] if layout_only else ["operating_point_evidence", "physical_gate_evidence"],
        "candidates": [
            {
                "candidate_id": item.candidate_id,
                "q_target": item.q_target,
                "target_features": item.target_features,
                "geometry_um": item.geometry_um,
                "geometry_sha256": item.geometry_sha256,
            }
            for item in result.candidates
        ],
    }
    _write_json(path, payload)


def _model_identity(model: Any) -> dict[str, Any]:
    """Return hash and architecture metadata when the frozen contract is available."""

    contract = getattr(model, "contract", None)
    if not isinstance(contract, dict):
        return {
            "model_id": str(getattr(model, "model_id", "")),
            "model_seed": int(getattr(model, "model_seed", 0)),
            "contract_payload_sha256": "",
            "contract_payload_serialization": "",
            "artifact_expected_sha256": {},
            "architecture": {},
        }
    canonical = json.dumps(
        contract,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    artifacts = contract.get("artifacts") or {}
    expected_hashes = {
        name: str((artifacts.get(name) or {}).get("sha256") or "")
        for name in ("summary", "weights", "training_table")
    }
    return {
        "model_id": str(contract.get("model_id") or getattr(model, "model_id", "")),
        "model_seed": int(contract.get("model_seed", getattr(model, "model_seed", 0))),
        "contract_payload_sha256": hashlib.sha256(canonical).hexdigest(),
        "contract_payload_serialization": "canonical_json_sort_keys_compact_utf8",
        "artifact_expected_sha256": expected_hashes,
        "architecture": dict(contract.get("architecture") or {}),
    }


def _extract_s4p_features_15ghz(
    path: Path, candidate_id: str
) -> dict[str, float]:
    """Recompute the canonical four-port 15 GHz features from Touchstone."""

    from ..analysis.extraction import multiport_single_ended_to_differential_z
    from ..sim.touchstone import load_touchstone

    if path.suffix.lower() != ".s4p":
        raise ValueError(f"fresh-EMX artifact is not .s4p for {candidate_id}")
    try:
        touchstone = load_touchstone(path)
    except Exception as exc:
        raise ValueError(f"invalid S4P artifact for {candidate_id}: {exc}") from exc
    frequencies = np.asarray(touchstone.freqs_hz, dtype=float)
    matrix = np.asarray(touchstone.s_matrix, dtype=np.complex128)
    expected_shape = (len(EXPECTED_S4P_FREQUENCIES_HZ), 4, 4)
    if touchstone.num_ports != 4 or matrix.shape != expected_shape:
        raise ValueError(
            f"S4P shape mismatch for {candidate_id}: "
            f"expected={expected_shape}, actual={matrix.shape}"
        )
    if frequencies.shape != EXPECTED_S4P_FREQUENCIES_HZ.shape or not np.allclose(
        frequencies,
        EXPECTED_S4P_FREQUENCIES_HZ,
        rtol=0.0,
        atol=S4P_FREQUENCY_TOLERANCE_HZ,
    ):
        raise ValueError(
            f"S4P frequency grid mismatch for {candidate_id}; "
            "expected 5-60 GHz / 0.5 GHz / 111 points"
        )
    if not np.isfinite(frequencies).all() or not np.isfinite(matrix).all():
        raise ValueError(f"non-finite S4P data for {candidate_id}")
    index = int(np.argmin(np.abs(frequencies - 15.0e9)))
    frequency_hz = float(frequencies[index])
    if abs(frequency_hz - 15.0e9) > S4P_FREQUENCY_TOLERANCE_HZ:
        raise ValueError(f"15 GHz is absent from the S4P grid for {candidate_id}")
    try:
        z_single = touchstone.to_z_parameters()
        z_diff = multiport_single_ended_to_differential_z(
            z_single, ((0, 1), (2, 3))
        )
    except Exception as exc:
        raise ValueError(
            f"S4P-to-Z conversion failed for {candidate_id}: {exc}"
        ) from exc
    if not np.isfinite(z_diff).all():
        raise ValueError(f"non-finite differential Z for {candidate_id}")

    center = z_diff[index]
    omega = 2.0 * math.pi * frequency_hz
    lp_nh = float(np.imag(center[0, 0]) / omega * 1.0e9)
    ls_nh = float(np.imag(center[1, 1]) / omega * 1.0e9)
    mutual_nh = float(np.imag(center[1, 0]) / omega * 1.0e9)
    with np.errstate(divide="ignore", invalid="ignore"):
        qp = float(np.imag(center[0, 0]) / np.real(center[0, 0]))
        qs = float(np.imag(center[1, 1]) / np.real(center[1, 1]))
    k_signed = float(
        mutual_nh / math.sqrt(max(abs(lp_nh * ls_nh), 1.0e-30))
    )
    reference = np.asarray(touchstone.reference_impedance_ohm, dtype=float)
    values = {
        "frequency_hz": frequency_hz,
        "Lp_nH": lp_nh,
        "Ls_nH": ls_nh,
        "Qp": qp,
        "Qs": qs,
        "Qmin": min(qp, qs),
        "K_abs": abs(k_signed),
        "K_signed": k_signed,
        "reference_impedance_min_ohm": float(np.min(reference)),
        "reference_impedance_max_ohm": float(np.max(reference)),
    }
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError(f"non-finite 15 GHz physical feature for {candidate_id}")
    if any(values[name] <= 0.0 for name in ("Lp_nH", "Ls_nH", "Qp", "Qs")):
        raise ValueError(f"non-positive 15 GHz L/Q feature for {candidate_id}")
    if not 0.0 <= values["K_abs"] <= 1.0 + 1.0e-6:
        raise ValueError(f"15 GHz |K| is outside [0, 1+1e-6] for {candidate_id}")
    return values


def _validate_backend_feature_match(
    backend: dict[str, Any],
    extracted: dict[str, float],
    candidate_id: str,
) -> None:
    """Require backend feature claims to match the S4P-derived values."""

    for name in ("Lp_nH", "Ls_nH", "Qp", "Qs", "K_abs"):
        try:
            claimed = float(backend[name])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"missing backend feature {name} for {candidate_id}"
            ) from exc
        observed = float(extracted[name])
        if not math.isfinite(claimed) or not math.isclose(
            claimed,
            observed,
            rel_tol=BACKEND_FEATURE_RELATIVE_TOLERANCE,
            abs_tol=BACKEND_FEATURE_ABSOLUTE_TOLERANCE,
        ):
            raise ValueError(
                f"backend feature mismatch for {candidate_id}: {name} "
                f"backend={claimed!r}, s4p={observed!r}"
            )


def _validated_backend_artifacts(
    root: Path,
    artifacts: dict[str, Any],
    candidate_id: str,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for kind in ("gds", "s4p"):
        value = str(artifacts.get(kind) or "")
        path = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"missing or unsafe {kind} artifact for {candidate_id}")
        if kind == "gds":
            _validate_gds_binary(path, candidate_id)
        result[kind] = str(path)
        result[f"{kind}_sha256"] = _sha256(path)
    evidence_value = str(artifacts.get("evidence_manifest") or "")
    evidence_path = (
        (root / evidence_value).resolve()
        if not Path(evidence_value).is_absolute()
        else Path(evidence_value).resolve()
    )
    if not evidence_path.is_relative_to(root) or not evidence_path.is_file():
        raise ValueError(
            f"missing or unsafe evidence_manifest artifact for {candidate_id}"
        )
    result["evidence_manifest"] = str(evidence_path)
    preview = artifacts.get("preview")
    if preview:
        path = (root / str(preview)).resolve() if not Path(str(preview)).is_absolute() else Path(str(preview)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"unsafe preview artifact for {candidate_id}")
        result["preview"] = str(path)
        result["preview_sha256"] = _sha256(path)
    return result


def _bind_physical_evidence(
    *,
    root: Path,
    artifacts: dict[str, str],
    candidate_id: str,
    q_target: int,
    geometry_sha256: str,
) -> dict[str, str]:
    """Cross-bind the candidate artifacts to its physical evidence manifest."""

    evidence = validate_physical_evidence_manifest(
        artifacts["evidence_manifest"],
        root=root,
        expected_candidate_id=candidate_id,
        expected_q_target=q_target,
        expected_geometry_sha256=geometry_sha256,
    )
    evidence_artifacts = evidence["artifacts"]
    for kind in ("gds", "s4p"):
        evidence_artifact = evidence_artifacts[kind]
        if Path(str(evidence_artifact["path"])).resolve() != Path(
            artifacts[kind]
        ).resolve():
            raise ValueError(
                f"physical evidence {kind} path mismatch for {candidate_id}"
            )
        if str(evidence_artifact["sha256"]).lower() != artifacts[
            f"{kind}_sha256"
        ].lower():
            raise ValueError(
                f"physical evidence {kind} SHA-256 mismatch for {candidate_id}"
            )
    if Path(str(evidence["evidence_manifest"])).resolve() != Path(
        artifacts["evidence_manifest"]
    ).resolve():
        raise ValueError(
            f"physical evidence manifest path mismatch for {candidate_id}"
        )
    evidence_manifest_sha256 = str(
        evidence["evidence_manifest_sha256"]
    ).lower()
    if _sha256(Path(artifacts["evidence_manifest"])) != evidence_manifest_sha256:
        raise ValueError(
            f"physical evidence manifest changed during validation for {candidate_id}"
        )

    exporter = evidence["structured_exporter"]
    result = dict(artifacts)
    result.update(
        {
            "evidence_manifest_sha256": evidence_manifest_sha256,
            "physical_evidence_validation_schema": str(evidence["schema"]),
            "physical_evidence_class": str(evidence["evidence_class"]),
            "structured_exporter_id": str(exporter["id"]),
            "structured_exporter_source_sha256": str(
                exporter["source_sha256"]
            ),
            "process_sha256": str(evidence["process_sha256"]),
            "config_sha256": str(evidence["config_sha256"]),
            "backend_sha256": str(evidence["backend_sha256"]),
            "emx_wrapper_sha256": str(evidence["emx_wrapper_sha256"]),
        }
    )
    return result


def _validate_gds_binary(path: Path, candidate_id: str) -> None:
    """Verify a parseable, non-empty GDS container; this is not a DRC gate."""

    import gdstk

    try:
        library = gdstk.read_gds(str(path))
    except Exception as exc:
        raise ValueError(f"invalid GDS artifact for {candidate_id}: {exc}") from exc
    drawable_count = sum(
        len(cell.polygons) + len(cell.paths) + len(cell.references)
        for cell in library.cells
    )
    if not library.cells or drawable_count == 0:
        raise ValueError(f"empty GDS artifact for {candidate_id}")


def _copy_selected_physical_artifacts(
    selected: CandidateMetrics,
    deliverables: Path,
    design_id: str,
    selected_artifacts: dict[str, str],
) -> dict[str, str]:
    copied = dict(selected_artifacts)
    for kind, suffix in (("gds", ".gds"), ("s4p", ".s4p")):
        source = selected.artifacts.get(kind)
        if not source:
            raise RuntimeError(f"selected physical candidate is missing {kind}")
        destination = deliverables / f"{design_id}_q{selected.q_target:02d}{suffix}"
        shutil.copy2(source, destination)
        copied[kind] = str(destination)
        copied[f"{kind}_sha256"] = _sha256(destination)
    return copied


def _write_candidates_csv(path: Path, candidates: tuple[CandidateMetrics, ...]) -> None:
    fieldnames = [
        "candidate_id",
        "q_target",
        "evidence_source",
        "declared_range_normalized_rmse",
        "relative_percent_rmse",
    ]
    for prefix in ("target", "observed", "abs_error", "percent_error"):
        fieldnames.extend(f"{prefix}__{name}" for name in FEATURE_NAMES)
    fieldnames.extend(
        f"fresh_emx__{name}" for name in FRESH_EMX_FEATURE_NAMES
    )
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in candidates:
            row: dict[str, Any] = {
                "candidate_id": item.candidate_id,
                "q_target": item.q_target,
                "evidence_source": item.evidence_source,
                "declared_range_normalized_rmse": item.declared_range_normalized_rmse,
                "relative_percent_rmse": item.relative_percent_rmse,
            }
            for name in FEATURE_NAMES:
                row[f"target__{name}"] = item.target_features[name]
                row[f"observed__{name}"] = item.observed_features[name]
                row[f"abs_error__{name}"] = item.per_feature_abs_error[name]
                row[f"percent_error__{name}"] = item.per_feature_percent_error[name]
            for name in FRESH_EMX_FEATURE_NAMES:
                row[f"fresh_emx__{name}"] = item.fresh_emx_features_15ghz.get(
                    name, ""
                )
            writer.writerow(row)


def _prepare_no_clobber_directory(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing run directory: {path}")
    path.mkdir(parents=True, exist_ok=False)


def _geometry_sha256(geometry: dict[str, float]) -> str:
    payload = json.dumps(
        {key: float(geometry[key]) for key in sorted(geometry)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
