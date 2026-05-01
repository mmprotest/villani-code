from villani_code.state import final_answer_block_reason


def test_concrete_edit_claim_without_edits_blocks_once() -> None:
    nudge = {}
    reason = final_answer_block_reason(
        instruction="implement fix",
        final_text="I'll update x.py to handle this case.",
        has_any_edit=False,
        changed_files=[],
        known_verification_command=True,
        verification_ran_after_last_edit=True,
        last_verification_failed=False,
        had_corrective_action_after_last_failure=False,
        nudge_state=nudge,
    )
    assert reason is not None
    reason2 = final_answer_block_reason(
        instruction="implement fix",
        final_text="I'll update x.py to handle this case.",
        has_any_edit=False,
        changed_files=[],
        known_verification_command=True,
        verification_ran_after_last_edit=True,
        last_verification_failed=False,
        had_corrective_action_after_last_failure=False,
        nudge_state=nudge,
    )
    assert reason2 is None


def test_vague_suggestion_does_not_block() -> None:
    reason = final_answer_block_reason(
        instruction="implement fix",
        final_text="One possible fix would be to update parser.py.",
        has_any_edit=False,
        changed_files=[],
        known_verification_command=True,
        verification_ran_after_last_edit=True,
        last_verification_failed=False,
        had_corrective_action_after_last_failure=False,
        nudge_state={},
    )
    assert reason is None


def test_plan_only_task_does_not_block() -> None:
    reason = final_answer_block_reason(
        instruction="Plan only, do not implement.",
        final_text="I'll update parser.py",
        has_any_edit=False,
        changed_files=[],
        known_verification_command=True,
        verification_ran_after_last_edit=False,
        last_verification_failed=True,
        had_corrective_action_after_last_failure=False,
        nudge_state={},
    )
    assert reason is None


def test_changed_files_without_verification_blocks_when_command_known() -> None:
    reason = final_answer_block_reason(
        instruction="implement fix",
        final_text="Done",
        has_any_edit=True,
        changed_files=["src/a.py"],
        known_verification_command=True,
        verification_ran_after_last_edit=False,
        last_verification_failed=False,
        had_corrective_action_after_last_failure=False,
        nudge_state={},
    )
    assert "have not verified" in str(reason)


def test_changed_files_without_known_verification_does_not_block() -> None:
    reason = final_answer_block_reason(
        instruction="implement fix",
        final_text="Done",
        has_any_edit=True,
        changed_files=["src/a.py"],
        known_verification_command=False,
        verification_ran_after_last_edit=False,
        last_verification_failed=False,
        had_corrective_action_after_last_failure=False,
        nudge_state={},
    )
    assert reason is None


def test_failed_verification_requires_followup_action() -> None:
    reason = final_answer_block_reason(
        instruction="implement fix",
        final_text="Done",
        has_any_edit=True,
        changed_files=["src/a.py"],
        known_verification_command=True,
        verification_ran_after_last_edit=True,
        last_verification_failed=True,
        had_corrective_action_after_last_failure=False,
        nudge_state={},
    )
    assert "last verification failed" in str(reason)


def test_failed_verification_with_followup_does_not_block() -> None:
    reason = final_answer_block_reason(
        instruction="implement fix",
        final_text="Done",
        has_any_edit=True,
        changed_files=["src/a.py"],
        known_verification_command=True,
        verification_ran_after_last_edit=True,
        last_verification_failed=True,
        had_corrective_action_after_last_failure=True,
        nudge_state={},
    )
    assert reason is None
