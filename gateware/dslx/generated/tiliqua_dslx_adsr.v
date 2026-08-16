module tiliqua_dslx_adsr(
  input wire clk,
  input wire gate,
  input wire previous_gate,
  input wire [2:0] phase,
  input wire [15:0] level,
  input wire [15:0] attack,
  input wire [15:0] decay,
  input wire [15:0] sustain,
  input wire [15:0] release_amount,
  output wire [19:0] out
);
  // ===== Pipe stage 0:
  wire p0_not_415_comb;
  wire p0_not_416_comb;
  wire p0_falling_comb;
  wire [2:0] p0_PHASE_RELEASE_comb;
  wire p0_rising_comb;
  wire [2:0] p0_sel_421_comb;
  wire [2:0] p0_PHASE_ATTACK_comb;
  wire p0_PHASE_DECAY_squeezed__1_comb;
  wire [2:0] p0_active_phase_comb;
  wire [2:0] p0_PHASE_SUSTAIN_comb;
  wire [2:0] p0_PHASE_DECAY_comb;
  wire [16:0] p0_concat_430_comb;
  wire [16:0] p0_concat_431_comb;
  wire [15:0] p0_sub_433_comb;
  wire p0_eq_435_comb;
  wire p0_eq_436_comb;
  wire [16:0] p0_wide_comb;
  wire [16:0] p0_literal_438_comb;
  wire p0_ule_439_comb;
  wire p0_uge_440_comb;
  wire p0_ugt_441_comb;
  wire p0_eq_445_comb;
  wire p0_eq_443_comb;
  wire p0_or_442_comb;
  wire p0_ugt_444_comb;
  wire [15:0] p0_bit_slice_446_comb;
  wire [15:0] p0_literal_447_comb;
  wire p0_or_448_comb;
  wire [15:0] p0_sub_449_comb;
  wire [15:0] p0_sub_450_comb;
  wire [15:0] p0_sign_ext_451_comb;
  wire [3:0] p0_concat_456_comb;
  wire [2:0] p0_concat_454_comb;
  wire [1:0] p0_concat_452_comb;
  wire p0_PHASE_SUSTAIN_squeezed_comb;
  wire p0_not_455_comb;
  wire [15:0] p0_sel_457_comb;
  wire [15:0] p0_sel_458_comb;
  wire [15:0] p0_and_459_comb;
  wire [4:0] p0_one_hot_471_comb;
  wire [3:0] p0_one_hot_477_comb;
  wire [2:0] p0_one_hot_483_comb;
  wire p0_next_phase__2_to_3_comb;
  wire p0_next_phase__0_to_2__1_to_2_comb;
  wire p0_next_phase__0_to_2__0_to_1_comb;
  wire [15:0] p0_next_level_comb;
  wire [3:0] p0_bit_slice_472_comb;
  wire [2:0] p0_bit_slice_478_comb;
  wire [1:0] p0_bit_slice_484_comb;
  wire [19:0] p0_concat_467_comb;
  wire p0_eq_473_comb;
  wire p0_eq_479_comb;
  wire p0_eq_485_comb;
  assign p0_not_415_comb = ~previous_gate;
  assign p0_not_416_comb = ~gate;
  assign p0_falling_comb = ~(gate | p0_not_415_comb);
  assign p0_PHASE_RELEASE_comb = 3'h4;
  assign p0_rising_comb = ~(p0_not_416_comb | previous_gate);
  assign p0_sel_421_comb = p0_falling_comb ? p0_PHASE_RELEASE_comb : phase;
  assign p0_PHASE_ATTACK_comb = 3'h1;
  assign p0_PHASE_DECAY_squeezed__1_comb = 1'h0;
  assign p0_active_phase_comb = p0_rising_comb ? p0_PHASE_ATTACK_comb : p0_sel_421_comb;
  assign p0_PHASE_SUSTAIN_comb = 3'h3;
  assign p0_PHASE_DECAY_comb = 3'h2;
  assign p0_concat_430_comb = {p0_PHASE_DECAY_squeezed__1_comb, level};
  assign p0_concat_431_comb = {p0_PHASE_DECAY_squeezed__1_comb, attack};
  assign p0_sub_433_comb = level - sustain;
  assign p0_eq_435_comb = p0_active_phase_comb == p0_PHASE_SUSTAIN_comb;
  assign p0_eq_436_comb = p0_active_phase_comb == p0_PHASE_DECAY_comb;
  assign p0_wide_comb = p0_concat_430_comb + p0_concat_431_comb;
  assign p0_literal_438_comb = 17'h0_fffe;
  assign p0_ule_439_comb = level <= sustain;
  assign p0_uge_440_comb = decay >= p0_sub_433_comb;
  assign p0_ugt_441_comb = level > release_amount;
  assign p0_eq_445_comb = p0_active_phase_comb == p0_PHASE_RELEASE_comb;
  assign p0_eq_443_comb = p0_active_phase_comb == p0_PHASE_ATTACK_comb;
  assign p0_or_442_comb = p0_eq_435_comb | p0_eq_436_comb;
  assign p0_ugt_444_comb = p0_wide_comb > p0_literal_438_comb;
  assign p0_bit_slice_446_comb = p0_wide_comb[15:0];
  assign p0_literal_447_comb = 16'hffff;
  assign p0_or_448_comb = p0_ule_439_comb | p0_uge_440_comb;
  assign p0_sub_449_comb = level - decay;
  assign p0_sub_450_comb = level - release_amount;
  assign p0_sign_ext_451_comb = {16{p0_ugt_441_comb}};
  assign p0_concat_456_comb = {p0_eq_445_comb, p0_eq_435_comb, p0_eq_436_comb, p0_eq_443_comb};
  assign p0_concat_454_comb = {p0_eq_435_comb, p0_eq_436_comb, p0_eq_443_comb};
  assign p0_concat_452_comb = {p0_or_442_comb, p0_eq_443_comb};
  assign p0_PHASE_SUSTAIN_squeezed_comb = 1'h1;
  assign p0_not_455_comb = ~p0_ugt_444_comb;
  assign p0_sel_457_comb = p0_ugt_444_comb ? p0_literal_447_comb : p0_bit_slice_446_comb;
  assign p0_sel_458_comb = p0_or_448_comb ? sustain : p0_sub_449_comb;
  assign p0_and_459_comb = p0_sub_450_comb & p0_sign_ext_451_comb;
  assign p0_one_hot_471_comb = {p0_concat_456_comb[3:0] == 4'h0, p0_concat_456_comb[3] && p0_concat_456_comb[2:0] == 3'h0, p0_concat_456_comb[2] && p0_concat_456_comb[1:0] == 2'h0, p0_concat_456_comb[1] && !p0_concat_456_comb[0], p0_concat_456_comb[0]};
  assign p0_one_hot_477_comb = {p0_concat_454_comb[2:0] == 3'h0, p0_concat_454_comb[2] && p0_concat_454_comb[1:0] == 2'h0, p0_concat_454_comb[1] && !p0_concat_454_comb[0], p0_concat_454_comb[0]};
  assign p0_one_hot_483_comb = {p0_concat_452_comb[1:0] == 2'h0, p0_concat_452_comb[1] && !p0_concat_452_comb[0], p0_concat_452_comb[0]};
  assign p0_next_phase__2_to_3_comb = p0_ugt_441_comb & p0_eq_445_comb;
  assign p0_next_phase__0_to_2__1_to_2_comb = p0_ugt_444_comb & p0_concat_452_comb[0] | p0_PHASE_SUSTAIN_squeezed_comb & p0_concat_452_comb[1];
  assign p0_next_phase__0_to_2__0_to_1_comb = p0_not_455_comb & p0_concat_454_comb[0] | p0_or_448_comb & p0_concat_454_comb[1] | p0_PHASE_SUSTAIN_squeezed_comb & p0_concat_454_comb[2];
  assign p0_next_level_comb = p0_sel_457_comb & {16{p0_concat_456_comb[0]}} | p0_sel_458_comb & {16{p0_concat_456_comb[1]}} | sustain & {16{p0_concat_456_comb[2]}} | p0_and_459_comb & {16{p0_concat_456_comb[3]}};
  assign p0_bit_slice_472_comb = p0_one_hot_471_comb[3:0];
  assign p0_bit_slice_478_comb = p0_one_hot_477_comb[2:0];
  assign p0_bit_slice_484_comb = p0_one_hot_483_comb[1:0];
  assign p0_concat_467_comb = {gate, p0_next_phase__2_to_3_comb, p0_next_phase__0_to_2__1_to_2_comb, p0_next_phase__0_to_2__0_to_1_comb, p0_next_level_comb};
  assign p0_eq_473_comb = p0_concat_456_comb == p0_bit_slice_472_comb;
  assign p0_eq_479_comb = p0_concat_454_comb == p0_bit_slice_478_comb;
  assign p0_eq_485_comb = p0_concat_452_comb == p0_bit_slice_484_comb;
  assign out = p0_concat_467_comb;
endmodule
