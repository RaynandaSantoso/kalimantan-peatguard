"""FIRMS MODIS C6.1 ingestion; retrieval coverage is separate from detections.

A successful empty export supports 'no reported detection', never absence of fire.
All dates are original UTC acquisition dates. No extra fire-type filter is applied.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from urllib.request import urlopen

import numpy as np
import pandas as pd
from pyproj import Transformer
import shapely
from shapely.geometry import shape
from shapely.ops import transform

from .study import day_range, parse_as_of

PRODUCT = 'FIRMS_MODIS_C6.1'
API = 'https://firms.modaps.eosdis.nasa.gov/api'
ARCHIVE = 'https://firms.modaps.eosdis.nasa.gov/download/'
REQUIRED = {'latitude', 'longitude', 'acq_date', 'acq_time', 'satellite',
            'instrument', 'confidence', 'version'}
IDENTITY = ['acquired_at_utc', 'satellite', 'instrument', 'product', 'latitude', 'longitude']


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _bounds(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError('bbox must be [west, south, east, north].')
    west, south, east, north = map(float, value)
    if not all(math.isfinite(x) for x in (west, south, east, north)) or not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError('Invalid geographic bbox.')
    return [west, south, east, north]


def read_ledger(path, study):
    """Read explicit retrieval evidence; source paths resolve against the ledger."""
    path = Path(path).resolve()
    ledger = json.loads(path.read_text())
    if ledger.get('schema_version') != 1 or not isinstance(ledger.get('intervals'), list):
        raise ValueError('Expected schema_version 1 and intervals list.')
    results = []
    for original in ledger['intervals']:
        item = dict(original)
        if item.get('product') != PRODUCT or item.get('stage') not in {'SP', 'NRT'}:
            raise ValueError('Each interval requires FIRMS_MODIS_C6.1 and SP or NRT stage.')
        start, end = date.fromisoformat(item['start']), date.fromisoformat(item['end'])
        if end < start or end > study.as_of.date():
            raise ValueError('Interval must be ordered and cannot extend past cutoff.')
        if end < study.start or start > study.end:
            continue
        item['bbox'] = _bounds(item['bbox'])
        if item.get('status') not in {'complete', 'failed', 'unknown'}:
            raise ValueError('Interval status must be complete, failed, or unknown.')
        if not item.get('provenance'):
            raise ValueError('Document the export/query evidence in provenance.')
        retrieved = parse_as_of(item['retrieved_at'])
        if retrieved > study.as_of:
            raise ValueError('Retrieval occurs after the fixed as-of cutoff.')
        if item['status'] == 'complete' and end >= retrieved.date():
            raise ValueError('Complete coverage requires the interval end UTC day to have closed before retrieval.')
        item['retrieved_at'] = retrieved.isoformat()
        if item.get('path'):
            file = (path.parent / item['path']).resolve()
            if not file.is_file():
                raise ValueError(f'Missing FIRMS source: {file}. Restore the file or update its path in {path}.')
            if not item.get('sha256') or sha256(file) != item['sha256']:
                raise ValueError(f'FIRMS source checksum mismatch: {file}. Restore the verified source before importing.')
            item['path'] = str(file)
        elif item['status'] == 'complete':
            raise ValueError('Complete intervals require an immutable, checksummed CSV, including empty responses.')
        results.append(item)
    return results


def normalize(interval):
    """Strict row validation, keeping rejected counts and original source fields."""
    columns = list(IDENTITY) + ['acq_date', 'acq_time', 'confidence', 'type', 'version',
                               'stage', 'provenance', 'source_sha256', 'source_row', 'retrieved_at', 'original_record']
    if not interval.get('path') or interval['status'] == 'failed':
        return pd.DataFrame(columns=columns), {'input_rows': 0, 'rejected_rows': 0, 'rejection_reasons': {}}
    with Path(interval['path']).open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        required = REQUIRED - {'instrument'} if interval.get('declared_instrument') == 'MODIS' else REQUIRED
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"FIRMS CSV is missing required MODIS columns: {', '.join(sorted(missing))}. Source: {interval['path']}")
        accepted, reasons, count = [], {}, 0
        west, south, east, north = interval['bbox']
        for row_number, row in enumerate(reader, 2):
            count += 1
            try:
                raw_time = row['acq_time'].strip()
                if not raw_time.isdigit() or len(raw_time) > 4:
                    raise ValueError('invalid acquisition time')
                clock = raw_time.zfill(4)
                acquired = datetime.strptime(row['acq_date'] + clock, '%Y-%m-%d%H%M').replace(tzinfo=timezone.utc)
                if acquired > parse_as_of(interval['retrieved_at']):
                    raise ValueError('acquisition after retrieval time')
                if not interval['start'] <= acquired.date().isoformat() <= interval['end']:
                    raise ValueError('outside declared interval')
                latitude, longitude, confidence = map(float, (row['latitude'], row['longitude'], row['confidence']))
                if not all(math.isfinite(x) for x in [latitude, longitude, confidence]):
                    raise ValueError('nonfinite numeric field')
                if not south <= latitude <= north or not west <= longitude <= east or not 0 <= confidence <= 100:
                    raise ValueError('outside bbox or confidence range')
                satellite = {'T': 'Terra', 'A': 'Aqua', 'Terra': 'Terra', 'Aqua': 'Aqua'}.get(row['satellite'])
                if satellite is None or row.get('instrument', interval.get('declared_instrument', '')).upper() != 'MODIS':
                    raise ValueError('unsupported sensor or satellite')
                version = row['version'].strip()
                # Legacy local C6.1 archive declares version 6.03; product is explicit in ledger.
                if version not in {'6.03', '61.03', '6.1', '6.1NRT'} or ('NRT' in version) != (interval['stage'] == 'NRT'):
                    raise ValueError('version or stage mismatch')
                type_value = row.get('type', '')
                if type_value and type_value not in {'0', '1', '2', '3'}:
                    raise ValueError('invalid type')
                accepted.append(dict(acquired_at_utc=acquired.isoformat(), satellite=satellite,
                    instrument='MODIS', product=PRODUCT, latitude=latitude, longitude=longitude,
                    acq_date=acquired.date().isoformat(), acq_time=clock, confidence=confidence,
                    type=type_value or None, version=version, stage=interval['stage'],
                    provenance=interval['provenance'], source_sha256=interval['sha256'],
                    source_row=row_number, retrieved_at=interval['retrieved_at'],
                    original_record=json.dumps(row, sort_keys=True)))
            except (ValueError, TypeError, AttributeError) as error:
                reason = str(error)
                reasons[reason] = reasons.get(reason, 0) + 1
    return pd.DataFrame(accepted, columns=columns), {'input_rows': count, 'rejected_rows': sum(reasons.values()), 'rejection_reasons': reasons}


def ingest(intervals):
    parts, reports, valid_intervals = [], [], []
    for item in intervals:
        frame, report = normalize(item)
        # Any malformed row makes coverage incomplete: never produce false negatives.
        validated = dict(item, validated_complete=item['status'] == 'complete' and report['rejected_rows'] == 0)
        valid_intervals.append(validated)
        reports.append(dict(provenance=item['provenance'], **report))
        parts.append(frame)
    detections = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=IDENTITY)
    before = len(detections)
    replacements = 0
    sp_replacements = 0
    if before:
        remove = np.zeros(before, dtype=bool)
        superseded_sp = np.zeros(before, dtype=bool)
        for item in valid_intervals:
            if item['stage'] != 'SP' or not item['validated_complete']:
                continue
            w, s, e, n = item['bbox']
            scope = (detections.acq_date.between(item['start'], item['end'])
                     & detections.longitude.between(w, e) & detections.latitude.between(s, n))
            remove |= ((detections.stage == 'NRT') & scope).to_numpy()
            older = (detections.source_sha256 != item['sha256']) & (detections.retrieved_at < item['retrieved_at'])
            superseded_sp |= ((detections.stage == 'SP') & scope & older).to_numpy()
        replacements = int(remove.sum())
        sp_replacements = int(superseded_sp.sum())
        detections = detections.loc[~(remove | superseded_sp)].copy()
        # Same identity/stage: newest retrieved record wins, then checksum, then row.
        detections = detections.sort_values(['retrieved_at', 'source_sha256', 'source_row'])
        grouped = detections.groupby(IDENTITY, dropna=False)
        changed = sum(group.original_record.nunique() > 1 for _, group in grouped)
        provenance = {key: json.dumps(sorted(set(group.provenance))) for key, group in grouped}
        detections = detections.drop_duplicates(IDENTITY, keep='last').reset_index(drop=True)
        detections['all_import_provenance'] = [provenance[tuple(row)] for row in detections[IDENTITY].itertuples(index=False, name=None)]
    else:
        changed = 0
    return detections, valid_intervals, {'imports': reports, 'input_rows': sum(r['input_rows'] for r in reports),
        'rejected_rows': sum(r['rejected_rows'] for r in reports), 'nrt_rows_replaced_by_complete_sp': replacements,
        'sp_rows_replaced_by_newer_complete_sp': sp_replacements,
        'duplicate_rows_removed': before - replacements - sp_replacements - len(detections), 'changed_record_groups': int(changed),
        'retained_rows': len(detections)}


def load_spatial(study, grid_path=None):
    """Read existing lattice; recompute neither cells nor mask policy."""
    grid_path = Path(grid_path or study.root / 'data/interim/study_grid_5km/study_grid_5km.csv')
    grid = pd.read_csv(grid_path)
    required = {'cell_id', 'grid_col', 'grid_row', 'intersects_big_peatland'}
    if not required <= set(grid):
        raise ValueError('Generate the existing candidate grid in preprocessing first.')
    selected = grid.intersects_big_peatland.astype(str).str.lower().map({'true': True, 'false': False})
    if selected.isna().any():
        raise ValueError('Invalid candidate grid membership flags.')
    grid = grid.loc[selected].copy()
    if grid.cell_id.duplicated().any() or grid.duplicated(['grid_col', 'grid_row']).any():
        raise ValueError('Duplicate study cells.')
    if not all(isinstance(v, (int, np.integer)) for v in grid.grid_col) or not all(isinstance(v, (int, np.integer)) for v in grid.grid_row):
        raise ValueError('Lattice addresses must be integer indices.')
    expected = [f'kp5_c{c}_r{r}' for c, r in zip(grid.grid_col, grid.grid_row)]
    if grid.cell_id.tolist() != expected or study.config['grid']['cell_size_m'] != 5000:
        raise ValueError('Candidate grid identity mismatch.')
    boundaries = study.config['boundaries']
    def xy(coordinates):
        if isinstance(coordinates[0], (int, float)):
            return coordinates[:2]
        return [xy(part) for part in coordinates]

    def original_geometry(feature):
        g = feature['geometry']
        return shape({'type': g['type'], 'coordinates': xy(g['coordinates'])})

    def geometry(key):
        content = json.loads((study.root / boundaries[key]).read_text())
        # force_2d preserves the notebook policy for BIG Z/M coordinates.
        return shapely.union_all([original_geometry(f) for f in content['features']])
    provinces, peat = geometry('provinces'), geometry('peatland')
    projector = Transformer.from_crs('EPSG:4326', study.config['grid']['crs_proj'], always_xy=True)
    # Match notebook: transform each original polygon, then intersect unions.
    def projected_union(key):
        content = json.loads((study.root / boundaries[key]).read_text())
        return shapely.union_all([transform(projector.transform, original_geometry(f)) for f in content['features']])
    mask = shapely.intersection(projected_union('provinces'), projected_union('peatland'))
    return grid, mask, projector, shapely.intersection(provinces, peat).bounds


def assign_cells(detections, grid, mask, projector):
    if detections.empty:
        return detections.assign(cell_id=pd.Series(dtype='str'))
    selected = detections.loc[detections.confidence > 80].copy()
    x, y = projector.transform(selected.longitude.to_numpy(), selected.latitude.to_numpy())
    covered = shapely.covers(mask, shapely.points(x, y))
    selected = selected.loc[covered].copy()
    selected['grid_col'] = np.floor(np.asarray(x)[covered] / 5000).astype(int)
    selected['grid_row'] = np.floor(np.asarray(y)[covered] / 5000).astype(int)
    selected = selected.merge(grid[['cell_id', 'grid_col', 'grid_row']], on=['grid_col', 'grid_row'], how='left', validate='many_to_one')
    if selected.cell_id.isna().any():
        raise ValueError('Retained point is outside the supplied candidate lattice.')
    return selected


def labels(study, detections, intervals, grid, mask, projector, mask_bounds, *, include_held_out=False):
    selected_dates = day_range(study.start, study.end) if include_held_out else study.development_dates()
    dates = [d.isoformat() for d in selected_dates]
    assigned = assign_cells(detections, grid, mask, projector)
    assigned = assigned.loc[assigned.acq_date.isin(dates)].copy() if not assigned.empty else assigned
    eligible = {}
    for day in dates:
        complete = [i for i in intervals if i['validated_complete'] and i['start'] <= day <= i['end']
                    and i['bbox'][0] <= mask_bounds[0] and i['bbox'][1] <= mask_bounds[1]
                    and i['bbox'][2] >= mask_bounds[2] and i['bbox'][3] >= mask_bounds[3]]
        eligible[day] = 'complete_sp' if any(i['stage'] == 'SP' for i in complete) else ('complete_nrt' if complete else 'unknown')
    frame = pd.MultiIndex.from_product([grid.cell_id, dates], names=['cell_id', 'acq_date']).to_frame(index=False)
    if not assigned.empty:
        counts = assigned.groupby(['cell_id', 'acq_date']).size().rename('reported_detection_count')
        frame = frame.join(counts, on=['cell_id', 'acq_date'])
    else:
        frame['reported_detection_count'] = np.nan
    frame['reported_detection_count'] = frame.reported_detection_count.fillna(0).astype('Int64')
    frame['retrieval_coverage'] = frame.acq_date.map(eligible)
    frame['detection_count'] = frame.reported_detection_count.where(frame.retrieval_coverage != 'unknown').astype('Int64')
    # Incomplete coverage still supports an observed positive, but never a negative.
    frame['fire_detected'] = (frame.reported_detection_count > 0).astype('Int8').where((frame.retrieval_coverage != 'unknown') | (frame.reported_detection_count > 0))
    return frame, assigned


def plot_eda(daily, assigned, study, output=None):
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    coverage = daily.groupby('acq_date').retrieval_coverage.first()
    dates = pd.to_datetime(coverage.index)
    if len(dates):
        axes[0].scatter(dates, coverage.map({'unknown': 0, 'complete_nrt': 1, 'complete_sp': 2}), s=8)
    axes[0].set(yticks=[0, 1, 2], yticklabels=['Unknown', 'Complete NRT', 'Complete SP'], title='Declared retrieval coverage (UTC)')
    if not assigned.empty:
        counts = assigned.groupby('acq_date').size()
        axes[1].scatter(pd.to_datetime(counts.index), counts.values, s=10)
    axes[1].set(title='Reported confidence > 80 detections in candidate mask', ylabel='Hotspot pixels (not incidents)')
    roles = ', '.join(study.metadata()['development_roles'])
    fig.suptitle(f'{study.profile}: {study.start}–{study.end}; development roles: {roles}\nPartial periods remain partial; unknown coverage is not zero')
    if output:
        fig.savefig(output, dpi=150)
    return fig


def build(study, ledger_path, *, grid_path=None, include_held_out=False):
    """Explicit publication into a new immutable build; never overwrite good data."""
    intervals = read_ledger(ledger_path, study)
    detections, validated, report = ingest(intervals)
    grid, mask, projector, bounds = load_spatial(study, grid_path)
    daily, assigned = labels(study, detections, validated, grid, mask, projector, bounds, include_held_out=include_held_out)
    identity = hashlib.sha256(json.dumps({'study': study.identity, 'intervals': validated,
        'grid': sha256(grid_path or study.root / 'data/interim/study_grid_5km/study_grid_5km.csv'),
        'boundaries': {k: sha256(study.root / v) for k, v in study.config['boundaries'].items()},
        'include_held_out': include_held_out}, sort_keys=True).encode()).hexdigest()[:20]
    destination = study.output_directory / 'firms' / identity
    if destination.exists():
        raise FileExistsError(f'Build already exists: {destination}; reuse it or choose a new cutoff.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.firms-', dir=destination.parent))
    try:
        exported = detections if include_held_out or detections.empty else detections.loc[detections.acq_date.isin({d.isoformat() for d in study.development_dates()})]
        exported.to_csv(temporary / 'normalized_detections.csv', index=False)
        assigned.to_csv(temporary / 'assigned_detections.csv', index=False)
        daily.to_csv(temporary / 'daily_labels.csv', index=False)
        import matplotlib.pyplot as plt
        development = {d.isoformat() for d in study.development_dates()}
        eda_daily = daily.loc[daily.acq_date.isin(development)]
        eda_assigned = assigned.loc[assigned.acq_date.isin(development)] if not assigned.empty else assigned
        plt.close(plot_eda(eda_daily, eda_assigned, study, temporary / 'development_eda.png'))
        metadata = {**study.metadata(), 'firms_build_id': identity, 'include_held_out': include_held_out,
            'intervals': validated, 'ingestion': report, 'type_policy': 'retain all types; confidence strictly greater than 80',
            'observation_caveat': 'No reported qualifying detection is not proof of no fire or cloud-free observation.',
            'files': {p.name: sha256(p) for p in temporary.iterdir()}}
        (temporary / 'manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')
        temporary.rename(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def _get(url):
    # Never include credential-bearing URLs or underlying HTTP errors in exceptions.
    try:
        with urlopen(url, timeout=60) as response:
            return response.read().decode('utf-8-sig')
    except Exception:
        raise RuntimeError('FIRMS request failed; retry within provider limits. MAP_KEY was not logged.') from None


def fetch(study, *, start, end, bbox, stage='SP', map_key=None, output, transport=None, now=None):
    """Fetch supported 1–5 day API windows, with availability checked first.

    Writes raw CSVs plus a ledger only on complete success; injected transport
    enables credential-free integration tests. Existing output is never replaced.
    """
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    if stage not in {'SP', 'NRT'} or start > end or end > study.as_of.date() or start < study.start or end > study.end:
        raise ValueError('Query must fit study dates, cutoff, and SP/NRT processing stage.')
    bbox = _bounds(bbox)
    key = map_key or os.environ.get('FIRMS_MAP_KEY')
    if not key or not key.isalnum():
        raise ValueError('Configure a free alphanumeric FIRMS_MAP_KEY; never commit it.')
    retrieved = parse_as_of(now or datetime.now(timezone.utc))
    if retrieved > study.as_of:
        raise ValueError('Actual retrieval time is after as-of; choose a current cutoff, never backdate evidence.')
    request = transport or _get
    source = f'MODIS_{stage}'
    availability = list(csv.DictReader(io.StringIO(request(f'{API}/data_availability/csv/{key}/{source}'))))
    available = next((r for r in availability if r.get('data_id') == source), None)
    if not available or not available.get('min_date') or not available.get('max_date'):
        raise ValueError('Provider availability response is missing MODIS dates; no coverage was recorded.')
    available_start, available_end = map(date.fromisoformat, (available['min_date'][:10], available['max_date'][:10]))
    if start < available_start or end > available_end:
        raise ValueError(f'{source} API supports {available_start} through {available_end} in this snapshot. '
            f'Request MODIS Collection 6.1 CSV at {ARCHIVE}; retain export start/end, bbox and confirmation, then import using a checksummed interval ledger.')
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Choose a new output directory; existing source files will not be overwritten.')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.firms-fetch-', dir=output.parent))
    intervals = []
    try:
        current = start
        while current <= end:
            stop = min(end, current + timedelta(days=4))
            box = ','.join(str(v) for v in bbox)
            text = request(f'{API}/area/csv/{key}/{source}/{box}/{(stop-current).days+1}/{current}')
            reader = csv.DictReader(io.StringIO(text))
            if not REQUIRED <= set(reader.fieldnames or []):
                raise ValueError('Provider returned an error or missing CSV header; no coverage recorded.')
            name = f'{source}_{current}_{stop}.csv'
            path = temporary / name
            path.write_text(text)
            item = dict(start=str(current), end=str(stop), bbox=bbox, product=PRODUCT, stage=stage,
                status='complete' if stop < retrieved.date() else 'unknown', path=str(path), sha256=sha256(path), retrieved_at=retrieved.isoformat(),
                provenance=f'FIRMS Area API {source}; availability {available_start}..{available_end}; cutoff snapshot')
            _, validation = normalize(item)
            if validation['rejected_rows']:
                raise ValueError('Provider CSV has rejected rows; no complete coverage recorded.')
            intervals.append(dict(item, path=name))
            current = stop + timedelta(days=1)
        (temporary / 'intervals.json').write_text(json.dumps({'schema_version': 1, 'intervals': intervals}, indent=2) + '\n')
        temporary.rename(output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output / 'intervals.json'
