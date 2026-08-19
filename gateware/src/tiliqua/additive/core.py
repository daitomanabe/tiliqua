# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Control engine + oscillator bank: the complete additive synthesis core."""

from amaranth import Module, Signal, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from tiliqua.dsp import ASQ

from .control import PAYLOAD_LAYOUT
from .control_engine import EFFECTIVE_LAYOUT, GROUP_DISPLAY_LAYOUT, AdditiveControlEngine
from .engine import AdditiveOscillatorBank, MASTER_BITS


class AdditiveCore(wiring.Component):
    """Calibrated ``IN 0-3`` + host frame -> calibrated additive ``OUT 0-3``.

    One input transfer produces one output transfer. The control engine
    recomputes the parameter bank every 128 samples from the host frame and
    the block-averaged CV, and the oscillator bank swaps to it at the next
    block boundary, so the sample stream is exactly the reference model's.
    """

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))
    frame: In(PAYLOAD_LAYOUT)
    effective: Out(EFFECTIVE_LAYOUT)
    cv_smoothed: Out(data.ArrayLayout(unsigned(16), 4))
    master_asq: Out(unsigned(MASTER_BITS))
    sample_index: Out(unsigned(32))
    sample_cycles: Out(unsigned(12))
    block_cycles: Out(unsigned(18))
    blocks_done: Out(unsigned(16))
    fault: Out(1)
    display_addr: Out(unsigned(10))
    display_data: Out(unsigned(8))
    display_en: Out(1)
    group_display_addr: Out(unsigned(6))
    group_display_data: Out(GROUP_DISPLAY_LAYOUT)
    group_display_en: Out(1)

    def __init__(self):
        self.bank = AdditiveOscillatorBank()
        self.control = AdditiveControlEngine()
        super().__init__()

    def elaborate(self, platform):
        m = Module()
        m.submodules.bank = bank = self.bank
        m.submodules.control = control = self.control
        wiring.connect(m, wiring.flipped(self.i), bank.i)
        wiring.connect(m, bank.o, wiring.flipped(self.o))
        m.d.comb += [
            control.frame.eq(self.frame),
            control.cv.eq(bank.cv),
            control.sample_start.eq(bank.sample_start),
            control.block_start.eq(bank.block_start),
            bank.param_addr.eq(control.param_addr),
            bank.param_data.eq(control.param_data),
            bank.param_en.eq(control.param_en),
            bank.commit.eq(control.commit),
            bank.master_asq.eq(control.master_asq),
            self.effective.eq(control.effective),
            self.master_asq.eq(control.master_asq),
            self.sample_index.eq(bank.sample_index),
            self.sample_cycles.eq(bank.sample_cycles),
            self.block_cycles.eq(control.block_cycles),
            self.blocks_done.eq(control.blocks_done),
            self.fault.eq(bank.fault | control.overrun),
            self.display_addr.eq(bank.display_addr),
            self.display_data.eq(bank.display_data),
            self.display_en.eq(bank.display_en),
            self.group_display_addr.eq(control.group_display_addr),
            self.group_display_data.eq(control.group_display_data),
            self.group_display_en.eq(control.group_display_en),
        ]
        for channel in range(4):
            m.d.comb += self.cv_smoothed[channel].eq(control.cv_smoothed[channel])
        return m
