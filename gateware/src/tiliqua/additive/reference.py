# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Normative integer reference model for the Tiliqua 1,000-sine additive engine.

The model is structurally independent from the RTL. It never imports Amaranth
and it describes the digital behaviour one 48 kHz sample at a time:

* 5 chord tones x 10 harmonics x 20 micro-detuned voices = 1,000 phase
  accumulators, each advanced by its own 32-bit increment;
* per-group (tone, harmonic) amplitude, pan, detune, phase-spread, stem
  membership and evolution, recomputed once per 128-sample control block and
  committed atomically at the block boundary;
* four calibrated outputs -- stereo master L/R, a low/sub stem and an
  upper-harmonic air stem -- each peak-bounded by construction to the
  +/-2.0 V ceiling.

``numpy`` is used only to vectorise the 1,000 oscillator updates; every
operation is exact integer arithmetic and mirrors the RTL data path.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np

from . import tables as T


class AdditiveStateError(ValueError):
    """Raised for a control state outside the specification."""


@dataclass(frozen=True)
class AdditiveControlState:
    """The UI-owned base state carried by one host control frame."""

    root_midi: int = T.DEFAULT_ROOT_MIDI
    harmony: int = T.DEFAULT_HARMONY
    sub_octave: int = 1
    output_enabled: int = 1
    master: int = T.q15(0.42)
    harmonic_levels: tuple[int, ...] = T.SPECTRUM_PRESETS_Q15["WARM"]
    detune_millicents: int = 5_000
    phase_spread: int = T.q15(0.58)
    evolution_amount: int = T.q15(0.62)
    evolution_rate_mhz: int = 20
    stereo_width: int = T.q15(0.82)
    morph_seconds: int = 18
    sub_focus: int = T.q15(0.36)

    def validate(self) -> "AdditiveControlState":
        checks = (
            ("root_midi", self.root_midi, T.ROOT_MIDI_MIN, T.ROOT_MIDI_MAX),
            ("harmony", self.harmony, 0, T.HARMONY_COUNT - 1),
            ("sub_octave", self.sub_octave, 0, 1),
            ("output_enabled", self.output_enabled, 0, 1),
            ("master", self.master, 0, T.Q15_ONE),
            ("detune_millicents", self.detune_millicents, 0, T.DETUNE_MILLICENTS_MAX),
            ("phase_spread", self.phase_spread, 0, T.Q15_ONE),
            ("evolution_amount", self.evolution_amount, 0, T.Q15_ONE),
            (
                "evolution_rate_mhz",
                self.evolution_rate_mhz,
                T.EVOLUTION_RATE_MHZ_MIN,
                T.EVOLUTION_RATE_MHZ_MAX,
            ),
            ("stereo_width", self.stereo_width, 0, T.Q15_ONE),
            ("morph_seconds", self.morph_seconds, T.MORPH_SECONDS_MIN, T.MORPH_SECONDS_MAX),
            ("sub_focus", self.sub_focus, 0, T.Q15_ONE),
        )
        for name, value, minimum, maximum in checks:
            if not isinstance(value, int) or isinstance(value, bool):
                raise AdditiveStateError(f"{name} must be an integer")
            if not minimum <= value <= maximum:
                raise AdditiveStateError(
                    f"{name}={value} outside {minimum}..{maximum}"
                )
        if len(self.harmonic_levels) != T.HARMONIC_COUNT:
            raise AdditiveStateError("harmonic_levels must hold ten values")
        for level in self.harmonic_levels:
            if not isinstance(level, int) or isinstance(level, bool):
                raise AdditiveStateError("harmonic levels must be integers")
            if not 0 <= level <= T.Q15_ONE:
                raise AdditiveStateError(f"harmonic level {level} outside 0..32767")
        return self

    def tone_note(self, tone: int) -> int:
        interval = T.HARMONY_INTERVALS[self.harmony][tone]
        drop = 12 if tone == 0 and self.sub_octave else 0
        return self.root_midi + interval - drop

    def with_preset(self, name: str) -> "AdditiveControlState":
        return replace(self, harmonic_levels=T.SPECTRUM_PRESETS_Q15[name])


