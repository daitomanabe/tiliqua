# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import unittest

from amaranth.sim import Simulator

from tiliqua.build.qor import parse_nextpnr_utilization
from tiliqua.dsp.snn import (
    BatchedLIFBank,
    MemoryBatchedLIFBank,
    ParallelLIFBank,
    PopulationCVConductor,
    PopulationEnsembleSonifier,
    SNNTestSource,
)
from tiliqua.dsp.synth import midi_note_phase_increment
from tiliqua.video.snn_visualizer import SNNVisualizer


class SNNVisualizerTests(unittest.TestCase):

    def test_control_meter_shows_selection_level_and_override(self):
        dut = SNNVisualizer(neuron_count=64)

        async def bench(ctx):
            ctx.set(dut.y, 68)
            ctx.set(dut.x, 104 + 128 + 40)
            ctx.set(dut.control_selected, 1)
            ctx.set(dut.control_override, 0b0010)
            ctx.set(dut.control_levels, 160 << 8)
            await ctx.delay(1e-9)
            filled = (ctx.get(dut.r), ctx.get(dut.g), ctx.get(dut.b))
            self.assertEqual(filled, (255, 232, 144))

            ctx.set(dut.x, 104 + 128 + 100)
            await ctx.delay(1e-9)
            self.assertEqual(
                (ctx.get(dut.r), ctx.get(dut.g), ctx.get(dut.b)),
                (8, 13, 24),
            )

            ctx.set(dut.x, 104 + 128)
            await ctx.delay(1e-9)
            self.assertEqual(
                (ctx.get(dut.r), ctx.get(dut.g), ctx.get(dut.b)),
                (255, 255, 255),
            )

        sim = Simulator(dut)
        sim.add_testbench(bench)
        sim.run()

    def test_ei_cells_have_distinct_rest_and_spike_colours(self):
        dut = SNNVisualizer(
            neuron_count=1024,
            membrane_level_bits=2,
            inhibitory_stride=4,
        )

        async def bench(ctx):
            ctx.set(dut.y, 108)
            ctx.set(dut.activity, 0)
            ctx.set(dut.burst, 0)
            ctx.set(dut.frame, 0)

            # Neuron 2 is excitatory. At level 2 it is blue/magenta.
            ctx.set(dut.x, 124)
            ctx.set(dut.spikes, 0)
            ctx.set(dut.membrane_levels, 2 << (2 * 2))
            await ctx.delay(1e-9)
            self.assertEqual(ctx.get(dut.neuron_index), 2)
            self.assertEqual(
                (ctx.get(dut.r), ctx.get(dut.g), ctx.get(dut.b)),
                (170, 42, 85),
            )

            # Neuron 3 is the first inhibitory source and is orange at rest.
            ctx.set(dut.x, 132)
            ctx.set(dut.membrane_levels, 2 << (3 * 2))
            await ctx.delay(1e-9)
            self.assertEqual(ctx.get(dut.neuron_index), 3)
            self.assertEqual(
                (ctx.get(dut.r), ctx.get(dut.g), ctx.get(dut.b)),
                (181, 42, 32),
            )

            # Spiking preserves the population distinction: white vs orange.
            ctx.set(dut.x, 124)
            ctx.set(dut.spikes, 1 << 2)
            await ctx.delay(1e-9)
            self.assertEqual(
                (ctx.get(dut.r), ctx.get(dut.g), ctx.get(dut.b)),
                (255, 255, 255),
            )

            ctx.set(dut.x, 132)
            ctx.set(dut.spikes, 1 << 3)
            await ctx.delay(1e-9)
            self.assertEqual(
                (ctx.get(dut.r), ctx.get(dut.g), ctx.get(dut.b)),
                (255, 96, 32),
            )

        sim = Simulator(dut)
        sim.add_testbench(bench)
        sim.run()

    def test_1024_external_display_row_preserves_ei_semantics(self):
        dut = SNNVisualizer(
            neuron_count=1024,
            membrane_level_bits=2,
            external_rows=True,
            inhibitory_stride=4,
        )

        async def bench(ctx):
            ctx.set(dut.y, 108)
            ctx.set(dut.spikes, 0)
            ctx.set(dut.membrane_levels, 0)
            ctx.set(dut.activity, 0)
            ctx.set(dut.burst, 0)
            ctx.set(dut.frame, 0)

            # Neurons 34/35 occupy words 2/3 of external display RAM row 1.
            row = (2 << 1) << (2 * 3)
            ctx.set(dut.external_row_data, row)
            ctx.set(dut.x, 380)
            await ctx.delay(1e-9)
            self.assertEqual(ctx.get(dut.neuron_index), 34)
            self.assertEqual(ctx.get(dut.external_row_addr), 1)
            self.assertEqual(
                (ctx.get(dut.r), ctx.get(dut.g), ctx.get(dut.b)),
                (170, 42, 85),
            )

            row = (2 << 1) << (3 * 3)
            ctx.set(dut.external_row_data, row)
            ctx.set(dut.x, 388)
            await ctx.delay(1e-9)
            self.assertEqual(ctx.get(dut.neuron_index), 35)
            self.assertEqual(ctx.get(dut.external_row_addr), 1)
            self.assertEqual(
                (ctx.get(dut.r), ctx.get(dut.g), ctx.get(dut.b)),
                (181, 42, 32),
            )

            row = 1 << (3 * 3)
            ctx.set(dut.external_row_data, row)
            await ctx.delay(1e-9)
            self.assertEqual(
                (ctx.get(dut.r), ctx.get(dut.g), ctx.get(dut.b)),
                (255, 96, 32),
            )

        sim = Simulator(dut)
        sim.add_testbench(bench)
        sim.run()


