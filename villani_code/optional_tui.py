from __future__ import annotations

TUI_INSTALL_HINT = (
    "Interactive mode requires the optional TUI dependencies. "
    "Install with `pip install .[tui]`."
)


class OptionalTUIDependencyError(RuntimeError):
    """Raised when interactive mode is requested without the optional TUI extra."""


def remap_textual_import_error(exc: ModuleNotFoundError) -> None:
    if exc.name == "textual":
        raise OptionalTUIDependencyError(TUI_INSTALL_HINT) from exc
    raise exc
