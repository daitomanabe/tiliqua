#!/usr/bin/env python3

# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Fit DSLX synth compile-time pitch calibration from measured ADC counts."""

import argparse
import json

from tiliqua.dsp.pitch_calibration import fit_pitch_cv_calibration


def parse_point(value):
    try:
        volts_text, counts_text = value.split(":", 1)
        return float(volts_text), float(counts_text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "point must use VOLTS:COUNTS, for example 1.0:4112"
        ) from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--point",
        action="append",
        required=True,
        type=parse_point,
        metavar="VOLTS:COUNTS",
        help="Repeat for at least two stable voltage/count measurements.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        fit = fit_pitch_cv_calibration(args.point)
    except ValueError as error:
        parser.error(str(error))

    result = {
        "zero_cv_counts": fit.zero_cv_counts,
        "counts_per_octave": fit.counts_per_octave,
        "maximum_residual_counts": round(fit.maximum_residual_counts, 3),
        "build_arguments": [
            "--pitch-zero-counts", str(fit.zero_cv_counts),
            "--counts-per-octave", str(fit.counts_per_octave),
        ],
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("Pitch CV calibration")
        print(f"  zero CV counts       {fit.zero_cv_counts}")
        print(f"  counts per octave    {fit.counts_per_octave}")
        print(f"  maximum residual     {fit.maximum_residual_counts:.3f} counts")
        print("  build arguments      " + " ".join(result["build_arguments"]))


if __name__ == "__main__":
    main()
