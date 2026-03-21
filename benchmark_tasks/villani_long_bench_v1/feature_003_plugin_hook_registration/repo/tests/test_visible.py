from app.cli import run_command
from app.registry import CommandRegistry
from app.runner import execute


def test_plugin_command_is_still_registered():
    registry = CommandRegistry().load_plugins()
    assert registry.available_commands() == ['build', 'inspect']


def test_post_run_hooks_are_returned_by_execute_and_cli():
    registry = CommandRegistry().load_plugins()
    assert execute('inspect', {'target': 'pkg'}, registry=registry) == {
        'message': 'inspect:pkg',
        'hooks': ['audit:inspect:inspect:pkg'],
    }
    assert run_command('inspect', 'pkg') == 'message=inspect:pkg hooks=audit:inspect:inspect:pkg'
