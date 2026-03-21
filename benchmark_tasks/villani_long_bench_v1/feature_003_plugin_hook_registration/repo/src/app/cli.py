from .registry import CommandRegistry
from .runner import execute


def run_command(command_name: str, target: str) -> str:
    registry = CommandRegistry().load_plugins()
    result = execute(command_name, {'target': target}, registry=registry)
    return f"message={result['message']} hooks={','.join(result['hooks']) or '-'}"
