# Historical data refresh report — 2026-10-25

> **Closure note — 2026-10-28:** The original report below is preserved as acquisition history. Verified FIRMS archive evidence subsequently enabled the strict 2020–2025 dataset, and the classical, ConvLSTM and calibration experiments are complete; see [findings](findings.md). The final study ends in 2025. Statements below about blocked publication, ticket status, untrained models and acquisition requirements describe the dated snapshot; the completed notebook experiments and final results are documented in the findings report.

> **Historical acquisition snapshot, not current setup instructions.** The original report below used training 2020–2024, validation 2025 and a provisional 2026 test interval. That design has been superseded: `config/study.json` now fixes training 2020–2024 and test 2025, with no validation split or 2026 study interval. Missing 2026 maps do not block the active experiment; verified FIRMS coverage for 2020–2025 remains required. Use [setup](setup.md) and [dataset preparation](dataset-preparation.md) for runnable current commands. Counts and local artifact paths below describe the historical run and are not bundled in Git.

Ticket #8 is **partially fulfilled**: obtainable weather and annual-map backfills succeeded, public FIRMS observations were acquired, and all source EDAs were regenerated. Publication of complete chronological model partitions remains blocked by verified FIRMS coverage and the absent 2026 same-year MODIS map. No complete-dataset or model-performance claim is made.

The selected experiment is expanded same-day UTC retrospective research: training 2020–2024, validation 2025, and a still-unfrozen 2026 held-out interval. The requested source envelope is 2020-01-01 through 2026-10-25 inclusive. Acquisition itself requested only missing dates from 2025 onward. The fixed as-of date bounds requested dates; actual discovery/retrieval timestamps are retained separately and do not establish historical issue-time availability.

## Actual acquired coverage

| Source | 2025 | 2026 present locally | Remaining gap/block |
| --- | --- | --- | --- |
| CHIRPS v2 Final | All 365 days | Jan 1–Sep 30: 273 days | Oct 1–25 is an unpublished tail in the configured EE collection |
| ERA5-Land daily | All 365 days | 284 days through Oct 12 | Oct 9 is an interior EE asset gap; Oct 13–25 is an unpublished tail |
| MCD12Q1 v6.1 | Actual 2025 map downloaded | No 2026 map published | Strict same-year assignment blocks 2026; carry-forward was not selected |
| GHSL R2023A | Reused static 2020 layer | Same static 2020 layer | No annual replacement intended |
| FIRMS MODIS C6.1 | Public Indonesia 2025 point CSV downloaded and normalized | Recent public Southeast Asia seven-day NRT snapshot downloaded and normalized | No verified complete rectangular/model-mask intervals; full 2026 archive/API access still needed |

All currently published requested CHIRPS/ERA5 dates in the configured provider were retrieved: 638 additional CHIRPS days and 649 additional ERA5 days across 2025/2026. Including historical files, validated totals are 2,465 and 2,476 days respectively. These are not full-year 2026 totals. Existing 2020–2024 annual files were reused, not redownloaded.

Source identities remain `UCSB-CHG/CHIRPS/DAILY` (`2.0_final`), `ECMWF/ERA5_LAND/DAILY_AGGR` (daily UTC means, K and 0–7 cm modeled volume fraction), `MODIS/061/MCD12Q1` (`LC_Type1/QC/LW`), and `JRC/GHSL/P2023A/GHS_BUILT_S/2020`. Each cumulative source manifest records active files, exact dates/native geometry and SHA-256. The local final coverage JSON includes copies of those records and code hashes. V3 was not substituted.

## FIRMS observations versus label coverage

