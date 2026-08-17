# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Parallel leaky-integrate-and-fire network for audio-rate experiments."""

from amaranth import Array, Cat, Const, Module, Mux, Signal, signed, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.memory import Memory
from amaranth.lib.wiring import In, Out

from . import ASQ, asq_from_volts
from .synth import midi_note_phase_increment


def balanced_sum(values):
    """Build a shallow adder tree instead of a linear combinational chain."""

    level = list(values)
    if not level:
        return Const(0)
    while len(level) > 1:
        next_level = []
        for index in range(0, len(level), 2):
            if index + 1 < len(level):
                next_level.append(level[index] + level[index + 1])
            else:
                next_level.append(level[index])
        level = next_level
    return level[0]


class PopulationEnsembleSonifier(wiring.Component):
    """Turn total, excitatory, inhibitory, and balance activity into four voices.

    The diagnostic SNN emits a bipolar pulse whose amplitude is the total
    spike count. That signal is useful for measurement, but perceptually it is
    close to broadband noise. This optional mapper samples the E/I population
    at a deliberately slow control rate and selects four related C-minor
    pentatonic notes: total activity, excitatory activity, inhibitory activity,
    and normalized E/I balance. Each voice has an independent phase-continuous
    triangle oscillator, so all four physical outputs are musically useful.

    One input transfer advances exactly one sample in every oscillator,
    preserving stream backpressure semantics.
    """

    VOICE_NOTES = (
        (36, 39, 41, 43, 46, 48, 51, 53),
        (48, 51, 53, 55, 58, 60, 63, 65),
        (43, 46, 48, 51, 53, 55, 58, 60),
        (24, 27, 29, 31, 34, 34, 34, 34),
    )

    def __init__(
        self, *, neuron_count=1024, sample_rate=48_000,
        control_period_samples=4096,
    ):
        if neuron_count != 1024:
            raise ValueError("population ensemble sonifier requires 1024 neurons")
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if (
            control_period_samples < 2
            or control_period_samples & (control_period_samples - 1)
        ):
            raise ValueError("control_period_samples must be a power of two >= 2")
        self.neuron_count = neuron_count
        self.sample_rate = sample_rate
        self.control_period_samples = control_period_samples
        self._increments = tuple(
            tuple(
                midi_note_phase_increment(note, sample_rate)
                for note in voice_notes
            )
            for voice_notes in self.VOICE_NOTES
        )
        self.phases = [
            Signal(32, init=phase)
            for phase in (0x00000000, 0x40000000, 0x80000000, 0xC0000000)
        ]
        self.note_indices = [Signal(3, init=2) for _ in range(4)]
        count_bits = (neuron_count + 1).bit_length()
        super().__init__({
            "i": In(stream.Signature(data.ArrayLayout(ASQ, 4))),
            "spike_count": In(unsigned(count_bits)),
            "excitatory_spike_count": In(unsigned(10)),
            "inhibitory_spike_count": In(unsigned(9)),
            "o": Out(stream.Signature(data.ArrayLayout(ASQ, 4))),
        })

    def elaborate(self, platform):
        m = Module()

        control_counter = Signal(range(self.control_period_samples))
        balance_control_divider = Signal(2)
        note_increments = [
            Array(Const(value, 32) for value in voice_increments)
            for voice_increments in self._increments
        ]
        sampled_note_indices = [Signal(3) for _ in range(4)]
        normalized_inhibition = Signal(10)

        # The population-study fixture measures roughly 23..64 excitatory and
        # 8..22 inhibitory spikes per sample under low/high drive. Independent
        # offsets and steps keep each voice mobile across that useful range.
        # The fourth voice compares I*3 with E, correcting the ring's 3:1
        # population-size bias before selecting a bass register.
        m.d.comb += [
            normalized_inhibition.eq(
                (self.inhibitory_spike_count << 1)
                + self.inhibitory_spike_count
            ),
            sampled_note_indices[0].eq(Mux(
                self.spike_count >= 112,
                7,
                self.spike_count >> 4,
            )),
            sampled_note_indices[1].eq(Mux(
                self.excitatory_spike_count >= 72,
                7,
                Mux(
                    self.excitatory_spike_count <= 16,
                    0,
                    (self.excitatory_spike_count - 16) >> 3,
                ),
            )),
            sampled_note_indices[2].eq(Mux(
                self.inhibitory_spike_count >= 28,
                7,
                self.inhibitory_spike_count >> 2,
            )),
            sampled_note_indices[3].eq(Mux(
                normalized_inhibition >= self.excitatory_spike_count + 8,
                4,
                Mux(
                    normalized_inhibition >= self.excitatory_spike_count + 2,
                    3,
                    Mux(
                        self.excitatory_spike_count >= normalized_inhibition + 8,
                        0,
                        Mux(
                            self.excitatory_spike_count
                            >= normalized_inhibition + 2,
                            1,
                            2,
                        ),
                    ),
                ),
            )),
            self.o.valid.eq(self.i.valid),
            self.i.ready.eq(self.o.ready),
        ]

        for channel, phase in enumerate(self.phases):
            triangle_folded = Signal(16)
            triangle_full = Signal(signed(16))
            triangle_quiet = Signal(signed(16))
            m.d.comb += [
                triangle_folded.eq(Mux(
                    phase[31],
                    ~phase[15:31],
                    phase[15:31],
                )),
                triangle_full.eq(triangle_folded ^ Const(0x8000, 16)),
                # Half scale is about 4.096 V peak in calibrated ASQ units.
                # Give the inhibitory voice 9/16 scale because it represents
                # one third as many neurons and otherwise recedes too far when
                # the four physical outputs are monitored at equal gain.
                triangle_quiet.eq(
                    (triangle_full >> 1) + (
                        (triangle_full >> 4) if channel == 2 else 0
                    )
                ),
                self.o.payload[channel].as_value().eq(triangle_quiet),
            ]

        with m.If(self.i.valid & self.o.ready):
            for phase, note_index, increments in zip(
                self.phases, self.note_indices, note_increments
            ):
                m.d.sync += phase.eq(phase + increments[note_index])
            with m.If(control_counter == self.control_period_samples - 1):
                m.d.sync += control_counter.eq(0)
                for note_index, sampled_note_index in zip(
                    self.note_indices[:3], sampled_note_indices[:3]
                ):
                    m.d.sync += note_index.eq(sampled_note_index)
                with m.If(balance_control_divider == 3):
                    m.d.sync += [
                        balance_control_divider.eq(0),
                        self.note_indices[3].eq(sampled_note_indices[3]),
                    ]
                with m.Else():
                    m.d.sync += balance_control_divider.eq(
                        balance_control_divider + 1
                    )
            with m.Else():
                m.d.sync += control_counter.eq(control_counter + 1)

        return m


