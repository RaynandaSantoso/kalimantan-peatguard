"""Download CHIRPS v2 Final daily Kalimantan subsets via Google Earth Engine.

Run: .venv/bin/python scripts/download_chirps_v2.py --project kalimantan-peat

The output retains the source 0.05-degree grid. It is not resampled to the
paper's 5 x 5 km grid and is not clipped to peatland. Seven-day checkpoints
allow a failed run to resume without requesting completed chunks again.
"""

from __future__ import annotations

import argparse
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import hashlib
import io
import json
from pathlib import Path
import time

import ee
import numpy as np
import requests


COLLECTION = "UCSB-CHG/CHIRPS/DAILY"
BOUNDS = (108.5, -5.0, 119.5, 4.5)  # W, S, E, N
TRANSFORM = [0.05, 0, BOUNDS[0], 0, -0.05, BOUNDS[3]]
HEIGHT, WIDTH = 190, 220
OUTPUT = Path("data/interim/chirps_v2")


def month_days(year: int, month: int) -> list[date]:
    return [date(year, month, day) for day in range(1, monthrange(year, month)[1] + 1)]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_days(days: list[date]) -> np.ndarray:
    start = str(days[0])
    end = str(days[-1] + timedelta(days=1))
    collection = (ee.ImageCollection(COLLECTION).filterDate(start, end)
                  .sort("system:time_start"))
    image = collection.toBands()
    expected = [f"{day:%Y%m%d}_precipitation" for day in days]
    bands = image.bandNames().getInfo()
    if bands != expected:
        raise ValueError(f"Unexpected CHIRPS bands for {start} to {end}: {bands}")
    region = ee.Geometry.Rectangle(BOUNDS, proj="EPSG:4326", geodesic=False)
    url = image.getDownloadURL({"region": region, "crs": "EPSG:4326",
                                "crs_transform": TRANSFORM, "format": "NPY"})
    for attempt in range(5):
        try:
            response = requests.get(url, timeout=180)
            response.raise_for_status()
            structured = np.load(io.BytesIO(response.content), allow_pickle=False)
            if structured.shape != (HEIGHT, WIDTH) or structured.dtype.names != tuple(expected):
                raise ValueError(f"Unexpected NPY grid or bands: {structured.shape}, "
                                 f"{structured.dtype.names}")
            values = np.stack([structured[band] for band in expected]).astype(np.float32)
            values[values < 0] = np.nan  # CHIRPS no-data sentinel is -9999
            return values
        except (requests.RequestException, ValueError) as error:
            if attempt == 4:
                raise RuntimeError(f"Failed to download {start} to {end}") from error
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def download_year(year: int) -> dict:
    days = [day for month in range(1, 13) for day in month_days(year, month)]
    result = OUTPUT / f"chirps_v2_kalimantan_{year}.npz"
    if result.exists():
        print(f"{year}: already saved", flush=True)
    else:
        values = np.full((len(days), HEIGHT, WIDTH), np.nan, dtype=np.float32)
        chunks = [days[i:i + 7] for i in range(0, len(days), 7)]
        def chunk_path(chunk: list[date]) -> Path:
            return OUTPUT / f".{chunk[0]}.ee.npy"
        def get_chunk(chunk: list[date]) -> tuple[list[date], np.ndarray]:
            path = chunk_path(chunk)
            if path.exists():
                data = np.load(path, allow_pickle=False)
            else:
                data = fetch_days(chunk)
                temporary = path.with_suffix(".tmp.npy")
                np.save(temporary, data)
                temporary.replace(path)
            if data.shape != (len(chunk), HEIGHT, WIDTH):
                raise ValueError(f"Invalid checkpoint: {path}")
            return chunk, data
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = {pool.submit(get_chunk, chunk): chunk for chunk in chunks}
            finished = 0
            for future in as_completed(pending):
                chunk, data = future.result()
                start_index = (chunk[0] - days[0]).days
                values[start_index:start_index + len(chunk)] = data
                finished += 1
                if finished % 10 == 0 or finished == len(chunks):
                    print(f"{year}: {finished}/{len(chunks)} seven-day chunks", flush=True)
        latitude = (BOUNDS[3] - 0.025 - 0.05 * np.arange(HEIGHT)).astype(np.float64)
        longitude = (BOUNDS[0] + 0.025 + 0.05 * np.arange(WIDTH)).astype(np.float64)
        np.savez_compressed(result, precipitation_mm_day=values,
                            dates=np.array([str(day) for day in days]),
                            latitude=latitude, longitude=longitude)
        print(f"{year}: saved {result} ({result.stat().st_size / 1e6:.1f} MB)", flush=True)
        for chunk in chunks:
            chunk_path(chunk).unlink()
    return {"year": year, "days": len(days), "file": str(result), "sha256": sha256(result)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Google Cloud project ID enabled for Earth Engine")
    parser.add_argument("--years", nargs="+", type=int, default=list(range(2020, 2025)))
    args = parser.parse_args()
    ee.Initialize(project=args.project)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    records = [download_year(year) for year in args.years]
    manifest = {
        "source": "CHIRPS v2.0 Final daily, Climate Hazards Center, accessed via Google Earth Engine",
        "earth_engine_collection": COLLECTION,
        "earth_engine_project": args.project,
        "source_page": "https://www.chc.ucsb.edu/data/chirps",
        "dataset_page": "https://developers.google.com/earth-engine/datasets/catalog/UCSB-CHG_CHIRPS_DAILY",
        "requested_bounds_wsen": BOUNDS,
        "crs": "EPSG:4326", "grid_transform": TRANSFORM,
        "unit": "mm/day", "nodata": "NaN converted from negative source sentinel",
        "shape_lat_lon": [HEIGHT, WIDTH], "latitude_order": "north_to_south",
        "output_type": "source-grid subset; no resampling or peat mask",
        "years": records,
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Wrote manifest.json", flush=True)


if __name__ == "__main__":
    main()
