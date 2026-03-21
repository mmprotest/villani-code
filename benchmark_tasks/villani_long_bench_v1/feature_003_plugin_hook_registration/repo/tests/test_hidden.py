from app.cli import run_command, status_lines
from app.registry import CommandRegistry
from app.runner import execute



def test_alias_lookup_and_hook_invocation_work_together():
    registry = CommandRegistry().load_plugins()
    assert registry.resolve_command('scan') == 'inspect'
    assert execute('scan', {'target': 'pkg'}, registry=registry) == {
        'message': 'inspect:pkg',
        'hooks': ['audit:inspect:inspect:pkg'],
    }
    assert run_command('scan', 'pkg') == 'message=inspect:pkg hooks=audit:inspect:inspect:pkg'



def test_duplicate_alias_does_not_override_first_plugin_and_status_stays_consistent():
    registry = CommandRegistry().load_plugins()
    assert registry.resolve_command('shadow') == 'shadow'
    assert registry.resolve_command('scan') == 'inspect'
    assert status_lines() == [
        'build: hooks=0 aliases=-',
        'inspect: hooks=1 aliases=i,scan',
        'shadow: hooks=0 aliases=-',
    ]



def test_existing_commands_still_have_no_hooks():
    registry = CommandRegistry().load_plugins()
    assert execute('build', {'target': 'pkg'}, registry=registry) == {
        'message': 'build:pkg',
        'hooks': [],
    }