class PopulationEnsembleSonifierTests(unittest.TestCase):

    def test_four_population_voices_are_bounded_and_backpressure_safe(self):
        dut = PopulationEnsembleSonifier(
            neuron_count=1024,
            sample_rate=48_000,
            control_period_samples=4,
        )
        samples = []

        async def bench(ctx):
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.o.ready, 1)
            ctx.set(dut.spike_count, 40)
            ctx.set(dut.excitatory_spike_count, 32)
            ctx.set(dut.inhibitory_spike_count, 8)

            for _ in range(17):
                samples.append(tuple(
                    ctx.get(dut.o.payload[channel].as_value())
                    for channel in range(4)
                ))
                await ctx.tick()

            # Total, E, and I update every control interval. The bass updates
            # every fourth interval; 32 versus 3*8 is strongly excitatory.
            self.assertEqual(
                tuple(ctx.get(note_index) for note_index in dut.note_indices),
                (2, 2, 2, 0),
            )
            phases_before_stall = tuple(ctx.get(phase) for phase in dut.phases)
            samples_before_stall = tuple(
                ctx.get(dut.o.payload[channel].as_value())
                for channel in range(4)
            )
            ctx.set(dut.o.ready, 0)
            for _ in range(4):
                await ctx.tick()
                self.assertEqual(
                    tuple(ctx.get(phase) for phase in dut.phases),
                    phases_before_stall,
                )
                self.assertEqual(
                    tuple(
                        ctx.get(dut.o.payload[channel].as_value())
                        for channel in range(4)
                    ),
                    samples_before_stall,
                )

            ctx.set(dut.o.ready, 1)
            await ctx.tick()
            self.assertEqual(
                tuple(ctx.get(phase) for phase in dut.phases),
                tuple(
                    (phase + midi_note_phase_increment(note, 48_000))
                    & 0xFFFFFFFF
                    for phase, note in zip(
                        phases_before_stall,
                        (41, 53, 48, 24),
                    )
                ),
            )

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        limits = (16384, 16384, 18432, 16384)
        self.assertTrue(all(
            -limits[channel] <= sample <= limits[channel] - 1
            for row in samples
            for channel, sample in enumerate(row)
        ))
        self.assertTrue(any(len(set(row)) > 1 for row in samples))

    def test_population_counts_clamp_without_wrapping(self):
        dut = PopulationEnsembleSonifier(
            neuron_count=1024,
            control_period_samples=2,
        )

        async def bench(ctx):
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.o.ready, 1)
            ctx.set(dut.spike_count, 1024)
            ctx.set(dut.excitatory_spike_count, 768)
            ctx.set(dut.inhibitory_spike_count, 256)
            for _ in range(3):
                await ctx.tick()
            self.assertEqual(
                tuple(ctx.get(note_index) for note_index in dut.note_indices),
                (7, 7, 7, 2),
            )

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()


