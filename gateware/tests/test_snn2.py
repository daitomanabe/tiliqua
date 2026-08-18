# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

from copy import deepcopy
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from amaranth.sim import Simulator

from tiliqua.snn2 import (
    ALIFState,
    SNN2EncoderReference,
    SNN2ManifestError,
    SNN2Reference,
    canonical_payload_sha256,
    compile_edge_banks,
    export_manifest,
    make_default_manifest,
    step_alif_neuron,
    validate_manifest,
)
from tiliqua.snn2.encoder import SNN2AudioEncoder
from tiliqua.snn2.rtl import SparseALIFNetwork
from tiliqua.video.snn2_visualizer import SNN2Visualizer


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "snn2" / "snn2_256x16_sparse_alif_v1.json"
GOLDEN_TRACES = ROOT / "snn2" / "golden" / "single_neuron_traces.json"


def rehash(manifest):
    manifest["payload_sha256"] = canonical_payload_sha256(manifest)
    return manifest


class SNN2ManifestTests(unittest.TestCase):

    def test_checked_in_manifest_is_canonical_and_reproducible(self):
        checked_in = json.loads(DEFAULT_MANIFEST.read_text())
        generated = make_default_manifest()
        self.assertEqual(validate_manifest(checked_in), generated)
        self.assertEqual(
            checked_in["payload_sha256"], canonical_payload_sha256(checked_in)
        )

    def test_edge_banks_have_two_rounds_and_preserve_all_records(self):
        manifest = make_default_manifest()
        banks = compile_edge_banks(manifest)
        self.assertEqual(tuple(map(len, banks)), (512, 512, 512, 512))
        records = 0
        for source in range(256):
            for round_index in range(2):
                for bank in range(4):
                    word = banks[bank][source * 2 + round_index]
                    target = word & 0xFF
                    weight = (word >> 8) & 0xFF
                    if weight & 0x80:
                        weight -= 256
                    self.assertEqual(target % 4, bank)
                    self.assertEqual(weight < 0, source % 4 == 3)
                    records += 1
        self.assertEqual(records, 2048)

    def test_deterministic_export_is_byte_identical(self):
        manifest = make_default_manifest()
        with TemporaryDirectory() as first, TemporaryDirectory() as second:
            first_result = export_manifest(manifest, Path(first))
            second_result = export_manifest(manifest, Path(second))
            self.assertEqual(first_result, second_result)
            for filename in first_result["files"]:
                self.assertEqual(
                    (Path(first) / filename).read_bytes(),
                    (Path(second) / filename).read_bytes(),
                )

    def test_topology_rejects_dale_duplicate_self_bank_and_range_errors(self):
        mutations = []

        dale = make_default_manifest()
        dale["edges"][0]["weight"] *= -1
        mutations.append(dale)

        duplicate = make_default_manifest()
        duplicate["edges"][4]["target"] = duplicate["edges"][0]["target"]
        mutations.append(duplicate)

        self_edge = make_default_manifest()
        source = 4
        position = source * 8
        self_edge["edges"][position]["target"] = source
        mutations.append(self_edge)

        wrong_bank = make_default_manifest()
        wrong_bank["edges"][0]["target"] ^= 1
        mutations.append(wrong_bank)

        invalid_weight = make_default_manifest()
        invalid_weight["edges"][0]["weight"] = 0
        mutations.append(invalid_weight)

        malformed = make_default_manifest()
        malformed["edges"].pop()
        mutations.append(malformed)

        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)):
                rehash(mutation)
                with self.assertRaises(SNN2ManifestError):
                    validate_manifest(mutation)

    def test_checksum_covers_every_behavioral_field(self):
        manifest = make_default_manifest()
        changed = deepcopy(manifest)
        changed["dynamics"]["adaptation_step"] += 1
        with self.assertRaisesRegex(SNN2ManifestError, "payload_sha256 mismatch"):
            validate_manifest(changed)


