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


def test_bashsafe_rejects_chaining_and_install():
    assert classify_bash_command("pwd && whoami").decision == Decision.ASK
    assert classify_bash_command("pip install x").decision == Decision.ASK


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
