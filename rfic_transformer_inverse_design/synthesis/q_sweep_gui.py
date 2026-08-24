"""Local three-input web application for exact-Q MLP synthesis."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .frozen_mlp import FrozenTandemMLP
from .q_sweep import PhysicalTarget3, execute_q_sweep


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


class JobStore:
    """Single active job store; EMX jobs are intentionally serialized per GUI."""

    def __init__(
        self,
        *,
        model: FrozenTandemMLP,
        output_root: Path,
        mode: str,
        physical_backend_command: str | None,
    ) -> None:
        self.model = model
        self.output_root = output_root.expanduser().resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.physical_backend_command = physical_backend_command
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

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
            )
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
        try:
            result = execute_q_sweep(
                model=self.model,
                target=job.target,
                output_dir=job.output_dir,
                mode=self.mode,
                physical_backend_command=self.physical_backend_command,
            )
            with self._lock:
                job.result = result.record()
                job.status = "SUCCEEDED"
                job.artifacts = _collect_artifacts(job.output_dir, result.record())
        except Exception as exc:
            with self._lock:
                job.status = "FAILED"
                job.error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                job.completed_utc = _utc_now()

    def public_record(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            result = job.result or {}
            return {
                "job_id": job.job_id,
                "status": job.status,
                "mode": self.mode,
                "created_utc": job.created_utc,
                "completed_utc": job.completed_utc,
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
                "scientific_boundary": result.get("scientific_boundary"),
                "candidates": result.get("candidates") or [],
                "artifacts": {
                    name: f"/api/jobs/{job_id}/artifacts/{name}"
                    for name in job.artifacts
                },
            }

    def artifact(self, job_id: str, name: str) -> Path:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            path = job.artifacts.get(name)
            if path is None or not path.is_file():
                raise FileNotFoundError(name)
            if not path.resolve().is_relative_to(job.output_dir.resolve()):
                raise PermissionError("artifact escaped the job directory")
            return path


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
    candidates_name = (
        "physical_candidates.csv" if result.get("mode") == "physical" else "proxy_candidates.csv"
    )
    candidates = {
        "selection": root / "selection.json",
        "manifest": root / "run_manifest.json",
        "candidates": root / candidates_name,
        "preview": Path(str((result.get("selected_artifacts") or {}).get("preview") or "")),
    }
    for kind in ("gds", "s4p"):
        value = (result.get("selected_artifacts") or {}).get(kind)
        if value:
            candidates[kind] = Path(str(value))
    return {
        name: path.resolve()
        for name, path in candidates.items()
        if path.is_file() and path.resolve().is_relative_to(root.resolve())
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
                path = self.server.store.artifact(match.group(1), match.group(2))
                media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                self._bytes(HTTPStatus.OK, path.read_bytes(), media, filename=path.name)
            except (KeyError, FileNotFoundError, PermissionError):
                self._json(HTTPStatus.NOT_FOUND, {"error": "artifact not found"})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/jobs":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            record = self.server.store.create(payload)
            self._json(HTTPStatus.ACCEPTED, record)
        except (ValueError, RuntimeError) as exc:
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
    parser.add_argument(
        "--output-root",
        default=os.environ.get("RFIC_Q_SWEEP_OUTPUT_ROOT", "q_sweep_gui_runs"),
    )
    parser.add_argument("--mode", choices=("proxy", "physical"), default=os.environ.get("RFIC_Q_SWEEP_MODE", "proxy"))
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
    model = FrozenTandemMLP.load(args.model_dir, contract_path=args.model_contract)
    store = JobStore(
        model=model,
        output_root=Path(args.output_root),
        mode=args.mode,
        physical_backend_command=args.physical_backend_command,
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
    return _HTML.replace("__MODE__", mode).replace(
        "__MODE_LABEL__", "Fresh real-EMX" if mode == "physical" else "Frozen proxy"
    )


_HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RFIC Transformer Inverse Synthesis</title>
<style>
:root{--ink:#101827;--muted:#5c6677;--line:#d8dee8;--blue:#0b6bdc;--green:#008b68;--orange:#de5a00;--bg:#f5f7fa}
*{box-sizing:border-box}html,body{overflow-x:hidden}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,Arial,sans-serif;letter-spacing:0}
[hidden]{display:none!important}
header{height:72px;background:#0a396c;color:white;display:flex;align-items:center;justify-content:space-between;padding:0 34px}
h1{font-size:23px;margin:0;font-weight:700}header span{font-size:13px;border:1px solid #83a9cf;padding:6px 10px;border-radius:4px}
main{max-width:1440px;margin:0 auto;padding:28px 32px 44px}.toolbar{display:grid;grid-template-columns:1fr 1fr 1fr 1fr auto;gap:14px;align-items:end;background:white;border:1px solid var(--line);padding:20px;border-radius:6px}
label{display:block;font-size:12px;font-weight:700;color:var(--muted);margin-bottom:7px}input{width:100%;height:42px;border:1px solid #aeb7c5;border-radius:4px;padding:0 11px;font-size:16px;background:white}
button{height:42px;border:0;border-radius:4px;background:var(--blue);color:white;font-weight:700;padding:0 22px;cursor:pointer}button:disabled{opacity:.55;cursor:not-allowed}
#status{margin:16px 0;padding:12px 14px;border-left:4px solid var(--blue);background:white;color:var(--muted);overflow-wrap:anywhere}
.result{display:grid;grid-template-columns:minmax(380px,42%) 1fr;gap:20px}.surface{background:white;border:1px solid var(--line);border-radius:6px;padding:18px;min-width:0}
.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:18px}.metric{border-top:3px solid var(--blue);padding:12px 4px;min-width:0}.metric strong{display:block;font-size:28px;overflow-wrap:anywhere}.metric small{color:var(--muted)}
img{width:100%;max-height:470px;object-fit:contain;background:#f7f8fa;border:1px solid var(--line)}table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}tr.selected{background:#e8f3ff;font-weight:700}
.downloads{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}.downloads a{color:var(--blue);font-weight:700;text-decoration:none;border:1px solid #a8c8ef;border-radius:4px;padding:8px 11px}.boundary{font-size:12px;color:var(--muted);margin-top:14px;line-height:1.45}
@media(max-width:900px){.toolbar{grid-template-columns:1fr 1fr}.result{grid-template-columns:1fr}.table-wrap{overflow:auto}}
@media(max-width:600px){header{height:auto;min-height:72px;padding:12px 20px;gap:12px}h1{font-size:20px}.metrics{grid-template-columns:1fr 1fr}.metric.evidence{grid-column:1/-1}.metric.evidence strong{font-size:12px}main{padding:24px 20px 40px}}
</style>
</head>
<body><header><h1>RFIC Transformer Inverse Synthesis</h1><span>__MODE_LABEL__ · Q = 10…20</span></header>
<main>
<form id="form" class="toolbar">
<div><label for="design">Design ID</label><input id="design" value="candidate_001" required></div>
<div><label for="lp">Lp (nH)</label><input id="lp" type="number" min="0.5" max="3" step="0.001" value="1.150" required></div>
<div><label for="ls">Ls (nH)</label><input id="ls" type="number" min="0.5" max="3" step="0.001" value="1.400" required></div>
<div><label for="k">|K|</label><input id="k" type="number" min="0.001" max="0.8" step="0.001" value="0.760" required></div>
<button id="run" type="submit">生成候选</button>
</form>
<div id="status">等待输入。</div>
<section id="result" class="result" hidden>
<div class="surface"><div class="metrics"><div class="metric"><small>Selected Q</small><strong id="selectedQ">-</strong></div><div class="metric"><small>Normalized RMSE</small><strong id="score">-</strong></div><div class="metric evidence"><small>Evidence</small><strong id="evidence">-</strong></div></div><img id="preview" alt="Selected transformer structure"><div class="downloads" id="downloads"></div><p class="boundary" id="boundary"></p></div>
<div class="surface table-wrap"><table><thead><tr><th>Q</th><th>Lp</th><th>Ls</th><th>Q observed</th><th>|K|</th><th>Lp err%</th><th>Ls err%</th><th>Q err%</th><th>K err%</th><th>Score</th></tr></thead><tbody id="rows"></tbody></table></div>
</section>
</main>
<script>
const form=document.getElementById('form'),run=document.getElementById('run'),statusBox=document.getElementById('status'),result=document.getElementById('result');
const fmt=(v,n=3)=>Number(v).toFixed(n);
form.addEventListener('submit',async(e)=>{e.preventDefault();run.disabled=true;result.hidden=true;statusBox.textContent='任务已提交。';
 const payload={design_id:design.value.trim(),lp_nh:Number(lp.value),ls_nh:Number(ls.value),k_abs:Number(k.value)};
 try{const response=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw new Error(data.error||'提交失败');poll(data.job_id)}catch(err){statusBox.textContent=err.message;run.disabled=false;}});
async function poll(id){try{const response=await fetch('/api/jobs/'+id);const data=await response.json();statusBox.textContent=data.status==='RUNNING'?'正在生成 11 个 Q 候选…':data.status;if(data.status==='SUCCEEDED'){render(data);run.disabled=false;return}if(data.status==='FAILED'){statusBox.textContent=data.error||'任务失败';run.disabled=false;return}setTimeout(()=>poll(id),900)}catch(err){statusBox.textContent=err.message;run.disabled=false}}
function render(data){result.hidden=false;selectedQ.textContent=data.selected_q;score.textContent=fmt(data.selection_score,5);evidence.textContent=data.evidence_source;preview.src=data.artifacts.preview+'?t='+Date.now();boundary.textContent=data.scientific_boundary||'';
 rows.innerHTML='';for(const c of data.candidates){const tr=document.createElement('tr');if(c.q_target===data.selected_q)tr.className='selected';const p=c.per_feature_percent_error,o=c.observed_features;tr.innerHTML=`<td>${c.q_target}</td><td>${fmt(o.Lp_nH)}</td><td>${fmt(o.Ls_nH)}</td><td>${fmt(o.Q_scalar)}</td><td>${fmt(o.K_abs)}</td><td>${fmt(p.Lp_nH,2)}</td><td>${fmt(p.Ls_nH,2)}</td><td>${fmt(p.Q_scalar,2)}</td><td>${fmt(p.K_abs,2)}</td><td>${fmt(c.declared_range_normalized_rmse,5)}</td>`;rows.appendChild(tr)}
 downloads.innerHTML='';const labels={gds:'下载 GDS',s4p:'下载 S4P',candidates:'候选误差 CSV',selection:'运行 JSON'};for(const key of ['gds','s4p','candidates','selection']){if(data.artifacts[key]){const a=document.createElement('a');a.href=data.artifacts[key];a.textContent=labels[key];downloads.appendChild(a)}}statusBox.textContent=`完成：Q=${data.selected_q}，证据源 ${data.evidence_source}`;}
</script></body></html>'''


if __name__ == "__main__":
    raise SystemExit(main())
