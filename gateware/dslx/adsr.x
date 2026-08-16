// Copyright (c) 2026 Daito Manabe
//
// SPDX-License-Identifier: CERN-OHL-S-2.0

// One deterministic ADSR control-rate step. The caller owns the state and
// feeds the returned gate/phase/level into the next accepted audio sample.

const PHASE_IDLE = u3:0;
const PHASE_ATTACK = u3:1;
const PHASE_DECAY = u3:2;
const PHASE_SUSTAIN = u3:3;
const PHASE_RELEASE = u3:4;

fn attack_step(level: u16, step: u16) -> (u16, u3) {
    let wide = (level as u17) + (step as u17);
    if wide >= u17:65535 {
        (u16:65535, PHASE_DECAY)
    } else {
        (wide as u16, PHASE_ATTACK)
    }
}

fn decay_step(level: u16, step: u16, sustain: u16) -> (u16, u3) {
    if level <= sustain || step >= level - sustain {
        (sustain, PHASE_SUSTAIN)
    } else {
        (level - step, PHASE_DECAY)
    }
}

fn release_step(level: u16, step: u16) -> (u16, u3) {
    if level <= step {
        (u16:0, PHASE_IDLE)
    } else {
        (level - step, PHASE_RELEASE)
    }
}

// Packed return value:
//   bits 15:0  = next envelope level (unsigned Q0.16)
//   bits 18:16 = next phase (0 idle, 1 attack, 2 decay, 3 sustain, 4 release)
//   bit  19    = gate state to feed into the next call
pub fn adsr_step(
    gate: u1,
    previous_gate: u1,
    phase: u3,
    level: u16,
    attack: u16,
    decay: u16,
    sustain: u16,
    release_amount: u16,
) -> u20 {
    let rising = gate == u1:1 && previous_gate == u1:0;
    let falling = gate == u1:0 && previous_gate == u1:1;
    let active_phase = if rising {
        PHASE_ATTACK
    } else if falling {
        PHASE_RELEASE
    } else {
        phase
    };

    let next = match active_phase {
        PHASE_ATTACK => attack_step(level, attack),
        PHASE_DECAY => decay_step(level, decay, sustain),
        PHASE_SUSTAIN => (sustain, PHASE_SUSTAIN),
        PHASE_RELEASE => release_step(level, release_amount),
        _ => (u16:0, PHASE_IDLE),
    };
    let next_level = next.0;
    let next_phase = next.1;
    ((gate as u20) << u5:19)
        | ((next_phase as u20) << u5:16)
        | (next_level as u20)
}
