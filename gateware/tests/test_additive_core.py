# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Complete additive core (control engine + oscillator bank) against the reference."""

from dataclasses import replace
import unittest

from amaranth.sim import Simulator

from tiliqua.additive import AdditiveReference, DEFAULT_CONTROL_STATE, tables as T
from tiliqua.additive.core import AdditiveCore
from tiliqua.additive.protocol import encode_payload


def frame_bits(state):
    return int.from_bytes(encode_payload(state), "little")


class AdditiveCoreTests(unittest.TestCase):

    def run_core(self, initial, samples, cv_stream, frames=None):
        """``frames`` maps a sample index -> state presented from that sample on."""
        frames = frames or {}
        dut = AdditiveCore()
        reference = AdditiveReference(initial)
        mismatches = []
        cycles = []
        cv_log = []

        async def bench(ctx):
            ctx.set(dut.frame.as_value(), frame_bits(initial))
            ctx.set(dut.o.ready, 0)
            for sample in range(samples):
                if sample in frames:
                    ctx.set(dut.frame.as_value(), frame_bits(frames[sample]))
                    reference.commit_frame(frames[sample])
                cv = cv_stream(sample)
                expected = reference.step_sample(cv)
                for channel in range(4):
                    ctx.set(dut.i.payload[channel].as_value(), cv[channel])
                ctx.set(dut.i.valid, 1)
                while not ctx.get(dut.i.ready):
                    await ctx.tick()
                await ctx.tick()
                ctx.set(dut.i.valid, 0)
                while not ctx.get(dut.o.valid):
                    await ctx.tick()
                actual = tuple(ctx.get(dut.o.payload[c].as_value()) for c in range(4))
                cycles.append(ctx.get(dut.sample_cycles))
                ctx.set(dut.o.ready, 1)
                await ctx.tick()
                ctx.set(dut.o.ready, 0)
                if actual != expected:
                    mismatches.append((sample, actual, expected))
                self.assertEqual(ctx.get(dut.fault), 0, f"fault at sample {sample}")
            cv_log.append([ctx.get(dut.cv_smoothed[i]) for i in range(4)])

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        return mismatches, cycles, cv_log, reference

    def test_core_matches_reference_with_cv_and_frame_changes(self):
        glass = replace(
            DEFAULT_CONTROL_STATE.with_preset("GLASS"), root_midi=45, harmony=6,
            detune_millicents=9_000, morph_seconds=2, stereo_width=T.Q15_ONE,
        )

        def cv_stream(sample):
            if sample < 2 * T.CONTROL_BLOCK_SAMPLES:
                return (0, 0, 0, 0)
            return (
                (sample * 37) % 8_000,
                ((sample * 53) % 8_000) - 4_000,
                ((sample * 71) % 8_000) - 4_000,
                ((sample * 19) % 8_000) - 4_000,
            )

        samples = 5 * T.CONTROL_BLOCK_SAMPLES
        mismatches, cycles, cv_log, reference = self.run_core(
            DEFAULT_CONTROL_STATE, samples, cv_stream,
            frames={T.CONTROL_BLOCK_SAMPLES + 40: glass},
        )
        self.assertEqual(mismatches[:3], [])
        self.assertEqual(len(mismatches), 0)
        self.assertLessEqual(max(cycles), T.SYNC_CYCLES_PER_SAMPLE - 200)
        self.assertEqual(reference.state, glass)
        self.assertNotEqual(cv_log[-1], [0, 0, 0, 0])


if __name__ == "__main__":
    unittest.main()