No `FIRMS_MAP_KEY` is configured, but official public downloads provided the [Indonesia 2025 file](https://firms.modaps.eosdis.nasa.gov/data/country/modis/2025/modis_2025_Indonesia.csv) and [recent MODIS snapshot](https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_SouthEast_Asia_7d.csv). The annual source is listed by NASA's [country/year index](https://firms.modaps.eosdis.nasa.gov/data/country/yearly_summary_files.txt). The recent snapshot's recorded acquisition dates describe observed rows only, not a complete retrieval interval. No full 2026 annual country file was present in that listing.

The public NRT CSV omits `instrument` and `type`; explicit endpoint-backed `declared_instrument: MODIS` is now supported without modifying the raw CSV. Type remains unknown and all types are retained. Both new sources passed normalization with no rejected rows. The country source has 17,847 raw observations across Indonesia in 2025; this is a source-integrity count, not a Kalimantan incident count.

These sources are stored in `data/raw/Firms/refresh-2026-10-25/`. The cumulative `data/raw/Firms/intervals.json` includes the existing archive and new downloads. All intervals are marked **unknown coverage**. The old archive has no original dates/bounds confirmation; the country extraction's exact clipping boundary has not been checked against the BIG study mask; the rolling NRT file does not certify complete UTC-day queries. Date/bbox fields there are row-validation envelopes, not assertions of complete source coverage. Known positives remain usable observations; no unsupported zero labels are created.

The remaining acquisition route is a free configured [FIRMS Area API](https://firms.modaps.eosdis.nasa.gov/api/area/) key or [authenticated archive exports](https://firms.modaps.eosdis.nasa.gov/download/) retaining requested inclusive dates, rectangle, collection, stage and confirmation. Runtime availability must determine supported SP/NRT intervals; do not infer them from first/last detections. Never paste credentials into tracked files. For development, obtain verified 2020–2025 coverage; for test preparation, separately establish the chosen available 2026 interval and processing stages.

## Verification completed

- All **85 credential-free integration tests pass**. The initial 82-test suite passed before acquisition; real-source fixes added three regression tests.
- Authenticated one-day CHIRPS and ERA5 smoke downloads verified actual bands, dates, shapes, units and missingness before bulk acquisition. Those validated partial 2025 files were safely extended to complete 2025 partitions.
- A repeated full weather refresh reported no CHIRPS changes and ERA5 `unchanged`, with no new dates acquired.
- SHA-256 and modification times of **29 original source/legacy artifact files** are unchanged, including historical weather, annual rasters, the static raster, candidate grid and old generated model tables.
- All **six source EDA notebooks ran from clean kernels in both repository-root and EDA working directories**. Their saved outputs are now refreshed. Weather EDA loads all 2,192 development days through 2025, and MODIS EDA includes the actual 2025 map. FIRMS EDA defaults to the local audited ledger so its 2025 data remains visible on rerun.
- Ordinary EDA excludes 2026 outcomes. The recent 2026 FIRMS snapshot is retained as a source artifact; no held-out target distributions, relationships, examples or scores were opened. No model was trained.

The real 2025 MODIS download exposed tiny provider serialization differences: easting changed about 0.000018 m while shape, CRS and nominal native lattice remained the same. Validation/EDA now allow **absolute 0.0001 m (0.1 mm)** affine serialization tolerance, with no relative tolerance on million-metre coordinates. Metre-scale shifts and shape mismatches still fail. Native source values and affine metadata are retained; this is not resampling to a different study grid.

## Real preparation attempt and exclusions

A strict run was attempted using all development years and the explicitly bounded **provisional** 2026-01-01–2026-09-30 structural window. This date bound was chosen from common published weather coverage, not from model outcomes; it is not a frozen final evaluation interval. Land-cover policy remained `strict_same_year`, coverage policy `require_complete`.

The attempt inspected **8,657,080 requested cell/date keys** across 3,512 cells and saved a keyed exclusion ledger. It refused publication, preserving previous artifacts. The diagnostics identify unknown FIRMS labels throughout the requested experiment and the missing annual map for 2026. Known-positive rows alone do not constitute a credible complete training/evaluation cohort, so the partial-subset policy was not enabled. No train/validation/test model partitions were published by this run.

Local diagnostic paths:

- `data/interim/builds/expanded/521ab034faa4440d5bfe/diagnostics/ba7c40ee908694e6edb3aa77/quality.json`
- `data/interim/builds/expanded/521ab034faa4440d5bfe/diagnostics/ba7c40ee908694e6edb3aa77/exclusions.parquet`

The diagnostic JSON's `held_out_prepared` field means held-out dates were included in the preparation **attempt**; the failed run's log and coverage report explicitly record `partitions_published: false`. Preprocessing and baseline outputs remain cleared because no complete new build exists. Source EDA success does not imply modeling eligibility.

## Historical reproduction commands (superseded)

The following commands are retained as acquisition evidence. Do not run them as current setup: the 2026 preparation bounds are rejected by the active configuration, and the named project belonged to the original run. Use the setup guide with your own project instead:

```text
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python -m peatguard --profile expanded --as-of 2026-10-25 --discover --project kalimantan-peat
.venv/bin/python scripts/download_chirps_v2.py --project kalimantan-peat --start 2025-01-01 --end latest --as-of 2026-10-25
.venv/bin/python scripts/download_era5_land_daily.py --project kalimantan-peat --start 2025-01-01 --end latest --as-of 2026-10-25
.venv/bin/python scripts/download_modis_landcover.py --project kalimantan-peat --start 2025-01-01 --end latest --as-of 2026-10-25 --include-held-out
```

For FIRMS, retain immutable CSVs with checksums and explicit interval evidence as described in [the ingestion guide](firms-refresh.md). Public rolling URLs are mutable: preserve this dated snapshot rather than overwrite it on rerun. The public country file alone does not satisfy this project's rectangular coverage contract.

The historical preparation attempt used the following superseded command:

```text
.venv/bin/python scripts/prepare_dataset.py --profile expanded --as-of 2026-10-25 --ledger data/raw/Firms/intervals.json --held-out-start 2026-01-01 --held-out-end 2026-09-30
```

The illustrated held-out bound remains provisional. A user-selected carry-forward experiment would need `--map-policy carry_forward`, with actual map year/staleness recorded; it must not be silently applied to clear this block.

To regenerate EDA, set `PEATGUARD_AS_OF=2026-10-25` and execute each source notebook in a clean project kernel. The local evidence runner at `data/interim/refresh_runs/2026-10-25/execute_eda.py` records both supported working directories. A clean clone needs its source files and environment restored; bulk data, caches, temporary kernel metadata and reports under `data/` are gitignored.

## Local audit bundle

`data/interim/refresh_runs/2026-10-25/coverage.json` contains requested, retrieved, validated, unavailable/provisional and blocked source states, source/image identities, checksums, split roles, policies, processing hashes, actual build diagnostics and notebook results. The same folder contains before/final read-only plans, smoke/bulk/repeat results, preservation fingerprints, public-download metadata, test output and per-notebook execution records. Canonical source manifests retain the acquisition run provenance and active immutable file hashes.

No credentials or bulk data were committed; no paid resources, email requests, model training, source switch or held-out outcome analysis was performed. **Ticket #8 remains incomplete at dataset publication until the stated coverage/map blocks are resolved.**
