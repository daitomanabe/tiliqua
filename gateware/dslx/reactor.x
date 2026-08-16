// Copyright (c) 2026 Tiliqua contributors
//
// SPDX-License-Identifier: CERN-OHL-S-2.0

// Audio-reactive control logic for Tiliqua's signed 16-bit sample format.
// The Amaranth wrapper owns the state and stream handshake; this function is
// deliberately combinational so generated RTL stays easy to inspect.

const DEFAULT_THRESHOLD = u16:4096;
const THRESHOLD_DEADBAND = u16:256;
const GATE_HIGH = s16:20000;
const SIGNED_MAX = u16:32767;

fn magnitude(sample: s16) -> u16 {
    if sample < s16:0 {
        (-sample) as u16
    } else {
        sample as u16
    }
}

fn nonzero_shift(delta: u16, shift: u4) -> u16 {
    let shifted = delta >> shift;
    if delta != u16:0 && shifted == u16:0 {
        u16:1
    } else {
        shifted
    }
}

fn envelope_step(sample_magnitude: u16, previous: u16) -> u16 {
    if sample_magnitude > previous {
        previous + nonzero_shift(sample_magnitude - previous, u4:3)
    } else {
        previous - nonzero_shift(previous - sample_magnitude, u4:7)
    }
}

fn clip_to_positive_s16(value: u16) -> s16 {
    if value > SIGNED_MAX {
        SIGNED_MAX as s16
    } else {
        value as s16
    }
}

// Packed return layout, least-significant bit first:
//   [15:0]  next envelope state
//   [16]    next gate state
//   [32:17] output 0: source passthrough
//   [48:33] output 1: envelope
//   [64:49] output 2: 5 V gate
//   [80:65] output 3: instantaneous magnitude
pub fn reactor_step(
    sample: s16,
    threshold_cv: s16,
    previous_envelope: u16,
    previous_gate: u1,
) -> uN[81] {
    let sample_magnitude = magnitude(sample);
    let requested_threshold = magnitude(threshold_cv);
    let threshold = if requested_threshold < THRESHOLD_DEADBAND {
        DEFAULT_THRESHOLD
    } else {
        requested_threshold
    };
    let threshold_low = threshold - (threshold >> u4:3);
    let next_envelope = envelope_step(sample_magnitude, previous_envelope);
    let next_gate = if previous_gate == u1:0 {
        (next_envelope >= threshold) as u1
    } else {
        (next_envelope > threshold_low) as u1
    };

    let envelope_out = clip_to_positive_s16(next_envelope);
    let magnitude_out = clip_to_positive_s16(sample_magnitude);
    let gate_out = if next_gate == u1:1 { GATE_HIGH } else { s16:0 };

    (next_envelope as uN[81])
        | ((next_gate as uN[81]) << u32:16)
        | (((sample as u16) as uN[81]) << u32:17)
        | (((envelope_out as u16) as uN[81]) << u32:33)
        | (((gate_out as u16) as uN[81]) << u32:49)
        | (((magnitude_out as u16) as uN[81]) << u32:65)
}
