# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Small parsers and contract checks for FPGA quality-of-results reports."""

from __future__ import annotations

import re


def parse_resource_report(
    report_text: str, resource_names: list[str]
) -> dict[str, int]:
    """Extract final Yosys resource counts from either column order."""

    resources = {}
    for name in resource_names:
        matches = list(re.finditer(
            rf"^\s*(?:(\d+)\s+{re.escape(name)}|"
            rf"{re.escape(name)}\s+(\d+))\s*$",
            report_text,
            re.MULTILINE,
        ))
        if not matches:
            raise ValueError(f"resource {name} not found in synthesis report")
        final_match = matches[-1]
        resources[name] = int(
            final_match.group(1) or final_match.group(2)
        )
    return resources


def parse_timing_summary(timing_lines: list[str]) -> dict[str, float]:
    """Extract achieved clock frequencies from final nextpnr summary lines."""

    clocks = {}
    clock_aliases = {
        "$glbnet$clk": "sync",
        "$glbnet$audio_clk": "audio",
        "$glbnet$dvi_clk": "dvi",
        "$glbnet$dvi5x_clk": "dvi5x",
    }
    for line in timing_lines:
        match = re.search(
            r"clock\s+'?([^']+)'?:\s+([0-9.]+) MHz \((PASS|FAIL)", line
        )
        if match and match.group(1) in clock_aliases:
            clocks[clock_aliases[match.group(1)]] = float(match.group(2))
    return clocks


def evaluate_synthesis_contract(
    resources: dict[str, int], clocks: dict[str, float], contract: dict
) -> list[str]:
    """Return human-readable violations of resource and clock budgets."""

    failures = []
    for name, requirement in contract["resources"].items():
        if name not in resources:
            failures.append(f"resource {name} was not found in synthesis report")
        elif resources[name] > requirement["maximum"]:
            failures.append(
                f"{name} {resources[name]} exceeds {requirement['maximum']}"
            )
    for name, minimum in contract["minimum_clock_mhz"].items():
        if name not in clocks:
            failures.append(f"clock {name} was not found in timing report")
        elif clocks[name] < minimum:
            failures.append(
                f"clock {name} {clocks[name]:.2f} MHz is below {minimum:.2f} MHz"
            )
    return failures
