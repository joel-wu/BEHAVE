module householder_reflector_apply10 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_side,
    input  logic [3:0]   in_m,
    input  logic [3:0]   in_n,
    input  logic [31:0]  in_tau,
    input  logic [31:0]  in_v0,
    input  logic [31:0]  in_v1,
    input  logic [31:0]  in_v2,
    input  logic [31:0]  in_v3,
    input  logic [31:0]  in_v4,
    input  logic [31:0]  in_v5,
    input  logic [31:0]  in_v6,
    input  logic [31:0]  in_v7,
    input  logic [31:0]  in_v8,
    input  logic [31:0]  in_v9,
    input  logic [31:0]  in_c00,
    input  logic [31:0]  in_c01,
    input  logic [31:0]  in_c02,
    input  logic [31:0]  in_c03,
    input  logic [31:0]  in_c04,
    input  logic [31:0]  in_c05,
    input  logic [31:0]  in_c06,
    input  logic [31:0]  in_c07,
    input  logic [31:0]  in_c08,
    input  logic [31:0]  in_c09,
    input  logic [31:0]  in_c10,
    input  logic [31:0]  in_c11,
    input  logic [31:0]  in_c12,
    input  logic [31:0]  in_c13,
    input  logic [31:0]  in_c14,
    input  logic [31:0]  in_c15,
    input  logic [31:0]  in_c16,
    input  logic [31:0]  in_c17,
    input  logic [31:0]  in_c18,
    input  logic [31:0]  in_c19,
    input  logic [31:0]  in_c20,
    input  logic [31:0]  in_c21,
    input  logic [31:0]  in_c22,
    input  logic [31:0]  in_c23,
    input  logic [31:0]  in_c24,
    input  logic [31:0]  in_c25,
    input  logic [31:0]  in_c26,
    input  logic [31:0]  in_c27,
    input  logic [31:0]  in_c28,
    input  logic [31:0]  in_c29,
    input  logic [31:0]  in_c30,
    input  logic [31:0]  in_c31,
    input  logic [31:0]  in_c32,
    input  logic [31:0]  in_c33,
    input  logic [31:0]  in_c34,
    input  logic [31:0]  in_c35,
    input  logic [31:0]  in_c36,
    input  logic [31:0]  in_c37,
    input  logic [31:0]  in_c38,
    input  logic [31:0]  in_c39,
    input  logic [31:0]  in_c40,
    input  logic [31:0]  in_c41,
    input  logic [31:0]  in_c42,
    input  logic [31:0]  in_c43,
    input  logic [31:0]  in_c44,
    input  logic [31:0]  in_c45,
    input  logic [31:0]  in_c46,
    input  logic [31:0]  in_c47,
    input  logic [31:0]  in_c48,
    input  logic [31:0]  in_c49,
    input  logic [31:0]  in_c50,
    input  logic [31:0]  in_c51,
    input  logic [31:0]  in_c52,
    input  logic [31:0]  in_c53,
    input  logic [31:0]  in_c54,
    input  logic [31:0]  in_c55,
    input  logic [31:0]  in_c56,
    input  logic [31:0]  in_c57,
    input  logic [31:0]  in_c58,
    input  logic [31:0]  in_c59,
    input  logic [31:0]  in_c60,
    input  logic [31:0]  in_c61,
    input  logic [31:0]  in_c62,
    input  logic [31:0]  in_c63,
    input  logic [31:0]  in_c64,
    input  logic [31:0]  in_c65,
    input  logic [31:0]  in_c66,
    input  logic [31:0]  in_c67,
    input  logic [31:0]  in_c68,
    input  logic [31:0]  in_c69,
    input  logic [31:0]  in_c70,
    input  logic [31:0]  in_c71,
    input  logic [31:0]  in_c72,
    input  logic [31:0]  in_c73,
    input  logic [31:0]  in_c74,
    input  logic [31:0]  in_c75,
    input  logic [31:0]  in_c76,
    input  logic [31:0]  in_c77,
    input  logic [31:0]  in_c78,
    input  logic [31:0]  in_c79,
    input  logic [31:0]  in_c80,
    input  logic [31:0]  in_c81,
    input  logic [31:0]  in_c82,
    input  logic [31:0]  in_c83,
    input  logic [31:0]  in_c84,
    input  logic [31:0]  in_c85,
    input  logic [31:0]  in_c86,
    input  logic [31:0]  in_c87,
    input  logic [31:0]  in_c88,
    input  logic [31:0]  in_c89,
    input  logic [31:0]  in_c90,
    input  logic [31:0]  in_c91,
    input  logic [31:0]  in_c92,
    input  logic [31:0]  in_c93,
    input  logic [31:0]  in_c94,
    input  logic [31:0]  in_c95,
    input  logic [31:0]  in_c96,
    input  logic [31:0]  in_c97,
    input  logic [31:0]  in_c98,
    input  logic [31:0]  in_c99,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_c00,
    output logic [31:0]  out_c01,
    output logic [31:0]  out_c02,
    output logic [31:0]  out_c03,
    output logic [31:0]  out_c04,
    output logic [31:0]  out_c05,
    output logic [31:0]  out_c06,
    output logic [31:0]  out_c07,
    output logic [31:0]  out_c08,
    output logic [31:0]  out_c09,
    output logic [31:0]  out_c10,
    output logic [31:0]  out_c11,
    output logic [31:0]  out_c12,
    output logic [31:0]  out_c13,
    output logic [31:0]  out_c14,
    output logic [31:0]  out_c15,
    output logic [31:0]  out_c16,
    output logic [31:0]  out_c17,
    output logic [31:0]  out_c18,
    output logic [31:0]  out_c19,
    output logic [31:0]  out_c20,
    output logic [31:0]  out_c21,
    output logic [31:0]  out_c22,
    output logic [31:0]  out_c23,
    output logic [31:0]  out_c24,
    output logic [31:0]  out_c25,
    output logic [31:0]  out_c26,
    output logic [31:0]  out_c27,
    output logic [31:0]  out_c28,
    output logic [31:0]  out_c29,
    output logic [31:0]  out_c30,
    output logic [31:0]  out_c31,
    output logic [31:0]  out_c32,
    output logic [31:0]  out_c33,
    output logic [31:0]  out_c34,
    output logic [31:0]  out_c35,
    output logic [31:0]  out_c36,
    output logic [31:0]  out_c37,
    output logic [31:0]  out_c38,
    output logic [31:0]  out_c39,
    output logic [31:0]  out_c40,
    output logic [31:0]  out_c41,
    output logic [31:0]  out_c42,
    output logic [31:0]  out_c43,
    output logic [31:0]  out_c44,
    output logic [31:0]  out_c45,
    output logic [31:0]  out_c46,
    output logic [31:0]  out_c47,
    output logic [31:0]  out_c48,
    output logic [31:0]  out_c49,
    output logic [31:0]  out_c50,
    output logic [31:0]  out_c51,
    output logic [31:0]  out_c52,
    output logic [31:0]  out_c53,
    output logic [31:0]  out_c54,
    output logic [31:0]  out_c55,
    output logic [31:0]  out_c56,
    output logic [31:0]  out_c57,
    output logic [31:0]  out_c58,
    output logic [31:0]  out_c59,
    output logic [31:0]  out_c60,
    output logic [31:0]  out_c61,
    output logic [31:0]  out_c62,
    output logic [31:0]  out_c63,
    output logic [31:0]  out_c64,
    output logic [31:0]  out_c65,
    output logic [31:0]  out_c66,
    output logic [31:0]  out_c67,
    output logic [31:0]  out_c68,
    output logic [31:0]  out_c69,
    output logic [31:0]  out_c70,
    output logic [31:0]  out_c71,
    output logic [31:0]  out_c72,
    output logic [31:0]  out_c73,
    output logic [31:0]  out_c74,
    output logic [31:0]  out_c75,
    output logic [31:0]  out_c76,
    output logic [31:0]  out_c77,
    output logic [31:0]  out_c78,
    output logic [31:0]  out_c79,
    output logic [31:0]  out_c80,
    output logic [31:0]  out_c81,
    output logic [31:0]  out_c82,
    output logic [31:0]  out_c83,
    output logic [31:0]  out_c84,
    output logic [31:0]  out_c85,
    output logic [31:0]  out_c86,
    output logic [31:0]  out_c87,
    output logic [31:0]  out_c88,
    output logic [31:0]  out_c89,
    output logic [31:0]  out_c90,
    output logic [31:0]  out_c91,
    output logic [31:0]  out_c92,
    output logic [31:0]  out_c93,
    output logic [31:0]  out_c94,
    output logic [31:0]  out_c95,
    output logic [31:0]  out_c96,
    output logic [31:0]  out_c97,
    output logic [31:0]  out_c98,
    output logic [31:0]  out_c99
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;
assign in_ready        = ~busy | output_consumed;
assign out_valid       = busy;

