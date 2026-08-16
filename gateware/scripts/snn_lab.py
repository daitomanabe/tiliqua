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
from tiliqua.build.qor import parse_nextpnr_utilization


ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / "snn-av-metrics.json"
CONTRACT = ROOT / "snn" / "snn_contract.json"
SYNTHESIS_CONTRACT = ROOT / "snn" / "snn_synthesis_contract.json"
SCALE_SYNTHESIS_CONTRACT = ROOT / "snn" / "snn_128_synthesis_contract.json"
FRONTIER_CONTRACT = ROOT / "snn" / "snn_256_frontier_contract.json"
BATCHED_SYNTHESIS_CONTRACT = (
    ROOT / "snn" / "snn_256x32_synthesis_contract.json"
)
MEMORY_SYNTHESIS_CONTRACT = (
    ROOT / "snn" / "snn_512x32_memory_synthesis_contract.json"
)
MEMORY_LIVE_SYNTHESIS_CONTRACT = (
    ROOT / "snn" / "snn_512x32_memory_live_synthesis_contract.json"
)
KILONEURON_SYNTHESIS_CONTRACT = (
    ROOT / "snn" / "snn_1024x32_memory_synthesis_contract.json"
)
KILONEURON_LIVE_SYNTHESIS_CONTRACT = (
    ROOT / "snn" / "snn_1024x32_memory_live_synthesis_contract.json"
)


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
        SCALE_SYNTHESIS_CONTRACT,
        FRONTIER_CONTRACT,
        BATCHED_SYNTHESIS_CONTRACT,
        MEMORY_SYNTHESIS_CONTRACT,
        MEMORY_LIVE_SYNTHESIS_CONTRACT,
        KILONEURON_SYNTHESIS_CONTRACT,
        KILONEURON_LIVE_SYNTHESIS_CONTRACT,
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"missing SNN lab files: {', '.join(missing)}")
    if shutil.which("verilator") is None:
        raise SystemExit("Verilator is required")
    print("SNN lab doctor: PASS")
    print("  base               64 fully parallel / 3.072 M updates/s")
    print("  batched            256 logical / 32 lanes / 12.288 M updates/s")
    print("  memory             512 logical / 32 lanes / 24.576 M updates/s")
    print("  kiloneuron         1024 logical / 32 lanes / 49.152 M updates/s")


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
    run([
        sys.executable,
        "src/top/snn_av/top.py",
        "build",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--name", "SNN-AV-LIVE",
    ])
    evaluate_bitstream(
        ROOT / "build" / f"snn-av-live-{args.hw}",
        SYNTHESIS_CONTRACT,
    )


def check(args: argparse.Namespace) -> None:
    doctor(args)
    quick(args)
    integration(args)
    if args.with_build:
        build(args)


def scale(args: argparse.Namespace) -> None:
    quick(args)
    METRICS.unlink(missing_ok=True)
    run([
        sys.executable,
        "src/top/snn_av/top.py",
        "sim",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--self-test",
        "--neurons", "128",
        "--name", "SNN-AV-128-LAB",
    ])
    report(args)
    run([
        sys.executable,
        "src/top/snn_av/top.py",
        "build",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--self-test",
        "--neurons", "128",
        "--name", "SNN-AV-128-LAB",
    ])
    evaluate_bitstream(
        ROOT / "build" / f"snn-av-128-lab-{args.hw}",
        SCALE_SYNTHESIS_CONTRACT,
    )
    run([
        sys.executable,
        "src/top/snn_av/top.py",
        "build",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--neurons", "128",
        "--name", "SNN-AV-128-LIVE",
    ])
    evaluate_bitstream(
        ROOT / "build" / f"snn-av-128-live-{args.hw}",
        SCALE_SYNTHESIS_CONTRACT,
    )


