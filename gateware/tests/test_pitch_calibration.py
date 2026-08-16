# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import unittest

from tiliqua.dsp.pitch_calibration import fit_pitch_cv_calibration


class PitchCalibrationTests(unittest.TestCase):

    def test_fits_offset_scale_and_reports_residual(self):
        fit = fit_pitch_cv_calibration([
            (-1.0, -3_990),
            (0.0, 100),
            (1.0, 4_190),
            (2.0, 8_280),
        ])
        self.assertEqual(fit.zero_cv_counts, 100)
        self.assertEqual(fit.counts_per_octave, 4_090)
        self.assertEqual(fit.maximum_residual_counts, 0.0)

    def test_rejects_degenerate_or_reversed_measurements(self):
        with self.assertRaises(ValueError):
            fit_pitch_cv_calibration([(0.0, 0)])
        with self.assertRaises(ValueError):
            fit_pitch_cv_calibration([(0.0, 100), (1.0, -3_900)])


if __name__ == "__main__":
    unittest.main()
