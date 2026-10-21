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


def download_year(year: int) -> dict:
    result = OUTPUT / f"modis_mcd12q1_061_kalimantan_{year}.tif"
    if result.exists():
        print(f"{year}: already saved", flush=True)
        with rasterio.open(result) as source:
            shape = [source.height, source.width]
            transform = list(source.transform)[:6]
    else:
        data, affine, projection = fetch_year(year)
        temporary = result.with_suffix(".tmp.tif")
        with rasterio.open(
            temporary, "w", driver="GTiff", width=data.shape[2], height=data.shape[1],
            count=len(BANDS), dtype="uint8", crs=MODIS_SINUSOIDAL,
            transform=affine, nodata=None, compress="DEFLATE", tiled=True,
            blockxsize=256, blockysize=256,
        ) as destination:
            destination.write(data)
            for number, band in enumerate(BANDS, start=1):
                destination.set_band_description(number, band)
            destination.update_tags(source_collection=COLLECTION,
                                    source_image=f"{year}_01_01",
                                    source_crs=projection["crs"])
        temporary.replace(result)
        shape = list(data.shape[1:])
        transform = list(affine)[:6]
        print(f"{year}: saved {result} ({result.stat().st_size / 1e6:.1f} MB)", flush=True)
    return {"year": year, "file": str(result), "shape_lat_lon": shape,
            "transform": transform, "sha256": sha256(result)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--years", nargs="+", type=int, default=list(range(2020, 2025)))
    args = parser.parse_args()
    ee.Initialize(project=args.project)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    records = [download_year(year) for year in args.years]
    grids = {(tuple(record["shape_lat_lon"]), tuple(record["transform"])) for record in records}
    if len(grids) != 1:
        raise ValueError("MODIS annual grids differ; cannot compare pixelwise")
    manifest = {
        "source": "MODIS MCD12Q1 Version 6.1, via Google Earth Engine",
        "earth_engine_collection": COLLECTION,
        "earth_engine_project": args.project,
        "dataset_page": "https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MCD12Q1",
        "doi": "10.5067/MODIS/MCD12Q1.061",
        "requested_bounds_wgs84_wsen": BOUNDS_WGS84,
        "bands_in_order": BANDS,
        "band_1_scheme": "IGBP LC_Type1, integer classes 1–17",
        "crs": "MODIS Sinusoidal sphere radius 6371007.181 m",
        "native_pixel_size_m": 463.3127165279165,
        "nodata": "None; LC_Type1 class 17 is water; QC code 0 is valid classified land",
        "paper_note": "Paper says MODIS Collection 6 Land Cover Type Product but does not specify collection minor version, band/classification scheme, or map year.",
        "reconstruction_note": "Earth Engine Collection 6.1 LC_Type1 annual maps are retained for EDA; no 5 km resampling or model-year selection yet.",
        "years": records,
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Wrote manifest.json", flush=True)


if __name__ == "__main__":
    main()
