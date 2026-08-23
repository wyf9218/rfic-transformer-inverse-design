"""Remote SSH execution helpers for EMX/Cadence runs."""

from __future__ import annotations

import json
import os
import posixpath
import shlex
import shutil
import subprocess
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import yaml

from ..core.topology import TransformerSpec
from ..core.types import TransformerRunConfig


def _remote_config_for_execution(run_config: TransformerRunConfig) -> TransformerRunConfig:
    remote_process_file = run_config.emx.remote_emx_process_file
    if remote_process_file is None or not str(remote_process_file).strip():
        return run_config
    return replace(
        run_config,
        emx=replace(
            run_config.emx,
            emx_process_file=str(remote_process_file),
        ),
    )


def _run_subprocess(
    command: list[str],
    *,
    capture: bool = True,
    failure_label: str,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            capture_output=capture,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            f"{failure_label} failed because '{command[0]}' is not available on this machine."
        ) from exc
    if stdout_path is not None:
        stdout_path.write_text(result.stdout if result.stdout is not None else "", encoding="utf-8")
    if stderr_path is not None:
        stderr_path.write_text(result.stderr if result.stderr is not None else "", encoding="utf-8")
    if result.returncode != 0:
        message = (
            f"{failure_label} failed with exit code {result.returncode}."
        )
        if stderr_path is not None:
            message += f" See {stderr_path} for details."
        elif result.stderr:
            message += f" stderr: {result.stderr.strip()}"
        raise RuntimeError(message)
    return result


def _ssh_command(*, host: str, remote_command: str) -> list[str]:
    return ["ssh", "-o", "BatchMode=yes", str(host), remote_command]


def _split_command(command: str) -> list[str]:
    return shlex.split(str(command), posix=(os.name != "nt"))


def _ssh_base_command(command: str) -> list[str]:
    return [*_split_command(command), "-o", "BatchMode=yes"]


def _uses_wsl_transport(command: str) -> bool:
    parts = _split_command(command)
    return bool(parts) and parts[0].lower() == "wsl"


def _wsl_local_path(path: Path) -> str:
    resolved = Path(path).resolve()
    drive = resolved.drive.rstrip(":").lower()
    if drive:
        suffix = resolved.as_posix().split(":", 1)[1].lstrip("/")
        return f"/mnt/{drive}/{suffix}"
    return resolved.as_posix()


def _transfer_local_path(path: Path, *, command: str) -> str:
    if _uses_wsl_transport(command) and os.name == "nt":
        return _wsl_local_path(path)
    return str(path)


def _ensure_remote_directory(*, ssh_command: str, host: str, remote_dir: str, log_dir: Path) -> None:
    command = [
        *_ssh_base_command(ssh_command),
        str(host),
        f"mkdir -p {shlex.quote(str(remote_dir))}",
    ]
    _run_subprocess(
        command,
        failure_label="Remote directory creation",
        stdout_path=log_dir / "ssh_mkdir.stdout.log",
        stderr_path=log_dir / "ssh_mkdir.stderr.log",
    )


def _scp_upload(*, scp_command: str, host: str, local_path: Path, remote_path: str, log_dir: Path) -> None:
    command = [
        *_split_command(scp_command),
        "-o",
        "BatchMode=yes",
        _transfer_local_path(local_path, command=scp_command),
        f"{host}:{remote_path}",
    ]
    _run_subprocess(
        command,
        failure_label=f"Upload to {host}",
        stdout_path=log_dir / f"scp_upload_{local_path.name}.stdout.log",
        stderr_path=log_dir / f"scp_upload_{local_path.name}.stderr.log",
    )


def _scp_download_file(*, scp_command: str, host: str, remote_path: str, local_path: Path, log_dir: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        *_split_command(scp_command),
        "-o",
        "BatchMode=yes",
        f"{host}:{remote_path}",
        _transfer_local_path(local_path, command=scp_command),
    ]
    _run_subprocess(
        command,
        failure_label=f"Download from {host}",
        stdout_path=log_dir / f"scp_download_{local_path.name}.stdout.log",
        stderr_path=log_dir / f"scp_download_{local_path.name}.stderr.log",
    )


def _scp_download_tree(*, scp_command: str, host: str, remote_dir: str, local_dir: Path, log_dir: Path) -> None:
    local_dir.parent.mkdir(parents=True, exist_ok=True)
    remote_name = posixpath.basename(str(remote_dir).rstrip("/"))
    downloaded_dir = local_dir.parent / remote_name
    if downloaded_dir.exists():
        if downloaded_dir.is_dir():
            shutil.rmtree(downloaded_dir)
        else:
            downloaded_dir.unlink()
    command = [
        *_split_command(scp_command),
        "-o",
        "BatchMode=yes",
        "-r",
        f"{host}:{remote_dir}",
        _transfer_local_path(local_dir.parent, command=scp_command),
    ]
    _run_subprocess(
        command,
        failure_label=f"Recursive download from {host}",
        stdout_path=log_dir / "scp_download_tree.stdout.log",
        stderr_path=log_dir / "scp_download_tree.stderr.log",
    )
    if downloaded_dir != local_dir:
        if local_dir.exists():
            shutil.rmtree(local_dir)
        downloaded_dir.rename(local_dir)


