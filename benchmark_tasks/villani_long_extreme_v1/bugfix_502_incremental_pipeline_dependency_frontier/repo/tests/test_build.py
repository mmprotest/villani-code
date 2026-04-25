from app.graph import Graph
from app.build import BuildSystem
def make():
    deps={"lexer":set(),"schema":set(),"semantic_index":{"schema"},"analysis_report":{"semantic_index"},"runtime_bundle":{"analysis_report"},"ui_assets":{"schema"}}
    kinds={"lexer":"runtime","schema":"runtime","semantic_index":"analysis","analysis_report":"analysis","runtime_bundle":"runtime","ui_assets":"runtime"}
    return BuildSystem(Graph(deps),kinds)
def test_rebuild_refreshes_transitive_runtime_consumers_only():
    rebuilt=make().rebuild({"schema"}); assert "runtime_bundle" in rebuilt and "ui_assets" in rebuilt
def test_analysis_nodes_may_be_skipped_from_output_but_must_not_block_frontier_walk():
    rebuilt=make().rebuild({"schema"}); assert "analysis_report" not in rebuilt and rebuilt==["runtime_bundle","schema","ui_assets"]
def test_unrelated_nodes_not_invalidated():
    assert make().rebuild({"lexer"})==["lexer"]
