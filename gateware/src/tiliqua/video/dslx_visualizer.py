# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Amaranth wrapper around the generated DSLX pixel shader."""

from pathlib import Path

from amaranth import ClockSignal, Instance, Module, Signal, unsigned
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out


class DSLXVisualizer(wiring.Component):
    """Map pixel position and audio control values to an RGB pixel."""

    x: In(unsigned(12))
    y: In(unsigned(12))
    center_x: In(unsigned(12))
    center_y: In(unsigned(12))
    envelope: In(unsigned(16))
    gate: In(unsigned(1))
    magnitude: In(unsigned(16))
    frame: In(unsigned(8))

    r: Out(unsigned(8))
    g: Out(unsigned(8))
    b: Out(unsigned(8))

    _VERILOG_NAME = "tiliqua_dslx_visualizer.v"

    def elaborate(self, platform):
        m = Module()

        verilog_path = (
            Path(__file__).resolve().parents[3]
            / "dslx"
            / "generated"
            / self._VERILOG_NAME
        )
        if platform is not None:
            platform.add_file(self._VERILOG_NAME, verilog_path.read_text())

        packed = Signal(24)
        m.submodules.dslx = Instance(
            "tiliqua_dslx_visualizer",
            i_clk=ClockSignal("dvi"),
            i_x=self.x,
            i_y=self.y,
            i_center_x=self.center_x,
            i_center_y=self.center_y,
            i_envelope=self.envelope,
            i_gate=self.gate,
            i_magnitude=self.magnitude,
            i_frame=self.frame,
            o_out=packed,
        )

        m.d.comb += [
            self.r.eq(packed[0:8]),
            self.g.eq(packed[8:16]),
            self.b.eq(packed[16:24]),
        ]

        return m
