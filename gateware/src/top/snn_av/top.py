# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Parallel spiking audio/video top for Tiliqua R5."""

from amaranth import (
    ClockSignal, Elaboratable, Module, ResetSignal, Signal, unsigned,
)
from amaranth.lib import data, wiring
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.memory import Memory

from tiliqua.build import sim
from tiliqua.build.cli import top_level_cli
from tiliqua.build.types import BitstreamHelp
from tiliqua.dsp import ASQ
from tiliqua.dsp.snn import (
    BatchedLIFBank,
    MemoryBatchedLIFBank,
    ParallelLIFBank,
    PopulationEnsembleSonifier,
    SNNTestSource,
)
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
        physical_lane_count=None, ei_ring=False, inhibitory_strength=1024,
        sonification=False,
    ):
        if clock_settings.modeline is None:
            raise ValueError("snn_av requires a fixed video mode")
        if neuron_count not in (64, 128, 256, 512, 1024):
            raise ValueError(
                "snn_av supports 64, 128, 256, 512, or 1024 neurons"
            )
        if ei_ring and (neuron_count != 1024 or physical_lane_count != 32):
            raise ValueError("E/I ring profile requires 1024 neurons and 32 lanes")
        if not ei_ring and inhibitory_strength != 1024:
            raise ValueError("inhibitory strength requires the E/I ring profile")
        if sonification and not (
            neuron_count == 1024 and physical_lane_count == 32 and ei_ring
        ):
            raise ValueError(
                "sonification requires the 1024-neuron 32-lane E/I ring profile"
            )
        if physical_lane_count is not None:
            valid_batched = (
                neuron_count == 256
                and physical_lane_count in (32, 64, 128)
            ) or (
                neuron_count in (512, 1024) and physical_lane_count == 32
            )
            if not valid_batched:
                raise ValueError(
                    "batched snn_av supports 256x(32,64,128), 512x32, or 1024x32"
                )
        self.clock_settings = clock_settings
        self.self_test = self_test
        self.neuron_count = neuron_count
        self.physical_lane_count = physical_lane_count or neuron_count
        self.ei_ring = ei_ring
        self.inhibitory_strength = inhibitory_strength
        self.sonification = sonification
        self.pmod0 = eurorack_pmod.EurorackPmod(clock_settings.audio_clock)
        if neuron_count in (512, 1024):
            if physical_lane_count != 32:
                raise ValueError(
                    f"{neuron_count}-neuron snn_av requires 32 physical lanes"
                )
            self.core = MemoryBatchedLIFBank(
                logical_neuron_count=neuron_count,
                physical_lane_count=physical_lane_count,
                ei_ring=ei_ring,
                inhibitory_strength=inhibitory_strength,
            )
        elif physical_lane_count is None:
            self.core = ParallelLIFBank(neuron_count=neuron_count)
        else:
            self.core = BatchedLIFBank(
                logical_neuron_count=neuron_count,
                physical_lane_count=physical_lane_count,
            )
        self.dvi_tgen = dvi.DVITimingGen()
        self.membrane_level_bits = getattr(self.core, "membrane_level_bits", 4)
        self.visualizer = SNNVisualizer(
            neuron_count=neuron_count,
            membrane_level_bits=self.membrane_level_bits,
            external_rows=neuron_count == 1024,
            inhibitory_stride=4 if ei_ring else None,
        )

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
        if ei_ring:
            architecture_brief = f"{architecture_brief} E/I ring"
        if sonification:
            architecture_brief = "1024-neuron E/I 4-voice AV ensemble"
        output_labels = (
            [
                "total activity tone", "excitatory voice",
                "inhibitory voice", "E/I balance bass",
            ]
            if sonification else
            ["spike audio", "population activity", "burst gate", "mean membrane"]
        )
        self.bitstream_help = BitstreamHelp(
            brief=architecture_brief,
            io_left=[
                "network drive", "leak control", "recurrence control",
                "threshold control", *output_labels,
            ],
            io_right=[
                "", "", (
                    "64x16 neuron grid" if neuron_count == 1024
                    else f"{neuron_count // 8}x8 neuron grid"
                ),
                "", "", "",
            ],
        )

        if self_test:
            self.bitstream_help = BitstreamHelp(
                brief=f"{architecture_brief} self-test",
                io_left=[
                    "unused", "unused", "unused", "unused",
                    *output_labels,
                ],
                io_right=[
                    "", "",
                    (
                        "64x16 SNN self-test" if neuron_count == 1024
                        else f"{neuron_count // 8}x8 SNN self-test"
                    ),
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
        if self.sonification:
            m.submodules.sonifier = sonifier = PopulationEnsembleSonifier(
                neuron_count=self.neuron_count,
                sample_rate=self.clock_settings.audio_clock.fs(),
            )
            m.d.comb += [
                sonifier.spike_count.eq(core.spike_count),
                sonifier.excitatory_spike_count.eq(
                    core.excitatory_spike_count
                ),
                sonifier.inhibitory_spike_count.eq(
                    core.inhibitory_spike_count
                ),
            ]
            wiring.connect(m, core.o, sonifier.i)
            wiring.connect(m, sonifier.o, pmod0.i_cal)
        else:
            wiring.connect(m, core.o, pmod0.i_cal)

        for member in dvi_tgen.timings.signature.members:
            m.d.comb += getattr(dvi_tgen.timings, member).eq(
                getattr(self.clock_settings.modeline, member)
            )

        frame = Signal(8)
        previous_vsync = Signal()
        frame_spikes = Signal(self.neuron_count)
        frame_membranes = Signal(
            self.neuron_count * self.membrane_level_bits
        )
        frame_activity = Signal(core.count_bits)
        frame_burst = Signal()
        m.d.dvi += previous_vsync.eq(dvi_tgen.ctrl.vsync)

        if self.neuron_count == 1024:
            # A dual-clock display RAM replaces thousands of direct bundled
            # CDC wires. The sync side writes one 32-neuron row whenever the
            # compute engine commits it; the DVI side reads the row containing
            # the current cell. Cell borders hide the one-cycle BRAM read
            # latency after an address change. Display state may span nearby
            # audio samples, but computation and audio remain sample-atomic.
            display_row_width = 32 * (1 + self.membrane_level_bits)
            m.submodules.display_memory = display_memory = Memory(
                shape=unsigned(display_row_width),
                depth=self.neuron_count // 32,
                init=[],
                attrs={"ram_style": "block"},
            )
            display_write = display_memory.write_port(domain="sync")
            display_read = display_memory.read_port(domain="dvi")
            m.d.comb += [
                display_write.addr.eq(core.display_row_addr),
                display_write.data.eq(core.display_row_data),
                display_write.en.eq(core.display_row_valid),
                display_read.addr.eq(visualizer.external_row_addr),
                display_read.en.eq(1),
                visualizer.external_row_data.eq(display_read.data),
            ]

            video_activity = Signal(core.count_bits)
            video_burst = Signal()
            m.submodules.activity_cdc = FFSynchronizer(
                core.spike_count, video_activity, o_domain="dvi"
            )
            m.submodules.burst_cdc = FFSynchronizer(
                core.spike_count >= 2, video_burst, o_domain="dvi"
            )
            with m.If(dvi_tgen.ctrl.vsync & ~previous_vsync):
                m.d.dvi += [
                    frame.eq(frame + 1),
                    frame_activity.eq(video_activity),
                    frame_burst.eq(video_burst),
                ]
        elif self.neuron_count == 512:
            # The SNN state is stable for almost the complete 48 kHz sample.
            # Toggle synchronization therefore supplies a bundled-data CDC:
            # after two DVI cycles the multi-bit payload has already been
            # stable for multiple source/destination cycles. Capture only the
            # first new sample after vsync so one frame never tears, while
            # avoiding two synchronizer FFs for every bundled payload bit.
            previous_sample_index = Signal.like(core.sample_index)
            snapshot_toggle = Signal()
            m.d.sync += previous_sample_index.eq(core.sample_index)
            with m.If(core.sample_index != previous_sample_index):
                m.d.sync += snapshot_toggle.eq(~snapshot_toggle)

            video_snapshot_toggle = Signal()
            m.submodules.snapshot_toggle_cdc = FFSynchronizer(
                snapshot_toggle, video_snapshot_toggle, o_domain="dvi"
            )
            observed_snapshot_toggle = Signal()
            capture_armed = Signal(init=1)
            with m.If(dvi_tgen.ctrl.vsync & ~previous_vsync):
                m.d.dvi += [frame.eq(frame + 1), capture_armed.eq(1)]
            with m.Elif(video_snapshot_toggle != observed_snapshot_toggle):
                m.d.dvi += observed_snapshot_toggle.eq(video_snapshot_toggle)
                with m.If(capture_armed):
                    m.d.dvi += [
                        capture_armed.eq(0),
                        frame_spikes.eq(core.spike_vector),
                        frame_membranes.eq(core.membrane_levels),
                        frame_activity.eq(core.spike_count),
                        frame_burst.eq(core.spike_count >= 2),
                    ]
        else:
            video_spikes = Signal(self.neuron_count)
            video_membranes = Signal(
                self.neuron_count * self.membrane_level_bits
            )
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
    parser.add_argument("--ei-ring", action="store_true")
    parser.add_argument("--sonification", action="store_true")
    parser.add_argument(
        "--inhibitory-strength",
        type=int,
        choices=range(256, 2049, 256),
        default=1024,
    )
    parser.add_argument(
        "--neurons", type=int, choices=(64, 128, 256, 512, 1024), default=64
    )
    parser.add_argument("--physical-lanes", type=int, choices=(32, 64, 128))


def argparse_fragment(args):
    if args.name == "SNN-AV":
        if args.physical_lanes is not None:
            memory_suffix = "-MEM" if args.neurons in (512, 1024) else ""
            args.name = (
                f"SNN-AV-{args.neurons}X{args.physical_lanes}"
                f"{memory_suffix}-LAB"
                if args.self_test
                else f"SNN-AV-{args.neurons}X{args.physical_lanes}{memory_suffix}"
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
        "ei_ring": args.ei_ring,
        "inhibitory_strength": args.inhibitory_strength,
        "sonification": args.sonification,
    }


if __name__ == "__main__":
    top_level_cli(
        SNNAVTop,
        sim_ports=simulation_ports,
        sim_harness="../../src/top/dslx_av/sim.cpp",
        argparse_callback=argparse_callback,
        argparse_fragment=argparse_fragment,
    )
