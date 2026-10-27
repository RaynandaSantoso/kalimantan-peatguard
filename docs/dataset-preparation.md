# Chronological same-day research datasets

Ticket #6 prepares validated local sources, cell/date joins, exclusion reports, fitted preprocessing and versioned chronological partitions. It does not download sources, train a model, construct forecast sequences or establish operational predictor availability. Follow [first-time setup](setup.md) for environment creation, required BIG files and source acquisition.

## One preparation command

```sh
.venv/bin/python scripts/prepare_dataset.py \
  --profile expanded --as-of 2026-10-25 \
  --ledger data/raw/Firms/your-export/intervals.json
```

The active expanded profile uses **2020–2024 for training and all of 2025 for testing**. There is no validation split and no 2026 study data. Its test calendar is frozen in `config/study.json`, so preparation writes separate train/test partitions by default while fitting preprocessing on training rows only. Default loading opens training only; test preparation does not authorize test analysis or tuning.

Optional `--held-out-start` and `--held-out-end` can explicitly restrict the test export within 2025. Dates after 2025-12-31 are rejected. `--start/--end` bound source dates and are recorded in the contract, so a shorter training request is never described as a full five-year cohort. A 2026 retrieval/as-of timestamp is provenance, not a study observation date. Existing 2026 source files remain stored outside the active experiment.

`--profile legacy` preserves the 2020–2023 training/2024 test reconstruction as a separate experiment; it has no validation role. Optional held-out export boundaries work there too. The refreshed legacy profile applies explicit coverage safety checks; it does not reproduce the old unsupported assumption that every missing hotspot date is a negative label. Preparation never overwrites old versioned builds or the original `train_2020_2023.parquet` / `test_2024.parquet` files. Section 9 refreshes the separate named convenience exports for the selected split.

## Coverage and scientific policies

The default `require_complete` policy refuses to publish a dataset if any requested cell/day has an unknown target, missing continuous feature, absent annual map or invalid category. A failure writes structural `quality.json` and keyed `exclusions.parquet` under the study's `diagnostics/<build-id>/`; a previous dataset remains unchanged. Unknown label days are never silently dropped or filled with zero. A structurally invalid source or checksum mismatch stops before publication.

If deliberately analyzing the eligible subset, explicitly choose `--coverage-policy exclude_ineligible`. The combined table still contains every requested key and its exclusion reasons. Model partitions contain only eligible rows, and quality reports retain requested versus eligible counts and complete common date ranges for each split. This policy can produce a biased subset; it is not evidence of complete source coverage. Observed positives from incomplete FIRMS exports remain positive observations; unsupported negatives remain unknown. NRT coverage is marked provisional. No reported satellite detection is not proof of no fire or cloud-free observation.

The default `--map-policy strict_same_year` preserves retrospective same-year assignment and records missing maps as exclusions/blockers. `--map-policy carry_forward` is an explicit alternative recording actual map year, staleness and unknown historical release evidence. Neither policy makes these research predictors forecast-ready. IGBP categories always use vocabulary 1–17, including water. Missing/invalid categories remain ineligible and their combined-table indicators remain nullable, never a valid all-zero vector.

The grid, all-downloaded-BIG candidate mask, boundary hashes, signed stable cell IDs and 5,000 m lattice must match. Scientific mask approval remains outstanding. MODIS mode aggregation keeps the original full province-grid warp extent. GHSL remains the buffered native 100 m **2020** R2023A proxy: distance from study-cell center to the nearest >0 built-pixel center in study LAEA, excluding nodata. Neither mask choices nor source proxies are changed by adding years.

Weather mapping uses the earliest available requested **training** source period only. CHIRPS keeps the nearest native pixel where its first training year's requested days are all finite; a masked nearest pixel uses the nearest valid pixel within **10 km**. ERA5 keeps the earliest training temperature mask and a **20 km** fallback limit. Later missing values cannot choose a new pixel. Each build saves cell/source indices, training reference date, fallback flag and distance. Source geometry and incoming periods are revalidated before sampling. Mappings are deterministically rebuilt per versioned build, and unchanged sources produce the same mapping; no hidden mutable mapping cache exists. This preserves the legacy mappings for the original complete training source periods while avoiding validation/test information when forming new mappings.

## Build contract and loading

A successful command prints the immutable directory:

`data/interim/builds/<profile>/<study-id>/datasets/<build-id>/`

Its manifest ties together the study profile, exact requested and split dates, explicitly selected held-out interval, source and ledger checksums, grid/boundary hashes, preprocessing code/version, fixed predictor schema/types/order, scientific assumptions, fitted ranges and artifact hashes. A source revision changes build identity. The loader detects a changed active upstream manifest and refuses stale data for a new baseline run. Source files are checked again immediately before publication. Completed builds are published by one directory rename; failed writes clean up only their temporary directory. Repeated identical preparation returns the validated existing build.

