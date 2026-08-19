# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Bit-serial arithmetic units against Python integer semantics."""

import math
import random
import unittest

from amaranth.sim import Simulator

from tiliqua.additive.arith import SerialDivider, SerialMultiplier, SerialSqrt


class SerialArithmeticTests(unittest.TestCase):

    def test_multiplier_is_exact_for_signed_operands(self):
        dut = SerialMultiplier(width=40)
        rng = random.Random(1)
        cases = [(0, 0), (1, -1), (-1, -1), (2**39 - 1, 2**39 - 1), (-(2**39), 3),
                 (-(2**39), -(2**39)), (12345, -6789), (83, -(1 << 30))]
        cases += [(rng.randrange(-(2**39), 2**39), rng.randrange(-(2**39), 2**39))
                  for _ in range(60)]
        cases += [(rng.randrange(-(2**16), 2**16), rng.randrange(0, 2**17)) for _ in range(40)]
        results = []

        async def bench(ctx):
            for a, b in cases:
                ctx.set(dut.a, a)
                ctx.set(dut.b, b)
                ctx.set(dut.start, 1)
                await ctx.tick()
                ctx.set(dut.start, 0)
                cycles = 1
                while not ctx.get(dut.done):
                    await ctx.tick()
                    cycles += 1
                results.append((ctx.get(dut.product), cycles))  # valid in the done cycle
                await ctx.tick()

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        for (a, b), (product, cycles) in zip(cases, results):
            self.assertEqual(product, a * b, (a, b))
            self.assertLessEqual(cycles, 43)

    def test_divider_and_sqrt_are_exact(self):
        divider = SerialDivider(width=32)
        root = SerialSqrt(width=40)
        rng = random.Random(2)
        div_cases = [(0, 1), (1, 1), (2**32 - 1, 1), (2**32 - 1, 2**32 - 1), (52430 << 16, 256),
                     (52430 << 16, 2**18 - 1), (7, 3)]
        div_cases += [(rng.randrange(2**32), rng.randrange(1, 2**18)) for _ in range(60)]
        sqrt_cases = [0, 1, 2, 3, 4, 2**40 - 1, 2**36, 50 * 32767**2, 255, 256, 65535, 65536]
        sqrt_cases += [rng.randrange(2**40) for _ in range(60)]
        div_results = []
        sqrt_results = []

        async def div_bench(ctx):
            for dividend, divisor in div_cases:
                ctx.set(divider.dividend, dividend)
                ctx.set(divider.divisor, divisor)
                ctx.set(divider.start, 1)
                await ctx.tick()
                ctx.set(divider.start, 0)
                while not ctx.get(divider.done):
                    await ctx.tick()
                div_results.append(ctx.get(divider.quotient))  # valid in the done cycle
                await ctx.tick()

        async def sqrt_bench(ctx):
            for radicand in sqrt_cases:
                ctx.set(root.radicand, radicand)
                ctx.set(root.start, 1)
                await ctx.tick()
                ctx.set(root.start, 0)
                while not ctx.get(root.done):
                    await ctx.tick()
                sqrt_results.append(ctx.get(root.root))  # valid in the done cycle
                await ctx.tick()

        for dut, bench in ((divider, div_bench), (root, sqrt_bench)):
            sim = Simulator(dut)
            sim.add_clock(1e-6)
            sim.add_testbench(bench)
            sim.run()
        for (dividend, divisor), quotient in zip(div_cases, div_results):
            self.assertEqual(quotient, dividend // divisor, (dividend, divisor))
        for radicand, value in zip(sqrt_cases, sqrt_results):
            self.assertEqual(value, math.isqrt(radicand), radicand)


if __name__ == "__main__":
    unittest.main()
