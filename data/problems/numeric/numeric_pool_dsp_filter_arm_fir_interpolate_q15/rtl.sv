module fir_interpolator_q15_l4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_sample,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_sample
);

    logic signed [15:0] state [0:7];
    logic signed [15:0] frame_out [0:3];
    logic [1:0]         out_idx;
    logic               busy;

    wire input_accepted;
    wire output_consumed;
    wire frame_complete;

    function automatic logic signed [15:0] coeff_at(input logic [1:0] phase, input logic [2:0] k);
        begin
            unique case ({phase, k})
                5'd0:  coeff_at = 16'sh0400;
                5'd1:  coeff_at = 16'sh0800;
                5'd2:  coeff_at = 16'sh0C00;
                5'd3:  coeff_at = 16'sh1000;
                5'd4:  coeff_at = 16'sh1400;
                5'd5:  coeff_at = 16'sh1800;
                5'd6:  coeff_at = 16'sh1C00;
                5'd7:  coeff_at = 16'sh2000;
                5'd8:  coeff_at = 16'sh0300;
                5'd9:  coeff_at = 16'sh0700;
                5'd10: coeff_at = 16'sh0B00;
                5'd11: coeff_at = 16'sh0F00;
                5'd12: coeff_at = 16'sh1300;
                5'd13: coeff_at = 16'sh1700;
                5'd14: coeff_at = 16'sh1B00;
                5'd15: coeff_at = 16'sh1F00;
                5'd16: coeff_at = 16'sh0200;
                5'd17: coeff_at = 16'sh0600;
                5'd18: coeff_at = 16'sh0A00;
                5'd19: coeff_at = 16'sh0E00;
                5'd20: coeff_at = 16'sh1200;
                5'd21: coeff_at = 16'sh1600;
                5'd22: coeff_at = 16'sh1A00;
                5'd23: coeff_at = 16'sh1E00;
                5'd24: coeff_at = 16'sh0100;
                5'd25: coeff_at = 16'sh0500;
                5'd26: coeff_at = 16'sh0900;
                5'd27: coeff_at = 16'sh0D00;
                5'd28: coeff_at = 16'sh1100;
                5'd29: coeff_at = 16'sh1500;
                5'd30: coeff_at = 16'sh1900;
                5'd31: coeff_at = 16'sh1D00;
            endcase
        end
    endfunction

    function automatic logic signed [15:0] sat16_from64_shift15(input logic signed [63:0] value);
        logic signed [63:0] shifted;
        begin
            shifted = value >>> 15;
            if (shifted > 64'sd32767)
                sat16_from64_shift15 = 16'sd32767;
            else if (shifted < -64'sd32768)
                sat16_from64_shift15 = -16'sd32768;
            else
                sat16_from64_shift15 = shifted[15:0];
        end
    endfunction

    logic signed [15:0] new_state0, new_state1, new_state2, new_state3;
    logic signed [15:0] new_state4, new_state5, new_state6, new_state7;

    logic signed [63:0] sum0, sum1, sum2, sum3;
    logic signed [31:0] prod00, prod01, prod02, prod03, prod04, prod05, prod06, prod07;
    logic signed [31:0] prod10, prod11, prod12, prod13, prod14, prod15, prod16, prod17;
    logic signed [31:0] prod20, prod21, prod22, prod23, prod24, prod25, prod26, prod27;
    logic signed [31:0] prod30, prod31, prod32, prod33, prod34, prod35, prod36, prod37;

    always_comb begin
        new_state0 = state[1];
        new_state1 = state[2];
        new_state2 = state[3];
        new_state3 = state[4];
        new_state4 = state[5];
        new_state5 = state[6];
        new_state6 = state[7];
        new_state7 = $signed(in_sample);

        prod00 = new_state0 * coeff_at(2'd0, 3'd0);
        prod01 = new_state1 * coeff_at(2'd0, 3'd1);
        prod02 = new_state2 * coeff_at(2'd0, 3'd2);
        prod03 = new_state3 * coeff_at(2'd0, 3'd3);
        prod04 = new_state4 * coeff_at(2'd0, 3'd4);
        prod05 = new_state5 * coeff_at(2'd0, 3'd5);
        prod06 = new_state6 * coeff_at(2'd0, 3'd6);
        prod07 = new_state7 * coeff_at(2'd0, 3'd7);

        prod10 = new_state0 * coeff_at(2'd1, 3'd0);
        prod11 = new_state1 * coeff_at(2'd1, 3'd1);
        prod12 = new_state2 * coeff_at(2'd1, 3'd2);
        prod13 = new_state3 * coeff_at(2'd1, 3'd3);
        prod14 = new_state4 * coeff_at(2'd1, 3'd4);
        prod15 = new_state5 * coeff_at(2'd1, 3'd5);
        prod16 = new_state6 * coeff_at(2'd1, 3'd6);
        prod17 = new_state7 * coeff_at(2'd1, 3'd7);

        prod20 = new_state0 * coeff_at(2'd2, 3'd0);
        prod21 = new_state1 * coeff_at(2'd2, 3'd1);
        prod22 = new_state2 * coeff_at(2'd2, 3'd2);
        prod23 = new_state3 * coeff_at(2'd2, 3'd3);
        prod24 = new_state4 * coeff_at(2'd2, 3'd4);
        prod25 = new_state5 * coeff_at(2'd2, 3'd5);
        prod26 = new_state6 * coeff_at(2'd2, 3'd6);
        prod27 = new_state7 * coeff_at(2'd2, 3'd7);

        prod30 = new_state0 * coeff_at(2'd3, 3'd0);
        prod31 = new_state1 * coeff_at(2'd3, 3'd1);
        prod32 = new_state2 * coeff_at(2'd3, 3'd2);
        prod33 = new_state3 * coeff_at(2'd3, 3'd3);
        prod34 = new_state4 * coeff_at(2'd3, 3'd4);
        prod35 = new_state5 * coeff_at(2'd3, 3'd5);
        prod36 = new_state6 * coeff_at(2'd3, 3'd6);
        prod37 = new_state7 * coeff_at(2'd3, 3'd7);

        sum0 = 64'sd0;
        sum0 = sum0 + {{32{prod00[31]}}, prod00};
        sum0 = sum0 + {{32{prod01[31]}}, prod01};
        sum0 = sum0 + {{32{prod02[31]}}, prod02};
        sum0 = sum0 + {{32{prod03[31]}}, prod03};
        sum0 = sum0 + {{32{prod04[31]}}, prod04};
        sum0 = sum0 + {{32{prod05[31]}}, prod05};
        sum0 = sum0 + {{32{prod06[31]}}, prod06};
        sum0 = sum0 + {{32{prod07[31]}}, prod07};

        sum1 = 64'sd0;
        sum1 = sum1 + {{32{prod10[31]}}, prod10};
        sum1 = sum1 + {{32{prod11[31]}}, prod11};
        sum1 = sum1 + {{32{prod12[31]}}, prod12};
        sum1 = sum1 + {{32{prod13[31]}}, prod13};
        sum1 = sum1 + {{32{prod14[31]}}, prod14};
        sum1 = sum1 + {{32{prod15[31]}}, prod15};
        sum1 = sum1 + {{32{prod16[31]}}, prod16};
        sum1 = sum1 + {{32{prod17[31]}}, prod17};

        sum2 = 64'sd0;
        sum2 = sum2 + {{32{prod20[31]}}, prod20};
        sum2 = sum2 + {{32{prod21[31]}}, prod21};
        sum2 = sum2 + {{32{prod22[31]}}, prod22};
        sum2 = sum2 + {{32{prod23[31]}}, prod23};
        sum2 = sum2 + {{32{prod24[31]}}, prod24};
        sum2 = sum2 + {{32{prod25[31]}}, prod25};
        sum2 = sum2 + {{32{prod26[31]}}, prod26};
        sum2 = sum2 + {{32{prod27[31]}}, prod27};

        sum3 = 64'sd0;
        sum3 = sum3 + {{32{prod30[31]}}, prod30};
        sum3 = sum3 + {{32{prod31[31]}}, prod31};
        sum3 = sum3 + {{32{prod32[31]}}, prod32};
        sum3 = sum3 + {{32{prod33[31]}}, prod33};
        sum3 = sum3 + {{32{prod34[31]}}, prod34};
        sum3 = sum3 + {{32{prod35[31]}}, prod35};
        sum3 = sum3 + {{32{prod36[31]}}, prod36};
        sum3 = sum3 + {{32{prod37[31]}}, prod37};
    end

    assign out_valid = busy;
    assign out_sample = frame_out[out_idx];
    assign output_consumed = out_valid && out_ready;
    assign frame_complete = output_consumed && (out_idx == 2'd3);
    assign in_ready = (~busy) || frame_complete;
    assign input_accepted = in_valid && in_ready;

    always_ff @(posedge clk) begin
        if (rst) begin
            state[0]    <= 16'sd0;
            state[1]    <= 16'sd0;
            state[2]    <= 16'sd0;
            state[3]    <= 16'sd0;
            state[4]    <= 16'sd0;
            state[5]    <= 16'sd0;
            state[6]    <= 16'sd0;
            state[7]    <= 16'sd0;
            frame_out[0] <= 16'sd0;
            frame_out[1] <= 16'sd0;
            frame_out[2] <= 16'sd0;
            frame_out[3] <= 16'sd0;
            out_idx     <= 2'd0;
            busy        <= 1'b0;
        end else if (input_accepted) begin
            state[0] <= new_state0;
            state[1] <= new_state1;
            state[2] <= new_state2;
            state[3] <= new_state3;
            state[4] <= new_state4;
            state[5] <= new_state5;
            state[6] <= new_state6;
            state[7] <= new_state7;

            frame_out[0] <= sat16_from64_shift15(sum0);
            frame_out[1] <= sat16_from64_shift15(sum1);
            frame_out[2] <= sat16_from64_shift15(sum2);
            frame_out[3] <= sat16_from64_shift15(sum3);

            out_idx <= 2'd0;
            busy    <= 1'b1;
        end else if (output_consumed) begin
            if (out_idx == 2'd3) begin
                out_idx <= 2'd0;
                busy    <= 1'b0;
            end else begin
                out_idx <= out_idx + 2'd1;
                busy    <= 1'b1;
            end
        end
    end

endmodule