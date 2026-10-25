"""Bounded, cumulative ERA5-Land refresh. See docs/era5-land-refresh.md."""
import argparse
import json

from peatguard import load_study
from peatguard.era5_land import EarthEngineProvider, refresh


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root")
    parser.add_argument("--profile", choices=("legacy", "expanded"), default="expanded")
    parser.add_argument("--start")
    parser.add_argument("--end", default="latest")
    parser.add_argument("--as-of")
    parser.add_argument("--project", required=True)
    parser.add_argument("--dry-run", action="store_true", help="Discover and plan without writing or downloading")
    parser.add_argument("--revise", nargs=2, metavar=("START", "END"), help="Explicit inclusive revision interval")
    parser.add_argument("--revision-id", help="Reuse the ID from a failed revision to resume its checkpoints")
    args = parser.parse_args()
    try:
        study = load_study(root=args.root, profile=args.profile, start=args.start, end=args.end, as_of=args.as_of)
        provider = EarthEngineProvider(study, args.project)
        result = refresh(study, provider, revise=args.revise, revision_id=args.revision_id, dry_run=args.dry_run)
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"ERA5 refresh blocked: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
