import argparse

from .formatters import format_summary
from .service import summarize_values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument('values', nargs='+', type=int)
    parser.add_argument('--format', choices=['text', 'json'], default='text')
    return parser


def main(argv: list[str] | None = None) -> tuple[int, str]:
    args = build_parser().parse_args(argv)
    summary = summarize_values(args.values)
    return 0, format_summary(summary, args.format)
