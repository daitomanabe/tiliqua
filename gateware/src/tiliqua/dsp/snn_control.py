# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Direct MIDI and front-panel control for the live SNN input vector."""

from amaranth import Array, Cat, Const, Module, Mux, Signal, signed, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from .. import midi
from . import ASQ


class SNNControlSurface(wiring.Component):
    """Route four analogue controls with optional local absolute overrides.

    The untouched state is transparent: all four calibrated PMOD samples pass
    through unchanged. MIDI CC 20..23 or an encoder step activates an absolute
    override for one parameter. CC 24 >= 64 or a one-second button hold clears
    every override and immediately returns control to the analogue inputs.
    Short button presses select drive, leak, recurrence, then threshold. A
    three-second hold remains available to the top-level reboot provider.
    """

    DRIVE_MAX = 8_000
    BIPOLAR_MAX = 4_000
    MIDI_CCS = (20, 21, 22, 23)
    PRESETS = (
        (800, 2400, -2400, 2400),
        (1800, 2400, 0, 2400),
        (3200, 0, 0, 0),
        (4400, 0, 0, 0),
        (5200, -2400, 2400, -2400),
        (6000, 0, 2400, 0),
        (4800, 2400, -2400, 0),
        (8000, -2400, 2400, -2400),
    )

    def __init__(self, *, clock_sync_hz=60_000_000, clear_hold_seconds=1.0):
        if clock_sync_hz < 10:
            raise ValueError("clock_sync_hz must be at least 10 Hz")
        if not 0.05 <= clear_hold_seconds < 2.5:
            raise ValueError("clear hold must be between 0.05 and 2.5 seconds")
        self.clear_hold_ticks = max(1, round(clock_sync_hz * clear_hold_seconds))
        self.selected = Signal(2)
        self.override_mask = Signal(4)
        self.control_values = [Signal(signed(16), name=f"control_value_{index}")
                               for index in range(4)]
        self.effective_values = [Signal(signed(16), name=f"effective_value_{index}")
                                 for index in range(4)]
        self.control_levels = Signal(32)
        super().__init__({
            "i": In(stream.Signature(data.ArrayLayout(ASQ, 4))),
            "o": Out(stream.Signature(data.ArrayLayout(ASQ, 4))),
            "i_midi": In(stream.Signature(midi.MidiMessage)),
            "encoder_step": In(unsigned(1)),
            "encoder_direction": In(unsigned(1)),
            "button": In(unsigned(1)),
        })

    def elaborate(self, platform):
        m = Module()

        control_array = Array(self.control_values)
        input_array = Array(self.i.payload[index].as_value() for index in range(4))
        midi_transfer = Signal()
        m.d.comb += [
            midi_transfer.eq(self.i_midi.valid & self.i_midi.ready),
            self.o.valid.eq(self.i.valid),
            self.i.ready.eq(self.o.ready),
            self.i_midi.ready.eq(1),
        ]

        for index in range(4):
            input_value = self.i.payload[index].as_value()
            m.d.comb += [
                self.effective_values[index].eq(Mux(
                    self.override_mask[index],
                    self.control_values[index],
                    input_value,
                )),
                self.o.payload[index].as_value().eq(self.effective_values[index]),
            ]
        level_values = [Signal(8, name=f"control_level_{index}")
                        for index in range(4)]
        m.d.comb += [
            level_values[0].eq(Mux(
                self.effective_values[0] <= 0,
                0,
                Mux(
                    self.effective_values[0] >= self.DRIVE_MAX,
                    255,
                    self.effective_values[0] >> 5,
                ),
            )),
            *(
                level.eq(Mux(
                    value <= -self.BIPOLAR_MAX,
                    0,
                    Mux(
                        value >= self.BIPOLAR_MAX,
                        255,
                        (value + self.BIPOLAR_MAX) >> 5,
                    ),
                ))
                for level, value in zip(level_values[1:], self.effective_values[1:])
            ),
            self.control_levels.eq(Cat(*level_values)),
        ]

        button_counter = Signal(range(self.clear_hold_ticks + 1))
        button_previous = Signal()
        clear_pulse = Signal()
        m.d.sync += button_previous.eq(self.button)
        with m.If(self.button):
            with m.If(button_counter < self.clear_hold_ticks):
                m.d.sync += button_counter.eq(button_counter + 1)
            with m.If(button_counter == self.clear_hold_ticks - 1):
                m.d.comb += clear_pulse.eq(1)
        with m.Else():
            with m.If(button_previous & (button_counter < self.clear_hold_ticks)):
                m.d.sync += self.selected.eq(self.selected + 1)
            m.d.sync += button_counter.eq(0)

        midi_message = self.i_midi.payload
        midi_cc = midi_message.midi_payload.control_change.controller_number
        midi_value = midi_message.midi_payload.control_change.data
        is_cc = Signal()
        is_program = Signal()
        m.d.comb += [
            is_cc.eq(midi_message.status.kind == midi.Status.Kind.CONTROL_CHANGE),
            is_program.eq(midi_message.status.kind == midi.Status.Kind.PROGRAM_CHANGE),
        ]

        selected_source = Signal(signed(16))
        encoder_candidate = Signal(signed(17))
        encoder_target = Signal(signed(16))
        selected_is_overridden = self.override_mask.bit_select(self.selected, 1)
        m.d.comb += [
            selected_source.eq(Mux(
                selected_is_overridden,
                control_array[self.selected],
                input_array[self.selected],
            )),
            encoder_candidate.eq(
                selected_source + Mux(self.encoder_direction, 125, -125)
            ),
            encoder_target.eq(Mux(
                self.selected == 0,
                Mux(
                    encoder_candidate < 0,
                    0,
                    Mux(encoder_candidate > self.DRIVE_MAX,
                        self.DRIVE_MAX, encoder_candidate),
                ),
                Mux(
                    encoder_candidate < -self.BIPOLAR_MAX,
                    -self.BIPOLAR_MAX,
                    Mux(encoder_candidate > self.BIPOLAR_MAX,
                        self.BIPOLAR_MAX, encoder_candidate),
                ),
            )),
        ]

        cc_index = Signal(2)
        cc_bipolar = Signal(signed(16))
        cc_bipolar_bounded = Signal(signed(16))
        cc_drive = Signal(signed(16))
        m.d.comb += [
            cc_index.eq(midi_cc - self.MIDI_CCS[0]),
            cc_bipolar.eq((midi_value.as_signed() - 64) << 6),
            cc_bipolar_bounded.eq(Mux(
                midi_value <= 1,
                -self.BIPOLAR_MAX,
                Mux(midi_value >= 126, self.BIPOLAR_MAX, cc_bipolar),
            )),
            cc_drive.eq(Mux(midi_value >= 125, self.DRIVE_MAX, midi_value << 6)),
        ]

        program_number = midi_message.midi_payload.program_change.program_number
        preset_arrays = [Array(Const(row[index], signed(16)) for row in self.PRESETS)
                         for index in range(4)]

        with m.If(clear_pulse):
            m.d.sync += self.override_mask.eq(0)
        with m.Elif(midi_transfer & is_cc & (midi_cc == 24)):
            with m.If(midi_value >= 64):
                m.d.sync += self.override_mask.eq(0)
            with m.Else():
                m.d.sync += self.override_mask.bit_select(self.selected, 1).eq(0)
        with m.Elif(
            midi_transfer & is_cc
            & (midi_cc >= self.MIDI_CCS[0]) & (midi_cc <= self.MIDI_CCS[-1])
        ):
            m.d.sync += [
                control_array[cc_index].eq(Mux(
                    cc_index == 0, cc_drive, cc_bipolar_bounded
                )),
                self.override_mask.bit_select(cc_index, 1).eq(1),
                self.selected.eq(cc_index),
            ]
        with m.Elif(midi_transfer & is_program & (program_number < len(self.PRESETS))):
            for index in range(4):
                m.d.sync += self.control_values[index].eq(preset_arrays[index][program_number])
            m.d.sync += self.override_mask.eq(0b1111)
        with m.Elif(self.encoder_step):
            m.d.sync += [
                control_array[self.selected].eq(encoder_target),
                self.override_mask.bit_select(self.selected, 1).eq(1),
            ]

        return m


