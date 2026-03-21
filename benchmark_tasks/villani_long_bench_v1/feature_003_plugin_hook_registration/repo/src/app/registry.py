from .plugins import PLUGIN_SPECS


class CommandRegistry:
    def __init__(self) -> None:
        self.commands = {
            'build': lambda payload: f"build:{payload['target']}",
        }

    def register_plugin(self, spec: dict[str, object]) -> None:
        self.commands.update(spec.get('commands', {}))

    def load_plugins(self) -> 'CommandRegistry':
        for spec in PLUGIN_SPECS:
            self.register_plugin(spec)
        return self

    def available_commands(self) -> list[str]:
        return sorted(self.commands)