class PopulationCVConductorTests(unittest.TestCase):

    def test_four_cv_outputs_are_bounded_quantized_and_backpressure_safe(self):
        dut = PopulationCVConductor(
            neuron_count=1024,
            sample_rate=48_000,
            clock_period_samples=16,
            clock_pulse_samples=2,
            gate_high_samples=8,
        )
        samples = []

        async def bench(ctx):
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.o.ready, 1)
            ctx.set(dut.spike_count, 64)
            for _ in range(70):
                samples.append(tuple(
                    ctx.get(dut.o.payload[channel].as_value())
                    for channel in range(4)
                ))
                await ctx.tick()

            state_before_stall = (
                ctx.get(dut.clock_counter),
                ctx.get(dut.step),
                ctx.get(dut.pitch_index),
                ctx.get(dut.gate_remaining),
                ctx.get(dut.modulation_cv),
            )
            output_before_stall = tuple(
                ctx.get(dut.o.payload[channel].as_value())
                for channel in range(4)
            )
            ctx.set(dut.o.ready, 0)
            for _ in range(8):
                await ctx.tick()
                self.assertEqual(
                    (
                        ctx.get(dut.clock_counter),
                        ctx.get(dut.step),
                        ctx.get(dut.pitch_index),
                        ctx.get(dut.gate_remaining),
                        ctx.get(dut.modulation_cv),
                    ),
                    state_before_stall,
                )
                self.assertEqual(
                    tuple(
                        ctx.get(dut.o.payload[channel].as_value())
                        for channel in range(4)
                    ),
                    output_before_stall,
                )

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

        five_volts = 20_000
        self.assertEqual(set(row[0] for row in samples), {0, five_volts})
        self.assertTrue(set(row[1] for row in samples).issubset(
            set(PopulationCVConductor.PITCH_ASQ)
        ))
        self.assertIn(PopulationCVConductor.PITCH_ASQ[4], [
            row[1] for row in samples
        ])
        self.assertEqual(set(row[2] for row in samples), {0, five_volts})
        self.assertTrue(all(0 <= row[3] <= five_volts for row in samples))
        self.assertGreater(samples[-1][3], samples[0][3])

    def test_population_extremes_clamp_pitch_density_and_modulation(self):
        dut = PopulationCVConductor(
            clock_period_samples=16,
            clock_pulse_samples=2,
            gate_high_samples=8,
        )

        async def bench(ctx):
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.o.ready, 1)
            ctx.set(dut.spike_count, 1024)
            for _ in range(16 * 3):
                await ctx.tick()
            self.assertEqual(ctx.get(dut.pitch_index), 7)
            self.assertLessEqual(ctx.get(dut.modulation_cv), 20_000)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

    def test_hardware_clock_keeps_running_during_stream_backpressure(self):
        dut = PopulationCVConductor(
            clock_period_samples=16,
            clock_pulse_samples=2,
            gate_high_samples=8,
            wall_clock_hz=800,
        )

        async def bench(ctx):
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.o.ready, 0)
            ctx.set(dut.spike_count, 64)
            modulation_before = ctx.get(dut.modulation_cv)
            for _ in range(101):
                await ctx.tick()
            self.assertEqual(ctx.get(dut.clock_counter), 1)
            self.assertEqual(ctx.get(dut.step), 1)
            self.assertEqual(ctx.get(dut.modulation_cv), modulation_before)
            self.assertEqual(ctx.get(dut.o.payload[0].as_value()), 20_000)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()


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

    def test_512_memory_batches_match_fully_parallel_model(self):
        expected = self.capture_network(
            ParallelLIFBank(neuron_count=512), sample_count=16
        )
        actual = self.capture_network(
            MemoryBatchedLIFBank(
                logical_neuron_count=512,
                physical_lane_count=32,
            ),
            sample_count=16,
        )
        self.assertEqual(actual, expected)

    def test_1024_memory_batches_match_fully_parallel_model(self):
        expected = self.capture_network(
            ParallelLIFBank(neuron_count=1024), sample_count=8
        )
        actual = self.capture_network(
            MemoryBatchedLIFBank(
                logical_neuron_count=1024,
                physical_lane_count=32,
            ),
            sample_count=8,
        )
        self.assertEqual(len(actual), len(expected))
        for actual_sample, expected_sample in zip(actual, expected):
            self.assertEqual(actual_sample[:3], expected_sample[:3])
            expected_levels = expected_sample[3]
            compressed_levels = 0
            for index in range(1024):
                compressed_levels |= (
                    ((expected_levels >> (index * 4)) & 0xF) >> 2
                ) << (index * 2)
            self.assertEqual(actual_sample[3], compressed_levels)

    def test_1024_ei_ring_matches_parallel_and_changes_dynamics(self):
        profiles = {}
        for inhibitory_strength in (512, 1024, 1536):
            expected = self.capture_network(
                ParallelLIFBank(
                    neuron_count=1024,
                    ei_ring=True,
                    inhibitory_strength=inhibitory_strength,
                ),
                sample_count=8,
            )
            actual = self.capture_network(
                MemoryBatchedLIFBank(
                    logical_neuron_count=1024,
                    physical_lane_count=32,
                    ei_ring=True,
                    inhibitory_strength=inhibitory_strength,
                ),
                sample_count=8,
            )
            profiles[inhibitory_strength] = actual
            for actual_sample, expected_sample in zip(actual, expected):
                self.assertEqual(actual_sample[:3], expected_sample[:3])
                expected_levels = expected_sample[3]
                compressed_levels = 0
                for index in range(1024):
                    compressed_levels |= (
                        ((expected_levels >> (index * 4)) & 0xF) >> 2
                    ) << (index * 2)
                self.assertEqual(actual_sample[3], compressed_levels)
        baseline = self.capture_network(
            ParallelLIFBank(neuron_count=1024), sample_count=8
        )
        self.assertNotEqual(profiles[1024], baseline)
        self.assertEqual(len({repr(profile) for profile in profiles.values()}), 3)

    def test_1024_ei_population_counters_match_spike_vector(self):
        dut = MemoryBatchedLIFBank(
            logical_neuron_count=1024,
            physical_lane_count=32,
            ei_ring=True,
        )
        checked = 0
        inhibitory_mask = sum(1 << index for index in range(3, 1024, 4))

        async def bench(ctx):
            nonlocal checked
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.i.payload[0].as_value(), 12_000)
            for channel in range(1, 4):
                ctx.set(dut.i.payload[channel].as_value(), 0)
            ctx.set(dut.o.ready, 1)
            while checked < 8:
                if ctx.get(dut.o.valid):
                    spikes = ctx.get(dut.spike_vector)
                    inhibitory = (spikes & inhibitory_mask).bit_count()
                    excitatory = spikes.bit_count() - inhibitory
                    self.assertEqual(
                        ctx.get(dut.excitatory_spike_count), excitatory
                    )
                    self.assertEqual(
                        ctx.get(dut.inhibitory_spike_count), inhibitory
                    )
                    self.assertEqual(
                        excitatory + inhibitory,
                        ctx.get(dut.spike_count),
                    )
                    checked += 1
                await ctx.tick()

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        self.assertEqual(checked, 8)

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