DEFAULT_CONTROL_STATE = AdditiveControlState().validate()


@dataclass(frozen=True)
class AdditiveGroupParameters:
    """Per-(tone, harmonic) values committed at a control-block boundary."""

    midpoint_increment: int = 0
    spacing_half_increment: int = 0
    spread_half_phase: int = 0
    gain_left: int = 0
    gain_right: int = 0
    gain_low: int = 0
    gain_air: int = 0
    amplitude: int = 0
    pan: int = 0
    low: bool = False
    air: bool = False


@dataclass
class AdditiveBlockStatistics:
    """Observable block-rate state used by tests and the HDMI visualiser."""

    block_index: int = 0
    master_asq: int = 0
    left_power: int = 0
    right_power: int = 0
    low_power: int = 0
    air_power: int = 0
    sub_gain: int = 0
    cv_smoothed: tuple[int, int, int, int] = (0, 0, 0, 0)
    cv_effective: dict = field(default_factory=dict)
    pitch_ratio_q16: int = 1 << 16


def normalize_cv(asq: int, *, unipolar: bool) -> int:
    """Map calibrated ASQ to the Q1.15 CV unit (1 V bipolar, 2 V unipolar)."""
    if unipolar:
        return T.clamp((asq * T.CV_SCALE_Q10) >> 11, 0, T.Q15_ONE)
    return T.clamp_q15_signed((asq * T.CV_SCALE_Q10) >> 10)


def smooth_q24(current_q8: int, target: int, coefficient_q24: int) -> int:
    """One-pole step on a value stored with eight extra fractional bits."""
    delta = (target << T.SMOOTHED_FRACTION_BITS) - current_q8
    return current_q8 + ((delta * coefficient_q24) >> T.SMOOTHING_SHIFT)


def smooth_increment(current: int, target: int, coefficient_q24: int) -> int:
    return current + (((target - current) * coefficient_q24) >> T.SMOOTHING_SHIFT)


def sine_q15(phase: int) -> int:
    return T.SINE_TABLE[(phase & T.PHASE_MASK) >> T.SINE_SHIFT]


def pan_gains(pan_q15: int) -> tuple[int, int]:
    left_index = (T.Q15_ONE - pan_q15) >> (16 - T.PAN_TABLE_BITS)
    right_index = (T.Q15_ONE + pan_q15) >> (16 - T.PAN_TABLE_BITS)
    return T.PAN_GAIN_TABLE[left_index], T.PAN_GAIN_TABLE[right_index]


