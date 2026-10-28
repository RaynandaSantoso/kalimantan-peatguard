"""Scientific alignment, leakage, masking and neural smoke regressions."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from peatguard.conv_lstm import (
    SpatialStore, prepare_store, build_model, masked_weighted_bce,
    batch_dataset, predict_cells, evaluate_scores,
)
from peatguard.datasets import CONTINUOUS, PREDICTORS

HAS_TF = importlib.util.find_spec('tensorflow') is not None


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cells = pd.DataFrame({'cell_id': ['kp5_c-1_r1', 'kp5_c0_r0'],
                                   'grid_col': [-1, 0], 'grid_row': [1, 0]})
        self.scaler = {c: {'min': 10., 'max': 30., 'constant': False} for c in CONTINUOUS}
        self.dates = pd.date_range('2023-12-29', '2024-01-03')
        rows = []
        for d, day in enumerate(self.dates):
            for c, cell in enumerate(self.cells.cell_id):
                features = dict(zip(PREDICTORS[:4], [d/10+c/20]*4))
                features.update({p: int(i == c) for i, p in enumerate(PREDICTORS[4:])})
                rows.append({'cell_id': cell, 'acq_date': day, 'fire_detected': int(c == 0), **features})
        self.table = pd.DataFrame(rows)

    def prepare(self, frame=None, name='store'):
        table = self.table if frame is None else frame
        path = self.root/f'{name}.parquet'
        table.to_parquet(path, index=False)
        return prepare_store(path, self.cells, self.scaler, self.root/name,
                             start='2023-12-29', end='2024-01-03')

    def test_train_only_scaling_and_north_up_scatter_after_shuffling(self):
        store = self.prepare(self.table.sample(frac=1, random_state=9))
        self.assertEqual(store.scaling[CONTINUOUS[0]]['max'], 15.)
        # Evaluation extremes are not clipped or used to refit.
        self.assertAlmostEqual(store.features[-1, 1, 0], 2.2, places=6)
        x, y = store.batch([3], length=3)
        self.assertEqual(x.shape, (1, 3, 2, 2, 23))
        np.testing.assert_allclose(x[0, :, 0, 0, 0], [0.4, 0.8, 1.2])
        np.testing.assert_array_equal(y[0, :, :, 0], [[1, 0], [0, 0]])
        np.testing.assert_array_equal(y[0, :, :, 1], [[1, 0], [0, 1]])
        self.assertEqual(store.dates[3], pd.Timestamp('2024-01-01'))
        with self.assertRaises(ValueError): store.batch([0], 3)

    def test_missing_history_unknown_labels_and_domain_are_not_negatives(self):
        frame = self.table.drop(index=2).copy()  # cell 0 missing on Dec 30
        frame.loc[frame.index == 7, 'fire_detected'] = np.nan
        store = self.prepare(frame)
        x, y = store.batch([3], length=3)
        self.assertFalse(x[0, 0, 0, 0, -1])
        self.assertTrue(x[0, 0, 0, 0, -2])
        self.assertFalse(y[..., 1].any())  # cell 0 missing history; cell 1 unknown target
        self.assertEqual(store.labels[3, 1], -1)
        self.assertFalse(x[0, :, 0, 1, -2].any())
        # Missing full days keep their calendar index; history never jumps over them.
        missing_day = self.table[self.table.acq_date != self.dates[1]]
        hole = self.prepare(missing_day, name='hole')
        self.assertFalse(hole.observed[1].any())
        self.assertFalse(hole.valid_cells(3, 3).any())
        self.assertTrue(hole.valid_cells(4, 3).all())

    def test_missing_constant_feature_is_still_unobserved(self):
        frame = self.table.copy()
        frame[PREDICTORS[0]] = 0.5
        frame.loc[frame.acq_date == self.dates[-1], PREDICTORS[0]] = np.nan
        store = self.prepare(frame)
        self.assertTrue(store.scaling[CONTINUOUS[0]]['constant'])
        self.assertFalse(store.observed[-1].any())
        self.assertFalse(store.valid_cells(len(self.dates)-1, 3).any())

    def test_duplicate_unknown_cells_and_invalid_categories_fail(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.prepare(pd.concat([self.table, self.table.iloc[[0]]]), name='duplicate')
        invalid = self.table.copy(); invalid.loc[0, 'cell_id'] = 'kp5_c9_r9'
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.prepare(invalid, name='unknown')
        invalid = self.table.copy(); invalid.loc[0, 'land_cover_1'] = 0
        with self.assertRaisesRegex(ValueError, 'one-hot'):
            self.prepare(invalid, name='onehot')

    def test_spatial_metadata_disagreement_fails(self):
        store = self.prepare()
        wrong = store.cells.copy(); wrong.loc[0, 'grid_row'] = 10
        with self.assertRaisesRegex(ValueError, 'disagree'):
            SpatialStore(store.dates, wrong, store.features, store.observed, store.labels, {})

    def test_constant_scaling_and_evaluation_baseline_counts(self):
        frame = self.table.copy(); frame[PREDICTORS[0]] = 0.5
        store = self.prepare(frame)
        self.assertTrue(store.scaling[CONTINUOUS[0]]['constant'])
        self.assertTrue((store.features[..., 0] == 0).all())
        metrics, counts = evaluate_scores([0, 1, 0, 1], [.2, .9, .1, .8], .25)
        self.assertEqual(metrics['ROC–AUC'], 1)
        self.assertAlmostEqual(metrics['Constant Brier'], .3125)
        self.assertEqual(counts.iloc[0]['Found'], 2)
        self.assertEqual(counts.iloc[0]['False alarms'], 0)
        _, tied = evaluate_scores([0, 1, 0, 1], [.5]*4, .5)
        self.assertTrue((tied['Actual alerts'] == 4).all())

    @unittest.skipUnless(HAS_TF, 'Install requirements-conv-lstm.txt for neural checks')
    def test_loss_exact_weight_mask_normalization_and_zero_gradient(self):
        import tensorflow as tf
        packed = tf.constant([[[[1., 1.], [0., 1.], [1., 0.]]]])
        p = tf.Variable([[[[.25], [.75], [.9]]]])
        with tf.GradientTape() as tape:
            loss = masked_weighted_bce(75)(packed, p)
        self.assertAlmostEqual(float(loss), -76*np.log(.25)/2, places=4)
        gradient = tape.gradient(loss, p).numpy()
        self.assertEqual(gradient[0, 0, 2, 0], 0)
        # Extra masked background must not dilute the loss.
        np.testing.assert_allclose(masked_weighted_bce(75)(packed[:, :, :2], p[:, :, :2]), loss)
        self.assertEqual(float(masked_weighted_bce()(tf.zeros_like(packed), p)), 0)

    @unittest.skipUnless(HAS_TF, 'Install requirements-conv-lstm.txt for neural checks')
    def test_architecture_training_stream_keyed_prediction_and_reload(self):
        import tensorflow as tf
        store = self.prepare()
        model = build_model(length=3, spatial_shape=(2, 2))
        recurrent = next(l for l in model.layers if isinstance(l, tf.keras.layers.ConvLSTM2D))
        self.assertEqual(recurrent.filters, 32)
        self.assertEqual(recurrent.activation.__name__, 'tanh')
        convs = [l for l in model.layers if isinstance(l, tf.keras.layers.Conv2D)]
        self.assertEqual([l.filters for l in convs], [64, 32, 1])
        self.assertEqual([l.kernel_size for l in convs], [(3, 3), (3, 3), (1, 1)])
        self.assertEqual(len([l for l in model.layers if isinstance(l, tf.keras.layers.BatchNormalization)]), 3)
        self.assertEqual([l.rate for l in model.layers if isinstance(l, tf.keras.layers.Dropout)], [.3, .3])
        targets = store.targets('2024-01-01', '2024-01-03', 3)
        history = model.fit(batch_dataset(store, targets, length=3), epochs=1, shuffle=False, verbose=0)
        self.assertTrue(np.isfinite(history.history['loss']).all())
        scores = predict_cells(model, store, targets, length=3)
        self.assertEqual(len(scores), 6)
        self.assertEqual(scores.fire_detected.sum(), 3)
        model.save_weights(self.root/'smoke.weights.h5')
        reloaded = build_model(length=3, spatial_shape=(2, 2))
        reloaded.optimizer.build(reloaded.trainable_variables)
        reloaded.load_weights(self.root/'smoke.weights.h5')
        pd.testing.assert_frame_equal(scores, predict_cells(reloaded, store, targets, length=3))


if __name__ == '__main__':
    unittest.main()