function automatic logic signed [31:0] wrap_mul32(
    input logic signed [31:0] a,
    input logic signed [31:0] b
);
    logic signed [63:0] prod;
    begin
        prod = a * b;
        wrap_mul32 = prod[31:0];
    end
endfunction

function automatic logic signed [31:0] wrap_add32(
    input logic signed [31:0] a,
    input logic signed [31:0] b
);
    logic signed [32:0] sum;
    begin
        sum = a + b;
        wrap_add32 = sum[31:0];
    end
endfunction

function automatic logic signed [31:0] wrap_sub32(
    input logic signed [31:0] a,
    input logic signed [31:0] b
);
    logic signed [32:0] diff;
    begin
        diff = a - b;
        wrap_sub32 = diff[31:0];
    end
endfunction

logic signed [31:0] tau_s;
logic signed [31:0] v0_s, v1_s, v2_s, v3_s, v4_s, v5_s, v6_s, v7_s, v8_s, v9_s;
logic signed [31:0] c00_s, c01_s, c02_s, c03_s, c04_s, c05_s, c06_s, c07_s, c08_s, c09_s;
logic signed [31:0] c10_s, c11_s, c12_s, c13_s, c14_s, c15_s, c16_s, c17_s, c18_s, c19_s;
logic signed [31:0] c20_s, c21_s, c22_s, c23_s, c24_s, c25_s, c26_s, c27_s, c28_s, c29_s;
logic signed [31:0] c30_s, c31_s, c32_s, c33_s, c34_s, c35_s, c36_s, c37_s, c38_s, c39_s;
logic signed [31:0] c40_s, c41_s, c42_s, c43_s, c44_s, c45_s, c46_s, c47_s, c48_s, c49_s;
logic signed [31:0] c50_s, c51_s, c52_s, c53_s, c54_s, c55_s, c56_s, c57_s, c58_s, c59_s;
logic signed [31:0] c60_s, c61_s, c62_s, c63_s, c64_s, c65_s, c66_s, c67_s, c68_s, c69_s;
logic signed [31:0] c70_s, c71_s, c72_s, c73_s, c74_s, c75_s, c76_s, c77_s, c78_s, c79_s;
logic signed [31:0] c80_s, c81_s, c82_s, c83_s, c84_s, c85_s, c86_s, c87_s, c88_s, c89_s;
logic signed [31:0] c90_s, c91_s, c92_s, c93_s, c94_s, c95_s, c96_s, c97_s, c98_s, c99_s;

logic signed [31:0] t0, t1, t2, t3, t4, t5, t6, t7, t8, t9;
logic signed [31:0] s0, s1, s2, s3, s4, s5, s6, s7, s8, s9;

