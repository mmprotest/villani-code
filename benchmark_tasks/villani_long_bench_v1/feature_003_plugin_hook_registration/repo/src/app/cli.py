from .registry import CommandRegistry
from .runner import execute
from .status import render_status



def run_command(command_name: str, target: str) -> str:
    registry = CommandRegistry().load_plugins()
    result = execute(command_name, {'target': target}, registry=registry)
    return f"message={result['message']} hooks={','.join(result['hooks']) or '-'}"



def status_lines() -> list[str]:
    return render_status(CommandRegistry().load_plugins())
