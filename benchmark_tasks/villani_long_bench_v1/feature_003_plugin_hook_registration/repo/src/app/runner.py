from .registry import CommandRegistry



def execute(command_name: str, payload: dict[str, str], registry: CommandRegistry | None = None) -> dict[str, object]:
    active = registry or CommandRegistry().load_plugins()
    handler = active.commands[command_name]
    message = handler(payload)
    return {
        'message': message,
        'hooks': [],
    }
