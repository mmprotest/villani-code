from __future__ import annotations
from app.reporting import emit_report
from app.validate import run_validation

def main(value: int, fmt: str = 'text') -> tuple[int, str]:
    ok, errors = run_validation(value)
    text = emit_report(ok, errors, fmt=fmt)
    return (0 if ok else 1), text