class PopulationCVConductor(wiring.Component):
    """Map SNN population state to four slow, unipolar modular CV outputs.

    The four outputs form one shared conductor bus for a larger modular system:
    an 8 Hz master clock, C-minor-pentatonic 1 V/oct pitch, a density-controlled
    gate, and a smoothed population-activity modulation voltage. All channels
    stay between 0 and +5 V. Pitch and modulation follow stream transfers;
    hardware clock and gate timing use the stable sync-domain wall clock.
    """

    PITCH_VOLTS = (0.0, 0.25, 5 / 12, 7 / 12, 10 / 12, 1.0, 1.25, 17 / 12)
    PITCH_ASQ = tuple(round(volts * 4000) for volts in PITCH_VOLTS)
    FIVE_VOLTS_ASQ = 20_000
    EUCLIDEAN_ORDER = (0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15)

    def __init__(
        self, *, neuron_count=1024, sample_rate=48_000,
        clock_period_samples=6000, clock_pulse_samples=480,
        gate_high_samples=3000, wall_clock_hz=None,
    ):
        if neuron_count != 1024:
            raise ValueError("population CV conductor requires 1024 neurons")
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if clock_period_samples < 16:
            raise ValueError("clock period must be at least 16 samples")
        if not 1 <= clock_pulse_samples < clock_period_samples:
            raise ValueError("clock pulse must fit inside one period")
        if not 1 <= gate_high_samples <= clock_period_samples:
            raise ValueError("gate high time must fit inside one period")
        if wall_clock_hz is not None and wall_clock_hz < 800:
            raise ValueError("wall clock must be at least 800 Hz")
        self.neuron_count = neuron_count
        self.sample_rate = sample_rate
        self.wall_clock_hz = wall_clock_hz
        if wall_clock_hz is None:
            self.clock_period_ticks = clock_period_samples
            self.clock_pulse_ticks = clock_pulse_samples
            self.gate_high_ticks = gate_high_samples
        else:
            # Tempo must not follow SNN stream backpressure. Hardware uses the
            # stable sync PLL for an exact 8 Hz clock, 10 ms trigger, and
            # 62.5 ms density gate. The transfer-count mode remains available
            # for compact simulations and stream-only unit tests.
            self.clock_period_ticks = round(wall_clock_hz / 8)
            self.clock_pulse_ticks = round(wall_clock_hz / 100)
            self.gate_high_ticks = round(wall_clock_hz / 16)
        self.clock_counter = Signal(range(self.clock_period_ticks))
        self.step = Signal(4)
        self.pitch_index = Signal(3, init=2)
        self.gate_remaining = Signal(range(self.gate_high_ticks + 1))
        self.modulation_cv = Signal(range(self.FIVE_VOLTS_ASQ + 1))
        count_bits = (neuron_count + 1).bit_length()
        super().__init__({
            "i": In(stream.Signature(data.ArrayLayout(ASQ, 4))),
            "spike_count": In(unsigned(count_bits)),
            "o": Out(stream.Signature(data.ArrayLayout(ASQ, 4))),
        })

    def elaborate(self, platform):
        m = Module()

        pitch_values = Array(Const(value, signed(16)) for value in self.PITCH_ASQ)
        pattern_values = Array(Const(value, 4) for value in self.EUCLIDEAN_ORDER)
        next_pitch_index = Signal(3)
        gate_density = Signal(4)
        modulation_target_wide = Signal(18)
        modulation_target = Signal(range(self.FIVE_VOLTS_ASQ + 1))
        transfer = Signal()
        five_volts = self.FIVE_VOLTS_ASQ

        m.d.comb += [
            transfer.eq(self.i.valid & self.o.ready),
            self.o.valid.eq(self.i.valid),
            self.i.ready.eq(self.o.ready),
            next_pitch_index.eq(Mux(
                self.spike_count >= 112,
                7,
                self.spike_count >> 4,
            )),
            gate_density.eq(Mux(
                self.spike_count < 16,
                2,
                Mux(self.spike_count >= 96, 12, self.spike_count >> 3),
            )),
            modulation_target_wide.eq(self.spike_count << 7),
            modulation_target.eq(Mux(
                modulation_target_wide > five_volts,
                five_volts,
                modulation_target_wide,
            )),
            self.o.payload[0].as_value().eq(Mux(
                self.clock_counter < self.clock_pulse_ticks,
                five_volts,
                0,
            )),
            self.o.payload[1].as_value().eq(pitch_values[self.pitch_index]),
            self.o.payload[2].as_value().eq(Mux(
                self.gate_remaining != 0,
                five_volts,
                0,
            )),
            self.o.payload[3].as_value().eq(self.modulation_cv),
        ]

        advance_clock = Const(1) if self.wall_clock_hz is not None else transfer
        with m.If(advance_clock):
            with m.If(self.clock_counter == self.clock_period_ticks - 1):
                m.d.sync += [
                    self.clock_counter.eq(0),
                    self.step.eq(self.step + 1),
                    self.pitch_index.eq(next_pitch_index),
                    self.gate_remaining.eq(Mux(
                        pattern_values[self.step] < gate_density,
                        self.gate_high_ticks,
                        0,
                    )),
                ]
            with m.Else():
                m.d.sync += self.clock_counter.eq(self.clock_counter + 1)
                with m.If(self.gate_remaining != 0):
                    m.d.sync += self.gate_remaining.eq(
                        self.gate_remaining - 1
                    )

        with m.If(transfer):
            with m.If(modulation_target > self.modulation_cv):
                m.d.sync += self.modulation_cv.eq(
                    self.modulation_cv
                    + ((modulation_target - self.modulation_cv) >> 10)
                    + 1
                )
            with m.Elif(modulation_target < self.modulation_cv):
                m.d.sync += self.modulation_cv.eq(
                    self.modulation_cv
                    - ((self.modulation_cv - modulation_target) >> 10)
                    - 1
                )

        return m


