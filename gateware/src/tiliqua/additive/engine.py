# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Time-multiplexed 1,000-oscillator additive engine for Tiliqua R5.

One 48 kHz sample is computed in roughly 1,020 sync cycles: a two-cycle
parameter preload, one oscillator per cycle through a five-stage pipeline
(phase read, phase update and sine address, sine accumulate, group multiply,
bus accumulate), a short drain, and a serial four-channel output stage
(saturate, soft limiter, master, ceiling). Per-group parameters live in a
double-buffered block RAM written by the control engine and swapped only at
a sample boundary, so every sample sees one consistent parameter set.
"""

from amaranth import Const, Module, Mux, Signal, signed, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.memory import Memory
from amaranth.lib.wiring import In, Out

from tiliqua.dsp import ASQ

from . import tables as T

PARAM_LAYOUT = data.StructLayout({
    "midpoint": unsigned(T.PHASE_BITS),
    "spacing_half": signed(T.PHASE_BITS),
    "spread_half": unsigned(T.PHASE_BITS),
    "gain_left": unsigned(T.GAIN_BITS),
    "gain_right": unsigned(T.GAIN_BITS),
    "gain_low": unsigned(T.GAIN_BITS),
    "gain_air": unsigned(T.GAIN_BITS),
})

GROUP_ADDR_BITS = 6
PARAM_BANK_DEPTH = 2 << GROUP_ADDR_BITS
MASTER_BITS = 14
VOICE_EDGE = T.VOICE_COUNT - 1  # 19: first voice sits at -19 half-steps


def group_parameters_to_dict(group) -> dict:
    """Convert reference ``AdditiveGroupParameters`` into PARAM_LAYOUT fields."""
    return {
        "midpoint": group.midpoint_increment & T.PHASE_MASK,
        "spacing_half": group.spacing_half_increment,
        "spread_half": group.spread_half_phase & T.PHASE_MASK,
        "gain_left": group.gain_left,
        "gain_right": group.gain_right,
        "gain_low": group.gain_low,
        "gain_air": group.gain_air,
    }


class AdditiveOscillatorBank(wiring.Component):
    """1,000 phase accumulators, four normalized buses, limiter, and master.

    ``i`` carries the calibrated ``IN 0-3`` sample and starts one synthesis
    sample per transfer; ``o`` carries ``OUT 0-3``. ``master_asq`` and a
    pending parameter commit are sampled only when a sample starts.
    """

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))
    master_asq: In(unsigned(MASTER_BITS))
    param_addr: In(unsigned(GROUP_ADDR_BITS))
    param_data: In(PARAM_LAYOUT)
    param_en: In(1)
    commit: In(1)
    committed: Out(1)
    sample_start: Out(1)
    cv: Out(data.ArrayLayout(ASQ, 4))
    sample_cycles: Out(unsigned(12))
    sample_index: Out(unsigned(32))
    fault: Out(1)
    busy: Out(1)

    def __init__(self):
        # Simulation probes.
        self.bus = [Signal(signed(41), name=f"bus{c}") for c in range(4)]
        self.active_bank = Signal()
        self.group_total_probe = Signal(signed(21))
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        # ------------------------------------------------------------------
        # Memories
        phase_init = list(T.INITIAL_PHASE_TABLE) + [0] * (1024 - T.OSCILLATOR_COUNT)
        m.submodules.phase_memory = phase_memory = Memory(
            shape=unsigned(T.PHASE_BITS), depth=1024, init=phase_init,
            attrs={"ram_style": "block"},
        )
        phase_read = phase_memory.read_port(domain="sync")
        phase_write = phase_memory.write_port(domain="sync")

        m.submodules.sine_rom = sine_rom = Memory(
            shape=signed(16), depth=T.SINE_TABLE_SIZE, init=list(T.SINE_TABLE),
            attrs={"rom_style": "block"},
        )
        sine_read = sine_rom.read_port(domain="sync")

        m.submodules.limiter_rom = limiter_rom = Memory(
            shape=unsigned(16), depth=T.LIMITER_TABLE_SIZE, init=list(T.LIMITER_TABLE),
            attrs={"rom_style": "block"},
        )
        limiter_read = limiter_rom.read_port(domain="sync")

        m.submodules.param_bank = param_bank = Memory(
            shape=PARAM_LAYOUT, depth=PARAM_BANK_DEPTH, init=[],
            attrs={"ram_style": "block"},
        )
        param_read = param_bank.read_port(domain="sync")
        param_write = param_bank.write_port(domain="sync")
        active = self.active_bank
        m.d.comb += [
            param_write.addr.eq((~active << GROUP_ADDR_BITS) | self.param_addr),
            param_write.data.eq(self.param_data),
            param_write.en.eq(self.param_en),
        ]

        # ------------------------------------------------------------------
        # Sample control
        commit_pending = Signal()
        master = Signal(MASTER_BITS)
        cv_latch = Signal(data.ArrayLayout(ASQ, 4))
        cycle_counter = Signal(12)
        m.d.comb += [
            self.cv.eq(cv_latch),
            self.committed.eq(0),
            self.sample_start.eq(0),
        ]
        with m.If(self.commit):
            m.d.sync += commit_pending.eq(1)

        # ------------------------------------------------------------------
        # Oscillator scheduler (stage A: issue)
        group = Signal(range(T.GROUP_COUNT))
        voice = Signal(range(T.VOICE_COUNT))
        osc = Signal(range(1024))
        running = Signal()

        # Current / next group parameters.
        cur_base_inc = Signal(unsigned(T.PHASE_BITS))
        cur_spacing2 = Signal(signed(T.PHASE_BITS + 1))
        cur_base_spr = Signal(unsigned(T.PHASE_BITS))
        cur_spread2 = Signal(unsigned(T.PHASE_BITS + 1))
        cur_gains = Signal(data.StructLayout({
            "left": unsigned(T.GAIN_BITS), "right": unsigned(T.GAIN_BITS),
            "low": unsigned(T.GAIN_BITS), "air": unsigned(T.GAIN_BITS),
        }))
        nxt = Signal(PARAM_LAYOUT)

        def load_current(source):
            return [
                cur_base_inc.eq(
                    (source.midpoint + source.spacing_half * Const(-VOICE_EDGE, signed(6)))[:T.PHASE_BITS]
                ),
                cur_spacing2.eq(source.spacing_half << 1),
                cur_base_spr.eq(
                    (source.spread_half * Const(-VOICE_EDGE, signed(6)))[:T.PHASE_BITS]
                ),
                cur_spread2.eq(source.spread_half << 1),
                cur_gains.left.eq(source.gain_left),
                cur_gains.right.eq(source.gain_right),
                cur_gains.low.eq(source.gain_low),
                cur_gains.air.eq(source.gain_air),
            ]

        inc_reg = Signal(unsigned(T.PHASE_BITS))
        spr_reg = Signal(unsigned(T.PHASE_BITS))
        inc_now = Signal(unsigned(T.PHASE_BITS))
        spr_now = Signal(unsigned(T.PHASE_BITS))
        m.d.comb += [
            inc_now.eq(Mux(voice == 0, cur_base_inc, (inc_reg + cur_spacing2)[:T.PHASE_BITS])),
            spr_now.eq(Mux(voice == 0, cur_base_spr, (spr_reg + cur_spread2)[:T.PHASE_BITS])),
            phase_read.addr.eq(osc),
            phase_read.en.eq(1),
        ]

        # Stage B registers (phase update)
        valid_b = Signal()
        osc_b = Signal(range(1024))
        voice_b = Signal(range(T.VOICE_COUNT))
        inc_b = Signal(unsigned(T.PHASE_BITS))
        spr_b = Signal(unsigned(T.PHASE_BITS))
        gains_b = Signal.like(cur_gains)
        # Stage C registers (sine accumulate)
        valid_c = Signal()
        voice_c = Signal(range(T.VOICE_COUNT))
        gains_c = Signal.like(cur_gains)
        group_acc = Signal(signed(21))
        # Stage D registers (group multiply)
        valid_d = Signal()
        group_total = Signal(signed(21))
        gains_d = Signal.like(cur_gains)
        # Stage E registers (bus accumulate)
        valid_e = Signal()
        products = [Signal(signed(36), name=f"product{c}") for c in range(4)]

        phase_new = Signal(unsigned(T.PHASE_BITS))
        effective = Signal(unsigned(T.PHASE_BITS))
        m.d.comb += [
            phase_new.eq((phase_read.data + inc_b)[:T.PHASE_BITS]),
            effective.eq((phase_new + spr_b)[:T.PHASE_BITS]),
            phase_write.addr.eq(osc_b),
            phase_write.data.eq(phase_new),
            phase_write.en.eq(valid_b),
            sine_read.addr.eq(effective[T.SINE_SHIFT:]),
            sine_read.en.eq(1),
            self.group_total_probe.eq(group_total),
        ]

        # Pipeline advance (every cycle).
        m.d.sync += [
            valid_b.eq(running),
            osc_b.eq(osc),
            voice_b.eq(voice),
            inc_b.eq(inc_now),
            spr_b.eq(spr_now),
            gains_b.eq(cur_gains),
            valid_c.eq(valid_b),
            voice_c.eq(voice_b),
            gains_c.eq(gains_b),
            valid_d.eq(valid_c & (voice_c == VOICE_EDGE)),
            gains_d.eq(gains_c),
            valid_e.eq(valid_d),
        ]
        sum_now = Signal(signed(21))
        m.d.comb += sum_now.eq(
            Mux(voice_c == 0, Const(0, signed(21)), group_acc) + sine_read.data
        )
        with m.If(valid_c):
            m.d.sync += group_acc.eq(sum_now)
            with m.If(voice_c == VOICE_EDGE):
                m.d.sync += group_total.eq(sum_now)

        group_total18 = Signal(signed(18))
        m.d.comb += group_total18.eq(group_total >> T.GROUP_SUM_SHIFT)
        for product, gain in zip(
            products, (gains_d.left, gains_d.right, gains_d.low, gains_d.air)
        ):
            m.d.sync += product.eq(group_total18 * gain)
        with m.If(valid_e):
            for bus, product in zip(self.bus, products):
                m.d.sync += bus.eq(bus + product)

        with m.If(running):
            m.d.sync += [
                inc_reg.eq(inc_now),
                spr_reg.eq(spr_now),
                osc.eq(osc + 1),
            ]
            with m.If(voice == VOICE_EDGE):
                m.d.sync += [
                    voice.eq(0),
                    group.eq(group + 1),
                ]
                m.d.sync += load_current(nxt)
            with m.Else():
                m.d.sync += voice.eq(voice + 1)
            with m.If(voice == VOICE_EDGE - 1):
                m.d.sync += nxt.eq(param_read.data)
        m.d.comb += [
            param_read.en.eq(1),
            param_read.addr.eq((active << GROUP_ADDR_BITS) | Mux(
                running, (group + 1)[:GROUP_ADDR_BITS], Const(0, GROUP_ADDR_BITS)
            )),
        ]

        # ------------------------------------------------------------------
        # Output stage
        channel = Signal(2)
        mix = Signal(signed(16))
        magnitude = Signal(unsigned(15))
        negative = Signal()
        limited = Signal(signed(17))
        scaled = Signal(signed(32))
        out_values = [Signal(signed(16), name=f"out{c}") for c in range(4)]
        shifted = Signal(signed(41))
        shifts = T.OUTPUT_SHIFTS
        with m.Switch(channel):
            for c in range(4):
                with m.Case(c):
                    m.d.comb += shifted.eq(self.bus[c] >> shifts[c])
        mix_sat = Signal(signed(16))
        m.d.comb += mix_sat.eq(Mux(
            shifted > T.MIX_MAX, T.MIX_MAX,
            Mux(shifted < -T.MIX_MAX, -T.MIX_MAX, shifted),
        ))
        knee = T.LIMITER_KNEE_Q13
        m.d.comb += [
            limiter_read.en.eq(1),
            limiter_read.addr.eq((magnitude - knee)[T.LIMITER_INDEX_SHIFT:T.LIMITER_INDEX_SHIFT + T.LIMITER_TABLE_BITS]),
        ]
        linear = Signal(unsigned(16))
        level = Signal(unsigned(16))
        m.d.comb += [
            linear.eq(magnitude << (15 - T.MIX_FRACTION_BITS)),
            level.eq(Mux(magnitude < knee, linear, limiter_read.data)),
        ]

        for c in range(4):
            m.d.comb += self.o.payload[c].as_value().eq(out_values[c])

        with m.FSM() as fsm:
            with m.State("IDLE"):
                m.d.comb += self.i.ready.eq(1)
                with m.If(self.i.valid):
                    m.d.sync += [
                        cv_latch.eq(self.i.payload),
                        master.eq(self.master_asq),
                        cycle_counter.eq(0),
                        group.eq(0),
                        voice.eq(0),
                        osc.eq(0),
                        self.sample_index.eq(self.sample_index + 1),
                    ]
                    for bus in self.bus:
                        m.d.sync += bus.eq(0)
                    m.d.comb += self.sample_start.eq(1)
                    with m.If(commit_pending):
                        m.d.sync += [
                            active.eq(~active),
                            commit_pending.eq(0),
                        ]
                        m.d.comb += self.committed.eq(1)
                    m.next = "PRELOAD0"
            with m.State("PRELOAD0"):
                # param_read.addr = group 0 of the active bank (not running).
                m.next = "PRELOAD1"
            with m.State("PRELOAD1"):
                m.d.sync += nxt.eq(param_read.data)
                m.next = "PRELOAD2"
            with m.State("PRELOAD2"):
                m.d.sync += load_current(nxt)
                m.next = "RUN"
            with m.State("RUN"):
                m.d.comb += running.eq(1)
                with m.If(osc == T.OSCILLATOR_COUNT - 1):
                    m.next = "DRAIN0"
            # Drain: B, C, D, E stages for the last voice.
            with m.State("DRAIN0"):
                m.next = "DRAIN1"
            with m.State("DRAIN1"):
                m.next = "DRAIN2"
            with m.State("DRAIN2"):
                m.next = "DRAIN3"
            with m.State("DRAIN3"):
                m.d.sync += channel.eq(0)
                m.next = "OUT_MIX"
            with m.State("OUT_MIX"):
                m.d.sync += [
                    mix.eq(mix_sat),
                    negative.eq(mix_sat < 0),
                    magnitude.eq(Mux(mix_sat < 0, -mix_sat, mix_sat)),
                ]
                m.next = "OUT_LUT"
            with m.State("OUT_LUT"):
                # limiter_read.addr settles from magnitude; data next cycle.
                m.next = "OUT_LEVEL"
            with m.State("OUT_LEVEL"):
                m.d.sync += limited.eq(Mux(negative, -level, level))
                m.next = "OUT_SCALE"
            with m.State("OUT_SCALE"):
                m.d.sync += scaled.eq(limited * master)
                m.next = "OUT_STORE"
            with m.State("OUT_STORE"):
                value = scaled >> 15
                clamped = Mux(
                    value > T.OUTPUT_CEILING_ASQ, T.OUTPUT_CEILING_ASQ,
                    Mux(value < -T.OUTPUT_CEILING_ASQ, -T.OUTPUT_CEILING_ASQ, value),
                )
                with m.Switch(channel):
                    for c in range(4):
                        with m.Case(c):
                            m.d.sync += out_values[c].eq(clamped)
                with m.If(channel == 3):
                    m.d.sync += self.sample_cycles.eq(cycle_counter + 1)
                    m.next = "EMIT"
                with m.Else():
                    m.d.sync += channel.eq(channel + 1)
                    m.next = "OUT_MIX"
            with m.State("EMIT"):
                m.d.comb += self.o.valid.eq(1)
                with m.If(self.o.ready):
                    m.next = "IDLE"

            m.d.comb += self.busy.eq(~fsm.ongoing("IDLE"))

        with m.If(self.busy & (cycle_counter != 0xFFF)):
            m.d.sync += cycle_counter.eq(cycle_counter + 1)
        # A new input while a sample is still in flight is an overrun.
        with m.If(self.busy & ~fsm.ongoing("EMIT") & self.i.valid):
            m.d.sync += self.fault.eq(1)
        return m
