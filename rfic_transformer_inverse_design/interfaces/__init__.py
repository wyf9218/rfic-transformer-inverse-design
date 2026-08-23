"""User-facing interfaces for rfic-transformer-inverse-design."""

from .cli import main as cli_main


def gui_main():
    from .gui_qt import main as _main

    return _main()


def gui_qt_main():
    return gui_main()


__all__ = ["cli_main", "gui_main", "gui_qt_main"]
