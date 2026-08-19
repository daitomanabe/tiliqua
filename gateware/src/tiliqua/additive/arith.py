# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Bit-serial multiply, divide, and square-root units for the control engine.

The block-rate control engine has ~160,000 sync cycles per 128-sample block
and performs a few thousand operations, so bit-serial arithmetic keeps the
logic small, avoids DSP-tile timing risk, and stays bit-exact with the Python
reference (floor semantics everywhere).
"""

from amaranth import Cat, Const, Module, Mux, Signal, signed, unsigned
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out


class SerialMultiplier(wiring.Component):
    """Signed ``width`` x ``width`` -> signed ``2 * width`` in <= ``width + 2`` cycles.

    Operates on magnitudes with an early exit when the remaining multiplier
    bits are zero, then restores the sign. ``done`` pulses for one cycle with
    ``product`` valid; ``product`` then holds until the next ``start``.
    """

    def __init__(self, width=40):
        self.width = width
        super().__init__({
            "a": In(signed(width)),
            "b": In(signed(width)),
            "start": In(1),
            "busy": Out(1),
            "done": Out(1),
            "product": Out(signed(2 * width)),
        })

    def elaborate(self, platform):
        m = Module()
        width = self.width
        multiplicand = Signal(unsigned(2 * width))
        multiplier = Signal(unsigned(width))
        accumulator = Signal(unsigned(2 * width))
        negative = Signal()
        a_mag = Signal(unsigned(width))
        b_mag = Signal(unsigned(width))
        m.d.comb += [
            a_mag.eq(Mux(self.a < 0, -self.a, self.a)),
            b_mag.eq(Mux(self.b < 0, -self.b, self.b)),
            self.done.eq(0),
        ]
        with m.FSM():
            with m.State("IDLE"):
                with m.If(self.start):
                    m.d.sync += [
                        multiplicand.eq(a_mag),
                        multiplier.eq(b_mag),
                        accumulator.eq(0),
                        negative.eq((self.a < 0) ^ (self.b < 0)),
                    ]
                    m.next = "RUN"
            with m.State("RUN"):
                m.d.comb += self.busy.eq(1)
                with m.If(multiplier == 0):
                    m.d.sync += self.product.eq(
                        Mux(negative, -accumulator, accumulator)
                    )
                    m.next = "DONE"
                with m.Else():
                    with m.If(multiplier[0]):
                        m.d.sync += accumulator.eq(accumulator + multiplicand)
                    m.d.sync += [
                        multiplicand.eq(multiplicand << 1),
                        multiplier.eq(multiplier >> 1),
                    ]
            with m.State("DONE"):
                m.d.comb += [self.busy.eq(1), self.done.eq(1)]
                m.next = "IDLE"
        return m


class SerialDivider(wiring.Component):
    """Unsigned restoring divider: ``quotient = dividend // divisor`` in ``width + 2`` cycles."""

    def __init__(self, width=32):
        self.width = width
        super().__init__({
            "dividend": In(unsigned(width)),
            "divisor": In(unsigned(width)),
            "start": In(1),
            "busy": Out(1),
            "done": Out(1),
            "quotient": Out(unsigned(width)),
        })

    def elaborate(self, platform):
        m = Module()
        width = self.width
        remainder = Signal(unsigned(width + 1))
        quotient = Signal(unsigned(width))
        divisor = Signal(unsigned(width))
        count = Signal(range(width + 1))
        m.d.comb += self.done.eq(0)
        trial = Signal(unsigned(width + 2))
        m.d.comb += trial.eq(Cat(quotient[width - 1], remainder))
        with m.FSM():
            with m.State("IDLE"):
                with m.If(self.start):
                    m.d.sync += [
                        remainder.eq(0),
                        quotient.eq(self.dividend),
                        divisor.eq(self.divisor),
                        count.eq(0),
                    ]
                    m.next = "RUN"
            with m.State("RUN"):
                m.d.comb += self.busy.eq(1)
                with m.If(trial >= divisor):
                    m.d.sync += [
                        remainder.eq(trial - divisor),
                        quotient.eq(Cat(1, quotient[:width - 1])),
                    ]
                with m.Else():
                    m.d.sync += [
                        remainder.eq(trial),
                        quotient.eq(Cat(0, quotient[:width - 1])),
                    ]
                m.d.sync += count.eq(count + 1)
                with m.If(count == width - 1):
                    m.next = "DONE"
            with m.State("DONE"):
                m.d.comb += [self.busy.eq(1), self.done.eq(1)]
                m.next = "IDLE"
        # The quotient register holds its final value from the DONE cycle on.
        m.d.comb += self.quotient.eq(quotient)
        return m


class SerialSqrt(wiring.Component):
    """Unsigned restoring integer square root: ``root = isqrt(radicand)``."""

    def __init__(self, width=40):
        if width % 2:
            width += 1
        self.width = width
        super().__init__({
            "radicand": In(unsigned(width)),
            "start": In(1),
            "busy": Out(1),
            "done": Out(1),
            "root": Out(unsigned(width // 2)),
        })

    def elaborate(self, platform):
        m = Module()
        width = self.width
        half = width // 2
        remainder = Signal(unsigned(half + 2))
        root = Signal(unsigned(half))
        value = Signal(unsigned(width))
        count = Signal(range(half + 1))
        m.d.comb += self.done.eq(0)
        trial_rem = Signal(unsigned(half + 4))
        trial_sub = Signal(unsigned(half + 4))
        m.d.comb += [
            trial_rem.eq(Cat(value[width - 2:width], remainder)),
            trial_sub.eq(Cat(Const(1, 1), Const(0, 1), root)),  # 4 * root + 1
        ]
        with m.FSM():
            with m.State("IDLE"):
                with m.If(self.start):
                    m.d.sync += [
                        remainder.eq(0),
                        root.eq(0),
                        value.eq(self.radicand),
                        count.eq(0),
                    ]
                    m.next = "RUN"
            with m.State("RUN"):
                m.d.comb += self.busy.eq(1)
                with m.If(trial_rem >= trial_sub):
                    m.d.sync += [
                        remainder.eq(trial_rem - trial_sub),
                        root.eq(Cat(1, root[:half - 1])),
                    ]
                with m.Else():
                    m.d.sync += [
                        remainder.eq(trial_rem),
                        root.eq(Cat(0, root[:half - 1])),
                    ]
                m.d.sync += [
                    value.eq(value << 2),
                    count.eq(count + 1),
                ]
                with m.If(count == half - 1):
                    m.next = "DONE"
            with m.State("DONE"):
                m.d.comb += [self.busy.eq(1), self.done.eq(1)]
                m.next = "IDLE"
        # The root register holds its final value from the DONE cycle on.
        m.d.comb += self.root.eq(root)
        return m
