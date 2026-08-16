#!/usr/bin/env python3

# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""One-command regression and FPGA QoR gate for the parallel SNN AV top."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from dslx_lab import evaluate_bitstream, evaluate_contract


ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / "snn-av-metrics.json"
CONTRACT = ROOT / "snn" / "snn_contract.json"
SYNTHESIS_CONTRACT = ROOT / "snn" / "snn_synthesis_contract.json"


def run(command: list[str]) -> None:
    print(f"\n[run] {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def doctor(_: argparse.Namespace) -> None:
    required = [
        ROOT / "src" / "tiliqua" / "dsp" / "snn.py",
        ROOT / "src" / "tiliqua" / "video" / "snn_visualizer.py",
        ROOT / "src" / "top" / "snn_av" / "top.py",
        ROOT / "tests" / "test_snn.py",
        CONTRACT,
        SYNTHESIS_CONTRACT,
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"missing SNN lab files: {', '.join(missing)}")
    if shutil.which("verilator") is None:
        raise SystemExit("Verilator is required")
    print("SNN lab doctor: PASS")
    print("  neurons            64 fully parallel")
    print("  neuron step rate    3.072 M updates/s at 48 kHz")


def quick(_: argparse.Namespace) -> None:
    run([sys.executable, "-m", "pytest", "-q", "tests/test_snn.py"])


def load_and_report() -> list[str]:
    if not METRICS.is_file():
        raise SystemExit(f"metrics not found: {METRICS}")
    metrics = json.loads(METRICS.read_text())
    contract = json.loads(CONTRACT.read_text())
    failures = evaluate_contract(metrics, contract)
    dvi = metrics["dvi"]
    print("\nSNN AV lab report")
    print(f"  result             {'PASS' if not failures else 'FAIL'}")
    print(f"  neuron samples     {metrics['test_sample_index']}")
    print(f"  DVI frames/pixels  {dvi['frames']} / {dvi['pixels']}")
    print(f"  DVI checksum       {dvi['checksum']}")
    print(f"  RGB ranges         R{dvi['r']} G{dvi['g']} B{dvi['b']}")
    for entry in metrics["audio"]:
        print(
            f"  audio {entry['channel']}            samples={entry['samples']} "
            f"min={entry['min']} max={entry['max']} mean|x|={entry['mean_abs']:.1f}"
        )
    for failure in failures:
        print(f"  failure            {failure}")
    return failures


def report(_: argparse.Namespace) -> None:
    if load_and_report():
        raise SystemExit(1)


def integration(args: argparse.Namespace) -> None:
    METRICS.unlink(missing_ok=True)
    run([
        sys.executable,
        "src/top/snn_av/top.py",
        "sim",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--self-test",
    ])
    report(args)


def build(args: argparse.Namespace) -> None:
    run([
        sys.executable,
        "src/top/snn_av/top.py",
        "build",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--self-test",
    ])
    evaluate_bitstream(
        ROOT / "build" / f"snn-av-lab-{args.hw}",
        SYNTHESIS_CONTRACT,
    )


def check(args: argparse.Namespace) -> None:
    doctor(args)
    quick(args)
    integration(args)
    if args.with_build:
        build(args)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hw", default="r5")
    parser.add_argument("--modeline", default="720x720p60r2")
    parser.add_argument("--with-build", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "quick", "sim", "report", "build", "check"):
        subparsers.add_parser(name)
    return parser


def main() -> None:
    args = make_parser().parse_args()
    commands = {
        "doctor": doctor,
        "quick": quick,
        "sim": integration,
        "report": report,
        "build": build,
        "check": check,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
