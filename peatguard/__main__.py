"""Print a read-only JSON refresh plan; redirection is explicitly user-controlled."""

import argparse
import json

from .inventory import build_inventory, discover_availability
from .study import load_study


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="Repository root (otherwise discover from working directory)")
    parser.add_argument("--profile", choices=["legacy", "expanded"], default="expanded")
    parser.add_argument("--start", help="Inclusive source-day start, YYYY-MM-DD")
    parser.add_argument("--end", default="latest", help="Inclusive end or latest (bounded by profile/as-of)")
    parser.add_argument("--as-of", help="ISO timestamp with UTC offset, or end-of-day UTC date")
    discovery = parser.add_mutually_exclusive_group()
    discovery.add_argument("--discover", action="store_true", help="Query Earth Engine asset dates only")
    discovery.add_argument("--availability", help="Read a previously captured provider-availability JSON")
    parser.add_argument("--project", help="Earth Engine-enabled project for optional metadata discovery")
    args = parser.parse_args()
    try:
        study = load_study(root=args.root, profile=args.profile, start=args.start,
                           end=args.end, as_of=args.as_of)
        availability = None
        if args.discover:
            availability = discover_availability(study, args.project)
        elif args.availability:
            from pathlib import Path
            availability = json.loads(Path(args.availability).read_text())
        print(json.dumps(build_inventory(study, availability=availability), indent=2))
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
