"""Refresh-boundary checks using small real files, no provider credentials."""

from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from peatguard.inventory import build_inventory, discover_availability, sha256
from peatguard.study import find_root, load_study


REPO = find_root()


class RefreshBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "config").mkdir()
        (self.root / "peatguard").mkdir()
        (self.root / "EDA").mkdir()
        self.config = json.loads((REPO / "config/study.json").read_text())
        # Synthetic three-period fixture exercises generic validation/held-out behavior.
        self.config['profiles']['expanded'] = {'start': '2020-01-01', 'end': '2026-12-31', 'splits': {'train': ['2020-01-01', '2024-12-31'], 'validation': ['2025-01-01', '2025-12-31'], 'test': ['2026-01-01', '2026-12-31']}, 'held_out': ['test'], 'final_test_interval_frozen': False}
        for key in ("chirps", "era5_land"):
            self.config["sources"][key]["shape"] = [2, 2]
        (self.root / "config/study.json").write_text(json.dumps(self.config))
        for key, value in self.config["boundaries"].items():
            path = self.root / value
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {}, "geometry": {
                    "type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}
            ]}))
        for key in ("chirps", "era5_land"):
            self.daily(key, ["2025-01-01", "2025-01-03"])
        self.raster("modis")
        self.raster("ghsl")
        firms = self.root / "data/raw/Firms/archive.csv"
        firms.parent.mkdir(parents=True)
        firms.write_text("latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,version,type\n"
                         "0,114,2025-01-01,0123,Terra,MODIS,90,6.1,0\n"
                         "0,114,2025-01-03,2359,Aqua,MODIS,70,6.1,0\n")

    def daily(self, key, dates):
        definition = self.config["sources"][key]
        directory = self.root / definition["directory"]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / definition["pattern"].replace("*", "2025")
        a, _, west, _, e, north = definition["grid_transform"]
        values = {band: np.full((len(dates), 2, 2), 0.5, dtype="float32")
                  for band in definition["bands"]}
        np.savez_compressed(path, dates=np.array(dates),
                            latitude=north + e * (np.arange(2) + .5),
                            longitude=west + a * (np.arange(2) + .5), **values)
        manifest = {"earth_engine_collection": definition["collection"], "years": [{
            "year": 2025, "days": len(dates), "file": path.relative_to(self.root).as_posix(),
            "sha256": sha256(path)}]}
        (directory / "manifest.json").write_text(json.dumps(manifest))
        return path

    def raster(self, key):
        definition = self.config["sources"][key]
        directory = self.root / definition["directory"]
        directory.mkdir(parents=True)
        path = directory / definition["pattern"].replace("*", "2024")
        annual = key == "modis"
        crs = CRS.from_proj4("+proj=sinu +R=6371007.181 +no_defs" if annual else "+proj=moll")
        transform = from_origin(0, 100, 463.3127165279165 if annual else 100,
                                463.3127165279165 if annual else 100)
        with rasterio.open(path, "w", driver="GTiff", width=2, height=2, count=3 if annual else 1,
                           dtype="uint8" if annual else "uint16", crs=crs, transform=transform,
                           nodata=None if annual else 65535) as target:
            target.write(np.ones((target.count, 2, 2), dtype=target.dtypes[0]))
            for i, band in enumerate(definition["bands"], 1):
                target.set_band_description(i, band)
        entry = {"year": 2024, "file": path.relative_to(self.root).as_posix(),
                 "sha256": sha256(path), "shape_lat_lon": [2, 2], "transform": list(transform)[:6]}
        manifest = ({"earth_engine_collection": definition["collection"], "years": [entry]} if annual
                    else {"earth_engine_image": definition["collection"], "output": entry["file"],
                          "sha256": entry["sha256"], "shape_rows_columns": [2, 2],
                          "transform": entry["transform"], "crs_wkt": crs.to_wkt()})
        (directory / "manifest.json").write_text(json.dumps(manifest))

    def study(self, **kwargs):
        options = dict(root=self.root, start="2025-01-01", end="latest", as_of="2025-01-04")
        return load_study(**(options | kwargs))

    def snapshot(self):
        return {str(p.relative_to(self.root)): (sha256(p), p.stat().st_mtime_ns)
                for p in self.root.rglob("*") if p.is_file()}

    def test_retired_archives_are_preserved_but_active_entries_still_validate(self):
        study = self.study()
        directory = self.root / self.config["sources"]["chirps"]["directory"]
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        active = manifest["years"][0]["file"]
        retired = directory / "chirps_v2_kalimantan_superseded.npz"
        retired.write_bytes(b"Preserved historical artifact")
        manifest["retired_files"] = [retired.relative_to(self.root).as_posix(), active]
        manifest_path.write_text(json.dumps(manifest))
        before = self.snapshot()
        report = build_inventory(study)["sources"]["chirps"]
        self.assertEqual(len(report["files"]), 1)
        self.assertEqual(report["files"][0]["status"], "validated")
        self.assertEqual(before, self.snapshot())
        manifest["retired_files"].append("../../outside.npz")
        manifest_path.write_text(json.dumps(manifest))
        report = build_inventory(study)["sources"]["chirps"]
        self.assertTrue(any(r["kind"] == "corrupt_manifest" for r in report["diagnostics"]))

    def test_public_modis_missing_sensor_requires_checksummed_interval_metadata(self):
        directory=self.root/self.config['sources']['firms']['directory']
        source=directory/'MODIS_C6_1_snapshot.csv'
        source.write_text('latitude,longitude,acq_date,acq_time,satellite,confidence,version\n0,114,2025-01-01,0007,Terra,90,6.1NRT\n')
        def record():
            return next(r for r in build_inventory(self.study())['sources']['firms']['files'] if r['path'].endswith(source.name))
        self.assertEqual(record()['status'],'corrupt_or_unverified')
        (directory/'intervals.json').write_text(json.dumps({'schema_version':1,'intervals':[{
            'product':'FIRMS_MODIS_C6.1','declared_instrument':'MODIS','path':source.name,'sha256':sha256(source)}]}))
        self.assertEqual(record()['status'],'structure_checked_coverage_unknown')

    def test_complete_refresh_boundary_is_read_only_and_has_no_network(self):
        before = self.snapshot()
        with patch("socket.socket", side_effect=AssertionError("Network is forbidden")):
            report = build_inventory(self.study())
        self.assertEqual(before, self.snapshot())
        rain = report["sources"]["chirps"]
        self.assertEqual(rain["validated_requested_days"], 2)
        self.assertEqual(rain["local_interior_gaps"], [{"start": "2025-01-02", "end": "2025-01-02", "days": 1}])
        self.assertEqual(rain["availability"]["status"], "not_checked")
        self.assertIsNone(rain["published_ranges"])
        self.assertEqual(report["sources"]["modis"]["validated_map_years"], [2024])
        self.assertEqual(report["sources"]["modis"]["requested_missing_map_years"], [2025])
        self.assertTrue(report["sources"]["ghsl"]["validated_static"])
        firms = report["sources"]["firms"]
        self.assertEqual(firms["files"][0]["detection_date_range"], ["2025-01-01", "2025-01-03"])
        self.assertEqual(firms["validated_ranges"], [])
        self.assertEqual(firms["label_eligibility"], "unknown_until_interval_ingestion")
        self.assertIn("discover_source_dates", [action["action"] for action in report["plan"]])
        self.assertNotIn("download_missing", [action["action"] for action in report["plan"]])

    def test_provider_dates_separate_unpublished_tail_and_interior_gaps(self):
        available = {"observed_at": "2025-01-04T00:00:00Z", "sources": {"chirps": {
            "status": "discovered", "collection": self.config["sources"]["chirps"]["collection"],
            "dates": ["2025-01-01", "2025-01-02", "2025-01-03"]}}}
        rain = build_inventory(self.study(), availability=available)["sources"]["chirps"]
        self.assertEqual(rain["published_not_local"][0]["start"], "2025-01-02")
        self.assertEqual(rain["unpublished_tail"][0]["start"], "2025-01-04")
        self.assertEqual(rain["source_interior_gaps"], [])
        available["sources"]["chirps"]["dates"].remove("2025-01-02")
        rain = build_inventory(self.study(), availability=available)["sources"]["chirps"]
        self.assertEqual(rain["source_interior_gaps"][0]["start"], "2025-01-02")

    def test_corrupt_source_and_corrupt_manifest_remain_diagnostics(self):
        source = self.root / "data/interim/chirps_v2/chirps_v2_kalimantan_2025.npz"
        source.write_bytes(b"not an array")
        era_manifest = self.root / "data/interim/era5_land_daily/manifest.json"
        era_manifest.write_text("invalid JSON")
        report = build_inventory(self.study())
        self.assertEqual(report["sources"]["chirps"]["validated_requested_days"], 0)
        self.assertEqual(report["sources"]["chirps"]["diagnostics"][0]["kind"], "corrupt_input")
        self.assertEqual(report["sources"]["era5_land"]["diagnostics"][0]["kind"], "corrupt_manifest")

    def test_malformed_static_manifest_does_not_abort_other_sources(self):
        path = self.root / "data/interim/ghsl_built_surface/manifest.json"
        manifest = json.loads(path.read_text())
        del manifest["output"]
        path.write_text(json.dumps(manifest))
        report = build_inventory(self.study())
        self.assertEqual(report["sources"]["ghsl"]["diagnostics"][0]["kind"], "corrupt_manifest")
        self.assertEqual(report["sources"]["chirps"]["validated_requested_days"], 2)

    def test_legacy_ghsl_wkt_serialization_is_not_a_changed_grid(self):
        path = self.root / "data/interim/ghsl_built_surface/manifest.json"
        manifest = json.loads(path.read_text())
        wkt = CRS.from_wkt(manifest["crs_wkt"]).to_wkt(version="WKT2_2019")
        manifest["crs_wkt"] = wkt.replace('METHOD["Mollweide"],',
                                         'METHOD["Mollweide"],PARAMETER["semi_minor",6378137],')
        path.write_text(json.dumps(manifest))
        self.assertTrue(build_inventory(self.study())["sources"]["ghsl"]["validated_static"])

    def test_latest_and_future_dates_obey_explicit_utc_cutoff(self):
        study = self.study(as_of="2025-01-04T00:30:00+08:00")
        self.assertEqual(study.end, date(2025, 1, 3))
        with self.assertRaisesRegex(ValueError, "after the as-of"):
            self.study(end="2025-01-05")
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            self.study(as_of="2025-01-04T12:00:00")
        legacy = self.study(profile="legacy", start="2024-02-28", as_of="2026-10-25")
        self.assertEqual(legacy.end, date(2024, 12, 31))
        self.assertIn(date(2024, 2, 29), legacy.split_dates("test", include_held_out=True))

    def test_profiles_cannot_mix_and_held_out_access_is_explicit(self):
        expanded = self.study(as_of="2026-10-25")
        self.assertTrue(all(day.year <= 2025 for day in expanded.development_dates()))
        with self.assertRaisesRegex(ValueError, "held out"):
            expanded.split_dates("test")
        self.assertEqual(expanded.split_dates("test", include_held_out=True)[0], date(2026, 1, 1))
        legacy = self.study(profile="legacy", start="2024-01-01", as_of="2026-10-25")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            expanded.require_compatible(legacy.metadata())
        expanded.require_compatible(expanded.metadata())
        self.assertNotEqual(legacy.output_directory, expanded.output_directory)
        self.assertFalse(expanded.output_directory.exists())

    def test_missing_credentials_are_safe_and_do_not_require_authentication(self):
        with patch.dict(os.environ, {"FIRMS_MAP_KEY": "secret-must-not-appear"}):
            report = build_inventory(self.study(), availability=discover_availability(self.study(), None))
        self.assertEqual(report["sources"]["chirps"]["availability"]["status"], "missing_project")
        self.assertNotIn("secret-must-not-appear", json.dumps(report))

    def test_opt_in_provider_discovery_only_requests_date_metadata(self):
        calls = []

        class Collection:
            def __init__(self, source):
                self.source = source

            def filterDate(self, start, end):
                calls.append((self.source, start, end))
                return self

            def aggregate_array(self, field):
                self.asserted_field = field
                if field != "system:time_start":
                    raise AssertionError("Unexpected provider operation")
                return SimpleNamespace(getInfo=lambda: [1735689600000, 1735862400000])

        provider = SimpleNamespace(Initialize=lambda **kwargs: None, ImageCollection=Collection)
        with patch.dict(sys.modules, {"ee": provider}):
            availability = discover_availability(self.study(), "fixture-project")
        report = build_inventory(self.study(), availability=availability)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(start == "2025-01-01" and end == "2025-01-05"
                            for _, start, end in calls))
        self.assertEqual(report["sources"]["chirps"]["source_interior_gaps"][0]["start"], "2025-01-02")
        self.assertIn("blocked_source_gap", [action["action"] for action in report["plan"]])

    def test_provider_initialization_failure_does_not_emit_secret_error_text(self):
        def fail(**kwargs):
            raise RuntimeError("secret-credential-in-provider-error")

        with patch.dict(sys.modules, {"ee": SimpleNamespace(Initialize=fail)}):
            report = build_inventory(self.study(), availability=discover_availability(self.study(), "fixture"))
        self.assertNotIn("secret-credential", json.dumps(report))
        self.assertEqual(report["sources"]["chirps"]["availability"]["status"],
                         "authentication_or_initialization_failed")

    def test_root_resolution_and_cli_from_both_working_directories(self):
        before = self.snapshot()
        for directory in (self.root, self.root / "EDA"):
            self.assertEqual(find_root(directory), self.root)
            # The application itself is importable in a fresh process, independently
            # of the fixture's repository-root marker and optional editable install.
            env = os.environ | {"PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"}
            process = subprocess.run([sys.executable, "-m", "peatguard", "--as-of", "2025-01-04",
                                      "--start", "2025-01-01"], cwd=directory, env=env,
                                     capture_output=True, text=True, check=True)
            report = json.loads(process.stdout)
            self.assertEqual(report["study"]["requested"]["end"], "2025-01-04")
        self.assertEqual(before, self.snapshot())

    def test_availability_identity_cannot_silently_change_source_product(self):
        availability = {"sources": {"chirps": {"status": "discovered", "collection": "other", "dates": []}}}
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            build_inventory(self.study(), availability=availability)


if __name__ == "__main__":
    unittest.main()
