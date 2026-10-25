"""Download annual MCD12Q1 v6.1 Kalimantan land cover on its native 500 m grid.

Run: .venv/bin/python scripts/download_modis_landcover.py --project kalimantan-peat

Each output GeoTIFF has three uint8 bands: LC_Type1 (IGBP), QC, and LW. The
land-cover class is categorical; do not interpolate it as a continuous value.
"""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import time

import ee
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.io import MemoryFile
import requests

COLLECTION = "MODIS/061/MCD12Q1"
BANDS = ("LC_Type1", "QC", "LW")
BOUNDS_WGS84 = (108.5, -5.0, 119.5, 4.5)
MODIS_SINUSOIDAL = CRS.from_proj4("+proj=sinu +R=6371007.181 +no_defs")
OUTPUT = Path("data/interim/modis_landcover")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_year(year: int) -> tuple[np.ndarray, rasterio.Affine, dict]:
    collection = ee.ImageCollection(COLLECTION).filterDate(
        str(date(year, 1, 1)), str(date(year + 1, 1, 1)))
    count = collection.size().getInfo()
    if count != 1:
        raise ValueError(f"Expected one MODIS map for {year}, found {count}")
    image = ee.Image(collection.first()).select(list(BANDS))
    if image.bandNames().getInfo() != list(BANDS):
        raise ValueError(f"Unexpected bands for {year}")
    projection = image.select("LC_Type1").projection().getInfo()
    if projection["crs"] != "SR-ORG:6974":
        raise ValueError(f"Unexpected MODIS projection for {year}: {projection}")
    region = ee.Geometry.Rectangle(BOUNDS_WGS84, proj="EPSG:4326", geodesic=False)
    url = image.getDownloadURL({
        "region": region,
        "crs": projection["crs"],
        "crs_transform": projection["transform"],
        "format": "GEO_TIFF",
    })
    for attempt in range(5):
        try:
            response = requests.get(url, timeout=180)
            response.raise_for_status()
            with MemoryFile(response.content) as memory:
                with memory.open() as source:
                    data = source.read()
                    transform = source.transform
            if data.shape[0] != len(BANDS) or data.dtype != np.uint8:
                raise ValueError(f"Unexpected GeoTIFF bands or type: {data.shape}, {data.dtype}")
            if not np.isin(data[0], np.arange(1, 18)).all():
                raise ValueError("Unexpected IGBP class outside 1–17")
            if not np.isin(data[1], np.arange(10)).all():
                raise ValueError("Unexpected QC code outside 0–9")
            if not np.isin(data[2], [1, 2]).all():
                raise ValueError("Unexpected land/water code outside 1–2")
            return data, transform, projection
        except (requests.RequestException, ValueError) as error:
            if attempt == 4:
                raise RuntimeError(f"Failed to download {year}") from error
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")



def main() -> None:
    from peatguard.study import load_study
    from peatguard.spatial import discover_maps, refresh_maps, select_maps
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root")
    parser.add_argument("--project", required=True)
    parser.add_argument("--profile", choices=["legacy", "expanded"], default="expanded")
    parser.add_argument("--start")
    parser.add_argument("--end", default="latest")
    parser.add_argument("--as-of")
    parser.add_argument("--policy", choices=["strict_same_year", "carry_forward"], default="strict_same_year")
    parser.add_argument("--include-held-out", action="store_true", help="Include held-out years in assignment report")
    args = parser.parse_args()
    study = load_study(root=args.root, profile=args.profile, start=args.start, end=args.end, as_of=args.as_of)
    available = discover_maps(study, args.project)
    report = refresh_maps(study, available, fetch_year, policy=args.policy)
    report["assignments"] = select_maps(study, policy=args.policy, include_held_out=args.include_held_out)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
