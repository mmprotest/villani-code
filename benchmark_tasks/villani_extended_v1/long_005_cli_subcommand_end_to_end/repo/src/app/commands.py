from __future__ import annotations
from app.service import compute_stats

def cmd_echo(args):
    return 0, ' '.join(args.values)

def cmd_stats(args):
    # BUG: not implemented end to end
    return 0, 'not implemented'
