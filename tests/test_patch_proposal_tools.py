from pathlib import Path

from villani_code.runtime.proposal_apply import apply_proposal_tool_call
from villani_code.runtime.proposal_tools import ProposalToolCall, extract_structured_proposal


def test_extract_structured_tool_call_from_response():
    response = {
        "content": [
            {"type": "text", "text": "ignored"},
            {"type": "tool_use", "id": "t1", "name": "propose_full_file_rewrite", "input": {"file_path": "src/app.py", "new_content": "x=2\n"}},
        ]
    }
    result = extract_structured_proposal(response)
    assert result.call is not None
    assert result.call.name == "propose_full_file_rewrite"
    assert result.call.arguments["file_path"] == "src/app.py"


def test_invalid_tool_arguments_rejected_safely(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    proposal = ProposalToolCall(name="propose_full_file_rewrite", arguments={"file_path": "src/app.py"})
    applied = apply_proposal_tool_call(workspace=tmp_path, proposal=proposal, target_file="src/app.py", allowed_files={"src/app.py"})
    assert applied.success is False
    assert applied.validation_error == "proposal_missing_new_content"


def test_snippet_replace_exact_match_required(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    proposal = ProposalToolCall(
        name="propose_snippet_replace",
        arguments={"file_path": "src/app.py", "old_snippet": "x=3\n", "new_snippet": "x=2\n"},
    )
    applied = apply_proposal_tool_call(workspace=tmp_path, proposal=proposal, target_file="src/app.py", allowed_files={"src/app.py"})
    assert applied.success is False
    assert applied.apply_error == "proposal_old_snippet_not_found"
