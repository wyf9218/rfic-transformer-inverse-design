#!/usr/bin/env python3
"""Independent, result-blind hostile QA for the frozen v10 candidate.

This harness deliberately imports only the frozen candidate and its local
synthetic fixture constructors.  It does not import, read, or execute any
business result, MARS controller, EMX flow, production entry, or signal path.
The assertions and attack matrix were independently specified before the v10
candidate was frozen.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable


HERE = Path(__file__).resolve().parent
PHYSICAL = HERE.parent
CANDIDATE = (
    PHYSICAL
    / "transport_runtime_layout_builder_v10_prepared_20260822T205518Z"
)
V9_PREPARED = (
    PHYSICAL
    / "transport_runtime_layout_builder_v9_prepared_20260822T202248Z"
)
V9_QA = (
    PHYSICAL
    / "independent_transport_runtime_layout_builder_v9_qa_20260822T203210Z"
)
V8_QA = (
    PHYSICAL
    / "independent_transport_runtime_layout_builder_v8_qa_20260822T200141Z"
)


class FixtureInterrupt(RuntimeError):
    """A local-only interruption used to leave a recoverable transaction."""


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


candidate_tests = load_module(
    "independent_v10_candidate_fixture_helpers",
    CANDIDATE / "test_transport_runtime_layout_builder_v10_synthetic.py",
)
builder = candidate_tests.builder


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def strict_json(path: Path) -> dict[str, Any]:
    value = builder.strict_json_loads(path.read_bytes())
    if not isinstance(value, dict):
        raise AssertionError(f"not an object: {path.name}")
    return value


def index_records(path: Path) -> dict[str, str]:
    records: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if match is None or match.group(2) in records:
            raise AssertionError(f"noncanonical SHA256SUMS: {path}")
        records[match.group(2)] = match.group(1)
    return records


def rejected(call: Callable[[], Any]) -> bool:
    try:
        call()
    except builder.BuildError:
        return True
    return False


def exception_type(call: Callable[[], Any]) -> tuple[str | None, Any]:
    try:
        value = call()
    except BaseException as exc:
        return type(exc).__name__, None
    return None, value


def terminal_observation(
    path: Path,
    expected_kind: str,
    authorization_sha256: str,
    decision_id: str,
) -> dict[str, Any]:
    result = {
        "exists": False,
        "strict": False,
        "status_exact": False,
        "mode_0444": False,
        "nlink_1": False,
        "canonical_bytes": False,
    }
    if not path.is_file() or path.is_symlink():
        return result
    journal_fd = os.open(
        path.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        value, _, identity, raw = builder.read_canonical_terminal_at(
            journal_fd, builder.JOURNAL_NAMES["terminal"]
        )
        if expected_kind == "pass":
            builder.validate_build_pass_receipt_schema(value)
            status_exact = value.get("status") == builder.BUILD_PASS_RECEIPT_STATUS
        else:
            builder.validate_fail_terminal_schema(
                value,
                authorization_sha256=authorization_sha256,
                decision_id=decision_id,
                terminal_publication_method=(
                    builder.SYNTHETIC_TERMINAL_PUBLICATION_METHOD
                ),
            )
            status_exact = (
                value.get("status")
                == "FAIL_CLOSED_RESULT_FREE_TRANSPORT_ATTEMPT"
            )
        canonical = raw == builder.canonical_json_bytes(value)
        mode = identity.mode == 0o444
        nlink = identity.nlink == 1
        result.update({
            "exists": True,
            "strict": status_exact and canonical and mode and nlink,
            "status_exact": status_exact,
            "mode_0444": mode,
            "nlink_1": nlink,
            "canonical_bytes": canonical,
        })
    finally:
        os.close(journal_fd)
    return result


def thaw_and_unlink_aliases(root: Path, aliases: list[Path]) -> None:
    for alias in aliases:
        with contextlib.suppress(OSError):
            if alias.is_symlink():
                alias.unlink()
    candidate_tests.thaw(root)


def make_case(
    temp: Path,
    site: Path,
    label: str,
    *,
    collision: bool,
) -> tuple[
    Path, Path, Path, Path, dict[str, Any], Path, str, list[str]
]:
    case = temp / label
    case.mkdir()
    evidence = case / "evidence"
    evidence.mkdir()
    synthetic_parent = case / "synthetic-parent"
    canonical_parent = case / "canonical-parent"
    synthetic_parent.mkdir()
    canonical_parent.mkdir()
    final_root = synthetic_parent / "final"
    if collision:
        final_root.mkdir()
        (final_root / "collision-marker.txt").write_text(
            "independent no-clobber collision\n", encoding="utf-8"
        )
    decision_id = f"independent-v10-{label}"
    auth, auth_path, auth_sha, argv = candidate_tests.make_authorization(
        evidence, site, final_root, decision_id
    )
    return (
        case,
        synthetic_parent,
        canonical_parent,
        final_root,
        auth,
        auth_path,
        auth_sha,
        argv,
    )


def execute_fixture(
    auth: dict[str, Any], auth_path: Path, auth_sha: str, argv: list[str],
    **kwargs: Any,
) -> dict[str, Any]:
    return candidate_tests.execute_synthetic(
        auth,
        auth_path,
        auth_sha,
        rename_impl=candidate_tests.synthetic_rename_noreplace,
        observed_argv=argv,
        linux_integration="NOT_RUN_NON_LINUX",
        **kwargs,
    )


def post_link_case(
    temp: Path,
    site: Path,
    side: str,
    phase: str,
    terminal_kind: str,
    attack: str,
) -> dict[str, Any]:
    label = f"postlink-{attack}-{side}-{phase}-{terminal_kind}"
    # A first-attempt FAIL uses an immediate no-clobber collision.  A recovery
    # FAIL first leaves durable INTENT+staging without a terminal, then adds a
    # collision so the second attempt reaches recover_existing's FAIL publisher.
    collision = terminal_kind == "fail" and phase == "first"
    (
        case,
        synthetic_parent,
        canonical_parent,
        final_root,
        auth,
        auth_path,
        auth_sha,
        argv,
    ) = make_case(temp, site, label, collision=collision)
    journal_name = Path(auth["journal"]["directory"]).name
    moved_parent = case / f"{side}-held-parent-moved"
    target = synthetic_parent if side == "synthetic" else canonical_parent
    old_expected = builder.EXPECTED_FINAL_ROOT
    original_revalidate = builder.SyntheticProductionPathLease.revalidate
    state: dict[str, Any] = {
        "mutated": False,
        "hook_calls": 0,
        "post_mutation_revalidate_calls": 0,
        "terminal_visible_before_mutation": False,
    }
    aliases: list[Path] = []

    def instrumented_revalidate(lease: Any) -> None:
        if state["mutated"]:
            state["post_mutation_revalidate_calls"] += 1
        return original_revalidate(lease)

    def mutate_after_link() -> None:
        state["hook_calls"] += 1
        state["terminal_visible_before_mutation"] = (
            synthetic_parent / journal_name
            / builder.JOURNAL_NAMES["terminal"]
        ).is_file()
        target.rename(moved_parent)
        if attack == "replacement":
            target.mkdir()
        else:
            target.symlink_to(moved_parent, target_is_directory=True)
            aliases.append(target)
        state["mutated"] = True

    builder.EXPECTED_FINAL_ROOT = canonical_parent / "ROOT"
    builder.SyntheticProductionPathLease.revalidate = instrumented_revalidate
    preparation_ok = phase == "first"
    preparation_error: str | None = None
    try:
        if phase == "recovery" and terminal_kind == "pass":
            preparation_error, _ = exception_type(lambda: execute_fixture(
                auth,
                auth_path,
                auth_sha,
                argv,
                terminal_mid_write_hook=lambda: (_ for _ in ()).throw(
                    FixtureInterrupt("pass terminal mid-write interruption")
                ),
            ))
            journal = synthetic_parent / journal_name
            preparation_ok = (
                preparation_error == "FixtureInterrupt"
                and final_root.is_dir()
                and (journal / builder.JOURNAL_NAMES["intent"]).is_file()
                and not (journal / builder.JOURNAL_NAMES["terminal"]).exists()
            )
        elif phase == "recovery" and terminal_kind == "fail":
            preparation_error, _ = exception_type(lambda: execute_fixture(
                auth,
                auth_path,
                auth_sha,
                argv,
                before_rename_hook=lambda: (_ for _ in ()).throw(
                    FixtureInterrupt("interrupt before staging publication")
                ),
                fail_terminal_mid_write_hook=lambda: (_ for _ in ()).throw(
                    FixtureInterrupt("fail terminal mid-write interruption")
                ),
            ))
            journal = synthetic_parent / journal_name
            final_root.mkdir()
            (final_root / "recovery-collision-marker.txt").write_text(
                "independent recovery no-clobber collision\n",
                encoding="utf-8",
            )
            preparation_ok = (
                preparation_error == "FixtureInterrupt"
                and final_root.is_dir()
                and (journal / builder.JOURNAL_NAMES["intent"]).is_file()
                and (journal / builder.JOURNAL_NAMES["staging"]).is_dir()
                and not (journal / builder.JOURNAL_NAMES["terminal"]).exists()
            )

        kwargs: dict[str, Any]
        if terminal_kind == "pass":
            kwargs = {
                "terminal_after_link_before_dir_fsync_hook": mutate_after_link
            }
        else:
            kwargs = {
                "fail_terminal_after_link_before_dir_fsync_hook": mutate_after_link
            }
        error_type, returned = exception_type(lambda: execute_fixture(
            auth, auth_path, auth_sha, argv, **kwargs
        ))
        origin_parent = (
            moved_parent if side == "synthetic" else synthetic_parent
        )
        terminal_path = (
            origin_parent / journal_name / builder.JOURNAL_NAMES["terminal"]
        )
        terminal = terminal_observation(
            terminal_path, terminal_kind, auth_sha, auth["decision_id"]
        )
        no_clobber = False
        if terminal["strict"]:
            before = terminal_path.read_bytes()
            journal_fd = os.open(
                terminal_path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                no_clobber = rejected(lambda: candidate_tests.synthetic_terminal_publish(
                    journal_fd,
                    builder.JOURNAL_NAMES["terminal"],
                    builder.canonical_json_bytes({
                        "schema": "independent-v10-must-not-clobber"
                    }),
                )) and terminal_path.read_bytes() == before
            finally:
                os.close(journal_fd)
        if attack == "alias":
            named_target_rejected = rejected(
                lambda: builder.open_directory_path(target)
            )
        else:
            named_target_rejected = (
                target.is_dir()
                and target.stat().st_ino != moved_parent.stat().st_ino
            )
        if side == "synthetic":
            named_terminal_not_authoritative = (
                attack == "alias"
                or not (
                    target / journal_name / builder.JOURNAL_NAMES["terminal"]
                ).exists()
            )
        else:
            named_terminal_not_authoritative = True
        return {
            "preparation_ok": preparation_ok,
            "preparation_error_type": preparation_error,
            "hook_called_once": state["hook_calls"] == 1,
            "terminal_visible_before_mutation": state[
                "terminal_visible_before_mutation"
            ],
            "post_mutation_revalidation_observed": (
                state["post_mutation_revalidate_calls"] >= 1
            ),
            "rejected_without_return": (
                error_type == "BuildError" and returned is None
            ),
            "error_type": error_type,
            "strict_terminal": terminal["strict"],
            "terminal_no_clobber": no_clobber,
            "named_target_refused_or_distinct": named_target_rejected,
            "named_terminal_not_authoritative": named_terminal_not_authoritative,
        }
    finally:
        builder.SyntheticProductionPathLease.revalidate = original_revalidate
        builder.EXPECTED_FINAL_ROOT = old_expected
        thaw_and_unlink_aliases(case, aliases)


def durability_window_case(
    temp: Path,
    site: Path,
    side: str,
    terminal_kind: str,
    trigger: str,
) -> dict[str, Any]:
    label = f"durability-{trigger}-{side}-{terminal_kind}"
    (
        case,
        synthetic_parent,
        canonical_parent,
        _,
        auth,
        auth_path,
        auth_sha,
        argv,
    ) = make_case(temp, site, label, collision=terminal_kind == "fail")
    journal_name = Path(auth["journal"]["directory"]).name
    target = synthetic_parent if side == "synthetic" else canonical_parent
    moved_parent = case / f"{side}-held-parent-moved"
    parent_info = synthetic_parent.stat()
    parent_identity = (parent_info.st_dev, parent_info.st_ino)
    old_expected = builder.EXPECTED_FINAL_ROOT
    original_revalidate = builder.SyntheticProductionPathLease.revalidate
    original_fsync = builder.os.fsync
    state = {
        "mutated": False,
        "trigger_calls": 0,
        "post_mutation_revalidate_calls": 0,
        "terminal_visible_before_mutation": False,
    }

    def instrumented_revalidate(lease: Any) -> None:
        if state["mutated"]:
            state["post_mutation_revalidate_calls"] += 1
        return original_revalidate(lease)

    def mutate() -> None:
        state["terminal_visible_before_mutation"] = (
            synthetic_parent / journal_name
            / builder.JOURNAL_NAMES["terminal"]
        ).is_file()
        target.rename(moved_parent)
        target.mkdir()
        state["mutated"] = True
        state["trigger_calls"] += 1

    def fsync_then_attack(fd: int) -> None:
        info = os.fstat(fd)
        original_fsync(fd)
        if state["mutated"]:
            return
        terminal_visible = (
            synthetic_parent / journal_name
            / builder.JOURNAL_NAMES["terminal"]
        ).is_file()
        if not terminal_visible:
            return
        if trigger == "journal":
            journal_path = synthetic_parent / journal_name
            jinfo = journal_path.stat()
            if (info.st_dev, info.st_ino) == (jinfo.st_dev, jinfo.st_ino):
                mutate()
        elif (info.st_dev, info.st_ino) == parent_identity:
            mutate()

    builder.EXPECTED_FINAL_ROOT = canonical_parent / "ROOT"
    builder.SyntheticProductionPathLease.revalidate = instrumented_revalidate
    builder.os.fsync = fsync_then_attack
    try:
        error_type, returned = exception_type(lambda: execute_fixture(
            auth, auth_path, auth_sha, argv
        ))
    finally:
        builder.os.fsync = original_fsync
    try:
        origin_parent = (
            moved_parent if side == "synthetic" else synthetic_parent
        )
        terminal_path = (
            origin_parent / journal_name / builder.JOURNAL_NAMES["terminal"]
        )
        terminal = terminal_observation(
            terminal_path, terminal_kind, auth_sha, auth["decision_id"]
        )
        return {
            "trigger_called_once": state["trigger_calls"] == 1,
            "terminal_visible_before_mutation": state[
                "terminal_visible_before_mutation"
            ],
            "post_mutation_revalidation_observed": (
                state["post_mutation_revalidate_calls"] >= 1
            ),
            "rejected_without_return": (
                error_type == "BuildError" and returned is None
            ),
            "error_type": error_type,
            "strict_terminal": terminal["strict"],
        }
    finally:
        builder.SyntheticProductionPathLease.revalidate = original_revalidate
        builder.EXPECTED_FINAL_ROOT = old_expected
        candidate_tests.thaw(case)


def existing_terminal_crash_recovery_case(
    temp: Path,
    site: Path,
    terminal_kind: str,
) -> dict[str, Any]:
    label = f"existing-crash-recovery-{terminal_kind}"
    (
        case,
        synthetic_parent,
        canonical_parent,
        _,
        auth,
        auth_path,
        auth_sha,
        argv,
    ) = make_case(temp, site, label, collision=terminal_kind == "fail")
    journal_name = Path(auth["journal"]["directory"]).name
    terminal_path = (
        synthetic_parent / journal_name / builder.JOURNAL_NAMES["terminal"]
    )
    old_expected = builder.EXPECTED_FINAL_ROOT
    builder.EXPECTED_FINAL_ROOT = canonical_parent / "ROOT"
    try:
        kwargs = {
            (
                "terminal_after_link_before_dir_fsync_hook"
                if terminal_kind == "pass"
                else "fail_terminal_after_link_before_dir_fsync_hook"
            ): lambda: (_ for _ in ()).throw(
                FixtureInterrupt("after-link crash")
            )
        }
        first_error, first_value = exception_type(lambda: execute_fixture(
            auth, auth_path, auth_sha, argv, **kwargs
        ))
        first_terminal = terminal_observation(
            terminal_path, terminal_kind, auth_sha, auth["decision_id"]
        )
        before = terminal_path.read_bytes() if first_terminal["strict"] else b""
        second_error, second_value = exception_type(lambda: execute_fixture(
            auth, auth_path, auth_sha, argv
        ))
        after = terminal_path.read_bytes() if terminal_path.is_file() else b""
        if terminal_kind == "pass":
            recovery_exact = (
                second_error is None
                and isinstance(second_value, dict)
                and second_value.get("status") == "ALREADY_TERMINAL_PASS"
            )
        else:
            recovery_exact = second_error == "BuildError" and second_value is None
        second_terminal = terminal_observation(
            terminal_path, terminal_kind, auth_sha, auth["decision_id"]
        )
        return {
            "first_interrupted_or_fail_closed": (
                first_value is None
                and first_error in {"FixtureInterrupt", "BuildError"}
            ),
            "first_strict_terminal": first_terminal["strict"],
            "second_recovery_exact": recovery_exact,
            "terminal_bytes_unchanged": before == after and bool(before),
            "second_strict_terminal": second_terminal["strict"],
        }
    finally:
        builder.EXPECTED_FINAL_ROOT = old_expected
        candidate_tests.thaw(case)


def existing_terminal_mutation_case(
    temp: Path,
    site: Path,
    terminal_kind: str,
    side: str,
    attack: str,
) -> dict[str, Any]:
    label = f"existing-{attack}-{side}-{terminal_kind}"
    (
        case,
        synthetic_parent,
        canonical_parent,
        _,
        auth,
        auth_path,
        auth_sha,
        argv,
    ) = make_case(temp, site, label, collision=terminal_kind == "fail")
    journal_name = Path(auth["journal"]["directory"]).name
    original_terminal_path = (
        synthetic_parent / journal_name / builder.JOURNAL_NAMES["terminal"]
    )
    moved_parent = case / f"{side}-held-parent-moved"
    target = synthetic_parent if side == "synthetic" else canonical_parent
    old_expected = builder.EXPECTED_FINAL_ROOT
    original_fsync = builder.os.fsync
    original_revalidate = builder.SyntheticProductionPathLease.revalidate
    parent_info = synthetic_parent.stat()
    parent_identity = (parent_info.st_dev, parent_info.st_ino)
    state = {
        "mutated": False,
        "trigger_calls": 0,
        "post_mutation_revalidate_calls": 0,
    }
    aliases: list[Path] = []

    def instrumented_revalidate(lease: Any) -> None:
        if state["mutated"]:
            state["post_mutation_revalidate_calls"] += 1
        return original_revalidate(lease)

    def fsync_then_attack(fd: int) -> None:
        info = os.fstat(fd)
        original_fsync(fd)
        if (
            not state["mutated"]
            and (info.st_dev, info.st_ino) == parent_identity
        ):
            target.rename(moved_parent)
            if attack == "replacement":
                target.mkdir()
            else:
                target.symlink_to(moved_parent, target_is_directory=True)
                aliases.append(target)
            state["mutated"] = True
            state["trigger_calls"] += 1

    builder.EXPECTED_FINAL_ROOT = canonical_parent / "ROOT"
    try:
        first_error, first_value = exception_type(lambda: execute_fixture(
            auth, auth_path, auth_sha, argv
        ))
        if terminal_kind == "pass":
            first_exact = (
                first_error is None
                and isinstance(first_value, dict)
                and first_value.get("status")
                == "PASS_RESULT_FREE_TRANSPORT_RUNTIME_LAYOUT_ONLY"
            )
        else:
            first_exact = first_error == "BuildError" and first_value is None
        before = original_terminal_path.read_bytes()
        first_terminal = terminal_observation(
            original_terminal_path,
            terminal_kind,
            auth_sha,
            auth["decision_id"],
        )
        builder.SyntheticProductionPathLease.revalidate = instrumented_revalidate
        builder.os.fsync = fsync_then_attack
        try:
            second_error, second_value = exception_type(lambda: execute_fixture(
                auth, auth_path, auth_sha, argv
            ))
        finally:
            builder.os.fsync = original_fsync
        origin_parent = (
            moved_parent if side == "synthetic" else synthetic_parent
        )
        terminal_path = (
            origin_parent / journal_name / builder.JOURNAL_NAMES["terminal"]
        )
        after = terminal_path.read_bytes() if terminal_path.is_file() else b""
        second_terminal = terminal_observation(
            terminal_path, terminal_kind, auth_sha, auth["decision_id"]
        )
        return {
            "first_terminal_exact": first_exact and first_terminal["strict"],
            "mutation_after_parent_fsync_called_once": (
                state["trigger_calls"] == 1
            ),
            "post_mutation_revalidation_observed": (
                state["post_mutation_revalidate_calls"] >= 1
            ),
            "second_rejected_without_return": (
                second_error == "BuildError" and second_value is None
            ),
            "terminal_bytes_unchanged": before == after and bool(before),
            "terminal_still_strict": second_terminal["strict"],
        }
    finally:
        builder.os.fsync = original_fsync
        builder.SyntheticProductionPathLease.revalidate = original_revalidate
        builder.EXPECTED_FINAL_ROOT = old_expected
        thaw_and_unlink_aliases(case, aliases)


def minimal_auth(synthetic_parent: Path) -> dict[str, Any]:
    return {
        "final_root": os.fspath(synthetic_parent / "final"),
        "journal": {
            "directory": os.fspath(synthetic_parent / ".journal"),
            "parent_path": os.fspath(synthetic_parent),
        },
    }


def path_separation_cases(temp: Path) -> dict[str, bool]:
    results: dict[str, bool] = {}
    old_expected = builder.EXPECTED_FINAL_ROOT
    root = temp / "path-separation"
    root.mkdir()
    try:
        # Synthetic parent lexically contains canonical parent.
        a = root / "synthetic-broad"
        c = a / "canonical-child"
        c.mkdir(parents=True)
        builder.EXPECTED_FINAL_ROOT = c / "ROOT"
        results["synthetic_broad_parent_rejected"] = rejected(
            lambda: builder._reject_synthetic_production_paths(minimal_auth(a))
        )

        # Canonical parent lexically contains synthetic parent.
        c = root / "canonical-broad"
        a = c / "synthetic-child"
        a.mkdir(parents=True)
        builder.EXPECTED_FINAL_ROOT = c / "ROOT"
        results["canonical_broad_parent_rejected"] = rejected(
            lambda: builder._reject_synthetic_production_paths(minimal_auth(a))
        )

        equal = root / "equal"
        equal.mkdir()
        builder.EXPECTED_FINAL_ROOT = equal / "ROOT"
        results["equal_parent_rejected"] = rejected(
            lambda: builder._reject_synthetic_production_paths(
                minimal_auth(equal)
            )
        )

        # Nofollow rejection for a symlink on either named side.
        real_s = root / "real-synthetic"
        real_c = root / "real-canonical"
        real_s.mkdir()
        real_c.mkdir()
        alias_s = root / "alias-synthetic"
        alias_c = root / "alias-canonical"
        alias_s.symlink_to(real_s, target_is_directory=True)
        alias_c.symlink_to(real_c, target_is_directory=True)
        builder.EXPECTED_FINAL_ROOT = real_c / "ROOT"
        results["synthetic_symlink_rejected"] = rejected(
            lambda: builder._reject_synthetic_production_paths(
                minimal_auth(alias_s)
            )
        )
        builder.EXPECTED_FINAL_ROOT = alias_c / "ROOT"
        results["canonical_symlink_rejected"] = rejected(
            lambda: builder._reject_synthetic_production_paths(
                minimal_auth(real_s)
            )
        )

        # Real disjoint roots are a positive control.
        disjoint_s = root / "disjoint-synthetic"
        disjoint_c = root / "disjoint-canonical"
        disjoint_s.mkdir()
        disjoint_c.mkdir()
        builder.EXPECTED_FINAL_ROOT = disjoint_c / "ROOT"
        lease = builder._reject_synthetic_production_paths(
            minimal_auth(disjoint_s)
        )
        try:
            lease.revalidate()
            results["real_disjoint_positive_control"] = True
        finally:
            lease.close()

        # Once admitted, either named parent replacement must be rejected.
        for side in ("synthetic", "canonical"):
            s = root / f"lease-{side}-synthetic"
            c = root / f"lease-{side}-canonical"
            s.mkdir()
            c.mkdir()
            builder.EXPECTED_FINAL_ROOT = c / "ROOT"
            lease = builder._reject_synthetic_production_paths(minimal_auth(s))
            target = s if side == "synthetic" else c
            moved = root / f"lease-{side}-moved"
            try:
                target.rename(moved)
                target.mkdir()
                results[f"post_admission_{side}_replacement_rejected"] = rejected(
                    lease.revalidate
                )
            finally:
                lease.close()
    finally:
        builder.EXPECTED_FINAL_ROOT = old_expected
        candidate_tests.thaw(root)
    return results


def terminal_no_clobber_cases(temp: Path) -> dict[str, bool]:
    root = temp / "terminal-no-clobber"
    root.mkdir()
    journal = root / "journal"
    journal.mkdir()
    journal_fd = os.open(
        journal,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    results: dict[str, bool] = {}
    try:
        name = builder.JOURNAL_NAMES["terminal"]
        first = builder.canonical_json_bytes({
            "schema": "independent-terminal-first",
            "status": "STRICT_FIRST_BYTES",
        })
        second = builder.canonical_json_bytes({
            "schema": "independent-terminal-second",
            "status": "MUST_NOT_OVERWRITE",
        })
        candidate_tests.synthetic_terminal_publish(journal_fd, name, first)
        path = journal / name
        before = path.read_bytes()
        info = path.stat()
        results["complete_second_publish_rejected"] = rejected(
            lambda: candidate_tests.synthetic_terminal_publish(
                journal_fd, name, second
            )
        )
        after_info = path.stat()
        results["complete_first_bytes_mode_nlink_unchanged"] = (
            path.read_bytes() == before == first
            and stat.S_IMODE(info.st_mode) == 0o444
            and info.st_nlink == 1
            and stat.S_IMODE(after_info.st_mode) == 0o444
            and after_info.st_nlink == 1
        )

        second_journal = root / "midwrite-journal"
        second_journal.mkdir()
        second_fd = os.open(
            second_journal,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            error, value = exception_type(
                lambda: candidate_tests.synthetic_terminal_publish(
                    second_fd,
                    name,
                    first,
                    mid_write_hook=lambda: (_ for _ in ()).throw(
                        FixtureInterrupt("mid-write")
                    ),
                )
            )
            results["midwrite_interrupted"] = (
                error == "FixtureInterrupt" and value is None
            )
            results["midwrite_no_terminal_or_temporary_residue"] = (
                not (second_journal / name).exists()
                and not any(
                    item.name.startswith(".TERMINAL.v10.complete.")
                    for item in second_journal.iterdir()
                )
            )
        finally:
            os.close(second_fd)
    finally:
        os.close(journal_fd)
        candidate_tests.thaw(root)
    return results


def candidate_closure_checks() -> tuple[dict[str, bool], dict[str, Any]]:
    expected = {
        "AUTHOR_BUILDER_V10_SYNTHETIC_OUTPUT.json",
        "AUTHOR_COMPILE_V10_OUTPUT.json",
        "AUTHOR_DOUBLE_RUN_V10_VALIDATION.json",
        "AUTHOR_SMOKE_V10_SYNTHETIC_OUTPUT.json",
        "AUTHOR_V10_WIP_FAILURES.json",
        "BUNDLE_MANIFEST.json",
        "PREPARED_RESULT_FREE_RECEIPT.json",
        "SHA256SUMS",
        "TRANSPORT_RUNTIME_LAYOUT_BUILDER_V10_PREPARED_CN.md",
        "TRANSPORT_RUNTIME_LAYOUT_CONTRACT_V10.json",
        "UPSTREAM_EVIDENCE_BINDINGS_V10.json",
        "build_result_free_transport_runtime_v10.py",
        "result_free_runtime_smoke_v10.py",
        "test_result_free_runtime_smoke_v10_synthetic.py",
        "test_transport_runtime_layout_builder_v10_synthetic.py",
    }
    observed = {item.name for item in CANDIDATE.iterdir()}
    records = index_records(CANDIDATE / "SHA256SUMS")
    manifest = strict_json(CANDIDATE / "BUNDLE_MANIFEST.json")
    receipt = strict_json(CANDIDATE / "PREPARED_RESULT_FREE_RECEIPT.json")
    contract = strict_json(CANDIDATE / "TRANSPORT_RUNTIME_LAYOUT_CONTRACT_V10.json")
    payload = {item["relative_path"] for item in manifest["files"]}
    checks = {
        "candidate_exact15": observed == expected,
        "candidate_index_exact14": (
            set(records) == expected - {"SHA256SUMS"}
            and len(records) == 14
        ),
        "candidate_all14_sha_match": all(
            sha256(CANDIDATE / name) == digest
            for name, digest in records.items()
        ),
        "candidate_manifest_exact12": (
            len(payload) == 12
            and payload
            == expected
            - {
                "BUNDLE_MANIFEST.json",
                "PREPARED_RESULT_FREE_RECEIPT.json",
                "SHA256SUMS",
            }
        ),
        "candidate_manifest_payload_sha_size_match": all(
            sha256(CANDIDATE / item["relative_path"]) == item["sha256"]
            and (CANDIDATE / item["relative_path"]).stat().st_size
            == item["size_bytes"]
            for item in manifest["files"]
        ),
        "candidate_root_mode_0555": (
            stat.S_IMODE(CANDIDATE.stat().st_mode) == 0o555
            and CANDIDATE.is_dir()
            and not CANDIDATE.is_symlink()
        ),
        "candidate_files_0444_regular_nlink1": all(
            item.is_file()
            and not item.is_symlink()
            and stat.S_IMODE(item.stat().st_mode) == 0o444
            and item.stat().st_nlink == 1
            for item in CANDIDATE.iterdir()
        ),
        "candidate_cache_zero": not any(
            item.name == "__pycache__" or item.suffix == ".pyc"
            for item in CANDIDATE.rglob("*")
        ),
        "candidate_prepared_receipt_scope_only": (
            receipt["status"]
            == "PASS_PREPARED_ONLY_AWAITING_FRESH_INDEPENDENT_QA_NOT_AUTHORIZED"
            and receipt["next_legal_action"]
            == "FRESH_INDEPENDENT_RESULT_BLIND_QA_OF_THIS_EXACT_FROZEN_PACKAGE_ONLY"
            and all(value is False for value in receipt["authority"].values())
        ),
        "candidate_manifest_authority_all_false": all(
            value is False for value in manifest["authority"].values()
        ),
        "candidate_contract_not_authority": (
            contract["is_authorization"] is False
            and contract["is_execution_authority"] is False
        ),
        "candidate_expected_index_sha": (
            sha256(CANDIDATE / "SHA256SUMS")
            == "e2073343323a19a153843079dd8b787c97929c02b6c9c4152fd03e0e2799acb2"
        ),
    }
    details = {
        "top_level_count": len(observed),
        "indexed_count": len(records),
        "payload_count": len(payload),
        "sha256_index_sha256": sha256(CANDIDATE / "SHA256SUMS"),
        "prepared_receipt_sha256": sha256(
            CANDIDATE / "PREPARED_RESULT_FREE_RECEIPT.json"
        ),
        "bundle_manifest_sha256": sha256(CANDIDATE / "BUNDLE_MANIFEST.json"),
        "builder_sha256": sha256(
            CANDIDATE / "build_result_free_transport_runtime_v10.py"
        ),
        "test_sha256": sha256(
            CANDIDATE / "test_transport_runtime_layout_builder_v10_synthetic.py"
        ),
        "smoke_sha256": sha256(CANDIDATE / "result_free_runtime_smoke_v10.py"),
        "smoke_test_sha256": sha256(
            CANDIDATE / "test_result_free_runtime_smoke_v10_synthetic.py"
        ),
    }
    return checks, details


def predecessor_checks() -> tuple[dict[str, bool], dict[str, Any]]:
    upstream = strict_json(CANDIDATE / "UPSTREAM_EVIDENCE_BINDINGS_V10.json")
    v9_binding = upstream["direct_v9_prepared"]
    v9_qa_binding = upstream["v9_independent_negative_qa"]
    v9_index = index_records(V9_PREPARED / "SHA256SUMS")
    v9_qa_index = index_records(V9_QA / "SHA256SUMS")
    v9_receipt = strict_json(V9_QA / "INDEPENDENT_QA_RECEIPT.json")
    v9_closure = strict_json(V9_QA / "PACKAGE_CLOSURE_QA.json")
    v8_receipt = strict_json(V8_QA / "INDEPENDENT_QA_RECEIPT.json")
    all10 = v9_qa_binding["all10_files"]
    v9_top = {item.name for item in V9_PREPARED.iterdir()}
    v9_qa_top = {item.name for item in V9_QA.iterdir()}
    checks = {
        "v9_prepared_exact15_index14": (
            len(v9_top) == 15
            and len(v9_index) == 14
            and set(v9_index) == v9_top - {"SHA256SUMS"}
        ),
        "v9_prepared_all14_sha_match": all(
            sha256(V9_PREPARED / name) == digest
            for name, digest in v9_index.items()
        ),
        "v9_prepared_direct_binding_exact": (
            v9_binding["directory"] == V9_PREPARED.name
            and v9_binding["sha256_index_sha256"]
            == sha256(V9_PREPARED / "SHA256SUMS")
            and v9_binding["builder_sha256"]
            == sha256(V9_PREPARED / "build_result_free_transport_runtime_v9.py")
            and v9_binding["test_sha256"]
            == sha256(V9_PREPARED / "test_transport_runtime_layout_builder_v9_synthetic.py")
            and v9_binding["smoke_sha256"]
            == sha256(V9_PREPARED / "result_free_runtime_smoke_v9.py")
            and v9_binding["smoke_test_sha256"]
            == sha256(V9_PREPARED / "test_result_free_runtime_smoke_v9_synthetic.py")
            and v9_binding["bundle_manifest_sha256"]
            == sha256(V9_PREPARED / "BUNDLE_MANIFEST.json")
            and v9_binding["prepared_receipt_sha256"]
            == sha256(V9_PREPARED / "PREPARED_RESULT_FREE_RECEIPT.json")
        ),
        "v9_formal_qa_exact10_index8": (
            len(v9_qa_top) == 10
            and set(all10) == v9_qa_top
            and len(v9_qa_index) == 8
            and set(v9_qa_index)
            == v9_qa_top
            - {"SHA256SUMS", "INDEPENDENT_QA_RECEIPT.json"}
        ),
        "v9_formal_qa_all10_deep_sha_match": all(
            sha256(V9_QA / name) == digest for name, digest in all10.items()
        ),
        "v9_formal_qa_index8_sha_match": all(
            sha256(V9_QA / name) == digest
            for name, digest in v9_qa_index.items()
        ),
        "v9_p1_finding_exact": (
            v9_receipt["finding_counts"]
            == {"P0": 0, "P1": 1, "P2": 0, "P3": 0}
            and len(v9_receipt["findings"]) == 1
            and v9_receipt["findings"][0]["id"] == "P1-V9-001"
            and v9_receipt["status"]
            == "NO_GO_INDEPENDENT_QA_RESULT_FREE_PACKAGE_ONLY"
            and v9_closure["finding_counts"]
            == {"P0": 0, "P1": 1, "P2": 0, "P3": 0}
        ),
        "v9_formal_qa_authority_all_false": all(
            value is False for value in v9_receipt["authority"].values()
        ),
        "v8_receipt_and_index_deep_bound": (
            upstream["retained_negative_predecessors"]["v8_receipt_sha256"]
            == sha256(V8_QA / "INDEPENDENT_QA_RECEIPT.json")
            and upstream["retained_negative_predecessors"]
            ["v8_sha256_index_sha256"]
            == sha256(V8_QA / "SHA256SUMS")
            and sum(v8_receipt["finding_counts"].values()) >= 1
            and all(value is False for value in v8_receipt["authority"].values())
        ),
        "v10_contract_names_p1_v9_001": (
            strict_json(CANDIDATE / "TRANSPORT_RUNTIME_LAYOUT_CONTRACT_V10.json")
            ["triggering_negative_qa"]["findings"][0]["id"]
            == "P1-V9-001"
        ),
    }
    details = {
        "v9_prepared_index_sha256": sha256(V9_PREPARED / "SHA256SUMS"),
        "v9_qa_index_sha256": sha256(V9_QA / "SHA256SUMS"),
        "v9_qa_receipt_sha256": sha256(
            V9_QA / "INDEPENDENT_QA_RECEIPT.json"
        ),
        "v9_qa_closure_sha256": sha256(V9_QA / "PACKAGE_CLOSURE_QA.json"),
        "v8_qa_receipt_sha256": sha256(
            V8_QA / "INDEPENDENT_QA_RECEIPT.json"
        ),
        "v8_qa_index_sha256": sha256(V8_QA / "SHA256SUMS"),
        "v9_finding_counts": v9_receipt["finding_counts"],
        "v9_finding_id": v9_receipt["findings"][0]["id"],
    }
    return checks, details


def compile_and_author_double_runs() -> tuple[dict[str, bool], dict[str, Any]]:
    sources = [
        "build_result_free_transport_runtime_v10.py",
        "test_transport_runtime_layout_builder_v10_synthetic.py",
        "result_free_runtime_smoke_v10.py",
        "test_result_free_runtime_smoke_v10_synthetic.py",
    ]
    compile_rounds: list[list[str]] = []
    for _ in range(2):
        passed: list[str] = []
        for name in sources:
            data = (CANDIDATE / name).read_bytes()
            compile(data, os.fspath(CANDIDATE / name), "exec")
            passed.append(name)
        compile_rounds.append(passed)

    run_details: dict[str, Any] = {}
    run_checks: dict[str, bool] = {
        "compile_4_of_4_twice": compile_rounds == [sources, sources]
    }
    for label, script, frozen, expected_count in (
        (
            "builder",
            "test_transport_runtime_layout_builder_v10_synthetic.py",
            "AUTHOR_BUILDER_V10_SYNTHETIC_OUTPUT.json",
            101,
        ),
        (
            "smoke",
            "test_result_free_runtime_smoke_v10_synthetic.py",
            "AUTHOR_SMOKE_V10_SYNTHETIC_OUTPUT.json",
            107,
        ),
    ):
        runs = [
            subprocess.run(
                [sys.executable, "-B", script],
                cwd=CANDIDATE,
                capture_output=True,
                timeout=240,
                check=False,
            )
            for _ in range(2)
        ]
        parsed: dict[str, Any] = {}
        parse_ok = False
        try:
            parsed = json.loads(runs[0].stdout)
            parse_ok = isinstance(parsed, dict)
        except BaseException:
            pass
        exact_counts = (
            parse_ok
            and parsed.get("pass_count") == expected_count
            and parsed.get("fail_count") == 0
            and len(parsed.get("checks", {})) == expected_count
            and all(parsed.get("checks", {}).values())
        )
        checks_for_label = {
            "both_rc_zero": all(item.returncode == 0 for item in runs),
            "both_stderr_empty": all(item.stderr == b"" for item in runs),
            "stdout_byte_identical": runs[0].stdout == runs[1].stdout,
            "matches_frozen_output": (
                runs[0].stdout == (CANDIDATE / frozen).read_bytes()
            ),
            "exact_pass_count": exact_counts,
        }
        for name, value in checks_for_label.items():
            run_checks[f"author_{label}_{name}"] = value
        run_details[label] = {
            "stdout_sha256": hashlib.sha256(runs[0].stdout).hexdigest(),
            "pass_count": parsed.get("pass_count") if parse_ok else None,
            "fail_count": parsed.get("fail_count") if parse_ok else None,
            "check_count": (
                len(parsed.get("checks", {})) if parse_ok else None
            ),
        }
    run_checks["candidate_cache_zero_after_compile_and_children"] = not any(
        item.name == "__pycache__" or item.suffix == ".pyc"
        for item in CANDIDATE.rglob("*")
    )
    run_details["compile"] = {"rounds": 2, "sources_per_round": 4}
    return run_checks, run_details


def static_terminal_order_checks() -> tuple[dict[str, bool], dict[str, Any]]:
    source = (
        CANDIDATE / "build_result_free_transport_runtime_v10.py"
    ).read_text(encoding="utf-8")
    lines = source.splitlines()
    starts = [
        index for index, line in enumerate(lines)
        if "terminal_evidence = terminal_publish_impl(" in line
    ]
    ordered = []
    for start in starts:
        tail = lines[start:start + 35]
        try:
            journal_at = next(
                index for index, line in enumerate(tail)
                if line.strip() == "os.fsync(journal_fd)"
            )
            parent_at = next(
                index for index, line in enumerate(tail)
                if line.strip() == "os.fsync(parent_fd)"
            )
            revalidate_at = next(
                index for index, line in enumerate(tail)
                if line.strip() == "revalidate_terminal_continuity()"
            )
            ordered.append(journal_at < parent_at < revalidate_at)
        except StopIteration:
            ordered.append(False)
    recovery = inspect.getsource(builder.recover_existing).splitlines()
    recovery_pairs = sum(
        recovery[index].strip() == "os.fsync(parent_fd)"
        and recovery[index + 1].strip() == "revalidate_terminal_continuity()"
        for index in range(len(recovery) - 1)
    )
    checks = {
        "exact_four_terminal_publish_calls": len(starts) == 4,
        "all_four_journal_parent_fsync_then_continuity": (
            len(ordered) == 4 and all(ordered)
        ),
        "existing_terminal_recovery_has_five_durable_revalidations": (
            recovery_pairs >= 5
        ),
        "terminal_continuity_revalidates_synthetic_and_production": (
            source.count("def revalidate_terminal_continuity()") == 2
            and source.count("revalidate_synthetic_separation()") >= 15
            and source.count("revalidate_production_trust(auth, trust_lease)")
            >= 12
        ),
        "no_overwrite_or_signal_primitive": (
            "os.replace(" not in source
            and "shutil.rmtree" not in source
            and "os.kill" not in source
            and "SIGCONT" not in source
        ),
    }
    return checks, {
        "terminal_publish_call_count": len(starts),
        "ordered_call_count": sum(ordered),
        "existing_terminal_recovery_durable_revalidation_pairs": recovery_pairs,
    }


def all_values_true(value: dict[str, Any]) -> bool:
    return bool(value) and all(item is True for item in value.values())


def main() -> int:
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}

    closure_checks, closure_details = candidate_closure_checks()
    checks.update({f"closure::{key}": value for key, value in closure_checks.items()})
    details["candidate_closure"] = closure_details

    predecessor_gate, predecessor_details = predecessor_checks()
    checks.update({
        f"predecessor::{key}": value
        for key, value in predecessor_gate.items()
    })
    details["predecessor_closure"] = predecessor_details

    static_checks, static_details = static_terminal_order_checks()
    checks.update({f"static::{key}": value for key, value in static_checks.items()})
    details["static_terminal_order"] = static_details

    compile_checks, compile_details = compile_and_author_double_runs()
    checks.update({f"rerun::{key}": value for key, value in compile_checks.items()})
    details["compile_and_author_double_runs"] = compile_details

    with tempfile.TemporaryDirectory(
        prefix="independent-v10-result-blind-qa-"
    ) as raw:
        temp = Path(raw).resolve()
        site = temp / "site-packages"
        candidate_tests.make_site(site)

        post_link_matrix: dict[str, dict[str, Any]] = {}
        for attack in ("replacement", "alias"):
            for phase in ("first", "recovery"):
                for terminal_kind in ("pass", "fail"):
                    for side in ("synthetic", "canonical"):
                        key = f"{attack}::{phase}::{terminal_kind}::{side}"
                        try:
                            value = post_link_case(
                                temp, site, side, phase, terminal_kind, attack
                            )
                        except BaseException as exc:
                            value = {
                                "fixture_exception": type(exc).__name__,
                                "fixture_ok": False,
                            }
                        post_link_matrix[key] = value
                        checks[f"post_link::{key}"] = all_values_true({
                            name: item for name, item in value.items()
                            if name not in {"preparation_error_type", "error_type"}
                        })
        details["post_link_matrix"] = post_link_matrix

        durability_matrix: dict[str, dict[str, Any]] = {}
        for trigger in ("journal", "parent"):
            for terminal_kind in ("pass", "fail"):
                for side in ("synthetic", "canonical"):
                    key = f"{trigger}::{terminal_kind}::{side}"
                    try:
                        value = durability_window_case(
                            temp, site, side, terminal_kind, trigger
                        )
                    except BaseException as exc:
                        value = {
                            "fixture_exception": type(exc).__name__,
                            "fixture_ok": False,
                        }
                    durability_matrix[key] = value
                    checks[f"durability::{key}"] = all_values_true({
                        name: item for name, item in value.items()
                        if name != "error_type"
                    })
        details["durability_window_matrix"] = durability_matrix

        crash_recovery: dict[str, dict[str, Any]] = {}
        for terminal_kind in ("pass", "fail"):
            try:
                value = existing_terminal_crash_recovery_case(
                    temp, site, terminal_kind
                )
            except BaseException as exc:
                value = {
                    "fixture_exception": type(exc).__name__,
                    "fixture_ok": False,
                }
            crash_recovery[terminal_kind] = value
            checks[f"existing_crash_recovery::{terminal_kind}"] = (
                all_values_true(value)
            )
        details["existing_terminal_after_link_crash_recovery"] = crash_recovery

        existing_matrix: dict[str, dict[str, Any]] = {}
        for attack in ("replacement", "alias"):
            for terminal_kind in ("pass", "fail"):
                for side in ("synthetic", "canonical"):
                    key = f"{attack}::{terminal_kind}::{side}"
                    try:
                        value = existing_terminal_mutation_case(
                            temp, site, terminal_kind, side, attack
                        )
                    except BaseException as exc:
                        value = {
                            "fixture_exception": type(exc).__name__,
                            "fixture_ok": False,
                        }
                    existing_matrix[key] = value
                    checks[f"existing_terminal::{key}"] = all_values_true(value)
        details["existing_terminal_durability_matrix"] = existing_matrix

        path_cases = path_separation_cases(temp)
        checks.update({f"path::{key}": value for key, value in path_cases.items()})
        details["path_separation"] = path_cases

        no_clobber = terminal_no_clobber_cases(temp)
        checks.update({
            f"no_clobber::{key}": value for key, value in no_clobber.items()
        })
        details["terminal_no_clobber"] = no_clobber

        candidate_tests.thaw(temp)

    # Prove the frozen candidate remained untouched and cache-free after every
    # local fixture and local candidate-test child.
    closure_after, closure_after_details = candidate_closure_checks()
    checks["closure_after::all_candidate_closure_checks_still_true"] = all(
        closure_after.values()
    )
    checks["closure_after::index_sha_unchanged"] = (
        closure_after_details["sha256_index_sha256"]
        == closure_details["sha256_index_sha256"]
    )
    details["candidate_closure_after"] = closure_after_details

    failed = sorted(name for name, value in checks.items() if value is not True)
    output = {
        "schema": "historical_200k_fixed10k_independent_transport_runtime_layout_builder_v10_hostile_qa_output_v1",
        "status": (
            "PASS_ALL_LOCAL_RESULT_BLIND_GATES"
            if not failed
            else "FAIL_ONE_OR_MORE_LOCAL_RESULT_BLIND_GATES"
        ),
        "audited_candidate": CANDIDATE.name,
        "scope": {
            "mars_accessed": False,
            "network_accessed": False,
            "results_or_emx_accessed": False,
            "production_entry_executed": False,
            "linux_actual_executed": False,
            "external_processes_inspected_or_controlled": False,
            "local_candidate_test_children_only": True,
            "signals_sent": False,
            "candidate_modified": False,
            "engineering_memory_modified": False,
            "temporary_local_result_blind_fixtures_only": True,
        },
        "matrix_counts": {
            "post_link_replacement_alias_first_recovery_pass_fail": 16,
            "durability_journal_parent_pass_fail": 8,
            "existing_terminal_after_link_crash_recovery": 2,
            "existing_terminal_replacement_alias_pass_fail": 8,
            "path_separation": 8,
            "terminal_no_clobber": 4,
        },
        "checks": checks,
        "pass_count": sum(value is True for value in checks.values()),
        "fail_count": len(failed),
        "failed_checks": failed,
        "details": details,
        "authority": {
            "local_native_preflight_authorized": False,
            "mars_access_authorized": False,
            "transport_runtime_layout_authorized": False,
            "result_access_authorized": False,
            "signals_authorized": False,
            "controller_or_outer_main_authorized": False,
            "deployment_or_resume_authorized": False,
        },
    }
    sys.stdout.buffer.write(
        json.dumps(
            output, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
