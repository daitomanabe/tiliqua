# Copyright (c) 2026 Tiliqua contributors
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Small, simulation-friendly building blocks for deterministic synth voices."""

import enum

from amaranth import Const, Module, Mux, Signal, signed, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.memory import Memory
from amaranth.lib.wiring import In, Out

from . import ASQ, asq_from_volts
from .dslx_adsr import DSLXADSR
from .dslx_voice import DSLXVoice
from .misc import GateDetector
from .vca import VCA


PHASE_BITS = 32


class Waveform(enum.IntEnum):
    SAW = 0
    TRIANGLE = 1
    SQUARE = 2
    SILENCE = 3


class DyadicRounding(enum.Enum):
    FLOOR = "floor"
    NEAREST_AWAY = "nearest_away"


def phase_increment(frequency_hz, sample_rate, phase_bits=PHASE_BITS):
    """Return the nearest phase increment for a phase-accumulator NCO."""

    if frequency_hz < 0:
        raise ValueError("frequency_hz must be non-negative")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if phase_bits <= 0:
        raise ValueError("phase_bits must be positive")

    increment = round(float(frequency_hz) * (1 << phase_bits) / sample_rate)
    if increment >= (1 << phase_bits):
        raise ValueError("frequency_hz must be below sample_rate")
    return increment


def midi_note_frequency(note):
    """Return the equal-tempered frequency for a MIDI note number."""

    if not 0 <= note <= 127:
        raise ValueError("note must be in the range 0..127")
    return 440.0 * 2.0 ** ((note - 69) / 12.0)


def midi_note_phase_increment(note, sample_rate, phase_bits=PHASE_BITS):
    """Return a phase increment for one equal-tempered MIDI note."""

    return phase_increment(
        midi_note_frequency(note),
        sample_rate,
        phase_bits=phase_bits,
    )


PITCH_CV_LAYOUT = data.StructLayout({
    "phase_increment": unsigned(PHASE_BITS),
    "note": unsigned(7),
})


class QuantizedPitchCV(wiring.Component):
    """Convert an ASQ CV to a semitone-quantized 1 V/octave pitch.

    Tiliqua ASQ inputs nominally use 4000 counts per volt. The default
    semitone conversion approximates ``counts * 12 / 4000`` with the
    shift-add expression ``counts * 49 / 16384``. A compile-time zero offset
    and counts-per-octave value can adapt this without spending a DSP
    multiplier on control-rate pitch conversion.

    This intentionally small first implementation is quantized to semitones;
    continuous pitch bend and per-device calibration belong in a later stage.
    """

    i: In(stream.Signature(ASQ))
    o: Out(stream.Signature(PITCH_CV_LAYOUT))

    def __init__(
        self,
        *,
        base_note=48,
        sample_rate=48_000,
        zero_cv_counts=0,
        counts_per_octave=4_000,
    ):
        if not 0 <= base_note <= 127:
            raise ValueError("base_note must be in the range 0..127")
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if not -32768 <= zero_cv_counts <= 32767:
            raise ValueError("zero_cv_counts must fit in a signed 16-bit input")
        if not 1_000 <= counts_per_octave <= 16_000:
            raise ValueError("counts_per_octave must be in the range 1000..16000")
        self._base_note = base_note
        self._sample_rate = sample_rate
        self._zero_cv_counts = zero_cv_counts
        self._counts_per_octave = counts_per_octave
        self._semitone_numerator = round(
            12 * (1 << 14) / counts_per_octave
        )
        self._increments = [
            midi_note_phase_increment(note, sample_rate)
            for note in range(128)
        ]
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        m.submodules.pitch_rom = pitch_rom = Memory(
            shape=unsigned(PHASE_BITS),
            depth=128,
            init=self._increments,
        )
        pitch_rport = pitch_rom.read_port()

        cv_latched = Signal(signed(16))
        corrected_counts = Signal(signed(17))
        magnitude = Signal(16)
        product = Signal(24)
        semitone_magnitude = Signal(8)
        semitone_offset = Signal(signed(9))
        semitone_latched = Signal(signed(9))
        note_wide = Signal(signed(10))
        note_clamped = Signal(7)
        note_latched = Signal(7)

        shifted_terms = [
            magnitude << bit
            for bit in range(self._semitone_numerator.bit_length())
            if self._semitone_numerator & (1 << bit)
        ]
        product_expression = shifted_terms[0]
        for term in shifted_terms[1:]:
            product_expression = product_expression + term

        m.d.comb += [
            corrected_counts.eq(cv_latched - self._zero_cv_counts),
            magnitude.eq(Mux(
                corrected_counts < 0,
                -corrected_counts,
                corrected_counts,
            )),
            product.eq(product_expression),
            semitone_magnitude.eq((product + 8192) >> 14),
            semitone_offset.eq(Mux(
                corrected_counts < 0,
                -semitone_magnitude,
                semitone_magnitude,
            )),
            note_wide.eq(self._base_note + semitone_latched),
            note_clamped.eq(Mux(
                note_wide < 0,
                0,
                Mux(note_wide > 127, 127, note_wide),
            )),
            pitch_rport.en.eq(1),
        ]

        with m.FSM():
            with m.State("WAIT"):
                m.d.comb += self.i.ready.eq(1)
                with m.If(self.i.valid):
                    m.d.sync += cv_latched.eq(self.i.payload.as_value())
                    m.next = "SCALE"

            # Keep constant-multiply/rounding and note clamp on separate
            # cycles. At 48 kHz this latency is negligible, and the split
            # avoids placing the whole CV exponential-address path inside one
            # 60 MHz timing arc.
            with m.State("SCALE"):
                m.d.sync += semitone_latched.eq(semitone_offset)
                m.next = "ADDRESS"

            with m.State("ADDRESS"):
                m.d.sync += [
                    pitch_rport.addr.eq(note_clamped),
                    note_latched.eq(note_clamped),
                ]
                m.next = "READ"

            # The ROM has a synchronous read port. Its output becomes valid
            # on the edge leaving this state.
            with m.State("READ"):
                m.next = "OUTPUT"

            with m.State("OUTPUT"):
                m.d.comb += [
                    self.o.valid.eq(1),
                    self.o.payload.phase_increment.eq(pitch_rport.data),
                    self.o.payload.note.eq(note_latched),
                ]
                with m.If(self.o.ready):
                    m.next = "WAIT"

        return m


class BasicNCO(wiring.Component):
    """Backpressure-safe full-scale saw, triangle and square oscillator.

    One input transfer advances one audio sample. Gain is deliberately kept
    outside this oscillator: selecting one waveform and applying one VCA costs
    one multiplier, whereas scaling all three waveforms here can cost three.
    """

    i: In(stream.Signature(unsigned(PHASE_BITS)))
    o: Out(stream.Signature(data.StructLayout({
        "saw": ASQ,
        "triangle": ASQ,
        "square": ASQ,
    })))
    phase: Out(unsigned(PHASE_BITS))

    def elaborate(self, platform):
        m = Module()

        phase = Signal(PHASE_BITS)
        saw_full = Signal(signed(16))
        triangle_folded = Signal(16)
        triangle_full = Signal(signed(16))
        square_full = Signal(signed(16))

        # Flip the sign bit so phase zero maps to the negative end of the
        # signed output range rather than zero.
        m.d.comb += [
            saw_full.eq(phase[16:32] ^ Const(0x8000, 16)),
            triangle_folded.eq(Mux(phase[31], ~phase[15:31], phase[15:31])),
            triangle_full.eq(triangle_folded ^ Const(0x8000, 16)),
            square_full.eq(Mux(
                phase[31],
                Const(32767, signed(16)),
                Const(-32767, signed(16)),
            )),
            self.o.payload.saw.as_value().eq(saw_full),
            self.o.payload.triangle.as_value().eq(triangle_full),
            self.o.payload.square.as_value().eq(square_full),
            self.o.valid.eq(self.i.valid),
            self.i.ready.eq(self.o.ready),
            self.phase.eq(phase),
        ]

        with m.If(self.i.valid & self.o.ready):
            m.d.sync += phase.eq(phase + self.i.payload)

        return m


class BasicVoice(wiring.Component):
    """NCO followed by one resource-aware DSLX waveform/drive stage."""

    i: In(stream.Signature(data.StructLayout({
        "phase_increment": unsigned(PHASE_BITS),
        "waveform": unsigned(2),
        "drive": unsigned(2),
    })))
    o: Out(stream.Signature(ASQ))
    phase: Out(unsigned(PHASE_BITS))

    def elaborate(self, platform):
        m = Module()

        m.submodules.nco = nco = BasicNCO()
        m.submodules.shaper = shaper = DSLXVoice()

        m.d.comb += [
            nco.i.valid.eq(self.i.valid),
            nco.i.payload.eq(self.i.payload.phase_increment),
            self.i.ready.eq(nco.i.ready),
            shaper.i.valid.eq(nco.o.valid),
            nco.o.ready.eq(shaper.i.ready),
            shaper.i.payload.saw.eq(nco.o.payload.saw),
            shaper.i.payload.triangle.eq(nco.o.payload.triangle),
            shaper.i.payload.square.eq(nco.o.payload.square),
            shaper.i.payload.waveform.eq(self.i.payload.waveform),
            shaper.i.payload.drive.eq(self.i.payload.drive),
            self.o.valid.eq(shaper.o.valid),
            self.o.payload.eq(shaper.o.payload),
            shaper.o.ready.eq(self.o.ready),
            self.phase.eq(nco.phase),
        ]

        return m


PLAYABLE_VOICE_INPUT_LAYOUT = data.StructLayout({
    "phase_increment": unsigned(PHASE_BITS),
    "waveform": unsigned(2),
    "drive": unsigned(2),
    "gate": unsigned(1),
    "attack_step": unsigned(16),
    "decay_step": unsigned(16),
    "sustain_level": unsigned(16),
    "release_step": unsigned(16),
})

PLAYABLE_VOICE_OUTPUT_LAYOUT = data.StructLayout({
    "sample": ASQ,
    "envelope": unsigned(16),
    "phase": unsigned(3),
    "gate": unsigned(1),
})


class PlayableVoice(wiring.Component):
    """One oscillator, one DSLX ADSR and one shared post-selection VCA.

    The oscillator and envelope streams are forked and joined atomically, so
    neither side can advance alone under backpressure. Waveform selection and
    drive happen before the VCA; all waveforms therefore share one multiplier.
    """

    i: In(stream.Signature(PLAYABLE_VOICE_INPUT_LAYOUT))
    o: Out(stream.Signature(PLAYABLE_VOICE_OUTPUT_LAYOUT))
    oscillator_phase: Out(unsigned(PHASE_BITS))

    def elaborate(self, platform):
        m = Module()

        m.submodules.voice = voice = BasicVoice()
        m.submodules.adsr = adsr = DSLXADSR()
        # Both operands are normalized audio values, so the 16-bit ASQ input
        # type is sufficient. The underlying ECP5 DSP tile is unchanged, but
        # operand latches and routing are narrower than the generic Q3.15 VCA.
        m.submodules.vca = vca = VCA(itype=ASQ)

        # Atomic stream fork: valid is only presented to a branch when the
        # other branch can accept the same transaction.
        m.d.comb += [
            voice.i.valid.eq(self.i.valid & adsr.i.ready),
            adsr.i.valid.eq(self.i.valid & voice.i.ready),
            self.i.ready.eq(voice.i.ready & adsr.i.ready),
            voice.i.payload.phase_increment.eq(self.i.payload.phase_increment),
            voice.i.payload.waveform.eq(self.i.payload.waveform),
            voice.i.payload.drive.eq(self.i.payload.drive),
            adsr.i.payload.gate.eq(self.i.payload.gate),
            adsr.i.payload.attack_step.eq(self.i.payload.attack_step),
            adsr.i.payload.decay_step.eq(self.i.payload.decay_step),
            adsr.i.payload.sustain_level.eq(self.i.payload.sustain_level),
            adsr.i.payload.release_step.eq(self.i.payload.release_step),
        ]

        envelope_gain = Signal(ASQ)
        m.d.comb += [
            # ADSR is unsigned Q0.16. The VCA uses signed Q3.15, so dropping
            # the least-significant bit maps full scale to +0.99997.
            envelope_gain.as_value().eq(adsr.o.payload.level >> 1),
            vca.i.valid.eq(voice.o.valid & adsr.o.valid),
            voice.o.ready.eq(vca.i.ready & adsr.o.valid),
            adsr.o.ready.eq(vca.i.ready & voice.o.valid),
            vca.i.payload[0].eq(voice.o.payload),
            vca.i.payload[1].eq(envelope_gain),
        ]

        envelope_latched = Signal(16)
        phase_latched = Signal(3)
        gate_latched = Signal()
        with m.If(vca.i.valid & vca.i.ready):
            m.d.sync += [
                envelope_latched.eq(adsr.o.payload.level),
                phase_latched.eq(adsr.o.payload.phase),
                gate_latched.eq(adsr.o.payload.gate),
            ]

        m.d.comb += [
            self.o.valid.eq(vca.o.valid),
            vca.o.ready.eq(self.o.ready),
            self.o.payload.sample.eq(vca.o.payload),
            self.o.payload.envelope.eq(envelope_latched),
            self.o.payload.phase.eq(phase_latched),
            self.o.payload.gate.eq(gate_latched),
            self.oscillator_phase.eq(voice.phase),
        ]

        return m


CV_PLAYABLE_VOICE_INPUT_LAYOUT = data.StructLayout({
    "pitch_cv": ASQ,
    "waveform": unsigned(2),
    "drive": unsigned(2),
    "gate": unsigned(1),
    "attack_step": unsigned(16),
    "decay_step": unsigned(16),
    "sustain_level": unsigned(16),
    "release_step": unsigned(16),
})

CV_PLAYABLE_VOICE_OUTPUT_LAYOUT = data.StructLayout({
    "sample": ASQ,
    "envelope": unsigned(16),
    "phase": unsigned(3),
    "note": unsigned(7),
    "gate": unsigned(1),
    "pitch_cv": ASQ,
})


class CVPlayableVoice(wiring.Component):
    """Serialized, alignment-safe 1 V/oct CV voice for the first tutorial.

    Control transactions are deliberately serialized until the corresponding
    audio result is consumed. This costs only a few 60 MHz clocks per 48 kHz
    sample, while making pitch/note/envelope alignment straightforward to
    inspect in simulation before introducing a deeper control pipeline.
    """

    i: In(stream.Signature(CV_PLAYABLE_VOICE_INPUT_LAYOUT))
    o: Out(stream.Signature(CV_PLAYABLE_VOICE_OUTPUT_LAYOUT))
    oscillator_phase: Out(unsigned(PHASE_BITS))

    def __init__(
        self,
        *,
        base_note=48,
        sample_rate=48_000,
        zero_cv_counts=0,
        counts_per_octave=4_000,
    ):
        self._base_note = base_note
        self._sample_rate = sample_rate
        self._zero_cv_counts = zero_cv_counts
        self._counts_per_octave = counts_per_octave
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        m.submodules.pitch = pitch = QuantizedPitchCV(
            base_note=self._base_note,
            sample_rate=self._sample_rate,
            zero_cv_counts=self._zero_cv_counts,
            counts_per_octave=self._counts_per_octave,
        )
        m.submodules.voice = voice = PlayableVoice()

        controls = Signal(PLAYABLE_VOICE_INPUT_LAYOUT)
        note_latched = Signal(7)
        pitch_cv_latched = Signal(ASQ)

        m.d.comb += [
            voice.i.payload.phase_increment.eq(
                pitch.o.payload.phase_increment
            ),
            pitch.i.payload.eq(self.i.payload.pitch_cv),
            voice.i.payload.waveform.eq(controls.waveform),
            voice.i.payload.drive.eq(controls.drive),
            voice.i.payload.gate.eq(controls.gate),
            voice.i.payload.attack_step.eq(controls.attack_step),
            voice.i.payload.decay_step.eq(controls.decay_step),
            voice.i.payload.sustain_level.eq(controls.sustain_level),
            voice.i.payload.release_step.eq(controls.release_step),
            self.o.payload.sample.eq(voice.o.payload.sample),
            self.o.payload.envelope.eq(voice.o.payload.envelope),
            self.o.payload.phase.eq(voice.o.payload.phase),
            self.o.payload.note.eq(note_latched),
            self.o.payload.gate.eq(voice.o.payload.gate),
            self.o.payload.pitch_cv.eq(pitch_cv_latched),
            self.oscillator_phase.eq(voice.oscillator_phase),
        ]

        with m.FSM():
            with m.State("WAIT-INPUT"):
                m.d.comb += [
                    pitch.i.valid.eq(self.i.valid),
                    self.i.ready.eq(pitch.i.ready),
                ]
                with m.If(self.i.valid & pitch.i.ready):
                    m.d.sync += [
                        controls.waveform.eq(self.i.payload.waveform),
                        controls.drive.eq(self.i.payload.drive),
                        controls.gate.eq(self.i.payload.gate),
                        controls.attack_step.eq(self.i.payload.attack_step),
                        controls.decay_step.eq(self.i.payload.decay_step),
                        controls.sustain_level.eq(
                            self.i.payload.sustain_level
                        ),
                        controls.release_step.eq(self.i.payload.release_step),
                        pitch_cv_latched.eq(self.i.payload.pitch_cv),
                    ]
                    m.next = "WAIT-PITCH"

            with m.State("WAIT-PITCH"):
                m.d.comb += [
                    voice.i.valid.eq(pitch.o.valid),
                    pitch.o.ready.eq(voice.i.ready),
                ]
                with m.If(pitch.o.valid & voice.i.ready):
                    m.d.sync += note_latched.eq(pitch.o.payload.note)
                    m.next = "WAIT-OUTPUT"

            with m.State("WAIT-OUTPUT"):
                m.d.comb += [
                    self.o.valid.eq(voice.o.valid),
                    voice.o.ready.eq(self.o.ready),
                ]
                with m.If(voice.o.valid & self.o.ready):
                    m.next = "WAIT-INPUT"

        return m


class CVSynthCore(wiring.Component):
    """Map four calibrated Tiliqua inputs to one playable diagnostic voice.

    Input 0 is pitch CV and input 1 is a conventional Eurorack gate. Outputs
    are voice, envelope, aligned 5 V gate, and full-wave voice magnitude.
    """

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))
    note: Out(unsigned(7))
    envelope: Out(unsigned(16))
    gate: Out(unsigned(1))
    oscillator_phase: Out(unsigned(PHASE_BITS))

    def __init__(
        self,
        *,
        sample_rate=48_000,
        base_note=48,
        zero_cv_counts=0,
        counts_per_octave=4_000,
        waveform=Waveform.TRIANGLE,
        drive=0,
        gate_on_volts=4.0,
        gate_off_volts=2.0,
        attack_step=2_048,
        decay_step=64,
        sustain_level=49_152,
        release_step=256,
    ):
        if not 0 <= gate_off_volts < gate_on_volts <= 8.0:
            raise ValueError(
                "require 0 <= gate_off_volts < gate_on_volts <= 8.0"
            )
        if not 0 <= int(waveform) <= 3:
            raise ValueError("waveform must be in the range 0..3")
        if not 0 <= drive <= 3:
            raise ValueError("drive must be in the range 0..3")
        for name, value in (
            ("attack_step", attack_step),
            ("decay_step", decay_step),
            ("sustain_level", sustain_level),
            ("release_step", release_step),
        ):
            if not 0 <= value <= 65535:
                raise ValueError(f"{name} must be in the range 0..65535")

        self._sample_rate = sample_rate
        self._base_note = base_note
        self._zero_cv_counts = zero_cv_counts
        self._counts_per_octave = counts_per_octave
        self._waveform = int(waveform)
        self._drive = drive
        self._gate_on = asq_from_volts(gate_on_volts)
        self._gate_off = asq_from_volts(gate_off_volts)
        self._attack_step = attack_step
        self._decay_step = decay_step
        self._sustain_level = sustain_level
        self._release_step = release_step
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        m.submodules.gate_detector = gate_detector = GateDetector(
            threshold_on=self._gate_on,
            threshold_off=self._gate_off,
        )
        m.submodules.voice = voice = CVPlayableVoice(
            base_note=self._base_note,
            sample_rate=self._sample_rate,
            zero_cv_counts=self._zero_cv_counts,
            counts_per_octave=self._counts_per_octave,
        )

        m.d.comb += [
            gate_detector.i.valid.eq(self.i.valid),
            gate_detector.i.payload.eq(self.i.payload[1]),
            self.i.ready.eq(gate_detector.i.ready),
            voice.i.valid.eq(gate_detector.o.valid),
            gate_detector.o.ready.eq(voice.i.ready),
            voice.i.payload.pitch_cv.eq(self.i.payload[0]),
            voice.i.payload.waveform.eq(self._waveform),
            voice.i.payload.drive.eq(self._drive),
            voice.i.payload.gate.eq(gate_detector.o.payload),
            voice.i.payload.attack_step.eq(self._attack_step),
            voice.i.payload.decay_step.eq(self._decay_step),
            voice.i.payload.sustain_level.eq(self._sustain_level),
            voice.i.payload.release_step.eq(self._release_step),
        ]

        sample = voice.o.payload.sample.as_value()
        magnitude = Signal(signed(16))
        m.d.comb += [
            magnitude.eq(Mux(
                sample == -32768,
                32767,
                Mux(sample < 0, -sample, sample),
            )),
            self.o.valid.eq(voice.o.valid),
            voice.o.ready.eq(self.o.ready),
            self.o.payload[0].eq(voice.o.payload.sample),
            self.o.payload[1].as_value().eq(
                voice.o.payload.envelope >> 1
            ),
            self.o.payload[2].eq(Mux(
                voice.o.payload.gate,
                asq_from_volts(5.0),
                0,
            )),
            self.o.payload[3].as_value().eq(magnitude),
            self.note.eq(voice.o.payload.note),
            self.envelope.eq(voice.o.payload.envelope),
            self.gate.eq(voice.o.payload.gate),
            self.oscillator_phase.eq(voice.oscillator_phase),
        ]

        return m


class SynthControlTestSource(wiring.Component):
    """Backpressure-safe pitch CV and gate sequence for the live synth top."""

    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))
    sample_index: Out(unsigned(32))

    def __init__(
        self,
        *,
        pitch_shift=11,
        gate_shift=10,
        zero_cv_counts=0,
        counts_per_octave=4_000,
    ):
        if not 1 <= gate_shift < pitch_shift <= 30:
            raise ValueError("require 1 <= gate_shift < pitch_shift <= 30")
        if not -32768 <= zero_cv_counts <= 32767:
            raise ValueError("zero_cv_counts must fit signed 16-bit ASQ")
        if not 1_000 <= counts_per_octave <= 16_000:
            raise ValueError("counts_per_octave must be in the range 1000..16000")
        if not -32768 <= zero_cv_counts + counts_per_octave <= 32767:
            raise ValueError("one-octave test CV must fit signed 16-bit ASQ")
        self._pitch_shift = pitch_shift
        self._gate_shift = gate_shift
        self._zero_cv_counts = zero_cv_counts
        self._counts_per_octave = counts_per_octave
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        sample_index = Signal(32)
        m.d.comb += [
            self.o.valid.eq(1),
            self.o.payload[0].as_value().eq(Mux(
                sample_index[self._pitch_shift],
                self._zero_cv_counts + self._counts_per_octave,
                self._zero_cv_counts,
            )),
            self.o.payload[1].eq(Mux(
                ~sample_index[self._gate_shift],
                asq_from_volts(5.0),
                0,
            )),
            self.o.payload[2].as_value().eq(Mux(
                ~sample_index[self._gate_shift],
                12_000,
                -12_000,
            )),
            self.o.payload[3].as_value().eq(0),
            self.sample_index.eq(sample_index),
        ]

        with m.If(self.o.valid & self.o.ready):
            m.d.sync += sample_index.eq(sample_index + 1)

        return m


class DyadicGain(wiring.Component):
    """Multiplier-free constant gain with an explicit signed rounding policy."""

    i: In(stream.Signature(ASQ))
    o: Out(stream.Signature(ASQ))

    def __init__(
        self,
        *,
        numerator,
        fractional_bits,
        rounding=DyadicRounding.NEAREST_AWAY,
    ):
        if not 1 <= fractional_bits <= 15:
            raise ValueError("fractional_bits must be in the range 1..15")
        if not 0 <= numerator <= (1 << fractional_bits):
            raise ValueError("gain must be a dyadic fraction in the range 0..1")
        self._numerator = numerator
        self._fractional_bits = fractional_bits
        self._rounding = DyadicRounding(rounding)
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        width = 17 + self._fractional_bits
        gain_sum = Signal(signed(width))
        shifted_terms = [
            self.i.payload.as_value() << bit
            for bit in range(self._fractional_bits + 1)
            if self._numerator & (1 << bit)
        ]
        if shifted_terms:
            gain_expression = shifted_terms[0]
            for term in shifted_terms[1:]:
                gain_expression = gain_expression + term
        else:
            gain_expression = Const(0, signed(width))

        scaled = Signal(signed(width))
        m.d.comb += gain_sum.eq(gain_expression)
        if self._rounding is DyadicRounding.FLOOR:
            m.d.comb += scaled.eq(gain_sum >> self._fractional_bits)
        else:
            magnitude = Signal(width)
            rounded_magnitude = Signal(width)
            m.d.comb += [
                magnitude.eq(Mux(gain_sum < 0, -gain_sum, gain_sum)),
                rounded_magnitude.eq(
                    (magnitude + (1 << (self._fractional_bits - 1)))
                    >> self._fractional_bits
                ),
                scaled.eq(Mux(
                    gain_sum < 0,
                    -rounded_magnitude,
                    rounded_magnitude,
                )),
            ]

        m.d.comb += [
            self.o.payload.as_value().eq(scaled),
            self.o.valid.eq(self.i.valid),
            self.i.ready.eq(self.o.ready),
        ]

        return m


class SynthTestSource(wiring.Component):
    """Deterministic four-channel stimulus for simulation and bench tests.

    Channel layout:

    * 0: triangle test tone
    * 1: constant threshold CV for an envelope/gate detector
    * 2: positive/negative burst timing marker
    * 3: full-wave-rectified triangle magnitude
    """

    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))
    phase: Out(unsigned(PHASE_BITS))
    sample_index: Out(unsigned(32))

    def __init__(
        self,
        *,
        sample_rate=48_000,
        tone_hz=440.0,
        waveform=Waveform.TRIANGLE,
        drive=0,
        amplitude_numerator=3,
        amplitude_fractional_bits=3,
        threshold_counts=4_096,
        burst_shift=10,
        attack_step=2_048,
        decay_step=64,
        sustain_level=49_152,
        release_step=256,
    ):
        if not 1 <= amplitude_fractional_bits <= 15:
            raise ValueError("amplitude_fractional_bits must be in the range 1..15")
        if not 0 < amplitude_numerator < (1 << amplitude_fractional_bits):
            raise ValueError("amplitude must be a dyadic fraction in the range 0..1")
        if not 0 <= threshold_counts <= 32767:
            raise ValueError("threshold_counts must be in the range 0..32767")
        if not 1 <= burst_shift <= 30:
            raise ValueError("burst_shift must be in the range 1..30")
        for name, value in (
            ("attack_step", attack_step),
            ("decay_step", decay_step),
            ("sustain_level", sustain_level),
            ("release_step", release_step),
        ):
            if not 0 <= value <= 65535:
                raise ValueError(f"{name} must be in the range 0..65535")
        if not 0 <= int(waveform) <= 3:
            raise ValueError("waveform must be in the range 0..3")
        if not 0 <= drive <= 3:
            raise ValueError("drive must be in the range 0..3")

        self._phase_increment = phase_increment(tone_hz, sample_rate)
        self._waveform = int(waveform)
        self._drive = drive
        self._amplitude_numerator = amplitude_numerator
        self._amplitude_fractional_bits = amplitude_fractional_bits
        self._amplitude_counts = (
            32768 * amplitude_numerator >> amplitude_fractional_bits
        )
        self._threshold_counts = threshold_counts
        self._burst_shift = burst_shift
        self._attack_step = attack_step
        self._decay_step = decay_step
        self._sustain_level = sustain_level
        self._release_step = release_step
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        m.submodules.voice = voice = PlayableVoice()
        m.submodules.output_gain = output_gain = DyadicGain(
            numerator=self._amplitude_numerator,
            fractional_bits=self._amplitude_fractional_bits,
        )
        sample_index = Signal(32)
        burst_active = Signal()
        selected = output_gain.o.payload.as_value()
        magnitude = Signal(signed(16))

        m.d.comb += [
            voice.i.valid.eq(1),
            voice.i.payload.phase_increment.eq(self._phase_increment),
            voice.i.payload.waveform.eq(self._waveform),
            voice.i.payload.drive.eq(self._drive),
            voice.i.payload.gate.eq(burst_active),
            voice.i.payload.attack_step.eq(self._attack_step),
            voice.i.payload.decay_step.eq(self._decay_step),
            voice.i.payload.sustain_level.eq(self._sustain_level),
            voice.i.payload.release_step.eq(self._release_step),
            burst_active.eq(~sample_index[self._burst_shift]),
            output_gain.i.valid.eq(voice.o.valid),
            output_gain.i.payload.eq(voice.o.payload.sample),
            voice.o.ready.eq(output_gain.i.ready),
            output_gain.o.ready.eq(self.o.ready),
            self.o.valid.eq(output_gain.o.valid),
            self.o.payload[0].as_value().eq(selected),
            self.o.payload[1].as_value().eq(
                Const(self._threshold_counts, signed(16))
            ),
            self.o.payload[2].as_value().eq(Mux(
                burst_active,
                Const(self._amplitude_counts, signed(16)),
                Const(-self._amplitude_counts, signed(16)),
            )),
            magnitude.eq(Mux(selected < 0, -selected, selected)),
            self.o.payload[3].as_value().eq(magnitude),
            self.phase.eq(voice.oscillator_phase),
            self.sample_index.eq(sample_index),
        ]

        with m.If(self.o.valid & self.o.ready):
            m.d.sync += sample_index.eq(sample_index + 1)

        return m
