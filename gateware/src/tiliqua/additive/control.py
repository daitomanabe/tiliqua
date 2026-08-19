# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Host control link: UART byte stream -> validated additive control frames."""

from amaranth import Cat, Const, Module, Mux, Signal, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.fifo import SyncFIFOBuffered
from amaranth.lib.wiring import In, Out
from amaranth_stdio.serial import AsyncSerialRX

from . import tables as T
from .protocol import (
    CRC_INITIAL,
    CRC_POLYNOMIAL,
    FLAG_OUTPUT_ENABLED,
    FLAG_SUB_OCTAVE,
    PAYLOAD_LENGTH,
    PROTOCOL_VERSION,
    REJECT_REASONS,
    SEQUENCE_WINDOW,
    START_BYTES,
    encode_payload,
)
from .reference import DEFAULT_CONTROL_STATE

CONTROL_BAUD_RATE = 115_200

# Byte-exact image of the 38-byte little-endian payload.
PAYLOAD_LAYOUT = data.StructLayout({
    "root_midi": unsigned(8),
    "harmony": unsigned(8),
    "flags": data.StructLayout({
        "sub_octave": unsigned(1),
        "output_enabled": unsigned(1),
        "reserved": unsigned(6),
    }),
    "morph_seconds": unsigned(8),
    "evolution_rate_mhz": unsigned(8),
    "reserved": unsigned(8),
    "master": unsigned(16),
    "harmonic_levels": data.ArrayLayout(unsigned(16), T.HARMONIC_COUNT),
    "detune_millicents": unsigned(16),
    "phase_spread": unsigned(16),
    "evolution_amount": unsigned(16),
    "stereo_width": unsigned(16),
    "sub_focus": unsigned(16),
})
assert PAYLOAD_LAYOUT.size == PAYLOAD_LENGTH * 8
assert FLAG_SUB_OCTAVE == 1 and FLAG_OUTPUT_ENABLED == 2

REJECT_CODES = {reason: index for index, reason in enumerate(REJECT_REASONS)}


def crc16_step(crc, byte):
    """Combinational CRC-16/CCITT update of ``crc`` by one byte."""
    value = crc ^ (byte << 8)
    for _ in range(8):
        shifted = (value << 1)[:16]
        value = Mux(value[15], shifted ^ CRC_POLYNOMIAL, shifted)
    return value


def default_payload_bits() -> int:
    """Power-on payload image: the specification default state."""
    return int.from_bytes(encode_payload(DEFAULT_CONTROL_STATE), "little")