def _rebase_payload_paths(payload: Any, *, remote_root: str, local_root: str) -> Any:
    if isinstance(payload, dict):
        return {
            key: _rebase_payload_paths(value, remote_root=remote_root, local_root=local_root)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_rebase_payload_paths(value, remote_root=remote_root, local_root=local_root) for value in payload]
    if isinstance(payload, tuple):
        return tuple(_rebase_payload_paths(value, remote_root=remote_root, local_root=local_root) for value in payload)
    if isinstance(payload, str):
        normalized = payload.replace("\\", "/")
        remote_root_norm = str(remote_root).replace("\\", "/").rstrip("/")
        if normalized == remote_root_norm:
            return str(local_root)
        prefix = remote_root_norm + "/"
        if normalized.startswith(prefix):
            suffix = normalized[len(prefix) :]
            return str(Path(local_root) / Path(*suffix.split("/")))
    return payload


def run_transformer_remote_ssh_roundtrip(
    *,
    run_config: TransformerRunConfig,
    geometry: TransformerSpec,
    local_work_dir: Path,
    cache_key: str,
) -> dict[str, object]:
    emx = run_config.emx
    if not emx.uses_remote_ssh():
        raise ValueError("Remote SSH roundtrip requested for a non-remote EMX configuration")

    host = str(emx.remote_ssh_host)
    ssh_command = str(emx.remote_ssh_command or "ssh")
    scp_command = str(emx.remote_scp_command or "scp")
    remote_repo_root = str(emx.remote_repo_root)
    remote_work_root = str(emx.remote_work_root)
    remote_request_dir = posixpath.join(remote_work_root, "requests", cache_key)
    remote_config_path = posixpath.join(remote_request_dir, "run_config.yaml")
    remote_geometry_path = posixpath.join(remote_request_dir, "geometry.json")
    remote_result_path = posixpath.join(remote_request_dir, "runner_result.json")

    local_request_dir = Path(local_work_dir) / "remote_request"
    local_request_dir.mkdir(parents=True, exist_ok=True)
    local_config_path = local_request_dir / "run_config.yaml"
    local_geometry_path = local_request_dir / "geometry.json"
    local_runner_result_path = local_request_dir / "runner_result.json"

    remote_run_config = _remote_config_for_execution(run_config)
    local_config_path.write_text(
        yaml.safe_dump(asdict(remote_run_config), sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    local_geometry_path.write_text(
        json.dumps(geometry.flat_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )

    _ensure_remote_directory(
        ssh_command=ssh_command,
        host=host,
        remote_dir=remote_request_dir,
        log_dir=local_request_dir,
    )
    _scp_upload(
        scp_command=scp_command,
        host=host,
        local_path=local_config_path,
        remote_path=remote_config_path,
        log_dir=local_request_dir,
    )
    _scp_upload(
        scp_command=scp_command,
        host=host,
        local_path=local_geometry_path,
        remote_path=remote_geometry_path,
        log_dir=local_request_dir,
    )

    remote_steps = [f"cd {shlex.quote(remote_repo_root)}"]
    if emx.remote_venv_activate is not None and str(emx.remote_venv_activate).strip():
        remote_steps.append(f". {shlex.quote(str(emx.remote_venv_activate))}")
    remote_steps.append(
        " ".join(
            [
                shlex.quote(str(emx.remote_python or "python")),
                "-m",
                "rfic_transformer_inverse_design.execution.remote_runner",
                "--config",
                shlex.quote(remote_config_path),
                "--geometry",
                shlex.quote(remote_geometry_path),
                "--root-dir",
                shlex.quote(remote_work_root),
                "--out-json",
                shlex.quote(remote_result_path),
            ]
        )
    )
    remote_command = " && ".join(remote_steps)
    _run_subprocess(
        [*_ssh_base_command(ssh_command), str(host), remote_command],
        failure_label=f"Remote EMX execution on {host}",
        stdout_path=local_request_dir / "ssh_runner.stdout.log",
        stderr_path=local_request_dir / "ssh_runner.stderr.log",
    )

    _scp_download_file(
        scp_command=scp_command,
        host=host,
        remote_path=remote_result_path,
        local_path=local_runner_result_path,
        log_dir=local_request_dir,
    )
    runner_result = json.loads(local_runner_result_path.read_text(encoding="utf-8"))
    remote_work_dir = str(runner_result["work_dir"])
    remote_summary_path = str(runner_result["summary_path"])

    _scp_download_tree(
        scp_command=scp_command,
        host=host,
        remote_dir=remote_work_dir,
        local_dir=Path(local_work_dir),
        log_dir=local_request_dir,
    )
    local_summary_path = Path(
        _rebase_payload_paths(
            remote_summary_path,
            remote_root=remote_work_dir,
            local_root=str(Path(local_work_dir).resolve()),
        )
    )
    payload = json.loads(local_summary_path.read_text(encoding="utf-8"))
    rebased_payload = _rebase_payload_paths(
        payload,
        remote_root=remote_work_dir,
        local_root=str(Path(local_work_dir).resolve()),
    )
    if not isinstance(rebased_payload, dict):
        raise RuntimeError("Remote EMX runner produced a non-dictionary payload")
    return rebased_payload
