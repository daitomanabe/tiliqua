# Copyright (c) 2026 Tiliqua contributors
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import unittest

from amaranth.sim import Simulator

from tiliqua.dsp.dslx_adsr import ADSRPhase, DSLXADSR
from tiliqua.dsp.dslx_voice import DSLXVoice
from tiliqua.dsp.vca import VCA
from tiliqua.dsp.synth import (
    PHASE_BITS,
    BasicNCO,
    BasicVoice,
    CVPlayableVoice,
    CVSynthCore,
    DyadicGain,
    DyadicRounding,
    PlayableVoice,
    QuantizedPitchCV,
    SynthTestSource,
    SynthControlTestSource,
    Waveform,
    midi_note_frequency,
    midi_note_phase_increment,
    phase_increment,
)


class SynthLibraryTests(unittest.TestCase):

    def test_vca_latches_operands_before_upstream_advances(self):
        dut = VCA()

        async def bench(ctx):
            ctx.set(dut.o.ready, 1)
            ctx.set(dut.i.payload[0].as_value(), 16_384)  # +0.5
            ctx.set(dut.i.payload[1].as_value(), 16_384)  # +0.5
            ctx.set(dut.i.valid, 1)
            while not ctx.get(dut.i.ready):
                await ctx.tick()
            await ctx.tick()

            # A legal producer may change its payload immediately after the
            # transfer. The in-flight multiplication must still be 0.5*0.5.
            ctx.set(dut.i.valid, 0)
            ctx.set(dut.i.payload[0].as_value(), 0)
            ctx.set(dut.i.payload[1].as_value(), 0)
            while not ctx.get(dut.o.valid):
                await ctx.tick()
            self.assertEqual(ctx.get(dut.o.payload.as_value()), 8_192)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

    def test_playable_voice_aligns_dslx_adsr_with_one_vca(self):
        dut = PlayableVoice()
        observed = []

        async def push(ctx, gate):
            ctx.set(dut.i.payload.phase_increment, 0)
            ctx.set(dut.i.payload.waveform, Waveform.SQUARE)
            ctx.set(dut.i.payload.drive, 0)
            ctx.set(dut.i.payload.gate, gate)
            ctx.set(dut.i.payload.attack_step, 32_768)
            ctx.set(dut.i.payload.decay_step, 4_096)
            ctx.set(dut.i.payload.sustain_level, 32_768)
            ctx.set(dut.i.payload.release_step, 32_768)
            ctx.set(dut.i.valid, 1)
            while not ctx.get(dut.i.ready):
                await ctx.tick()
            await ctx.tick()
            ctx.set(dut.i.valid, 0)
            while not ctx.get(dut.o.valid):
                await ctx.tick()
            observed.append((
                ctx.get(dut.o.payload.sample.as_value()),
                ctx.get(dut.o.payload.envelope),
                ctx.get(dut.o.payload.phase),
            ))
            await ctx.tick()

        async def bench(ctx):
            ctx.set(dut.o.ready, 1)
            for gate in (1, 1, 0, 0):
                await push(ctx, gate)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

        self.assertEqual(
            [(envelope, phase) for _, envelope, phase in observed],
            [
                (32_768, ADSRPhase.ATTACK),
                (65_535, ADSRPhase.DECAY),
                (32_767, ADSRPhase.RELEASE),
                (0, ADSRPhase.IDLE),
            ],
        )
        self.assertLess(observed[0][0], -16_000)
        self.assertLess(observed[1][0], -32_000)
        self.assertLess(observed[2][0], -16_000)
        self.assertEqual(observed[3][0], 0)

    def test_cv_playable_voice_aligns_pitch_gate_and_audio(self):
        dut = CVPlayableVoice(base_note=48, sample_rate=48_000)
        observed = []

        async def push(ctx, cv_counts, gate):
            ctx.set(dut.i.payload.pitch_cv.as_value(), cv_counts)
            ctx.set(dut.i.payload.waveform, Waveform.SQUARE)
            ctx.set(dut.i.payload.drive, 0)
            ctx.set(dut.i.payload.gate, gate)
            ctx.set(dut.i.payload.attack_step, 65_535)
            ctx.set(dut.i.payload.decay_step, 0)
            ctx.set(dut.i.payload.sustain_level, 65_535)
            ctx.set(dut.i.payload.release_step, 65_535)
            ctx.set(dut.i.valid, 1)
            while not ctx.get(dut.i.ready):
                await ctx.tick()
            await ctx.tick()
            ctx.set(dut.i.valid, 0)
            while not ctx.get(dut.o.valid):
                await ctx.tick()
            observed.append((
                ctx.get(dut.o.payload.note),
                ctx.get(dut.o.payload.envelope),
                ctx.get(dut.o.payload.sample.as_value()),
            ))
            await ctx.tick()

        async def bench(ctx):
            ctx.set(dut.o.ready, 1)
            await push(ctx, 0, 1)
            await push(ctx, 4_000, 1)
            self.assertEqual(
                ctx.get(dut.oscillator_phase),
                (
                    midi_note_phase_increment(48, 48_000)
                    + midi_note_phase_increment(60, 48_000)
                ) & 0xFFFFFFFF,
            )
            await push(ctx, 4_000, 0)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

        self.assertEqual([item[0] for item in observed], [48, 60, 60])
        self.assertEqual([item[1] for item in observed], [65_535, 65_535, 0])
        self.assertLess(observed[0][2], -32_000)
        self.assertEqual(observed[2][2], 0)

    def test_cv_synth_core_maps_pitch_gate_and_diagnostic_outputs(self):
        dut = CVSynthCore(
            waveform=Waveform.SQUARE,
            attack_step=65_535,
            decay_step=0,
            sustain_level=65_535,
            release_step=65_535,
        )
        observed = []

        async def push(ctx, pitch_cv, gate_cv):
            ctx.set(dut.i.payload[0].as_value(), pitch_cv)
            ctx.set(dut.i.payload[1].as_value(), gate_cv)
            ctx.set(dut.i.payload[2].as_value(), 0)
            ctx.set(dut.i.payload[3].as_value(), 0)
            ctx.set(dut.i.valid, 1)
            while not ctx.get(dut.i.ready):
                await ctx.tick()
            await ctx.tick()
            ctx.set(dut.i.valid, 0)
            while not ctx.get(dut.o.valid):
                await ctx.tick()
            observed.append((
                ctx.get(dut.note),
                ctx.get(dut.o.payload[0].as_value()),
                ctx.get(dut.o.payload[1].as_value()),
                ctx.get(dut.o.payload[2].as_value()),
                ctx.get(dut.o.payload[3].as_value()),
            ))
            await ctx.tick()

        async def bench(ctx):
            ctx.set(dut.o.ready, 1)
            # GateDetector reports the registered hysteresis state, so each
            # physical transition intentionally takes effect one sample later.
            for gate_cv in (20_000, 20_000, 0, 0):
                await push(ctx, 4_000, gate_cv)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

        self.assertEqual([row[0] for row in observed], [60, 60, 60, 60])
        self.assertEqual([row[2] for row in observed], [0, 32_767, 32_767, 0])
        self.assertEqual([row[3] for row in observed], [0, 20_000, 20_000, 0])
        self.assertEqual(observed[0][1], 0)
        self.assertLess(observed[1][1], -32_000)
        self.assertEqual(observed[3][1], 0)
        self.assertGreater(observed[1][4], 32_000)

    def test_synth_control_source_freezes_under_backpressure(self):
        dut = SynthControlTestSource()

        async def bench(ctx):
            ctx.set(dut.o.ready, 0)
            for _ in range(8):
                await ctx.tick()
            self.assertEqual(ctx.get(dut.sample_index), 0)
            self.assertEqual(ctx.get(dut.o.payload[0].as_value()), 0)
            self.assertEqual(ctx.get(dut.o.payload[1].as_value()), 20_000)
            ctx.set(dut.o.ready, 1)
            for _ in range(2_049):
                await ctx.tick()
            self.assertEqual(ctx.get(dut.sample_index), 2_049)
            self.assertEqual(ctx.get(dut.o.payload[0].as_value()), 4_000)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

    def test_synth_control_source_tracks_compile_time_pitch_calibration(self):
        dut = SynthControlTestSource(
            pitch_shift=2,
            gate_shift=1,
            zero_cv_counts=100,
            counts_per_octave=4_090,
        )
        pitches = []

        async def bench(ctx):
            ctx.set(dut.o.ready, 1)
            for _ in range(5):
                pitches.append(ctx.get(dut.o.payload[0].as_value()))
                await ctx.tick()

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        self.assertEqual(pitches, [100, 100, 100, 100, 4_190])

    def test_dslx_adsr_sequence_and_backpressure(self):
        dut = DSLXADSR()
        observed = []

        async def push(ctx, gate):
            ctx.set(dut.i.payload.gate, gate)
            ctx.set(dut.i.payload.attack_step, 40_000)
            ctx.set(dut.i.payload.decay_step, 20_000)
            ctx.set(dut.i.payload.sustain_level, 30_000)
            ctx.set(dut.i.payload.release_step, 10_000)
            ctx.set(dut.i.valid, 1)
            while not ctx.get(dut.i.ready):
                await ctx.tick()
            await ctx.tick()
            ctx.set(dut.i.valid, 0)
            while not ctx.get(dut.o.valid):
                await ctx.tick()
            observed.append((
                ctx.get(dut.o.payload.level),
                ctx.get(dut.o.payload.phase),
            ))
            await ctx.tick()

        async def bench(ctx):
            ctx.set(dut.o.ready, 1)
            for gate in (0, 1, 1, 1, 1, 0, 0, 0):
                await push(ctx, gate)

            # Fill the elastic output register and prove its value is held.
            ctx.set(dut.o.ready, 0)
            ctx.set(dut.i.payload.gate, 1)
            ctx.set(dut.i.payload.attack_step, 1_000)
            ctx.set(dut.i.payload.decay_step, 1_000)
            ctx.set(dut.i.payload.sustain_level, 20_000)
            ctx.set(dut.i.payload.release_step, 1_000)
            ctx.set(dut.i.valid, 1)
            await ctx.tick()
            ctx.set(dut.i.valid, 0)
            held = (
                ctx.get(dut.o.payload.level),
                ctx.get(dut.o.payload.phase),
            )
            for _ in range(4):
                await ctx.tick()
                self.assertEqual(ctx.get(dut.i.ready), 0)
                self.assertEqual(
                    (ctx.get(dut.o.payload.level), ctx.get(dut.o.payload.phase)),
                    held,
                )

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

        self.assertEqual(observed, [
            (0, ADSRPhase.IDLE),
            (40_000, ADSRPhase.ATTACK),
            (65_535, ADSRPhase.DECAY),
            (45_535, ADSRPhase.DECAY),
            (30_000, ADSRPhase.SUSTAIN),
            (20_000, ADSRPhase.RELEASE),
            (10_000, ADSRPhase.RELEASE),
            (0, ADSRPhase.IDLE),
        ])

    def test_dslx_voice_simulation_model_matches_fixed_vectors(self):
        dut = DSLXVoice()
        cases = [
            (-32768, 0, 32767, 0, 0, -32768),
            (-32768, -12000, 32767, 1, 0, -12000),
            (-32768, 0, 12345, 2, 0, 12345),
            (-32768, 12345, 32767, 3, 3, 0),
            (20000, 0, 0, 0, 1, 32767),
            (-20000, 0, 0, 0, 1, -32768),
            (4096, 0, 0, 0, 2, 16384),
            (-4096, 0, 0, 0, 3, -32768),
        ]

        async def bench(ctx):
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.o.ready, 1)
            for saw, triangle, square, waveform, drive, expected in cases:
                ctx.set(dut.i.payload.saw.as_value(), saw)
                ctx.set(dut.i.payload.triangle.as_value(), triangle)
                ctx.set(dut.i.payload.square.as_value(), square)
                ctx.set(dut.i.payload.waveform, waveform)
                ctx.set(dut.i.payload.drive, drive)
                await ctx.tick()
                self.assertEqual(ctx.get(dut.o.valid), 1)
                self.assertEqual(ctx.get(dut.o.payload.as_value()), expected)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

    def test_phase_increment(self):
        increment = phase_increment(440.0, 48_000)
        actual_hz = increment * 48_000 / (1 << PHASE_BITS)
        self.assertLess(abs(actual_hz - 440.0), 0.001)

        with self.assertRaises(ValueError):
            phase_increment(-1, 48_000)
        with self.assertRaises(ValueError):
            phase_increment(440, 0)

    def test_quantized_pitch_cv_maps_octaves_without_a_multiplier(self):
        dut = QuantizedPitchCV(base_note=48, sample_rate=48_000)
        cases = [
            (0, 48),
            (4_000, 60),
            (-4_000, 36),
            (2_000, 54),
            (8_000, 72),
            (-32_768, 0),
            (32_767, 127),
        ]

        async def bench(ctx):
            ctx.set(dut.o.ready, 1)
            for cv_counts, expected_note in cases:
                ctx.set(dut.i.payload.as_value(), cv_counts)
                ctx.set(dut.i.valid, 1)
                while not ctx.get(dut.i.ready):
                    await ctx.tick()
                await ctx.tick()
                ctx.set(dut.i.valid, 0)

                while not ctx.get(dut.o.valid):
                    await ctx.tick()
                self.assertEqual(ctx.get(dut.o.payload.note), expected_note)
                self.assertEqual(
                    ctx.get(dut.o.payload.phase_increment),
                    midi_note_phase_increment(expected_note, 48_000),
                )
                await ctx.tick()

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

    def test_quantized_pitch_cv_accepts_compile_time_calibration(self):
        dut = QuantizedPitchCV(
            base_note=48,
            sample_rate=48_000,
            zero_cv_counts=100,
            counts_per_octave=4_096,
        )
        notes = []

        async def bench(ctx):
            ctx.set(dut.o.ready, 1)
            for cv_counts in (100, 4_196, -3_996):
                ctx.set(dut.i.payload.as_value(), cv_counts)
                ctx.set(dut.i.valid, 1)
                while not ctx.get(dut.i.ready):
                    await ctx.tick()
                await ctx.tick()
                ctx.set(dut.i.valid, 0)
                while not ctx.get(dut.o.valid):
                    await ctx.tick()
                notes.append(ctx.get(dut.o.payload.note))
                await ctx.tick()

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        self.assertEqual(notes, [48, 60, 36])

    def test_midi_note_helpers_are_precise_and_validated(self):
        self.assertEqual(midi_note_frequency(69), 440.0)
        increment = midi_note_phase_increment(69, 48_000)
        actual_hz = increment * 48_000 / (1 << PHASE_BITS)
        self.assertLess(abs(actual_hz - 440.0), 0.001)
        with self.assertRaises(ValueError):
            midi_note_frequency(128)

    def test_source_is_deterministic_and_backpressure_safe(self):
        dut = SynthTestSource()
        samples = []

        async def bench(ctx):
            ctx.set(dut.o.ready, 0)
            for _ in range(12):
                await ctx.tick()
                self.assertEqual(ctx.get(dut.sample_index), 0)
            # The voice shaper and shared VCA can hold two in-flight samples;
            # after that bounded pipeline fill, all state must stop.
            self.assertEqual(
                ctx.get(dut.phase), 2 * phase_increment(440.0, 48_000)
            )
            self.assertEqual(ctx.get(dut.o.valid), 1)
            self.assertEqual(ctx.get(dut.o.payload[0].as_value()), -384)

            ctx.set(dut.o.ready, 1)
            while len(samples) < 2_200:
                if ctx.get(dut.o.valid):
                    samples.append(ctx.get(dut.o.payload[0].as_value()))
                await ctx.tick()

            self.assertEqual(ctx.get(dut.sample_index), 2_200)
            expected_phase = (
                2_201 * phase_increment(440.0, 48_000)
            ) & 0xFFFFFFFF
            self.assertEqual(ctx.get(dut.phase), expected_phase)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

        self.assertLess(min(samples), -11_000)
        self.assertGreater(max(samples), 11_000)
        self.assertGreater(samples.count(0), 800)

    def test_basic_voice_selects_before_drive_and_holds_when_stalled(self):
        dut = BasicVoice()

        async def bench(ctx):
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.i.payload.phase_increment, 0)
            ctx.set(dut.i.payload.waveform, Waveform.SQUARE)
            ctx.set(dut.i.payload.drive, 1)
            ctx.set(dut.o.ready, 0)

            await ctx.tick()
            self.assertEqual(ctx.get(dut.o.valid), 1)
            self.assertEqual(ctx.get(dut.o.payload.as_value()), -32768)
            held_phase = ctx.get(dut.phase)
            for _ in range(4):
                await ctx.tick()
                self.assertEqual(ctx.get(dut.o.valid), 1)
                self.assertEqual(ctx.get(dut.o.payload.as_value()), -32768)
                self.assertEqual(ctx.get(dut.phase), held_phase)

            ctx.set(dut.o.ready, 1)
            await ctx.tick()
            ctx.set(dut.i.payload.waveform, Waveform.SILENCE)
            ctx.set(dut.i.payload.drive, 3)
            await ctx.tick()
            self.assertEqual(ctx.get(dut.o.payload.as_value()), 0)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

    def test_dyadic_gain_rounding_is_explicit_and_signed(self):
        def run_case(rounding):
            dut = DyadicGain(
                numerator=3,
                fractional_bits=3,
                rounding=rounding,
            )
            actual = []

            async def bench(ctx):
                ctx.set(dut.i.valid, 1)
                ctx.set(dut.o.ready, 1)
                for sample in (1, 2, -1, -2, 4, -4):
                    ctx.set(dut.i.payload.as_value(), sample)
                    await ctx.delay(1e-9)
                    actual.append(ctx.get(dut.o.payload.as_value()))

            sim = Simulator(dut)
            sim.add_testbench(bench)
            sim.run()
            return actual

        self.assertEqual(
            run_case(DyadicRounding.FLOOR),
            [0, 0, -1, -1, 1, -2],
        )
        self.assertEqual(
            run_case(DyadicRounding.NEAREST_AWAY),
            [0, 1, 0, -1, 2, -2],
        )

    def test_basic_nco_waveform_quadrants(self):
        dut = BasicNCO()
        waveforms = []

        async def bench(ctx):
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.i.payload, 1 << 30)
            ctx.set(dut.o.ready, 1)

            for _ in range(4):
                waveforms.append((
                    ctx.get(dut.o.payload.saw.as_value()),
                    ctx.get(dut.o.payload.triangle.as_value()),
                    ctx.get(dut.o.payload.square.as_value()),
                ))
                await ctx.tick()

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

        self.assertLess(waveforms[0][0], -32_000)
        self.assertLess(waveforms[1][0], 0)
        self.assertGreaterEqual(waveforms[2][0], 0)
        self.assertGreater(waveforms[3][0], 0)
        self.assertLess(waveforms[0][1], -32_000)
        self.assertGreater(waveforms[2][1], 32_000)
        self.assertLess(waveforms[0][2], 0)
        self.assertGreater(waveforms[2][2], 0)


if __name__ == "__main__":
    unittest.main()
