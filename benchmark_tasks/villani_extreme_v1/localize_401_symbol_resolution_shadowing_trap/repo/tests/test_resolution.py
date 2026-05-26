from app.cli import run
PROGRAM=["import pkg.real as value","x = value","def inner:","value = temp_local","alias = value"]

def test_formatter_output_for_shadowed_alias():
    assert run(PROGRAM, "inner", "value") == "inner:value -> <local:value=temp_local>"

def test_module_alias_still_resolves_import():
    assert run(PROGRAM, "missing", "value") == "missing:value -> pkg.real"

def test_local_assignment_shadowing_beats_module_import():
    assert run(PROGRAM, "inner", "alias") == "inner:alias -> <local:alias=value>"
