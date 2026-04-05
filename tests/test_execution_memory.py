from villani_code.execution_memory import ExecutionMemory, action_similarity, normalize_action


def test_action_similarity_handles_equivalent_bash_shapes() -> None:
    left = normalize_action("Bash", {"command": "python3 -m pytest tests/test_a.py"})
    right = normalize_action("Bash", {"command": "python -m   pytest tests/test_a.py"})

    assert action_similarity(left, right) >= 0.8


def test_material_change_detection_uses_relevant_file_deltas() -> None:
    memory = ExecutionMemory()
    repeat = memory.assess_repeat("Bash", {"command": "pytest tests/test_a.py"}, changed_files_now=set())
    assert not repeat.matched

    memory.update_from_tool(
        turn_index=1,
        tool_name="Bash",
        tool_input={"command": "pytest tests/test_a.py src/app.py"},
        result={"is_error": True, "content": "AssertionError: failed"},
        changed_files_now=set(),
    )

    unchanged = memory.assess_repeat(
        "Bash", {"command": "pytest tests/test_a.py src/app.py"}, changed_files_now=set()
    )
    assert unchanged.matched
    assert not unchanged.material_change

    changed = memory.assess_repeat(
        "Bash",
        {"command": "pytest tests/test_a.py src/app.py"},
        changed_files_now={"src/app.py"},
    )
    assert changed.matched
    assert changed.material_change


def test_memory_updates_and_summary_generation() -> None:
    memory = ExecutionMemory()
    memory.register_artifact_state({"pending_verification": True})
    memory.update_from_tool(
        turn_index=1,
        tool_name="Bash",
        tool_input={"command": "python -m pytest"},
        result={"is_error": True, "content": "No module named pytest"},
        changed_files_now=set(),
    )

    summary = memory.build_turn_summary()

    assert "Environment facts:" in summary
    assert "No module named pytest" in summary
    assert "Most relevant recent failure" in summary


def test_repeat_retry_escalates_without_new_evidence() -> None:
    memory = ExecutionMemory()
    action = {"command": "python -m pytest tests/test_loop.py"}

    first = memory.assess_repeat("Bash", action, changed_files_now=set())
    memory.update_from_tool(
        turn_index=1,
        tool_name="Bash",
        tool_input=action,
        result={"is_error": True, "content": "AssertionError: boom"},
        changed_files_now=set(),
        repeat_assessment=first,
    )

    second = memory.assess_repeat("Bash", action, changed_files_now=set())
    memory.update_from_tool(
        turn_index=2,
        tool_name="Bash",
        tool_input=action,
        result={"is_error": True, "content": "AssertionError: boom"},
        changed_files_now=set(),
        repeat_assessment=second,
    )

    third = memory.assess_repeat("Bash", action, changed_files_now=set())
    assert third.matched
    assert third.escalation_level >= 2
