from app.cli import build_registry


def test_primary_command_still_resolves():
    reg = build_registry()
    assert reg.resolve('serve') == 'run-server'


def test_alias_resolves_to_registered_command():
    reg = build_registry()
    reg.register('test', 'run-test', alias='t')
    assert reg.resolve('t') == 'run-test'
