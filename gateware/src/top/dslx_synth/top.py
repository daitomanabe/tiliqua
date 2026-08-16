# Copyright (c) 2026 Tiliqua contributors
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""One-voice CV/gate DSLX synth with diagnostic DVI output."""

from amaranth import ClockSignal, Elaboratable, Module, ResetSignal, Signal
from amaranth.lib import data, wiring
from amaranth.lib.cdc import FFSynchronizer

from tiliqua.build import sim
from tiliqua.build.cli import top_level_cli
from tiliqua.build.types import BitstreamHelp
from tiliqua.dsp import ASQ
from tiliqua.dsp.stream_util import SyncFIFOBuffered
from tiliqua.dsp.synth import (
    CVSynthCore,
    SynthControlTestSource,
    Waveform,
)
from tiliqua.periph import eurorack_pmod
from tiliqua.platform import RebootProvider
from tiliqua.video import dvi
from tiliqua.video.dslx_visualizer import DSLXVisualizer


class DSLXSynthTop(Elaboratable):
    """Route calibrated pitch CV and gate inputs through one DSLX voice."""

    bitstream_help = BitstreamHelp(
        brief="DSLX 1V/oct CV and gate synth",
        io_left=[
            "1V/oct pitch", "gate", "unused", "unused",
            "voice", "ADSR envelope", "5V gate", "voice magnitude",
        ],
        io_right=["", "", "synth video", "", "", ""],
    )

    def __init__(
        self,
        *,
        clock_settings,
        self_test=False,
        base_note=48,
        pitch_zero_counts=0,
        counts_per_octave=4_000,
        waveform="triangle",
        drive=0,
        gate_on_volts=4.0,
        gate_off_volts=2.0,
    ):
        if clock_settings.modeline is None:
            raise ValueError("dslx_synth requires a fixed video mode")

        waveform_value = {
            "saw": Waveform.SAW,
            "triangle": Waveform.TRIANGLE,
            "square": Waveform.SQUARE,
            "silence": Waveform.SILENCE,
        }[waveform]

        self.clock_settings = clock_settings
        self.self_test = self_test
        self.pitch_zero_counts = pitch_zero_counts
        self.counts_per_octave = counts_per_octave
        self.pmod0 = eurorack_pmod.EurorackPmod(clock_settings.audio_clock)
        self.core = CVSynthCore(
            sample_rate=clock_settings.audio_clock.fs(),
            base_note=base_note,
            zero_cv_counts=pitch_zero_counts,
            counts_per_octave=counts_per_octave,
            waveform=waveform_value,
            drive=drive,
            gate_on_volts=gate_on_volts,
            gate_off_volts=gate_off_volts,
        )
        self.dvi_tgen = dvi.DVITimingGen()
        self.visualizer = DSLXVisualizer()

        self.video_r = Signal(8)
        self.video_g = Signal(8)
        self.video_b = Signal(8)
        self.self_test_active = Signal(init=int(self_test))
        self.test_phase = Signal(32)
        self.test_sample_index = Signal(32)
        self.test_note = Signal(7)
        self.sim_regression_name = "DSLX-SYNTH"
        self.sim_metrics_filename = "dslx-synth-metrics.json"

        if self_test:
            self.bitstream_help = BitstreamHelp(
                brief="DSLX playable synth self-test",
                io_left=[
                    "unused", "unused", "unused", "unused",
                    "C3/C4 voice", "ADSR envelope", "5V gate", "magnitude",
                ],
                io_right=["", "", "self-test video", "", "", ""],
            )

        super().__init__()

    def elaborate(self, platform):
        m = Module()

        if sim.is_hw(platform):
            m.submodules.car = platform.clock_domain_generator(self.clock_settings)
            m.submodules.reboot = reboot = RebootProvider(
                self.clock_settings.frequencies.sync
            )
            m.submodules.btn = FFSynchronizer(
                platform.request("encoder").s.i, reboot.button
            )
            m.submodules.pmod0_provider = pmod0_provider = (
                eurorack_pmod.FFCProvider()
            )
            wiring.connect(m, self.pmod0.pins, pmod0_provider.pins)
            m.d.comb += self.pmod0.codec_mute.eq(reboot.mute)
        else:
            m.submodules.car = sim.FakeTiliquaDomainGenerator()

        m.submodules.pmod0 = pmod0 = self.pmod0
        m.submodules.core = core = self.core
        m.submodules.dvi_tgen = dvi_tgen = self.dvi_tgen
        m.submodules.visualizer = visualizer = self.visualizer

        m.d.comb += [
            self.self_test_active.eq(self.self_test),
            self.test_phase.eq(core.oscillator_phase),
            self.test_note.eq(core.note),
        ]

        if self.self_test:
            m.submodules.test_source = test_source = SynthControlTestSource(
                zero_cv_counts=self.pitch_zero_counts,
                counts_per_octave=self.counts_per_octave,
            )
            m.submodules.test_source_buffer = test_source_buffer = (
                SyncFIFOBuffered(shape=data.ArrayLayout(ASQ, 4), depth=2)
            )
            wiring.connect(m, test_source.o, test_source_buffer.i)
            wiring.connect(m, test_source_buffer.o, core.i)
            m.d.comb += self.test_sample_index.eq(test_source.sample_index)
        else:
            wiring.connect(m, pmod0.o_cal, core.i)
        wiring.connect(m, core.o, pmod0.i_cal)

        processed_audio = Signal(data.ArrayLayout(ASQ, 4))
        with m.If(core.o.valid & core.o.ready):
            m.d.sync += processed_audio.eq(core.o.payload)

        video_envelope = Signal(16)
        video_gate_sample = Signal(16)
        video_magnitude = Signal(16)
        m.submodules.envelope_cdc = FFSynchronizer(
            processed_audio[1].as_value(), video_envelope, o_domain="dvi"
        )
        m.submodules.gate_cdc = FFSynchronizer(
            processed_audio[2].as_value(), video_gate_sample, o_domain="dvi"
        )
        m.submodules.magnitude_cdc = FFSynchronizer(
            processed_audio[3].as_value(), video_magnitude, o_domain="dvi"
        )

        for member in dvi_tgen.timings.signature.members:
            m.d.comb += getattr(dvi_tgen.timings, member).eq(
                getattr(self.clock_settings.modeline, member)
            )

        frame = Signal(8)
        previous_vsync = Signal()
        m.d.dvi += previous_vsync.eq(dvi_tgen.ctrl.vsync)
        with m.If(dvi_tgen.ctrl.vsync & ~previous_vsync):
            m.d.dvi += frame.eq(frame + 1)

        m.d.comb += [
            visualizer.x.eq(dvi_tgen.x),
            visualizer.y.eq(dvi_tgen.y),
            visualizer.center_x.eq(self.clock_settings.modeline.h_active // 2),
            visualizer.center_y.eq(self.clock_settings.modeline.v_active // 2),
            visualizer.envelope.eq(video_envelope),
            visualizer.gate.eq(video_gate_sample != 0),
            visualizer.magnitude.eq(video_magnitude),
            visualizer.frame.eq(frame),
        ]

        with m.If(dvi_tgen.ctrl.de):
            m.d.comb += [
                self.video_r.eq(visualizer.r),
                self.video_g.eq(visualizer.g),
                self.video_b.eq(visualizer.b),
            ]

        if sim.is_hw(platform):
            m.submodules.dvi_gen = dvi_gen = dvi.DVIPHY()
            m.d.dvi += [
                dvi_gen.i.de.eq(dvi_tgen.ctrl_phy.de),
                dvi_gen.i.r.eq(self.video_r),
                dvi_gen.i.g.eq(self.video_g),
                dvi_gen.i.b.eq(self.video_b),
                dvi_gen.i.hsync.eq(dvi_tgen.ctrl_phy.hsync),
                dvi_gen.i.vsync.eq(dvi_tgen.ctrl_phy.vsync),
            ]

        return m


def simulation_ports(fragment):
    return {
        "clk_sync": (ClockSignal("sync"), None),
        "rst_sync": (ResetSignal("sync"), None),
        "clk_dvi": (ClockSignal("dvi"), None),
        "rst_dvi": (ResetSignal("dvi"), None),
        "clk_audio": (ClockSignal("audio"), None),
        "rst_audio": (ResetSignal("audio"), None),
        "i2s_sdin1": (fragment.pmod0.pins.i2s.sdin1, None),
        "i2s_sdout1": (fragment.pmod0.pins.i2s.sdout1, None),
        "i2s_lrck": (fragment.pmod0.pins.i2s.lrck, None),
        "i2s_bick": (fragment.pmod0.pins.i2s.bick, None),
        "dvi_de": (fragment.dvi_tgen.ctrl_phy.de, None),
        "dvi_vsync": (fragment.dvi_tgen.ctrl_phy.vsync, None),
        "dvi_hsync": (fragment.dvi_tgen.ctrl_phy.hsync, None),
        "dvi_r": (fragment.video_r, None),
        "dvi_g": (fragment.video_g, None),
        "dvi_b": (fragment.video_b, None),
        "self_test_active": (fragment.self_test_active, None),
        "test_phase": (fragment.test_phase, None),
        "test_sample_index": (fragment.test_sample_index, None),
        "test_note": (fragment.test_note, None),
    }


def argparse_callback(parser):
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--base-note", type=int, default=48)
    parser.add_argument("--pitch-zero-counts", type=int, default=0)
    parser.add_argument("--counts-per-octave", type=int, default=4_000)
    parser.add_argument(
        "--waveform",
        choices=("saw", "triangle", "square", "silence"),
        default="triangle",
    )
    parser.add_argument("--drive", type=int, choices=range(4), default=0)
    parser.add_argument("--gate-on-volts", type=float, default=4.0)
    parser.add_argument("--gate-off-volts", type=float, default=2.0)


def argparse_fragment(args):
    if args.self_test and args.name == "DSLX-SYNTH":
        args.name = "DSLX-SYNTH-LAB"
    return {
        "self_test": args.self_test,
        "base_note": args.base_note,
        "pitch_zero_counts": args.pitch_zero_counts,
        "counts_per_octave": args.counts_per_octave,
        "waveform": args.waveform,
        "drive": args.drive,
        "gate_on_volts": args.gate_on_volts,
        "gate_off_volts": args.gate_off_volts,
    }


if __name__ == "__main__":
    top_level_cli(
        DSLXSynthTop,
        sim_ports=simulation_ports,
        sim_harness="../../src/top/dslx_av/sim.cpp",
        argparse_callback=argparse_callback,
        argparse_fragment=argparse_fragment,
    )