class ControlFrameDecoder(wiring.Component):
    """Validate framed control bytes and expose the last accepted payload.

    The decoder never applies backpressure. ``frame`` holds the default
    specification state after reset and changes only when a complete frame
    passes start bytes, version, length, CRC, field bounds, and the sequence
    freshness rule; ``frame_valid`` pulses for one cycle on acceptance. An
    inter-byte timeout returns the machine to hunting so a truncated frame
    cannot swallow the following one. ``link_alive`` stays high while frames
    keep arriving within ``link_timeout_cycles``.
    """

    i: In(stream.Signature(unsigned(8)))
    frame: Out(PAYLOAD_LAYOUT)
    frame_valid: Out(1)
    sequence: Out(8)
    accepted_count: Out(16)
    rejected_count: Out(16)
    reject_reason: Out(3)
    link_alive: Out(1)

    def __init__(self, *, byte_timeout_cycles=1_200_000, link_timeout_cycles=120_000_000):
        if byte_timeout_cycles < 16 or link_timeout_cycles < 16:
            raise ValueError("timeouts must be at least 16 cycles")
        self.byte_timeout_cycles = byte_timeout_cycles
        self.link_timeout_cycles = link_timeout_cycles
        # Sequence byte of the frame currently being parsed (simulation probe).
        self.sequence_pending = Signal(8)
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        payload = Signal(PAYLOAD_LENGTH * 8)
        payload_view = data.View(PAYLOAD_LAYOUT, payload)
        committed = Signal(PAYLOAD_LENGTH * 8, init=default_payload_bits())
        m.d.comb += self.frame.as_value().eq(committed)

        byte = Signal(8)
        strobe = Signal()
        crc = Signal(16)
        crc_high = Signal(8)
        sequence = self.sequence_pending
        last_sequence = Signal(8)
        have_last = Signal()
        index = Signal(range(PAYLOAD_LENGTH))
        byte_timer = Signal(range(self.byte_timeout_cycles + 1))
        link_timer = Signal(range(self.link_timeout_cycles + 1))
        hunting = Signal()

        m.d.comb += [
            self.i.ready.eq(1),
            strobe.eq(self.i.valid),
            byte.eq(self.i.payload),
            self.frame_valid.eq(0),
        ]

        # Bounds check on the assembled payload image.
        in_bounds = Signal()
        level_checks = [
            payload_view.harmonic_levels[h] <= T.Q15_ONE for h in range(T.HARMONIC_COUNT)
        ]
        bounds_ok = (
            (payload_view.root_midi >= T.ROOT_MIDI_MIN)
            & (payload_view.root_midi <= T.ROOT_MIDI_MAX)
            & (payload_view.harmony < T.HARMONY_COUNT)
            & (payload_view.morph_seconds >= T.MORPH_SECONDS_MIN)
            & (payload_view.morph_seconds <= T.MORPH_SECONDS_MAX)
            & (payload_view.evolution_rate_mhz >= T.EVOLUTION_RATE_MHZ_MIN)
            & (payload_view.evolution_rate_mhz <= T.EVOLUTION_RATE_MHZ_MAX)
            & (payload_view.master <= T.Q15_ONE)
            & (payload_view.detune_millicents <= T.DETUNE_MILLICENTS_MAX)
            & (payload_view.phase_spread <= T.Q15_ONE)
            & (payload_view.evolution_amount <= T.Q15_ONE)
            & (payload_view.stereo_width <= T.Q15_ONE)
            & (payload_view.sub_focus <= T.Q15_ONE)
        )
        for check in level_checks:
            bounds_ok = bounds_ok & check
        m.d.comb += in_bounds.eq(bounds_ok)

        delta = Signal(8)
        fresh = Signal()
        m.d.comb += [
            delta.eq(sequence - last_sequence),
            fresh.eq(~have_last | ((delta >= 1) & (delta <= SEQUENCE_WINDOW))),
        ]

        timed_out = Signal()
        m.d.comb += timed_out.eq(
            ~hunting & ~strobe & (byte_timer == self.byte_timeout_cycles)
        )

        def reject(reason):
            m.d.sync += [
                self.rejected_count.eq(self.rejected_count + 1),
                self.reject_reason.eq(REJECT_CODES[reason]),
            ]
            m.next = "START1"

        def in_frame_state(name, body):
            """A frame state: inter-byte timeout first, then the byte handler."""
            with m.State(name):
                with m.If(timed_out):
                    reject("timeout")
                with m.Elif(strobe):
                    body()

        with m.FSM() as fsm:
            m.d.comb += hunting.eq(fsm.ongoing("START1"))
            with m.State("START1"):
                with m.If(strobe & (byte == START_BYTES[0])):
                    m.next = "START2"

            def start2():
                with m.If(byte == START_BYTES[1]):
                    m.next = "VERSION"
                with m.Elif(byte != START_BYTES[0]):
                    reject("start")
            in_frame_state("START2", start2)

            def version():
                with m.If(byte == PROTOCOL_VERSION):
                    m.d.sync += crc.eq(crc16_step(Const(CRC_INITIAL, 16), byte))
                    m.next = "SEQUENCE"
                with m.Else():
                    reject("version")
            in_frame_state("VERSION", version)

            def sequence_byte():
                m.d.sync += [
                    sequence.eq(byte),
                    crc.eq(crc16_step(crc, byte)),
                ]
                m.next = "LENGTH"
            in_frame_state("SEQUENCE", sequence_byte)

            def length():
                with m.If(byte == PAYLOAD_LENGTH):
                    m.d.sync += [
                        crc.eq(crc16_step(crc, byte)),
                        index.eq(0),
                    ]
                    m.next = "PAYLOAD"
                with m.Else():
                    reject("length")
            in_frame_state("LENGTH", length)

            def payload_byte():
                m.d.sync += [
                    payload.word_select(index, 8).eq(byte),
                    crc.eq(crc16_step(crc, byte)),
                ]
                with m.If(index == PAYLOAD_LENGTH - 1):
                    m.next = "CRC_HIGH"
                with m.Else():
                    m.d.sync += index.eq(index + 1)
            in_frame_state("PAYLOAD", payload_byte)

            def crc_high_byte():
                m.d.sync += crc_high.eq(byte)
                m.next = "CRC_LOW"
            in_frame_state("CRC_HIGH", crc_high_byte)

            def crc_low_byte():
                with m.If(Cat(byte, crc_high) != crc):
                    reject("crc")
                with m.Elif(~in_bounds):
                    reject("bounds")
                with m.Elif(~fresh):
                    reject("stale")
                with m.Else():
                    m.d.sync += [
                        committed.eq(payload),
                        last_sequence.eq(sequence),
                        have_last.eq(1),
                        self.sequence.eq(sequence),
                        self.accepted_count.eq(self.accepted_count + 1),
                        self.reject_reason.eq(REJECT_CODES["none"]),
                        link_timer.eq(0),
                    ]
                    m.d.comb += self.frame_valid.eq(1)
                    m.next = "START1"
            in_frame_state("CRC_LOW", crc_low_byte)

        # Inter-byte timer: held at zero while hunting or on every byte.
        with m.If(strobe | hunting | timed_out):
            m.d.sync += byte_timer.eq(0)
        with m.Else():
            m.d.sync += byte_timer.eq(byte_timer + 1)

        # Link watchdog.
        with m.If(link_timer != self.link_timeout_cycles):
            with m.If(~self.frame_valid):
                m.d.sync += link_timer.eq(link_timer + 1)
        m.d.comb += self.link_alive.eq(
            have_last & (link_timer != self.link_timeout_cycles)
        )
        return m


