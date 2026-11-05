module signal_processing_audio_codec_opus_silk_biquad_alt (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic  [15:0] in_sample0,
    input  logic  [15:0] in_sample1,
    input  logic  [31:0] in_B0_Q28,
    input  logic  [31:0] in_B1_Q28,
    input  logic  [31:0] in_B2_Q28,
    input  logic  [31:0] in_A0_Q28,
    input  logic  [31:0] in_A1_Q28,
    input  logic         in_clear_state,

    output logic         out_valid,
    input  logic         out_ready,
    output logic  [15:0] out_sample0,
    output logic  [15:0] out_sample1
);

logic busy;
logic signed [31:0] S0, S1, S2, S3;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic signed [31:0] wrap32(input logic signed [63:0] v);
    begin
        wrap32 = v[31:0];
    end
endfunction

function automatic logic signed [31:0] smulwb(
    input logic signed [31:0] a,
    input logic signed [31:0] b
);
    logic signed [15:0] b16;
    logic signed [47:0] prod;
    logic signed [47:0] shifted;
    begin
        b16 = b[15:0];
        prod = a * b16;
        shifted = prod >>> 16;
        smulwb = shifted[31:0];
    end
endfunction

function automatic logic signed [31:0] smlawb(
    input logic signed [31:0] a,
    input logic signed [31:0] b,
    input logic signed [31:0] c
);
    logic signed [15:0] c16;
    logic signed [47:0] prod;
    logic signed [47:0] shifted;
    logic signed [48:0] sum_ext;
    begin
        c16 = c[15:0];
        prod = b * c16;
        shifted = prod >>> 16;
        sum_ext = $signed({a[31], a}) + $signed({shifted[47], shifted});
        smlawb = sum_ext[31:0];
    end
endfunction

function automatic logic signed [31:0] rshift_round_14(input logic signed [31:0] x);
    begin
        rshift_round_14 = ((x >>> 13) + 32'sd1) >>> 1;
    end
endfunction

function automatic logic [15:0] sat16_u(input logic signed [31:0] v);
    begin
        if (v > 32'sd32767)
            sat16_u = 16'h7FFF;
        else if (v < -32'sd32768)
            sat16_u = 16'h8000;
        else
            sat16_u = v[15:0];
    end
endfunction

logic signed [31:0] sample0_s, sample1_s;
logic signed [31:0] B0_Q28_s, B1_Q28_s, B2_Q28_s, A0_Q28_s, A1_Q28_s;
logic signed [31:0] use_S0, use_S1, use_S2, use_S3;
logic signed [31:0] negA0, negA1;
logic signed [31:0] A0_L_Q28, A0_U_Q28, A1_L_Q28, A1_U_Q28;
logic signed [31:0] out_pre_ch0, out_pre_ch1;
logic signed [31:0] out32_Q14_ch0, out32_Q14_ch1;
logic signed [31:0] rs0, rs1, rs2, rs3;
logic signed [31:0] next_S0_a, next_S0_b, next_S0_c;
logic signed [31:0] next_S1_a, next_S1_b, next_S1_c;
logic signed [31:0] next_S2_a, next_S2_b, next_S2_c;
logic signed [31:0] next_S3_a, next_S3_b, next_S3_c;
logic signed [32:0] tmp0_add, tmp1_add;
logic signed [32:0] tmp0_shift, tmp1_shift;
logic signed [31:0] tmp0, tmp1;

always_comb begin
    sample0_s = $signed(in_sample0);
    sample1_s = $signed(in_sample1);
    B0_Q28_s  = $signed(in_B0_Q28);
    B1_Q28_s  = $signed(in_B1_Q28);
    B2_Q28_s  = $signed(in_B2_Q28);
    A0_Q28_s  = $signed(in_A0_Q28);
    A1_Q28_s  = $signed(in_A1_Q28);

    if (in_clear_state != 1'b0) begin
        use_S0 = 32'sd0;
        use_S1 = 32'sd0;
        use_S2 = 32'sd0;
        use_S3 = 32'sd0;
    end else begin
        use_S0 = S0;
        use_S1 = S1;
        use_S2 = S2;
        use_S3 = S3;
    end

    negA0 = wrap32(-$signed({A0_Q28_s[31], A0_Q28_s}));
    negA1 = wrap32(-$signed({A1_Q28_s[31], A1_Q28_s}));

    A0_L_Q28 = $signed({18'd0, negA0[13:0]});
    A0_U_Q28 = negA0 >>> 14;
    A1_L_Q28 = $signed({18'd0, negA1[13:0]});
    A1_U_Q28 = negA1 >>> 14;

    out_pre_ch0 = smlawb(use_S0, B0_Q28_s, sample0_s);
    out_pre_ch1 = smlawb(use_S2, B0_Q28_s, sample1_s);

    out32_Q14_ch0 = wrap32($signed({{30{out_pre_ch0[31]}}, out_pre_ch0, 2'b00}));
    out32_Q14_ch1 = wrap32($signed({{30{out_pre_ch1[31]}}, out_pre_ch1, 2'b00}));

    rs0 = rshift_round_14(smulwb(out32_Q14_ch0, A0_L_Q28));
    rs1 = rshift_round_14(smulwb(out32_Q14_ch0, A1_L_Q28));
    rs2 = rshift_round_14(smulwb(out32_Q14_ch1, A0_L_Q28));
    rs3 = rshift_round_14(smulwb(out32_Q14_ch1, A1_L_Q28));

    next_S0_a = wrap32($signed({use_S1[31], use_S1}) + $signed({rs0[31], rs0}));
    next_S0_b = smlawb(next_S0_a, out32_Q14_ch0, A0_U_Q28);
    next_S0_c = smlawb(next_S0_b, B1_Q28_s, sample0_s);

    next_S1_a = rs1;
    next_S1_b = smlawb(next_S1_a, out32_Q14_ch0, A1_U_Q28);
    next_S1_c = smlawb(next_S1_b, B2_Q28_s, sample0_s);

    next_S2_a = wrap32($signed({use_S3[31], use_S3}) + $signed({rs2[31], rs2}));
    next_S2_b = smlawb(next_S2_a, out32_Q14_ch1, A0_U_Q28);
    next_S2_c = smlawb(next_S2_b, B1_Q28_s, sample1_s);

    next_S3_a = rs3;
    next_S3_b = smlawb(next_S3_a, out32_Q14_ch1, A1_U_Q28);
    next_S3_c = smlawb(next_S3_b, B2_Q28_s, sample1_s);

    tmp0_add = $signed({out32_Q14_ch0[31], out32_Q14_ch0}) + 33'sd16383;
    tmp1_add = $signed({out32_Q14_ch1[31], out32_Q14_ch1}) + 33'sd16383;
    tmp0_shift = tmp0_add >>> 14;
    tmp1_shift = tmp1_add >>> 14;
    tmp0 = tmp0_shift[31:0];
    tmp1 = tmp1_shift[31:0];
end

always_ff @(posedge clk) begin
    if (rst) begin
        S0 <= 32'sd0;
        S1 <= 32'sd0;
        S2 <= 32'sd0;
        S3 <= 32'sd0;
        out_sample0 <= 16'd0;
        out_sample1 <= 16'd0;
        busy <= 1'b0;
    end else if (input_accepted) begin
        S0 <= next_S0_c;
        S1 <= next_S1_c;
        S2 <= next_S2_c;
        S3 <= next_S3_c;
        out_sample0 <= sat16_u(tmp0);
        out_sample1 <= sat16_u(tmp1);
        busy <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
