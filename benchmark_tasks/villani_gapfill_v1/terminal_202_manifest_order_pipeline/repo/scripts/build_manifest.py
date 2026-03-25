from pathlib import Path

def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    items = (repo / "data" / "files.txt").read_text(encoding="utf-8").splitlines()
    out = repo / "manifest.txt"
    out.write_text("
".join(reversed(items)) + "
", encoding="utf-8")
    print(out)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
