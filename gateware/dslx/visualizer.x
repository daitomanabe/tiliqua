// Copyright (c) 2026 Daito Manabe
//
// SPDX-License-Identifier: CERN-OHL-S-2.0

// A deliberately small combinational "pixel shader" for the first DSLX
// audio/video tutorial. RGB is packed as 0xBBGGRR so the Amaranth wrapper can
// expose each byte without changing the generated logic.

fn abs_diff(a: u12, b: u12) -> u12 {
    if a > b { a - b } else { b - a }
}

pub fn visualizer_pixel(
    x: u12,
    y: u12,
    center_x: u12,
    center_y: u12,
    envelope: u16,
    gate: u1,
    magnitude: u16,
    frame: u8,
) -> u24 {
    let dx = abs_diff(x, center_x);
    let dy = abs_diff(y, center_y);

    // Envelope grows a center square from 32 to 95 pixels in radius.
    let radius = u12:32 + ((envelope >> u5:9) as u12);
    let inside_pulse = dx < radius && dy < radius;

    // Magnitude widens a cross from 8 to 39 pixels.
    let cross_width = u12:8 + ((magnitude >> u5:10) as u12);
    let inside_cross = dx < cross_width || dy < cross_width;

    // A slowly moving 32-pixel checker provides visible output at silence.
    let phase_x = x + ((frame as u12) << u3:2);
    let checker = phase_x[5:6] ^ y[5:6];

    let envelope_u8 = (envelope >> u5:7) as u8;
    let magnitude_u8 = (magnitude >> u5:7) as u8;

    let red = if gate == u1:1 {
        u8:64 + (envelope_u8 >> u3:2)
    } else {
        u8:8 + (envelope_u8 >> u3:4)
    };
    let green = if inside_pulse {
        envelope_u8
    } else if checker == u1:1 {
        magnitude_u8 >> u3:2
    } else {
        u8:4
    };
    let blue = if inside_cross {
        u8:64 + (magnitude_u8 >> u3:2)
    } else if checker == u1:1 {
        u8:24
    } else {
        u8:8
    };

    (red as u24)
        | ((green as u24) << u5:8)
        | ((blue as u24) << u5:16)
}
