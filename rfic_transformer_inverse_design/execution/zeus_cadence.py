"""Zeus-side Cadence OA round-trip helpers for transformer EMX runs."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

import numpy as np

from ..network_analysis import reduce_s_params_by_shorting
from ..sim.base import SParameterResult, SolverType
from ..sim.emx.layout_export import EMXLayoutManifest, EMXPort
from ..sim.emx.render import render_emx_layout_preview, render_emx_port_debug_panels
from ..sim.emx.simulation import EMXSimulation
from ..sim.touchstone import load_touchstone

from ..analysis.extraction import (
    extract_transformer_metrics,
    extract_transformer_metrics_from_differential,
    extract_transformer_metrics_from_single_ended_pairs,
)
from ..analysis.objective import score_transformer_result
from ..core.topology import TransformerSpec
from ..core.types import TransformerEvalResult, TransformerLayoutExport, TransformerRunConfig
from .serialization import _json_default

logger = logging.getLogger(__name__)

DEFAULT_CADENCE_INSTALL_ROOT = "/opt/cadence/IC"
DEFAULT_PDK_CDS_LIB = "/path/to/pdk/cds.lib"
DEFAULT_TECH_LIB = "exampleTechLib"
DEFAULT_LAYER_MAP = "/path/to/pdk/layers.layermap"
DEFAULT_CADENCE_ACCESS_DIRS = ("top", "bottom", "left", "right")
CADENCE_FATAL_STDERR_MARKERS = ("*Error*", "License call failed", "License initialization failed")

CadenceStopAfter = Literal["export", "strmin", "dbcreatepin", "strmout", "emx"]


def _write_touchstone_ri(path: Path, result: SParameterResult, z0_ohm: float = 50.0) -> Path:
    path = Path(path)
    n_ports = int(result.num_ports)
    with path.open("w", encoding="ascii") as handle:
        handle.write(f"! {n_ports}-port synthetic data\n")
        handle.write(f"# GHz S RI R {float(z0_ohm):g}\n")
        for idx, freq_hz in enumerate(result.freqs_hz):
            values = [f"{float(freq_hz) / 1e9:.12g}"]
            for row in range(n_ports):
                for col in range(n_ports):
                    s = complex(result.s_matrix[idx, row, col])
                    values.extend([f"{s.real:.16e}", f"{s.imag:.16e}"])
            handle.write(" ".join(values) + "\n")
    return path


def _transformer_port_reduction(
    raw_result: SParameterResult,
    manifest: EMXLayoutManifest | None = None,
) -> tuple[list[int], list[int]] | None:
    if raw_result.num_ports <= 4:
        return None

    if manifest is not None and len(manifest.ports) == raw_result.num_ports:
        port_names = [port.name for port in manifest.ports]
        keep = [port_names.index(name) for name in ("P001", "P002", "P003", "P004") if name in port_names]
        if len(keep) != 4:
            raise ValueError(
                "Unable to reduce multiport transformer result because manifest does not contain "
                "P001/P002/P003/P004 signal ports"
            )
        short = [idx for idx in range(len(port_names)) if idx not in keep]
        if not short:
            return None
        return keep, short

    return [0, 1, 2, 3], list(range(4, raw_result.num_ports))


def prepare_transformer_touchstone_result(
    *,
    raw_result: SParameterResult,
    target,
    manifest: EMXLayoutManifest | None = None,
    raw_touchstone_path: Path | None = None,
    out_dir: Path | None = None,
    differential_port_pairs: tuple[tuple[int, int], tuple[int, int]] | None = None,
    ground_unused_s8p_ports: bool = False,
) -> dict[str, object]:
    reduced_touchstone_path: Path | None = None
    effective_touchstone_path = Path(raw_touchstone_path) if raw_touchstone_path is not None else None

    if raw_result.num_ports == 2:
        metrics, diff_result, diff_z = extract_transformer_metrics_from_differential(raw_result, target)
        single_result = None
    elif differential_port_pairs is not None:
        single_result = raw_result
        metrics, diff_result, diff_z = extract_transformer_metrics_from_single_ended_pairs(
            raw_result,
            target,
            differential_port_pairs,
            ground_unused_ports=bool(ground_unused_s8p_ports),
        )
    else:
        single_result = raw_result
        reduction = _transformer_port_reduction(raw_result, manifest)
        if reduction is not None:
            ports_to_keep, ports_to_short = reduction
            reduced_s = reduce_s_params_by_shorting(
                raw_result.s_matrix,
                ports_to_short=ports_to_short,
                ports_to_keep=ports_to_keep,
                gamma_load=-1.0,
            )
            single_result = SParameterResult(freqs_hz=raw_result.freqs_hz, s_matrix=reduced_s)
            if out_dir is not None and raw_touchstone_path is not None:
                reduced_touchstone_path = _write_touchstone_ri(
                    Path(out_dir) / f"{Path(raw_touchstone_path).stem}_reduced.s4p",
                    single_result,
                    z0_ohm=50.0,
                )
                effective_touchstone_path = reduced_touchstone_path
        if single_result.num_ports != 4:
            raise ValueError(
                "Transformer evaluation only supports 4-port single-ended or 2-port differential EMX "
                f"results after reduction, got {single_result.num_ports} ports"
            )
        metrics, diff_result, diff_z = extract_transformer_metrics(single_result, target)

    return {
        "single_result": single_result,
        "differential_result": diff_result,
        "differential_z": diff_z,
        "metrics": metrics,
        "touchstone_path": effective_touchstone_path,
        "raw_touchstone_path": (Path(raw_touchstone_path) if raw_touchstone_path is not None else None),
        "reduced_touchstone_path": reduced_touchstone_path,
    }


@dataclass(frozen=True)
class ZeusCadenceWorkspace:
    """Per-run scratch Cadence workspace rooted inside one evaluation directory."""

    root_dir: Path
    cadence_dir: Path
    skill_dir: Path
    cds_lib_path: Path
    oa_lib_name: str
    oa_lib_dir: Path
    strmin_log_path: Path
    strmin_summary_path: Path
    strmin_stdout_path: Path
    strmin_stderr_path: Path
    create_lib_skill_path: Path
    create_lib_stdout_path: Path
    create_lib_stderr_path: Path
    create_pins_skill_path: Path
    create_pins_stdout_path: Path
    create_pins_stderr_path: Path
    strmout_log_path: Path
    strmout_summary_path: Path
    strmout_stdout_path: Path
    strmout_stderr_path: Path
    streamout_dir: Path
    streamout_gds_path: Path
    streamout_preview_path: Path
    streamout_debug_preview_path: Path


@dataclass(frozen=True)
class CadenceRoundtripExport:
    """One exported transformer layout participating in a shared Cadence round-trip."""

    cache_key: str
    geometry: TransformerSpec
    work_dir: Path
    layout: TransformerLayoutExport


def load_emx_layout_manifest(manifest_path: Path) -> EMXLayoutManifest:
    """Load the JSON manifest written by transformer layout export."""

    raw = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    ports = tuple(
        EMXPort(
            name=str(port["name"]),
            signal_labels=tuple(str(label) for label in port["signal_labels"]),
            ground_labels=tuple(str(label) for label in port["ground_labels"]),
            internal_size_um=tuple(float(v) for v in port["internal_size_um"]),
            signal_internal_size_um=(
                None
                if port.get("signal_internal_size_um") is None
                else tuple(float(v) for v in port["signal_internal_size_um"])
            ),
            ground_internal_size_um=(
                None
                if port.get("ground_internal_size_um") is None
                else tuple(float(v) for v in port["ground_internal_size_um"])
            ),
            internal_signal_labels=bool(port.get("internal_signal_labels", True)),
            internal_ground_labels=bool(port.get("internal_ground_labels", True)),
        )
        for port in raw["ports"]
    )
    return EMXLayoutManifest(
        layout_path=str(raw["layout_path"]),
        top_cell=str(raw["top_cell"]),
        ports=ports,
        metal_layer=int(raw["metal_layer"]),
        metal_datatype=int(raw["metal_datatype"]),
        ground_layer=(None if raw.get("ground_layer") is None else int(raw["ground_layer"])),
        ground_datatype=(None if raw.get("ground_datatype") is None else int(raw["ground_datatype"])),
        label_layer=int(raw["label_layer"]),
        label_datatype=int(raw["label_datatype"]),
        cadence_pin_purpose=(
            None if raw.get("cadence_pin_purpose") is None else int(raw["cadence_pin_purpose"])
        ),
    )


def collect_cadence_pin_labels(manifest: EMXLayoutManifest) -> tuple[str, ...]:
    """Return the manifest label names that must exist as OA pins, preserving order."""

    labels: list[str] = []
    seen: set[str] = set()
    for port in manifest.ports:
        for label in (*port.signal_labels, *port.ground_labels):
            name = str(label)
            if name in seen:
                continue
            seen.add(name)
            labels.append(name)
    return tuple(labels)


def sanitize_oa_lib_name(name: str) -> str:
    """Normalize a scratch OA library name to a conservative Cadence-safe token."""

    token = re.sub(r"[^A-Za-z0-9_]", "_", str(name).strip())
    token = re.sub(r"_+", "_", token).strip("_")
    if not token:
        token = "xfmr_cadpins"
    if token[0].isdigit():
        token = f"xfmr_{token}"
    return token[:96]


def build_local_cds_lib(*, pdk_cds_lib: str, oa_lib_name: str, oa_lib_dir: Path) -> str:
    """Create the local cds.lib body for one scratch Cadence workspace."""

    return (
        f"INCLUDE {pdk_cds_lib}\n"
        f"DEFINE {oa_lib_name} {Path(oa_lib_dir).as_posix()}\n"
    )


def build_cadence_env(
    *,
    cadence_install_root: str = DEFAULT_CADENCE_INSTALL_ROOT,
    license_file: str | None = None,
    cdslmd_license_file: str | None = None,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the validated Cadence environment for dbAccess/strmin/strmout on Zeus."""

    env = dict(os.environ if base_env is None else base_env)
    lib_path = f"{cadence_install_root}/CAE/lib"
    existing_ld = env.get("LD_LIBRARY_PATH")
    env["LD_LIBRARY_PATH"] = lib_path if not existing_ld else f"{lib_path}:{existing_ld}"
    env["CDS_SKIP_OS_CHECK_ON_STARTUP"] = "1"
    if license_file:
        env["LM_LICENSE_FILE"] = license_file
        env["CDS_LIC_FILE"] = license_file
    if cdslmd_license_file:
        env["CDSLMD_LICENSE_FILE"] = cdslmd_license_file
    elif license_file and "CDSLMD_LICENSE_FILE" not in env:
        env["CDSLMD_LICENSE_FILE"] = license_file
    return env


