from villani_code.tools import tool_specs


def test_tool_schemas_are_strict():
    specs = tool_specs()
    assert specs
    for spec in specs:
        schema = spec["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False

    by_name = {s["name"]: s for s in specs}
    assert "file_path" in by_name["Read"]["input_schema"]["required"]
    assert "pattern" in by_name["Grep"]["input_schema"]["required"]
    assert "command" in by_name["Bash"]["input_schema"]["required"]