class AdditiveReference:
    """Sample-exact model of the additive engine and its control scheduler."""

    def __init__(self, state: AdditiveControlState | None = None):
        self.state = (state or DEFAULT_CONTROL_STATE).validate()
        self._latched_state = self.state
        self._cv_delayed = [0, 0, 0, 0]
        self.pending_state: AdditiveControlState | None = None
        self.sample_index = 0
        self.block_index = 0
        self.initialized = False
        self._block_prepared_at = -1

        self.phases = np.array(T.INITIAL_PHASE_TABLE, dtype=np.uint32)
        self._voice_offsets = np.array(
            [T.voice_offset(voice) for voice in range(T.VOICE_COUNT)] * T.GROUP_COUNT,
            dtype=np.int64,
        )
        self._sine = np.array(T.SINE_TABLE, dtype=np.int64)

        self.base_increments = [0] * T.GROUP_COUNT
        self.weights_q8 = [0] * T.GROUP_COUNT
        self.groups = [AdditiveGroupParameters() for _ in range(T.GROUP_COUNT)]
        self._increments = np.zeros(T.OSCILLATOR_COUNT, dtype=np.uint32)
        self._spread_offsets = np.zeros(T.OSCILLATOR_COUNT, dtype=np.uint32)
        self._gains = np.zeros((4, T.GROUP_COUNT), dtype=np.int64)

        self.timbre_lfo_phase = 0
        self.pitch_lfo_phase = 0
        self.stereo_lfo_phase = 0

        self.detune_q8 = 0
        self.phase_spread_q8 = 0
        self.evolution_q8 = 0
        self.stereo_width_q8 = 0
        self.sub_focus_q8 = 0
        self.master_q8 = 0

        self.cv_accumulators = [0, 0, 0, 0]
        self.cv_smoothed = [0, 0, 0, 0]
        self.master_asq = 0
        self.statistics = AdditiveBlockStatistics()

    # ------------------------------------------------------------------
    # Host control

    def commit_frame(self, state: AdditiveControlState) -> None:
        """Queue a validated frame.

        It is latched at the next block start and its parameters take effect
        one block later, exactly like the RTL control engine.
        """
        self.pending_state = state.validate()

    # ------------------------------------------------------------------
    # Block-rate control engine

    def _cv_effective(self) -> dict:
        cv0, cv1, cv2, cv3 = self.cv_smoothed
        offset0 = (cv0 * T.CV_OFFSET_DEPTH_Q15) >> 15
        offset3 = (cv3 * T.CV_OFFSET_DEPTH_Q15) >> 15
        return {
            "master": T.clamp_q15((self.master_q8 >> 8) + offset0),
            "sub_focus": T.clamp_q15((self.sub_focus_q8 >> 8) + offset0),
            "pitch_octaves_q10": (cv1 * T.PITCH_OFFSET_RANGE_Q10) >> 15,
            "tilt_cv": cv2,
            "phase_spread": T.clamp_q15((self.phase_spread_q8 >> 8) + offset3),
            "evolution": T.clamp_q15((self.evolution_q8 >> 8) + offset3),
            "stereo_width": T.clamp_q15((self.stereo_width_q8 >> 8) + offset3),
            "detune_millicents": self.detune_q8 >> 8,
        }

    def step_block(self) -> None:
        """Commit the parameters for the block that starts now.

        Schedule (mirrors the RTL, whose control engine needs most of a block
        to finish): the parameters taking effect at block ``b`` are computed
        from the frame latched at the start of block ``b - 1`` and the CV
        average accumulated during block ``b - 2``. Block 0 is silent because
        nothing has been computed yet.
        """
        cv_average = [acc >> 7 for acc in self.cv_accumulators]
        self.cv_accumulators = [0, 0, 0, 0]
        state = self._latched_state
        cv_now = self._cv_delayed
        if self.pending_state is not None:
            self._latched_state = self.pending_state
            self.pending_state = None
        self._cv_delayed = cv_average
        self.state = state

        if self.block_index == 0:
            self.master_asq = 0
            self._commit([AdditiveGroupParameters() for _ in range(T.GROUP_COUNT)])
            self.statistics = AdditiveBlockStatistics(block_index=0)
            self.block_index = 1
            return

        # CV: normalize the delayed block average, then a 1/8 one-pole.
        for index in range(4):
            normalized = normalize_cv(cv_now[index], unipolar=(index == 0))
            if not self.initialized:
                self.cv_smoothed[index] = normalized
            else:
                self.cv_smoothed[index] += (
                    normalized - self.cv_smoothed[index]
                ) >> T.CV_SMOOTHING_SHIFT

        # UI parameter smoothing (0.45 s) and master smoothing (0.40 s). A
        # disabled output ramps the master linearly to an exact zero so mute
        # neither clicks nor lingers.
        if not self.initialized:
            self.detune_q8 = state.detune_millicents << 8
            self.phase_spread_q8 = state.phase_spread << 8
            self.evolution_q8 = state.evolution_amount << 8
            self.stereo_width_q8 = state.stereo_width << 8
            self.sub_focus_q8 = state.sub_focus << 8
            self.master_q8 = (state.master if state.output_enabled else 0) << 8
        else:
            coefficient = T.PARAMETER_SMOOTHING_Q24
            self.detune_q8 = smooth_q24(self.detune_q8, state.detune_millicents, coefficient)
            self.phase_spread_q8 = smooth_q24(self.phase_spread_q8, state.phase_spread, coefficient)
            self.evolution_q8 = smooth_q24(self.evolution_q8, state.evolution_amount, coefficient)
            self.stereo_width_q8 = smooth_q24(self.stereo_width_q8, state.stereo_width, coefficient)
            self.sub_focus_q8 = smooth_q24(self.sub_focus_q8, state.sub_focus, coefficient)
            if state.output_enabled:
                self.master_q8 = smooth_q24(self.master_q8, state.master, T.MASTER_SMOOTHING_Q24)
            else:
                self.master_q8 = max(0, self.master_q8 - T.MUTE_RAMP_Q8)

        effective = self._cv_effective()
        if not state.output_enabled:
            # CV cannot reopen a muted output; the ramp-down is CV-free.
            effective["master"] = self.master_q8 >> 8
        effective_master_asq = T.master_asq(effective["master"])
        pitch_ratio = T.exp2_q16(effective["pitch_octaves_q10"])
        evolution = effective["evolution"]
        phase_spread = effective["phase_spread"]
        stereo_width = effective["stereo_width"]
        detune = effective["detune_millicents"]
        sub_main_scale, sub_scale = T.SUB_MIX_TABLE[
            effective["sub_focus"] >> (15 - T.SUB_MIX_TABLE_BITS)
        ]
        morph = T.MORPH_COEFFICIENT_TABLE_Q24[state.morph_seconds]

        # Evolution LFOs advance once per block.
        rate = state.evolution_rate_mhz
        self.timbre_lfo_phase = (
            self.timbre_lfo_phase + rate * T.LFO_INCREMENT_PER_MHZ
        ) & T.PHASE_MASK
        self.pitch_lfo_phase = (
            self.pitch_lfo_phase + rate * T.LFO_PITCH_INCREMENT_PER_MHZ
        ) & T.PHASE_MASK
        self.stereo_lfo_phase = (
            self.stereo_lfo_phase + rate * T.LFO_STEREO_INCREMENT_PER_MHZ
        ) & T.PHASE_MASK

        # Pass 1: per-group targets.
        amplitudes = [0] * T.GROUP_COUNT
        pans = [0] * T.GROUP_COUNT
        lows = [False] * T.GROUP_COUNT
        airs = [False] * T.GROUP_COUNT
        midpoints = [0] * T.GROUP_COUNT
        spacings = [0] * T.GROUP_COUNT
        spreads = [0] * T.GROUP_COUNT
        for group in range(T.GROUP_COUNT):
            tone, harmonic = divmod(group, T.HARMONIC_COUNT)
            note = state.tone_note(tone)
            target_base = T.NOTE_INCREMENT_TABLE[note] * (harmonic + 1)
            if not self.initialized:
                self.base_increments[group] = target_base
            else:
                self.base_increments[group] = smooth_increment(
                    self.base_increments[group], target_base, morph
                )
            base = self.base_increments[group]
            lows[group] = base <= T.LOW_STEM_INCREMENT
            airs[group] = base >= T.AIR_STEM_INCREMENT

            # Two-stage products keep every intermediate inside 48 bits so the
            # RTL serial multiplier can reproduce them exactly.
            drift = (
                sine_q15(self.pitch_lfo_phase + T.group_offset_phase(group)) * evolution
            ) >> 15
            drift_term = (base * drift) >> 15
            midpoint = base + ((drift_term * T.DRIFT_RATIO_Q16) >> 16)
            midpoint = (midpoint * pitch_ratio) >> 16
            midpoints[group] = midpoint & T.PHASE_MASK
            spacings[group] = (
                ((midpoint * detune) >> 15) * T.DETUNE_SPACING_Q40
            ) >> (T.DETUNE_SPACING_SHIFT - 15)
            spreads[group] = (
                ((phase_spread * T.PHASE_VARIATION_Q15[group]) >> 15) * T.SPREAD_UNIT
            ) >> 15

            motion = (
                (
                    sine_q15(self.timbre_lfo_phase + T.group_timbre_offset_phase(group))
                    * T.WEIGHT_MOTION_DEPTH_Q15
                ) >> 15
            ) * evolution >> 15
            weight_motion = max(
                T.WEIGHT_MOTION_FLOOR_Q15, T.WEIGHT_MOTION_BASE_Q15 + motion
            )
            target_weight = (
                ((state.harmonic_levels[harmonic] * T.TONE_WEIGHTS_Q15[tone]) >> 15)
                * weight_motion
            ) >> 15
            if not self.initialized:
                self.weights_q8[group] = target_weight << 8
            else:
                self.weights_q8[group] = smooth_q24(
                    self.weights_q8[group], target_weight, T.PARAMETER_SMOOTHING_Q24
                )
            weight = self.weights_q8[group] >> 8
            tilt_octaves = (effective["tilt_cv"] * T.TILT_STEP_Q10[harmonic]) >> 15
            amplitudes[group] = T.clamp_q15((weight * T.exp2_q16(tilt_octaves)) >> 16)

            if lows[group]:
                pans[group] = 0
            else:
                pan_motion = (
                    (
                        sine_q15(self.stereo_lfo_phase + T.group_offset_phase(group))
                        * T.PAN_MOTION_DEPTH_Q15
                    ) >> 15
                ) * evolution >> 15
                pans[group] = T.clamp_q15_signed(
                    (
                        (
                            T.TONE_POSITION_PAN_Q15[tone]
                            + T.HARMONIC_PAN_Q15[group]
                            + pan_motion
                        )
                        * stereo_width
                    ) >> 15
                )

        # RMS normalization per bus: root of the weighted power sum.
        panned = [pan_gains(pan) for pan in pans]
        left_weights = [(a * left) >> 15 for a, (left, _) in zip(amplitudes, panned)]
        right_weights = [(a * right) >> 15 for a, (_, right) in zip(amplitudes, panned)]
        left_power = sum(w * w for w in left_weights)
        right_power = sum(w * w for w in right_weights)
        low_power = sum(a * a for a, low in zip(amplitudes, lows) if low)
        air_power = sum(a * a for a, air in zip(amplitudes, airs) if air)

        def normalization(power: int) -> int:
            if power <= 0:
                return 0
            root = max(T.isqrt(power), T.NORMALIZATION_FLOOR)
            return (T.GAIN_UNIT << T.NORMALIZATION_SHIFT) // root

        # The stereo pair shares one root (the louder side) so panning never
        # shifts the image and centred content is bit-identical on L and R.
        norm_master = normalization(max(left_power, right_power))
        norm_low = normalization(low_power)
        norm_air = normalization(air_power)
        sub_gain = (T.GAIN_UNIT * sub_scale) >> 15

        def scaled(weight: int, norm: int) -> int:
            unit = (weight * norm) >> T.NORMALIZATION_SHIFT
            return (unit * sub_main_scale) >> 15

        # Pass 2: committed per-group parameters.
        groups = []
        for group in range(T.GROUP_COUNT):
            amplitude = amplitudes[group]
            gain_left = scaled(left_weights[group], norm_master)
            gain_right = scaled(right_weights[group], norm_master)
            gain_low = scaled(amplitude, norm_low) if lows[group] else 0
            gain_air = ((amplitude * norm_air) >> T.NORMALIZATION_SHIFT) if airs[group] else 0
            if group == 0:
                gain_left += sub_gain
                gain_right += sub_gain
                gain_low += sub_gain
            groups.append(AdditiveGroupParameters(
                midpoint_increment=midpoints[group],
                spacing_half_increment=spacings[group],
                spread_half_phase=spreads[group],
                gain_left=gain_left,
                gain_right=gain_right,
                gain_low=gain_low,
                gain_air=gain_air,
                amplitude=amplitude,
                pan=pans[group],
                low=lows[group],
                air=airs[group],
            ))
        self._commit(groups)

        self.master_asq = effective_master_asq
        self.statistics = AdditiveBlockStatistics(
            block_index=self.block_index,
            master_asq=effective_master_asq,
            left_power=left_power,
            right_power=right_power,
            low_power=low_power,
            air_power=air_power,
            sub_gain=sub_gain,
            cv_smoothed=tuple(self.cv_smoothed),
            cv_effective=effective,
            pitch_ratio_q16=pitch_ratio,
        )
        self.block_index += 1
        self.initialized = True

    def _commit(self, groups: Sequence[AdditiveGroupParameters]) -> None:
        self.groups = list(groups)
        midpoints = np.repeat(
            np.array([g.midpoint_increment for g in groups], dtype=np.int64),
            T.VOICE_COUNT,
        )
        spacings = np.repeat(
            np.array([g.spacing_half_increment for g in groups], dtype=np.int64),
            T.VOICE_COUNT,
        )
        spreads = np.repeat(
            np.array([g.spread_half_phase for g in groups], dtype=np.int64),
            T.VOICE_COUNT,
        )
        self._increments = (
            (midpoints + self._voice_offsets * spacings) & T.PHASE_MASK
        ).astype(np.uint32)
        self._spread_offsets = (
            (self._voice_offsets * spreads) & T.PHASE_MASK
        ).astype(np.uint32)
        self._gains = np.array(
            [
                [g.gain_left for g in groups],
                [g.gain_right for g in groups],
                [g.gain_low for g in groups],
                [g.gain_air for g in groups],
            ],
            dtype=np.int64,
        )

    # ------------------------------------------------------------------
    # Sample-rate oscillator engine

    def prepare_block(self) -> bool:
        """Run the control block due at this sample index, once; True if it ran."""
        if self.sample_index % T.CONTROL_BLOCK_SAMPLES != 0:
            return False
        if self._block_prepared_at == self.sample_index:
            return False
        self.step_block()
        self._block_prepared_at = self.sample_index
        return True

    def step_sample(self, cv_asq: Sequence[int] = (0, 0, 0, 0)) -> tuple[int, int, int, int]:
        """Advance all 1,000 oscillators by one sample and return OUT 0-3."""
        self.prepare_block()
        for index in range(4):
            self.cv_accumulators[index] += int(cv_asq[index])

        self.phases = self.phases + self._increments  # uint32 wraps
        effective = self.phases + self._spread_offsets
        sines = self._sine[effective >> T.SINE_SHIFT]
        group_sums = sines.reshape(T.GROUP_COUNT, T.VOICE_COUNT).sum(axis=1)
        group_sums18 = group_sums >> T.GROUP_SUM_SHIFT
        accumulators = self._gains @ group_sums18
        outputs = tuple(
            self.output_stage(int(value), channel)
            for channel, value in enumerate(accumulators)
        )
        self.sample_index += 1
        return outputs

    def output_stage(self, accumulator: int, channel: int) -> int:
        """Normalized mix -> saturate -> soft limiter -> master -> ceiling."""
        mix = T.clamp(accumulator >> T.OUTPUT_SHIFTS[channel], -T.MIX_MAX, T.MIX_MAX)
        limited = T.limiter(mix)
        scaled = (limited * self.master_asq) >> 15
        return T.clamp(scaled, -T.OUTPUT_CEILING_ASQ, T.OUTPUT_CEILING_ASQ)

    def render(
        self,
        samples: int,
        cv_asq: Sequence[int] | np.ndarray = (0, 0, 0, 0),
    ) -> np.ndarray:
        """Render ``samples`` frames; returns an int64 array shaped (4, samples)."""
        cv = np.asarray(cv_asq, dtype=np.int64)
        if cv.ndim == 1:
            cv = np.broadcast_to(cv, (samples, 4))
        elif cv.shape != (samples, 4):
            raise ValueError("cv_asq must be four values or shaped (samples, 4)")
        output = np.zeros((4, samples), dtype=np.int64)
        for frame in range(samples):
            output[:, frame] = self.step_sample(cv[frame])
        return output

    # ------------------------------------------------------------------
    # Introspection helpers

    def group_sines(self) -> np.ndarray:
        """Current sine values of every oscillator (for display tests)."""
        effective = self.phases + self._spread_offsets
        return self._sine[effective >> T.SINE_SHIFT]

    def oscillator_increments(self) -> np.ndarray:
        return self._increments.copy()

    def oscillator_frequencies_hz(self) -> np.ndarray:
        return self._increments.astype(np.float64) * T.SAMPLE_RATE / (1 << T.PHASE_BITS)
