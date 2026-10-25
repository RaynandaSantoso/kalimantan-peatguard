"""Bounded development-only CHIRPS v2/v3 comparison; never switch active sources.

Run from the installed project environment. Raw candidates/results stay in the
ignored assessments directory; the tracked report records the decision.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
from rasterio.features import geometry_mask
from rasterio.transform import Affine

from peatguard.chirps import CHIRPSArchive, EarthEngineCHIRPS, _atomic_json, _coordinates, _validate_values
from peatguard.inventory import sha256, ranges
from peatguard.study import load_study, day_range

COLLECTIONS = {'rnl':'UCSB-CHC/CHIRPS/V3/DAILY_RNL','sat':'UCSB-CHC/CHIRPS/V3/DAILY_SAT'}


def identity(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()[:24]


def native_alignment(projection, spec):
    """Require nominal native resolution and bounded nearest-center registration."""
    if projection['crs']!='EPSG:4326': raise ValueError('Candidate CRS differs.')
    a,b,c,d,e,f=projection['transform'];ta,tb,tc,td,te,tf=spec['grid_transform']
    if not np.allclose([a,b,d,e],[ta,0,0,te],rtol=0,atol=1e-8):
        raise ValueError('Candidate does not retain nominal 0.05-degree resolution.')
    h,w=spec['shape']
    offsets=[]
    for x,y in [(tc+ta*.5,tf+te*.5),(tc+ta*(w-.5),tf+te*(h-.5))]:
        col,row=(x-c)/a-.5,(y-f)/e-.5
        offsets.append([col-round(col),row-round(row)])
    if np.max(np.abs(offsets))>.001:
        raise ValueError('Candidate is shifted by more than 0.001 native pixel; explicit alignment review needed.')
    return {'candidate_native_projection':projection,'target_crs':'EPSG:4326',
        'target_transform':spec['grid_transform'],'target_shape':spec['shape'],
        'sampling':'Earth Engine default nearest neighbor to explicit v2 grid; no bilinear interpolation',
        'corner_center_registration_offsets_pixels':offsets,'exact_native_grid_equality':projection['transform']==spec['grid_transform']}


class Candidate:
    def __init__(self,spec,variant,project,start,end):
        self.spec=dict(spec,collection=COLLECTIONS[variant])
        self.adapter=EarthEngineCHIRPS(self.spec,project)

    def discover(self,start,end):
        self.discovery=self.adapter.discover(start,end)
        if not self.discovery['dates']:
            raise ValueError('No published candidate dates in requested development interval.')
        ee=self.adapter.ee
        collection=ee.ImageCollection(self.spec['collection'])
        first_day=date.fromisoformat(self.discovery['dates'][0])
        last_day=date.fromisoformat(self.discovery['dates'][-1])
        self.projection=(collection.filterDate(str(first_day),str(first_day+timedelta(days=1))).first()
                         .select('precipitation').projection().getInfo())
        last=(collection.filterDate(str(last_day),str(last_day+timedelta(days=1))).first()
              .select('precipitation').projection().getInfo())
        if self.projection!=last: raise ValueError('First/last candidate projections differ.')
        self.alignment=native_alignment(self.projection,self.spec)
        return self.discovery

    def fetch(self,days):
        import requests
        ee=self.adapter.ee
        stamps=[ee.Date(str(day)).millis() for day in days]
        collection=(ee.ImageCollection(self.spec['collection']).filter(ee.Filter.inList('system:time_start',stamps))
                    .select('precipitation').sort('system:time_start'))
        image=collection.toBands()
        expected=[self.discovery['image_ids'][str(day)]+'_precipitation' for day in days]
        if image.bandNames().getInfo()!=expected: raise ValueError('Candidate source date/band identity changed.')
        names=[f'day_{day:%Y%m%d}' for day in days]
        image=image.rename(names)
        a,_,west,_,e,north=self.spec['grid_transform'];h,w=self.spec['shape']
        region=ee.Geometry.Rectangle([west,north+e*h,west+a*w,north],proj='EPSG:4326',geodesic=False)
        try:
            url=image.getDownloadURL({'region':region,'crs':'EPSG:4326','crs_transform':self.spec['grid_transform'],'format':'NPY'})
            response=requests.get(url,timeout=180);response.raise_for_status()
            array=np.load(io.BytesIO(response.content),allow_pickle=False)
        except Exception:
            raise RuntimeError('Candidate download failed; no source product was replaced. Valid checkpoints can resume.') from None
        if array.shape!=(h,w) or array.dtype.names!=tuple(names):raise ValueError('Candidate payload grid/bands differ.')
        values=np.stack([array[name] for name in names]).astype('float32')
        values[values==-9999]=np.nan
        _validate_values(values,days,self.spec)
        return values


def paired_metrics(reference,candidate,mask):
    """Unweighted matched pixel-days, not an independent accuracy validation."""
    if reference.shape!=candidate.shape or mask.shape!=reference.shape[1:] or not mask.any():
        raise ValueError('Comparison grid/mask mismatch.')
    for values in [reference,candidate]:
        if np.isinf(values).any() or np.any(values[np.isfinite(values)]<0):raise ValueError('Invalid rainfall values.')
    valid2=np.isfinite(reference)&mask[None]
    valid3=np.isfinite(candidate)&mask[None]
    matched=valid2&valid3
    x,y=reference[matched].astype('float64'),candidate[matched].astype('float64')
    if not len(x):raise ValueError('No matched valid rainfall coverage.')
    diff=y-x
    def distribution(values):
        return {'mean_mm_day':float(values.mean()),'quantiles_mm_day':dict(zip(['p05','p50','p95','p99'],map(float,np.quantile(values,[.05,.5,.95,.99])))),
                'wet_ge_1mm_fraction':float(np.mean(values>=1))}
    return {'scope':'unweighted matched provincial pixel-days; candidate minus v2',
        'requested_pixel_days':int(reference.shape[0]*mask.sum()),'v2_valid_pixel_days':int(valid2.sum()),
        'v3_valid_pixel_days':int(valid3.sum()),'matched_pixel_days':len(x),
        'v2_only_pixel_days':int((valid2&~valid3).sum()),'v3_only_pixel_days':int((valid3&~valid2).sum()),
        'both_missing_pixel_days':int((mask[None]&~valid2&~valid3).sum()),
        'v2':distribution(x),'v3':distribution(y),'mean_difference_mm_day':float(diff.mean()),
        'mae_mm_day':float(np.abs(diff).mean()),'rmse_mm_day':float(np.sqrt(np.mean(diff**2))),
        'pearson_pixel_day_correlation':float(np.corrcoef(x,y)[0,1]) if np.std(x)>0 and np.std(y)>0 else None,
        'total_ratio_v3_v2':float(y.sum()/x.sum()) if x.sum()>0 else None,
        'wet_day_threshold_disagreement_fraction':float(np.mean((x>=1)!=(y>=1)))}


def province_mask(study):
    spec=study.config['sources']['chirps']
    content=json.loads((study.root/study.config['boundaries']['provinces']).read_text())
    def xy(coords):
        if isinstance(coords[0],(int,float)):return coords[:2]
        return [xy(part) for part in coords]
    geometries=[{'type':f['geometry']['type'],'coordinates':xy(f['geometry']['coordinates'])} for f in content['features']]
    return geometry_mask(geometries,out_shape=tuple(spec['shape']),transform=Affine(*spec['grid_transform']),invert=True)


def assess(study,*,project=None,variants=('rnl','sat'),factory=Candidate):
    days=day_range(study.start,study.end)
    if not days or len(days)>92 or not set(days)<=set(study.development_dates()):
        raise ValueError('Choose at most 92 contiguous development days; held-out dates are prohibited.')
    if not variants or len(set(variants))!=len(variants) or not set(variants)<=COLLECTIONS.keys():
        raise ValueError('Select unique rnl and/or sat variants.')
    archive=CHIRPSArchive(study);manifest,parts=archive.validated();spec=archive.definition
    sources={str(archive.path.relative_to(study.root)):sha256(archive.path),
        study.config['boundaries']['provinces']:sha256(study.root/study.config['boundaries']['provinces']),
        'config/study.json':sha256(study.root/'config/study.json')}
    for part in parts.values():sources[str(part['path'].relative_to(study.root))]=sha256(part['path'])
    local={}
    for part in parts.values():
        indices=[i for i,d in enumerate(part['dates']) if d in days]
        if indices:
            with np.load(part['path'],allow_pickle=False) as data:
                values=data['precipitation_mm_day']
                for i in indices:local[part['dates'][i]]=values[i].copy()
    if set(days)-local.keys():raise ValueError('Local validated v2 coverage is incomplete for the requested comparison; backfill or choose a complete development interval.')
    baseline=np.stack([local[d] for d in days]);mask=province_mask(study)
    base=study.root/'data/interim/assessments/chirps_v3'
    results={};daily_rows=[]
    for variant in variants:
        provider=factory(spec,variant,project,days[0],days[-1])
        discovery=provider.discover(days[0],days[-1])
        published=[date.fromisoformat(d) for d in discovery['dates']]
        if published!=sorted(set(published)) or not set(published)<=set(days):raise ValueError('Invalid candidate discovery dates.')
        alignment=native_alignment(provider.projection,spec)
        arrays=[];received=[];receipts=[]
        for i in range(0,len(published),7):
            chunk=published[i:i+7]
            key={'collection':COLLECTIONS[variant],'variant':variant,'stage':'historical_daily_collection','dates':list(map(str,chunk)),
                 'grid':alignment,'source_image_ids':{str(d):discovery['image_ids'][str(d)] for d in chunk}}
            cache=base/'cache'/variant/identity(key)
            receipt_path=cache/'receipt.json';path=cache/'rainfall.npz'
            if receipt_path.exists():
                receipt=json.loads(receipt_path.read_text())
                if receipt['identity']!=key or sha256(path)!=receipt['sha256']:raise ValueError('Candidate cache identity/checksum mismatch.')
                with np.load(path,allow_pickle=False) as data:
                    values=data['precipitation_mm_day'].copy()
                    if data['dates'].tolist()!=list(map(str,chunk)):raise ValueError('Candidate cache dates differ.')
            else:
                values=provider.fetch(chunk)
                _validate_values(values,chunk,spec)
                cache.mkdir(parents=True,exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=cache,suffix='.npz',delete=False) as stream:
                    pending=Path(stream.name)
                    np.savez_compressed(stream,precipitation_mm_day=values,dates=np.array(list(map(str,chunk))))
                pending.replace(path)
                receipt={'identity':key,'sha256':sha256(path),'retrieved_at':datetime.now(timezone.utc).isoformat()}
                _atomic_json(receipt_path,receipt)
            _validate_values(values,chunk,spec)
            arrays.extend(values);received.extend(chunk)
            receipts.append({'path':str(path.relative_to(study.root)),'sha256':sha256(path),
                'receipt_sha256':sha256(receipt_path),'retrieved_at':receipt['retrieved_at']})
            print(f'{variant}: validated {len(received)}/{len(published)} available days',flush=True)
        candidate=np.full_like(baseline,np.nan)
        for d,values in zip(received,arrays):candidate[days.index(d)]=values
        metrics=paired_metrics(baseline,candidate,mask)
        common=np.isfinite(baseline)&np.isfinite(candidate)&mask[None]
        for i,d in enumerate(days):
            valid=common[i];x,y=baseline[i][valid],candidate[i][valid]
            daily_rows.append({'variant':variant,'date':str(d),'matched_pixels':int(valid.sum()),
                'v2_mean_mm_day':float(x.mean()) if len(x) else None,'v3_mean_mm_day':float(y.mean()) if len(y) else None})
        results[variant]={'collection':COLLECTIONS[variant],'version':'3.0_'+variant,'stage':'historical daily collection (final-era interval)','units':'mm/day',
            'alignment':alignment,'discovery':discovery,'missing_requested_dates':ranges(set(days)-set(received)),
            'actual_received_dates':list(map(str,received)),'candidate_files':receipts,'metrics':metrics}
    for name,digest in sources.items():
        if sha256(study.root/name)!=digest:raise ValueError('v2/boundary inputs changed during comparison.')
    metadata={'study':study.metadata(),'reference_product':{key:spec[key] for key in ['collection','version','bands','units','shape','grid_transform']},'requested_dates':list(map(str,days)),'source_checksums':sources,
        'script_sha256':sha256(Path(__file__)),'variants':results,'mask_pixels':int(mask.sum()),
        'mask_policy':'five BIG provinces; native v2 pixel-center inclusion, unweighted; not peat-cell model eligibility',
        'held_out_outcomes_accessed':False,'active_source_changed':False,'interpretation':'product differences, not independent accuracy validation'}
    build_id=identity({'sources':sources,'script':metadata['script_sha256'],'variants':list(variants),'dates':metadata['requested_dates'],
        'candidate_hashes':{v:[r['sha256'] for r in results[v]['candidate_files']] for v in variants}})
    output=base/build_id
    if output.exists():
        existing=json.loads((output/'comparison.json').read_text())
        for name,digest in existing['artifact_checksums'].items():
            if sha256(output/name)!=digest:raise ValueError('Comparison artifact checksum differs.')
        return output
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=Path(tempfile.mkdtemp(prefix='.comparison-',dir=base))
    try:
        daily=pd.DataFrame(daily_rows);daily.to_csv(temporary/'daily_matched_means.csv',index=False)
        import matplotlib.pyplot as plt
        fig,axes=plt.subplots(len(variants),1,figsize=(11,3.6*len(variants)),squeeze=False,constrained_layout=True)
        for ax,variant in zip(axes[:,0],variants):
            rows=daily[daily.variant.eq(variant)]
            ax.plot(pd.to_datetime(rows.date),rows.v2_mean_mm_day,label='v2 final')
            ax.plot(pd.to_datetime(rows.date),rows.v3_mean_mm_day,label=f'v3 {variant}')
            ax.set(ylabel='Matched provincial mean mm/day',title=f'{variant}: {days[0]}–{days[-1]}; matched pixels only')
            ax.legend();ax.grid(alpha=.2)
        fig.savefig(temporary/'daily_comparison.png',dpi=150);plt.close(fig)
        metadata['build_id']=build_id
        metadata['artifact_checksums']={p.name:sha256(p) for p in temporary.iterdir()}
        _atomic_json(temporary/'comparison.json',metadata)
        temporary.rename(output)
    except Exception:
        import shutil
        shutil.rmtree(temporary,ignore_errors=True)
        raise
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root');parser.add_argument('--project',required=True)
    parser.add_argument('--start',default='2024-01-01');parser.add_argument('--end',default='2024-02-29')
    parser.add_argument('--as-of',required=True);parser.add_argument('--variants',nargs='+',choices=list(COLLECTIONS),default=['rnl','sat'])
    args=parser.parse_args()
    try:
        study=load_study(root=args.root,profile='expanded',start=args.start,end=args.end,as_of=args.as_of)
        print(assess(study,project=args.project,variants=args.variants))
    except (ValueError,OSError,RuntimeError) as error:parser.exit(1,f'Assessment blocked: {error}\n')


if __name__=='__main__':main()
