from __future__ import annotations
import json

def emit_report(ok: bool, errors: list[str], fmt: str = 'text') -> str:
    if fmt == 'json':
        print('validation finished')  # BUG pollutes structured output
        return json.dumps({'ok': ok})  # BUG missing error details
    if ok:
        return 'OK'
    return 'ERROR: ' + '; '.join(errors)
