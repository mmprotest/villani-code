def inspect_handler(payload: dict[str, str]) -> str:
    return f"inspect:{payload['target']}"


def audit_hook(command_name: str, message: str) -> str:
    return f"audit:{command_name}:{message}"


PLUGIN_SPECS = [
    {
        'name': 'audit',
        'commands': {'inspect': inspect_handler},
        'post_run_hooks': {'inspect': audit_hook},
    }
]