always_comb begin
    tau_s = in_tau;
    v0_s = in_v0; v1_s = in_v1; v2_s = in_v2; v3_s = in_v3; v4_s = in_v4;
    v5_s = in_v5; v6_s = in_v6; v7_s = in_v7; v8_s = in_v8; v9_s = in_v9;

    c00_s = in_c00; c01_s = in_c01; c02_s = in_c02; c03_s = in_c03; c04_s = in_c04; c05_s = in_c05; c06_s = in_c06; c07_s = in_c07; c08_s = in_c08; c09_s = in_c09;
    c10_s = in_c10; c11_s = in_c11; c12_s = in_c12; c13_s = in_c13; c14_s = in_c14; c15_s = in_c15; c16_s = in_c16; c17_s = in_c17; c18_s = in_c18; c19_s = in_c19;
    c20_s = in_c20; c21_s = in_c21; c22_s = in_c22; c23_s = in_c23; c24_s = in_c24; c25_s = in_c25; c26_s = in_c26; c27_s = in_c27; c28_s = in_c28; c29_s = in_c29;
    c30_s = in_c30; c31_s = in_c31; c32_s = in_c32; c33_s = in_c33; c34_s = in_c34; c35_s = in_c35; c36_s = in_c36; c37_s = in_c37; c38_s = in_c38; c39_s = in_c39;
    c40_s = in_c40; c41_s = in_c41; c42_s = in_c42; c43_s = in_c43; c44_s = in_c44; c45_s = in_c45; c46_s = in_c46; c47_s = in_c47; c48_s = in_c48; c49_s = in_c49;
    c50_s = in_c50; c51_s = in_c51; c52_s = in_c52; c53_s = in_c53; c54_s = in_c54; c55_s = in_c55; c56_s = in_c56; c57_s = in_c57; c58_s = in_c58; c59_s = in_c59;
    c60_s = in_c60; c61_s = in_c61; c62_s = in_c62; c63_s = in_c63; c64_s = in_c64; c65_s = in_c65; c66_s = in_c66; c67_s = in_c67; c68_s = in_c68; c69_s = in_c69;
    c70_s = in_c70; c71_s = in_c71; c72_s = in_c72; c73_s = in_c73; c74_s = in_c74; c75_s = in_c75; c76_s = in_c76; c77_s = in_c77; c78_s = in_c78; c79_s = in_c79;
    c80_s = in_c80; c81_s = in_c81; c82_s = in_c82; c83_s = in_c83; c84_s = in_c84; c85_s = in_c85; c86_s = in_c86; c87_s = in_c87; c88_s = in_c88; c89_s = in_c89;
    c90_s = in_c90; c91_s = in_c91; c92_s = in_c92; c93_s = in_c93; c94_s = in_c94; c95_s = in_c95; c96_s = in_c96; c97_s = in_c97; c98_s = in_c98; c99_s = in_c99;

    t0 = wrap_mul32(tau_s, v0_s);
    t1 = wrap_mul32(tau_s, v1_s);
    t2 = wrap_mul32(tau_s, v2_s);
    t3 = wrap_mul32(tau_s, v3_s);
    t4 = wrap_mul32(tau_s, v4_s);
    t5 = wrap_mul32(tau_s, v5_s);
    t6 = wrap_mul32(tau_s, v6_s);
    t7 = wrap_mul32(tau_s, v7_s);
    t8 = wrap_mul32(tau_s, v8_s);
    t9 = wrap_mul32(tau_s, v9_s);

    if (in_side == 1'b0) begin
        s0 = wrap_mul32(v0_s, c00_s);
        if (in_m >= 4'd2) s0 = wrap_add32(s0, wrap_mul32(v1_s, c10_s));
        if (in_m >= 4'd3) s0 = wrap_add32(s0, wrap_mul32(v2_s, c20_s));
        if (in_m >= 4'd4) s0 = wrap_add32(s0, wrap_mul32(v3_s, c30_s));
        if (in_m >= 4'd5) s0 = wrap_add32(s0, wrap_mul32(v4_s, c40_s));
        if (in_m >= 4'd6) s0 = wrap_add32(s0, wrap_mul32(v5_s, c50_s));
        if (in_m >= 4'd7) s0 = wrap_add32(s0, wrap_mul32(v6_s, c60_s));
        if (in_m >= 4'd8) s0 = wrap_add32(s0, wrap_mul32(v7_s, c70_s));
        if (in_m >= 4'd9) s0 = wrap_add32(s0, wrap_mul32(v8_s, c80_s));
        if (in_m >= 4'd10) s0 = wrap_add32(s0, wrap_mul32(v9_s, c90_s));

        s1 = wrap_mul32(v0_s, c01_s);
        if (in_m >= 4'd2) s1 = wrap_add32(s1, wrap_mul32(v1_s, c11_s));
        if (in_m >= 4'd3) s1 = wrap_add32(s1, wrap_mul32(v2_s, c21_s));
        if (in_m >= 4'd4) s1 = wrap_add32(s1, wrap_mul32(v3_s, c31_s));
        if (in_m >= 4'd5) s1 = wrap_add32(s1, wrap_mul32(v4_s, c41_s));
        if (in_m >= 4'd6) s1 = wrap_add32(s1, wrap_mul32(v5_s, c51_s));
        if (in_m >= 4'd7) s1 = wrap_add32(s1, wrap_mul32(v6_s, c61_s));
        if (in_m >= 4'd8) s1 = wrap_add32(s1, wrap_mul32(v7_s, c71_s));
        if (in_m >= 4'd9) s1 = wrap_add32(s1, wrap_mul32(v8_s, c81_s));
        if (in_m >= 4'd10) s1 = wrap_add32(s1, wrap_mul32(v9_s, c91_s));

        s2 = wrap_mul32(v0_s, c02_s);
        if (in_m >= 4'd2) s2 = wrap_add32(s2, wrap_mul32(v1_s, c12_s));
        if (in_m >= 4'd3) s2 = wrap_add32(s2, wrap_mul32(v2_s, c22_s));
        if (in_m >= 4'd4) s2 = wrap_add32(s2, wrap_mul32(v3_s, c32_s));
        if (in_m >= 4'd5) s2 = wrap_add32(s2, wrap_mul32(v4_s, c42_s));
        if (in_m >= 4'd6) s2 = wrap_add32(s2, wrap_mul32(v5_s, c52_s));
        if (in_m >= 4'd7) s2 = wrap_add32(s2, wrap_mul32(v6_s, c62_s));
        if (in_m >= 4'd8) s2 = wrap_add32(s2, wrap_mul32(v7_s, c72_s));
        if (in_m >= 4'd9) s2 = wrap_add32(s2, wrap_mul32(v8_s, c82_s));
        if (in_m >= 4'd10) s2 = wrap_add32(s2, wrap_mul32(v9_s, c92_s));

        s3 = wrap_mul32(v0_s, c03_s);
        if (in_m >= 4'd2) s3 = wrap_add32(s3, wrap_mul32(v1_s, c13_s));
        if (in_m >= 4'd3) s3 = wrap_add32(s3, wrap_mul32(v2_s, c23_s));
        if (in_m >= 4'd4) s3 = wrap_add32(s3, wrap_mul32(v3_s, c33_s));
        if (in_m >= 4'd5) s3 = wrap_add32(s3, wrap_mul32(v4_s, c43_s));
        if (in_m >= 4'd6) s3 = wrap_add32(s3, wrap_mul32(v5_s, c53_s));
        if (in_m >= 4'd7) s3 = wrap_add32(s3, wrap_mul32(v6_s, c63_s));
        if (in_m >= 4'd8) s3 = wrap_add32(s3, wrap_mul32(v7_s, c73_s));
        if (in_m >= 4'd9) s3 = wrap_add32(s3, wrap_mul32(v8_s, c83_s));
        if (in_m >= 4'd10) s3 = wrap_add32(s3, wrap_mul32(v9_s, c93_s));

        s4 = wrap_mul32(v0_s, c04_s);
        if (in_m >= 4'd2) s4 = wrap_add32(s4, wrap_mul32(v1_s, c14_s));
        if (in_m >= 4'd3) s4 = wrap_add32(s4, wrap_mul32(v2_s, c24_s));
        if (in_m >= 4'd4) s4 = wrap_add32(s4, wrap_mul32(v3_s, c34_s));
        if (in_m >= 4'd5) s4 = wrap_add32(s4, wrap_mul32(v4_s, c44_s));
        if (in_m >= 4'd6) s4 = wrap_add32(s4, wrap_mul32(v5_s, c54_s));
        if (in_m >= 4'd7) s4 = wrap_add32(s4, wrap_mul32(v6_s, c64_s));
        if (in_m >= 4'd8) s4 = wrap_add32(s4, wrap_mul32(v7_s, c74_s));
        if (in_m >= 4'd9) s4 = wrap_add32(s4, wrap_mul32(v8_s, c84_s));
        if (in_m >= 4'd10) s4 = wrap_add32(s4, wrap_mul32(v9_s, c94_s));

        s5 = wrap_mul32(v0_s, c05_s);
        if (in_m >= 4'd2) s5 = wrap_add32(s5, wrap_mul32(v1_s, c15_s));
        if (in_m >= 4'd3) s5 = wrap_add32(s5, wrap_mul32(v2_s, c25_s));
        if (in_m >= 4'd4) s5 = wrap_add32(s5, wrap_mul32(v3_s, c35_s));
        if (in_m >= 4'd5) s5 = wrap_add32(s5, wrap_mul32(v4_s, c45_s));
        if (in_m >= 4'd6) s5 = wrap_add32(s5, wrap_mul32(v5_s, c55_s));
        if (in_m >= 4'd7) s5 = wrap_add32(s5, wrap_mul32(v6_s, c65_s));
        if (in_m >= 4'd8) s5 = wrap_add32(s5, wrap_mul32(v7_s, c75_s));
        if (in_m >= 4'd9) s5 = wrap_add32(s5, wrap_mul32(v8_s, c85_s));
        if (in_m >= 4'd10) s5 = wrap_add32(s5, wrap_mul32(v9_s, c95_s));

        s6 = wrap_mul32(v0_s, c06_s);
        if (in_m >= 4'd2) s6 = wrap_add32(s6, wrap_mul32(v1_s, c16_s));
        if (in_m >= 4'd3) s6 = wrap_add32(s6, wrap_mul32(v2_s, c26_s));
        if (in_m >= 4'd4) s6 = wrap_add32(s6, wrap_mul32(v3_s, c36_s));
        if (in_m >= 4'd5) s6 = wrap_add32(s6, wrap_mul32(v4_s, c46_s));
        if (in_m >= 4'd6) s6 = wrap_add32(s6, wrap_mul32(v5_s, c56_s));
        if (in_m >= 4'd7) s6 = wrap_add32(s6, wrap_mul32(v6_s, c66_s));
        if (in_m >= 4'd8) s6 = wrap_add32(s6, wrap_mul32(v7_s, c76_s));
        if (in_m >= 4'd9) s6 = wrap_add32(s6, wrap_mul32(v8_s, c86_s));
        if (in_m >= 4'd10) s6 = wrap_add32(s6, wrap_mul32(v9_s, c96_s));

        s7 = wrap_mul32(v0_s, c07_s);
        if (in_m >= 4'd2) s7 = wrap_add32(s7, wrap_mul32(v1_s, c17_s));
        if (in_m >= 4'd3) s7 = wrap_add32(s7, wrap_mul32(v2_s, c27_s));
        if (in_m >= 4'd4) s7 = wrap_add32(s7, wrap_mul32(v3_s, c37_s));
        if (in_m >= 4'd5) s7 = wrap_add32(s7, wrap_mul32(v4_s, c47_s));
        if (in_m >= 4'd6) s7 = wrap_add32(s7, wrap_mul32(v5_s, c57_s));
        if (in_m >= 4'd7) s7 = wrap_add32(s7, wrap_mul32(v6_s, c67_s));
        if (in_m >= 4'd8) s7 = wrap_add32(s7, wrap_mul32(v7_s, c77_s));
        if (in_m >= 4'd9) s7 = wrap_add32(s7, wrap_mul32(v8_s, c87_s));
        if (in_m >= 4'd10) s7 = wrap_add32(s7, wrap_mul32(v9_s, c97_s));

        s8 = wrap_mul32(v0_s, c08_s);
        if (in_m >= 4'd2) s8 = wrap_add32(s8, wrap_mul32(v1_s, c18_s));
        if (in_m >= 4'd3) s8 = wrap_add32(s8, wrap_mul32(v2_s, c28_s));
        if (in_m >= 4'd4) s8 = wrap_add32(s8, wrap_mul32(v3_s, c38_s));
        if (in_m >= 4'd5) s8 = wrap_add32(s8, wrap_mul32(v4_s, c48_s));
        if (in_m >= 4'd6) s8 = wrap_add32(s8, wrap_mul32(v5_s, c58_s));
        if (in_m >= 4'd7) s8 = wrap_add32(s8, wrap_mul32(v6_s, c68_s));
        if (in_m >= 4'd8) s8 = wrap_add32(s8, wrap_mul32(v7_s, c78_s));
        if (in_m >= 4'd9) s8 = wrap_add32(s8, wrap_mul32(v8_s, c88_s));
        if (in_m >= 4'd10) s8 = wrap_add32(s8, wrap_mul32(v9_s, c98_s));

        s9 = wrap_mul32(v0_s, c09_s);
        if (in_m >= 4'd2) s9 = wrap_add32(s9, wrap_mul32(v1_s, c19_s));
        if (in_m >= 4'd3) s9 = wrap_add32(s9, wrap_mul32(v2_s, c29_s));
        if (in_m >= 4'd4) s9 = wrap_add32(s9, wrap_mul32(v3_s, c39_s));
        if (in_m >= 4'd5) s9 = wrap_add32(s9, wrap_mul32(v4_s, c49_s));
        if (in_m >= 4'd6) s9 = wrap_add32(s9, wrap_mul32(v5_s, c59_s));
        if (in_m >= 4'd7) s9 = wrap_add32(s9, wrap_mul32(v6_s, c69_s));
        if (in_m >= 4'd8) s9 = wrap_add32(s9, wrap_mul32(v7_s, c79_s));
        if (in_m >= 4'd9) s9 = wrap_add32(s9, wrap_mul32(v8_s, c89_s));
        if (in_m >= 4'd10) s9 = wrap_add32(s9, wrap_mul32(v9_s, c99_s));
    end else begin
        s0 = wrap_mul32(v0_s, c00_s);
        if (in_n >= 4'd2) s0 = wrap_add32(s0, wrap_mul32(v1_s, c01_s));
        if (in_n >= 4'd3) s0 = wrap_add32(s0, wrap_mul32(v2_s, c02_s));
        if (in_n >= 4'd4) s0 = wrap_add32(s0, wrap_mul32(v3_s, c03_s));
        if (in_n >= 4'd5) s0 = wrap_add32(s0, wrap_mul32(v4_s, c04_s));
        if (in_n >= 4'd6) s0 = wrap_add32(s0, wrap_mul32(v5_s, c05_s));
        if (in_n >= 4'd7) s0 = wrap_add32(s0, wrap_mul32(v6_s, c06_s));
        if (in_n >= 4'd8) s0 = wrap_add32(s0, wrap_mul32(v7_s, c07_s));
        if (in_n >= 4'd9) s0 = wrap_add32(s0, wrap_mul32(v8_s, c08_s));
        if (in_n >= 4'd10) s0 = wrap_add32(s0, wrap_mul32(v9_s, c09_s));

        s1 = wrap_mul32(v0_s, c10_s);
        if (in_n >= 4'd2) s1 = wrap_add32(s1, wrap_mul32(v1_s, c11_s));
        if (in_n >= 4'd3) s1 = wrap_add32(s1, wrap_mul32(v2_s, c12_s));
        if (in_n >= 4'd4) s1 = wrap_add32(s1, wrap_mul32(v3_s, c13_s));
        if (in_n >= 4'd5) s1 = wrap_add32(s1, wrap_mul32(v4_s, c14_s));
        if (in_n >= 4'd6) s1 = wrap_add32(s1, wrap_mul32(v5_s, c15_s));
        if (in_n >= 4'd7) s1 = wrap_add32(s1, wrap_mul32(v6_s, c16_s));
        if (in_n >= 4'd8) s1 = wrap_add32(s1, wrap_mul32(v7_s, c17_s));
        if (in_n >= 4'd9) s1 = wrap_add32(s1, wrap_mul32(v8_s, c18_s));
        if (in_n >= 4'd10) s1 = wrap_add32(s1, wrap_mul32(v9_s, c19_s));

        s2 = wrap_mul32(v0_s, c20_s);
        if (in_n >= 4'd2) s2 = wrap_add32(s2, wrap_mul32(v1_s, c21_s));
        if (in_n >= 4'd3) s2 = wrap_add32(s2, wrap_mul32(v2_s, c22_s));
        if (in_n >= 4'd4) s2 = wrap_add32(s2, wrap_mul32(v3_s, c23_s));
        if (in_n >= 4'd5) s2 = wrap_add32(s2, wrap_mul32(v4_s, c24_s));
        if (in_n >= 4'd6) s2 = wrap_add32(s2, wrap_mul32(v5_s, c25_s));
        if (in_n >= 4'd7) s2 = wrap_add32(s2, wrap_mul32(v6_s, c26_s));
        if (in_n >= 4'd8) s2 = wrap_add32(s2, wrap_mul32(v7_s, c27_s));
        if (in_n >= 4'd9) s2 = wrap_add32(s2, wrap_mul32(v8_s, c28_s));
        if (in_n >= 4'd10) s2 = wrap_add32(s2, wrap_mul32(v9_s, c29_s));

        s3 = wrap_mul32(v0_s, c30_s);
        if (in_n >= 4'd2) s3 = wrap_add32(s3, wrap_mul32(v1_s, c31_s));
        if (in_n >= 4'd3) s3 = wrap_add32(s3, wrap_mul32(v2_s, c32_s));
        if (in_n >= 4'd4) s3 = wrap_add32(s3, wrap_mul32(v3_s, c33_s));
        if (in_n >= 4'd5) s3 = wrap_add32(s3, wrap_mul32(v4_s, c34_s));
        if (in_n >= 4'd6) s3 = wrap_add32(s3, wrap_mul32(v5_s, c35_s));
        if (in_n >= 4'd7) s3 = wrap_add32(s3, wrap_mul32(v6_s, c36_s));
        if (in_n >= 4'd8) s3 = wrap_add32(s3, wrap_mul32(v7_s, c37_s));
        if (in_n >= 4'd9) s3 = wrap_add32(s3, wrap_mul32(v8_s, c38_s));
        if (in_n >= 4'd10) s3 = wrap_add32(s3, wrap_mul32(v9_s, c39_s));

        s4 = wrap_mul32(v0_s, c40_s);
        if (in_n >= 4'd2) s4 = wrap_add32(s4, wrap_mul32(v1_s, c41_s));
        if (in_n >= 4'd3) s4 = wrap_add32(s4, wrap_mul32(v2_s, c42_s));
        if (in_n >= 4'd4) s4 = wrap_add32(s4, wrap_mul32(v3_s, c43_s));
        if (in_n >= 4'd5) s4 = wrap_add32(s4, wrap_mul32(v4_s, c44_s));
        if (in_n >= 4'd6) s4 = wrap_add32(s4, wrap_mul32(v5_s, c45_s));
        if (in_n >= 4'd7) s4 = wrap_add32(s4, wrap_mul32(v6_s, c46_s));
        if (in_n >= 4'd8) s4 = wrap_add32(s4, wrap_mul32(v7_s, c47_s));
        if (in_n >= 4'd9) s4 = wrap_add32(s4, wrap_mul32(v8_s, c48_s));
        if (in_n >= 4'd10) s4 = wrap_add32(s4, wrap_mul32(v9_s, c49_s));

        s5 = wrap_mul32(v0_s, c50_s);
        if (in_n >= 4'd2) s5 = wrap_add32(s5, wrap_mul32(v1_s, c51_s));
        if (in_n >= 4'd3) s5 = wrap_add32(s5, wrap_mul32(v2_s, c52_s));
        if (in_n >= 4'd4) s5 = wrap_add32(s5, wrap_mul32(v3_s, c53_s));
        if (in_n >= 4'd5) s5 = wrap_add32(s5, wrap_mul32(v4_s, c54_s));
        if (in_n >= 4'd6) s5 = wrap_add32(s5, wrap_mul32(v5_s, c55_s));
        if (in_n >= 4'd7) s5 = wrap_add32(s5, wrap_mul32(v6_s, c56_s));
        if (in_n >= 4'd8) s5 = wrap_add32(s5, wrap_mul32(v7_s, c57_s));
        if (in_n >= 4'd9) s5 = wrap_add32(s5, wrap_mul32(v8_s, c58_s));
        if (in_n >= 4'd10) s5 = wrap_add32(s5, wrap_mul32(v9_s, c59_s));

        s6 = wrap_mul32(v0_s, c60_s);
        if (in_n >= 4'd2) s6 = wrap_add32(s6, wrap_mul32(v1_s, c61_s));
        if (in_n >= 4'd3) s6 = wrap_add32(s6, wrap_mul32(v2_s, c62_s));
        if (in_n >= 4'd4) s6 = wrap_add32(s6, wrap_mul32(v3_s, c63_s));
        if (in_n >= 4'd5) s6 = wrap_add32(s6, wrap_mul32(v4_s, c64_s));
        if (in_n >= 4'd6) s6 = wrap_add32(s6, wrap_mul32(v5_s, c65_s));
        if (in_n >= 4'd7) s6 = wrap_add32(s6, wrap_mul32(v6_s, c66_s));
        if (in_n >= 4'd8) s6 = wrap_add32(s6, wrap_mul32(v7_s, c67_s));
        if (in_n >= 4'd9) s6 = wrap_add32(s6, wrap_mul32(v8_s, c68_s));
        if (in_n >= 4'd10) s6 = wrap_add32(s6, wrap_mul32(v9_s, c69_s));

        s7 = wrap_mul32(v0_s, c70_s);
        if (in_n >= 4'd2) s7 = wrap_add32(s7, wrap_mul32(v1_s, c71_s));
        if (in_n >= 4'd3) s7 = wrap_add32(s7, wrap_mul32(v2_s, c72_s));
        if (in_n >= 4'd4) s7 = wrap_add32(s7, wrap_mul32(v3_s, c73_s));
        if (in_n >= 4'd5) s7 = wrap_add32(s7, wrap_mul32(v4_s, c74_s));
        if (in_n >= 4'd6) s7 = wrap_add32(s7, wrap_mul32(v5_s, c75_s));
        if (in_n >= 4'd7) s7 = wrap_add32(s7, wrap_mul32(v6_s, c76_s));
        if (in_n >= 4'd8) s7 = wrap_add32(s7, wrap_mul32(v7_s, c77_s));
        if (in_n >= 4'd9) s7 = wrap_add32(s7, wrap_mul32(v8_s, c78_s));
        if (in_n >= 4'd10) s7 = wrap_add32(s7, wrap_mul32(v9_s, c79_s));

        s8 = wrap_mul32(v0_s, c80_s);
        if (in_n >= 4'd2) s8 = wrap_add32(s8, wrap_mul32(v1_s, c81_s));
        if (in_n >= 4'd3) s8 = wrap_add32(s8, wrap_mul32(v2_s, c82_s));
        if (in_n >= 4'd4) s8 = wrap_add32(s8, wrap_mul32(v3_s, c83_s));
        if (in_n >= 4'd5) s8 = wrap_add32(s8, wrap_mul32(v4_s, c84_s));
        if (in_n >= 4'd6) s8 = wrap_add32(s8, wrap_mul32(v5_s, c85_s));
        if (in_n >= 4'd7) s8 = wrap_add32(s8, wrap_mul32(v6_s, c86_s));
        if (in_n >= 4'd8) s8 = wrap_add32(s8, wrap_mul32(v7_s, c87_s));
        if (in_n >= 4'd9) s8 = wrap_add32(s8, wrap_mul32(v8_s, c88_s));
        if (in_n >= 4'd10) s8 = wrap_add32(s8, wrap_mul32(v9_s, c89_s));

        s9 = wrap_mul32(v0_s, c90_s);
        if (in_n >= 4'd2) s9 = wrap_add32(s9, wrap_mul32(v1_s, c91_s));
        if (in_n >= 4'd3) s9 = wrap_add32(s9, wrap_mul32(v2_s, c92_s));
        if (in_n >= 4'd4) s9 = wrap_add32(s9, wrap_mul32(v3_s, c93_s));
        if (in_n >= 4'd5) s9 = wrap_add32(s9, wrap_mul32(v4_s, c94_s));
        if (in_n >= 4'd6) s9 = wrap_add32(s9, wrap_mul32(v5_s, c95_s));
        if (in_n >= 4'd7) s9 = wrap_add32(s9, wrap_mul32(v6_s, c96_s));
        if (in_n >= 4'd8) s9 = wrap_add32(s9, wrap_mul32(v7_s, c97_s));
        if (in_n >= 4'd9) s9 = wrap_add32(s9, wrap_mul32(v8_s, c98_s));
        if (in_n >= 4'd10) s9 = wrap_add32(s9, wrap_mul32(v9_s, c99_s));
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy <= 1'b0;
        out_c00 <= 32'd0; out_c01 <= 32'd0; out_c02 <= 32'd0; out_c03 <= 32'd0; out_c04 <= 32'd0; out_c05 <= 32'd0; out_c06 <= 32'd0; out_c07 <= 32'd0; out_c08 <= 32'd0; out_c09 <= 32'd0;
        out_c10 <= 32'd0; out_c11 <= 32'd0; out_c12 <= 32'd0; out_c13 <= 32'd0; out_c14 <= 32'd0; out_c15 <= 32'd0; out_c16 <= 32'd0; out_c17 <= 32'd0; out_c18 <= 32'd0; out_c19 <= 32'd0;
        out_c20 <= 32'd0; out_c21 <= 32'd0; out_c22 <= 32'd0; out_c23 <= 32'd0; out_c24 <= 32'd0; out_c25 <= 32'd0; out_c26 <= 32'd0; out_c27 <= 32'd0; out_c28 <= 32'd0; out_c29 <= 32'd0;
        out_c30 <= 32'd0; out_c31 <= 32'd0; out_c32 <= 32'd0; out_c33 <= 32'd0; out_c34 <= 32'd0; out_c35 <= 32'd0; out_c36 <= 32'd0; out_c37 <= 32'd0; out_c38 <= 32'd0; out_c39 <= 32'd0;
        out_c40 <= 32'd0; out_c41 <= 32'd0; out_c42 <= 32'd0; out_c43 <= 32'd0; out_c44 <= 32'd0; out_c45 <= 32'd0; out_c46 <= 32'd0; out_c47 <= 32'd0; out_c48 <= 32'd0; out_c49 <= 32'd0;
        out_c50 <= 32'd0; out_c51 <= 32'd0; out_c52 <= 32'd0; out_c53 <= 32'd0; out_c54 <= 32'd0; out_c55 <= 32'd0; out_c56 <= 32'd0; out_c57 <= 32'd0; out_c58 <= 32'd0; out_c59 <= 32'd0;
        out_c60 <= 32'd0; out_c61 <= 32'd0; out_c62 <= 32'd0; out_c63 <= 32'd0; out_c64 <= 32'd0; out_c65 <= 32'd0; out_c66 <= 32'd0; out_c67 <= 32'd0; out_c68 <= 32'd0; out_c69 <= 32'd0;
        out_c70 <= 32'd0; out_c71 <= 32'd0; out_c72 <= 32'd0; out_c73 <= 32'd0; out_c74 <= 32'd0; out_c75 <= 32'd0; out_c76 <= 32'd0; out_c77 <= 32'd0; out_c78 <= 32'd0; out_c79 <= 32'd0;
        out_c80 <= 32'd0; out_c81 <= 32'd0; out_c82 <= 32'd0; out_c83 <= 32'd0; out_c84 <= 32'd0; out_c85 <= 32'd0; out_c86 <= 32'd0; out_c87 <= 32'd0; out_c88 <= 32'd0; out_c89 <= 32'd0;
        out_c90 <= 32'd0; out_c91 <= 32'd0; out_c92 <= 32'd0; out_c93 <= 32'd0; out_c94 <= 32'd0; out_c95 <= 32'd0; out_c96 <= 32'd0; out_c97 <= 32'd0; out_c98 <= 32'd0; out_c99 <= 32'd0;
    end else if (input_accepted) begin
        busy <= 1'b1;

        out_c00 <= in_c00; out_c01 <= in_c01; out_c02 <= in_c02; out_c03 <= in_c03; out_c04 <= in_c04; out_c05 <= in_c05; out_c06 <= in_c06; out_c07 <= in_c07; out_c08 <= in_c08; out_c09 <= in_c09;
        out_c10 <= in_c10; out_c11 <= in_c11; out_c12 <= in_c12; out_c13 <= in_c13; out_c14 <= in_c14; out_c15 <= in_c15; out_c16 <= in_c16; out_c17 <= in_c17; out_c18 <= in_c18; out_c19 <= in_c19;
        out_c20 <= in_c20; out_c21 <= in_c21; out_c22 <= in_c22; out_c23 <= in_c23; out_c24 <= in_c24; out_c25 <= in_c25; out_c26 <= in_c26; out_c27 <= in_c27; out_c28 <= in_c28; out_c29 <= in_c29;
        out_c30 <= in_c30; out_c31 <= in_c31; out_c32 <= in_c32; out_c33 <= in_c33; out_c34 <= in_c34; out_c35 <= in_c35; out_c36 <= in_c36; out_c37 <= in_c37; out_c38 <= in_c38; out_c39 <= in_c39;
        out_c40 <= in_c40; out_c41 <= in_c41; out_c42 <= in_c42; out_c43 <= in_c43; out_c44 <= in_c44; out_c45 <= in_c45; out_c46 <= in_c46; out_c47 <= in_c47; out_c48 <= in_c48; out_c49 <= in_c49;
        out_c50 <= in_c50; out_c51 <= in_c51; out_c52 <= in_c52; out_c53 <= in_c53; out_c54 <= in_c54; out_c55 <= in_c55; out_c56 <= in_c56; out_c57 <= in_c57; out_c58 <= in_c58; out_c59 <= in_c59;
        out_c60 <= in_c60; out_c61 <= in_c61; out_c62 <= in_c62; out_c63 <= in_c63; out_c64 <= in_c64; out_c65 <= in_c65; out_c66 <= in_c66; out_c67 <= in_c67; out_c68 <= in_c68; out_c69 <= in_c69;
        out_c70 <= in_c70; out_c71 <= in_c71; out_c72 <= in_c72; out_c73 <= in_c73; out_c74 <= in_c74; out_c75 <= in_c75; out_c76 <= in_c76; out_c77 <= in_c77; out_c78 <= in_c78; out_c79 <= in_c79;
        out_c80 <= in_c80; out_c81 <= in_c81; out_c82 <= in_c82; out_c83 <= in_c83; out_c84 <= in_c84; out_c85 <= in_c85; out_c86 <= in_c86; out_c87 <= in_c87; out_c88 <= in_c88; out_c89 <= in_c89;
        out_c90 <= in_c90; out_c91 <= in_c91; out_c92 <= in_c92; out_c93 <= in_c93; out_c94 <= in_c94; out_c95 <= in_c95; out_c96 <= in_c96; out_c97 <= in_c97; out_c98 <= in_c98; out_c99 <= in_c99;

        if (in_tau != 32'd0) begin
            if (in_side == 1'b0) begin
                if (in_n >= 4'd1) begin
                    if (in_m >= 4'd1) out_c00 <= wrap_sub32(c00_s, wrap_mul32(s0, t0));
                    if (in_m >= 4'd2) out_c10 <= wrap_sub32(c10_s, wrap_mul32(s0, t1));
                    if (in_m >= 4'd3) out_c20 <= wrap_sub32(c20_s, wrap_mul32(s0, t2));
                    if (in_m >= 4'd4) out_c30 <= wrap_sub32(c30_s, wrap_mul32(s0, t3));
                    if (in_m >= 4'd5) out_c40 <= wrap_sub32(c40_s, wrap_mul32(s0, t4));
                    if (in_m >= 4'd6) out_c50 <= wrap_sub32(c50_s, wrap_mul32(s0, t5));
                    if (in_m >= 4'd7) out_c60 <= wrap_sub32(c60_s, wrap_mul32(s0, t6));
                    if (in_m >= 4'd8) out_c70 <= wrap_sub32(c70_s, wrap_mul32(s0, t7));
                    if (in_m >= 4'd9) out_c80 <= wrap_sub32(c80_s, wrap_mul32(s0, t8));
                    if (in_m >= 4'd10) out_c90 <= wrap_sub32(c90_s, wrap_mul32(s0, t9));
                end
                if (in_n >= 4'd2) begin
                    if (in_m >= 4'd1) out_c01 <= wrap_sub32(c01_s, wrap_mul32(s1, t0));
                    if (in_m >= 4'd2) out_c11 <= wrap_sub32(c11_s, wrap_mul32(s1, t1));
                    if (in_m >= 4'd3) out_c21 <= wrap_sub32(c21_s, wrap_mul32(s1, t2));
                    if (in_m >= 4'd4) out_c31 <= wrap_sub32(c31_s, wrap_mul32(s1, t3));
                    if (in_m >= 4'd5) out_c41 <= wrap_sub32(c41_s, wrap_mul32(s1, t4));
                    if (in_m >= 4'd6) out_c51 <= wrap_sub32(c51_s, wrap_mul32(s1, t5));
                    if (in_m >= 4'd7) out_c61 <= wrap_sub32(c61_s, wrap_mul32(s1, t6));
                    if (in_m >= 4'd8) out_c71 <= wrap_sub32(c71_s, wrap_mul32(s1, t7));
                    if (in_m >= 4'd9) out_c81 <= wrap_sub32(c81_s, wrap_mul32(s1, t8));
                    if (in_m >= 4'd10) out_c91 <= wrap_sub32(c91_s, wrap_mul32(s1, t9));
                end
                if (in_n >= 4'd3) begin
                    if (in_m >= 4'd1) out_c02 <= wrap_sub32(c02_s, wrap_mul32(s2, t0));
                    if (in_m >= 4'd2) out_c12 <= wrap_sub32(c12_s, wrap_mul32(s2, t1));
                    if (in_m >= 4'd3) out_c22 <= wrap_sub32(c22_s, wrap_mul32(s2, t2));
                    if (in_m >= 4'd4) out_c32 <= wrap_sub32(c32_s, wrap_mul32(s2, t3));
                    if (in_m >= 4'd5) out_c42 <= wrap_sub32(c42_s, wrap_mul32(s2, t4));
                    if (in_m >= 4'd6) out_c52 <= wrap_sub32(c52_s, wrap_mul32(s2, t5));
                    if (in_m >= 4'd7) out_c62 <= wrap_sub32(c62_s, wrap_mul32(s2, t6));
                    if (in_m >= 4'd8) out_c72 <= wrap_sub32(c72_s, wrap_mul32(s2, t7));
                    if (in_m >= 4'd9) out_c82 <= wrap_sub32(c82_s, wrap_mul32(s2, t8));
                    if (in_m >= 4'd10) out_c92 <= wrap_sub32(c92_s, wrap_mul32(s2, t9));
                end
                if (in_n >= 4'd4) begin
                    if (in_m >= 4'd1) out_c03 <= wrap_sub32(c03_s, wrap_mul32(s3, t0));
                    if (in_m >= 4'd2) out_c13 <= wrap_sub32(c13_s, wrap_mul32(s3, t1));
                    if (in_m >= 4'd3) out_c23 <= wrap_sub32(c23_s, wrap_mul32(s3, t2));
                    if (in_m >= 4'd4) out_c33 <= wrap_sub32(c33_s, wrap_mul32(s3, t3));
                    if (in_m >= 4'd5) out_c43 <= wrap_sub32(c43_s, wrap_mul32(s3, t4));
                    if (in_m >= 4'd6) out_c53 <= wrap_sub32(c53_s, wrap_mul32(s3, t5));
                    if (in_m >= 4'd7) out_c63 <= wrap_sub32(c63_s, wrap_mul32(s3, t6));
                    if (in_m >= 4'd8) out_c73 <= wrap_sub32(c73_s, wrap_mul32(s3, t7));
                    if (in_m >= 4'd9) out_c83 <= wrap_sub32(c83_s, wrap_mul32(s3, t8));
                    if (in_m >= 4'd10) out_c93 <= wrap_sub32(c93_s, wrap_mul32(s3, t9));
                end
                if (in_n >= 4'd5) begin
                    if (in_m >= 4'd1) out_c04 <= wrap_sub32(c04_s, wrap_mul32(s4, t0));
                    if (in_m >= 4'd2) out_c14 <= wrap_sub32(c14_s, wrap_mul32(s4, t1));
                    if (in_m >= 4'd3) out_c24 <= wrap_sub32(c24_s, wrap_mul32(s4, t2));
                    if (in_m >= 4'd4) out_c34 <= wrap_sub32(c34_s, wrap_mul32(s4, t3));
                    if (in_m >= 4'd5) out_c44 <= wrap_sub32(c44_s, wrap_mul32(s4, t4));
                    if (in_m >= 4'd6) out_c54 <= wrap_sub32(c54_s, wrap_mul32(s4, t5));
                    if (in_m >= 4'd7) out_c64 <= wrap_sub32(c64_s, wrap_mul32(s4, t6));
                    if (in_m >= 4'd8) out_c74 <= wrap_sub32(c74_s, wrap_mul32(s4, t7));
                    if (in_m >= 4'd9) out_c84 <= wrap_sub32(c84_s, wrap_mul32(s4, t8));
                    if (in_m >= 4'd10) out_c94 <= wrap_sub32(c94_s, wrap_mul32(s4, t9));
                end
                if (in_n >= 4'd6) begin
                    if (in_m >= 4'd1) out_c05 <= wrap_sub32(c05_s, wrap_mul32(s5, t0));
                    if (in_m >= 4'd2) out_c15 <= wrap_sub32(c15_s, wrap_mul32(s5, t1));
                    if (in_m >= 4'd3) out_c25 <= wrap_sub32(c25_s, wrap_mul32(s5, t2));
                    if (in_m >= 4'd4) out_c35 <= wrap_sub32(c35_s, wrap_mul32(s5, t3));
                    if (in_m >= 4'd5) out_c45 <= wrap_sub32(c45_s, wrap_mul32(s5, t4));
                    if (in_m >= 4'd6) out_c55 <= wrap_sub32(c55_s, wrap_mul32(s5, t5));
                    if (in_m >= 4'd7) out_c65 <= wrap_sub32(c65_s, wrap_mul32(s5, t6));
                    if (in_m >= 4'd8) out_c75 <= wrap_sub32(c75_s, wrap_mul32(s5, t7));
                    if (in_m >= 4'd9) out_c85 <= wrap_sub32(c85_s, wrap_mul32(s5, t8));
                    if (in_m >= 4'd10) out_c95 <= wrap_sub32(c95_s, wrap_mul32(s5, t9));
                end
                if (in_n >= 4'd7) begin
                    if (in_m >= 4'd1) out_c06 <= wrap_sub32(c06_s, wrap_mul32(s6, t0));
                    if (in_m >= 4'd2) out_c16 <= wrap_sub32(c16_s, wrap_mul32(s6, t1));
                    if (in_m >= 4'd3) out_c26 <= wrap_sub32(c26_s, wrap_mul32(s6, t2));
                    if (in_m >= 4'd4) out_c36 <= wrap_sub32(c36_s, wrap_mul32(s6, t3));
                    if (in_m >= 4'd5) out_c46 <= wrap_sub32(c46_s, wrap_mul32(s6, t4));
                    if (in_m >= 4'd6) out_c56 <= wrap_sub32(c56_s, wrap_mul32(s6, t5));
                    if (in_m >= 4'd7) out_c66 <= wrap_sub32(c66_s, wrap_mul32(s6, t6));
                    if (in_m >= 4'd8) out_c76 <= wrap_sub32(c76_s, wrap_mul32(s6, t7));
                    if (in_m >= 4'd9) out_c86 <= wrap_sub32(c86_s, wrap_mul32(s6, t8));
                    if (in_m >= 4'd10) out_c96 <= wrap_sub32(c96_s, wrap_mul32(s6, t9));
                end
                if (in_n >= 4'd8) begin
                    if (in_m >= 4'd1) out_c07 <= wrap_sub32(c07_s, wrap_mul32(s7, t0));
                    if (in_m >= 4'd2) out_c17 <= wrap_sub32(c17_s, wrap_mul32(s7, t1));
                    if (in_m >= 4'd3) out_c27 <= wrap_sub32(c27_s, wrap_mul32(s7, t2));
                    if (in_m >= 4'd4) out_c37 <= wrap_sub32(c37_s, wrap_mul32(s7, t3));
                    if (in_m >= 4'd5) out_c47 <= wrap_sub32(c47_s, wrap_mul32(s7, t4));
                    if (in_m >= 4'd6) out_c57 <= wrap_sub32(c57_s, wrap_mul32(s7, t5));
                    if (in_m >= 4'd7) out_c67 <= wrap_sub32(c67_s, wrap_mul32(s7, t6));
                    if (in_m >= 4'd8) out_c77 <= wrap_sub32(c77_s, wrap_mul32(s7, t7));
                    if (in_m >= 4'd9) out_c87 <= wrap_sub32(c87_s, wrap_mul32(s7, t8));
                    if (in_m >= 4'd10) out_c97 <= wrap_sub32(c97_s, wrap_mul32(s7, t9));
                end
                if (in_n >= 4'd9) begin
                    if (in_m >= 4'd1) out_c08 <= wrap_sub32(c08_s, wrap_mul32(s8, t0));
                    if (in_m >= 4'd2) out_c18 <= wrap_sub32(c18_s, wrap_mul32(s8, t1));
                    if (in_m >= 4'd3) out_c28 <= wrap_sub32(c28_s, wrap_mul32(s8, t2));
                    if (in_m >= 4'd4) out_c38 <= wrap_sub32(c38_s, wrap_mul32(s8, t3));
                    if (in_m >= 4'd5) out_c48 <= wrap_sub32(c48_s, wrap_mul32(s8, t4));
                    if (in_m >= 4'd6) out_c58 <= wrap_sub32(c58_s, wrap_mul32(s8, t5));
                    if (in_m >= 4'd7) out_c68 <= wrap_sub32(c68_s, wrap_mul32(s8, t6));
                    if (in_m >= 4'd8) out_c78 <= wrap_sub32(c78_s, wrap_mul32(s8, t7));
                    if (in_m >= 4'd9) out_c88 <= wrap_sub32(c88_s, wrap_mul32(s8, t8));
                    if (in_m >= 4'd10) out_c98 <= wrap_sub32(c98_s, wrap_mul32(s8, t9));
                end
                if (in_n >= 4'd10) begin
                    if (in_m >= 4'd1) out_c09 <= wrap_sub32(c09_s, wrap_mul32(s9, t0));
                    if (in_m >= 4'd2) out_c19 <= wrap_sub32(c19_s, wrap_mul32(s9, t1));
                    if (in_m >= 4'd3) out_c29 <= wrap_sub32(c29_s, wrap_mul32(s9, t2));
                    if (in_m >= 4'd4) out_c39 <= wrap_sub32(c39_s, wrap_mul32(s9, t3));
                    if (in_m >= 4'd5) out_c49 <= wrap_sub32(c49_s, wrap_mul32(s9, t4));
                    if (in_m >= 4'd6) out_c59 <= wrap_sub32(c59_s, wrap_mul32(s9, t5));
                    if (in_m >= 4'd7) out_c69 <= wrap_sub32(c69_s, wrap_mul32(s9, t6));
                    if (in_m >= 4'd8) out_c79 <= wrap_sub32(c79_s, wrap_mul32(s9, t7));
                    if (in_m >= 4'd9) out_c89 <= wrap_sub32(c89_s, wrap_mul32(s9, t8));
                    if (in_m >= 4'd10) out_c99 <= wrap_sub32(c99_s, wrap_mul32(s9, t9));
                end
            end else begin
                if (in_m >= 4'd1) begin
                    if (in_n >= 4'd1) out_c00 <= wrap_sub32(c00_s, wrap_mul32(s0, t0));
                    if (in_n >= 4'd2) out_c01 <= wrap_sub32(c01_s, wrap_mul32(s0, t1));
                    if (in_n >= 4'd3) out_c02 <= wrap_sub32(c02_s, wrap_mul32(s0, t2));
                    if (in_n >= 4'd4) out_c03 <= wrap_sub32(c03_s, wrap_mul32(s0, t3));
                    if (in_n >= 4'd5) out_c04 <= wrap_sub32(c04_s, wrap_mul32(s0, t4));
                    if (in_n >= 4'd6) out_c05 <= wrap_sub32(c05_s, wrap_mul32(s0, t5));
                    if (in_n >= 4'd7) out_c06 <= wrap_sub32(c06_s, wrap_mul32(s0, t6));
                    if (in_n >= 4'd8) out_c07 <= wrap_sub32(c07_s, wrap_mul32(s0, t7));
                    if (in_n >= 4'd9) out_c08 <= wrap_sub32(c08_s, wrap_mul32(s0, t8));
                    if (in_n >= 4'd10) out_c09 <= wrap_sub32(c09_s, wrap_mul32(s0, t9));
                end
                if (in_m >= 4'd2) begin
                    if (in_n >= 4'd1) out_c10 <= wrap_sub32(c10_s, wrap_mul32(s1, t0));
                    if (in_n >= 4'd2) out_c11 <= wrap_sub32(c11_s, wrap_mul32(s1, t1));
                    if (in_n >= 4'd3) out_c12 <= wrap_sub32(c12_s, wrap_mul32(s1, t2));
                    if (in_n >= 4'd4) out_c13 <= wrap_sub32(c13_s, wrap_mul32(s1, t3));
                    if (in_n >= 4'd5) out_c14 <= wrap_sub32(c14_s, wrap_mul32(s1, t4));
                    if (in_n >= 4'd6) out_c15 <= wrap_sub32(c15_s, wrap_mul32(s1, t5));
                    if (in_n >= 4'd7) out_c16 <= wrap_sub32(c16_s, wrap_mul32(s1, t6));
                    if (in_n >= 4'd8) out_c17 <= wrap_sub32(c17_s, wrap_mul32(s1, t7));
                    if (in_n >= 4'd9) out_c18 <= wrap_sub32(c18_s, wrap_mul32(s1, t8));
                    if (in_n >= 4'd10) out_c19 <= wrap_sub32(c19_s, wrap_mul32(s1, t9));
                end
                if (in_m >= 4'd3) begin
                    if (in_n >= 4'd1) out_c20 <= wrap_sub32(c20_s, wrap_mul32(s2, t0));
                    if (in_n >= 4'd2) out_c21 <= wrap_sub32(c21_s, wrap_mul32(s2, t1));
                    if (in_n >= 4'd3) out_c22 <= wrap_sub32(c22_s, wrap_mul32(s2, t2));
                    if (in_n >= 4'd4) out_c23 <= wrap_sub32(c23_s, wrap_mul32(s2, t3));
                    if (in_n >= 4'd5) out_c24 <= wrap_sub32(c24_s, wrap_mul32(s2, t4));
                    if (in_n >= 4'd6) out_c25 <= wrap_sub32(c25_s, wrap_mul32(s2, t5));
                    if (in_n >= 4'd7) out_c26 <= wrap_sub32(c26_s, wrap_mul32(s2, t6));
                    if (in_n >= 4'd8) out_c27 <= wrap_sub32(c27_s, wrap_mul32(s2, t7));
                    if (in_n >= 4'd9) out_c28 <= wrap_sub32(c28_s, wrap_mul32(s2, t8));
                    if (in_n >= 4'd10) out_c29 <= wrap_sub32(c29_s, wrap_mul32(s2, t9));
                end
                if (in_m >= 4'd4) begin
                    if (in_n >= 4'd1) out_c30 <= wrap_sub32(c30_s, wrap_mul32(s3, t0));
                    if (in_n >= 4'd2) out_c31 <= wrap_sub32(c31_s, wrap_mul32(s3, t1));
                    if (in_n >= 4'd3) out_c32 <= wrap_sub32(c32_s, wrap_mul32(s3, t2));
                    if (in_n >= 4'd4) out_c33 <= wrap_sub32(c33_s, wrap_mul32(s3, t3));
                    if (in_n >= 4'd5) out_c34 <= wrap_sub32(c34_s, wrap_mul32(s3, t4));
                    if (in_n >= 4'd6) out_c35 <= wrap_sub32(c35_s, wrap_mul32(s3, t5));
                    if (in_n >= 4'd7) out_c36 <= wrap_sub32(c36_s, wrap_mul32(s3, t6));
                    if (in_n >= 4'd8) out_c37 <= wrap_sub32(c37_s, wrap_mul32(s3, t7));
                    if (in_n >= 4'd9) out_c38 <= wrap_sub32(c38_s, wrap_mul32(s3, t8));
                    if (in_n >= 4'd10) out_c39 <= wrap_sub32(c39_s, wrap_mul32(s3, t9));
                end
                if (in_m >= 4'd5) begin
                    if (in_n >= 4'd1) out_c40 <= wrap_sub32(c40_s, wrap_mul32(s4, t0));
                    if (in_n >= 4'd2) out_c41 <= wrap_sub32(c41_s, wrap_mul32(s4, t1));
                    if (in_n >= 4'd3) out_c42 <= wrap_sub32(c42_s, wrap_mul32(s4, t2));
                    if (in_n >= 4'd4) out_c43 <= wrap_sub32(c43_s, wrap_mul32(s4, t3));
                    if (in_n >= 4'd5) out_c44 <= wrap_sub32(c44_s, wrap_mul32(s4, t4));
                    if (in_n >= 4'd6) out_c45 <= wrap_sub32(c45_s, wrap_mul32(s4, t5));
                    if (in_n >= 4'd7) out_c46 <= wrap_sub32(c46_s, wrap_mul32(s4, t6));
                    if (in_n >= 4'd8) out_c47 <= wrap_sub32(c47_s, wrap_mul32(s4, t7));
                    if (in_n >= 4'd9) out_c48 <= wrap_sub32(c48_s, wrap_mul32(s4, t8));
                    if (in_n >= 4'd10) out_c49 <= wrap_sub32(c49_s, wrap_mul32(s4, t9));
                end
                if (in_m >= 4'd6) begin
                    if (in_n >= 4'd1) out_c50 <= wrap_sub32(c50_s, wrap_mul32(s5, t0));
                    if (in_n >= 4'd2) out_c51 <= wrap_sub32(c51_s, wrap_mul32(s5, t1));
                    if (in_n >= 4'd3) out_c52 <= wrap_sub32(c52_s, wrap_mul32(s5, t2));
                    if (in_n >= 4'd4) out_c53 <= wrap_sub32(c53_s, wrap_mul32(s5, t3));
                    if (in_n >= 4'd5) out_c54 <= wrap_sub32(c54_s, wrap_mul32(s5, t4));
                    if (in_n >= 4'd6) out_c55 <= wrap_sub32(c55_s, wrap_mul32(s5, t5));
                    if (in_n >= 4'd7) out_c56 <= wrap_sub32(c56_s, wrap_mul32(s5, t6));
                    if (in_n >= 4'd8) out_c57 <= wrap_sub32(c57_s, wrap_mul32(s5, t7));
                    if (in_n >= 4'd9) out_c58 <= wrap_sub32(c58_s, wrap_mul32(s5, t8));
                    if (in_n >= 4'd10) out_c59 <= wrap_sub32(c59_s, wrap_mul32(s5, t9));
                end
                if (in_m >= 4'd7) begin
                    if (in_n >= 4'd1) out_c60 <= wrap_sub32(c60_s, wrap_mul32(s6, t0));
                    if (in_n >= 4'd2) out_c61 <= wrap_sub32(c61_s, wrap_mul32(s6, t1));
                    if (in_n >= 4'd3) out_c62 <= wrap_sub32(c62_s, wrap_mul32(s6, t2));
                    if (in_n >= 4'd4) out_c63 <= wrap_sub32(c63_s, wrap_mul32(s6, t3));
                    if (in_n >= 4'd5) out_c64 <= wrap_sub32(c64_s, wrap_mul32(s6, t4));
                    if (in_n >= 4'd6) out_c65 <= wrap_sub32(c65_s, wrap_mul32(s6, t5));
                    if (in_n >= 4'd7) out_c66 <= wrap_sub32(c66_s, wrap_mul32(s6, t6));
                    if (in_n >= 4'd8) out_c67 <= wrap_sub32(c67_s, wrap_mul32(s6, t7));
                    if (in_n >= 4'd9) out_c68 <= wrap_sub32(c68_s, wrap_mul32(s6, t8));
                    if (in_n >= 4'd10) out_c69 <= wrap_sub32(c69_s, wrap_mul32(s6, t9));
                end
                if (in_m >= 4'd8) begin
                    if (in_n >= 4'd1) out_c70 <= wrap_sub32(c70_s, wrap_mul32(s7, t0));
                    if (in_n >= 4'd2) out_c71 <= wrap_sub32(c71_s, wrap_mul32(s7, t1));
                    if (in_n >= 4'd3) out_c72 <= wrap_sub32(c72_s, wrap_mul32(s7, t2));
                    if (in_n >= 4'd4) out_c73 <= wrap_sub32(c73_s, wrap_mul32(s7, t3));
                    if (in_n >= 4'd5) out_c74 <= wrap_sub32(c74_s, wrap_mul32(s7, t4));
                    if (in_n >= 4'd6) out_c75 <= wrap_sub32(c75_s, wrap_mul32(s7, t5));
                    if (in_n >= 4'd7) out_c76 <= wrap_sub32(c76_s, wrap_mul32(s7, t6));
                    if (in_n >= 4'd8) out_c77 <= wrap_sub32(c77_s, wrap_mul32(s7, t7));
                    if (in_n >= 4'd9) out_c78 <= wrap_sub32(c78_s, wrap_mul32(s7, t8));
                    if (in_n >= 4'd10) out_c79 <= wrap_sub32(c79_s, wrap_mul32(s7, t9));
                end
                if (in_m >= 4'd9) begin
                    if (in_n >= 4'd1) out_c80 <= wrap_sub32(c80_s, wrap_mul32(s8, t0));
                    if (in_n >= 4'd2) out_c81 <= wrap_sub32(c81_s, wrap_mul32(s8, t1));
                    if (in_n >= 4'd3) out_c82 <= wrap_sub32(c82_s, wrap_mul32(s8, t2));
                    if (in_n >= 4'd4) out_c83 <= wrap_sub32(c83_s, wrap_mul32(s8, t3));
                    if (in_n >= 4'd5) out_c84 <= wrap_sub32(c84_s, wrap_mul32(s8, t4));
                    if (in_n >= 4'd6) out_c85 <= wrap_sub32(c85_s, wrap_mul32(s8, t5));
                    if (in_n >= 4'd7) out_c86 <= wrap_sub32(c86_s, wrap_mul32(s8, t6));
                    if (in_n >= 4'd8) out_c87 <= wrap_sub32(c87_s, wrap_mul32(s8, t7));
                    if (in_n >= 4'd9) out_c88 <= wrap_sub32(c88_s, wrap_mul32(s8, t8));
                    if (in_n >= 4'd10) out_c89 <= wrap_sub32(c89_s, wrap_mul32(s8, t9));
                end
                if (in_m >= 4'd10) begin
                    if (in_n >= 4'd1) out_c90 <= wrap_sub32(c90_s, wrap_mul32(s9, t0));
                    if (in_n >= 4'd2) out_c91 <= wrap_sub32(c91_s, wrap_mul32(s9, t1));
                    if (in_n >= 4'd3) out_c92 <= wrap_sub32(c92_s, wrap_mul32(s9, t2));
                    if (in_n >= 4'd4) out_c93 <= wrap_sub32(c93_s, wrap_mul32(s9, t3));
                    if (in_n >= 4'd5) out_c94 <= wrap_sub32(c94_s, wrap_mul32(s9, t4));
                    if (in_n >= 4'd6) out_c95 <= wrap_sub32(c95_s, wrap_mul32(s9, t5));
                    if (in_n >= 4'd7) out_c96 <= wrap_sub32(c96_s, wrap_mul32(s9, t6));
                    if (in_n >= 4'd8) out_c97 <= wrap_sub32(c97_s, wrap_mul32(s9, t7));
                    if (in_n >= 4'd9) out_c98 <= wrap_sub32(c98_s, wrap_mul32(s9, t8));
                    if (in_n >= 4'd10) out_c99 <= wrap_sub32(c99_s, wrap_mul32(s9, t9));
                end
            end
        end
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule