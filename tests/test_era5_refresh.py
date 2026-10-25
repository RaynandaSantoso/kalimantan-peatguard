"""Credential-free refresh boundary fixtures: files, cutoff, source -> committed archive/EDA."""
import copy
from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
import types
from unittest.mock import MagicMock
import unittest
from unittest.mock import patch

import numpy as np

from peatguard import load_study
from peatguard.era5_land import (archive, coordinates, load_development, read_partition,
                                 refresh, summarize_development, _save_npz)
from peatguard.inventory import sha256


class Provider:
    def __init__(self, days):
        self.days = list(days)
        self.calls = []
        self.fail = None
        self.revision = 0
        self.mask_change = False

    def discover(self, start, end):
        return [day for day in self.days if start <= day <= end]

    def fetch(self, days):
        self.calls.append(list(days))
        if self.fail and self.fail in days:
            raise RuntimeError("credential-secret-must-not-escape")
        values = np.empty((len(days), 2, 2, 3), np.float32)
        values[:, 0] = 290 + self.revision
        values[:, 1] = .4
        values[:, :, 0, 0] = np.nan
        if self.mask_change:
            values[-1, 1, 1, 1] = np.nan
        return values


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / "config").mkdir()
        (self.root / "peatguard").mkdir()
        config = json.loads((Path(__file__).resolve().parents[1] / "config/study.json").read_text())
        # Synthetic three-period fixture exercises generic validation/held-out behavior.
        config['profiles']['expanded'] = {'start': '2020-01-01', 'end': '2026-12-31', 'splits': {'train': ['2020-01-01', '2024-12-31'], 'validation': ['2025-01-01', '2025-12-31'], 'test': ['2026-01-01', '2026-12-31']}, 'held_out': ['test'], 'final_test_interval_frozen': False}
        config["sources"]["era5_land"]["shape"] = [2, 3]
        (self.root / "config/study.json").write_text(json.dumps(config))
        self.study = self.load("2025-01-01", "2025-01-10")
        self.days = [date(2025, 1, day) for day in range(1, 9) if day != 4]
        self.provider = Provider(self.days)
        self.directory = self.root / "data/interim/era5_land_daily"

    def tearDown(self):
        self.temp.cleanup()

    def load(self, start, end):
        return load_study(root=self.root, start=start, end=end, as_of="2026-10-25")

    def snapshot(self):
        return {str(path.relative_to(self.root)): (sha256(path), path.stat().st_mtime_ns)
                for path in self.root.rglob("*") if path.is_file()}

    def manifest(self):
        return json.loads((self.directory / "manifest.json").read_text())

    def test_delayed_gap_dryrun_then_partial_commit_and_repeat(self):
        before = self.snapshot()
        plan = refresh(self.study, self.provider, dry_run=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(plan["interior_gaps"], [{"start": "2025-01-04", "end": "2025-01-04", "days": 1}])
        self.assertEqual(plan["unpublished_tail"][0]["start"], "2025-01-09")
        result = refresh(self.study, self.provider)
        self.assertEqual(result["status"], "committed")
        entry = self.manifest()["years"][0]
        self.assertEqual(entry["days"], 7)
        self.assertFalse(entry["complete_calendar_year"])
        self.assertEqual(entry["units"], ["K", "m3/m3"])
        self.assertEqual(entry["source_bands"], ["temperature_2m", "volumetric_soil_water_layer_1"])
        before = self.snapshot()
        self.assertEqual(refresh(self.study, self.provider)["status"], "unchanged")
        self.assertEqual(self.snapshot(), before)

    def test_resume_failure_preserves_lastgood_and_checkpoint(self):
        initial = self.load("2025-01-01", "2025-01-02")
        refresh(initial, self.provider, chunk_days=2)
        old_manifest = (self.directory / "manifest.json").read_bytes()
        old_entry = self.manifest()["years"][0]
        old_bytes = (self.root / old_entry["file"]).read_bytes()
        self.provider.fail = date(2025, 1, 7)
        with self.assertRaisesRegex(RuntimeError, "last-good inventory preserved") as caught:
            refresh(self.study, self.provider, chunk_days=2)
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual((self.directory / "manifest.json").read_bytes(), old_manifest)
        self.assertEqual((self.root / old_entry["file"]).read_bytes(), old_bytes)
        completed_before_failure = list(self.provider.calls)
        self.provider.fail = None
        refresh(self.study, self.provider, chunk_days=2)
        # The first successful new checkpoint (Jan 3) was not fetched again.
        self.assertEqual(sum(date(2025, 1, 3) in call for call in self.provider.calls), 1)
        self.assertEqual(self.manifest()["years"][0]["days"], 7)
        self.assertIn(old_entry["file"], self.manifest()["retired_files"])

    def test_extension_and_bounded_revision_invalidates_only_old_source(self):
        refresh(self.study, self.provider)
        old = self.manifest()["years"][0]
        self.provider.days += [date(2025, 1, 9)]
        refresh(self.study, self.provider)
        self.assertEqual(self.provider.calls[-1], [date(2025, 1, 9)])
        previous = self.manifest()["years"][0]
        self.provider.revision = 3
        result = refresh(self.study, self.provider, revise=("2025-01-02", "2025-01-03"))
        self.assertEqual(result["revisions"][0]["changed_dates"], ["2025-01-02", "2025-01-03"])
        current = self.manifest()
        self.assertEqual(current["downstream_invalidation"], [previous["sha256"]])
        _, partitions, _ = archive(self.study)
        days, values, _ = partitions[2025]
        self.assertEqual(values[days.index(date(2025, 1, 2)), 0, 1, 1], 293)
        self.assertEqual(values[days.index(date(2025, 1, 1)), 0, 1, 1], 290)
        self.assertTrue((self.root / old["file"]).exists())
        self.assertEqual(len(current["runs"]), 3)

    def test_year_extension_preserves_prior_inventory_and_heldout_guard(self):
        refresh(self.study, self.provider)
        old = copy.deepcopy(self.manifest()["years"][0])
        self.provider.days += [date(2026, 1, 1)]
        refresh(self.load("2026-01-01", "2026-01-02"), self.provider)
        self.assertEqual(self.manifest()["years"][0], old)
        loaded = load_development(self.load("2025-01-01", "2026-01-02"))
        self.assertEqual(loaded["dates"], self.days)
        self.assertTrue(all(day.year != 2026 for day in loaded["dates"]))

    def test_legacy_import_and_replacement_keep_original_bytes(self):
        self.directory.mkdir(parents=True)
        days = self.days[:2]
        values = self.provider.fetch(days)
        spec = self.study.config["sources"]["era5_land"]
        path = self.directory / "era5_land_kalimantan_2025.npz"
        _save_npz(path, days, values, spec)
        digest = sha256(path)
        entry = {"year": 2025, "days": 2, "file": path.relative_to(self.root).as_posix(), "sha256": digest}
        (self.directory / "manifest.json").write_text(json.dumps({"earth_engine_collection": spec["collection"], "years": [entry]}))
        refresh(self.study, self.provider)
        self.assertEqual(sha256(path), digest)
        self.assertIn(entry["file"], self.manifest()["retired_files"])

    def test_corrupt_archive_and_wrong_grid_are_diagnosed_before_fetch(self):
        refresh(self.study, self.provider)
        entry = self.manifest()["years"][0]
        (self.root / entry["file"]).write_bytes(b"corrupt")
        self.provider.calls.clear()
        with self.assertRaisesRegex(ValueError, "Checksum"):
            refresh(self.study, self.provider)
        self.assertEqual(self.provider.calls, [])

    def test_missingness_changed_mask_is_not_hidden(self):
        self.provider.mask_change = True
        with self.assertRaisesRegex(ValueError, "missingness"):
            refresh(self.study, self.provider)
        self.assertFalse((self.directory / "manifest.json").exists())

    def test_grid_date_band_and_physical_errors_preserve_archive(self):
        for mode in ("grid", "date", "kelvin", "fraction"):
            with self.subTest(mode=mode):
                provider = Provider(self.days)
                fetch = provider.fetch
                def bad(days):
                    values = fetch(days)
                    if mode == "grid": return values[:, :, :, :2]
                    if mode == "kelvin": values[:, 0, 1, 1] = -2
                    if mode == "fraction": values[:, 1, 1, 1] = 2
                    return values
                provider.fetch = bad
                if mode == "date":
                    provider.discover = lambda start, end: [date(2025, 1, 1), date(2025, 1, 1)]
                with self.assertRaises(ValueError): refresh(self.study, provider)
                self.assertFalse((self.directory / "manifest.json").exists())

    def test_corrupt_checkpoint_rejected_not_silently_trusted(self):
        self.provider.fail = date(2025, 1, 7)
        with self.assertRaises(RuntimeError): refresh(self.study, self.provider, chunk_days=2)
        checkpoint = next(self.directory.glob(".checkpoints/*/data.npz"))
        checkpoint.write_bytes(b"bad")
        self.provider.fail = None
        with self.assertRaisesRegex(ValueError, "Checksum"):
            refresh(self.study, self.provider, chunk_days=2)
        self.assertFalse((self.directory / "manifest.json").exists())

    def test_failure_before_manifest_promotion_preserves_previous_commit(self):
        refresh(self.load("2025-01-01", "2025-01-02"), self.provider)
        before = (self.directory / "manifest.json").read_bytes()
        old = self.manifest()["years"][0]
        from peatguard import era5_land
        write_json = era5_land._atomic_json
        def fail_commit(path, payload):
            if path.name == "manifest.json":
                raise OSError("simulated disk error before atomic commit")
            return write_json(path, payload)
        with patch("peatguard.era5_land._atomic_json", side_effect=fail_commit):
            with self.assertRaises(OSError):
                refresh(self.study, self.provider)
        self.assertEqual((self.directory / "manifest.json").read_bytes(), before)
        self.assertEqual(sha256(self.root / old["file"]), old["sha256"])
        self.assertFalse((self.directory / ".refresh.lock").exists())
        refresh(self.study, self.provider)
        self.assertEqual(self.manifest()["years"][0]["days"], 7)

    def test_changed_checkpoint_identity_and_invalid_revision_are_rejected(self):
        self.provider.fail = date(2025, 1, 7)
        with self.assertRaises(RuntimeError):
            refresh(self.study, self.provider, chunk_days=2)
        receipt_path = next(self.directory.glob(".checkpoints/*/receipt.json"))
        receipt = json.loads(receipt_path.read_text())
        receipt["source_identity"]["version"] = "different"
        receipt_path.write_text(json.dumps(receipt))
        self.provider.fail = None
        with self.assertRaisesRegex(ValueError, "identity"):
            refresh(self.study, self.provider, chunk_days=2)
        with self.assertRaisesRegex(ValueError, "Revision dates"):
            refresh(self.study, self.provider, revise=("2024-12-31", "2025-01-01"))

    def test_live_adapter_metadata_bounds_without_credentials(self):
        from peatguard.era5_land import EarthEngineProvider
        ee = types.ModuleType("ee")
        ee.Initialize = MagicMock()
        ee.ImageCollection = MagicMock()
        collection = ee.ImageCollection.return_value
        collection.filterDate.return_value = collection
        epoch = int(__import__("datetime").datetime(2025, 1, 2, tzinfo=__import__("datetime").timezone.utc).timestamp()*1000)
        collection.aggregate_array.return_value.getInfo.return_value = [epoch]
        with patch.dict("sys.modules", {"ee": ee}):
            provider = EarthEngineProvider(self.study, "fixture-project")
            self.assertEqual(provider.discover(date(2025, 1, 1), date(2025, 1, 10)), [date(2025, 1, 2)])
            collection.filterDate.assert_called_once_with("2025-01-01", "2025-01-11")
            ee.Initialize.assert_called_once_with(project="fixture-project")
            ee.Initialize.side_effect = RuntimeError("secret")
            with self.assertRaises(RuntimeError) as caught:
                EarthEngineProvider(self.study, "fixture-project")
            self.assertNotIn("secret", str(caught.exception))

    def test_leapday_and_coverage_eda_denominators(self):
        study = self.load("2024-02-28", "2024-03-01")
        provider = Provider([date(2024, 2, 28), date(2024, 2, 29), date(2024, 3, 1)])
        refresh(study, provider)
        loaded = load_development(study)
        daily, monthly, quality, maps, counts = summarize_development(loaded, np.ones((2, 3), dtype=bool))
        self.assertEqual(monthly.loc["2024-02-01", ("temperature_C", "count")], 2)
        self.assertFalse(monthly.loc["2024-02-01", ("temperature_C", "complete_month")])
        self.assertEqual(quality.loc[2024, "calendar_days"], 366)
        self.assertFalse(quality.loc[2024, "complete_year"])
        self.assertAlmostEqual(float(maps[0, 1, 1]), 16.85, places=4)
        self.assertTrue(np.isnan(maps[:, 0, 0]).all())
        self.assertEqual(counts[0, 1, 1], 3)


if __name__ == "__main__":
    unittest.main()
