from pathlib import Path

from villani_code.permissions import Decision, PermissionConfig, PermissionEngine, bash_matches, classify_bash_command


def test_permissions_precedence_deny_ask_allow(tmp_path: Path):
    cfg = PermissionConfig.from_strings(
        deny=["Bash(rm -rf *)"],
        ask=["Bash(git push *)"],
        allow=["Bash(*)"],
    )
    engine = PermissionEngine(cfg, tmp_path)
    assert engine.evaluate("Bash", {"command": "rm -rf build"}) == Decision.DENY
    assert engine.evaluate("Bash", {"command": "git push origin main"}) == Decision.ASK
    assert engine.evaluate("Bash", {"command": "echo ok"}) == Decision.ALLOW


def test_bash_operator_aware_matching():
    assert bash_matches("npm run test *", "npm run test unit")
    assert not bash_matches("npm run test *", "npm run test && rm -rf /")


def test_bashsafe_allows_readonly_commands():
    cls = classify_bash_command("git status")
    assert cls.decision == Decision.ALLOW


def test_bashsafe_allows_common_test_commands() -> None:
    assert classify_bash_command("pytest tests/test_x.py").decision == Decision.ALLOW
    assert classify_bash_command("python -m pytest").decision == Decision.ALLOW
    assert classify_bash_command("uv run pytest").decision == Decision.ALLOW
    assert classify_bash_command("poetry run pytest").decision == Decision.ALLOW
    assert classify_bash_command("npm test").decision == Decision.ALLOW
    assert classify_bash_command("pnpm test").decision == Decision.ALLOW


def test_bashsafe_allows_lint_type_and_format_checks() -> None:
    assert classify_bash_command("ruff check .").decision == Decision.ALLOW
    assert classify_bash_command("ruff format --check .").decision == Decision.ALLOW
    assert classify_bash_command("mypy villani_code").decision == Decision.ALLOW


def test_bashsafe_allows_version_and_git_readonly_commands() -> None:
    assert classify_bash_command("python --version").decision == Decision.ALLOW
    assert classify_bash_command("python -V").decision == Decision.ALLOW
    assert classify_bash_command("node --version").decision == Decision.ALLOW
    assert classify_bash_command("node -v").decision == Decision.ALLOW
    assert classify_bash_command("git diff").decision == Decision.ALLOW
    assert classify_bash_command("git log --oneline").decision == Decision.ALLOW
    assert classify_bash_command("git show HEAD").decision == Decision.ALLOW
    assert classify_bash_command("git branch").decision == Decision.ALLOW


def test_bashsafe_rejects_chaining_and_install():
    assert classify_bash_command("pwd && whoami").decision == Decision.ASK
    assert classify_bash_command("pip install x").decision == Decision.ASK
    assert classify_bash_command("ruff check . > out.txt").decision == Decision.ASK


def test_bash_defaults_to_ask_without_bashsafe(tmp_path: Path):
    cfg = PermissionConfig.from_strings(deny=[], ask=[], allow=["Read(*)"])
    engine = PermissionEngine(cfg, tmp_path)
    decision = engine.evaluate_with_reason("Bash", {"command": "pwd"})
    assert decision.decision == Decision.ASK


def test_public_target_for_exposes_normalized_target(tmp_path: Path):
    cfg = PermissionConfig.from_strings(deny=[], ask=[], allow=[])
    engine = PermissionEngine(cfg, tmp_path)
    assert engine.target_for("Write", {"file_path": "a.txt"}) == "a.txt"


def test_bash_matches_malformed_input_fails_closed():
    assert bash_matches("*", "echo \"unterminated") is False


def test_permission_engine_allows_execution_plan_without_weakening_mutations(tmp_path: Path) -> None:
    engine = PermissionEngine(PermissionConfig.from_strings(deny=[], ask=["ExecutionPlan(*)", "Write(*)", "Patch(*)"], allow=["BashSafe(*)"]), tmp_path)

    assert engine.evaluate("ExecutionPlan", {"summary": "plan", "risk": "high"}) == Decision.ALLOW
    assert engine.evaluate("Write", {"file_path": "a.txt"}) == Decision.ASK
    assert engine.evaluate("Patch", {"file_path": "a.txt"}) == Decision.ASK
    assert engine.evaluate("Bash", {"command": "pip install x"}) == Decision.ASK


def test_execution_plan_still_respects_explicit_deny(tmp_path: Path) -> None:
    engine = PermissionEngine(PermissionConfig.from_strings(deny=["ExecutionPlan(*)"], ask=[], allow=[]), tmp_path)

    assert engine.evaluate("ExecutionPlan", {"summary": "plan"}) == Decision.DENY
