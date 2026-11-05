/* verilator lint_off WIDTH */
/* verilator lint_off WIDTHEXPAND */
/* verilator lint_off WIDTHTRUNC */
/* verilator lint_off SELRANGE */
module ai_ml_tx_attn_sdpa_gemma2_sdpa_softcap_attn (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_q0,
    input  logic [31:0]  in_q1,
    input  logic [31:0]  in_q2,
    input  logic [31:0]  in_q3,
    input  logic [31:0]  in_k0_0,
    input  logic [31:0]  in_k0_1,
    input  logic [31:0]  in_k0_2,
    input  logic [31:0]  in_k0_3,
    input  logic [31:0]  in_k1_0,
    input  logic [31:0]  in_k1_1,
    input  logic [31:0]  in_k1_2,
    input  logic [31:0]  in_k1_3,
    input  logic [31:0]  in_k2_0,
    input  logic [31:0]  in_k2_1,
    input  logic [31:0]  in_k2_2,
    input  logic [31:0]  in_k2_3,
    input  logic [31:0]  in_k3_0,
    input  logic [31:0]  in_k3_1,
    input  logic [31:0]  in_k3_2,
    input  logic [31:0]  in_k3_3,
    input  logic         in_mask_valid,
    input  logic [31:0]  in_mask0,
    input  logic [31:0]  in_mask1,
    input  logic [31:0]  in_mask2,
    input  logic [31:0]  in_mask3,
    input  logic [31:0]  in_scaling,
    input  logic [31:0]  in_softcap,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_attn0,
    output logic [31:0]  out_attn1,
    output logic [31:0]  out_attn2,
    output logic [31:0]  out_attn3
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

function automatic logic [31:0] fp32_div_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sign_bit;
    logic [63:0] ma, mb, numerator, quotient, remainder;
    begin
        sign_bit = a[31] ^ b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000) ||
            ((a[30:0] == 31'h7f800000) && (b[30:0] == 31'h7f800000)) ||
            ((a[30:0] == 0) && (b[30:0] == 0)))
            fp32_div_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) || (b[30:0] == 0))
            fp32_div_bits = {sign_bit, 8'hff, 23'b0};
        else if ((a[30:0] == 0) || (b[30:0] == 31'h7f800000))
            fp32_div_bits = {sign_bit, 31'b0};
        else begin
            for (i=0; i<23; i=i+1) begin
                if (!ma[23]) begin ma=ma<<1; ea=ea-1; end
                if (!mb[23]) begin mb=mb<<1; eb=eb-1; end
            end
            e = ea-eb+127;
            if (ma < mb) begin ma=ma<<1; e=e-1; end
            numerator = ma<<26;
            quotient = numerator/mb;
            remainder = numerator%mb;
            quotient = quotient | {63'b0, (remainder != 0)};
            fp32_div_bits = fp_round_pack(sign_bit, e, quotient);
        end
    end
endfunction

    function automatic logic fp32_is_nan(input logic [31:0] x);
        begin
            fp32_is_nan = (x[30:23] == 8'hFF) && (x[22:0] != 23'd0);
        end
    endfunction

    localparam logic [31:0] FP32_CANON_QNAN  = 32'h7FC00000;

    function automatic logic [31:0] tanh_fp32(input logic [31:0] x);
        logic signx;
        logic [31:0] absx;
        begin
            signx = x[31];
            absx = {1'b0, x[30:0]};

            if (fp32_is_nan(x)) begin
                tanh_fp32 = 32'h7FC00000;
            end else if (x == 32'h7F800000) begin
                tanh_fp32 = 32'h3F800000;
            end else if (x == 32'hFF800000) begin
                tanh_fp32 = 32'hBF800000;
            end else if (x == 32'h00000000 || x == 32'h80000000) begin
                tanh_fp32 = x;
            end else if (x[30:23] == 8'd0) begin
                tanh_fp32 = {signx, 31'd0};
            end else if (absx < 32'h3D800000) begin
                tanh_fp32 = x;
            end else if (absx < 32'h3E800000) begin
                tanh_fp32 = signx ? 32'hBE4C0000 : 32'h3E4C0000;
            end else if (absx < 32'h3F000000) begin
                tanh_fp32 = signx ? 32'hBECC0000 : 32'h3ECC0000;
            end else if (absx < 32'h3F400000) begin
                tanh_fp32 = signx ? 32'hBF180000 : 32'h3F180000;
            end else if (absx < 32'h3F800000) begin
                tanh_fp32 = signx ? 32'hBF400000 : 32'h3F400000;
            end else if (absx < 32'h3FC00000) begin
                tanh_fp32 = signx ? 32'hBF666666 : 32'h3F666666;
            end else if (absx < 32'h40000000) begin
                tanh_fp32 = signx ? 32'hBF733333 : 32'h3F733333;
            end else if (absx < 32'h40400000) begin
                tanh_fp32 = signx ? 32'hBF7A0000 : 32'h3F7A0000;
            end else if (absx < 32'h40800000) begin
                tanh_fp32 = signx ? 32'hBF7D999A : 32'h3F7D999A;
            end else if (absx < 32'h40A00000) begin
                tanh_fp32 = signx ? 32'hBF7F0000 : 32'h3F7F0000;
            end else begin
                tanh_fp32 = signx ? 32'hBF800000 : 32'h3F800000;
            end
        end
    endfunction

    function automatic logic [31:0] row_compute(
        input logic [31:0] q0,
        input logic [31:0] q1,
        input logic [31:0] q2,
        input logic [31:0] q3,
        input logic [31:0] k0,
        input logic [31:0] k1,
        input logic [31:0] k2,
        input logic [31:0] k3,
        input logic        mask_valid,
        input logic [31:0] mask,
        input logic [31:0] scaling,
        input logic [31:0] softcap
    );
        logic [31:0] p0, p1, p2, p3;
        logic [31:0] s0, s1, dot;
        logic [31:0] scaled, divided, tval, softcapped;
        begin
            p0 = fp32_mul_bits(q0, k0);
            p1 = fp32_mul_bits(q1, k1);
            p2 = fp32_mul_bits(q2, k2);
            p3 = fp32_mul_bits(q3, k3);
            s0 = fp32_add_bits(p0, p1);
            s1 = fp32_add_bits(p2, p3);
            dot = fp32_add_bits(s0, s1);
            scaled = fp32_mul_bits(dot, scaling);
            divided = fp32_div_bits(scaled, softcap);
            tval = tanh_fp32(divided);
            softcapped = fp32_mul_bits(tval, softcap);
            if (mask_valid)
                row_compute = fp32_add_bits(softcapped, mask);
            else
                row_compute = softcapped;
        end
    endfunction

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy      <= 1'b0;
            out_attn0 <= 32'h00000000;
            out_attn1 <= 32'h00000000;
            out_attn2 <= 32'h00000000;
            out_attn3 <= 32'h00000000;
        end else if (input_accepted) begin
            out_attn0 <= row_compute(in_q0, in_q1, in_q2, in_q3, in_k0_0, in_k0_1, in_k0_2, in_k0_3, in_mask_valid, in_mask0, in_scaling, in_softcap);
            out_attn1 <= row_compute(in_q0, in_q1, in_q2, in_q3, in_k1_0, in_k1_1, in_k1_2, in_k1_3, in_mask_valid, in_mask1, in_scaling, in_softcap);
            out_attn2 <= row_compute(in_q0, in_q1, in_q2, in_q3, in_k2_0, in_k2_1, in_k2_2, in_k2_3, in_mask_valid, in_mask2, in_scaling, in_softcap);
            out_attn3 <= row_compute(in_q0, in_q1, in_q2, in_q3, in_k3_0, in_k3_1, in_k3_2, in_k3_3, in_mask_valid, in_mask3, in_scaling, in_softcap);
            busy      <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