def cadence_binary(cadence_install_root: str, tool_name: str) -> str:
    """Return the uname26-wrapped Cadence CLI invocation for Zeus."""

    wrapper = Path("/usr/local/bin/uname26")
    binary = Path(cadence_install_root) / "bin" / tool_name
    if wrapper.exists():
        return f"{wrapper} {binary}"
    return str(binary)


def build_create_library_skill(*, oa_lib_name: str, oa_lib_dir: Path, tech_lib_name: str) -> str:
    """Build the SKILL snippet that creates and tech-binds the scratch OA library."""

    return (
        "let((libObj techLib)\n"
        f"  libObj = ddGetObj(\"{oa_lib_name}\")\n"
        "  unless(libObj\n"
        f"    libObj = ddCreateLib(\"{oa_lib_name}\" \"{Path(oa_lib_dir).as_posix()}\")\n"
        "  )\n"
        f"  techLib = ddGetObj(\"{tech_lib_name}\")\n"
        "  unless(techLib\n"
        f"    error(\"Missing tech lib {tech_lib_name} in cds.lib\")\n"
        "  )\n"
        f"  techBindTechFile(libObj \"{tech_lib_name}\")\n"
        ")\n"
        "exit()\n"
    )


def build_create_pins_skill(
    *,
    oa_lib_name: str,
    top_cell: str,
    labels: tuple[str, ...],
    access_dirs: tuple[str, ...] = DEFAULT_CADENCE_ACCESS_DIRS,
    manufacturing_grid_um: float = 0.005,
) -> str:
    """Build the SKILL snippet that converts manifest pin labels into OA pins."""

    return build_create_pins_batch_skill(
        oa_lib_name=oa_lib_name,
        cells=((top_cell, labels),),
        access_dirs=access_dirs,
        manufacturing_grid_um=manufacturing_grid_um,
    )


