from pathlib import Path

from villani_code.permissions import Decision, PermissionConfig, PermissionEngine, bash_matches


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


def test_bash_matches_never_throws_on_malformed_commands() -> None:
    commands = [
        'echo "hello',
        r"ls -la /c/Users/Simon/OneDrive/Documents/Python\ Scripts/villani-code/",
        "echo hi && cat file > out.txt",
    ]
    for command in commands:
        assert isinstance(bash_matches("*", command), bool)
