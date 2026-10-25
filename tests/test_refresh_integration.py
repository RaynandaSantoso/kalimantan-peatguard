"""Verify parallel source changes publish manifests understood by shared inventory."""
from datetime import date
import unittest
import json
import numpy as np

import test_inventory as fixtures
from peatguard.chirps import CHIRPSArchive
from peatguard.era5_land import refresh
from peatguard.inventory import build_inventory


class IntegrationTests(unittest.TestCase):
    setUp = fixtures.RefreshBoundaryTests.setUp
    daily = fixtures.RefreshBoundaryTests.daily
    raster = fixtures.RefreshBoundaryTests.raster
    study = fixtures.RefreshBoundaryTests.study
    snapshot = fixtures.RefreshBoundaryTests.snapshot

    def test_replaced_legacy_sources_remain_valid_in_shared_inventory(self):
        study = self.study()
        manifest_path = self.root / self.config['sources']['chirps']['directory'] / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        spec = self.config['sources']['chirps']
        manifest.update(unit='mm/day', shape_lat_lon=spec['shape'],
                        grid_transform=spec['grid_transform'], crs='EPSG:4326')
        manifest_path.write_text(json.dumps(manifest))
        before = self.snapshot()
        days = [date(2025,1,d) for d in range(1,5)]
        class Rain:
            def discover(self, start, end):
                return {'dates': list(map(str,days)), 'observed_at': study.as_of.isoformat(),
                        'image_ids': {str(d): f'{d:%Y%m%d}' for d in days}}
            def fetch(self, requested): return np.full((len(requested),2,2),2,np.float32)
        class Weather:
            def discover(self, start, end): return days
            def fetch(self, requested):
                values = np.empty((len(requested),2,2,2),np.float32)
                values[:,0] = 280
                values[:,1] = .4
                return values
        CHIRPSArchive(study).refresh(Rain())
        refresh(study, Weather())
        report = build_inventory(study)
        for key in ('chirps','era5_land'):
            source = report['sources'][key]
            self.assertEqual(source['validated_requested_days'],4)
            self.assertEqual(len(source['files']),1)
            self.assertEqual(source['files'][0]['status'],'validated')
            self.assertEqual(source['diagnostics'],[])
        after = self.snapshot()
        for name, fingerprint in before.items():
            if name.endswith('.npz'):
                self.assertEqual(after[name],fingerprint, 'Legacy source bytes/mtime changed')
