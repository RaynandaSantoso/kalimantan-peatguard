"""Disk-backed spatial sequences for the documented same-day reconstruction.

TensorFlow is imported only by model/training functions. See
``docs/conv-lstm-replication.md`` for reported settings versus assumptions.
"""
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .datasets import CONTINUOUS, PREDICTORS


@dataclass
class SpatialStore:
    dates: pd.DatetimeIndex
    cells: pd.DataFrame
    features: np.ndarray
    observed: np.ndarray
    labels: np.ndarray
    scaling: dict

    def __post_init__(self):
        self.cells = self.cells.reset_index(drop=True).copy()
        if self.cells.empty or self.cells.cell_id.duplicated().any():
            raise ValueError('Grid must contain unique study cells.')
        parsed = self.cells.cell_id.str.extract(r'^kp5_c(-?\d+)_r(-?\d+)$')
        if parsed.isna().any().any():
            raise ValueError('Invalid stable cell ID.')
        addresses = parsed.astype(int).to_numpy()
        if not np.array_equal(addresses, self.cells[['grid_col', 'grid_row']].to_numpy()):
            raise ValueError('Cell ID and lattice metadata disagree.')
        if self.cells.duplicated(['grid_col', 'grid_row']).any():
            raise ValueError('Duplicate spatial addresses.')
        if not self.dates.equals(pd.date_range(self.dates.min(), self.dates.max(), freq='D')):
            raise ValueError('Store calendar must contain consecutive daily positions.')
        self.rows = (self.cells.grid_row.max() - self.cells.grid_row).to_numpy()
        self.cols = (self.cells.grid_col - self.cells.grid_col.min()).to_numpy()
        self.height, self.width = int(self.rows.max()+1), int(self.cols.max()+1)
        self.domain = np.zeros((self.height, self.width), bool)
        self.domain[self.rows, self.cols] = True

    def valid_cells(self, target, length):
        if length < 1 or target < length-1 or target >= len(self.dates):
            raise ValueError('Sequence is outside the available calendar.')
        return self.observed[target-length+1:target+1].all(axis=0) & (self.labels[target] >= 0)

    def targets(self, start, end, length=7):
        indices = np.flatnonzero((self.dates >= start) & (self.dates <= end))
        return np.array([i for i in indices if i >= length-1 and self.valid_cells(i, length).any()], dtype=int)

    def batch(self, targets, length=7):
        """Channels-last input; packed output holds [label, supervision mask]."""
        targets = np.asarray(targets, dtype=int)
        x = np.zeros((len(targets), length, self.height, self.width, len(PREDICTORS)+2), np.float32)
        y = np.zeros((len(targets), self.height, self.width, 2), np.float32)
        x[..., -2] = self.domain
        for b, target in enumerate(targets):
            valid = self.valid_cells(target, length)
            for step, day in enumerate(range(target-length+1, target+1)):
                known = self.observed[day]
                x[b, step, self.rows, self.cols, :len(PREDICTORS)] = np.where(
                    known[:, None], self.features[day], 0.)
                x[b, step, self.rows, self.cols, -1] = known
            y[b, self.rows, self.cols, 0] = np.maximum(self.labels[target], 0)
            y[b, self.rows, self.cols, 1] = valid
        return x, y


