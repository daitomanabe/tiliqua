# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""SNN2 16x16 population view with fixed diagnostic regions."""

from amaranth import Elaboratable, Module, Mux, Signal

from .snn_visualizer import SNNVisualizer


class SNN2Visualizer(Elaboratable):
    """Overlay encoder, population, scheduler, and fault diagnostics."""

    def __init__(self, *, external_rows=False):
        self.external_rows = external_rows
        self.x = Signal(12)
        self.y = Signal(12)
        self.spikes = Signal(256)
        self.membrane_levels = Signal(256 * 4)
        self.band_levels = Signal(8 * 8)
        self.excitatory_count = Signal(range(193))
        self.inhibitory_count = Signal(range(65))
        self.scheduler_cycles = Signal(range(2049))
        self.fault = Signal()
        self.frame = Signal(8)
        self.external_row_addr = Signal(4)
        self.external_row_data = Signal(16 * 5)
        self.r = Signal(8)
        self.g = Signal(8)
        self.b = Signal(8)

    def elaborate(self, platform):
        m = Module()
        m.submodules.base = base = SNNVisualizer(
            neuron_count=256,
            membrane_level_bits=4,
            external_rows=self.external_rows,
            external_row_neurons=16,
            inhibitory_stride=4,
        )
        m.d.comb += [
            base.x.eq(self.x),
            base.y.eq(self.y),
            base.spikes.eq(self.spikes),
            base.membrane_levels.eq(self.membrane_levels),
            base.activity.eq(self.excitatory_count + self.inhibitory_count),
            base.burst.eq((self.excitatory_count + self.inhibitory_count) > 32),
            base.frame.eq(self.frame),
            base.external_row_data.eq(self.external_row_data),
            self.external_row_addr.eq(base.external_row_addr),
            base.control_selected.eq(0),
            base.control_override.eq(0),
            base.control_levels.eq(0),
            self.r.eq(base.r),
            self.g.eq(base.g),
            self.b.eq(base.b),
        ]

        inside_bands = Signal()
        band_index = Signal(3)
        band_x = Signal(6)
        band_y = Signal(5)
        band_level = Signal(8)
        band_filled = Signal()
        inside_rates = Signal()
        rate_region = Signal(2)
        rate_x = Signal(8)
        rate_filled = Signal()
        inside_fault = Signal()
        m.d.comb += [
            inside_bands.eq(
                (self.x >= 104) & (self.x < 616)
                & (self.y >= 56) & (self.y < 80)
            ),
            band_index.eq((self.x - 104)[6:9]),
            band_x.eq((self.x - 104)[:6]),
            band_y.eq(self.y - 56),
            band_level.eq(self.band_levels.word_select(band_index, 8)),
            band_filled.eq(((23 - band_y) << 4) < band_level),
            inside_rates.eq(
                (self.x >= 104) & (self.x < 616)
                & (self.y >= 84) & (self.y < 96)
            ),
            rate_region.eq(Mux(self.x < 274, 0, Mux(self.x < 444, 1, 2))),
            rate_x.eq(Mux(
                self.x < 274,
                self.x - 104,
                Mux(self.x < 444, self.x - 274, self.x - 444),
            )),
            rate_filled.eq(Mux(
                rate_region == 0,
                rate_x * 192 < self.excitatory_count * 171,
                Mux(
                    rate_region == 1,
                    rate_x * 64 < self.inhibitory_count * 171,
                    rate_x * 640 < self.scheduler_cycles * 171,
                ),
            )),
            inside_fault.eq(
                (self.x >= 624) & (self.x < 696)
                & (self.y >= 56) & (self.y < 96)
            ),
        ]

        with m.If(inside_bands):
            with m.If((band_x < 2) | (band_x >= 62) | (band_y < 2)):
                m.d.comb += [self.r.eq(30), self.g.eq(42), self.b.eq(64)]
            with m.Elif(band_filled):
                m.d.comb += [
                    self.r.eq(32 + (band_index << 4)),
                    self.g.eq(224 - (band_index << 3)),
                    self.b.eq(255),
                ]
            with m.Else():
                m.d.comb += [self.r.eq(5), self.g.eq(8), self.b.eq(18)]
        with m.Elif(inside_rates):
            with m.If(rate_filled):
                with m.If(rate_region == 0):
                    m.d.comb += [self.r.eq(40), self.g.eq(220), self.b.eq(255)]
                with m.Elif(rate_region == 1):
                    m.d.comb += [self.r.eq(255), self.g.eq(96), self.b.eq(32)]
                with m.Else():
                    m.d.comb += [self.r.eq(190), self.g.eq(120), self.b.eq(255)]
            with m.Else():
                m.d.comb += [self.r.eq(8), self.g.eq(8), self.b.eq(12)]
        with m.Elif(inside_fault):
            m.d.comb += [
                self.r.eq(Mux(self.fault, 255, 16)),
                self.g.eq(Mux(self.fault, 0, 96)),
                self.b.eq(Mux(self.fault, 0, 48)),
            ]

        return m
