# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Frozen constants and lookup tables for the Tiliqua additive engine.

Everything in this module is a pure function of the specification in
``docs/additive_specification.rst``. The integer reference model and the RTL
both consume these tables, so a change here is a change to the normative
digital behaviour and must be accompanied by a specification update.
"""

from __future__ import annotations

import math

SAMPLE_RATE = 48_000
SYNC_CYCLES_PER_SAMPLE = 1_250

TONE_COUNT = 5
HARMONIC_COUNT = 10
VOICE_COUNT = 20
GROUP_COUNT = TONE_COUNT * HARMONIC_COUNT
OSCILLATOR_COUNT = GROUP_COUNT * VOICE_COUNT

PHASE_BITS = 32
PHASE_MASK = (1 << PHASE_BITS) - 1
SINE_INDEX_BITS = 12
SINE_TABLE_SIZE = 1 << SINE_INDEX_BITS
SINE_SHIFT = PHASE_BITS - SINE_INDEX_BITS
Q15_ONE = 32_767

CONTROL_BLOCK_SAMPLES = 128
BLOCKS_PER_SECOND = SAMPLE_RATE / CONTROL_BLOCK_SAMPLES

ASQ_PER_VOLT = 4_000
OUTPUT_CEILING_ASQ = 8_000  # +/-2.0 V on every calibrated output
GROUP_SUM_SHIFT = 3
MAX_GROUP_SUM = VOICE_COUNT * Q15_ONE
MAX_GROUP_SUM18 = math.ceil(MAX_GROUP_SUM / (1 << GROUP_SUM_SHIFT))
MIX_FRACTION_BITS = 13  # normalized mix ``y`` is signed Q3.13, 1.0 = 8192
MIX_ONE = 1 << MIX_FRACTION_BITS
MIX_MAX = (1 << 15) - 1
OUTPUT_SHIFT = 19
AIR_STEM_TRIM_SHIFT = 2  # the decorrelated upper stem is lifted by +12 dB before limiting
OUTPUT_SHIFTS = (OUTPUT_SHIFT, OUTPUT_SHIFT, OUTPUT_SHIFT, OUTPUT_SHIFT - AIR_STEM_TRIM_SHIFT)
GAIN_UNIT = round(MIX_ONE * (1 << OUTPUT_SHIFT) / (MAX_GROUP_SUM / (1 << GROUP_SUM_SHIFT)))
GAIN_BITS = 17
NORMALIZATION_SHIFT = 16
NORMALIZATION_FLOOR = 256  # minimum RMS root (Q1.15 units) before dividing

LIMITER_KNEE = 0.70
LIMITER_KNEE_Q13 = round(LIMITER_KNEE * MIX_ONE)
LIMITER_TABLE_BITS = 10
LIMITER_TABLE_SIZE = 1 << LIMITER_TABLE_BITS
LIMITER_INDEX_SHIFT = 5
SUB_MIX_TABLE_BITS = 8
SUB_MIX_TABLE_SIZE = 1 << SUB_MIX_TABLE_BITS

LOW_STEM_CUTOFF_HZ = 120.0
AIR_STEM_CUTOFF_HZ = 600.0

HARMONY_NAMES = (
    "OPEN FIFTH",
    "MINOR 9 + 13",
    "MINOR 9",
    "MAJOR 9",
    "SUSPENDED",
    "QUARTAL",
    "LYDIAN AIR",
)
HARMONY_INTERVALS = (
    (0, 7, 12, 19, 24),
    (0, 3, 10, 14, 21),
    (0, 3, 7, 10, 14),
    (0, 4, 7, 11, 14),
    (0, 2, 7, 9, 14),
    (0, 5, 10, 15, 20),
    (0, 4, 7, 11, 18),
)
HARMONY_COUNT = len(HARMONY_INTERVALS)
DEFAULT_HARMONY = 1  # C minor 9 add 13

ROOT_MIDI_MIN = 24
ROOT_MIDI_MAX = 60
DEFAULT_ROOT_MIDI = 36  # C2; the sub-octave tone sounds C1 (32.7 Hz)
NOTE_TABLE_SIZE = 128

DETUNE_MILLICENTS_MAX = 18_000
EVOLUTION_RATE_MHZ_MIN = 5
EVOLUTION_RATE_MHZ_MAX = 80
MORPH_SECONDS_MIN = 2
MORPH_SECONDS_MAX = 120
MORPH_TABLE_SIZE = 128


def q15(value: float) -> int:
    """Round a unit-range float to the unsigned Q1.15 parameter format."""
    return max(0, min(Q15_ONE, round(value * Q15_ONE)))


def q15_signed(value: float) -> int:
    return max(-Q15_ONE, min(Q15_ONE, round(value * Q15_ONE)))


SPECTRUM_PRESETS = {
    "PURE": (1.00, 0.018, 0.012, 0.009, 0.007, 0.006, 0.005, 0.004, 0.003, 0.003),
    "WARM": (1.00, 0.52, 0.34, 0.24, 0.18, 0.14, 0.11, 0.085, 0.068, 0.052),
    "HOLLOW": (1.00, 0.035, 0.50, 0.028, 0.30, 0.022, 0.20, 0.018, 0.13, 0.015),
    "GLASS": (0.72, 0.24, 0.14, 0.42, 0.11, 0.30, 0.09, 0.22, 0.075, 0.16),
}
SPECTRUM_PRESETS_Q15 = {
    name: tuple(q15(level) for level in levels)
    for name, levels in SPECTRUM_PRESETS.items()
}

TONE_WEIGHTS = (1.00, 0.84, 0.74, 0.64, 0.56)
TONE_WEIGHTS_Q15 = tuple(q15(weight) for weight in TONE_WEIGHTS)
TONE_POSITION_PAN_Q15 = tuple(
    q15_signed((tone - 2) / 2 * 0.72) for tone in range(TONE_COUNT)
)
HARMONIC_PAN_UNIT_Q15 = q15(0.07)

WEIGHT_MOTION_BASE_Q15 = q15(0.78)
WEIGHT_MOTION_DEPTH_Q15 = q15(0.22)
WEIGHT_MOTION_FLOOR_Q15 = q15(0.35)
PAN_MOTION_DEPTH_Q15 = q15(0.16)
SUB_FOCUS_GAIN_Q15 = q15(0.68)
CV_OFFSET_DEPTH_Q15 = 16_384  # +/-1 V (or +2 V on IN 0) adds +/-0.5 to a unit parameter

PITCH_OFFSET_RANGE_Q10 = round(2 / 12 * 1024)  # +/-1 V -> +/-2 semitones
TILT_OCTAVES_AT_TOP_HARMONIC = 2.0
TILT_STEP_Q10 = tuple(
    round(TILT_OCTAVES_AT_TOP_HARMONIC * 1024 * index / (HARMONIC_COUNT - 1))
    for index in range(HARMONIC_COUNT)
)

DRIFT_CENTS = 2.2
DRIFT_RATIO_Q16 = round((2 ** (DRIFT_CENTS / 1200) - 1) * (1 << 16))
DETUNE_SPACING_Q40 = round(
    math.log(2) / 1200 / 1000 / (VOICE_COUNT - 1) * (1 << 40)
)
DETUNE_SPACING_SHIFT = 40
SPREAD_UNIT = round((1 << PHASE_BITS) / (2 * (VOICE_COUNT - 1)))

GOLDEN_ANGLE = 2.399_963_229_728_653
INITIAL_SPACING_RADIANS = 0.013_713
GROUP_OFFSET_RADIANS = 1.618_033_988_75
GROUP_OFFSET_STEP = round(GROUP_OFFSET_RADIANS / (2 * math.pi) * (1 << PHASE_BITS))
TIMBRE_OFFSET_STEP = round(GROUP_OFFSET_STEP * 0.73)

LFO_INCREMENT_PER_MHZ = round((1 << PHASE_BITS) / BLOCKS_PER_SECOND / 1000)
LFO_PITCH_RATIO = 0.73
LFO_STEREO_RATIO = 0.51
LFO_PITCH_INCREMENT_PER_MHZ = round(LFO_INCREMENT_PER_MHZ * LFO_PITCH_RATIO)
LFO_STEREO_INCREMENT_PER_MHZ = round(LFO_INCREMENT_PER_MHZ * LFO_STEREO_RATIO)

SMOOTHING_SHIFT = 24
SMOOTHED_FRACTION_BITS = 8
CV_SMOOTHING_SHIFT = 3
CV_SCALE_Q10 = 8_389  # 4000 ASQ * 8389 >> 10 = 32767


def smoothing_coefficient_q24(time_constant_seconds: float) -> int:
    """Per-block one-pole coefficient for the given time constant."""
    return round(
        (1 - math.exp(-1 / (BLOCKS_PER_SECOND * time_constant_seconds)))
        * (1 << SMOOTHING_SHIFT)
    )


PARAMETER_SMOOTHING_Q24 = smoothing_coefficient_q24(0.45)
MASTER_SMOOTHING_Q24 = smoothing_coefficient_q24(0.40)
MUTE_RAMP_Q8 = 1 << 15  # linear mute ramp: full scale reaches exact zero in 256 blocks


def morph_coefficient_q24(morph_seconds: int) -> int:
    """Coefficient reaching 95% of a new chord target in ``morph_seconds``."""
    duration = min(MORPH_SECONDS_MAX, max(MORPH_SECONDS_MIN, morph_seconds))
    time_constant = duration / -math.log(0.05)
    return smoothing_coefficient_q24(time_constant)


MORPH_COEFFICIENT_TABLE_Q24 = tuple(
    morph_coefficient_q24(seconds) for seconds in range(MORPH_TABLE_SIZE)
)


def phase_increment(frequency_hz: float) -> int:
    return round(frequency_hz * (1 << PHASE_BITS) / SAMPLE_RATE)


def midi_note_frequency(note: int) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


NOTE_INCREMENT_TABLE = tuple(
    phase_increment(midi_note_frequency(note)) for note in range(NOTE_TABLE_SIZE)
)
LOW_STEM_INCREMENT = phase_increment(LOW_STEM_CUTOFF_HZ)
AIR_STEM_INCREMENT = phase_increment(AIR_STEM_CUTOFF_HZ)


def sine_table() -> tuple[int, ...]:
    """Full-wave signed Q1.15 sine, indexed by the top 12 phase bits."""
    return tuple(
        round(math.sin(2 * math.pi * index / SINE_TABLE_SIZE) * Q15_ONE)
        for index in range(SINE_TABLE_SIZE)
    )


SINE_TABLE = sine_table()

EXP2_FRACTION_BITS = 10
EXP2_FRACTION_SIZE = 1 << EXP2_FRACTION_BITS


def exp2_fraction_table() -> tuple[int, ...]:
    """``round((2**(i/1024) - 1) * 65536)`` for the fractional octave."""
    return tuple(
        round((2 ** (index / EXP2_FRACTION_SIZE) - 1) * (1 << 16))
        for index in range(EXP2_FRACTION_SIZE)
    )


EXP2_FRACTION_TABLE = exp2_fraction_table()
EXP2_MAX_OCTAVES = 4


def exp2_q16(octaves_q10: int) -> int:
    """Return ``2**(octaves_q10 / 1024)`` in unsigned Q16.16.

    The integer octave is applied as a shift and the fractional octave comes
    from :data:`EXP2_FRACTION_TABLE`, so the function is bit-exact and cheap
    in RTL. The exponent is clamped to +/-4 octaves.
    """
    bounded = max(
        -EXP2_MAX_OCTAVES * EXP2_FRACTION_SIZE,
        min(EXP2_MAX_OCTAVES * EXP2_FRACTION_SIZE - 1, octaves_q10),
    )
    integer = bounded >> EXP2_FRACTION_BITS
    fraction = bounded & (EXP2_FRACTION_SIZE - 1)
    base = (1 << 16) + EXP2_FRACTION_TABLE[fraction]
    if integer >= 0:
        return base << integer
    return base >> -integer


PAN_TABLE_BITS = 8
PAN_TABLE_SIZE = 1 << PAN_TABLE_BITS


def pan_gain_table() -> tuple[int, ...]:
    """Equal-power gain ``sqrt((i + 0.5) / 256)`` in unsigned Q1.15."""
    return tuple(
        q15(math.sqrt((index + 0.5) / PAN_TABLE_SIZE))
        for index in range(PAN_TABLE_SIZE)
    )


PAN_GAIN_TABLE = pan_gain_table()


def phase_variation_q15(group: int) -> int:
    tone, harmonic = divmod(group, HARMONIC_COUNT)
    return round((0.72 + ((tone * 3 + harmonic) % 9) * 0.055) * Q15_ONE)


PHASE_VARIATION_Q15 = tuple(phase_variation_q15(group) for group in range(GROUP_COUNT))


def harmonic_pan_q15(group: int) -> int:
    tone, harmonic = divmod(group, HARMONIC_COUNT)
    return (((harmonic * 2 + tone) % 5) - 2) * HARMONIC_PAN_UNIT_Q15


HARMONIC_PAN_Q15 = tuple(harmonic_pan_q15(group) for group in range(GROUP_COUNT))


def group_offset_phase(group: int) -> int:
    return (group * GROUP_OFFSET_STEP) & PHASE_MASK


def group_timbre_offset_phase(group: int) -> int:
    return (group * TIMBRE_OFFSET_STEP) & PHASE_MASK


def initial_group_midpoint_phase(group: int) -> int:
    return round(group * GOLDEN_ANGLE / (2 * math.pi) * (1 << PHASE_BITS)) & PHASE_MASK


def initial_group_spacing_half_phase(group: int) -> int:
    return round(
        group * INITIAL_SPACING_RADIANS / (2 * math.pi) * (1 << PHASE_BITS) / 2
    ) & PHASE_MASK


def voice_offset(voice: int) -> int:
    """Signed odd multiplier ``2 * voice - 19`` placing voices around the midpoint."""
    return 2 * voice - (VOICE_COUNT - 1)


def initial_phase(oscillator: int) -> int:
    group, voice = divmod(oscillator, VOICE_COUNT)
    return (
        initial_group_midpoint_phase(group)
        + voice_offset(voice) * initial_group_spacing_half_phase(group)
    ) & PHASE_MASK


def initial_phase_table() -> tuple[int, ...]:
    return tuple(initial_phase(index) for index in range(OSCILLATOR_COUNT))


INITIAL_PHASE_TABLE = initial_phase_table()


def clamp(value: int, minimum: int, maximum: int) -> int:
    return min(max(value, minimum), maximum)


def clamp_q15(value: int) -> int:
    return clamp(value, 0, Q15_ONE)


def clamp_q15_signed(value: int) -> int:
    return clamp(value, -Q15_ONE, Q15_ONE)


def master_asq(master_q15: int) -> int:
    return (master_q15 * OUTPUT_CEILING_ASQ) >> 15


def isqrt(value: int) -> int:
    """Floor integer square root (the RTL uses a bit-serial equivalent)."""
    if value < 0:
        raise ValueError("isqrt of a negative value")
    return math.isqrt(value)


def limiter_table() -> tuple[int, ...]:
    """Exponential soft knee from ``LIMITER_KNEE`` to 1.0, unsigned Q1.15.

    Entry ``i`` covers ``|y| = knee + (i + 0.5) * 2**LIMITER_INDEX_SHIFT``
    in Q3.13 units. The curve is continuous in value and slope at the knee.
    """
    table = []
    for index in range(LIMITER_TABLE_SIZE):
        u = LIMITER_KNEE + (index + 0.5) * (1 << LIMITER_INDEX_SHIFT) / MIX_ONE
        level = LIMITER_KNEE + (1 - LIMITER_KNEE) * (
            1 - math.exp(-(u - LIMITER_KNEE) / (1 - LIMITER_KNEE))
        )
        table.append(q15(level))
    return tuple(table)


LIMITER_TABLE = limiter_table()


def limiter(mix_q13: int) -> int:
    """Map a normalized mix sample to the unit limiter output (signed Q1.15)."""
    magnitude = min(abs(mix_q13), MIX_MAX)
    if magnitude < LIMITER_KNEE_Q13:
        level = magnitude << (15 - MIX_FRACTION_BITS)
    else:
        level = LIMITER_TABLE[(magnitude - LIMITER_KNEE_Q13) >> LIMITER_INDEX_SHIFT]
    return -level if mix_q13 < 0 else level


def sub_mix_table() -> tuple[tuple[int, int], ...]:
    """``(main, sub)`` Q1.15 scales renormalizing the sub-focus term."""
    table = []
    for index in range(SUB_MIX_TABLE_SIZE):
        s = SUB_FOCUS_GAIN_Q15 / Q15_ONE * index / (SUB_MIX_TABLE_SIZE - 1)
        scale = 1 / math.sqrt(1 + s * s)
        table.append((q15(scale), q15(s * scale)))
    return tuple(table)


SUB_MIX_TABLE = sub_mix_table()
