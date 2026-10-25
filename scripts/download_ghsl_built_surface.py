"""Download native 100 m GHSL 2020 built-up surface around Kalimantan.

Run: .venv/bin/python scripts/download_ghsl_built_surface.py --project kalimantan-peat

A one-degree buffer beyond the earlier Kalimantan download rectangle retains
nearby settlements for later distance calculations. Tile checkpoints let an
interrupted download resume. This is source data, not a distance-to-activity
layer; the settlement threshold and distance metric remain analysis choices.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import io
import json
import math
from pathlib import Path
import time

import ee
import numpy as np
from pyproj import CRS, Transformer
import rasterio
from rasterio.transform import Affine
from rasterio.windows import Window
import requests

IMAGE = "JRC/GHSL/P2023A/GHS_BUILT_S/2020"
BAND = "built_surface"
BOUNDS_WGS84 = (107.5, -6.0, 120.5, 5.5)  # W/S/E/N, includes neighboring settlements
TILE_SIZE = 2500
NODATA = 65535
OUTPUT = Path("data/interim/ghsl_built_surface")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def grid_for_bounds(projection: dict) -> tuple[int, int, int, int, Affine, CRS]:
    source_crs = CRS.from_wkt(projection["wkt"])
    converter = Transformer.from_crs("EPSG:4326", source_crs, always_xy=True)
    west, south, east, north = BOUNDS_WGS84
    fractions = np.linspace(0, 1, 1001)
    lon = np.concatenate([west + (east-west)*fractions,
                          west + (east-west)*fractions,
                          np.full_like(fractions, west), np.full_like(fractions, east)])
    lat = np.concatenate([np.full_like(fractions, south), np.full_like(fractions, north),
                          south + (north-south)*fractions,
                          south + (north-south)*fractions])
    x, y = converter.transform(lon, lat)
    a, _, c, _, e, f = projection["transform"]
    if a != 100 or e != -100:
        raise ValueError(f"Unexpected GHSL native grid: {projection['transform']}")
    col0 = math.floor((np.min(x)-c)/a)
    col1 = math.ceil((np.max(x)-c)/a)
    row0 = math.floor((f-np.max(y))/abs(e))
    row1 = math.ceil((f-np.min(y))/abs(e))
    native = Affine(a, 0, c, 0, e, f)
    clipped = native * Affine.translation(col0, row0)
    return col0, row0, col1-col0, row1-row0, clipped, source_crs


def fetch_tile(image: ee.Image, projection: dict, col: int, row: int,
               width: int, height: int) -> np.ndarray:
    a, _, c, _, e, f = projection["transform"]
    left = c + col*a
    top = f + row*e
    right = left + width*a
    bottom = top + height*e
    region = ee.Geometry.Rectangle([left+1, bottom+1, right-1, top-1],
                                   proj=ee.Projection(projection["wkt"]),
                                   geodesic=False)
    expected = (height, width)
    for attempt in range(5):
        try:
            url = image.getDownloadURL({"region": region, "crs": projection["wkt"],
                                        "crs_transform": projection["transform"],
                                        "format": "NPY"})
            response = requests.get(url, timeout=180)
            response.raise_for_status()
            structured = np.load(io.BytesIO(response.content), allow_pickle=False)
            if structured.shape != expected or structured.dtype.names != (BAND,):
                raise ValueError(f"Unexpected GHSL tile grid: {structured.shape}, "
                                 f"{structured.dtype.names}; expected {expected}")
            values = structured[BAND]
            if values.dtype != np.dtype("uint16") or np.any((values > 10000) & (values != NODATA)):
                raise ValueError("Unexpected GHSL built-up surface value")
            return values
        except (requests.RequestException, ee.EEException, ValueError) as error:
            if attempt == 4:
                raise RuntimeError(f"Failed GHSL tile at col {col}, row {row}") from error
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Required only when acquiring the static source")
    parser.add_argument("--root")
    args = parser.parse_args()
    from peatguard.study import load_study
    from peatguard.spatial import validated_static, atomic_json
    study = load_study(root=args.root)
    global OUTPUT
    OUTPUT = study.root / study.config["sources"]["ghsl"]["directory"]
    cached = OUTPUT / "ghsl_built_surface_2020_kalimantan_buffered.tif"
    if cached.exists() or (OUTPUT / "manifest.json").exists():
        print(f"Reused validated static GHSL 2020: {validated_static(study)}")
        return
    if not args.project:
        parser.error("--project is required for initial acquisition; cached data need no credentials")
    ee.Initialize(project=args.project)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    output = OUTPUT / "ghsl_built_surface_2020_kalimantan_buffered.tif"
    projection = ee.Image(IMAGE).select(BAND).projection().getInfo()
    col0, row0, width, height, transform, crs = grid_for_bounds(projection)
    tiles = [(x, y, min(TILE_SIZE, width-x), min(TILE_SIZE, height-y))
             for y in range(0, height, TILE_SIZE)
             for x in range(0, width, TILE_SIZE)]
    if output.exists():
        print(f"Already saved {output}", flush=True)
    else:
        partial = OUTPUT / ".2020.partial.tif"
        done_path = OUTPUT / ".2020.done.npy"
        if partial.exists() != done_path.exists():
            raise RuntimeError("Incomplete checkpoint pair; inspect GHSL output directory")
        if partial.exists():
            done = np.load(done_path, allow_pickle=False)
            if done.shape != (len(tiles),):
                raise ValueError("Checkpoint count differs from tile grid")
        else:
            done = np.zeros(len(tiles), dtype=bool)
            with rasterio.open(partial, "w", driver="GTiff", width=width, height=height,
                               count=1, dtype="uint16", crs=crs, transform=transform,
                               nodata=NODATA, compress="DEFLATE", predictor=2,
                               tiled=True, blockxsize=256, blockysize=256,
                               SPARSE_OK="TRUE") as destination:
                destination.set_band_description(1, BAND)
                destination.update_tags(source_image=IMAGE, unit="m2 per 100 m cell")
            np.save(done_path, done)
        image = ee.Image(IMAGE).select(BAND).unmask(NODATA, False)
        pending = [index for index in range(len(tiles)) if not done[index]]
        print(f"GHSL 2020 native grid: {width} × {height}; {len(pending)} of {len(tiles)} tiles pending", flush=True)
        with rasterio.open(partial, "r+") as destination:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(fetch_tile, image, projection,
                                       col0+x, row0+y, w, h): index
                           for index, (x, y, w, h) in enumerate(tiles) if not done[index]}
                completed = 0
                for future in as_completed(futures):
                    index = futures[future]
                    x, y, w, h = tiles[index]
                    destination.write(future.result(), 1, window=Window(x, y, w, h))
                    done[index] = True
                    np.save(done_path, done)
                    completed += 1
                    if completed % 5 == 0 or completed == len(pending):
                        print(f"GHSL tiles: {int(done.sum())}/{len(tiles)}", flush=True)
        if not done.all():
            raise RuntimeError("GHSL download remains incomplete")
        # Validate every block before exposing the completed static source.
        from peatguard.inventory import _raster
        _raster(partial, study.config["sources"]["ghsl"], {}, {
            "shape_rows_columns": [height, width], "transform": list(transform)[:6],
            "crs_wkt": crs.to_wkt(),
        })
        # Publish source bytes outside glob discovery. The manifest is the
        # commit pointer; failed publication leaves a harmless orphan.
        partitions = OUTPUT / '.partitions'
        partitions.mkdir(exist_ok=True)
        output = partitions / f'ghsl_built_surface_2020_{sha256(partial)[:20]}.tif'
        partial.replace(output)
        done_path.unlink()
        print(f"Saved {output} ({output.stat().st_size / 1e6:.1f} MB)", flush=True)
    with rasterio.open(output) as source:
        if source.shape != (height, width) or source.transform != transform:
            raise ValueError("Saved GHSL grid differs from expected native subset")
    manifest = {
        "source": "GHSL GHS-BUILT-S R2023A 2020 built-up surface, via Google Earth Engine",
        "earth_engine_image": IMAGE,
        "earth_engine_project": args.project,
        "dataset_page": "https://developers.google.com/earth-engine/datasets/catalog/JRC_GHSL_P2023A_GHS_BUILT_S",
        "source_doi": "10.2905/9F06F36F-4B11-47EC-ABB0-4F8B7B1D72EA",
        "requested_bounds_wgs84_wsen": BOUNDS_WGS84,
        "buffer_purpose": "Retain settlements beyond Kalimantan province edges for later nearest-settlement distances",
        "native_pixel_size_m": 100,
        "unit": "m2 built-up surface per 100 m grid cell",
        "nodata": NODATA,
        "shape_rows_columns": [height, width],
        "transform": list(transform)[:6],
        "crs_wkt": crs.to_wkt(),
        "output": str(output),
        "sha256": sha256(output),
        "paper_note": "Paper names GHSL but does not specify product, epoch, settlement threshold, or distance metric. This is source/proxy EDA only.",
    }
    manifest["output"] = output.relative_to(study.root).as_posix()
    atomic_json(OUTPUT / "manifest.json", manifest)
    print("Wrote manifest.json", flush=True)


if __name__ == "__main__":
    main()
