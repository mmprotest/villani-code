from __future__ import annotations
from .registry import CommandRegistry

def build_registry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register('serve', 'run-server')
    registry.register('build', 'run-build')
    return registry
