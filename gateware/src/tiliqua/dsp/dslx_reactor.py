# Copyright (c) 2026 Tiliqua contributors
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Streaming wrapper around the generated DSLX audio reactor."""

from pathlib import Path

from amaranth import ClockSignal, Instance, Module, Signal
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from tiliqua.build.types import BitstreamHelp
from tiliqua.dsp import ASQ


class DSLXReactor(wiring.Component):
    """Four-channel Tiliqua core backed by generated Google DSLX logic."""

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    bitstream_help = BitstreamHelp(
        brief="DSLX envelope follower and gate",
        io_left=[
            "signal", "threshold CV", "reserved", "reserved",
            "passthrough", "envelope", "5V gate", "magnitude",
        ],
        io_right=["", "", "", "", "", ""],
    )

    _PACKED_WIDTH = 81
    _VERILOG_NAME = "tiliqua_dslx_reactor.v"

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

        previous_envelope = Signal(16)
        previous_gate = Signal()
        packed = Signal(self._PACKED_WIDTH)
        output_valid = Signal()
        output_payload = Signal(data.ArrayLayout(ASQ, 4))
        accept_input = Signal()

        m.submodules.dslx = Instance(
            "tiliqua_dslx_reactor",
            i_clk=ClockSignal("sync"),
            i_sample=self.i.payload[0].as_value(),
            i_threshold_cv=self.i.payload[1].as_value(),
            i_previous_envelope=previous_envelope,
            i_previous_gate=previous_gate,
            o_out=packed,
        )

        m.d.comb += [
            accept_input.eq(~output_valid | self.o.ready),
            self.i.ready.eq(accept_input),
            self.o.valid.eq(output_valid),
            self.o.payload.eq(output_payload),
        ]

        # A one-entry elastic output register keeps the DSLX combinational path
        # local to this core and holds both payload and state under backpressure.
        with m.If(accept_input):
            m.d.sync += output_valid.eq(self.i.valid)
            with m.If(self.i.valid):
                m.d.sync += [
                    previous_envelope.eq(packed[0:16]),
                    previous_gate.eq(packed[16]),
                    output_payload[0].eq(packed[17:33]),
                    output_payload[1].eq(packed[33:49]),
                    output_payload[2].eq(packed[49:65]),
                    output_payload[3].eq(packed[65:81]),
                ]

        return m
