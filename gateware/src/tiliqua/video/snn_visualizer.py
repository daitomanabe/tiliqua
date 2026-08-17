# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Frame-buffer-free 8x8 through 64x16 neural activity visualizer."""

from amaranth import Cat, Elaboratable, Module, Mux, Signal


class SNNVisualizer(Elaboratable):
    """Draw one cell per neuron from synchronized spike/membrane snapshots."""

    def __init__(
        self, *, neuron_count=64, membrane_level_bits=4, external_rows=False,
        inhibitory_stride=None,
    ):
        if neuron_count not in (64, 128, 256, 512, 1024):
            raise ValueError(
                "visualizer supports 64, 128, 256, 512, or 1024 neurons"
            )
        self.neuron_count = neuron_count
        if membrane_level_bits not in (2, 4):
            raise ValueError("membrane_level_bits must be 2 or 4")
        self.membrane_level_bits = membrane_level_bits
        self.external_rows = external_rows
        if inhibitory_stride is not None and inhibitory_stride != 4:
            raise ValueError("only an inhibitory stride of four is supported")
        self.inhibitory_stride = inhibitory_stride
        self.x_cell_shift = {64: 6, 128: 5, 256: 4, 512: 3, 1024: 3}[
            neuron_count
        ]
        self.y_cell_shift = 5 if neuron_count == 1024 else 6
        self.x = Signal(12)
        self.y = Signal(12)
        self.spikes = Signal(neuron_count)
        self.membrane_levels = Signal(neuron_count * membrane_level_bits)
        self.activity = Signal((neuron_count + 1).bit_length())
        self.burst = Signal()
        self.frame = Signal(8)
        self.control_selected = Signal(2)
        self.control_override = Signal(4)
        self.control_levels = Signal(32)
        self.neuron_index = Signal(range(neuron_count))
        self.external_row_addr = Signal(range(max(2, neuron_count // 32)))
        self.external_row_data = Signal(32 * (1 + membrane_level_bits))
        self.r = Signal(8)
        self.g = Signal(8)
        self.b = Signal(8)

    def elaborate(self, platform):
        m = Module()

        local_x = Signal(10)
        local_y = Signal(10)
        selected_spike = Signal()
        selected_level = Signal(self.membrane_level_bits)
        inside_grid = Signal()
        cell_edge = Signal()
        checker = Signal()
        level_byte = Signal(8)
        activity_byte = Signal(8)
        inhibitory_cell = Signal()
        inside_controls = Signal()
        control_index = Signal(2)
        control_x = Signal(7)
        control_level = Signal(8)
        control_filled = Signal()
        control_selected = Signal()
        control_overridden = Signal()

        m.d.comb += [
            inside_grid.eq(
                (self.x >= 104) & (self.x < 616)
                & (self.y >= 104) & (self.y < 616)
            ),
            local_x.eq(self.x - 104),
            local_y.eq(self.y - 104),
            self.neuron_index.eq(Cat(
                local_x[self.x_cell_shift:9],
                local_y[self.y_cell_shift:9],
            )),
            self.external_row_addr.eq(self.neuron_index[5:]),
            cell_edge.eq(
                (local_x[0:self.x_cell_shift] < 2)
                | (local_x[0:self.x_cell_shift] >= (1 << self.x_cell_shift) - 2)
                | (local_y[0:self.y_cell_shift] < 2)
                | (local_y[0:self.y_cell_shift]
                   >= (1 << self.y_cell_shift) - 2)
            ),
            checker.eq(self.x[5] ^ self.y[5] ^ self.frame[3]),
            level_byte.eq(Cat(*(
                [selected_level] * (8 // self.membrane_level_bits)
            ))),
            activity_byte.eq(Mux(self.activity > 63, 255, self.activity << 2)),
            inhibitory_cell.eq(
                (self.neuron_index[:2] == 3)
                if self.inhibitory_stride == 4 else 0
            ),
            inside_controls.eq(
                (self.x >= 104) & (self.x < 616)
                & (self.y >= 56) & (self.y < 80)
            ),
            control_index.eq((self.x - 104)[7:9]),
            control_x.eq((self.x - 104)[:7]),
            control_level.eq(self.control_levels.word_select(control_index, 8)),
            control_filled.eq(control_x < (control_level >> 1)),
            control_selected.eq(control_index == self.control_selected),
            control_overridden.eq(self.control_override.bit_select(control_index, 1)),
        ]

        if self.external_rows:
            selected_display = Signal(1 + self.membrane_level_bits)
            m.d.comb += [
                selected_display.eq(self.external_row_data.word_select(
                    self.neuron_index[:5], 1 + self.membrane_level_bits
                )),
                selected_spike.eq(selected_display[0]),
                selected_level.eq(selected_display[1:]),
            ]
        else:
            m.d.comb += [
                selected_spike.eq(self.spikes.bit_select(
                    self.neuron_index, 1
                )),
                selected_level.eq(self.membrane_levels.word_select(
                    self.neuron_index, self.membrane_level_bits
                )),
            ]

        with m.If(inside_controls):
            with m.If((control_x < 2) | (control_x >= 126)
                      | (self.y < 58) | (self.y >= 78)):
                m.d.comb += [
                    self.r.eq(Mux(control_selected, 255, 38)),
                    self.g.eq(Mux(control_selected, 255, 52)),
                    self.b.eq(Mux(control_selected, 255, 72)),
                ]
            with m.Elif(control_filled):
                m.d.comb += [
                    self.r.eq(Mux(control_overridden, 255, 36 + (control_index << 4))),
                    self.g.eq(Mux(control_index == 1, 232, 88 + (control_index << 5))),
                    self.b.eq(Mux(control_index == 0, 255, 128 + (control_index << 4))),
                ]
            with m.Else():
                m.d.comb += [self.r.eq(8), self.g.eq(13), self.b.eq(24)]
        with m.Elif(~inside_grid):
            m.d.comb += [
                self.r.eq(Mux(checker, 10, 3)),
                self.g.eq(Mux(self.burst, activity_byte, Mux(checker, 14, 4))),
                self.b.eq(Mux(checker, 24, 8)),
            ]
        with m.Elif(cell_edge):
            m.d.comb += [self.r.eq(20), self.g.eq(32), self.b.eq(48)]
        with m.Elif(selected_spike):
            m.d.comb += [
                self.r.eq(255),
                self.g.eq(Mux(inhibitory_cell, 96, 255)),
                self.b.eq(Mux(inhibitory_cell, 32, 255)),
            ]
        with m.Else():
            m.d.comb += [
                self.r.eq(Mux(inhibitory_cell, 96 + (level_byte >> 1), level_byte)),
                self.g.eq(Mux(self.burst, activity_byte, level_byte >> 2)),
                self.b.eq(Mux(inhibitory_cell, 32, 255 - level_byte)),
            ]

        return m
