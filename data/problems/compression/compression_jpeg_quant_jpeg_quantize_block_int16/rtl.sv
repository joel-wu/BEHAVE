module compression_jpeg_quant_jpeg_quantize_block_int16 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_q00,
    input  logic [15:0]  in_q01,
    input  logic [15:0]  in_q02,
    input  logic [15:0]  in_q03,
    input  logic [15:0]  in_q04,
    input  logic [15:0]  in_q05,
    input  logic [15:0]  in_q06,
    input  logic [15:0]  in_q07,
    input  logic [15:0]  in_q08,
    input  logic [15:0]  in_q09,
    input  logic [15:0]  in_q10,
    input  logic [15:0]  in_q11,
    input  logic [15:0]  in_q12,
    input  logic [15:0]  in_q13,
    input  logic [15:0]  in_q14,
    input  logic [15:0]  in_q15,
    input  logic [15:0]  in_q16,
    input  logic [15:0]  in_q17,
    input  logic [15:0]  in_q18,
    input  logic [15:0]  in_q19,
    input  logic [15:0]  in_q20,
    input  logic [15:0]  in_q21,
    input  logic [15:0]  in_q22,
    input  logic [15:0]  in_q23,
    input  logic [15:0]  in_q24,
    input  logic [15:0]  in_q25,
    input  logic [15:0]  in_q26,
    input  logic [15:0]  in_q27,
    input  logic [15:0]  in_q28,
    input  logic [15:0]  in_q29,
    input  logic [15:0]  in_q30,
    input  logic [15:0]  in_q31,
    input  logic [15:0]  in_q32,
    input  logic [15:0]  in_q33,
    input  logic [15:0]  in_q34,
    input  logic [15:0]  in_q35,
    input  logic [15:0]  in_q36,
    input  logic [15:0]  in_q37,
    input  logic [15:0]  in_q38,
    input  logic [15:0]  in_q39,
    input  logic [15:0]  in_q40,
    input  logic [15:0]  in_q41,
    input  logic [15:0]  in_q42,
    input  logic [15:0]  in_q43,
    input  logic [15:0]  in_q44,
    input  logic [15:0]  in_q45,
    input  logic [15:0]  in_q46,
    input  logic [15:0]  in_q47,
    input  logic [15:0]  in_q48,
    input  logic [15:0]  in_q49,
    input  logic [15:0]  in_q50,
    input  logic [15:0]  in_q51,
    input  logic [15:0]  in_q52,
    input  logic [15:0]  in_q53,
    input  logic [15:0]  in_q54,
    input  logic [15:0]  in_q55,
    input  logic [15:0]  in_q56,
    input  logic [15:0]  in_q57,
    input  logic [15:0]  in_q58,
    input  logic [15:0]  in_q59,
    input  logic [15:0]  in_q60,
    input  logic [15:0]  in_q61,
    input  logic [15:0]  in_q62,
    input  logic [15:0]  in_q63,

    input  logic [15:0]  in_w00,
    input  logic [15:0]  in_w01,
    input  logic [15:0]  in_w02,
    input  logic [15:0]  in_w03,
    input  logic [15:0]  in_w04,
    input  logic [15:0]  in_w05,
    input  logic [15:0]  in_w06,
    input  logic [15:0]  in_w07,
    input  logic [15:0]  in_w08,
    input  logic [15:0]  in_w09,
    input  logic [15:0]  in_w10,
    input  logic [15:0]  in_w11,
    input  logic [15:0]  in_w12,
    input  logic [15:0]  in_w13,
    input  logic [15:0]  in_w14,
    input  logic [15:0]  in_w15,
    input  logic [15:0]  in_w16,
    input  logic [15:0]  in_w17,
    input  logic [15:0]  in_w18,
    input  logic [15:0]  in_w19,
    input  logic [15:0]  in_w20,
    input  logic [15:0]  in_w21,
    input  logic [15:0]  in_w22,
    input  logic [15:0]  in_w23,
    input  logic [15:0]  in_w24,
    input  logic [15:0]  in_w25,
    input  logic [15:0]  in_w26,
    input  logic [15:0]  in_w27,
    input  logic [15:0]  in_w28,
    input  logic [15:0]  in_w29,
    input  logic [15:0]  in_w30,
    input  logic [15:0]  in_w31,
    input  logic [15:0]  in_w32,
    input  logic [15:0]  in_w33,
    input  logic [15:0]  in_w34,
    input  logic [15:0]  in_w35,
    input  logic [15:0]  in_w36,
    input  logic [15:0]  in_w37,
    input  logic [15:0]  in_w38,
    input  logic [15:0]  in_w39,
    input  logic [15:0]  in_w40,
    input  logic [15:0]  in_w41,
    input  logic [15:0]  in_w42,
    input  logic [15:0]  in_w43,
    input  logic [15:0]  in_w44,
    input  logic [15:0]  in_w45,
    input  logic [15:0]  in_w46,
    input  logic [15:0]  in_w47,
    input  logic [15:0]  in_w48,
    input  logic [15:0]  in_w49,
    input  logic [15:0]  in_w50,
    input  logic [15:0]  in_w51,
    input  logic [15:0]  in_w52,
    input  logic [15:0]  in_w53,
    input  logic [15:0]  in_w54,
    input  logic [15:0]  in_w55,
    input  logic [15:0]  in_w56,
    input  logic [15:0]  in_w57,
    input  logic [15:0]  in_w58,
    input  logic [15:0]  in_w59,
    input  logic [15:0]  in_w60,
    input  logic [15:0]  in_w61,
    input  logic [15:0]  in_w62,
    input  logic [15:0]  in_w63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_c00,
    output logic [15:0]  out_c01,
    output logic [15:0]  out_c02,
    output logic [15:0]  out_c03,
    output logic [15:0]  out_c04,
    output logic [15:0]  out_c05,
    output logic [15:0]  out_c06,
    output logic [15:0]  out_c07,
    output logic [15:0]  out_c08,
    output logic [15:0]  out_c09,
    output logic [15:0]  out_c10,
    output logic [15:0]  out_c11,
    output logic [15:0]  out_c12,
    output logic [15:0]  out_c13,
    output logic [15:0]  out_c14,
    output logic [15:0]  out_c15,
    output logic [15:0]  out_c16,
    output logic [15:0]  out_c17,
    output logic [15:0]  out_c18,
    output logic [15:0]  out_c19,
    output logic [15:0]  out_c20,
    output logic [15:0]  out_c21,
    output logic [15:0]  out_c22,
    output logic [15:0]  out_c23,
    output logic [15:0]  out_c24,
    output logic [15:0]  out_c25,
    output logic [15:0]  out_c26,
    output logic [15:0]  out_c27,
    output logic [15:0]  out_c28,
    output logic [15:0]  out_c29,
    output logic [15:0]  out_c30,
    output logic [15:0]  out_c31,
    output logic [15:0]  out_c32,
    output logic [15:0]  out_c33,
    output logic [15:0]  out_c34,
    output logic [15:0]  out_c35,
    output logic [15:0]  out_c36,
    output logic [15:0]  out_c37,
    output logic [15:0]  out_c38,
    output logic [15:0]  out_c39,
    output logic [15:0]  out_c40,
    output logic [15:0]  out_c41,
    output logic [15:0]  out_c42,
    output logic [15:0]  out_c43,
    output logic [15:0]  out_c44,
    output logic [15:0]  out_c45,
    output logic [15:0]  out_c46,
    output logic [15:0]  out_c47,
    output logic [15:0]  out_c48,
    output logic [15:0]  out_c49,
    output logic [15:0]  out_c50,
    output logic [15:0]  out_c51,
    output logic [15:0]  out_c52,
    output logic [15:0]  out_c53,
    output logic [15:0]  out_c54,
    output logic [15:0]  out_c55,
    output logic [15:0]  out_c56,
    output logic [15:0]  out_c57,
    output logic [15:0]  out_c58,
    output logic [15:0]  out_c59,
    output logic [15:0]  out_c60,
    output logic [15:0]  out_c61,
    output logic [15:0]  out_c62,
    output logic [15:0]  out_c63
);

