module compression_jpeg_transform_jpeg_idct_2d_8x8_int (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_coef_0,
    input  logic [15:0]  in_coef_1,
    input  logic [15:0]  in_coef_2,
    input  logic [15:0]  in_coef_3,
    input  logic [15:0]  in_coef_4,
    input  logic [15:0]  in_coef_5,
    input  logic [15:0]  in_coef_6,
    input  logic [15:0]  in_coef_7,
    input  logic [15:0]  in_coef_8,
    input  logic [15:0]  in_coef_9,
    input  logic [15:0]  in_coef_10,
    input  logic [15:0]  in_coef_11,
    input  logic [15:0]  in_coef_12,
    input  logic [15:0]  in_coef_13,
    input  logic [15:0]  in_coef_14,
    input  logic [15:0]  in_coef_15,
    input  logic [15:0]  in_coef_16,
    input  logic [15:0]  in_coef_17,
    input  logic [15:0]  in_coef_18,
    input  logic [15:0]  in_coef_19,
    input  logic [15:0]  in_coef_20,
    input  logic [15:0]  in_coef_21,
    input  logic [15:0]  in_coef_22,
    input  logic [15:0]  in_coef_23,
    input  logic [15:0]  in_coef_24,
    input  logic [15:0]  in_coef_25,
    input  logic [15:0]  in_coef_26,
    input  logic [15:0]  in_coef_27,
    input  logic [15:0]  in_coef_28,
    input  logic [15:0]  in_coef_29,
    input  logic [15:0]  in_coef_30,
    input  logic [15:0]  in_coef_31,
    input  logic [15:0]  in_coef_32,
    input  logic [15:0]  in_coef_33,
    input  logic [15:0]  in_coef_34,
    input  logic [15:0]  in_coef_35,
    input  logic [15:0]  in_coef_36,
    input  logic [15:0]  in_coef_37,
    input  logic [15:0]  in_coef_38,
    input  logic [15:0]  in_coef_39,
    input  logic [15:0]  in_coef_40,
    input  logic [15:0]  in_coef_41,
    input  logic [15:0]  in_coef_42,
    input  logic [15:0]  in_coef_43,
    input  logic [15:0]  in_coef_44,
    input  logic [15:0]  in_coef_45,
    input  logic [15:0]  in_coef_46,
    input  logic [15:0]  in_coef_47,
    input  logic [15:0]  in_coef_48,
    input  logic [15:0]  in_coef_49,
    input  logic [15:0]  in_coef_50,
    input  logic [15:0]  in_coef_51,
    input  logic [15:0]  in_coef_52,
    input  logic [15:0]  in_coef_53,
    input  logic [15:0]  in_coef_54,
    input  logic [15:0]  in_coef_55,
    input  logic [15:0]  in_coef_56,
    input  logic [15:0]  in_coef_57,
    input  logic [15:0]  in_coef_58,
    input  logic [15:0]  in_coef_59,
    input  logic [15:0]  in_coef_60,
    input  logic [15:0]  in_coef_61,
    input  logic [15:0]  in_coef_62,
    input  logic [15:0]  in_coef_63,

    input  logic [15:0]  in_quant_0,
    input  logic [15:0]  in_quant_1,
    input  logic [15:0]  in_quant_2,
    input  logic [15:0]  in_quant_3,
    input  logic [15:0]  in_quant_4,
    input  logic [15:0]  in_quant_5,
    input  logic [15:0]  in_quant_6,
    input  logic [15:0]  in_quant_7,
    input  logic [15:0]  in_quant_8,
    input  logic [15:0]  in_quant_9,
    input  logic [15:0]  in_quant_10,
    input  logic [15:0]  in_quant_11,
    input  logic [15:0]  in_quant_12,
    input  logic [15:0]  in_quant_13,
    input  logic [15:0]  in_quant_14,
    input  logic [15:0]  in_quant_15,
    input  logic [15:0]  in_quant_16,
    input  logic [15:0]  in_quant_17,
    input  logic [15:0]  in_quant_18,
    input  logic [15:0]  in_quant_19,
    input  logic [15:0]  in_quant_20,
    input  logic [15:0]  in_quant_21,
    input  logic [15:0]  in_quant_22,
    input  logic [15:0]  in_quant_23,
    input  logic [15:0]  in_quant_24,
    input  logic [15:0]  in_quant_25,
    input  logic [15:0]  in_quant_26,
    input  logic [15:0]  in_quant_27,
    input  logic [15:0]  in_quant_28,
    input  logic [15:0]  in_quant_29,
    input  logic [15:0]  in_quant_30,
    input  logic [15:0]  in_quant_31,
    input  logic [15:0]  in_quant_32,
    input  logic [15:0]  in_quant_33,
    input  logic [15:0]  in_quant_34,
    input  logic [15:0]  in_quant_35,
    input  logic [15:0]  in_quant_36,
    input  logic [15:0]  in_quant_37,
    input  logic [15:0]  in_quant_38,
    input  logic [15:0]  in_quant_39,
    input  logic [15:0]  in_quant_40,
    input  logic [15:0]  in_quant_41,
    input  logic [15:0]  in_quant_42,
    input  logic [15:0]  in_quant_43,
    input  logic [15:0]  in_quant_44,
    input  logic [15:0]  in_quant_45,
    input  logic [15:0]  in_quant_46,
    input  logic [15:0]  in_quant_47,
    input  logic [15:0]  in_quant_48,
    input  logic [15:0]  in_quant_49,
    input  logic [15:0]  in_quant_50,
    input  logic [15:0]  in_quant_51,
    input  logic [15:0]  in_quant_52,
    input  logic [15:0]  in_quant_53,
    input  logic [15:0]  in_quant_54,
    input  logic [15:0]  in_quant_55,
    input  logic [15:0]  in_quant_56,
    input  logic [15:0]  in_quant_57,
    input  logic [15:0]  in_quant_58,
    input  logic [15:0]  in_quant_59,
    input  logic [15:0]  in_quant_60,
    input  logic [15:0]  in_quant_61,
    input  logic [15:0]  in_quant_62,
    input  logic [15:0]  in_quant_63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_pix_0,
    output logic [7:0]   out_pix_1,
    output logic [7:0]   out_pix_2,
    output logic [7:0]   out_pix_3,
    output logic [7:0]   out_pix_4,
    output logic [7:0]   out_pix_5,
    output logic [7:0]   out_pix_6,
    output logic [7:0]   out_pix_7,
    output logic [7:0]   out_pix_8,
    output logic [7:0]   out_pix_9,
    output logic [7:0]   out_pix_10,
    output logic [7:0]   out_pix_11,
    output logic [7:0]   out_pix_12,
    output logic [7:0]   out_pix_13,
    output logic [7:0]   out_pix_14,
    output logic [7:0]   out_pix_15,
    output logic [7:0]   out_pix_16,
    output logic [7:0]   out_pix_17,
    output logic [7:0]   out_pix_18,
    output logic [7:0]   out_pix_19,
    output logic [7:0]   out_pix_20,
    output logic [7:0]   out_pix_21,
    output logic [7:0]   out_pix_22,
    output logic [7:0]   out_pix_23,
    output logic [7:0]   out_pix_24,
    output logic [7:0]   out_pix_25,
    output logic [7:0]   out_pix_26,
    output logic [7:0]   out_pix_27,
    output logic [7:0]   out_pix_28,
    output logic [7:0]   out_pix_29,
    output logic [7:0]   out_pix_30,
    output logic [7:0]   out_pix_31,
    output logic [7:0]   out_pix_32,
    output logic [7:0]   out_pix_33,
    output logic [7:0]   out_pix_34,
    output logic [7:0]   out_pix_35,
    output logic [7:0]   out_pix_36,
    output logic [7:0]   out_pix_37,
    output logic [7:0]   out_pix_38,
    output logic [7:0]   out_pix_39,
    output logic [7:0]   out_pix_40,
    output logic [7:0]   out_pix_41,
    output logic [7:0]   out_pix_42,
    output logic [7:0]   out_pix_43,
    output logic [7:0]   out_pix_44,
    output logic [7:0]   out_pix_45,
    output logic [7:0]   out_pix_46,
    output logic [7:0]   out_pix_47,
    output logic [7:0]   out_pix_48,
    output logic [7:0]   out_pix_49,
    output logic [7:0]   out_pix_50,
    output logic [7:0]   out_pix_51,
    output logic [7:0]   out_pix_52,
    output logic [7:0]   out_pix_53,
    output logic [7:0]   out_pix_54,
    output logic [7:0]   out_pix_55,
    output logic [7:0]   out_pix_56,
    output logic [7:0]   out_pix_57,
    output logic [7:0]   out_pix_58,
    output logic [7:0]   out_pix_59,
    output logic [7:0]   out_pix_60,
    output logic [7:0]   out_pix_61,
    output logic [7:0]   out_pix_62,
    output logic [7:0]   out_pix_63
);

