"""Complete fixture refresh boundary; no provider credentials or network."""
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from peatguard.chirps import CHIRPSArchive, EarthEngineCHIRPS, load_development, rainfall_summary
from peatguard.inventory import sha256
from peatguard.study import day_range, load_study


class Provider:
    def __init__(self, days, value=2, fail_after=None):
        self.days = [date.fromisoformat(day) if isinstance(day, str) else day for day in days]
        self.value, self.fail_after = value, fail_after
        self.calls, self.discoveries = [], []

    def discover(self, start, end):
        self.discoveries.append((start, end))
        days = [day for day in self.days if start <= day <= end]
        return {"dates": [str(day) for day in days],
                "image_ids": {str(day): f"{day:%Y%m%d}" for day in days},
                "observed_at": "2026-10-25T00:00:00+00:00"}

    def fetch(self, days):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("fixture interrupted")
        self.calls.append(days)
        return np.full((len(days), 2, 2), self.value, dtype=np.float32)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / 'config').mkdir()
        (self.root / 'peatguard').mkdir()
        self.config = json.loads((Path(__file__).resolve().parents[1] / 'config/study.json').read_text())
        # Synthetic three-period fixture exercises generic validation/held-out behavior.
        self.config['profiles']['expanded'] = {'start': '2020-01-01', 'end': '2026-12-31', 'splits': {'train': ['2020-01-01', '2024-12-31'], 'validation': ['2025-01-01', '2025-12-31'], 'test': ['2026-01-01', '2026-12-31']}, 'held_out': ['test'], 'final_test_interval_frozen': False}
        self.config['sources']['chirps']['shape'] = [2, 2]
        self.write_config()
        self.study = self.study_for('2025-01-01', '2025-01-10')
        self.archive = CHIRPSArchive(self.study)

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        (self.root / 'config/study.json').write_text(json.dumps(self.config))

    def study_for(self, start, end):
        return load_study(root=self.root, start=start, end=end, as_of='2026-10-25')

    def manifest(self):
        return json.loads(self.archive.path.read_text())

    def snapshot(self):
        manifest = self.manifest()
        return self.archive.path.read_bytes(), {
            entry['file']: sha256(self.root / entry['file']) for entry in manifest['years']}

    def test_bounded_partial_source_to_eda_and_duplicate(self):
        provider = Provider(day_range(date(2025, 1, 1), date(2025, 1, 5)))
        with patch('socket.socket', side_effect=AssertionError('network forbidden')):
            report = self.archive.refresh(provider)
            loaded = load_development(self.study)
            summary = rainfall_summary(loaded, np.ones((2, 2), bool))
        self.assertEqual(provider.discoveries, [(self.study.start, self.study.end)])
        self.assertEqual([str(day) for chunk in provider.calls for day in chunk],
                         [f'2025-01-0{day}' for day in range(1, 6)])
        self.assertEqual(report['coverage']['unpublished_tail'],
                         [{'start': '2025-01-06', 'end': '2025-01-10', 'days': 5}])
        self.assertFalse(self.manifest()['years'][0]['complete_calendar_year'])
        self.assertEqual(len(self.manifest()['years'][0]['date_provenance']), 5)
        self.assertEqual(summary['monthly'].iloc[0].valid_days, 5)
        self.assertEqual(summary['monthly'].iloc[0].observed_total_mm, 10)
        self.assertFalse(summary['monthly'].iloc[0].complete_month)
        self.assertTrue(summary['climatology_complete_months'].empty)
        self.assertEqual(summary['complete_annual_totals'], {})
        before = self.snapshot()
        provider.calls.clear()
        self.assertEqual(self.archive.refresh(provider)['changed'], [])
        self.assertEqual(provider.calls, [])
        self.assertEqual(self.snapshot(), before)

    def test_interruption_resume_extension_and_retained_years(self):
        provider = Provider(day_range(self.study.start, self.study.end), fail_after=1)
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            self.archive.refresh(provider, chunk_days=3)
        self.assertFalse(self.archive.path.exists())
        provider.fail_after = None
        self.archive.refresh(provider, chunk_days=3)
        self.assertEqual(sum(len(days) for days in provider.calls), 10)
        self.assertEqual(len(self.manifest()['years'][0]['dates']), 10)
        # A request that crosses the year boundary preserves the older inventory.
        next_study = self.study_for('2025-12-30', '2026-01-02')
        next_provider = Provider(day_range(next_study.start, next_study.end))
        CHIRPSArchive(next_study).refresh(next_provider)
        self.assertEqual([entry['year'] for entry in self.manifest()['years']], [2025, 2026])
        self.assertEqual(self.manifest()['years'][0]['days'], 12)
        self.assertEqual(len(self.manifest()['years'][0]['dates']), 12)
        self.assertEqual(self.manifest()['years'][1]['dates'], ['2026-01-01', '2026-01-02'])

    def test_failed_extension_and_failed_promotion_keep_previous_archive(self):
        self.archive.refresh(Provider(['2025-01-01']))
        before = self.snapshot()
        broken = Provider(['2025-01-01', '2025-01-02'], fail_after=0)
        with self.assertRaises(RuntimeError):
            self.archive.refresh(broken)
        self.assertEqual(before, self.snapshot())
        with patch('peatguard.chirps._atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.archive.refresh(Provider(['2025-01-01', '2025-01-02']))
        self.assertEqual(before, self.snapshot())
        # Failure specifically at inventory promotion, after all chunk commits.
        import peatguard.chirps as module
        original = module._atomic_json
        def promotion_failure(path, value):
            if path == self.archive.path:
                raise OSError('promotion interrupted')
            return original(path, value)
        with patch('peatguard.chirps._atomic_json', side_effect=promotion_failure):
            with self.assertRaises(OSError):
                self.archive.refresh(Provider(['2025-01-01', '2025-01-02']))
        self.assertEqual(before, self.snapshot())
        self.archive.refresh(Provider(['2025-01-01', '2025-01-02']))
        self.assertEqual(self.manifest()['years'][0]['days'], 2)

    def test_revision_reports_changed_hashes_invalidation_and_rechecks(self):
        self.archive.refresh(Provider(['2025-01-01', '2025-01-02']))
        before = self.manifest()['years'][0]
        report = self.archive.refresh(Provider(['2025-01-01', '2025-01-02'], value=9),
                                      refresh_start='2025-01-02', refresh_end='2025-01-02')
        after = self.manifest()['years'][0]
        self.assertNotEqual(before['sha256'], after['sha256'])
        self.assertEqual(report['invalidations'][0]['upstream_sha256'], before['sha256'])
        self.assertEqual(self.manifest()['downstream_invalidations'], report['invalidations'])
        self.assertIn(before['file'], self.manifest()['retired_files'])
        with np.load(self.root / after['file']) as arrays:
            np.testing.assert_array_equal(arrays['precipitation_mm_day'][:, 0, 0], [2, 9])
        again = Provider(['2025-01-01', '2025-01-02'], value=11)
        self.archive.refresh(again, refresh_start='2025-01-02', refresh_end='2025-01-02')
        self.assertEqual(len(again.calls), 1)  # Successful revision cache must not hide later revisions.

    def test_corrupt_committed_data_and_grid_are_rejected_before_fetch(self):
        provider = Provider(['2025-01-01'])
        self.archive.refresh(provider)
        entry = self.manifest()['years'][0]
        (self.root / entry['file']).write_bytes(b'broken')
        provider.calls.clear()
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.archive.refresh(provider)
        self.assertEqual(provider.calls, [])
        self.config['sources']['chirps']['grid_transform'][2] += 1
        self.write_config()
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            CHIRPSArchive(self.study_for('2025-01-01', '2025-01-10')).validated()

    def test_corrupt_checkpoint_is_not_reused(self):
        provider = Provider(day_range(self.study.start, self.study.end), fail_after=1)
        with self.assertRaises(RuntimeError):
            self.archive.refresh(provider, chunk_days=3)
        ref = next((self.archive.directory / '.checkpoints').glob('*.json'))
        cached = json.loads(ref.read_text())
        (self.root / cached['file']).write_bytes(b'broken')
        provider.fail_after = None
        with self.assertRaisesRegex(ValueError, 'checkpoint'):
            self.archive.refresh(provider, chunk_days=3)
        self.assertFalse(self.archive.path.exists())

    def test_interior_gap_separate_from_tail_and_empty_discovery(self):
        report = self.archive.refresh(Provider(['2025-01-01', '2025-01-03']))['coverage']
        self.assertEqual(report['interior_source_gaps'], [{'start': '2025-01-02', 'end': '2025-01-02', 'days': 1}])
        self.assertEqual(report['unpublished_tail'][0]['start'], '2025-01-04')
        empty = self.archive.refresh(Provider([]))['coverage']
        self.assertEqual(empty['unpublished_tail'], [])
        self.assertEqual(empty['unpublished_or_outside_coverage'][0]['days'], 10)

    def test_bad_payload_and_out_of_bounds_discovery_do_not_promote(self):
        provider = Provider(['2025-01-01'], value=-1)
        with self.assertRaisesRegex(ValueError, 'Invalid CHIRPS'):
            self.archive.refresh(provider)
        self.assertFalse(self.archive.path.exists())
        provider.discover = lambda *_: {'dates': ['2026-01-01']}
        with self.assertRaisesRegex(ValueError, 'out-of-request'):
            self.archive.refresh(provider)
        with self.assertRaisesRegex(ValueError, 'bounded'):
            self.archive.refresh(provider, refresh_start='2024-01-01', refresh_end='2025-01-02')

    def test_legacy_import_and_per_date_provenance_preserved(self):
        directory = self.archive.directory
        directory.mkdir(parents=True)
        path, digest = self.archive._save_values(directory, [date(2025, 1, 1)], np.ones((1, 2, 2)))
        definition = self.archive.definition
        legacy = {'earth_engine_collection': definition['collection'], 'unit': 'mm/day',
                  'shape_lat_lon': definition['shape'], 'grid_transform': definition['grid_transform'],
                  'crs': 'EPSG:4326', 'years': [{'year': 2025, 'days': 1,
                      'file': path.relative_to(self.root).as_posix(), 'sha256': digest}]}
        self.archive.path.write_text(json.dumps(legacy))
        self.archive.refresh(Provider(['2025-01-01', '2025-01-02']))
        after = self.manifest()['years'][0]
        self.assertIsNone(after['date_provenance']['2025-01-01']['retrieved_at'])
        self.assertIn('legacy_manifest_sha256', after['date_provenance']['2025-01-01'])
        self.assertTrue(path.exists())
        self.assertIn(legacy['years'][0]['file'], self.manifest()['retired_files'])

    def test_held_out_default_exclusion_and_leap_year_totals(self):
        leap = self.study_for('2024-01-01', '2026-01-02')
        days = day_range(date(2024, 1, 1), date(2024, 12, 31)) + [date(2026, 1, 1)]
        CHIRPSArchive(leap).refresh(Provider(days))
        loaded = load_development(leap)
        self.assertEqual(list(loaded['annual']), [2024])
        self.assertFalse(loaded['held_out_opened'])
        summary = rainfall_summary(loaded, np.ones((2, 2), bool))
        self.assertTrue(summary['quality'].loc[2024].complete_calendar_year)
        self.assertEqual(summary['monthly'].loc['2024-02-01'].valid_days, 29)
        np.testing.assert_array_equal(summary['complete_annual_totals'][2024], np.full((2, 2), 732))
        self.assertIn(2026, load_development(leap, include_held_out=True)['annual'])
        legacy = load_study(root=self.root, profile='legacy', as_of='2026-10-25')
        with self.assertRaisesRegex(ValueError, 'No validated development'):
            load_development(legacy)

    def test_missing_pixel_days_not_zero_and_complete_month_climatology(self):
        days = day_range(date(2025, 1, 1), date(2025, 2, 3))
        provider = Provider(days)
        self.archive = CHIRPSArchive(self.study_for('2025-01-01', '2025-02-03'))
        original_fetch = provider.fetch
        def fetch(days):
            values = original_fetch(days)
            values[:, 0, 0] = np.nan
            return values
        provider.fetch = fetch
        self.archive.refresh(provider)
        summary = rainfall_summary(load_development(self.archive.study), np.ones((2, 2), bool))
        self.assertEqual(summary['daily'].iloc[0].valid_pixels, 3)
        self.assertEqual(summary['daily'].iloc[0].wet_pixel_pct, 100)
        self.assertEqual(summary['monthly'].iloc[0].valid_days, 31)
        self.assertTrue(summary['monthly'].iloc[0].complete_month)
        self.assertFalse(summary['monthly'].iloc[1].complete_month)
        self.assertEqual(list(summary['climatology_complete_months'].index), [1])

    def test_earth_engine_discovery_inclusive_end_metadata_only(self):
        import sys
        from types import SimpleNamespace
        calls = []
        class Collection:
            def filterDate(self, start, end):
                calls.append((start, end)); return self
            def sort(self, field): return self
            def aggregate_array(self, field):
                values = [1735689600000] if field == 'system:time_start' else ['20250101']
                return SimpleNamespace(getInfo=lambda: values)
        fake = SimpleNamespace(Initialize=lambda **_: None, ImageCollection=lambda _: Collection())
        with patch.dict(sys.modules, {'ee': fake}):
            provider = EarthEngineCHIRPS(self.archive.definition, 'fixture')
            result = provider.discover(self.study.start, self.study.end)
        self.assertEqual(calls, [('2025-01-01', '2025-01-11')])
        self.assertEqual(result['image_ids'], {'2025-01-01': '20250101'})

    def test_provider_fetch_exact_bands_grid_sentinel_and_units(self):
        import io
        import sys
        from types import SimpleNamespace
        stamps, queried = [], []
        days = [date(2025, 1, 1), date(2025, 1, 3)]
        names = ["20250101_precipitation", "20250103_precipitation"]
        payload = np.zeros((2, 2), dtype=[(name, "f4") for name in names])
        payload[names[0]] = [[-9999, 2], [3, 4]]
        payload[names[1]] = 5
        stream = io.BytesIO()
        np.save(stream, payload)
        class Collection:
            def filter(self, value): queried.append(value); return self
            def sort(self, field): return self
            def select(self, band):
                self.selected = band; return self
            def toBands(self): return self
            def bandNames(self): return SimpleNamespace(getInfo=lambda: names)
            def getDownloadURL(self, options):
                self.options = options; return "https://fixture.invalid/signed-secret"
        collection = Collection()
        fake = SimpleNamespace(Initialize=lambda **_: None,
            ImageCollection=lambda _: collection,
            Date=lambda value: SimpleNamespace(millis=lambda: value),
            Filter=SimpleNamespace(inList=lambda field, days: (field, days)),
            Geometry=SimpleNamespace(Rectangle=lambda *args, **kwargs: "fixture-region"))
        response = SimpleNamespace(content=stream.getvalue(), raise_for_status=lambda: None)
        with patch.dict(sys.modules, {"ee": fake}), patch("requests.get", return_value=response) as request:
            provider = EarthEngineCHIRPS(self.archive.definition, "fixture")
            values = provider.fetch(days)
        self.assertEqual(queried, [("system:time_start", ["2025-01-01", "2025-01-03"])])
        self.assertEqual(collection.selected, "precipitation")
        self.assertEqual(collection.options["crs_transform"], self.archive.definition["grid_transform"])
        self.assertEqual(values.shape, (2, 2, 2))
        self.assertTrue(np.isnan(values[0, 0, 0]))
        self.assertEqual(values[1, 0, 0], 5)  # mm/day is not scaled or temporally aggregated.
        self.assertEqual(request.call_count, 1)

    def test_initialization_error_does_not_expose_secret(self):
        import sys
        from types import SimpleNamespace
        def fail(**_): raise RuntimeError('secret fixture token')
        with patch.dict(sys.modules, {'ee': SimpleNamespace(Initialize=fail)}):
            with self.assertRaises(RuntimeError) as caught:
                EarthEngineCHIRPS(self.archive.definition, 'fixture')
        self.assertNotIn('secret', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
