# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Framed host control protocol: Python model and RTL decoder gates."""

from dataclasses import replace
import unittest

from amaranth.sim import Simulator

from tiliqua.additive import DEFAULT_CONTROL_STATE, tables as T
from tiliqua.additive.control import (
    AdditiveControlLink,
    ControlFrameDecoder,
    REJECT_CODES,
)
from tiliqua.additive.protocol import (
    FRAME_LENGTH,
    FrameDecoder,
    FrameError,
    PAYLOAD_LENGTH,
    corrupt,
    crc16_ccitt,
    decode_frame,
    encode_frame,
    encode_payload,
    frame_with_field,
    sequence_is_fresh,
)


GLASS = replace(
    DEFAULT_CONTROL_STATE.with_preset("GLASS"),
    root_midi=45,
    harmony=6,
    master=T.q15(0.34),
    detune_millicents=7_500,
    phase_spread=T.q15(0.82),
    evolution_amount=T.q15(0.72),
    evolution_rate_mhz=14,
    stereo_width=T.q15(0.94),
    morph_seconds=30,
    sub_focus=0,
    sub_octave=0,
    output_enabled=0,
)


def frame_fields(view_getter):
    """Read a decoded RTL payload view into a dict comparable to a state."""
    return {
        "root_midi": view_getter("root_midi"),
        "harmony": view_getter("harmony"),
        "master": view_getter("master"),
        "detune_millicents": view_getter("detune_millicents"),
        "phase_spread": view_getter("phase_spread"),
        "evolution_amount": view_getter("evolution_amount"),
        "evolution_rate_mhz": view_getter("evolution_rate_mhz"),
        "stereo_width": view_getter("stereo_width"),
        "morph_seconds": view_getter("morph_seconds"),
        "sub_focus": view_getter("sub_focus"),
    }


def state_fields(state):
    return {
        "root_midi": state.root_midi,
        "harmony": state.harmony,
        "master": state.master,
        "detune_millicents": state.detune_millicents,
        "phase_spread": state.phase_spread,
        "evolution_amount": state.evolution_amount,
        "evolution_rate_mhz": state.evolution_rate_mhz,
        "stereo_width": state.stereo_width,
        "morph_seconds": state.morph_seconds,
        "sub_focus": state.sub_focus,
    }


class ProtocolTests(unittest.TestCase):

    def test_frame_geometry_and_crc_check_value(self):
        self.assertEqual(FRAME_LENGTH, 45)
        self.assertEqual(PAYLOAD_LENGTH, 38)
        self.assertEqual(crc16_ccitt(b"123456789"), 0x29B1)
        frame = encode_frame(DEFAULT_CONTROL_STATE, 7)
        self.assertEqual(len(frame), FRAME_LENGTH)
        self.assertEqual(frame[:5], bytes((0xA5, 0x5A, 1, 7, 38)))
        self.assertNotIn(b"BITSTREAM", frame)

    def test_encode_decode_roundtrip_for_every_preset_and_flag(self):
        states = [
            DEFAULT_CONTROL_STATE,
            GLASS,
            replace(DEFAULT_CONTROL_STATE, output_enabled=0),
            replace(DEFAULT_CONTROL_STATE, sub_octave=0, root_midi=60, harmony=0),
            replace(DEFAULT_CONTROL_STATE, master=T.Q15_ONE, harmonic_levels=(T.Q15_ONE,) * 10),
            replace(DEFAULT_CONTROL_STATE, master=0, harmonic_levels=(0,) * 10,
                    detune_millicents=18_000, evolution_rate_mhz=80, morph_seconds=120),
        ]
        for sequence, state in enumerate(states):
            decoded, seq = decode_frame(encode_frame(state, sequence))
            self.assertEqual(decoded, state)
            self.assertEqual(seq, sequence)
            self.assertEqual(encode_payload(decoded), encode_payload(state))

    def test_decode_rejects_start_version_length_crc_and_bounds(self):
        frame = encode_frame(DEFAULT_CONTROL_STATE, 3)
        cases = {
            "start": corrupt(frame, 0),
            "version": corrupt(frame, 2),
            "length": corrupt(frame, 4),
            "crc": corrupt(frame, 10),
        }
        for reason, bad in cases.items():
            with self.assertRaises(FrameError) as context:
                decode_frame(bad)
            self.assertEqual(context.exception.reason, reason)
        with self.assertRaises(FrameError) as context:
            decode_frame(frame[:-1])
        self.assertEqual(context.exception.reason, "length")
        with self.assertRaises(FrameError) as context:
            decode_frame(frame + b"\x00")
        self.assertEqual(context.exception.reason, "length")
        for bad_field in (
            {"root_midi": 23}, {"root_midi": 61}, {"harmony": 7}, {"master": 32_768},
            {"detune_millicents": 18_001}, {"evolution_rate_mhz": 4},
            {"evolution_rate_mhz": 81}, {"morph_seconds": 1}, {"morph_seconds": 121},
            {"harmonic_levels": (0,) * 9 + (32_768,)}, {"sub_focus": 65_535},
        ):
            with self.assertRaises(FrameError) as context:
                decode_frame(frame_with_field(DEFAULT_CONTROL_STATE, 3, **bad_field))
            self.assertEqual(context.exception.reason, "bounds", bad_field)

    def test_sequence_freshness_window(self):
        self.assertTrue(sequence_is_fresh(0, None))
        self.assertTrue(sequence_is_fresh(1, 0))
        self.assertTrue(sequence_is_fresh(127, 0))
        self.assertFalse(sequence_is_fresh(128, 0))
        self.assertFalse(sequence_is_fresh(0, 0))
        self.assertFalse(sequence_is_fresh(255, 0))
        self.assertTrue(sequence_is_fresh(0, 255))
        self.assertTrue(sequence_is_fresh(5, 250))

    def test_streaming_decoder_resyncs_after_garbage_truncation_and_stale(self):
        decoder = FrameDecoder()
        good = encode_frame(DEFAULT_CONTROL_STATE, 1)
        second = encode_frame(GLASS, 2)
        stream = (
            b"\x00\xa5\x00garbage"
            + good
            + second[:20]  # truncated: the next frame's start is eaten
            + good
            + encode_frame(GLASS, 3)
            + encode_frame(GLASS, 3)  # duplicate -> stale
            + encode_frame(GLASS, 200)  # backwards -> stale
            + corrupt(encode_frame(GLASS, 4), 30)  # CRC
            + frame_with_field(GLASS, 5, harmony=9)  # bounds
            + encode_frame(DEFAULT_CONTROL_STATE, 6)
        )
        accepted = list(decoder.feed_bytes(stream))
        self.assertEqual([f.sequence for f in accepted], [1, 3, 6])
        self.assertEqual(accepted[1].state, GLASS)
        self.assertEqual(accepted[2].state, DEFAULT_CONTROL_STATE)
        self.assertEqual(decoder.rejected["stale"], 2)
        self.assertEqual(decoder.rejected["crc"], 2)  # truncated+good, and the corrupt one
        self.assertEqual(decoder.rejected["bounds"], 1)
        self.assertEqual(decoder.accepted, 3)

        decoder = FrameDecoder()
        list(decoder.feed_bytes(second[:20]))
        decoder.timeout()
        self.assertEqual(decoder.rejected["timeout"], 1)
        self.assertEqual([f.sequence for f in decoder.feed_bytes(second)], [2])


