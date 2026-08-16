# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Streaming state wrapper around the generated DSLX ADSR step function."""

import enum
from pathlib import Path

from amaranth import ClockSignal, Instance, Module, Mux, Signal, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out


class ADSRPhase(enum.IntEnum):
    IDLE = 0
    ATTACK = 1
    DECAY = 2
    SUSTAIN = 3
    RELEASE = 4


ADSR_INPUT_LAYOUT = data.StructLayout({
    "gate": unsigned(1),
    "attack_step": unsigned(16),
    "decay_step": unsigned(16),
    "sustain_level": unsigned(16),
    "release_step": unsigned(16),
})

ADSR_OUTPUT_LAYOUT = data.StructLayout({
    "level": unsigned(16),
    "phase": unsigned(3),
    "gate": unsigned(1),
})


class DSLXADSR(wiring.Component):
    """Advance an ADSR only when an input sample is accepted.

    The generated DSLX is a pure combinational step function. This wrapper
    owns its previous gate, phase and level, and adds one elastic output
    register so state and output remain frozen under stream backpressure.
    """

    i: In(stream.Signature(ADSR_INPUT_LAYOUT))
    o: Out(stream.Signature(ADSR_OUTPUT_LAYOUT))

    _PACKED_WIDTH = 20
    _VERILOG_NAME = "tiliqua_dslx_adsr.v"

    def elaborate(self, platform):
        m = Module()

        previous_gate = Signal()
        phase = Signal(3, init=ADSRPhase.IDLE)
        level = Signal(16)
        packed = Signal(self._PACKED_WIDTH)

        if platform is None:
            active_phase = Signal(3)
            next_phase = Signal(3)
            next_level = Signal(16)
            attack_wide = Signal(17)
            decay_distance = Signal(16)

            m.d.comb += [
                active_phase.eq(Mux(
                    self.i.payload.gate & ~previous_gate,
                    ADSRPhase.ATTACK,
                    Mux(
                        ~self.i.payload.gate & previous_gate,
                        ADSRPhase.RELEASE,
                        phase,
                    ),
                )),
                attack_wide.eq(level + self.i.payload.attack_step),
                decay_distance.eq(level - self.i.payload.sustain_level),
                next_level.eq(0),
                next_phase.eq(ADSRPhase.IDLE),
            ]

            with m.Switch(active_phase):
                with m.Case(ADSRPhase.ATTACK):
                    with m.If(attack_wide >= 65535):
                        m.d.comb += [
                            next_level.eq(65535),
                            next_phase.eq(ADSRPhase.DECAY),
                        ]
                    with m.Else():
                        m.d.comb += [
                            next_level.eq(attack_wide),
                            next_phase.eq(ADSRPhase.ATTACK),
                        ]
                with m.Case(ADSRPhase.DECAY):
                    with m.If(
                        (level <= self.i.payload.sustain_level)
                        | (self.i.payload.decay_step >= decay_distance)
                    ):
                        m.d.comb += [
                            next_level.eq(self.i.payload.sustain_level),
                            next_phase.eq(ADSRPhase.SUSTAIN),
                        ]
                    with m.Else():
                        m.d.comb += [
                            next_level.eq(level - self.i.payload.decay_step),
                            next_phase.eq(ADSRPhase.DECAY),
                        ]
                with m.Case(ADSRPhase.SUSTAIN):
                    m.d.comb += [
                        next_level.eq(self.i.payload.sustain_level),
                        next_phase.eq(ADSRPhase.SUSTAIN),
                    ]
                with m.Case(ADSRPhase.RELEASE):
                    with m.If(level <= self.i.payload.release_step):
                        m.d.comb += [
                            next_level.eq(0),
                            next_phase.eq(ADSRPhase.IDLE),
                        ]
                    with m.Else():
                        m.d.comb += [
                            next_level.eq(level - self.i.payload.release_step),
                            next_phase.eq(ADSRPhase.RELEASE),
                        ]

            m.d.comb += packed.eq(
                (self.i.payload.gate << 19)
                | (next_phase << 16)
                | next_level
            )
        else:
            verilog_path = (
                Path(__file__).resolve().parents[3]
                / "dslx"
                / "generated"
                / self._VERILOG_NAME
            )
            platform.add_file(self._VERILOG_NAME, verilog_path.read_text())
            m.submodules.dslx = Instance(
                "tiliqua_dslx_adsr",
                i_clk=ClockSignal("sync"),
                i_gate=self.i.payload.gate,
                i_previous_gate=previous_gate,
                i_phase=phase,
                i_level=level,
                i_attack=self.i.payload.attack_step,
                i_decay=self.i.payload.decay_step,
                i_sustain=self.i.payload.sustain_level,
                i_release_amount=self.i.payload.release_step,
                o_out=packed,
            )

        output_valid = Signal()
        output_payload = Signal(ADSR_OUTPUT_LAYOUT)
        accept_input = Signal()
        m.d.comb += [
            accept_input.eq(~output_valid | self.o.ready),
            self.i.ready.eq(accept_input),
            self.o.valid.eq(output_valid),
            self.o.payload.eq(output_payload),
        ]

        with m.If(accept_input):
            m.d.sync += output_valid.eq(self.i.valid)
            with m.If(self.i.valid):
                m.d.sync += [
                    previous_gate.eq(packed[19]),
                    phase.eq(packed[16:19]),
                    level.eq(packed[0:16]),
                    output_payload.level.eq(packed[0:16]),
                    output_payload.phase.eq(packed[16:19]),
                    output_payload.gate.eq(packed[19]),
                ]

        return m
