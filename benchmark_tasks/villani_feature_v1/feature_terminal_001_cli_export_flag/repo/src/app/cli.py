from __future__ import annotations
import argparse
import json
import sys

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='app.cli')
    subparsers = parser.add_subparsers(dest='command', required=True)
    export = subparsers.add_parser('export')
    export.add_argument('--kind', default='json', choices=['json', 'csv'])
    return parser

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == 'export':
        if args.kind == 'json':
            sys.stdout.write(json.dumps({'ok': True}))
        else:
            sys.stdout.write('ok\n')
        return 0
    return 1

if __name__ == '__main__':
    raise SystemExit(main())
