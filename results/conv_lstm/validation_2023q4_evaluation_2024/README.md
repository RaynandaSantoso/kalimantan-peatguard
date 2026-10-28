# Recorded CNN–ConvLSTM experiment

The research experiment is complete. See [the project findings and final scope](../../../docs/findings.md). These files document the completed notebook experiments and their saved results.

These small files preserve the recorded results without requiring local model/data artifacts. Training and scaler fitting used January 2020–September 2023. October–December 2023 was used for validation. The selected model was evaluated on all of 2024, with no full-year refit. The raw full-year evaluation is preserved, followed by a separate January–June calibration / July–December evaluation experiment. This differs from the paper's full 2020–2023 training cohort.

- `training_history.csv`: all 43 completed epochs, combining the initial and resumed fits. The epoch column is zero-based; index 22 means displayed epoch 23.
- `training_summary.json`: selected epoch, loss and experiment split. Its `calibration_fitted: false` records the original training/raw-evaluation stage; the later completed calibration is recorded separately in `calibration_spec.json`. The actual run resumed after a manual interruption, repeating part of epoch 2 and resetting early-stopping patience.
- `raw_metrics.json`, `raw_alert_counts.csv`, `coverage.csv`: raw-model evaluation and counts on eligible 2024 cell-days.
- `evaluation_spec.json`: source identity, checkpoint/scaler fingerprints, versions and split settings. Paths are relative to the project root and refer to local artifacts that are not included here.

The selected epoch was 23; early stopping ended training at epoch 43. ROC–AUC was 0.8780. At the fixed 0.5 threshold, 27 of 70 positive cell-days were found, with 9,201 false alarms. Raw Brier error was about 157 times the training-frequency constant's error. Ranking signal does not establish calibrated probabilities or operational alert usefulness. A false alarm denotes no qualifying recorded detection, not verified absence of fire. Both 2024 and 2025 outcomes had been inspected previously.

The main notebook is `cnn_conv_lstm.ipynb`; methodological details are in `docs/conv-lstm-replication.md`. Large prepared arrays, predictions and model weights remain under gitignored `data/interim/`. Committing this results folder preserves these tables and summaries; it does not make the model checkpoint or full dataset available.

## Isotonic calibration — 2026-10-28

- `calibration_metrics.csv`: raw/isotonic scores and training/calibration frequency constants on exactly the same July–December 2024 cohort.
- `calibration_alert_counts.csv`: fixed-threshold and descriptive alert-target counts, including ties.
- `calibration_spec.json`: fitting/evaluation intervals, counts, runtime versions, original checkpoint/scaler fingerprints, and hashes of input predictions and output artifacts. The referenced mapping and keyed predictions remain in the gitignored run directory.

Calibration used 639,184 January–June cell-days (9 positives); evaluation used 646,208 July–December cell-days (61 positives). Brier fell from 0.0124778301 raw to 0.0000942723 isotonic. The calibration-frequency constant scored 0.0000943944, so the isotonic gain over that constant was only 0.129%. ROC–AUC decreased from 0.860607 to 0.841654; average precision decreased from 0.002492 to 0.002069. At score ≥0.5 the calibrated version issued no alerts. Its descriptive 10,000-alert target produced 14,850 alerts, so detection counts cannot be compared as an equal-budget improvement. Few calibration positives, seasonal differences and previously viewed outcomes limit the result.

Section 7 of the notebook is enabled by default and can run independently from saved raw predictions without retraining or loading TensorFlow. The original `evaluation_spec.json` records only the raw stage; `calibration_spec.json` records completion of the later calibration stage.
