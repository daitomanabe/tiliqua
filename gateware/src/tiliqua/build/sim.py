# Copyright (c) 2024 S. Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""Utilities for simulating Tiliqua designs."""

import glob
import os
import shutil
import subprocess
import tempfile

from amaranth              import *
from amaranth.back         import verilog
from amaranth.build        import *
from amaranth.lib          import wiring
from amaranth.lib.wiring   import In, Out

from .types import FirmwareLocation

class FakeTiliquaDomainGenerator(Elaboratable):
    """ Fake Clock generator for Tiliqua platform. """

    def __init__(self, *, clock_frequencies=None, clock_signal_name=None):
        pass

    def elaborate(self, platform):
        m = Module()

        m.domains.sync   = ClockDomain()
        m.domains.audio  = ClockDomain()
        m.domains.dvi    = ClockDomain()
        m.domains.fast   = ClockDomain()

        return m

class FakePSRAMSimulationInterface(wiring.Signature):
    def __init__(self):
        super().__init__({
            "idle":           Out(unsigned(1)),
            "read_ready":     Out(unsigned(1)),
            "write_ready":    Out(unsigned(1)),
            "address_ptr":    Out(unsigned(32)),
            "read_data_view":  In(unsigned(32)),
            "write_data":     Out(unsigned(32)),
        })

# Main purpose of using this custom platform instead of
# simply None is to track extra files added to the build.
class VerilatorPlatform():
    def __init__(self, hw_platform):
        self.files = {}
        self.ila = False
        self.psram_id = hw_platform.psram_id
        self.psram_registers = hw_platform.psram_registers

    def add_file(self, file_name, contents):
        self.files[file_name] = contents

def is_hw(platform):
    # assumption: anything that inherits from Platform is a
    # real hardware platform. Anything else isn't.
    # is there a better way of doing this?
    return isinstance(platform, Platform)

def soc_simulation_ports(fragment):
    return {
        "clk_sync":       (ClockSignal("sync"),                          None),
        "rst_sync":       (ResetSignal("sync"),                          None),
        "clk_dvi":        (ClockSignal("dvi"),                           None),
        "rst_dvi":        (ResetSignal("dvi"),                           None),
        "clk_audio":      (ClockSignal("audio"),                         None),
        "rst_audio":      (ResetSignal("audio"),                         None),
        "i2s_sdin1":      (fragment.pmod0.pins.i2s.sdin1,                None),
        "i2s_sdout1":     (fragment.pmod0.pins.i2s.sdout1,               None),
        "i2s_lrck":       (fragment.pmod0.pins.i2s.lrck,                 None),
        "i2s_bick":       (fragment.pmod0.pins.i2s.bick,                 None),
        "uart0_w_data":   (fragment.uart0._tx_data.f.data.w_data,        None),
        "uart0_w_stb":    (fragment.uart0._tx_data.f.data.w_stb,         None),
        "address_ptr":    (fragment.psram_periph.simif.address_ptr,      None),
        "read_data_view": (fragment.psram_periph.simif.read_data_view,   None),
        "write_data":     (fragment.psram_periph.simif.write_data,       None),
        "read_ready":     (fragment.psram_periph.simif.read_ready,       None),
        "write_ready":    (fragment.psram_periph.simif.write_ready,      None),
        "idle":           (fragment.psram_periph.simif.idle,             None),
        "spiflash_addr":  (fragment.spiflash_periph.spi_mmap.simif_addr, None),
        "spiflash_data":  (fragment.spiflash_periph.spi_mmap.simif_data, None),
        "dvi_de":         (fragment.fb.simif.de,                    None),
        "dvi_vsync":      (fragment.fb.simif.vsync,                 None),
        "dvi_hsync":      (fragment.fb.simif.hsync,                 None),
        "dvi_r":          (fragment.fb.simif.r,                     None),
        "dvi_g":          (fragment.fb.simif.g,                     None),
        "dvi_b":          (fragment.fb.simif.b,                     None),
    }