class SNN2ReferenceTests(unittest.TestCase):

    def test_single_neuron_golden_traces(self):
        fixture = json.loads(GOLDEN_TRACES.read_text())
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                actual = step_alif_neuron(
                    ALIFState(**case["initial"]),
                    excitatory_events=case["input"]["excitatory_events"],
                    inhibitory_events=case["input"]["inhibitory_events"],
                    external_drive=case["input"]["external_drive"],
                    bias=case["input"]["bias"],
                    dynamics=case["dynamics"],
                    adaptation_gain_q8=case["input"]["adaptation_gain_q8"],
                )
                self.assertEqual(actual, ALIFState(**case["expected"]))

    def test_4096_sample_reference_is_deterministic_and_bounded(self):
        manifest = make_default_manifest()
        first = SNN2Reference(manifest)
        second = SNN2Reference(manifest)
        for sample in range(4096):
            previous_spike_count = first.spike_vector.bit_count()
            fixture = {
                "encoder_spikes": ((sample * 73) ^ (sample >> 3)) & 0xFF,
                "external_drive": 6_000 + (sample % 7) * 1_500,
                "inhibitory_gain_q8": (128, 256, 384, 512)[sample % 4],
                "adaptation_gain_q8": (0, 128, 256, 512)[(sample // 4) % 4],
            }
            self.assertEqual(first.step(**fixture), second.step(**fixture))
            self.assertEqual(first.snapshot(), second.snapshot())
            self.assertEqual(first.last_event_count, previous_spike_count * 8)
            self.assertTrue(all(
                -(1 << 17) <= state.v <= (1 << 17) - 1
                and 0 <= state.ie <= 0xFFFF
                and 0 <= state.ii <= 0xFFFF
                and 0 <= state.a <= 0xFFFF
                and 0 <= state.r <= 15
                for state in first.states
            ))
            self.assertTrue(-32768 <= first.last_outputs[0] <= 32767)
            self.assertTrue(0 <= first.last_outputs[1] <= 20_000)
            self.assertTrue(0 <= first.last_outputs[2] <= 20_000)
            self.assertTrue(-20_000 <= first.last_outputs[3] <= 20_000)

    def test_recurrent_events_have_exactly_one_sample_delay(self):
        manifest = make_default_manifest()
        model = SNN2Reference(manifest)
        model.step(encoder_spikes=0xFF, external_drive=16_000)
        previous_spikes = model.spike_vector.bit_count()
        self.assertEqual(model.last_event_count, 0)
        model.step(encoder_spikes=0, external_drive=0)
        self.assertEqual(model.last_event_count, previous_spikes * 8)


class SNN2EncoderTests(unittest.TestCase):

    def test_control_limits_and_center_are_frozen(self):
        model = SNN2EncoderReference(make_default_manifest())
        low = model.step((0, -32768, -32768, -32768))
        center = model.step((0, 0, 0, 0))
        high = model.step((0, 32767, 32767, 32767))
        self.assertEqual(
            (low.external_drive, low.inhibitory_gain_q8, low.adaptation_gain_q8),
            (3_072, 128, 0),
        )
        self.assertEqual(
            (
                center.external_drive,
                center.inhibitory_gain_q8,
                center.adaptation_gain_q8,
            ),
            (7_680, 320, 256),
        )
        self.assertEqual(
            (
                high.external_drive,
                high.inhibitory_gain_q8,
                high.adaptation_gain_q8,
            ),
            (12_288, 512, 512),
        )

    def test_each_frozen_center_frequency_selects_its_own_band(self):
        manifest = make_default_manifest()
        frequencies = manifest["encoder"]["center_frequencies_hz"]
        for expected_band, frequency in enumerate(frequencies):
            with self.subTest(frequency=frequency):
                model = SNN2EncoderReference(manifest)
                accumulated = [0] * 8
                for sample in range(8192):
                    signal = round(
                        2_000 * math.sin(
                            2 * math.pi * frequency * sample / 48_000
                        )
                    )
                    model.step((signal, 0, 0, 0))
                    if sample >= 4096:
                        for band, value in enumerate(model.last_band_samples):
                            accumulated[band] += abs(value)
                self.assertEqual(
                    max(range(8), key=accumulated.__getitem__), expected_band
                )

    def test_serial_rtl_matches_encoder_reference_and_holds_backpressure(self):
        manifest = make_default_manifest()
        dut = SNN2AudioEncoder(manifest)
        model = SNN2EncoderReference(manifest)
        fixtures = [
            (
                round(8_000 * math.sin(2 * math.pi * 640 * sample / 48_000)),
                (-4_000, 0, 4_000)[sample % 3],
                (4_000, 0, -4_000)[sample % 3],
                (-4_000, 4_000)[sample % 2],
            )
            for sample in range(64)
        ]

        async def bench(ctx):
            ctx.set(dut.o.ready, 0)
            for fixture in fixtures:
                expected = model.step(fixture)
                for channel, value in enumerate(fixture):
                    ctx.set(dut.i.payload[channel].as_value(), value)
                ctx.set(dut.i.valid, 1)
                while not ctx.get(dut.i.ready):
                    await ctx.tick()
                await ctx.tick()
                ctx.set(dut.i.valid, 0)
                while not ctx.get(dut.o.valid):
                    await ctx.tick()
                actual = (
                    ctx.get(dut.o.payload.encoder_spikes),
                    ctx.get(dut.o.payload.external_drive),
                    ctx.get(dut.o.payload.inhibitory_gain_q8),
                    ctx.get(dut.o.payload.adaptation_gain_q8),
                    tuple(ctx.get(dut.band_levels[index]) for index in range(8)),
                )
                self.assertEqual(actual, (
                    expected.encoder_spikes,
                    expected.external_drive,
                    expected.inhibitory_gain_q8,
                    expected.adaptation_gain_q8,
                    expected.band_levels,
                ))
                for _ in range(3):
                    await ctx.tick()
                    self.assertEqual(
                        (
                            ctx.get(dut.o.valid),
                            ctx.get(dut.o.payload.encoder_spikes),
                            ctx.get(dut.o.payload.external_drive),
                        ),
                        (1, expected.encoder_spikes, expected.external_drive),
                    )
                ctx.set(dut.o.ready, 1)
                await ctx.tick()
                ctx.set(dut.o.ready, 0)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()


class SNN2VisualizerTests(unittest.TestCase):

    def test_population_band_and_fault_colours_are_distinct(self):
        dut = SNN2Visualizer()

        async def bench(ctx):
            ctx.set(dut.y, 108)
            ctx.set(dut.x, 140)
            ctx.set(dut.membrane_levels, 8 << (2 * 4))
            await ctx.delay(1e-9)
            excitatory = (ctx.get(dut.r), ctx.get(dut.g), ctx.get(dut.b))

            ctx.set(dut.x, 156)
            ctx.set(dut.membrane_levels, 8 << (3 * 4))
            await ctx.delay(1e-9)
            inhibitory = (ctx.get(dut.r), ctx.get(dut.g), ctx.get(dut.b))
            self.assertNotEqual(excitatory, inhibitory)

            ctx.set(dut.x, 104 + 2 * 64 + 32)
            ctx.set(dut.y, 77)
            ctx.set(dut.band_levels, 255 << (2 * 8))
            await ctx.delay(1e-9)
            self.assertEqual(
                (ctx.get(dut.r), ctx.get(dut.g), ctx.get(dut.b)),
                (64, 208, 255),
            )

            ctx.set(dut.x, 630)
            ctx.set(dut.y, 60)
            ctx.set(dut.fault, 1)
            await ctx.delay(1e-9)
            self.assertEqual(
                (ctx.get(dut.r), ctx.get(dut.g), ctx.get(dut.b)),
                (255, 0, 0),
            )

        sim = Simulator(dut)
        sim.add_testbench(bench)
        sim.run()


class SNN2RTLTests(unittest.TestCase):

    def test_rtl_matches_reference_in_all_states_outputs_and_counters(self):
        manifest = make_default_manifest()
        dut = SparseALIFNetwork(manifest)
        reference = SNN2Reference(manifest)
        fixtures = [
            (0xFF, 16_000, 256, 256),
            (0x55, 9_000, 384, 128),
            (0xAA, 12_000, 128, 512),
            (0xFF, 16_000, 512, 256),
            (0x0F, 7_500, 256, 0),
            (0xF0, 14_000, 384, 512),
            (0x33, 11_000, 128, 128),
            (0xCC, 15_000, 512, 256),
        ]

        async def bench(ctx):
            ctx.set(dut.o.ready, 0)
            for sample, (
                encoder_spikes,
                external_drive,
                inhibitory_gain,
                adaptation_gain,
            ) in enumerate(fixtures):
                expected_output = reference.step(
                    encoder_spikes=encoder_spikes,
                    external_drive=external_drive,
                    inhibitory_gain_q8=inhibitory_gain,
                    adaptation_gain_q8=adaptation_gain,
                )
                ctx.set(dut.i.payload.encoder_spikes, encoder_spikes)
                ctx.set(dut.i.payload.external_drive, external_drive)
                ctx.set(dut.i.payload.inhibitory_gain_q8, inhibitory_gain)
                ctx.set(dut.i.payload.adaptation_gain_q8, adaptation_gain)
                ctx.set(dut.i.valid, 1)
                while not ctx.get(dut.i.ready):
                    await ctx.tick()
                await ctx.tick()
                ctx.set(dut.i.valid, 0)
                while not ctx.get(dut.o.valid):
                    await ctx.tick()

                actual_output = tuple(
                    ctx.get(dut.o.payload[channel].as_value())
                    for channel in range(4)
                )
                self.assertEqual(actual_output, expected_output)
                self.assertEqual(ctx.get(dut.spike_vector), reference.spike_vector)
                self.assertEqual(ctx.get(dut.spike_count), reference.spike_vector.bit_count())
                self.assertEqual(ctx.get(dut.event_count), reference.last_event_count)
                self.assertEqual(ctx.get(dut.sample_index), sample + 1)
                self.assertLessEqual(ctx.get(dut.scheduler_cycles), 640)
                self.assertLess(ctx.get(dut.deadline_cycles), 1250)
                self.assertEqual(ctx.get(dut.fault), 0)

                held = (
                    actual_output,
                    ctx.get(dut.spike_vector),
                    ctx.get(dut.sample_index),
                    ctx.get(dut.event_count),
                )
                for _ in range(4):
                    await ctx.tick()
                    self.assertEqual(
                        (
                            tuple(
                                ctx.get(dut.o.payload[channel].as_value())
                                for channel in range(4)
                            ),
                            ctx.get(dut.spike_vector),
                            ctx.get(dut.sample_index),
                            ctx.get(dut.event_count),
                        ),
                        held,
                    )

                for index, expected_state in enumerate(reference.states):
                    ctx.set(dut.debug_index, index)
                    await ctx.tick()
                    await ctx.tick()
                    self.assertEqual(
                        (
                            ctx.get(dut.debug_v),
                            ctx.get(dut.debug_ie),
                            ctx.get(dut.debug_ii),
                            ctx.get(dut.debug_a),
                            ctx.get(dut.debug_r),
                            ctx.get(dut.debug_s),
                        ),
                        (
                            expected_state.v,
                            expected_state.ie,
                            expected_state.ii,
                            expected_state.a,
                            expected_state.r,
                            expected_state.s,
                        ),
                    )

                ctx.set(dut.o.ready, 1)
                await ctx.tick()
                ctx.set(dut.o.ready, 0)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

    def test_all_neurons_spiking_issues_every_edge_before_deadline(self):
        manifest = make_default_manifest()
        initial_spikes = (1 << 256) - 1
        dut = SparseALIFNetwork(manifest, initial_spikes=initial_spikes)
        reference = SNN2Reference(manifest, initial_spikes=initial_spikes)
        expected = reference.step(
            encoder_spikes=0,
            external_drive=0,
            inhibitory_gain_q8=512,
            adaptation_gain_q8=0,
        )

        async def bench(ctx):
            ctx.set(dut.o.ready, 0)
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.i.payload.encoder_spikes, 0)
            ctx.set(dut.i.payload.external_drive, 0)
            ctx.set(dut.i.payload.inhibitory_gain_q8, 512)
            ctx.set(dut.i.payload.adaptation_gain_q8, 0)
            while not ctx.get(dut.i.ready):
                await ctx.tick()
            await ctx.tick()
            ctx.set(dut.i.valid, 0)
            while not ctx.get(dut.o.valid):
                await ctx.tick()
            self.assertEqual(
                tuple(
                    ctx.get(dut.o.payload[channel].as_value())
                    for channel in range(4)
                ),
                expected,
            )
            self.assertEqual(ctx.get(dut.event_count), 2048)
            self.assertEqual(ctx.get(dut.scheduler_cycles), 531)
            self.assertEqual(ctx.get(dut.deadline_cycles), 613)
            self.assertEqual(ctx.get(dut.fault), 0)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()


if __name__ == "__main__":
    unittest.main()
