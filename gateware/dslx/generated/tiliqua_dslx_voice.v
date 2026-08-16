module tiliqua_dslx_voice(
  input wire clk,
  input wire [15:0] saw,
  input wire [15:0] triangle,
  input wire [15:0] square,
  input wire [1:0] waveform,
  input wire [1:0] drive,
  output wire [15:0] out
);
  function automatic priority_sel_1b_2way (input reg [1:0] sel, input reg case0, input reg case1, input reg default_value);
    begin
      casez (sel)
        2'b?1: begin
          priority_sel_1b_2way = case0;
        end
        2'b10: begin
          priority_sel_1b_2way = case1;
        end
        2'b00: begin
          priority_sel_1b_2way = default_value;
        end
        default: begin
          // Propagate X
          priority_sel_1b_2way = 1'dx;
        end
      endcase
    end
  endfunction
  function automatic [13:0] priority_sel_14b_3way (input reg [2:0] sel, input reg [13:0] case0, input reg [13:0] case1, input reg [13:0] case2, input reg [13:0] default_value);
    begin
      casez (sel)
        3'b??1: begin
          priority_sel_14b_3way = case0;
        end
        3'b?10: begin
          priority_sel_14b_3way = case1;
        end
        3'b100: begin
          priority_sel_14b_3way = case2;
        end
        3'b000: begin
          priority_sel_14b_3way = default_value;
        end
        default: begin
          // Propagate X
          priority_sel_14b_3way = 14'dx;
        end
      endcase
    end
  endfunction

  // ===== Pipe stage 0:
  wire [1:0] p0_literal_312_comb;
  wire [1:0] p0_literal_313_comb;
  wire p0_bit_slice_314_comb;
  wire p0_bit_slice_315_comb;
  wire p0_eq_317_comb;
  wire p0_eq_318_comb;
  wire p0_nor_319_comb;
  wire p0_bit_slice_320_comb;
  wire p0_bit_slice_321_comb;
  wire [2:0] p0_concat_322_comb;
  wire p0_eq_327_comb;
  wire p0_nor_328_comb;
  wire [15:0] p0_one_hot_sel_380_comb;
  wire p0_eq_330_comb;
  wire p0_or_331_comb;
  wire [12:0] p0_bit_slice_332_comb;
  wire p0_literal_333_comb;
  wire p0_bit_slice_334_comb;
  wire p0_or_335_comb;
  wire p0_bit_slice_337_comb;
  wire p0_bit_slice_336_comb;
  wire [1:0] p0_concat_338_comb;
  wire p0_bit_slice_339_comb;
  wire [2:0] p0_concat_340_comb;
  wire [13:0] p0_bit_slice_341_comb;
  wire [13:0] p0_bit_slice_342_comb;
  wire [13:0] p0_bit_slice_343_comb;
  wire [13:0] p0_concat_344_comb;
  wire [1:0] p0_concat_345_comb;
  wire p0_bit_slice_346_comb;
  wire p0_not_347_comb;
  wire p0_sel_374_comb;
  wire p0_priority_sel_349_comb;
  wire [13:0] p0_priority_sel_350_comb;
  wire p0_one_hot_sel_373_comb;
  wire p0_nor_352_comb;
  wire [18:0] p0_concat_353_comb;
  wire [18:0] p0_S16_MIN_WIDE_comb;
  wire [18:0] p0_S16_MAX_WIDE_comb;
  wire p0_slt_356_comb;
  wire [15:0] p0_concat_357_comb;
  wire [15:0] p0_literal_358_comb;
  wire [2:0] p0_one_hot_368_comb;
  wire [3:0] p0_one_hot_375_comb;
  wire p0_sgt_359_comb;
  wire [15:0] p0_sel_360_comb;
  wire [15:0] p0_literal_361_comb;
  wire [1:0] p0_bit_slice_369_comb;
  wire [2:0] p0_bit_slice_376_comb;
  wire [15:0] p0_sel_364_comb;
  wire p0_eq_370_comb;
  wire p0_eq_377_comb;
  assign p0_literal_312_comb = 2'h2;
  assign p0_literal_313_comb = 2'h1;
  assign p0_bit_slice_314_comb = waveform[0];
  assign p0_bit_slice_315_comb = waveform[1];
  assign p0_eq_317_comb = waveform == p0_literal_312_comb;
  assign p0_eq_318_comb = waveform == p0_literal_313_comb;
  assign p0_nor_319_comb = ~(p0_bit_slice_314_comb | p0_bit_slice_315_comb);
  assign p0_bit_slice_320_comb = drive[0];
  assign p0_bit_slice_321_comb = drive[1];
  assign p0_concat_322_comb = {p0_eq_317_comb, p0_eq_318_comb, p0_nor_319_comb};
  assign p0_eq_327_comb = drive == p0_literal_313_comb;
  assign p0_nor_328_comb = ~(p0_bit_slice_320_comb | p0_bit_slice_321_comb);
  assign p0_one_hot_sel_380_comb = saw & {16{p0_concat_322_comb[0]}} | triangle & {16{p0_concat_322_comb[1]}} | square & {16{p0_concat_322_comb[2]}};
  assign p0_eq_330_comb = drive == p0_literal_312_comb;
  assign p0_or_331_comb = p0_eq_327_comb | p0_nor_328_comb;
  assign p0_bit_slice_332_comb = p0_one_hot_sel_380_comb[12:0];
  assign p0_literal_333_comb = 1'h0;
  assign p0_bit_slice_334_comb = p0_one_hot_sel_380_comb[0];
  assign p0_or_335_comb = p0_eq_330_comb | p0_or_331_comb;
  assign p0_bit_slice_337_comb = p0_one_hot_sel_380_comb[14];
  assign p0_bit_slice_336_comb = p0_one_hot_sel_380_comb[15];
  assign p0_concat_338_comb = {p0_eq_330_comb, p0_or_331_comb};
  assign p0_bit_slice_339_comb = p0_one_hot_sel_380_comb[13];
  assign p0_concat_340_comb = {p0_eq_330_comb, p0_eq_327_comb, p0_nor_328_comb};
  assign p0_bit_slice_341_comb = p0_one_hot_sel_380_comb[15:2];
  assign p0_bit_slice_342_comb = p0_one_hot_sel_380_comb[14:1];
  assign p0_bit_slice_343_comb = p0_one_hot_sel_380_comb[13:0];
  assign p0_concat_344_comb = {p0_bit_slice_332_comb, p0_literal_333_comb};
  assign p0_concat_345_comb = {p0_eq_327_comb, p0_nor_328_comb};
  assign p0_bit_slice_346_comb = p0_one_hot_sel_380_comb[1];
  assign p0_not_347_comb = ~p0_bit_slice_334_comb;
  assign p0_sel_374_comb = p0_or_335_comb ? p0_bit_slice_336_comb : p0_bit_slice_337_comb;
  assign p0_priority_sel_349_comb = priority_sel_1b_2way(p0_concat_338_comb, p0_bit_slice_336_comb, p0_bit_slice_337_comb, p0_bit_slice_339_comb);
  assign p0_priority_sel_350_comb = priority_sel_14b_3way(p0_concat_340_comb, p0_bit_slice_341_comb, p0_bit_slice_342_comb, p0_bit_slice_343_comb, p0_concat_344_comb);
  assign p0_one_hot_sel_373_comb = p0_bit_slice_346_comb & p0_concat_345_comb[0] | p0_bit_slice_334_comb & p0_concat_345_comb[1];
  assign p0_nor_352_comb = ~(p0_not_347_comb | p0_bit_slice_320_comb | p0_bit_slice_321_comb);
  assign p0_concat_353_comb = {p0_bit_slice_336_comb, p0_sel_374_comb, p0_priority_sel_349_comb, p0_priority_sel_350_comb, p0_one_hot_sel_373_comb, p0_nor_352_comb};
  assign p0_S16_MIN_WIDE_comb = 19'h7_8000;
  assign p0_S16_MAX_WIDE_comb = 19'h0_7fff;
  assign p0_slt_356_comb = $signed(p0_concat_353_comb) < $signed(p0_S16_MIN_WIDE_comb);
  assign p0_concat_357_comb = {p0_priority_sel_350_comb, p0_one_hot_sel_373_comb, p0_nor_352_comb};
  assign p0_literal_358_comb = 16'h8000;
  assign p0_one_hot_368_comb = {p0_concat_345_comb[1:0] == 2'h0, p0_concat_345_comb[1] && !p0_concat_345_comb[0], p0_concat_345_comb[0]};
  assign p0_one_hot_375_comb = {p0_concat_322_comb[2:0] == 3'h0, p0_concat_322_comb[2] && p0_concat_322_comb[1:0] == 2'h0, p0_concat_322_comb[1] && !p0_concat_322_comb[0], p0_concat_322_comb[0]};
  assign p0_sgt_359_comb = $signed(p0_concat_353_comb) > $signed(p0_S16_MAX_WIDE_comb);
  assign p0_sel_360_comb = p0_slt_356_comb ? p0_literal_358_comb : p0_concat_357_comb;
  assign p0_literal_361_comb = 16'h7fff;
  assign p0_bit_slice_369_comb = p0_one_hot_368_comb[1:0];
  assign p0_bit_slice_376_comb = p0_one_hot_375_comb[2:0];
  assign p0_sel_364_comb = p0_sgt_359_comb ? p0_literal_361_comb : p0_sel_360_comb;
  assign p0_eq_370_comb = p0_concat_345_comb == p0_bit_slice_369_comb;
  assign p0_eq_377_comb = p0_concat_322_comb == p0_bit_slice_376_comb;
  assign out = p0_sel_364_comb;
endmodule
