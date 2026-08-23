#!/usr/bin/env python3
"""Patch explicit MARS EMX/Cadence paths into a dataset config.

The script is intentionally conservative: it only changes fields supplied on the
command line, writes a new config by default, and reports INCOMPLETE while any
required path placeholder remains. It does not guess install locations.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PATH_FIELDS = {
    "emx_binary": "emx_binary",
    "emx_process_file": "emx_process_file",
    "cadence_install_root": "cadence_install_root",
    "cadence_pdk_cds_lib": "cadence_pdk_cds_lib",
    "cadence_layer_map": "cadence_layer_map",
}

TEXT_FIELDS = {
    "cadence_tech_lib": "cadence_tech_lib",
    "license_file": "license_file",
    "cdslmd_license_file": "cdslmd_license_file",
    "execution_mode": "execution_mode",
}

REQUIRED_FIELDS = (
    "emx_binary",
    "emx_process_file",
    "cadence_install_root",
    "cadence_pdk_cds_lib",
    "cadence_tech_lib",
    "cadence_layer_map",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    load_yaml, dump_yaml, yaml_backend = _yaml_io()

    config_path = Path(args.config).expanduser().resolve()
    out_config = _output_path(config_path, args)
    summary_path = Path(args.summary).expanduser().resolve() if args.summary else out_config.with_suffix(out_config.suffix + ".path_patch_summary.json")

    raw = load_yaml(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise SystemExit(f"Config top level must be a mapping: {config_path}")
    before = dict(raw.get("emx") or {})
    emx = dict(before)
    changes = _apply_updates(emx, args)
    raw["emx"] = emx

    remaining_placeholders = _remaining_placeholders(emx)
    missing_paths = _missing_paths(emx) if args.check_paths else []
    overall_status = "FAIL" if missing_paths else ("INCOMPLETE" if remaining_placeholders else "PASS")

    out_config.parent.mkdir(parents=True, exist_ok=True)
    out_config.write_text(dump_yaml(raw), encoding="utf-8")
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "yaml_backend": yaml_backend,
        "input_config": str(config_path),
        "out_config": str(out_config),
        "in_place": bool(args.in_place),
        "check_paths": bool(args.check_paths),
        "changed_fields": changes,
        "remaining_placeholder_fields": remaining_placeholders,
        "missing_path_fields": missing_paths,
        "required_fields": list(REQUIRED_FIELDS),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"config={out_config}")
    print(f"summary={summary_path}")
    print(f"changed_fields={','.join(changes) if changes else 'none'}")
    if remaining_placeholders:
        print(f"remaining_placeholder_fields={','.join(remaining_placeholders)}")
    if missing_paths:
        print(f"missing_path_fields={','.join(missing_paths)}")
    return 2 if overall_status != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", help="Input run-config YAML")
    parser.add_argument("--out-config", help="Output config path. Required unless --in-place is used")
    parser.add_argument("--summary", help="Output path-patch summary JSON")
    parser.add_argument("--in-place", action="store_true", help="Overwrite the input config")
    parser.add_argument("--check-paths", action="store_true", help="Require path-valued fields to exist on this filesystem")
    parser.add_argument("--no-fail-exit", action="store_true")
    for field in sorted(PATH_FIELDS):
        parser.add_argument(f"--{field.replace('_', '-')}")
    for field in sorted(TEXT_FIELDS):
        parser.add_argument(f"--{field.replace('_', '-')}")
    return parser.parse_args(argv)


def _output_path(config_path: Path, args: argparse.Namespace) -> Path:
    if args.in_place and args.out_config:
        raise SystemExit("Use either --in-place or --out-config, not both")
    if args.in_place:
        return config_path
    if not args.out_config:
        raise SystemExit("--out-config is required unless --in-place is used")
    return Path(args.out_config).expanduser().resolve()


def _apply_updates(emx: dict[str, Any], args: argparse.Namespace) -> list[str]:
    changed: list[str] = []
    for field in (*PATH_FIELDS, *TEXT_FIELDS):
        value = getattr(args, field)
        if value is None:
            continue
        emx[field] = str(Path(value).expanduser()) if field in PATH_FIELDS else str(value)
        changed.append(field)
    return changed


def _remaining_placeholders(emx: dict[str, Any]) -> list[str]:
    return [field for field in REQUIRED_FIELDS if _looks_like_placeholder(emx.get(field))]


def _missing_paths(emx: dict[str, Any]) -> list[str]:
    missing = []
    for field in PATH_FIELDS:
        value = emx.get(field)
        if _looks_like_placeholder(value):
            continue
        if not Path(str(value)).expanduser().exists():
            missing.append(field)
    return missing


def _looks_like_placeholder(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    upper = text.upper()
    return "REPLACE" in upper or text.startswith("/REPLACE/")


def _yaml_io() -> tuple[Callable[[str], Any], Callable[[dict[str, Any]], str], str]:
    try:
        import yaml
    except ImportError:
        return _parse_simple_yaml_mapping, _dump_simple_yaml_mapping, "internal-simple-yaml"
    return yaml.safe_load, lambda raw: yaml.safe_dump(raw, sort_keys=False, allow_unicode=False), "pyyaml"


def _parse_simple_yaml_mapping(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any] | list[Any], dict[str, Any] | None, str | None]] = [(-1, root, None, None)]
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if stripped.startswith("-"):
            item = stripped[1:].strip()
            if not item or ":" in item:
                raise ValueError("Internal YAML fallback only supports scalar list items")
            if stack[-1][0] == indent and isinstance(stack[-1][1], dict) and not stack[-1][1]:
                _, _, parent, key = stack[-1]
                if parent is None or key is None:
                    raise ValueError(f"Cannot attach YAML list item: {raw_line!r}")
                values: list[Any] = []
                parent[key] = values
                stack[-1] = (indent, values, parent, key)
            while stack and indent < stack[-1][0]:
                stack.pop()
            if not stack or not isinstance(stack[-1][1], list):
                raise ValueError(f"Cannot parse YAML list item: {raw_line!r}")
            stack[-1][1].append(_parse_simple_yaml_scalar(item))
            continue
        if ":" not in stripped:
            raise ValueError(f"Cannot parse YAML line: {raw_line!r}")
        key, value = stripped.split(":", 1)
        key = key.strip().strip("'\"")
        value = value.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if not isinstance(parent, dict):
            raise ValueError(f"Cannot add mapping entry below YAML list: {raw_line!r}")
        if not value:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child, parent, key))
        else:
            parent[key] = _parse_simple_yaml_scalar(value)
    return root


def _parse_simple_yaml_scalar(value: str) -> Any:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    lower = text.lower()
    if lower in {"null", "none", "~"}:
        return None
    if lower == "true":
        return True
    if lower == "false":
        return False
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_simple_yaml_scalar(part.strip()) for part in _split_inline_list(inner)]
    try:
        if any(marker in text for marker in (".", "e", "E")):
            return float(text)
        return int(text)
    except ValueError:
        return text


def _split_inline_list(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue
        if char == ",":
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current or text.endswith(","):
        parts.append("".join(current).strip())
    return parts


def _dump_simple_yaml_mapping(raw: dict[str, Any]) -> str:
    lines: list[str] = []
    _append_yaml_mapping(lines, raw, indent=0)
    return "\n".join(lines) + "\n"


def _append_yaml_mapping(lines: list[str], raw: dict[str, Any], *, indent: int) -> None:
    prefix = " " * indent
    for key, value in raw.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            _append_yaml_mapping(lines, value, indent=indent + 2)
        else:
            lines.append(f"{prefix}{key}: {_format_simple_yaml_scalar(value)}")


def _format_simple_yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_simple_yaml_scalar(item) for item in value) + "]"
    text = str(value)
    if not text:
        return '""'
    if any(char.isspace() for char in text) or text[0] in "-!@#$%^&*{}[]:,>|`":
        return json.dumps(text)
    return text


if __name__ == "__main__":
    raise SystemExit(main())
