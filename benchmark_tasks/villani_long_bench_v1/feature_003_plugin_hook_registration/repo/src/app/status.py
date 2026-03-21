from .registry import CommandRegistry



def render_status(registry: CommandRegistry | None = None) -> list[str]:
    active = registry or CommandRegistry().load_plugins()
    return [f"{name}: hooks=0 aliases=-" for name in active.available_commands()]
