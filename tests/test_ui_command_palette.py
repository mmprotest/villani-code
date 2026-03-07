from villani_code.tui.components.command_palette import CommandPalette, fuzzy_score


def test_fuzzy_score_prefers_substring_match() -> None:
    assert fuzzy_score("diff", "/diff open diff viewer") > fuzzy_score("dfv", "/diff open diff viewer")


def test_palette_search_returns_expected_top_result() -> None:
    palette = CommandPalette()
    top = palette.search("settings", limit=1)
    assert top
    assert top[0][1].action.target == "settings"



def test_palette_search_commands_only_returns_slash_commands() -> None:
    palette = CommandPalette()
    results = palette.search_commands("/")
    assert results
    assert all(item.trigger.startswith("/") for _, item in results)


def test_palette_command_by_trigger_returns_only_known_slash_command() -> None:
    palette = CommandPalette()
    assert palette.command_by_trigger("/help") is not None
    assert palette.command_by_trigger("toggle verbose") is None