def frontier_report(args: argparse.Namespace) -> None:
    """Prove that the 256-way fully parallel design crosses the R5 boundary."""

    failures = load_and_report()
    build_dir = ROOT / "build" / f"snn-av-256-lab-{args.hw}"
    timing_path = build_dir / "top.tim"
    report_path = build_dir / "top.rpt"
    bitstream_path = build_dir / "top.bit"
    if not timing_path.is_file() or not report_path.is_file():
        raise SystemExit("256 frontier build did not produce top.tim and top.rpt")

    contract = json.loads(FRONTIER_CONTRACT.read_text())
    timing_text = timing_path.read_text()
    resources = parse_nextpnr_utilization(timing_text)
    resource_name = contract["expected_non_fit"]["resource"]
    if resource_name not in resources:
        failures.append(f"nextpnr report has no {resource_name} utilization")
        used = available = percent = 0
    else:
        used, available, percent = resources[resource_name]
        if used <= available:
            failures.append(
                f"{resource_name} no longer exceeds the device: {used}/{available}"
            )
        if percent < contract["expected_non_fit"]["minimum_percent"]:
            failures.append(
                f"{resource_name} utilization {percent}% below expected frontier"
            )

    expected_error = contract["expected_non_fit"]["error_substring"]
    if expected_error not in timing_text:
        failures.append("nextpnr did not report the expected legal-placement error")
    if bitstream_path.exists():
        failures.append("unexpected bitstream exists for the known non-fitting design")

    print("\nSNN 256 fully-parallel frontier report")
    print(f"  result             {'PASS' if not failures else 'FAIL'}")
    print(f"  architecture       256 physical LIF lanes / 12.288 M updates/s")
    print(f"  {resource_name:18} {used:>5} / {available:<5} ({percent}%)")
    print(f"  placement          expected non-fit")
    print(f"  bitstream          {'unexpected' if bitstream_path.exists() else 'not produced'}")
    for failure in failures:
        print(f"  failure            {failure}")
    if failures:
        raise SystemExit("256 fully-parallel frontier contract failed")


def frontier(args: argparse.Namespace) -> None:
    """Simulate 256 lanes, then require the characterized R5 non-fit result."""

    quick(args)
    METRICS.unlink(missing_ok=True)
    run([
        sys.executable,
        "src/top/snn_av/top.py",
        "sim",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--self-test",
        "--neurons", "256",
        "--name", "SNN-AV-256-LAB",
    ])
    if load_and_report():
        raise SystemExit("256 simulation contract failed")

    command = [
        sys.executable,
        "src/top/snn_av/top.py",
        "build",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--self-test",
        "--neurons", "256",
        "--name", "SNN-AV-256-LAB",
    ]
    print(f"\n[run, expected non-fit] {' '.join(command)}", flush=True)
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode == 0:
        raise SystemExit(
            "256 fully-parallel build now succeeds; replace the non-fit contract"
        )
    frontier_report(args)


def batch_report(args: argparse.Namespace) -> None:
    """Enforce the 256x32 self bitstream and optional live bitstream."""

    profiles = ("lab", "live") if args.with_live_build else ("lab",)
    for profile in profiles:
        print(f"\n256-logical / 32-lane {profile} profile")
        evaluate_bitstream(
            ROOT / "build" / f"snn-av-256x32-{profile}-{args.hw}",
            BATCHED_SYNTHESIS_CONTRACT,
        )


def batch(args: argparse.Namespace) -> None:
    """Run the complete 256-logical / 32-lane simulation and build gate."""

    quick(args)
    METRICS.unlink(missing_ok=True)
    run([
        sys.executable,
        "src/top/snn_av/top.py",
        "sim",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--self-test",
        "--neurons", "256",
        "--physical-lanes", "32",
        "--name", "SNN-AV-256X32-LAB",
    ])
    report(args)
    profiles = [("LAB", True)]
    if args.with_live_build:
        profiles.append(("LIVE", False))
    else:
        print(
            "\n[skip] 256x32 live P&R; pass --with-live-build for the "
            "long-running experimental gate"
        )
    for profile, self_test in profiles:
        command = [
            sys.executable,
            "src/top/snn_av/top.py",
            "build",
            "--hw", args.hw,
            "--modeline", args.modeline,
            "--neurons", "256",
            "--physical-lanes", "32",
            "--name", f"SNN-AV-256X32-{profile}",
        ]
        if self_test:
            command.append("--self-test")
        run(command)
        evaluate_bitstream(
            ROOT / "build" / f"snn-av-256x32-{profile.lower()}-{args.hw}",
            BATCHED_SYNTHESIS_CONTRACT,
        )


