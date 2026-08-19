# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Block-rate control engine RTL against the reference, parameter for parameter."""

from dataclasses import replace
import unittest

from amaranth.sim import Simulator

from tiliqua.additive import AdditiveReference, DEFAULT_CONTROL_STATE, tables as T
from tiliqua.additive.control_engine import AdditiveControlEngine
from tiliqua.additive.engine import group_parameters_to_dict
from tiliqua.additive.protocol import encode_payload

PARAM_FIELDS = (
    "midpoint", "spacing_half", "spread_half",
    "gain_left", "gain_right", "gain_low", "gain_air",
)


def frame_bits(state):
    return int.from_bytes(encode_payload(state), "little")


class ControlEngineTests(unittest.TestCase):

    def run_blocks(self, schedule, cv_stream):
        """``schedule`` maps block index -> state to present before that block starts."""
        dut = AdditiveControlEngine()
        reference = AdditiveReference(schedule[0])
        blocks = max(schedule) + 1
        mismatches = []
        cycles_log = []
        effective_log = []

        async def bench(ctx):
            ctx.set(dut.frame.as_value(), frame_bits(schedule[0]))
            for block in range(blocks):
                writes = {}
                committed = {}
                for offset in range(T.CONTROL_BLOCK_SAMPLES):
                    sample = block * T.CONTROL_BLOCK_SAMPLES + offset
                    cv = cv_stream(sample)
                    for channel in range(4):
                        ctx.set(dut.cv[channel].as_value(), cv[channel])
                    ctx.set(dut.sample_start, 1)
                    ctx.set(dut.block_start, int(offset == 0))
                    await ctx.tick()
                    ctx.set(dut.sample_start, 0)
                    ctx.set(dut.block_start, 0)
                    if ctx.get(dut.param_en):
                        writes[ctx.get(dut.param_addr)] = {
                            name: ctx.get(getattr(dut.param_data, name)) for name in PARAM_FIELDS
                        }
                    await ctx.tick()
                    reference.step_sample(cv)
                # Let the engine finish the block computation.
                guard = 0
                while ctx.get(dut.busy):
                    if ctx.get(dut.param_en):
                        writes[ctx.get(dut.param_addr)] = {
                            name: ctx.get(getattr(dut.param_data, name)) for name in PARAM_FIELDS
                        }
                    if ctx.get(dut.commit):
                        committed["master_asq"] = ctx.get(dut.master_asq)
                    await ctx.tick()
                    guard += 1
                    self.assertLess(guard, 200_000)
                cycles_log.append(ctx.get(dut.block_cycles))
                self.assertEqual(ctx.get(dut.overrun), 0)
                # A frame scheduled for the next block is presented before its
                # start pulse (RTL) and before the matching reference block step.
                if block + 1 in schedule:
                    ctx.set(dut.frame.as_value(), frame_bits(schedule[block + 1]))
                    reference.commit_frame(schedule[block + 1])
                # The engine just produced the parameters for block + 1.
                self.assertTrue(reference.prepare_block())
                self.assertEqual(len(writes), T.GROUP_COUNT, f"block {block} wrote {len(writes)}")
                for index, group in enumerate(reference.groups):
                    expected = group_parameters_to_dict(group)
                    expected["spacing_half"] = expected["spacing_half"] & T.PHASE_MASK
                    actual = dict(writes[index])
                    actual["spacing_half"] = actual["spacing_half"] & T.PHASE_MASK
                    if actual != expected:
                        mismatches.append((block + 1, index, actual, expected))
                if ctx.get(dut.master_asq) != reference.master_asq:
                    mismatches.append((block + 1, "master", ctx.get(dut.master_asq), reference.master_asq))
                effective_log.append({
                    "master": ctx.get(dut.effective.master),
                    "pitch": ctx.get(dut.effective.pitch_octaves_q10),
                    "cv": [ctx.get(dut.cv_smoothed[i]) for i in range(4)],
                })

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        return mismatches, cycles_log, effective_log, reference

    def test_default_then_glass_frame_with_cv_matches_reference(self):
        glass = replace(
            DEFAULT_CONTROL_STATE.with_preset("GLASS"), root_midi=45, harmony=6,
            detune_millicents=12_000, phase_spread=T.q15(0.9), stereo_width=T.Q15_ONE,
            sub_focus=T.q15(0.7), morph_seconds=2, evolution_rate_mhz=80,
        )
        schedule = {0: DEFAULT_CONTROL_STATE, 2: glass}

        def cv_stream(sample):
            return (
                (sample * 37) % 8_000,
                ((sample * 53) % 8_000) - 4_000,
                ((sample * 71) % 8_000) - 4_000,
                ((sample * 19) % 8_000) - 4_000,
            )

        mismatches, cycles, effective, reference = self.run_blocks(schedule, cv_stream)
        self.assertEqual(mismatches[:3], [])
        self.assertEqual(len(mismatches), 0)
        self.assertLess(max(cycles), T.CONTROL_BLOCK_SAMPLES * T.SYNC_CYCLES_PER_SAMPLE // 2)
        self.assertGreater(effective[-1]["master"], 0)
        self.assertNotEqual(effective[-1]["cv"], [0, 0, 0, 0])

    def test_mute_and_worst_case_match_reference(self):
        worst = replace(
            DEFAULT_CONTROL_STATE, master=T.Q15_ONE, harmonic_levels=(T.Q15_ONE,) * 10,
            detune_millicents=0, phase_spread=0, evolution_amount=0, stereo_width=0,
            sub_focus=T.Q15_ONE,
        )
        muted = replace(worst, output_enabled=0)
        schedule = {0: worst, 1: muted, 3: worst}
        mismatches, cycles, _, _ = self.run_blocks(
            schedule, lambda sample: (8_000, 4_000, -4_000, 4_000)
        )
        self.assertEqual(len(mismatches), 0)
        self.assertLess(max(cycles), T.CONTROL_BLOCK_SAMPLES * T.SYNC_CYCLES_PER_SAMPLE // 2)


if __name__ == "__main__":
    unittest.main()
