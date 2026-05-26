from app.buildsystem import BuildSystem
FILES = {"base":"BASE", "mid":"use base\n{{base}}-MID", "top":"use mid\n{{mid}}-TOP", "side":"SIDE"}

def test_transitive_dependents_refresh_after_change():
    bs=BuildSystem(); bs.load(FILES); assert bs.build_all()["top"] == "BASE-MID-TOP"; bs.update_file("base", "BASE2"); assert bs.build_all()["top"] == "BASE2-MID-TOP"

def test_unrelated_targets_stay_cached():
    bs=BuildSystem(); bs.load(FILES); bs.build_all(); bs.compiler.compile_count=0; bs.update_file("base", "BASE2"); bs.build_all(); assert bs.compiler.compile_count == 3

def test_only_dependency_frontier_rebuilds():
    bs=BuildSystem(); bs.load(FILES); bs.build_all(); bs.compiler.compile_count=0; bs.update_file("side", "SIDE2"); bs.build_all(); assert bs.compiler.compile_count == 1