class SNNMidiCCDecoder(wiring.Component):
    """Small running-status decoder for only MIDI CC and Program Change.

    System realtime bytes are ignored without disturbing a partial message.
    Unsupported channel/system statuses clear running status. This is much
    smaller than the general decoder and is sufficient for the four SNN CCs.
    """

    def __init__(self):
        super().__init__({
            "i": In(stream.Signature(unsigned(8))),
            "o": Out(stream.Signature(midi.MidiMessage)),
        })

    def elaborate(self, platform):
        m = Module()

        WAIT_DATA0 = 0
        WAIT_DATA1 = 1
        OUTPUT = 2
        state = Signal(2)
        running_status = Signal(8)
        running_valid = Signal()
        data0 = Signal(7)
        data1 = Signal(7)
        byte = self.i.payload
        is_status = Signal()
        is_realtime = Signal()
        supported_status = Signal()
        is_program = Signal()
        m.d.comb += [
            is_status.eq(byte[7]),
            is_realtime.eq(byte >= 0xF8),
            supported_status.eq((byte[4:8] == 0xB) | (byte[4:8] == 0xC)),
            is_program.eq(running_status[4:8] == 0xC),
            self.i.ready.eq(state != OUTPUT),
            self.o.valid.eq(state == OUTPUT),
            self.o.payload.status.is_status.eq(1),
            self.o.payload.status.kind.eq(Mux(
                is_program,
                midi.Status.Kind.PROGRAM_CHANGE,
                midi.Status.Kind.CONTROL_CHANGE,
            )),
            self.o.payload.status.nibble.channel.eq(running_status[:4]),
            self.o.payload.midi_payload.raw.byte0.eq(data0),
            self.o.payload.midi_payload.raw.byte1.eq(data1),
        ]

        with m.If((state == OUTPUT) & self.o.ready):
            m.d.sync += state.eq(WAIT_DATA0)
        with m.Elif(self.i.valid & self.i.ready):
            with m.If(is_realtime):
                pass
            with m.Elif(is_status):
                with m.If(supported_status):
                    m.d.sync += [
                        running_status.eq(byte),
                        running_valid.eq(1),
                        state.eq(WAIT_DATA0),
                    ]
                with m.Else():
                    m.d.sync += [running_valid.eq(0), state.eq(WAIT_DATA0)]
            with m.Elif(running_valid):
                with m.If(state == WAIT_DATA0):
                    m.d.sync += data0.eq(byte[:7])
                    with m.If(is_program):
                        m.d.sync += [data1.eq(0), state.eq(OUTPUT)]
                    with m.Else():
                        m.d.sync += state.eq(WAIT_DATA1)
                with m.Elif(state == WAIT_DATA1):
                    m.d.sync += [data1.eq(byte[:7]), state.eq(OUTPUT)]

        return m
