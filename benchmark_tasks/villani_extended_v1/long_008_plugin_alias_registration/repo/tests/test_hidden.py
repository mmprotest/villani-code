from app.cli import available_commands, resolve_command

def test_alias_resolves_to_same_handler():
    assert resolve_command('b') == 'build_handler'
    assert resolve_command('release') == 'deploy_handler'

def test_invalid_plugin_entry_does_not_crash():
    names = available_commands()
    assert 'build' in names and 'deploy' in names

def test_aliases_appear_in_help_listing():
    names = available_commands()
    assert 'b' in names and 'release' in names
