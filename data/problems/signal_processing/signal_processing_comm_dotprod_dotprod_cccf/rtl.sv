module signal_processing_comm_dotprod_dotprod_cccf (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0_re,
    input  logic [31:0] in_x0_im,
    input  logic [31:0] in_x1_re,
    input  logic [31:0] in_x1_im,
    input  logic [31:0] in_x2_re,
    input  logic [31:0] in_x2_im,
    input  logic [31:0] in_x3_re,
    input  logic [31:0] in_x3_im,
    input  logic [31:0] in_h0_re,
    input  logic [31:0] in_h0_im,
    input  logic [31:0] in_h1_re,
    input  logic [31:0] in_h1_im,
    input  logic [31:0] in_h2_re,
    input  logic [31:0] in_h2_im,
    input  logic [31:0] in_h3_re,
    input  logic [31:0] in_h3_im,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y_re,
    output logic [31:0] out_y_im
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

function automatic logic [31:0] fp32_sub_bits(input logic [31:0] a, b);
    fp32_sub_bits = fp32_add_bits(a, b ^ 32'h80000000);
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

    logic [31:0] p0_rr, p0_ii, p0_ri, p0_ir, p0_re, p0_im;
    logic [31:0] p1_rr, p1_ii, p1_ri, p1_ir, p1_re, p1_im;
    logic [31:0] p2_rr, p2_ii, p2_ri, p2_ir, p2_re, p2_im;
    logic [31:0] p3_rr, p3_ii, p3_ri, p3_ir, p3_re, p3_im;
    logic [31:0] sum1_re, sum2_re, sum3_re;
    logic [31:0] sum1_im, sum2_im, sum3_im;

    logic        busy;
    logic        input_accepted;
    logic        output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        p0_rr = fp32_mul_bits(in_x0_re, in_h0_re);
        p0_ii = fp32_mul_bits(in_x0_im, in_h0_im);
        p0_ri = fp32_mul_bits(in_x0_re, in_h0_im);
        p0_ir = fp32_mul_bits(in_x0_im, in_h0_re);
        p0_re = fp32_sub_bits(p0_rr, p0_ii);
        p0_im = fp32_add_bits(p0_ri, p0_ir);

        p1_rr = fp32_mul_bits(in_x1_re, in_h1_re);
        p1_ii = fp32_mul_bits(in_x1_im, in_h1_im);
        p1_ri = fp32_mul_bits(in_x1_re, in_h1_im);
        p1_ir = fp32_mul_bits(in_x1_im, in_h1_re);
        p1_re = fp32_sub_bits(p1_rr, p1_ii);
        p1_im = fp32_add_bits(p1_ri, p1_ir);

        p2_rr = fp32_mul_bits(in_x2_re, in_h2_re);
        p2_ii = fp32_mul_bits(in_x2_im, in_h2_im);
        p2_ri = fp32_mul_bits(in_x2_re, in_h2_im);
        p2_ir = fp32_mul_bits(in_x2_im, in_h2_re);
        p2_re = fp32_sub_bits(p2_rr, p2_ii);
        p2_im = fp32_add_bits(p2_ri, p2_ir);

        p3_rr = fp32_mul_bits(in_x3_re, in_h3_re);
        p3_ii = fp32_mul_bits(in_x3_im, in_h3_im);
        p3_ri = fp32_mul_bits(in_x3_re, in_h3_im);
        p3_ir = fp32_mul_bits(in_x3_im, in_h3_re);
        p3_re = fp32_sub_bits(p3_rr, p3_ii);
        p3_im = fp32_add_bits(p3_ri, p3_ir);

        sum1_re = fp32_add_bits(p0_re, p1_re);
        sum2_re = fp32_add_bits(sum1_re, p2_re);
        sum3_re = fp32_add_bits(sum2_re, p3_re);

        sum1_im = fp32_add_bits(p0_im, p1_im);
        sum2_im = fp32_add_bits(sum1_im, p2_im);
        sum3_im = fp32_add_bits(sum2_im, p3_im);
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy     <= 1'b0;
            out_y_re <= 32'h00000000;
            out_y_im <= 32'h00000000;
        end else if (input_accepted) begin
            busy     <= 1'b1;
            out_y_re <= sum3_re;
            out_y_im <= sum3_im;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