def build_create_pins_batch_skill(
    *,
    oa_lib_name: str,
    cells: tuple[tuple[str, tuple[str, ...]], ...],
    access_dirs: tuple[str, ...] = DEFAULT_CADENCE_ACCESS_DIRS,
    manufacturing_grid_um: float = 0.005,
) -> str:
    """Build one SKILL snippet that creates OA pins across many imported cells."""

    if not cells:
        raise ValueError("Cadence pin-creation batch must include at least one cell")
    manufacturing_grid_um = float(manufacturing_grid_um)
    if not np.isfinite(manufacturing_grid_um) or manufacturing_grid_um <= 0.0:
        raise ValueError("manufacturing_grid_um must be finite and positive")
    manufacturing_grid_literal = f"{manufacturing_grid_um:.12g}"
    access_items = " ".join(f"\"{direction}\"" for direction in access_dirs)
    body = (
        "procedure(_xfmrBBoxContainsPoint(bbox pt)\n"
        "  let((ll ur x y)\n"
        "    ll = car(bbox)\n"
        "    ur = cadr(bbox)\n"
        "    x = car(pt)\n"
        "    y = cadr(pt)\n"
        "    x >= car(ll) && x <= car(ur) && y >= cadr(ll) && y <= cadr(ur)\n"
        "  )\n"
        ")\n"
        "\n"
        "procedure(_xfmrFindLabel(cv labelName)\n"
        "  let((found)\n"
        "    found = nil\n"
        "    foreach(fig cv~>shapes\n"
        "      when(fig~>objType == \"label\" && fig~>theLabel == labelName\n"
        "        found = fig\n"
        "      )\n"
        "    )\n"
        "    found\n"
        "  )\n"
        ")\n"
        "\n"
        "procedure(_xfmrFindPinFigure(cv labelFig)\n"
        "  let((found pt labelLpp labelLayer)\n"
        "    found = nil\n"
        "    pt = labelFig~>xy\n"
        "    labelLpp = labelFig~>lpp\n"
        "    labelLayer = car(labelLpp)\n"
        "    foreach(fig cv~>shapes\n"
        "      when(\n"
        "        fig != labelFig &&\n"
        "        member(fig~>objType '(\"rect\" \"polygon\" \"path\" \"pathSeg\")) &&\n"
        "        car(fig~>lpp) == labelLayer &&\n"
        "        cadr(fig~>lpp) == \"pin\" &&\n"
        "        _xfmrBBoxContainsPoint(fig~>bBox pt)\n"
        "        found = fig\n"
        "      )\n"
        "    )\n"
        "    found\n"
        "  )\n"
        ")\n"
        "\n"
        "procedure(_xfmrFindDrawingFigure(cv labelFig)\n"
        "  let((found pt labelLpp labelLayer)\n"
        "    found = nil\n"
        "    pt = labelFig~>xy\n"
        "    labelLpp = labelFig~>lpp\n"
        "    labelLayer = car(labelLpp)\n"
        "    foreach(fig cv~>shapes\n"
        "      when(\n"
        "        fig != labelFig &&\n"
        "        member(fig~>objType '(\"rect\" \"polygon\" \"path\" \"pathSeg\")) &&\n"
        "        car(fig~>lpp) == labelLayer &&\n"
        "        cadr(fig~>lpp) == \"drawing\" &&\n"
        "        _xfmrBBoxContainsPoint(fig~>bBox pt)\n"
        "        found = fig\n"
        "      )\n"
        "    )\n"
        "    unless(found\n"
        "      foreach(fig cv~>shapes\n"
        "        when(\n"
        "          fig != labelFig &&\n"
        "          member(fig~>objType '(\"rect\" \"polygon\" \"path\" \"pathSeg\")) &&\n"
        "          cadr(fig~>lpp) == \"drawing\" &&\n"
        "          _xfmrBBoxContainsPoint(fig~>bBox pt)\n"
        "          found = fig\n"
        "        )\n"
        "      )\n"
        "    )\n"
        "    found\n"
        "  )\n"
        ")\n"
        "\n"
        "procedure(_xfmrBBoxMinDim(fig)\n"
        "  let((bbox ll ur width height)\n"
        "    bbox = fig~>bBox\n"
        "    ll = car(bbox)\n"
        "    ur = cadr(bbox)\n"
        "    width = car(ur) - car(ll)\n"
        "    height = cadr(ur) - cadr(ll)\n"
        "    min(width height)\n"
        "  )\n"
        ")\n"
        "\n"
        "procedure(_xfmrPolygonCrossingsAtY(points y)\n"
        "  let((crossings pts p1 p2 x1 y1 x2 y2 x)\n"
        "    crossings = nil\n"
        "    pts = append(points list(car(points)))\n"
        "    while(cdr(pts)\n"
        "      p1 = car(pts)\n"
        "      p2 = cadr(pts)\n"
        "      x1 = car(p1)\n"
        "      y1 = cadr(p1)\n"
        "      x2 = car(p2)\n"
        "      y2 = cadr(p2)\n"
        "      when(\n"
        "        y1 != y2 && y >= min(y1 y2) && y < max(y1 y2)\n"
        "        x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)\n"
        "        crossings = cons(x crossings)\n"
        "      )\n"
        "      pts = cdr(pts)\n"
        "    )\n"
        "    sort(crossings 'lessp)\n"
        "  )\n"
        ")\n"
        "\n"
        "procedure(_xfmrPolygonCrossingsAtX(points x)\n"
        "  let((crossings pts p1 p2 x1 y1 x2 y2 y)\n"
        "    crossings = nil\n"
        "    pts = append(points list(car(points)))\n"
        "    while(cdr(pts)\n"
        "      p1 = car(pts)\n"
        "      p2 = cadr(pts)\n"
        "      x1 = car(p1)\n"
        "      y1 = cadr(p1)\n"
        "      x2 = car(p2)\n"
        "      y2 = cadr(p2)\n"
        "      when(\n"
        "        x1 != x2 && x >= min(x1 x2) && x < max(x1 x2)\n"
        "        y = y1 + (x - x1) * (y2 - y1) / (x2 - x1)\n"
        "        crossings = cons(y crossings)\n"
        "      )\n"
        "      pts = cdr(pts)\n"
        "    )\n"
        "    sort(crossings 'lessp)\n"
        "  )\n"
        ")\n"
        "\n"
        "procedure(_xfmrMinSpanFromCrossings(crossings)\n"
        "  let((xs span candidate)\n"
        "    xs = crossings\n"
        "    span = 0.0\n"
        "    while(cdr(xs)\n"
        "      candidate = cadr(xs) - car(xs)\n"
        "      when(candidate > 0.0 && (span <= 0.0 || candidate < span)\n"
        "        span = candidate\n"
        "      )\n"
        "      xs = cddr(xs)\n"
        "    )\n"
        "    span\n"
        "  )\n"
        ")\n"
        "\n"
        "procedure(_xfmrTraceWidthForFigure(fig pt)\n"
        "  let((spanX spanY)\n"
        "    if(fig~>objType == \"polygon\" then\n"
        "      spanX = _xfmrMinSpanFromCrossings(_xfmrPolygonCrossingsAtY(fig~>points cadr(pt)))\n"
        "      spanY = _xfmrMinSpanFromCrossings(_xfmrPolygonCrossingsAtX(fig~>points car(pt)))\n"
        "      if(spanX > 0.0 && spanY > 0.0 then\n"
        "        min(spanX spanY)\n"
        "      else\n"
        "        _xfmrBBoxMinDim(fig)\n"
        "      )\n"
        "    else\n"
        "      _xfmrBBoxMinDim(fig)\n"
        "    )\n"
        "  )\n"
        ")\n"
        "\n"
        "procedure(_xfmrTraceWidthForLabel(cv labelFig)\n"
        "  let((traceFig)\n"
        "    traceFig = _xfmrFindDrawingFigure(cv labelFig)\n"
        "    if(traceFig then\n"
        "      max(_xfmrTraceWidthForFigure(traceFig labelFig~>xy) 0.5)\n"
        "    else\n"
        "      0.5\n"
        "    )\n"
        "  )\n"
        ")\n"
        "\n"
        "procedure(_xfmrSnapToGrid(value grid)\n"
        "  grid * round(value / grid)\n"
        ")\n"
        "\n"
        "procedure(_xfmrGridCenteredContainedBBox(bbox pt grid)\n"
        "  let((ll ur center halfWidth halfHeight tolerance)\n"
        "    ll = car(bbox)\n"
        "    ur = cadr(bbox)\n"
        "    tolerance = grid / 1000.0\n"
        "    center = list(_xfmrSnapToGrid(car(pt) grid) _xfmrSnapToGrid(cadr(pt) grid))\n"
        "    unless(\n"
        "      abs(car(center) - car(pt)) <= tolerance &&\n"
        "      abs(cadr(center) - cadr(pt)) <= tolerance\n"
        "      error(sprintf(nil \"Imported pin label is off manufacturing grid: %L\" pt))\n"
        "    )\n"
        "    halfWidth = floor((min(car(center) - car(ll) car(ur) - car(center)) + tolerance) / grid) * grid\n"
        "    halfHeight = floor((min(cadr(center) - cadr(ll) cadr(ur) - cadr(center)) + tolerance) / grid) * grid\n"
        "    unless(halfWidth > 0.0 && halfHeight > 0.0\n"
        "      error(sprintf(nil \"Pin rectangle cannot be centered on manufacturing grid: bbox=%L label=%L\" bbox pt))\n"
        "    )\n"
        "    list(\n"
        "      list(car(center) - halfWidth cadr(center) - halfHeight)\n"
        "      list(car(center) + halfWidth cadr(center) + halfHeight)\n"
        "    )\n"
        "  )\n"
        ")\n"
        "\n"
        "procedure(_xfmrCreateLabelPinRect(cv labelFig pinFig manufacturingGrid)\n"
        "  let((pt halfWidth halfHeight ll ur pinLpp bbox)\n"
        "    pt = labelFig~>xy\n"
        "    bbox = nil\n"
        "    when(pinFig\n"
        "      bbox = pinFig~>bBox\n"
        "      dbDeleteObject(pinFig)\n"
        "    )\n"
        "    pinLpp = list(car(labelFig~>lpp) \"pin\")\n"
        "    if(bbox then\n"
        "      ll = car(bbox)\n"
        "      ur = cadr(bbox)\n"
        "    else\n"
        "      halfWidth = 0.25\n"
        "      halfHeight = _xfmrTraceWidthForLabel(cv labelFig) / 2.0\n"
        "      ll = list(car(pt) - halfWidth cadr(pt) - halfHeight)\n"
        "      ur = list(car(pt) + halfWidth cadr(pt) + halfHeight)\n"
        "    )\n"
        "    bbox = _xfmrGridCenteredContainedBBox(list(ll ur) pt manufacturingGrid)\n"
        "    ll = car(bbox)\n"
        "    ur = cadr(bbox)\n"
        "    dbCreateRect(cv pinLpp list(ll ur))\n"
        "  )\n"
        ")\n"
        "\n"
        "let((accessDir manufacturingGrid)\n"
        f"  accessDir = list({access_items})\n"
        f"  manufacturingGrid = {manufacturing_grid_literal}\n"
    )
    for top_cell, labels in cells:
        label_items = " ".join(f"\"{label}\"" for label in labels)
        body += (
            "  let((cv)\n"
            f"    cv = dbOpenCellViewByType(\"{oa_lib_name}\" \"{top_cell}\" \"layout\" \"\" \"a\")\n"
            "    unless(cv\n"
            f"      error(\"Could not open {oa_lib_name}/{top_cell}/layout\")\n"
            "    )\n"
            f"    foreach(port '({label_items})\n"
            "      let((label pinFig net term pin)\n"
            "        label = _xfmrFindLabel(cv port)\n"
            "        unless(label\n"
            "          error(sprintf(nil \"Missing imported label %s\" port))\n"
            "        )\n"
            "        pinFig = _xfmrFindPinFigure(cv label)\n"
            "        pinFig = _xfmrCreateLabelPinRect(cv label pinFig manufacturingGrid)\n"
            "        net = dbMakeNet(cv port)\n"
            "        term = dbFindTermByName(cv port)\n"
            "        unless(term\n"
            "          term = dbCreateTerm(net port \"inputOutput\")\n"
            "        )\n"
            "        pin = dbCreatePin(net pinFig port)\n"
            "        pin~>accessDir = accessDir\n"
            "      )\n"
            "    )\n"
            "    dbSave(cv)\n"
            "    dbClose(cv)\n"
            "  )\n"
        )
    body += ")\nexit()\n"
    return body


