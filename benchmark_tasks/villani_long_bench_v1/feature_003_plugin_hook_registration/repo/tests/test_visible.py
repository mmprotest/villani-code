from app.cli import run_command, status_lines
from app.registry import CommandRegistry
from app.runner import execute



def test_plugin_commands_are_registered_with_status_output():
    registry = CommandRegistry().load_plugins()
    assert registry.available_commands() == ['build', 'inspect', 'shadow']
    assert status_lines() == [
        'build: hooks=0 aliases=-',
        'inspect: hooks=1 aliases=i,scan',
        'shadow: hooks=0 aliases=-',
    ]



def test_post_run_hooks_are_returned_by_execute_and_cli():
    registry = CommandRegistry().load_plugins()
    assert execute('inspect', {'target': 'pkg'}, registry=registry) == {
        'message': 'inspect:pkg',
        'hooks': ['audit:inspect:inspect:pkg'],
    }
    assert run_command('inspect', 'pkg') == 'message=inspect:pkg hooks=audit:inspect:inspect:pkg'
