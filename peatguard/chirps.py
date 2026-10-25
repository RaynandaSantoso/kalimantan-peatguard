"""Bounded CHIRPS v2 Final refresh and development-only rainfall summaries.

Publication timestamps are not historical release evidence. Source-day UTC
observations remain retrospective inputs, not next-day forecast features.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import uuid

import numpy as np

from .inventory import _daily, ranges, sha256
from .study import Study, day_range


BAND = "precipitation_mm_day"


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _contained(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ValueError("CHIRPS path escapes the repository.")
    return path


def _identity(definition: dict) -> dict:
    return {key: definition[key] for key in
            ("collection", "version", "shape", "grid_transform", "bands", "units")}


def _coordinates(definition: dict) -> tuple[np.ndarray, np.ndarray]:
    h, w = definition["shape"]
    a, _, west, _, e, north = definition["grid_transform"]
    return north + e * (np.arange(h) + .5), west + a * (np.arange(w) + .5)


def _validate_values(values: np.ndarray, days: list[date], definition: dict) -> None:
    if (values.shape != (len(days), *definition["shape"])
            or not np.issubdtype(values.dtype, np.number)
            or np.isinf(values).any()
            or not np.isfinite(values).any(axis=(1, 2)).all()
            or np.nanmin(values) < 0):
        raise ValueError("Invalid CHIRPS precipitation: grid, dates, physical values or missing day.")


class EarthEngineCHIRPS:
    """Lazy authenticated provider adapter; no authentication at import time."""

    def __init__(self, definition: dict, project: str):
        try:
            import ee
            ee.Initialize(project=project)
        except Exception:
            raise RuntimeError("Earth Engine initialization failed; configure access and --project.") from None
        self.ee, self.definition = ee, definition

    def discover(self, start: date, end: date) -> dict:
        ee = self.ee
        try:
            collection = (ee.ImageCollection(self.definition["collection"])
                          .filterDate(str(start), str(end + timedelta(days=1)))
                          .sort("system:time_start"))
            stamps = collection.aggregate_array("system:time_start").getInfo()
            ids = collection.aggregate_array("system:index").getInfo()
            days = [datetime.fromtimestamp(value / 1000, timezone.utc).date() for value in stamps]
            if len(ids) != len(days) or len(days) != len(set(days)):
                raise ValueError("Unexpected provider dates or image identifiers.")
            return {"dates": [str(day) for day in days],
                    "image_ids": {str(day): image_id for day, image_id in zip(days, ids)},
                    "observed_at": datetime.now(timezone.utc).isoformat()}
        except Exception:
            raise RuntimeError("CHIRPS date discovery failed; no source files were promoted.") from None

    def fetch(self, days: list[date]) -> np.ndarray:
        import requests
        import time

        ee, definition = self.ee, self.definition
        expected = [f"{day:%Y%m%d}_precipitation" for day in days]
        a, _, west, _, e, north = definition["grid_transform"]
        h, w = definition["shape"]
        try:
            # Explicit filtering prevents an interior source gap becoming a spurious band.
            stamps = [ee.Date(str(day)).millis() for day in days]
            collection = (ee.ImageCollection(definition["collection"])
                          .filter(ee.Filter.inList("system:time_start", stamps))
                          .sort("system:time_start"))
            image = collection.select("precipitation").toBands()
            if image.bandNames().getInfo() != expected:
                raise ValueError("Provider returned unexpected date/band identity.")
            region = ee.Geometry.Rectangle([west, north + e*h, west + a*w, north],
                                           proj="EPSG:4326", geodesic=False)
            url = image.getDownloadURL({"region": region, "crs": "EPSG:4326",
                                        "crs_transform": definition["grid_transform"], "format": "NPY"})
            for attempt in range(3):
                try:
                    response = requests.get(url, timeout=180)
                    response.raise_for_status()
                    data = np.load(io.BytesIO(response.content), allow_pickle=False)
                    if data.shape != (h, w) or data.dtype.names != tuple(expected):
                        raise ValueError("Provider returned incompatible native grid/bands.")
                    values = np.stack([data[name] for name in expected]).astype(np.float32)
                    values[values == -9999] = np.nan
                    _validate_values(values, days, definition)
                    return values
                except requests.RequestException:
                    if attempt == 2:
                        raise
                    time.sleep(2 ** attempt)
        except Exception:
            # Signed URLs and provider errors can contain credentials. Do not expose them.
            raise RuntimeError("CHIRPS acquisition failed; validated archive preserved, rerun to resume.") from None
        raise AssertionError("unreachable")


class CHIRPSArchive:
    def __init__(self, study: Study):
        self.study = study
        self.definition = study.config["sources"]["chirps"]
        if (self.definition["collection"] != "UCSB-CHG/CHIRPS/DAILY"
                or self.definition["version"] != "2.0_final"
                or self.definition["bands"] != [BAND]
                or self.definition["units"] != ["mm/day"]):
            raise ValueError("This adapter only supports CHIRPS v2 Final precipitation in mm/day.")
        self.directory = _contained(study.root, self.definition["directory"])
        self.path = self.directory / "manifest.json"

    def read_manifest(self) -> dict:
        if not self.path.exists():
            # Never silently import unknown provenance from pre-existing arrays.
            if list(self.directory.glob(self.definition["pattern"])):
                raise ValueError("Unmanifested CHIRPS files; restore their provenance before refresh.")
            return {"years": []}
        manifest = json.loads(self.path.read_text())
        d = self.definition
        if (manifest.get("earth_engine_collection") != d["collection"]
                or manifest.get("unit") != "mm/day"
                or manifest.get("shape_lat_lon") != d["shape"]
                or manifest.get("grid_transform") != d["grid_transform"]
                or manifest.get("crs") != "EPSG:4326"
                or not isinstance(manifest.get("years"), list)):
            raise ValueError("CHIRPS manifest has incompatible source, units or grid.")
        if "source_identity" in manifest and manifest["source_identity"] != _identity(d):
            raise ValueError("CHIRPS source/version/grid identity changed.")
        return manifest

    def validated(self) -> tuple[dict, dict[int, dict]]:
        manifest = self.read_manifest()
        validated = {}
        for entry in manifest["years"]:
            year = int(entry["year"])
            if year in validated:
                raise ValueError("Duplicate annual CHIRPS partition in manifest.")
            path = _contained(self.study.root, entry["file"])
            if sha256(path) != entry["sha256"]:
                raise ValueError(f"CHIRPS checksum mismatch for {year}; restore or quarantine before acquisition.")
            days = _daily(path, self.definition)
            if len(days) != entry["days"] or any(day.year != year for day in days):
                raise ValueError("CHIRPS manifest year/day count mismatch.")
            if "dates" in entry and entry["dates"] != [str(day) for day in days]:
                raise ValueError("CHIRPS manifest actual dates mismatch.")
            validated[year] = {"entry": entry, "path": path, "dates": days}
        return manifest, validated

    def coverage(self, published: list[date], local: set[date]) -> dict:
        wanted = set(day_range(self.study.start, self.study.end))
        available = set(published)
        envelope = set(day_range(min(available), max(available))) if available else set()
        return {"requested": self.study.metadata()["requested"],
                "published_ranges": ranges(available), "validated_ranges": ranges(local),
                "published_not_local": ranges((wanted & available) - local),
                "interior_source_gaps": ranges((wanted & envelope) - available),
                "unpublished_tail": ranges({day for day in wanted if available and day > max(available)}),
                "unpublished_or_outside_coverage": ranges(wanted - envelope - available),
                "model_eligible": "not_assessed"}

    @contextmanager
    def _lock(self):
        import fcntl
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / ".refresh.lock").open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another CHIRPS refresh is active for this archive.") from None
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _save_values(self, directory: Path, days: list[date], values: np.ndarray) -> tuple[Path, str]:
        _validate_values(values, days, self.definition)
        latitude, longitude = _coordinates(self.definition)
        if not directory.resolve().is_relative_to(self.study.root):
            raise ValueError("CHIRPS output path escapes the repository.")
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f".{uuid.uuid4().hex}.tmp.npz"
        try:
            np.savez_compressed(temporary, precipitation_mm_day=values,
                                dates=np.array([str(day) for day in days]),
                                latitude=latitude, longitude=longitude)
            _daily(temporary, self.definition)
            digest = sha256(temporary)
            path = directory / f"{days[0].year}-{digest}.npz"
            if path.exists():
                if sha256(path) != digest:
                    raise ValueError("Corrupt content-addressed CHIRPS file.")
            else:
                with temporary.open("rb") as stream:
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            return path, digest
        finally:
            temporary.unlink(missing_ok=True)

    def _chunk(self, days: list[date], provider, *, refresh: bool) -> tuple[np.ndarray, Path]:
        identity = {"source": _identity(self.definition), "dates": [str(day) for day in days],
                    "refresh": refresh,
                    "as_of": self.study.as_of.isoformat() if refresh else None}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        metadata = self.directory / ".checkpoints" / f"{key}.json"
        if metadata.exists():
            cached = json.loads(metadata.read_text())
            path = _contained(self.study.root, cached["file"])
            if cached.get("identity") != identity or sha256(path) != cached.get("sha256"):
                raise ValueError("Corrupt or incompatible CHIRPS checkpoint; remove it explicitly to retry.")
            if _daily(path, self.definition) != days:
                raise ValueError("CHIRPS checkpoint date mismatch.")
            with np.load(path, allow_pickle=False) as data:
                return data[BAND].copy(), metadata
        values = np.asarray(provider.fetch(days))
        _validate_values(values, days, self.definition)
        path, digest = self._save_values(self.directory / ".checkpoints" / "data", days, values)
        _atomic_json(metadata, {"identity": identity, "file": path.relative_to(self.study.root).as_posix(),
                                "sha256": digest})
        return values, metadata

    def refresh(self, provider, *, refresh_start: str | None = None,
                refresh_end: str | None = None, chunk_days: int = 7) -> dict:
        """Discover first, then atomically publish all changed annual partitions.

        A failed run leaves the active manifest and every referenced file unchanged.
        Checkpoints survive failure, and successful explicit revisions retire their
        cache references so a later revision can really check the provider again.
        """
        if chunk_days < 1 or chunk_days > 7:
            raise ValueError("chunk_days must be between one and seven.")
        if bool(refresh_start) != bool(refresh_end):
            raise ValueError("Explicit revision refresh requires both start and end.")
        revision = set()
        if refresh_start:
            a, b = date.fromisoformat(refresh_start), date.fromisoformat(refresh_end)
            if not self.study.start <= a <= b <= self.study.end:
                raise ValueError("Revision interval must be bounded by the requested study interval.")
            revision = set(day_range(a, b))
        with self._lock():
            manifest, current = self.validated()
            discovery = provider.discover(self.study.start, self.study.end)
            published = [date.fromisoformat(value) for value in discovery["dates"]]
            wanted = set(day_range(self.study.start, self.study.end))
            if (len(published) != len(set(published)) or not set(published) <= wanted
                    or published != sorted(published)):
                raise ValueError("Provider discovery returned duplicate, unordered or out-of-request dates.")
            local = {day for item in current.values() for day in item["dates"]}
            pending = sorted((set(published) - local) | (set(published) & revision))
            if not pending:
                return {"coverage": self.coverage(published, local), "changed": [], "reused": len(local),
                        "discovery": discovery, "invalidations": []}
            updates, checkpoints = {}, []
            for year in sorted({day.year for day in pending}):
                days_for_year = [day for day in pending if day.year == year]
                for offset in range(0, len(days_for_year), chunk_days):
                    days = days_for_year[offset:offset + chunk_days]
                    # Revision and ordinary acquisition checkpoints have distinct identities.
                    values, checkpoint = self._chunk(days, provider, refresh=bool(revision))
                    updates.update(zip(days, values))
                    checkpoints.append(checkpoint)
            records = {year: value["entry"] for year, value in current.items()}
            changed, invalidations, retired = [], [], set(manifest.get("retired_files", []))
            for year in sorted({day.year for day in pending}):
                previous = current.get(year)
                old_values = {}
                if previous:
                    with np.load(previous["path"], allow_pickle=False) as data:
                        old_values = dict(zip(previous["dates"], data[BAND]))
                merged = old_values | {day: values for day, values in updates.items() if day.year == year}
                days = sorted(merged)
                values = np.stack([merged[day] for day in days])
                changed_dates = [day for day in days if day not in old_values
                                 or not np.array_equal(merged[day], old_values[day], equal_nan=True)]
                if not changed_dates:
                    continue
                path, digest = self._save_values(self.directory / ".partitions", days, values)
                old_hash = previous["entry"]["sha256"] if previous else None
                image_ids = dict((previous or {}).get("entry", {}).get("provider_image_ids", {}))
                image_ids.update({str(day): discovery.get("image_ids", {}).get(str(day))
                                  for day in days if day in updates})
                date_provenance = dict((previous or {}).get("entry", {}).get("date_provenance", {}))
                # Existing legacy dates retain their original entry provenance; a
                # new retrieval timestamp must never relabel their acquisition.
                for day in days:
                    if day in updates:
                        date_provenance[str(day)] = {
                            "retrieved_at": datetime.now(timezone.utc).isoformat(),
                            "provider_image_id": image_ids[str(day)],
                            "discovery_observed_at": discovery.get("observed_at"),
                            "as_of": self.study.as_of.isoformat()}
                    elif str(day) not in date_provenance:
                        date_provenance[str(day)] = {
                            "legacy_manifest_sha256": sha256(self.path),
                            "retrieved_at": previous["entry"].get("retrieved_at"),
                            "note": "Imported legacy provenance; original retrieval time may be unknown"}
                records[year] = {"year": year, "days": len(days), "dates": [str(day) for day in days],
                                 "file": path.relative_to(self.study.root).as_posix(), "sha256": digest,
                                 "retrieved_at": datetime.now(timezone.utc).isoformat(),
                                 "as_of": self.study.as_of.isoformat(),
                                 "provider_image_ids": image_ids, "date_provenance": date_provenance,
                                 "complete_calendar_year": days == day_range(date(year, 1, 1), date(year, 12, 31))}
                if previous:
                    retired.add(previous["entry"]["file"])
                event = {"year": year, "previous_sha256": old_hash, "sha256": digest,
                         "changed_ranges": ranges(changed_dates),
                         "reason": "explicit_bounded_revision" if revision else "extension_or_backfill"}
                changed.append(event)
                if old_hash:
                    invalidations.append({"source": "chirps", "upstream_file": previous["entry"]["file"],
                                          "upstream_sha256": old_hash,
                                          "affected_ranges": ranges(changed_dates),
                                          "action": "rebuild_downstream_consumers_of_upstream_sha256"})
            if changed:
                # Invalidation is promoted with inventory, not as a separate fallible write.
                next_manifest = manifest | {
                    "schema_version": 2, "source": "CHIRPS v2.0 Final daily, source-grid subset",
                    "earth_engine_collection": self.definition["collection"],
                    "source_identity": _identity(self.definition), "crs": "EPSG:4326",
                    "shape_lat_lon": self.definition["shape"],
                    "grid_transform": self.definition["grid_transform"], "unit": "mm/day",
                    "date_convention": "source_day_utc", "nodata": "NaN; source sentinel -9999",
                    "years": [records[year] for year in sorted(records)],
                    "retired_files": sorted(retired - {record["file"] for record in records.values()}),
                    "refresh_history": manifest.get("refresh_history", []) + [{
                        "as_of": self.study.as_of.isoformat(), "changes": changed,
                        "publication_discovery": discovery,
                        "refresh_interval": [refresh_start, refresh_end] if revision else None}],
                    "downstream_invalidations": manifest.get("downstream_invalidations", []) + invalidations,
                    "availability_evidence": "current assets, not historical release timestamps"}
                _atomic_json(self.path, next_manifest)
            for checkpoint in checkpoints:
                checkpoint.unlink(missing_ok=True)
            new_local = local | set(updates)
            return {"coverage": self.coverage(published, new_local), "changed": changed,
                    "reused": len(local - set(updates)), "discovery": discovery,
                    "invalidations": invalidations}


def load_development(study: Study, *, include_held_out: bool = False) -> dict:
    """Load validated source-day arrays, with held-out values excluded by default."""
    archive = CHIRPSArchive(study)
    manifest, current = archive.validated()
    allowed = set(day_range(study.start, study.end) if include_held_out else study.development_dates())
    result = {}
    for year, item in sorted(current.items()):
        indices = [i for i, day in enumerate(item["dates"]) if day in allowed]
        if not indices:
            continue
        with np.load(item["path"], allow_pickle=False) as data:
            result[year] = {"dates": [item["dates"][i] for i in indices],
                            "rain": data[BAND][indices].copy()}
    if not result:
        raise ValueError("No validated development rainfall is available; run a bounded CHIRPS backfill.")
    latitude, longitude = _coordinates(archive.definition)
    return {"annual": result, "latitude": latitude, "longitude": longitude,
            "manifest": manifest, "study": study.metadata(), "held_out_opened": include_held_out}


def rainfall_summary(loaded: dict, province_mask: np.ndarray) -> dict:
    """Actual-day denominators; incomplete months/years never enter climatology."""
    import pandas as pd
    mask = np.asarray(province_mask, dtype=bool)
    if mask.shape != (len(loaded["latitude"]), len(loaded["longitude"])) or not mask.any():
        raise ValueError("Province mask is empty or has an incompatible native grid.")
    daily, quality, annual_totals = [], [], {}
    for year, item in loaded["annual"].items():
        days, rain = item["dates"], item["rain"]
        pixels = rain[:, mask]
        counts = np.isfinite(pixels).sum(axis=1)
        means = np.divide(np.nansum(pixels, axis=1), counts,
                          out=np.full(len(days), np.nan), where=counts > 0)
        wet = np.divide(((pixels >= 1) & np.isfinite(pixels)).sum(axis=1) * 100, counts,
                        out=np.full(len(days), np.nan), where=counts > 0)
        complete = days == day_range(date(year, 1, 1), date(year, 12, 31))
        quality.append({"year": year, "days_present": len(days),
                        "calendar_days": len(day_range(date(year, 1, 1), date(year, 12, 31))),
                        "complete_calendar_year": complete, "first_day": str(days[0]),
                        "last_day": str(days[-1]), "province_missing_pct": 100*np.isnan(pixels).mean(),
                        "province_valid_pixel_days": int(np.isfinite(pixels).sum())})
        daily.append(pd.DataFrame({"date": pd.to_datetime(days), "mean_mm_day": means,
                                   "wet_pixel_pct": wet, "valid_pixels": counts}))
        if complete:
            totals = rain.sum(axis=0)  # Missing pixel-days propagate, never become zero totals.
            annual_totals[year] = totals
    frame = pd.concat(daily).set_index("date").sort_index()
    # Reindexing reports completely absent months too; min_count prevents a false zero.
    frame = frame.reindex(pd.date_range(frame.index.min(), frame.index.max(), freq="D"))
    monthly = frame["mean_mm_day"].resample("MS").agg(["sum", "count"])
    monthly["sum"] = frame["mean_mm_day"].resample("MS").sum(min_count=1)
    monthly.columns = ["observed_total_mm", "valid_days"]
    monthly["calendar_days"] = monthly.index.days_in_month
    monthly["complete_month"] = monthly.valid_days == monthly.calendar_days
    monthly["period_status"] = np.where(monthly.complete_month, "complete", "partial / missing")
    complete_months = monthly.loc[monthly.complete_month, "observed_total_mm"]
    climatology = complete_months.groupby(complete_months.index.month).agg(["mean", "min", "max", "count"])
    return {"daily": frame, "monthly": monthly, "quality": pd.DataFrame(quality).set_index("year"),
            "climatology_complete_months": climatology, "complete_annual_totals": annual_totals}