localparam integer CONST_BITS = 13;
localparam integer PASS1_BITS = 2;

localparam signed [31:0] FIX_0_298631336 = 32'sd2446;
localparam signed [31:0] FIX_0_390180644 = 32'sd3196;
localparam signed [31:0] FIX_0_541196100 = 32'sd4433;
localparam signed [31:0] FIX_0_765366865 = 32'sd6270;
localparam signed [31:0] FIX_0_899976223 = 32'sd7373;
localparam signed [31:0] FIX_1_175875602 = 32'sd9633;
localparam signed [31:0] FIX_1_501321110 = 32'sd12299;
localparam signed [31:0] FIX_1_847759065 = 32'sd15137;
localparam signed [31:0] FIX_1_961570560 = 32'sd16069;
localparam signed [31:0] FIX_2_053119869 = 32'sd16819;
localparam signed [31:0] FIX_2_562915447 = 32'sd20995;
localparam signed [31:0] FIX_3_072711026 = 32'sd25172;

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;
assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic signed [63:0] descale_fn(input signed [63:0] x, input integer n);
    reg signed [63:0] t;
    begin
        t = x + (64'sd1 <<< (n - 1));
        descale_fn = t >>> n;
    end
endfunction

function automatic [7:0] clamp_u8_fn(input signed [63:0] v);
    begin
        if (v < 0)
            clamp_u8_fn = 8'd0;
        else if (v > 255)
            clamp_u8_fn = 8'd255;
        else
            clamp_u8_fn = v[7:0];
    end
endfunction

function automatic signed [63:0] deq_fn(input logic [15:0] c, input logic [15:0] q);
    reg signed [63:0] cs;
    reg signed [63:0] qs;
    begin
        cs = $signed(c);
        qs = {48'd0, q};
        deq_fn = cs * qs;
    end
endfunction

task automatic idct_pass1_task(
    input  signed [63:0] x0,
    input  signed [63:0] x1,
    input  signed [63:0] x2,
    input  signed [63:0] x3,
    input  signed [63:0] x4,
    input  signed [63:0] x5,
    input  signed [63:0] x6,
    input  signed [63:0] x7,
    output signed [63:0] o0,
    output signed [63:0] o1,
    output signed [63:0] o2,
    output signed [63:0] o3,
    output signed [63:0] o4,
    output signed [63:0] o5,
    output signed [63:0] o6,
    output signed [63:0] o7
);
    reg signed [63:0] z1;
    reg signed [63:0] z2;
    reg signed [63:0] z3;
    reg signed [63:0] z4;
    reg signed [63:0] z5;
    reg signed [63:0] tmp0;
    reg signed [63:0] tmp1;
    reg signed [63:0] tmp2;
    reg signed [63:0] tmp3;
    reg signed [63:0] tmp10;
    reg signed [63:0] tmp11;
    reg signed [63:0] tmp12;
    reg signed [63:0] tmp13;
    reg signed [63:0] dcval;
    begin
        if ((x1 == 0) && (x2 == 0) && (x3 == 0) && (x4 == 0) && (x5 == 0) && (x6 == 0) && (x7 == 0)) begin
            dcval = x0 <<< PASS1_BITS;
            o0 = dcval; o1 = dcval; o2 = dcval; o3 = dcval;
            o4 = dcval; o5 = dcval; o6 = dcval; o7 = dcval;
        end else begin
            z2 = x2;
            z3 = x6;
            z1 = (z2 + z3) * FIX_0_541196100;
            tmp2 = z1 + z3 * (-FIX_1_847759065);
            tmp3 = z1 + z2 * FIX_0_765366865;

            z2 = x0;
            z3 = x4;
            tmp0 = (z2 + z3) <<< CONST_BITS;
            tmp1 = (z2 - z3) <<< CONST_BITS;

            tmp10 = tmp0 + tmp3;
            tmp13 = tmp0 - tmp3;
            tmp11 = tmp1 + tmp2;
            tmp12 = tmp1 - tmp2;

            tmp0 = x7;
            tmp1 = x5;
            tmp2 = x3;
            tmp3 = x1;

            z1 = tmp0 + tmp3;
            z2 = tmp1 + tmp2;
            z3 = tmp0 + tmp2;
            z4 = tmp1 + tmp3;
            z5 = (z3 + z4) * FIX_1_175875602;

            tmp0 = tmp0 * FIX_0_298631336;
            tmp1 = tmp1 * FIX_2_053119869;
            tmp2 = tmp2 * FIX_3_072711026;
            tmp3 = tmp3 * FIX_1_501321110;
            z1 = z1 * (-FIX_0_899976223);
            z2 = z2 * (-FIX_2_562915447);
            z3 = z3 * (-FIX_1_961570560);
            z4 = z4 * (-FIX_0_390180644);

            z3 = z3 + z5;
            z4 = z4 + z5;

            tmp0 = tmp0 + z1 + z3;
            tmp1 = tmp1 + z2 + z4;
            tmp2 = tmp2 + z2 + z3;
            tmp3 = tmp3 + z1 + z4;

            o0 = descale_fn(tmp10 + tmp3, CONST_BITS - PASS1_BITS);
            o1 = descale_fn(tmp11 + tmp2, CONST_BITS - PASS1_BITS);
            o2 = descale_fn(tmp12 + tmp1, CONST_BITS - PASS1_BITS);
            o3 = descale_fn(tmp13 + tmp0, CONST_BITS - PASS1_BITS);
            o4 = descale_fn(tmp13 - tmp0, CONST_BITS - PASS1_BITS);
            o5 = descale_fn(tmp12 - tmp1, CONST_BITS - PASS1_BITS);
            o6 = descale_fn(tmp11 - tmp2, CONST_BITS - PASS1_BITS);
            o7 = descale_fn(tmp10 - tmp3, CONST_BITS - PASS1_BITS);
        end
    end
endtask

task automatic idct_pass2_task(
    input  signed [63:0] w0,
    input  signed [63:0] w1,
    input  signed [63:0] w2,
    input  signed [63:0] w3,
    input  signed [63:0] w4,
    input  signed [63:0] w5,
    input  signed [63:0] w6,
    input  signed [63:0] w7,
    output [7:0] p0,
    output [7:0] p1,
    output [7:0] p2,
    output [7:0] p3,
    output [7:0] p4,
    output [7:0] p5,
    output [7:0] p6,
    output [7:0] p7
);
    reg signed [63:0] z1;
    reg signed [63:0] z2;
    reg signed [63:0] z3;
    reg signed [63:0] z4;
    reg signed [63:0] z5;
    reg signed [63:0] tmp0;
    reg signed [63:0] tmp1;
    reg signed [63:0] tmp2;
    reg signed [63:0] tmp3;
    reg signed [63:0] tmp10;
    reg signed [63:0] tmp11;
    reg signed [63:0] tmp12;
    reg signed [63:0] tmp13;
    reg signed [63:0] y0;
    reg signed [63:0] y1;
    reg signed [63:0] y2;
    reg signed [63:0] y3;
    reg signed [63:0] y4;
    reg signed [63:0] y5;
    reg signed [63:0] y6;
    reg signed [63:0] y7;
    reg signed [63:0] sval;
    begin
        if ((w1 == 0) && (w2 == 0) && (w3 == 0) && (w4 == 0) && (w5 == 0) && (w6 == 0) && (w7 == 0)) begin
            sval = descale_fn(w0, PASS1_BITS + 3) + 64'sd128;
            p0 = clamp_u8_fn(sval); p1 = clamp_u8_fn(sval); p2 = clamp_u8_fn(sval); p3 = clamp_u8_fn(sval);
            p4 = clamp_u8_fn(sval); p5 = clamp_u8_fn(sval); p6 = clamp_u8_fn(sval); p7 = clamp_u8_fn(sval);
        end else begin
            z2 = w2;
            z3 = w6;
            z1 = (z2 + z3) * FIX_0_541196100;
            tmp2 = z1 + z3 * (-FIX_1_847759065);
            tmp3 = z1 + z2 * FIX_0_765366865;

            tmp0 = (w0 + w4) <<< CONST_BITS;
            tmp1 = (w0 - w4) <<< CONST_BITS;

            tmp10 = tmp0 + tmp3;
            tmp13 = tmp0 - tmp3;
            tmp11 = tmp1 + tmp2;
            tmp12 = tmp1 - tmp2;

            tmp0 = w7;
            tmp1 = w5;
            tmp2 = w3;
            tmp3 = w1;

            z1 = tmp0 + tmp3;
            z2 = tmp1 + tmp2;
            z3 = tmp0 + tmp2;
            z4 = tmp1 + tmp3;
            z5 = (z3 + z4) * FIX_1_175875602;

            tmp0 = tmp0 * FIX_0_298631336;
            tmp1 = tmp1 * FIX_2_053119869;
            tmp2 = tmp2 * FIX_3_072711026;
            tmp3 = tmp3 * FIX_1_501321110;
            z1 = z1 * (-FIX_0_899976223);
            z2 = z2 * (-FIX_2_562915447);
            z3 = z3 * (-FIX_1_961570560);
            z4 = z4 * (-FIX_0_390180644);

            z3 = z3 + z5;
            z4 = z4 + z5;

            tmp0 = tmp0 + z1 + z3;
            tmp1 = tmp1 + z2 + z4;
            tmp2 = tmp2 + z2 + z3;
            tmp3 = tmp3 + z1 + z4;

            y0 = descale_fn(tmp10 + tmp3, CONST_BITS + PASS1_BITS + 3);
            y1 = descale_fn(tmp11 + tmp2, CONST_BITS + PASS1_BITS + 3);
            y2 = descale_fn(tmp12 + tmp1, CONST_BITS + PASS1_BITS + 3);
            y3 = descale_fn(tmp13 + tmp0, CONST_BITS + PASS1_BITS + 3);
            y4 = descale_fn(tmp13 - tmp0, CONST_BITS + PASS1_BITS + 3);
            y5 = descale_fn(tmp12 - tmp1, CONST_BITS + PASS1_BITS + 3);
            y6 = descale_fn(tmp11 - tmp2, CONST_BITS + PASS1_BITS + 3);
            y7 = descale_fn(tmp10 - tmp3, CONST_BITS + PASS1_BITS + 3);

            p0 = clamp_u8_fn(y0 + 64'sd128);
            p1 = clamp_u8_fn(y1 + 64'sd128);
            p2 = clamp_u8_fn(y2 + 64'sd128);
            p3 = clamp_u8_fn(y3 + 64'sd128);
            p4 = clamp_u8_fn(y4 + 64'sd128);
            p5 = clamp_u8_fn(y5 + 64'sd128);
            p6 = clamp_u8_fn(y6 + 64'sd128);
            p7 = clamp_u8_fn(y7 + 64'sd128);
        end
    end
endtask

reg signed [63:0] d0;
reg signed [63:0] d1;
reg signed [63:0] d2;
reg signed [63:0] d3;
reg signed [63:0] d4;
reg signed [63:0] d5;
reg signed [63:0] d6;
reg signed [63:0] d7;
reg signed [63:0] d8;
reg signed [63:0] d9;
reg signed [63:0] d10;
reg signed [63:0] d11;
reg signed [63:0] d12;
reg signed [63:0] d13;
reg signed [63:0] d14;
reg signed [63:0] d15;
reg signed [63:0] d16;
reg signed [63:0] d17;
reg signed [63:0] d18;
reg signed [63:0] d19;
reg signed [63:0] d20;
reg signed [63:0] d21;
reg signed [63:0] d22;
reg signed [63:0] d23;
reg signed [63:0] d24;
reg signed [63:0] d25;
reg signed [63:0] d26;
reg signed [63:0] d27;
reg signed [63:0] d28;
reg signed [63:0] d29;
reg signed [63:0] d30;
reg signed [63:0] d31;
reg signed [63:0] d32;
reg signed [63:0] d33;
reg signed [63:0] d34;
reg signed [63:0] d35;
reg signed [63:0] d36;
reg signed [63:0] d37;
reg signed [63:0] d38;
reg signed [63:0] d39;
reg signed [63:0] d40;
reg signed [63:0] d41;
reg signed [63:0] d42;
reg signed [63:0] d43;
reg signed [63:0] d44;
reg signed [63:0] d45;
reg signed [63:0] d46;
reg signed [63:0] d47;
reg signed [63:0] d48;
reg signed [63:0] d49;
reg signed [63:0] d50;
reg signed [63:0] d51;
reg signed [63:0] d52;
reg signed [63:0] d53;
reg signed [63:0] d54;
reg signed [63:0] d55;
reg signed [63:0] d56;
reg signed [63:0] d57;
reg signed [63:0] d58;
reg signed [63:0] d59;
reg signed [63:0] d60;
reg signed [63:0] d61;
reg signed [63:0] d62;
reg signed [63:0] d63;

reg signed [63:0] w0;
reg signed [63:0] w1;
reg signed [63:0] w2;
reg signed [63:0] w3;
reg signed [63:0] w4;
reg signed [63:0] w5;
reg signed [63:0] w6;
reg signed [63:0] w7;
reg signed [63:0] w8;
reg signed [63:0] w9;
reg signed [63:0] w10;
reg signed [63:0] w11;
reg signed [63:0] w12;
reg signed [63:0] w13;
reg signed [63:0] w14;
reg signed [63:0] w15;
reg signed [63:0] w16;
reg signed [63:0] w17;
reg signed [63:0] w18;
reg signed [63:0] w19;
reg signed [63:0] w20;
reg signed [63:0] w21;
reg signed [63:0] w22;
reg signed [63:0] w23;
reg signed [63:0] w24;
reg signed [63:0] w25;
reg signed [63:0] w26;
reg signed [63:0] w27;
reg signed [63:0] w28;
reg signed [63:0] w29;
reg signed [63:0] w30;
reg signed [63:0] w31;
reg signed [63:0] w32;
reg signed [63:0] w33;
reg signed [63:0] w34;
reg signed [63:0] w35;
reg signed [63:0] w36;
reg signed [63:0] w37;
reg signed [63:0] w38;
reg signed [63:0] w39;
reg signed [63:0] w40;
reg signed [63:0] w41;
reg signed [63:0] w42;
reg signed [63:0] w43;
reg signed [63:0] w44;
reg signed [63:0] w45;
reg signed [63:0] w46;
reg signed [63:0] w47;
reg signed [63:0] w48;
reg signed [63:0] w49;
reg signed [63:0] w50;
reg signed [63:0] w51;
reg signed [63:0] w52;
reg signed [63:0] w53;
reg signed [63:0] w54;
reg signed [63:0] w55;
reg signed [63:0] w56;
reg signed [63:0] w57;
reg signed [63:0] w58;
reg signed [63:0] w59;
reg signed [63:0] w60;
reg signed [63:0] w61;
reg signed [63:0] w62;
reg signed [63:0] w63;

always_ff @(posedge clk) begin
    if (rst) begin
        busy <= 1'b0;
        out_pix_0 <= 8'd0;   out_pix_1 <= 8'd0;   out_pix_2 <= 8'd0;   out_pix_3 <= 8'd0;
        out_pix_4 <= 8'd0;   out_pix_5 <= 8'd0;   out_pix_6 <= 8'd0;   out_pix_7 <= 8'd0;
        out_pix_8 <= 8'd0;   out_pix_9 <= 8'd0;   out_pix_10 <= 8'd0;  out_pix_11 <= 8'd0;
        out_pix_12 <= 8'd0;  out_pix_13 <= 8'd0;  out_pix_14 <= 8'd0;  out_pix_15 <= 8'd0;
        out_pix_16 <= 8'd0;  out_pix_17 <= 8'd0;  out_pix_18 <= 8'd0;  out_pix_19 <= 8'd0;
        out_pix_20 <= 8'd0;  out_pix_21 <= 8'd0;  out_pix_22 <= 8'd0;  out_pix_23 <= 8'd0;
        out_pix_24 <= 8'd0;  out_pix_25 <= 8'd0;  out_pix_26 <= 8'd0;  out_pix_27 <= 8'd0;
        out_pix_28 <= 8'd0;  out_pix_29 <= 8'd0;  out_pix_30 <= 8'd0;  out_pix_31 <= 8'd0;
        out_pix_32 <= 8'd0;  out_pix_33 <= 8'd0;  out_pix_34 <= 8'd0;  out_pix_35 <= 8'd0;
        out_pix_36 <= 8'd0;  out_pix_37 <= 8'd0;  out_pix_38 <= 8'd0;  out_pix_39 <= 8'd0;
        out_pix_40 <= 8'd0;  out_pix_41 <= 8'd0;  out_pix_42 <= 8'd0;  out_pix_43 <= 8'd0;
        out_pix_44 <= 8'd0;  out_pix_45 <= 8'd0;  out_pix_46 <= 8'd0;  out_pix_47 <= 8'd0;
        out_pix_48 <= 8'd0;  out_pix_49 <= 8'd0;  out_pix_50 <= 8'd0;  out_pix_51 <= 8'd0;
        out_pix_52 <= 8'd0;  out_pix_53 <= 8'd0;  out_pix_54 <= 8'd0;  out_pix_55 <= 8'd0;
        out_pix_56 <= 8'd0;  out_pix_57 <= 8'd0;  out_pix_58 <= 8'd0;  out_pix_59 <= 8'd0;
        out_pix_60 <= 8'd0;  out_pix_61 <= 8'd0;  out_pix_62 <= 8'd0;  out_pix_63 <= 8'd0;
    end else begin
        if (input_accepted) begin
            d0  = deq_fn(in_coef_0,  in_quant_0);   d1  = deq_fn(in_coef_1,  in_quant_1);
            d2  = deq_fn(in_coef_2,  in_quant_2);   d3  = deq_fn(in_coef_3,  in_quant_3);
            d4  = deq_fn(in_coef_4,  in_quant_4);   d5  = deq_fn(in_coef_5,  in_quant_5);
            d6  = deq_fn(in_coef_6,  in_quant_6);   d7  = deq_fn(in_coef_7,  in_quant_7);
            d8  = deq_fn(in_coef_8,  in_quant_8);   d9  = deq_fn(in_coef_9,  in_quant_9);
            d10 = deq_fn(in_coef_10, in_quant_10);  d11 = deq_fn(in_coef_11, in_quant_11);
            d12 = deq_fn(in_coef_12, in_quant_12);  d13 = deq_fn(in_coef_13, in_quant_13);
            d14 = deq_fn(in_coef_14, in_quant_14);  d15 = deq_fn(in_coef_15, in_quant_15);
            d16 = deq_fn(in_coef_16, in_quant_16);  d17 = deq_fn(in_coef_17, in_quant_17);
            d18 = deq_fn(in_coef_18, in_quant_18);  d19 = deq_fn(in_coef_19, in_quant_19);
            d20 = deq_fn(in_coef_20, in_quant_20);  d21 = deq_fn(in_coef_21, in_quant_21);
            d22 = deq_fn(in_coef_22, in_quant_22);  d23 = deq_fn(in_coef_23, in_quant_23);
            d24 = deq_fn(in_coef_24, in_quant_24);  d25 = deq_fn(in_coef_25, in_quant_25);
            d26 = deq_fn(in_coef_26, in_quant_26);  d27 = deq_fn(in_coef_27, in_quant_27);
            d28 = deq_fn(in_coef_28, in_quant_28);  d29 = deq_fn(in_coef_29, in_quant_29);
            d30 = deq_fn(in_coef_30, in_quant_30);  d31 = deq_fn(in_coef_31, in_quant_31);
            d32 = deq_fn(in_coef_32, in_quant_32);  d33 = deq_fn(in_coef_33, in_quant_33);
            d34 = deq_fn(in_coef_34, in_quant_34);  d35 = deq_fn(in_coef_35, in_quant_35);
            d36 = deq_fn(in_coef_36, in_quant_36);  d37 = deq_fn(in_coef_37, in_quant_37);
            d38 = deq_fn(in_coef_38, in_quant_38);  d39 = deq_fn(in_coef_39, in_quant_39);
            d40 = deq_fn(in_coef_40, in_quant_40);  d41 = deq_fn(in_coef_41, in_quant_41);
            d42 = deq_fn(in_coef_42, in_quant_42);  d43 = deq_fn(in_coef_43, in_quant_43);
            d44 = deq_fn(in_coef_44, in_quant_44);  d45 = deq_fn(in_coef_45, in_quant_45);
            d46 = deq_fn(in_coef_46, in_quant_46);  d47 = deq_fn(in_coef_47, in_quant_47);
            d48 = deq_fn(in_coef_48, in_quant_48);  d49 = deq_fn(in_coef_49, in_quant_49);
            d50 = deq_fn(in_coef_50, in_quant_50);  d51 = deq_fn(in_coef_51, in_quant_51);
            d52 = deq_fn(in_coef_52, in_quant_52);  d53 = deq_fn(in_coef_53, in_quant_53);
            d54 = deq_fn(in_coef_54, in_quant_54);  d55 = deq_fn(in_coef_55, in_quant_55);
            d56 = deq_fn(in_coef_56, in_quant_56);  d57 = deq_fn(in_coef_57, in_quant_57);
            d58 = deq_fn(in_coef_58, in_quant_58);  d59 = deq_fn(in_coef_59, in_quant_59);
            d60 = deq_fn(in_coef_60, in_quant_60);  d61 = deq_fn(in_coef_61, in_quant_61);
            d62 = deq_fn(in_coef_62, in_quant_62);  d63 = deq_fn(in_coef_63, in_quant_63);

            idct_pass1_task(d0,  d8,  d16, d24, d32, d40, d48, d56, w0,  w8,  w16, w24, w32, w40, w48, w56);
            idct_pass1_task(d1,  d9,  d17, d25, d33, d41, d49, d57, w1,  w9,  w17, w25, w33, w41, w49, w57);
            idct_pass1_task(d2,  d10, d18, d26, d34, d42, d50, d58, w2,  w10, w18, w26, w34, w42, w50, w58);
            idct_pass1_task(d3,  d11, d19, d27, d35, d43, d51, d59, w3,  w11, w19, w27, w35, w43, w51, w59);
            idct_pass1_task(d4,  d12, d20, d28, d36, d44, d52, d60, w4,  w12, w20, w28, w36, w44, w52, w60);
            idct_pass1_task(d5,  d13, d21, d29, d37, d45, d53, d61, w5,  w13, w21, w29, w37, w45, w53, w61);
            idct_pass1_task(d6,  d14, d22, d30, d38, d46, d54, d62, w6,  w14, w22, w30, w38, w46, w54, w62);
            idct_pass1_task(d7,  d15, d23, d31, d39, d47, d55, d63, w7,  w15, w23, w31, w39, w47, w55, w63);

            idct_pass2_task(w0,  w1,  w2,  w3,  w4,  w5,  w6,  w7,  out_pix_0,  out_pix_1,  out_pix_2,  out_pix_3,  out_pix_4,  out_pix_5,  out_pix_6,  out_pix_7);
            idct_pass2_task(w8,  w9,  w10, w11, w12, w13, w14, w15, out_pix_8,  out_pix_9,  out_pix_10, out_pix_11, out_pix_12, out_pix_13, out_pix_14, out_pix_15);
            idct_pass2_task(w16, w17, w18, w19, w20, w21, w22, w23, out_pix_16, out_pix_17, out_pix_18, out_pix_19, out_pix_20, out_pix_21, out_pix_22, out_pix_23);
            idct_pass2_task(w24, w25, w26, w27, w28, w29, w30, w31, out_pix_24, out_pix_25, out_pix_26, out_pix_27, out_pix_28, out_pix_29, out_pix_30, out_pix_31);
            idct_pass2_task(w32, w33, w34, w35, w36, w37, w38, w39, out_pix_32, out_pix_33, out_pix_34, out_pix_35, out_pix_36, out_pix_37, out_pix_38, out_pix_39);
            idct_pass2_task(w40, w41, w42, w43, w44, w45, w46, w47, out_pix_40, out_pix_41, out_pix_42, out_pix_43, out_pix_44, out_pix_45, out_pix_46, out_pix_47);
            idct_pass2_task(w48, w49, w50, w51, w52, w53, w54, w55, out_pix_48, out_pix_49, out_pix_50, out_pix_51, out_pix_52, out_pix_53, out_pix_54, out_pix_55);
            idct_pass2_task(w56, w57, w58, w59, w60, w61, w62, w63, out_pix_56, out_pix_57, out_pix_58, out_pix_59, out_pix_60, out_pix_61, out_pix_62, out_pix_63);

            busy <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end
end

endmodule