def memory_report(args: argparse.Namespace) -> None:
    """Enforce the 512x32 block-memory self-test and live contracts."""

    profiles = (
        ("lab", MEMORY_SYNTHESIS_CONTRACT),
        ("live", MEMORY_LIVE_SYNTHESIS_CONTRACT),
    )
    for profile, contract in profiles:
        print(f"\n512-logical / 32-lane block-memory {profile} profile")
        evaluate_bitstream(
            ROOT / "build" / f"snn-av-512x32-mem-{profile}-{args.hw}",
            contract,
        )


def memory(args: argparse.Namespace) -> None:
    """Run 512x32 equivalence, AV simulation, self-test, and live build gates."""

    quick(args)
    METRICS.unlink(missing_ok=True)
    run([
        sys.executable,
        "src/top/snn_av/top.py",
        "sim",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--self-test",
        "--neurons", "512",
        "--physical-lanes", "32",
        "--name", "SNN-AV-512X32-MEM-LAB",
    ])
    report(args)
    run([
        sys.executable,
        "src/top/snn_av/top.py",
        "build",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--self-test",
        "--neurons", "512",
        "--physical-lanes", "32",
        "--name", "SNN-AV-512X32-MEM-LAB",
    ])
    run([
        sys.executable,
        "src/top/snn_av/top.py",
        "build",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--neurons", "512",
        "--physical-lanes", "32",
        "--name", "SNN-AV-512X32-MEM-LIVE",
    ])
    memory_report(args)


def kiloneuron_report(args: argparse.Namespace) -> None:
    """Enforce the 1024x32 block-memory self-test and live contracts."""

    profiles = (
        ("lab", KILONEURON_SYNTHESIS_CONTRACT),
        ("live", KILONEURON_LIVE_SYNTHESIS_CONTRACT),
    )
    for profile, contract in profiles:
        print(f"\n1024-logical / 32-lane block-memory {profile} profile")
        evaluate_bitstream(
            ROOT / "build" / f"snn-av-1024x32-mem-{profile}-{args.hw}",
            contract,
        )


def kiloneuron(args: argparse.Namespace) -> None:
    """Run 1024x32 equivalence, AV simulation, and both R5 build gates."""

    quick(args)
    METRICS.unlink(missing_ok=True)
    run([
        sys.executable,
        "src/top/snn_av/top.py",
        "sim",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--self-test",
        "--neurons", "1024",
        "--physical-lanes", "32",
        "--name", "SNN-AV-1024X32-MEM-LAB",
    ])
    report(args)
    for profile, self_test in (("LAB", True), ("LIVE", False)):
        command = [
            sys.executable,
            "src/top/snn_av/top.py",
            "build",
            "--hw", args.hw,
            "--modeline", args.modeline,
            "--neurons", "1024",
            "--physical-lanes", "32",
            "--name", f"SNN-AV-1024X32-MEM-{profile}",
        ]
        if self_test:
            command.append("--self-test")
        run(command)
    kiloneuron_report(args)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hw", default="r5")
    parser.add_argument("--modeline", default="720x720p60r2")
    parser.add_argument("--with-build", action="store_true")
    parser.add_argument(
        "--with-live-build",
        action="store_true",
        help="Batch only: include the long-running experimental live P&R",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in (
        "doctor", "quick", "sim", "report", "build", "check", "scale",
        "frontier", "frontier-report",
        "batch", "batch-report",
        "memory", "memory-report",
        "kiloneuron", "kiloneuron-report",
    ):
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
        "scale": scale,
        "frontier": frontier,
        "frontier-report": frontier_report,
        "batch": batch,
        "batch-report": batch_report,
        "memory": memory,
        "memory-report": memory_report,
        "kiloneuron": kiloneuron,
        "kiloneuron-report": kiloneuron_report,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
