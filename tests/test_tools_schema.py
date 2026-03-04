from villani_code.tools import sanitize_json_schema, tool_specs


def test_tool_schemas_are_strict():
    specs = tool_specs()
    assert specs
    for spec in specs:
        schema = spec["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert "$defs" not in schema
        assert "$schema" not in schema
        assert "$ref" not in str(schema)
        assert "title" not in str(schema)

    by_name = {s["name"]: s for s in specs}
    assert "file_path" in by_name["Read"]["input_schema"]["required"]
    assert "pattern" in by_name["Grep"]["input_schema"]["required"]
    assert "command" in by_name["Bash"]["input_schema"]["required"]


def test_schema_sanitizer_preserves_required_and_additional_properties():
    raw = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Example",
        "type": "object",
        "properties": {
            "name": {"type": "string", "title": "Name"},
        },
        "required": ["name"],
        "$defs": {"Inner": {"type": "object", "properties": {"x": {"type": "string"}}}},
    }
    cleaned = sanitize_json_schema(raw)
    assert cleaned["required"] == ["name"]
    assert cleaned["additionalProperties"] is False
    assert "$schema" not in cleaned
    assert "$defs" not in cleaned
