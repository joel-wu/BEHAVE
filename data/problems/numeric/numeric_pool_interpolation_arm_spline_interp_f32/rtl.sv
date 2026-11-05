module cubic_spline_interpolator_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_xq,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y,

    input  logic [31:0] x0,
    input  logic [31:0] x1,
    input  logic [31:0] x2,
    input  logic [31:0] x3,

    input  logic [31:0] y0,
    input  logic [31:0] y1,
    input  logic [31:0] y2,
    input  logic [31:0] y3,

    input  logic [31:0] b0,
    input  logic [31:0] b1,
    input  logic [31:0] b2,

    input  logic [31:0] c0,
    input  logic [31:0] c1,
    input  logic [31:0] c2,

    input  logic [31:0] d0,
    input  logic [31:0] d1,
    input  logic [31:0] d2
);

    logic busy;
    wire  input_accepted;
    wire  output_consumed;

    logic [31:0] sel_xi, sel_yi, sel_bi, sel_ci, sel_di;
    logic [31:0] dx_w, dx2_w, dx3_w, t1_w, t2_w, t3_w, s1_w, s2_w, out_y_w;
    logic signed [1:0] cmp_x1_w, cmp_x2_w;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

// Embedded from tools/data/fp32_rne.svh; keep numerical changes in that template.
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

function automatic logic signed [1:0] fp32_cmp_bits(input logic [31:0] a, b);
    if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
        fp32_cmp_bits = 2'sb10; // -2: unordered, never an ordered <= result.
    else if ((a == b) || ((a[30:0] == 0) && (b[30:0] == 0))) fp32_cmp_bits = 0;
    else if (a[31] != b[31]) fp32_cmp_bits = a[31] ? -2'sd1 : 2'sd1;
    else if ((a[30:0] < b[30:0]) != a[31]) fp32_cmp_bits = -2'sd1;
    else fp32_cmp_bits = 2'sd1;
endfunction


    always_comb begin
        cmp_x1_w = fp32_cmp_bits(in_xq, x1);
        cmp_x2_w = fp32_cmp_bits(in_xq, x2);

        sel_xi = x2;
        sel_yi = y2;
        sel_bi = b2;
        sel_ci = c2;
        sel_di = d2;

        if (cmp_x1_w == -2'sd1 || cmp_x1_w == 0) begin
            sel_xi = x0;
            sel_yi = y0;
            sel_bi = b0;
            sel_ci = c0;
            sel_di = d0;
        end else if (cmp_x2_w == -2'sd1 || cmp_x2_w == 0) begin
            sel_xi = x1;
            sel_yi = y1;
            sel_bi = b1;
            sel_ci = c1;
            sel_di = d1;
        end
    end

    assign dx_w = fp32_add_bits(in_xq, {~sel_xi[31], sel_xi[30:0]});

    assign dx2_w = fp32_mul_bits(dx_w, dx_w);

    assign dx3_w = fp32_mul_bits(dx2_w, dx_w);

    assign t1_w = fp32_mul_bits(sel_bi, dx_w);

    assign t2_w = fp32_mul_bits(sel_ci, dx2_w);

    assign t3_w = fp32_mul_bits(sel_di, dx3_w);

    assign s1_w = fp32_add_bits(sel_yi, t1_w);

    assign s2_w = fp32_add_bits(s1_w, t2_w);

    assign out_y_w = fp32_add_bits(s2_w, t3_w);

    always_ff @(posedge clk) begin
        if (rst) begin
            busy  <= 1'b0;
            out_y <= 32'h00000000;
        end else if (input_accepted) begin
            busy  <= 1'b1;
            out_y <= out_y_w;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
