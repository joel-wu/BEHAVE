module sparse_fir_q15_block4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_x0,
    input  logic [15:0]  in_x1,
    input  logic [15:0]  in_x2,
    input  logic [15:0]  in_x3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_y0,
    output logic [15:0]  out_y1,
    output logic [15:0]  out_y2,
    output logic [15:0]  out_y3
);

logic busy;
logic signed [15:0] state [0:10];
logic [3:0] stateIndex;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic [3:0] mod11_add(
    input [3:0] a,
    input [3:0] b
);
    logic [4:0] sum;
    begin
        sum = a + b;
        if (sum >= 5'd11) begin
            mod11_add = sum - 5'd11;
        end else begin
            mod11_add = sum[3:0];
        end
    end
endfunction

function automatic [3:0] mod11_sub(
    input [3:0] a,
    input [3:0] b
);
    begin
        if (a >= b) begin
            mod11_sub = a - b;
        end else begin
            mod11_sub = a + 4'd11 - b;
        end
    end
endfunction

function automatic logic signed [15:0] sat16(
    input logic signed [31:0] value
);
    begin
        if (value > 32'sd32767) begin
            sat16 = 16'sd32767;
        end else if (value < -32'sd32768) begin
            sat16 = -16'sd32768;
        end else begin
            sat16 = value[15:0];
        end
    end
endfunction

logic [3:0] idx_w0, idx_w1, idx_w2, idx_w3;
logic [3:0] new_stateIndex;

logic signed [15:0] st0, st1, st2, st3, st4, st5, st6, st7, st8, st9, st10;

logic [3:0] rb0, rb1, rb2, rb3;

logic [3:0] i00, i01, i02, i03;
logic [3:0] i10, i11, i12, i13;
logic [3:0] i20, i21, i22, i23;
logic [3:0] i30, i31, i32, i33;

logic signed [15:0] s00, s01, s02, s03;
logic signed [15:0] s10, s11, s12, s13;
logic signed [15:0] s20, s21, s22, s23;
logic signed [15:0] s30, s31, s32, s33;

logic signed [31:0] p00, p01, p02, p03;
logic signed [31:0] p10, p11, p12, p13;
logic signed [31:0] p20, p21, p22, p23;
logic signed [31:0] p30, p31, p32, p33;

logic signed [31:0] a0_0, a0_1, a0_2, a0_3;
logic signed [31:0] a1_0, a1_1, a1_2, a1_3;
logic signed [31:0] a2_0, a2_1, a2_2, a2_3;
logic signed [31:0] acc0, acc1, acc2, acc3;

logic signed [31:0] tmp0, tmp1, tmp2, tmp3;
logic signed [15:0] y0_calc, y1_calc, y2_calc, y3_calc;

always_comb begin
    idx_w0 = stateIndex;
    idx_w1 = mod11_add(stateIndex, 4'd1);
    idx_w2 = mod11_add(stateIndex, 4'd2);
    idx_w3 = mod11_add(stateIndex, 4'd3);
    new_stateIndex = mod11_add(stateIndex, 4'd4);

    st0  = state[0];
    st1  = state[1];
    st2  = state[2];
    st3  = state[3];
    st4  = state[4];
    st5  = state[5];
    st6  = state[6];
    st7  = state[7];
    st8  = state[8];
    st9  = state[9];
    st10 = state[10];

    if (idx_w0 == 4'd0) st0 = $signed(in_x0);
    if (idx_w1 == 4'd0) st0 = $signed(in_x1);
    if (idx_w2 == 4'd0) st0 = $signed(in_x2);
    if (idx_w3 == 4'd0) st0 = $signed(in_x3);

    if (idx_w0 == 4'd1) st1 = $signed(in_x0);
    if (idx_w1 == 4'd1) st1 = $signed(in_x1);
    if (idx_w2 == 4'd1) st1 = $signed(in_x2);
    if (idx_w3 == 4'd1) st1 = $signed(in_x3);

    if (idx_w0 == 4'd2) st2 = $signed(in_x0);
    if (idx_w1 == 4'd2) st2 = $signed(in_x1);
    if (idx_w2 == 4'd2) st2 = $signed(in_x2);
    if (idx_w3 == 4'd2) st2 = $signed(in_x3);

    if (idx_w0 == 4'd3) st3 = $signed(in_x0);
    if (idx_w1 == 4'd3) st3 = $signed(in_x1);
    if (idx_w2 == 4'd3) st3 = $signed(in_x2);
    if (idx_w3 == 4'd3) st3 = $signed(in_x3);

    if (idx_w0 == 4'd4) st4 = $signed(in_x0);
    if (idx_w1 == 4'd4) st4 = $signed(in_x1);
    if (idx_w2 == 4'd4) st4 = $signed(in_x2);
    if (idx_w3 == 4'd4) st4 = $signed(in_x3);

    if (idx_w0 == 4'd5) st5 = $signed(in_x0);
    if (idx_w1 == 4'd5) st5 = $signed(in_x1);
    if (idx_w2 == 4'd5) st5 = $signed(in_x2);
    if (idx_w3 == 4'd5) st5 = $signed(in_x3);

    if (idx_w0 == 4'd6) st6 = $signed(in_x0);
    if (idx_w1 == 4'd6) st6 = $signed(in_x1);
    if (idx_w2 == 4'd6) st6 = $signed(in_x2);
    if (idx_w3 == 4'd6) st6 = $signed(in_x3);

    if (idx_w0 == 4'd7) st7 = $signed(in_x0);
    if (idx_w1 == 4'd7) st7 = $signed(in_x1);
    if (idx_w2 == 4'd7) st7 = $signed(in_x2);
    if (idx_w3 == 4'd7) st7 = $signed(in_x3);

    if (idx_w0 == 4'd8) st8 = $signed(in_x0);
    if (idx_w1 == 4'd8) st8 = $signed(in_x1);
    if (idx_w2 == 4'd8) st8 = $signed(in_x2);
    if (idx_w3 == 4'd8) st8 = $signed(in_x3);

    if (idx_w0 == 4'd9) st9 = $signed(in_x0);
    if (idx_w1 == 4'd9) st9 = $signed(in_x1);
    if (idx_w2 == 4'd9) st9 = $signed(in_x2);
    if (idx_w3 == 4'd9) st9 = $signed(in_x3);

    if (idx_w0 == 4'd10) st10 = $signed(in_x0);
    if (idx_w1 == 4'd10) st10 = $signed(in_x1);
    if (idx_w2 == 4'd10) st10 = $signed(in_x2);
    if (idx_w3 == 4'd10) st10 = $signed(in_x3);

    rb0 = mod11_sub(new_stateIndex, 4'd4);
    rb1 = mod11_sub(new_stateIndex, 4'd6);
    rb2 = mod11_sub(new_stateIndex, 4'd9);
    rb3 = mod11_sub(new_stateIndex, 4'd0);

    i00 = rb0;
    i01 = mod11_add(rb0, 4'd1);
    i02 = mod11_add(rb0, 4'd2);
    i03 = mod11_add(rb0, 4'd3);

    i10 = rb1;
    i11 = mod11_add(rb1, 4'd1);
    i12 = mod11_add(rb1, 4'd2);
    i13 = mod11_add(rb1, 4'd3);

    i20 = rb2;
    i21 = mod11_add(rb2, 4'd1);
    i22 = mod11_add(rb2, 4'd2);
    i23 = mod11_add(rb2, 4'd3);

    i30 = rb3;
    i31 = mod11_add(rb3, 4'd1);
    i32 = mod11_add(rb3, 4'd2);
    i33 = mod11_add(rb3, 4'd3);

    unique case (i00)
        4'd0: s00 = st0;   4'd1: s00 = st1;   4'd2: s00 = st2;   4'd3: s00 = st3;
        4'd4: s00 = st4;   4'd5: s00 = st5;   4'd6: s00 = st6;   4'd7: s00 = st7;
        4'd8: s00 = st8;   4'd9: s00 = st9;   4'd10: s00 = st10;
    endcase
    unique case (i01)
        4'd0: s01 = st0;   4'd1: s01 = st1;   4'd2: s01 = st2;   4'd3: s01 = st3;
        4'd4: s01 = st4;   4'd5: s01 = st5;   4'd6: s01 = st6;   4'd7: s01 = st7;
        4'd8: s01 = st8;   4'd9: s01 = st9;   4'd10: s01 = st10;
    endcase
    unique case (i02)
        4'd0: s02 = st0;   4'd1: s02 = st1;   4'd2: s02 = st2;   4'd3: s02 = st3;
        4'd4: s02 = st4;   4'd5: s02 = st5;   4'd6: s02 = st6;   4'd7: s02 = st7;
        4'd8: s02 = st8;   4'd9: s02 = st9;   4'd10: s02 = st10;
    endcase
    unique case (i03)
        4'd0: s03 = st0;   4'd1: s03 = st1;   4'd2: s03 = st2;   4'd3: s03 = st3;
        4'd4: s03 = st4;   4'd5: s03 = st5;   4'd6: s03 = st6;   4'd7: s03 = st7;
        4'd8: s03 = st8;   4'd9: s03 = st9;   4'd10: s03 = st10;
    endcase

    unique case (i10)
        4'd0: s10 = st0;   4'd1: s10 = st1;   4'd2: s10 = st2;   4'd3: s10 = st3;
        4'd4: s10 = st4;   4'd5: s10 = st5;   4'd6: s10 = st6;   4'd7: s10 = st7;
        4'd8: s10 = st8;   4'd9: s10 = st9;   4'd10: s10 = st10;
    endcase
    unique case (i11)
        4'd0: s11 = st0;   4'd1: s11 = st1;   4'd2: s11 = st2;   4'd3: s11 = st3;
        4'd4: s11 = st4;   4'd5: s11 = st5;   4'd6: s11 = st6;   4'd7: s11 = st7;
        4'd8: s11 = st8;   4'd9: s11 = st9;   4'd10: s11 = st10;
    endcase
    unique case (i12)
        4'd0: s12 = st0;   4'd1: s12 = st1;   4'd2: s12 = st2;   4'd3: s12 = st3;
        4'd4: s12 = st4;   4'd5: s12 = st5;   4'd6: s12 = st6;   4'd7: s12 = st7;
        4'd8: s12 = st8;   4'd9: s12 = st9;   4'd10: s12 = st10;
    endcase
    unique case (i13)
        4'd0: s13 = st0;   4'd1: s13 = st1;   4'd2: s13 = st2;   4'd3: s13 = st3;
        4'd4: s13 = st4;   4'd5: s13 = st5;   4'd6: s13 = st6;   4'd7: s13 = st7;
        4'd8: s13 = st8;   4'd9: s13 = st9;   4'd10: s13 = st10;
    endcase

    unique case (i20)
        4'd0: s20 = st0;   4'd1: s20 = st1;   4'd2: s20 = st2;   4'd3: s20 = st3;
        4'd4: s20 = st4;   4'd5: s20 = st5;   4'd6: s20 = st6;   4'd7: s20 = st7;
        4'd8: s20 = st8;   4'd9: s20 = st9;   4'd10: s20 = st10;
    endcase
    unique case (i21)
        4'd0: s21 = st0;   4'd1: s21 = st1;   4'd2: s21 = st2;   4'd3: s21 = st3;
        4'd4: s21 = st4;   4'd5: s21 = st5;   4'd6: s21 = st6;   4'd7: s21 = st7;
        4'd8: s21 = st8;   4'd9: s21 = st9;   4'd10: s21 = st10;
    endcase
    unique case (i22)
        4'd0: s22 = st0;   4'd1: s22 = st1;   4'd2: s22 = st2;   4'd3: s22 = st3;
        4'd4: s22 = st4;   4'd5: s22 = st5;   4'd6: s22 = st6;   4'd7: s22 = st7;
        4'd8: s22 = st8;   4'd9: s22 = st9;   4'd10: s22 = st10;
    endcase
    unique case (i23)
        4'd0: s23 = st0;   4'd1: s23 = st1;   4'd2: s23 = st2;   4'd3: s23 = st3;
        4'd4: s23 = st4;   4'd5: s23 = st5;   4'd6: s23 = st6;   4'd7: s23 = st7;
        4'd8: s23 = st8;   4'd9: s23 = st9;   4'd10: s23 = st10;
    endcase

    unique case (i30)
        4'd0: s30 = st0;   4'd1: s30 = st1;   4'd2: s30 = st2;   4'd3: s30 = st3;
        4'd4: s30 = st4;   4'd5: s30 = st5;   4'd6: s30 = st6;   4'd7: s30 = st7;
        4'd8: s30 = st8;   4'd9: s30 = st9;   4'd10: s30 = st10;
    endcase
    unique case (i31)
        4'd0: s31 = st0;   4'd1: s31 = st1;   4'd2: s31 = st2;   4'd3: s31 = st3;
        4'd4: s31 = st4;   4'd5: s31 = st5;   4'd6: s31 = st6;   4'd7: s31 = st7;
        4'd8: s31 = st8;   4'd9: s31 = st9;   4'd10: s31 = st10;
    endcase
    unique case (i32)
        4'd0: s32 = st0;   4'd1: s32 = st1;   4'd2: s32 = st2;   4'd3: s32 = st3;
        4'd4: s32 = st4;   4'd5: s32 = st5;   4'd6: s32 = st6;   4'd7: s32 = st7;
        4'd8: s32 = st8;   4'd9: s32 = st9;   4'd10: s32 = st10;
    endcase
    unique case (i33)
        4'd0: s33 = st0;   4'd1: s33 = st1;   4'd2: s33 = st2;   4'd3: s33 = st3;
        4'd4: s33 = st4;   4'd5: s33 = st5;   4'd6: s33 = st6;   4'd7: s33 = st7;
        4'd8: s33 = st8;   4'd9: s33 = st9;   4'd10: s33 = st10;
    endcase

    p00 = s00 * 16'sh2000;
    p01 = s01 * 16'sh2000;
    p02 = s02 * 16'sh2000;
    p03 = s03 * 16'sh2000;

    p10 = s10 * -16'sh2000;
    p11 = s11 * -16'sh2000;
    p12 = s12 * -16'sh2000;
    p13 = s13 * -16'sh2000;

    p20 = s20 * 16'sh1000;
    p21 = s21 * 16'sh1000;
    p22 = s22 * 16'sh1000;
    p23 = s23 * 16'sh1000;

    p30 = s30 * 16'sh0800;
    p31 = s31 * 16'sh0800;
    p32 = s32 * 16'sh0800;
    p33 = s33 * 16'sh0800;

    a0_0 = p00;
    a0_1 = p01;
    a0_2 = p02;
    a0_3 = p03;

    a1_0 = a0_0 + p10;
    a1_1 = a0_1 + p11;
    a1_2 = a0_2 + p12;
    a1_3 = a0_3 + p13;

    a2_0 = a1_0 + p20;
    a2_1 = a1_1 + p21;
    a2_2 = a1_2 + p22;
    a2_3 = a1_3 + p23;

    acc0 = a2_0 + p30;
    acc1 = a2_1 + p31;
    acc2 = a2_2 + p32;
    acc3 = a2_3 + p33;

    tmp0 = acc0 >>> 15;
    tmp1 = acc1 >>> 15;
    tmp2 = acc2 >>> 15;
    tmp3 = acc3 >>> 15;

    y0_calc = sat16(tmp0);
    y1_calc = sat16(tmp1);
    y2_calc = sat16(tmp2);
    y3_calc = sat16(tmp3);
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy <= 1'b0;
        stateIndex <= 4'd0;
        state[0] <= 16'sd0;
        state[1] <= 16'sd0;
        state[2] <= 16'sd0;
        state[3] <= 16'sd0;
        state[4] <= 16'sd0;
        state[5] <= 16'sd0;
        state[6] <= 16'sd0;
        state[7] <= 16'sd0;
        state[8] <= 16'sd0;
        state[9] <= 16'sd0;
        state[10] <= 16'sd0;
        out_y0 <= 16'h0000;
        out_y1 <= 16'h0000;
        out_y2 <= 16'h0000;
        out_y3 <= 16'h0000;
    end else if (input_accepted) begin
        busy <= 1'b1;
        state[idx_w0] <= $signed(in_x0);
        state[idx_w1] <= $signed(in_x1);
        state[idx_w2] <= $signed(in_x2);
        state[idx_w3] <= $signed(in_x3);
        stateIndex <= new_stateIndex;
        out_y0 <= y0_calc[15:0];
        out_y1 <= y1_calc[15:0];
        out_y2 <= y2_calc[15:0];
        out_y3 <= y3_calc[15:0];
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule