# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Structurally independent integer reference model for SNN2 v1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .manifest import NEURON_COUNT, validate_manifest


def _sat(value: int, minimum: int, maximum: int) -> int:
    return min(max(value, minimum), maximum)


def sat_s18(value: int) -> int:
    return _sat(value, -(1 << 17), (1 << 17) - 1)


def sat_s16(value: int) -> int:
    return _sat(value, -(1 << 15), (1 << 15) - 1)


def sat_u16(value: int) -> int:
    return _sat(value, 0, (1 << 16) - 1)


@dataclass(frozen=True)
class ALIFState:
    v: int = 0
    ie: int = 0
    ii: int = 0
    a: int = 0
    r: int = 0
    s: int = 0


@dataclass(frozen=True)
class SNN2EncoderOutput:
    encoder_spikes: int
    external_drive: int
    inhibitory_gain_q8: int
    adaptation_gain_q8: int
    band_levels: tuple[int, ...]


def map_control_q8(
    sample: int,
    *,
    low: int,
    center: int,
    high: int,
    slope_numerator: int,
    slope_shift: int,
    clamp: int = 4_000,
) -> int:
    """Map calibrated ASQ control using the frozen shift-add approximation.

    The two endpoints are explicit. Interior values use an arithmetic right
    shift, matching the synthesizable RTL without inferring a divider.
    """

    if sample <= -clamp:
        return low
    if sample >= clamp:
        return high
    mapped = center + ((sample * slope_numerator) >> slope_shift)
    return _sat(mapped, low, high)


class SNN2EncoderReference:
    """Normative serial-equivalent Q2.14 analysis bank and rate encoder."""

    def __init__(self, manifest: dict) -> None:
        self.manifest = validate_manifest(manifest)
        self.encoder = self.manifest["encoder"]
        self.z1 = [0] * 8
        self.z2 = [0] * 8
        self.phase = [0] * 8
        self.last_band_samples = (0,) * 8
        self.sample_index = 0

    def step(self, samples: Sequence[int]) -> SNN2EncoderOutput:
        if len(samples) != 4:
            raise ValueError("SNN2 encoder requires exactly four ASQ samples")
        if any(not -(1 << 15) <= sample <= (1 << 15) - 1 for sample in samples):
            raise ValueError("all SNN2 encoder inputs must fit signed 16-bit ASQ")

        clamp = self.encoder["control_clamp_asq"]
        gain_mapping = self.encoder["control_mapping"]["gain"]
        adaptation_mapping = self.encoder["control_mapping"]["adaptation"]
        encoder_gain = map_control_q8(
            samples[1], clamp=clamp, **{
                key.removesuffix("_q8"): value
                for key, value in gain_mapping.items()
            }
        )
        inhibitory_gain = map_control_q8(
            samples[2], clamp=clamp, **{
                key.removesuffix("_q8"): value
                for key, value in gain_mapping.items()
            }
        )
        adaptation_gain = map_control_q8(
            samples[3], clamp=clamp, **{
                key.removesuffix("_q8"): value
                for key, value in adaptation_mapping.items()
            }
        )
        phase_limit = 1 << self.encoder["phase_bits"]
        phase_mask = phase_limit - 1
        spikes = 0
        levels = []
        band_samples = []

        x = samples[0]
        for band, coefficients in enumerate(
            self.encoder["filter_coefficients_q2_14"]
        ):
            y = sat_s18(((coefficients["b0"] * x) >> 14) + self.z1[band])
            z1 = sat_s18(
                ((coefficients["b1"] * x) >> 14)
                - ((coefficients["a1"] * y) >> 14)
                + self.z2[band]
            )
            z2 = sat_s18(
                ((coefficients["b2"] * x) >> 14)
                - ((coefficients["a2"] * y) >> 14)
            )
            self.z1[band] = z1
            self.z2[band] = z2
            band_samples.append(y)

            magnitude = abs(y)
            increment = min(
                (magnitude * encoder_gain)
                >> self.encoder["phase_increment_shift"],
                phase_mask,
            )
            phase_sum = self.phase[band] + increment
            if phase_sum >= phase_limit:
                spikes |= 1 << band
            self.phase[band] = phase_sum & phase_mask
            levels.append(min(magnitude >> 5, 255))

        self.sample_index += 1
        self.last_band_samples = tuple(band_samples)
        return SNN2EncoderOutput(
            encoder_spikes=spikes,
            external_drive=(self.encoder["drive_base"] * encoder_gain) >> 8,
            inhibitory_gain_q8=inhibitory_gain,
            adaptation_gain_q8=adaptation_gain,
            band_levels=tuple(levels),
        )