def build_strmin_command(
    *,
    cadence_install_root: str,
    workspace: ZeusCadenceWorkspace,
    input_gds_path: Path,
    top_cell: str,
    layer_map_path: str,
    tech_lib_name: str,
) -> list[str]:
    """Build the Cadence XStream-in command that imports the exported GDS into OA."""

    return [
        *cadence_binary(cadence_install_root, "strmin").split(),
        "-library",
        workspace.oa_lib_name,
        "-strmFile",
        str(Path(input_gds_path).resolve()),
        "-topCell",
        str(top_cell),
        "-view",
        "layout",
        "-attachTechFileOfLib",
        str(tech_lib_name),
        "-layerMap",
        str(layer_map_path),
        "-runDir",
        str(workspace.cadence_dir),
        "-logFile",
        str(workspace.strmin_log_path),
        "-summaryFile",
        str(workspace.strmin_summary_path),
    ]


def build_dbaccess_command(
    *,
    cadence_install_root: str,
    skill_path: Path,
) -> list[str]:
    """Build the dbAccess command that executes one SKILL file."""

    return [
        *cadence_binary(cadence_install_root, "dbAccess").split(),
        "-load",
        str(Path(skill_path).resolve()),
    ]


def build_strmout_command(
    *,
    cadence_install_root: str,
    workspace: ZeusCadenceWorkspace,
    top_cell: str,
    layer_map_path: str,
    tech_lib_name: str,
    cadence_pin_purpose: int,
) -> list[str]:
    """Build the Cadence XStream-out command that preserves OA pins into GDS."""

    return [
        *cadence_binary(cadence_install_root, "strmout").split(),
        "-library",
        workspace.oa_lib_name,
        "-topCell",
        str(top_cell),
        "-view",
        "layout",
        "-strmFile",
        str(workspace.streamout_gds_path),
        "-techLib",
        str(tech_lib_name),
        "-layerMap",
        str(layer_map_path),
        "-convertPin",
        "geometryAndText",
        "-pinAttNum",
        str(int(cadence_pin_purpose)),
        "-runDir",
        str(workspace.cadence_dir),
        "-logFile",
        str(workspace.strmout_log_path),
        "-summaryFile",
        str(workspace.strmout_summary_path),
    ]


def create_zeus_cadence_workspace(*, root_dir: Path, oa_lib_name: str, pdk_cds_lib: str) -> ZeusCadenceWorkspace:
    """Create the on-disk scratch workspace and write its local cds.lib."""

    root_dir = Path(root_dir).resolve()
    cadence_dir = root_dir / "cadence"
    skill_dir = cadence_dir / "skill"
    oa_lib_dir = cadence_dir / "oa_lib"
    streamout_dir = root_dir / "streamout"
    cadence_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.mkdir(parents=True, exist_ok=True)
    oa_lib_dir.mkdir(parents=True, exist_ok=True)
    streamout_dir.mkdir(parents=True, exist_ok=True)

    workspace = ZeusCadenceWorkspace(
        root_dir=root_dir,
        cadence_dir=cadence_dir,
        skill_dir=skill_dir,
        cds_lib_path=cadence_dir / "cds.lib",
        oa_lib_name=oa_lib_name,
        oa_lib_dir=oa_lib_dir,
        strmin_log_path=cadence_dir / "strmin.log",
        strmin_summary_path=cadence_dir / "strmin.sum",
        strmin_stdout_path=cadence_dir / "strmin.stdout.log",
        strmin_stderr_path=cadence_dir / "strmin.stderr.log",
        create_lib_skill_path=skill_dir / "create_oa_lib.il",
        create_lib_stdout_path=cadence_dir / "dbaccess_create_lib.stdout.log",
        create_lib_stderr_path=cadence_dir / "dbaccess_create_lib.stderr.log",
        create_pins_skill_path=skill_dir / "create_cadence_pins.il",
        create_pins_stdout_path=cadence_dir / "dbaccess_create_pins.stdout.log",
        create_pins_stderr_path=cadence_dir / "dbaccess_create_pins.stderr.log",
        strmout_log_path=cadence_dir / "strmout.log",
        strmout_summary_path=cadence_dir / "strmout.sum",
        strmout_stdout_path=cadence_dir / "strmout.stdout.log",
        strmout_stderr_path=cadence_dir / "strmout.stderr.log",
        streamout_dir=streamout_dir,
        streamout_gds_path=streamout_dir / "transformer_layout_cadpins.gds",
        streamout_preview_path=streamout_dir / "transformer_layout_preview.png",
        streamout_debug_preview_path=streamout_dir / "transformer_port_debug.png",
    )
    workspace.cds_lib_path.write_text(
        build_local_cds_lib(
            pdk_cds_lib=pdk_cds_lib,
            oa_lib_name=workspace.oa_lib_name,
            oa_lib_dir=workspace.oa_lib_dir,
        ),
        encoding="ascii",
    )
    return workspace


def _batch_workspace_dir(root_dir: Path, exports: tuple[CadenceRoundtripExport, ...]) -> Path:
    digest = sha256()
    for export in exports:
        digest.update(export.cache_key.encode("ascii"))
        digest.update(b"\n")
    return Path(root_dir) / "cadence_batches" / digest.hexdigest()[:16]


def _batch_top_cell_name(run_config: TransformerRunConfig, exports: tuple[CadenceRoundtripExport, ...]) -> str:
    joined = "_".join(export.cache_key[:8] for export in exports[:4])
    if len(exports) > 4:
        joined = f"{joined}_{len(exports)}"
    return sanitize_oa_lib_name(f"{run_config.emx.top_cell_prefix}_BATCH_{joined}")


