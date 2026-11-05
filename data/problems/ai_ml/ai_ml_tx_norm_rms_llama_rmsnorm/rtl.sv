module ai_ml_tx_norm_rms_llama_rmsnorm (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x0,
    input  logic [15:0] in_x1,
    input  logic [15:0] in_x2,
    input  logic [15:0] in_x3,
    input  logic [15:0] in_w0,
    input  logic [15:0] in_w1,
    input  logic [15:0] in_w2,
    input  logic [15:0] in_w3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_y0,
    output logic [15:0] out_y1,
    output logic [15:0] out_y2,
    output logic [15:0] out_y3
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

function automatic logic [31:0] fp32_sqrt_bits(input logic [31:0] a);
    integer e, i;
    logic [63:0] mant, radicand, root, rem, trial;
    begin
        if (a[30:0] > 31'h7f800000) fp32_sqrt_bits = 32'h7fc00000;
        else if (a[30:0] == 0) fp32_sqrt_bits = a;
        else if (a[31]) fp32_sqrt_bits = 32'h7fc00000;
        else if (a[30:0] == 31'h7f800000) fp32_sqrt_bits = a;
        else begin
            e = a[30:23] == 0 ? 1 : int'(a[30:23]);
            mant = {40'b0, (a[30:23] != 0), a[22:0]};
            for (i=0; i<23; i=i+1) begin
                if (!mant[23]) begin mant=mant<<1; e=e-1; end
            end
            e = e-127;
            if (e & 1) mant = mant<<1;
            radicand = mant<<29;
            root = 0; rem = 0;
            for (i=26; i>=0; i=i-1) begin
                rem = (rem<<2) | ((radicand>>(2*i)) & 64'd3);
                trial = (root<<2) | 64'd1;
                root = root<<1;
                if (rem >= trial) begin rem=rem-trial; root=root|64'd1; end
            end
            root = root | {63'b0, (rem != 0)};
            fp32_sqrt_bits = fp_round_pack(1'b0, (e >>> 1)+127, root);
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

    localparam logic [31:0] FP32_ONE_QUARTER = 32'h3E800000;
    localparam logic [31:0] FP32_EPSILON     = 32'h358637BD;
    localparam logic [31:0] FP32_ONE         = 32'h3F800000;
    localparam logic [31:0] FP32_POS_INF     = 32'h7F800000;
    localparam logic [31:0] FP32_CANON_QNAN  = 32'h7FC00000;
    localparam logic [15:0] BF16_CANON_QNAN  = 16'h7FC0;

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic fp32_is_zero(input logic [31:0] a);
        begin
            fp32_is_zero = (a[30:0] == 31'd0);
        end
    endfunction

    function automatic logic [31:0] bf16_to_fp32(input logic [15:0] a);
    bf16_to_fp32 = {a, 16'b0};
endfunction

    function automatic logic [15:0] fp32_to_bf16_bits(input logic [31:0] a);
    logic [31:0] rounded;
    begin
        if (a[30:0] > 31'h7f800000) fp32_to_bf16_bits = {a[31], 15'h7fc0};
        else begin
            rounded = a + 32'h00007fff + {31'b0,a[16]};
            fp32_to_bf16_bits = rounded[31:16];
        end
    end
endfunction
function automatic logic [15:0] fp32_to_bf16(input logic [31:0] a);
    fp32_to_bf16 = fp32_to_bf16_bits(a);
endfunction

    logic [31:0] x0_fp, x1_fp, x2_fp, x3_fp;
    logic [31:0] w0_fp, w1_fp, w2_fp, w3_fp;
    logic [31:0] sq0, sq1, sq2, sq3;
    logic [31:0] sum01, sum23, sum_all, mean, mean_eps;
    logic [31:0] sqrt_mean, scale;
    logic [31:0] norm0, norm1, norm2, norm3;
    logic [31:0] prod0, prod1, prod2, prod3;
    logic [15:0] y0_calc, y1_calc, y2_calc, y3_calc;

    always_comb begin
        x0_fp = bf16_to_fp32(in_x0);
        x1_fp = bf16_to_fp32(in_x1);
        x2_fp = bf16_to_fp32(in_x2);
        x3_fp = bf16_to_fp32(in_x3);
        w0_fp = bf16_to_fp32(in_w0);
        w1_fp = bf16_to_fp32(in_w1);
        w2_fp = bf16_to_fp32(in_w2);
        w3_fp = bf16_to_fp32(in_w3);

        sq0 = fp32_mul_bits(x0_fp, x0_fp);
        sq1 = fp32_mul_bits(x1_fp, x1_fp);
        sq2 = fp32_mul_bits(x2_fp, x2_fp);
        sq3 = fp32_mul_bits(x3_fp, x3_fp);

        sum01    = fp32_add_bits(sq0, sq1);
        sum23    = fp32_add_bits(sq2, sq3);
        sum_all  = fp32_add_bits(sum01, sum23);
        mean     = fp32_mul_bits(sum_all, FP32_ONE_QUARTER);
        mean_eps = fp32_add_bits(mean, FP32_EPSILON);

        sqrt_mean = fp32_sqrt_bits(mean_eps);
        if (fp32_is_zero(sqrt_mean)) begin
            scale = FP32_POS_INF;
        end else begin
            scale = fp32_div_bits(FP32_ONE, sqrt_mean);
        end

        norm0 = bf16_to_fp32(fp32_to_bf16(fp32_mul_bits(x0_fp, scale)));
        norm1 = bf16_to_fp32(fp32_to_bf16(fp32_mul_bits(x1_fp, scale)));
        norm2 = bf16_to_fp32(fp32_to_bf16(fp32_mul_bits(x2_fp, scale)));
        norm3 = bf16_to_fp32(fp32_to_bf16(fp32_mul_bits(x3_fp, scale)));

        prod0 = fp32_mul_bits(w0_fp, norm0);
        prod1 = fp32_mul_bits(w1_fp, norm1);
        prod2 = fp32_mul_bits(w2_fp, norm2);
        prod3 = fp32_mul_bits(w3_fp, norm3);

        y0_calc = fp32_to_bf16(prod0);
        y1_calc = fp32_to_bf16(prod1);
        y2_calc = fp32_to_bf16(prod2);
        y3_calc = fp32_to_bf16(prod3);
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy   <= 1'b0;
            out_y0 <= 16'h0000;
            out_y1 <= 16'h0000;
            out_y2 <= 16'h0000;
            out_y3 <= 16'h0000;
        end else if (input_accepted) begin
            busy   <= 1'b1;
            out_y0 <= y0_calc;
            out_y1 <= y1_calc;
            out_y2 <= y2_calc;
            out_y3 <= y3_calc;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
