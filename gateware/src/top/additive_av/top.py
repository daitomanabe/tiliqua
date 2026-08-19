# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""1,000-sine additive instrument with host control, four CVs, four outputs, and HDMI."""

from __future__ import annotations

from amaranth import Cat, ClockSignal, Const, Elaboratable, Module, Mux, ResetSignal, Signal, signed, unsigned
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.memory import Memory

from tiliqua.additive import tables as T
from tiliqua.additive.control import ControlFrameDecoder, SerialByteRx
from tiliqua.additive.core import AdditiveCore
from tiliqua.build import sim
from tiliqua.build.cli import top_level_cli
from tiliqua.build.types import BitstreamHelp
from tiliqua.periph import eurorack_pmod
from tiliqua.platform import RebootProvider
from tiliqua.video import dvi
from tiliqua.video.additive_visualizer import AdditiveVisualizer


class AdditiveAVTop(Elaboratable):
    """Join the additive core, the host control link, calibrated I/O, and DVI."""

    bitstream_help = BitstreamHelp(
        brief="1000-sine additive instrument",
        io_left=[
            "master/sub CV 0..2V", "pitch CV +/-1V", "tilt CV +/-1V",
            "spread/evolution CV", "master L", "master R", "low/sub stem", "air stem",
        ],
        io_right=["", "", "1000-sine state view", "", "", ""],
    )

    def __init__(self, *, clock_settings, link_timeout_seconds=2.0):
        if clock_settings.modeline is None:
            raise ValueError("additive_av requires a fixed video mode")
        self.clock_settings = clock_settings
        sync_hz = int(clock_settings.frequencies.sync)
        self.pmod0 = eurorack_pmod.EurorackPmod(clock_settings.audio_clock)
        self.core = AdditiveCore()
        self.decoder = ControlFrameDecoder(
            byte_timeout_cycles=sync_hz // 50,
            link_timeout_cycles=int(sync_hz * link_timeout_seconds),
        )
        self.dvi_tgen = dvi.DVITimingGen()
        self.visualizer = AdditiveVisualizer()

        self.video_r = Signal(8)
        self.video_g = Signal(8)
        self.video_b = Signal(8)
        # Simulation-only control byte injection (hardware uses the UART).
        self.control_byte = Signal(8)
        self.control_strobe = Signal()
        # Debug / regression ports.
        self.sample_index_debug = Signal(32)
        self.fault_debug = Signal()
        self.master_asq_debug = Signal(14)
        self.effective_master_debug = Signal(15)
        self.cv0_smoothed_debug = Signal(16)
        self.cv1_smoothed_debug = Signal(16)
        self.accepted_count_debug = Signal(16)
        self.rejected_count_debug = Signal(16)
        self.link_alive_debug = Signal()
        self.block_cycles_debug = Signal(18)
        self.sample_cycles_debug = Signal(12)
        self.sim_regression_name = "ADDITIVE-AV"
        self.sim_metrics_filename = "additive-av-metrics.json"
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        if sim.is_hw(platform):
            m.submodules.car = platform.clock_domain_generator(self.clock_settings)
            encoder_pins = platform.request("encoder")
            m.submodules.reboot = reboot = RebootProvider(
                self.clock_settings.frequencies.sync
            )
            m.submodules.button_cdc = FFSynchronizer(encoder_pins.s.i, reboot.button)
            m.submodules.pmod0_provider = pmod0_provider = eurorack_pmod.FFCProvider()
            wiring.connect(m, self.pmod0.pins, pmod0_provider.pins)
            m.d.comb += self.pmod0.codec_mute.eq(reboot.mute)
        else:
            m.submodules.car = sim.FakeTiliquaDomainGenerator()

        m.submodules.pmod0 = pmod0 = self.pmod0
        m.submodules.core = core = self.core
        m.submodules.decoder = decoder = self.decoder
        m.submodules.dvi_tgen = dvi_tgen = self.dvi_tgen
        m.submodules.visualizer = visualizer = self.visualizer

        wiring.connect(m, pmod0.o_cal, core.i)
        wiring.connect(m, core.o, pmod0.i_cal)
        m.d.comb += core.frame.eq(decoder.frame)

        if sim.is_hw(platform):
            uart_pins = platform.request("uart")
            m.submodules.control_rx = control_rx = SerialByteRx(
                system_clk_hz=int(self.clock_settings.frequencies.sync), pins=uart_pins,
            )
            wiring.connect(m, control_rx.o, decoder.i)
            # Never transmit: the RP2040 bridge inspects FPGA->host bytes.
            m.d.comb += uart_pins.tx.o.eq(1)
        else:
            m.d.comb += [
                decoder.i.payload.eq(self.control_byte),
                decoder.i.valid.eq(self.control_strobe),
            ]

        m.d.comb += [
            self.sample_index_debug.eq(core.sample_index),
            self.fault_debug.eq(core.fault),
            self.master_asq_debug.eq(core.master_asq),
            self.effective_master_debug.eq(core.effective.master),
            self.cv0_smoothed_debug.eq(core.cv_smoothed[0]),
            self.cv1_smoothed_debug.eq(core.cv_smoothed[1]),
            self.accepted_count_debug.eq(decoder.accepted_count),
            self.rejected_count_debug.eq(decoder.rejected_count),
            self.link_alive_debug.eq(decoder.link_alive),
            self.block_cycles_debug.eq(core.block_cycles),
            self.sample_cycles_debug.eq(core.sample_cycles),
        ]

        for member in dvi_tgen.timings.signature.members:
            m.d.comb += getattr(dvi_tgen.timings, member).eq(
                getattr(self.clock_settings.modeline, member)
            )

        # ---------------- display memories (sync write, dvi read) ----------------
        m.submodules.phase_display = phase_display = Memory(
            shape=unsigned(8), depth=1024, init=[], attrs={"ram_style": "block"},
        )
        phase_write = phase_display.write_port(domain="sync")
        phase_read = phase_display.read_port(domain="dvi")
        m.submodules.group_display = group_display = Memory(
            shape=unsigned(32), depth=64, init=[], attrs={"ram_style": "block"},
        )
        group_write = group_display.write_port(domain="sync")
        group_read = group_display.read_port(domain="dvi")
        m.d.comb += [
            phase_write.addr.eq(core.display_addr),
            phase_write.data.eq(core.display_data),
            phase_write.en.eq(core.display_en),
            group_write.addr.eq(core.group_display_addr),
            group_write.data.eq(core.group_display_data.as_value()),
            group_write.en.eq(core.group_display_en),
            phase_read.addr.eq(visualizer.particle_addr),
            phase_read.en.eq(1),
            group_read.addr.eq(visualizer.group_addr),
            group_read.en.eq(1),
            visualizer.particle_data.eq(phase_read.data),
            visualizer.group_amplitude.eq(group_read.data[0:16]),
            visualizer.group_low.eq(group_read.data[16]),
            visualizer.group_air.eq(group_read.data[17]),
            visualizer.group_pan.eq(group_read.data[18:26]),
        ]

        # ---------------- scalar status bundle (sync -> dvi, latched at vsync) ----
        def byte_of_q15(value):
            return value[7:15]

        def byte_of_detune(value):
            return (value * 29)[11:19]

        frame = decoder.frame
        effective = core.effective
        levels = Cat(*(byte_of_q15(frame.harmonic_levels[h]) for h in range(T.HARMONIC_COUNT)))
        base = Cat(
            byte_of_q15(frame.master), byte_of_q15(frame.sub_focus),
            byte_of_q15(frame.phase_spread), byte_of_q15(frame.evolution_amount),
            byte_of_q15(frame.stereo_width), byte_of_detune(frame.detune_millicents),
            Const(0, 16),
        )
        eff = Cat(
            byte_of_q15(effective.master), byte_of_q15(effective.sub_focus),
            byte_of_q15(effective.phase_spread), byte_of_q15(effective.evolution),
            byte_of_q15(effective.stereo_width), byte_of_detune(effective.detune_millicents),
            Const(0, 16),
        )
        pitch_scaled = Signal(signed(10))
        pitch_byte = Signal(signed(8))
        m.d.comb += [
            pitch_scaled.eq((effective.pitch_octaves_q10 * 3) >> 2),
            pitch_byte.eq(Mux(
                pitch_scaled > 127, 127, Mux(pitch_scaled < -127, -127, pitch_scaled)
            )),
        ]
        cv_bytes = Cat(
            core.cv_smoothed[0][7:15],
            core.cv_smoothed[1][8:16],
            core.cv_smoothed[2][8:16],
            core.cv_smoothed[3][8:16],
        )
        status = Signal(80 + 64 + 64 + 8 + 8 + 32 + 8 + 8 + 4)
        m.d.comb += status.eq(Cat(
            levels, base, eff, pitch_byte, core.cv_smoothed[2][8:16], cv_bytes,
            core.master_asq[5:13], core.block_cycles[10:18],
            decoder.link_alive, core.fault, core.control.overrun, decoder.accepted_count[0],
        ))
        status_dvi = Signal.like(status)
        m.submodules.status_cdc = FFSynchronizer(status, status_dvi, o_domain="dvi")

        frame_counter = Signal(8)
        previous_vsync = Signal()
        status_frame = Signal.like(status)
        m.d.dvi += previous_vsync.eq(dvi_tgen.ctrl.vsync)
        with m.If(dvi_tgen.ctrl.vsync & ~previous_vsync):
            m.d.dvi += [
                frame_counter.eq(frame_counter + 1),
                status_frame.eq(status_dvi),
            ]

        offset = 0

        def take(width):
            nonlocal offset
            value = status_frame[offset:offset + width]
            offset += width
            return value

        m.d.comb += [
            visualizer.x.eq(dvi_tgen.x),
            visualizer.y.eq(dvi_tgen.y),
            visualizer.frame.eq(frame_counter),
            visualizer.levels.eq(take(80)),
            visualizer.base.eq(take(64)),
            visualizer.effective.eq(take(64)),
            visualizer.pitch.eq(take(8)),
            visualizer.tilt.eq(take(8)),
            visualizer.cv.eq(take(32)),
            visualizer.master_asq.eq(take(8)),
            visualizer.utilization.eq(take(8)),
            visualizer.link_alive.eq(take(1)),
            visualizer.fault.eq(take(1)),
            visualizer.overrun.eq(take(1)),
            visualizer.activity.eq(take(1)),
        ]
        assert offset == len(status)

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
        "control_byte": (fragment.control_byte, None),
        "control_strobe": (fragment.control_strobe, None),
        "sample_index_debug": (fragment.sample_index_debug, None),
        "fault_debug": (fragment.fault_debug, None),
        "master_asq_debug": (fragment.master_asq_debug, None),
        "effective_master_debug": (fragment.effective_master_debug, None),
        "cv0_smoothed_debug": (fragment.cv0_smoothed_debug, None),
        "cv1_smoothed_debug": (fragment.cv1_smoothed_debug, None),
        "accepted_count_debug": (fragment.accepted_count_debug, None),
        "rejected_count_debug": (fragment.rejected_count_debug, None),
        "link_alive_debug": (fragment.link_alive_debug, None),
        "block_cycles_debug": (fragment.block_cycles_debug, None),
        "sample_cycles_debug": (fragment.sample_cycles_debug, None),
    }


def argparse_callback(parser):
    parser.add_argument(
        "--nextpnr-seed",
        type=int,
        default=None,
        help="Fix the nextpnr placement seed for reproducible QoR",
    )


def argparse_fragment(args):
    if args.name == "ADDITIVE-AV":
        args.name = "ADDITIVE-AV"
    return {}


if __name__ == "__main__":
    top_level_cli(
        AdditiveAVTop,
        sim_ports=simulation_ports,
        sim_harness="../../src/top/additive_av/sim.cpp",
        argparse_callback=argparse_callback,
        argparse_fragment=argparse_fragment,
    )
