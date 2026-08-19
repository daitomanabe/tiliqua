# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Specification gates for the 1,000-sine additive reference model."""

from dataclasses import replace
import unittest

import numpy as np

from tiliqua.additive import (
    AdditiveControlState,
    AdditiveReference,
    AdditiveStateError,
    DEFAULT_CONTROL_STATE,
    normalize_cv,
    tables as T,
)


def rms_dbfs(samples: np.ndarray) -> float:
    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
    return 20 * np.log10(max(rms, 1e-9) / T.Q15_ONE)


def band_energy_fraction(samples: np.ndarray, *, low_hz=None, high_hz=None) -> float:
    x = samples.astype(np.float64) * np.hanning(len(samples))
    spectrum = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(samples), 1 / T.SAMPLE_RATE)
    mask = np.ones_like(freqs, dtype=bool)
    if low_hz is not None:
        mask &= freqs >= low_hz
    if high_hz is not None:
        mask &= freqs <= high_hz
    total = spectrum.sum()
    return float(spectrum[mask].sum() / total) if total > 0 else 0.0


def settle(reference: AdditiveReference, seconds: float, cv=(0, 0, 0, 0)) -> np.ndarray:
    return reference.render(int(seconds * T.SAMPLE_RATE), cv)


class AdditiveTableTests(unittest.TestCase):

    def test_model_is_exactly_one_thousand_oscillators(self):
        self.assertEqual(T.TONE_COUNT * T.HARMONIC_COUNT * T.VOICE_COUNT, 1000)
        self.assertEqual(T.OSCILLATOR_COUNT, 1000)
        self.assertEqual(len(T.INITIAL_PHASE_TABLE), 1000)
        self.assertEqual(T.SYNC_CYCLES_PER_SAMPLE, 60_000_000 // T.SAMPLE_RATE)

    def test_sine_table_is_odd_symmetric_and_full_scale(self):
        half = T.SINE_TABLE_SIZE // 2
        quarter = half // 2
        self.assertEqual(T.SINE_TABLE[0], 0)
        self.assertEqual(T.SINE_TABLE[quarter], T.Q15_ONE)
        self.assertEqual(T.SINE_TABLE[half], 0)
        self.assertEqual(T.SINE_TABLE[3 * quarter], -T.Q15_ONE)
        for index in range(half):
            self.assertEqual(T.SINE_TABLE[index], -T.SINE_TABLE[index + half])
        self.assertEqual(max(abs(v) for v in T.SINE_TABLE), T.Q15_ONE)

    def test_exp2_and_pan_tables_are_monotonic_and_anchored(self):
        self.assertEqual(T.exp2_q16(0), 1 << 16)
        self.assertEqual(T.exp2_q16(1024), 1 << 17)
        self.assertEqual(T.exp2_q16(-1024), 1 << 15)
        self.assertEqual(T.exp2_q16(2048), 1 << 18)
        self.assertEqual(T.exp2_q16(-2048), 1 << 14)
        self.assertEqual(T.exp2_q16(10_000), T.exp2_q16(4 * 1024 - 1))
        previous = 0
        for octaves in range(-4096, 4096):
            value = T.exp2_q16(octaves)
            self.assertGreaterEqual(value, previous)
            previous = value
        self.assertGreater(T.PAN_GAIN_TABLE[-1], T.Q15_ONE - 40)
        self.assertLess(T.PAN_GAIN_TABLE[0], 2_000)
        self.assertEqual(list(T.PAN_GAIN_TABLE), sorted(T.PAN_GAIN_TABLE))
        centre = T.PAN_GAIN_TABLE[T.PAN_TABLE_SIZE // 2 - 1]
        self.assertAlmostEqual(centre / T.Q15_ONE, 0.7071, delta=0.003)

    def test_limiter_is_continuous_monotonic_and_unit_bounded(self):
        knee_linear = T.LIMITER_KNEE_Q13 << (15 - T.MIX_FRACTION_BITS)
        self.assertGreaterEqual(T.LIMITER_TABLE[0], knee_linear)
        self.assertLess(T.LIMITER_TABLE[0] - knee_linear, 128)
        self.assertEqual(list(T.LIMITER_TABLE), sorted(T.LIMITER_TABLE))
        self.assertLessEqual(T.LIMITER_TABLE[-1], T.Q15_ONE)
        previous = 0
        for mix in range(0, T.MIX_MAX + 1, 7):
            value = T.limiter(mix)
            self.assertGreaterEqual(value, previous)
            self.assertLessEqual(value, T.Q15_ONE)
            self.assertEqual(T.limiter(-mix), -value)
            previous = value
        self.assertEqual(T.limiter(0), 0)
        self.assertEqual(T.limiter(T.MIX_ONE // 2), T.MIX_ONE // 2 << 2)
        self.assertEqual(T.limiter(1 << 20), T.limiter(T.MIX_MAX))

    def test_note_and_stem_increments_match_48khz_phase_units(self):
        self.assertEqual(T.NOTE_INCREMENT_TABLE[69], round(440 * 2**32 / 48_000))
        self.assertEqual(T.LOW_STEM_INCREMENT, round(120 * 2**32 / 48_000))
        self.assertEqual(T.AIR_STEM_INCREMENT, round(600 * 2**32 / 48_000))
        self.assertEqual(T.GAIN_UNIT, 52_430)
        self.assertLess(T.GAIN_UNIT * 2, 1 << T.GAIN_BITS)

    def test_cv_normalization_maps_volts_to_unit_range(self):
        self.assertEqual(normalize_cv(4_000, unipolar=False), T.Q15_ONE)
        self.assertEqual(normalize_cv(-4_000, unipolar=False), -T.Q15_ONE)
        self.assertEqual(normalize_cv(0, unipolar=False), 0)
        self.assertEqual(normalize_cv(20_000, unipolar=False), T.Q15_ONE)
        self.assertEqual(normalize_cv(8_000, unipolar=True), T.Q15_ONE)
        self.assertAlmostEqual(normalize_cv(4_000, unipolar=True), 16_384, delta=2)
        self.assertEqual(normalize_cv(-4_000, unipolar=True), 0)


class AdditiveControlStateTests(unittest.TestCase):

    def test_default_state_is_c_minor_9_add_13_with_c1_sub(self):
        state = DEFAULT_CONTROL_STATE
        self.assertEqual(T.HARMONY_NAMES[state.harmony], "MINOR 9 + 13")
        self.assertEqual([state.tone_note(t) for t in range(5)], [24, 39, 46, 50, 57])
        self.assertAlmostEqual(T.midi_note_frequency(24), 32.703, delta=0.001)
        self.assertEqual(state.output_enabled, 1)
        self.assertEqual(state.harmonic_levels, T.SPECTRUM_PRESETS_Q15["WARM"])

    def test_validation_rejects_out_of_range_fields(self):
        bad = (
            replace(DEFAULT_CONTROL_STATE, root_midi=23),
            replace(DEFAULT_CONTROL_STATE, harmony=7),
            replace(DEFAULT_CONTROL_STATE, master=32_768),
            replace(DEFAULT_CONTROL_STATE, detune_millicents=18_001),
            replace(DEFAULT_CONTROL_STATE, evolution_rate_mhz=4),
            replace(DEFAULT_CONTROL_STATE, morph_seconds=121),
            replace(DEFAULT_CONTROL_STATE, harmonic_levels=(0,) * 9),
            replace(DEFAULT_CONTROL_STATE, harmonic_levels=(0,) * 9 + (40_000,)),
            replace(DEFAULT_CONTROL_STATE, sub_octave=2),
            replace(DEFAULT_CONTROL_STATE, master=0.5),
        )
        for state in bad:
            with self.assertRaises(AdditiveStateError):
                state.validate()
        DEFAULT_CONTROL_STATE.with_preset("GLASS").validate()


class AdditiveReferenceTests(unittest.TestCase):

    def test_default_frequencies_cover_five_tones_ten_harmonics_twenty_voices(self):
        reference = AdditiveReference()
        reference.render(T.CONTROL_BLOCK_SAMPLES + 1)  # block 1 carries the chord
        frequencies = reference.oscillator_frequencies_hz().reshape(
            T.GROUP_COUNT, T.VOICE_COUNT
        )
        for tone in range(T.TONE_COUNT):
            base = T.midi_note_frequency(DEFAULT_CONTROL_STATE.tone_note(tone))
            for harmonic in range(T.HARMONIC_COUNT):
                group = tone * T.HARMONIC_COUNT + harmonic
                centre = float(np.mean(frequencies[group]))
                self.assertAlmostEqual(centre / (base * (harmonic + 1)), 1.0, delta=0.003)
                span_cents = 1200 * np.log2(frequencies[group][-1] / frequencies[group][0])
                self.assertAlmostEqual(span_cents, 10.0, delta=0.4)
                self.assertEqual(len(set(frequencies[group].tolist())), T.VOICE_COUNT)
        self.assertAlmostEqual(float(np.mean(frequencies[0])), 32.70, delta=0.15)

    def test_outputs_stay_inside_the_ceiling_and_mute_exactly(self):
        reference = AdditiveReference()
        output = settle(reference, 0.5)
        self.assertLessEqual(int(np.abs(output).max()), T.OUTPUT_CEILING_ASQ)
        for channel in range(4):
            self.assertGreater(int(np.abs(output[channel]).max()), 200)
        phases_before = reference.phases.copy()
        reference.commit_frame(replace(DEFAULT_CONTROL_STATE, output_enabled=0))
        muted = settle(reference, 1.0)
        tail = muted[:, -T.SAMPLE_RATE // 10:]
        self.assertTrue(np.all(tail == 0))
        ramp = np.abs(muted[0, : T.SAMPLE_RATE // 10])
        self.assertGreater(int(ramp.max()), 0)
        self.assertEqual(reference.master_asq, 0)
        self.assertFalse(np.array_equal(phases_before, reference.phases))
        reference.commit_frame(DEFAULT_CONTROL_STATE)
        resumed = settle(reference, 0.5, cv=(8_000, 0, 0, 0))
        self.assertGreater(int(np.abs(resumed[:, -4_800:]).max()), 200)

    def test_mute_cannot_be_reopened_by_cv(self):
        reference = AdditiveReference(replace(DEFAULT_CONTROL_STATE, output_enabled=0))
        output = settle(reference, 0.2, cv=(8_000, 4_000, 4_000, 4_000))
        self.assertTrue(np.all(output == 0))

    def test_worst_case_settings_are_bounded_with_and_without_cv(self):
        worst = replace(
            DEFAULT_CONTROL_STATE,
            master=T.Q15_ONE,
            harmonic_levels=(T.Q15_ONE,) * 10,
            detune_millicents=0,
            phase_spread=0,
            evolution_amount=0,
            stereo_width=0,
            sub_focus=T.Q15_ONE,
        )
        for cv in ((0, 0, 0, 0), (8_000, 4_000, 4_000, 4_000), (8_000, -4_000, -4_000, -4_000)):
            reference = AdditiveReference(worst)
            output = settle(reference, 1.0, cv=cv)
            self.assertLessEqual(int(np.abs(output).max()), T.OUTPUT_CEILING_ASQ)
            self.assertGreater(int(np.abs(output[0]).max()), T.OUTPUT_CEILING_ASQ // 2)
            for group in reference.groups:
                for gain in (group.gain_left, group.gain_right, group.gain_low, group.gain_air):
                    self.assertLess(gain, 1 << T.GAIN_BITS)

    def test_stems_are_distinct_low_and_upper_harmonic_content(self):
        reference = AdditiveReference()
        settle(reference, 0.5)
        output = settle(reference, 1.0)
        low = output[2]
        air = output[3]
        self.assertGreater(band_energy_fraction(low, high_hz=150), 0.98)
        self.assertGreater(band_energy_fraction(air, low_hz=550), 0.98)
        self.assertGreater(rms_dbfs(low), -40)
        self.assertGreater(rms_dbfs(air), -45)
        lows = [group.low for group in reference.groups]
        airs = [group.air for group in reference.groups]
        self.assertEqual([i for i, flag in enumerate(lows) if flag], [0, 1, 2, 10, 20])
        self.assertTrue(all(not (a and b) for a, b in zip(lows, airs)))
        self.assertGreaterEqual(sum(airs), 20)

    def test_low_band_is_mono_coherent_and_width_zero_collapses_to_mono(self):
        reference = AdditiveReference()
        settle(reference, 0.5)
        output = settle(reference, 1.0)
        left = np.fft.rfft(output[0].astype(np.float64))
        right = np.fft.rfft(output[1].astype(np.float64))
        freqs = np.fft.rfftfreq(output.shape[1], 1 / T.SAMPLE_RATE)
        low_bins = freqs < 100
        self.assertLess(
            float(np.abs(left[low_bins] - right[low_bins]).sum()
                  / np.abs(left[low_bins]).sum()),
            0.01,
        )
        self.assertFalse(np.array_equal(output[0], output[1]))
        for group in reference.groups:
            if group.low:
                self.assertEqual(group.gain_left, group.gain_right)

        mono = AdditiveReference(replace(DEFAULT_CONTROL_STATE, stereo_width=0))
        settle(mono, 0.6)
        output = settle(mono, 0.2)
        self.assertTrue(np.array_equal(output[0], output[1]))

    def test_in0_raises_master_and_sub_response(self):
        quiet = AdditiveReference()
        settle(quiet, 1.0, cv=(0, 0, 0, 0))
        loud = AdditiveReference()
        settle(loud, 1.0, cv=(8_000, 0, 0, 0))
        self.assertGreater(loud.master_asq, quiet.master_asq * 1.8)
        self.assertGreater(loud.statistics.sub_gain, quiet.statistics.sub_gain)
        self.assertAlmostEqual(
            loud.statistics.cv_effective["master"],
            T.clamp_q15(DEFAULT_CONTROL_STATE.master + 16_384),
            delta=8,
        )

    def test_in1_transposes_every_oscillator_by_two_semitones_per_volt(self):
        base = AdditiveReference()
        settle(base, 1.0)
        up = AdditiveReference()
        settle(up, 1.0, cv=(0, 4_000, 0, 0))
        down = AdditiveReference()
        settle(down, 1.0, cv=(0, -4_000, 0, 0))
        ratio_up = up.oscillator_frequencies_hz() / base.oscillator_frequencies_hz()
        ratio_down = down.oscillator_frequencies_hz() / base.oscillator_frequencies_hz()
        self.assertTrue(np.allclose(ratio_up, 2 ** (2 / 12), rtol=0.004))
        self.assertTrue(np.allclose(ratio_down, 2 ** (-2 / 12), rtol=0.004))
        self.assertAlmostEqual(
            up.statistics.pitch_ratio_q16 / T.exp2_q16(T.PITCH_OFFSET_RANGE_Q10),
            1.0,
            delta=0.002,
        )

    def test_in2_tilts_the_harmonic_spectrum(self):
        def harmonic_ratio(reference):
            groups = reference.groups
            return groups[9].amplitude / max(1, groups[0].amplitude)

        flat = AdditiveReference()
        settle(flat, 1.0)
        bright = AdditiveReference()
        settle(bright, 1.0, cv=(0, 0, 4_000, 0))
        dark = AdditiveReference()
        settle(dark, 1.0, cv=(0, 0, -4_000, 0))
        self.assertAlmostEqual(harmonic_ratio(bright) / harmonic_ratio(flat), 4.0, delta=0.2)
        self.assertAlmostEqual(harmonic_ratio(dark) / harmonic_ratio(flat), 0.25, delta=0.02)
        self.assertEqual(bright.groups[0].amplitude, flat.groups[0].amplitude)

    def test_in3_widens_phase_spread_evolution_and_stereo(self):
        base = AdditiveReference()
        settle(base, 1.0)
        moved = AdditiveReference()
        settle(moved, 1.0, cv=(0, 0, 0, 4_000))
        self.assertGreater(
            moved.groups[5].spread_half_phase, base.groups[5].spread_half_phase * 1.5
        )
        self.assertGreater(
            moved.statistics.cv_effective["evolution"],
            base.statistics.cv_effective["evolution"],
        )
        self.assertGreater(
            moved.statistics.cv_effective["stereo_width"],
            base.statistics.cv_effective["stereo_width"],
        )
        self.assertAlmostEqual(
            moved.statistics.cv_effective["phase_spread"],
            T.clamp_q15(DEFAULT_CONTROL_STATE.phase_spread + 16_384),
            delta=8,
        )

    def test_cv_never_overwrites_the_stored_ui_state(self):
        reference = AdditiveReference()
        settle(reference, 0.5, cv=(8_000, 4_000, 4_000, 4_000))
        self.assertEqual(reference.state, DEFAULT_CONTROL_STATE)
        settle(reference, 1.0, cv=(0, 0, 0, 0))
        self.assertEqual(reference.statistics.cv_smoothed, (0, 0, 0, 0))
        self.assertEqual(reference.statistics.pitch_ratio_q16, 1 << 16)

    def test_frames_latch_at_a_block_start_and_apply_one_block_later(self):
        reference = AdditiveReference()
        reference.render(T.CONTROL_BLOCK_SAMPLES + 5)
        changed = replace(DEFAULT_CONTROL_STATE, harmony=0)
        reference.commit_frame(changed)
        # Rest of block 1: nothing changes.
        reference.render(T.CONTROL_BLOCK_SAMPLES - 6)
        self.assertEqual(reference.state, DEFAULT_CONTROL_STATE)
        reference.render(1)
        self.assertEqual(reference.state, DEFAULT_CONTROL_STATE)
        # Block 2 start: the frame is latched but block 2 still plays the
        # parameters computed from the old state.
        reference.render(1)
        self.assertEqual(reference.state, DEFAULT_CONTROL_STATE)
        self.assertEqual(reference._latched_state, changed)
        reference.render(T.CONTROL_BLOCK_SAMPLES - 1)
        self.assertEqual(reference.state, DEFAULT_CONTROL_STATE)
        # Block 3 start: the new state drives the parameters.
        reference.render(1)
        self.assertEqual(reference.state, changed)

    def test_block_zero_is_silent_and_block_one_plays(self):
        reference = AdditiveReference()
        first = reference.render(T.CONTROL_BLOCK_SAMPLES)
        self.assertTrue(np.all(first == 0))
        self.assertEqual(reference.master_asq, 0)
        second = reference.render(T.CONTROL_BLOCK_SAMPLES)
        self.assertGreater(int(np.abs(second).max()), 0)
        self.assertGreater(reference.master_asq, 0)

    def test_harmony_morph_is_slow_monotonic_and_phase_continuous(self):
        reference = AdditiveReference()
        settle(reference, 0.2)
        before = list(reference.base_increments)
        target_state = replace(DEFAULT_CONTROL_STATE, harmony=0, morph_seconds=2)
        reference.commit_frame(target_state)
        targets = [
            T.NOTE_INCREMENT_TABLE[target_state.tone_note(g // 10)] * (g % 10 + 1)
            for g in range(T.GROUP_COUNT)
        ]
        snapshots = []
        for _ in range(8):
            phase_before = reference.phases.copy()
            reference.render(1)
            increments = reference.oscillator_increments()
            expected = (phase_before.astype(np.uint64) + increments) & T.PHASE_MASK
            self.assertTrue(np.array_equal(reference.phases.astype(np.uint64), expected))
            settle(reference, 0.25)
            snapshots.append(list(reference.base_increments))
        for group in (12, 25, 48):
            values = [before[group]] + [snapshot[group] for snapshot in snapshots]
            direction = np.sign(targets[group] - before[group])
            deltas = np.diff(values) * direction
            self.assertTrue(np.all(deltas >= 0))
            remaining = abs(targets[group] - values[-1])
            self.assertLess(remaining, abs(targets[group] - before[group]) * 0.06)

    def test_master_smoothing_reaches_target_without_overshoot(self):
        reference = AdditiveReference(replace(DEFAULT_CONTROL_STATE, master=0))
        settle(reference, 0.2)
        reference.commit_frame(replace(DEFAULT_CONTROL_STATE, master=T.Q15_ONE))
        levels = []
        for _ in range(12):
            settle(reference, 0.25)
            levels.append(reference.master_asq)
        self.assertTrue(all(b >= a for a, b in zip(levels, levels[1:])))
        self.assertGreater(levels[-1], T.OUTPUT_CEILING_ASQ * 0.99)
        self.assertLessEqual(levels[-1], T.OUTPUT_CEILING_ASQ)
        self.assertLess(levels[0], T.OUTPUT_CEILING_ASQ * 0.6)

    def test_reference_is_deterministic_and_dc_free(self):
        first = AdditiveReference()
        second = AdditiveReference()
        cv = np.zeros((T.SAMPLE_RATE // 2, 4), dtype=np.int64)
        cv[:, 1] = (np.arange(T.SAMPLE_RATE // 2) % 4_000) - 2_000
        a = first.render(T.SAMPLE_RATE // 2, cv)
        b = second.render(T.SAMPLE_RATE // 2, cv)
        self.assertTrue(np.array_equal(a, b))
        for channel in range(4):
            self.assertLess(abs(float(a[channel].mean())), 40)
        self.assertTrue(np.all(np.isfinite(a)))


if __name__ == "__main__":
    unittest.main()
