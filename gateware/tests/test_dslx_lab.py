# Copyright (c) 2026 Tiliqua contributors
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

from tiliqua.build.qor import (
    evaluate_synthesis_contract,
    parse_resource_report,
    parse_timing_summary,
)


def test_synthesis_report_parsers_use_final_summary():
    resources = parse_resource_report(
        """
            99 LUT4
             3 MULT18X18D
          1090 LUT4
             1 MULT18X18D
        """,
        ["LUT4", "MULT18X18D"],
    )
    assert resources == {"LUT4": 1090, "MULT18X18D": 1}

    clocks = parse_timing_summary([
        "Info: Max frequency for clock       '$glbnet$clk': "
        "64.23 MHz (PASS at 60.00 MHz)",
        "Info: Max frequency for clock '$glbnet$audio_clk': "
        "75.00 MHz (PASS at 12.29 MHz)",
    ])
    assert clocks == {"sync": 64.23, "audio": 75.0}


def test_synthesis_contract_reports_resource_and_timing_regressions():
    failures = evaluate_synthesis_contract(
        {"LUT4": 1201, "MULT18X18D": 1},
        {"sync": 59.9},
        {
            "resources": {
                "LUT4": {"maximum": 1200},
                "MULT18X18D": {"maximum": 1},
            },
            "minimum_clock_mhz": {"sync": 60.0},
        },
    )
    assert len(failures) == 2
    assert "LUT4" in failures[0]
    assert "sync" in failures[1]
