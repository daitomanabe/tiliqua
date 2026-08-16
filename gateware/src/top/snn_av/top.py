# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Parallel spiking audio/video top for Tiliqua R5."""

from amaranth import ClockSignal, Elaboratable, Module, ResetSignal, Signal
from amaranth.lib import data, wiring
from amaranth.lib.cdc import FFSynchronizer

from tiliqua.build import sim
from tiliqua.build.cli import top_level_cli
from tiliqua.build.types import BitstreamHelp
from tiliqua.dsp import ASQ
from tiliqua.dsp.snn import BatchedLIFBank, ParallelLIFBank, SNNTestSource
from tiliqua.dsp.stream_util import SyncFIFOBuffered
from tiliqua.periph import eurorack_pmod
from tiliqua.platform import RebootProvider
from tiliqua.video import dvi
from tiliqua.video.snn_visualizer import SNNVisualizer


class SNNAVTop(Elaboratable):
    """Connect a parallel LIF population to calibrated audio and DVI."""

    bitstream_help = BitstreamHelp(
        brief="64-neuron spiking audio/video network",
        io_left=[
            "network drive", "reserved", "reserved", "reserved",
            "spike audio", "population activity", "burst gate", "mean membrane",
        ],
        io_right=["", "", "8x8 neuron grid", "", "", ""],
    )

    def __init__(
        self, *, clock_settings, self_test=False, neuron_count=64,
        physical_lane_count=None,
    ):
        if clock_settings.modeline is None:
            raise ValueError("snn_av requires a fixed video mode")
        if neuron_count not in (64, 128, 256):
            raise ValueError("snn_av supports 64, 128, or 256 neurons")
        if physical_lane_count is not None and (
            neuron_count != 256 or physical_lane_count not in (32, 64, 128)
        ):
            raise ValueError(
                "batched snn_av supports 256 neurons with 32, 64, or 128 lanes"
            )
        self.clock_settings = clock_settings
        self.self_test = self_test
        self.neuron_count = neuron_count
        self.physical_lane_count = physical_lane_count or neuron_count
        self.pmod0 = eurorack_pmod.EurorackPmod(clock_settings.audio_clock)
        if physical_lane_count is None:
            self.core = ParallelLIFBank(neuron_count=neuron_count)
        else:
            self.core = BatchedLIFBank(
                logical_neuron_count=neuron_count,
                physical_lane_count=physical_lane_count,
            )
        self.dvi_tgen = dvi.DVITimingGen()
        self.visualizer = SNNVisualizer(neuron_count=neuron_count)

        self.video_r = Signal(8)
        self.video_g = Signal(8)
        self.video_b = Signal(8)
        self.self_test_active = Signal(init=int(self_test))
        self.test_phase = Signal(32)
        self.test_sample_index = Signal(32)
        self.sim_regression_name = "SNN-AV"
        self.sim_metrics_filename = "snn-av-metrics.json"
        architecture_brief = (
            f"{neuron_count}-neuron spiking audio/video network"
            if physical_lane_count is None
            else f"{neuron_count}-neuron {physical_lane_count}-lane batched spiking AV"
        )
        self.bitstream_help = BitstreamHelp(
            brief=architecture_brief,
            io_left=[
                "network drive", "leak control", "recurrence control",
                "threshold control", "spike audio", "population activity",
                "burst gate", "mean membrane",
            ],
            io_right=[
                "", "", f"{neuron_count // 8}x8 neuron grid",
                "", "", "",
            ],
        )

        if self_test:
            self.bitstream_help = BitstreamHelp(
                brief=f"{architecture_brief} self-test",
                io_left=[
                    "unused", "unused", "unused", "unused",
                    "spike audio", "population activity", "burst gate", "mean membrane",
                ],
                io_right=[
                    "", "",
                    f"{neuron_count // 8}x8 SNN self-test",
                    "", "", "",
                ],
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
        m.submodules.core = core = self.core
        m.submodules.dvi_tgen = dvi_tgen = self.dvi_tgen
        m.submodules.visualizer = visualizer = self.visualizer
        m.d.comb += [
            self.self_test_active.eq(self.self_test),
            self.test_phase.eq(core.spike_vector[:32]),
        ]

        if self.self_test:
            m.submodules.test_source = test_source = SNNTestSource()
            m.submodules.test_source_buffer = test_source_buffer = SyncFIFOBuffered(
                shape=data.ArrayLayout(ASQ, 4), depth=2
            )
            wiring.connect(m, test_source.o, test_source_buffer.i)
            wiring.connect(m, test_source_buffer.o, core.i)
            m.d.comb += self.test_sample_index.eq(test_source.sample_index)
        else:
            wiring.connect(m, pmod0.o_cal, core.i)
            m.d.comb += self.test_sample_index.eq(core.sample_index)
        wiring.connect(m, core.o, pmod0.i_cal)

        video_spikes = Signal(self.neuron_count)
        video_membranes = Signal(self.neuron_count * 4)
        video_activity = Signal(core.count_bits)
        video_burst = Signal()
        m.submodules.spike_cdc = FFSynchronizer(
            core.spike_vector, video_spikes, o_domain="dvi"
        )
        m.submodules.membrane_cdc = FFSynchronizer(
            core.membrane_levels, video_membranes, o_domain="dvi"
        )
        m.submodules.activity_cdc = FFSynchronizer(
            core.spike_count, video_activity, o_domain="dvi"
        )
        m.submodules.burst_cdc = FFSynchronizer(
            core.spike_count >= 2, video_burst, o_domain="dvi"
        )

        for member in dvi_tgen.timings.signature.members:
            m.d.comb += getattr(dvi_tgen.timings, member).eq(
                getattr(self.clock_settings.modeline, member)
            )

        frame = Signal(8)
        previous_vsync = Signal()
        frame_spikes = Signal(self.neuron_count)
        frame_membranes = Signal(self.neuron_count * 4)
        frame_activity = Signal(core.count_bits)
        frame_burst = Signal()
        m.d.dvi += previous_vsync.eq(dvi_tgen.ctrl.vsync)
        with m.If(dvi_tgen.ctrl.vsync & ~previous_vsync):
            m.d.dvi += [
                frame.eq(frame + 1),
                frame_spikes.eq(video_spikes),
                frame_membranes.eq(video_membranes),
                frame_activity.eq(video_activity),
                frame_burst.eq(video_burst),
            ]

        m.d.comb += [
            visualizer.x.eq(dvi_tgen.x),
            visualizer.y.eq(dvi_tgen.y),
            visualizer.spikes.eq(frame_spikes),
            visualizer.membrane_levels.eq(frame_membranes),
            visualizer.activity.eq(frame_activity),
            visualizer.burst.eq(frame_burst),
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
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--neurons", type=int, choices=(64, 128, 256), default=64)
    parser.add_argument("--physical-lanes", type=int, choices=(32, 64, 128))


def argparse_fragment(args):
    if args.name == "SNN-AV":
        if args.physical_lanes is not None:
            args.name = (
                f"SNN-AV-{args.neurons}X{args.physical_lanes}-LAB"
                if args.self_test
                else f"SNN-AV-{args.neurons}X{args.physical_lanes}"
            )
        elif args.self_test:
            args.name = (
                "SNN-AV-LAB" if args.neurons == 64
                else f"SNN-AV-{args.neurons}-LAB"
            )
        elif args.neurons != 64:
            args.name = f"SNN-AV-{args.neurons}"
    return {
        "self_test": args.self_test,
        "neuron_count": args.neurons,
        "physical_lane_count": args.physical_lanes,
    }


if __name__ == "__main__":
    top_level_cli(
        SNNAVTop,
        sim_ports=simulation_ports,
        sim_harness="../../src/top/dslx_av/sim.cpp",
        argparse_callback=argparse_callback,
        argparse_fragment=argparse_fragment,
    )
