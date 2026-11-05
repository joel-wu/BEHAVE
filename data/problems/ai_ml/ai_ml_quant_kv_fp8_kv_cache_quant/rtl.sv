module ai_ml_quant_kv_fp8_kv_cache_quant (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_k0_bf16,
    input  logic [15:0]  in_k1_bf16,
    input  logic [15:0]  in_k2_bf16,
    input  logic [15:0]  in_k3_bf16,
    input  logic [31:0]  in_amax_fp32,
    input  logic [15:0]  in_head_dim,
    input  logic [15:0]  in_quant_block_size,
    input  logic [15:0]  in_cache_block_size,
    input  logic [31:0]  in_slot_mapping,
    input  logic [15:0]  in_token_idx,
    input  logic [15:0]  in_head_dim_idx,
    input  logic         in_use_ue8m0,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_q0_fp8,
    output logic [7:0]   out_q1_fp8,
    output logic [7:0]   out_q2_fp8,
    output logic [7:0]   out_q3_fp8,
    output logic [31:0]  out_scale_fp32,
    output logic [31:0]  out_dst_offset,
    output logic [31:0]  out_dst_scale_idx,
    output logic         out_write_enable
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

    localparam logic [31:0] FP32_448  = 32'h43E00000;
    localparam logic [31:0] FP32_1E4  = 32'h38D1B717;
    localparam logic [31:0] CANON_NAN = 32'h7FC00000;

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic is_nan(input logic [31:0] a);
        begin is_nan = (a[30:23] == 8'hFF) && (a[22:0] != 23'd0); end
    endfunction
    function automatic logic is_inf(input logic [31:0] a);
        begin is_inf = (a[30:23] == 8'hFF) && (a[22:0] == 23'd0); end
    endfunction

    function automatic logic signed [1:0] fp32_cmp_bits(input logic [31:0] a, b);
    if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000))
        fp32_cmp_bits = 2'sb10; // -2: unordered, never an ordered <= result.
    else if ((a == b) || ((a[30:0] == 0) && (b[30:0] == 0))) fp32_cmp_bits = 0;
    else if (a[31] != b[31]) fp32_cmp_bits = a[31] ? -2'sd1 : 2'sd1;
    else if ((a[30:0] < b[30:0]) != a[31]) fp32_cmp_bits = -2'sd1;
    else fp32_cmp_bits = 2'sd1;
endfunction
function automatic integer fp32_cmp_fn(input logic [31:0] a, b);
    fp32_cmp_fn = int'($signed(fp32_cmp_bits(a,b)));
