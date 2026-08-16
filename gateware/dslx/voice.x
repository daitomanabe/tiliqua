// Copyright (c) 2026 Daito Manabe
//
// SPDX-License-Identifier: CERN-OHL-S-2.0

// Resource-aware oscillator voice shaping. Waveform selection happens before
// drive so a later variable-gain VCA only needs to process one signal.

const S16_MIN_WIDE = s19:-32768;
const S16_MAX_WIDE = s19:32767;

fn select_waveform(
    saw: s16,
    triangle: s16,
    square: s16,
    waveform: u2,
) -> s16 {
    match waveform {
        u2:0 => saw,
        u2:1 => triangle,
        u2:2 => square,
        _ => s16:0,
    }
}

fn apply_drive(sample: s16, drive: u2) -> s19 {
    let wide = sample as s19;
    match drive {
        u2:0 => wide,
        u2:1 => wide << u3:1,
        u2:2 => wide << u3:2,
        _ => wide << u3:3,
    }
}

fn saturate_s16(value: s19) -> s16 {
    if value > S16_MAX_WIDE {
        s16:32767
    } else if value < S16_MIN_WIDE {
        s16:-32768
    } else {
        value as s16
    }
}

// waveform: 0=saw, 1=triangle, 2=square, 3=silence.
// drive:    0=x1, 1=x2, 2=x4, 3=x8 with explicit signed saturation.
pub fn voice_sample(
    saw: s16,
    triangle: s16,
    square: s16,
    waveform: u2,
    drive: u2,
) -> s16 {
    saturate_s16(apply_drive(
        select_waveform(saw, triangle, square, waveform),
        drive,
    ))
}
