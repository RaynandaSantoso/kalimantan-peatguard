# First-time setup and research reproduction

The research experiments concluded on 2026-10-28; see [findings and final scope](findings.md). These instructions reproduce the completed notebook experiments and their supporting data workflows.

The repository contains code and notebooks, not the bulk research data. `data/raw/` and `data/interim/` are excluded from Git. A fresh clone must acquire the sources and regenerate derived files. There is no single command that restores every input, and no automatic daily download schedule.

The retained `expanded` dataset profile covers **2020-01-01 through 2025-12-31**: training 2020–2024, held-out test 2025, no separate dataset validation partition. `legacy` uses training 2020–2023 and test 2024. A 2026 acquisition timestamp does not add 2026 observations to either study. `--end latest` stops at the profile end or acquisition cutoff, whichever is earlier.

## 1. Create the environment

From the cloned repository root, with Python 3.10 or later:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e . --no-deps
.venv/bin/python -m ipykernel install --user --name kalimantan-peatguard --display-name "Kalimantan PeatGuard"
```

Select that kernel in your notebook editor. The requirements include LightGBM for the baseline notebook.

For neural training/inference, also install `.venv/bin/python -m pip install -r requirements-conv-lstm.txt`. Installing only the local package does not install all notebook dependencies.

On macOS, an XGBoost import error about `libomp.dylib` requires the OpenMP runtime (`brew install libomp`).

The small, credential-free fixtures can be checked before downloading real data:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The last full local run on 2026-10-28 ran 100 tests: 99 passed and one dataset/notebook integration test errored because it executes a Markdown cell after the baseline notebook layout changed. Neural checks require TensorFlow. This is a known verification limitation, not a claim of a clean suite.

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

Cover the full 2020–2025 interval and the entire candidate study mask. Multiple exports/API acquisitions must be represented together in a ledger; their file paths resolve relative to that ledger. Public country CSVs and recent rolling snapshots are not automatically accepted as complete study-wide coverage. The maintainer has now supplied the original 2020–2024 confirmation (request 814923) and the matching 2025 archive from request 830638. These local files and their provenance are excluded from Git; a fresh clone still needs its own inputs and evidence.

A full-dataset build requires that coverage to be verified. Unknown retrieval coverage must not become a zero-fire label. See the FIRMS guide for the ledger schema, checksum calculation requirements, import command and separate held-out access.

## 5. Prepare the dataset and inspect development data

After the BIG grid, validated source inventories and full FIRMS evidence exist:

```sh
.venv/bin/python scripts/prepare_dataset.py --profile expanded --as-of 2026-10-25 --ledger data/raw/Firms/intervals.json
```

Replace the ledger path with your actual combined ledger and use a cutoff consistent with its retrieval evidence. The command prepares separate train/test partitions, fits preprocessing on training only, and prints the immutable build directory. Strict coverage failures produce diagnostics rather than a complete dataset. See [dataset preparation](dataset-preparation.md) for details.

For simple imports, run `preprocessing.ipynb` through section 9. It copies the prepared partitions to `data/interim/model_table/train_2020_2024.parquet` and `test_2025.parquet`, with build provenance in `prepared_dataset.json`. `base_models.ipynb` imports those named files directly using pandas and separates predictors from labels. Its setup cells load both train and test; later sections fit and evaluate Logistic Regression, LightGBM, XGBoost and Random Forest. Section 2 fits 2020–2024 and evaluates 2025; sections 4–5 use separate 2020–2023 models for 2024 evaluation/calibration. Both evaluation years have already been inspected. Default source EDA still uses training only. The direct pandas imports do not perform the versioned loader’s source-freshness checks; regenerate exports after changing inputs. Source EDA notebooks require their corresponding inputs; FIRMS EDA accepts `FIRMS_LEDGER` for your ledger path. Default EDA excludes the 2025 test outcomes.

The [dated acquisition report](data-refresh-2026-10-25-report.md) preserves an older experiment and local verification history. Its provisional 2026 commands are superseded by this guide. Bulk inputs, generated datasets and local audit bundles referenced there are not included in a clone.

## 6. Inspect the completed model experiments

Read [the findings](findings.md) and the saved outputs in `base_models.ipynb` and `cnn_conv_lstm.ipynb` before running model cells. Classical model cells perform training; they are not a read-only results viewer.

The ConvLSTM notebook defaults to `RUN_MODE = "saved"`. Its configured local run requires the September-2023-fitted arrays, source/build metadata, epoch history and selected checkpoint under `data/interim/conv_lstm/20261027T155222671757Z/`. Saved mode does not train; section 6 repeats inference. A fresh clone does not include these artifacts. `RUN_MODE = "new"` deliberately prepares and trains a separate run after restoring the required sources; it does not reconstruct the interrupted run's exact history. No full-year refit is performed.

Section 7 enables isotonic calibration by default. It can also run independently from the saved raw predictions and evaluation record, with matching checkpoint/scaler files, without loading TensorFlow or repeating network inference. It fits January–June 2024 and scores July–December only. Set `RUN_DIR` explicitly for another run. See [the specification](conv-lstm-replication.md) for safeguards and output files.

Small ConvLSTM result tables, history and provenance are readable under `results/conv_lstm/validation_2023q4_evaluation_2024/` without model/data restoration. The recorded raw-stage no-calibration status and training summary describe those original stages; `calibration_spec.json` records the completed later calibration experiment.
