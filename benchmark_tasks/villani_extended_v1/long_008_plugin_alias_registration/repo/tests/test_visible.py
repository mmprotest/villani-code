from app.cli import available_commands, resolve_command

def test_direct_command_still_resolves():
    assert resolve_command('build') == 'build_handler'
    assert 'build' in available_commands()
