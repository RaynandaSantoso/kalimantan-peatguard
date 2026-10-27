"""Fixed-cutoff local sources -> keyed partitions -> guarded baseline loading."""
from datetime import date
import csv
import os
import subprocess
import sys
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from pyproj import Transformer
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import box,mapping

from peatguard.datasets import CoverageError,CONTINUOUS,PREDICTORS,load_build,prepare
from peatguard.inventory import sha256
from peatguard.study import day_range,load_study


class DatasetTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root/'config').mkdir(); (self.root/'peatguard').mkdir()
        self.config = json.loads((Path(__file__).parents[1]/'config/study.json').read_text())
        # Synthetic three-period fixture exercises generic validation/held-out behavior.
        self.config['profiles']['expanded'] = {'start': '2020-01-01', 'end': '2026-12-31', 'splits': {'train': ['2020-01-01', '2024-12-31'], 'validation': ['2025-01-01', '2025-12-31'], 'test': ['2026-01-01', '2026-12-31']}, 'held_out': ['test'], 'final_test_interval_frozen': False}
        for source,transform in [('chirps',[.05,0,113.95,0,-.05,.05]),('era5_land',[.1,0,113.9,0,-.1,.1])]:
            self.config['sources'][source].update(shape=[2,2],grid_transform=transform)
        (self.root/'config/study.json').write_text(json.dumps(self.config))
        self.study = load_study(root=self.root,start='2024-12-30',end='2026-01-02',as_of='2026-10-25')
        self.days = day_range(self.study.start,self.study.end)
        self.held = ('2026-01-01','2026-01-02')
        polygon = {'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':mapping(box(114.001,.001,114.02,.02))}]}
        for path in self.config['boundaries'].values():
            file=self.root/path; file.parent.mkdir(parents=True,exist_ok=True);file.write_text(json.dumps(polygon))
        grid_dir=self.root/'data/interim/study_grid_5km';grid_dir.mkdir(parents=True)
        pd.DataFrame([dict(cell_id='kp5_c0_r0',grid_col=0,grid_row=0,x_center_m=2500.,y_center_m=2500.,intersects_big_peatland=True)]).to_csv(grid_dir/'study_grid_5km.csv',index=False)
        (grid_dir/'manifest.json').write_text(json.dumps({'cell_size_m':5000,'grid_crs_proj':self.config['grid']['crs_proj'],
            'lattice_origin_m':[0,0],'big_mask_rule':'all downloaded BIG polygons, dissolved and intersected with province union',
            'bounds_indices_stop_exclusive':{'col_start':0,'col_stop':1,'row_start':0,'row_stop':1},
            'sources':[{'path':p,'sha256':sha256(self.root/p)} for p in self.config['boundaries'].values()]}))
        self.write_daily('chirps');self.write_daily('era5_land')
        self.write_maps()
        spec=self.config['sources']['ghsl'];directory=self.root/spec['directory'];directory.mkdir(parents=True)
        path=directory/spec['pattern']
        affine=from_origin(0,5000,100,100);crs=CRS.from_proj4(self.config['grid']['crs_proj'])
        self.raster(path,np.ones((1,50,50),np.uint16),crs,affine,['built_surface'],65535)
        (directory/'manifest.json').write_text(json.dumps({'earth_engine_image':spec['collection'],'output':str(path.relative_to(self.root)),
            'sha256':sha256(path),'shape_rows_columns':[50,50],'transform':list(affine)[:6],'crs_wkt':crs.to_wkt()}))
        source=self.root/'fires.csv'
        with source.open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['latitude','longitude','acq_date','acq_time','satellite','instrument','confidence','version','type'])
            writer.writeheader();writer.writerow(dict(latitude=.01,longitude=114.01,acq_date='2024-12-30',acq_time='0007',satellite='Terra',instrument='MODIS',confidence=81,version='6.03',type=0))
        self.ledger=self.root/'ledger.json'
        self.ledger.write_text(json.dumps({'schema_version':1,'intervals':[dict(start=str(self.study.start),end=str(self.study.end),bbox=[113,-1,115,1],
            product='FIRMS_MODIS_C6.1',stage='SP',status='complete',path='fires.csv',sha256=sha256(source),retrieved_at='2026-10-25T00:00:00Z',provenance='fixture export confirmation')]}))

    def raster(self,path,values,crs,affine,bands,nodata=None):
        with rasterio.open(path,'w',driver='GTiff',width=values.shape[2],height=values.shape[1],count=len(bands),dtype=values.dtype,crs=crs,transform=affine,nodata=nodata) as dst:
            dst.write(values)
            for i,band in enumerate(bands,1):dst.set_band_description(i,band)

    def write_daily(self,source,*,omit=None,missing=None,extreme=100):
        spec=self.config['sources'][source];directory=self.root/spec['directory'];directory.mkdir(parents=True,exist_ok=True)
        records=[]
        for year in sorted({d.year for d in self.days}):
            days=[d for d in self.days if d.year==year and str(d)!=omit]
            values={}
            for band in spec['bands']:
                data=np.full((len(days),2,2),2. if source=='chirps' else (280. if band=='temperature_2m_K' else .4),np.float32)
                if source=='chirps':
                    for i,d in enumerate(days):
                        if d==date(2024,12,31):data[i]=4
                        if d.year>=2025:data[i]=extreme if d.year==2025 else extreme*2
                if missing:
                    for i,d in enumerate(days):
                        if str(d)==missing:data[i,0,1]=np.nan
                values[band]=data
            a,_,west,_,e,north=spec['grid_transform']
            path=directory/spec['pattern'].replace('*',str(year))
            np.savez_compressed(path,dates=np.array(list(map(str,days))),latitude=north+e*(np.arange(2)+.5),longitude=west+a*(np.arange(2)+.5),**values)
            records.append(dict(year=year,days=len(days),file=str(path.relative_to(self.root)),sha256=sha256(path)))
        manifest=dict(earth_engine_collection=spec['collection'],years=records,shape_lat_lon=[2,2],grid_transform=spec['grid_transform'],crs='EPSG:4326')
        if source=='chirps':manifest['unit']='mm/day'
        (directory/'manifest.json').write_text(json.dumps(manifest))

    def write_maps(self):
        spec=self.config['sources']['modis'];directory=self.root/spec['directory'];directory.mkdir(parents=True,exist_ok=True)
        crs=CRS.from_proj4('+proj=sinu +R=6371007.181 +no_defs');pixel=463.3127165279165
        x,_=Transformer.from_crs(4326,crs,always_xy=True).transform(114,0)
        affine=from_origin(round(x/pixel)*pixel-10*pixel,10*pixel,pixel,pixel)
        records=[]
        for year,code in [(2023,2),(2024,2),(2025,17),(2026,3)]:
            data=np.ones((3,20,20),np.uint8);data[0]=code
            path=directory/spec['pattern'].replace('*',str(year));self.raster(path,data,crs,affine,spec['bands'])
            records.append(dict(year=year,file=str(path.relative_to(self.root)),sha256=sha256(path),shape_lat_lon=[20,20],transform=list(affine)[:6]))
        (directory/'manifest.json').write_text(json.dumps(dict(earth_engine_collection=spec['collection'],years=records)))

    def build(self,**kwargs):return prepare(self.study,self.ledger,held_out_interval=self.held,**kwargs)

    def test_preprocessing_notebook_has_visible_defaults_without_environment_setup(self):
        notebook = json.loads((Path(__file__).parents[1] / 'preprocessing.ipynb').read_text())
        setup = next(c for c in notebook['cells'] if c['cell_type'] == 'code'
                     and 'default_ledger = ' in ''.join(c['source']))
        import shutil
        destination = self.root / 'data/raw/Firms/intervals.json'
        destination.parent.mkdir(parents=True)
        # The stub isolates notebook configuration from source preparation.
        shutil.copy(self.ledger, destination)
        scope = {'ROOT': self.root}
        with patch.dict(os.environ, {}, clear=True), patch('peatguard.datasets.prepare', return_value=self.root / 'prepared') as mocked:
            exec(''.join(setup['source']), scope)
        self.assertEqual(scope['ledger'], str(destination))
        self.assertEqual(scope['study'].as_of.date(), date(2026, 10, 25))
        self.assertEqual(mocked.call_args.args[1], str(destination))
        report = self.root / 'quality.json'
        report.write_text(json.dumps({'splits': {'train': {
            'requested_start': '2020-01-01', 'requested_end': '2024-12-31',
            'requested_rows': 2, 'eligible_rows': 1, 'excluded_rows': 1,
            'excluded_reason_counts': {'unknown_label': 1}}}}))
        scope['display'] = lambda table: None
        with patch.dict(os.environ, {}, clear=True), patch('peatguard.datasets.prepare', side_effect=CoverageError(report)):
            exec(''.join(setup['source']), scope)
        self.assertIsNone(scope['build_directory'], 'A blocked rerun must not retain the previous successful build')
        self.assertEqual(scope['quality_report'], report)
        with patch('peatguard.datasets.load_build') as loader:
            exec(''.join(notebook['cells'][17]['source']), scope)
        loader.assert_not_called()

    def test_active_research_profile_trains_through_2024_and_tests_2025_only(self):
        active = json.loads((Path(__file__).parents[1] / 'config/study.json').read_text())
        self.config['profiles']['expanded'] = active['profiles']['expanded']
        (self.root / 'config/study.json').write_text(json.dumps(self.config))
        self.study = load_study(root=self.root, start='2024-12-30', as_of='2026-10-25')
        self.assertEqual(str(self.study.end), '2025-12-31')
        self.assertEqual(set(self.study.metadata()['splits']), {'train', 'test'})
        self.assertEqual(self.study.metadata()['development_roles'], ['train'])
        self.assertEqual(self.study.profile_config['splits']['train'], ['2020-01-01', '2024-12-31'])
        with self.assertRaises(ValueError):
            load_study(root=self.root, end='2026-01-01', as_of='2026-10-25')
        output = prepare(self.study, self.ledger)
        contract, parts = load_build(output)
        self.assertEqual(set(contract['partitions']), {'train', 'test'})
        self.assertEqual(set(parts), {'train'})
        self.assertEqual(len(parts['train']), 2)
        with self.assertRaisesRegex(ValueError, 'Held-out'):
            load_build(output, roles=['test'])
        _, held = load_build(output, roles=['test'], open_held_out=True)
        self.assertEqual(len(held['test']), 365)
        self.assertEqual(set(pd.to_datetime(held['test'].acq_date).dt.year), {2025})
        combined = pd.read_parquet(output / 'combined.parquet')
        self.assertLessEqual(pd.to_datetime(combined.acq_date).max(), pd.Timestamp('2025-12-31'))
        self.assertEqual(contract['descriptor']['held_out_interval'], ['2025-01-01', '2025-12-31'])

        # Exercise the actual notebook export and simple baseline imports. Old
        # paper-reproduction files must survive the new chronological exports.
        directory = self.root / 'data/interim/model_table'
        directory.mkdir(parents=True)
        legacy = directory / 'train_2020_2023.parquet'
        legacy.write_bytes(b'preserve original experiment')
        notebooks = Path(__file__).parents[1]
        preprocessing = json.loads((notebooks / 'preprocessing.ipynb').read_text())
        export_cell = next(c for c in preprocessing['cells'] if c.get('id') == 'named-partition-exports')
        scope = {'ROOT': self.root, 'build_directory': output, 'Path': Path, 'json': json}
        exec(''.join(export_cell['source']), scope)
        self.assertEqual(legacy.read_bytes(), b'preserve original experiment')
        self.assertEqual(sha256(directory / 'train_2024.parquet'), sha256(output / 'train.parquet'))
        self.assertEqual(sha256(directory / 'test_2025.parquet'), sha256(output / 'test.parquet'))
        provenance = json.loads((directory / 'prepared_dataset.json').read_text())
        self.assertEqual(provenance['build_id'], output.name)
        self.assertEqual(provenance['partitions']['test']['start'], '2025-01-01')

        # The baseline uses full-study filenames; the fixture's training request
        # deliberately covers only 2024, so supply its explicit equivalent.
        import shutil
        shutil.copyfile(directory / 'train_2024.parquet', directory / 'train_2020_2024.parquet')
        baseline = json.loads((notebooks / 'base_models.ipynb').read_text())
        for cwd in [self.root, self.root / 'EDA']:
            cwd.mkdir(exist_ok=True)
            before = Path.cwd()
            try:
                os.chdir(cwd)
                baseline_scope = {}
                for cell in baseline['cells'][1:3]:
                    exec(''.join(cell['source']), baseline_scope)
            finally:
                os.chdir(before)
            self.assertEqual(len(baseline_scope['train']), 2)
            self.assertEqual(len(baseline_scope['test']), 365)
            self.assertEqual(baseline_scope['feature_columns'], PREDICTORS)

        # A blocked preparation must not publish stale convenience exports.
        scope['build_directory'] = None
        before_hash = sha256(directory / 'test_2025.parquet')
        exec(''.join(export_cell['source']), scope)
        self.assertEqual(scope['exported_paths'], {})
        self.assertEqual(sha256(directory / 'test_2025.parquet'), before_hash)

    def test_complete_source_to_partitions_train_only_scaling_and_guarded_loading(self):
        before={p:sha256(p) for p in self.root.rglob('*') if p.is_file()}
        with patch('socket.socket',side_effect=AssertionError('No acquisition')):output=self.build()
        manifest,parts=load_build(output)
        self.assertEqual(set(parts),{'train','validation'})
        self.assertEqual(len(parts['train']),2);self.assertEqual(len(parts['validation']),365)
        self.assertEqual(parts['train'].columns.tolist()[3:],PREDICTORS)
        self.assertEqual(manifest['fitted_ranges']['precipitation_mm_day'],dict(min=2.,max=4.,constant=False))
        self.assertTrue(manifest['fitted_ranges']['temperature_2m_C']['constant'])
        self.assertTrue((parts['validation'].temperature_2m_C_scaled==0).all())
        self.assertTrue((parts['validation'].precipitation_mm_day_scaled>1).all())
        self.assertTrue((parts['validation'].land_cover_17==1).all())
        self.assertEqual(manifest['out_of_range_counts']['validation']['precipitation_mm_day'],365)
        self.assertTrue(all(sha256(p)==digest for p,digest in before.items()))
        with self.assertRaises(ValueError):load_build(output,roles=['test'])
        # Even a corrupt test file is never read/hashed by default development loading.
        test=output/'test.parquet';data=test.read_bytes();test.write_bytes(b'not parquet')
        load_build(output)
        with self.assertRaises(ValueError):load_build(output,roles=['test'],open_held_out=True)
        test.write_bytes(data)
        _,test_parts=load_build(output,roles=['test'],open_held_out=True)
        self.assertEqual(len(test_parts['test']),2)
        self.assertTrue((output.parent/'held_out_access.jsonl').exists())
        combined=pd.read_parquet(output/'combined.parquet')
        self.assertEqual(len(combined),369);self.assertFalse(combined.duplicated(['cell_id','acq_date']).any())
        self.assertTrue(combined.eligible.all())

    def test_missing_source_day_blocks_then_explicit_exclusion_is_audited(self):
        self.write_daily('chirps',omit='2025-02-01')
        with self.assertRaises(CoverageError) as caught:self.build()
        quality=json.loads(caught.exception.report_path.read_text())
        self.assertEqual(quality['excluded_rows'],1)
        output=self.build(coverage_policy='exclude_ineligible')
        manifest,parts=load_build(output)
        self.assertEqual(len(parts['validation']),364)
        excluded=pd.read_parquet(output/'exclusions.parquet')
        self.assertIn('missing_precipitation_mm_day',excluded.exclusion_reasons.iloc[0])
        self.assertEqual(manifest['quality']['requested_rows'],369)

    def test_unknown_retrieval_never_becomes_negative_or_disappears(self):
        ledger=json.loads(self.ledger.read_text());ledger['intervals'][0]['status']='unknown';self.ledger.write_text(json.dumps(ledger))
        with self.assertRaises(CoverageError):self.build()
        output=self.build(coverage_policy='exclude_ineligible')
        combined=pd.read_parquet(output/'combined.parquet')
        self.assertTrue(combined.loc[combined.acq_date!=pd.Timestamp('2024-12-30'),'fire_detected'].isna().all())
        self.assertEqual(len(pd.read_parquet(output/'exclusions.parquet')),368)

    def test_later_missing_pixel_is_not_remapped(self):
        self.write_daily('chirps',missing='2025-02-01')
        with self.assertRaises(CoverageError):self.build()
        output=self.build(coverage_policy='exclude_ineligible')
        mapping=pd.read_csv(output/'source_mappings.csv')
        self.assertTrue(mapping.fallback_distance_m.eq(0).all())
        self.assertIn('2024-12-30',mapping.reference_training_date.tolist())

    def test_revisions_make_old_build_stale_and_failed_publication_preserves_it(self):
        output=self.build();original=(output/'manifest.json').read_bytes()
        self.assertEqual(self.build(),output)
        self.write_daily('chirps',extreme=300)
        with self.assertRaisesRegex(ValueError,'Upstream'):load_build(output)
        with patch('pandas.DataFrame.to_parquet',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.build()
        self.assertEqual((output/'manifest.json').read_bytes(),original)
        new=self.build();self.assertNotEqual(new,output)
        self.assertEqual(json.loads((new/'manifest.json').read_text())['fitted_ranges'],json.loads(original)['fitted_ranges'])

    def test_invalid_category_is_excluded_with_nullable_not_all_zero_encoding(self):
        from peatguard.spatial import dominant_classes
        def missing_class(*args,**kwargs):
            result=dominant_classes(*args,**kwargs)
            result['2025']=0
            return result
        with patch('peatguard.datasets.spatial.dominant_classes',side_effect=missing_class):
            with self.assertRaises(CoverageError):self.build()
            output=self.build(coverage_policy='exclude_ineligible')
        frame=pd.read_parquet(output/'combined.parquet')
        invalid=frame.loc[frame.split.eq('validation')]
        self.assertTrue(invalid.land_cover.isna().all())
        self.assertTrue(invalid[[f'land_cover_{i}' for i in range(1,18)]].isna().all().all())
        self.assertTrue(invalid.exclusion_reasons.str.contains('invalid_land_cover').all())
        self.assertTrue(load_build(output)[1]['validation'].empty)

    def test_fallback_distances_are_recorded_and_limits_enforced(self):
        # Mask the nearest rainfall pixel in training as well as later periods.
        directory=self.root/self.config['sources']['chirps']['directory']
        manifest=json.loads((directory/'manifest.json').read_text())
        for entry in manifest['years']:
            path=self.root/entry['file']
            with np.load(path) as source: content={k:source[k] for k in source.files}
            content['precipitation_mm_day'][:,0,1]=np.nan
            np.savez_compressed(path,**content);entry['sha256']=sha256(path)
        (directory/'manifest.json').write_text(json.dumps(manifest))
        output=self.build()
        mapping=pd.read_csv(output/'source_mappings.csv')
        row=mapping.loc[mapping.source.eq('chirps')].iloc[0]
        self.assertTrue(row.used_fallback);self.assertGreater(row.fallback_distance_m,0)
        self.assertLess(row.fallback_distance_m,10000)
        # Enlarge source spacing in the fixture while retaining the nearest
        # masked native pixel; every possible fallback is now beyond 10 km.
        transform=self.config['sources']['chirps']['grid_transform']
        transform[:]=[.5,0,113.25,0,-.5,.25]
        (self.root/'config/study.json').write_text(json.dumps(self.config))
        self.study=load_study(root=self.root,start='2024-12-30',end='2026-01-02',as_of='2026-10-25')
        self.write_daily('chirps')
        manifest=json.loads((directory/'manifest.json').read_text())
        for entry in manifest['years']:
            path=self.root/entry['file']
            with np.load(path) as source: content={k:source[k] for k in source.files}
            content['precipitation_mm_day'][:,0,1]=np.nan
            np.savez_compressed(path,**content);entry['sha256']=sha256(path)
        (directory/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,'fallback exceeds 10000'):self.build()

    def test_duplicate_grid_and_bad_geometry_fail_before_publication(self):
        path=self.root/'data/interim/study_grid_5km/study_grid_5km.csv'
        original=pd.read_csv(path)
        pd.concat([original,original]).to_csv(path,index=False)
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.build()
        original['x_center_m']=42
        original.to_csv(path,index=False)
        with self.assertRaisesRegex(ValueError,'centers'):self.build()

    def test_cli_loader_and_preprocessing_from_both_supported_directories(self):
        script=Path(__file__).resolve().parents[1]/'scripts/prepare_dataset.py'
        result=subprocess.run([sys.executable,str(script),'--root',str(self.root),
            '--as-of','2026-10-25','--start','2024-12-30','--end','2026-01-02','--ledger',str(self.ledger),
            '--held-out-start','2026-01-01','--held-out-end','2026-01-02'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        output=Path(json.loads(result.stdout)['build_directory'])
        (self.root/'EDA').mkdir()
        before=Path.cwd()
        try:
            for cwd in [self.root,self.root/'EDA']:
                os.chdir(cwd)
                contract, partitions = load_build(output)
                self.assertEqual(contract['predictors'],PREDICTORS)
                self.assertEqual(len(partitions['validation']),365)
                self.assertNotIn('test',partitions)
        finally:os.chdir(before)
        self.assertFalse((output.parent/'held_out_access.jsonl').exists())
        preprocessing=json.loads((script.parents[1]/'preprocessing.ipynb').read_text())
        environment={'FIRMS_LEDGER':str(self.ledger),'PEATGUARD_AS_OF':'2026-10-25',
            'PEATGUARD_PROFILE':'expanded','PEATGUARD_START':'2024-12-30','PEATGUARD_END':'2026-01-02',
            'PEATGUARD_HELD_OUT_START':'2026-01-01','PEATGUARD_HELD_OUT_END':'2026-01-02',
            'PEATGUARD_MAP_POLICY':'strict_same_year','PEATGUARD_COVERAGE_POLICY':'require_complete'}
        with patch.dict(os.environ,environment):
            scope={'ROOT':self.root}
            for cell in preprocessing['cells'][14:]:
                if cell['cell_type']=='code':exec(''.join(cell['source']),scope)
        self.assertEqual(scope['build_directory'],output)
        self.assertEqual(set(scope['development']),{'train','validation'})

    def test_legacy_profile_has_separate_identity_and_guarded_2024(self):
        self.study=load_study(root=self.root,profile='legacy',start='2023-12-30',end='2024-01-02',as_of='2026-10-25')
        self.days=day_range(self.study.start,self.study.end)
        # Replace fixture inventories cleanly with the requested legacy interval.
        import shutil
        for key in ['chirps','era5_land']:
            shutil.rmtree(self.root/self.config['sources'][key]['directory'])
            self.write_daily(key)
        source=self.root/'fires.csv'
        source.write_text(source.read_text().splitlines()[0]+'\n')
        ledger=json.loads(self.ledger.read_text());item=ledger['intervals'][0]
        item.update(start='2023-12-30',end='2024-01-02',sha256=sha256(source))
        self.ledger.write_text(json.dumps(ledger))
        output=prepare(self.study,self.ledger,held_out_interval=('2024-01-01','2024-01-02'))
        manifest,parts=load_build(output)
        self.assertEqual(set(parts),{'train'})
        self.assertEqual(len(parts['train']),2)
        self.assertEqual(manifest['descriptor']['study']['profile'],'legacy')
        self.assertIn('legacy',output.parts)
        with self.assertRaises(ValueError):load_build(output,roles=['test'])

    def test_constant_training_feature_ignores_validation_and_test_extremes(self):
        directory=self.root/self.config['sources']['era5_land']['directory']
        manifest=json.loads((directory/'manifest.json').read_text())
        for entry in manifest['years']:
            if entry['year']==2024:continue
            path=self.root/entry['file']
            with np.load(path) as source: content={k:source[k] for k in source.files}
            content['temperature_2m_K'][:]=350 if entry['year']==2025 else 400
            np.savez_compressed(path,**content);entry['sha256']=sha256(path)
        (directory/'manifest.json').write_text(json.dumps(manifest))
        output=self.build();contract,parts=load_build(output)
        self.assertTrue(contract['fitted_ranges']['temperature_2m_C']['constant'])
        self.assertTrue(parts['validation'].temperature_2m_C_scaled.eq(0).all())
        self.assertEqual(contract['out_of_range_counts']['validation']['temperature_2m_C'],365)
        self.assertEqual(contract['out_of_range_counts']['test']['temperature_2m_C'],2)

    def test_missing_map_blocks_or_explicit_carry_forward_retains_map_year(self):
        directory=self.root/self.config['sources']['modis']['directory']
        manifest=json.loads((directory/'manifest.json').read_text())
        missing=next(e for e in manifest['years'] if e['year']==2025)
        (self.root/missing['file']).unlink()
        manifest['years']=[e for e in manifest['years'] if e['year']!=2025]
        (directory/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(CoverageError):self.build()
        output=self.build(map_policy='carry_forward')
        combined=pd.read_parquet(output/'combined.parquet')
        validation=combined.loc[combined.split.eq('validation')]
        self.assertTrue(validation.map_year.eq(2024).all())
        self.assertTrue(validation.map_staleness_years.eq(1).all())
        self.assertTrue(validation.land_cover_2.eq(1).all())

    def test_concurrent_input_change_is_detected_before_publication(self):
        from peatguard.spatial import static_distances
        def changing(*args,**kwargs):
            result=static_distances(*args,**kwargs)
            source=self.root/'fires.csv'
            source.write_text(source.read_text()+'\n')
            return result
        with patch('peatguard.datasets.spatial.static_distances',side_effect=changing):
            with self.assertRaisesRegex(ValueError,'changed during preparation'):self.build()
        self.assertEqual(list((self.study.output_directory/'datasets').glob('*/manifest.json')),[])

    def test_heldout_bounds_and_default_development_only(self):
        output=prepare(self.study,self.ledger)
        self.assertEqual(set(load_build(output)[1]),{'train','validation'})
        self.assertFalse((output/'test.parquet').exists())
        with self.assertRaises(ValueError):prepare(self.study,self.ledger,held_out_interval=('2025-12-31','2026-01-02'))
