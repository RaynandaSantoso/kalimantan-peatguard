# kalimantan-peatguard
Peatland fire risk monitoring for Kalimantan using ML

## First-time setup

Start with [environment setup and data acquisition](docs/setup.md). Bulk data is
not included in Git. Weather and raster scripts support historical downloads;
FIRMS may require authenticated archive exports and verified coverage evidence.
The two BIG geographic inputs must be supplied separately. The guide explains
these prerequisites and the current limits on complete dataset reproduction.

## Read-only study inventory and refresh plan

The shared configuration is in `config/study.json`. Install the local Python
package once from the repository root after installing the requirements:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e . --no-deps
.venv/bin/python -m peatguard --profile expanded --as-of 2026-10-25
```

This prints a JSON plan to the terminal. The default run is entirely local: it
does not authenticate, contact a provider, download anything, update manifests,
build labels, or create output directories. It reads and checks existing files,
including their hashes, array dates/grids/bands and raster metadata/values.
Existing annual files and manifests remain unchanged. Validation includes
coastal source masks; it does not imply every model cell has usable features.
The report's `plan` lists reuse, discovery, missing downloads and blocked
intervals as appropriate; it never executes those actions.

To save a report, explicitly redirect the command's output to a local file of
your choice. To inspect a narrower inclusive interval:

```bash
.venv/bin/python -m peatguard --profile expanded --start 2025-01-01 --end 2025-12-31 --as-of 2026-10-25
```

Dates are inclusive **source-day UTC** dates for this retrospective research
profile. An `--as-of` date means the end of that UTC date; a timestamp requires
an explicit UTC offset. `--end latest` (the default) resolves to the earlier of
the cutoff's UTC date and the profile's end. An explicit end after the cutoff
is rejected. Today's requested date is not a claim that today's source data
have been published. The resolved cutoff and dates are included in the report.

The profiles are deliberately separate:

| Profile | Training | Held-out test |
| --- | --- | --- |
| `legacy` | 2020–2023 | 2024 |
| `expanded` | 2020–2024 | 2025 |

The active expanded study ends on **2025-12-31**. There is no validation
split and no 2026 study interval. The full 2025 test calendar is fixed
(`final_test_interval_frozen: true`). Development EDA and default model
loading use training dates only; test outcomes require explicit access.
Previously downloaded 2026 source files remain stored for future work and
are outside this experiment. A retrieval timestamp in 2026 records when a
file was obtained; it does not put 2026 observations into the study.

Each report has a profile-specific study ID derived from the configuration,
resolved dates and cutoff, and a separate reserved build directory. Future
consumers can use `require_compatible()` to reject mixed profiles/builds. This
command does not migrate the old model tables or change the legacy baseline
notebook; the versioned preparation task will wire those consumers to the new
contract. The existing tables remain retrospective same-day research data.

Provider availability is **unknown** in the default local report. To query
current Earth Engine asset dates without downloading imagery, opt in:

```bash
.venv/bin/python -m peatguard --as-of 2026-10-25 --discover --project YOUR_PROJECT_ID
```

The optional discovery uses your existing Earth Engine authentication and
reports initialization or access failures without emitting credential values.
It distinguishes source interior gaps, unpublished tails, and published dates
not yet available locally. Asset dates discovered now do not prove that the
same data were available at a historical forecast cutoff. Discovery does not
infer FIRMS archive coverage or change annual map assignment. A captured
availability object can also be supplied with `--availability FILE`; its
`sources` entries use the configured source key, `status: "discovered"`, the
matching `collection`, and a list of ISO `dates`. The inventory's individual
source sections expose the corresponding published ranges/map years.

The local FIRMS CSV is recognized and checked structurally, but its first/last
detection dates cannot prove its requested export interval. The raw inventory alone does not consume interval ledgers; daily coverage and
label eligibility remain unknown there. Use the FIRMS workflow below to establish coverage;
missing retrieval must never become a negative fire label. Missing source files,
invalid manifests, checksum mismatches and malformed data appear in diagnostics.
Credential hints only indicate whether setup appears present, not whether
access works. No secret values are included. Use free access only; this task
does not provision paid infrastructure or run automatic training.

Run the credential-free integration checks from the repository root:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

All six source EDA notebooks now use the shared root resolver and can run from
both the root and `EDA/` after editable installation. Source EDA defaults to
configured development periods and reports missing/partial coverage explicitly.
Preprocessing and baseline setup now use the versioned chronological dataset contract described below.

## Bounded source refresh workflows

The source scripts support first-time acquisition and explicit later refreshes.
Available backfill was acquired locally, but data is excluded from Git.
Complete dataset publication requires verified FIRMS coverage; the maintainer’s local archive evidence is now recorded, but bulk files are excluded from Git.

| Source | Workflow and reproduction guide |
| --- | --- |
| CHIRPS v2 Final | [Bounded acquisition, resume, revisions and partial-period rainfall EDA](docs/chirps-refresh.md) |
| ERA5-Land | [UTC daily temperature/soil water, atomic refresh and development EDA](docs/era5-land-refresh.md) |
| FIRMS MODIS C6.1 | [Free API/archive import, declared coverage, deduplication and nullable daily labels](docs/firms-refresh.md) |
| MODIS / GHSL | [Discovered annual maps, strict/carry-forward policies and static 2020 features](docs/spatial-refresh.md) |

Earth Engine access and a FIRMS MAP_KEY must be configured locally for live
acquisition. Credential-free tests use small provider fixtures. No paid hosting
or automatic training is introduced. Authenticated provider smoke tests remain
required before the full backfill. Existing sources and legacy generated tables
are preserved; refreshed manifests identify active data versions.

## Preprocessing: candidate 5 km study grid

Run `preprocessing.ipynb` from the repository root with the project environment
after downloading the source data. This is the working preprocessing pipeline.
It reads the existing BIG province and peatland GeoJSON files under
`data/raw/boundaries/` and `data/raw/peatland/`, builds full 5,000 m squares in
a local equal-area projection, and flags intersections with the dissolved BIG
layer inside the five provinces. All 146 downloaded BIG polygons are included
as a candidate mask; their classification remains subject to review.

The notebook explains the projection, fixed lattice origin, stable cell IDs,
boundary-cell rule, and overlap fractions. It validates geometry and area
coverage, then saves a CSV, WGS84 GeoJSON, overview/close-up PNG, and source-hashed
manifest under `data/interim/study_grid_5km/` (excluded from Git). It then
calls the shared preparation workflow for validated FIRMS labels, keyed weather
joins, annual classes, static distances and chronological exports. Review the grid map and mask against the
paper before adopting the resulting candidate dataset. The projection and
inclusion rules are documented reconstruction choices.

## Reproduce the dataset EDA

The notebooks inspect configured development periods for daily rainfall, air temperature, modeled surface soil water, annual land cover, and static GHSL built-up surface. The commands below reproduce the legacy source interval; see the bounded refresh guides above for expanded data. Downloaded data and their generated manifests are kept under `data/interim/` and excluded from Git. The scripts reproduce them.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e . --no-deps
.venv/bin/earthengine authenticate
.venv/bin/python scripts/download_chirps_v2.py --project kalimantan-peat --profile legacy --end 2024-12-31
.venv/bin/python scripts/download_era5_land_daily.py --project kalimantan-peat --profile legacy --end 2024-12-31
.venv/bin/python scripts/download_modis_landcover.py --project kalimantan-peat --profile legacy --end 2024-12-31
.venv/bin/python scripts/download_ghsl_built_surface.py --project kalimantan-peat
```

