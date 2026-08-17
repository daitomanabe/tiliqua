# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import unittest

from amaranth.sim import Simulator

from tiliqua import midi
from tiliqua.dsp.snn_control import SNNControlSurface, SNNMidiCCDecoder
from tiliqua.test import stream


class SNNControlSurfaceTests(unittest.TestCase):

    def test_analogue_midi_encoder_and_button_priority(self):
        dut = SNNControlSurface(clock_sync_hz=100, clear_hold_seconds=0.1)

        async def pulse_midi(ctx, kind, byte0, byte1=0):
            ctx.set(dut.i_midi.payload.status.kind, kind)
            ctx.set(dut.i_midi.payload.midi_payload.raw.byte0, byte0)
            ctx.set(dut.i_midi.payload.midi_payload.raw.byte1, byte1)
            ctx.set(dut.i_midi.valid, 1)
            await ctx.tick()
            ctx.set(dut.i_midi.valid, 0)

        async def bench(ctx):
            ctx.set(dut.i.valid, 1)
            ctx.set(dut.o.ready, 1)
            analogue = (1000, -500, 250, 750)
            for channel, value in enumerate(analogue):
                ctx.set(dut.i.payload[channel].as_value(), value)
            await ctx.tick()
            self.assertEqual(
                tuple(ctx.get(dut.o.payload[index].as_value()) for index in range(4)),
                analogue,
            )
            self.assertEqual(ctx.get(dut.override_mask), 0)

            await pulse_midi(ctx, midi.Status.Kind.CONTROL_CHANGE, 20, 127)
            self.assertEqual(ctx.get(dut.override_mask), 0b0001)
            self.assertEqual(ctx.get(dut.control_values[0]), 8000)
            self.assertEqual(ctx.get(dut.o.payload[0].as_value()), 8000)
            self.assertEqual(ctx.get(dut.o.payload[1].as_value()), -500)

            await pulse_midi(ctx, midi.Status.Kind.CONTROL_CHANGE, 21, 0)
            self.assertEqual(ctx.get(dut.override_mask), 0b0011)
            self.assertEqual(ctx.get(dut.control_values[1]), -4000)
            self.assertEqual(ctx.get(dut.selected), 1)

            # CC 24 below midpoint releases only the selected parameter.
            await pulse_midi(ctx, midi.Status.Kind.CONTROL_CHANGE, 24, 0)
            self.assertEqual(ctx.get(dut.override_mask), 0b0001)
            self.assertEqual(ctx.get(dut.o.payload[1].as_value()), -500)

            # One encoder step starts from the latest analogue value.
            ctx.set(dut.encoder_direction, 1)
            ctx.set(dut.encoder_step, 1)
            await ctx.tick()
            ctx.set(dut.encoder_step, 0)
            self.assertEqual(ctx.get(dut.override_mask), 0b0011)
            self.assertEqual(ctx.get(dut.control_values[1]), -375)

            # Short release moves selection without changing a value.
            ctx.set(dut.button, 1)
            await ctx.tick()
            ctx.set(dut.button, 0)
            await ctx.tick()
            self.assertEqual(ctx.get(dut.selected), 2)

            # Program 4 recalls one bounded four-control preset.
            await pulse_midi(ctx, midi.Status.Kind.PROGRAM_CHANGE, 4)
            self.assertEqual(ctx.get(dut.override_mask), 0b1111)
            self.assertEqual(
                tuple(ctx.get(value) for value in dut.control_values),
                (5200, -2400, 2400, -2400),
            )

            # A one-second hold at this compact test clock returns to analogue.
            ctx.set(dut.button, 1)
            for _ in range(11):
                await ctx.tick()
            self.assertEqual(ctx.get(dut.override_mask), 0)
            ctx.set(dut.button, 0)
            await ctx.tick()
            self.assertEqual(
                tuple(ctx.get(dut.o.payload[index].as_value()) for index in range(4)),
                analogue,
            )

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()


class SNNMidiCCDecoderTests(unittest.TestCase):

    def test_cc_program_running_status_and_realtime(self):
        dut = SNNMidiCCDecoder()

        async def bench(ctx):
            for byte in (0xB2, 20, 64):
                await stream.put(ctx, dut.i, byte)
            first = await stream.get(ctx, dut.o)
            for byte in (0xF8, 21, 127):
                await stream.put(ctx, dut.i, byte)
            second = await stream.get(ctx, dut.o)
            self.assertEqual(first.status.kind, midi.Status.Kind.CONTROL_CHANGE)
            self.assertEqual(first.status.nibble.channel, 2)
            self.assertEqual(first.midi_payload.control_change.controller_number, 20)
            self.assertEqual(first.midi_payload.control_change.data, 64)
            self.assertEqual(second.midi_payload.control_change.controller_number, 21)
            self.assertEqual(second.midi_payload.control_change.data, 127)

            for byte in (0xC3, 5):
                await stream.put(ctx, dut.i, byte)
            program = await stream.get(ctx, dut.o)
            self.assertEqual(program.status.kind, midi.Status.Kind.PROGRAM_CHANGE)
            self.assertEqual(program.status.nibble.channel, 3)
            self.assertEqual(program.midi_payload.program_change.program_number, 5)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

    def test_all_midi_cc_extremes_are_bounded(self):
        dut = SNNControlSurface(clock_sync_hz=100, clear_hold_seconds=0.1)

        async def bench(ctx):
            ctx.set(dut.o.ready, 1)
            for cc in range(20, 24):
                for value in (0, 127):
                    ctx.set(dut.i_midi.payload.status.kind,
                            midi.Status.Kind.CONTROL_CHANGE)
                    ctx.set(
                        dut.i_midi.payload.midi_payload.control_change.controller_number,
                        cc,
                    )
                    ctx.set(dut.i_midi.payload.midi_payload.control_change.data, value)
                    ctx.set(dut.i_midi.valid, 1)
                    await ctx.tick()
                    ctx.set(dut.i_midi.valid, 0)
                    actual = ctx.get(dut.control_values[cc - 20])
                    if cc == 20:
                        self.assertTrue(0 <= actual <= 8000)
                    else:
                        self.assertTrue(-4000 <= actual <= 4000)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
