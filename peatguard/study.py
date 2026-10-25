"""Resolve a study once, with inclusive dates and explicit held-out access."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path


def find_root(start: str | Path | None = None) -> Path:
    """Find the project from a working directory or a file inside it."""
    path = Path(start or Path.cwd()).resolve()
    if path.is_file():
        path = path.parent
    for candidate in (path, *path.parents):
        if (candidate / "config/study.json").is_file() and (candidate / "peatguard").is_dir():
            return candidate
    raise FileNotFoundError("Cannot locate PeatGuard: run inside the repository or pass --root.")


def day_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range(max(0, (end - start).days + 1))]


def parse_as_of(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        # A bare date is an explicit end-of-day UTC snapshot, not host-local time.
        value = (datetime.combine(date.fromisoformat(value), time.max, timezone.utc)
                 if len(value) == 10 else datetime.fromisoformat(value.replace("Z", "+00:00")))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as-of needs a UTC offset (or a date interpreted as end-of-day UTC).")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Study:
    root: Path
    config: dict
    profile: str
    start: date
    end: date
    as_of: datetime

    @property
    def profile_config(self) -> dict:
        return self.config["profiles"][self.profile]

    def split_dates(self, role: str, *, include_held_out: bool = False) -> list[date]:
        """Outcome consumers must deliberately opt into a held-out partition."""
        if role in self.profile_config["held_out"] and not include_held_out:
            raise ValueError(f"{role} is held out; explicit include_held_out=True is required.")
        start, end = map(date.fromisoformat, self.profile_config["splits"][role])
        return day_range(max(start, self.start), min(end, self.end))

    def development_dates(self) -> list[date]:
        return [day for role in self.profile_config["splits"]
                if role not in self.profile_config["held_out"] for day in self.split_dates(role)]

    @property
    def identity(self) -> str:
        payload = {"config": self.config, "profile": self.profile,
                   "start": str(self.start), "end": str(self.end), "as_of": self.as_of.isoformat()}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]

    @property
    def output_directory(self) -> Path:
        """Reserved build location; resolving it does not create it."""
        return self.root / "data/interim/builds" / self.profile / self.identity

    def require_compatible(self, metadata: dict) -> None:
        if metadata.get("profile") != self.profile or metadata.get("study_id") != self.identity:
            raise ValueError("Dataset profile/study_id mismatch; do not mix study builds.")

    def metadata(self) -> dict:
        return {
            "schema_version": self.config["schema_version"], "study_id": self.identity,
            "profile": self.profile, "as_of": self.as_of.isoformat(),
            "requested": {"start": str(self.start), "end": str(self.end), "inclusive": True},
            "date_convention": self.config["date_convention"], "target": self.config["target"],
            "splits": self.profile_config["splits"],
            "held_out": self.profile_config["held_out"],
            "final_test_interval_frozen": self.profile_config.get("final_test_interval_frozen", False),
            "output_directory": self.output_directory.relative_to(self.root).as_posix(),
            "development_roles": [role for role in self.profile_config["splits"]
                                  if role not in self.profile_config["held_out"]],
            "grid": self.config["grid"],
        }


def load_study(*, root: str | Path | None = None, profile: str = "expanded",
               start: str | None = None, end: str = "latest",
               as_of: str | datetime | None = None) -> Study:
    root = find_root(root)
    config = json.loads((root / "config/study.json").read_text())
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported study configuration schema.")
    if profile not in config["profiles"]:
        raise ValueError(f"Unknown profile: {profile}")
    selected = config["profiles"][profile]
    lower, upper = date.fromisoformat(selected["start"]), date.fromisoformat(selected["end"])
    cutoff = parse_as_of(as_of)
    requested_start = date.fromisoformat(start) if start else lower
    requested_end = min(cutoff.date(), upper) if end == "latest" else date.fromisoformat(end)
    if requested_end > cutoff.date():
        raise ValueError("Requested end is after the as-of cutoff; future dates are not available.")
    if not lower <= requested_start <= requested_end <= upper:
        raise ValueError("Requested dates must be ordered and contained in the selected profile.")
    previous = lower - timedelta(days=1)
    for split_start, split_end in selected["splits"].values():
        a, b = date.fromisoformat(split_start), date.fromisoformat(split_end)
        if a != previous + timedelta(days=1) or b < a:
            raise ValueError("Profile splits must be contiguous, ordered and non-overlapping.")
        previous = b
    if previous != upper or not set(selected["held_out"]) <= selected["splits"].keys():
        raise ValueError("Splits must cover the profile and declare valid held-out roles.")
    return Study(root, config, profile, requested_start, requested_end, cutoff)
