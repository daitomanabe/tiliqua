# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Host-side fitting helpers for compile-time 1 V/oct pitch calibration."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PitchCVFit:
    zero_cv_counts: int
    counts_per_octave: int
    maximum_residual_counts: float


def fit_pitch_cv_calibration(points):
    """Fit ``counts = zero + volts * counts_per_octave`` by least squares."""

    samples = [(float(volts), float(counts)) for volts, counts in points]
    if len(samples) < 2:
        raise ValueError("at least two voltage/count points are required")

    mean_volts = sum(volts for volts, _ in samples) / len(samples)
    mean_counts = sum(counts for _, counts in samples) / len(samples)
    denominator = sum((volts - mean_volts) ** 2 for volts, _ in samples)
    if denominator == 0:
        raise ValueError("calibration points must use at least two voltages")

    slope = sum(
        (volts - mean_volts) * (counts - mean_counts)
        for volts, counts in samples
    ) / denominator
    intercept = mean_counts - slope * mean_volts
    zero_cv_counts = round(intercept)
    counts_per_octave = round(slope)

    if not -32768 <= zero_cv_counts <= 32767:
        raise ValueError("fitted zero_cv_counts does not fit signed 16-bit ASQ")
    if not 1_000 <= counts_per_octave <= 16_000:
        raise ValueError("fitted counts_per_octave is outside 1000..16000")

    maximum_residual = max(
        abs(counts - (zero_cv_counts + volts * counts_per_octave))
        for volts, counts in samples
    )
    return PitchCVFit(
        zero_cv_counts=zero_cv_counts,
        counts_per_octave=counts_per_octave,
        maximum_residual_counts=maximum_residual,
    )
