# Kalimantan PeatGuard

This learning project reconstructs the CNN–ConvLSTM fire-risk methodology described by Mahdiyasa et al. (2026), compares it with classical ML baselines, and evaluates isotonic probability calibration.

Concerned by peatland fires in Kalimantan, Indonesia, I wanted to explore how machine learning could help us understand fire risk. I came across a research paper on the topic and decided to learn by reconstructing its methodology. This project gave me an opportunity to work with real environmental data, compare models, and understand their practical limitations.

**This project is for learning purposes only.**

LLM assistance: I used large language models extensively while developing and learning from this project.

## What was built

- Source acquisition, validation and six EDA notebooks covering BIG geography, FIRMS detections, CHIRPS rainfall, ERA5-Land weather, MODIS land cover, and GHSL built-up surface.
- A stable 5,000 m lattice in a local equal-area projection, with 3,512 candidate study cells and explicit boundary/overlap rules.
- Keyed cell/day datasets, source coverage evidence, training-fitted preprocessing, exclusion reports, checksums and versioned build manifests.
- Logistic Regression, LightGBM, XGBoost and Random Forest baselines, plus a seven-day spatial-sequence ConvLSTM reconstruction.
- Evaluation of ranking, Brier error, detection/false-alarm counts and isotonic probability calibration on separate fitting/scoring cohorts.

The local expanded exports contain **6,416,424 training rows (2020–2024)** and **1,281,880 evaluation rows (2025)**, with 21 predictors. Rows are cell-days, not independent fire events. Bulk sources, model tables, prepared neural arrays and checkpoints are excluded from Git.

## Main findings

| Experiment | Recorded finding |
| --- | --- |
| Classical models: fit 2020–2024, evaluate 2025 | Random Forest had the highest ROC–AUC (0.847); XGBoost had the highest average precision. None beat the training-frequency constant on Brier error. |
| Saved ConvLSTM: fit Jan 2020–Sep 2023, select on Oct–Dec 2023, evaluate 2024 | ROC–AUC 0.8780 and average precision 0.002238. At score ≥0.5, it found 27 of 70 positive cell-days with 9,201 false alarms. |
| ConvLSTM isotonic: fit Jan–Jun 2024, evaluate Jul–Dec 2024 | Brier fell from 0.0124778301 to 0.0000942723, only 0.129% below the calibration-frequency constant. ROC–AUC and average precision decreased. Only nine positives supported fitting. |

These experiments use different training/evaluation cohorts and inputs; they do not establish a controlled architecture superiority result. Both 2024 and 2025 outcomes were inspected, so neither should be presented as a new untouched final test. The large raw-to-calibrated Brier improvement does not establish useful alerts or a robust improvement over a constant prediction.

Read [findings and lessons](docs/findings.md), [the baseline notebook](base_models.ipynb), [the ConvLSTM notebook](cnn_conv_lstm.ipynb), and [the saved ConvLSTM results](results/conv_lstm/validation_2023q4_evaluation_2024/README.md).

## Reproduction and data access

Start with [environment setup and data acquisition](docs/setup.md). A fresh clone needs Earth Engine access, the original BIG geographic inputs or equivalent documented exports, and FIRMS detections with verified export-interval evidence. There is no single command that restores every source or reproduces the original provider snapshot.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e . --no-deps
```

Neural training/inference additionally requires `requirements-conv-lstm.txt`. Saved-mode ConvLSTM evaluation requires the local checkpoint and prepared arrays; a fresh clone does not include them. The standalone calibration cell requires the saved raw predictions, evaluation record and matching checkpoint/scaler, but does not load TensorFlow or retrain the network.

After acquiring the inputs, run `preprocessing.ipynb` through section 9 or use the [dataset preparation workflow](docs/dataset-preparation.md). Run notebook experiments deliberately: the baseline notebook fits models, while the ConvLSTM notebook defaults to loading its saved run. Raw full-year scores and later-cohort calibration scores have separate scoring support.

| Guide | Purpose |
| --- | --- |
| [Setup](docs/setup.md) | Environment, source prerequisites and notebook execution |
| [Dataset preparation](docs/dataset-preparation.md) | Coverage, keyed joins, scaling, partitions and build provenance |
| [CHIRPS](docs/chirps-refresh.md) | Manual bounded rainfall acquisition, resume and revisions |
| [ERA5-Land](docs/era5-land-refresh.md) | Manual UTC weather acquisition and development EDA |
| [FIRMS](docs/firms-refresh.md) | Coverage ledgers, normalization and nullable labels |
| [Spatial sources](docs/spatial-refresh.md) | Annual land cover and the static settlement-distance proxy |
| [ConvLSTM reconstruction](docs/conv-lstm-replication.md) | Architecture, split, checkpoint, calibration and assumptions |
| [CHIRPS v3 assessment](docs/chirps-v3-migration-assessment.md) | Completed bounded comparison; v2 retained for this study |
| [Historical acquisition report](docs/data-refresh-2026-10-25-report.md) | Dated backfill evidence and superseded 2026 design |

## Study configuration and local inventory

`config/study.json` retains two dataset profiles:

| Profile | Training partition | Test partition |
| --- | --- | --- |
| `legacy` | 2020–2023 | 2024 |
| `expanded` | 2020–2024 | 2025 |

These are dataset partition definitions. The ConvLSTM notebook makes its own documented training/validation split within the earlier years. The fixed 2025 test calendar remains in configuration; its outcomes have already been evaluated by the classical baseline notebook. Default source EDA and guarded loading still expose training only. The baseline notebook explicitly reads both named partitions.

The study ends on **2025-12-31**. Downloaded 2026 source files are retained historical artifacts outside the completed experiment. A 2026 retrieval timestamp does not put 2026 observations into the study.

```sh
.venv/bin/python -m peatguard --profile expanded --as-of 2026-10-25
```

The default inventory command is local and read-only: it checks existing source files and prints a JSON report without fetching data, preparing labels, training, or updating manifests. Its refresh plan is diagnostic output, not an executing scheduler. `--as-of` bounds source-day UTC dates; it does not recreate an archived provider catalog. Optional `--discover --project YOUR_PROJECT` queries current Earth Engine asset dates. First/last FIRMS detections alone do not prove retrieval coverage; the separate ingestion/preparation workflow consumes interval ledgers.

## Validation and limitations

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The suite includes credential-free fixtures for source validation, refresh recovery, geographic alignment, dataset leakage boundaries, neural smoke checks and standalone calibration. Neural checks require TensorFlow. The last full local run on 2026-10-28 ran 100 tests: 99 passed and one existing dataset/notebook integration test errored because it executes a Markdown cell after a baseline notebook layout change. This documentation update does not repair that test or claim a clean suite.

The BIG peatland mask remains a candidate pending scientific review. Source proxies, same-year annual maps, seven-day sequence length, same-day timing, loss and training choices limit exact reproduction of the paper. Satellite non-detection does not prove fire absence. Sparse positives, spatial/temporal dependence, seasonal differences and previously viewed evaluation outcomes limit generalization claims. Detailed evidence and interpretation are in [the findings report](docs/findings.md).

## Reference

Mahdiyasa et al., *A Calibrated Spatio-Temporal Machine Learning Model for Tropical Peatland Fire Risk*, DOI [10.1016/j.nhres.2026.07.007](https://doi.org/10.1016/j.nhres.2026.07.007). The local paper is `papers/mahdiyasa2026.pdf`. This repository documents a methodological reconstruction and its deviations; it does not claim exact numerical reproduction.