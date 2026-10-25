#!/usr/bin/env python3
"""Fetch supported FIRMS MODIS intervals using a free environment MAP_KEY."""
import argparse
from peatguard.study import load_study
from peatguard.firms import fetch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root')
    parser.add_argument('--profile', choices=['legacy', 'expanded'], default='expanded')
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--bbox', type=float, nargs=4, required=True, metavar=('WEST','SOUTH','EAST','NORTH'))
    parser.add_argument('--stage', choices=['SP', 'NRT'], default='SP')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    study = load_study(root=args.root, profile=args.profile, start=args.start, end=args.end, as_of=args.as_of)
    try:
        print(fetch(study, start=args.start, end=args.end, bbox=args.bbox, stage=args.stage, output=args.output))
    except (ValueError, RuntimeError, FileExistsError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
