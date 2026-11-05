module compression_jpeg_transform_jpeg_fdct_2d_8x8_int (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [11:0]  in_s0,
    input  logic [11:0]  in_s1,
    input  logic [11:0]  in_s2,
    input  logic [11:0]  in_s3,
    input  logic [11:0]  in_s4,
    input  logic [11:0]  in_s5,
    input  logic [11:0]  in_s6,
    input  logic [11:0]  in_s7,
    input  logic [11:0]  in_s8,
    input  logic [11:0]  in_s9,
    input  logic [11:0]  in_s10,
    input  logic [11:0]  in_s11,
    input  logic [11:0]  in_s12,
    input  logic [11:0]  in_s13,
    input  logic [11:0]  in_s14,
    input  logic [11:0]  in_s15,
    input  logic [11:0]  in_s16,
    input  logic [11:0]  in_s17,
    input  logic [11:0]  in_s18,
    input  logic [11:0]  in_s19,
    input  logic [11:0]  in_s20,
    input  logic [11:0]  in_s21,
    input  logic [11:0]  in_s22,
    input  logic [11:0]  in_s23,
    input  logic [11:0]  in_s24,
    input  logic [11:0]  in_s25,
    input  logic [11:0]  in_s26,
    input  logic [11:0]  in_s27,
    input  logic [11:0]  in_s28,
    input  logic [11:0]  in_s29,
    input  logic [11:0]  in_s30,
    input  logic [11:0]  in_s31,
    input  logic [11:0]  in_s32,
    input  logic [11:0]  in_s33,
    input  logic [11:0]  in_s34,
    input  logic [11:0]  in_s35,
    input  logic [11:0]  in_s36,
    input  logic [11:0]  in_s37,
    input  logic [11:0]  in_s38,
    input  logic [11:0]  in_s39,
    input  logic [11:0]  in_s40,
    input  logic [11:0]  in_s41,
    input  logic [11:0]  in_s42,
    input  logic [11:0]  in_s43,
    input  logic [11:0]  in_s44,
    input  logic [11:0]  in_s45,
    input  logic [11:0]  in_s46,
    input  logic [11:0]  in_s47,
    input  logic [11:0]  in_s48,
    input  logic [11:0]  in_s49,
    input  logic [11:0]  in_s50,
    input  logic [11:0]  in_s51,
    input  logic [11:0]  in_s52,
    input  logic [11:0]  in_s53,
    input  logic [11:0]  in_s54,
    input  logic [11:0]  in_s55,
    input  logic [11:0]  in_s56,
    input  logic [11:0]  in_s57,
    input  logic [11:0]  in_s58,
    input  logic [11:0]  in_s59,
    input  logic [11:0]  in_s60,
    input  logic [11:0]  in_s61,
    input  logic [11:0]  in_s62,
    input  logic [11:0]  in_s63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_d0,
    output logic [15:0]  out_d1,
    output logic [15:0]  out_d2,
    output logic [15:0]  out_d3,
    output logic [15:0]  out_d4,
    output logic [15:0]  out_d5,
    output logic [15:0]  out_d6,
    output logic [15:0]  out_d7,
    output logic [15:0]  out_d8,
    output logic [15:0]  out_d9,
    output logic [15:0]  out_d10,
    output logic [15:0]  out_d11,
    output logic [15:0]  out_d12,
    output logic [15:0]  out_d13,
    output logic [15:0]  out_d14,
    output logic [15:0]  out_d15,
    output logic [15:0]  out_d16,
    output logic [15:0]  out_d17,
    output logic [15:0]  out_d18,
    output logic [15:0]  out_d19,
    output logic [15:0]  out_d20,
    output logic [15:0]  out_d21,
    output logic [15:0]  out_d22,
    output logic [15:0]  out_d23,
    output logic [15:0]  out_d24,
    output logic [15:0]  out_d25,
    output logic [15:0]  out_d26,
    output logic [15:0]  out_d27,
    output logic [15:0]  out_d28,
    output logic [15:0]  out_d29,
    output logic [15:0]  out_d30,
    output logic [15:0]  out_d31,
    output logic [15:0]  out_d32,
    output logic [15:0]  out_d33,
    output logic [15:0]  out_d34,
    output logic [15:0]  out_d35,
    output logic [15:0]  out_d36,
    output logic [15:0]  out_d37,
    output logic [15:0]  out_d38,
    output logic [15:0]  out_d39,
    output logic [15:0]  out_d40,
    output logic [15:0]  out_d41,
    output logic [15:0]  out_d42,
    output logic [15:0]  out_d43,
    output logic [15:0]  out_d44,
    output logic [15:0]  out_d45,
    output logic [15:0]  out_d46,
    output logic [15:0]  out_d47,
    output logic [15:0]  out_d48,
    output logic [15:0]  out_d49,
    output logic [15:0]  out_d50,
    output logic [15:0]  out_d51,
    output logic [15:0]  out_d52,
    output logic [15:0]  out_d53,
    output logic [15:0]  out_d54,
    output logic [15:0]  out_d55,
    output logic [15:0]  out_d56,
    output logic [15:0]  out_d57,
    output logic [15:0]  out_d58,
    output logic [15:0]  out_d59,
    output logic [15:0]  out_d60,
    output logic [15:0]  out_d61,
    output logic [15:0]  out_d62,
    output logic [15:0]  out_d63
);

    localparam logic signed [15:0] FIX_0_298631336 = 16'sd2446;
    localparam logic signed [15:0] FIX_0_390180644 = 16'sd3196;
    localparam logic signed [15:0] FIX_0_541196100 = 16'sd4433;
    localparam logic signed [15:0] FIX_0_765366865 = 16'sd6270;
    localparam logic signed [15:0] FIX_0_899976223 = 16'sd7373;
    localparam logic signed [15:0] FIX_1_175875602 = 16'sd9633;
    localparam logic signed [15:0] FIX_1_501321110 = 16'sd12299;
    localparam logic signed [15:0] FIX_1_847759065 = 16'sd15137;
    localparam logic signed [15:0] FIX_1_961570560 = 16'sd16069;
    localparam logic signed [15:0] FIX_2_053119869 = 16'sd16819;
    localparam logic signed [15:0] FIX_2_562915447 = 16'sd20995;
    localparam logic signed [15:0] FIX_3_072711026 = 16'sd25172;

    logic busy;
    wire input_accepted;
    wire output_consumed;

    logic [15:0] out_mem [0:63];

    function automatic logic signed [31:0] sx12(input logic [11:0] v);
        begin
            sx12 = {{20{v[11]}}, v};
        end
    endfunction

    function automatic logic signed [31:0] descale32_11(input logic signed [63:0] xv);
        logic signed [63:0] t;
        begin
            t = xv + 64'sd1024;
            descale32_11 = t >>> 11;
        end
    endfunction

    function automatic logic signed [31:0] descale32_2(input logic signed [63:0] xv);
        logic signed [63:0] t;
        begin
            t = xv + 64'sd2;
            descale32_2 = t >>> 2;
        end
    endfunction

    function automatic logic signed [31:0] descale32_15(input logic signed [63:0] xv);
        logic signed [63:0] t;
        begin
            t = xv + 64'sd16384;
            descale32_15 = t >>> 15;
        end
    endfunction

    task automatic row_pass;
        input  logic signed [31:0] ix0;
        input  logic signed [31:0] ix1;
        input  logic signed [31:0] ix2;
        input  logic signed [31:0] ix3;
        input  logic signed [31:0] ix4;
        input  logic signed [31:0] ix5;
        input  logic signed [31:0] ix6;
        input  logic signed [31:0] ix7;
        output logic signed [31:0] oy0;
        output logic signed [31:0] oy1;
        output logic signed [31:0] oy2;
        output logic signed [31:0] oy3;
        output logic signed [31:0] oy4;
        output logic signed [31:0] oy5;
        output logic signed [31:0] oy6;
        output logic signed [31:0] oy7;

        logic signed [31:0] tmp0;
        logic signed [31:0] tmp1;
        logic signed [31:0] tmp2;
        logic signed [31:0] tmp3;
        logic signed [31:0] tmp4;
        logic signed [31:0] tmp5;
        logic signed [31:0] tmp6;
        logic signed [31:0] tmp7;
        logic signed [31:0] tmp10;
        logic signed [31:0] tmp11;
        logic signed [31:0] tmp12;
        logic signed [31:0] tmp13;
        logic signed [63:0] z1;
        logic signed [63:0] z2;
        logic signed [63:0] z3;
        logic signed [63:0] z4;
        logic signed [63:0] z5;
        logic signed [63:0] acc;
        begin
            tmp0 = ix0 + ix7;
            tmp7 = ix0 - ix7;
            tmp1 = ix1 + ix6;
            tmp6 = ix1 - ix6;
            tmp2 = ix2 + ix5;
            tmp5 = ix2 - ix5;
            tmp3 = ix3 + ix4;
            tmp4 = ix3 - ix4;

            tmp10 = tmp0 + tmp3;
            tmp13 = tmp0 - tmp3;
            tmp11 = tmp1 + tmp2;
            tmp12 = tmp1 - tmp2;

            oy0 = (tmp10 + tmp11) <<< 2;
            oy4 = (tmp10 - tmp11) <<< 2;

            z1 = (tmp12 + tmp13) * FIX_0_541196100;
            acc = z1 + tmp13 * FIX_0_765366865;
            oy2 = descale32_11(acc);
            acc = z1 + tmp12 * (-FIX_1_847759065);
            oy6 = descale32_11(acc);

            z1 = tmp4 + tmp7;
            z2 = tmp5 + tmp6;
            z3 = tmp4 + tmp6;
            z4 = tmp5 + tmp7;
            z5 = (z3 + z4) * FIX_1_175875602;

            tmp4 = tmp4 * FIX_0_298631336;
            tmp5 = tmp5 * FIX_2_053119869;
            tmp6 = tmp6 * FIX_3_072711026;
            tmp7 = tmp7 * FIX_1_501321110;
            z1 = z1 * (-FIX_0_899976223);
            z2 = z2 * (-FIX_2_562915447);
            z3 = z3 * (-FIX_1_961570560);
            z4 = z4 * (-FIX_0_390180644);

            z3 = z3 + z5;
            z4 = z4 + z5;

            acc = tmp4 + z1 + z3;
            oy7 = descale32_11(acc);
            acc = tmp5 + z2 + z4;
            oy5 = descale32_11(acc);
            acc = tmp6 + z2 + z3;
            oy3 = descale32_11(acc);
            acc = tmp7 + z1 + z4;
            oy1 = descale32_11(acc);
        end
    endtask

    task automatic col_pass;
        input  logic signed [31:0] ix0;
        input  logic signed [31:0] ix1;
        input  logic signed [31:0] ix2;
        input  logic signed [31:0] ix3;
        input  logic signed [31:0] ix4;
        input  logic signed [31:0] ix5;
        input  logic signed [31:0] ix6;
        input  logic signed [31:0] ix7;
        output logic signed [31:0] oy0;
        output logic signed [31:0] oy1;
        output logic signed [31:0] oy2;
        output logic signed [31:0] oy3;
        output logic signed [31:0] oy4;
        output logic signed [31:0] oy5;
        output logic signed [31:0] oy6;
        output logic signed [31:0] oy7;

        logic signed [31:0] tmp0;
        logic signed [31:0] tmp1;
        logic signed [31:0] tmp2;
        logic signed [31:0] tmp3;
        logic signed [31:0] tmp4;
        logic signed [31:0] tmp5;
        logic signed [31:0] tmp6;
        logic signed [31:0] tmp7;
        logic signed [31:0] tmp10;
        logic signed [31:0] tmp11;
        logic signed [31:0] tmp12;
        logic signed [31:0] tmp13;
        logic signed [63:0] z1;
        logic signed [63:0] z2;
        logic signed [63:0] z3;
        logic signed [63:0] z4;
        logic signed [63:0] z5;
        logic signed [63:0] acc;
        begin
            tmp0 = ix0 + ix7;
            tmp7 = ix0 - ix7;
            tmp1 = ix1 + ix6;
            tmp6 = ix1 - ix6;
            tmp2 = ix2 + ix5;
            tmp5 = ix2 - ix5;
            tmp3 = ix3 + ix4;
            tmp4 = ix3 - ix4;

            tmp10 = tmp0 + tmp3;
            tmp13 = tmp0 - tmp3;
            tmp11 = tmp1 + tmp2;
            tmp12 = tmp1 - tmp2;

            acc = tmp10 + tmp11;
            oy0 = descale32_2(acc);
            acc = tmp10 - tmp11;
            oy4 = descale32_2(acc);

            z1 = (tmp12 + tmp13) * FIX_0_541196100;
            acc = z1 + tmp13 * FIX_0_765366865;
            oy2 = descale32_15(acc);
            acc = z1 + tmp12 * (-FIX_1_847759065);
            oy6 = descale32_15(acc);

            z1 = tmp4 + tmp7;
            z2 = tmp5 + tmp6;
            z3 = tmp4 + tmp6;
            z4 = tmp5 + tmp7;
            z5 = (z3 + z4) * FIX_1_175875602;

            tmp4 = tmp4 * FIX_0_298631336;
            tmp5 = tmp5 * FIX_2_053119869;
            tmp6 = tmp6 * FIX_3_072711026;
            tmp7 = tmp7 * FIX_1_501321110;
            z1 = z1 * (-FIX_0_899976223);
            z2 = z2 * (-FIX_2_562915447);
            z3 = z3 * (-FIX_1_961570560);
            z4 = z4 * (-FIX_0_390180644);

            z3 = z3 + z5;
            z4 = z4 + z5;

            acc = tmp4 + z1 + z3;
            oy7 = descale32_15(acc);
            acc = tmp5 + z2 + z4;
            oy5 = descale32_15(acc);
            acc = tmp6 + z2 + z3;
            oy3 = descale32_15(acc);
            acc = tmp7 + z1 + z4;
            oy1 = descale32_15(acc);
        end
    endtask

    wire signed [31:0] s0  = sx12(in_s0);
    wire signed [31:0] s1  = sx12(in_s1);
    wire signed [31:0] s2  = sx12(in_s2);
    wire signed [31:0] s3  = sx12(in_s3);
    wire signed [31:0] s4  = sx12(in_s4);
    wire signed [31:0] s5  = sx12(in_s5);
    wire signed [31:0] s6  = sx12(in_s6);
    wire signed [31:0] s7  = sx12(in_s7);
    wire signed [31:0] s8  = sx12(in_s8);
    wire signed [31:0] s9  = sx12(in_s9);
    wire signed [31:0] s10 = sx12(in_s10);
    wire signed [31:0] s11 = sx12(in_s11);
    wire signed [31:0] s12 = sx12(in_s12);
    wire signed [31:0] s13 = sx12(in_s13);
    wire signed [31:0] s14 = sx12(in_s14);
    wire signed [31:0] s15 = sx12(in_s15);
    wire signed [31:0] s16 = sx12(in_s16);
    wire signed [31:0] s17 = sx12(in_s17);
    wire signed [31:0] s18 = sx12(in_s18);
    wire signed [31:0] s19 = sx12(in_s19);
    wire signed [31:0] s20 = sx12(in_s20);
    wire signed [31:0] s21 = sx12(in_s21);
    wire signed [31:0] s22 = sx12(in_s22);
    wire signed [31:0] s23 = sx12(in_s23);
    wire signed [31:0] s24 = sx12(in_s24);
    wire signed [31:0] s25 = sx12(in_s25);
    wire signed [31:0] s26 = sx12(in_s26);
    wire signed [31:0] s27 = sx12(in_s27);
    wire signed [31:0] s28 = sx12(in_s28);
    wire signed [31:0] s29 = sx12(in_s29);
    wire signed [31:0] s30 = sx12(in_s30);
    wire signed [31:0] s31 = sx12(in_s31);
    wire signed [31:0] s32 = sx12(in_s32);
    wire signed [31:0] s33 = sx12(in_s33);
    wire signed [31:0] s34 = sx12(in_s34);
    wire signed [31:0] s35 = sx12(in_s35);
    wire signed [31:0] s36 = sx12(in_s36);
    wire signed [31:0] s37 = sx12(in_s37);
    wire signed [31:0] s38 = sx12(in_s38);
    wire signed [31:0] s39 = sx12(in_s39);
    wire signed [31:0] s40 = sx12(in_s40);
    wire signed [31:0] s41 = sx12(in_s41);
    wire signed [31:0] s42 = sx12(in_s42);
    wire signed [31:0] s43 = sx12(in_s43);
    wire signed [31:0] s44 = sx12(in_s44);
    wire signed [31:0] s45 = sx12(in_s45);
    wire signed [31:0] s46 = sx12(in_s46);
    wire signed [31:0] s47 = sx12(in_s47);
    wire signed [31:0] s48 = sx12(in_s48);
    wire signed [31:0] s49 = sx12(in_s49);
    wire signed [31:0] s50 = sx12(in_s50);
    wire signed [31:0] s51 = sx12(in_s51);
    wire signed [31:0] s52 = sx12(in_s52);
    wire signed [31:0] s53 = sx12(in_s53);
    wire signed [31:0] s54 = sx12(in_s54);
    wire signed [31:0] s55 = sx12(in_s55);
    wire signed [31:0] s56 = sx12(in_s56);
    wire signed [31:0] s57 = sx12(in_s57);
    wire signed [31:0] s58 = sx12(in_s58);
    wire signed [31:0] s59 = sx12(in_s59);
    wire signed [31:0] s60 = sx12(in_s60);
    wire signed [31:0] s61 = sx12(in_s61);
    wire signed [31:0] s62 = sx12(in_s62);
    wire signed [31:0] s63 = sx12(in_s63);

    logic signed [31:0] r0_0, r0_1, r0_2, r0_3, r0_4, r0_5, r0_6, r0_7;
    logic signed [31:0] r1_0, r1_1, r1_2, r1_3, r1_4, r1_5, r1_6, r1_7;
    logic signed [31:0] r2_0, r2_1, r2_2, r2_3, r2_4, r2_5, r2_6, r2_7;
    logic signed [31:0] r3_0, r3_1, r3_2, r3_3, r3_4, r3_5, r3_6, r3_7;
    logic signed [31:0] r4_0, r4_1, r4_2, r4_3, r4_4, r4_5, r4_6, r4_7;
    logic signed [31:0] r5_0, r5_1, r5_2, r5_3, r5_4, r5_5, r5_6, r5_7;
    logic signed [31:0] r6_0, r6_1, r6_2, r6_3, r6_4, r6_5, r6_6, r6_7;
    logic signed [31:0] r7_0, r7_1, r7_2, r7_3, r7_4, r7_5, r7_6, r7_7;

    logic signed [31:0] d0_c0, d1_c0, d2_c0, d3_c0, d4_c0, d5_c0, d6_c0, d7_c0;
    logic signed [31:0] d0_c1, d1_c1, d2_c1, d3_c1, d4_c1, d5_c1, d6_c1, d7_c1;
    logic signed [31:0] d0_c2, d1_c2, d2_c2, d3_c2, d4_c2, d5_c2, d6_c2, d7_c2;
    logic signed [31:0] d0_c3, d1_c3, d2_c3, d3_c3, d4_c3, d5_c3, d6_c3, d7_c3;
    logic signed [31:0] d0_c4, d1_c4, d2_c4, d3_c4, d4_c4, d5_c4, d6_c4, d7_c4;
    logic signed [31:0] d0_c5, d1_c5, d2_c5, d3_c5, d4_c5, d5_c5, d6_c5, d7_c5;
    logic signed [31:0] d0_c6, d1_c6, d2_c6, d3_c6, d4_c6, d5_c6, d6_c6, d7_c6;
    logic signed [31:0] d0_c7, d1_c7, d2_c7, d3_c7, d4_c7, d5_c7, d6_c7, d7_c7;

    always_comb begin
        row_pass(s0,  s1,  s2,  s3,  s4,  s5,  s6,  s7,  r0_0, r0_1, r0_2, r0_3, r0_4, r0_5, r0_6, r0_7);
        row_pass(s8,  s9,  s10, s11, s12, s13, s14, s15, r1_0, r1_1, r1_2, r1_3, r1_4, r1_5, r1_6, r1_7);
        row_pass(s16, s17, s18, s19, s20, s21, s22, s23, r2_0, r2_1, r2_2, r2_3, r2_4, r2_5, r2_6, r2_7);
        row_pass(s24, s25, s26, s27, s28, s29, s30, s31, r3_0, r3_1, r3_2, r3_3, r3_4, r3_5, r3_6, r3_7);
        row_pass(s32, s33, s34, s35, s36, s37, s38, s39, r4_0, r4_1, r4_2, r4_3, r4_4, r4_5, r4_6, r4_7);
        row_pass(s40, s41, s42, s43, s44, s45, s46, s47, r5_0, r5_1, r5_2, r5_3, r5_4, r5_5, r5_6, r5_7);
        row_pass(s48, s49, s50, s51, s52, s53, s54, s55, r6_0, r6_1, r6_2, r6_3, r6_4, r6_5, r6_6, r6_7);
        row_pass(s56, s57, s58, s59, s60, s61, s62, s63, r7_0, r7_1, r7_2, r7_3, r7_4, r7_5, r7_6, r7_7);

        col_pass(r0_0, r1_0, r2_0, r3_0, r4_0, r5_0, r6_0, r7_0, d0_c0, d1_c0, d2_c0, d3_c0, d4_c0, d5_c0, d6_c0, d7_c0);
        col_pass(r0_1, r1_1, r2_1, r3_1, r4_1, r5_1, r6_1, r7_1, d0_c1, d1_c1, d2_c1, d3_c1, d4_c1, d5_c1, d6_c1, d7_c1);
        col_pass(r0_2, r1_2, r2_2, r3_2, r4_2, r5_2, r6_2, r7_2, d0_c2, d1_c2, d2_c2, d3_c2, d4_c2, d5_c2, d6_c2, d7_c2);
        col_pass(r0_3, r1_3, r2_3, r3_3, r4_3, r5_3, r6_3, r7_3, d0_c3, d1_c3, d2_c3, d3_c3, d4_c3, d5_c3, d6_c3, d7_c3);
        col_pass(r0_4, r1_4, r2_4, r3_4, r4_4, r5_4, r6_4, r7_4, d0_c4, d1_c4, d2_c4, d3_c4, d4_c4, d5_c4, d6_c4, d7_c4);
        col_pass(r0_5, r1_5, r2_5, r3_5, r4_5, r5_5, r6_5, r7_5, d0_c5, d1_c5, d2_c5, d3_c5, d4_c5, d5_c5, d6_c5, d7_c5);
        col_pass(r0_6, r1_6, r2_6, r3_6, r4_6, r5_6, r6_6, r7_6, d0_c6, d1_c6, d2_c6, d3_c6, d4_c6, d5_c6, d6_c6, d7_c6);
        col_pass(r0_7, r1_7, r2_7, r3_7, r4_7, r5_7, r6_7, r7_7, d0_c7, d1_c7, d2_c7, d3_c7, d4_c7, d5_c7, d6_c7, d7_c7);
    end

    assign output_consumed = out_valid & out_ready;
    assign input_accepted  = in_valid & in_ready;
    assign in_ready        = ~busy | output_consumed;
    assign out_valid       = busy;

    assign out_d0  = out_mem[0];
    assign out_d1  = out_mem[1];
    assign out_d2  = out_mem[2];
    assign out_d3  = out_mem[3];
    assign out_d4  = out_mem[4];
    assign out_d5  = out_mem[5];
    assign out_d6  = out_mem[6];
    assign out_d7  = out_mem[7];
    assign out_d8  = out_mem[8];
    assign out_d9  = out_mem[9];
    assign out_d10 = out_mem[10];
    assign out_d11 = out_mem[11];
    assign out_d12 = out_mem[12];
    assign out_d13 = out_mem[13];
    assign out_d14 = out_mem[14];
    assign out_d15 = out_mem[15];
    assign out_d16 = out_mem[16];
    assign out_d17 = out_mem[17];
    assign out_d18 = out_mem[18];
    assign out_d19 = out_mem[19];
    assign out_d20 = out_mem[20];
    assign out_d21 = out_mem[21];
    assign out_d22 = out_mem[22];
    assign out_d23 = out_mem[23];
    assign out_d24 = out_mem[24];
    assign out_d25 = out_mem[25];
    assign out_d26 = out_mem[26];
    assign out_d27 = out_mem[27];
    assign out_d28 = out_mem[28];
    assign out_d29 = out_mem[29];
    assign out_d30 = out_mem[30];
    assign out_d31 = out_mem[31];
    assign out_d32 = out_mem[32];
    assign out_d33 = out_mem[33];
    assign out_d34 = out_mem[34];
    assign out_d35 = out_mem[35];
    assign out_d36 = out_mem[36];
    assign out_d37 = out_mem[37];
    assign out_d38 = out_mem[38];
    assign out_d39 = out_mem[39];
    assign out_d40 = out_mem[40];
    assign out_d41 = out_mem[41];
    assign out_d42 = out_mem[42];
    assign out_d43 = out_mem[43];
    assign out_d44 = out_mem[44];
    assign out_d45 = out_mem[45];
    assign out_d46 = out_mem[46];
    assign out_d47 = out_mem[47];
    assign out_d48 = out_mem[48];
    assign out_d49 = out_mem[49];
    assign out_d50 = out_mem[50];
    assign out_d51 = out_mem[51];
    assign out_d52 = out_mem[52];
    assign out_d53 = out_mem[53];
    assign out_d54 = out_mem[54];
    assign out_d55 = out_mem[55];
    assign out_d56 = out_mem[56];
    assign out_d57 = out_mem[57];
    assign out_d58 = out_mem[58];
    assign out_d59 = out_mem[59];
    assign out_d60 = out_mem[60];
    assign out_d61 = out_mem[61];
    assign out_d62 = out_mem[62];
    assign out_d63 = out_mem[63];

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            out_mem[0]  <= 16'd0;
            out_mem[1]  <= 16'd0;
            out_mem[2]  <= 16'd0;
            out_mem[3]  <= 16'd0;
            out_mem[4]  <= 16'd0;
            out_mem[5]  <= 16'd0;
            out_mem[6]  <= 16'd0;
            out_mem[7]  <= 16'd0;
            out_mem[8]  <= 16'd0;
            out_mem[9]  <= 16'd0;
            out_mem[10] <= 16'd0;
            out_mem[11] <= 16'd0;
            out_mem[12] <= 16'd0;
            out_mem[13] <= 16'd0;
            out_mem[14] <= 16'd0;
            out_mem[15] <= 16'd0;
            out_mem[16] <= 16'd0;
            out_mem[17] <= 16'd0;
            out_mem[18] <= 16'd0;
            out_mem[19] <= 16'd0;
            out_mem[20] <= 16'd0;
            out_mem[21] <= 16'd0;
            out_mem[22] <= 16'd0;
            out_mem[23] <= 16'd0;
            out_mem[24] <= 16'd0;
            out_mem[25] <= 16'd0;
            out_mem[26] <= 16'd0;
            out_mem[27] <= 16'd0;
            out_mem[28] <= 16'd0;
            out_mem[29] <= 16'd0;
            out_mem[30] <= 16'd0;
            out_mem[31] <= 16'd0;
            out_mem[32] <= 16'd0;
            out_mem[33] <= 16'd0;
            out_mem[34] <= 16'd0;
            out_mem[35] <= 16'd0;
            out_mem[36] <= 16'd0;
            out_mem[37] <= 16'd0;
            out_mem[38] <= 16'd0;
            out_mem[39] <= 16'd0;
            out_mem[40] <= 16'd0;
            out_mem[41] <= 16'd0;
            out_mem[42] <= 16'd0;
            out_mem[43] <= 16'd0;
            out_mem[44] <= 16'd0;
            out_mem[45] <= 16'd0;
            out_mem[46] <= 16'd0;
            out_mem[47] <= 16'd0;
            out_mem[48] <= 16'd0;
            out_mem[49] <= 16'd0;
            out_mem[50] <= 16'd0;
            out_mem[51] <= 16'd0;
            out_mem[52] <= 16'd0;
            out_mem[53] <= 16'd0;
            out_mem[54] <= 16'd0;
            out_mem[55] <= 16'd0;
            out_mem[56] <= 16'd0;
            out_mem[57] <= 16'd0;
            out_mem[58] <= 16'd0;
            out_mem[59] <= 16'd0;
            out_mem[60] <= 16'd0;
            out_mem[61] <= 16'd0;
            out_mem[62] <= 16'd0;
            out_mem[63] <= 16'd0;
        end else if (input_accepted) begin
            out_mem[0]  <= d0_c0[15:0];
            out_mem[1]  <= d0_c1[15:0];
            out_mem[2]  <= d0_c2[15:0];
            out_mem[3]  <= d0_c3[15:0];
            out_mem[4]  <= d0_c4[15:0];
            out_mem[5]  <= d0_c5[15:0];
            out_mem[6]  <= d0_c6[15:0];
            out_mem[7]  <= d0_c7[15:0];
            out_mem[8]  <= d1_c0[15:0];
            out_mem[9]  <= d1_c1[15:0];
            out_mem[10] <= d1_c2[15:0];
            out_mem[11] <= d1_c3[15:0];
            out_mem[12] <= d1_c4[15:0];
            out_mem[13] <= d1_c5[15:0];
            out_mem[14] <= d1_c6[15:0];
            out_mem[15] <= d1_c7[15:0];
            out_mem[16] <= d2_c0[15:0];
            out_mem[17] <= d2_c1[15:0];
            out_mem[18] <= d2_c2[15:0];
            out_mem[19] <= d2_c3[15:0];
            out_mem[20] <= d2_c4[15:0];
            out_mem[21] <= d2_c5[15:0];
            out_mem[22] <= d2_c6[15:0];
            out_mem[23] <= d2_c7[15:0];
            out_mem[24] <= d3_c0[15:0];
            out_mem[25] <= d3_c1[15:0];
            out_mem[26] <= d3_c2[15:0];
            out_mem[27] <= d3_c3[15:0];
            out_mem[28] <= d3_c4[15:0];
            out_mem[29] <= d3_c5[15:0];
            out_mem[30] <= d3_c6[15:0];
            out_mem[31] <= d3_c7[15:0];
            out_mem[32] <= d4_c0[15:0];
            out_mem[33] <= d4_c1[15:0];
            out_mem[34] <= d4_c2[15:0];
            out_mem[35] <= d4_c3[15:0];
            out_mem[36] <= d4_c4[15:0];
            out_mem[37] <= d4_c5[15:0];
            out_mem[38] <= d4_c6[15:0];
            out_mem[39] <= d4_c7[15:0];
            out_mem[40] <= d5_c0[15:0];
            out_mem[41] <= d5_c1[15:0];
            out_mem[42] <= d5_c2[15:0];
            out_mem[43] <= d5_c3[15:0];
            out_mem[44] <= d5_c4[15:0];
            out_mem[45] <= d5_c5[15:0];
            out_mem[46] <= d5_c6[15:0];
            out_mem[47] <= d5_c7[15:0];
            out_mem[48] <= d6_c0[15:0];
            out_mem[49] <= d6_c1[15:0];
            out_mem[50] <= d6_c2[15:0];
            out_mem[51] <= d6_c3[15:0];
            out_mem[52] <= d6_c4[15:0];
            out_mem[53] <= d6_c5[15:0];
            out_mem[54] <= d6_c6[15:0];
            out_mem[55] <= d6_c7[15:0];
            out_mem[56] <= d7_c0[15:0];
            out_mem[57] <= d7_c1[15:0];
            out_mem[58] <= d7_c2[15:0];
            out_mem[59] <= d7_c3[15:0];
            out_mem[60] <= d7_c4[15:0];
            out_mem[61] <= d7_c5[15:0];
            out_mem[62] <= d7_c6[15:0];
            out_mem[63] <= d7_c7[15:0];
            busy <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule