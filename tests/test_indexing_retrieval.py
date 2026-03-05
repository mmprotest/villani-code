from pathlib import Path

from villani_code.indexing import DEFAULT_IGNORE, RepoIndex
from villani_code.retrieval import Retriever


def _copy_fixture(tmp_path: Path) -> Path:
    src = Path(__file__).parent / "fixtures" / "small_repo"
    dst = tmp_path / "repo"
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    return dst


def test_index_build_extracts_symbols(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    index = RepoIndex.build(repo, DEFAULT_IGNORE)
    by_path = {item.path: item for item in index.iter_files()}

    assert "src/app.py" in by_path
    assert "helper_func" in by_path["src/app.py"].symbols
    assert "Worker" in by_path["src/app.py"].symbols


def test_index_save_load_roundtrip_and_rebuild(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    index = RepoIndex.build(repo, DEFAULT_IGNORE)
    index_path = repo / ".villani_code" / "index" / "index.json"
    index.save(index_path)

    loaded = RepoIndex.load(index_path)
    assert [f.path for f in loaded.iter_files()] == [f.path for f in index.iter_files()]
    assert not loaded.needs_rebuild(repo)

    app = repo / "src" / "app.py"
    app.write_text(app.read_text(encoding="utf-8") + "\n# change\n", encoding="utf-8")
    assert loaded.needs_rebuild(repo)


def test_retrieval_ranks_symbol_match_first(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    index = RepoIndex.build(repo, DEFAULT_IGNORE)
    retriever = Retriever(index)
    hits = retriever.query("Where is parse_value defined?", k=3)

    assert hits
    assert hits[0].path == "pkg/mod.rs"
    assert "symbol match" in hits[0].reason or "snippet match" in hits[0].reason
