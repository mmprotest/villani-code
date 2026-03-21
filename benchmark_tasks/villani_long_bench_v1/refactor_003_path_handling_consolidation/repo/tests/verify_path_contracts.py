from pathlib import Path

from app.bundle import plan_bundle
from app.registry import build_registry
from app.reporting import render_report
from app.scanner import collect_manifest


workspace = Path('/tmp/bench-workspace')
nested = workspace / 'nested'
report = nested / 'report.txt'
assert plan_bundle(str(workspace), ['./nested/report.txt', str(report)]) == ['nested/report.txt', 'nested/report.txt']
assert collect_manifest(str(workspace), [str(report)]) == 'nested/report.txt'
assert build_registry(str(workspace), ['./nested/report.txt', str(report)]) == {'nested/report.txt': str(report)}
assert render_report(str(workspace), ['./nested/report.txt', str(report)]) == 'nested/report.txt,nested/report.txt'
