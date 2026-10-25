"""Bounded comparison fixtures; no provider credentials or held-out outcomes."""
from datetime import date
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

import test_inventory as fixtures
from peatguard.study import load_study

path=Path(__file__).resolve().parents[1]/'scripts/assess_chirps_migration.py'
spec=importlib.util.spec_from_file_location('chirps_migration',path)
migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)


class MigrationTests(unittest.TestCase):
    setUp=fixtures.RefreshBoundaryTests.setUp
    daily=fixtures.RefreshBoundaryTests.daily
    raster=fixtures.RefreshBoundaryTests.raster
    study=fixtures.RefreshBoundaryTests.study
    snapshot=fixtures.RefreshBoundaryTests.snapshot

    def ready(self):
        self.daily('chirps',['2025-01-01','2025-01-02','2025-01-03'])
        definition=self.config['sources']['chirps'];directory=self.root/definition['directory']
        manifest=json.loads((directory/'manifest.json').read_text())
        manifest.update(unit='mm/day',crs='EPSG:4326',shape_lat_lon=[2,2],grid_transform=definition['grid_transform'])
        (directory/'manifest.json').write_text(json.dumps(manifest))
        boundary=self.root/self.config['boundaries']['provinces']
        boundary.write_text(json.dumps({'type':'FeatureCollection','features':[{'type':'Feature','properties':{},
            'geometry':{'type':'Polygon','coordinates':[[[108.5,4.5],[108.6,4.5],[108.6,4.4],[108.5,4.4],[108.5,4.5]]]}}]}))
        self.calls=[]
        owner=self
        class Provider:
            def __init__(self,*args):self.projection={'crs':'EPSG:4326','transform':[.05,0,-180,0,-.05,60]}
            def discover(self,start,end):
                days=['2025-01-01','2025-01-02','2025-01-03']
                return {'dates':days,'image_ids':{d:d.replace('-','') for d in days},'observed_at':'2026-10-25T00:00:00Z'}
            def fetch(self,days):owner.calls.append(days);return np.full((len(days),2,2),1.5,np.float32)
        self.provider=Provider
        self.selected=load_study(root=self.root,start='2025-01-01',end='2025-01-03',as_of='2026-10-25')

    def test_real_files_to_comparison_preserve_sources_and_resume_cached_data(self):
        self.ready();before=self.snapshot()
        with patch('socket.socket',side_effect=AssertionError('Network forbidden')):
            output=migration.assess(self.selected,factory=self.provider)
            again=migration.assess(self.selected,factory=self.provider)
        self.assertEqual(output,again);self.assertEqual(len(self.calls),2)
        result=json.loads((output/'comparison.json').read_text())
        self.assertFalse(result['active_source_changed']);self.assertFalse(result['held_out_outcomes_accessed'])
        for variant in ['rnl','sat']:
            stats=result['variants'][variant]['metrics']
            self.assertEqual(stats['matched_pixel_days'],12)
            self.assertEqual(stats['mean_difference_mm_day'],1.)
            self.assertEqual(stats['rmse_mm_day'],1.)
        after=self.snapshot()
        self.assertTrue(all(after[name]==fingerprint for name,fingerprint in before.items()))
        self.assertTrue((output/'daily_comparison.png').exists())

    def test_missingness_uses_matched_pairs_and_reports_exclusions(self):
        a=np.array([[[0.,2.],[np.nan,4.]]]);b=np.array([[[1.,np.nan],[3.,6.]]])
        result=migration.paired_metrics(a,b,np.ones((2,2),bool))
        self.assertEqual(result['matched_pixel_days'],2)
        self.assertEqual(result['v2_only_pixel_days'],1);self.assertEqual(result['v3_only_pixel_days'],1)
        self.assertEqual(result['mean_difference_mm_day'],1.5)
        self.assertEqual(result['v2']['mean_mm_day'],2.)
        self.assertEqual(result['v3']['mean_mm_day'],3.5)

    def test_heldout_and_large_interval_fail_before_provider(self):
        self.ready()
        held=load_study(root=self.root,start='2026-01-01',end='2026-01-02',as_of='2026-10-25')
        with self.assertRaisesRegex(ValueError,'held-out'):migration.assess(held,factory=lambda *a:self.fail('Provider called'))
        long=load_study(root=self.root,start='2025-01-01',end='2025-05-01',as_of='2026-10-25')
        with self.assertRaisesRegex(ValueError,'92'):migration.assess(long,factory=lambda *a:self.fail('Provider called'))

    def test_bad_native_grid_or_corrupt_cache_cannot_be_reused(self):
        self.ready()
        with self.assertRaisesRegex(ValueError,'resolution'):
            migration.native_alignment({'crs':'EPSG:4326','transform':[.1,0,-180,0,-.1,60]},self.config['sources']['chirps'])
        with self.assertRaisesRegex(ValueError,'shifted'):
            migration.native_alignment({'crs':'EPSG:4326','transform':[.05,0,-179.99,0,-.05,60]},self.config['sources']['chirps'])
        migration.assess(self.selected,factory=self.provider,variants=['rnl'])
        cache=next((self.root/'data/interim/assessments').rglob('rainfall.npz'))
        cache.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'checksum'):migration.assess(self.selected,factory=self.provider,variants=['rnl'])
