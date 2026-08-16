# Copyright (c) 2024 S. Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

from amaranth import *
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from . import ASQ, mac


class VCA(wiring.Component):

    """
    Voltage Controlled Amplifier (simple multiplier with saturation).
    Output values are clipped to fit in the output type.

    Members
    -------
    i : :py:`In(stream.Signature(data.ArrayLayout(itype, 2)))`
        2-channel input stream.
    o : :py:`Out(stream.Signature(otype))`
        Output stream, :py:`i.payload[0] * i.payload[1]`.
    """

    def __init__(self, itype=mac.SQNative, otype=ASQ, macp=None):
        self.itype = itype
        self.macp = macp or mac.MAC.default()
        super().__init__({
            "i": In(stream.Signature(data.ArrayLayout(itype, 2))),
            "o": Out(stream.Signature(otype))
        })

    def elaborate(self, platform):
        m = Module()

        m.submodules.macp = mp = self.macp
        operand_a = Signal(self.itype)
        operand_b = Signal(self.itype)

        with m.FSM() as fsm:

            with m.State('WAIT-VALID'):
                m.d.comb += self.i.ready.eq(1),
                with m.If(self.i.valid):
                   # The upstream stream is free to advance as soon as ready
                   # is asserted. Latch both operands so a delayed/shared MAC
                   # never observes the following transaction by accident.
                   m.d.sync += [
                       operand_a.eq(self.i.payload[0]),
                       operand_b.eq(self.i.payload[1]),
                   ]
                   m.next = 'MAC'

            with m.State('MAC'):
                with mp.Multiply(m, a=operand_a, b=operand_b):
                    m.d.sync += self.o.payload.eq(mp.result.z.saturate(self.o.payload.shape()))
                    m.next = 'WAIT-READY'

            with m.State('WAIT-READY'):
                m.d.comb += self.o.valid.eq(1),
                with m.If(self.o.ready):
                    m.next = 'WAIT-VALID'

        return m
