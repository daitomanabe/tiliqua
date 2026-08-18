#!/usr/bin/env python3

# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Manifest, integer-model, and sparse-RTL gates for SNN2 v1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from dslx_lab import evaluate_bitstream
from tiliqua.snn2 import export_manifest, make_default_manifest, validate_manifest


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "snn2" / "snn2_256x16_sparse_alif_v1.json"
GOLDEN_TRACES = ROOT / "snn2" / "golden" / "single_neuron_traces.json"
AV_CONTRACT = ROOT / "snn2" / "snn2_av_contract.json"
SYNTHESIS_CONTRACT = ROOT / "snn2" / "snn2_synthesis_contract.json"
METRICS = ROOT / "snn2-av-metrics.json"


def run(command: list[str]) -> None:
    print(f"\n[run] {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def doctor(_: argparse.Namespace) -> None:
    required = [
        ROOT / "src" / "tiliqua" / "snn2" / "manifest.py",
        ROOT / "src" / "tiliqua" / "snn2" / "reference.py",
        ROOT / "src" / "tiliqua" / "snn2" / "encoder.py",
        ROOT / "src" / "tiliqua" / "snn2" / "rtl.py",
        ROOT / "src" / "tiliqua" / "video" / "snn2_visualizer.py",
        ROOT / "src" / "top" / "snn2_av" / "top.py",
        ROOT / "src" / "top" / "snn2_av" / "sim.cpp",
        ROOT / "tests" / "test_snn2.py",
        DEFAULT_MANIFEST,
        GOLDEN_TRACES,
        AV_CONTRACT,
        SYNTHESIS_CONTRACT,
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"missing SNN2 files: {', '.join(missing)}")
    if shutil.which("verilator") is None:
        raise SystemExit("Verilator is required for the SNN2 AV contract")
    manifest = validate_manifest(json.loads(DEFAULT_MANIFEST.read_text()))
    print("SNN2 lab doctor: PASS")
    print(f"  profile            {manifest['profile']}")
    print(f"  payload SHA-256    {manifest['payload_sha256']}")
    print("  implementation     manifest + encoder + 256x16 sparse ALIF + DVI")
    print("  hardware           not loaded; SRAM validation remains separate")


def quick(_: argparse.Namespace) -> None:
    run([
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_snn2.py",
        "-k",
        "not 4096_sample and not all_neurons_spiking",
    ])


def stress(_: argparse.Namespace) -> None:
    run([
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_snn2.py",
        "-k",
        "4096_sample or all_neurons_spiking or golden_traces",
    ])


def evaluate_av_contract(metrics: dict, contract: dict) -> list[str]:
    failures = []
    if not metrics.get("self_test"):
        failures.append("simulation did not report self-test mode")
    dvi = metrics["dvi"]
    expected_dvi = contract["dvi"]
    if dvi["frames"] < expected_dvi["minimum_frames"]:
        failures.append("DVI frame count is below the frozen minimum")
    if dvi["pixels"] < expected_dvi["minimum_pixels"]:
        failures.append("DVI pixel count is below the frozen minimum")
    for channel in ("r", "g", "b"):
        if dvi[channel][1] - dvi[channel][0] < expected_dvi["minimum_rgb_span"]:
            failures.append(f"DVI {channel.upper()} span is below the minimum")

    network = metrics["network"]
    expected_network = contract["network"]
    if network["fault"]:
        failures.append("SNN2 latched a scheduler/deadline fault")
    if network["maximum_scheduler_cycles"] > expected_network["maximum_scheduler_cycles"]:
        failures.append("scheduler exceeded 640 cycles")
    if network["maximum_excitatory_spikes"] < expected_network["minimum_excitatory_spikes"]:
        failures.append("excitatory population remained silent")
    if network["maximum_inhibitory_spikes"] < expected_network["minimum_inhibitory_spikes"]:
        failures.append("inhibitory population remained silent")
    if network["band_activity_mask"] != expected_network["required_band_activity_mask"]:
        failures.append("not all eight encoder bands were observed")

    audio = {str(entry["channel"]): entry for entry in metrics["audio"]}
    for channel, expected in contract["audio"].items():
        actual = audio[channel]
        if actual["samples"] < expected.get("minimum_samples", 0):
            failures.append(f"audio {channel} sample count is too low")
        if "minimum_bipolar_peak" in expected:
            peak = expected["minimum_bipolar_peak"]
            if actual["min"] > -peak or actual["max"] < peak:
                failures.append(f"audio {channel} does not cross both polarities")
        if actual["max"] < expected.get("minimum_peak", actual["max"]):
            failures.append(f"audio {channel} peak is too low")
        if actual["max"] > expected.get("maximum_peak", actual["max"]):
            failures.append(f"audio {channel} peak exceeds the safe contract")
        if actual["min"] < expected.get("minimum_floor", actual["min"]):
            failures.append(f"audio {channel} floor exceeds the safe contract")
        if actual["nonzero"] < expected.get("minimum_nonzero_samples", 0):
            failures.append(f"audio {channel} is inactive")
    return failures


def load_and_report() -> list[str]:
    if not METRICS.is_file():
        raise SystemExit(f"metrics not found: {METRICS}")
    metrics = json.loads(METRICS.read_text())
    contract = json.loads(AV_CONTRACT.read_text())
    failures = evaluate_av_contract(metrics, contract)
    dvi = metrics["dvi"]
    network = metrics["network"]
    print("\nSNN2 AV report")
    print(f"  result             {'PASS' if not failures else 'FAIL'}")
    print(f"  neural samples     {metrics['test_sample_index']}")
    print(f"  DVI frames/pixels  {dvi['frames']} / {dvi['pixels']}")
    print(f"  DVI checksum       {dvi['checksum']}")
    print(
        "  network maxima     "
        f"E={network['maximum_excitatory_spikes']} "
        f"I={network['maximum_inhibitory_spikes']} "
        f"scheduler={network['maximum_scheduler_cycles']}"
    )
    for entry in metrics["audio"]:
        print(
            f"  output {entry['channel']}           samples={entry['samples']} "
            f"min={entry['min']} max={entry['max']} mean|x|={entry['mean_abs']:.1f}"
        )
    for failure in failures:
        print(f"  failure            {failure}")
    return failures


def av_report(_: argparse.Namespace) -> None:
    if load_and_report():
        raise SystemExit(1)


def integration(args: argparse.Namespace) -> None:
    METRICS.unlink(missing_ok=True)
    run([
        sys.executable,
        "src/top/snn2_av/top.py",
        "sim",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--self-test",
    ])
    av_report(args)


def build(args: argparse.Namespace) -> None:
    for profile, self_test in (("lab", True), ("live", False)):
        command = [
            sys.executable,
            "src/top/snn2_av/top.py",
            "build",
            "--hw", args.hw,
            "--modeline", args.modeline,
        ]
        if self_test:
            command.extend(("--self-test", "--nextpnr-seed", "2"))
        run(command)
        evaluate_bitstream(
            ROOT / "build" / f"snn2-av-{profile}-{args.hw}",
            SYNTHESIS_CONTRACT,
        )


def check(args: argparse.Namespace) -> None:
    doctor(args)
    run([sys.executable, "-m", "pytest", "-q", "tests/test_snn2.py"])
    integration(args)
    if args.with_build:
        build(args)


def report(_: argparse.Namespace) -> None:
    manifest = validate_manifest(json.loads(DEFAULT_MANIFEST.read_text()))
    print("SNN2 v1 phase report")
    print("  reference freeze   implemented")
    print("  neuron engine      implemented: 256 logical / 16 physical lanes")
    print("  sparse scheduler   implemented: 2048 edges / 4 banks / no-drop counter")
    print("  AV path            implemented: 8-band encoder / 4 outputs / DVI")
    print("  R5 synthesis/QoR   implemented: self-test + live profiles")
    print("  SRAM validation    pending")
    print(f"  payload SHA-256    {manifest['payload_sha256']}")


def import_manifest(args: argparse.Namespace) -> None:
    source = Path(args.manifest).resolve()
    manifest = validate_manifest(json.loads(source.read_text()))
    output = (
        Path(args.output).resolve()
        if args.output
        else ROOT / "build" / "snn2-import" / manifest["payload_sha256"][:16]
    )
    derived = export_manifest(manifest, output)
    print("SNN2 manifest import: PASS")
    print(f"  source             {source}")
    print(f"  output             {output}")
    print(f"  payload SHA-256    {derived['payload_sha256']}")
    print(f"  derived files      {len(derived['files'])}")
    print("  hardware action    none")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hw", default="r5")
    parser.add_argument("--modeline", default="720x720p60r2")
    parser.add_argument("--with-build", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor")
    subparsers.add_parser("quick")
    subparsers.add_parser("stress")
    subparsers.add_parser("report")
    subparsers.add_parser("av-report")
    subparsers.add_parser("check")
    importer = subparsers.add_parser("import-manifest")
    importer.add_argument("manifest")
    importer.add_argument("--output")
    return parser


def main() -> None:
    args = make_parser().parse_args()
    commands = {
        "doctor": doctor,
        "quick": quick,
        "stress": stress,
        "report": report,
        "av-report": av_report,
        "check": check,
        "import-manifest": import_manifest,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
