# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Musical stereo and modular-CV output mapping for SNN2 population state."""

from amaranth import Array, Const, Module, Mux, Signal, signed, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from tiliqua.dsp import ASQ
from tiliqua.dsp.synth import midi_note_phase_increment


class SNN2PerformanceMapper(wiring.Component):
    """Generate stereo triangle voices, 1 V/oct pitch, and a density gate.

    Excitatory activity selects a C-minor-pentatonic melody. Inhibitory
    activity selects a counter-voice, while population-normalized E/I balance
    selects a slower bass shared by the stereo pair. The pitch CV always
    reports the currently sounding melody note. A 16-step Euclidean ordering
    turns total population activity into a bounded 0/5 V gate pattern.
    """

    MELODY_NOTES = (48, 51, 53, 55, 58, 60, 63, 65)
    COUNTER_NOTES = (43, 46, 48, 51, 53, 55, 58, 60)
    BASS_NOTES = (24, 27, 29, 31, 34, 36, 39, 41)
    PITCH_VOLTS = (0.0, 0.25, 5 / 12, 7 / 12, 10 / 12, 1.0, 1.25, 17 / 12)
    PITCH_ASQ = tuple(round(volts * 4000) for volts in PITCH_VOLTS)
    EUCLIDEAN_ORDER = (0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15)
    FIVE_VOLTS_ASQ = 20_000

    def __init__(
        self,
        *,
        sample_rate=48_000,
        control_period_samples=6000,
        gate_high_samples=3000,
        wall_clock_hz=None,
    ):
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if control_period_samples < 16:
            raise ValueError("control period must be at least 16 samples")
        if not 1 <= gate_high_samples <= control_period_samples:
            raise ValueError("gate high time must fit inside one control period")
        if wall_clock_hz is not None and wall_clock_hz < 800:
            raise ValueError("wall clock must be at least 800 Hz")
        self.sample_rate = sample_rate
        self.wall_clock_hz = wall_clock_hz
        if wall_clock_hz is None:
            self.control_period_ticks = control_period_samples
            self.gate_high_ticks = gate_high_samples
        else:
            self.control_period_ticks = round(wall_clock_hz / 8)
            self.gate_high_ticks = round(wall_clock_hz / 16)
        self.control_counter = Signal(range(self.control_period_ticks))
        self.step = Signal(4)
        self.gate_remaining = Signal(
            range(self.gate_high_ticks + 1), init=self.gate_high_ticks
        )
        self.note_indices = [Signal(3, init=2) for _ in range(3)]
        self.phases = [
            Signal(32, init=value)
            for value in (0x00000000, 0x55555555, 0xAAAAAAAA)
        ]
        note_sets = (self.MELODY_NOTES, self.COUNTER_NOTES, self.BASS_NOTES)
        self._increments = tuple(
            tuple(midi_note_phase_increment(note, sample_rate) for note in notes)
            for notes in note_sets
        )
        super().__init__({
            "i": In(stream.Signature(data.ArrayLayout(ASQ, 4))),
            "excitatory_spike_count": In(unsigned(8)),
            "inhibitory_spike_count": In(unsigned(7)),
            "o": Out(stream.Signature(data.ArrayLayout(ASQ, 4))),
        })

    def elaborate(self, platform):
        m = Module()

        increments = [
            Array(Const(value, 32) for value in voice)
            for voice in self._increments
        ]
        pitch_values = Array(Const(value, signed(16)) for value in self.PITCH_ASQ)
        pattern_values = Array(Const(value, 4) for value in self.EUCLIDEAN_ORDER)
        next_indices = [Signal(3) for _ in range(3)]
        excitatory = Signal(9)
        normalized_inhibitory = Signal(9)
        total_activity = Signal(9)
        gate_density = Signal(4)
        transfer = Signal()

        m.d.comb += [
            transfer.eq(self.i.valid & self.o.ready),
            self.o.valid.eq(self.i.valid),
            self.i.ready.eq(self.o.ready),
            excitatory.eq(self.excitatory_spike_count),
            normalized_inhibitory.eq(
                (self.inhibitory_spike_count << 1)
                + self.inhibitory_spike_count
            ),
            total_activity.eq(
                self.excitatory_spike_count + self.inhibitory_spike_count
            ),
            next_indices[0].eq(Mux(
                excitatory >= 24,
                7,
                Mux(excitatory >= 20, 6,
                    Mux(excitatory >= 16, 5,
                        Mux(excitatory >= 12, 4,
                            Mux(excitatory >= 9, 3,
                                Mux(excitatory >= 6, 2,
                                    Mux(excitatory >= 3, 1, 0))))))),
            ),
            next_indices[1].eq(Mux(
                self.inhibitory_spike_count >= 8,
                7,
                self.inhibitory_spike_count[:3],
            )),
            next_indices[2].eq(Mux(
                normalized_inhibitory >= excitatory + 16,
                0,
                Mux(normalized_inhibitory >= excitatory + 8, 1,
                    Mux(normalized_inhibitory >= excitatory + 3, 2,
                        Mux(excitatory >= normalized_inhibitory + 24, 7,
                            Mux(excitatory >= normalized_inhibitory + 16, 6,
                                Mux(excitatory >= normalized_inhibitory + 8, 5,
                                    Mux(excitatory >= normalized_inhibitory + 3, 4, 3))))))),
            ),
            gate_density.eq(Mux(
                total_activity < 4,
                2,
                Mux(total_activity >= 24, 12, total_activity >> 1),
            )),
            self.o.payload[2].as_value().eq(pitch_values[self.note_indices[0]]),
            self.o.payload[3].as_value().eq(Mux(
                self.gate_remaining != 0,
                self.FIVE_VOLTS_ASQ,
                0,
            )),
        ]

        triangles = []
        for phase in self.phases:
            folded = Signal(16)
            triangle = Signal(signed(16))
            m.d.comb += [
                folded.eq(Mux(phase[31], ~phase[15:31], phase[15:31])),
                triangle.eq(folded ^ Const(0x8000, 16)),
            ]
            triangles.append(triangle)

        left_mix = Signal(signed(18))
        right_mix = Signal(signed(18))
        m.d.comb += [
            left_mix.eq(
                (triangles[0] >> 2)
                + (triangles[0] >> 3)
                + (triangles[2] >> 3)
            ),
            right_mix.eq(
                (triangles[1] >> 2)
                + (triangles[1] >> 3)
                + (triangles[2] >> 3)
            ),
            self.o.payload[0].as_value().eq(left_mix),
            self.o.payload[1].as_value().eq(right_mix),
        ]

        with m.If(transfer):
            for phase, note_index, voice_increments in zip(
                self.phases, self.note_indices, increments
            ):
                m.d.sync += phase.eq(phase + voice_increments[note_index])

        advance_clock = Const(1) if self.wall_clock_hz is not None else transfer
        with m.If(advance_clock):
            with m.If(self.control_counter == self.control_period_ticks - 1):
                m.d.sync += [
                    self.control_counter.eq(0),
                    self.step.eq(self.step + 1),
                    self.note_indices[0].eq(next_indices[0]),
                    self.note_indices[1].eq(next_indices[1]),
                    self.note_indices[2].eq(next_indices[2]),
                    self.gate_remaining.eq(Mux(
                        pattern_values[self.step] < gate_density,
                        self.gate_high_ticks,
                        0,
                    )),
                ]
            with m.Else():
                m.d.sync += self.control_counter.eq(self.control_counter + 1)
                with m.If(self.gate_remaining != 0):
                    m.d.sync += self.gate_remaining.eq(self.gate_remaining - 1)

        return m
