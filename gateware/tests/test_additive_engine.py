# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""RTL oscillator bank against the integer reference, sample for sample."""

from dataclasses import replace
import unittest

from amaranth.sim import Simulator

from tiliqua.additive import AdditiveReference, DEFAULT_CONTROL_STATE, tables as T
from tiliqua.additive.engine import AdditiveOscillatorBank, group_parameters_to_dict


async def write_block_parameters(ctx, dut, reference):
    """Write the reference's committed groups + master into the inactive bank."""
    for index, group in enumerate(reference.groups):
        fields = group_parameters_to_dict(group)
        ctx.set(dut.param_addr, index)
        for name, value in fields.items():
            ctx.set(getattr(dut.param_data, name), value)
        ctx.set(dut.param_en, 1)
        await ctx.tick()
    ctx.set(dut.param_en, 0)
    ctx.set(dut.master_asq, reference.master_asq)
    ctx.set(dut.commit, 1)
    await ctx.tick()
    ctx.set(dut.commit, 0)


async def run_sample(ctx, dut, cv=(0, 0, 0, 0), ready_delay=0):
    """Feed one input sample and return (outputs, cycles)."""
    for channel in range(4):
        ctx.set(dut.i.payload[channel].as_value(), cv[channel])
    ctx.set(dut.i.valid, 1)
    while not ctx.get(dut.i.ready):
        await ctx.tick()
    await ctx.tick()
    ctx.set(dut.i.valid, 0)
    ctx.set(dut.o.ready, 0)
    while not ctx.get(dut.o.valid):
        await ctx.tick()
    first = tuple(ctx.get(dut.o.payload[c].as_value()) for c in range(4))
    for _ in range(ready_delay):
        await ctx.tick()
        assert ctx.get(dut.o.valid) == 1
        held = tuple(ctx.get(dut.o.payload[c].as_value()) for c in range(4))
        assert held == first
    cycles = ctx.get(dut.sample_cycles)
    ctx.set(dut.o.ready, 1)
    await ctx.tick()
    ctx.set(dut.o.ready, 0)
    return first, cycles


