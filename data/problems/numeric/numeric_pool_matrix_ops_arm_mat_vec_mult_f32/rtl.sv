module matrix_vector_multiply_f32_4x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a00,
    input  logic [31:0] in_a01,
    input  logic [31:0] in_a02,
    input  logic [31:0] in_a03,
    input  logic [31:0] in_a10,
    input  logic [31:0] in_a11,
    input  logic [31:0] in_a12,
    input  logic [31:0] in_a13,
    input  logic [31:0] in_a20,
    input  logic [31:0] in_a21,
    input  logic [31:0] in_a22,
    input  logic [31:0] in_a23,
    input  logic [31:0] in_a30,
    input  logic [31:0] in_a31,
    input  logic [31:0] in_a32,
    input  logic [31:0] in_a33,
    input  logic [31:0] in_v0,
    input  logic [31:0] in_v1,
    input  logic [31:0] in_v2,
    input  logic [31:0] in_v3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3
);

function automatic logic [63:0] fp_shift_jam(input logic [63:0] x, input integer n);
    if (n <= 0) fp_shift_jam = x;
    else if (n >= 64) fp_shift_jam = {63'b0, |x};
    else fp_shift_jam = (x >> n) | {63'b0, |(x << (64-n))};
endfunction

function automatic logic [31:0] fp_round_pack(
    input logic sign_bit, input integer exponent, input logic [63:0] sig
);
    integer e;
    logic [63:0] shifted, rounded;
    begin
        e = exponent;
        shifted = sig;
        if (e < 1) begin
            shifted = fp_shift_jam(shifted, 1-e);
            e = 1;
        end
        rounded = (shifted >> 3) + {63'b0, (shifted[2] &&
                  (shifted[1] || shifted[0] || shifted[3]))};
        if (rounded[24]) begin
            rounded = rounded >> 1;
            e = e+1;
        end
        if (e >= 255) fp_round_pack = {sign_bit, 8'hff, 23'b0};
        else if (rounded < 64'h800000)
            fp_round_pack = {sign_bit, 8'b0, rounded[22:0]};
        else fp_round_pack = {sign_bit, e[7:0], rounded[22:0]};
    end
endfunction

function automatic logic [31:0] fp32_add_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sa, sb, sign_bit;
    logic [63:0] ma, mb, hi, lo, sum;
    begin
        sa = a[31]; sb = b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
            fp32_add_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) && (b[30:0] == 31'h7f800000) && (sa != sb))
            fp32_add_bits = 32'h7fc00000;
        else if (a[30:0] == 31'h7f800000) fp32_add_bits = a;
        else if (b[30:0] == 31'h7f800000) fp32_add_bits = b;
        else if ((a[30:0] == 0) && (b[30:0] == 0))
            fp32_add_bits = {sa & sb, 31'b0};
        else begin
            if ((ea > eb) || ((ea == eb) && (ma >= mb))) begin
                hi = ma << 3; lo = fp_shift_jam(mb << 3, ea-eb);
                sign_bit = sa; e = ea;
            end else begin
                hi = mb << 3; lo = fp_shift_jam(ma << 3, eb-ea);
                sign_bit = sb; e = eb;
            end
            sum = sa == sb ? hi+lo : hi-lo;
            if (sum == 0) fp32_add_bits = 32'b0;
            else begin
                if (sum[27]) begin
                    sum = fp_shift_jam(sum, 1); e = e+1;
                end
                for (i=0; i<26; i=i+1) begin
                    if (!sum[26] && e>1) begin sum=sum<<1; e=e-1; end
                end
                fp32_add_bits = fp_round_pack(sign_bit, e, sum);
            end
        end
    end
endfunction

function automatic logic [31:0] fp32_mul_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sign_bit;
    logic [63:0] ma, mb, product, sig;
    begin
        sign_bit = a[31] ^ b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000) ||
            ((a[30:0] == 31'h7f800000) && (b[30:0] == 0)) ||
            ((b[30:0] == 31'h7f800000) && (a[30:0] == 0)))
            fp32_mul_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) || (b[30:0] == 31'h7f800000))
            fp32_mul_bits = {sign_bit, 8'hff, 23'b0};
        else if ((a[30:0] == 0) || (b[30:0] == 0))
            fp32_mul_bits = {sign_bit, 31'b0};
        else begin
            for (i=0; i<23; i=i+1) begin
                if (!ma[23]) begin ma=ma<<1; ea=ea-1; end
                if (!mb[23]) begin mb=mb<<1; eb=eb-1; end
            end
            product = ma*mb;
            e = ea+eb-127;
            if (product[47]) begin sig=fp_shift_jam(product,21); e=e+1; end
            else sig=fp_shift_jam(product,20);
            fp32_mul_bits = fp_round_pack(sign_bit, e, sig);
        end
    end
endfunction

    logic busy;
    wire input_accepted;
    wire output_consumed;

    logic [31:0] calc_y0;
    logic [31:0] calc_y1;
    logic [31:0] calc_y2;
    logic [31:0] calc_y3;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        logic [31:0] p00, p01, p02, p03, s00, s01;
        logic [31:0] p10, p11, p12, p13, s10, s11;
        logic [31:0] p20, p21, p22, p23, s20, s21;
        logic [31:0] p30, p31, p32, p33, s30, s31;

        p00 = fp32_mul_bits(in_a00, in_v0);
        p01 = fp32_mul_bits(in_a01, in_v1);
        p02 = fp32_mul_bits(in_a02, in_v2);
        p03 = fp32_mul_bits(in_a03, in_v3);
        s00 = fp32_add_bits(p00, p01);
        s01 = fp32_add_bits(s00, p02);
        calc_y0 = fp32_add_bits(s01, p03);

        p10 = fp32_mul_bits(in_a10, in_v0);
        p11 = fp32_mul_bits(in_a11, in_v1);
        p12 = fp32_mul_bits(in_a12, in_v2);
        p13 = fp32_mul_bits(in_a13, in_v3);
        s10 = fp32_add_bits(p10, p11);
        s11 = fp32_add_bits(s10, p12);
        calc_y1 = fp32_add_bits(s11, p13);

        p20 = fp32_mul_bits(in_a20, in_v0);
        p21 = fp32_mul_bits(in_a21, in_v1);
        p22 = fp32_mul_bits(in_a22, in_v2);
        p23 = fp32_mul_bits(in_a23, in_v3);
        s20 = fp32_add_bits(p20, p21);
        s21 = fp32_add_bits(s20, p22);
        calc_y2 = fp32_add_bits(s21, p23);

        p30 = fp32_mul_bits(in_a30, in_v0);
        p31 = fp32_mul_bits(in_a31, in_v1);
        p32 = fp32_mul_bits(in_a32, in_v2);
        p33 = fp32_mul_bits(in_a33, in_v3);
        s30 = fp32_add_bits(p30, p31);
        s31 = fp32_add_bits(s30, p32);
        calc_y3 = fp32_add_bits(s31, p33);
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy   <= 1'b0;
            out_y0 <= 32'h00000000;
            out_y1 <= 32'h00000000;
            out_y2 <= 32'h00000000;
            out_y3 <= 32'h00000000;
        end else if (input_accepted) begin
            out_y0 <= calc_y0;
            out_y1 <= calc_y1;
            out_y2 <= calc_y2;
            out_y3 <= calc_y3;
            busy   <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
