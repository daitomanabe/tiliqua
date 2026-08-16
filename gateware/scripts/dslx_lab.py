#!/usr/bin/env python3

# Copyright (c) 2026 Tiliqua contributors
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""One-command local regression loop for the DSLX synth laboratory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from tiliqua.build.qor import (
    evaluate_synthesis_contract,
    parse_resource_report,
    parse_timing_summary,
)


GATEWARE_ROOT = Path(__file__).resolve().parents[1]
METRICS_PATH = GATEWARE_ROOT / "dslx-av-metrics.json"
CONTRACT_PATH = GATEWARE_ROOT / "dslx" / "lab_contract.json"
SYNTHESIS_CONTRACT_PATH = (
    GATEWARE_ROOT / "dslx" / "lab_synthesis_contract.json"
)


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    printable = " ".join(command)
    print(f"\n[run] {printable}", flush=True)
    subprocess.run(command, cwd=GATEWARE_ROOT, env=env, check=True)


def tool_version(executable: str, *arguments: str) -> str | None:
    path = shutil.which(executable)
    if path is None:
        return None
    if Path(path).name.startswith("yowasp-"):
        return f"{path} (probe skipped; prefer native OSS CAD Suite if builds stall)"
    try:
        result = subprocess.run(
            [path, *arguments],
            cwd=GATEWARE_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return f"{path} (version check timed out)"
    first_line = result.stdout.strip().splitlines()
    return first_line[0] if first_line else path


def resolve_xlsynth_driver(requested: str | None) -> str | None:
    candidate = requested or os.environ.get("XLSYNTH_DRIVER")
    if candidate:
        path = Path(candidate).expanduser()
        return str(path.resolve()) if path.is_file() else None
    return shutil.which("xlsynth-driver")


def doctor(args: argparse.Namespace) -> None:
    checks = [
        ("Python", sys.version.split()[0]),
        ("Verilator", tool_version("verilator", "--version")),
        ("openFPGALoader", tool_version("openFPGALoader", "-V")),
        ("Yosys", tool_version(os.environ.get("YOSYS", "yosys"), "-V")),
        ("nextpnr-ecp5", tool_version(
            os.environ.get("NEXTPNR_ECP5", "nextpnr-ecp5"), "--version"
        )),
        ("xlsynth-driver", resolve_xlsynth_driver(args.xlsynth_driver)),
    ]

    print("DSLX synth lab doctor")
    for label, value in checks:
        print(f"  {label:18} {value or 'not found (optional for checked-in RTL)'}")

    required_files = [
        GATEWARE_ROOT / "dslx" / "generated" / "tiliqua_dslx_adsr.v",
        GATEWARE_ROOT / "dslx" / "generated" / "tiliqua_dslx_reactor.v",
        GATEWARE_ROOT / "dslx" / "generated" / "tiliqua_dslx_voice.v",
        GATEWARE_ROOT / "dslx" / "generated" / "tiliqua_dslx_visualizer.v",
        CONTRACT_PATH,
        SYNTHESIS_CONTRACT_PATH,
    ]
    missing = [str(path.relative_to(GATEWARE_ROOT)) for path in required_files
               if not path.is_file() or path.stat().st_size == 0]
    if missing:
        raise SystemExit(f"missing required lab files: {', '.join(missing)}")

    if checks[1][1] is None:
        raise SystemExit("Verilator is required for integration simulation")

    if args.hardware:
        run(["openFPGALoader", "--scan-usb"])


def quick(args: argparse.Namespace) -> None:
    run([
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_synth.py",
        "tests/test_dslx_lab.py",
        "tests/test_pitch_calibration.py",
    ])

    driver = resolve_xlsynth_driver(args.xlsynth_driver)
    if driver is None:
        print("\n[skip] xlsynth-driver not found; using checked-in generated RTL")
        return

    env = os.environ.copy()
    env["XLSYNTH_DRIVER"] = driver
    run(["./scripts/test_dslx_adsr.sh"], env=env)
    run(["./scripts/test_dslx_reactor.sh"], env=env)
    run(["./scripts/test_dslx_voice.sh"], env=env)
    run(["./scripts/test_dslx_visualizer.sh"], env=env)


def load_metrics() -> dict:
    if not METRICS_PATH.is_file():
        raise SystemExit(f"metrics not found: {METRICS_PATH}")
    return json.loads(METRICS_PATH.read_text())


def evaluate_contract(metrics: dict, contract: dict) -> list[str]:
    failures: list[str] = []
    dvi = metrics["dvi"]
    dvi_contract = contract["dvi"]

    if dvi["frames"] < dvi_contract["minimum_frames"]:
        failures.append(f"DVI frames {dvi['frames']} below minimum")
    if dvi["pixels"] < dvi_contract["minimum_pixels"]:
        failures.append(f"DVI pixels {dvi['pixels']} below minimum")
    for channel in ("r", "g", "b"):
        span = dvi[channel][1] - dvi[channel][0]
        if span < dvi_contract["minimum_rgb_span"]:
            failures.append(f"DVI {channel.upper()} span {span} below minimum")

    audio = {str(entry["channel"]): entry for entry in metrics["audio"]}
    for channel, expected in contract["audio"].items():
        actual = audio[channel]
        if actual["samples"] < expected.get("minimum_samples", 0):
            failures.append(f"audio {channel} sample count is too low")
        if "minimum_bipolar_peak" in expected:
            peak = expected["minimum_bipolar_peak"]
            if actual["min"] > -peak or actual["max"] < peak:
                failures.append(f"audio {channel} is not bipolar above {peak}")
        if actual["max"] < expected.get("minimum_peak", 0):
            failures.append(f"audio {channel} peak is too low")
        if actual["min"] > expected.get("maximum_floor", actual["min"]):
            failures.append(f"audio {channel} floor is too high")

    if not metrics.get("self_test"):
        failures.append("simulation did not report self-test mode")
    return failures


def print_report(metrics: dict, failures: list[str]) -> None:
    dvi = metrics["dvi"]
    print("\nDSLX synth lab report")
    print(f"  result             {'PASS' if not failures else 'FAIL'}")
    print(f"  self-test samples  {metrics['test_sample_index']}")
    print(f"  DVI frames/pixels  {dvi['frames']} / {dvi['pixels']}")
    print(f"  DVI checksum       {dvi['checksum']}")
    print(f"  RGB ranges         R{dvi['r']} G{dvi['g']} B{dvi['b']}")
    print("  audio              ch  samples      min      max  mean|x|")
    for entry in metrics["audio"]:
        print(
            f"                     {entry['channel']:>2}  {entry['samples']:>7}  "
            f"{entry['min']:>7}  {entry['max']:>7}  {entry['mean_abs']:>7.1f}"
        )
    for failure in failures:
        print(f"  failure            {failure}")


def report(_: argparse.Namespace) -> None:
    metrics = load_metrics()
    contract = json.loads(CONTRACT_PATH.read_text())
    failures = evaluate_contract(metrics, contract)
    print_report(metrics, failures)
    if failures:
        raise SystemExit(1)


def integration(args: argparse.Namespace) -> None:
    METRICS_PATH.unlink(missing_ok=True)
    run([
        sys.executable,
        "src/top/dslx_av/top.py",
        "sim",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--self-test",
    ])
    report(args)


def evaluate_bitstream(
    build_dir: Path,
    synthesis_contract_path: Path = SYNTHESIS_CONTRACT_PATH,
) -> None:
    timing_path = build_dir / "top.tim"
    report_path = build_dir / "top.rpt"
    bitstream_path = build_dir / "top.bit"
    if (
        not timing_path.is_file()
        or not report_path.is_file()
        or not bitstream_path.is_file()
    ):
        raise SystemExit("build did not produce top.tim, top.rpt and top.bit")

    synthesis_contract = json.loads(synthesis_contract_path.read_text())
    resources = parse_resource_report(
        report_path.read_text(), list(synthesis_contract["resources"])
    )

    timing_lines = [
        line.strip() for line in timing_path.read_text().splitlines()
        if "Max frequency for clock" in line
    ][-4:]
    print("\nFPGA timing summary")
    for line in timing_lines:
        print(f"  {line}")
    clocks = parse_timing_summary(timing_lines)

    print("\nFPGA resource summary")
    for name, actual in resources.items():
        maximum = synthesis_contract["resources"][name]["maximum"]
        print(f"  {name:18} {actual:>5} / {maximum:<5}")

    failures = evaluate_synthesis_contract(
        resources, clocks, synthesis_contract
    )
    if any("FAIL" in line for line in timing_lines):
        failures.append("nextpnr reported a timing failure")
    for failure in failures:
        print(f"  failure            {failure}")
    if failures:
        raise SystemExit("FPGA synthesis contract failed")

    digest = hashlib.sha256(bitstream_path.read_bytes()).hexdigest()
    print(f"  bitstream          {bitstream_path}")
    print(f"  sha256             {digest}")


def build_bitstream(args: argparse.Namespace) -> None:
    run([
        sys.executable,
        "src/top/dslx_av/top.py",
        "build",
        "--hw", args.hw,
        "--modeline", args.modeline,
        "--self-test",
    ])

    build_dir = GATEWARE_ROOT / "build" / f"dslx-av-lab-{args.hw}"
    evaluate_bitstream(build_dir)


def check(args: argparse.Namespace) -> None:
    doctor(args)
    quick(args)
    integration(args)
    if args.with_build:
        build_bitstream(args)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--xlsynth-driver",
        help="Absolute path to the pinned xlsynth-driver (optional).",
    )
    parser.add_argument("--hw", default="r5", help="Tiliqua hardware revision")
    parser.add_argument(
        "--modeline", default="720x720p60r2", help="Static DVI modeline"
    )
    parser.add_argument(
        "--hardware",
        action="store_true",
        help="Doctor only: include a read-only USB/JTAG scan.",
    )
    parser.add_argument(
        "--with-build",
        action="store_true",
        help="Check only: also synthesize and enforce the FPGA timing report.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="Check local tools and generated RTL")
    subparsers.add_parser("quick", help="Run unit and DSLX vector tests")
    subparsers.add_parser("sim", help="Run self-test AV simulation and contract")
    subparsers.add_parser("report", help="Re-evaluate the latest metrics")
    subparsers.add_parser("build", help="Build self-test bitstream and enforce timing")
    subparsers.add_parser("check", help="Run doctor, quick tests and simulation")
    return parser


def main() -> None:
    args = make_parser().parse_args()
    commands = {
        "doctor": doctor,
        "quick": quick,
        "sim": integration,
        "report": report,
        "build": build_bitstream,
        "check": check,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