def step_alif_neuron(
    state: ALIFState,
    *,
    excitatory_events: int,
    inhibitory_events: int,
    external_drive: int,
    bias: int,
    dynamics: dict[str, int],
    adaptation_gain_q8: int = 256,
) -> ALIFState:
    """Apply the normative SNN2 recurrence once with widened intermediates."""

    ie_next = sat_u16(
        state.ie - (state.ie >> dynamics["tau_e"]) + excitatory_events
    )
    ii_next = sat_u16(
        state.ii - (state.ii >> dynamics["tau_i"]) + inhibitory_events
    )
    a_decay = sat_u16(state.a - (state.a >> dynamics["tau_a"]))
    v_leak = state.v - (state.v >> dynamics["tau_m"])
    v_candidate = sat_s18(
        v_leak + ie_next - ii_next + external_drive + bias
    )
    adaptive_threshold = dynamics["threshold_base"] + a_decay
    adaptation_step = (
        dynamics["adaptation_step"] * adaptation_gain_q8
    ) >> 8

    if state.r:
        return ALIFState(
            v=dynamics["reset_level"],
            ie=ie_next,
            ii=ii_next,
            a=a_decay,
            r=state.r - 1,
            s=0,
        )
    if v_candidate >= adaptive_threshold:
        return ALIFState(
            v=dynamics["reset_level"],
            ie=ie_next,
            ii=ii_next,
            a=sat_u16(a_decay + adaptation_step),
            r=dynamics["refractory_samples"],
            s=1,
        )
    return ALIFState(
        v=v_candidate,
        ie=ie_next,
        ii=ii_next,
        a=a_decay,
        r=0,
        s=0,
    )


