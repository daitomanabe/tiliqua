# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""SNN2 population diagnostics and additive-performance visualization."""

from amaranth import Elaboratable, Module, Mux, Signal

from .snn_visualizer import SNNVisualizer


class SNN2Visualizer(Elaboratable):
    """Overlay encoder, population, scheduler, and fault diagnostics.

    The optional additive view represents the Mac demo's exact structural
    topology as five chord-tone lanes, ten harmonic rows, and twenty phase
    particles per cell. Particle brightness comes from the measured encoder
    bands; phase motion is illustrative because individual oscillator phases
    are not transported over the mono analogue link.
    """

    ADDITIVE_TONES = 5
    ADDITIVE_HARMONICS = 10
    ADDITIVE_MICRO_SINES = 20
    ADDITIVE_OSCILLATORS = (
        ADDITIVE_TONES * ADDITIVE_HARMONICS * ADDITIVE_MICRO_SINES
    )

    def __init__(self, *, external_rows=False, additive_view=False):
        self.external_rows = external_rows
        self.additive_view = additive_view
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
        if self.additive_view:
            return self._elaborate_additive(m)

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

    def _elaborate_additive(self, m):
        """Draw the 5 x 10 x 20 additive oscillator field."""

        checker = Signal()
        inside_bands = Signal()
        band_index = Signal(3)
        band_x = Signal(6)
        band_y = Signal(7)
        band_level = Signal(8)
        band_threshold = Signal(7)
        band_filled = Signal()

        inside_field = Signal()
        tone_index = Signal(3)
        tone_x = Signal(7)
        harmonic_index = Signal(4)
        harmonic_y = Signal(5)
        cell_edge = Signal()
        particle_x = Signal(7)
        micro_column = Signal(3)
        micro_row = Signal(2)
        micro_index = Signal(5)
        particle_pixel = Signal()
        particle_band = Signal(3)
        particle_level = Signal(8)
        particle_phase = Signal(8)
        particle_active = Signal()

        inside_sub = Signal()
        sub_x = Signal(10)
        sub_level = Signal(8)
        sub_filled = Signal()
        inside_rates = Signal()
        rate_region = Signal(2)
        rate_x = Signal(8)
        rate_filled = Signal()
        inside_fault = Signal()

        m.d.comb += [
            self.external_row_addr.eq(0),
            checker.eq(self.x[5] ^ self.y[5] ^ self.frame[4]),
            inside_bands.eq(
                (self.x >= 104) & (self.x < 616)
                & (self.y >= 48) & (self.y < 128)
            ),
            band_index.eq((self.x - 104)[6:9]),
            band_x.eq((self.x - 104)[:6]),
            band_y.eq(self.y - 48),
            band_level.eq(self.band_levels.word_select(band_index, 8)),
            band_threshold.eq(79 - (band_level >> 2)),
            band_filled.eq(band_y >= band_threshold),

            inside_field.eq(
                (self.x >= 40) & (self.x < 680)
                & (self.y >= 160) & (self.y < 480)
            ),
            tone_index.eq((self.x - 40)[7:10]),
            tone_x.eq((self.x - 40)[:7]),
            harmonic_index.eq((self.y - 160)[5:9]),
            harmonic_y.eq((self.y - 160)[:5]),
            cell_edge.eq(
                (tone_x < 2) | (tone_x >= 126) | (harmonic_y < 1)
            ),
            particle_x.eq(tone_x - 24),
            micro_column.eq(particle_x[4:7]),
            micro_row.eq(harmonic_y[3:5]),
            micro_index.eq(
                micro_column + (micro_row << 2) + micro_row
            ),
            particle_pixel.eq(
                (tone_x >= 24) & (tone_x < 104)
                & (particle_x[:4] < 6) & (harmonic_y[:3] < 5)
            ),
            particle_band.eq(Mux(
                harmonic_index > 7, 7, harmonic_index[:3]
            )),
            particle_level.eq(
                self.band_levels.word_select(particle_band, 8)
            ),
            particle_phase.eq(
                self.frame
                + (tone_index << 5)
                + (harmonic_index << 4)
                + (micro_index << 3)
            ),
            particle_active.eq(
                particle_phase[:6] < (particle_level >> 2)
            ),

            inside_sub.eq(
                (self.x >= 40) & (self.x < 680)
                & (self.y >= 496) & (self.y < 520)
            ),
            sub_x.eq(self.x - 40),
            sub_level.eq(self.band_levels[:8]),
            sub_filled.eq(sub_x < (sub_level << 1)),
            inside_rates.eq(
                (self.x >= 104) & (self.x < 616)
                & (self.y >= 544) & (self.y < 576)
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
                (self.x >= 664) & (self.x < 704)
                & (self.y >= 48) & (self.y < 128)
            ),
            self.r.eq(Mux(checker, 5, 2)),
            self.g.eq(Mux(checker, 8, 3)),
            self.b.eq(Mux(checker, 16, 7)),
        ]

        with m.If(inside_bands):
            with m.If((band_x < 2) | (band_x >= 62) | (band_y < 2)):
                m.d.comb += [self.r.eq(24), self.g.eq(38), self.b.eq(62)]
            with m.Elif(band_filled):
                m.d.comb += [
                    self.r.eq(32 + (band_index << 4)),
                    self.g.eq(224 - (band_index << 3)),
                    self.b.eq(255),
                ]
            with m.Else():
                m.d.comb += [self.r.eq(4), self.g.eq(7), self.b.eq(16)]
        with m.Elif(inside_field):
            with m.If(cell_edge):
                m.d.comb += [
                    self.r.eq(14 + (tone_index << 2)),
                    self.g.eq(24 + (tone_index << 3)),
                    self.b.eq(48 + (harmonic_index << 2)),
                ]
            with m.Elif(particle_pixel):
                with m.If(particle_active):
                    m.d.comb += [
                        self.r.eq(
                            40 + (tone_index << 4) + (harmonic_index << 2)
                        ),
                        self.g.eq(100 + (particle_level >> 1)),
                        self.b.eq(220 - (harmonic_index << 3)),
                    ]
                with m.Else():
                    m.d.comb += [
                        self.r.eq(4 + (tone_index << 1)),
                        self.g.eq(6 + (harmonic_index << 1)),
                        self.b.eq(12 + (micro_index >> 2)),
                    ]
            with m.Else():
                m.d.comb += [self.r.eq(3), self.g.eq(5), self.b.eq(11)]
        with m.Elif(inside_sub):
            with m.If((self.y < 498) | (self.y >= 518)):
                m.d.comb += [self.r.eq(24), self.g.eq(32), self.b.eq(48)]
            with m.Elif(sub_filled):
                m.d.comb += [self.r.eq(80), self.g.eq(112), self.b.eq(255)]
            with m.Else():
                m.d.comb += [self.r.eq(5), self.g.eq(7), self.b.eq(14)]
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
