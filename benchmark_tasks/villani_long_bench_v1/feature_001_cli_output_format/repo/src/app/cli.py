import argparse

from .commands import run_stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument('values', nargs='+', type=int)
    parser.add_argument('--format', choices=['text', 'json'], default='text')
    return parser


def main(argv: list[str] | None = None) -> tuple[int, str]:
    args = build_parser().parse_args(argv)
    return 0, run_stats(args.values, args.format)
