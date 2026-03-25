from pathlib import Path

def profile_settings(name: str) -> dict:
    text = (Path(__file__).resolve().parents[1] / "profiles" / f"{name}.txt").read_text(encoding="utf-8")
    parts = dict(line.split("=", 1) for line in text.splitlines() if line)
    return parts
