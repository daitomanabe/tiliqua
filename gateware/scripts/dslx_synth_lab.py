#!/usr/bin/env python3

# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Regression loop for both self-test and live-input DSLX synth builds."""

from __future__ import annotations

import argparse
import json
import sys

from dslx_lab import (
    GATEWARE_ROOT,
    doctor as common_doctor,
    evaluate_bitstream,
    evaluate_contract,
    print_report,
    quick as common_quick,
    run,
)


SYNTH_METRICS_PATH = GATEWARE_ROOT / "dslx-synth-metrics.json"
SYNTH_CONTRACT_PATH = GATEWARE_ROOT / "dslx" / "synth_lab_contract.json"
SYNTHESIS_CONTRACT_PATH = (
    GATEWARE_ROOT / "dslx" / "synth_synthesis_contract.json"
)


def doctor(args: argparse.Namespace) -> None:
    common_doctor(args)
    required = [
        GATEWARE_ROOT / "src" / "top" / "dslx_synth" / "top.py",
        SYNTH_CONTRACT_PATH,
        SYNTHESIS_CONTRACT_PATH,
    ]
    missing = [
        str(path.relative_to(GATEWARE_ROOT))
        for path in required
        if not path.is_file() or path.stat().st_size == 0
    ]
    if missing:
        raise SystemExit(f"missing synth lab files: {', '.join(missing)}")
    print("  synth profiles     self-test + live-input")


def quick(args: argparse.Namespace) -> None:
    common_quick(args)


def integration(args: argparse.Namespace) -> None:
    SYNTH_METRICS_PATH.unlink(missing_ok=True)
    run([
        sys.executable,
        "src/top/dslx_synth/top.py",
        "sim",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--self-test",
    ])
    metrics = json.loads(SYNTH_METRICS_PATH.read_text())
    contract = json.loads(SYNTH_CONTRACT_PATH.read_text())
    failures = evaluate_contract(metrics, contract)
    print_report(metrics, failures)
    if failures:
        raise SystemExit(1)


def build_profile(args: argparse.Namespace, *, self_test: bool) -> None:
    command = [
        sys.executable,
        "src/top/dslx_synth/top.py",
        "build",
        "--hw", args.hw,
        "--modeline", args.modeline,
    ]
    profile = "live-input"
    directory = f"dslx-synth-{args.hw}"
    if self_test:
        command.append("--self-test")
        profile = "self-test"
        directory = f"dslx-synth-lab-{args.hw}"
    print(f"\nDSLX synth FPGA profile: {profile}")
    run(command)
    evaluate_bitstream(
        GATEWARE_ROOT / "build" / directory,
        SYNTHESIS_CONTRACT_PATH,
    )


def build(args: argparse.Namespace) -> None:
    # Both profiles are mandatory: self-test proves the deterministic source;
    # live-input catches timing paths that begin at the calibrated ADC FIFO.
    build_profile(args, self_test=True)
    build_profile(args, self_test=False)


def check(args: argparse.Namespace) -> None:
    doctor(args)
    quick(args)
    integration(args)
    if args.with_build:
        build(args)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlsynth-driver")
    parser.add_argument("--hw", default="r5")
    parser.add_argument("--modeline", default="720x720p60r2")
    parser.add_argument("--hardware", action="store_true")
    parser.add_argument("--with-build", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor")
    subparsers.add_parser("quick")
    subparsers.add_parser("sim")
    subparsers.add_parser("build")
    subparsers.add_parser("check")
    return parser


def main() -> None:
    args = make_parser().parse_args()
    {
        "doctor": doctor,
        "quick": quick,
        "sim": integration,
        "build": build,
        "check": check,
    }[args.command](args)


if __name__ == "__main__":
    main()
