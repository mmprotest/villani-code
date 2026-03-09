from __future__ import annotations

from importlib import resources

from villani_code.tui.app import VillaniTUI


def test_tui_stylesheet_packaged_as_resource() -> None:
    stylesheet = resources.files("villani_code.tui").joinpath("styles.tcss")
    assert stylesheet.is_file(), (
        "TUI stylesheet is missing from the installed package: "
        "villani_code/tui/styles.tcss"
    )


def test_tui_app_css_path_points_to_expected_stylesheet() -> None:
    assert VillaniTUI.CSS_PATH == "styles.tcss"
