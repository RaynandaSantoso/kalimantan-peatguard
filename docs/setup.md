# First-time setup and data acquisition

The repository contains code and notebooks, not the bulk research data. `data/raw/` and `data/interim/` are excluded from Git. A fresh clone must acquire the sources and regenerate derived files. There is no single command that restores every input, and no automatic daily download schedule.

The active `expanded` profile covers **2020-01-01 through 2025-12-31**: training 2020–2024, held-out test 2025, no validation split. `legacy` uses training 2020–2023 and test 2024. A 2026 acquisition timestamp does not add 2026 observations to either study. `--end latest` stops at the profile end or acquisition cutoff, whichever is earlier.

## 1. Create the environment

From the cloned repository root, with Python 3.10 or later:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e . --no-deps
.venv/bin/python -m ipykernel install --user --name kalimantan-peatguard --display-name "Kalimantan PeatGuard"
```

Select that kernel in your notebook editor. For baseline notebooks that import the optional modeling libraries, also install:

```sh
.venv/bin/python -m pip install scikit-learn xgboost seaborn
```

On macOS, an XGBoost import error about `libomp.dylib` requires the OpenMP runtime (`brew install libomp`).

The small, credential-free fixtures can be checked before downloading real data:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

## 2. Supply the BIG geographic inputs

The current repository has no acquisition script for these two required files:

- `data/raw/boundaries/big_kalimantan_provinces.geojson`
- `data/raw/peatland/big_peta_lahan_gambut_kalimantan.geojson`

Obtain the original BIG province and peatland exports from the project maintainer, including their provenance and permission to redistribute, or reproduce those exports from the original provider. An exact public download procedure is not yet documented. Arbitrary replacement polygons will change the study mask, grid and downstream results. This remains a fresh-clone reproducibility prerequisite; installing Python packages will not create these inputs.

Run sections 1–6 of `preprocessing.ipynb` to generate the candidate 5 km grid and manifest under `data/interim/study_grid_5km/`. Section 7 requires the remaining data and FIRMS ledger, so run it after acquisition. The candidate peatland mask still requires scientific review.

## 3. Download weather and spatial sources

Use your own Earth Engine access and enabled project. Authenticate locally:

```sh
.venv/bin/earthengine authenticate
```

Replace `YOUR_PROJECT` below with your project ID. These commands request the full active history, including the 2025 test-year source files; default development EDA still uses training dates only.

```sh
.venv/bin/python scripts/download_chirps_v2.py --project YOUR_PROJECT --profile expanded --start 2020-01-01 --end 2025-12-31 --as-of 2026-10-25
.venv/bin/python scripts/download_era5_land_daily.py --project YOUR_PROJECT --profile expanded --start 2020-01-01 --end 2025-12-31 --as-of 2026-10-25
.venv/bin/python scripts/download_modis_landcover.py --project YOUR_PROJECT --profile expanded --start 2020-01-01 --end 2025-12-31 --as-of 2026-10-25 --include-held-out
.venv/bin/python scripts/download_ghsl_built_surface.py --project YOUR_PROJECT
```

The cutoff above is the documented acquisition snapshot. For a new run, use its actual cutoff consistently and retain the generated retrieval timestamps; the scripts query current provider assets, not an archived catalog. Data published or revised since the original run can produce different hashes.

CHIRPS and ERA5-Land download available daily history; MODIS downloads available annual maps; GHSL downloads a static 2020 built-up layer. A first run acquires missing historical files. Later explicit runs reuse validated files and acquire missing dates, with separate flags for weather revisions. No old weather data needs to be downloaded manually merely because it is historical. Requested dates outside the configured study are rejected; extending to a later study requires a separate configuration decision.

See the [CHIRPS](chirps-refresh.md), [ERA5-Land](era5-land-refresh.md) and [spatial](spatial-refresh.md) guides for discovery, resume and validation details. Weather downloads can be large and take time; inspect a small request first.

## 4. Acquire FIRMS detections and coverage evidence

FIRMS has two acquisition routes:

- `scripts/fetch_firms.py` downloads supported date intervals with your free `FIRMS_MAP_KEY`, checks runtime API availability and creates checksummed CSVs plus an interval ledger. Store the key only in your local environment.
- If the API rejects historical dates, request MODIS Collection 6.1 CSV exports through the archive route described in the [FIRMS guide](firms-refresh.md). Retain the requested inclusive dates, bounding rectangle, processing stage and export confirmation, then create the documented ledger. `scripts/import_firms.py` processes local data; it does not obtain an archive export for you.

Cover the full 2020–2025 interval and the entire candidate study mask. Multiple exports/API acquisitions must be represented together in a ledger; their file paths resolve relative to that ledger. Public country CSVs and recent rolling snapshots are not automatically accepted as complete study-wide coverage. The existing maintainer archive also lacks verified original coverage evidence, so copying its detections alone does not establish valid negative labels.

The current full-dataset build remains blocked until that coverage is verified. Unknown retrieval coverage must not become a zero-fire label. See the FIRMS guide for the ledger schema, checksum calculation requirements, import command and separate held-out access.

## 5. Prepare the dataset and inspect development data

After the BIG grid, validated source inventories and full FIRMS evidence exist:

```sh
.venv/bin/python scripts/prepare_dataset.py --profile expanded --as-of 2026-10-25 --ledger data/raw/Firms/intervals.json
```

Replace the ledger path with your actual combined ledger and use a cutoff consistent with its retrieval evidence. The command prepares separate train/test partitions, fits preprocessing on training only, and prints the immutable build directory. Strict coverage failures produce diagnostics rather than a complete dataset. See [dataset preparation](dataset-preparation.md) for details.

Set `PEATGUARD_BUILD` to that exact directory before running `baseline_models.ipynb`. The notebook currently loads and validates the training dataset; it does not train or evaluate models. Source EDA notebooks require their corresponding inputs; FIRMS EDA accepts `FIRMS_LEDGER` for your ledger path. Default EDA excludes the 2025 test outcomes.

The [dated acquisition report](data-refresh-2026-10-25-report.md) preserves an older experiment and local verification history. Its provisional 2026 commands are superseded by this guide. Bulk inputs, generated datasets and local audit bundles referenced there are not included in a clone.
