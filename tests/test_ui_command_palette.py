from ui.command_palette import CommandPalette, fuzzy_score


def test_fuzzy_score_prefers_substring_match() -> None:
    assert fuzzy_score("diff", "/diff open diff viewer") > fuzzy_score("dfv", "/diff open diff viewer")


def test_palette_search_returns_expected_top_result() -> None:
    palette = CommandPalette()
    top = palette.search("settings", limit=1)
    assert top
    assert top[0][1].action.target == "settings"