endfunction

    function automatic logic [31:0] bf16_to_fp32_fn(input logic [15:0] a);
        begin bf16_to_fp32_fn = {a, 16'h0000}; end
    endfunction

    function automatic logic [31:0] clamp_amax_fn(input logic [31:0] a);
        begin
            if (is_nan(a)) clamp_amax_fn = FP32_1E4;
            else if (fp32_cmp_fn(a, FP32_1E4) < 0) clamp_amax_fn = FP32_1E4;
            else clamp_amax_fn = a;
        end
    endfunction

    function automatic logic [31:0] ceil_pow2_fn(input logic [31:0] x);
        logic [7:0] exp;
        logic [22:0] frac;
        logic needs_bump;
        logic [8:0] bumped;
        begin
            exp = x[30:23];
            frac = x[22:0];
            needs_bump = (frac != 23'd0) ? 1'b1 : 1'b0;
            bumped = exp + {7'd0, needs_bump};
            if (bumped == 9'd0) ceil_pow2_fn = 32'h00800000;
            else if (bumped >= 9'h0FF) ceil_pow2_fn = 32'h7F800000;
            else ceil_pow2_fn = {1'b0, bumped[7:0], 23'd0};
        end
    endfunction

    function automatic logic [4:0] highest_set_23(input logic [22:0] x);
        integer i;
        logic [4:0] pos;
        begin
            pos = 5'd0;
            for (i = 0; i < 23; i = i + 1) begin
                if (x[i]) pos = i[4:0];
            end
            highest_set_23 = pos;
        end
    endfunction

    function automatic logic [7:0] encode_e4m3(input logic [31:0] y);
        logic sign;
        logic [7:0] exp;
        logic [22:0] frac;
        logic subnormal_input;
        logic [4:0] leading_one_pos;
        logic signed [9:0] exponent_unbiased;
        logic [23:0] significand24;
        logic signed [9:0] target_exp;
        logic [4:0] keep;
        logic [19:0] lost;
        logic [19:0] half20;
        logic [4:0] total_shift;
        logic [23:0] keep_sub;
        logic [23:0] lost_sub;
        logic [23:0] half_sub;
        begin
            sign = y[31];
            exp = y[30:23];
            frac = y[22:0];
            encode_e4m3 = 8'h00;

            if (is_nan(y)) encode_e4m3 = 8'h7F;
            else if (is_inf(y)) encode_e4m3 = sign ? 8'hFE : 8'h7E;
            else if (exp == 8'd0 && frac == 23'd0) encode_e4m3 = sign ? 8'h80 : 8'h00;
            else begin
                subnormal_input = (exp == 8'd0);
                leading_one_pos = highest_set_23(frac);
                if (subnormal_input) begin
                    exponent_unbiased = $signed({5'd0, leading_one_pos}) - 10'sd149;
                    significand24 = ({1'b0, frac} << (5'd23 - leading_one_pos)) & 24'hFFFFFF;
                end else begin
                    exponent_unbiased = $signed({2'b00, exp}) - 10'sd127;
                    significand24 = {1'b1, frac};
                end
                target_exp = exponent_unbiased + 10'sd7;

                if (target_exp >= 10'sd1) begin
                    if (target_exp > 10'sd15) begin
                        encode_e4m3 = sign ? 8'hFE : 8'h7E;
                    end else begin
                        keep = {1'b0, significand24[23:20]};
                        lost = significand24[19:0];
                        half20 = 20'h80000;
                        if ((lost > half20) || ((lost == half20) && keep[0])) keep = keep + 5'd1;
                        if (keep == 5'd16) begin
                            keep = 5'd8;
                            target_exp = target_exp + 10'sd1;
                            if (target_exp > 10'sd15) begin
                                encode_e4m3 = sign ? 8'hFE : 8'h7E;
                            end else begin
                                encode_e4m3 = {sign, target_exp[3:0], keep[2:0]};
                            end
                        end else begin
                            if (target_exp == 10'sd15 && keep > 5'd14) keep = 5'd14;
                            encode_e4m3 = {sign, target_exp[3:0], keep[2:0]};
                        end
                    end
                end else begin
                    // target_exp < 1 → subnormal e4m3
                    begin
                        logic signed [10:0] ts_signed;
                        logic [31:0] sig32;
                        logic [31:0] keep32;
                        logic [31:0] lost32;
                        logic [31:0] half32;
                        logic [31:0] mask32;
                        ts_signed = 11'sd14 - exponent_unbiased;
                        sig32 = {8'd0, significand24};
                        keep32 = 32'd0;
                        lost32 = 32'd0;
                        half32 = 32'd0;
                        mask32 = 32'd0;
                        if (ts_signed > 11'sd31 || ts_signed <= 11'sd0) begin
                            encode_e4m3 = sign ? 8'h80 : 8'h00;
                        end else begin
                            keep32 = sig32 >> ts_signed[4:0];
                            mask32 = (32'd1 << ts_signed[4:0]) - 32'd1;
                            lost32 = sig32 & mask32;
                            half32 = 32'd1 << (ts_signed[4:0] - 5'd1);
                            if ((lost32 > half32) || ((lost32 == half32) && keep32[0])) keep32 = keep32 + 32'd1;
                            if (keep32 >= 32'd8) encode_e4m3 = {sign, 4'd1, 3'd0};
                            else if (keep32 == 32'd0) encode_e4m3 = sign ? 8'h80 : 8'h00;
                            else encode_e4m3 = {sign, 4'd0, keep32[2:0]};
                        end
                    end
                end
            end
        end
    endfunction

    // Address calculation widths:
    // slot_idx max ~2^31, head_dim 16-bit, cache_block_size 16-bit, quant_block_size 16-bit.
    // block_idx = slot/cbs <= 2^31 / 1 = 2^31
    // cache_stride = head_dim + head_dim*4/qbs. head_dim * 4 may overflow 16 bits, use 32-bit.
    // dst_offset = block_idx * cbs * cache_stride + ... up to ~2^31 * 2^16 * 2^18 = huge,
    // but we only output 32-bit, so mod 2^32 is fine.

    logic [31:0] amax_eff;
    logic [31:0] scale_pre;
    logic [31:0] scale_c;
    logic [31:0] x0_fp32, x1_fp32, x2_fp32, x3_fp32;
    logic [31:0] y0, y1, y2, y3;
    logic [7:0]  q0, q1, q2, q3;
    logic signed [31:0] slot_idx_s;
    logic invalid;
    logic [31:0] block_idx;
    logic [31:0] block_offset;
    logic [31:0] cache_stride;
    logic [31:0] dst_offset_raw;
    logic [31:0] dst_scale_idx_raw;
    logic [63:0] dsi_inner_64;
    logic [31:0] dsi_inner_32;

    logic [7:0]  q0_next, q1_next, q2_next, q3_next;
    logic [31:0] scale_next, dst_offset_next, dst_scale_idx_next;
    logic        we_next;

    always_comb begin
        slot_idx_s = $signed(in_slot_mapping);
        invalid = (slot_idx_s < 32'sd0) || (in_quant_block_size == 16'd0) || (in_cache_block_size == 16'd0) || (in_head_dim_idx >= in_head_dim);

        amax_eff = clamp_amax_fn(in_amax_fp32);
        scale_pre = fp32_div_bits(amax_eff, FP32_448);
        if (in_use_ue8m0) scale_c = ceil_pow2_fn(scale_pre);
        else              scale_c = scale_pre;

        x0_fp32 = bf16_to_fp32_fn(in_k0_bf16);
        x1_fp32 = bf16_to_fp32_fn(in_k1_bf16);
        x2_fp32 = bf16_to_fp32_fn(in_k2_bf16);
        x3_fp32 = bf16_to_fp32_fn(in_k3_bf16);

        y0 = fp32_div_bits(x0_fp32, scale_c);
        y1 = fp32_div_bits(x1_fp32, scale_c);
        y2 = fp32_div_bits(x2_fp32, scale_c);
        y3 = fp32_div_bits(x3_fp32, scale_c);

        q0 = encode_e4m3(y0);
        q1 = encode_e4m3(y1);
        q2 = encode_e4m3(y2);
        q3 = encode_e4m3(y3);

        // Address calc — only meaningful if valid
        if (in_cache_block_size != 16'd0) begin
            block_idx = $unsigned(slot_idx_s) / {16'd0, in_cache_block_size};
            block_offset = $unsigned(slot_idx_s) % {16'd0, in_cache_block_size};
        end else begin
            block_idx = 32'd0;
            block_offset = 32'd0;
        end

        if (in_quant_block_size != 16'd0) begin
            cache_stride = {16'd0, in_head_dim} + ({14'd0, in_head_dim, 2'b00} / {16'd0, in_quant_block_size});
        end else begin
            cache_stride = {16'd0, in_head_dim};
        end

        // Use 64-bit for the divide (numerator can need 34 bits), then take low 32 bits.
        if (in_quant_block_size != 16'd0) begin
            dsi_inner_64 = (({32'd0, block_offset} * {48'd0, in_head_dim} + {48'd0, in_head_dim_idx}) * 64'd4)
                          / {48'd0, in_quant_block_size};
        end else begin
            dsi_inner_64 = 64'd0;
        end
        dsi_inner_32 = dsi_inner_64[31:0];

        // Address arithmetic mod 2^32 — multiplication mod is closed under mod, so use 32-bit.
        dst_offset_raw = block_idx * {16'd0, in_cache_block_size} * cache_stride
                        + block_offset * {16'd0, in_head_dim}
                        + {16'd0, in_head_dim_idx};

        if (in_quant_block_size != 16'd0) begin
            dst_scale_idx_raw = block_idx * {16'd0, in_cache_block_size} * cache_stride
                                + {16'd0, in_cache_block_size} * {16'd0, in_head_dim}
                                + dsi_inner_32;
        end else begin
            dst_scale_idx_raw = 32'd0;
        end

        if (invalid) begin
            q0_next = 8'd0;
            q1_next = 8'd0;
            q2_next = 8'd0;
            q3_next = 8'd0;
            scale_next = 32'd0;
            dst_offset_next = 32'd0;
            dst_scale_idx_next = 32'd0;
            we_next = 1'b0;
        end else begin
            q0_next = q0;
            q1_next = q1;
            q2_next = q2;
            q3_next = q3;
            scale_next = scale_c;
            dst_offset_next = dst_offset_raw;
            dst_scale_idx_next = dst_scale_idx_raw;
            we_next = 1'b1;
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy <= 1'b0;
            out_q0_fp8 <= 8'd0;
            out_q1_fp8 <= 8'd0;
            out_q2_fp8 <= 8'd0;
            out_q3_fp8 <= 8'd0;
            out_scale_fp32 <= 32'd0;
            out_dst_offset <= 32'd0;
            out_dst_scale_idx <= 32'd0;
            out_write_enable <= 1'b0;
        end else if (input_accepted) begin
            busy <= 1'b1;
            out_q0_fp8 <= q0_next;
            out_q1_fp8 <= q1_next;
            out_q2_fp8 <= q2_next;
            out_q3_fp8 <= q3_next;
            out_scale_fp32 <= scale_next;
            out_dst_offset <= dst_offset_next;
            out_dst_scale_idx <= dst_scale_idx_next;
            out_write_enable <= we_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
