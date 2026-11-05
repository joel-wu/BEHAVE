// Linear interpolation over a fixed 256-entry FP32 table with
// uniform spacing xSpacing = 2^-8.  Implements the spec algorithm:
//
//   t = fp32_div(in_x - x1, xSpacing)
//   i = fp32_to_int32_sat(t)
//   if x < x1            -> table[0]
//   else if i_u >= 255   -> table[255]
//   else                 -> y0 + (x - x0) * ((y1 - y0) / (x1_seg - x0))
//
// Fixed power-of-two scaling uses full gradual-underflow rounding.

module linear_interp_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y,

    output logic        table_req_valid,
    output logic [7:0]  table_req_addr,
    input  logic        table_req_ready,
    input  logic        table_resp_valid,
    input  logic [31:0] table_resp_rdata
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

    localparam logic [31:0] FP32_CANON_NAN = 32'h7FC00000;
    localparam logic [31:0] FP32_INF_POS   = 32'h7F800000;
    localparam logic [31:0] X1             = 32'h00000000; // +0.0
    localparam logic [31:0] XSPACING       = 32'h3B800000; // 2^-8 = 1/256

    // ── FP32 helper: signed-int32 saturating cast (matches fp_lib.fp32_to_int32_sat) ──
    function automatic logic signed [31:0] fp32_to_int32_cast(input logic [31:0] x);
        logic        sign;
        logic [7:0]  exp;
        logic [22:0] frac;
        logic [23:0] mant;
        logic signed [31:0] e;
        logic [63:0] mag64;
        begin
            sign = x[31];
            exp  = x[30:23];
            frac = x[22:0];
            mant = {1'b1, frac};
            e    = $signed({1'b0, exp}) - 32'sd127;
            mag64 = 64'd0;

            if (exp == 8'h00) begin
                fp32_to_int32_cast = 32'sd0;
            end else if (exp == 8'hFF) begin
                if (frac != 23'd0) begin
                    fp32_to_int32_cast = 32'sd0;
                end else if (sign) begin
                    fp32_to_int32_cast = -32'sd2147483648;
                end else begin
                    fp32_to_int32_cast = 32'sd2147483647;
                end
            end else if (e >= 32'sd31) begin
                if (sign) begin
                    fp32_to_int32_cast = -32'sd2147483648;
                end else begin
                    fp32_to_int32_cast = 32'sd2147483647;
                end
            end else begin
                if (e < 0) begin
                    mag64 = 64'd0;
                end else if (e >= 23) begin
                    mag64 = ({40'd0, mant} << (e - 32'sd23));
                end else begin
                    mag64 = ({40'd0, mant} >> (32'sd23 - e));
                end
                if (sign) begin
                    fp32_to_int32_cast = -$signed(mag64[31:0]);
                end else begin
                    fp32_to_int32_cast = $signed(mag64[31:0]);
                end
            end
        end
    endfunction

    // Fixed +/-8 scaling, including subnormal normalization and rounding.
    function automatic logic [31:0] fp32_scale_pow2(input logic [31:0] x, input logic signed [9:0] k);
    // This task calls only k=+8 or -8; use the full rounding primitive.
    fp32_scale_pow2 = fp32_mul_bits(x, k == 10'sd8 ? 32'h43800000 : 32'h3b800000);
endfunction

    // ── `fp32_cmp(x, x1) < 0` test where x1 = +0.  Note that
    //    `fp_lib.fp32_cmp` returns -2 on NaN, so behavior treats NaN
    //    as `< 0` here (it routes NaN inputs to `table[0]`).  Match
    //    that semantic exactly, otherwise NaN inputs diverge between
    //    behavior (low-bound clamp) and RTL (interpolation path).
    function automatic logic fp32_lt_zero(input logic [31:0] x);
        logic [7:0] exp_x;
        logic [22:0] frac_x;
        begin
            exp_x  = x[30:23];
            frac_x = x[22:0];
            if ((exp_x == 8'hFF) && (frac_x != 23'd0)) begin
                // NaN: fp_lib.fp32_cmp returns -2, so `< 0` is true
                fp32_lt_zero = 1'b1;
            end else if (x[31] && ((exp_x != 8'h00) || (frac_x != 23'd0))) begin
                // Strictly negative finite or -Inf
                fp32_lt_zero = 1'b1;
            end else begin
                fp32_lt_zero = 1'b0;
            end
        end
    endfunction

    // ── fp32_from_uint8(k): exact FP32 of integer k for 0 ≤ k ≤ 255.
    //    Priority-encode the highest set bit; place k's lower bits in
    //    the mantissa.  Matches behavior.py's fp32_from_uint8 exactly
    //    (no rounding because 0..255 is always representable).
    function automatic logic [31:0] fp32_from_uint8(input logic [7:0] k);
        logic [3:0] msb;
        logic [22:0] frac_out;
        logic [7:0] exp_out;
        begin
            if (k == 8'd0) begin
                fp32_from_uint8 = 32'd0;
            end else begin
                // Find highest set bit (priority encoder).  Bottom-up
                // overwrite naturally lands on the highest set bit.
                msb = 4'd0;
                for (int j = 0; j < 8; j = j + 1) begin
                    if (k[j]) msb = j[3:0];
                end
                exp_out = 8'd127 + {4'd0, msb};
                // mantissa = (k - 2^msb) << (23 - msb)
                frac_out = ({15'd0, k} - ({15'd0, 8'd1} << msb)) << (5'd23 - {1'b0, msb});
                fp32_from_uint8 = {1'b0, exp_out, frac_out};
            end
        end
    endfunction

    // ── Combinational FP units (full IEEE-754 RNE; copied from the
    //    rescued bilinear interpolator with the lead-bit / mant_round
    //    / exponent-overflow fixes already applied) ──

    // ── State machine ──
    typedef enum logic [3:0] {
        ST_IDLE,
        ST_CLASSIFY,
        ST_REQ_Y0,
        ST_WAIT_Y0,
        ST_REQ_Y1,
        ST_WAIT_Y1,
        ST_COMPUTE,
        ST_OUT
    } state_t;

    state_t      state;
    logic        busy;

    logic [31:0] x_reg;
    logic [7:0]  i_clamped;       // segment index in [0, 254]
    logic        path_low;        // x < x1 → return table[0]
    logic        path_high;       // i_u >= 255 → return table[255]
    logic [7:0]  fetch_addr;
    logic [31:0] y0_reg, y1_reg;
    logic [31:0] result_reg;

    // Combinational: classify the input once it has been latched
    logic [31:0] t_comb;
    logic signed [31:0] i_signed_comb;
    logic [31:0] i_unsigned_comb;
    logic        lt_zero_comb;
    logic        is_high_comb;
    logic [7:0]  i_clamped_comb;

    always_comb begin
        // t = (x_reg - 0) / (1/256) = mul_by_256 (exact, no rounding)
        t_comb = fp32_scale_pow2(fp32_sub_bits(x_reg, X1), 10'sd8);
        i_signed_comb   = fp32_to_int32_cast(t_comb);
        i_unsigned_comb = i_signed_comb;            // bit-cast (two's-complement)
        lt_zero_comb    = fp32_lt_zero(x_reg);
        // i_u >= nValues - 1 = 255  →  high path
        is_high_comb    = (i_unsigned_comb >= 32'd255);
        // Otherwise i is in [0, 254]
        i_clamped_comb  = i_signed_comb[7:0];
    end

    // Final-result combinational network (only valid in ST_OUT after
    // both y0_reg and y1_reg have been populated; for the boundary
    // paths, result_reg is set directly to y0_reg in ST_WAIT_Y0).
    logic [31:0] x0_fp, x1_seg_fp, num, den, frac, dx, mul_dx_frac, interp_y;

    always_comb begin
        // x0 = i/256, x1_seg = (i+1)/256.  fp32_from_uint8 produces
        // the exact FP32 of an integer in [0,255]; mul by 1/256 is

        x0_fp     = fp32_scale_pow2(fp32_from_uint8(i_clamped),         -10'sd8);
        x1_seg_fp = fp32_scale_pow2(fp32_from_uint8(i_clamped + 8'd1),  -10'sd8);

        num = fp32_sub_bits(y1_reg, y0_reg);
        // den = x1_seg - x0 = 1/256 = XSPACING (constant by construction
        // of the uniformly-spaced grid; identical bit pattern to
        // fp_lib.fp32_sub of the two computed neighbours).
        den = XSPACING;
        // frac = num / (1/256) = num * 256.  Exact.
        frac        = fp32_scale_pow2(num, 10'sd8);
        dx          = fp32_sub_bits(x_reg, x0_fp);
        mul_dx_frac = fp32_mul_bits(dx, frac);
        interp_y    = fp32_add_bits(y0_reg, mul_dx_frac);
        // den is participating in the result chain only via `frac`;
        // referenced here so verilator does not warn on unused signal.
        if (den == 32'd0) begin /* unreachable for XSPACING constant */ end
    end

    wire input_accepted  = in_valid  & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready        = ~busy | output_consumed;
    assign out_valid       = busy && (state == ST_OUT);
    assign out_y           = result_reg;

    assign table_req_valid = (state == ST_REQ_Y0) || (state == ST_REQ_Y1);
    assign table_req_addr  = fetch_addr;

    always_ff @(posedge clk) begin
        if (rst) begin
            state      <= ST_IDLE;
            busy       <= 1'b0;
            x_reg      <= 32'd0;
            i_clamped  <= 8'd0;
            path_low   <= 1'b0;
            path_high  <= 1'b0;
            fetch_addr <= 8'd0;
            y0_reg     <= 32'd0;
            y1_reg     <= 32'd0;
            result_reg <= 32'd0;
        end else begin
            if (input_accepted) begin
                busy       <= 1'b1;
                x_reg      <= in_x;
                state      <= ST_CLASSIFY;
            end else if (output_consumed) begin
                busy       <= 1'b0;
                state      <= ST_IDLE;
            end else begin
                case (state)
                    ST_CLASSIFY: begin
                        path_low   <= lt_zero_comb;
                        path_high  <= !lt_zero_comb && is_high_comb;
                        i_clamped  <= i_clamped_comb;
                        if (lt_zero_comb) begin
                            fetch_addr <= 8'd0;
                            state      <= ST_REQ_Y0;
                        end else if (is_high_comb) begin
                            fetch_addr <= 8'd255;
                            state      <= ST_REQ_Y0;
                        end else begin
                            fetch_addr <= i_clamped_comb;
                            state      <= ST_REQ_Y0;
                        end
                    end

                    ST_REQ_Y0: begin
                        if (table_req_ready) state <= ST_WAIT_Y0;
                    end

                    ST_WAIT_Y0: begin
                        if (table_resp_valid) begin
                            y0_reg <= table_resp_rdata;
                            if (path_low || path_high) begin
                                result_reg <= table_resp_rdata;
                                state      <= ST_OUT;
                            end else begin
                                fetch_addr <= i_clamped + 8'd1;
                                state      <= ST_REQ_Y1;
                            end
                        end
                    end

                    ST_REQ_Y1: begin
                        if (table_req_ready) state <= ST_WAIT_Y1;
                    end

                    ST_WAIT_Y1: begin
                        if (table_resp_valid) begin
                            y1_reg     <= table_resp_rdata;
                            state      <= ST_COMPUTE;
                        end
                    end

                    ST_COMPUTE: begin
                        // y1_reg is now valid (latched in WAIT_Y1).
                        // interp_y is combinational over y0_reg/y1_reg/
                        // x_reg/i_clamped — register it before raising
                        // out_valid so the consumer never observes a
                        // stale result_reg in the first ST_OUT cycle.
                        result_reg <= interp_y;
                        state      <= ST_OUT;
                    end

                    ST_OUT: begin
                        // hold result_reg until consumed
                    end

                    default: ;
                endcase
            end
        end
    end

endmodule
