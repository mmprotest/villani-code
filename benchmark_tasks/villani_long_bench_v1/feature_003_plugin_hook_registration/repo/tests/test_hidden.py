from app.registry import CommandRegistry
from app.runner import execute


def test_hook_registry_is_available_on_loaded_plugins():
    registry = CommandRegistry().load_plugins()
    assert registry.post_run_hooks['inspect']


def test_existing_commands_still_have_no_hooks():
    registry = CommandRegistry().load_plugins()
    assert execute('build', {'target': 'pkg'}, registry=registry) == {
        'message': 'build:pkg',
        'hooks': [],
    }