class ParallelLIFBank(wiring.Component):
    """Update many integer LIF neurons in parallel for every audio sample.

    The network deliberately uses only add, subtract, compare, and shifts.
    Each neuron has a distinct bias and threshold, receives the previous
    population spike count, and is excited by its left neighbor's previous
    spike. This small recurrent topology produces deterministic collective
    rhythms without multipliers or RAM arbitration.
    """

    def __init__(
        self, *, neuron_count=64, leak_shift=5, ei_ring=False,
        inhibitory_strength=1024,
    ):
        if neuron_count < 8 or neuron_count > 1024 or neuron_count & (neuron_count - 1):
            raise ValueError("neuron_count must be a power of two in the range 8..1024")
        if not 2 <= leak_shift <= 8:
            raise ValueError("leak_shift must be in the range 2..8")
        if inhibitory_strength not in range(256, 2049, 256):
            raise ValueError("inhibitory_strength must be 256..2048 in steps of 256")
        self.neuron_count = neuron_count
        self.leak_shift = leak_shift
        self.ei_ring = ei_ring
        self.inhibitory_strength = inhibitory_strength
        self.count_bits = (neuron_count + 1).bit_length()
        super().__init__({
            "i": In(stream.Signature(data.ArrayLayout(ASQ, 4))),
            "o": Out(stream.Signature(data.ArrayLayout(ASQ, 4))),
        })

        self.membranes = []
        for index in range(neuron_count):
            threshold = self.threshold_for(index)
            self.membranes.append(
                Signal(16, init=(index * 997 + 313) % threshold, name=f"membrane_{index}")
            )
        self.spike_vector = Signal(neuron_count)
        self.membrane_levels = Signal(neuron_count * 4)
        self.spike_count = Signal(range(neuron_count + 1))
        self.sample_index = Signal(32)
        self.leak_mode = Signal(2, init=1)
        self.recurrent_mode = Signal(2, init=1)
        self.threshold_mode = Signal(2, init=1)

    @staticmethod
    def threshold_for(index):
        return 7_000 + (index % 16) * 400

    @staticmethod
    def bias_for(index):
        return 180 + (index % 8) * 24

    def elaborate(self, platform):
        m = Module()

        output_valid = Signal()
        output_payload = Signal(data.ArrayLayout(ASQ, 4))
        accept_input = Signal()
        pending_update = Signal()
        pending_groups = Signal()
        pending_spikes = Signal()
        pending_output = Signal()

        input_sample = self.i.payload[0].as_value()
        input_magnitude = Signal(16)
        next_input_drive = Signal(11)
        input_drive = Signal(11)
        leak_control = self.i.payload[1].as_value()
        recurrent_control = self.i.payload[2].as_value()
        threshold_control = self.i.payload[3].as_value()
        next_leak_mode = Signal(2)
        next_recurrent_mode = Signal(2)
        next_threshold_mode = Signal(2)
        m.d.comb += [
            input_magnitude.eq(Mux(
                input_sample == -32768,
                Const(32768, 16),
                Mux(input_sample < 0, -input_sample, input_sample),
            )),
            next_input_drive.eq(input_magnitude >> 5),
            next_leak_mode.eq(Mux(
                leak_control < -1000, 0, Mux(leak_control > 1000, 2, 1)
            )),
            next_recurrent_mode.eq(Mux(
                recurrent_control < -1000,
                0,
                Mux(recurrent_control > 1000, 2, 1),
            )),
            next_threshold_mode.eq(Mux(
                threshold_control < -1000,
                0,
                Mux(threshold_control > 1000, 2, 1),
            )),
            accept_input.eq(
                ~pending_update & ~pending_groups & ~pending_spikes & ~pending_output
                & (~output_valid | self.o.ready)
            ),
            self.i.ready.eq(accept_input),
            self.o.valid.eq(output_valid),
            self.o.payload.eq(output_payload),
            self.membrane_levels.eq(Cat(*[
                membrane[12:16] for membrane in self.membranes
            ])),
        ]

        next_spikes = []
        next_membranes = []
        leak_amounts = []
        next_leak_amounts = []
        dynamic_thresholds = []
        next_dynamic_thresholds = []
        for threshold_index in range(16):
            threshold = self.threshold_for(threshold_index)
            dynamic_threshold = Signal(14, name=f"dynamic_threshold_{threshold_index}")
            next_dynamic_threshold = Signal(
                14, name=f"next_dynamic_threshold_{threshold_index}"
            )
            m.d.comb += next_dynamic_threshold.eq(Mux(
                next_threshold_mode == 0,
                threshold - 800,
                Mux(next_threshold_mode == 2, threshold + 800, threshold),
            ))
            dynamic_thresholds.append(dynamic_threshold)
            next_dynamic_thresholds.append(next_dynamic_threshold)

        recurrent_drive = Signal(self.count_bits + 3)
        next_recurrent_drive = Signal.like(recurrent_drive)
        m.d.comb += next_recurrent_drive.eq(Mux(
            next_recurrent_mode == 0,
            self.spike_count << 1,
            Mux(
                next_recurrent_mode == 2,
                self.spike_count << 3,
                self.spike_count << 2,
            ),
        ))
        for index, membrane in enumerate(self.membranes):
            candidate_base = Signal(18, name=f"candidate_base_{index}")
            candidate = Signal(18, name=f"candidate_{index}")
            spike = Signal(name=f"next_spike_{index}")
            leak_amount = Signal(12, name=f"leak_amount_{index}")
            next_leak_amount = Signal(12, name=f"next_leak_amount_{index}")
            neighbor = self.spike_vector[(index - 1) % self.neuron_count]
            dynamic_threshold = dynamic_thresholds[index % 16]
            reset_level = (index * 37 + 101) & 0x1FF
            m.d.comb += [
                next_leak_amount.eq(Mux(
                    next_leak_mode == 0,
                    membrane >> 6,
                    Mux(
                        next_leak_mode == 2,
                        membrane >> 4,
                        membrane >> self.leak_shift,
                    ),
                )),
                candidate_base.eq(
                    membrane
                    - leak_amount
                    + self.bias_for(index)
                    + input_drive
                    + recurrent_drive
                ),
                spike.eq(candidate >= dynamic_threshold),
            ]
            if self.ei_ring and index % 4 == 0 and self.inhibitory_strength == 1024:
                m.d.comb += candidate.eq(Mux(
                    neighbor,
                    Mux(candidate_base >= 1024, candidate_base - 1024, 0),
                    candidate_base,
                ))
            elif self.ei_ring and index % 4 == 0:
                m.d.comb += candidate.eq(Mux(
                    neighbor,
                    Mux(
                        candidate_base >= self.inhibitory_strength,
                        candidate_base - self.inhibitory_strength,
                        0,
                    ),
                    candidate_base,
                ))
            else:
                m.d.comb += candidate.eq(
                    candidate_base + Mux(neighbor, 256, 0)
                )
            leak_amounts.append(leak_amount)
            next_leak_amounts.append(next_leak_amount)
            next_spikes.append(spike)
            next_membranes.append(Mux(spike, reset_level, candidate[:16]))

        next_spike_vector = Cat(*next_spikes)
        group_size = 8
        group_spike_counts = []
        group_membrane_sums = []
        next_group_spike_counts = []
        next_group_membrane_sums = []
        for group_start in range(0, self.neuron_count, group_size):
            group_stop = group_start + group_size
            group_spike_count = Signal(range(group_size + 1))
            group_membrane_sum = Signal(range(group_size * 15 + 1))
            group_spike_counts.append(group_spike_count)
            group_membrane_sums.append(group_membrane_sum)
            next_group_spike_counts.append(balanced_sum([
                self.spike_vector[index]
                for index in range(group_start, group_stop)
            ]))
            next_group_membrane_sums.append(balanced_sum([
                self.membranes[index][12:16]
                for index in range(group_start, group_stop)
            ]))

        registered_spike_count = Signal(range(self.neuron_count + 1))
        membrane_sum = Signal(4 + self.count_bits)
        next_membrane_mean = Signal(16)
        membrane_mean = Signal(16)
        membrane_scaled = Signal(17)
        activity_scaled = Signal(self.count_bits + 9)
        pulse_amplitude = Signal(self.count_bits + 13)
        pulse_sample = Signal(signed(self.count_bits + 13))
        m.d.comb += [
            registered_spike_count.eq(balanced_sum([
                group_spike_count for group_spike_count in group_spike_counts
            ])),
            # The fourth DAC channel is a monitor, so sum the upper four bits
            # and scale the population total. The neuron state itself remains
            # 16-bit; narrowing and registering groups of eight protects timing.
            membrane_sum.eq(balanced_sum([
                group_membrane_sum for group_membrane_sum in group_membrane_sums
            ])),
            next_membrane_mean.eq(
                membrane_sum << (12 - (self.neuron_count.bit_length() - 1))
            ),
            membrane_scaled.eq(membrane_mean << 1),
            activity_scaled.eq(self.spike_count << 9),
            pulse_amplitude.eq(self.spike_count << 12),
            pulse_sample.eq(Mux(
                self.sample_index[0],
                -pulse_amplitude.as_signed(),
                pulse_amplitude.as_signed(),
            )),
        ]

        # Stage 0 registers input drive, recurrent drive, per-neuron leak, and
        # the 16 threshold classes. Stage 1 updates all
        # neurons, stage 2 registers groups of eight, stage 3 reduces the group
        # totals, and stage 4 maps population state to DAC channels. Five
        # 60 MHz cycles are negligible inside one 48 kHz audio period.
        with m.If(pending_update):
            m.d.sync += [
                pending_update.eq(0),
                pending_groups.eq(1),
                self.spike_vector.eq(next_spike_vector),
                self.sample_index.eq(self.sample_index + 1),
            ]
            for membrane, next_membrane in zip(self.membranes, next_membranes):
                m.d.sync += membrane.eq(next_membrane)
        with m.Elif(pending_groups):
            m.d.sync += [
                pending_groups.eq(0),
                pending_spikes.eq(1),
            ]
            for group_spike_count, next_group_spike_count in zip(
                group_spike_counts, next_group_spike_counts
            ):
                m.d.sync += group_spike_count.eq(next_group_spike_count)
            for group_membrane_sum, next_group_membrane_sum in zip(
                group_membrane_sums, next_group_membrane_sums
            ):
                m.d.sync += group_membrane_sum.eq(next_group_membrane_sum)
        with m.Elif(pending_spikes):
            m.d.sync += [
                pending_spikes.eq(0),
                pending_output.eq(1),
                self.spike_count.eq(registered_spike_count),
                membrane_mean.eq(next_membrane_mean),
            ]
        with m.Elif(pending_output):
            m.d.sync += [
                pending_output.eq(0),
                output_valid.eq(1),
                output_payload[0].as_value().eq(Mux(
                    pulse_sample > 32767,
                    32767,
                    Mux(pulse_sample < -32768, -32768, pulse_sample[:16]),
                )),
                output_payload[1].as_value().eq(Mux(
                    activity_scaled > 32767,
                    32767,
                    activity_scaled[:16],
                )),
                output_payload[2].eq(Mux(
                    self.spike_count >= 2,
                    asq_from_volts(5.0),
                    0,
                )),
                output_payload[3].as_value().eq(Mux(
                    membrane_scaled > 32767,
                    32767,
                    membrane_scaled[:16],
                )),
            ]
        with m.Elif(accept_input):
            m.d.sync += output_valid.eq(0)
            with m.If(self.i.valid):
                m.d.sync += [
                    pending_update.eq(1),
                    input_drive.eq(next_input_drive),
                    recurrent_drive.eq(next_recurrent_drive),
                    self.leak_mode.eq(next_leak_mode),
                    self.recurrent_mode.eq(next_recurrent_mode),
                    self.threshold_mode.eq(next_threshold_mode),
                ]
                for leak_amount, next_leak_amount in zip(
                    leak_amounts, next_leak_amounts
                ):
                    m.d.sync += leak_amount.eq(next_leak_amount)
                for dynamic_threshold, next_dynamic_threshold in zip(
                    dynamic_thresholds, next_dynamic_thresholds
                ):
                    m.d.sync += dynamic_threshold.eq(next_dynamic_threshold)

        return m


