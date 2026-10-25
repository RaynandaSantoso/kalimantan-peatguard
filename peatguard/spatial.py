"""Validated annual/static sources and explicit retrospective feature policies.

Native categorical modes and static built-pixel distances match preprocessing;
map year is not publication evidence. No provider calls occur on import.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import reproject, Resampling

from .inventory import _local_path, _raster, _read_manifest, _source_files, sha256
from .study import Study

MODIS_CRS = CRS.from_proj4('+proj=sinu +R=6371007.181 +no_defs')


def _same_grid(first, second):
    # Earth Engine/GDAL serialization can vary by tens of micrometres on
    # the same native MODIS lattice. Tolerance is absolute metres, never
    # relative to the million-metre easting/northing coordinates.
    return first[0] == second[0] and np.allclose(first[1], second[1], rtol=0, atol=1e-4)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, suffix='.tmp', delete=False) as stream:
        pending = Path(stream.name)
        json.dump(value, stream, indent=2)
        stream.write('\n')
    try:
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def validated_maps(study: Study) -> dict[int, Path]:
    """Fail closed on corrupt, missing, or unmanifested maps; absence is allowed."""
    definition = study.config['sources']['modis']
    directory = _local_path(study.root, definition['directory'])
    if not (directory / 'manifest.json').exists() and not list(directory.glob(definition['pattern'])):
        return {}
    files, diagnostics, _ = _source_files(study, definition)
    if diagnostics or any(row['status'] != 'validated' for row in files):
        raise ValueError(f'MODIS cache validation failed: {diagnostics or files}')
    maps = {row['map_year']: study.root / row['path'] for row in files}
    geometries = []
    for path in maps.values():
        with rasterio.open(path) as source:
            a = source.transform
            if not np.allclose([a.a, a.b, a.d, a.e], [463.3127165279165, 0, 0, -463.3127165279165]):
                raise ValueError('MODIS cached raster does not retain native pixel size.')
            geometries.append((source.shape, tuple(a)[:6]))
    if geometries and any(not _same_grid(geometries[0], grid) for grid in geometries[1:]):
        raise ValueError('MODIS annual maps differ in spatial alignment.')
    return maps


def validated_static(study: Study) -> Path:
    files, diagnostics, _ = _source_files(study, study.config['sources']['ghsl'])
    if diagnostics or len(files) != 1 or files[0]['status'] != 'validated':
        raise ValueError(f'GHSL cache validation failed: {diagnostics or files}')
    path = study.root / files[0]['path']
    with rasterio.open(path) as source:
        a = source.transform
        if not np.allclose([a.a, a.b, a.d, a.e], [100, 0, 0, -100]):
            raise ValueError('GHSL cache does not retain its native 100 m grid.')
    return path


def select_maps(study: Study, *, policy: str = 'strict_same_year',
                include_held_out: bool = False) -> list[dict]:
    """Resolve each requested calendar year; fallback must be chosen explicitly."""
    if policy not in ('strict_same_year', 'carry_forward'):
        raise ValueError('Choose strict_same_year or explicitly select carry_forward.')
    maps = validated_maps(study)
    days = ([study.start, study.end] if include_held_out else study.development_dates())
    # Iterate the actual requested year span, never infer gaps from detection dates.
    years = sorted({d.year for d in days})
    if include_held_out:
        years = list(range(study.start.year, study.end.year + 1))
    rows = []
    for year in years:
        eligible = [y for y in maps if y <= year]
        selected = year if year in maps else (max(eligible) if eligible and policy == 'carry_forward' else None)
        rows.append({'data_year': year, 'map_year': selected, 'policy': policy,
                     'status': 'ready' if selected is not None else 'blocked_missing_map',
                     'staleness_years': year-selected if selected is not None else None,
                     'path': maps[selected].relative_to(study.root).as_posix() if selected is not None else None,
                     'historical_release_evidence': 'unknown',
                     'forecast_availability_proven': False})
    return rows


def discover_maps(study: Study, project: str) -> dict:
    """Discover nominal map years only; timestamps are not release timestamps."""
    if not project:
        raise ValueError('Earth Engine project is required for discovery.')
    import ee
    ee.Initialize(project=project)
    collection = study.config['sources']['modis']['collection']
    # Prior years support explicitly selected carry-forward, even for a 2026 request.
    earliest = min(date.fromisoformat(p['start']).year for p in study.config['profiles'].values())
    stamps = (ee.ImageCollection(collection)
              .filterDate(f'{earliest}-01-01', f'{study.end.year+1}-01-01')
              .aggregate_array('system:time_start').getInfo())
    cutoff_ms = study.as_of.timestamp()*1000
    years = sorted({datetime.fromtimestamp(stamp/1000, timezone.utc).year
                    for stamp in stamps if stamp <= cutoff_ms})
    return {'collection': collection, 'version': study.config['sources']['modis']['version'],
            'years': years, 'observed_at': datetime.now(timezone.utc).isoformat(),
            'historical_release_evidence': 'unknown',
            'evidence': 'currently_published_assets_not_historical_release_dates'}


def refresh_maps(study: Study, available: dict, fetch_year, *, policy='strict_same_year') -> dict:
    """Validate downloads, preserve old records, atomically publish cumulative manifest.

    fetch_year(year) returns data, affine, projection, as in the native downloader.
    Discovery is a separate operation and unavailable years are reported, never fetched.
    """
    definition = study.config['sources']['modis']
    if available.get('collection') != definition['collection'] or available.get('version') != definition['version']:
        raise ValueError('Availability product/version mismatch.')
    if policy not in ('strict_same_year', 'carry_forward'):
        raise ValueError('Unknown annual map assignment policy.')
    published = available.get('years', [])
    if any(not isinstance(y, int) or y > study.end.year for y in published):
        raise ValueError('Invalid or future map year.')
    current = validated_maps(study)
    directory = _local_path(study.root, definition['directory'])
    manifest, diagnostics = (_read_manifest(study.root, definition)
                             if (directory / 'manifest.json').exists() else ({}, []))
    if diagnostics:
        raise ValueError(f'Invalid manifest: {diagnostics}')
    records = {r['year']: r for r in manifest.get('years', [])}
    expected_grid = None
    if records:
        grids = [(tuple(r['shape_lat_lon']), tuple(r['transform'])) for r in records.values()]
        if any(not _same_grid(grids[0], grid) for grid in grids[1:]):
            raise ValueError('MODIS cached annual grids differ.')
        expected_grid = grids[0]
    requested = list(range(study.start.year, study.end.year+1))
    acquire = set(requested)
    if policy == 'carry_forward':
        for year in requested:
            previous = [y for y in published if y <= year]
            if previous:
                acquire.add(max(previous))
    added = []
    for year in sorted(acquire):
        if year in current or year not in published:
            continue
        data, affine, projection = fetch_year(year)
        if projection.get('crs') != 'SR-ORG:6974':
            raise ValueError('Unexpected MODIS provider projection.')
        if (data.ndim != 3 or data.shape[0] != 3 or data.dtype != np.uint8
                or not np.isin(data[0], range(1, 18)).all()
                or not np.isin(data[1], range(10)).all()
                or not np.isin(data[2], [1, 2]).all()):
            raise ValueError('Invalid native MODIS bands/type/categories.')
        grid = (tuple(data.shape[1:]), tuple(affine)[:6])
        if expected_grid is not None and not _same_grid(grid, expected_grid):
            raise ValueError('MODIS incoming native grid differs from previous years.')
        # Native pixel size and lattice alignment are independent of manifest claims.
        native = projection.get('transform')
        if (native is None or not np.allclose([affine.a, affine.b, affine.d, affine.e],
                                             [native[0], native[1], native[3], native[4]])
                or not np.isclose(affine.a, 463.3127165279165)
                or not np.isclose(affine.e, -463.3127165279165)):
            raise ValueError('MODIS raster is not on the native pixel grid.')
        if not np.allclose([(affine.c-native[2])/affine.a, (affine.f-native[5])/affine.e],
                           np.round([(affine.c-native[2])/affine.a, (affine.f-native[5])/affine.e]), rtol=0, atol=1e-6):
            raise ValueError('MODIS raster is shifted off the native lattice.')
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, suffix='.tif', delete=False) as stream:
            pending = Path(stream.name)
        try:
            with rasterio.open(pending, 'w', driver='GTiff', width=data.shape[2], height=data.shape[1],
                               count=3, dtype='uint8', crs=MODIS_CRS, transform=affine,
                               compress='DEFLATE') as dest:
                dest.write(data)
                for i, band in enumerate(definition['bands'], 1):
                    dest.set_band_description(i, band)
                dest.update_tags(source_collection=definition['collection'], source_image=f'{year}_01_01')
            record = {'year': year, 'shape_lat_lon': list(data.shape[1:]),
                      'transform': list(affine)[:6], 'sha256': sha256(pending),
                      'retrieved_at': datetime.now(timezone.utc).isoformat(),
                      'historical_release_evidence': 'unknown'}
            _raster(pending, definition, record, manifest)
            partitions = directory / '.partitions'
            partitions.mkdir(exist_ok=True)
            destination = partitions / f'modis_mcd12q1_061_kalimantan_{year}_{record["sha256"][:20]}.tif'
            record['file'] = destination.relative_to(study.root).as_posix()
            pending.replace(destination)
            records[year] = record
            expected_grid = grid
            manifest.update(earth_engine_collection=definition['collection'],
                            version=definition['version'], bands_in_order=definition['bands'],
                            years=[records[y] for y in sorted(records)], availability=available)
            # Publish each validated map independently; subsequent failures retain progress.
            atomic_json(directory / 'manifest.json', manifest)
            added.append(year)
        finally:
            pending.unlink(missing_ok=True)
    return {'added_years': added, 'reused_years': sorted(set(requested)&current.keys()),
            'unavailable_years': sorted(set(requested)-set(published)-current.keys()),
            'cached_years': sorted(records), 'historical_release_evidence': 'unknown'}


def dominant_classes(study: Study, cells, maps: dict[int, Path] | None = None, *, allow_missing=False):
    """Same rasterio mode resampling as preprocessing, preserving signed lattice IDs."""
    import pandas as pd
    from rasterio.transform import Affine
    if cells.empty or cells.cell_id.duplicated().any():
        raise ValueError('Expected nonempty unique study cells.')
    for row in cells.itertuples():
        if row.cell_id != f'kp5_c{row.grid_col}_r{row.grid_row}':
            raise ValueError('Cell ID differs from stable lattice indices.')
    size = study.config['grid']['cell_size_m']
    origin = study.config['grid']['lattice_origin_m']
    left, top = int(cells.grid_col.min()), int(cells.grid_row.max())
    width, height = int(cells.grid_col.max()-left+1), int(top-cells.grid_row.min()+1)
    # GDAL's approximate warp can resolve tied categorical modes differently
    # for a smaller destination extent. Preserve the full province lattice
    # used by preprocessing, even when only peat-intersecting rows are emitted.
    grid_manifest = study.root / 'data/interim/study_grid_5km/manifest.json'
    if grid_manifest.exists():
        extent = json.loads(grid_manifest.read_text())['bounds_indices_stop_exclusive']
        left, top = extent['col_start'], extent['row_stop']-1
        width, height = extent['col_stop']-left, extent['row_stop']-extent['row_start']
        if (cells.grid_col.min() < left or cells.grid_col.max() >= left+width
                or cells.grid_row.min() < top-height+1 or cells.grid_row.max() > top):
            raise ValueError('Study cells fall outside the preserved full lattice.')
    affine = Affine(size, 0, origin[0]+left*size, 0, -size, origin[1]+(top+1)*size)
    output = pd.DataFrame({'cell_id': cells.cell_id.to_numpy()})
    for year, path in sorted((validated_maps(study) if maps is None else maps).items()):
        destination = np.zeros((height, width), np.uint8)
        with rasterio.open(path) as source:
            reproject(source=rasterio.band(source, 1), destination=destination,
                      src_transform=source.transform, src_crs=source.crs,
                      dst_transform=affine, dst_crs=study.config['grid']['crs_proj'],
                      dst_nodata=0, resampling=Resampling.mode)
        values = destination[(top-cells.grid_row).to_numpy(), (cells.grid_col-left).to_numpy()]
        if not np.isin(values, range(1, 18)).all() and not allow_missing:
            raise ValueError(f'MODIS {year} has missing study-cell coverage.')
        output[str(year)] = values
    return output


def static_distances(study: Study, cells):
    """2020 >0 built-pixel-center distance in the existing study LAEA projection."""
    import pandas as pd
    from pyproj import Transformer
    from scipy.spatial import cKDTree
    path = validated_static(study)
    parts = []
    with rasterio.open(path) as source:
        converter = Transformer.from_crs(source.crs, study.config['grid']['crs_proj'], always_xy=True)
        for _, window in source.block_windows(1):
            values = source.read(1, window=window)
            rows, cols = np.where((values > 0) & (values != source.nodata))
            if not len(rows):
                continue
            x = source.transform.c + (window.col_off+cols+0.5)*source.transform.a
            y = source.transform.f + (window.row_off+rows+0.5)*source.transform.e
            x, y = converter.transform(x, y)
            parts.append(np.column_stack([x, y]).astype(np.float32))
    if not parts:
        raise ValueError('No valid built pixels in buffered GHSL 2020 raster.')
    distances, _ = cKDTree(np.concatenate(parts)).query(cells[['x_center_m', 'y_center_m']].to_numpy())
    return pd.DataFrame({'cell_id': cells.cell_id.to_numpy(), 'distance_to_built_m': distances,
                         'ghsl_reference_epoch': 2020, 'ghsl_source_sha256': sha256(path),
                         'proxy_rule': '2020 built_surface>0; nearest pixel center; study LAEA metres'})
