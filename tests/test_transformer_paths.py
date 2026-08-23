from pathlib import Path

from rfic_transformer_inverse_design.paths import bundled_proc_dir, resolve_local_path


def test_resolve_local_path_recovers_missing_absolute_proc_using_bundled_copy() -> None:
    stale_absolute = Path(
        r"C:\Users\example\dev\old-project\rfic_transformer_inverse_design\process\assets\proc\default_typical.proc"
    )

    resolved = resolve_local_path(stale_absolute, extra_roots=(bundled_proc_dir(),))

    assert resolved == (bundled_proc_dir() / "default_typical.proc").resolve()
