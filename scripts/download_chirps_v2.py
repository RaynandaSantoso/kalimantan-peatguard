"""Refresh a bounded CHIRPS v2 Final interval; see docs/chirps-refresh.md."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Direct script execution works from either supported working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from peatguard.chirps import CHIRPSArchive, EarthEngineCHIRPS
from peatguard.study import load_study


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Earth Engine enabled Cloud project ID")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--profile", choices=["legacy", "expanded"], default="expanded")
    parser.add_argument("--start")
    parser.add_argument("--end", default="latest", help="Inclusive ISO date, or latest bounded by --as-of")
    parser.add_argument("--as-of", help="Fixed offset timestamp, or date interpreted as end-of-day UTC")
    parser.add_argument("--years", nargs="+", type=int, help="Convenience backfill years; cannot combine with --start/end")
    parser.add_argument("--refresh-start", help="Explicit revision interval start, inclusive")
    parser.add_argument("--refresh-end", help="Explicit revision interval end, inclusive")
    parser.add_argument("--discover-only", action="store_true", help="Metadata and local validation only; no acquisition")
    args = parser.parse_args()
    try:
        if args.years:
            if args.start or args.end != "latest":
                raise ValueError("Use --years or --start/--end, not both.")
            if sorted(set(args.years)) != list(range(min(args.years), max(args.years)+1)):
                raise ValueError("--years must describe a contiguous interval.")
            args.start, args.end = f"{min(args.years)}-01-01", f"{max(args.years)}-12-31"
            # A current-year convenience request is explicitly bounded by the cutoff.
            resolved = load_study(root=args.root, profile=args.profile, as_of=args.as_of)
            args.end = min(args.end, str(resolved.end))
        study = load_study(root=args.root, profile=args.profile, start=args.start,
                           end=args.end, as_of=args.as_of)
        archive = CHIRPSArchive(study)
        # Validate locally before authentication, so corrupt artifacts fail without a download.
        _, validated = archive.validated()
        provider = EarthEngineCHIRPS(archive.definition, args.project)
        if args.discover_only:
            discovery = provider.discover(study.start, study.end)
            from datetime import date
            local = {day for item in validated.values() for day in item["dates"]}
            report = {"read_only": True, "study": study.metadata(), "discovery": discovery,
                      "coverage": archive.coverage([date.fromisoformat(d) for d in discovery["dates"]], local)}
        else:
            report = {"study": study.metadata(), **archive.refresh(provider,
                refresh_start=args.refresh_start, refresh_end=args.refresh_end)}
        print(json.dumps(report, indent=2))
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
