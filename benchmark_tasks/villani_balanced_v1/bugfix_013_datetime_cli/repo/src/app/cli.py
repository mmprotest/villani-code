import argparse
from datetime import datetime, timezone
from .date_utils import resolve_today

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('command')
    parser.add_argument('--date', default='today')
    args=parser.parse_args(argv)
    if args.command=='report' and args.date=='today':
        print(resolve_today(datetime(2024,1,1,0,30,tzinfo=timezone.utc)))
        return 0
    return 1
