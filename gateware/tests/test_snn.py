# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import unittest

from amaranth.sim import Simulator

from tiliqua.dsp.snn import ParallelLIFBank, SNNTestSource


class ParallelLIFBankTests(unittest.TestCase):

    def test_network_is_bipolar_deterministic_and_backpressure_safe(self):
        dut = ParallelLIFBank(neuron_count=16)
        outputs = []

        async def bench(ctx):
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.i.payload[0].as_value(), 12_000)
            ctx.set(dut.i.payload[1].as_value(), 0)
            ctx.set(dut.i.payload[2].as_value(), 0)
            ctx.set(dut.i.payload[3].as_value(), 0)
            ctx.set(dut.o.ready, 0)

            await ctx.tick()
            while not ctx.get(dut.o.valid):
                await ctx.tick()
            held_index = ctx.get(dut.sample_index)
            held_payload = tuple(
                ctx.get(dut.o.payload[channel].as_value()) for channel in range(4)
            )
            held_spikes = ctx.get(dut.spike_vector)
            for _ in range(8):
                await ctx.tick()
                self.assertEqual(ctx.get(dut.i.ready), 0)
                self.assertEqual(ctx.get(dut.sample_index), held_index)
                self.assertEqual(ctx.get(dut.spike_vector), held_spikes)
                self.assertEqual(
                    tuple(ctx.get(dut.o.payload[channel].as_value()) for channel in range(4)),
                    held_payload,
                )

            ctx.set(dut.o.ready, 1)
            while len(outputs) < 4_096:
                if ctx.get(dut.o.valid):
                    outputs.append(tuple(
                        ctx.get(dut.o.payload[channel].as_value())
                        for channel in range(4)
                    ))
                    self.assertEqual(
                        ctx.get(dut.spike_count),
                        ctx.get(dut.spike_vector).bit_count(),
                    )
                await ctx.tick()

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

        audio = [row[0] for row in outputs]
        activity = [row[1] for row in outputs]
        gate = [row[2] for row in outputs]
        membrane = [row[3] for row in outputs]
        self.assertLess(min(audio), -8_000)
        self.assertGreater(max(audio), 8_000)
        self.assertGreater(max(activity), 1_000)
        self.assertEqual(min(gate), 0)
        self.assertGreater(max(gate), 15_000)
        self.assertGreater(max(membrane), 3_000)

    def test_source_freezes_while_stalled(self):
        dut = SNNTestSource()

        async def bench(ctx):
            ctx.set(dut.o.ready, 0)
            for _ in range(8):
                await ctx.tick()
            self.assertEqual(ctx.get(dut.sample_index), 0)
            self.assertEqual(ctx.get(dut.o.payload[0].as_value()), 3_000)
            ctx.set(dut.o.ready, 1)
            for _ in range(2_049):
                await ctx.tick()
            self.assertEqual(ctx.get(dut.sample_index), 2_049)
            self.assertEqual(ctx.get(dut.o.payload[0].as_value()), 12_000)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()


if __name__ == "__main__":
    unittest.main()
