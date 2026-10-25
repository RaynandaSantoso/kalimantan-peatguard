"""Versioned same-day research datasets; no acquisition or model fitting.

The directory rename is the commit. Joins use explicit cell/date keys, while
source mappings and scaler parameters are recorded as auditable artifacts.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import numpy as np
import pandas as pd
from pyproj import Transformer

from . import firms, spatial
from .chirps import CHIRPSArchive
from .era5_land import archive as weather_archive, coordinates
from .inventory import sha256, ranges
from .study import day_range

VERSION = 'chronological_same_day_v1'
KEYS = ['cell_id', 'acq_date']
CONTINUOUS = ['precipitation_mm_day', 'temperature_2m_C', 'soil_water_layer1_fraction', 'distance_to_built_m']
PREDICTORS = [f'{name}_scaled' for name in CONTINUOUS] + [f'land_cover_{i}' for i in range(1, 18)]


class CoverageError(ValueError):
    def __init__(self, report_path):
        self.report_path = Path(report_path)
        super().__init__(f'Required coverage is incomplete; inspect {report_path}. No dataset was published.')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n')


def _fingerprints(study, ledger_path):
    """Capture input identity before reading features, then check again at commit."""
    sources = {}
    def register(path):
        path = Path(path).resolve()
        key = path.relative_to(study.root).as_posix() if path.is_relative_to(study.root) else str(path)
        sources[key] = sha256(path)
    ledger_path = Path(ledger_path).resolve()
    register(ledger_path)
    for item in json.loads(ledger_path.read_text())['intervals']:
        if item.get('path'): register(ledger_path.parent/item['path'])
    register(study.root/'config/study.json')
    for name in ['study_grid_5km.csv','manifest.json']:
        register(study.root/'data/interim/study_grid_5km'/name)
    for path in study.config['boundaries'].values(): register(study.root/path)
    for source in ['chirps','era5_land','modis','ghsl']:
        path = study.root/study.config['sources'][source]['directory']/'manifest.json'
        if not path.exists(): continue
        register(path)
        manifest = json.loads(path.read_text())
        if source=='ghsl':
            register(study.root/manifest['output'])
        else:
            for entry in manifest['years']: register(study.root/entry['file'])
    return sources


def _grid(study):
    directory = study.root / 'data/interim/study_grid_5km'
    manifest = json.loads((directory/'manifest.json').read_text())
    expected = study.config['grid']
    if (manifest['cell_size_m'] != expected['cell_size_m']
            or manifest['grid_crs_proj'] != expected['crs_proj']
            or manifest['lattice_origin_m'] != expected['lattice_origin_m']
            or manifest['big_mask_rule'] != 'all downloaded BIG polygons, dissolved and intersected with province union'):
        raise ValueError('Candidate grid geometry/mask does not match study configuration.')
    recorded = {source['path']:source['sha256'] for source in manifest['sources']}
    if set(recorded)!=set(study.config['boundaries'].values()):
        raise ValueError('Candidate grid manifest must identify both study boundaries.')
    for source in manifest['sources']:
        if sha256(study.root/source['path']) != source['sha256']:
            raise ValueError('Candidate grid boundary hash changed; rebuild/review grid first.')
    cells, mask, projector, bounds = firms.load_spatial(study)
    if cells.empty:
        raise ValueError('Candidate study grid is empty.')
    size = expected['cell_size_m']
    for axis, index in [('x','grid_col'), ('y','grid_row')]:
        centers = (cells[index].to_numpy()+.5)*size
        if not np.allclose(cells[f'{axis}_center_m'], centers, rtol=0, atol=1e-6):
            raise ValueError('Study-cell centers differ from stable lattice addresses.')
    return cells, mask, projector, bounds


def _mapping(study, cells, spec, reference, source, limit):
    """Freeze nearest native pixels from earliest available training day only."""
    lat, lon = coordinates(spec)
    inverse = Transformer.from_crs(study.config['grid']['crs_proj'], 'EPSG:4326', always_xy=True)
    cell_lon, cell_lat = inverse.transform(cells.x_center_m, cells.y_center_m)
    rows = np.abs(lat[:,None]-cell_lat).argmin(axis=0)
    cols = np.abs(lon[:,None]-cell_lon).argmin(axis=0)
    valid = np.isfinite(reference)
    valid_rows, valid_cols = np.where(valid)
    if not len(valid_rows):
        raise ValueError(f'{source} has no valid training reference pixels.')
    projector = Transformer.from_crs('EPSG:4326', study.config['grid']['crs_proj'], always_xy=True)
    x, y = projector.transform(lon[valid_cols], lat[valid_rows])
    distances = np.zeros(len(cells))
    fallback = ~valid[rows,cols]
    for index in np.where(fallback)[0]:
        squared = (x-cells.iloc[index].x_center_m)**2+(y-cells.iloc[index].y_center_m)**2
        nearest = squared.argmin()
        rows[index], cols[index] = valid_rows[nearest], valid_cols[nearest]
        distances[index] = np.sqrt(squared[nearest])
        if distances[index] > limit:
            raise ValueError(f'{source} coastal fallback exceeds {limit} m for {cells.iloc[index].cell_id}.')
    return pd.DataFrame({'cell_id': cells.cell_id.to_numpy(), 'source':source, 'source_row':rows,
                         'source_col':cols, 'used_fallback':fallback, 'fallback_distance_m':distances})


def _weather_features(study, cells, selected_days):
    rain = CHIRPSArchive(study)
    rain_manifest, rain_parts = rain.validated()
    _, era_parts, _ = weather_archive(study)
    training = set(study.split_dates('train'))
    rain_reference = None
    rain_reference_day = None
    # Historical rule: the first training year's all-day finite rainfall mask.
    for year, part in sorted(rain_parts.items()):
        indices = [i for i,d in enumerate(part['dates']) if d in training]
        if indices:
            with np.load(part['path'],allow_pickle=False) as data:
                rain_reference = np.where(np.isfinite(data['precipitation_mm_day'][indices]).all(axis=0),1.,np.nan)
            rain_reference_day = str(part['dates'][indices[0]])
            break
    era_reference = None
    era_reference_day = None
    for _, (days,values,_) in sorted(era_parts.items()):
        indices = [i for i,d in enumerate(days) if d in training]
        if indices:
            era_reference = values[indices[0],0]
            era_reference_day = str(days[indices[0]])
            break
    if rain_reference is None or era_reference is None:
        raise ValueError('Both weather sources require validated training data to establish mappings.')
    rain_map = _mapping(study,cells,study.config['sources']['chirps'],rain_reference,'chirps',10000)
    era_map = _mapping(study,cells,study.config['sources']['era5_land'],era_reference,'era5_land',20000)
    rain_rows, rain_cols = rain_map.source_row.to_numpy(), rain_map.source_col.to_numpy()
    era_rows, era_cols = era_map.source_row.to_numpy(), era_map.source_col.to_numpy()
    rain_frames, era_frames = [], []
    allowed = set(selected_days)
    for _, part in sorted(rain_parts.items()):
        indices = [i for i,d in enumerate(part['dates']) if d in allowed]
        if not indices: continue
        with np.load(part['path'],allow_pickle=False) as data:
            values = data['precipitation_mm_day'][indices][:,rain_rows,rain_cols]
        for i,index in enumerate(indices):
            rain_frames.append(pd.DataFrame({'cell_id':cells.cell_id.to_numpy(), 'acq_date':str(part['dates'][index]),
                                              'precipitation_mm_day':values[i]}))
    for _, (days,values,_) in sorted(era_parts.items()):
        for i,d in enumerate(days):
            if d not in allowed: continue
            era_frames.append(pd.DataFrame({'cell_id':cells.cell_id.to_numpy(),'acq_date':str(d),
                'temperature_2m_C':values[i,0,era_rows,era_cols]-273.15,
                'soil_water_layer1_fraction':values[i,1,era_rows,era_cols]}))
    rain_table = pd.concat(rain_frames,ignore_index=True) if rain_frames else pd.DataFrame(columns=KEYS+CONTINUOUS[:1])
    era_table = pd.concat(era_frames,ignore_index=True) if era_frames else pd.DataFrame(columns=KEYS+CONTINUOUS[1:3])
    mapping = pd.concat([rain_map,era_map],ignore_index=True)
    mapping['reference_training_date'] = mapping.source.map({'chirps':rain_reference_day,'era5_land':era_reference_day})
    return rain_table,era_table,mapping


def _encode(table, fitted=None):
    """Fit using eligible training rows only, with a fixed categorical vocabulary."""
    frame = table.copy()
    eligible = frame.eligible
    train = frame.loc[eligible & frame.split.eq('train')]
    if fitted is None:
        if train.empty: raise ValueError('No eligible training rows; cannot fit preprocessing.')
        fitted = {}
        for column in CONTINUOUS:
            lower,upper = float(train[column].min()),float(train[column].max())
            fitted[column] = dict(min=lower,max=upper,constant=lower==upper)
    ranges = {}
    for column,params in fitted.items():
        span = params['max']-params['min']
        frame[column+'_scaled'] = (0. if params['constant'] else (frame[column]-params['min'])/span)
        frame[column+'_scaled'] = frame[column+'_scaled'].where(eligible).astype('float64')
    for value in range(1,18):
        frame[f'land_cover_{value}'] = frame.land_cover.eq(value).astype('Int8').where(eligible)
    for role,rows in frame.groupby('split',sort=False):
        ranges[role] = {column: int(((rows[column]<p['min'])|(rows[column]>p['max'])).loc[rows.eligible].sum())
                        for column,p in fitted.items()}
    return frame,fitted,ranges


def prepare(study, ledger_path, *, map_policy='strict_same_year', coverage_policy='require_complete', held_out_interval=None):
    """Prepare one immutable build; include a fixed test calendar or explicit test bounds.

    require_complete blocks any ineligible requested row. exclude_ineligible
    explicitly publishes partial partitions with a complete exclusion ledger.
    Held-out export is structural preparation, not permission for a consumer to
    open outcomes; load_build enforces a separate opt-in.
    """
    if coverage_policy not in ('require_complete','exclude_ineligible'):
        raise ValueError('Choose require_complete or explicitly select exclude_ineligible.')
    if (study.config['target']!='same_day_modis_detection_confidence_gt_80'
            or study.config['date_convention']!='source_day_utc'
            or study.config['grid']['lattice_origin_m']!=[0,0]
            or study.config['grid']['mask_rule']!='all_downloaded_BIG_polygons'):
        raise ValueError('This preparation version only supports the declared same-day UTC candidate reconstruction.')
    selected = {d:role for role in study.metadata()['development_roles'] for d in study.split_dates(role)}
    # A frozen calendar test split can be prepared without opening its outcomes
    # for EDA or model selection. Unfrozen experiments still require boundaries.
    if held_out_interval is None and study.profile_config.get('final_test_interval_frozen', False):
        test_days = sorted(d for role in study.profile_config['held_out']
                           for d in study.split_dates(role, include_held_out=True))
        if test_days:
            held_out_interval = (str(test_days[0]), str(test_days[-1]))
    if held_out_interval:
        start,end = map(date.fromisoformat,held_out_interval)
        allowed = {d for role in study.profile_config['held_out'] for d in study.split_dates(role,include_held_out=True)}
        days = day_range(start,end)
        if not days or not set(days) <= allowed:
            raise ValueError('Held-out interval must be ordered, explicit and contained in requested test dates.')
        for role in study.profile_config['held_out']:
            for d in study.split_dates(role,include_held_out=True):
                if start <= d <= end: selected[d] = role
    if not selected or 'train' not in selected.values():
        raise ValueError('Requested preparation must contain training dates.')
    selected_days = sorted(selected)
    sources = _fingerprints(study,ledger_path)
    cells,mask,projector,bounds = _grid(study)
    intervals = firms.read_ledger(ledger_path,study)
    detections,validated,ingestion = firms.ingest(intervals)
    # Build complete source-day labels then select only the declared export dates.
    labels,_ = firms.labels(study,detections,validated,cells,mask,projector,bounds,include_held_out=bool(held_out_interval))
    labels = labels.loc[labels.acq_date.isin({str(d) for d in selected})].copy()
    rain,weather,mapping = _weather_features(study,cells,selected_days)
    table = labels.merge(rain,on=KEYS,how='left',validate='one_to_one').merge(weather,on=KEYS,how='left',validate='one_to_one')
    assignments = spatial.select_maps(study,policy=map_policy,include_held_out=bool(held_out_interval))
    assignments = [row for row in assignments if row['data_year'] in {d.year for d in selected}]
    maps = spatial.validated_maps(study)
    wanted = {row['map_year'] for row in assignments if row['map_year'] is not None}
    annual = spatial.dominant_classes(study,cells,{y:p for y,p in maps.items() if y in wanted},allow_missing=True).set_index('cell_id')
    table['data_year'] = pd.to_datetime(table.acq_date).dt.year.astype('int16')
    class_rows = []
    for row in assignments:
        for cell_id in cells.cell_id:
            class_rows.append({'cell_id':cell_id,'data_year':row['data_year'],
                               'land_cover':annual.loc[cell_id,str(row['map_year'])] if row['map_year'] is not None else pd.NA,
                               'map_year':row['map_year'], 'map_staleness_years':row['staleness_years']})
    classes = pd.DataFrame(class_rows)
    table = table.merge(classes,on=['cell_id','data_year'],how='left',validate='many_to_one')
    distances = spatial.static_distances(study,cells)
    table = table.merge(distances,on='cell_id',how='left',validate='many_to_one')
    expected_rows = len(cells)*len(selected)
    if len(table)!=expected_rows or table.duplicated(KEYS).any():
        raise ValueError('Cell/date join cardinality differs from requested lattice/calendar.')
    table['split'] = table.acq_date.map({str(d):r for d,r in selected.items()}).astype('string')
    reasons = pd.Series('',index=table.index,dtype='string')
    for condition,reason in [(table.fire_detected.isna(),'unknown_fire_label'),
        *[(~np.isfinite(pd.to_numeric(table[c],errors='coerce')),'missing_'+c) for c in CONTINUOUS],
        (~table.land_cover.isin(range(1,18)),'missing_or_invalid_land_cover')]:
        reasons = reasons + np.where(condition,reason+';', '')
    table['exclusion_reasons'] = reasons.str.rstrip(';')
    table['eligible'] = table.exclusion_reasons.eq('')
    table['provisional_nrt'] = table.retrieval_coverage.eq('complete_nrt')
    table['acq_date'] = pd.to_datetime(table.acq_date).astype('datetime64[ns]')
    table['cell_id'] = table.cell_id.astype('string')
    table['land_cover'] = table.land_cover.where(table.land_cover.isin(range(1,18))).astype('Int8')
    for col in ['map_year','map_staleness_years']: table[col] = table[col].astype('Int16')
    for col in CONTINUOUS: table[col] = pd.to_numeric(table[col]).astype('float64')
    code = {name:sha256(Path(__file__).parent/name) for name in ['datasets.py','study.py','firms.py','spatial.py','chirps.py','era5_land.py','inventory.py']}
    from importlib.metadata import version
    runtime = {name:version(name) for name in ['numpy','pandas','rasterio','pyproj','shapely','pyarrow']}
    descriptor = {'runtime_versions':runtime,'study':study.metadata(),'sources':sources,'preprocessing_version':VERSION,'code_sha256':code,
        'map_policy':map_policy,'coverage_policy':coverage_policy,'held_out_interval':list(held_out_interval) if held_out_interval else None,
        'feature_columns':PREDICTORS,'feature_dtypes':{c:'float64' if c.endswith('_scaled') else 'int8' for c in PREDICTORS},'date_roles':{str(d):r for d,r in selected.items()},
        'mapping_policy':'training-reference only; CHIRPS <=10km, ERA5 <=20km; no later remapping',
        'scientific_status':'candidate_pending_review; retrospective same-day UTC; not forecast-ready'}
    build_id = _digest(descriptor)[:24]
    parent = study.output_directory/'datasets'
    destination = parent/build_id
    quality = {'build_id':build_id,'requested_rows':expected_rows,'eligible_rows':int(table.eligible.sum()),
        'excluded_rows':int((~table.eligible).sum()),'selected_days':len(selected),'cells':len(cells),
        'coverage_policy':coverage_policy,'map_assignments':assignments,'splits':{},
        'held_out_prepared':bool(held_out_interval),'held_out_final_interval_frozen':study.profile_config.get('final_test_interval_frozen',False)}
    for role,rows in table.groupby('split',sort=False):
        eligible_dates = rows.groupby('acq_date').eligible.all()
        quality['splits'][role] = dict(requested_rows=len(rows),eligible_rows=int(rows.eligible.sum()),
            excluded_rows=int((~rows.eligible).sum()),requested_start=str(rows.acq_date.min().date()),requested_end=str(rows.acq_date.max().date()),
            complete_cell_days=int(eligible_dates.sum()),
            common_complete_ranges=ranges({d.date() for d,complete in eligible_dates.items() if complete}),
            retrieval_coverage_counts={str(k):int(v) for k,v in rows.retrieval_coverage.value_counts().items()},
            excluded_reason_counts={r:int(rows.exclusion_reasons.str.contains(r,regex=False).sum())
                for r in sorted({reason for text in rows.exclusion_reasons for reason in text.split(';') if reason})})
    if coverage_policy=='require_complete' and not table.eligible.all():
        failure = study.output_directory/'diagnostics'/build_id
        failure.mkdir(parents=True,exist_ok=True)
        table.loc[~table.eligible,KEYS+['split','exclusion_reasons']].to_parquet(failure/'exclusions.parquet',index=False)
        _json(failure/'quality.json',quality)
        raise CoverageError(failure/'quality.json')
    table,scaler,out_of_range = _encode(table)
    if destination.exists():
        load_build(destination,validate_sources=True)
        return destination
    parent.mkdir(parents=True,exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.dataset-',dir=parent))
    try:
        table = table.sort_values(['acq_date','cell_id']).reset_index(drop=True)
        table.to_parquet(temporary/'combined.parquet',index=False)
        table.loc[~table.eligible,KEYS+['split','exclusion_reasons']].to_parquet(temporary/'exclusions.parquet',index=False)
        mapping.to_csv(temporary/'source_mappings.csv',index=False)
        _json(temporary/'scaler.json',scaler)
        _json(temporary/'quality.json',quality)
        partitions = {}
        for role in dict.fromkeys(selected.values()):
            rows = table.loc[table.split.eq(role)&table.eligible,KEYS+['fire_detected']+PREDICTORS].copy()
            rows['fire_detected'] = rows.fire_detected.astype('int8')
            for column in PREDICTORS: rows[column] = rows[column].astype('float64' if column.endswith('_scaled') else 'int8')
            name = role+'.parquet'
            rows.to_parquet(temporary/name,index=False)
            partitions[role] = dict(file=name,rows=len(rows),dtypes={c:str(t) for c,t in rows.dtypes.items()},
                held_out=role in study.profile_config['held_out'])
        artifacts = {p.name:sha256(p) for p in temporary.iterdir()}
        manifest = {'build_id':build_id,'descriptor':descriptor,'predictors':PREDICTORS,'partitions':partitions,
            'fitted_ranges':scaler,'out_of_range_counts':out_of_range,'files':artifacts,'feature_vocabulary':list(range(1,18)),
            'combined_schema':{c:str(t) for c,t in table.dtypes.items()},'quality':quality,
            'held_out_outcomes_opened_by_preparation':False,'ingestion_report_scope':'source integrity counts; no held-out target distributions'}
        # Guard against a source revision arriving while features were prepared.
        for name,digest in sources.items():
            if sha256(study.root/name)!=digest: raise ValueError('Upstream inputs changed during preparation; retry.')
        _json(temporary/'manifest.json',manifest)
        temporary.rename(destination)
    except Exception:
        shutil.rmtree(temporary,ignore_errors=True)
        raise
    return destination


def load_build(path, *, roles=None, open_held_out=False, validate_sources=True):
    """Read only selected partitions; default never opens test or combined files."""
    path = Path(path).resolve()
    manifest = json.loads((path/'manifest.json').read_text())
    descriptor = manifest['descriptor']
    if _digest(descriptor)[:24]!=manifest['build_id'] or path.name!=manifest['build_id']:
        raise ValueError('Build identity mismatch.')
    if descriptor['preprocessing_version']!=VERSION:
        raise ValueError('Unsupported preprocessing version.')
    if manifest['predictors']!=PREDICTORS or descriptor['feature_columns']!=PREDICTORS:
        raise ValueError('Unsupported predictor schema/order.')
    selected = list(roles) if roles is not None else [r for r,p in manifest['partitions'].items() if not p['held_out']]
    if not selected or any(r not in manifest['partitions'] for r in selected):
        raise ValueError('Requested partition is not in this build.')
    if any(manifest['partitions'][r]['held_out'] for r in selected) and not open_held_out:
        raise ValueError('Held-out outcomes require explicit open_held_out=True.')
    if validate_sources:
        from .study import find_root
        root = find_root(path)
        for name,digest in descriptor['sources'].items():
            if sha256(root/name)!=digest: raise ValueError('Upstream inputs changed; rebuild before baseline use.')
    for name in ['scaler.json','source_mappings.csv','quality.json']:
        if sha256(path/name)!=manifest['files'].get(name):
            raise ValueError('Preprocessing artifact checksum mismatch.')
    if json.loads((path/'scaler.json').read_text())!=manifest['fitted_ranges']:
        raise ValueError('Fitted preprocessing metadata differs within build.')
    loaded = {}
    for role in selected:
        info = manifest['partitions'][role]
        file = path/info['file']
        if file.parent!=path or sha256(file)!=manifest['files'].get(info['file']):
            raise ValueError('Partition checksum/path mismatch.')
        frame = pd.read_parquet(file)
        if (len(frame)!=info['rows'] or frame.columns.tolist()!=KEYS+['fire_detected']+PREDICTORS
                or {c:str(t) for c,t in frame.dtypes.items()}!=info['dtypes'] or frame.duplicated(KEYS).any()):
            raise ValueError('Partition schema, types, row count or key uniqueness mismatch.')
        if (not np.isfinite(frame[PREDICTORS].to_numpy()).all()
                or not frame.fire_detected.isin([0,1]).all()
                or not frame[[f'land_cover_{i}' for i in range(1,18)]].sum(axis=1).eq(1).all()):
            raise ValueError('Invalid eligible predictors, categories or binary target.')
        expected_roles = frame.acq_date.dt.strftime('%Y-%m-%d').map(descriptor['date_roles'])
        if not expected_roles.eq(role).all():
            raise ValueError('Partition dates differ from declared chronological split.')
        loaded[role] = frame
    if any(manifest['partitions'][r]['held_out'] for r in selected):
        from datetime import datetime,timezone
        # Append a local access record without changing immutable build contents.
        audit = path.parent/'held_out_access.jsonl'
        with audit.open('a') as stream:
            stream.write(json.dumps({'build_id':manifest['build_id'],'roles':selected,
                'opened_at':datetime.now(timezone.utc).isoformat()})+'\n')
    return manifest,loaded
