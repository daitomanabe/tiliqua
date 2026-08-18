# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Sparse adaptive SNN2 audio/CV and DVI top for Tiliqua R5."""

from __future__ import annotations

import json
from pathlib import Path

from amaranth import ClockSignal, Elaboratable, Module, ResetSignal, Signal, unsigned
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.memory import Memory

from tiliqua.build import sim
from tiliqua.build.cli import top_level_cli
from tiliqua.build.types import BitstreamHelp
from tiliqua.periph import eurorack_pmod
from tiliqua.platform import RebootProvider
from tiliqua.snn2.encoder import SNN2AudioEncoder, SNN2TestSource
from tiliqua.snn2.manifest import validate_manifest
from tiliqua.snn2.performance import SNN2PerformanceMapper
from tiliqua.snn2.rtl import SparseALIFNetwork
from tiliqua.video import dvi
from tiliqua.video.snn2_visualizer import SNN2Visualizer


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = ROOT / "snn2" / "snn2_256x16_sparse_alif_v1.json"


class SNN2AVTop(Elaboratable):
    """Join the frozen SNN2 manifest, calibrated I/O, and diagnostic DVI."""

    bitstream_help = BitstreamHelp(
        brief="256-neuron sparse adaptive SNN2",
        io_left=[
            "signal", "encoder gain", "inhibitory gain", "adaptation gain",
            "learned readout", "excitatory rate", "inhibitory rate", "E/I balance",
        ],
        io_right=["", "", "16x16 SNN2 + meters", "", "", ""],
    )

    def __init__(
        self, *, clock_settings, self_test=False, performance=False,
        manifest=None,
    ):
        if clock_settings.modeline is None:
            raise ValueError("snn2_av requires a fixed video mode")
        if manifest is None:
            manifest = json.loads(DEFAULT_MANIFEST.read_text())
        self.manifest = validate_manifest(manifest)
        self.clock_settings = clock_settings
        self.self_test = self_test
        self.performance = performance
        self.pmod0 = eurorack_pmod.EurorackPmod(clock_settings.audio_clock)
        self.core = SparseALIFNetwork(self.manifest)
        self.encoder = None if self_test else SNN2AudioEncoder(self.manifest)
        self.test_source = SNN2TestSource() if self_test else None
        self.dvi_tgen = dvi.DVITimingGen()
        self.visualizer = SNN2Visualizer(external_rows=True)

        self.video_r = Signal(8)
        self.video_g = Signal(8)
        self.video_b = Signal(8)
        self.self_test_active = Signal(init=int(self_test))
        self.cv_output_active = Signal()
        self.performance_output_active = Signal(init=int(performance))
        self.test_phase = Signal(32)
        self.test_sample_index = Signal(32)
        self.snn2_fault = Signal()
        self.scheduler_cycles_debug = Signal(range(2049))
        self.excitatory_count_debug = Signal(range(193))
        self.inhibitory_count_debug = Signal(range(65))
        self.band_levels_debug = Signal(64)
        self.sim_regression_name = "SNN2-AV"
        self.sim_metrics_filename = "snn2-av-metrics.json"
        if self_test:
            self.bitstream_help = BitstreamHelp(
                brief="256-neuron sparse adaptive SNN2 self-test",
                io_left=[
                    "unused", "unused", "unused", "unused",
                    "learned readout", "excitatory rate",
                    "inhibitory rate", "E/I balance",
                ],
                io_right=["", "", "SNN2 test + meters", "", "", ""],
            )
        if performance:
            self.bitstream_help = BitstreamHelp(
                brief="SNN2 stereo music plus pitch CV and gate",
                io_left=[
                    "unused" if self_test else "signal",
                    "unused" if self_test else "encoder gain",
                    "unused" if self_test else "inhibitory gain",
                    "unused" if self_test else "adaptation gain",
                    "stereo music L", "stereo music R",
                    "1V/oct melody", "density gate",
                ],
                io_right=["", "", "SNN2 music + CV", "", "", ""],
            )
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        if sim.is_hw(platform):
            m.submodules.car = platform.clock_domain_generator(self.clock_settings)
            encoder_pins = platform.request("encoder")
            m.submodules.reboot = reboot = RebootProvider(
                self.clock_settings.frequencies.sync
            )
            m.submodules.button_cdc = FFSynchronizer(
                encoder_pins.s.i, reboot.button
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
            self.cv_output_active.eq(0),
            self.performance_output_active.eq(self.performance),
            self.test_phase.eq(core.spike_vector[:32]),
            self.test_sample_index.eq(core.sample_index),
            self.snn2_fault.eq(core.fault),
            self.scheduler_cycles_debug.eq(core.scheduler_cycles),
            self.excitatory_count_debug.eq(core.excitatory_spike_count),
            self.inhibitory_count_debug.eq(core.inhibitory_spike_count),
        ]

        if self.self_test:
            m.submodules.test_source = test_source = self.test_source
            wiring.connect(m, test_source.o, core.i)
            for band in range(8):
                m.d.comb += self.band_levels_debug.word_select(band, 8).eq(
                    test_source.band_levels[band]
                )
        else:
            m.submodules.encoder = encoder = self.encoder
            wiring.connect(m, pmod0.o_cal, encoder.i)
            wiring.connect(m, encoder.o, core.i)
            for band in range(8):
                m.d.comb += self.band_levels_debug.word_select(band, 8).eq(
                    encoder.band_levels[band]
                )
        if self.performance:
            m.submodules.performance_mapper = performance_mapper = (
                SNN2PerformanceMapper(
                    sample_rate=self.clock_settings.audio_clock.fs(),
                    # Shorten only transfer-count simulation so the 100 ms AV
                    # regression observes several note/gate updates. Hardware
                    # timing below remains an exact 8 Hz sync-domain clock.
                    control_period_samples=(
                        6000 if sim.is_hw(platform) else 512
                    ),
                    gate_high_samples=(
                        3000 if sim.is_hw(platform) else 256
                    ),
                    activity_profile=(
                        "dense" if self.self_test else "sparse"
                    ),
                    wall_clock_hz=(
                        int(self.clock_settings.frequencies.sync)
                        if sim.is_hw(platform)
                        else None
                    ),
                )
            )
            m.d.comb += [
                performance_mapper.excitatory_spike_count.eq(
                    core.excitatory_spike_count
                ),
                performance_mapper.inhibitory_spike_count.eq(
                    core.inhibitory_spike_count
                ),
            ]
            wiring.connect(m, core.o, performance_mapper.i)
            wiring.connect(m, performance_mapper.o, pmod0.i_cal)
        else:
            wiring.connect(m, core.o, pmod0.i_cal)

        for member in dvi_tgen.timings.signature.members:
            m.d.comb += getattr(dvi_tgen.timings, member).eq(
                getattr(self.clock_settings.modeline, member)
            )

        m.submodules.display_memory = display_memory = Memory(
            shape=unsigned(16 * 5),
            depth=16,
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

        video_bands = Signal(64)
        video_excitatory = Signal(range(193))
        video_inhibitory = Signal(range(65))
        video_scheduler = Signal(range(2049))
        video_fault = Signal()
        for name, source, target in (
            ("bands", self.band_levels_debug, video_bands),
            ("excitatory", core.excitatory_spike_count, video_excitatory),
            ("inhibitory", core.inhibitory_spike_count, video_inhibitory),
            ("scheduler", core.scheduler_cycles, video_scheduler),
            ("fault", core.fault, video_fault),
        ):
            setattr(
                m.submodules,
                f"{name}_cdc",
                FFSynchronizer(source, target, o_domain="dvi"),
            )

        frame = Signal(8)
        previous_vsync = Signal()
        frame_bands = Signal(64)
        frame_excitatory = Signal(range(193))
        frame_inhibitory = Signal(range(65))
        frame_scheduler = Signal(range(2049))
        frame_fault = Signal()
        m.d.dvi += previous_vsync.eq(dvi_tgen.ctrl.vsync)
        with m.If(dvi_tgen.ctrl.vsync & ~previous_vsync):
            m.d.dvi += [
                frame.eq(frame + 1),
                frame_bands.eq(video_bands),
                frame_excitatory.eq(video_excitatory),
                frame_inhibitory.eq(video_inhibitory),
                frame_scheduler.eq(video_scheduler),
                frame_fault.eq(video_fault),
            ]

        m.d.comb += [
            visualizer.x.eq(dvi_tgen.x),
            visualizer.y.eq(dvi_tgen.y),
            visualizer.band_levels.eq(frame_bands),
            visualizer.excitatory_count.eq(frame_excitatory),
            visualizer.inhibitory_count.eq(frame_inhibitory),
            visualizer.scheduler_cycles.eq(frame_scheduler),
            visualizer.fault.eq(frame_fault),
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
        "cv_output_active": (fragment.cv_output_active, None),
        "performance_output_active": (
            fragment.performance_output_active, None
        ),
        "test_phase": (fragment.test_phase, None),
        "test_sample_index": (fragment.test_sample_index, None),
        "snn2_fault": (fragment.snn2_fault, None),
        "scheduler_cycles_debug": (fragment.scheduler_cycles_debug, None),
        "excitatory_count_debug": (fragment.excitatory_count_debug, None),
        "inhibitory_count_debug": (fragment.inhibitory_count_debug, None),
        "band_levels_debug": (fragment.band_levels_debug, None),
    }


def argparse_callback(parser):
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--performance",
        action="store_true",
        help="Output stereo SNN2 music, 1 V/oct pitch, and a density gate",
    )
    parser.add_argument(
        "--nextpnr-seed",
        type=int,
        default=None,
        help="Fix the nextpnr placement seed for reproducible SNN2 QoR",
    )


def argparse_fragment(args):
    if args.name == "SNN2-AV":
        if args.performance:
            args.name = (
                "SNN2-AV-PERFORMANCE-LAB"
                if args.self_test
                else "SNN2-AV-PERFORMANCE-LIVE"
            )
        else:
            args.name = "SNN2-AV-LAB" if args.self_test else "SNN2-AV-LIVE"
    return {"self_test": args.self_test, "performance": args.performance}


if __name__ == "__main__":
    top_level_cli(
        SNN2AVTop,
        sim_ports=simulation_ports,
        sim_harness="../../src/top/snn2_av/sim.cpp",
        argparse_callback=argparse_callback,
        argparse_fragment=argparse_fragment,
    )
