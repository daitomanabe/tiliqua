# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Parallel leaky-integrate-and-fire network for audio-rate experiments."""

from amaranth import Cat, Const, Module, Mux, Signal, signed, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from . import ASQ, asq_from_volts


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


class ParallelLIFBank(wiring.Component):
    """Update many integer LIF neurons in parallel for every audio sample.

    The network deliberately uses only add, subtract, compare, and shifts.
    Each neuron has a distinct bias and threshold, receives the previous
    population spike count, and is excited by its left neighbor's previous
    spike. This small recurrent topology produces deterministic collective
    rhythms without multipliers or RAM arbitration.
    """

    def __init__(self, *, neuron_count=64, leak_shift=5):
        if neuron_count < 8 or neuron_count > 256 or neuron_count & (neuron_count - 1):
            raise ValueError("neuron_count must be a power of two in the range 8..256")
        if not 2 <= leak_shift <= 8:
            raise ValueError("leak_shift must be in the range 2..8")
        self.neuron_count = neuron_count
        self.leak_shift = leak_shift
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
        pending_spikes = Signal()
        pending_output = Signal()

        input_sample = self.i.payload[0].as_value()
        input_magnitude = Signal(16)
        input_drive = Signal(11)
        m.d.comb += [
            input_magnitude.eq(Mux(input_sample < 0, -input_sample, input_sample)),
            input_drive.eq(input_magnitude >> 5),
            accept_input.eq(
                ~pending_spikes & ~pending_output
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
        for index, membrane in enumerate(self.membranes):
            candidate = Signal(18, name=f"candidate_{index}")
            spike = Signal(name=f"next_spike_{index}")
            neighbor = self.spike_vector[(index - 1) % self.neuron_count]
            threshold = self.threshold_for(index)
            reset_level = (index * 37 + 101) & 0x1FF
            m.d.comb += [
                candidate.eq(
                    membrane
                    - (membrane >> self.leak_shift)
                    + self.bias_for(index)
                    + input_drive
                    + (self.spike_count << 2)
                    + Mux(neighbor, 256, 0)
                ),
                spike.eq(candidate >= threshold),
            ]
            next_spikes.append(spike)
            next_membranes.append(Mux(spike, reset_level, candidate[:16]))

        next_spike_vector = Cat(*next_spikes)
        registered_spike_count = Signal(range(self.neuron_count + 1))
        membrane_sum = Signal(8 + self.count_bits)
        next_membrane_mean = Signal(16)
        membrane_mean = Signal(16)
        membrane_scaled = Signal(17)
        activity_scaled = Signal(17)
        pulse_amplitude = Signal(18)
        pulse_sample = Signal(signed(18))
        m.d.comb += [
            registered_spike_count.eq(balanced_sum([
                self.spike_vector[index] for index in range(self.neuron_count)
            ])),
            # The fourth DAC channel is a monitor, so average the upper eight
            # bits. The neuron state itself remains 16-bit; narrowing only
            # this reduction tree keeps the 64-way monitor path at 60 MHz.
            membrane_sum.eq(balanced_sum([
                membrane[8:16] for membrane in self.membranes
            ])),
            next_membrane_mean.eq(
                (membrane_sum >> (self.neuron_count.bit_length() - 1)) << 8
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

        # Stage 1 updates all neurons and registers one spike bit per neuron.
        # Stage 2 reduces those registered bits into a population count. Stage
        # 3 maps the registered count to DAC channels. Three 60 MHz cycles are
        # still negligible inside one 48 kHz audio period and break both long
        # candidate->population and population->saturation timing paths.
        with m.If(pending_spikes):
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
                    pending_spikes.eq(1),
                    self.spike_vector.eq(next_spike_vector),
                    self.sample_index.eq(self.sample_index + 1),
                ]
                for membrane, next_membrane in zip(self.membranes, next_membranes):
                    m.d.sync += membrane.eq(next_membrane)

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
            self.o.payload[1].as_value().eq(Mux(sample_index[12], 8_000, -8_000)),
            self.o.payload[2].as_value().eq(0),
            self.o.payload[3].as_value().eq(0),
            self.sample_index.eq(sample_index),
        ]
        with m.If(self.o.valid & self.o.ready):
            m.d.sync += sample_index.eq(sample_index + 1)
        return m
