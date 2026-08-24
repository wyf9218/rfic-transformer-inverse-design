"""Exact Q=10..20 sweep around one frozen three-feature target."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .frozen_mlp import FrozenTandemMLP, GEOMETRY_COLUMNS


Q_SWEEP_VALUES = tuple(range(10, 21))
FEATURE_NAMES = ("Lp_nH", "Ls_nH", "Q_scalar", "K_abs")
DECLARED_FEATURE_SPANS = np.asarray((2.5, 2.5, 20.0, 0.8), dtype=float)


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
    geometry_um: dict[str, float]
    geometry_sha256: str
    per_feature_abs_error: dict[str, float]
    per_feature_percent_error: dict[str, float]
    declared_range_normalized_rmse: float
    relative_percent_rmse: float
    evidence_source: str
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
    candidates: tuple[CandidateMetrics, ...]
    selected_artifacts: dict[str, str]
    scientific_boundary: str

    def record(self) -> dict[str, Any]:
        value = asdict(self)
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
) -> QSweepResult:
    """Create a no-clobber run directory and execute proxy or physical selection."""

    if mode not in {"proxy", "physical"}:
        raise ValueError("mode must be 'proxy' or 'physical'")
    root = Path(output_dir).expanduser().resolve()
    _prepare_no_clobber_directory(root)
    started_utc = _utc_now()
    proxy = run_q_sweep(model, target)
    _write_candidates_csv(root / "proxy_candidates.csv", proxy.candidates)
    _write_json(root / "proxy_candidates.json", proxy.record())
    _write_backend_request(root / "physical_backend_request.json", model, proxy)

    if mode == "physical":
        try:
            if not physical_backend_command:
                raise RuntimeError(
                    "physical mode requires RFIC_Q_SWEEP_PHYSICAL_BACKEND or "
                    "--physical-backend-command; proxy ranking cannot be reported as "
                    "physical validation"
                )
            result = _run_physical_backend(
                proxy=proxy,
                root=root,
                command=physical_backend_command,
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
                    "q_values": list(Q_SWEEP_VALUES),
                    "candidate_count": len(proxy.candidates),
                    "failure_type": type(exc).__name__,
                    "failure_message": str(exc),
                    "evidence_source": "FROZEN_FORWARD_PROXY_DIAGNOSTIC_ONLY",
                    "scientific_boundary": (
                        "Physical selection was not completed. The proxy candidates "
                        "must not be reported as fresh-EMX validation."
                    ),
                },
            )
            raise
        _write_candidates_csv(root / "physical_candidates.csv", result.candidates)
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


def _run_physical_backend(
    *,
    proxy: QSweepResult,
    root: Path,
    command: str,
) -> QSweepResult:
    physical_root = root / "physical_backend"
    physical_root.mkdir(exist_ok=False)
    request = root / "physical_backend_request.json"
    process = subprocess.run(
        shlex.split(command)
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
    payload = _read_json(result_path)
    if payload.get("schema") != "rfic_q_sweep_physical_results.v1":
        raise ValueError("physical backend returned an unsupported schema")
    if payload.get("label_source") != "FRESH_REAL_EMX":
        raise ValueError("physical backend label_source must be FRESH_REAL_EMX")
    rows = payload.get("results")
    if not isinstance(rows, list):
        raise ValueError("physical backend results must be a list")
    expected = {item.candidate_id: item for item in proxy.candidates}
    if {str(row.get("candidate_id")) for row in rows} != set(expected):
        raise ValueError("physical backend must return exactly all eleven candidates")
    candidates: list[CandidateMetrics] = []
    for row in rows:
        candidate_id = str(row["candidate_id"])
        proxy_item = expected[candidate_id]
        if int(row.get("q_target")) != proxy_item.q_target:
            raise ValueError(f"Q mismatch for {candidate_id}")
        if str(row.get("geometry_sha256")) != proxy_item.geometry_sha256:
            raise ValueError(f"geometry hash mismatch for {candidate_id}")
        feature_record = row.get("features_15ghz") or {}
        qp = float(feature_record["Qp"])
        qs = float(feature_record["Qs"])
        physical = np.asarray(
            (
                float(feature_record["Lp_nH"]),
                float(feature_record["Ls_nH"]),
                min(qp, qs),
                abs(float(feature_record["K_abs"])),
            ),
            dtype=float,
        )
        if not np.isfinite(physical).all() or qp <= 0.0 or qs <= 0.0:
            raise ValueError(f"invalid fresh-EMX features for {candidate_id}")
        artifacts = _validated_backend_artifacts(
            physical_root,
            row.get("artifacts") or {},
            candidate_id,
        )
        target_vector = np.asarray(
            [proxy_item.target_features[name] for name in FEATURE_NAMES], dtype=float
        )
        candidates.append(
            _candidate_metrics(
                candidate_id=candidate_id,
                q_target=proxy_item.q_target,
                target_features=target_vector,
                observed_features=physical,
                geometry=proxy_item.geometry_um,
                evidence_source="FRESH_REAL_EMX",
                artifacts=artifacts,
            )
        )
    candidates.sort(key=lambda item: item.q_target)
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
        candidates=tuple(candidates),
        selected_artifacts=selected.artifacts,
        scientific_boundary=(
            "The selected candidate minimizes the fixed declared-range-normalized "
            "four-feature score across eleven fresh real-EMX evaluations at 15 GHz. "
            "Foundry DRC and independent HFSS correlation remain separate gates."
        ),
    )


def _candidate_metrics(
    *,
    candidate_id: str,
    q_target: int,
    target_features: np.ndarray,
    observed_features: np.ndarray,
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
    return CandidateMetrics(
        candidate_id=candidate_id,
        q_target=int(q_target),
        target_features={name: float(value) for name, value in zip(FEATURE_NAMES, target)},
        observed_features={name: float(value) for name, value in zip(FEATURE_NAMES, observed)},
        geometry_um={key: float(value) for key, value in geometry.items()},
        geometry_sha256=_geometry_sha256(geometry),
        per_feature_abs_error={name: float(value) for name, value in zip(FEATURE_NAMES, absolute)},
        per_feature_percent_error={name: float(value) for name, value in zip(FEATURE_NAMES, percent)},
        declared_range_normalized_rmse=score,
        relative_percent_rmse=percent_rmse,
        evidence_source=evidence_source,
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


def _write_backend_request(path: Path, model: FrozenTandemMLP, result: QSweepResult) -> None:
    payload = {
        "schema": "rfic_q_sweep_physical_request.v1",
        "model_id": model.model_id,
        "model_seed": model.model_seed,
        "target_frequency_ghz": model.target_frequency_ghz,
        "q_values": list(result.q_values),
        "required_label_source": "FRESH_REAL_EMX",
        "required_outputs": ["GDS", "S4P", "Lp", "Ls", "Qp", "Qs", "K_abs"],
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
        result[kind] = str(path)
        result[f"{kind}_sha256"] = _sha256(path)
    preview = artifacts.get("preview")
    if preview:
        path = (root / str(preview)).resolve() if not Path(str(preview)).is_absolute() else Path(str(preview)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"unsafe preview artifact for {candidate_id}")
        result["preview"] = str(path)
        result["preview_sha256"] = _sha256(path)
    return result


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
