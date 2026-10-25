"""Bounded ERA5-Land UTC acquisition and development EDA.

A manifest is the atomic commit pointer. Immutable replacement files mean an
interruption cannot invalidate the previously committed archive. Nothing runs on
import; the provider adapter is optional and initialized only for live refreshes.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import uuid

import numpy as np

from .inventory import sha256, ranges
from .study import Study, day_range

COLLECTION = "ECMWF/ERA5_LAND/DAILY_AGGR"
SOURCE_BANDS = ("temperature_2m", "volumetric_soil_water_layer_1")
BANDS = ("temperature_2m_K", "soil_water_layer1_fraction")
UNITS = ("K", "m3/m3")


def definition(study):
    value = study.config["sources"]["era5_land"]
    if (value["collection"] != COLLECTION or tuple(value["bands"]) != BANDS
            or tuple(value["units"]) != UNITS):
        raise ValueError("Incompatible ERA5-Land source, bands or units.")
    return value


def coordinates(spec):
    h, w = spec["shape"]
    a, b, west, d, e, north = spec["grid_transform"]
    if b != 0 or d != 0 or a <= 0 or e >= 0:
        raise ValueError("Expected north-to-south rectilinear native grid.")
    return north + e * (np.arange(h) + .5), west + a * (np.arange(w) + .5)


def _identity(spec):
    return {key: spec[key] for key in ("collection", "version", "bands", "units", "shape", "grid_transform")}


def validate_arrays(days, values, spec, mask=None):
    """Reject wrong dates, geometry, units/ranges and changing source masks."""
    if not days or days != sorted(set(days)):
        raise ValueError("ERA5 dates must be nonempty, unique and chronological.")
    if values.shape != (len(days), 2, *spec["shape"]) or not np.issubdtype(values.dtype, np.number):
        raise ValueError("ERA5 shape/bands do not match the configured source grid.")
    if np.isinf(values).any() or not np.isfinite(values).any(axis=(2, 3)).all():
        raise ValueError("Infinite or entirely missing ERA5 band/day.")
    if np.nanmin(values[:, 0]) <= 0:
        raise ValueError("ERA5 temperature must be stored in kelvin, above zero.")
    if np.nanmin(values[:, 1]) < 0 or np.nanmax(values[:, 1]) > 1:
        raise ValueError("ERA5 layer-1 water must be a fraction within [0, 1].")
    finite = np.isfinite(values)
    expected = finite[0] if mask is None else mask
    if not np.all(finite == expected):
        raise ValueError("Unexpected per-period missingness: ERA5 source mask changed.")
    return expected


def read_partition(path, spec, expected_hash=None):
    if expected_hash is not None and sha256(path) != expected_hash:
        raise ValueError(f"Checksum differs for {path.name}; inspect or reacquire explicitly.")
    with np.load(path, allow_pickle=False) as source:
        if set(source.files) != {*BANDS, "dates", "latitude", "longitude"}:
            raise ValueError("ERA5 partition has unexpected/missing bands or metadata.")
        days = [date.fromisoformat(str(value)) for value in source["dates"]]
        lat, lon = coordinates(spec)
        if (source["latitude"].shape != lat.shape or source["longitude"].shape != lon.shape
                or not np.allclose(source["latitude"], lat, rtol=0, atol=1e-8)
                or not np.allclose(source["longitude"], lon, rtol=0, atol=1e-8)):
            raise ValueError("ERA5 coordinates differ from the configured native grid.")
        values = np.stack([source[band] for band in BANDS], axis=1)
    validate_arrays(days, values, spec)
    return days, values


def _inside(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError("ERA5 paths must stay inside the study repository.")
    return path


def archive(study):
    """Import and validate legacy or refreshed manifests without rewriting them."""
    spec = definition(study)
    directory = _inside(study.root, spec["directory"])
    path = directory / "manifest.json"
    if not path.exists():
        if list(directory.glob(spec["pattern"])):
            raise ValueError("Unmanifested ERA5 files exist; establish provenance before refreshing.")
        return {}, {}, None
    manifest = json.loads(path.read_text())
    if manifest.get("earth_engine_collection") != COLLECTION:
        raise ValueError("ERA5 manifest source does not match configuration.")
    if manifest.get("source_identity", _identity(spec)) != _identity(spec):
        raise ValueError("ERA5 manifest grid/version/units differ from configuration.")
    for key, expected in (("shape_lat_lon", spec["shape"]), ("grid_transform", spec["grid_transform"])):
        if key in manifest and manifest[key] != expected:
            raise ValueError(f"ERA5 manifest {key} differs from configuration.")
    partitions, mask = {}, None
    for entry in manifest.get("years", []):
        year = entry["year"]
        if year in partitions:
            raise ValueError("Duplicate ERA5 annual partition.")
        source = _inside(study.root, entry["file"])
        days, values = read_partition(source, spec, entry["sha256"])
        if len(days) != entry["days"] or any(day.year != year for day in days):
            raise ValueError("ERA5 partition year/day count differs from manifest.")
        mask = validate_arrays(days, values, spec, mask)
        partitions[year] = (days, values, entry)
    if "years" not in manifest:
        raise ValueError("ERA5 manifest lacks annual inventory.")
    return manifest, partitions, mask


def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".era5-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _save_npz(path, days, values, spec):
    lat, lon = coordinates(spec)
    with path.open("wb") as stream:
        np.savez_compressed(stream, **dict(zip(BANDS, values.transpose(1, 0, 2, 3))),
                            dates=np.array([str(day) for day in days]), latitude=lat, longitude=lon)
        stream.flush()
        os.fsync(stream.fileno())


def _chunks(days, size):
    chunk = []
    for day in days:
        if chunk and (len(chunk) == size or day != chunk[-1] + timedelta(days=1)):
            yield chunk
            chunk = []
        chunk.append(day)
    if chunk:
        yield chunk


def refresh(study: Study, provider, *, revise=None, revision_id=None, chunk_days=7, dry_run=False):
    """Discover before acquiring; publish all requested changes as one transaction.

    Provider protocol: discover(start, end) -> list[date], fetch(days) -> array
    [day, two bands, latitude, longitude]. Endpoints are inclusive. A revision
    interval is explicit and bounded. Reuse a printed revision_id after failure
    to resume that revision's checkpoints; a new revision gets a fresh namespace.
    """
    if not 1 <= chunk_days <= 7:
        raise ValueError("chunk_days must be between one and seven.")
    spec = definition(study)
    manifest, partitions, mask = archive(study)
    if revise:
        a, b = [date.fromisoformat(value) if isinstance(value, str) else value for value in revise]
        if not study.start <= a <= b <= study.end:
            raise ValueError("Revision dates must be ordered inside the requested interval.")
        revision_days = set(day_range(a, b))
    else:
        if revision_id:
            raise ValueError("revision_id requires an explicit revision interval.")
        revision_days = set()
    published = provider.discover(study.start, study.end)
    if (published != sorted(set(published))
            or any(not study.start <= day <= study.end for day in published)):
        raise ValueError("Provider discovery returned duplicate, unordered or out-of-bounds dates.")
    wanted = set(day_range(study.start, study.end))
    available = set(published)
    local = {day for days, _, _ in partitions.values() for day in days}
    missing = wanted - available
    interior = {day for day in missing if published and published[0] < day < published[-1]}
    tail = {day for day in missing if published and day > published[-1]}
    selected = sorted((available - local) | (available & revision_days))
    run_id = revision_id or (uuid.uuid4().hex if revise else "incremental")
    plan = {"study": study.metadata(), "published_ranges": ranges(available),
            "published_days": len(available), "local_days": len(local & wanted),
            "interior_gaps": ranges(interior), "unpublished_tail": ranges(tail),
            "unavailable_other": ranges(missing - interior - tail),
            "acquire_ranges": ranges(selected), "acquire_days": len(selected),
            "revision_id": run_id if revise else None,
            "availability_evidence": "current_assets_not_historical_release_evidence"}
    if dry_run or not selected:
        return dict(plan, status="dry_run" if dry_run else "unchanged")
    directory = _inside(study.root, spec["directory"])
    directory.mkdir(parents=True, exist_ok=True)
    # A directory lock prevents two writers from publishing incompatible snapshots.
    lock = directory / ".refresh.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError("ERA5 refresh lock exists; check for an active writer before removing it.") from None
    try:
        # Re-read after locking; never overwrite an archive changed since planning.
        current = directory / "manifest.json"
        if (json.loads(current.read_text()) if current.exists() else {}) != manifest:
            raise RuntimeError("ERA5 inventory changed during planning; retry refresh.")
        fetched = {}
        for days in _chunks(selected, chunk_days):
            key = hashlib.sha256(json.dumps({"source": _identity(spec), "days": list(map(str, days)),
                                             "revision": run_id}, sort_keys=True).encode()).hexdigest()
            cache = directory / ".checkpoints" / key
            data_path, receipt_path = cache / "data.npz", cache / "receipt.json"
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text())
                if receipt["source_identity"] != _identity(spec) or receipt["dates"] != list(map(str, days)):
                    raise ValueError("ERA5 checkpoint identity/date mismatch.")
                actual_days, values = read_partition(data_path, spec, receipt["sha256"])
                if actual_days != days:
                    raise ValueError("ERA5 checkpoint dates do not match request.")
            else:
                try:
                    values = provider.fetch(days)
                except Exception:
                    raise RuntimeError(f"ERA5 acquisition failed for {days[0]} through {days[-1]}; "
                                       f"last-good inventory preserved; revision_id={plan['revision_id']}") from None
                validate_arrays(days, values, spec, mask)
                cache.mkdir(parents=True, exist_ok=True)
                temporary = cache / f".{uuid.uuid4().hex}.npz"
                try:
                    _save_npz(temporary, days, values, spec)
                    read_partition(temporary, spec)
                    os.replace(temporary, data_path)
                finally:
                    temporary.unlink(missing_ok=True)
                _atomic_json(receipt_path, {"source_identity": _identity(spec), "dates": list(map(str, days)),
                                           "sha256": sha256(data_path),
                                           "retrieved_at": datetime.now(timezone.utc).isoformat()})
            mask = validate_arrays(days, values, spec, mask)
            fetched.update(zip(days, values))
        records, revisions = [], []
        for year in sorted(set(partitions) | {day.year for day in fetched}):
            old_days, old_values, old_entry = partitions.get(year, ([], [], None))
            replacements = {day: values for day, values in fetched.items() if day.year == year}
            if not replacements:
                records.append(old_entry)
                continue
            merged = dict(zip(old_days, old_values))
            changed = [day for day in replacements if day in merged and
                       not np.array_equal(merged[day], replacements[day], equal_nan=True)]
            merged.update(replacements)
            days = sorted(merged)
            values = np.stack([merged[day] for day in days])
            validate_arrays(days, values, spec, mask)
            temporary = directory / f".era5-{uuid.uuid4().hex}.npz"
            try:
                _save_npz(temporary, days, values, spec)
                read_partition(temporary, spec)
                digest = sha256(temporary)
                target = directory / ".partitions" / f"era5_land_kalimantan_{year}_{digest[:20]}.npz"
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            records.append({"year": year, "days": len(days), "file": target.relative_to(study.root).as_posix(),
                            "sha256": digest, "coverage": ranges(days),
                            "complete_calendar_year": len(days) == (366 if calendar.isleap(year) else 365),
                            "retrieved_at": datetime.now(timezone.utc).isoformat(),
                            "source_bands": list(SOURCE_BANDS), "units": list(UNITS)})
            if changed:
                revisions.append({"year": year, "changed_dates": list(map(str, changed)),
                                  "old_sha256": old_entry["sha256"], "new_sha256": digest,
                                  "invalidate_downstream_source_sha256": old_entry["sha256"]})
        new_manifest = dict(manifest, schema_version=2, source="ERA5-Land daily UTC means via Earth Engine",
                            earth_engine_collection=COLLECTION, source_identity=_identity(spec),
                            chosen_variables=dict(zip(BANDS, UNITS)), daily_window="00:00–23:00 UTC hourly means",
                            crs="EPSG:4326", grid_transform=spec["grid_transform"], shape_lat_lon=spec["shape"],
                            years=records)
        new_manifest["retired_files"] = sorted(set(manifest.get("retired_files", [])) |
                                               {partitions[item["year"]][2]["file"] for item in records
                                                if item["year"] in partitions and
                                                item["file"] != partitions[item["year"]][2]["file"]})
        new_manifest["runs"] = manifest.get("runs", []) + [dict(plan, retrieved_at=datetime.now(timezone.utc).isoformat())]
        new_manifest["revisions"] = manifest.get("revisions", []) + revisions
        new_manifest["downstream_invalidation"] = sorted(set(manifest.get("downstream_invalidation", [])) |
                                                        {item["old_sha256"] for item in revisions})
        _atomic_json(current, new_manifest)
        return dict(plan, status="committed", revisions=revisions)
    finally:
        lock.rmdir()


class EarthEngineProvider:
    """Live adapter; metadata discovery never assumes the latest requested day exists."""
    def __init__(self, study, project):
        import ee
        self.ee, self.spec = ee, definition(study)
        if not project:
            raise ValueError("A configured free Earth Engine project is required.")
        try:
            ee.Initialize(project=project)
        except Exception:
            raise RuntimeError("Earth Engine initialization failed; check project and credentials locally.") from None

    def discover(self, start, end):
        try:
            times = (self.ee.ImageCollection(COLLECTION)
                     .filterDate(str(start), str(end + timedelta(days=1)))
                     .aggregate_array("system:time_start").getInfo())
            days = [datetime.fromtimestamp(value / 1000, timezone.utc).date() for value in times]
            if len(days) != len(set(days)):
                raise ValueError("Duplicate source dates.")
            return sorted(days)
        except Exception:
            raise RuntimeError("ERA5 metadata discovery failed; no data were acquired.") from None

    def fetch(self, days):
        import requests
        ee = self.ee
        collection = (ee.ImageCollection(COLLECTION).filterDate(str(days[0]), str(days[-1] + timedelta(days=1)))
                      .select(list(SOURCE_BANDS)).sort("system:time_start")
                      .map(lambda image: image.unmask(-9999, False)))
        image = collection.toBands()
        names = tuple(f"{day:%Y%m%d}_{band}" for day in days for band in SOURCE_BANDS)
        if tuple(image.bandNames().getInfo()) != names:
            raise ValueError("Discovered source dates/bands changed during acquisition.")
        h, w = self.spec["shape"]
        a, _, west, _, e, north = self.spec["grid_transform"]
        inset = min(a, -e) / 10
        region = ee.Geometry.Rectangle([west + inset, north + e*h + inset,
                                        west + a*w - inset, north - inset],
                                       proj="EPSG:4326", geodesic=False)
        url = image.getDownloadURL({"region": region, "crs": "EPSG:4326",
                                    "crs_transform": self.spec["grid_transform"], "format": "NPY"})
        response = requests.get(url, timeout=180)
        response.raise_for_status()
        with io.BytesIO(response.content) as stream:
            structured = np.load(stream, allow_pickle=False)
        if structured.shape != (h, w) or structured.dtype.names != names:
            raise ValueError("Unexpected ERA5 source grid/bands.")
        values = np.stack([structured[name] for name in names]).astype(np.float32)
        values[values == -9999] = np.nan
        return values.reshape(len(days), 2, h, w)


def load_development(study):
    """Validate archive, then expose only configured development source days."""
    manifest, partitions, _ = archive(study)
    allowed = set(study.development_dates())
    days, arrays = [], []
    for year in sorted(partitions):
        source_days, values, _ = partitions[year]
        for index, day in enumerate(source_days):
            if day in allowed:
                days.append(day)
                arrays.append(values[index])
    if not days:
        raise FileNotFoundError("No validated ERA5 development days: run the bounded refresh first.")
    values = np.stack(arrays)
    return {"dates": days, "values": values, "latitude": coordinates(definition(study))[0],
            "longitude": coordinates(definition(study))[1], "manifest": manifest,
            "missing_development_ranges": ranges(allowed - set(days)),
            "caption": f"{days[0]}–{days[-1]} UTC; {len(days)}/{len(allowed)} requested development days; "
                       "partial periods use observed valid-day denominators"}


def summarize_development(loaded, mask):
    """Coverage-aware spatial/daily/monthly means; no zero-filled missing periods."""
    import pandas as pd
    values = loaded["values"]
    if mask.shape != values.shape[2:] or not mask.any():
        raise ValueError("Province mask is empty or differs from the native grid.")
    pixels = values[:, :, mask]
    counts = np.isfinite(pixels).sum(axis=2)
    means = np.divide(np.nansum(pixels, axis=2), counts,
                      out=np.full(counts.shape, np.nan, dtype=float), where=counts > 0)
    frame = pd.DataFrame({"temperature_C": means[:, 0] - 273.15,
                          "soil_water_fraction": means[:, 1],
                          "temperature_valid_pixels": counts[:, 0], "soil_valid_pixels": counts[:, 1]},
                         index=pd.DatetimeIndex(loaded["dates"], name="date"))
    monthly = frame[["temperature_C", "soil_water_fraction"]].resample("MS").agg(["mean", "count"])
    monthly["calendar_days"] = monthly.index.days_in_month
    for band in ("temperature_C", "soil_water_fraction"):
        monthly[(band, "complete_month")] = monthly[(band, "count")] == monthly.index.days_in_month
    rows = []
    for year, group in frame.groupby(frame.index.year):
        expected = 366 if calendar.isleap(int(year)) else 365
        row = {"year": year, "observed_days": len(group), "calendar_days": expected,
               "complete_year": len(group) == expected}
        for band in ("temperature_C", "soil_water_fraction"):
            row.update({f"{band}_valid_days": int(group[band].count()),
                        f"{band}_min": group[band].min(), f"{band}_max": group[band].max()})
        rows.append(row)
    quality = pd.DataFrame(rows).set_index("year")
    valid = np.isfinite(values).sum(axis=0)
    maps = np.divide(np.nansum(values, axis=0), valid, out=np.full(valid.shape, np.nan), where=valid > 0)
    maps[0] -= 273.15
    return frame, monthly, quality, maps, valid
