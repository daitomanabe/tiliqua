# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Frame-buffer-free 8x8 through 64x8 neural activity visualizer."""

from amaranth import Cat, Elaboratable, Module, Mux, Signal


class SNNVisualizer(Elaboratable):
    """Draw one cell per neuron from synchronized spike/membrane snapshots."""

    def __init__(self, *, neuron_count=64):
        if neuron_count not in (64, 128, 256, 512):
            raise ValueError("visualizer supports 64, 128, 256, or 512 neurons")
        self.neuron_count = neuron_count
        self.x_cell_shift = {64: 6, 128: 5, 256: 4, 512: 3}[neuron_count]
        self.x = Signal(12)
        self.y = Signal(12)
        self.spikes = Signal(neuron_count)
        self.membrane_levels = Signal(neuron_count * 4)
        self.activity = Signal((neuron_count + 1).bit_length())
        self.burst = Signal()
        self.frame = Signal(8)
        self.r = Signal(8)
        self.g = Signal(8)
        self.b = Signal(8)

    def elaborate(self, platform):
        m = Module()

        local_x = Signal(10)
        local_y = Signal(10)
        neuron_index = Signal(range(self.neuron_count))
        selected_spike = Signal()
        selected_level = Signal(4)
        inside_grid = Signal()
        cell_edge = Signal()
        checker = Signal()
        level_byte = Signal(8)
        activity_byte = Signal(8)

        m.d.comb += [
            inside_grid.eq(
                (self.x >= 104) & (self.x < 616)
                & (self.y >= 104) & (self.y < 616)
            ),
            local_x.eq(self.x - 104),
            local_y.eq(self.y - 104),
            neuron_index.eq(Cat(
                local_x[self.x_cell_shift:9],
                local_y[6:9],
            )),
            selected_spike.eq(self.spikes.bit_select(neuron_index, 1)),
            selected_level.eq(self.membrane_levels.word_select(neuron_index, 4)),
            cell_edge.eq(
                (local_x[0:self.x_cell_shift] < 2)
                | (local_x[0:self.x_cell_shift] >= (1 << self.x_cell_shift) - 2)
                | (local_y[0:6] < 2) | (local_y[0:6] >= 62)
            ),
            checker.eq(self.x[5] ^ self.y[5] ^ self.frame[3]),
            level_byte.eq(Cat(selected_level, selected_level)),
            activity_byte.eq(Mux(self.activity > 63, 255, self.activity << 2)),
        ]

        with m.If(~inside_grid):
            m.d.comb += [
                self.r.eq(Mux(checker, 10, 3)),
                self.g.eq(Mux(self.burst, activity_byte, Mux(checker, 14, 4))),
                self.b.eq(Mux(checker, 24, 8)),
            ]
        with m.Elif(cell_edge):
            m.d.comb += [self.r.eq(20), self.g.eq(32), self.b.eq(48)]
        with m.Elif(selected_spike):
            m.d.comb += [self.r.eq(255), self.g.eq(255), self.b.eq(255)]
        with m.Else():
            m.d.comb += [
                self.r.eq(level_byte),
                self.g.eq(Mux(self.burst, activity_byte, level_byte >> 2)),
                self.b.eq(255 - level_byte),
            ]

        return m
