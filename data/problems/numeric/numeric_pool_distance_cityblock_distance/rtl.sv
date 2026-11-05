module CityblockDistanceF32x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a0,
    input  logic [31:0] in_a1,
    input  logic [31:0] in_a2,
    input  logic [31:0] in_a3,
    input  logic [31:0] in_b0,
    input  logic [31:0] in_b1,
    input  logic [31:0] in_b2,
    input  logic [31:0] in_b3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_distance
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

    localparam logic [31:0] CANON_NAN = 32'h7FC00000;

    logic busy;
    logic [31:0] result_comb;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [31:0] fp32_abs_bits(input logic [31:0] x);
        begin
            if ((x[30:23] == 8'hFF) && (x[22:0] != 23'h0)) begin
                fp32_abs_bits = CANON_NAN;
            end else begin
                fp32_abs_bits = {1'b0, x[30:0]};
            end
        end
    endfunction

    always_comb begin
        logic [31:0] d0;
        logic [31:0] d1;
        logic [31:0] d2;
        logic [31:0] d3;
        logic [31:0] s0;
        logic [31:0] s1;

        d0 = fp32_abs_bits(fp32_sub_bits(in_a0, in_b0));
        d1 = fp32_abs_bits(fp32_sub_bits(in_a1, in_b1));
        d2 = fp32_abs_bits(fp32_sub_bits(in_a2, in_b2));
        d3 = fp32_abs_bits(fp32_sub_bits(in_a3, in_b3));
        s0 = fp32_add_bits(d0, d1);
        s1 = fp32_add_bits(s0, d2);
        result_comb = fp32_add_bits(s1, d3);
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy         <= 1'b0;
            out_distance <= 32'h00000000;
        end else begin
            if (input_accepted) begin
                busy         <= 1'b1;
                out_distance <= result_comb;
            end else if (output_consumed) begin
                busy         <= 1'b0;
                out_distance <= 32'h00000000;
            end
        end
    end

endmodule
