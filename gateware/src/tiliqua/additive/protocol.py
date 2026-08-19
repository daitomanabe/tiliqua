# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Framed host control protocol for the additive instrument.

One frame carries the complete UI base state (:class:`AdditiveControlState`)
over the FLASH/DEBUG CDC UART at 115200 8N1, host to FPGA only:

.. code-block:: text

   offset  size  field
   0       1     start byte 0xA5
   1       1     start byte 0x5A
   2       1     protocol version (1)
   3       1     sequence number, wraps modulo 256
   4       1     payload length (38)
   5       38    payload, little-endian fields, see PAYLOAD_FIELDS
   43      2     CRC-16/CCITT-FALSE over bytes 2..42, big-endian

The receiver accepts a frame only when the start bytes, version, length, CRC,
every field bound, and the sequence freshness rule all pass; it then commits
the payload atomically at the next control-block boundary. The pure-Python
:class:`FrameDecoder` below is the normative model of the RTL decoder.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import struct
from typing import Iterable, Iterator

from . import tables as T
from .reference import AdditiveControlState, AdditiveStateError

START_BYTES = b"\xa5\x5a"
PROTOCOL_VERSION = 1
PAYLOAD_LENGTH = 38
HEADER_LENGTH = len(START_BYTES) + 3
CRC_LENGTH = 2
FRAME_LENGTH = HEADER_LENGTH + PAYLOAD_LENGTH + CRC_LENGTH
CRC_POLYNOMIAL = 0x1021
CRC_INITIAL = 0xFFFF
SEQUENCE_WINDOW = 127
FLAG_SUB_OCTAVE = 0x01
FLAG_OUTPUT_ENABLED = 0x02

# (name, struct format, offset) -- little-endian, matching the RTL StructLayout.
PAYLOAD_FIELDS = (
    ("root_midi", "B", 0),
    ("harmony", "B", 1),
    ("flags", "B", 2),
    ("morph_seconds", "B", 3),
    ("evolution_rate_mhz", "B", 4),
    ("reserved", "B", 5),
    ("master", "<H", 6),
    ("harmonic_levels", "<10H", 8),
    ("detune_millicents", "<H", 28),
    ("phase_spread", "<H", 30),
    ("evolution_amount", "<H", 32),
    ("stereo_width", "<H", 34),
    ("sub_focus", "<H", 36),
)
PAYLOAD_STRUCT = struct.Struct("<BBBBBBH10HHHHHH")
assert PAYLOAD_STRUCT.size == PAYLOAD_LENGTH


class FrameError(ValueError):
    """Raised for a malformed, stale, or out-of-range frame."""

    def __init__(self, reason: str, message: str | None = None):
        super().__init__(message or reason)
        self.reason = reason


REJECT_REASONS = (
    "none",
    "start",
    "version",
    "length",
    "crc",
    "bounds",
    "stale",
    "timeout",
)


