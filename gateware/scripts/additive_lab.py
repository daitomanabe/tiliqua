#!/usr/bin/env python3

# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Reference, RTL, AV-simulation, and R5 QoR gates for the ADDITIVE profile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from dslx_lab import evaluate_bitstream


ROOT = Path(__file__).resolve().parents[1]
AV_CONTRACT = ROOT / "additive" / "additive_av_contract.json"
SYNTHESIS_CONTRACT = ROOT / "additive" / "additive_synthesis_contract.json"
METRICS = ROOT / "additive-av-metrics.json"
TESTS = [
    "tests/test_additive.py",
    "tests/test_additive_arith.py",
    "tests/test_additive_control.py",
    "tests/test_additive_visualizer.py",
    "tests/test_additive_engine.py",
    "tests/test_additive_control_engine.py",
    "tests/test_additive_core.py",
]
QUICK_TESTS = TESTS[:4]


def run(command: list[str]) -> None:
    print(f"\n[run] {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def doctor(_: argparse.Namespace) -> None:
    required = [
        ROOT / "src" / "tiliqua" / "additive" / name
        for name in (
            "tables.py", "reference.py", "protocol.py", "control.py", "arith.py",
            "engine.py", "control_engine.py", "core.py",
        )
    ] + [
        ROOT / "src" / "tiliqua" / "video" / "additive_visualizer.py",
        ROOT / "src" / "top" / "additive_av" / "top.py",
        ROOT / "src" / "top" / "additive_av" / "sim.cpp",
        AV_CONTRACT,
        SYNTHESIS_CONTRACT,
    ] + [ROOT / test for test in TESTS]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"missing ADDITIVE files: {', '.join(missing)}")
    if shutil.which("verilator") is None:
        raise SystemExit("Verilator is required for the ADDITIVE AV contract")
    print("ADDITIVE lab doctor: PASS")
    print("  model              5 x 10 x 20 = 1000 sine oscillators, four outputs")
    print("  control            45-byte CRC frames over the FLASH/DEBUG UART")
    print("  hardware           not loaded; SRAM validation remains separate")


def quick(_: argparse.Namespace) -> None:
    run([sys.executable, "-m", "pytest", "-q", *QUICK_TESTS])


def unit(_: argparse.Namespace) -> None:
    run([sys.executable, "-m", "pytest", "-q", "-n", "4", *TESTS])


def evaluate_av_contract(metrics: dict, contract: dict) -> list[str]:
    failures = []
    if not metrics.get("pass", False):
        failures.append("the Verilator harness reported FAIL")
    dvi = metrics["dvi"]
    expected = contract["dvi"]
    if dvi["frames"] < expected["minimum_frames"]:
        failures.append("DVI frame count is below the frozen minimum")
    if dvi["pixels"] < expected["minimum_pixels"]:
        failures.append("DVI pixel count is below the frozen minimum")
    for channel in ("r", "g", "b"):
        if dvi[channel][1] - dvi[channel][0] < expected["minimum_rgb_span"]:
            failures.append(f"DVI {channel.upper()} span is below the minimum")
    engine = contract["engine"]
    if metrics["fault"]:
        failures.append("the engine latched a deadline/overrun fault")
    if metrics["max_sample_cycles"] > engine["maximum_sample_cycles"]:
        failures.append("a sample exceeded the sync-cycle budget")
    if metrics["max_block_cycles"] > engine["maximum_block_cycles"]:
        failures.append("a control block exceeded its cycle budget")
    control = contract["control"]
    if metrics["control"]["accepted"] != control["accepted_frames"]:
        failures.append("unexpected accepted control frame count")
    if metrics["control"]["rejected"] != control["rejected_frames"]:
        failures.append("unexpected rejected control frame count")
    if not metrics["control"]["link_alive"]:
        failures.append("the control link is not alive at the end of the run")
    audio = contract["audio"]
    for channel in metrics["audio"]:
        if channel["nonzero"] < audio["minimum_nonzero"]:
            failures.append(f"OUT {channel['channel']} is mostly silent")
        if max(abs(channel["min"]), abs(channel["max"])) > audio["ceiling_asq"]:
            failures.append(f"OUT {channel['channel']} exceeded the +/-2 V ceiling")
        if max(abs(channel["min"]), abs(channel["max"])) < audio["minimum_peak_asq"]:
            failures.append(f"OUT {channel['channel']} carries no additive audio")
    return failures


def av_report(_: argparse.Namespace) -> None:
    if not METRICS.is_file():
        raise SystemExit(f"metrics not found: {METRICS}")
    metrics = json.loads(METRICS.read_text())
    contract = json.loads(AV_CONTRACT.read_text())
    failures = evaluate_av_contract(metrics, contract)
    print("ADDITIVE AV contract")
    print(f"  samples            {metrics['sample_index']}")
    print(f"  sample cycles      {metrics['max_sample_cycles']} / 1250")
    print(f"  block cycles       {metrics['max_block_cycles']} / 160000")
    print(f"  control            accepted {metrics['control']['accepted']} "
          f"rejected {metrics['control']['rejected']} "
          f"alive {metrics['control']['link_alive']}")
    print(f"  cv smoothed        {metrics['cv_smoothed']}")
    print(f"  dvi                {metrics['dvi']['frames']} frames, "
          f"{metrics['dvi']['pixels']} pixels")
    for channel in metrics["audio"]:
        print(f"  OUT {channel['channel']}              min {channel['min']} "
              f"max {channel['max']} nonzero {channel['nonzero']} "
              f"zero-crossings {channel['zero_crossings']}")
    for failure in failures:
        print(f"  failure            {failure}")
    if failures:
        raise SystemExit("ADDITIVE AV contract: FAIL")
    print("ADDITIVE AV contract: PASS")


def integration(args: argparse.Namespace) -> None:
    METRICS.unlink(missing_ok=True)
    run([
        sys.executable,
        "src/top/additive_av/top.py",
        "sim",
        "--hw", args.hw,
        "--modeline", args.modeline,
    ])
    av_report(args)


def build(args: argparse.Namespace) -> None:
    command = [
        sys.executable,
        "src/top/additive_av/top.py",
        "build",
        "--hw", args.hw,
        "--modeline", args.modeline,
    ]
    if args.seed is not None:
        command.extend(("--nextpnr-seed", str(args.seed)))
    run(command)
    evaluate_bitstream(ROOT / "build" / f"additive-av-{args.hw}", SYNTHESIS_CONTRACT)


def check(args: argparse.Namespace) -> None:
    doctor(args)
    unit(args)
    integration(args)
    if args.with_build:
        build(args)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hw", default="r5")
    parser.add_argument("--modeline", default="720x720p60r2")
    parser.add_argument("--with-build", action="store_true")
    parser.add_argument("--seed", type=int, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "quick", "unit", "integration", "av-report", "build", "check"):
        subparsers.add_parser(name)
    return parser


def main() -> None:
    parser = make_parser()
    args = parser.parse_args()
    commands = {
        "doctor": doctor,
        "quick": quick,
        "unit": unit,
        "integration": integration,
        "av-report": av_report,
        "build": build,
        "check": check,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
