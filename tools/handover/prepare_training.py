#!/usr/bin/env python3
"""Relocate the frozen original-100K training entry without changing its recipe.

``prepare`` and ``check`` never train or evaluate. ``run`` and ``resume`` are
explicit, foreground, CPU-only training commands; no scheduler or EM simulator
is invoked. These are the historical minimum-Q experiments, not the later
symmetric-Q recipe. Recovery is at completed-stage boundaries, not live Adam/RNG.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


MOTHER_SHA = "fe94d15b0d083330fe7a64d3e059d083a5aa32d73394c85fc4b881e9904e68a2"
DATA = "research/dataset_size_811_20260921_v1/data/"
ADAPTER = "research/dataset_size_811_20260921_v1/train_dataset_sizes.py"
CORE = "research/datasize_20260921_v1/train_original100k_nested.py"
TRAINER = "models/original100k/trainer_runtime.py"
SUMMARY = "models/original100k/physical_feature_tandem_inverse_summary.json"
SPLITTER = "runtime/model_splitting.py"
CONTRACT = DATA + "DATASET_SIZE_CONTRACT.json"
PINS = {
    ADAPTER: "bd59a6b6d0188d67e1c0f5ae702ce7654e9c6a2f0870625054fd032a8580af55",
    CORE: "ddb5088d9143ac9bd9b870fd4bef1cbdff93ef36c7f0e0176db3fefac03bed49",
    TRAINER: "fbfda2023f862af2f0b185e41c4bf899f97cb79b0d0d763d20501c03525e8a4f",
    SUMMARY: "f05c4c0b3937bf9107f95d38d14679995aedc936071852ac7696f9838c5d43c1",
    SPLITTER: "9db4b7f1ed9435f73e95e49c558003bdaaf153579c5c44f04fb779aa31e0ef8a",
    CONTRACT: "809ee456a2ad6c96b52dd52750e9eae394d3b201a77f883251fe353e8153db3c",
    DATA + "DATASET_SIZE_INDICES.json": "1364e39c819736ea0de65b51c839b8e9eec77bb3dd998409e0306f5771500286",
    DATA + "QUALIFICATION.json": "14dc38a45cb6243aed7336f1214b3e027b73a276fd634b01f410f162928ac678",
}
ARMS = {"D10k": [8000, 1000, 1000], "D50k": [40000, 5000, 5000],
        "D100k": [78891, 9740, 11369]}
SCHEMA = "original100k_portable_training_preparation.v1"


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_new(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def checked(path: Path, expected: str) -> Path:
    path = path.resolve(strict=True)
    if not path.is_file() or digest(path) != expected:
        raise ValueError(f"Frozen identity mismatch: {path}")
    return path


def environment(work: Path, threads: int) -> dict[str, str]:
    env = dict(os.environ)
    env.update({"EUCAP_DATASET_CORE": str(work / CORE),
                "EUCAP_DATASIZE_TRAINER": str(work / TRAINER),
                "EUCAP_DATASIZE_SPLITTER": str(work / SPLITTER),
                "EUCAP_DATASIZE_SUMMARY": str(work / SUMMARY),
                "PYTHONDONTWRITEBYTECODE": "1"})
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[key] = str(threads)
    return env


def relocated_contract(original, work: Path, dataset: Path):
    """Change only location fields, preserving all scientific fields and hashes."""
    result = json.loads(json.dumps(original))
    for key, path in {"mother": dataset,
                      "indices": work / DATA / "DATASET_SIZE_INDICES.json",
                      "qualification": work / DATA / "QUALIFICATION.json",
                      "original_summary": work / SUMMARY,
                      "splitter": work / SPLITTER}.items():
        result[key]["path"] = str(path)
    return result


def prepare(assets: Path, dataset: Path, out: Path, threads: int):
    assets = assets.resolve(strict=True)
    dataset = checked(dataset, MOTHER_SHA)
    if out.is_symlink() or out.exists():
        raise FileExistsError(f"No-clobber output already exists: {out}")
    out = out.resolve()
    if out.is_relative_to(assets):
        raise ValueError("Use an output outside the private assets repository")
    sources = {name: checked(assets / name, sha) for name, sha in PINS.items()}
    # All source identities are checked before creating any output.
    out.mkdir(parents=True, exist_ok=False)
    work = out / "frozen"
    for name, source in sources.items():
        destination = work / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    contract = relocated_contract(read(sources[CONTRACT]), work, dataset)
    relocated = out / "RELOCATED_CONTRACT.json"
    write_new(relocated, contract)
    identity = {"schema": SCHEMA, "created_utc": datetime.now(timezone.utc).isoformat(),
                "source_pins": PINS, "source_csv": str(dataset),
                "source_csv_sha256": MOTHER_SHA, "contract_sha256": digest(relocated),
                "threads": threads, "counts_train_validation_test": ARMS,
                "training_started": False, "fresh_emx": "NOT_RUN",
                "recipe": "historical_seed20260713_minimum_Q_hard_feasible_decoder",
                "recovery": "completed_stages_only_not_live_optimizer_RNG"}
    write_new(out / "PREPARATION.json", identity)
    return {"status": "PREPARED_NOT_TRAINED", "out": str(out),
            "next": "Run check, then explicitly run one arm only if new training is intended"}


def validate_prepared(out: Path):
    out = out.resolve(strict=True)
    identity = read(out / "PREPARATION.json")
    if identity.get("schema") != SCHEMA or identity.get("source_pins") != PINS:
        raise ValueError("Prepared source identity does not match the frozen entry")
    if identity.get("source_csv_sha256") != MOTHER_SHA:
        raise ValueError("Wrong mother dataset identity")
    threads = identity.get("threads")
    if type(threads) is not int or not 1 <= threads <= 8:
        raise ValueError("Preparation CPU thread cap must be 1..8")
    work = out / "frozen"
    for name, sha in PINS.items():
        checked(work / name, sha)
    dataset = checked(Path(identity["source_csv"]), MOTHER_SHA)
    contract_path = checked(out / "RELOCATED_CONTRACT.json", identity["contract_sha256"])
    if read(contract_path) != relocated_contract(read(work / CONTRACT), work, dataset):
        raise ValueError("Only path relocation is permitted; recipe/split changes refused")
    return work, contract_path, environment(work, threads)


@contextlib.contextmanager
def local_environment(env):
    previous = dict(os.environ)
    previous_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)
        sys.dont_write_bytecode = previous_bytecode


def check(out: Path):
    work, contract_path, env = validate_prepared(out)
    with local_environment(env):
        spec = importlib.util.spec_from_file_location("handover_frozen_dataset_adapter", work / ADAPTER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        contract, indices, _ = module.load_contract(contract_path)
        runtime = module.c.load_runtime()
        # Parse only. Do not call runtime.main, matrix building, inference or training.
        args = runtime._parse_args(module.trainer_argv(contract, (work / ADAPTER).parent / "runs" / "check_not_executed"))
        return {"status": "PASS_OFFLINE_PREPARATION", "training_started": False,
                "inference_started": False, "fresh_emx": "NOT_RUN",
                "counts_train_validation_test": ARMS, "common_test_rows": len(indices["common_test"]),
                "seed": args.seed, "resume_boundary": "completed stages only",
                "dependencies": {"python": sys.version.split()[0], "numpy": module.np.__version__}}


def execute(out: Path, arm: str, mode: str):
    work, contract_path, env = validate_prepared(out)
    # The unchanged adapter provides no-clobber, exact-identity resume and flock.
    command = [sys.executable, "-B", str(work / ADAPTER), mode,
               "--contract", str(contract_path), "--arm", arm]
    return subprocess.run(command, env=env, check=True).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "check", "run", "resume"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--dataset", type=Path, help="Extracted unchanged original100k.csv")
    parser.add_argument("--arm", choices=ARMS)
    parser.add_argument("--threads", type=int, default=2, choices=range(1, 9))
    args = parser.parse_args(argv)
    if args.mode == "prepare":
        if args.assets is None or args.dataset is None:
            parser.error("prepare requires --assets and --dataset")
        result = prepare(args.assets, args.dataset, args.out, args.threads)
    elif args.mode == "check":
        result = check(args.out)
    else:
        if args.arm is None:
            parser.error("run/resume requires an explicit --arm")
        return execute(args.out, args.arm, args.mode)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
