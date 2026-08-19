# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Pixel-level semantics of the additive HDMI view."""

import unittest

from amaranth.sim import Simulator

from tiliqua.video import additive_visualizer as V
from tiliqua.video.additive_visualizer import AdditiveVisualizer


class AdditiveVisualizerTests(unittest.TestCase):

    def run_pixels(self, setup, probes):
        """``probes`` is a list of (x, y, overrides) -> list of (r, g, b)."""
        dut = AdditiveVisualizer()
        results = []

        async def bench(ctx):
            for name, value in setup.items():
                ctx.set(getattr(dut, name), value)
            for x, y, overrides in probes:
                ctx.set(dut.x, x)
                ctx.set(dut.y, y)
                for name, value in overrides.items():
                    ctx.set(getattr(dut, name), value)
                await ctx.delay(1e-9)
                results.append((ctx.get(dut.r), ctx.get(dut.g), ctx.get(dut.b)))

        sim = Simulator(dut)
        sim.add_testbench(bench)
        sim.run()
        return results

    def test_particle_field_addresses_all_1000_oscillators_and_follows_phase(self):
        dut = AdditiveVisualizer()
        addresses = set()
        groups = set()

        async def bench(ctx):
            for tone in range(5):
                for harmonic in range(10):
                    for voice in range(20):
                        ctx.set(dut.x, V.FIELD_X + tone * V.CELL_WIDTH + 10)
                        ctx.set(dut.y, V.FIELD_Y + harmonic * V.CELL_HEIGHT + V.VOICE_ROW0 + voice)
                        await ctx.delay(1e-9)
                        addresses.add(ctx.get(dut.particle_addr))
                        groups.add(ctx.get(dut.group_addr))
                        self.assertEqual(
                            ctx.get(dut.particle_addr), (tone * 10 + harmonic) * 20 + voice
                        )

        sim = Simulator(dut)
        sim.add_testbench(bench)
        sim.run()
        self.assertEqual(addresses, set(range(1000)))
        self.assertEqual(groups, set(range(50)))

        # Relative phase 0 paints voice row pixels 4..7; phase 128 paints 64..67.
        cell_x = V.FIELD_X + 2 * V.CELL_WIDTH
        row_y = V.FIELD_Y + 3 * V.CELL_HEIGHT + V.VOICE_ROW0 + 5
        base = {"group_amplitude": 30_000, "particle_data": 0}
        lit0, dark0, lit128, dark128, dim = self.run_pixels(base, [
            (cell_x + 5, row_y, {}),
            (cell_x + 9, row_y, {}),
            (cell_x + 65, row_y, {"particle_data": 128}),
            (cell_x + 5, row_y, {"particle_data": 128}),
            (cell_x + 65, row_y, {"particle_data": 128, "group_amplitude": 0}),
        ])
        self.assertGreater(sum(lit0), sum(dark0) + 100)
        self.assertGreater(sum(lit128), sum(dark128) + 100)
        self.assertGreater(sum(lit128), sum(dim) + 60)  # amplitude drives brightness

        # Low and air groups have distinct particle colours from body groups.
        body, low, air = self.run_pixels({"group_amplitude": 30_000, "particle_data": 0}, [
            (cell_x + 5, row_y, {"group_low": 0, "group_air": 0}),
            (cell_x + 5, row_y, {"group_low": 1, "group_air": 0}),
            (cell_x + 5, row_y, {"group_low": 0, "group_air": 1}),
        ])
        self.assertNotEqual(body, low)
        self.assertNotEqual(body, air)
        self.assertNotEqual(low, air)

        # The pan marker sits at the header row, centre for pan 0, right for +.
        header_y = V.FIELD_Y + 3 * V.CELL_HEIGHT + 2
        centre, right, off = self.run_pixels({"group_pan": 0}, [
            (cell_x + 64, header_y, {}),
            (cell_x + 64 + 30, header_y, {"group_pan": 60}),
            (cell_x + 64, header_y, {"group_pan": 60}),
        ])
        self.assertEqual(centre, (200, 200, 220))
        self.assertEqual(right, (200, 200, 220))
        self.assertNotEqual(off, (200, 200, 220))

    def test_level_bars_ownership_meters_cv_and_status_are_semantic(self):
        # Harmonic 3 at full level fills its bar; harmonic 4 at zero is empty.
        levels = 255 << (3 * 8)
        full, empty = self.run_pixels({"levels": levels}, [
            (V.BAR_X + 3 * V.BAR_W + 20, V.LEVELS_Y + 40, {}),
            (V.BAR_X + 4 * V.BAR_W + 20, V.LEVELS_Y + 40, {}),
        ])
        self.assertGreater(sum(full), sum(empty) + 300)

        # Ownership meter 0 (master): base 128 -> dim to x 32, effective 255 -> bright to 63.
        base = 128
        effective = 255
        dim_pixel, bright_pixel, none_pixel = self.run_pixels(
            {"base": base, "effective": effective}, [
                (V.BAR_X + 20, V.METERS_Y + 4, {}),     # inside base (x < 32), outside band rows
                (V.BAR_X + 40, V.METERS_Y + 20, {}),    # beyond base, inside effective (x < 63)
                (V.BAR_X + 40, V.METERS_Y + 20, {"effective": 100}),  # effective shorter than x
            ]
        )
        self.assertEqual(dim_pixel, (60, 70, 120))
        self.assertEqual(bright_pixel, (255, 150, 40))
        self.assertEqual(none_pixel, (6, 6, 12))

        # Bipolar pitch meter (index 6): positive fills right of centre.
        pos, neg, mid = self.run_pixels({"pitch": 100}, [
            (V.BAR_X + 6 * V.BAR_W + 40, V.METERS_Y + 20, {}),
            (V.BAR_X + 6 * V.BAR_W + 24, V.METERS_Y + 20, {}),
            (V.BAR_X + 6 * V.BAR_W + 24, V.METERS_Y + 20, {"pitch": -100}),
        ])
        self.assertEqual(pos, (255, 150, 40))
        self.assertEqual(neg, (6, 6, 12))
        self.assertEqual(mid, (255, 150, 40))

        # CV meter 1 (bipolar): +100 fills right of centre; IN 0 unipolar fills from left.
        cv_word = (100 << 8) | 200
        cv_right, cv_left, cv0 = self.run_pixels({"cv": cv_word}, [
            (V.CV_X + V.CV_W + 64 + 20, V.CV_Y + 20, {}),
            (V.CV_X + V.CV_W + 64 - 20, V.CV_Y + 20, {}),
            (V.CV_X + 100, V.CV_Y + 20, {}),
        ])
        self.assertEqual(cv_right[1], 220)
        self.assertEqual(cv_left, (6, 8, 10))
        self.assertEqual(cv0[1], 220)

        # Master bar and status indicators.
        master_on, master_off, link, fault, overrun, activity, dark = self.run_pixels(
            {"master_asq": 100, "link_alive": 1, "fault": 1, "overrun": 1, "activity": 1}, [
                (V.BAR_X + 150, V.MASTER_Y + 10, {}),
                (V.BAR_X + 250, V.MASTER_Y + 10, {}),
                (V.STATUS_X + 10, V.STATUS_Y + 10, {}),
                (V.STATUS_X + 64 + 10, V.STATUS_Y + 10, {}),
                (V.STATUS_X + 128 + 10, V.STATUS_Y + 10, {}),
                (V.STATUS_X + 192 + 10, V.STATUS_Y + 10, {}),
                (V.STATUS_X + 10, V.STATUS_Y + 10, {"link_alive": 0}),
            ]
        )
        self.assertEqual(master_on, (255, 120, 200))
        self.assertEqual(master_off, (8, 6, 12))
        self.assertEqual(link, (40, 230, 90))
        self.assertEqual(fault, (255, 0, 0))
        self.assertEqual(overrun, (255, 140, 0))
        self.assertEqual(activity, (200, 200, 255))
        self.assertEqual(dark, (14, 14, 20))


if __name__ == "__main__":
    unittest.main()