def simulate(fragment, ports, harness, hw_platform, clock_settings, tracing=False, archiver=None):

    build_dst = "build"
    dst = f"{build_dst}/tiliqua_soc.v"
    print(f"write verilog implementation of 'tiliqua_soc' to '{dst}'...")

    sim_platform = VerilatorPlatform(hw_platform)

    os.makedirs(build_dst, exist_ok=True)
    with open(dst, "w") as f:
        f.write(verilog.convert(
            fragment,
            platform=sim_platform,
            ports=ports
            ))

    # Check modeline requirement early
    if hasattr(fragment, "fb") and fragment.fb.fixed_modeline is None:
        raise ValueError("Simulation requires specifying a static video mode with `--modeline`")

    has_soc = hasattr(fragment, "fw_location")

    if archiver and has_soc:
        # Generate fake `bootinfo` (expected by user bitstreams with an SoC, the
        # bootloader creates and saves this at a defined position in PSRAM)

        bootinfo_path = os.path.join(build_dst, "bootinfo.bin")
        manifest_path = os.path.join(build_dst, "manifest.json")

        manifest = archiver.write_manifest()
        manifest.write_to_path(manifest_path)

        dvi_clk_hz = clock_settings.frequencies.dvi
        h_active = fragment.fb.fixed_modeline.h_active
        v_active = fragment.fb.fixed_modeline.v_active
        print(f"Generating bootinfo for simulation: {h_active}x{v_active}@{dvi_clk_hz}Hz")
        subprocess.check_call([
            "cargo", "run",
            "--manifest-path", "src/rs/bootinfo_gen/Cargo.toml",
            "--",
            "--manifest", manifest_path,
            "--h-active", str(h_active),
            "--v-active", str(v_active),
            "--fixed-pclk-hz", str(dvi_clk_hz),
            "--output", bootinfo_path
        ], env=os.environ)
        print(f"Generated bootinfo: {bootinfo_path}")

    # Write all additional files added with platform.add_file()
    # to build/ directory, so verilator build can find them.
    for file in sim_platform.files:
        with open(os.path.join("build", file), "w") as f:
            f.write(sim_platform.files[file])

    tracing_flags = ["--trace-fst", "--trace-structs"] if tracing else []

    if hasattr(fragment, "fb") or hasattr(fragment, "dvi_tgen"):
        dvi_clk_hz = clock_settings.frequencies.dvi
        dvi_h_active = clock_settings.modeline.h_active
        dvi_v_active = clock_settings.modeline.v_active
        video_cflags = [
           "-CFLAGS", f"-DDVI_H_ACTIVE={dvi_h_active}",
           "-CFLAGS", f"-DDVI_V_ACTIVE={dvi_v_active}",
           "-CFLAGS", f"-DDVI_CLK_HZ={dvi_clk_hz}",
        ]
    else:
        video_cflags = []

    if hasattr(fragment, "psram_periph"):
        psram_cflags = [
           "-CFLAGS", f"-DPSRAM_SIM=1",
       ]
    else:
        psram_cflags = []

    firmware_cflags = []
    bootinfo_cflags = []
    if has_soc:
        firmware_cflags += [
           "-CFLAGS", f"-DFIRMWARE_BIN_PATH=\\\"{fragment.firmware_bin_path}\\\"",
        ]
        bootinfo_offset = fragment.bootinfo_base - fragment.psram_base
        bootinfo_cflags += [
            "-CFLAGS", f"-DBOOTINFO_BIN_PATH=\\\"{bootinfo_path}\\\"",
            "-CFLAGS", f"-DBOOTINFO_OFFSET={hex(bootinfo_offset)}",
        ]
        match fragment.fw_location:
            case FirmwareLocation.PSRAM:
                firmware_cflags += [
                    "-CFLAGS", f"-DPSRAM_FW_OFFSET={hex(fragment.fw_base - fragment.psram_base)}",
                ]
            case FirmwareLocation.SPIFlash:
                firmware_cflags += [
                    "-CFLAGS", f"-DSPIFLASH_FW_OFFSET={hex(fragment.fw_base - fragment.spiflash_base)}",
                ]

    clock_sync_hz = clock_settings.frequencies.sync
    audio_clk_hz = clock_settings.frequencies.audio
    fast_clk_hz = clock_settings.frequencies.fast
    regression_cflags = []
    if hasattr(fragment, "sim_regression_name"):
        regression_cflags += [
            "-CFLAGS",
            f"-DREGRESSION_NAME=\\\"{fragment.sim_regression_name}\\\"",
        ]
    if hasattr(fragment, "sim_metrics_filename"):
        regression_cflags += [
            "-CFLAGS",
            f"-DMETRICS_FILENAME=\\\"{fragment.sim_metrics_filename}\\\"",
        ]

    original_workdir = os.getcwd()
    logical_verilator_dst = os.path.abspath("build/obj_dir")
    staging_context = None

    # GNU Make files emitted by Verilator do not reliably escape whitespace in
    # source paths. Stage only the compiled inputs in a short temporary path;
    # generated frames, traces and metrics still land in the project directory.
    if any(character.isspace() for character in original_workdir):
        staging_context = tempfile.TemporaryDirectory(
            prefix="tiliqua-verilator-"
        )
        verilator_workdir = staging_context.name
        verilator_dst = os.path.join(verilator_workdir, "obj_dir")

        harness_candidates = [
            os.path.abspath(harness),
            os.path.abspath(os.path.join(logical_verilator_dst, harness)),
        ]
        harness_source = next(
            (candidate for candidate in harness_candidates if os.path.isfile(candidate)),
            None,
        )
        if harness_source is None:
            raise FileNotFoundError(f"simulation harness not found: {harness}")

        staged_harness = os.path.join(
            verilator_workdir, os.path.basename(harness_source)
        )
        staged_verilog = os.path.join(verilator_workdir, "tiliqua_soc.v")
        shutil.copy(harness_source, staged_harness)
        shutil.copy(dst, staged_verilog)

        staged_extra_files = []
        for file in sim_platform.files:
            if file.endswith(".sv") or file.endswith(".v"):
                source = os.path.join("build", file)
                destination = os.path.join(verilator_workdir, os.path.basename(file))
                shutil.copy(source, destination)
                staged_extra_files.append(os.path.basename(destination))

        verilator_mdir = "obj_dir"
        verilator_harness = os.path.basename(staged_harness)
        verilator_verilog = os.path.basename(staged_verilog)
        verilator_extra_files = staged_extra_files
    else:
        verilator_workdir = original_workdir
        verilator_dst = logical_verilator_dst
        shutil.rmtree(verilator_dst, ignore_errors=True)
        verilator_mdir = verilator_dst
        verilator_harness = harness
        verilator_verilog = dst
        verilator_extra_files = [
            file for file in sim_platform.files
            if file.endswith(".sv") or file.endswith(".v")
        ]

    # Copy shared testbench headers somewhere Verilator's build
    # process can see them.
    os.makedirs(verilator_dst)
    testbench_utils = glob.glob("./src/tb_cpp/*.h")
    for header in testbench_utils:
        shutil.copy(header, verilator_dst)

    print(f"verilate '{dst}' into C++ binary...")
    subprocess.check_call(["verilator",
                           "-Wno-COMBDLY",
                           "-Wno-CASEINCOMPLETE",
                           "-Wno-CASEOVERLAP",
                           "-Wno-WIDTHEXPAND",
                           "-Wno-WIDTHTRUNC",
                           "-Wno-TIMESCALEMOD",
                           "-Wno-PINMISSING",
                           "-Wno-ASCRANGE",
                           "-Wno-UNSIGNED",
                           "-Wno-CMPCONST",
                           "-cc"] + tracing_flags + [
                           "--exe",
                           "--Mdir", f"{verilator_mdir}",
                           "-Ibuild",
                           "--build",
                           "-j", "0",
                           "-CFLAGS", f"-DSYNC_CLK_HZ={clock_sync_hz}",
                           "-CFLAGS", f"-DAUDIO_CLK_HZ={audio_clk_hz}",
                           "-CFLAGS", f"-DFAST_CLK_HZ={fast_clk_hz}",
                          ] + video_cflags + psram_cflags + firmware_cflags + bootinfo_cflags + regression_cflags + [
                           verilator_harness,
                           verilator_verilog,
                          ] + verilator_extra_files,
                          env=os.environ,
                          cwd=verilator_workdir)

    print(f"run verilated binary '{verilator_dst}/Vtiliqua_soc'...")
    subprocess.check_call([f"{verilator_dst}/Vtiliqua_soc"],
                          env=os.environ,
                          cwd=original_workdir)

    if staging_context is not None:
        staging_context.cleanup()

    print(f"done.")
