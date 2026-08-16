# Copyright (c) 2026 Tiliqua contributors
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Streaming wrapper around the generated DSLX oscillator voice shaper."""

from pathlib import Path

from amaranth import ClockSignal, Const, Instance, Module, Mux, Signal, signed
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from tiliqua.dsp import ASQ


VOICE_SHAPER_LAYOUT = data.StructLayout({
    "saw": ASQ,
    "triangle": ASQ,
    "square": ASQ,
    "waveform": 2,
    "drive": 2,
})


class DSLXVoice(wiring.Component):
    """Select one oscillator waveform and apply saturating power-of-two drive."""

    i: In(stream.Signature(VOICE_SHAPER_LAYOUT))
    o: Out(stream.Signature(ASQ))

    _VERILOG_NAME = "tiliqua_dslx_voice.v"

    def elaborate(self, platform):
        m = Module()

        shaped = Signal(signed(16))
        if platform is None:
            # Amaranth simulation model. DSLX fixed vectors independently prove
            # that this expression stays bit-accurate with the generated RTL.
            selected = Signal(signed(16))
            driven = Signal(signed(19))
            m.d.comb += [
                selected.eq(Mux(
                    self.i.payload.waveform == 0,
                    self.i.payload.saw.as_value(),
                    Mux(
                        self.i.payload.waveform == 1,
                        self.i.payload.triangle.as_value(),
                        Mux(
                            self.i.payload.waveform == 2,
                            self.i.payload.square.as_value(),
                            0,
                        ),
                    ),
                )),
                driven.eq(selected << self.i.payload.drive),
                shaped.eq(Mux(
                    driven > 32767,
                    Const(32767, signed(16)),
                    Mux(
                        driven < -32768,
                        Const(-32768, signed(16)),
                        driven,
                    ),
                )),
            ]
        else:
            verilog_path = (
                Path(__file__).resolve().parents[3]
                / "dslx"
                / "generated"
                / self._VERILOG_NAME
            )
            platform.add_file(self._VERILOG_NAME, verilog_path.read_text())
            m.submodules.dslx = Instance(
                "tiliqua_dslx_voice",
                i_clk=ClockSignal("sync"),
                i_saw=self.i.payload.saw.as_value(),
                i_triangle=self.i.payload.triangle.as_value(),
                i_square=self.i.payload.square.as_value(),
                i_waveform=self.i.payload.waveform,
                i_drive=self.i.payload.drive,
                o_out=shaped,
            )

        output_valid = Signal()
        output_payload = Signal(ASQ)
        accept_input = Signal()
        m.d.comb += [
            accept_input.eq(~output_valid | self.o.ready),
            self.i.ready.eq(accept_input),
            self.o.valid.eq(output_valid),
            self.o.payload.eq(output_payload),
        ]

        # One elastic register limits the DSLX combinational path and holds the
        # chosen sample stable while the downstream stream is backpressured.
        with m.If(accept_input):
            m.d.sync += output_valid.eq(self.i.valid)
            with m.If(self.i.valid):
                m.d.sync += output_payload.as_value().eq(shaped)

        return m
