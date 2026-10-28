# Findings and lessons — 2026-10-28

## Completed scope

Kalimantan PeatGuard is a completed retrospective geospatial ML investigation presented in Jupyter notebooks. It covers environmental source acquisition and validation, same-day satellite-detection labels, classical classifiers, a CNN–ConvLSTM reconstruction, and isotonic calibration. Supporting Python modules, acquisition scripts and saved result tables document the experiments.

The acquisition scripts support manual reproduction of bounded research intervals. Historical 2026 downloads are outside the study, which ends in 2025. CHIRPS v2 remains the rainfall source; the separate v3 assessment did not switch the study inputs.

## Data and experiments

The candidate grid contains 3,512 cells at 5 km resolution. The expanded table contains 6,416,424 training cell-days in 2020–2024 (1,009 positive cell-days) and 1,281,880 evaluation cell-days in 2025 (88 positives), with 21 predictors. The target is at least one qualifying MODIS detection with confidence strictly greater than 80 within the candidate mask on the same UTC day. These observations are not independent fire incidents or verified fire-absence measurements.

| Experiment | Model fitting | Model selection | Scoring |
| --- | --- | --- | --- |
| Expanded classical baselines | 2020–2024 | Fixed configurations; no reported test-based tuning | 2025 |
| Earlier-year classical comparison | 2020–2023 | Fixed configurations | 2024 |
| Saved ConvLSTM | Jan 2020–Sep 2023 | Oct–Dec 2023 validation; selected epoch 23, stopped at 43 | 2024 |
| Exploratory isotonic calibration | Fixed earlier-year models; new mappings fitted Jan–Jun 2024 | No replacement network/checkpoint selected | Jul–Dec 2024 |

The ConvLSTM was not refitted on all of 2020–2023. Its fitting cohort therefore differs from the classical comparison, and its spatial sequences contain context absent from the tabular inputs. Both 2024 and 2025 outcomes were previously inspected. Fixed dataset calendars and guarded loading do not make those observations untouched final tests again.

## Recorded results

### Expanded classical baselines

The [baseline notebook](../base_models.ipynb), section 2, records these 2025 results:

| Model | ROC–AUC | Brier error, training-frequency constant = 100 | Actual alerts near target 1,000 | Positive cell-days found | False alarms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Logistic Regression | 0.773 | 100.02 | 1,000 | 0 | 1,000 |
| LightGBM | 0.808 | 590.55 | 1,009 | 9 | 1,000 |
| XGBoost | 0.637 | 121.58 | 1,000 | 10 | 990 |
| Random Forest | 0.847 | 103.13 | 1,000 | 5 | 995 |

Random Forest led ROC–AUC; XGBoost led average precision. None beat the constant on Brier error, and none found a positive at the fixed 0.5 threshold. Ranking metrics and detection counts describe different aspects of the scores. The alert targets are descriptive ranks over an entire evaluation year, including ties, not operational daily budgets.

### Saved ConvLSTM

The [recorded raw evaluation](../results/conv_lstm/validation_2023q4_evaluation_2024/raw_metrics.json) covers 1,285,392 cell-days in 2024, including 70 positives. ROC–AUC was 0.878043 and average precision was 0.002238, about 41.1 times evaluation prevalence. Raw Brier was 0.00854633 against 0.0000544604 for the fitting-frequency constant: approximately 157 times its error.

At score ≥0.5, the model found 27 positives, missed 43 and produced 9,201 false alarms. The descriptive highest 10,000 scores found 28 positives with 9,972 false alarms. Ranking signal did not establish dependable alerts or reliable raw probabilities. These results cannot isolate an architecture effect against differently fitted classical models.

### Isotonic calibration

The ConvLSTM mapping was fitted on 639,184 January–June 2024 cell-days with only **nine positives**. Evaluation used 646,208 July–December cell-days with 61 positives. The [calibration table](../results/conv_lstm/validation_2023q4_evaluation_2024/calibration_metrics.csv) reports:

| Version | July–December Brier | ROC–AUC | Average precision |
| --- | ---: | ---: | ---: |
| Raw | 0.0124778301 | 0.860607 | 0.002492 |
| Isotonic | 0.0000942723 | 0.841654 | 0.002069 |
| Training-frequency constant | 0.0000943890 | 0.500000 | 0.0000944 |
| Calibration-frequency constant | 0.0000943944 | 0.500000 | 0.0000944 |

Isotonic substantially reduced raw probability error, but the gain over the calibration-frequency constant was only 0.129%; no uncertainty estimate establishes a robust benefit. ROC–AUC and average precision decreased as scores merged into ties. At 0.5 the calibrated version issued no alerts. Its descriptive 10,000-alert target produced 14,850 alerts and found 32 positives, so that count is not an equal-budget improvement over the raw version's 27 positives in 10,000 alerts.

The classical calibration experiment in baseline section 5 also produced probability errors close to the constant baseline and decreased average precision. Its mappings belong to the 2020–2023 classical fits, not to the expanded 2020–2024 fits or the ConvLSTM.

## Lessons

- **Coverage is part of the target definition.** A cell-day without a recorded hotspot can receive a negative label only when retrieval coverage is supported; even then it is not verified fire absence. Ledger evidence and keyed exclusions matter as much as the classifier.
- **Spatial and temporal contracts matter.** Coordinate systems, stable cell IDs, calendar alignment, fixed category vocabularies and fitting-only preprocessing prevent plausible-looking but mismatched inputs.
- **Different metrics answer different questions.** ROC–AUC can show ranking signal while probability error and false-alarm counts remain poor. Average precision relative to prevalence is not alert precision.
- **Calibration needs a meaningful baseline.** A dramatic raw-to-calibrated Brier decrease can largely reflect correcting probability scale. The constant-frequency comparisons reveal the small remaining gain here.
- **Large tables do not guarantee ample event evidence.** Positive cell-days are sparse and dependent, and annual counts vary sharply: 815 of the 1,009 training positives occurred in 2023. Nine calibration positives support only an exploratory conclusion.
- **Reconstruction requires explicit assumptions.** The paper leaves parts of preprocessing, timing and calibration unspecified. The implementation records its choices rather than claiming identical inputs or exact numerical reproduction.

## Limits and closure

The all-downloaded-BIG peatland mask remains a candidate; its scientific classification has not been independently approved. Weather and land-cover assignments are retrospective proxies. No issue-time predictor archive or future-day target experiment validates forecasting capability. The paper's reported results use unresolved cohort/calibration details, and the local experiments differ in training support and inputs.

The recorded neural run was interrupted during epoch 2 and resumed from partial-epoch weights, repeating some batches and restarting early-stopping patience. Its history describes that actual run; a continuous rerun need not reproduce it exactly. The reported evaluation uses the validation-selected checkpoint without a full-year refit.

The completed contribution is the documented pipeline, experiments and evaluation lessons. The findings do not establish that the data are unusable, that one architecture is intrinsically superior, or that a deployable warning system has been validated.

For reproduction limits, see [setup](setup.md). For exact neural configuration and provenance, see [the reconstruction specification](conv-lstm-replication.md) and [saved results](../results/conv_lstm/validation_2023q4_evaluation_2024/README.md). Bulk data, weights and keyed prediction files are local artifacts excluded from Git. The small result tables can be read without restoring them.
