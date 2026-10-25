#!/usr/bin/env python3
"""Build MODIS UTC labels from checksummed, explicitly declared intervals."""
import argparse
from peatguard.study import load_study
from peatguard.firms import build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root')
    parser.add_argument('--profile', choices=['legacy', 'expanded'], default='expanded')
    parser.add_argument('--start')
    parser.add_argument('--end', default='latest')
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--ledger', required=True)
    parser.add_argument('--grid')
    parser.add_argument('--include-held-out', action='store_true', help='Explicitly enable test-period labels; default exports development dates only.')
    args = parser.parse_args()
    study = load_study(root=args.root, profile=args.profile, start=args.start, end=args.end, as_of=args.as_of)
    try:
        print(build(study, args.ledger, grid_path=args.grid, include_held_out=args.include_held_out))
    except (ValueError, FileNotFoundError, FileExistsError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