class RTLDecoderTests(unittest.TestCase):

    def run_bytes(self, dut, script):
        """Drive ``script`` (bytes or ('gap', cycles)) into the decoder."""
        accepted = []
        log = []

        def byte_sequence(ctx):
            # The sequence register latches on the same edge; read the
            # in-flight value from the payload byte stream instead.
            return ctx.get(dut.sequence_pending)

        async def bench(ctx):
            ctx.set(dut.i.valid, 0)
            for item in script:
                if isinstance(item, tuple):
                    for _ in range(item[1]):
                        await ctx.tick()
                    continue
                for byte in item:
                    ctx.set(dut.i.payload, byte)
                    ctx.set(dut.i.valid, 1)
                    # frame_valid is combinational in the accepting cycle.
                    if ctx.get(dut.frame_valid):
                        accepted.append(byte_sequence(ctx))
                    await ctx.tick()
                    ctx.set(dut.i.valid, 0)
                    await ctx.tick()
            for _ in range(4):
                await ctx.tick()
            log.append({
                "accepted_count": ctx.get(dut.accepted_count),
                "rejected_count": ctx.get(dut.rejected_count),
                "reject_reason": ctx.get(dut.reject_reason),
                "link_alive": ctx.get(dut.link_alive),
                "fields": frame_fields(lambda name: ctx.get(getattr(dut.frame, name))),
                "levels": [ctx.get(dut.frame.harmonic_levels[h]) for h in range(10)],
                "sub_octave": ctx.get(dut.frame.flags.sub_octave),
                "output_enabled": ctx.get(dut.frame.flags.output_enabled),
            })

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()
        return accepted, log[0]

    def test_rtl_decoder_boots_with_default_state_and_accepts_frames(self):
        dut = ControlFrameDecoder(byte_timeout_cycles=64, link_timeout_cycles=2_000)
        accepted, result = self.run_bytes(dut, [b""])
        self.assertEqual(result["fields"], state_fields(DEFAULT_CONTROL_STATE))
        self.assertEqual(result["levels"], list(DEFAULT_CONTROL_STATE.harmonic_levels))
        self.assertEqual(result["sub_octave"], 1)
        self.assertEqual(result["output_enabled"], 1)
        self.assertEqual(result["link_alive"], 0)

        model = FrameDecoder()
        stream = encode_frame(DEFAULT_CONTROL_STATE, 10) + encode_frame(GLASS, 11)
        expected = [f.sequence for f in model.feed_bytes(stream)]
        accepted, result = self.run_bytes(dut, [stream])
        self.assertEqual(accepted, expected)
        self.assertEqual(result["accepted_count"], 2)
        self.assertEqual(result["rejected_count"], 0)
        self.assertEqual(result["fields"], state_fields(GLASS))
        self.assertEqual(result["levels"], list(GLASS.harmonic_levels))
        self.assertEqual(result["sub_octave"], 0)
        self.assertEqual(result["output_enabled"], 0)
        self.assertEqual(result["link_alive"], 1)

    def test_rtl_decoder_rejects_like_the_model_and_keeps_last_good_frame(self):
        dut = ControlFrameDecoder(byte_timeout_cycles=64, link_timeout_cycles=2_000)
        model = FrameDecoder()
        good = encode_frame(DEFAULT_CONTROL_STATE, 1)
        stream = (
            b"\x00\xa5\x00"
            + good
            + encode_frame(GLASS, 2)[:20]
            + good
            + encode_frame(GLASS, 3)
            + encode_frame(GLASS, 3)
            + encode_frame(GLASS, 200)
            + corrupt(encode_frame(GLASS, 4), 30)
            + frame_with_field(GLASS, 5, harmony=9)
            + corrupt(encode_frame(GLASS, 5), 2)
            + corrupt(encode_frame(GLASS, 5), 4)
        )
        expected = [f.sequence for f in model.feed_bytes(stream)]
        accepted, result = self.run_bytes(dut, [stream])
        self.assertEqual(accepted, expected)
        self.assertEqual(result["accepted_count"], model.accepted)
        self.assertEqual(result["rejected_count"], sum(model.rejected.values()))
        self.assertEqual(result["reject_reason"], REJECT_CODES["length"])
        self.assertEqual(result["fields"], state_fields(GLASS))

        for reason, stream in (
            ("crc", corrupt(encode_frame(GLASS, 9), 30)),
            ("bounds", frame_with_field(GLASS, 9, master=40_000)),
            ("stale", encode_frame(GLASS, 3)),
            ("version", corrupt(encode_frame(GLASS, 9), 2)),
            ("start", b"\xa5\x00"),
        ):
            dut = ControlFrameDecoder(byte_timeout_cycles=64, link_timeout_cycles=2_000)
            accepted, result = self.run_bytes(dut, [encode_frame(GLASS, 3), stream])
            self.assertEqual(accepted, [3], reason)
            self.assertEqual(result["reject_reason"], REJECT_CODES[reason], reason)
            self.assertEqual(result["rejected_count"], 1, reason)
            self.assertEqual(result["fields"], state_fields(GLASS), reason)

    def test_rtl_decoder_timeout_recovers_a_truncated_frame(self):
        dut = ControlFrameDecoder(byte_timeout_cycles=64, link_timeout_cycles=400)
        partial = encode_frame(GLASS, 2)[:20]
        whole = encode_frame(GLASS, 2)
        accepted, result = self.run_bytes(dut, [partial, ("gap", 200), whole])
        self.assertEqual(accepted, [2])
        self.assertEqual(result["rejected_count"], 1)
        self.assertEqual(result["accepted_count"], 1)
        self.assertEqual(result["link_alive"], 1)
        accepted, result = self.run_bytes(dut, [("gap", 500)])
        self.assertEqual(result["link_alive"], 0)
        accepted, result = self.run_bytes(dut, [encode_frame(GLASS, 3)])
        self.assertEqual(accepted, [3])
        self.assertEqual(result["link_alive"], 1)

    def test_rtl_serial_link_decodes_115200_8n1_bits(self):
        clock_hz = 115_200 * 16
        dut = AdditiveControlLink(
            system_clk_hz=clock_hz, byte_timeout_cycles=16 * 40, link_timeout_cycles=16 * 4_000
        )
        divisor = dut.rx.divisor
        self.assertEqual(divisor, 16)
        frames = encode_frame(GLASS, 1) + encode_frame(DEFAULT_CONTROL_STATE, 2)
        accepted = []
        result = {}

        async def bench(ctx):
            rx = dut.rx.phy.i
            ctx.set(rx, 1)
            for _ in range(divisor * 4):
                await ctx.tick()
            for byte in frames:
                bits = [0] + [(byte >> bit) & 1 for bit in range(8)] + [1]
                for bit in bits:
                    ctx.set(rx, bit)
                    for _ in range(divisor):
                        if ctx.get(dut.frame_valid):
                            accepted.append(ctx.get(dut.decoder.sequence_pending))
                        await ctx.tick()
            for _ in range(divisor * 20):
                if ctx.get(dut.frame_valid):
                    accepted.append(ctx.get(dut.decoder.sequence_pending))
                await ctx.tick()
            result["fields"] = frame_fields(lambda name: ctx.get(getattr(dut.frame, name)))
            result["accepted"] = ctx.get(dut.accepted_count)
            result["rejected"] = ctx.get(dut.rejected_count)
            result["alive"] = ctx.get(dut.link_alive)

        sim = Simulator(dut)
        sim.add_clock(1 / clock_hz)
        sim.add_testbench(bench)
        sim.run()
        self.assertEqual(accepted, [1, 2])
        self.assertEqual(result["accepted"], 2)
        self.assertEqual(result["rejected"], 0)
        self.assertEqual(result["alive"], 1)
        self.assertEqual(result["fields"], state_fields(DEFAULT_CONTROL_STATE))


if __name__ == "__main__":
    unittest.main()