class BatchedLIFBank(wiring.Component):
    """Preserve 256 logical neurons while reusing physical update lanes.

    Both batches read the previous complete spike vector. Lower-state writes
    therefore cannot influence the upper batch in the same audio sample, and
    the result is sample-for-sample equivalent to :class:`ParallelLIFBank`.
    """

    def __init__(
        self, *, logical_neuron_count=256, physical_lane_count=128, leak_shift=5
    ):
        if logical_neuron_count != 256 or physical_lane_count not in (32, 64, 128):
            raise ValueError(
                "batched LIF supports 256 neurons with 32, 64, or 128 lanes"
            )
        if logical_neuron_count % physical_lane_count:
            raise ValueError("logical neuron count must divide into equal batches")
        if not 2 <= leak_shift <= 8:
            raise ValueError("leak_shift must be in the range 2..8")
        self.neuron_count = logical_neuron_count
        self.physical_lane_count = physical_lane_count
        self.batch_count = logical_neuron_count // physical_lane_count
        self.leak_shift = leak_shift
        self.count_bits = (logical_neuron_count + 1).bit_length()
        self.membrane_level_bits = 2 if logical_neuron_count == 1024 else 4
        super().__init__({
            "i": In(stream.Signature(data.ArrayLayout(ASQ, 4))),
            "o": Out(stream.Signature(data.ArrayLayout(ASQ, 4))),
        })

        self.membranes = []
        for index in range(logical_neuron_count):
            threshold = ParallelLIFBank.threshold_for(index)
            self.membranes.append(
                Signal(16, init=(index * 997 + 313) % threshold,
                       name=f"membrane_{index}")
            )
        self.spike_vector = Signal(logical_neuron_count)
        self.membrane_levels = Signal(logical_neuron_count * 4)
        self.spike_count = Signal(range(logical_neuron_count + 1))
        self.sample_index = Signal(32)
        self.leak_mode = Signal(2, init=1)
        self.recurrent_mode = Signal(2, init=1)
        self.threshold_mode = Signal(2, init=1)

    def elaborate(self, platform):
        m = Module()

        output_valid = Signal()
        output_payload = Signal(data.ArrayLayout(ASQ, 4))
        accept_input = Signal()
        pending_select = Signal()
        pending_update = Signal()
        batch_index = Signal(range(self.batch_count))
        pending_groups = Signal()
        pending_spikes = Signal()
        pending_output = Signal()

        input_sample = self.i.payload[0].as_value()
        input_magnitude = Signal(16)
        next_input_drive = Signal(11)
        input_drive = Signal(11)
        leak_control = self.i.payload[1].as_value()
        recurrent_control = self.i.payload[2].as_value()
        threshold_control = self.i.payload[3].as_value()
        next_leak_mode = Signal(2)
        next_recurrent_mode = Signal(2)
        next_threshold_mode = Signal(2)
        m.d.comb += [
            input_magnitude.eq(Mux(
                input_sample == -32768,
                Const(32768, 16),
                Mux(input_sample < 0, -input_sample, input_sample),
            )),
            next_input_drive.eq(input_magnitude >> 5),
            next_leak_mode.eq(Mux(
                leak_control < -1000, 0, Mux(leak_control > 1000, 2, 1)
            )),
            next_recurrent_mode.eq(Mux(
                recurrent_control < -1000,
                0,
                Mux(recurrent_control > 1000, 2, 1),
            )),
            next_threshold_mode.eq(Mux(
                threshold_control < -1000,
                0,
                Mux(threshold_control > 1000, 2, 1),
            )),
            accept_input.eq(
                ~pending_select & ~pending_update & ~pending_groups
                & ~pending_spikes & ~pending_output
                & (~output_valid | self.o.ready)
            ),
            self.i.ready.eq(accept_input),
            self.o.valid.eq(output_valid),
            self.o.payload.eq(output_payload),
            self.membrane_levels.eq(Cat(*[
                membrane[12:16] for membrane in self.membranes
            ])),
        ]

        dynamic_thresholds = []
        next_dynamic_thresholds = []
        for threshold_index in range(16):
            threshold = ParallelLIFBank.threshold_for(threshold_index)
            dynamic_threshold = Signal(14, name=f"dynamic_threshold_{threshold_index}")
            next_dynamic_threshold = Signal(
                14, name=f"next_dynamic_threshold_{threshold_index}"
            )
            m.d.comb += next_dynamic_threshold.eq(Mux(
                next_threshold_mode == 0,
                threshold - 800,
                Mux(next_threshold_mode == 2, threshold + 800, threshold),
            ))
            dynamic_thresholds.append(dynamic_threshold)
            next_dynamic_thresholds.append(next_dynamic_threshold)

        recurrent_drive = Signal(self.count_bits + 3)
        next_recurrent_drive = Signal.like(recurrent_drive)
        m.d.comb += next_recurrent_drive.eq(Mux(
            next_recurrent_mode == 0,
            self.spike_count << 1,
            Mux(
                next_recurrent_mode == 2,
                self.spike_count << 3,
                self.spike_count << 2,
            ),
        ))

        lane_leak_amounts = []
        selected_membranes = []
        selected_neighbors = []
        selected_resets = []
        selected_leaks = []
        next_lane_spikes = []
        next_lane_membranes = []
        for lane in range(self.physical_lane_count):
            batch_membranes = Array([
                self.membranes[lane + batch * self.physical_lane_count]
                for batch in range(self.batch_count)
            ])
            batch_neighbors = Array([
                self.spike_vector[
                    self.neuron_count - 1
                    if lane == 0 and batch == 0
                    else lane + batch * self.physical_lane_count - 1
                ]
                for batch in range(self.batch_count)
            ])
            batch_resets = Array([
                Const(
                    ((lane + batch * self.physical_lane_count) * 37 + 101)
                    & 0x1FF,
                    9,
                )
                for batch in range(self.batch_count)
            ])
            active_membrane = Signal(16, name=f"active_membrane_{lane}")
            selected_membrane = Signal(16, name=f"selected_membrane_{lane}")
            lane_leak = Signal(12, name=f"lane_leak_{lane}")
            selected_leak = Signal(12, name=f"selected_leak_{lane}")
            candidate = Signal(18, name=f"candidate_lane_{lane}")
            spike = Signal(name=f"next_spike_lane_{lane}")
            active_neighbor = Signal(name=f"active_neighbor_{lane}")
            selected_neighbor = Signal(name=f"selected_neighbor_{lane}")
            active_reset = Signal(9, name=f"active_reset_{lane}")
            selected_reset = Signal(9, name=f"selected_reset_{lane}")
            m.d.comb += [
                active_membrane.eq(batch_membranes[batch_index]),
                active_neighbor.eq(batch_neighbors[batch_index]),
                active_reset.eq(batch_resets[batch_index]),
                selected_leak.eq(Mux(
                    self.leak_mode == 0,
                    active_membrane >> 6,
                    Mux(
                        self.leak_mode == 2,
                        active_membrane >> 4,
                        active_membrane >> self.leak_shift,
                    ),
                )),
                candidate.eq(
                    selected_membrane
                    - lane_leak
                    + ParallelLIFBank.bias_for(lane)
                    + input_drive
                    + recurrent_drive
                    + Mux(selected_neighbor, 256, 0)
                ),
                spike.eq(candidate >= dynamic_thresholds[lane % 16]),
            ]
            lane_leak_amounts.append(lane_leak)
            selected_membranes.append((selected_membrane, active_membrane))
            selected_neighbors.append((selected_neighbor, active_neighbor))
            selected_resets.append((selected_reset, active_reset))
            selected_leaks.append(selected_leak)
            next_lane_spikes.append(spike)
            next_lane_membranes.append(Mux(spike, selected_reset, candidate[:16]))

        next_lane_spike_vector = Cat(*next_lane_spikes)
        assembled_spike_vector = Signal(self.neuron_count)

        group_size = 8
        group_spike_counts = []
        group_membrane_sums = []
        next_group_spike_counts = []
        next_group_membrane_sums = []
        for group_start in range(0, self.neuron_count, group_size):
            group_stop = group_start + group_size
            group_spike_count = Signal(range(group_size + 1))
            group_membrane_sum = Signal(range(group_size * 15 + 1))
            group_spike_counts.append(group_spike_count)
            group_membrane_sums.append(group_membrane_sum)
            next_group_spike_counts.append(balanced_sum([
                self.spike_vector[index]
                for index in range(group_start, group_stop)
            ]))
            next_group_membrane_sums.append(balanced_sum([
                self.membranes[index][12:16]
                for index in range(group_start, group_stop)
            ]))

        registered_spike_count = Signal(range(self.neuron_count + 1))
        membrane_sum = Signal(4 + self.count_bits)
        next_membrane_mean = Signal(16)
        membrane_mean = Signal(16)
        membrane_scaled = Signal(17)
        activity_scaled = Signal(self.count_bits + 9)
        pulse_amplitude = Signal(self.count_bits + 13)
        pulse_sample = Signal(signed(self.count_bits + 13))
        m.d.comb += [
            registered_spike_count.eq(balanced_sum(group_spike_counts)),
            membrane_sum.eq(balanced_sum(group_membrane_sums)),
            next_membrane_mean.eq(
                membrane_sum << (12 - (self.neuron_count.bit_length() - 1))
            ),
            membrane_scaled.eq(membrane_mean << 1),
            activity_scaled.eq(self.spike_count << 9),
            pulse_amplitude.eq(self.spike_count << 12),
            pulse_sample.eq(Mux(
                self.sample_index[0],
                -pulse_amplitude.as_signed(),
                pulse_amplitude.as_signed(),
            )),
        ]

        # Stage 0 registers controls. Each logical batch then has a select
        # stage followed by an update stage, so the state mux and 18-bit
        # candidate/compare chain never occupy the same sync path. All batches
        # read the old complete spike vector. The grouped reduction and output
        # stages then match ParallelLIFBank.
        with m.If(pending_select):
            m.d.sync += [pending_select.eq(0), pending_update.eq(1)]
            for selected_membrane, active_membrane in selected_membranes:
                m.d.sync += selected_membrane.eq(active_membrane)
            for selected_neighbor, active_neighbor in selected_neighbors:
                m.d.sync += selected_neighbor.eq(active_neighbor)
            for selected_reset, active_reset in selected_resets:
                m.d.sync += selected_reset.eq(active_reset)
            for lane_leak, selected_leak in zip(
                lane_leak_amounts, selected_leaks
            ):
                m.d.sync += lane_leak.eq(selected_leak)
        with m.Elif(pending_update):
            m.d.sync += [
                assembled_spike_vector.word_select(
                    batch_index, self.physical_lane_count
                ).eq(next_lane_spike_vector),
            ]
            with m.Switch(batch_index):
                for batch in range(self.batch_count):
                    with m.Case(batch):
                        for lane, next_membrane in enumerate(next_lane_membranes):
                            m.d.sync += self.membranes[
                                lane + batch * self.physical_lane_count
                            ].eq(next_membrane)
            with m.If(batch_index == self.batch_count - 1):
                final_spike_parts = [
                    assembled_spike_vector.word_select(
                        batch, self.physical_lane_count
                    )
                    for batch in range(self.batch_count - 1)
                ]
                final_spike_parts.append(next_lane_spike_vector)
                m.d.sync += [
                    pending_update.eq(0),
                    pending_groups.eq(1),
                    self.spike_vector.eq(Cat(*final_spike_parts)),
                    self.sample_index.eq(self.sample_index + 1),
                ]
            with m.Else():
                m.d.sync += [
                    pending_update.eq(0),
                    pending_select.eq(1),
                    batch_index.eq(batch_index + 1),
                ]
        with m.Elif(pending_groups):
            m.d.sync += [pending_groups.eq(0), pending_spikes.eq(1)]
            for group_spike_count, next_group_spike_count in zip(
                group_spike_counts, next_group_spike_counts
            ):
                m.d.sync += group_spike_count.eq(next_group_spike_count)
            for group_membrane_sum, next_group_membrane_sum in zip(
                group_membrane_sums, next_group_membrane_sums
            ):
                m.d.sync += group_membrane_sum.eq(next_group_membrane_sum)
        with m.Elif(pending_spikes):
            m.d.sync += [
                pending_spikes.eq(0),
                pending_output.eq(1),
                self.spike_count.eq(registered_spike_count),
                membrane_mean.eq(next_membrane_mean),
            ]
        with m.Elif(pending_output):
            m.d.sync += [
                pending_output.eq(0),
                output_valid.eq(1),
                output_payload[0].as_value().eq(Mux(
                    pulse_sample > 32767,
                    32767,
                    Mux(pulse_sample < -32768, -32768, pulse_sample[:16]),
                )),
                output_payload[1].as_value().eq(Mux(
                    activity_scaled > 32767,
                    32767,
                    activity_scaled[:16],
                )),
                output_payload[2].eq(Mux(
                    self.spike_count >= 2,
                    asq_from_volts(5.0),
                    0,
                )),
                output_payload[3].as_value().eq(Mux(
                    membrane_scaled > 32767,
                    32767,
                    membrane_scaled[:16],
                )),
            ]
        with m.Elif(accept_input):
            m.d.sync += output_valid.eq(0)
            with m.If(self.i.valid):
                m.d.sync += [
                    pending_select.eq(1),
                    batch_index.eq(0),
                    input_drive.eq(next_input_drive),
                    recurrent_drive.eq(next_recurrent_drive),
                    self.leak_mode.eq(next_leak_mode),
                    self.recurrent_mode.eq(next_recurrent_mode),
                    self.threshold_mode.eq(next_threshold_mode),
                ]
                for dynamic_threshold, next_dynamic_threshold in zip(
                    dynamic_thresholds, next_dynamic_thresholds
                ):
                    m.d.sync += dynamic_threshold.eq(next_dynamic_threshold)

        return m


