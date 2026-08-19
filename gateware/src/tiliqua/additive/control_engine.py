# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Block-rate control engine: UI frame + CV -> committed oscillator parameters.

This is the RTL counterpart of ``AdditiveReference.step_block``. At every
control-block start it latches the current host frame and the previous
block's CV averages, then runs a microprogram over bit-serial arithmetic:
CV normalization and smoothing, parameter/master smoothing, evolution LFOs,
fifty per-group targets (morph, drift, pitch, detune, spread, weight motion,
tilt, pan), RMS normalization with integer square roots and divisions, and a
second pass writing the four bus gains per group into the oscillator bank's
inactive parameter buffer. It finishes well inside one block and pulses
``commit`` so the bank swaps at the next block boundary.
"""

from amaranth import Array, Cat, Const, Module, Mux, Signal, signed, unsigned
from amaranth.lib import data, wiring
from amaranth.lib.memory import Memory
from amaranth.lib.wiring import In, Out

from tiliqua.dsp import ASQ

from . import tables as T
from .arith import SerialDivider, SerialMultiplier, SerialSqrt
from .control import PAYLOAD_LAYOUT
from .engine import GROUP_ADDR_BITS, MASTER_BITS, PARAM_LAYOUT

MUL_WIDTH = 40
POWER_BITS = 38
Q8_BITS = 24

STAGING_LAYOUT = data.StructLayout({
    "amplitude": unsigned(16),
    "left_weight": unsigned(16),
    "right_weight": unsigned(16),
    "low": unsigned(1),
    "air": unsigned(1),
    "midpoint": unsigned(T.PHASE_BITS),
    "spacing_half": signed(T.PHASE_BITS),
    "spread_half": unsigned(T.PHASE_BITS),
})

GROUP_DISPLAY_LAYOUT = data.StructLayout({
    "amplitude": unsigned(16),
    "low": unsigned(1),
    "air": unsigned(1),
    "pan": signed(8),
    "reserved": unsigned(6),
})

EFFECTIVE_LAYOUT = data.StructLayout({
    "master": unsigned(15),
    "sub_focus": unsigned(15),
    "pitch_octaves_q10": signed(12),
    "tilt_cv": signed(16),
    "phase_spread": unsigned(15),
    "evolution": unsigned(15),
    "stereo_width": unsigned(15),
    "detune_millicents": unsigned(15),
    "master_asq": unsigned(MASTER_BITS),
})


def _tone_interval_table():
    table = []
    for harmony in range(T.HARMONY_COUNT):
        for tone in range(T.TONE_COUNT):
            table.append(T.HARMONY_INTERVALS[harmony][tone])
    return table


class AdditiveControlEngine(wiring.Component):
    """Compute and commit one parameter set per control block."""

    frame: In(PAYLOAD_LAYOUT)
    cv: In(data.ArrayLayout(ASQ, 4))
    sample_start: In(1)
    block_start: In(1)
    param_addr: Out(unsigned(GROUP_ADDR_BITS))
    param_data: Out(PARAM_LAYOUT)
    param_en: Out(1)
    commit: Out(1)
    master_asq: Out(unsigned(MASTER_BITS))
    effective: Out(EFFECTIVE_LAYOUT)
    cv_smoothed: Out(data.ArrayLayout(signed(16), 4))
    busy: Out(1)
    overrun: Out(1)
    blocks_done: Out(unsigned(16))
    block_cycles: Out(unsigned(18))
    # Display write port: per-group amplitude, stem flags, and pan.
    group_display_addr: Out(unsigned(GROUP_ADDR_BITS))
    group_display_data: Out(GROUP_DISPLAY_LAYOUT)
    group_display_en: Out(1)

    def __init__(self):
        self.amplitude_probe = Signal(16)
        # Simulation probes (block-rate internals).
        self.probe = {
            name: Signal(unsigned(POWER_BITS), name=f"probe_{name}")
            for name in ("power_left", "power_right", "power_low", "power_air",
                         "norm_master", "norm_low", "norm_air", "sub_gain")
        }
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        m.submodules.mul = mul = SerialMultiplier(MUL_WIDTH)
        m.submodules.div = div = SerialDivider(32)
        m.submodules.sqrt = sqrt = SerialSqrt(POWER_BITS)

        # ------------------------------------------------------------------
        # Tables
        m.submodules.sine_rom = sine_rom = Memory(
            shape=signed(16), depth=T.SINE_TABLE_SIZE, init=list(T.SINE_TABLE),
            attrs={"rom_style": "block"},
        )
        sine_rd = sine_rom.read_port(domain="sync")
        m.submodules.exp2_rom = exp2_rom = Memory(
            shape=unsigned(16), depth=T.EXP2_FRACTION_SIZE, init=list(T.EXP2_FRACTION_TABLE),
            attrs={"rom_style": "block"},
        )
        exp2_rd = exp2_rom.read_port(domain="sync")
        note_table = Array(Const(v, 32) for v in T.NOTE_INCREMENT_TABLE)
        morph_table = Array(Const(v, 17) for v in T.MORPH_COEFFICIENT_TABLE_Q24)
        pan_table = Array(Const(v, 16) for v in T.PAN_GAIN_TABLE)
        sub_main_table = Array(Const(v[0], 16) for v in T.SUB_MIX_TABLE)
        sub_scale_table = Array(Const(v[1], 16) for v in T.SUB_MIX_TABLE)
        interval_table = Array(Const(v, 5) for v in _tone_interval_table())
        tone_weight_table = Array(Const(v, 16) for v in T.TONE_WEIGHTS_Q15)
        tone_pan_table = Array(Const(v, signed(16)) for v in T.TONE_POSITION_PAN_Q15)
        variation_table = Array(Const(v, 16) for v in T.PHASE_VARIATION_Q15)
        harmonic_pan_table = Array(Const(v, signed(16)) for v in T.HARMONIC_PAN_Q15)
        tilt_step_table = Array(Const(v, 12) for v in T.TILT_STEP_Q10)

        # Per-group state memories.
        m.submodules.base_mem = base_mem = Memory(
            shape=unsigned(T.PHASE_BITS), depth=64, init=[], attrs={"ram_style": "block"},
        )
        base_rd = base_mem.read_port(domain="sync")
        base_wr = base_mem.write_port(domain="sync")
        m.submodules.weight_mem = weight_mem = Memory(
            shape=unsigned(Q8_BITS), depth=64, init=[], attrs={"ram_style": "block"},
        )
        weight_rd = weight_mem.read_port(domain="sync")
        weight_wr = weight_mem.write_port(domain="sync")
        m.submodules.staging_mem = staging_mem = Memory(
            shape=STAGING_LAYOUT, depth=64, init=[], attrs={"ram_style": "block"},
        )
        staging_rd = staging_mem.read_port(domain="sync")
        staging_wr = staging_mem.write_port(domain="sync")

        # ------------------------------------------------------------------
        # Registers
        st = Signal(PAYLOAD_LAYOUT)          # latched frame
        cv_acc = [Signal(signed(24), name=f"cv_acc{i}") for i in range(4)]
        cv_hold = [Signal(signed(24), name=f"cv_hold{i}") for i in range(4)]  # block b-2 sum
        cv_avg = [Signal(signed(17), name=f"cv_avg{i}") for i in range(4)]
        cv_s = [Signal(signed(16), name=f"cv_s{i}") for i in range(4)]
        initialized = Signal()

        detune_q8 = Signal(unsigned(Q8_BITS))
        ps_q8 = Signal(unsigned(Q8_BITS))
        evo_q8 = Signal(unsigned(Q8_BITS))
        width_q8 = Signal(unsigned(Q8_BITS))
        sub_q8 = Signal(unsigned(Q8_BITS))
        master_q8 = Signal(unsigned(Q8_BITS))

        eff = Signal(EFFECTIVE_LAYOUT)
        pitch_ratio = Signal(unsigned(20))
        sub_main = Signal(unsigned(16))
        sub_scale = Signal(unsigned(16))
        morph = Signal(unsigned(17))
        timbre_lfo = Signal(unsigned(T.PHASE_BITS))
        pitch_lfo = Signal(unsigned(T.PHASE_BITS))
        stereo_lfo = Signal(unsigned(T.PHASE_BITS))

        g = Signal(unsigned(GROUP_ADDR_BITS))
        tone = Signal(unsigned(3))
        harmonic = Signal(unsigned(4))
        offset_g = Signal(unsigned(T.PHASE_BITS))
        toffset_g = Signal(unsigned(T.PHASE_BITS))
        note = Signal(unsigned(8))
        target_base = Signal(unsigned(36))
        base = Signal(unsigned(T.PHASE_BITS))
        low = Signal()
        air = Signal()
        drift = Signal(signed(17))
        drift_term = Signal(signed(33))
        midpoint = Signal(signed(36))
        spacing_tmp = Signal(signed(36))
        spacing = Signal(signed(T.PHASE_BITS))
        spread_tmp = Signal(unsigned(18))
        spread = Signal(unsigned(T.PHASE_BITS))
        motion = Signal(signed(17))
        weight_motion = Signal(unsigned(16))
        target_weight = Signal(unsigned(16))
        weight_q8 = Signal(unsigned(Q8_BITS))
        tilt_x = Signal(signed(13))
        exp2_int = Signal(signed(4))
        exp2_value = Signal(unsigned(20))
        amplitude = Signal(unsigned(16))
        pan_motion = Signal(signed(17))
        pan = Signal(signed(16))
        left_weight = Signal(unsigned(16))
        right_weight = Signal(unsigned(16))
        power_left = Signal(unsigned(POWER_BITS))
        power_right = Signal(unsigned(POWER_BITS))
        power_low = Signal(unsigned(POWER_BITS))
        power_air = Signal(unsigned(POWER_BITS))
        norm_master = Signal(unsigned(32))
        norm_low = Signal(unsigned(32))
        norm_air = Signal(unsigned(32))
        sub_gain = Signal(unsigned(T.GAIN_BITS))
        tmp = Signal(signed(48))
        tmp2 = Signal(signed(48))
        gain_left = Signal(unsigned(T.GAIN_BITS + 1))
        gain_right = Signal(unsigned(T.GAIN_BITS + 1))
        gain_low = Signal(unsigned(T.GAIN_BITS + 1))
        gain_air = Signal(unsigned(T.GAIN_BITS + 1))
        cycle = Signal(unsigned(18))
        start_pending = Signal()

        m.d.comb += [
            self.effective.eq(eff),
            self.amplitude_probe.eq(amplitude),
            self.probe["power_left"].eq(power_left),
            self.probe["power_right"].eq(power_right),
            self.probe["power_low"].eq(power_low),
            self.probe["power_air"].eq(power_air),
            self.probe["norm_master"].eq(norm_master),
            self.probe["norm_low"].eq(norm_low),
            self.probe["norm_air"].eq(norm_air),
            self.probe["sub_gain"].eq(sub_gain),
            base_rd.addr.eq(g),
            weight_rd.addr.eq(g),
            staging_rd.addr.eq(g),
        ]
        for i in range(4):
            m.d.comb += self.cv_smoothed[i].eq(cv_s[i])

        # CV accumulation runs continuously on every sample start.
        for i in range(4):
            with m.If(self.sample_start):
                with m.If(self.block_start):
                    m.d.sync += [
                        cv_hold[i].eq(cv_acc[i]),
                        cv_acc[i].eq(self.cv[i].as_value().as_signed()),
                    ]
                with m.Else():
                    m.d.sync += cv_acc[i].eq(cv_acc[i] + self.cv[i].as_value().as_signed())

        # ------------------------------------------------------------------
        # Microprogram sequencer
        steps = []
        labels = {}

        def label(name):
            labels[name] = len(steps)

        def do(*stmts):
            def build(nxt):
                m.d.sync += list(stmts)
                m.next = nxt
            steps.append(build)

        def comb_then(comb_stmts, sync_stmts=()):
            def build(nxt):
                m.d.comb += list(comb_stmts)
                m.d.sync += list(sync_stmts)
                m.next = nxt
            steps.append(build)

        def multiply(a, b, store):
            def issue(nxt):
                m.d.comb += [mul.a.eq(a), mul.b.eq(b), mul.start.eq(1)]
                m.next = nxt
            def wait(nxt):
                with m.If(mul.done):
                    m.d.sync += list(store(mul.product))
                    m.next = nxt
            steps.append(issue)
            steps.append(wait)

        def divide(dividend, divisor, store):
            def issue(nxt):
                m.d.comb += [div.dividend.eq(dividend), div.divisor.eq(divisor), div.start.eq(1)]
                m.next = nxt
            def wait(nxt):
                with m.If(div.done):
                    m.d.sync += list(store(div.quotient))
                    m.next = nxt
            steps.append(issue)
            steps.append(wait)

        def square_root(radicand, store):
            def issue(nxt):
                m.d.comb += [sqrt.radicand.eq(radicand), sqrt.start.eq(1)]
                m.next = nxt
            def wait(nxt):
                with m.If(sqrt.done):
                    m.d.sync += list(store(sqrt.root))
                    m.next = nxt
            steps.append(issue)
            steps.append(wait)

        def branch(condition, target):
            def build(nxt):
                with m.If(condition):
                    m.next = labels_resolved(target)
                with m.Else():
                    m.next = nxt
            steps.append(build)

        def jump(target):
            def build(nxt):
                m.next = labels_resolved(target)
            steps.append(build)

        def labels_resolved(name):
            return f"S{labels[name]}"

        def clamp_q15(value):
            return Mux(value < 0, 0, Mux(value > T.Q15_ONE, T.Q15_ONE, value))

        def clamp_q15_signed(value):
            return Mux(value < -T.Q15_ONE, -T.Q15_ONE, Mux(value > T.Q15_ONE, T.Q15_ONE, value))

        def smooth_store(current, target, coefficient):
            """Schedule cur += ((target<<8) - cur) * coeff >> 24 (floor)."""
            delta = (target << T.SMOOTHED_FRACTION_BITS).as_signed() - current.as_signed()
            multiply(delta, coefficient, lambda p: [
                current.eq((current.as_signed() + (p >> T.SMOOTHING_SHIFT))[:Q8_BITS])
            ])

        # ---------------- block prologue ----------------
        label("start")
        # CV averages of block b-2 (cv_hold) -> normalize -> smooth.
        do(*[cv_avg[i].eq(cv_hold[i] >> 7) for i in range(4)])
        for i in range(4):
            if i == 0:
                multiply(cv_avg[i], Const(T.CV_SCALE_Q10, 15), lambda p, i=i: [
                    tmp.eq(p >> 11),
                ])
                do(tmp2.eq(Mux(tmp < 0, 0, Mux(tmp > T.Q15_ONE, T.Q15_ONE, tmp))))
            else:
                multiply(cv_avg[i], Const(T.CV_SCALE_Q10, 15), lambda p, i=i: [
                    tmp.eq(p >> 10),
                ])
                do(tmp2.eq(clamp_q15_signed(tmp)))
            do(cv_s[i].eq(Mux(
                initialized,
                cv_s[i] + ((tmp2[:17].as_signed() - cv_s[i]) >> T.CV_SMOOTHING_SHIFT),
                tmp2,
            )))

        # Parameter smoothing.
        label("smooth")
        branch(initialized, "smooth_run")
        do(
            detune_q8.eq(st.detune_millicents << 8),
            ps_q8.eq(st.phase_spread << 8),
            evo_q8.eq(st.evolution_amount << 8),
            width_q8.eq(st.stereo_width << 8),
            sub_q8.eq(st.sub_focus << 8),
            master_q8.eq(Mux(st.flags.output_enabled, st.master, 0) << 8),
        )
        jump("effective")
        label("smooth_run")
        smooth_store(detune_q8, st.detune_millicents, Const(T.PARAMETER_SMOOTHING_Q24, 18))
        smooth_store(ps_q8, st.phase_spread, Const(T.PARAMETER_SMOOTHING_Q24, 18))
        smooth_store(evo_q8, st.evolution_amount, Const(T.PARAMETER_SMOOTHING_Q24, 18))
        smooth_store(width_q8, st.stereo_width, Const(T.PARAMETER_SMOOTHING_Q24, 18))
        smooth_store(sub_q8, st.sub_focus, Const(T.PARAMETER_SMOOTHING_Q24, 18))
        branch(~st.flags.output_enabled, "mute_ramp")
        smooth_store(master_q8, st.master, Const(T.MASTER_SMOOTHING_Q24, 18))
        jump("effective")
        label("mute_ramp")
        do(master_q8.eq(Mux(master_q8 > T.MUTE_RAMP_Q8, master_q8 - T.MUTE_RAMP_Q8, 0)))

        # Effective values.
        label("effective")
        offset0 = cv_s[0] >> 1
        offset3 = cv_s[3] >> 1
        do(
            eff.master.eq(Mux(
                st.flags.output_enabled,
                clamp_q15((master_q8 >> 8).as_signed() + offset0),
                master_q8 >> 8,
            )),
            eff.sub_focus.eq(clamp_q15((sub_q8 >> 8).as_signed() + offset0)),
            eff.tilt_cv.eq(cv_s[2]),
            eff.phase_spread.eq(clamp_q15((ps_q8 >> 8).as_signed() + offset3)),
            eff.evolution.eq(clamp_q15((evo_q8 >> 8).as_signed() + offset3)),
            eff.stereo_width.eq(clamp_q15((width_q8 >> 8).as_signed() + offset3)),
            eff.detune_millicents.eq(detune_q8 >> 8),
            morph.eq(morph_table[st.morph_seconds[:7]]),
        )
        multiply(cv_s[1], Const(T.PITCH_OFFSET_RANGE_Q10, 9), lambda p: [
            eff.pitch_octaves_q10.eq(p >> 15),
        ])
        multiply(eff.master, Const(T.OUTPUT_CEILING_ASQ, 14), lambda p: [
            eff.master_asq.eq(p >> 15),
        ])
        # exp2 for the pitch ratio: bounded exponent, fraction lookup, shift.
        exp2_x = Signal(signed(13))
        do(exp2_x.eq(eff.pitch_octaves_q10))
        label("exp2_pitch")

        def exp2_sequence(store):
            bounded = Signal(signed(13))
            do(bounded.eq(Mux(
                exp2_x < -4096, -4096, Mux(exp2_x > 4095, 4095, exp2_x)
            )))
            comb_then([exp2_rd.addr.eq(bounded[:10])], [exp2_int.eq(bounded >> 10)])
            base_value = (Const(1 << 16, 17) + exp2_rd.data)
            do(exp2_value.eq(Mux(
                exp2_int >= 0,
                base_value << exp2_int[:2],
                base_value >> (-exp2_int)[:3],
            )))
            do(*store(exp2_value))

        exp2_sequence(lambda v: [pitch_ratio.eq(v)])
        do(
            sub_main.eq(sub_main_table[eff.sub_focus >> 7]),
            sub_scale.eq(sub_scale_table[eff.sub_focus >> 7]),
        )
        # LFO advance.
        multiply(st.evolution_rate_mhz, Const(T.LFO_INCREMENT_PER_MHZ, 15), lambda p: [
            timbre_lfo.eq((timbre_lfo + p)[:T.PHASE_BITS]),
        ])
        multiply(st.evolution_rate_mhz, Const(T.LFO_PITCH_INCREMENT_PER_MHZ, 15), lambda p: [
            pitch_lfo.eq((pitch_lfo + p)[:T.PHASE_BITS]),
        ])
        multiply(st.evolution_rate_mhz, Const(T.LFO_STEREO_INCREMENT_PER_MHZ, 15), lambda p: [
            stereo_lfo.eq((stereo_lfo + p)[:T.PHASE_BITS]),
        ])
        do(
            g.eq(0), tone.eq(0), harmonic.eq(0), offset_g.eq(0), toffset_g.eq(0),
            power_left.eq(0), power_right.eq(0), power_low.eq(0), power_air.eq(0),
        )

        # ---------------- pass 1: per-group targets ----------------
        label("pass1")
        do(note.eq(
            st.root_midi
            + interval_table[st.harmony[:3] * T.TONE_COUNT + tone]
            - Mux((tone == 0) & st.flags.sub_octave, 12, 0)
        ))
        do()  # base/weight read ports follow ``g``; one cycle of read latency
        multiply(note_table[note[:7]], harmonic + 1, lambda p: [target_base.eq(p)])
        branch(initialized, "morph")
        do(base.eq(target_base))
        jump("base_ready")
        label("morph")
        multiply(target_base.as_signed() - base_rd.data.as_signed(), morph, lambda p: [
            base.eq((base_rd.data.as_signed() + (p >> T.SMOOTHING_SHIFT))[:T.PHASE_BITS]),
        ])
        label("base_ready")
        comb_then(
            [base_wr.addr.eq(g), base_wr.data.eq(base), base_wr.en.eq(1),
             sine_rd.addr.eq((pitch_lfo + offset_g)[T.SINE_SHIFT:T.PHASE_BITS])],
            [low.eq(base <= T.LOW_STEM_INCREMENT), air.eq(base >= T.AIR_STEM_INCREMENT)],
        )
        multiply(sine_rd.data, eff.evolution, lambda p: [drift.eq(p >> 15)])
        multiply(base, drift, lambda p: [drift_term.eq(p >> 15)])
        multiply(drift_term, Const(T.DRIFT_RATIO_Q16, 8), lambda p: [
            midpoint.eq(base + (p >> 16)),
        ])
        multiply(midpoint, pitch_ratio, lambda p: [midpoint.eq(p >> 16)])
        multiply(midpoint, eff.detune_millicents, lambda p: [spacing_tmp.eq(p >> 15)])
        multiply(spacing_tmp, Const(T.DETUNE_SPACING_Q40, 17), lambda p: [
            spacing.eq(p >> (T.DETUNE_SPACING_SHIFT - 15)),
        ])
        multiply(eff.phase_spread, variation_table[g], lambda p: [spread_tmp.eq(p >> 15)])
        multiply(spread_tmp, Const(T.SPREAD_UNIT, 28), lambda p: [spread.eq(p >> 15)])
        comb_then([sine_rd.addr.eq((timbre_lfo + toffset_g)[T.SINE_SHIFT:T.PHASE_BITS])])
        multiply(sine_rd.data, Const(T.WEIGHT_MOTION_DEPTH_Q15, 16), lambda p: [tmp.eq(p >> 15)])
        multiply(tmp, eff.evolution, lambda p: [motion.eq(p >> 15)])
        do(weight_motion.eq(Mux(
            (Const(T.WEIGHT_MOTION_BASE_Q15, 17).as_signed() + motion) < T.WEIGHT_MOTION_FLOOR_Q15,
            T.WEIGHT_MOTION_FLOOR_Q15,
            Const(T.WEIGHT_MOTION_BASE_Q15, 17).as_signed() + motion,
        )))
        multiply(st.harmonic_levels[harmonic], tone_weight_table[tone], lambda p: [tmp.eq(p >> 15)])
        multiply(tmp, weight_motion, lambda p: [target_weight.eq(p >> 15)])
        branch(initialized, "weight_smooth")
        do(weight_q8.eq(target_weight << 8))
        jump("weight_ready")
        label("weight_smooth")
        multiply(
            (target_weight << 8).as_signed() - weight_rd.data.as_signed(),
            Const(T.PARAMETER_SMOOTHING_Q24, 18),
            lambda p: [weight_q8.eq((weight_rd.data.as_signed() + (p >> T.SMOOTHING_SHIFT))[:Q8_BITS])],
        )
        label("weight_ready")
        comb_then([weight_wr.addr.eq(g), weight_wr.data.eq(weight_q8), weight_wr.en.eq(1)])
        multiply(eff.tilt_cv, tilt_step_table[harmonic], lambda p: [tilt_x.eq(p >> 15)])
        do(exp2_x.eq(tilt_x))
        exp2_sequence(lambda v: [tmp.eq(v)])
        multiply(weight_q8 >> 8, tmp, lambda p: [amplitude.eq(clamp_q15(p >> 16))])
        # Pan.
        branch(low, "pan_zero")
        comb_then([sine_rd.addr.eq((stereo_lfo + offset_g)[T.SINE_SHIFT:T.PHASE_BITS])])
        multiply(sine_rd.data, Const(T.PAN_MOTION_DEPTH_Q15, 16), lambda p: [tmp.eq(p >> 15)])
        multiply(tmp, eff.evolution, lambda p: [pan_motion.eq(p >> 15)])
        multiply(
            tone_pan_table[tone] + harmonic_pan_table[g] + pan_motion,
            eff.stereo_width,
            lambda p: [pan.eq(clamp_q15_signed(p >> 15))],
        )
        jump("pan_ready")
        label("pan_zero")
        do(pan.eq(0))
        label("pan_ready")
        multiply(amplitude, pan_table[(Const(T.Q15_ONE, 17) - pan)[8:16]], lambda p: [
            left_weight.eq(p >> 15),
        ])
        multiply(amplitude, pan_table[(Const(T.Q15_ONE, 17) + pan)[8:16]], lambda p: [
            right_weight.eq(p >> 15),
        ])
        multiply(left_weight, left_weight, lambda p: [power_left.eq(power_left + p)])
        multiply(right_weight, right_weight, lambda p: [power_right.eq(power_right + p)])
        multiply(amplitude, amplitude, lambda p: [
            power_low.eq(Mux(low, power_low + p, power_low)),
            power_air.eq(Mux(air, power_air + p, power_air)),
        ])
        comb_then([
            self.group_display_addr.eq(g),
            self.group_display_data.amplitude.eq(amplitude),
            self.group_display_data.low.eq(low),
            self.group_display_data.air.eq(air),
            self.group_display_data.pan.eq(pan >> 8),
            self.group_display_en.eq(1),
        ])
        comb_then([
            staging_wr.addr.eq(g),
            staging_wr.data.amplitude.eq(amplitude),
            staging_wr.data.left_weight.eq(left_weight),
            staging_wr.data.right_weight.eq(right_weight),
            staging_wr.data.low.eq(low),
            staging_wr.data.air.eq(air),
            staging_wr.data.midpoint.eq(midpoint[:T.PHASE_BITS]),
            staging_wr.data.spacing_half.eq(spacing),
            staging_wr.data.spread_half.eq(spread),
            staging_wr.en.eq(1),
        ])
        # Next group.
        do(
            g.eq(g + 1),
            offset_g.eq((offset_g + T.GROUP_OFFSET_STEP)[:T.PHASE_BITS]),
            toffset_g.eq((toffset_g + T.TIMBRE_OFFSET_STEP)[:T.PHASE_BITS]),
            harmonic.eq(Mux(harmonic == T.HARMONIC_COUNT - 1, 0, harmonic + 1)),
            tone.eq(Mux(harmonic == T.HARMONIC_COUNT - 1, tone + 1, tone)),
        )
        branch(g != T.GROUP_COUNT, "pass1")

        # ---------------- normalization ----------------
        def normalization(power, store):
            zero_label = f"norm_zero_{len(steps)}"
            done_label = f"norm_done_{len(steps)}"
            branch(power == 0, zero_label)
            square_root(power, lambda r: [tmp.eq(Mux(r < T.NORMALIZATION_FLOOR, T.NORMALIZATION_FLOOR, r))])
            divide(Const(T.GAIN_UNIT << T.NORMALIZATION_SHIFT, 32), tmp[:32], lambda q: [store.eq(q)])
            jump(done_label)
            label(zero_label)
            do(store.eq(0))
            label(done_label)

        power_master = Signal(unsigned(POWER_BITS))
        do(power_master.eq(Mux(power_left > power_right, power_left, power_right)))
        normalization(power_master, norm_master)
        normalization(power_low, norm_low)
        normalization(power_air, norm_air)
        multiply(Const(T.GAIN_UNIT, 17), sub_scale, lambda p: [sub_gain.eq(p >> 15)])
        do(g.eq(0))

        # ---------------- pass 2: bus gains ----------------
        label("pass2")
        do()  # staging read port follows ``g``
        do(
            amplitude.eq(staging_rd.data.amplitude),
            left_weight.eq(staging_rd.data.left_weight),
            right_weight.eq(staging_rd.data.right_weight),
            low.eq(staging_rd.data.low),
            air.eq(staging_rd.data.air),
            midpoint.eq(staging_rd.data.midpoint),
            spacing.eq(staging_rd.data.spacing_half),
            spread.eq(staging_rd.data.spread_half),
        )

        def scaled(weight, norm, store):
            multiply(weight, norm, lambda p: [tmp.eq(p >> T.NORMALIZATION_SHIFT)])
            multiply(tmp, sub_main, lambda p: [store.eq(p >> 15)])

        scaled(left_weight, norm_master, gain_left)
        scaled(right_weight, norm_master, gain_right)
        scaled(amplitude, norm_low, gain_low)
        multiply(amplitude, norm_air, lambda p: [gain_air.eq(p >> T.NORMALIZATION_SHIFT)])
        do(
            gain_low.eq(Mux(low, gain_low, 0)),
            gain_air.eq(Mux(air, gain_air, 0)),
        )
        do(
            gain_left.eq(Mux(g == 0, gain_left + sub_gain, gain_left)),
            gain_right.eq(Mux(g == 0, gain_right + sub_gain, gain_right)),
            gain_low.eq(Mux(g == 0, gain_low + sub_gain, gain_low)),
        )
        comb_then([
            self.param_addr.eq(g),
            self.param_data.midpoint.eq(midpoint[:T.PHASE_BITS]),
            self.param_data.spacing_half.eq(spacing),
            self.param_data.spread_half.eq(spread),
            self.param_data.gain_left.eq(gain_left),
            self.param_data.gain_right.eq(gain_right),
            self.param_data.gain_low.eq(gain_low),
            self.param_data.gain_air.eq(gain_air),
            self.param_en.eq(1),
        ])
        do(g.eq(g + 1))
        branch(g != T.GROUP_COUNT, "pass2")

        # ---------------- commit ----------------
        comb_then([self.commit.eq(1)], [
            self.master_asq.eq(eff.master_asq),
            initialized.eq(1),
            self.blocks_done.eq(self.blocks_done + 1),
            self.block_cycles.eq(cycle),
        ])
        label("end")

        # ---------------- build the FSM ----------------
        with m.FSM() as fsm:
            with m.State("IDLE"):
                with m.If(self.block_start | start_pending):
                    m.d.sync += [
                        st.eq(self.frame),
                        cycle.eq(0),
                        start_pending.eq(0),
                    ]
                    m.next = "S0"
            for index, build in enumerate(steps):
                with m.State(f"S{index}"):
                    nxt = f"S{index + 1}" if index + 1 < len(steps) else "IDLE"
                    build(nxt)
            m.d.comb += self.busy.eq(~fsm.ongoing("IDLE"))

        with m.If(self.busy):
            m.d.sync += cycle.eq(cycle + 1)
            with m.If(self.block_start):
                # A block started before the previous computation finished.
                m.d.sync += [self.overrun.eq(1), start_pending.eq(1)]
        return m
