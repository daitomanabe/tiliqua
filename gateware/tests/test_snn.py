# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import unittest

from amaranth.sim import Simulator

from tiliqua.build.qor import parse_nextpnr_utilization
from tiliqua.dsp.snn import BatchedLIFBank, ParallelLIFBank, SNNTestSource


class ParallelLIFBankTests(unittest.TestCase):

    def capture_network(self, dut, sample_count):
        outputs = []

        async def bench(ctx):
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.i.payload[0].as_value(), 12_000)
            for index in range(1, 4):
                ctx.set(dut.i.payload[index].as_value(), 0)
            ctx.set(dut.o.ready, 1)
            while len(outputs) < sample_count:
                if ctx.get(dut.o.valid):
                    outputs.append((
                        tuple(
                            ctx.get(dut.o.payload[channel].as_value())
                            for channel in range(4)
                        ),
                        ctx.get(dut.spike_vector),
                        ctx.get(dut.spike_count),
                        ctx.get(dut.membrane_levels),
                    ))
                await ctx.tick()

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        return outputs

    def mean_activity(self, channel, control):
        dut = ParallelLIFBank(neuron_count=16)
        outputs = []

        async def bench(ctx):
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.i.payload[0].as_value(), 8_000)
            for index in range(1, 4):
                ctx.set(dut.i.payload[index].as_value(), 0)
            ctx.set(dut.i.payload[channel].as_value(), control)
            ctx.set(dut.o.ready, 1)
            while len(outputs) < 2_048:
                if ctx.get(dut.o.valid):
                    outputs.append(ctx.get(dut.o.payload[1].as_value()))
                await ctx.tick()

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        return sum(outputs[512:]) / len(outputs[512:])

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

    def test_grouped_reduction_is_exact_for_128_neurons(self):
        dut = ParallelLIFBank(neuron_count=128)
        checked = 0

        async def bench(ctx):
            nonlocal checked
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.i.payload[0].as_value(), 12_000)
            for index in range(1, 4):
                ctx.set(dut.i.payload[index].as_value(), 0)
            ctx.set(dut.o.ready, 1)
            while checked < 64:
                if ctx.get(dut.o.valid):
                    self.assertEqual(
                        ctx.get(dut.spike_count),
                        ctx.get(dut.spike_vector).bit_count(),
                    )
                    checked += 1
                await ctx.tick()

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        self.assertEqual(checked, 64)

    def test_grouped_reduction_is_exact_for_256_neurons(self):
        dut = ParallelLIFBank(neuron_count=256)
        checked = 0

        async def bench(ctx):
            nonlocal checked
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.i.payload[0].as_value(), 12_000)
            for index in range(1, 4):
                ctx.set(dut.i.payload[index].as_value(), 0)
            ctx.set(dut.o.ready, 1)
            while checked < 16:
                if ctx.get(dut.o.valid):
                    self.assertEqual(
                        ctx.get(dut.spike_count),
                        ctx.get(dut.spike_vector).bit_count(),
                    )
                    checked += 1
                await ctx.tick()

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        self.assertEqual(checked, 16)

    def test_256_logical_batches_match_fully_parallel_model(self):
        expected = self.capture_network(
            ParallelLIFBank(neuron_count=256), sample_count=32
        )
        for physical_lane_count in (128, 64, 32):
            with self.subTest(physical_lane_count=physical_lane_count):
                actual = self.capture_network(
                    BatchedLIFBank(
                        logical_neuron_count=256,
                        physical_lane_count=physical_lane_count,
                    ),
                    sample_count=32,
                )
                self.assertEqual(actual, expected)

    def test_three_cv_controls_change_population_activity(self):
        weak_leak = self.mean_activity(1, -8_000)
        strong_leak = self.mean_activity(1, 8_000)
        self.assertGreater(weak_leak, strong_leak)

        weak_recurrence = self.mean_activity(2, -8_000)
        strong_recurrence = self.mean_activity(2, 8_000)
        self.assertLess(weak_recurrence, strong_recurrence)

        low_threshold = self.mean_activity(3, -8_000)
        high_threshold = self.mean_activity(3, 8_000)
        self.assertGreater(low_threshold, high_threshold)

    def test_nextpnr_frontier_utilization_parser(self):
        report = """
Info: Device utilisation:
Info:               TRELLIS_FF:   12342/  24288    50%
Info:             TRELLIS_COMB:   35628/  24288   146%
ERROR: Unable to find legal placement for all cells
"""
        self.assertEqual(
            parse_nextpnr_utilization(report)["TRELLIS_COMB"],
            (35_628, 24_288, 146),
        )


if __name__ == "__main__":
    unittest.main()