class MemoryBatchedLIFBank(wiring.Component):
    """Update 512 or 1024 logical neurons from 32-lane block-memory rows.

    One 512-bit memory word contains the 32 membrane values for a logical
    batch. A synchronous read, row capture, candidate, and compare/write
    pipeline keeps block-RAM delay and threshold logic on separate paths. The complete old spike
    vector remains stable until the final batch, preserving the discrete-time
    semantics of :class:`ParallelLIFBank`.
    """

    def __init__(
        self, *, logical_neuron_count=512, physical_lane_count=32, leak_shift=5,
        ei_ring=False, inhibitory_strength=1024,
    ):
        if logical_neuron_count not in (512, 1024) or physical_lane_count != 32:
            raise ValueError(
                "memory-batched LIF supports 512 or 1024 neurons with 32 lanes"
            )
        if not 2 <= leak_shift <= 8:
            raise ValueError("leak_shift must be in the range 2..8")
        self.neuron_count = logical_neuron_count
        self.physical_lane_count = physical_lane_count
        self.batch_count = logical_neuron_count // physical_lane_count
        self.leak_shift = leak_shift
        self.count_bits = (logical_neuron_count + 1).bit_length()
        self.membrane_level_bits = 2 if logical_neuron_count == 1024 else 4
        self.ei_ring = ei_ring
        if inhibitory_strength not in range(256, 2049, 256):
            raise ValueError("inhibitory_strength must be 256..2048 in steps of 256")
        self.inhibitory_strength = inhibitory_strength
        super().__init__({
            "i": In(stream.Signature(data.ArrayLayout(ASQ, 4))),
            "o": Out(stream.Signature(data.ArrayLayout(ASQ, 4))),
        })

        initial_rows = []
        initial_levels = 0
        for batch in range(self.batch_count):
            row = 0
            for lane in range(self.physical_lane_count):
                index = batch * self.physical_lane_count + lane
                threshold = ParallelLIFBank.threshold_for(index)
                membrane = (index * 997 + 313) % threshold
                row |= membrane << (lane * 16)
                initial_levels |= (
                    (membrane >> (16 - self.membrane_level_bits))
                    & ((1 << self.membrane_level_bits) - 1)
                ) << (index * self.membrane_level_bits)
            initial_rows.append(row)
        self.initial_rows = initial_rows

        self.spike_vector = Signal(logical_neuron_count)
        self.membrane_levels = Signal(
            logical_neuron_count * self.membrane_level_bits,
            init=initial_levels,
        )
        self.spike_count = Signal(range(logical_neuron_count + 1))
        self.excitatory_spike_count = Signal(range(769))
        self.inhibitory_spike_count = Signal(range(257))
        self.sample_index = Signal(32)
        self.leak_mode = Signal(2, init=1)
        self.recurrent_mode = Signal(2, init=1)
        self.threshold_mode = Signal(2, init=1)
        self.display_row_valid = Signal()
        self.display_row_addr = Signal(range(self.batch_count))
        self.display_row_data = Signal(
            self.physical_lane_count * (1 + self.membrane_level_bits)
        )

    def elaborate(self, platform):
        m = Module()

        output_valid = Signal()
        output_payload = Signal(data.ArrayLayout(ASQ, 4))
        accept_input = Signal()
        pending_read = Signal()
        pending_capture = Signal()
        pending_candidate = Signal()
        pending_update = Signal()
        pending_accumulate = Signal()
        batch_index = Signal(range(self.batch_count))
        pending_groups = Signal()
        pending_supergroups = Signal()
        pending_spikes = Signal()
        pending_output = Signal()

        input_sample = self.i.payload[0].as_value()
        input_magnitude = Signal(16)
        next_input_drive = Signal(11)
        input_drive = Signal(11)
        leak_control = self.i.payload[1].as_value()
        recurrent_control = self.i.payload[2].as_value()
        threshold_control = self.i.payload[3].as_value()
        next_leak_mode = Signal(2)
        next_recurrent_mode = Signal(2)
        next_threshold_mode = Signal(2)
        m.d.comb += [
            input_magnitude.eq(Mux(
                input_sample == -32768,
                Const(32768, 16),
                Mux(input_sample < 0, -input_sample, input_sample),
            )),
            next_input_drive.eq(input_magnitude >> 5),
            next_leak_mode.eq(Mux(
                leak_control < -1000, 0, Mux(leak_control > 1000, 2, 1)
            )),
            next_recurrent_mode.eq(Mux(
                recurrent_control < -1000,
                0,
                Mux(recurrent_control > 1000, 2, 1),
            )),
            next_threshold_mode.eq(Mux(
                threshold_control < -1000,
                0,
                Mux(threshold_control > 1000, 2, 1),
            )),
            accept_input.eq(
                ~pending_read & ~pending_capture & ~pending_candidate
                & ~pending_update & ~pending_accumulate & ~pending_groups
                & ~pending_supergroups
                & ~pending_spikes & ~pending_output
                & (~output_valid | self.o.ready)
            ),
            self.i.ready.eq(accept_input),
            self.o.valid.eq(output_valid),
            self.o.payload.eq(output_payload),
        ]

        dynamic_thresholds = []
        next_dynamic_thresholds = []
        for threshold_index in range(16):
            threshold = ParallelLIFBank.threshold_for(threshold_index)
            dynamic_threshold = Signal(14, name=f"dynamic_threshold_{threshold_index}")
            next_dynamic_threshold = Signal(
                14, name=f"next_dynamic_threshold_{threshold_index}"
            )
            m.d.comb += next_dynamic_threshold.eq(Mux(
                next_threshold_mode == 0,
                threshold - 800,
                Mux(next_threshold_mode == 2, threshold + 800, threshold),
            ))
            dynamic_thresholds.append(dynamic_threshold)
            next_dynamic_thresholds.append(next_dynamic_threshold)

        recurrent_drive = Signal(self.count_bits + 3)
        next_recurrent_drive = Signal.like(recurrent_drive)
        m.d.comb += next_recurrent_drive.eq(Mux(
            next_recurrent_mode == 0,
            self.spike_count << 1,
            Mux(
                next_recurrent_mode == 2,
                self.spike_count << 3,
                self.spike_count << 2,
            ),
        ))

        m.submodules.state_memory = state_memory = Memory(
            shape=unsigned(self.physical_lane_count * 16),
            depth=self.batch_count,
            init=self.initial_rows,
            attrs={"ram_style": "block"},
        )
        state_read = state_memory.read_port()
        state_write = state_memory.write_port()
        m.d.comb += [
            state_read.addr.eq(batch_index),
            state_read.en.eq(pending_read),
            state_write.addr.eq(batch_index),
            state_write.en.eq(pending_update),
        ]

        selected_spike_row = Signal(self.physical_lane_count)
        selected_neighbor_tail = Signal()
        selected_membranes = [
            Signal(16, name=f"selected_membrane_{lane}")
            for lane in range(self.physical_lane_count)
        ]
        selected_resets = [
            Signal(9, name=f"selected_reset_{lane}")
            for lane in range(self.physical_lane_count)
        ]
        neighbor_tails = Array([
            self.spike_vector[
                self.neuron_count - 1
                if batch == 0
                else batch * self.physical_lane_count - 1
            ]
            for batch in range(self.batch_count)
        ])
        reset_tables = [
            Array([
                Const(
                    ((batch * self.physical_lane_count + lane) * 37 + 101)
                    & 0x1FF,
                    9,
                )
                for batch in range(self.batch_count)
            ])
            for lane in range(self.physical_lane_count)
        ]

        next_lane_spikes = []
        next_lane_membranes = []
        lane_candidates = []
        for lane in range(self.physical_lane_count):
            membrane = selected_membranes[lane]
            leak_amount = Signal(12, name=f"lane_leak_{lane}")
            candidate_base = Signal(18, name=f"candidate_base_lane_{lane}")
            computed_candidate = Signal(
                18, name=f"computed_candidate_lane_{lane}"
            )
            candidate = Signal(18, name=f"candidate_lane_{lane}")
            spike = Signal(name=f"next_spike_lane_{lane}")
            neighbor = (
                selected_neighbor_tail if lane == 0 else selected_spike_row[lane - 1]
            )
            m.d.comb += [
                leak_amount.eq(Mux(
                    self.leak_mode == 0,
                    membrane >> 6,
                    Mux(
                        self.leak_mode == 2,
                        membrane >> 4,
                        membrane >> self.leak_shift,
                    ),
                )),
                candidate_base.eq(
                    membrane
                    - leak_amount
                    + ParallelLIFBank.bias_for(lane)
                    + input_drive
                    + recurrent_drive
                ),
                spike.eq(candidate >= dynamic_thresholds[lane % 16]),
            ]
            if self.ei_ring and lane % 4 == 0 and self.inhibitory_strength == 1024:
                m.d.comb += computed_candidate.eq(Mux(
                    neighbor,
                    Mux(candidate_base >= 1024, candidate_base - 1024, 0),
                    candidate_base,
                ))
            elif self.ei_ring and lane % 4 == 0:
                m.d.comb += computed_candidate.eq(Mux(
                    neighbor,
                    Mux(
                        candidate_base >= self.inhibitory_strength,
                        candidate_base - self.inhibitory_strength,
                        0,
                    ),
                    candidate_base,
                ))
            else:
                m.d.comb += computed_candidate.eq(
                    candidate_base + Mux(neighbor, 256, 0)
                )
            lane_candidates.append((candidate, computed_candidate))
            next_lane_spikes.append(spike)
            next_lane_membranes.append(
                Mux(spike, selected_resets[lane], candidate[:16])
            )

        next_lane_spike_vector = Cat(*next_lane_spikes)
        next_lane_membrane_levels = Cat(*[
            membrane[16 - self.membrane_level_bits:16]
            for membrane in next_lane_membranes
        ])
        next_display_row = Cat(*[
            Cat(
                spike,
                membrane[16 - self.membrane_level_bits:16],
            )
            for spike, membrane in zip(next_lane_spikes, next_lane_membranes)
        ])
        m.d.comb += [
            state_write.data.eq(Cat(*next_lane_membranes)),
            self.display_row_valid.eq(pending_update),
            self.display_row_addr.eq(batch_index),
            self.display_row_data.eq(next_display_row),
        ]
        assembled_spike_vector = Signal(self.neuron_count)

        # The 1024 profile accumulates exact population statistics while each
        # 32-neuron row is already present in the arithmetic lanes. This
        # removes the large post-update reduction trees and lets the per-neuron
        # video shadow use two bits without changing any of the four audio
        # outputs or the neural state itself.
        batch_spike_count = Signal(range(self.physical_lane_count + 1))
        batch_excitatory_spike_count = Signal(range(25))
        batch_inhibitory_spike_count = Signal(range(9))
        batch_membrane_sum = Signal(range(self.physical_lane_count * 15 + 1))
        sample_spike_accumulator = Signal(range(self.neuron_count + 1))
        sample_excitatory_spike_accumulator = Signal(range(769))
        sample_inhibitory_spike_accumulator = Signal(range(257))
        sample_membrane_accumulator = Signal(range(self.neuron_count * 15 + 1))
        registered_batch_spike_count = Signal.like(batch_spike_count)
        registered_batch_excitatory_spike_count = Signal.like(
            batch_excitatory_spike_count
        )
        registered_batch_inhibitory_spike_count = Signal.like(
            batch_inhibitory_spike_count
        )
        registered_batch_membrane_sum = Signal.like(batch_membrane_sum)
        accumulated_final_batch = Signal()
        m.d.comb += [
            batch_excitatory_spike_count.eq(balanced_sum([
                spike
                for lane, spike in enumerate(next_lane_spikes)
                if lane % 4 != 3
            ])),
            batch_inhibitory_spike_count.eq(balanced_sum([
                spike
                for lane, spike in enumerate(next_lane_spikes)
                if lane % 4 == 3
            ])),
            batch_spike_count.eq(
                batch_excitatory_spike_count + batch_inhibitory_spike_count
            ),
            batch_membrane_sum.eq(balanced_sum([
                membrane[12:16] for membrane in next_lane_membranes
            ])),
        ]

        group_size = 8
        group_spike_counts = []
        group_membrane_sums = []
        next_group_spike_counts = []
        next_group_membrane_sums = []
        for group_start in range(0, self.neuron_count, group_size):
            group_stop = group_start + group_size
            group_spike_count = Signal(range(group_size + 1))
            group_membrane_sum = Signal(range(group_size * 15 + 1))
            group_spike_counts.append(group_spike_count)
            group_membrane_sums.append(group_membrane_sum)
            next_group_spike_counts.append(balanced_sum([
                self.spike_vector[index]
                for index in range(group_start, group_stop)
            ]))
            next_group_membrane_sums.append(balanced_sum([
                self.membrane_levels.word_select(index, 4)
                for index in range(group_start, group_stop)
            ]))

        # A 1024-neuron population has 128 group values. Register groups of
        # eight before the final reduction so the monitoring/audio path does
        # not grow an extra routed adder level. The 512 profile keeps its
        # established two-stage aggregation and therefore its existing QoR.
        supergroup_spike_counts = []
        supergroup_membrane_sums = []
        next_supergroup_spike_counts = []
        next_supergroup_membrane_sums = []
        if self.neuron_count == 1024:
            for supergroup_start in range(0, len(group_spike_counts), 8):
                supergroup_spike_count = Signal(range(65))
                supergroup_membrane_sum = Signal(range(8 * group_size * 15 + 1))
                supergroup_spike_counts.append(supergroup_spike_count)
                supergroup_membrane_sums.append(supergroup_membrane_sum)
                next_supergroup_spike_counts.append(balanced_sum(
                    group_spike_counts[supergroup_start:supergroup_start + 8]
                ))
                next_supergroup_membrane_sums.append(balanced_sum(
                    group_membrane_sums[supergroup_start:supergroup_start + 8]
                ))

        registered_spike_count = Signal(range(self.neuron_count + 1))
        membrane_sum = Signal(4 + self.count_bits)
        next_membrane_mean = Signal(16)
        membrane_mean = Signal(16)
        membrane_scaled = Signal(17)
        activity_scaled = Signal(self.count_bits + 9)
        pulse_amplitude = Signal(self.count_bits + 13)
        pulse_sample = Signal(signed(self.count_bits + 13))
        m.d.comb += [
            registered_spike_count.eq(balanced_sum(
                supergroup_spike_counts
                if self.neuron_count == 1024 else group_spike_counts
            )),
            membrane_sum.eq(balanced_sum(
                supergroup_membrane_sums
                if self.neuron_count == 1024 else group_membrane_sums
            )),
            next_membrane_mean.eq(
                membrane_sum << (12 - (self.neuron_count.bit_length() - 1))
            ),
            membrane_scaled.eq(membrane_mean << 1),
            activity_scaled.eq(self.spike_count << 9),
            pulse_amplitude.eq(self.spike_count << 12),
            pulse_sample.eq(Mux(
                self.sample_index[0],
                -pulse_amplitude.as_signed(),
                pulse_amplitude.as_signed(),
            )),
        ]

        # Every batch gets a synchronous block-memory read, a row-register
        # stage, a candidate stage, and a compare/write stage. Neighbor spikes are captured from the old
        # population during the read issue; no write can feed another neuron
        # in the same sample. The row and candidate registers prevent DP16KD
        # output delay, the adder chain, and threshold/write logic from sharing
        # one timing path.
        with m.If(pending_read):
            m.d.sync += [
                pending_read.eq(0),
                pending_capture.eq(1),
                selected_spike_row.eq(
                    self.spike_vector.word_select(
                        batch_index, self.physical_lane_count
                    )
                ),
                selected_neighbor_tail.eq(neighbor_tails[batch_index]),
            ]
            for lane, selected_reset in enumerate(selected_resets):
                m.d.sync += selected_reset.eq(reset_tables[lane][batch_index])
        with m.Elif(pending_capture):
            m.d.sync += [pending_capture.eq(0), pending_candidate.eq(1)]
            for lane, selected_membrane in enumerate(selected_membranes):
                m.d.sync += selected_membrane.eq(
                    state_read.data.word_select(lane, 16)
                )
        with m.Elif(pending_candidate):
            m.d.sync += [pending_candidate.eq(0), pending_update.eq(1)]
            for candidate, computed_candidate in lane_candidates:
                m.d.sync += candidate.eq(computed_candidate)
        with m.Elif(pending_update):
            m.d.sync += [
                assembled_spike_vector.word_select(
                    batch_index, self.physical_lane_count
                ).eq(next_lane_spike_vector),
                self.membrane_levels.word_select(
                    batch_index,
                    self.physical_lane_count * self.membrane_level_bits,
                ).eq(next_lane_membrane_levels),
            ]
            with m.If(batch_index == self.batch_count - 1):
                final_spike_parts = [
                    assembled_spike_vector.word_select(
                        batch, self.physical_lane_count
                    )
                    for batch in range(self.batch_count - 1)
                ]
                final_spike_parts.append(next_lane_spike_vector)
                m.d.sync += [
                    pending_update.eq(0),
                    self.spike_vector.eq(Cat(*final_spike_parts)),
                    self.sample_index.eq(self.sample_index + 1),
                ]
                if self.neuron_count == 1024 and self.ei_ring:
                    m.d.sync += [
                        pending_accumulate.eq(1),
                        accumulated_final_batch.eq(1),
                        registered_batch_spike_count.eq(batch_spike_count),
                        registered_batch_excitatory_spike_count.eq(
                            batch_excitatory_spike_count
                        ),
                        registered_batch_inhibitory_spike_count.eq(
                            batch_inhibitory_spike_count
                        ),
                        registered_batch_membrane_sum.eq(batch_membrane_sum),
                    ]
                elif self.neuron_count == 1024:
                    m.d.sync += [
                        pending_output.eq(1),
                        self.spike_count.eq(
                            sample_spike_accumulator + batch_spike_count
                        ),
                        membrane_mean.eq(
                            (sample_membrane_accumulator + batch_membrane_sum)
                            << 2
                        ),
                    ]
                else:
                    m.d.sync += pending_groups.eq(1)
            with m.Else():
                m.d.sync += [
                    pending_update.eq(0),
                    pending_read.eq(1),
                    batch_index.eq(batch_index + 1),
                ]
                if self.neuron_count == 1024 and self.ei_ring:
                    m.d.sync += [
                        pending_read.eq(0),
                        pending_accumulate.eq(1),
                        accumulated_final_batch.eq(0),
                        registered_batch_spike_count.eq(batch_spike_count),
                        registered_batch_excitatory_spike_count.eq(
                            batch_excitatory_spike_count
                        ),
                        registered_batch_inhibitory_spike_count.eq(
                            batch_inhibitory_spike_count
                        ),
                        registered_batch_membrane_sum.eq(batch_membrane_sum),
                    ]
                elif self.neuron_count == 1024:
                    m.d.sync += [
                        sample_spike_accumulator.eq(
                            sample_spike_accumulator + batch_spike_count
                        ),
                        sample_membrane_accumulator.eq(
                            sample_membrane_accumulator + batch_membrane_sum
                        ),
                    ]
        with m.Elif(pending_accumulate):
            m.d.sync += pending_accumulate.eq(0)
            with m.If(accumulated_final_batch):
                m.d.sync += [
                    pending_output.eq(1),
                    self.spike_count.eq(
                        sample_spike_accumulator + registered_batch_spike_count
                    ),
                    self.excitatory_spike_count.eq(
                        sample_excitatory_spike_accumulator
                        + registered_batch_excitatory_spike_count
                    ),
                    self.inhibitory_spike_count.eq(
                        sample_inhibitory_spike_accumulator
                        + registered_batch_inhibitory_spike_count
                    ),
                    membrane_mean.eq(
                        (
                            sample_membrane_accumulator
                            + registered_batch_membrane_sum
                        ) << 2
                    ),
                ]
            with m.Else():
                m.d.sync += [
                    pending_read.eq(1),
                    sample_spike_accumulator.eq(
                        sample_spike_accumulator + registered_batch_spike_count
                    ),
                    sample_excitatory_spike_accumulator.eq(
                        sample_excitatory_spike_accumulator
                        + registered_batch_excitatory_spike_count
                    ),
                    sample_inhibitory_spike_accumulator.eq(
                        sample_inhibitory_spike_accumulator
                        + registered_batch_inhibitory_spike_count
                    ),
                    sample_membrane_accumulator.eq(
                        sample_membrane_accumulator
                        + registered_batch_membrane_sum
                    ),
                ]
        with m.Elif(pending_groups):
            m.d.sync += pending_groups.eq(0)
            if self.neuron_count == 1024:
                m.d.sync += pending_supergroups.eq(1)
            else:
                m.d.sync += pending_spikes.eq(1)
            for group_spike_count, next_group_spike_count in zip(
                group_spike_counts, next_group_spike_counts
            ):
                m.d.sync += group_spike_count.eq(next_group_spike_count)
            for group_membrane_sum, next_group_membrane_sum in zip(
                group_membrane_sums, next_group_membrane_sums
            ):
                m.d.sync += group_membrane_sum.eq(next_group_membrane_sum)
        with m.Elif(pending_supergroups):
            m.d.sync += [pending_supergroups.eq(0), pending_spikes.eq(1)]
            for supergroup_spike_count, next_supergroup_spike_count in zip(
                supergroup_spike_counts, next_supergroup_spike_counts
            ):
                m.d.sync += supergroup_spike_count.eq(next_supergroup_spike_count)
            for supergroup_membrane_sum, next_supergroup_membrane_sum in zip(
                supergroup_membrane_sums, next_supergroup_membrane_sums
            ):
                m.d.sync += supergroup_membrane_sum.eq(
                    next_supergroup_membrane_sum
                )
        with m.Elif(pending_spikes):
            m.d.sync += [
                pending_spikes.eq(0),
                pending_output.eq(1),
                self.spike_count.eq(registered_spike_count),
                membrane_mean.eq(next_membrane_mean),
            ]
        with m.Elif(pending_output):
            m.d.sync += [
                pending_output.eq(0),
                output_valid.eq(1),
                output_payload[0].as_value().eq(Mux(
                    pulse_sample > 32767,
                    32767,
                    Mux(pulse_sample < -32768, -32768, pulse_sample[:16]),
                )),
                output_payload[1].as_value().eq(Mux(
                    activity_scaled > 32767,
                    32767,
                    activity_scaled[:16],
                )),
                output_payload[2].eq(Mux(
                    self.spike_count >= 2,
                    asq_from_volts(5.0),
                    0,
                )),
                output_payload[3].as_value().eq(Mux(
                    membrane_scaled > 32767,
                    32767,
                    membrane_scaled[:16],
                )),
            ]
        with m.Elif(accept_input):
            m.d.sync += output_valid.eq(0)
            with m.If(self.i.valid):
                m.d.sync += [
                    pending_read.eq(1),
                    batch_index.eq(0),
                    sample_spike_accumulator.eq(0),
                    sample_excitatory_spike_accumulator.eq(0),
                    sample_inhibitory_spike_accumulator.eq(0),
                    sample_membrane_accumulator.eq(0),
                    input_drive.eq(next_input_drive),
                    recurrent_drive.eq(next_recurrent_drive),
                    self.leak_mode.eq(next_leak_mode),
                    self.recurrent_mode.eq(next_recurrent_mode),
                    self.threshold_mode.eq(next_threshold_mode),
                ]
                for dynamic_threshold, next_dynamic_threshold in zip(
                    dynamic_thresholds, next_dynamic_thresholds
                ):
                    m.d.sync += dynamic_threshold.eq(next_dynamic_threshold)

        return m


class SNNTestSource(wiring.Component):
    """Deterministic slow drive steps for simulation and SRAM measurements."""

    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))
    sample_index: Out(unsigned(32))

    def elaborate(self, platform):
        m = Module()

        sample_index = Signal(32)
        drive = Signal(signed(16))
        m.d.comb += [
            drive.eq(Mux(sample_index[11], 12_000, 3_000)),
            self.o.valid.eq(1),
            self.o.payload[0].as_value().eq(drive),
            self.o.payload[1].as_value().eq(0),
            self.o.payload[2].as_value().eq(0),
            self.o.payload[3].as_value().eq(0),
            self.sample_index.eq(sample_index),
        ]
        with m.If(self.o.valid & self.o.ready):
            m.d.sync += sample_index.eq(sample_index + 1)
        return m