function automatic logic [4:0] flss16(input logic [15:0] val);
    begin
        if (val[15]) begin
            flss16 = 5'd16;
        end else if (val[14]) begin
            flss16 = 5'd15;
        end else if (val[13]) begin
            flss16 = 5'd14;
        end else if (val[12]) begin
            flss16 = 5'd13;
        end else if (val[11]) begin
            flss16 = 5'd12;
        end else if (val[10]) begin
            flss16 = 5'd11;
        end else if (val[9]) begin
            flss16 = 5'd10;
        end else if (val[8]) begin
            flss16 = 5'd9;
        end else if (val[7]) begin
            flss16 = 5'd8;
        end else if (val[6]) begin
            flss16 = 5'd7;
        end else if (val[5]) begin
            flss16 = 5'd6;
        end else if (val[4]) begin
            flss16 = 5'd5;
        end else if (val[3]) begin
            flss16 = 5'd4;
        end else if (val[2]) begin
            flss16 = 5'd3;
        end else begin
            flss16 = 5'd2;
        end
    end
endfunction

function automatic logic [15:0] quantize_one(input logic [15:0] qi_bits, input logic [15:0] wi);
    logic signed [15:0] qi_s;
    logic [15:0] mag16;
    logic [4:0]  bitpos;
    logic [4:0]  b;
    logic [5:0]  r;
    logic [31:0] pow2r;
    logic [31:0] wi32;
    logic [31:0] fq;
    logic [31:0] fr;
    logic [15:0] corr16;
    logic [31:0] recip32;
    logic [5:0]  shift_plus_16;
    logic [31:0] sum32;
    logic [47:0] product48;
    logic [47:0] shifted48;
    logic [31:0] quot32;
    logic signed [31:0] quot32_s;
    logic signed [31:0] result32;
    begin
        qi_s = qi_bits;
        wi32 = {16'd0, wi};

        if (qi_s < 0) begin
            mag16 = -qi_s;
        end else begin
            mag16 = qi_s[15:0];
        end

        if (wi <= 16'd1) begin
            recip32 = 32'd1;
            corr16 = 16'd0;
            shift_plus_16 = 6'd0;
        end else begin
            bitpos = flss16(wi);
            b = bitpos - 5'd1;
            r = 6'd16 + {1'b0, b};
            pow2r = 32'd1 << r;
            fq = pow2r / wi32;
            fr = pow2r - (fq * wi32);
            corr16 = wi >> 1;
            if (fr == 32'd0) begin
                fq = fq >> 1;
                r = r - 6'd1;
            end else if (fr <= ({16'd0, wi} >> 1)) begin
                corr16 = corr16 + 16'd1;
            end else begin
                fq = fq + 32'd1;
            end
            recip32 = fq;
            shift_plus_16 = r;
        end

        sum32 = {16'd0, mag16} + {16'd0, corr16};
        product48 = sum32 * recip32;
        shifted48 = product48 >> shift_plus_16;
        quot32 = shifted48[31:0];
        quot32_s = quot32;

        if (qi_s < 0) begin
            result32 = -quot32_s;
        end else begin
            result32 = quot32_s;
        end

        quantize_one = result32[15:0];
    end
endfunction

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_ff @(posedge clk) begin
    if (rst) begin
        busy <= 1'b0;
        out_c00 <= 16'd0;
        out_c01 <= 16'd0;
        out_c02 <= 16'd0;
        out_c03 <= 16'd0;
        out_c04 <= 16'd0;
        out_c05 <= 16'd0;
        out_c06 <= 16'd0;
        out_c07 <= 16'd0;
        out_c08 <= 16'd0;
        out_c09 <= 16'd0;
        out_c10 <= 16'd0;
        out_c11 <= 16'd0;
        out_c12 <= 16'd0;
        out_c13 <= 16'd0;
        out_c14 <= 16'd0;
        out_c15 <= 16'd0;
        out_c16 <= 16'd0;
        out_c17 <= 16'd0;
        out_c18 <= 16'd0;
        out_c19 <= 16'd0;
        out_c20 <= 16'd0;
        out_c21 <= 16'd0;
        out_c22 <= 16'd0;
        out_c23 <= 16'd0;
        out_c24 <= 16'd0;
        out_c25 <= 16'd0;
        out_c26 <= 16'd0;
        out_c27 <= 16'd0;
        out_c28 <= 16'd0;
        out_c29 <= 16'd0;
        out_c30 <= 16'd0;
        out_c31 <= 16'd0;
        out_c32 <= 16'd0;
        out_c33 <= 16'd0;
        out_c34 <= 16'd0;
        out_c35 <= 16'd0;
        out_c36 <= 16'd0;
        out_c37 <= 16'd0;
        out_c38 <= 16'd0;
        out_c39 <= 16'd0;
        out_c40 <= 16'd0;
        out_c41 <= 16'd0;
        out_c42 <= 16'd0;
        out_c43 <= 16'd0;
        out_c44 <= 16'd0;
        out_c45 <= 16'd0;
        out_c46 <= 16'd0;
        out_c47 <= 16'd0;
        out_c48 <= 16'd0;
        out_c49 <= 16'd0;
        out_c50 <= 16'd0;
        out_c51 <= 16'd0;
        out_c52 <= 16'd0;
        out_c53 <= 16'd0;
        out_c54 <= 16'd0;
        out_c55 <= 16'd0;
        out_c56 <= 16'd0;
        out_c57 <= 16'd0;
        out_c58 <= 16'd0;
        out_c59 <= 16'd0;
        out_c60 <= 16'd0;
        out_c61 <= 16'd0;
        out_c62 <= 16'd0;
        out_c63 <= 16'd0;
    end else if (input_accepted) begin
        busy <= 1'b1;
        out_c00 <= quantize_one(in_q00, in_w00);
        out_c01 <= quantize_one(in_q01, in_w01);
        out_c02 <= quantize_one(in_q02, in_w02);
        out_c03 <= quantize_one(in_q03, in_w03);
        out_c04 <= quantize_one(in_q04, in_w04);
        out_c05 <= quantize_one(in_q05, in_w05);
        out_c06 <= quantize_one(in_q06, in_w06);
        out_c07 <= quantize_one(in_q07, in_w07);
        out_c08 <= quantize_one(in_q08, in_w08);
        out_c09 <= quantize_one(in_q09, in_w09);
        out_c10 <= quantize_one(in_q10, in_w10);
        out_c11 <= quantize_one(in_q11, in_w11);
        out_c12 <= quantize_one(in_q12, in_w12);
        out_c13 <= quantize_one(in_q13, in_w13);
        out_c14 <= quantize_one(in_q14, in_w14);
        out_c15 <= quantize_one(in_q15, in_w15);
        out_c16 <= quantize_one(in_q16, in_w16);
        out_c17 <= quantize_one(in_q17, in_w17);
        out_c18 <= quantize_one(in_q18, in_w18);
        out_c19 <= quantize_one(in_q19, in_w19);
        out_c20 <= quantize_one(in_q20, in_w20);
        out_c21 <= quantize_one(in_q21, in_w21);
        out_c22 <= quantize_one(in_q22, in_w22);
        out_c23 <= quantize_one(in_q23, in_w23);
        out_c24 <= quantize_one(in_q24, in_w24);
        out_c25 <= quantize_one(in_q25, in_w25);
        out_c26 <= quantize_one(in_q26, in_w26);
        out_c27 <= quantize_one(in_q27, in_w27);
        out_c28 <= quantize_one(in_q28, in_w28);
        out_c29 <= quantize_one(in_q29, in_w29);
        out_c30 <= quantize_one(in_q30, in_w30);
        out_c31 <= quantize_one(in_q31, in_w31);
        out_c32 <= quantize_one(in_q32, in_w32);
        out_c33 <= quantize_one(in_q33, in_w33);
        out_c34 <= quantize_one(in_q34, in_w34);
        out_c35 <= quantize_one(in_q35, in_w35);
        out_c36 <= quantize_one(in_q36, in_w36);
        out_c37 <= quantize_one(in_q37, in_w37);
        out_c38 <= quantize_one(in_q38, in_w38);
        out_c39 <= quantize_one(in_q39, in_w39);
        out_c40 <= quantize_one(in_q40, in_w40);
        out_c41 <= quantize_one(in_q41, in_w41);
        out_c42 <= quantize_one(in_q42, in_w42);
        out_c43 <= quantize_one(in_q43, in_w43);
        out_c44 <= quantize_one(in_q44, in_w44);
        out_c45 <= quantize_one(in_q45, in_w45);
        out_c46 <= quantize_one(in_q46, in_w46);
        out_c47 <= quantize_one(in_q47, in_w47);
        out_c48 <= quantize_one(in_q48, in_w48);
        out_c49 <= quantize_one(in_q49, in_w49);
        out_c50 <= quantize_one(in_q50, in_w50);
        out_c51 <= quantize_one(in_q51, in_w51);
        out_c52 <= quantize_one(in_q52, in_w52);
        out_c53 <= quantize_one(in_q53, in_w53);
        out_c54 <= quantize_one(in_q54, in_w54);
        out_c55 <= quantize_one(in_q55, in_w55);
        out_c56 <= quantize_one(in_q56, in_w56);
        out_c57 <= quantize_one(in_q57, in_w57);
        out_c58 <= quantize_one(in_q58, in_w58);
        out_c59 <= quantize_one(in_q59, in_w59);
        out_c60 <= quantize_one(in_q60, in_w60);
        out_c61 <= quantize_one(in_q61, in_w61);
        out_c62 <= quantize_one(in_q62, in_w62);
        out_c63 <= quantize_one(in_q63, in_w63);
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule