#!/usr/bin/env python3

# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""One-command regression and FPGA QoR gate for the parallel SNN AV top."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

from amaranth.sim import Simulator

from dslx_lab import evaluate_bitstream, evaluate_contract
from tiliqua.build.qor import parse_nextpnr_utilization
from tiliqua.dsp.snn import MemoryBatchedLIFBank


ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / "snn-av-metrics.json"
INHIBITION_STUDY = ROOT / "build" / "snn-inhibition-study.json"
POPULATION_STUDY = ROOT / "build" / "snn-population-study.json"
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
EI_RING_SYNTHESIS_CONTRACT = (
    ROOT / "snn" / "snn_1024x32_ei_ring_synthesis_contract.json"
)
EI_RING_LIVE_SYNTHESIS_CONTRACT = (
    ROOT / "snn" / "snn_1024x32_ei_ring_live_synthesis_contract.json"
)
SONIFICATION_LIVE_SYNTHESIS_CONTRACT = (
    ROOT / "snn" / "snn_1024x32_ei_sonification_live_synthesis_contract.json"
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
        EI_RING_SYNTHESIS_CONTRACT,
        EI_RING_LIVE_SYNTHESIS_CONTRACT,
        SONIFICATION_LIVE_SYNTHESIS_CONTRACT,
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
    print("  ei-ring            1024 logical / 32 lanes / 3:1 E/I ring")
    print("  sonification       E/I population -> C-minor pentatonic triangle")


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


def ei_ring_report(args: argparse.Namespace) -> None:
    """Enforce the 1024x32 E/I ring self-test and live contracts."""

    profiles = (
        ("lab", EI_RING_SYNTHESIS_CONTRACT),
        ("live", EI_RING_LIVE_SYNTHESIS_CONTRACT),
    )
    for profile, contract in profiles:
        print(f"\n1024-logical / 32-lane E/I ring {profile} profile")
        evaluate_bitstream(
            ROOT / "build" / f"snn-av-1024x32-ei-mem-{profile}-{args.hw}",
            contract,
        )


def ei_ring(args: argparse.Namespace) -> None:
    """Run E/I equivalence, AV simulation, and both R5 build gates."""

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
        "--ei-ring",
        "--name", "SNN-AV-1024X32-EI-MEM-LAB",
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
            "--ei-ring",
            "--name", f"SNN-AV-1024X32-EI-MEM-{profile}",
        ]
        if self_test:
            command.append("--self-test")
        run(command)
    ei_ring_report(args)


def sonification_report(args: argparse.Namespace) -> None:
    """Enforce the R5 QoR contract for the live pitched-output profile."""

    print("\n1024-neuron E/I four-voice sonification live profile")
    evaluate_bitstream(
        ROOT / "build" / f"snn-av-1024x32-ei-ensemble-live-{args.hw}",
        SONIFICATION_LIVE_SYNTHESIS_CONTRACT,
    )


def sonification(args: argparse.Namespace) -> None:
    """Regress, simulate, and build the optional pitched-output profile."""

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
        "--ei-ring",
        "--sonification",
        "--name", "SNN-AV-1024X32-EI-ENSEMBLE-LAB",
    ])
    failures = load_and_report()
    metrics = json.loads(METRICS.read_text())
    tones = metrics["audio"]
    crossing_ranges = ((10, 18), (22, 32), (16, 24), (7, 14))
    for channel, (tone, (minimum, maximum)) in enumerate(
        zip(tones, crossing_ranges)
    ):
        if not minimum <= tone["zero_crossings"] <= maximum:
            failures.append(
                f"audio {channel} zero crossings do not match its voice register"
            )
        if not 6_000.0 <= tone["mean_abs"] <= 9_000.0:
            failures.append(
                f"audio {channel} mean amplitude is outside the tone range"
            )
    if len({tone["zero_crossings"] for tone in tones}) != 4:
        failures.append("the four output voices do not have distinct initial pitches")
    print("\nSNN sonification simulation contract")
    print(f"  result             {'PASS' if not failures else 'FAIL'}")
    for channel, tone in enumerate(tones):
        print(
            f"  audio {channel}           {tone['zero_crossings']:2d} crossings / "
            f"mean absolute {tone['mean_abs']:.1f}"
        )
    for failure in failures:
        print(f"  failure            {failure}")
    if failures:
        raise SystemExit("sonification simulation contract failed")

    run([
        sys.executable,
        "src/top/snn_av/top.py",
        "build",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--neurons", "1024",
        "--physical-lanes", "32",
        "--ei-ring",
        "--sonification",
        "--name", "SNN-AV-1024X32-EI-ENSEMBLE-LIVE",
    ])
    sonification_report(args)


def inhibition_study(args: argparse.Namespace) -> None:
    """Sweep three constant inhibitory strengths through AV simulation."""

    quick(args)
    results = []
    for strength in (512, 1024, 1536):
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
            "--ei-ring",
            "--inhibitory-strength", str(strength),
            "--name", f"SNN-AV-1024X32-EI-{strength}-STUDY",
        ])
        metrics = json.loads(METRICS.read_text())
        results.append({
            "inhibitory_strength": strength,
            "neuron_samples": metrics["test_sample_index"],
            "dvi_checksum": metrics["dvi"]["checksum"],
            "audio_mean_abs": [entry["mean_abs"] for entry in metrics["audio"]],
            "audio_ranges": [
                {"min": entry["min"], "max": entry["max"]}
                for entry in metrics["audio"]
            ],
        })
    checksums = {item["dvi_checksum"] for item in results}
    channel_spreads = []
    for channel in range(4):
        values = [item["audio_mean_abs"][channel] for item in results]
        channel_spreads.append(max(values) - min(values))
    passed = len(checksums) == len(results) and all(spread > 1.0 for spread in channel_spreads)
    payload = {
        "pass": passed,
        "profiles": results,
        "distinct_dvi_checksums": len(checksums),
        "audio_mean_abs_spreads": channel_spreads,
        "scope": "simulation-only; hardware timing and SRAM validation remain pending",
    }
    INHIBITION_STUDY.parent.mkdir(parents=True, exist_ok=True)
    INHIBITION_STUDY.write_text(json.dumps(payload, indent=2) + "\n")
    print("\n1024-neuron E/I inhibition-strength study")
    print(" strength  DVI checksum       audio mean|x| ch0 / ch1 / ch2 / ch3")
    for item in results:
        audio = " / ".join(f"{value:7.1f}" for value in item["audio_mean_abs"])
        print(f" {item['inhibitory_strength']:8d}  {item['dvi_checksum']}  {audio}")
    print(" spreads                    " + " / ".join(f"{x:7.1f}" for x in channel_spreads))
    print(f"\nOVERALL: {'PASS' if passed else 'FAIL'}")
    print(f"Result: {INHIBITION_STUDY}")
    if not passed:
        raise SystemExit(1)
    if args.with_build:
        for strength in (512, 1536):
            run([
                sys.executable,
                "src/top/snn_av/top.py",
                "build",
                "--hw", args.hw,
                "--modeline", args.modeline,
                "--self-test",
                "--neurons", "1024",
                "--physical-lanes", "32",
                "--ei-ring",
                "--inhibitory-strength", str(strength),
                "--name", f"SNN-AV-1024X32-EI-{strength}-STUDY",
            ])
        inhibition_study_report(args)


def inhibition_study_report(args: argparse.Namespace) -> None:
    """Enforce R5 QoR for the two inhibition-strength study endpoints."""

    for strength in (512, 1536):
        print(f"\n1024-neuron E/I strength {strength} synthesis profile")
        evaluate_bitstream(
            ROOT / "build" / f"snn-av-1024x32-ei-{strength}-study-{args.hw}",
            EI_RING_SYNTHESIS_CONTRACT,
        )


def capture_population_profile(inhibitory_strength: int) -> dict:
    """Measure normalized E/I rates without changing the synthesized core."""

    segment_samples = 192
    warmup_samples = 64
    dut = MemoryBatchedLIFBank(
        logical_neuron_count=1024,
        physical_lane_count=32,
        ei_ring=True,
        inhibitory_strength=inhibitory_strength,
    )
    rows = []
    inhibitory_mask = sum(1 << index for index in range(3, 1024, 4))

    async def bench(ctx):
        ctx.set(dut.i.valid, 1)
        for channel in range(1, 4):
            ctx.set(dut.i.payload[channel].as_value(), 0)
        ctx.set(dut.o.ready, 1)
        while len(rows) < segment_samples * 2:
            drive = 3_000 if len(rows) < segment_samples else 12_000
            ctx.set(dut.i.payload[0].as_value(), drive)
            if ctx.get(dut.o.valid):
                spikes = ctx.get(dut.spike_vector)
                inhibitory = (spikes & inhibitory_mask).bit_count()
                excitatory = spikes.bit_count() - inhibitory
                total = ctx.get(dut.spike_count)
                if excitatory + inhibitory != total:
                    raise AssertionError(
                        "E/I population counts do not equal total spike count"
                    )
                rows.append((excitatory, inhibitory))
            await ctx.tick()

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(bench)
    sim.run()

    def summarize(segment):
        excitatory = [row[0] for row in segment]
        inhibitory = [row[1] for row in segment]
        excitatory_mean = sum(excitatory) / len(segment)
        inhibitory_mean = sum(inhibitory) / len(segment)
        excitatory_rate = excitatory_mean / 768
        inhibitory_rate = inhibitory_mean / 256
        excitatory_delta = [value - excitatory_mean for value in excitatory]
        inhibitory_delta = [value - inhibitory_mean for value in inhibitory]
        correlation_denominator = math.sqrt(
            sum(value * value for value in excitatory_delta)
            * sum(value * value for value in inhibitory_delta)
        )
        zero_lag_correlation = (
            sum(
                excitatory_value * inhibitory_value
                for excitatory_value, inhibitory_value in zip(
                    excitatory_delta, inhibitory_delta
                )
            ) / correlation_denominator
            if correlation_denominator else 0.0
        )
        return {
            "samples": len(segment),
            "excitatory_mean_spikes": excitatory_mean,
            "inhibitory_mean_spikes": inhibitory_mean,
            "excitatory_rate_per_neuron": excitatory_rate,
            "inhibitory_rate_per_neuron": inhibitory_rate,
            "inhibitory_to_excitatory_rate_ratio": (
                inhibitory_rate / excitatory_rate
            ),
            "zero_lag_population_correlation": zero_lag_correlation,
        }

    return {
        "inhibitory_strength": inhibitory_strength,
        "low_drive": summarize(rows[warmup_samples:segment_samples]),
        "high_drive": summarize(
            rows[segment_samples + warmup_samples:segment_samples * 2]
        ),
        "exact_total_count_checks": len(rows),
    }


def population_study(args: argparse.Namespace) -> None:
    """Characterize excitatory and inhibitory population firing rates."""

    quick(args)
    strengths = (512, 1024, 1536)
    profiles = [capture_population_profile(strength) for strength in strengths]
    failures = []
    for profile in profiles:
        low = profile["low_drive"]
        high = profile["high_drive"]
        if min(
            low["excitatory_rate_per_neuron"],
            low["inhibitory_rate_per_neuron"],
        ) <= 0:
            failures.append(
                f"strength {profile['inhibitory_strength']} silenced a population"
            )
        for population in ("excitatory", "inhibitory"):
            key = f"{population}_rate_per_neuron"
            if high[key] <= low[key]:
                failures.append(
                    f"strength {profile['inhibitory_strength']} {population} "
                    "population did not respond to high drive"
                )
    for drive in ("low_drive", "high_drive"):
        ratios = [
            profile[drive]["inhibitory_to_excitatory_rate_ratio"]
            for profile in profiles
        ]
        if ratios != sorted(ratios) or len(set(ratios)) != len(ratios):
            failures.append(f"{drive} normalized E/I ratio is not strictly increasing")
        excitatory_rates = [
            profile[drive]["excitatory_rate_per_neuron"]
            for profile in profiles
        ]
        if (
            excitatory_rates != sorted(excitatory_rates, reverse=True)
            or len(set(excitatory_rates)) != len(excitatory_rates)
        ):
            failures.append(f"{drive} excitatory rate is not strictly decreasing")

    payload = {
        "pass": not failures,
        "profiles": profiles,
        "failures": failures,
        "stimulus": {
            "low_drive_digital": 3000,
            "high_drive_digital": 12000,
            "segment_samples": 192,
            "discarded_warmup_samples": 64,
            "controls": "neutral",
        },
        "scope": "deterministic RTL simulation; no bitstream or hardware claim",
    }
    POPULATION_STUDY.parent.mkdir(parents=True, exist_ok=True)
    POPULATION_STUDY.write_text(json.dumps(payload, indent=2) + "\n")

    print("\n1024-neuron E/I population-rate study")
    print(" strength drive   excit rate   inhib rate   inhib/excit   correlation")
    for profile in profiles:
        for drive in ("low_drive", "high_drive"):
            item = profile[drive]
            print(
                f" {profile['inhibitory_strength']:8d} "
                f"{drive.removesuffix('_drive'):>5}   "
                f"{item['excitatory_rate_per_neuron']:.5f}      "
                f"{item['inhibitory_rate_per_neuron']:.5f}      "
                f"{item['inhibitory_to_excitatory_rate_ratio']:.5f}        "
                f"{item['zero_lag_population_correlation']:.5f}"
            )
    for failure in failures:
        print(f" failure: {failure}")
    print(f"\nOVERALL: {'PASS' if not failures else 'FAIL'}")
    print(f"Result: {POPULATION_STUDY}")
    if failures:
        raise SystemExit(1)


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
        "ei-ring", "ei-ring-report",
        "sonification", "sonification-report",
        "inhibition-study",
        "inhibition-study-report",
        "population-study",
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
        "ei-ring": ei_ring,
        "ei-ring-report": ei_ring_report,
        "sonification": sonification,
        "sonification-report": sonification_report,
        "inhibition-study": inhibition_study,
        "inhibition-study-report": inhibition_study_report,
        "population-study": population_study,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
