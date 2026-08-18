# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Serial fixed-point eight-band encoder for the SNN2 AV path."""

from __future__ import annotations

from amaranth import Array, Const, Module, Mux, Signal, signed
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from tiliqua.dsp import ASQ

from .manifest import validate_manifest
from .rtl import SNN2_INPUT_LAYOUT


def _sat_s18(value):
    return Mux(
        value > (1 << 17) - 1,
        Const((1 << 17) - 1, signed(18)),
        Mux(
            value < -(1 << 17),
            Const(-(1 << 17), signed(18)),
            value[:18].as_signed(),
        ),
    )


class SNN2AudioEncoder(wiring.Component):
    """Convert four calibrated ASQ inputs into one atomic SNN2 input record.

    One shared signed multiplier evaluates eight transposed-direct-form-II
    biquads and their phase accumulators in 104 cycles. This keeps the encoder
    comfortably inside the 1,250-cycle neural-sample period without creating
    forty parallel coefficient multipliers.
    """

    def __init__(self, manifest: dict):
        self.manifest = validate_manifest(manifest)
        super().__init__({
            "i": In(stream.Signature(data.ArrayLayout(ASQ, 4))),
            "o": Out(stream.Signature(SNN2_INPUT_LAYOUT)),
            "band_levels": Out(data.ArrayLayout(8, 8)),
        })
        self.sample_index = Signal(32)
        self.busy = Signal()

    def elaborate(self, platform):
        m = Module()
        encoder = self.manifest["encoder"]
        coefficients = encoder["filter_coefficients_q2_14"]

        output_valid = Signal()
        output_payload = Signal(SNN2_INPUT_LAYOUT)
        working = Signal()
        x_sample = Signal(signed(16))
        encoder_gain_q8 = Signal(10, init=320)
        external_drive = Signal(signed(18))
        inhibitory_gain_q8 = Signal(10, init=320)
        adaptation_gain_q8 = Signal(10, init=256)
        band = Signal(3)
        spikes = Signal(8)

        z1 = [Signal(signed(18), name=f"encoder_z1_{index}") for index in range(8)]
        z2 = [Signal(signed(18), name=f"encoder_z2_{index}") for index in range(8)]
        phase = [Signal(encoder["phase_bits"], name=f"encoder_phase_{index}")
                 for index in range(8)]
        z1_array = Array(z1)
        z2_array = Array(z2)
        phase_array = Array(phase)

        coefficient_arrays = {
            key: Array(Const(record[key], signed(16)) for record in coefficients)
            for key in ("b0", "b1", "b2", "a1", "a2")
        }

        multiplier_a = Signal(signed(18))
        multiplier_b = Signal(signed(16))
        product = Signal(signed(34))
        product_work = Signal(signed(34))
        scaled_product_work = Signal(signed(20))
        coefficient_work = Signal(signed(16))
        y_work = Signal(signed(18))
        z1_work = Signal(signed(21))
        z2_work = Signal(signed(21))
        magnitude = Signal(18)
        phase_increment_wide = Signal(signed(34))
        phase_increment = Signal(encoder["phase_bits"])
        phase_sum = Signal(encoder["phase_bits"] + 1)
        phase_overflow = Signal()

        input_values = [self.i.payload[index].as_value() for index in range(4)]
        clamp = encoder["control_clamp_asq"]
        gain_mapping = encoder["control_mapping"]["gain"]
        adaptation_mapping = encoder["control_mapping"]["adaptation"]

        mapped_encoder_gain = Signal(10)
        mapped_inhibitory_gain = Signal(10)
        mapped_adaptation_gain = Signal(10)
        mapped_drive = Signal(signed(18))
        bounded_encoder = Signal(signed(17))
        bounded_inhibitory = Signal(signed(17))
        bounded_adaptation = Signal(signed(17))
        scaled_encoder = Signal(signed(19))
        scaled_inhibitory = Signal(signed(19))

        def bounded_control(value):
            return Mux(
                value <= -clamp,
                Const(-clamp, signed(17)),
                Mux(value >= clamp, Const(clamp, signed(17)), value),
            )

        def gain_scaled(value):
            # 3 == 2 + 1. Keep this explicit so synthesis cannot turn the CV
            # mapping back into a general multiplier.
            return (value << 1) + value

        m.d.comb += [
            self.o.valid.eq(output_valid),
            self.o.payload.eq(output_payload),
            self.busy.eq(working),
            multiplier_a.eq(0),
            multiplier_b.eq(0),
            product.eq(multiplier_a * multiplier_b),
            scaled_product_work.eq(product_work >> 14),
            magnitude.eq(Mux(
                y_work == -(1 << 17),
                (1 << 17) - 1,
                Mux(y_work < 0, -y_work, y_work),
            )),
            phase_increment_wide.eq(product_work),
            phase_increment.eq(Mux(
                (phase_increment_wide >> encoder["phase_increment_shift"])
                > (1 << encoder["phase_bits"]) - 1,
                (1 << encoder["phase_bits"]) - 1,
                phase_increment_wide >> encoder["phase_increment_shift"],
            )),
            phase_sum.eq(phase_array[band] + phase_increment),
            phase_overflow.eq(phase_sum[encoder["phase_bits"]]),
            bounded_encoder.eq(bounded_control(input_values[1])),
            bounded_inhibitory.eq(bounded_control(input_values[2])),
            bounded_adaptation.eq(bounded_control(input_values[3])),
            scaled_encoder.eq(gain_scaled(bounded_encoder)),
            scaled_inhibitory.eq(gain_scaled(bounded_inhibitory)),
            mapped_encoder_gain.eq(Mux(
                bounded_encoder <= -clamp,
                gain_mapping["low_q8"],
                Mux(
                    bounded_encoder >= clamp,
                    gain_mapping["high_q8"],
                    gain_mapping["center_q8"]
                    + (scaled_encoder >> gain_mapping["slope_shift"]),
                ),
            )),
            mapped_inhibitory_gain.eq(Mux(
                bounded_inhibitory <= -clamp,
                gain_mapping["low_q8"],
                Mux(
                    bounded_inhibitory >= clamp,
                    gain_mapping["high_q8"],
                    gain_mapping["center_q8"]
                    + (scaled_inhibitory >> gain_mapping["slope_shift"]),
                ),
            )),
            mapped_adaptation_gain.eq(Mux(
                bounded_adaptation <= -clamp,
                adaptation_mapping["low_q8"],
                Mux(
                    bounded_adaptation >= clamp,
                    adaptation_mapping["high_q8"],
                    adaptation_mapping["center_q8"]
                    + (bounded_adaptation >> adaptation_mapping["slope_shift"]),
                ),
            )),
            # drive_base is frozen at 6144, so gain * drive_base / 256 is
            # exactly gain * 24 and needs only two shifts plus one adder.
            mapped_drive.eq((mapped_encoder_gain << 4) + (mapped_encoder_gain << 3)),
        ]

        with m.FSM(init="IDLE"):
            with m.State("IDLE"):
                m.d.comb += [
                    working.eq(0),
                    self.i.ready.eq(~output_valid | self.o.ready),
                ]
                with m.If(output_valid & self.o.ready):
                    m.d.sync += output_valid.eq(0)
                with m.If(self.i.valid & self.i.ready):
                    m.d.sync += [
                        output_valid.eq(0),
                        x_sample.eq(input_values[0]),
                        encoder_gain_q8.eq(mapped_encoder_gain),
                        external_drive.eq(mapped_drive),
                        inhibitory_gain_q8.eq(mapped_inhibitory_gain),
                        adaptation_gain_q8.eq(mapped_adaptation_gain),
                        band.eq(0),
                        spikes.eq(0),
                    ]
                    m.next = "PREPARE_B0"

            with m.State("PREPARE_B0"):
                m.d.comb += working.eq(1)
                m.d.sync += coefficient_work.eq(coefficient_arrays["b0"][band])
                m.next = "Y_B0_MULT"

            with m.State("Y_B0_MULT"):
                m.d.comb += [
                    working.eq(1),
                    multiplier_a.eq(x_sample),
                    multiplier_b.eq(coefficient_work),
                ]
                m.d.sync += product_work.eq(product)
                m.next = "Y_B0_COMMIT"

            with m.State("Y_B0_COMMIT"):
                m.d.comb += working.eq(1)
                m.d.sync += [
                    y_work.eq(_sat_s18(scaled_product_work + z1_array[band])),
                    coefficient_work.eq(coefficient_arrays["b1"][band]),
                ]
                m.next = "Z1_B1_MULT"

            with m.State("Z1_B1_MULT"):
                m.d.comb += [
                    working.eq(1),
                    multiplier_a.eq(x_sample),
                    multiplier_b.eq(coefficient_work),
                ]
                m.d.sync += product_work.eq(product)
                m.next = "Z1_B1_COMMIT"

            with m.State("Z1_B1_COMMIT"):
                m.d.comb += working.eq(1)
                m.d.sync += [
                    z1_work.eq(scaled_product_work + z2_array[band]),
                    coefficient_work.eq(coefficient_arrays["a1"][band]),
                ]
                m.next = "Z1_A1_MULT"

            with m.State("Z1_A1_MULT"):
                m.d.comb += [
                    working.eq(1),
                    multiplier_a.eq(y_work),
                    multiplier_b.eq(coefficient_work),
                ]
                m.d.sync += product_work.eq(product)
                m.next = "Z1_A1_COMMIT"

            with m.State("Z1_A1_COMMIT"):
                m.d.comb += working.eq(1)
                m.d.sync += [
                    z1_array[band].eq(_sat_s18(z1_work - scaled_product_work)),
                    coefficient_work.eq(coefficient_arrays["b2"][band]),
                ]
                m.next = "Z2_B2_MULT"

            with m.State("Z2_B2_MULT"):
                m.d.comb += [
                    working.eq(1),
                    multiplier_a.eq(x_sample),
                    multiplier_b.eq(coefficient_work),
                ]
                m.d.sync += product_work.eq(product)
                m.next = "Z2_B2_COMMIT"

            with m.State("Z2_B2_COMMIT"):
                m.d.comb += working.eq(1)
                m.d.sync += [
                    z2_work.eq(scaled_product_work),
                    coefficient_work.eq(coefficient_arrays["a2"][band]),
                ]
                m.next = "Z2_A2_MULT"

            with m.State("Z2_A2_MULT"):
                m.d.comb += [
                    working.eq(1),
                    multiplier_a.eq(y_work),
                    multiplier_b.eq(coefficient_work),
                ]
                m.d.sync += product_work.eq(product)
                m.next = "Z2_A2_COMMIT"

            with m.State("Z2_A2_COMMIT"):
                m.d.comb += working.eq(1)
                m.d.sync += z2_array[band].eq(
                    _sat_s18(z2_work - scaled_product_work)
                )
                m.next = "PHASE_MULT"

            with m.State("PHASE_MULT"):
                m.d.comb += [
                    working.eq(1),
                    multiplier_a.eq(magnitude),
                    multiplier_b.eq(encoder_gain_q8),
                ]
                m.d.sync += [
                    product_work.eq(product),
                    self.band_levels[band].eq(Mux(
                        magnitude > (255 << 5), 255, magnitude >> 5
                    )),
                ]
                m.next = "PHASE_COMMIT"

            with m.State("PHASE_COMMIT"):
                m.d.comb += working.eq(1)
                m.d.sync += [
                    phase_array[band].eq(phase_sum[:encoder["phase_bits"]]),
                ]
                with m.If(phase_overflow):
                    m.d.sync += spikes.bit_select(band, 1).eq(1)
                with m.If(band == 7):
                    final_spikes = spikes | Mux(phase_overflow, 1 << band, 0)
                    m.d.sync += [
                        output_payload.encoder_spikes.eq(final_spikes),
                        output_payload.external_drive.eq(external_drive),
                        output_payload.inhibitory_gain_q8.eq(inhibitory_gain_q8),
                        output_payload.adaptation_gain_q8.eq(adaptation_gain_q8),
                        output_valid.eq(1),
                        self.sample_index.eq(self.sample_index + 1),
                    ]
                    m.next = "IDLE"
                with m.Else():
                    m.d.sync += band.eq(band + 1)
                    m.next = "PREPARE_B0"

        return m


class SNN2TestSource(wiring.Component):
    """Deterministic one-band-at-a-time source for complete AV self-test."""

    def __init__(self):
        super().__init__({
            "o": Out(stream.Signature(SNN2_INPUT_LAYOUT)),
            "band_levels": Out(data.ArrayLayout(8, 8)),
        })
        self.sample_index = Signal(32)

    def elaborate(self, platform):
        m = Module()
        selected = self.sample_index[:3]
        m.d.comb += [
            self.o.valid.eq(1),
            self.o.payload.encoder_spikes.eq(1 << selected),
            self.o.payload.external_drive.eq(16_000),
            self.o.payload.inhibitory_gain_q8.eq(256),
            self.o.payload.adaptation_gain_q8.eq(256),
        ]
        for band in range(8):
            m.d.comb += self.band_levels[band].eq(Mux(selected == band, 255, 0))
        with m.If(self.o.ready):
            m.d.sync += self.sample_index.eq(self.sample_index + 1)
        return m
