# Copyright (c) 2026 Tiliqua contributors
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Minimal audio-reactive DSLX audio/video top for Tiliqua."""

from amaranth import ClockSignal, Elaboratable, Module, ResetSignal, Signal
from amaranth.lib import data, wiring
from amaranth.lib.cdc import FFSynchronizer

from tiliqua.build import sim
from tiliqua.build.cli import top_level_cli
from tiliqua.build.types import BitstreamHelp
from tiliqua.dsp import ASQ
from tiliqua.dsp.dslx_reactor import DSLXReactor
from tiliqua.dsp.synth import SynthTestSource
from tiliqua.dsp.stream_util import SyncFIFOBuffered
from tiliqua.periph import eurorack_pmod
from tiliqua.platform import RebootProvider
from tiliqua.video import dvi
from tiliqua.video.dslx_visualizer import DSLXVisualizer


class DSLXAVTop(Elaboratable):
    """Run the DSLX audio reactor and use its controls to draw DVI pixels."""

    bitstream_help = BitstreamHelp(
        brief="DSLX basic audio-reactive video",
        io_left=[
            "signal", "threshold CV", "reserved", "reserved",
            "passthrough", "envelope", "5V gate", "magnitude",
        ],
        io_right=["", "", "video (fixed)", "", "", ""],
    )

    def __init__(self, *, clock_settings, self_test=False):
        if clock_settings.modeline is None:
            raise ValueError("dslx_av requires a fixed video mode: use --modeline")

        self.clock_settings = clock_settings
        self.self_test = self_test
        self.pmod0 = eurorack_pmod.EurorackPmod(clock_settings.audio_clock)
        self.dvi_tgen = dvi.DVITimingGen()
        self.reactor = DSLXReactor()
        self.visualizer = DSLXVisualizer()

        # Exposed for the Verilator DVI testbench.
        self.video_r = Signal(8)
        self.video_g = Signal(8)
        self.video_b = Signal(8)
        self.self_test_active = Signal(init=int(self_test))
        self.test_phase = Signal(32)
        self.test_sample_index = Signal(32)

        if self_test:
            self.bitstream_help = BitstreamHelp(
                brief="DSLX deterministic audio/video self-test",
                io_left=[
                    "unused", "unused", "unused", "unused",
                    "triangle", "envelope", "5V gate", "magnitude",
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
            m.submodules.pmod0_provider = pmod0_provider = eurorack_pmod.FFCProvider()
            wiring.connect(m, self.pmod0.pins, pmod0_provider.pins)
            m.d.comb += self.pmod0.codec_mute.eq(reboot.mute)
        else:
            m.submodules.car = sim.FakeTiliquaDomainGenerator()

        m.submodules.pmod0 = pmod0 = self.pmod0
        m.submodules.reactor = reactor = self.reactor
        m.submodules.dvi_tgen = dvi_tgen = self.dvi_tgen
        m.submodules.visualizer = visualizer = self.visualizer

        m.d.comb += self.self_test_active.eq(self.self_test)

        # Audio path: either ADC or a deterministic no-cable test signal enters
        # the same DSLX reactor, then always exits through the real DAC path.
        if self.self_test:
            m.submodules.test_source = test_source = SynthTestSource()
            m.submodules.test_source_buffer = test_source_buffer = SyncFIFOBuffered(
                shape=data.ArrayLayout(ASQ, 4), depth=2
            )
            wiring.connect(m, test_source.o, test_source_buffer.i)
            wiring.connect(m, test_source_buffer.o, reactor.i)
            m.d.comb += [
                self.test_phase.eq(test_source.phase),
                self.test_sample_index.eq(test_source.sample_index),
            ]
        else:
            wiring.connect(m, pmod0.o_cal, reactor.i)
        wiring.connect(m, reactor.o, pmod0.i_cal)

        # Hold the most recently accepted reactor output for the video CDC.
        processed_audio = Signal(data.ArrayLayout(ASQ, 4))
        with m.If(reactor.o.valid & reactor.o.ready):
            m.d.sync += processed_audio.eq(reactor.o.payload)

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
    }


def argparse_callback(parser):
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Use an internal deterministic tone instead of ADC inputs.",
    )


def argparse_fragment(args):
    if args.self_test and args.name == "DSLX-AV":
        args.name = "DSLX-AV-LAB"
    return {"self_test": args.self_test}


if __name__ == "__main__":
    top_level_cli(
        DSLXAVTop,
        sim_ports=simulation_ports,
        sim_harness="../../src/top/dslx_av/sim.cpp",
        argparse_callback=argparse_callback,
        argparse_fragment=argparse_fragment,
    )