On macOS, XGBoost also needs the OpenMP runtime. If its import reports a missing
`libomp.dylib`, install it with `brew install libomp`, then restart the notebook
kernel.

The project ID must be an Earth Engine-enabled Google Cloud project accessible to your account. Open the CHIRPS, ERA5-Land, MODIS and GHSL notebooks under `EDA/` with the repository root as their working directory after downloading. The CHIRPS, ERA5-Land, and GHSL scripts can resume from checkpoints if interrupted.

The ERA5-Land reconstruction uses daily UTC means of `temperature_2m` and `volumetric_soil_water_layer_1` (0–7 cm). The source paper names ERA5-Land at daily resolution but does not specify its exact bands, depth, daily statistic, or time zone. The preprocessing notebook assigns these values to the 5 × 5 km study cells using its documented nearest-source rule.

The MODIS reconstruction uses annual MCD12Q1 Version 6.1 `LC_Type1` (IGBP) maps, with `QC` and `LW` for quality checks. The paper does not identify its exact MODIS version, classification scheme, or map year. See the [spatial source guide](docs/spatial-refresh.md) for these choices and their limits.

The GHSL EDA uses the 2020 GHS-BUILT-S R2023A built-up surface product. It compares several tentative definitions of a settlement; the paper does not identify its GHSL layer, settlement threshold, or distance algorithm. No exploratory distance has been adopted as the final model feature.