class SNN2Reference:
    """Integer oracle for manifest, scheduler, ALIF state, and four readouts."""

    def __init__(
        self, manifest: dict, *, initial_spikes: int = 0,
    ) -> None:
        self.manifest = validate_manifest(manifest)
        self.dynamics = self.manifest["dynamics"]
        self.states = [
            ALIFState(
                v=neuron["initial_v"],
                ie=neuron["initial_ie"],
                ii=neuron["initial_ii"],
                a=neuron["initial_a"],
                r=neuron["initial_r"],
                s=(initial_spikes >> index) & 1,
            )
            for index, neuron in enumerate(self.manifest["neurons"])
        ]
        self.spike_vector = initial_spikes & ((1 << NEURON_COUNT) - 1)
        self.sample_index = 0
        self.previous_readout = 0
        self.dc_state = 0
        self.excitatory_lp = 0
        self.inhibitory_lp = 0
        self.last_event_count = 0
        self.last_outputs = (0, 0, 0, 0)

    def _schedule(self, inhibitory_gain_q8: int) -> tuple[list[int], list[int], int]:
        excitatory = [0] * NEURON_COUNT
        inhibitory = [0] * NEURON_COUNT
        event_count = 0
        edges = self.manifest["edges"]
        for source in range(NEURON_COUNT):
            if not ((self.spike_vector >> source) & 1):
                continue
            for edge in edges[source * 8:(source + 1) * 8]:
                target = edge["target"]
                if edge["weight"] > 0:
                    excitatory[target] += edge["weight"]
                else:
                    magnitude = ((-edge["weight"]) * inhibitory_gain_q8) >> 8
                    inhibitory[target] += magnitude
                event_count += 1
        return (
            [sat_u16(value) for value in excitatory],
            [sat_u16(value) for value in inhibitory],
            event_count,
        )

    def step(
        self,
        *,
        encoder_spikes: int,
        external_drive: int,
        inhibitory_gain_q8: int = 256,
        adaptation_gain_q8: int = 256,
    ) -> tuple[int, int, int, int]:
        if not 0 <= encoder_spikes <= 0xFF:
            raise ValueError("encoder_spikes must be an 8-bit mask")
        if not -(1 << 17) <= external_drive <= (1 << 17) - 1:
            raise ValueError("external_drive must fit signed 18-bit")
        if not 128 <= inhibitory_gain_q8 <= 512:
            raise ValueError("inhibitory_gain_q8 must be 128..512")
        if not 0 <= adaptation_gain_q8 <= 512:
            raise ValueError("adaptation_gain_q8 must be 0..512")

        events_e, events_i, event_count = self._schedule(inhibitory_gain_q8)
        next_states: list[ALIFState] = []
        for index, (state, neuron) in enumerate(
            zip(self.states, self.manifest["neurons"])
        ):
            direct = 0
            if index % 4 != 3:
                excitatory_rank = index - ((index + 1) // 4)
                if (encoder_spikes >> (excitatory_rank // 24)) & 1:
                    direct = external_drive
            next_states.append(step_alif_neuron(
                state,
                excitatory_events=events_e[index],
                inhibitory_events=events_i[index],
                external_drive=direct,
                bias=neuron["bias"],
                dynamics=self.dynamics,
                adaptation_gain_q8=adaptation_gain_q8,
            ))

        self.states = next_states
        self.spike_vector = sum(
            state.s << index for index, state in enumerate(next_states)
        )
        self.last_event_count = event_count
        self.sample_index += 1

        excitatory_count = sum(
            state.s for index, state in enumerate(next_states) if index % 4 != 3
        )
        inhibitory_count = sum(
            state.s for index, state in enumerate(next_states) if index % 4 == 3
        )
        raw_readout = sum(
            state.s * weight
            for state, weight in zip(next_states, self.manifest["readout_weights"])
        )
        dc_candidate = (
            raw_readout - self.previous_readout
            + self.dc_state - (self.dc_state >> 8)
        )
        self.previous_readout = raw_readout
        self.dc_state = sat_s18(dc_candidate)

        excitatory_target = excitatory_count * 100
        inhibitory_target = inhibitory_count * 300
        self.excitatory_lp += (
            excitatory_target - self.excitatory_lp
        ) >> 5
        self.inhibitory_lp += (
            inhibitory_target - self.inhibitory_lp
        ) >> 5
        balance = excitatory_target - inhibitory_target
        self.last_outputs = (
            sat_s16(self.dc_state),
            _sat(self.excitatory_lp, 0, 20_000),
            _sat(self.inhibitory_lp, 0, 20_000),
            _sat(balance, -20_000, 20_000),
        )
        return self.last_outputs

    def snapshot(self) -> tuple[ALIFState, ...]:
        return tuple(self.states)

    def run(
        self,
        fixtures: Iterable[tuple[int, int, int, int]],
    ) -> list[tuple[int, int, int, int]]:
        return [
            self.step(
                encoder_spikes=encoder_spikes,
                external_drive=external_drive,
                inhibitory_gain_q8=inhibitory_gain,
                adaptation_gain_q8=adaptation_gain,
            )
            for (
                encoder_spikes,
                external_drive,
                inhibitory_gain,
                adaptation_gain,
            ) in fixtures
        ]


def states_to_rows(states: Sequence[ALIFState]) -> list[dict[str, int]]:
    return [
        {"v": state.v, "ie": state.ie, "ii": state.ii,
         "a": state.a, "r": state.r, "s": state.s}
        for state in states
    ]