def _write_batch_input_gds(
    *,
    layouts: tuple[TransformerLayoutExport, ...],
    out_path: Path,
    batch_top_cell: str,
) -> Path:
    import gdstk

    if not layouts:
        raise ValueError("Batch Cadence import requires at least one layout")

    merged_lib = None
    batch_cell = None
    next_origin_x = 0.0
    spacing_um = 50.0
    for layout in layouts:
        source_lib = gdstk.read_gds(str(layout.gds_path))
        source_top = next((cell for cell in source_lib.cells if cell.name == layout.top_cell), None)
        if source_top is None:
            raise ValueError(f"Top cell {layout.top_cell} not found in {layout.gds_path}")
        if merged_lib is None:
            merged_lib = gdstk.Library(unit=source_lib.unit, precision=source_lib.precision)
            batch_cell = merged_lib.new_cell(batch_top_cell)
        copied_top = source_top.copy(layout.top_cell)
        merged_lib.add(copied_top)
        bbox = copied_top.bounding_box()
        if bbox is None:
            origin = (next_origin_x, 0.0)
            width_um = 0.0
        else:
            (min_x, min_y), (max_x, _max_y) = bbox
            origin = (next_origin_x - float(min_x), -float(min_y))
            width_um = float(max_x) - float(min_x)
        batch_cell.add(gdstk.Reference(copied_top, origin=origin))
        next_origin_x += width_um + spacing_um

    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged_lib.write_gds(str(out_path))
    return out_path