## CHIRPS v3 migration assessment

Ticket #7 is complete: [assessment and reproduction](docs/chirps-v3-migration-assessment.md).
A bounded January–February 2024 comparison acquired both v3 daily variants
separately and compared matched provincial pixel-days against the local v2
archive. Similar accumulated rainfall conceals substantial daily differences.
The current experiment remains v2; a future source switch requires a consistent
all-years rebuild and a separate decision. Ticket #8 has now acquired available data; see the coverage report below for residual blocks.

## Chronological dataset preparation

Ticket #6 replaces fixed-year, order-dependent preparation with one local build
from validated source inventories and declared FIRMS interval evidence:

```bash
.venv/bin/python scripts/prepare_dataset.py --profile expanded --as-of 2026-10-25 --ledger data/raw/Firms/your-export/intervals.json
```

The default expanded preparation creates training 2020–2024 and the fixed
2025 test partition, with preprocessing fitted on training rows only.
The test partition stays closed during development loading and EDA.
Explicit held-out boundaries, when supplied, must lie within 2025.

See [dataset preparation and contract](docs/dataset-preparation.md) for strict
coverage versus explicit exclusions, annual map policies, training-only scaling,
fixed IGBP 1–17 vocabulary, source mappings, build provenance and failure recovery.
Unknown labels and missing features remain in keyed exclusion reports; they do
not silently become negatives or disappear. Coastal fallback limits remain
10 km for CHIRPS and 20 km for ERA5-Land.

Completed artifacts live in the profile/study/build directory and preserve old
`data/interim/model_table/` outputs. `preprocessing.ipynb` retains candidate grid
generation and calls the shared preparation function. `base_models.ipynb`
directly imports `train_2020_2024.parquet` and `test_2025.parquet` from
`data/interim/model_table/`, without fitting or evaluating models. Section 9 of
preprocessing writes these named copies and their build provenance.

The local strict build is complete for training **2020–2024** and test **2025**,
using FIRMS archive requests 814923 and 830638. Named exports contain 6,416,424
training rows and 1,281,880 test rows, with 21 predictors and no excluded cell/days.
A fresh clone must acquire the source files and coverage evidence separately.
`base_models.ipynb` loads both named exports; no model has been trained or scored.
This remains same-day retrospective research; forecast-time availability and
multi-horizon sequences are separate work.

## Actual backfill status — 2026-10-25

[Coverage, verification and remaining blocks](docs/data-refresh-2026-10-25-report.md).
CHIRPS and ERA5-Land contain all 365 days of 2025 plus available 2026 dates;
MODIS contains the 2025 map. Public FIRMS 2025 country data and a recent 2026
NRT snapshot are present with unverified label coverage. All six source EDA
notebooks were regenerated from clean kernels and verified from root and EDA
contexts. Their ordinary plots cover training years 2020–2024; 2025 is the test year
and 2026 is outside the active study. The report describes an earlier blocked attempt. Subsequent archive evidence
has enabled strict publication of the current 2020–2024 train / 2025 test dataset. Bulk data and acquisition evidence remain gitignored.

Current research split (updated 2026-10-25): train 2020–2024, test 2025.
The dated backfill report describes an earlier acquisition snapshot; its
2026 evaluation envelope has been superseded. The missing 2026 annual map
is outside this experiment. The local FIRMS coverage evidence is now recorded.
