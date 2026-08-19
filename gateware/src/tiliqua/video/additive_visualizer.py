# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""HDMI view of the real additive synthesis state (720x720).

Every drawn element is driven by committed engine state, never by an
illustrative animation:

* the 5 x 10 particle field shows, per (tone, harmonic) cell, the twenty
  voices as rows whose horizontal position is the oscillator's phase relative
  to voice 0 of its group -- detune beating and phase spread are therefore
  visible as slowly rotating diagonal streaks -- and whose brightness is the
  group's committed amplitude; the cell header carries the real pan;
* the top bars are the ten UI harmonic levels of the latched host frame;
* the ownership meters show the UI base value (dim) under the effective
  value after CV modulation (bright) for master, sub, spread, evolution,
  width, and detune, plus bipolar pitch and tilt that are CV-only;
* the CV meters show the four smoothed CV inputs;
* the bottom row shows the master level, block-engine utilization, and the
  link-alive / fault / overrun / frame-activity indicators.
"""

from amaranth import Elaboratable, Module, Mux, Signal, signed

FIELD_X = 40
FIELD_Y = 160
CELL_WIDTH = 128
CELL_HEIGHT = 32
VOICE_ROW0 = 6
VOICE_COUNT = 20
PARTICLE_WIDTH = 4
PARTICLE_SPAN = 120  # horizontal travel for one full relative-phase turn

LEVELS_Y = 24
LEVELS_H = 80
METERS_Y = 112
METERS_H = 40
CV_Y = 488
CV_H = 40
MASTER_Y = 536
MASTER_H = 24
UTIL_Y = 568
UTIL_H = 12
STATUS_Y = 592
STATUS_H = 32
BAR_X = 40
BAR_W = 64
CV_X = 104
CV_W = 128
STATUS_X = 104


class AdditiveVisualizer(Elaboratable):
    """Combinational pixel painter for the additive instrument."""

    OWNERSHIP_METERS = 8  # master, sub, spread, evolution, width, detune, pitch, tilt

    def __init__(self):
        self.x = Signal(12)
        self.y = Signal(12)
        self.frame = Signal(8)
        # Per-oscillator relative phase (dual-clock BRAM, 1-clock latency).
        self.particle_addr = Signal(10)
        self.particle_data = Signal(8)
        # Per-group display record (dual-clock BRAM, 1-clock latency).
        self.group_addr = Signal(6)
        self.group_amplitude = Signal(16)
        self.group_low = Signal()
        self.group_air = Signal()
        self.group_pan = Signal(signed(8))
        # Scalars (already synchronised and frame-latched by the top).
        self.levels = Signal(10 * 8)
        self.base = Signal(8 * 8)          # master, sub, spread, evolution, width, detune, 0, 0
        self.effective = Signal(8 * 8)     # same order, after CV
        self.pitch = Signal(signed(8))     # effective pitch offset, +/-127 = +/-2 st
        self.tilt = Signal(signed(8))      # smoothed IN 2
        self.cv = Signal(4 * 8)            # smoothed CV, signed bytes (IN 0 unipolar)
        self.master_asq = Signal(8)        # master_asq >> 5
        self.utilization = Signal(8)       # block cycles >> 10
        self.link_alive = Signal()
        self.fault = Signal()
        self.overrun = Signal()
        self.activity = Signal()           # toggles on each accepted frame
        self.r = Signal(8)
        self.g = Signal(8)
        self.b = Signal(8)

    def elaborate(self, platform):
        m = Module()
        x = self.x
        y = self.y

        # ---------------- particle field ----------------
        inside_field = Signal()
        tone = Signal(3)
        tone_x = Signal(7)
        harmonic = Signal(4)
        cell_y = Signal(5)
        voice = Signal(5)
        voice_row = Signal()
        header_row = Signal()
        group = Signal(6)
        particle_x = Signal(8)
        particle_hit = Signal()
        amplitude8 = Signal(8)
        pan_x = Signal(8)
        pan_hit = Signal()
        m.d.comb += [
            inside_field.eq(
                (x >= FIELD_X) & (x < FIELD_X + 5 * CELL_WIDTH)
                & (y >= FIELD_Y) & (y < FIELD_Y + 10 * CELL_HEIGHT)
            ),
            tone.eq((x - FIELD_X)[7:10]),
            tone_x.eq((x - FIELD_X)[:7]),
            harmonic.eq((y - FIELD_Y)[5:9]),
            cell_y.eq((y - FIELD_Y)[:5]),
            voice.eq(cell_y - VOICE_ROW0),
            voice_row.eq((cell_y >= VOICE_ROW0) & (cell_y < VOICE_ROW0 + VOICE_COUNT)),
            header_row.eq(cell_y == 2),
            group.eq((tone << 3) + (tone << 1) + harmonic),  # tone * 10 + harmonic
            self.group_addr.eq(group),
            # address = group * 20 + voice
            self.particle_addr.eq((group << 4) + (group << 2) + voice),
            # data * 120 = data * 128 - data * 8, adders only.
            particle_x.eq(4 + (((self.particle_data << 7) - (self.particle_data << 3)) >> 8)),
            particle_hit.eq(
                voice_row & (tone_x >= particle_x) & (tone_x < particle_x + PARTICLE_WIDTH)
            ),
            amplitude8.eq(self.group_amplitude >> 7),
            pan_x.eq(64 + (self.group_pan >> 1)),
            pan_hit.eq(header_row & (tone_x >= pan_x) & (tone_x < pan_x + 2)),
        ]

        # ---------------- bars ----------------
        bar_index = Signal(4)
        bar_x = Signal(6)
        inside_levels = Signal()
        level_y = Signal(7)
        level_value = Signal(8)
        level_filled = Signal()
        inside_meters = Signal()
        meter_index = Signal(4)
        meter_y = Signal(6)
        base_value = Signal(8)
        effective_value = Signal(8)
        base_filled = Signal()
        effective_filled = Signal()
        bipolar_meter = Signal()
        bipolar_value = Signal(signed(8))
        bipolar_filled = Signal()
        inside_cv = Signal()
        cv_index = Signal(2)
        cv_x = Signal(7)
        cv_value = Signal(signed(8))
        cv_filled = Signal()
        inside_master = Signal()
        master_x = Signal(10)
        inside_util = Signal()
        inside_status = Signal()
        status_index = Signal(3)
        status_on = Signal()
        checker = Signal()
        m.d.comb += [
            bar_index.eq((x - BAR_X)[6:10]),
            bar_x.eq((x - BAR_X)[:6]),
            inside_levels.eq(
                (x >= BAR_X) & (x < BAR_X + 10 * BAR_W) & (y >= LEVELS_Y) & (y < LEVELS_Y + LEVELS_H)
            ),
            level_y.eq(y - LEVELS_Y),
            level_value.eq(self.levels.word_select(bar_index, 8)),
            # 80 rows, fill from the bottom: row r filled when (79 - r) * 3.2 < value
            level_filled.eq(((LEVELS_H - 1 - level_y) << 4) < ((level_value << 2) + level_value)),
            inside_meters.eq(
                (x >= BAR_X) & (x < BAR_X + self.OWNERSHIP_METERS * BAR_W)
                & (y >= METERS_Y) & (y < METERS_Y + METERS_H)
            ),
            meter_index.eq(bar_index),
            meter_y.eq(y - METERS_Y),
            base_value.eq(self.base.word_select(meter_index[:3], 8)),
            effective_value.eq(self.effective.word_select(meter_index[:3], 8)),
            base_filled.eq(bar_x < (base_value >> 2)),
            effective_filled.eq(bar_x < (effective_value >> 2)),
            bipolar_meter.eq(meter_index >= 6),
            bipolar_value.eq(Mux(meter_index == 6, self.pitch, self.tilt)),
            bipolar_filled.eq(Mux(
                bipolar_value >= 0,
                (bar_x >= 32) & (bar_x < 32 + (bipolar_value >> 2)),
                (bar_x < 32) & (bar_x >= 32 - ((-bipolar_value) >> 2)),
            )),
            inside_cv.eq(
                (x >= CV_X) & (x < CV_X + 4 * CV_W) & (y >= CV_Y) & (y < CV_Y + CV_H)
            ),
            cv_index.eq((x - CV_X)[7:9]),
            cv_x.eq((x - CV_X)[:7]),
            cv_value.eq(self.cv.word_select(cv_index, 8)),
            cv_filled.eq(Mux(
                cv_index == 0,
                cv_x < cv_value.as_unsigned(),
                Mux(
                    cv_value >= 0,
                    (cv_x >= 64) & (cv_x < 64 + (cv_value >> 1)),
                    (cv_x < 64) & (cv_x >= 64 - ((-cv_value) >> 1)),
                ),
            )),
            inside_master.eq(
                (x >= BAR_X) & (x < BAR_X + 512) & (y >= MASTER_Y) & (y < MASTER_Y + MASTER_H)
            ),
            master_x.eq(x - BAR_X),
            inside_util.eq(
                (x >= BAR_X) & (x < BAR_X + 512) & (y >= UTIL_Y) & (y < UTIL_Y + UTIL_H)
            ),
            inside_status.eq(
                (x >= STATUS_X) & (x < STATUS_X + 4 * 64)
                & (y >= STATUS_Y) & (y < STATUS_Y + STATUS_H)
            ),
            status_index.eq((x - STATUS_X)[6:9]),
            status_on.eq(Mux(
                status_index == 0, self.link_alive,
                Mux(status_index == 1, self.fault,
                    Mux(status_index == 2, self.overrun, self.activity)),
            )),
            checker.eq(x[5] ^ y[5] ^ self.frame[4]),
            self.r.eq(Mux(checker, 4, 2)),
            self.g.eq(Mux(checker, 6, 3)),
            self.b.eq(Mux(checker, 12, 6)),
        ]

        with m.If(inside_levels):
            with m.If((bar_x < 2) | (bar_x >= 62) | (level_y < 2)):
                m.d.comb += [self.r.eq(20), self.g.eq(32), self.b.eq(56)]
            with m.Elif(level_filled):
                m.d.comb += [
                    self.r.eq(48 + (bar_index << 4)),
                    self.g.eq(200 - (bar_index << 3)),
                    self.b.eq(255),
                ]
            with m.Else():
                m.d.comb += [self.r.eq(4), self.g.eq(7), self.b.eq(16)]
        with m.Elif(inside_meters):
            with m.If((bar_x < 2) | (bar_x >= 62) | (meter_y < 2) | (meter_y >= 38)):
                m.d.comb += [self.r.eq(36), self.g.eq(32), self.b.eq(48)]
            with m.Elif(bipolar_meter):
                with m.If(bar_x == 32):
                    m.d.comb += [self.r.eq(90), self.g.eq(90), self.b.eq(110)]
                with m.Elif(bipolar_filled):
                    m.d.comb += [self.r.eq(255), self.g.eq(150), self.b.eq(40)]
                with m.Else():
                    m.d.comb += [self.r.eq(6), self.g.eq(6), self.b.eq(12)]
            with m.Elif(effective_filled & (meter_y >= 8) & (meter_y < 32)):
                # Effective value (UI base + CV): bright.
                m.d.comb += [self.r.eq(255), self.g.eq(150), self.b.eq(40)]
            with m.Elif(base_filled):
                # UI-owned base value: dim, full height.
                m.d.comb += [self.r.eq(60), self.g.eq(70), self.b.eq(120)]
            with m.Else():
                m.d.comb += [self.r.eq(6), self.g.eq(6), self.b.eq(12)]
        with m.Elif(inside_field):
            with m.If((tone_x < 2) | (tone_x >= 126) | (cell_y < 1)):
                m.d.comb += [
                    self.r.eq(12 + (tone << 2)),
                    self.g.eq(20 + (tone << 3)),
                    self.b.eq(40 + (harmonic << 2)),
                ]
            with m.Elif(pan_hit):
                m.d.comb += [self.r.eq(200), self.g.eq(200), self.b.eq(220)]
            with m.Elif(particle_hit):
                with m.If(self.group_low):
                    m.d.comb += [
                        self.r.eq(40 + (amplitude8 >> 2)),
                        self.g.eq(80 + (amplitude8 >> 1)),
                        self.b.eq(120 + (amplitude8 >> 1)),
                    ]
                with m.Elif(self.group_air):
                    m.d.comb += [
                        self.r.eq(120 + (amplitude8 >> 1)),
                        self.g.eq(120 + (amplitude8 >> 1)),
                        self.b.eq(90 + (amplitude8 >> 1)),
                    ]
                with m.Else():
                    m.d.comb += [
                        self.r.eq(30 + (tone << 4) + (amplitude8 >> 1)),
                        self.g.eq(40 + (amplitude8 >> 1)),
                        self.b.eq(60 + (amplitude8 >> 1)),
                    ]
            with m.Else():
                m.d.comb += [self.r.eq(3), self.g.eq(4), self.b.eq(9)]
        with m.Elif(inside_cv):
            with m.If((cv_x < 2) | (cv_x >= 126) | ((y - CV_Y) < 2) | ((y - CV_Y) >= CV_H - 2)):
                m.d.comb += [self.r.eq(36), self.g.eq(44), self.b.eq(48)]
            with m.Elif((cv_index != 0) & (cv_x == 64)):
                m.d.comb += [self.r.eq(90), self.g.eq(100), self.b.eq(100)]
            with m.Elif(cv_filled):
                m.d.comb += [
                    self.r.eq(40 + (cv_index << 5)),
                    self.g.eq(220),
                    self.b.eq(160 - (cv_index << 4)),
                ]
            with m.Else():
                m.d.comb += [self.r.eq(6), self.g.eq(8), self.b.eq(10)]
        with m.Elif(inside_master):
            with m.If(master_x < (self.master_asq << 1)):
                m.d.comb += [self.r.eq(255), self.g.eq(120), self.b.eq(200)]
            with m.Else():
                m.d.comb += [self.r.eq(8), self.g.eq(6), self.b.eq(12)]
        with m.Elif(inside_util):
            with m.If(master_x < (self.utilization << 1)):
                m.d.comb += [self.r.eq(120), self.g.eq(120), self.b.eq(140)]
            with m.Else():
                m.d.comb += [self.r.eq(6), self.g.eq(6), self.b.eq(10)]
        with m.Elif(inside_status):
            with m.If(status_on):
                with m.If(status_index == 0):
                    m.d.comb += [self.r.eq(40), self.g.eq(230), self.b.eq(90)]
                with m.Elif(status_index == 1):
                    m.d.comb += [self.r.eq(255), self.g.eq(0), self.b.eq(0)]
                with m.Elif(status_index == 2):
                    m.d.comb += [self.r.eq(255), self.g.eq(140), self.b.eq(0)]
                with m.Else():
                    m.d.comb += [self.r.eq(200), self.g.eq(200), self.b.eq(255)]
            with m.Else():
                m.d.comb += [self.r.eq(14), self.g.eq(14), self.b.eq(20)]
        return m
