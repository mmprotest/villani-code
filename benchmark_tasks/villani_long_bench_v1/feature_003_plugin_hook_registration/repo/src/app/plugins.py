def inspect_handler(payload: dict[str, str]) -> str:
    return f"inspect:{payload['target']}"



def shadow_handler(payload: dict[str, str]) -> str:
    return f"shadow:{payload['target']}"



def audit_hook(command_name: str, message: str) -> str:
    return f"audit:{command_name}:{message}"


PLUGIN_SPECS = [
    {
        'name': 'audit',
        'description': 'inspection commands',
        'commands': {'inspect': inspect_handler},
        'aliases': {'inspect': ['scan', 'i']},
        'post_run_hooks': {'inspect': audit_hook},
    },
    {
        'name': 'shadow',
        'description': 'shadow diagnostics',
        'commands': {'shadow': shadow_handler},
        'aliases': {'shadow': ['scan']},
        'post_run_hooks': {},
    },
]
