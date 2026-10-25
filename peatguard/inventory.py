"""Read-only import of the existing source archive into a refresh plan.

Validation here concerns source-file integrity, not model eligibility or the
scientific validity of the candidate mask. No labels or target summaries are built.
"""

import csv
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import zipfile

import numpy as np
from rasterio.errors import RasterioError

from .study import Study, day_range


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ranges(days) -> list[dict]:
    result = []
    for day in sorted(set(days)):
        if result and (day - date.fromisoformat(result[-1]["end"])).days == 1:
            result[-1]["end"] = str(day)
            result[-1]["days"] += 1
        else:
            result.append({"start": str(day), "end": str(day), "days": 1})
    return result


def _local_path(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Inventory paths must remain inside the repository.")
    return path


def _read_manifest(root: Path, definition: dict) -> tuple[dict, list]:
    path = _local_path(root, definition["directory"]) / "manifest.json"
    if not path.exists():
        return {}, [{"kind": "missing_manifest", "path": path.relative_to(root).as_posix()}]
    try:
        manifest = json.loads(path.read_text())
        if not isinstance(manifest, dict):
            raise ValueError("Source manifest must be a JSON object.")
        actual = manifest.get("earth_engine_collection", manifest.get("earth_engine_image"))
        if actual != definition["collection"]:
            raise ValueError("Source identity differs from configured collection.")
        if definition["cadence"] == "static":
            for field in ("output", "sha256", "shape_rows_columns", "transform", "crs_wkt"):
                if field not in manifest:
                    raise ValueError(f"Static manifest is missing {field}.")
        else:
            entries = manifest.get("years")
            if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
                raise ValueError("Source manifest must contain a list of annual file records.")
        return manifest, []
    except (ValueError, OSError, TypeError) as error:
        return {}, [{"kind": "corrupt_manifest", "path": path.relative_to(root).as_posix(),
                     "reason": str(error)}]


def _daily(path: Path, definition: dict) -> list[date]:
    with np.load(path, allow_pickle=False) as data:
        days = [date.fromisoformat(str(value)) for value in data["dates"]]
        if not days or days != sorted(set(days)):
            raise ValueError("Daily dates must be nonempty, unique and chronological.")
        height, width = definition["shape"]
        a, _, west, _, e, north = definition["grid_transform"]
        latitude = north + e * (np.arange(height) + 0.5)
        longitude = west + a * (np.arange(width) + 0.5)
        if (data["latitude"].shape != latitude.shape or data["longitude"].shape != longitude.shape
                or not np.allclose(data["latitude"], latitude, rtol=0, atol=1e-8)
                or not np.allclose(data["longitude"], longitude, rtol=0, atol=1e-8)):
            raise ValueError("Coordinates do not match the configured native grid.")
        for band in definition["bands"]:
            values = data[band]
            if values.shape != (len(days), height, width) or not np.issubdtype(values.dtype, np.number):
                raise ValueError(f"Unexpected shape/type for {band}.")
            if np.isinf(values).any() or not np.isfinite(values).any(axis=(1, 2)).all():
                raise ValueError(f"Infinite or entirely missing daily values in {band}.")
            # Coastal/source masks remain NaN, rather than being interpreted as zero.
            if band in ("precipitation_mm_day", "soil_water_layer1_fraction") and np.nanmin(values) < 0:
                raise ValueError(f"Negative physical values in {band}.")
            if band == "soil_water_layer1_fraction" and np.nanmax(values) > 1:
                raise ValueError("Soil-water fraction exceeds one.")
    return days


def _raster(path: Path, definition: dict, entry: dict, manifest: dict) -> None:
    import rasterio

    with rasterio.open(path) as source:
        shape = entry.get("shape_lat_lon", manifest.get("shape_rows_columns"))
        transform = entry.get("transform", manifest.get("transform"))
        if source.descriptions != tuple(definition["bands"]) or source.crs is None:
            raise ValueError("Raster bands/CRS do not match source metadata.")
        if list(source.shape) != shape or not np.allclose(list(source.transform)[:6], transform):
            raise ValueError("Raster geometry differs from its manifest.")
        if definition["cadence"] == "annual":
            from rasterio.crs import CRS

            expected = CRS.from_proj4("+proj=sinu +R=6371007.181 +no_defs")
            if source.crs != expected or source.dtypes != ("uint8",) * 3 or source.nodata is not None:
                raise ValueError("Unexpected MODIS native CRS/type/nodata.")
        else:
            # The legacy GHSL WKT2 includes an extra semi_minor conversion
            # parameter that GeoTIFF drops. Compare normalized operational
            # projection parameters, in addition to exact file hash/transform.
            expected = rasterio.crs.CRS.from_wkt(manifest["crs_wkt"])
            if source.crs.to_dict() != expected.to_dict():
                raise ValueError("GHSL CRS differs from manifest.")
        # Read by block so integrity checking the large GHSL raster is memory bounded.
        for _, window in source.block_windows(1):
            values = source.read(window=window)
            if definition["cadence"] == "annual":
                if (not np.isin(values[0], range(1, 18)).all()
                        or not np.isin(values[1], range(10)).all()
                        or not np.isin(values[2], [1, 2]).all()):
                    raise ValueError("Invalid MODIS categorical values.")
            elif (source.dtypes != ("uint16",) or source.nodata != 65535
                  or np.any((values > 10000) & (values != 65535))):
                raise ValueError("Invalid GHSL values/type/nodata.")


def _source_files(study: Study, definition: dict) -> tuple[list, list, set]:
    manifest, diagnostics = _read_manifest(study.root, definition)
    entries = manifest.get("years", [])
    if definition["cadence"] == "static" and manifest:
        entries = [{"file": manifest["output"], "sha256": manifest["sha256"]}]
    by_path = {}
    for entry in entries:
        try:
            path = _local_path(study.root, entry["file"])
            if path in by_path:
                raise ValueError("Duplicate file entry in source manifest.")
            by_path[path] = entry
        except (ValueError, KeyError, TypeError) as error:
            diagnostics.append({"kind": "corrupt_manifest", "reason": str(error)})
    directory = _local_path(study.root, definition["directory"])
    # Retained superseded archives remain on disk for reproducibility. Only
    # explicitly retired, root-contained paths are excluded from discovery;
    # an active manifest entry always takes precedence over retirement.
    retired = set()
    retirement = manifest.get("retired_files", [])
    if not isinstance(retirement, list):
        diagnostics.append({"kind": "corrupt_manifest", "reason": "retired_files must be a list."})
        retirement = []
    for item in retirement:
        try:
            if not isinstance(item, str):
                raise ValueError("Retired file path must be a string.")
            retired.add(_local_path(study.root, item))
        except (ValueError, TypeError) as error:
            diagnostics.append({"kind": "corrupt_manifest", "reason": str(error)})
    paths = set(by_path) | (set(directory.glob(definition["pattern"])) - retired)
    files, validated = [], set()
    for path in sorted(paths):
        record = {"path": path.relative_to(study.root).as_posix()}
        entry = by_path.get(path)
        try:
            path = _local_path(study.root, record["path"])
            if not path.exists():
                record["status"] = "missing_file"
                files.append(record)
                continue
            record.update(size_bytes=path.stat().st_size, sha256=sha256(path))
            if entry is None:
                raise ValueError("Unmanifested file: source provenance has not been imported.")
            if record["sha256"] != entry.get("sha256"):
                raise ValueError("Checksum differs from manifest.")
            if definition["cadence"] == "daily":
                days = _daily(path, definition)
                if any(day.year != entry["year"] for day in days) or len(days) != entry["days"]:
                    raise ValueError("Daily partition year/count differs from manifest.")
                record["retrieved_ranges"] = ranges(days)
                overlap = validated.intersection(days)
                if overlap:
                    raise ValueError("Duplicate dates across source partitions.")
                validated.update(days)
                record["complete_calendar_year"] = days == day_range(date(entry["year"], 1, 1),
                                                                      date(entry["year"], 12, 31))
            else:
                _raster(path, definition, entry, manifest)
                if definition["cadence"] == "annual":
                    year = int(entry["year"])
                    if year in validated:
                        raise ValueError("Duplicate map year in source partitions.")
                    validated.add(year)
                    record["map_year"] = year
                else:
                    record["reference_epoch"] = 2020
            record["status"] = "validated"
        except (OSError, ValueError, KeyError, TypeError, EOFError, zipfile.BadZipFile, RasterioError) as error:
            record.update(status="corrupt_or_unverified", reason=str(error))
            diagnostics.append({"kind": "corrupt_input", "path": record["path"], "reason": str(error)})
        files.append(record)
    return files, diagnostics, validated


def _firms(study: Study, definition: dict) -> dict:
    files, diagnostics = [], []
    # NASA's public named-MODIS NRT CSV omits instrument/type columns.
    # Accept a missing sensor only when an explicit interval declaration is
    # tied to these exact source bytes; this does not establish coverage.
    sensor_metadata = {}
    ledger_path = study.root / definition["directory"] / "intervals.json"
    if ledger_path.exists():
        try:
            ledger = json.loads(ledger_path.read_text())
            for item in ledger["intervals"]:
                if (item.get("declared_instrument") == "MODIS"
                        and item.get("product") == definition["collection"]):
                    candidate = ledger_path.parent / item["path"]
                    candidate = _local_path(study.root, str(candidate))
                    sensor_metadata[candidate] = item["sha256"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            diagnostics.append({"kind": "invalid_interval_sensor_metadata", "reason": str(error)})
    for path in sorted(_local_path(study.root, definition["directory"]).glob(definition["pattern"])):
        record = {"path": path.relative_to(study.root).as_posix()}
        try:
            path = _local_path(study.root, record["path"])
            record["sha256"] = sha256(path)
            dates = []
            with path.open(newline="") as stream:
                reader = csv.DictReader(stream)
                required = {"latitude", "longitude", "acq_date", "acq_time", "satellite",
                            "instrument", "confidence", "version"}
                if sensor_metadata.get(path) == record["sha256"]:
                    required.remove("instrument")
                if not required <= set(reader.fieldnames or []):
                    raise ValueError("FIRMS CSV is missing required columns.")
                for row in reader:
                    if any(row.get(field) is None for field in required):
                        raise ValueError("FIRMS CSV has an incomplete row.")
                    dates.append(date.fromisoformat(row["acq_date"]))
                    stamp = row["acq_time"].zfill(4)
                    if (len(stamp) != 4 or not stamp.isdigit()
                            or int(stamp[:2]) > 23 or int(stamp[2:]) > 59):
                        raise ValueError("Invalid FIRMS UTC acquisition time.")
                    lat, lon, confidence = (float(row[field]) for field in
                                             ("latitude", "longitude", "confidence"))
                    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= confidence <= 100):
                        raise ValueError("Invalid FIRMS coordinates or confidence.")
            record.update(status="structure_checked_coverage_unknown", rows=len(dates),
                          detection_date_range=[str(min(dates)), str(max(dates))] if dates else None)
        except (OSError, ValueError, KeyError, csv.Error) as error:
            record.update(status="corrupt_or_unverified", reason=str(error))
            diagnostics.append({"kind": "corrupt_input", "path": record["path"], "reason": str(error)})
        files.append(record)
    diagnostics.append({"kind": "unknown_export_coverage",
                        "reason": "Detection dates cannot establish successful query/export intervals; "
                                  "no negative labels or complete daily coverage can be inferred."})
    return {"files": files, "diagnostics": diagnostics, "validated_ranges": [],
            "label_eligibility": "unknown_until_interval_ingestion", "availability": {"status": "not_checked"}}


def discover_availability(study: Study, project: str | None) -> dict:
    """Optional current-provider metadata reads only; never download images."""
    result = {"observed_at": datetime.now(timezone.utc).isoformat(), "sources": {},
              "evidence": "current_assets_not_historical_release_evidence"}
    definitions = {key: value for key, value in study.config["sources"].items()
                   if value["cadence"] in ("daily", "annual")}
    if not project:
        result["sources"] = {key: {"status": "missing_project", "dates": []} for key in definitions}
        return result
    try:
        import ee
        ee.Initialize(project=project)
    except Exception:
        # Provider exception text can contain credential paths or tokens. Do not emit it.
        result["sources"] = {key: {"status": "authentication_or_initialization_failed", "dates": []}
                             for key in definitions}
        return result
    for key, definition in definitions.items():
        try:
            start = date(study.start.year, 1, 1) if definition["cadence"] == "annual" else study.start
            end = date.fromordinal(study.end.toordinal() + 1)
            times = (ee.ImageCollection(definition["collection"]).filterDate(str(start), str(end))
                     .aggregate_array("system:time_start").getInfo())
            dates = sorted({datetime.fromtimestamp(value / 1000, timezone.utc).date().isoformat()
                            for value in times})
            result["sources"][key] = {"status": "discovered", "collection": definition["collection"],
                                      "dates": dates}
        except Exception:
            result["sources"][key] = {"status": "provider_discovery_failed", "dates": []}
    return result


def _refresh_actions(sources: dict) -> list[dict]:
    actions = []
    for key, source in sources.items():
        for record in source["files"]:
            if record["status"] == "validated":
                actions.append({"source": key, "action": "reuse_validated_file", "path": record["path"]})
            elif record["status"] in ("missing_file", "corrupt_or_unverified"):
                actions.append({"source": key, "action": "inspect_or_reacquire", "path": record["path"]})
        if source["cadence"] == "daily":
            if source.get("availability_unknown_ranges"):
                actions.append({"source": key, "action": "discover_source_dates",
                                "ranges": source["availability_unknown_ranges"]})
            for field, action in [("published_not_local", "download_missing"),
                                  ("source_interior_gaps", "blocked_source_gap"),
                                  ("unpublished_tail", "wait_for_publication")]:
                if source.get(field):
                    actions.append({"source": key, "action": action, "ranges": source[field]})
            # No images in the queried interval, or dates preceding the first
            # returned image, need an explicit coverage diagnostic too.
            outside = source.get("unpublished_or_outside_source_coverage", [])
            if outside:
                actions.append({"source": key, "action": "check_source_coverage_or_publication",
                                "ranges": outside})
        elif source["cadence"] == "annual":
            missing = set(source["requested_missing_map_years"])
            available = source["published_map_years"]
            if missing and available is None:
                actions.append({"source": key, "action": "discover_map_years", "years": sorted(missing)})
            elif missing:
                for years, action in [(missing & set(available), "download_maps"),
                                      (missing - set(available), "blocked_unpublished_maps")]:
                    if years:
                        actions.append({"source": key, "action": action, "years": sorted(years)})
        elif source["cadence"] == "detections":
            actions.append({"source": key, "action": "establish_export_or_query_coverage",
                            "note": "Import an archive with declared coverage or configure FIRMS access; "
                                    "do not derive negative labels from absent detections."})
        elif not source["validated_static"]:
            actions.append({"source": key, "action": "validate_or_acquire_static_source"})
    return actions


def build_inventory(study: Study, *, availability: dict | None = None) -> dict:
    report = {"study": study.metadata(), "read_only": True, "sources": {}, "boundaries": {}}
    report["availability_evidence"] = {
        "observed_at": (availability or {}).get("observed_at"),
        "note": "Asset dates are not historical release timestamps or proof of availability at issuance.",
    }
    report["credentials"] = {
        "earth_engine": "configured_not_verified" if any([
            (Path.home() / ".config/earthengine/credentials").exists(),
            (Path.home() / ".config/gcloud/application_default_credentials.json").exists(),
            bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")),
        ]) else "no_local_credential_hint; configure access before provider discovery",
        "firms": "configured_not_verified" if os.environ.get("FIRMS_MAP_KEY") else "missing_FIRMS_MAP_KEY",
        "note": "No secret values are read or printed; credentials are not needed for local inventory.",
    }
    wanted = set(day_range(study.start, study.end))
    for key, definition in study.config["sources"].items():
        base = {"collection": definition["collection"], "version": definition["version"],
                "cadence": definition["cadence"]}
        if definition["cadence"] == "detections":
            report["sources"][key] = base | _firms(study, definition)
            continue
        files, diagnostics, valid = _source_files(study, definition)
        source = base | {"files": files, "diagnostics": diagnostics}
        published = (availability or {}).get("sources", {}).get(key, {"status": "not_checked"})
        source["availability"] = {"status": published["status"]}
        known = published["status"] == "discovered"
        if known and published.get("collection") != definition["collection"]:
            raise ValueError(f"Availability source identity mismatch: {key}")
        dates = {date.fromisoformat(value) for value in published.get("dates", [])} if known else set()
        if definition["cadence"] == "daily":
            missing = wanted - valid
            source.update(validated_ranges=ranges(valid), requested_missing_ranges=ranges(missing),
                          retrieved_ranges=[interval for record in files
                                            for interval in record.get("retrieved_ranges", [])],
                          validated_requested_days=len(wanted & valid),
                          published_ranges=ranges(dates) if known else None)
            if known:
                envelope = set(day_range(min(dates), max(dates))) if dates else set()
                source["published_not_local"] = ranges((dates & wanted) - valid)
                source["source_interior_gaps"] = ranges((envelope & wanted) - dates)
                source["unpublished_tail"] = ranges({d for d in wanted if dates and d > max(dates)})
                source["unpublished_or_outside_source_coverage"] = ranges(wanted - dates - envelope)
            else:
                source["availability_unknown_ranges"] = ranges(missing)
            local_envelope = set(day_range(min(valid), max(valid))) if valid else set()
            source["local_interior_gaps"] = ranges((wanted & local_envelope) - valid)
        elif definition["cadence"] == "annual":
            years = {d.year for d in wanted}
            source.update(validated_map_years=sorted(valid), requested_missing_map_years=sorted(years - valid),
                          published_map_years=sorted({d.year for d in dates}) if known else None,
                          assignment_policy="strict_same_year_retrospective; no implicit carry-forward")
        else:
            source.update(reference_epoch=2020, validated_static=any(f["status"] == "validated" for f in files))
        report["sources"][key] = source
    for key, value in study.config["boundaries"].items():
        record = {"path": value}
        try:
            path = _local_path(study.root, value)
            document = json.loads(path.read_text())
            if document["type"] != "FeatureCollection" or not document["features"]:
                raise ValueError("Expected a nonempty GeoJSON FeatureCollection.")
            record.update(status="structure_checked", sha256=sha256(path), features=len(document["features"]))
        except (OSError, ValueError, KeyError, TypeError) as error:
            record.update(status="missing_or_corrupt", reason=str(error))
        report["boundaries"][key] = record
    report["model_eligibility"] = "not_assessed; file integrity does not establish label/feature readiness"
    report["plan"] = _refresh_actions(report["sources"])
    return report
