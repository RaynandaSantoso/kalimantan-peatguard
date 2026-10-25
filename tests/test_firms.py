"""Source-to-label and provider boundary tests; no credentials or network."""
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('MPLBACKEND', 'Agg')
import pandas as pd
from pyproj import Transformer
from shapely.geometry import box, mapping
from peatguard.study import load_study
from peatguard.firms import (PRODUCT, REQUIRED, build, fetch, ingest, labels,
                             load_spatial, read_ledger, sha256)


class FirmsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / 'config').mkdir()
        (self.root / 'peatguard').mkdir()
        origin = Path(__file__).resolve().parents[1]
        config = json.loads((origin / 'config/study.json').read_text())
        # Synthetic source fixture retains a separate validation and held-out period.
        config['profiles']['expanded'] = {'start': '2020-01-01', 'end': '2026-12-31', 'splits': {'train': ['2020-01-01', '2024-12-31'], 'validation': ['2025-01-01', '2025-12-31'], 'test': ['2026-01-01', '2026-12-31']}, 'held_out': ['test'], 'final_test_interval_frozen': False}
        (self.root / 'config/study.json').write_text(json.dumps(config))
        self.study = load_study(root=self.root, start='2025-01-01', end='2025-01-06', as_of='2026-10-25')
        self.fields = sorted(REQUIRED | {'type', 'frp'})
        transformer = Transformer.from_crs('EPSG:4326', self.study.config['grid']['crs_proj'], always_xy=True)
        x, y = transformer.transform(114.01, .01)
        col, row = int(x // 5000), int(y // 5000)
        self.grid_path = self.root / 'grid.csv'
        pd.DataFrame([dict(cell_id=f'kp5_c{col}_r{row}', grid_col=col, grid_row=row,
                           intersects_big_peatland=True)]).to_csv(self.grid_path, index=False)
        polygon = dict(type='FeatureCollection', features=[dict(type='Feature', properties={}, geometry=mapping(box(114.001,.001,114.02,.02)))])
        for value in self.study.config['boundaries'].values():
            path = self.root / value
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(polygon))

    def row(self, **changes):
        row = dict(latitude='.01', longitude='114.01', acq_date='2025-01-01', acq_time='007',
                   satellite='Terra', instrument='MODIS', confidence='81', version='6.03', type='2', frp='4')
        return dict(row, **changes)

    def interval(self, name, rows, **changes):
        path = self.root / name
        with path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=self.fields)
            writer.writeheader()
            writer.writerows(rows)
        return dict(start='2025-01-01', end='2025-01-02', bbox=[113,-1,115,1],
            product=PRODUCT, stage='SP', status='complete', path=str(path), sha256=sha256(path),
            retrieved_at='2026-10-25T00:00:00+00:00', provenance=name, **changes)

    def ledger(self, intervals):
        path = self.root / 'ledger.json'
        path.write_text(json.dumps(dict(schema_version=1, intervals=intervals)))
        return path

    def run_labels(self, intervals):
        detections, validated, report = ingest(read_ledger(self.ledger(intervals), self.study))
        spatial = load_spatial(self.study, self.grid_path)
        daily, assigned = labels(self.study, detections, validated, *spatial)
        return daily, assigned, report

    def test_complete_pipeline_zeros_unknown_confidence_type_and_no_writes(self):
        intervals = [self.interval('a.csv', [self.row(), self.row(confidence='80',acq_time='1200')])]
        ledger = self.ledger(intervals)
        hashes = {p: sha256(p) for p in self.root.rglob('*') if p.is_file()}
        with patch('socket.socket', side_effect=AssertionError('No network')):
            output = build(self.study, ledger, grid_path=self.grid_path)
        daily = pd.read_csv(output / 'daily_labels.csv')
        self.assertEqual(daily.loc[0,'fire_detected'], 1)
        self.assertEqual(daily.loc[1,'fire_detected'], 0)
        self.assertTrue(daily.loc[2:,'fire_detected'].isna().all())
        self.assertEqual(pd.read_csv(output / 'assigned_detections.csv').type.iloc[0], 2)
        self.assertGreater((output / 'development_eda.png').stat().st_size, 1000)
        self.assertTrue(all(sha256(p) == value for p, value in hashes.items()))
        with self.assertRaises(FileExistsError):
            build(self.study, ledger, grid_path=self.grid_path)

    def test_overlapping_exports_dedupe_and_sensor_overpass_survive(self):
        a = self.interval('a.csv', [self.row(), self.row(satellite='Aqua',version='61.03'), self.row(acq_time='1200')])
        b = self.interval('b.csv', [self.row(frp='5')])
        daily, assigned, report = self.run_labels([a,b])
        self.assertEqual(len(assigned), 3)
        self.assertEqual(report['duplicate_rows_removed'], 1)
        self.assertEqual(report['changed_record_groups'], 1)
        self.assertEqual(assigned.acq_time.iloc[0], '0007')
        self.assertTrue(assigned.acquired_at_utc.str.endswith('+00:00').all())
        self.assertEqual(daily.detection_count.iloc[0], 3)

    def test_sp_complete_replaces_nrt_even_removed_or_moved_record(self):
        nrt = self.interval('nrt.csv', [self.row(version='6.1NRT')])
        nrt['stage'] = 'NRT'
        sp = self.interval('sp.csv', [])
        daily, assigned, report = self.run_labels([nrt,sp])
        self.assertTrue(assigned.empty)
        self.assertEqual(report['nrt_rows_replaced_by_complete_sp'], 1)
        self.assertEqual(daily.fire_detected.iloc[0], 0)

    def test_failed_unknown_and_malformed_intervals_never_negative(self):
        failed = self.interval('failed.csv', [], )
        failed['status'] = 'failed'
        unknown = self.interval('unknown.csv', [self.row(acq_time='2460')])
        daily, _, report = self.run_labels([failed,unknown])
        self.assertTrue(daily.fire_detected.isna().all())
        self.assertEqual(report['rejected_rows'], 1)
        unknown['status'] = 'unknown'
        daily, _, _ = self.run_labels([unknown])
        self.assertTrue(daily.fire_detected.isna().all())

    def test_partial_bbox_cannot_authorize_negatives(self):
        source = self.interval('small.csv', [])
        source['bbox'] = [114.005,.005,114.015,.015]
        daily, _, _ = self.run_labels([source])
        self.assertTrue(daily.fire_detected.isna().all())

    def test_public_modis_schema_needs_explicit_sensor_provenance(self):
        path=self.root/'public-modis.csv'
        fields=[f for f in self.fields if f not in {'instrument','type'}]
        row=self.row(version='6.1NRT')
        with path.open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
            writer.writerow({k:v for k,v in row.items() if k in fields})
        item=self.interval('other.csv',[])
        item.update(path=str(path),sha256=sha256(path),stage='NRT')
        with self.assertRaisesRegex(ValueError,'missing required'):ingest([item])
        item['declared_instrument']='MODIS'
        data,_,_=ingest([item])
        self.assertEqual(data.instrument.tolist(),['MODIS'])
        self.assertTrue(data.type.isna().all())
        self.assertNotIn('instrument',json.loads(data.original_record.iloc[0]))

    def test_notebook_setup_refreshes_cached_importer(self):
        import contextlib
        import io
        import peatguard.firms as firms

        path = self.root / 'public-nrt.csv'
        fields = [f for f in self.fields if f != 'instrument']
        row = self.row(version='6.1NRT')
        with path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerow({k: v for k, v in row.items() if k in fields})
        item = self.interval('placeholder.csv', [])
        item.update(path=str(path), sha256=sha256(path), stage='NRT', declared_instrument='MODIS')
        current_normalize = firms.normalize

        def cached_legacy_normalize(interval):
            with Path(interval['path']).open() as stream:
                if not REQUIRED <= set(csv.DictReader(stream).fieldnames):
                    raise ValueError('FIRMS CSV is missing required MODIS columns.')
            return current_normalize(interval)

        notebook = json.loads((Path(__file__).resolve().parents[1] / 'EDA/02_FIRMS_EDA.ipynb').read_text())
        setup = next(c for c in notebook['cells'] if c['cell_type'] == 'code')
        with patch.object(firms, 'normalize', cached_legacy_normalize):
            with self.assertRaisesRegex(ValueError, 'missing required MODIS columns'):
                firms.ingest([item])
            scope = {}
            with contextlib.redirect_stdout(io.StringIO()):
                exec(''.join(setup['source']), scope)
            detections, _, report = scope['ingest']([item])
        self.assertEqual(report['rejected_rows'], 0)
        self.assertEqual(detections.instrument.tolist(), ['MODIS'])

    def test_bad_cutoff_checksum_and_outside_interval(self):
        source = self.interval('a.csv', [self.row(acq_date='2025-01-03')])
        _, _, report = self.run_labels([source])
        self.assertEqual(report['rejected_rows'], 1)
        source['end'] = '2027-01-01'
        with self.assertRaises(ValueError):
            read_ledger(self.ledger([source]), self.study)
        source['end'] = '2025-01-02'
        source['sha256'] = 'broken'
        with self.assertRaises(ValueError):
            read_ledger(self.ledger([source]), self.study)

    def test_nrt_and_utc_wita_boundary_preserved(self):
        source = self.interval('nrt.csv', [self.row(acq_time='1559',version='6.1NRT'), self.row(acq_time='1600',version='6.1NRT')])
        source['stage'] = 'NRT'
        daily, assigned, _ = self.run_labels([source])
        self.assertEqual(assigned.acq_date.nunique(),1)
        wita = pd.to_datetime(assigned.acquired_at_utc, utc=True).dt.tz_convert('Asia/Makassar')
        self.assertEqual(wita.dt.day.tolist(),[1,2])
        self.assertEqual(daily.retrieval_coverage.iloc[0], 'complete_nrt')

    def test_provider_fetch_chunks_and_empty_success(self):
        calls = []
        header = ','.join(self.fields)+'\n'
        def response(url):
            calls.append(url)
            return ('data_id,min_date,max_date\nMODIS_SP,2020-01-01,2026-01-01\n'
                    if 'data_availability' in url else header)
        path = fetch(self.study,start='2025-01-01',end='2025-01-06',bbox=[113,-1,115,1],
            map_key='FAKE',output=self.root/'fetched',transport=response,now='2026-10-25T00:00:00+00:00')
        self.assertEqual(len(calls),3)
        self.assertTrue(calls[1].endswith('/5/2025-01-01'))
        self.assertTrue(calls[2].endswith('/1/2025-01-06'))
        imported = read_ledger(path,self.study)
        self.assertEqual(len(imported),2)
        daily,_,_ = self.run_labels(imported)
        self.assertTrue((daily.fire_detected==0).all())

    def test_provider_unsupported_history_error_response_and_secret(self):
        def unavailable(url):
            return 'data_id,min_date,max_date\nMODIS_SP,2025-01-02,2026-01-01\n'
        with self.assertRaisesRegex(ValueError,'archive|download'):
            fetch(self.study,start='2025-01-01',end='2025-01-02',bbox=[113,-1,115,1],
                map_key='FAKE',output=self.root/'fetch',transport=unavailable,now='2026-10-25T00:00:00+00:00')
        self.assertFalse((self.root/'fetch').exists())
        def malformed(url):
            return 'data_id,min_date,max_date\nMODIS_SP,2020-01-01,2026-01-01\n' if 'data_availability' in url else 'Invalid MAP_KEY'
        with self.assertRaises(ValueError):
            fetch(self.study,start='2025-01-01',end='2025-01-02',bbox=[113,-1,115,1],
                map_key='FAKE',output=self.root/'fetch',transport=malformed,now='2026-10-25T00:00:00+00:00')
        self.assertFalse((self.root/'fetch').exists())

    def test_complete_interval_must_be_closed_at_retrieval(self):
        source = self.interval('a.csv', [])
        for retrieved in ['2024-12-31T12:00:00Z', '2025-01-01T12:00:00Z', '2025-01-02T23:59:59Z']:
            source['retrieved_at'] = retrieved
            with self.assertRaisesRegex(ValueError, 'closed'):
                read_ledger(self.ledger([source]), self.study)

    def test_newer_complete_sp_removes_deleted_and_moved_old_sp(self):
        old = self.interval('old.csv', [self.row()])
        old['retrieved_at'] = '2026-10-24T00:00:00Z'
        empty = self.interval('new-empty.csv', [])
        daily, assigned, report = self.run_labels([old, empty])
        self.assertTrue(assigned.empty)
        self.assertEqual(daily.fire_detected.iloc[0], 0)
        self.assertEqual(report['sp_rows_replaced_by_newer_complete_sp'], 1)
        moved = self.interval('new-moved.csv', [self.row(longitude='114.011')])
        _, assigned, report = self.run_labels([old, moved])
        self.assertEqual(len(assigned), 1)
        self.assertEqual(assigned.longitude.iloc[0], 114.011)
        # A failed revision must not erase earlier successful observations.
        empty['status'] = 'failed'
        _, assigned, _ = self.run_labels([old, empty])
        self.assertEqual(len(assigned), 1)

    def test_heldout_guard(self):
        self.study = load_study(root=self.root,start='2026-01-01',end='2026-01-02',as_of='2026-10-25')
        source = self.interval('a.csv',[self.row(acq_date='2026-01-01')])
        source['start'],source['end']='2026-01-01','2026-01-02'
        normalized, validated, _ = ingest(read_ledger(self.ledger([source]),self.study))
        spatial = load_spatial(self.study,self.grid_path)
        guarded,_ = labels(self.study,normalized,validated,*spatial)
        self.assertTrue(guarded.empty)
        enabled,_ = labels(self.study,normalized,validated,*spatial,include_held_out=True)
        self.assertEqual(len(enabled),2)


if __name__ == '__main__':
    unittest.main()