def prepare_store(table_path, cells, source_scaler, output_dir, *,
                  fit_end='2023-12-31', start='2020-01-01', end='2024-12-31'):
    """Two bounded-memory passes; invert export scaling, then refit training only.

    Missing cell-days retain observed=False and label=-1. Output directories must
    be new, so a failed or different reconstruction is never reused silently.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    dates = pd.date_range(start, end, freq='D')
    cells = cells.sort_values('cell_id').reset_index(drop=True)
    cell_index = pd.Index(cells.cell_id)
    if not (dates.min() <= pd.Timestamp(fit_end) <= dates.max()):
        raise ValueError('Scaling fit boundary outside calendar.')
    old_min = np.array([source_scaler[c]['min'] for c in CONTINUOUS])
    old_span = np.array([source_scaler[c]['max']-source_scaler[c]['min'] for c in CONTINUOUS])
    if not np.isfinite(old_min).all() or not np.isfinite(old_span).all() or (old_span < 0).any():
        raise ValueError('Invalid source scaling ranges.')
    for c, span in zip(CONTINUOUS, old_span):
        if bool(source_scaler[c]['constant']) != (span == 0):
            raise ValueError('Source constant flag disagrees with range.')
    parquet = pq.ParquetFile(table_path)
    columns = ['cell_id', 'acq_date', 'fire_detected'] + PREDICTORS
    low, high = np.full(4, np.inf), np.full(4, -np.inf)

    def chunks():
        for batch in parquet.iter_batches(batch_size=65536, columns=columns):
            frame = batch.to_pandas()
            day = pd.to_datetime(frame.acq_date, errors='raise')
            if day.isna().any() or not day.between(start, end).all() or not day.eq(day.dt.normalize()).all():
                raise ValueError('Unexpected export date.')
            raw = frame[PREDICTORS[:4]].to_numpy(dtype=float) * old_span + old_min
            yield frame, day, raw

    for frame, day, raw in chunks():
        fit = (day <= fit_end).to_numpy()
        values = raw[fit]
        if len(values):
            if not np.isfinite(values).all():
                raise ValueError('Nonfinite training predictors in eligible export.')
            low = np.minimum(low, values.min(axis=0))
            high = np.maximum(high, values.max(axis=0))
    if not np.isfinite(low).all():
        raise ValueError('No training observations for scaling.')
    span = high-low
    scaling = {c: {'min': float(a), 'max': float(b), 'constant': bool(a == b)}
               for c, a, b in zip(CONTINUOUS, low, high)}
    shape = (len(dates), len(cells))
    features = np.lib.format.open_memmap(output_dir/'features.npy', mode='w+', dtype='float32', shape=(*shape, len(PREDICTORS)))
    observed = np.lib.format.open_memmap(output_dir/'observed.npy', mode='w+', dtype='bool', shape=shape)
    labels = np.lib.format.open_memmap(output_dir/'labels.npy', mode='w+', dtype='int8', shape=shape)
    features[:] = 0; observed[:] = False; labels[:] = -1
    seen = np.zeros(shape, bool)
    for frame, day, raw in chunks():
        d = dates.get_indexer(day)
        c = cell_index.get_indexer(frame.cell_id)
        if (c < 0).any():
            raise ValueError('Export contains cells outside the candidate study grid.')
        flat = d*len(cells)+c
        if len(np.unique(flat)) != len(flat) or seen[d, c].any():
            raise ValueError('Duplicate cell/date key.')
        seen[d, c] = True
        target = frame.fire_detected.to_numpy(dtype=float, na_value=np.nan)
        known_target = np.isfinite(target)
        if not np.isin(target[known_target], [0, 1]).all():
            raise ValueError('Nonbinary observed label.')
        onehot = frame[PREDICTORS[4:]].to_numpy(dtype=float)
        if not (np.isin(onehot, [0, 1]).all() and (onehot.sum(axis=1) == 1).all()):
            raise ValueError('Land-cover vocabulary must be one-hot 1–17.')
        continuous = (raw-low)/np.where(span == 0, 1., span)
        continuous[:, span == 0] = 0.
        values = np.column_stack([continuous, onehot])
        known_features = np.isfinite(raw).all(axis=1) & np.isfinite(values).all(axis=1)
        features[d, c] = np.where(known_features[:, None], values, 0.)
        observed[d, c] = known_features
        labels[d[known_target], c[known_target]] = target[known_target].astype(np.int8)
    for array in (features, observed, labels):
        array.flush()
    (output_dir/'scaling.json').write_text(json.dumps({
        'fit_start': start, 'fit_end': fit_end, 'source_ranges': source_scaler,
        'fitted_ranges': scaling, 'predictors': PREDICTORS,
    }, indent=2)+'\n')
    return SpatialStore(dates, cells, features, observed, labels, scaling)


def build_model(length=7, channels=23, positive_weight=75., learning_rate=0.001, *, spatial_shape):
    """Published four-layer order; explicit reconstruction defaults."""
    import tensorflow as tf
    layers = tf.keras.layers
    model = tf.keras.Sequential([
        layers.Input(shape=(length, *spatial_shape, channels)),
        layers.ConvLSTM2D(32, 3, padding='same', activation='tanh',
                          recurrent_activation='sigmoid', return_sequences=False),
        layers.BatchNormalization(), layers.Dropout(0.30),
        layers.Conv2D(64, 3, padding='same', activation='relu'),
        layers.BatchNormalization(), layers.Dropout(0.30),
        layers.Conv2D(32, 3, padding='same', activation='relu'),
        layers.BatchNormalization(),
        layers.Conv2D(1, 1, padding='same', activation='sigmoid'),
    ], name='mahdiyasa_same_day_reconstruction')
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate),
                  loss=masked_weighted_bce(positive_weight))
    return model


def masked_weighted_bce(positive_weight=75.):
    """Eq. (2), normalized by valid count, not background or weight sum."""
    import tensorflow as tf

    def loss(packed, predicted):
        y, mask = packed[..., :1], packed[..., 1:2]
        p = tf.clip_by_value(predicted, tf.keras.backend.epsilon(), 1.-tf.keras.backend.epsilon())
        terms = -(positive_weight*y*tf.math.log(p) + (1.-y)*tf.math.log1p(-p))
        return tf.math.divide_no_nan(tf.reduce_sum(mask*terms), tf.reduce_sum(mask))
    loss.__name__ = 'masked_weighted_bce'
    return loss


def batch_dataset(store, targets, length=7, batch_size=2):
    """Finite, deterministic batches with a bounded one-batch prefetch."""
    import tensorflow as tf
    targets = np.asarray(targets, dtype=int)
    if not len(targets):
        raise ValueError('No eligible sequence targets.')

    def generate():
        for offset in range(0, len(targets), batch_size):
            yield store.batch(targets[offset:offset+batch_size], length)
    signature = (
        tf.TensorSpec((None, length, store.height, store.width, len(PREDICTORS)+2), tf.float32),
        tf.TensorSpec((None, store.height, store.width, 2), tf.float32),
    )
    dataset = tf.data.Dataset.from_generator(generate, output_signature=signature)
    count = (len(targets)+batch_size-1)//batch_size
    return dataset.apply(tf.data.experimental.assert_cardinality(count)).prefetch(1)


def predict_cells(model, store, targets, length=7, batch_size=2):
    """Stream maps; retain keyed study-cell scores only, never background."""
    frames = []
    for offset in range(0, len(targets), batch_size):
        batch_targets = targets[offset:offset+batch_size]
        x, y = store.batch(batch_targets, length)
        predictions = np.asarray(model(x, training=False))[..., 0]
        for b, target in enumerate(batch_targets):
            valid = y[b, store.rows, store.cols, 1].astype(bool)
            frames.append(pd.DataFrame({
                'cell_id': store.cells.cell_id.to_numpy()[valid],
                'acq_date': store.dates[target],
                'fire_detected': store.labels[target, valid],
                'raw_probability': predictions[b, store.rows[valid], store.cols[valid]],
            }))
    if not frames:
        raise ValueError('No valid evaluation predictions.')
    result = pd.concat(frames, ignore_index=True)
    if result.duplicated(['cell_id', 'acq_date']).any():
        raise ValueError('Duplicate prediction keys.')
    return result


def evaluate_scores(labels, probabilities, constant_rate):
    """Ranking/probability metrics and descriptive whole-period alert counts."""
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
    y, p = np.asarray(labels), np.asarray(probabilities)
    if not (len(y) and y.shape == p.shape and np.isin(y, [0, 1]).all()
            and np.isfinite(p).all() and ((p >= 0) & (p <= 1)).all()):
        raise ValueError('Expected aligned binary labels and finite probabilities.')
    both_classes = len(np.unique(y)) == 2
    baseline = brier_score_loss(y, np.full(len(y), constant_rate))
    brier = brier_score_loss(y, p)
    ap = average_precision_score(y, p) if y.sum() else np.nan
    metrics = {'Cell-days': len(y), 'Positives': int(y.sum()),
               'ROC–AUC': roc_auc_score(y, p) if both_classes else np.nan,
               'Average precision': ap, 'Prevalence': y.mean(),
               'AP / chance': ap/y.mean() if y.mean() else np.nan,
               'Brier': brier, 'Constant Brier': baseline,
               'Brier (baseline=100)': 100*brier/baseline if baseline else np.nan}
    counts = []
    thresholds = [('Fixed 0.5', 0.5)]
    for k in [100, 1000, 10000]:
        count = min(k, len(p))
        thresholds.append((f'Target {k:,} alerts', np.partition(p, len(p)-count)[len(p)-count]))
    for description, threshold in thresholds:
        alerts, actual = p >= threshold, y == 1
        tp, fp = int((alerts & actual).sum()), int((alerts & ~actual).sum())
        counts.append({'Rule': description, 'Threshold': threshold,
                       'Actual alerts': int(alerts.sum()), 'Found': tp, 'False alarms': fp,
                       'Missed': int(actual.sum())-tp, 'True negatives': int((~alerts & ~actual).sum())})
    return metrics, pd.DataFrame(counts)
