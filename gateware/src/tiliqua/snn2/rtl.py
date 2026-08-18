# Copyright (c) 2026 Daito Manabe
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""Memory-banked 256-neuron, 16-lane sparse ALIF engine for SNN2 v1."""

from __future__ import annotations

from amaranth import Array, Cat, Const, Module, Mux, Signal, signed, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.memory import Memory
from amaranth.lib.wiring import In, Out

from tiliqua.dsp import ASQ

from .manifest import compile_edge_banks, validate_manifest


SNN2_INPUT_LAYOUT = data.StructLayout({
    "encoder_spikes": unsigned(8),
    "external_drive": signed(18),
    "inhibitory_gain_q8": unsigned(10),
    "adaptation_gain_q8": unsigned(10),
})


def _balanced_sum(values):
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


def _pack_initial_state(neuron):
    return (
        (neuron["initial_v"] & ((1 << 18) - 1))
        | (neuron["initial_ie"] << 18)
        | (neuron["initial_ii"] << 34)
        | (neuron["initial_a"] << 50)
        | (neuron["initial_r"] << 66)
    )


class SparseALIFNetwork(wiring.Component):
    """Execute one atomic SNN2 sample with no sparse-event drops.

    Sixteen state memories hold one physical lane each. Sixteen event
    sub-banks refine the specified four target banks by ``target_id[3:0]``;
    each four-edge issue round still touches exactly one of the four normative
    ``target_id[1:0]`` banks, while the sub-banking provides the 16 simultaneous
    reads required by the neuron engine.
    """

    def __init__(self, manifest: dict, *, initial_spikes: int = 0):
        self.manifest = validate_manifest(manifest)
        self.edge_banks = compile_edge_banks(self.manifest)
        self.initial_spikes = initial_spikes & ((1 << 256) - 1)
        super().__init__({
            "i": In(stream.Signature(SNN2_INPUT_LAYOUT)),
            "o": Out(stream.Signature(data.ArrayLayout(ASQ, 4))),
            "debug_index": In(unsigned(8)),
            "debug_v": Out(signed(18)),
            "debug_ie": Out(unsigned(16)),
            "debug_ii": Out(unsigned(16)),
            "debug_a": Out(unsigned(16)),
            "debug_r": Out(unsigned(4)),
            "debug_s": Out(unsigned(1)),
        })
        self.spike_vector = Signal(256, init=self.initial_spikes)
        self.display_row_addr = Signal(4)
        self.display_row_data = Signal(16 * 5)
        self.display_row_valid = Signal()
        self.spike_count = Signal(range(257), init=self.initial_spikes.bit_count())
        self.excitatory_spike_count = Signal(range(193))
        self.inhibitory_spike_count = Signal(range(65))
        self.readout_debug = Signal(signed(18))
        self.sample_index = Signal(32)
        self.event_count = Signal(range(2049))
        self.scheduler_cycles = Signal(range(2049))
        self.deadline_cycles = Signal(range(1251))
        self.fault = Signal()
        self.busy = Signal()

    def elaborate(self, platform):
        m = Module()
        dynamics = self.manifest["dynamics"]

        output_valid = Signal()
        output_payload = Signal(data.ArrayLayout(ASQ, 4))
        working = Signal()
        scheduler_working = Signal()
        deadline_work = Signal(range(1251))
        scheduler_work = Signal(range(2049))

        encoder_spikes = Signal(8)
        external_drive = Signal(signed(18))
        inhibitory_gain_q8 = Signal(10, init=256)
        adaptation_gain_q8 = Signal(10, init=256)

        clear_addr = Signal(4)
        issue_addr = Signal(9)
        batch_index = Signal(4)
        next_spike_vector = Signal(256)
        excitatory_accumulator = Signal(range(193))
        inhibitory_accumulator = Signal(range(65))
        readout_accumulator = Signal(signed(18))
        m.d.comb += self.readout_debug.eq(readout_accumulator)

        previous_readout = Signal(signed(18))
        dc_state = Signal(signed(18))
        excitatory_lp = Signal(range(20_001))
        inhibitory_lp = Signal(range(20_001))

        m.d.comb += [
            self.o.valid.eq(output_valid),
            self.o.payload.eq(output_payload),
            self.busy.eq(working),
        ]
        with m.If(working & (deadline_work < 1250)):
            m.d.sync += deadline_work.eq(deadline_work + 1)
        with m.If(scheduler_working):
            m.d.sync += scheduler_work.eq(scheduler_work + 1)

        state_memories = []
        state_reads = []
        state_writes = []
        for lane in range(16):
            initial = [
                _pack_initial_state(self.manifest["neurons"][batch * 16 + lane])
                for batch in range(16)
            ]
            memory = Memory(
                shape=unsigned(70),
                depth=16,
                init=initial,
                attrs={"ram_style": "block"},
            )
            setattr(m.submodules, f"state_memory_{lane}", memory)
            write = memory.write_port()
            read = memory.read_port(transparent_for=(write,))
            state_memories.append(memory)
            state_reads.append(read)
            state_writes.append(write)

        event_memories = []
        event_reads = []
        event_writes = []
        for lane in range(16):
            memory = Memory(
                # The validated v1 fan-in bound is below 16 bits, but the
                # scheduler retains one guard bit for each population so no
                # per-edge clamp can make accumulation order-dependent.
                shape=unsigned(34),
                depth=16,
                init=[0] * 16,
                attrs={"ram_style": "distributed"},
            )
            setattr(m.submodules, f"event_memory_{lane}", memory)
            write = memory.write_port()
            read = memory.read_port(transparent_for=(write,))
            event_memories.append(memory)
            event_reads.append(read)
            event_writes.append(write)

        edge_reads = []
        for bank, words in enumerate(self.edge_banks):
            memory = Memory(
                shape=unsigned(16),
                depth=512,
                init=list(words),
                attrs={"rom_style": "block"},
            )
            setattr(m.submodules, f"edge_memory_{bank}", memory)
            read = memory.read_port()
            edge_reads.append(read)
            m.d.comb += read.addr.eq(issue_addr)

        stage_valid = [Signal(name=f"event_stage_valid_{lane}") for lane in range(16)]
        stage_addr = [Signal(4, name=f"event_stage_addr_{lane}") for lane in range(16)]
        stage_exc = [Signal(16, name=f"event_stage_exc_{lane}") for lane in range(16)]
        stage_inh = [Signal(16, name=f"event_stage_inh_{lane}") for lane in range(16)]
        current_valid = []
        current_addr = []
        current_exc = []
        current_inh = []
        scale_valid = []
        scale_addr = []
        scale_magnitude = []
        scale_is_inhibitory = []

        source_index = issue_addr[1:9]
        source_spiked = self.spike_vector.bit_select(source_index, 1)
        source_is_inhibitory = source_index[:2] == 3
        edge_request_valid = Signal()
        requested_source_spiked = Signal()
        requested_source_is_inhibitory = Signal()
        for lane in range(16):
            record = edge_reads[lane % 4].data
            target = record[:8]
            weight = record[8:16].as_signed()
            magnitude = Signal(8, name=f"event_magnitude_{lane}")
            scaled_inhibition = Signal(18, name=f"scaled_inhibition_{lane}")
            scale_valid_lane = Signal(name=f"scale_valid_{lane}")
            scale_addr_lane = Signal(4, name=f"scale_addr_{lane}")
            scale_magnitude_lane = Signal(8, name=f"scale_magnitude_{lane}")
            scale_is_inhibitory_lane = Signal(
                name=f"scale_is_inhibitory_{lane}"
            )
            valid = Signal(name=f"current_event_valid_{lane}")
            address = Signal(4, name=f"current_event_addr_{lane}")
            exc = Signal(16, name=f"current_event_exc_{lane}")
            inh = Signal(16, name=f"current_event_inh_{lane}")
            m.d.comb += [
                magnitude.eq(Mux(weight < 0, -weight, weight)),
                scaled_inhibition.eq(
                    scale_magnitude_lane * inhibitory_gain_q8
                ),
                valid.eq(scale_valid_lane),
                address.eq(scale_addr_lane),
                exc.eq(Mux(
                    scale_is_inhibitory_lane, 0, scale_magnitude_lane
                )),
                inh.eq(Mux(
                    scale_is_inhibitory_lane,
                    scaled_inhibition >> 8,
                    0,
                )),
            ]
            scale_valid.append(scale_valid_lane)
            scale_addr.append(scale_addr_lane)
            scale_magnitude.append(scale_magnitude_lane)
            scale_is_inhibitory.append(scale_is_inhibitory_lane)
            current_valid.append(valid)
            current_addr.append(address)
            current_exc.append(exc)
            current_inh.append(inh)

        for lane in range(16):
            event_exc_sum = Signal(18, name=f"event_exc_sum_{lane}")
            event_inh_sum = Signal(18, name=f"event_inh_sum_{lane}")
            event_exc_next = Signal(17, name=f"event_exc_next_{lane}")
            event_inh_next = Signal(17, name=f"event_inh_next_{lane}")
            m.d.comb += [
                state_reads[lane].addr.eq(self.debug_index[4:8]),
                state_writes[lane].addr.eq(batch_index),
                state_writes[lane].data.eq(0),
                state_writes[lane].en.eq(0),
                event_reads[lane].addr.eq(0),
                event_writes[lane].addr.eq(stage_addr[lane]),
                event_writes[lane].data.eq(0),
                event_writes[lane].en.eq(0),
                event_exc_sum.eq(
                    event_reads[lane].data[:17] + stage_exc[lane]
                ),
                event_inh_sum.eq(
                    event_reads[lane].data[17:34] + stage_inh[lane]
                ),
                event_exc_next.eq(event_exc_sum[:17]),
                event_inh_next.eq(event_inh_sum[:17]),
            ]
            with m.If(stage_valid[lane]):
                m.d.comb += [
                    event_writes[lane].en.eq(1),
                    event_writes[lane].data.eq(Cat(
                        event_exc_next, event_inh_next
                    )),
                ]

        debug_words = Array(read.data for read in state_reads)
        debug_word = debug_words[self.debug_index[:4]]
        m.d.comb += [
            self.debug_v.eq(debug_word[:18].as_signed()),
            self.debug_ie.eq(debug_word[18:34]),
            self.debug_ii.eq(debug_word[34:50]),
            self.debug_a.eq(debug_word[50:66]),
            self.debug_r.eq(debug_word[66:70]),
            self.debug_s.eq(self.spike_vector.bit_select(self.debug_index, 1)),
        ]

        adaptation_step_scaled_wide = Signal(26)
        adaptation_step_scaled = Signal(18)
        m.d.comb += [
            adaptation_step_scaled_wide.eq(
                dynamics["adaptation_step"] * adaptation_gain_q8
            ),
            adaptation_step_scaled.eq(adaptation_step_scaled_wide >> 8),
        ]

        lane_spikes = []
        lane_next_states = []
        lane_membrane_levels = []
        lane_readout_terms = []
        stage1_captures = []
        stage2_captures = []
        stage3_captures = []
        for lane in range(16):
            state_word = state_reads[lane].data
            event_word = event_reads[lane].data
            v = state_word[:18].as_signed()
            ie = state_word[18:34]
            ii = state_word[34:50]
            adaptation = state_word[50:66]
            refractory = state_word[66:70]

            ie_wide = Signal(17, name=f"ie_wide_{lane}")
            ii_wide = Signal(17, name=f"ii_wide_{lane}")
            ie_next_comb = Signal(16, name=f"ie_next_comb_{lane}")
            ii_next_comb = Signal(16, name=f"ii_next_comb_{lane}")
            adaptation_decay_comb = Signal(16, name=f"adaptation_decay_comb_{lane}")
            v_leak_comb = Signal(signed(19), name=f"v_leak_comb_{lane}")
            drive_bias_comb = Signal(signed(20), name=f"drive_bias_comb_{lane}")

            stage1_ie = Signal(16, name=f"stage1_ie_{lane}")
            stage1_ii = Signal(16, name=f"stage1_ii_{lane}")
            stage1_adaptation = Signal(16, name=f"stage1_adaptation_{lane}")
            stage1_refractory = Signal(4, name=f"stage1_refractory_{lane}")
            stage1_v_leak = Signal(signed(19), name=f"stage1_v_leak_{lane}")
            stage1_drive_bias = Signal(signed(20), name=f"stage1_drive_bias_{lane}")
            stage1_readout_weight = Signal(signed(8), name=f"stage1_readout_{lane}")

            v_candidate_wide = Signal(signed(23), name=f"v_candidate_wide_{lane}")
            v_candidate_comb = Signal(signed(18), name=f"v_candidate_comb_{lane}")
            stage2_v_candidate = Signal(signed(18), name=f"stage2_v_{lane}")
            stage2_ie = Signal(16, name=f"stage2_ie_{lane}")
            stage2_ii = Signal(16, name=f"stage2_ii_{lane}")
            stage2_adaptation = Signal(16, name=f"stage2_adaptation_{lane}")
            stage2_refractory = Signal(4, name=f"stage2_refractory_{lane}")
            stage2_readout_weight = Signal(signed(8), name=f"stage2_readout_{lane}")

            adaptive_threshold = Signal(signed(19), name=f"adaptive_threshold_{lane}")
            adaptation_sum = Signal(18, name=f"adaptation_sum_{lane}")
            adaptation_next = Signal(16, name=f"adaptation_next_{lane}")
            spike_comb = Signal(name=f"spike_comb_{lane}")
            v_next_comb = Signal(signed(18), name=f"v_next_comb_{lane}")
            r_next = Signal(4, name=f"r_next_{lane}")
            a_next = Signal(16, name=f"a_next_{lane}")
            next_state_comb = Signal(70, name=f"next_state_comb_{lane}")
            stage3_state = Signal(70, name=f"stage3_state_{lane}")
            stage3_spike = Signal(name=f"stage3_spike_{lane}")

            biases = Array(
                Const(
                    self.manifest["neurons"][batch * 16 + lane]["bias"],
                    signed(18),
                )
                for batch in range(16)
            )
            readout_weights = Array(
                Const(
                    self.manifest["readout_weights"][batch * 16 + lane],
                    signed(8),
                )
                for batch in range(16)
            )
            encoder_masks = []
            for batch in range(16):
                neuron_index = batch * 16 + lane
                if neuron_index % 4 == 3:
                    encoder_masks.append(Const(0, 8))
                else:
                    excitatory_rank = neuron_index - ((neuron_index + 1) // 4)
                    encoder_masks.append(Const(1 << (excitatory_rank // 24), 8))
            encoder_masks = Array(encoder_masks)
            direct_drive = Signal(signed(18), name=f"direct_drive_{lane}")
            readout_term = Signal(signed(9), name=f"readout_term_{lane}")
            membrane_level = Signal(4, name=f"membrane_level_{lane}")

            m.d.comb += [
                ie_wide.eq(ie - (ie >> dynamics["tau_e"]) + event_word[:17]),
                ii_wide.eq(ii - (ii >> dynamics["tau_i"]) + event_word[17:34]),
                ie_next_comb.eq(Mux(ie_wide > 0xFFFF, 0xFFFF, ie_wide[:16])),
                ii_next_comb.eq(Mux(ii_wide > 0xFFFF, 0xFFFF, ii_wide[:16])),
                adaptation_decay_comb.eq(
                    adaptation - (adaptation >> dynamics["tau_a"])
                ),
                v_leak_comb.eq(v - (v >> dynamics["tau_m"])),
                direct_drive.eq(Mux(
                    (encoder_spikes & encoder_masks[batch_index]) != 0,
                    external_drive,
                    0,
                )),
                drive_bias_comb.eq(direct_drive + biases[batch_index]),
                v_candidate_wide.eq(
                    stage1_v_leak
                    + stage1_ie
                    - stage1_ii
                    + stage1_drive_bias
                ),
                v_candidate_comb.eq(Mux(
                    v_candidate_wide > (1 << 17) - 1,
                    Const((1 << 17) - 1, signed(18)),
                    Mux(
                        v_candidate_wide < -(1 << 17),
                        Const(-(1 << 17), signed(18)),
                        v_candidate_wide[:18],
                    ),
                )),
                adaptive_threshold.eq(
                    dynamics["threshold_base"] + stage2_adaptation
                ),
                adaptation_sum.eq(
                    stage2_adaptation + adaptation_step_scaled
                ),
                adaptation_next.eq(Mux(
                    adaptation_sum > 0xFFFF,
                    0xFFFF,
                    adaptation_sum[:16],
                )),
                spike_comb.eq(
                    (stage2_refractory == 0)
                    & (stage2_v_candidate >= adaptive_threshold)
                ),
                v_next_comb.eq(Mux(
                    (stage2_refractory != 0) | spike_comb,
                    dynamics["reset_level"],
                    stage2_v_candidate,
                )),
                r_next.eq(Mux(
                    stage2_refractory != 0,
                    stage2_refractory - 1,
                    Mux(spike_comb, dynamics["refractory_samples"], 0),
                )),
                a_next.eq(Mux(
                    spike_comb, adaptation_next, stage2_adaptation
                )),
                next_state_comb.eq(Cat(
                    v_next_comb, stage2_ie, stage2_ii, a_next, r_next
                )),
                readout_term.eq(Mux(
                    stage3_spike,
                    stage2_readout_weight,
                    0,
                )),
                # Offset-binary high nibble maps the complete signed 18-bit
                # membrane range monotonically onto sixteen display levels.
                membrane_level.eq(stage3_state[14:18] ^ 0b1000),
            ]
            stage1_captures.append((
                (stage1_ie, ie_next_comb),
                (stage1_ii, ii_next_comb),
                (stage1_adaptation, adaptation_decay_comb),
                (stage1_refractory, refractory),
                (stage1_v_leak, v_leak_comb),
                (stage1_drive_bias, drive_bias_comb),
                (stage1_readout_weight, readout_weights[batch_index]),
            ))
            stage2_captures.append((
                (stage2_v_candidate, v_candidate_comb),
                (stage2_ie, stage1_ie),
                (stage2_ii, stage1_ii),
                (stage2_adaptation, stage1_adaptation),
                (stage2_refractory, stage1_refractory),
                (stage2_readout_weight, stage1_readout_weight),
            ))
            stage3_captures.append((
                (stage3_state, next_state_comb),
                (stage3_spike, spike_comb),
            ))
            lane_spikes.append(stage3_spike)
            lane_next_states.append(stage3_state)
            lane_readout_terms.append(readout_term)
            lane_membrane_levels.append(membrane_level)

        lane_spike_vector = Cat(*lane_spikes)
        lane_excitatory_count = Signal(range(13))
        lane_inhibitory_count = Signal(range(5))
        lane_readout_sum = Signal(signed(16))
        m.d.comb += [
            lane_excitatory_count.eq(_balanced_sum([
                lane_spikes[lane] for lane in range(16) if lane % 4 != 3
            ])),
            lane_inhibitory_count.eq(_balanced_sum([
                lane_spikes[lane] for lane in range(16) if lane % 4 == 3
            ])),
            lane_readout_sum.eq(_balanced_sum(lane_readout_terms)),
            self.display_row_addr.eq(batch_index),
            self.display_row_data.eq(Cat(*[
                Cat(lane_spikes[lane], lane_membrane_levels[lane])
                for lane in range(16)
            ])),
            self.display_row_valid.eq(0),
        ]

        excitatory_target = Signal(16)
        inhibitory_target = Signal(16)
        excitatory_difference = Signal(signed(18))
        inhibitory_difference = Signal(signed(18))
        excitatory_difference_work = Signal(signed(18))
        inhibitory_difference_work = Signal(signed(18))
        excitatory_lp_next = Signal(16)
        inhibitory_lp_next = Signal(16)
        balance = Signal(signed(18))
        balance_work = Signal(signed(18))
        dc_feedback = Signal(signed(19))
        dc_candidate = Signal(signed(20))
        dc_candidate_work = Signal(signed(20))
        dc_next = Signal(signed(18))
        dc_output = Signal(signed(16))
        m.d.comb += [
            excitatory_target.eq(excitatory_accumulator * 100),
            inhibitory_target.eq(inhibitory_accumulator * 300),
            excitatory_difference.eq(excitatory_target - excitatory_lp),
            inhibitory_difference.eq(inhibitory_target - inhibitory_lp),
            excitatory_lp_next.eq(
                excitatory_lp + (excitatory_difference_work >> 5)
            ),
            inhibitory_lp_next.eq(
                inhibitory_lp + (inhibitory_difference_work >> 5)
            ),
            balance.eq(excitatory_target - inhibitory_target),
            dc_feedback.eq(dc_state - (dc_state >> 8)),
            dc_candidate.eq(
                readout_accumulator - previous_readout + dc_feedback
            ),
            dc_next.eq(Mux(
                dc_candidate_work > (1 << 17) - 1,
                Const((1 << 17) - 1, signed(18)),
                Mux(
                    dc_candidate_work < -(1 << 17),
                    Const(-(1 << 17), signed(18)),
                    dc_candidate_work[:18],
                ),
            )),
            dc_output.eq(Mux(
                dc_next > 32767,
                Const(32767, signed(16)),
                Mux(
                    dc_next < -32768,
                    Const(-32768, signed(16)),
                    dc_next[:16],
                ),
            )),
        ]

        with m.FSM(init="IDLE"):
            with m.State("IDLE"):
                m.d.comb += [
                    working.eq(0),
                    scheduler_working.eq(0),
                    self.i.ready.eq(~output_valid | self.o.ready),
                ]
                with m.If(output_valid & self.o.ready):
                    m.d.sync += output_valid.eq(0)
                with m.If(self.i.valid & self.i.ready):
                    m.d.sync += [
                        output_valid.eq(0),
                        encoder_spikes.eq(self.i.payload.encoder_spikes),
                        external_drive.eq(self.i.payload.external_drive),
                        inhibitory_gain_q8.eq(self.i.payload.inhibitory_gain_q8),
                        adaptation_gain_q8.eq(self.i.payload.adaptation_gain_q8),
                        clear_addr.eq(0),
                        deadline_work.eq(0),
                        scheduler_work.eq(0),
                        self.event_count.eq(0),
                    ]
                    m.next = "CLEAR"

            with m.State("CLEAR"):
                m.d.comb += [working.eq(1), scheduler_working.eq(1)]
                for lane in range(16):
                    m.d.comb += [
                        event_writes[lane].addr.eq(clear_addr),
                        event_writes[lane].data.eq(0),
                        event_writes[lane].en.eq(1),
                    ]
                    m.d.sync += stage_valid[lane].eq(0)
                with m.If(clear_addr == 15):
                    m.d.sync += issue_addr.eq(0)
                    m.d.sync += edge_request_valid.eq(0)
                    m.next = "ISSUE"
                with m.Else():
                    m.d.sync += clear_addr.eq(clear_addr + 1)

            with m.State("ISSUE"):
                m.d.comb += [working.eq(1), scheduler_working.eq(1)]
                for lane in range(16):
                    m.d.comb += event_reads[lane].addr.eq(current_addr[lane])
                    m.d.sync += [
                        scale_valid[lane].eq(
                            edge_request_valid
                            & requested_source_spiked
                            & ((edge_reads[lane % 4].data[:4]) == lane)
                        ),
                        scale_addr[lane].eq(edge_reads[lane % 4].data[4:8]),
                        scale_magnitude[lane].eq(Mux(
                            edge_reads[lane % 4].data[15],
                            -edge_reads[lane % 4].data[8:16].as_signed(),
                            edge_reads[lane % 4].data[8:16],
                        )),
                        scale_is_inhibitory[lane].eq(
                            requested_source_is_inhibitory
                        ),
                        stage_valid[lane].eq(current_valid[lane]),
                        stage_addr[lane].eq(current_addr[lane]),
                        stage_exc[lane].eq(current_exc[lane]),
                        stage_inh[lane].eq(current_inh[lane]),
                    ]
                m.d.sync += [
                    edge_request_valid.eq(1),
                    requested_source_spiked.eq(source_spiked),
                    requested_source_is_inhibitory.eq(source_is_inhibitory),
                ]
                with m.If(source_spiked):
                    m.d.sync += self.event_count.eq(self.event_count + 4)
                with m.If(issue_addr == 511):
                    m.next = "EDGE_DRAIN"
                with m.Else():
                    m.d.sync += issue_addr.eq(issue_addr + 1)

            with m.State("EDGE_DRAIN"):
                m.d.comb += [working.eq(1), scheduler_working.eq(1)]
                for lane in range(16):
                    m.d.comb += event_reads[lane].addr.eq(current_addr[lane])
                    m.d.sync += [
                        scale_valid[lane].eq(
                            edge_request_valid
                            & requested_source_spiked
                            & ((edge_reads[lane % 4].data[:4]) == lane)
                        ),
                        scale_addr[lane].eq(edge_reads[lane % 4].data[4:8]),
                        scale_magnitude[lane].eq(Mux(
                            edge_reads[lane % 4].data[15],
                            -edge_reads[lane % 4].data[8:16].as_signed(),
                            edge_reads[lane % 4].data[8:16],
                        )),
                        scale_is_inhibitory[lane].eq(
                            requested_source_is_inhibitory
                        ),
                        stage_valid[lane].eq(current_valid[lane]),
                        stage_addr[lane].eq(current_addr[lane]),
                        stage_exc[lane].eq(current_exc[lane]),
                        stage_inh[lane].eq(current_inh[lane]),
                    ]
                m.d.sync += edge_request_valid.eq(0)
                m.next = "SCALE_DRAIN"

            with m.State("SCALE_DRAIN"):
                m.d.comb += [working.eq(1), scheduler_working.eq(1)]
                for lane in range(16):
                    m.d.comb += event_reads[lane].addr.eq(current_addr[lane])
                    m.d.sync += [
                        scale_valid[lane].eq(0),
                        stage_valid[lane].eq(current_valid[lane]),
                        stage_addr[lane].eq(current_addr[lane]),
                        stage_exc[lane].eq(current_exc[lane]),
                        stage_inh[lane].eq(current_inh[lane]),
                    ]
                m.next = "FLUSH"

            with m.State("FLUSH"):
                m.d.comb += [working.eq(1), scheduler_working.eq(1)]
                for lane in range(16):
                    m.d.sync += stage_valid[lane].eq(0)
                m.d.sync += [
                    self.scheduler_cycles.eq(scheduler_work + 1),
                    batch_index.eq(0),
                    next_spike_vector.eq(0),
                    excitatory_accumulator.eq(0),
                    inhibitory_accumulator.eq(0),
                    readout_accumulator.eq(0),
                ]
                with m.If(
                    (self.event_count != (self.spike_count << 3))
                    | (scheduler_work + 1 > 640)
                ):
                    m.d.sync += self.fault.eq(1)
                m.next = "NEURON_READ"

            with m.State("NEURON_READ"):
                m.d.comb += working.eq(1)
                for lane in range(16):
                    m.d.comb += [
                        state_reads[lane].addr.eq(batch_index),
                        event_reads[lane].addr.eq(batch_index),
                    ]
                m.next = "NEURON_CURRENT"

            with m.State("NEURON_CURRENT"):
                m.d.comb += working.eq(1)
                for lane in range(16):
                    m.d.comb += [
                        state_reads[lane].addr.eq(batch_index),
                        event_reads[lane].addr.eq(batch_index),
                    ]
                    for target, source in stage1_captures[lane]:
                        m.d.sync += target.eq(source)
                m.next = "NEURON_V"

            with m.State("NEURON_V"):
                m.d.comb += working.eq(1)
                for lane in range(16):
                    for target, source in stage2_captures[lane]:
                        m.d.sync += target.eq(source)
                m.next = "NEURON_COMMIT"

            with m.State("NEURON_COMMIT"):
                m.d.comb += working.eq(1)
                for lane in range(16):
                    for target, source in stage3_captures[lane]:
                        m.d.sync += target.eq(source)
                m.next = "NEURON_WRITE"

            with m.State("NEURON_WRITE"):
                m.d.comb += [working.eq(1), self.display_row_valid.eq(1)]
                for lane in range(16):
                    m.d.comb += [
                        state_reads[lane].addr.eq(batch_index),
                        event_reads[lane].addr.eq(batch_index),
                        state_writes[lane].addr.eq(batch_index),
                        state_writes[lane].data.eq(lane_next_states[lane]),
                        state_writes[lane].en.eq(1),
                    ]
                m.d.sync += next_spike_vector.word_select(
                    batch_index, 16
                ).eq(lane_spike_vector)
                m.d.sync += [
                    excitatory_accumulator.eq(
                        excitatory_accumulator + lane_excitatory_count
                    ),
                    inhibitory_accumulator.eq(
                        inhibitory_accumulator + lane_inhibitory_count
                    ),
                    readout_accumulator.eq(
                        readout_accumulator + lane_readout_sum
                    ),
                ]
                with m.If(batch_index == 15):
                    final_vector = Cat(
                        *[
                            next_spike_vector.word_select(batch, 16)
                            for batch in range(15)
                        ],
                        lane_spike_vector,
                    )
                    m.d.sync += [
                        self.spike_vector.eq(final_vector),
                    ]
                    m.next = "OUTPUT_PREPARE"
                with m.Else():
                    m.d.sync += batch_index.eq(batch_index + 1)
                    m.next = "NEURON_READ"

            with m.State("OUTPUT_PREPARE"):
                m.d.comb += working.eq(1)
                m.d.sync += [
                    self.spike_count.eq(
                        excitatory_accumulator + inhibitory_accumulator
                    ),
                    self.excitatory_spike_count.eq(excitatory_accumulator),
                    self.inhibitory_spike_count.eq(inhibitory_accumulator),
                    excitatory_difference_work.eq(excitatory_difference),
                    inhibitory_difference_work.eq(inhibitory_difference),
                    balance_work.eq(balance),
                    dc_candidate_work.eq(dc_candidate),
                    previous_readout.eq(readout_accumulator),
                ]
                m.next = "OUTPUT_COMMIT"

            with m.State("OUTPUT_COMMIT"):
                m.d.comb += working.eq(1)
                m.d.sync += [
                    self.sample_index.eq(self.sample_index + 1),
                    dc_state.eq(dc_next),
                    excitatory_lp.eq(excitatory_lp_next),
                    inhibitory_lp.eq(inhibitory_lp_next),
                    output_payload[0].as_value().eq(dc_output),
                    output_payload[1].as_value().eq(excitatory_lp_next),
                    output_payload[2].as_value().eq(inhibitory_lp_next),
                    output_payload[3].as_value().eq(Mux(
                        balance_work > 20_000,
                        20_000,
                        Mux(balance_work < -20_000, -20_000, balance_work),
                    )),
                    output_valid.eq(1),
                    self.deadline_cycles.eq(deadline_work + 1),
                ]
                with m.If(deadline_work + 1 >= 1250):
                    m.d.sync += self.fault.eq(1)
                m.next = "IDLE"

        return m
