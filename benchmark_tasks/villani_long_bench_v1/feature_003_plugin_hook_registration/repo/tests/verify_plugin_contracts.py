from app.cli import run_command, status_lines
from app.registry import CommandRegistry
from app.runner import execute


registry = CommandRegistry().load_plugins()
assert registry.resolve_command('scan') == 'inspect'
assert execute('inspect', {'target': 'pkg'}, registry=registry) == {
    'message': 'inspect:pkg',
    'hooks': ['audit:inspect:inspect:pkg'],
}
assert execute('scan', {'target': 'pkg'}, registry=registry) == {
    'message': 'inspect:pkg',
    'hooks': ['audit:inspect:inspect:pkg'],
}
assert run_command('scan', 'pkg') == 'message=inspect:pkg hooks=audit:inspect:inspect:pkg'
assert status_lines() == [
    'build: hooks=0 aliases=-',
    'inspect: hooks=1 aliases=i,scan',
    'shadow: hooks=0 aliases=-',
]
