"""Annual/static integration fixtures: missing years, safe reuse, stable geometry."""
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
from rasterio.transform import from_origin
import rasterio

import test_inventory as fixtures
from peatguard.spatial import (discover_maps, dominant_classes, refresh_maps, select_maps,
                                static_distances, validated_maps, validated_static)
from peatguard.inventory import sha256


class SpatialTests(unittest.TestCase):
    # Reuse only fixture construction, not the existing test methods.
    setUp = fixtures.RefreshBoundaryTests.setUp
    daily = fixtures.RefreshBoundaryTests.daily
    raster = fixtures.RefreshBoundaryTests.raster
    study = fixtures.RefreshBoundaryTests.study
    snapshot = fixtures.RefreshBoundaryTests.snapshot
    def test_policies_preserve_real_year_and_do_not_fabricate_assets(self):
        study = self.study(as_of='2026-10-25')
        strict = select_maps(study, include_held_out=True)
        self.assertEqual([r['status'] for r in strict], ['blocked_missing_map']*2)
        fallback = select_maps(study, policy='carry_forward', include_held_out=True)
        self.assertEqual([r['map_year'] for r in fallback], [2024, 2024])
        self.assertEqual([r['staleness_years'] for r in fallback], [1, 2])
        self.assertTrue(all(r['historical_release_evidence'] == 'unknown' for r in fallback))
        self.assertEqual([r['data_year'] for r in select_maps(study, policy='carry_forward')], [2025])
        with self.assertRaises(ValueError): select_maps(study, policy='automatic')

    def available(self, years):
        return dict(collection=self.config['sources']['modis']['collection'], version='6.1_IGBP', years=years)

    def fetched(self, year):
        affine = from_origin(0, 100, 463.3127165279165, 463.3127165279165)
        return np.ones((3,2,2), np.uint8), affine, dict(crs='SR-ORG:6974', transform=list(affine)[:6])

    def test_discovery_to_refresh_reuse_and_eda_selection(self):
        study = self.study()
        report = refresh_maps(study, self.available([2024, 2025]), self.fetched)
        self.assertEqual(report['added_years'], [2025])
        manifest_path = self.root / self.config['sources']['modis']['directory'] / 'manifest.json'
        first = manifest_path.read_bytes()
        report = refresh_maps(study, self.available([2024, 2025]), lambda y: self.fail('Downloaded cached map'))
        self.assertEqual(first, manifest_path.read_bytes())
        self.assertEqual(sorted(validated_maps(study)), [2024, 2025])
        self.assertEqual(select_maps(study)[0]['map_year'], 2025)

    def test_empty_cache_can_acquire_and_failed_commit_is_recoverable(self):
        import shutil
        from peatguard.spatial import atomic_json
        study = self.study()
        directory = self.root / self.config['sources']['modis']['directory']
        shutil.rmtree(directory)
        self.assertEqual(validated_maps(study), {})
        refresh_maps(study, self.available([2025]), self.fetched)
        self.assertEqual(sorted(validated_maps(study)), [2025])
        # A failed commit may leave immutable source bytes, but must never
        # expose an unmanifested top-level raster or break the previous map.
        study = self.study(as_of='2026-10-25')
        old_manifest = (directory/'manifest.json').read_bytes()
        with patch('peatguard.spatial.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                refresh_maps(study, self.available([2025,2026]), self.fetched)
        self.assertEqual((directory/'manifest.json').read_bytes(), old_manifest)
        self.assertEqual(sorted(validated_maps(study)), [2025])
        report = refresh_maps(study, self.available([2025,2026]), self.fetched)
        self.assertEqual(report['added_years'], [2026])
        self.assertEqual(sorted(validated_maps(study)), [2025,2026])

    def test_static_manifest_failure_does_not_block_a_retry(self):
        import importlib.util
        import shutil
        from rasterio.crs import CRS
        directory = self.root / self.config['sources']['ghsl']['directory']
        shutil.rmtree(directory)
        module_path = Path(__file__).resolve().parents[1] / 'scripts/download_ghsl_built_surface.py'
        spec = importlib.util.spec_from_file_location('ghsl_refresh_fixture', module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        crs = CRS.from_proj4('+proj=moll')
        affine = from_origin(0,100,100,100)
        projection = dict(wkt=crs.to_wkt(), transform=list(affine)[:6])
        image = SimpleNamespace(select=lambda band: image, projection=lambda: SimpleNamespace(getInfo=lambda: projection),
                                unmask=lambda *args: image)
        ee = SimpleNamespace(Initialize=lambda **kw:None, Image=lambda name:image)
        argv = ['download_ghsl_built_surface.py','--root',str(self.root),'--project','fixture']
        with patch.object(module,'ee',ee), patch.object(module,'grid_for_bounds',return_value=(0,0,2,2,affine,crs)), \
             patch.object(module,'fetch_tile',return_value=np.ones((2,2),np.uint16)), patch.object(sys,'argv',argv):
            with patch('peatguard.spatial.atomic_json',side_effect=OSError('disk full')):
                with self.assertRaises(OSError): module.main()
            self.assertFalse((directory/'manifest.json').exists())
            self.assertEqual(list(directory.glob(self.config['sources']['ghsl']['pattern'])),[])
            module.main()
        path = validated_static(self.study())
        self.assertIn('.partitions',path.parts)
        self.assertTrue(path.exists())

    def test_explicit_carry_forward_can_acquire_prior_published_map(self):
        import shutil
        directory = self.root / self.config['sources']['modis']['directory']
        shutil.rmtree(directory)
        report = refresh_maps(self.study(), self.available([2024]), self.fetched, policy='carry_forward')
        self.assertEqual(report['added_years'], [2024])
        self.assertEqual(select_maps(self.study(), policy='carry_forward')[0]['map_year'],2024)

    def test_micrometre_serialization_variation_is_not_a_new_modis_grid(self):
        def tiny_change(year):
            values,affine,projection=self.fetched(year)
            from rasterio.transform import Affine
            adjusted=Affine(affine.a-4.2e-10,0,affine.c-0.000018,0,affine.e,affine.f+0.000001)
            projection['transform']=list(adjusted)[:6]
            return values,adjusted,projection
        result=refresh_maps(self.study(),self.available([2024,2025]),tiny_change)
        self.assertEqual(result['added_years'],[2025])
        self.assertEqual(sorted(validated_maps(self.study())),[2024,2025])

    def test_bad_download_keeps_last_good_maps_and_manifest(self):
        study = self.study()
        before = self.snapshot()
        def bad(year):
            values, affine, projection = self.fetched(year)
            values[0,0,0] = 0
            return values, affine, projection
        with self.assertRaises(ValueError): refresh_maps(study, self.available([2025]), bad)
        self.assertEqual(before, self.snapshot())
        def shifted(year):
            values, _, projection = self.fetched(year)
            return values, from_origin(1,100,463.3127165279165,463.3127165279165), projection
        with self.assertRaises(ValueError): refresh_maps(study, self.available([2025]), shifted)
        self.assertEqual(before, self.snapshot())

    def test_missing_map_not_requested_and_static_not_rewritten(self):
        study = self.study(as_of='2026-10-25')
        before = self.snapshot()
        report = refresh_maps(study, self.available([2024]), lambda y: self.fail('Unpublished map requested'))
        self.assertEqual(report['unavailable_years'], [2025,2026])
        self.assertTrue(validated_static(study).exists())
        self.assertEqual(before, self.snapshot())

    def test_invalid_raster_rejected_before_selection(self):
        study = self.study()
        path = validated_maps(study)[2024]
        with rasterio.open(path, 'r+') as source:
            source.write(np.zeros((2,2), np.uint8), 1)
        with self.assertRaises(ValueError): select_maps(study, policy='carry_forward')

    def test_stable_cell_mode_and_2020_distances(self):
        study = self.study()
        # Small cells in the operational study CRS fit inside fixture native raster.
        self.config['grid']['cell_size_m'] = 100
        self.config['grid']['crs_proj'] = '+proj=sinu +R=6371007.181 +no_defs'
        (self.root/'config/study.json').write_text(json.dumps(self.config))
        study = self.study()
        cells = pd.DataFrame({'cell_id':['kp5_c0_r0'], 'grid_col':[0], 'grid_row':[0],
                              'x_center_m':[50.], 'y_center_m':[50.]})
        result = dominant_classes(study, cells)
        self.assertEqual(result.cell_id.tolist(), ['kp5_c0_r0'])
        self.assertEqual(result['2024'].tolist(), [1])
        # Distance computation retains identity/epoch even with differing native CRS.
        distances = static_distances(study, cells)
        self.assertEqual(distances.ghsl_reference_epoch.tolist(), [2020])
        self.assertEqual(distances.cell_id.tolist(), cells.cell_id.tolist())
        self.assertTrue(np.isfinite(distances.distance_to_built_m).all())
        cells['cell_id'] = 'unstable'
        with self.assertRaises(ValueError): dominant_classes(study, cells)

    def test_mode_uses_original_full_province_lattice_for_subset_cells(self):
        directory = self.root / 'data/interim/study_grid_5km'
        directory.mkdir(parents=True)
        (directory/'manifest.json').write_text(json.dumps({'bounds_indices_stop_exclusive': {
            'col_start': -2, 'col_stop': 4, 'row_start': -3, 'row_stop': 3}}))
        cells = pd.DataFrame({'cell_id': ['kp5_c0_r0'], 'grid_col': [0], 'grid_row': [0]})
        observed = []
        def warp(**kwargs):
            observed.append((kwargs['destination'].shape, kwargs['dst_transform']))
            kwargs['destination'][:] = 1
        with patch('peatguard.spatial.reproject', side_effect=warp):
            output = dominant_classes(self.study(), cells)
        self.assertEqual(output['2024'].tolist(), [1])
        self.assertEqual(observed[0][0], (6, 6))
        self.assertEqual(observed[0][1].c, -10000)
        self.assertEqual(observed[0][1].f, 15000)

    def test_metadata_discovery_records_current_evidence_only(self):
        study = self.study(as_of='2026-10-25')
        class Collection:
            def filterDate(self, start, end):
                self.interval = start, end
                return self
            def aggregate_array(self, name):
                return SimpleNamespace(getInfo=lambda:[1704067200000,1735689600000])
        collection = Collection()
        ee = SimpleNamespace(Initialize=lambda **kw:None, ImageCollection=lambda name: collection)
        with patch.dict(sys.modules, {'ee':ee}):
            report = discover_maps(study, 'test-project')
        self.assertEqual(report['years'], [2024,2025])
        self.assertEqual(collection.interval, ('2020-01-01','2027-01-01'))
        self.assertEqual(report['historical_release_evidence'], 'unknown')