class SerialByteRx(wiring.Component):
    """115200 8N1 receiver feeding a shallow FIFO that is never back-pressured."""

    o: Out(stream.Signature(unsigned(8)))

    def __init__(self, *, system_clk_hz, pins=None, baud_rate=CONTROL_BAUD_RATE, rx_depth=8):
        self.divisor = int(system_clk_hz // baud_rate)
        self.phy = AsyncSerialRX(divisor=self.divisor, pins=pins)
        self.rx_fifo = SyncFIFOBuffered(width=8, depth=rx_depth)
        super().__init__()

    def elaborate(self, platform):
        m = Module()
        m.submodules.phy = self.phy
        m.submodules.rx_fifo = self.rx_fifo
        m.d.comb += [
            self.rx_fifo.w_data.eq(self.phy.data),
            self.rx_fifo.w_en.eq(self.phy.rdy),
            self.phy.ack.eq(self.rx_fifo.w_rdy),
        ]
        wiring.connect(m, self.rx_fifo.r_stream, wiring.flipped(self.o))
        return m


class AdditiveControlLink(wiring.Component):
    """UART pins -> :class:`ControlFrameDecoder`, for hardware tops."""

    frame: Out(PAYLOAD_LAYOUT)
    frame_valid: Out(1)
    sequence: Out(8)
    accepted_count: Out(16)
    rejected_count: Out(16)
    reject_reason: Out(3)
    link_alive: Out(1)

    def __init__(self, *, system_clk_hz, pins=None, byte_timeout_cycles=None,
                 link_timeout_cycles=None):
        system_clk_hz = int(system_clk_hz)
        self.rx = SerialByteRx(system_clk_hz=system_clk_hz, pins=pins)
        self.decoder = ControlFrameDecoder(
            byte_timeout_cycles=byte_timeout_cycles or system_clk_hz // 50,
            link_timeout_cycles=link_timeout_cycles or system_clk_hz * 2,
        )
        super().__init__()

    def elaborate(self, platform):
        m = Module()
        m.submodules.rx = rx = self.rx
        m.submodules.decoder = decoder = self.decoder
        wiring.connect(m, rx.o, decoder.i)
        m.d.comb += [
            self.frame.eq(decoder.frame),
            self.frame_valid.eq(decoder.frame_valid),
            self.sequence.eq(decoder.sequence),
            self.accepted_count.eq(decoder.accepted_count),
            self.rejected_count.eq(decoder.rejected_count),
            self.reject_reason.eq(decoder.reject_reason),
            self.link_alive.eq(decoder.link_alive),
        ]
        return m
