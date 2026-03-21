from __future__ import annotations
import argparse
import sys
from app.commands import cmd_echo

def build_parser():
    parser = argparse.ArgumentParser(prog='bench-app')
    sub = parser.add_subparsers(dest='command', required=True)
    echo = sub.add_parser('echo')
    echo.add_argument('values', nargs='+')
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == 'echo':
        code, text = cmd_echo(args)
        print(text)
        return code
    print('unknown command', file=sys.stderr)
    return 2

if __name__ == '__main__':
    raise SystemExit(main())