Artifacts are `combined.parquet`, keyed `exclusions.parquet`, `source_mappings.csv`, `quality.json`, `scaler.json`, and one eligible `<role>.parquet` per selected split. The combined and exclusion files are research audit artifacts and can contain a deliberately prepared held-out period; do not open them during development if test outcomes were exported. Default baseline loading never reads or hashes those files or the test partition. Held-out structural quality metadata excludes target balance, relationships and scores.

Min/max ranges use **eligible training rows only**. Continuous predictors are rainfall mm/day, UTC mean temperature °C (source remains K), modeled layer-1 0–7 cm soil-water fraction and static built-up distance metres. A constant training feature has an explicit flag and scales to zero in all partitions. Validation/test extremes never change ranges, categories or column order; values are not clipped. Out-of-range counts are recorded separately, including raw deviations for constant features. Keys, target, quality flags, map years and provenance are excluded from predictors.

For simple imports, preprocessing section 9 exports `data/interim/model_table/train_2020_2024.parquet` and `test_2025.parquet`, plus `prepared_dataset.json` identifying the source build, exact dates, row counts and checksums. The copies preserve the prepared predictor schema and training-fitted preprocessing; original 2020–2023/2024 outputs remain unchanged. `base_models.ipynb` reads the named files with pandas and creates `X_train`, `y_train`, `X_test`, and `y_test`. Both partitions are explicitly loaded; no model fitting or test scoring occurs. Use training data only for exploration and model choices. Direct pandas reads bypass the guarded loader and its source-freshness checks, so rerun preparation/export after source changes.

For guarded versioned loading, `load_build(path)` still opens training only. Deliberate test access uses `load_build(path, roles=['test'], open_held_out=True)` and records access in a sibling `held_out_access.jsonl`. This records local API access, not manual reads through pandas.

`preprocessing.ipynb` sections 1–6 retain candidate grid generation. Section 7 runs without environment setup, defaulting to `data/raw/Firms/intervals.json` and the visible `DEFAULT_AS_OF = "2026-10-25"` acquisition snapshot. This cutoff records when data was obtained; the active study still ends on 2025-12-31. Override defaults with `FIRMS_LEDGER`, `PEATGUARD_AS_OF`, and optional `PEATGUARD_PROFILE`, `PEATGUARD_START/END`, `PEATGUARD_MAP_POLICY`, `PEATGUARD_COVERAGE_POLICY`, and both `PEATGUARD_HELD_OUT_START/END`. Section 8 inspects development partitions only; section 9 copies all prepared partitions without inspecting held-out outcomes. When strict coverage blocks preparation, the notebook displays structural exclusions and the saved quality-report path, sets `build_directory = None`, and skips partition inspection. It does not silently reuse an earlier build or relax coverage. Both notebooks resolve paths from root or `EDA/` after editable installation.

## Verification and current local limits

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_datasets.py' -v
```

Fixed-cutoff integration fixtures use real small NPZ/GeoTIFF/CSV source files, declared complete FIRMS intervals, the stable lattice and the normal source validators. They exercise keyed preparation, correct disjoint partitions, missing-day blocking/exclusions, unknown targets, later pixel missingness, fixed category vocabulary, training-only/constant scaling, stale sources, publication failure recovery and default held-out exclusion. No network or credentials are required.

The local 2020–2025 source inputs now support a complete strict dataset build. FIRMS request 814923 supplies training 2020–2024, backed by its original completion email. Request 830638 supplies the 2025 science-quality test detections; a checksummed 2025-only subset preserves the full original 2025/2026 archive and provisional files outside the study. Confirmation/derivation records and bulk files remain under gitignored `data/raw/Firms/`. A fresh clone must acquire its own inputs. No model training or performance claim is made. The dated [historical report](data-refresh-2026-10-25-report.md) describes the earlier blocked attempt, not current local publication status. Forecast-time predictors and t+1…t+10 labels remain separate work.

Verification completed locally: 14 new dataset integration tests, including the CLI and new preprocessing/baseline notebook cells on fixture inputs. Baseline setup executes from both supported directories. Read-only checks against existing sources reproduce the original 3,512-cell weather mapping: two CHIRPS fallbacks (maximum 4,548.99 m) and 69 ERA5 fallbacks (maximum 17,308.37 m). This historical mapping check is separate from the subsequently completed expanded dataset build.

The dated ticket #8 report retains its original acquisition history. Its provisional 2026 test design is superseded by the current 2025 test split; a missing 2026 land-cover map no longer blocks the active study.