def crc16_ccitt(data: bytes | Iterable[int], crc: int = CRC_INITIAL) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no xorout."""
    for byte in data:
        crc ^= (byte & 0xFF) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ CRC_POLYNOMIAL) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def encode_payload(state: AdditiveControlState) -> bytes:
    state.validate()
    flags = (FLAG_SUB_OCTAVE if state.sub_octave else 0) | (
        FLAG_OUTPUT_ENABLED if state.output_enabled else 0
    )
    return PAYLOAD_STRUCT.pack(
        state.root_midi,
        state.harmony,
        flags,
        state.morph_seconds,
        state.evolution_rate_mhz,
        0,
        state.master,
        *state.harmonic_levels,
        state.detune_millicents,
        state.phase_spread,
        state.evolution_amount,
        state.stereo_width,
        state.sub_focus,
    )


def decode_payload(payload: bytes) -> AdditiveControlState:
    """Unpack 38 payload bytes; raises :class:`FrameError` ``bounds``."""
    if len(payload) != PAYLOAD_LENGTH:
        raise FrameError("length", f"payload is {len(payload)} bytes")
    fields = PAYLOAD_STRUCT.unpack(payload)
    (
        root_midi, harmony, flags, morph_seconds, evolution_rate_mhz, _reserved,
        master, *rest,
    ) = fields
    levels = tuple(rest[:10])
    detune, phase_spread, evolution_amount, stereo_width, sub_focus = rest[10:]
    state = AdditiveControlState(
        root_midi=root_midi,
        harmony=harmony,
        sub_octave=int(bool(flags & FLAG_SUB_OCTAVE)),
        output_enabled=int(bool(flags & FLAG_OUTPUT_ENABLED)),
        master=master,
        harmonic_levels=levels,
        detune_millicents=detune,
        phase_spread=phase_spread,
        evolution_amount=evolution_amount,
        evolution_rate_mhz=evolution_rate_mhz,
        stereo_width=stereo_width,
        morph_seconds=morph_seconds,
        sub_focus=sub_focus,
    )
    try:
        return state.validate()
    except AdditiveStateError as error:
        raise FrameError("bounds", str(error)) from error


def encode_frame(state: AdditiveControlState, sequence: int) -> bytes:
    if not 0 <= sequence <= 255:
        raise ValueError("sequence must fit in one byte")
    body = bytes((PROTOCOL_VERSION, sequence, PAYLOAD_LENGTH)) + encode_payload(state)
    return START_BYTES + body + crc16_ccitt(body).to_bytes(2, "big")


def decode_frame(frame: bytes) -> tuple[AdditiveControlState, int]:
    """Decode one complete frame; raises :class:`FrameError` on any fault."""
    if len(frame) < HEADER_LENGTH:
        raise FrameError("length", "frame shorter than its header")
    if frame[:2] != START_BYTES:
        raise FrameError("start")
    if frame[2] != PROTOCOL_VERSION:
        raise FrameError("version")
    if frame[4] != PAYLOAD_LENGTH or len(frame) != FRAME_LENGTH:
        raise FrameError("length")
    body = frame[2:HEADER_LENGTH + PAYLOAD_LENGTH]
    if crc16_ccitt(body) != int.from_bytes(frame[-2:], "big"):
        raise FrameError("crc")
    return decode_payload(frame[HEADER_LENGTH:HEADER_LENGTH + PAYLOAD_LENGTH]), frame[3]


def sequence_is_fresh(sequence: int, last_sequence: int | None) -> bool:
    if last_sequence is None:
        return True
    delta = (sequence - last_sequence) & 0xFF
    return 1 <= delta <= SEQUENCE_WINDOW


@dataclass
class DecodedFrame:
    sequence: int
    state: AdditiveControlState


class FrameDecoder:
    """Byte-at-a-time decoder mirroring the RTL state machine.

    Feed bytes with :meth:`feed`; accepted frames are returned. Rejections are
    counted by reason and the decoder resynchronises on the next start byte.
    Call :meth:`timeout` when the inter-byte timer expires.
    """

    def __init__(self):
        self.state = "start1"
        self.sequence = 0
        self.remaining = 0
        self.payload = bytearray()
        self.crc = CRC_INITIAL
        self.crc_high = 0
        self.last_sequence: int | None = None
        self.accepted = 0
        self.rejected = {reason: 0 for reason in REJECT_REASONS if reason != "none"}
        self.last_reject = "none"

    def _reject(self, reason: str) -> None:
        self.rejected[reason] += 1
        self.last_reject = reason
        self.state = "start1"

    def timeout(self) -> None:
        if self.state != "start1":
            self._reject("timeout")

    def feed(self, byte: int) -> DecodedFrame | None:
        byte &= 0xFF
        if self.state == "start1":
            if byte == START_BYTES[0]:
                self.state = "start2"
            return None
        if self.state == "start2":
            if byte == START_BYTES[1]:
                self.state = "version"
            elif byte != START_BYTES[0]:
                self._reject("start")
            return None
        if self.state == "version":
            if byte != PROTOCOL_VERSION:
                self._reject("version")
                return None
            self.crc = crc16_ccitt((byte,))
            self.state = "sequence"
            return None
        if self.state == "sequence":
            self.sequence = byte
            self.crc = crc16_ccitt((byte,), self.crc)
            self.state = "length"
            return None
        if self.state == "length":
            if byte != PAYLOAD_LENGTH:
                self._reject("length")
                return None
            self.crc = crc16_ccitt((byte,), self.crc)
            self.payload = bytearray()
            self.remaining = PAYLOAD_LENGTH
            self.state = "payload"
            return None
        if self.state == "payload":
            self.payload.append(byte)
            self.crc = crc16_ccitt((byte,), self.crc)
            self.remaining -= 1
            if self.remaining == 0:
                self.state = "crc_high"
            return None
        if self.state == "crc_high":
            self.crc_high = byte
            self.state = "crc_low"
            return None
        if self.state == "crc_low":
            received = (self.crc_high << 8) | byte
            if received != self.crc:
                self._reject("crc")
                return None
            try:
                decoded = decode_payload(bytes(self.payload))
            except FrameError:
                self._reject("bounds")
                return None
            if not sequence_is_fresh(self.sequence, self.last_sequence):
                self._reject("stale")
                return None
            self.last_sequence = self.sequence
            self.accepted += 1
            self.last_reject = "none"
            self.state = "start1"
            return DecodedFrame(self.sequence, decoded)
        raise AssertionError(f"unknown decoder state {self.state}")

    def feed_bytes(self, data: bytes) -> Iterator[DecodedFrame]:
        for byte in data:
            frame = self.feed(byte)
            if frame is not None:
                yield frame


def corrupt(frame: bytes, offset: int, mask: int = 0x01) -> bytes:
    """Return a copy of ``frame`` with one byte XOR-ed (test helper)."""
    mutated = bytearray(frame)
    mutated[offset] ^= mask
    return bytes(mutated)


def frame_with_field(state: AdditiveControlState, sequence: int, **changes) -> bytes:
    """Encode ``state`` with raw field overrides that bypass validation."""
    payload = bytearray(encode_payload(replace(state)))
    for name, value in changes.items():
        for field_name, fmt, offset in PAYLOAD_FIELDS:
            if field_name == name:
                struct.pack_into(fmt, payload, offset, *(
                    value if isinstance(value, (tuple, list)) else (value,)
                ))
                break
        else:
            raise KeyError(name)
    body = bytes((PROTOCOL_VERSION, sequence, PAYLOAD_LENGTH)) + bytes(payload)
    return START_BYTES + body + crc16_ccitt(body).to_bytes(2, "big")
