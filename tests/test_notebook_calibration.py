"""Exercise the standalone calibration cell without training or TensorFlow."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd

from peatguard.inventory import sha256


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'run'
        self.output = self.run / 'development_2024_evaluation'
        self.output.mkdir(parents=True)
        checkpoint = self.root / 'checkpoint'
        scaler = self.root / 'scaler.json'
        checkpoint.write_bytes(b'fixed model')
        scaler.write_text('{}')
        self.record = {
            'status': 'raw 2024 evaluation complete; no calibration fitted',
            'checkpoint': 'checkpoint', 'checkpoint_sha256': sha256(checkpoint),
            'scaling_file': 'scaler.json', 'scaling_sha256': sha256(scaler),
            'fit_interval': ['2020-01-01', '2023-09-30'],
            'validation_interval': ['2023-10-01', '2023-12-31'],
            'training_constant_rate': .1,
        }
        self.rows = pd.DataFrame({
            'cell_id': ['a', 'b', 'c', 'd'] * 2,
            'acq_date': pd.to_datetime(['2024-06-30'] * 4 + ['2024-07-01'] * 4),
            'fire_detected': [0, 1, 0, 1, 0, 0, 1, 1],
            'raw_probability': [.1, .2, .3, .4, 0., .25, .35, 1.],
        })
        notebook = json.loads((Path(__file__).resolve().parents[1] / 'cnn_conv_lstm.ipynb').read_text())
        cell = next(c for c in notebook['cells'] if c['cell_type'] == 'code'
                    and 'calibration_spec = {' in ''.join(c['source']))
        self.code = compile(''.join(cell['source']), 'notebook:calibration', 'exec')

    def run_cell(self):
        self.rows.to_parquet(self.output / 'raw_2024_predictions.parquet', index=False)
        (self.output / 'evaluation_spec.json').write_text(json.dumps(self.record))
        namespace = {'RUN_DIR': self.run}
        with patch('peatguard.study.find_root', return_value=self.root), \
                patch('IPython.display.display'), patch('builtins.print'):
            exec(self.code, namespace)
        return joblib.load(self.output / 'isotonic_jan_jun_2024.joblib')

    def test_later_labels_do_not_fit_mapping_and_saved_scores_reload(self):
        mapping = self.run_cell()
        exported = pd.read_parquet(self.output / 'calibration_evaluation_jul_dec_2024.parquet')
        pd.testing.assert_frame_equal(exported[self.rows.columns].reset_index(drop=True),
                                      self.rows.iloc[4:].reset_index(drop=True))
        np.testing.assert_allclose(exported.calibrated_probability,
                                   mapping.predict(exported.raw_probability))
        # Scores beyond fitting support clip to the learned endpoint probabilities.
        np.testing.assert_allclose(mapping.predict([0., 1.]), [0., 1.])
        self.rows.loc[4:, 'fire_detected'] = [1, 1, 0, 0]
        changed = self.run_cell()
        np.testing.assert_array_equal(mapping.X_thresholds_, changed.X_thresholds_)
        np.testing.assert_array_equal(mapping.y_thresholds_, changed.y_thresholds_)
        spec = json.loads((self.output / 'calibration_spec.json').read_text())
        self.assertEqual(spec['calibration_fit_cell_days'], 4)
        self.assertEqual(spec['evaluation_cell_days'], 4)
        for name, fingerprint in spec['artifacts'].items():
            self.assertEqual(sha256(self.output / name), fingerprint)

    def test_invalid_inputs_fail_before_saving_calibration(self):
        original = self.rows.copy()
        cases = [
            ('acq_date', 0, pd.Timestamp('2025-01-01'), '2024'),
            ('acq_date', 0, pd.NaT, '2024'),
            ('cell_id', 1, 'a', 'unique'),
            ('raw_probability', 0, np.nan, 'finite probabilities'),
            ('raw_probability', 0, 1.1, 'finite probabilities'),
            ('fire_detected', 0, -1, 'binary labels'),
        ]
        for column, row, value, message in cases:
            with self.subTest(column=column, value=value):
                self.rows = original.copy()
                self.rows.loc[row, column] = value
                with self.assertRaisesRegex(ValueError, message):
                    self.run_cell()
                self.assertFalse((self.output / 'calibration_spec.json').exists())
        self.rows = original.copy()
        self.rows.loc[:3, 'fire_detected'] = 0
        with self.assertRaisesRegex(ValueError, 'Both calendar halves'):
            self.run_cell()

    def test_model_selection_overlap_and_changed_checkpoint_fail(self):
        self.record['validation_interval'][1] = '2024-01-01'
        with self.assertRaisesRegex(ValueError, 'Model selection'):
            self.run_cell()
        self.record['validation_interval'][1] = '2023-12-31'
        (self.root / 'checkpoint').write_bytes(b'changed model')
        with self.assertRaisesRegex(ValueError, 'checkpoint differs'):
            self.run_cell()


if __name__ == '__main__':
    unittest.main()
