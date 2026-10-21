"""Download daily mean ERA5-Land temperature and top-layer soil water via Earth Engine.

Run: .venv/bin/python scripts/download_era5_land_daily.py --project kalimantan-peat

This retains the native 0.1-degree grid. Seven-day checkpoints let an
interrupted download resume. No 5 x 5 km resampling or peatland mask is applied.
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

COLLECTION = "ECMWF/ERA5_LAND/DAILY_AGGR"
BANDS = ("temperature_2m", "volumetric_soil_water_layer_1")
BOUNDS = (108.45, -5.05, 119.55, 4.55)  # source grid outer edges, W/S/E/N
REQUEST_BOUNDS = (108.46, -5.04, 119.54, 4.54)  # inset avoids an extra edge row
TRANSFORM = [0.1, 0, BOUNDS[0], 0, -0.1, BOUNDS[3]]
HEIGHT, WIDTH = 96, 111
OUTPUT = Path("data/interim/era5_land_daily")


def year_days(year: int) -> list[date]:
    return [date(year, month, day)
            for month in range(1, 13)
            for day in range(1, monthrange(year, month)[1] + 1)]


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
                  .select(list(BANDS)).sort("system:time_start")
                  .map(lambda image: image.unmask(-9999, False)))
    image = collection.toBands()
    expected = tuple(f"{day:%Y%m%d}_{band}" for day in days for band in BANDS)
    names = tuple(image.bandNames().getInfo())
    if names != expected:
        raise ValueError(f"Unexpected bands for {start} to {end}: {names}")
    region = ee.Geometry.Rectangle(REQUEST_BOUNDS, proj="EPSG:4326", geodesic=False)
    url = image.getDownloadURL({"region": region, "crs": "EPSG:4326",
                                "crs_transform": TRANSFORM, "format": "NPY"})
    for attempt in range(5):
        try:
            response = requests.get(url, timeout=180)
            response.raise_for_status()
            structured = np.load(io.BytesIO(response.content), allow_pickle=False)
            if structured.shape != (HEIGHT, WIDTH) or structured.dtype.names != expected:
                raise ValueError(f"Unexpected grid/bands: {structured.shape}, "
                                 f"{structured.dtype.names}")
            values = np.stack([structured[name] for name in expected]).astype(np.float32)
            values[values == -9999] = np.nan
            return values.reshape(len(days), len(BANDS), HEIGHT, WIDTH)
        except (requests.RequestException, ValueError) as error:
            if attempt == 4:
                raise RuntimeError(f"Failed to download {start} to {end}") from error
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def download_year(year: int) -> dict:
    days = year_days(year)
    result = OUTPUT / f"era5_land_kalimantan_{year}.npz"
    if result.exists():
        print(f"{year}: already saved", flush=True)
    else:
        values = np.full((len(days), len(BANDS), HEIGHT, WIDTH), np.nan, np.float32)
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
            if data.shape != (len(chunk), len(BANDS), HEIGHT, WIDTH):
                raise ValueError(f"Invalid checkpoint: {path}")
            return chunk, data
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = {pool.submit(get_chunk, chunk): chunk for chunk in chunks}
            finished = 0
            for future in as_completed(pending):
                chunk, data = future.result()
                index = (chunk[0] - days[0]).days
                values[index:index + len(chunk)] = data
                finished += 1
                if finished % 10 == 0 or finished == len(chunks):
                    print(f"{year}: {finished}/{len(chunks)} seven-day chunks", flush=True)
        latitude = (BOUNDS[3] - 0.05 - 0.1 * np.arange(HEIGHT)).astype(np.float64)
        longitude = (BOUNDS[0] + 0.05 + 0.1 * np.arange(WIDTH)).astype(np.float64)
        np.savez_compressed(result, temperature_2m_K=values[:, 0],
                            soil_water_layer1_fraction=values[:, 1],
                            dates=np.array([str(day) for day in days]),
                            latitude=latitude, longitude=longitude)
        print(f"{year}: saved {result} ({result.stat().st_size / 1e6:.1f} MB)", flush=True)
        for chunk in chunks:
            chunk_path(chunk).unlink()
    return {"year": year, "days": len(days), "file": str(result), "sha256": sha256(result)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--years", nargs="+", type=int, default=list(range(2020, 2025)))
    args = parser.parse_args()
    ee.Initialize(project=args.project)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    records = [download_year(year) for year in args.years]
    manifest = {
        "source": "ERA5-Land Daily Aggregated, ECMWF, via Google Earth Engine",
        "earth_engine_collection": COLLECTION,
        "earth_engine_project": args.project,
        "dataset_page": "https://developers.google.com/earth-engine/datasets/catalog/ECMWF_ERA5_LAND_DAILY_AGGR",
        "paper_note": "Mahdiyasa et al. name ERA5-Land daily air temperature and soil moisture but do not specify bands, soil layer, daily statistic, or time zone.",
        "chosen_variables": {
            "temperature_2m_K": "Daily mean 2 m air temperature, kelvin",
            "soil_water_layer1_fraction": "Daily mean model volumetric soil water, layer 1 (0-7 cm), m3/m3",
        },
        "daily_window": "00:00-23:00 UTC hourly means as supplied by Earth Engine",
        "native_grid_bounds_wsen": BOUNDS,
        "request_bounds_wsen": REQUEST_BOUNDS,
        "crs": "EPSG:4326", "grid_transform": TRANSFORM,
        "shape_lat_lon": [HEIGHT, WIDTH], "latitude_order": "north_to_south",
        "nodata": "NaN converted from Earth Engine -9999 unmask sentinel",
        "output_type": "source-grid subset; no resampling or peat mask",
        "years": records,
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Wrote manifest.json", flush=True)


if __name__ == "__main__":
    main()