def _run_logged_command(
    *,
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
    failure_label: str,
    fatal_stderr_markers: tuple[str, ...] = (),
) -> None:
    result = subprocess.run(
        command,
        cwd=str(Path(cwd).resolve()),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    fatal_markers = tuple(marker for marker in fatal_stderr_markers if marker in result.stderr)
    if result.returncode != 0 or fatal_markers:
        marker_detail = f"; fatal stderr markers={fatal_markers}" if fatal_markers else ""
        raise RuntimeError(
            f"{failure_label} failed with exit code {result.returncode}{marker_detail}. "
            f"See {stderr_path} for details."
        )


def _write_diff_analysis(*, work_dir: Path, differential_sparams, differential_z: np.ndarray) -> None:
    np.savez_compressed(
        Path(work_dir) / "differential_analysis.npz",
        freqs_hz=differential_sparams.freqs_hz,
        s_diff=differential_sparams.s_matrix,
        z_diff=differential_z,
    )


def _render_lumped_compare(*, work_dir: Path, run_config: TransformerRunConfig, differential_sparams) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from ..analysis.extraction import build_lumped_transformer_sparameters

    out_path = Path(work_dir) / "lumped_compare.png"
    lumped = build_lumped_transformer_sparameters(differential_sparams.freqs_hz, run_config.target)
    freq_ghz = differential_sparams.freqs_hz / 1e9
    fig, axes = plt.subplots(2, 1, figsize=(7.0, 6.0), dpi=180, sharex=True)
    for ax, (row, col, title) in zip(
        axes,
        ((0, 0, "Sdd11"), (1, 0, "Sdd21")),
    ):
        emx_mag = 20.0 * np.log10(np.maximum(np.abs(differential_sparams.s_matrix[:, row, col]), 1e-12))
        lumped_mag = 20.0 * np.log10(np.maximum(np.abs(lumped.s_matrix[:, row, col]), 1e-12))
        ax.plot(freq_ghz, emx_mag, label="EMX", linewidth=1.8)
        ax.plot(freq_ghz, lumped_mag, label="Lumped", linewidth=1.4, linestyle="--")
        ax.set_ylabel("Mag (dB)")
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
    axes[1].set_xlabel("Frequency (GHz)")
    axes[0].legend(loc="best")
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _prepare_emx_simulation(
    *,
    run_config: TransformerRunConfig,
    work_dir: Path,
    layout: TransformerLayoutExport,
    manifest: EMXLayoutManifest,
) -> tuple[EMXSimulation, list[str]]:
    """Construct the production solver arguments without creating files or children."""
    emx_dir = Path(work_dir) / "emx"
    sim = EMXSimulation(
        emx_binary=run_config.emx.emx_binary,
        process_file=run_config.emx.emx_process_file,
        top_cell=layout.top_cell,
        extra_args=list(run_config.emx.extra_args),
    )
    sim._emx_home = run_config.emx.emx_home
    sim._use_cadence_license_env = run_config.emx.use_cadence_license_env
    sim._license_file = run_config.emx.license_file
    sim._cdslmd_license_file = run_config.emx.cdslmd_license_file
    sim._skip_os_check = run_config.emx.skip_os_check
    sim._project_dir = emx_dir.resolve()
    frequency_points_hz = run_config.target.frequency_points_hz()
    f_start_hz = float(frequency_points_hz[0])
    f_stop_hz = float(frequency_points_hz[-1])
    sim.configure_solver(
        SolverType.FREQUENCY_DOMAIN,
        freq_start_hz=f_start_hz,
        freq_stop_hz=f_stop_hz,
        num_freq_points=len(frequency_points_hz),
        freq_points_hz=frequency_points_hz,
    )
    sim._layout_path = layout.gds_path
    sim._layout_manifest = manifest
    sim._top_cell = layout.top_cell
    command = sim._build_emx_command(layout.gds_path)
    return sim, command


def _run_emx(
    *,
    run_config: TransformerRunConfig,
    work_dir: Path,
    layout: TransformerLayoutExport,
    manifest: EMXLayoutManifest,
) -> dict[str, object]:
    emx_dir = Path(work_dir) / "emx"
    emx_dir.mkdir(parents=True, exist_ok=True)
    sim, command = _prepare_emx_simulation(
        run_config=run_config, work_dir=work_dir, layout=layout, manifest=manifest,
    )
    sim.create_project(emx_dir)
    (emx_dir / "emx_command.json").write_text(json.dumps(command, indent=2), encoding="utf-8")
    sim.connect()
    try:
        sim.run_solver()
    finally:
        sim.disconnect()

    raw_touchstone_path = sim._last_touchstone_path or (emx_dir / f"emx.s{len(manifest.ports)}p")
    if not raw_touchstone_path.exists():
        raise FileNotFoundError(f"EMX completed without producing Touchstone output: {raw_touchstone_path}")
    raw_result = load_touchstone(raw_touchstone_path)
    prepared = prepare_transformer_touchstone_result(
        raw_result=raw_result,
        target=run_config.target,
        manifest=manifest,
        raw_touchstone_path=raw_touchstone_path,
        out_dir=emx_dir,
        differential_port_pairs=run_config.emx.differential_port_pairs,
        ground_unused_s8p_ports=run_config.emx.ground_unused_s8p_ports,
    )
    metrics = prepared["metrics"]
    diff_result = prepared["differential_result"]
    diff_z = prepared["differential_z"]
    objective = score_transformer_result(
        target=run_config.target,
        metrics=metrics,
        differential_sparams=diff_result,
    )
    _write_diff_analysis(
        work_dir=Path(work_dir),
        differential_sparams=diff_result,
        differential_z=diff_z,
    )
    try:
        _render_lumped_compare(
            work_dir=Path(work_dir),
            run_config=run_config,
            differential_sparams=diff_result,
        )
    except Exception as exc:  # pragma: no cover - plotting stack is environment-dependent
        logger.warning("Skipping lumped compare render for %s: %s", work_dir, exc)

    return {
        "touchstone_path": str(prepared["touchstone_path"] or raw_touchstone_path),
        "raw_touchstone_path": str(prepared["raw_touchstone_path"] or raw_touchstone_path),
        "reduced_touchstone_path": (
            None
            if prepared["reduced_touchstone_path"] is None
            else str(prepared["reduced_touchstone_path"])
        ),
        "command": command,
        "metrics": metrics.as_dict(),
        "objective": objective.as_dict(),
        "num_freqs": int(diff_result.num_freqs),
        "raw_num_ports": int(raw_result.num_ports),
        "effective_num_ports": int(diff_result.num_ports if prepared["single_result"] is None else prepared["single_result"].num_ports),
    }


def run_transformer_zeus_cadence_roundtrip(
    *,
    run_config: TransformerRunConfig,
    geometry: TransformerSpec,
    root_dir: Path,
    stop_after: CadenceStopAfter = "emx",
    oa_lib_name: str | None = None,
    cadence_install_root: str = DEFAULT_CADENCE_INSTALL_ROOT,
    pdk_cds_lib: str = DEFAULT_PDK_CDS_LIB,
    tech_lib_name: str = DEFAULT_TECH_LIB,
    layer_map_path: str = DEFAULT_LAYER_MAP,
) -> dict[str, object]:
    """Run export -> strmin -> dbCreatePin -> strmout -> EMX inside one run directory."""

    from .evaluator import TransformerEmxEvaluator

    evaluator = TransformerEmxEvaluator(run_config=run_config, root_dir=Path(root_dir))
    export_result = evaluator.export_only(geometry)
    payload = export_result.summary_dict()
    payload["ok"] = False

    summary_path = export_result.work_dir / "summary_cadence_roundtrip.json"
    manifest = None

    try:
        if export_result.error is not None:
            raise RuntimeError(export_result.error)
        if export_result.layout is None:
            raise RuntimeError("Transformer export completed without producing layout artifacts")

        manifest = load_emx_layout_manifest(export_result.layout.manifest_path)
        pin_labels = collect_cadence_pin_labels(manifest)
        if not pin_labels:
            raise RuntimeError("Layout manifest did not contain any signal or ground labels for OA pin creation")
        if manifest.cadence_pin_purpose is None:
            raise RuntimeError(
                "Layout manifest does not declare cadence_pin_purpose, so OA pins cannot be exported for EMX"
            )

        workspace = create_zeus_cadence_workspace(
            root_dir=export_result.work_dir,
            oa_lib_name=sanitize_oa_lib_name(
                oa_lib_name if oa_lib_name is not None else f"xfmr_cadpins_{export_result.cache_key}"
            ),
            pdk_cds_lib=pdk_cds_lib,
        )
        workspace.create_lib_skill_path.write_text(
            build_create_library_skill(
                oa_lib_name=workspace.oa_lib_name,
                oa_lib_dir=workspace.oa_lib_dir,
                tech_lib_name=tech_lib_name,
            ),
            encoding="ascii",
        )
        workspace.create_pins_skill_path.write_text(
            build_create_pins_skill(
                oa_lib_name=workspace.oa_lib_name,
                top_cell=export_result.layout.top_cell,
                labels=pin_labels,
                manufacturing_grid_um=run_config.emx.foundry_layout.manufacturing_grid_um,
            ),
            encoding="ascii",
        )

        cadence_env = build_cadence_env(
            cadence_install_root=cadence_install_root,
            license_file=run_config.emx.license_file,
            cdslmd_license_file=run_config.emx.cdslmd_license_file,
        )
        create_lib_cmd = build_dbaccess_command(
            cadence_install_root=cadence_install_root,
            skill_path=workspace.create_lib_skill_path,
        )
        strmin_cmd = build_strmin_command(
            cadence_install_root=cadence_install_root,
            workspace=workspace,
            input_gds_path=export_result.layout.gds_path,
            top_cell=export_result.layout.top_cell,
            layer_map_path=layer_map_path,
            tech_lib_name=tech_lib_name,
        )
        create_pins_cmd = build_dbaccess_command(
            cadence_install_root=cadence_install_root,
            skill_path=workspace.create_pins_skill_path,
        )
        strmout_cmd = build_strmout_command(
            cadence_install_root=cadence_install_root,
            workspace=workspace,
            top_cell=export_result.layout.top_cell,
            layer_map_path=layer_map_path,
            tech_lib_name=tech_lib_name,
            cadence_pin_purpose=manifest.cadence_pin_purpose,
        )

        payload["cadence"] = {
            "workspace_dir": str(workspace.cadence_dir),
            "cds_lib": str(workspace.cds_lib_path),
            "oa_lib_name": workspace.oa_lib_name,
            "oa_lib_dir": str(workspace.oa_lib_dir),
            "top_cell": export_result.layout.top_cell,
            "pin_labels": list(pin_labels),
            "create_lib_command": create_lib_cmd,
            "strmin_command": strmin_cmd,
            "create_pins_command": create_pins_cmd,
            "strmout_command": strmout_cmd,
            "streamout_gds": str(workspace.streamout_gds_path),
            "streamout_preview": str(workspace.streamout_preview_path),
            "streamout_debug_preview": str(workspace.streamout_debug_preview_path),
        }

        if stop_after == "export":
            payload["ok"] = True
            payload["stop_after"] = stop_after
            return payload

        _run_logged_command(
            command=create_lib_cmd,
            cwd=workspace.cadence_dir,
            env=cadence_env,
            stdout_path=workspace.create_lib_stdout_path,
            stderr_path=workspace.create_lib_stderr_path,
            failure_label="Cadence OA library creation",
            fatal_stderr_markers=CADENCE_FATAL_STDERR_MARKERS,
        )
        _run_logged_command(
            command=strmin_cmd,
            cwd=workspace.cadence_dir,
            env=cadence_env,
            stdout_path=workspace.strmin_stdout_path,
            stderr_path=workspace.strmin_stderr_path,
            failure_label="Cadence strmin import",
            fatal_stderr_markers=CADENCE_FATAL_STDERR_MARKERS,
        )

        if stop_after == "strmin":
            payload["ok"] = True
            payload["stop_after"] = stop_after
            return payload

        _run_logged_command(
            command=create_pins_cmd,
            cwd=workspace.cadence_dir,
            env=cadence_env,
            stdout_path=workspace.create_pins_stdout_path,
            stderr_path=workspace.create_pins_stderr_path,
            failure_label="Cadence dbCreatePin pass",
            fatal_stderr_markers=CADENCE_FATAL_STDERR_MARKERS,
        )

        if stop_after == "dbcreatepin":
            payload["ok"] = True
            payload["stop_after"] = stop_after
            return payload

        _run_logged_command(
            command=strmout_cmd,
            cwd=workspace.cadence_dir,
            env=cadence_env,
            stdout_path=workspace.strmout_stdout_path,
            stderr_path=workspace.strmout_stderr_path,
            failure_label="Cadence strmout export",
            fatal_stderr_markers=CADENCE_FATAL_STDERR_MARKERS,
        )
        try:
            render_emx_layout_preview(
                workspace.streamout_gds_path,
                workspace.streamout_preview_path,
                manifest_path=export_result.layout.manifest_path,
            )
            render_emx_port_debug_panels(
                workspace.streamout_gds_path,
                workspace.streamout_debug_preview_path,
                manifest_path=export_result.layout.manifest_path,
            )
        except Exception as exc:  # pragma: no cover - plotting stack is environment-dependent
            logger.warning("Skipping Cadence round-trip preview rendering for %s: %s", workspace.streamout_gds_path, exc)
            workspace.streamout_preview_path.write_text(
                f"preview render failed: {exc}\n",
                encoding="utf-8",
            )
            workspace.streamout_debug_preview_path.write_text(
                f"debug preview render failed: {exc}\n",
                encoding="utf-8",
            )
        payload["artifacts"] = {
            "export_gds": str(export_result.layout.gds_path),
            "export_manifest": str(export_result.layout.manifest_path),
            "export_preview": str(export_result.layout.preview_path),
            "export_debug_preview": str(export_result.layout.debug_preview_path),
            "cadence_gds": str(workspace.streamout_gds_path),
            "cadence_preview": str(workspace.streamout_preview_path),
            "cadence_debug_preview": str(workspace.streamout_debug_preview_path),
            "top_cell": export_result.layout.top_cell,
        }

        if stop_after == "strmout":
            payload["ok"] = True
            payload["stop_after"] = stop_after
            return payload

        roundtrip_layout = TransformerLayoutExport(
            gds_path=workspace.streamout_gds_path,
            manifest_path=export_result.layout.manifest_path,
            preview_path=workspace.streamout_preview_path,
            debug_preview_path=workspace.streamout_debug_preview_path,
            top_cell=export_result.layout.top_cell,
        )
        payload.update(
            _run_emx(
                run_config=run_config,
                work_dir=export_result.work_dir,
                layout=roundtrip_layout,
                manifest=manifest,
            )
        )
        payload["ok"] = True
        payload["stop_after"] = stop_after
        return payload
    except Exception as exc:
        payload["error"] = str(exc)
        payload["ok"] = False
        return payload
    finally:
        summary_path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def run_transformer_zeus_cadence_roundtrip_batch(
    *,
    run_config: TransformerRunConfig,
    exports: tuple[CadenceRoundtripExport, ...],
    stop_after: CadenceStopAfter = "emx",
    oa_lib_name: str | None = None,
    cadence_install_root: str = DEFAULT_CADENCE_INSTALL_ROOT,
    pdk_cds_lib: str = DEFAULT_PDK_CDS_LIB,
    tech_lib_name: str = DEFAULT_TECH_LIB,
    layer_map_path: str = DEFAULT_LAYER_MAP,
) -> dict[str, dict[str, object]]:
    """Run one shared Cadence import/pin/export pass for many already-exported layouts."""

    payloads: dict[str, dict[str, object]] = {
        export.cache_key: {"ok": False, "cache_key": export.cache_key}
        for export in exports
    }

    def _write_batch_summaries() -> None:
        for export in exports:
            summary_path = export.work_dir / "summary_cadence_roundtrip.json"
            summary_path.write_text(
                json.dumps(payloads[export.cache_key], indent=2, default=_json_default),
                encoding="utf-8",
            )

    if not exports:
        return payloads

    manifests: dict[str, EMXLayoutManifest] = {}
    pin_labels_by_key: dict[str, tuple[str, ...]] = {}
    cadence_pin_purpose: int | None = None
    for export in exports:
        payload = payloads[export.cache_key]
        try:
            manifest = load_emx_layout_manifest(export.layout.manifest_path)
            pin_labels = collect_cadence_pin_labels(manifest)
            if not pin_labels:
                raise RuntimeError("Layout manifest did not contain any signal or ground labels for OA pin creation")
            if manifest.cadence_pin_purpose is None:
                raise RuntimeError(
                    "Layout manifest does not declare cadence_pin_purpose, so OA pins cannot be exported for EMX"
                )
            if cadence_pin_purpose is None:
                cadence_pin_purpose = manifest.cadence_pin_purpose
            elif cadence_pin_purpose != manifest.cadence_pin_purpose:
                raise RuntimeError("Batch Cadence round-trip requires a consistent cadence_pin_purpose across layouts")
            manifests[export.cache_key] = manifest
            pin_labels_by_key[export.cache_key] = pin_labels
            payload["artifacts"] = {
                "export_gds": str(export.layout.gds_path),
                "export_manifest": str(export.layout.manifest_path),
                "export_preview": str(export.layout.preview_path),
                "export_debug_preview": str(export.layout.debug_preview_path),
                "top_cell": export.layout.top_cell,
            }
        except Exception as exc:
            payload["error"] = str(exc)

    valid_exports = tuple(export for export in exports if export.cache_key in manifests)
    batch_workspace = None
    if valid_exports:
        try:
            batch_root_dir = _batch_workspace_dir(Path(exports[0].work_dir).parent.parent, valid_exports)
            batch_top_cell = _batch_top_cell_name(run_config, valid_exports)
            batch_workspace = create_zeus_cadence_workspace(
                root_dir=batch_root_dir,
                oa_lib_name=sanitize_oa_lib_name(
                    oa_lib_name if oa_lib_name is not None else f"xfmr_cadbatch_{batch_top_cell}"
                ),
                pdk_cds_lib=pdk_cds_lib,
            )
            batch_input_gds_path = batch_workspace.streamout_dir / "transformer_layout_batch_input.gds"
            _write_batch_input_gds(
                layouts=tuple(export.layout for export in valid_exports),
                out_path=batch_input_gds_path,
                batch_top_cell=batch_top_cell,
            )
            batch_workspace.create_lib_skill_path.write_text(
                build_create_library_skill(
                    oa_lib_name=batch_workspace.oa_lib_name,
                    oa_lib_dir=batch_workspace.oa_lib_dir,
                    tech_lib_name=tech_lib_name,
                ),
                encoding="ascii",
            )
            batch_workspace.create_pins_skill_path.write_text(
                build_create_pins_batch_skill(
                    oa_lib_name=batch_workspace.oa_lib_name,
                    cells=tuple(
                        (export.layout.top_cell, pin_labels_by_key[export.cache_key])
                        for export in valid_exports
                    ),
                    manufacturing_grid_um=run_config.emx.foundry_layout.manufacturing_grid_um,
                ),
                encoding="ascii",
            )

            cadence_env = build_cadence_env(
                cadence_install_root=cadence_install_root,
                license_file=run_config.emx.license_file,
                cdslmd_license_file=run_config.emx.cdslmd_license_file,
            )
            create_lib_cmd = build_dbaccess_command(
                cadence_install_root=cadence_install_root,
                skill_path=batch_workspace.create_lib_skill_path,
            )
            strmin_cmd = build_strmin_command(
                cadence_install_root=cadence_install_root,
                workspace=batch_workspace,
                input_gds_path=batch_input_gds_path,
                top_cell=batch_top_cell,
                layer_map_path=layer_map_path,
                tech_lib_name=tech_lib_name,
            )
            create_pins_cmd = build_dbaccess_command(
                cadence_install_root=cadence_install_root,
                skill_path=batch_workspace.create_pins_skill_path,
            )
            strmout_cmd = build_strmout_command(
                cadence_install_root=cadence_install_root,
                workspace=batch_workspace,
                top_cell=batch_top_cell,
                layer_map_path=layer_map_path,
                tech_lib_name=tech_lib_name,
                cadence_pin_purpose=int(cadence_pin_purpose),
            )

            for export in valid_exports:
                payload = payloads[export.cache_key]
                payload["cadence"] = {
                    "workspace_dir": str(batch_workspace.cadence_dir),
                    "cds_lib": str(batch_workspace.cds_lib_path),
                    "oa_lib_name": batch_workspace.oa_lib_name,
                    "oa_lib_dir": str(batch_workspace.oa_lib_dir),
                    "top_cell": export.layout.top_cell,
                    "batch_top_cell": batch_top_cell,
                    "pin_labels": list(pin_labels_by_key[export.cache_key]),
                    "create_lib_command": create_lib_cmd,
                    "strmin_command": strmin_cmd,
                    "create_pins_command": create_pins_cmd,
                    "strmout_command": strmout_cmd,
                    "streamout_gds": str(batch_workspace.streamout_gds_path),
                    "streamout_preview": str(export.work_dir / "streamout" / "transformer_layout_preview.png"),
                    "streamout_debug_preview": str(export.work_dir / "streamout" / "transformer_port_debug.png"),
                }

            if stop_after == "export":
                for export in valid_exports:
                    payloads[export.cache_key]["ok"] = True
                    payloads[export.cache_key]["stop_after"] = stop_after
                _write_batch_summaries()
                return payloads

            _run_logged_command(
                command=create_lib_cmd,
                cwd=batch_workspace.cadence_dir,
                env=cadence_env,
                stdout_path=batch_workspace.create_lib_stdout_path,
                stderr_path=batch_workspace.create_lib_stderr_path,
                failure_label="Cadence OA library creation",
                fatal_stderr_markers=CADENCE_FATAL_STDERR_MARKERS,
            )
            _run_logged_command(
                command=strmin_cmd,
                cwd=batch_workspace.cadence_dir,
                env=cadence_env,
                stdout_path=batch_workspace.strmin_stdout_path,
                stderr_path=batch_workspace.strmin_stderr_path,
                failure_label="Cadence strmin import",
                fatal_stderr_markers=CADENCE_FATAL_STDERR_MARKERS,
            )

            if stop_after == "strmin":
                for export in valid_exports:
                    payloads[export.cache_key]["ok"] = True
                    payloads[export.cache_key]["stop_after"] = stop_after
                _write_batch_summaries()
                return payloads

            _run_logged_command(
                command=create_pins_cmd,
                cwd=batch_workspace.cadence_dir,
                env=cadence_env,
                stdout_path=batch_workspace.create_pins_stdout_path,
                stderr_path=batch_workspace.create_pins_stderr_path,
                failure_label="Cadence dbCreatePin pass",
                fatal_stderr_markers=CADENCE_FATAL_STDERR_MARKERS,
            )

            if stop_after == "dbcreatepin":
                for export in valid_exports:
                    payloads[export.cache_key]["ok"] = True
                    payloads[export.cache_key]["stop_after"] = stop_after
                _write_batch_summaries()
                return payloads

            _run_logged_command(
                command=strmout_cmd,
                cwd=batch_workspace.cadence_dir,
                env=cadence_env,
                stdout_path=batch_workspace.strmout_stdout_path,
                stderr_path=batch_workspace.strmout_stderr_path,
                failure_label="Cadence strmout export",
                fatal_stderr_markers=CADENCE_FATAL_STDERR_MARKERS,
            )

            for export in valid_exports:
                preview_path = export.work_dir / "streamout" / "transformer_layout_preview.png"
                debug_preview_path = export.work_dir / "streamout" / "transformer_port_debug.png"
                preview_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    render_emx_layout_preview(
                        batch_workspace.streamout_gds_path,
                        preview_path,
                        manifest_path=export.layout.manifest_path,
                    )
                    render_emx_port_debug_panels(
                        batch_workspace.streamout_gds_path,
                        debug_preview_path,
                        manifest_path=export.layout.manifest_path,
                    )
                except Exception as exc:  # pragma: no cover - plotting stack is environment-dependent
                    logger.warning(
                        "Skipping batched Cadence round-trip preview rendering for %s: %s",
                        export.layout.top_cell,
                        exc,
                    )
                    preview_path.write_text(f"preview render failed: {exc}\n", encoding="utf-8")
                    debug_preview_path.write_text(f"debug preview render failed: {exc}\n", encoding="utf-8")
                payloads[export.cache_key]["artifacts"] = {
                    **(payloads[export.cache_key].get("artifacts") or {}),
                    "cadence_gds": str(batch_workspace.streamout_gds_path),
                    "cadence_preview": str(preview_path),
                    "cadence_debug_preview": str(debug_preview_path),
                    "top_cell": export.layout.top_cell,
                }

            if stop_after == "strmout":
                for export in valid_exports:
                    payloads[export.cache_key]["ok"] = True
                    payloads[export.cache_key]["stop_after"] = stop_after
                _write_batch_summaries()
                return payloads

            for export in valid_exports:
                payload = payloads[export.cache_key]
                try:
                    roundtrip_layout = TransformerLayoutExport(
                        gds_path=batch_workspace.streamout_gds_path,
                        manifest_path=export.layout.manifest_path,
                        preview_path=Path(str(payload["artifacts"]["cadence_preview"])),
                        debug_preview_path=Path(str(payload["artifacts"]["cadence_debug_preview"])),
                        top_cell=export.layout.top_cell,
                    )
                    payload.update(
                        _run_emx(
                            run_config=run_config,
                            work_dir=export.work_dir,
                            layout=roundtrip_layout,
                            manifest=manifests[export.cache_key],
                        )
                    )
                    payload["ok"] = True
                    payload["stop_after"] = stop_after
                except Exception as exc:
                    payload["error"] = str(exc)
                    payload["ok"] = False
        except Exception as exc:
            for export in valid_exports:
                payload = payloads[export.cache_key]
                payload["error"] = str(exc)
                payload["ok"] = False
    _write_batch_summaries()
    return payloads


def result_from_roundtrip_payload(
    *,
    payload: dict[str, object],
    geometry: TransformerSpec,
    run_config: TransformerRunConfig,
    work_dir: Path,
    cache_key: str,
    geometry_check: dict[str, object] | None,
) -> TransformerEvalResult:
    """Rebuild a structured evaluator result from the Zeus round-trip summary payload."""

    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
    cadence_artifacts = artifacts if isinstance(artifacts, dict) else {}

    layout = None
    cadence_gds = cadence_artifacts.get("cadence_gds")
    cadence_preview = cadence_artifacts.get("cadence_preview")
    cadence_debug = cadence_artifacts.get("cadence_debug_preview")
    export_manifest = cadence_artifacts.get("export_manifest")
    top_cell = cadence_artifacts.get("top_cell")
    if cadence_gds is not None and export_manifest is not None:
        layout = TransformerLayoutExport(
            gds_path=Path(str(cadence_gds)),
            manifest_path=Path(str(export_manifest)),
            preview_path=Path(str(cadence_preview)) if cadence_preview is not None else Path(str(cadence_gds)).with_suffix(".png"),
            debug_preview_path=Path(str(cadence_debug)) if cadence_debug is not None else Path(str(cadence_gds)).with_name("transformer_port_debug.png"),
            top_cell=str(top_cell) if top_cell is not None else str(run_config.emx.top_cell_prefix),
        )

    touchstone_path = None
    command = None
    metrics = None
    objective = None
    single_result = None
    diff_result = None
    diff_z = None
    error = None if bool(payload.get("ok", False)) else str(payload.get("error"))

    selected_touchstone = payload.get("reduced_touchstone_path") or payload.get("touchstone_path")
    if selected_touchstone is not None:
        touchstone_path = Path(str(selected_touchstone))
        command = [str(part) for part in payload.get("command", [])] if payload.get("command") is not None else None
        raw_result = load_touchstone(touchstone_path)
        manifest = None
        if export_manifest is not None:
            manifest = load_emx_layout_manifest(Path(str(export_manifest)))
        prepared = prepare_transformer_touchstone_result(
            raw_result=raw_result,
            target=run_config.target,
            manifest=manifest,
            raw_touchstone_path=touchstone_path,
            out_dir=touchstone_path.parent,
            differential_port_pairs=run_config.emx.differential_port_pairs,
            ground_unused_s8p_ports=run_config.emx.ground_unused_s8p_ports,
        )
        touchstone_path = Path(str(prepared["touchstone_path"] or touchstone_path))
        single_result = prepared["single_result"]
        metrics = prepared["metrics"]
        diff_result = prepared["differential_result"]
        diff_z = prepared["differential_z"]
        objective = score_transformer_result(
            target=run_config.target,
            metrics=metrics,
            differential_sparams=diff_result,
        )

    return TransformerEvalResult(
        cache_key=cache_key,
        geometry=geometry,
        target=run_config.target,
        layout=layout,
        metrics=metrics,
        objective=objective,
        single_ended_sparams=single_result,
        differential_sparams=diff_result,
        differential_z=diff_z,
        work_dir=Path(work_dir),
        touchstone_path=touchstone_path,
        command=command,
        geometry_check=geometry_check,
        error=error,
    )