class OscillatorBankTests(unittest.TestCase):

    def run_equivalence(self, state, blocks, cv_stream=None, ready_delay=0):
        dut = AdditiveOscillatorBank()
        reference = AdditiveReference(state)
        mismatches = []
        cycle_log = []

        async def bench(ctx):
            ctx.set(dut.o.ready, 0)
            for block in range(blocks):
                self.assertTrue(reference.prepare_block())
                await write_block_parameters(ctx, dut, reference)
                for offset in range(T.CONTROL_BLOCK_SAMPLES):
                    sample = block * T.CONTROL_BLOCK_SAMPLES + offset
                    cv = cv_stream(sample) if cv_stream else (0, 0, 0, 0)
                    expected = reference.step_sample(cv)
                    actual, cycles = await run_sample(ctx, dut, cv, ready_delay)
                    cycle_log.append(cycles)
                    if actual != expected:
                        mismatches.append((sample, actual, expected))
                    self.assertEqual(ctx.get(dut.fault), 0)
                    self.assertEqual(ctx.get(dut.sample_index), sample + 1)
                    self.assertEqual(
                        tuple(ctx.get(dut.cv[c].as_value()) for c in range(4)), tuple(cv)
                    )

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        return mismatches, cycle_log, reference

    def test_default_chord_matches_reference_for_three_blocks(self):
        mismatches, cycles, reference = self.run_equivalence(DEFAULT_CONTROL_STATE, 3)
        self.assertEqual(mismatches[:5], [])
        self.assertEqual(len(mismatches), 0)
        self.assertLessEqual(max(cycles), T.SYNC_CYCLES_PER_SAMPLE - 200)
        self.assertGreaterEqual(min(cycles), T.OSCILLATOR_COUNT)
        self.assertGreater(reference.master_asq, 0)

    def test_worst_case_and_mute_match_reference_with_backpressure(self):
        worst = replace(
            DEFAULT_CONTROL_STATE,
            master=T.Q15_ONE,
            harmonic_levels=(T.Q15_ONE,) * 10,
            detune_millicents=0,
            phase_spread=0,
            evolution_amount=0,
            stereo_width=0,
            sub_focus=T.Q15_ONE,
        )
        mismatches, cycles, _ = self.run_equivalence(worst, 2, ready_delay=3)
        self.assertEqual(len(mismatches), 0)
        self.assertLessEqual(max(cycles), T.SYNC_CYCLES_PER_SAMPLE - 200)

        muted = replace(DEFAULT_CONTROL_STATE, output_enabled=0)
        mismatches, _, reference = self.run_equivalence(muted, 1)
        self.assertEqual(len(mismatches), 0)
        self.assertEqual(reference.master_asq, 0)

    def test_transposed_and_tilted_parameters_match_reference(self):
        def cv_stream(sample):
            return (6_000, 3_000 if sample < 200 else -3_000, 2_500, -2_000)

        glass = replace(
            DEFAULT_CONTROL_STATE.with_preset("GLASS"), root_midi=45, harmony=6,
            detune_millicents=12_000, phase_spread=T.q15(0.9), stereo_width=T.Q15_ONE,
        )
        mismatches, cycles, _ = self.run_equivalence(glass, 3, cv_stream=cv_stream)
        self.assertEqual(len(mismatches), 0)
        self.assertLessEqual(max(cycles), T.SYNC_CYCLES_PER_SAMPLE - 200)

    def test_rtl_outputs_are_bounded_mute_exactly_and_keep_low_band_mono(self):
        """Output-contract properties asserted directly on the RTL outputs."""
        worst = replace(
            DEFAULT_CONTROL_STATE,
            master=T.Q15_ONE,
            harmonic_levels=(T.Q15_ONE,) * 10,
            detune_millicents=0,
            phase_spread=0,
            evolution_amount=0,
            stereo_width=0,
            sub_focus=T.Q15_ONE,
        )
        dut = AdditiveOscillatorBank()
        reference = AdditiveReference(worst)
        outputs = []
        muted_outputs = []

        async def bench(ctx):
            ctx.set(dut.o.ready, 0)
            reference.prepare_block()
            await write_block_parameters(ctx, dut, reference)
            for _ in range(T.CONTROL_BLOCK_SAMPLES):
                actual, cycles = await run_sample(ctx, dut)
                outputs.append(actual)
                self.assertLessEqual(cycles, T.SYNC_CYCLES_PER_SAMPLE)
            # Same worst-case gains, master forced to zero: exact silence.
            ctx.set(dut.master_asq, 0)
            ctx.set(dut.commit, 1)
            await ctx.tick()
            ctx.set(dut.commit, 0)
            for _ in range(40):
                actual, _ = await run_sample(ctx, dut)
                muted_outputs.append(actual)
            self.assertEqual(ctx.get(dut.fault), 0)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

        peak = max(abs(v) for frame in outputs for v in frame)
        self.assertLessEqual(peak, T.OUTPUT_CEILING_ASQ)
        self.assertGreater(peak, 1_000)
        for frame in outputs:
            self.assertEqual(frame[0], frame[1])  # stereo_width = 0 -> exact mono
        for channel in range(4):
            self.assertTrue(any(frame[channel] != 0 for frame in outputs))
        # The air stem (>= 600 Hz) is bipolar within one block; long-window DC
        # behaviour of the low buses is the reference's gate
        # (test_reference_is_deterministic_and_dc_free) and the RTL is exact.
        self.assertLess(min(frame[3] for frame in outputs), 0)
        self.assertGreater(max(frame[3] for frame in outputs), 0)
        self.assertTrue(all(frame == (0, 0, 0, 0) for frame in muted_outputs))


if __name__ == "__main__":
    unittest.main()
