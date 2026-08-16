module tiliqua_dslx_visualizer(
  input wire clk,
  input wire [11:0] x,
  input wire [11:0] y,
  input wire [11:0] center_x,
  input wire [11:0] center_y,
  input wire [15:0] envelope,
  input wire gate,
  input wire [15:0] magnitude,
  input wire [7:0] frame,
  output wire [23:0] out
);
  // ===== Pipe stage 0:
  wire [1:0] p0_literal_450_comb;
  wire p0_lit_8_squeezed_comb;
  wire [2:0] p0_bit_slice_453_comb;
  wire [1:0] p0_bit_slice_454_comb;
  wire [9:0] p0_bit_slice_455_comb;
  wire [9:0] p0_concat_456_comb;
  wire [3:0] p0_concat_458_comb;
  wire [3:0] p0_literal_459_comb;
  wire [2:0] p0_concat_462_comb;
  wire [2:0] p0_literal_463_comb;
  wire [9:0] p0_add_464_comb;
  wire p0_ugt_465_comb;
  wire [11:0] p0_sub_466_comb;
  wire [11:0] p0_sub_467_comb;
  wire [4:0] p0_literal_468_comb;
  wire [3:0] p0_add_469_comb;
  wire [2:0] p0_bit_slice_470_comb;
  wire p0_ugt_471_comb;
  wire [11:0] p0_sub_472_comb;
  wire [11:0] p0_sub_473_comb;
  wire [3:0] p0_literal_474_comb;
  wire [2:0] p0_add_475_comb;
  wire [4:0] p0_bit_slice_476_comb;
  wire p0_bit_slice_477_comb;
  wire p0_bit_slice_478_comb;
  wire p0_bit_slice_479_comb;
  wire [11:0] p0_dx_comb;
  wire [11:0] p0_cross_width__1_comb;
  wire [11:0] p0_dy_comb;
  wire [11:0] p0_radius__1_comb;
  wire p0_checker_comb;
  wire [5:0] p0_lit_4_squeezed_comb;
  wire [5:0] p0_bit_slice_486_comb;
  wire [1:0] p0_concat_487_comb;
  wire [1:0] p0_literal_488_comb;
  wire p0_ult_489_comb;
  wire p0_ult_490_comb;
  wire [3:0] p0_literal_491_comb;
  wire p0_lit_24_squeezed_comb;
  wire p0_ult_493_comb;
  wire p0_ult_494_comb;
  wire [5:0] p0_sel_495_comb;
  wire [1:0] p0_add_496_comb;
  wire [2:0] p0_bit_slice_497_comb;
  wire [5:0] p0_bit_slice_498_comb;
  wire p0_inside_cross_comb;
  wire [6:0] p0_concat_500_comb;
  wire [6:0] p0_concat_501_comb;
  wire p0_inside_pulse_comb;
  wire [7:0] p0_concat_503_comb;
  wire [7:0] p0_envelope_u8_comb;
  wire [6:0] p0_concat_506_comb;
  wire [6:0] p0_concat_507_comb;
  wire [6:0] p0_blue_squeezed_comb;
  wire [7:0] p0_green_comb;
  wire [6:0] p0_red_squeezed_comb;
  wire [23:0] p0_concat_513_comb;
  assign p0_literal_450_comb = 2'h0;
  assign p0_lit_8_squeezed_comb = 1'h0;
  assign p0_bit_slice_453_comb = magnitude[15:13];
  assign p0_bit_slice_454_comb = envelope[15:14];
  assign p0_bit_slice_455_comb = x[11:2];
  assign p0_concat_456_comb = {p0_literal_450_comb, frame};
  assign p0_concat_458_comb = {p0_lit_8_squeezed_comb, p0_bit_slice_453_comb};
  assign p0_literal_459_comb = 4'h1;
  assign p0_concat_462_comb = {p0_lit_8_squeezed_comb, p0_bit_slice_454_comb};
  assign p0_literal_463_comb = 3'h1;
  assign p0_add_464_comb = p0_bit_slice_455_comb + p0_concat_456_comb;
  assign p0_ugt_465_comb = x > center_x;
  assign p0_sub_466_comb = center_x - x;
  assign p0_sub_467_comb = x - center_x;
  assign p0_literal_468_comb = 5'h00;
  assign p0_add_469_comb = p0_concat_458_comb + p0_literal_459_comb;
  assign p0_bit_slice_470_comb = magnitude[12:10];
  assign p0_ugt_471_comb = y > center_y;
  assign p0_sub_472_comb = center_y - y;
  assign p0_sub_473_comb = y - center_y;
  assign p0_literal_474_comb = 4'h0;
  assign p0_add_475_comb = p0_concat_462_comb + p0_literal_463_comb;
  assign p0_bit_slice_476_comb = envelope[13:9];
  assign p0_bit_slice_477_comb = p0_add_464_comb[3];
  assign p0_bit_slice_478_comb = y[5];
  assign p0_bit_slice_479_comb = envelope[14];
  assign p0_dx_comb = p0_ugt_465_comb ? p0_sub_467_comb : p0_sub_466_comb;
  assign p0_cross_width__1_comb = {p0_literal_468_comb, p0_add_469_comb, p0_bit_slice_470_comb};
  assign p0_dy_comb = p0_ugt_471_comb ? p0_sub_473_comb : p0_sub_472_comb;
  assign p0_radius__1_comb = {p0_literal_474_comb, p0_add_475_comb, p0_bit_slice_476_comb};
  assign p0_checker_comb = p0_bit_slice_477_comb ^ p0_bit_slice_478_comb;
  assign p0_lit_4_squeezed_comb = 6'h04;
  assign p0_bit_slice_486_comb = magnitude[14:9];
  assign p0_concat_487_comb = {p0_lit_8_squeezed_comb, p0_bit_slice_479_comb};
  assign p0_literal_488_comb = 2'h1;
  assign p0_ult_489_comb = p0_dx_comb < p0_cross_width__1_comb;
  assign p0_ult_490_comb = p0_dy_comb < p0_cross_width__1_comb;
  assign p0_literal_491_comb = 4'h8;
  assign p0_lit_24_squeezed_comb = 1'h1;
  assign p0_ult_493_comb = p0_dx_comb < p0_radius__1_comb;
  assign p0_ult_494_comb = p0_dy_comb < p0_radius__1_comb;
  assign p0_sel_495_comb = p0_checker_comb ? p0_bit_slice_486_comb : p0_lit_4_squeezed_comb;
  assign p0_add_496_comb = p0_concat_487_comb + p0_literal_488_comb;
  assign p0_bit_slice_497_comb = envelope[13:11];
  assign p0_bit_slice_498_comb = envelope[14:9];
  assign p0_inside_cross_comb = p0_ult_489_comb | p0_ult_490_comb;
  assign p0_concat_500_comb = {p0_literal_450_comb, p0_checker_comb, p0_literal_491_comb};
  assign p0_concat_501_comb = {p0_lit_24_squeezed_comb, p0_bit_slice_486_comb};
  assign p0_inside_pulse_comb = p0_ult_493_comb & p0_ult_494_comb;
  assign p0_concat_503_comb = {p0_literal_450_comb, p0_sel_495_comb};
  assign p0_envelope_u8_comb = envelope[14:7];
  assign p0_concat_506_comb = {p0_literal_450_comb, p0_add_496_comb, p0_bit_slice_497_comb};
  assign p0_concat_507_comb = {p0_lit_24_squeezed_comb, p0_bit_slice_498_comb};
  assign p0_blue_squeezed_comb = p0_inside_cross_comb ? p0_concat_501_comb : p0_concat_500_comb;
  assign p0_green_comb = p0_inside_pulse_comb ? p0_envelope_u8_comb : p0_concat_503_comb;
  assign p0_red_squeezed_comb = gate ? p0_concat_507_comb : p0_concat_506_comb;
  assign p0_concat_513_comb = {p0_lit_8_squeezed_comb, p0_blue_squeezed_comb, p0_green_comb, p0_lit_8_squeezed_comb, p0_red_squeezed_comb};
  assign out = p0_concat_513_comb;
endmodule
