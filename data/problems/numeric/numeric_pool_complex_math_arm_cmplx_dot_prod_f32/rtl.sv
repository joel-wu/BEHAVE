module complex_dot_product_f32x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_base_a,
    input  logic [7:0]  in_base_b,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_real,
    output logic [31:0] out_imag,

    output logic        vec_a_req_valid,
    output logic [7:0]  vec_a_req_addr,
    input  logic        vec_a_req_ready,
    input  logic        vec_a_resp_valid,
    input  logic [31:0] vec_a_resp_rdata,

    output logic        vec_b_req_valid,
    output logic [7:0]  vec_b_req_addr,
    input  logic        vec_b_req_ready,
    input  logic        vec_b_resp_valid,
    input  logic [31:0] vec_b_resp_rdata
);

    typedef enum logic [2:0] {
        ST_IDLE    = 3'd0,
        ST_REQ     = 3'd1,
        ST_WAIT    = 3'd2,
        ST_COMPUTE = 3'd3,
        ST_OUT     = 3'd4
    } state_t;

    state_t state;

    logic [7:0] base_a_reg, base_b_reg;
    logic [3:0] req_idx_a, req_idx_b;
    logic [3:0] recv_idx_a, recv_idx_b;
    logic [31:0] a_mem [0:7];
    logic [31:0] b_mem [0:7];

    logic [31:0] real_sum_reg, imag_sum_reg;
    logic [2:0] comp_idx;

    logic [31:0] ar_cur, ai_cur, br_cur, bi_cur;
    logic [31:0] m0_res, m1_res, m2_res, m3_res;
    logic [31:0] partial_real_sum, partial_imag_sum;
    logic [31:0] next_real_sum, next_imag_sum;

    logic input_accepted;
    logic output_consumed;
    logic all_a_received_now, all_b_received_now;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = (state == ST_IDLE);
    assign out_valid = (state == ST_OUT);

    assign all_a_received_now = (recv_idx_a == 4'd8) || ((recv_idx_a == 4'd7) && vec_a_resp_valid);
    assign all_b_received_now = (recv_idx_b == 4'd8) || ((recv_idx_b == 4'd7) && vec_b_resp_valid);

    always_comb begin
        vec_a_req_valid = 1'b0;
        vec_a_req_addr  = 8'h00;
        vec_b_req_valid = 1'b0;
        vec_b_req_addr  = 8'h00;

        if (state == ST_REQ) begin
            vec_a_req_valid = (req_idx_a < 4'd8);
            vec_b_req_valid = (req_idx_b < 4'd8);
            vec_a_req_addr  = base_a_reg + {4'd0, req_idx_a};
            vec_b_req_addr  = base_b_reg + {4'd0, req_idx_b};
        end
    end

    always_comb begin
        ar_cur = 32'h00000000;
        ai_cur = 32'h00000000;
        br_cur = 32'h00000000;
        bi_cur = 32'h00000000;

        unique case (comp_idx)
            3'd0: begin
                ar_cur = a_mem[0];
                ai_cur = a_mem[1];
                br_cur = b_mem[0];
                bi_cur = b_mem[1];
            end
            3'd1: begin
                ar_cur = a_mem[2];
                ai_cur = a_mem[3];
                br_cur = b_mem[2];
                bi_cur = b_mem[3];
            end
            3'd2: begin
                ar_cur = a_mem[4];
                ai_cur = a_mem[5];
                br_cur = b_mem[4];
                bi_cur = b_mem[5];
            end
            3'd3: begin
                ar_cur = a_mem[6];
                ai_cur = a_mem[7];
                br_cur = b_mem[6];
                bi_cur = b_mem[7];
            end
            default: begin
                ar_cur = 32'h00000000;
                ai_cur = 32'h00000000;
                br_cur = 32'h00000000;
                bi_cur = 32'h00000000;
            end
        endcase
    end

    fp32_mul mul0 (.a(ar_cur), .b(br_cur), .y(m0_res));
    fp32_mul mul1 (.a(ai_cur), .b(bi_cur), .y(m1_res));
    fp32_mul mul2 (.a(ar_cur), .b(bi_cur), .y(m2_res));
    fp32_mul mul3 (.a(ai_cur), .b(br_cur), .y(m3_res));
    // Scalar order: round each product addition/subtraction separately.
    fp32_add add_real_term (.a(real_sum_reg), .b(m0_res), .y(partial_real_sum));
    fp32_sub sub_real_term (.a(partial_real_sum), .b(m1_res), .y(next_real_sum));
    fp32_add add_imag_term0 (.a(imag_sum_reg), .b(m2_res), .y(partial_imag_sum));
    fp32_add add_imag_term1 (.a(partial_imag_sum), .b(m3_res), .y(next_imag_sum));

    integer i;
    always_ff @(posedge clk) begin
        if (rst) begin
            state        <= ST_IDLE;
            base_a_reg   <= 8'h00;
            base_b_reg   <= 8'h00;
            req_idx_a    <= 4'd0;
            req_idx_b    <= 4'd0;
            recv_idx_a   <= 4'd0;
            recv_idx_b   <= 4'd0;
            real_sum_reg <= 32'h00000000;
            imag_sum_reg <= 32'h00000000;
            comp_idx     <= 3'd0;
            out_real     <= 32'h00000000;
            out_imag     <= 32'h00000000;
            for (i = 0; i < 8; i = i + 1) begin
                a_mem[i] <= 32'h00000000;
                b_mem[i] <= 32'h00000000;
            end
        end else begin
            case (state)
                ST_IDLE: begin
                    if (input_accepted) begin
                        base_a_reg   <= in_base_a;
                        base_b_reg   <= in_base_b;
                        req_idx_a    <= 4'd0;
                        req_idx_b    <= 4'd0;
                        recv_idx_a   <= 4'd0;
                        recv_idx_b   <= 4'd0;
                        real_sum_reg <= 32'h00000000;
                        imag_sum_reg <= 32'h00000000;
                        comp_idx     <= 3'd0;
                        state        <= ST_REQ;
                    end
                end

                ST_REQ: begin
                    if (vec_a_resp_valid && recv_idx_a < 4'd8) begin
                        a_mem[recv_idx_a[2:0]] <= vec_a_resp_rdata;
                        recv_idx_a <= recv_idx_a + 4'd1;
                    end
                    if (vec_b_resp_valid && recv_idx_b < 4'd8) begin
                        b_mem[recv_idx_b[2:0]] <= vec_b_resp_rdata;
                        recv_idx_b <= recv_idx_b + 4'd1;
                    end
                    if (vec_a_req_valid && vec_a_req_ready)
                        req_idx_a <= req_idx_a + 4'd1;
                    if (vec_b_req_valid && vec_b_req_ready)
                        req_idx_b <= req_idx_b + 4'd1;
                    if (((req_idx_a == 4'd8) || ((req_idx_a == 4'd7) && vec_a_req_ready)) &&
                        ((req_idx_b == 4'd8) || ((req_idx_b == 4'd7) && vec_b_req_ready)))
                        state <= ST_WAIT;
                end

                ST_WAIT: begin
                    if (vec_a_resp_valid) begin
                        a_mem[recv_idx_a[2:0]] <= vec_a_resp_rdata;
                        recv_idx_a <= recv_idx_a + 4'd1;
                    end
                    if (vec_b_resp_valid) begin
                        b_mem[recv_idx_b[2:0]] <= vec_b_resp_rdata;
                        recv_idx_b <= recv_idx_b + 4'd1;
                    end

                    if (all_a_received_now && all_b_received_now) begin
                        real_sum_reg <= 32'h00000000;
                        imag_sum_reg <= 32'h00000000;
                        comp_idx     <= 3'd0;
                        state        <= ST_COMPUTE;
                    end
                end

                ST_COMPUTE: begin
                    real_sum_reg <= next_real_sum;
                    imag_sum_reg <= next_imag_sum;
                    if (comp_idx == 3'd3) begin
                        out_real <= next_real_sum;
                        out_imag <= next_imag_sum;
                        state    <= ST_OUT;
                    end else begin
                        comp_idx <= comp_idx + 3'd1;
                    end
                end

                ST_OUT: begin
                    if (output_consumed) begin
                        state <= ST_IDLE;
                    end
                end

                default: begin
                    state <= ST_IDLE;
                end
            endcase
        end
    end

endmodule

module fp32_add (
    input  logic [31:0] a,
    input  logic [31:0] b,
    output logic [31:0] y
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
assign y = fp32_add_bits(a, b);
endmodule

module fp32_sub (
    input  logic [31:0] a,
    input  logic [31:0] b,
    output logic [31:0] y
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
assign y = fp32_add_bits(a, b ^ 32'h80000000);
endmodule

module fp32_addsub_core (
    input  logic [31:0] a,
    input  logic [31:0] b,
    input  logic        sub,
    output logic [31:0] y
);
    localparam logic [31:0] FP32_CANONICAL_NAN = 32'h7FC00000;

    logic sign_a, sign_b_eff, sign_b_raw;
    logic [7:0] exp_a_raw, exp_b_raw;
    logic [22:0] frac_a_raw, frac_b_raw;

    logic a_is_nan, b_is_nan, a_is_inf, b_is_inf, a_is_zero, b_is_zero;
    logic [7:0] exp_a, exp_b;
    logic [23:0] mant_a, mant_b;

    logic [7:0] exp_big, exp_small;
    logic [23:0] mant_big, mant_small;
    logic sign_big, sign_small;

    logic [7:0] exp_diff;
    logic [26:0] ext_big, ext_small_pre, ext_small;
    logic [27:0] sum_ext;
    logic [26:0] diff_ext;
    logic [7:0] exp_res_pre;
    logic sign_res_pre;
    logic [26:0] mag_pre;

    logic [4:0] lead_pos;
    logic [7:0] shift_left_amt;
    logic [8:0] exp_norm;
    logic [26:0] mag_norm;

    logic [23:0] mant_main;
    logic guard_bit, round_bit, sticky_bit, increment;
    logic [24:0] mant_rounded;
    logic [8:0] exp_rounded;
    logic [22:0] frac_final;

    integer idx;

    always_comb begin
        sign_a         = a[31];
        sign_b_raw     = b[31];
        sign_b_eff     = sign_b_raw ^ sub;
        exp_a_raw      = a[30:23];
        exp_b_raw      = b[30:23];
        frac_a_raw     = a[22:0];
        frac_b_raw     = b[22:0];

        a_is_nan       = (exp_a_raw == 8'hFF) && (frac_a_raw != 23'd0);
        b_is_nan       = (exp_b_raw == 8'hFF) && (frac_b_raw != 23'd0);
        a_is_inf       = (exp_a_raw == 8'hFF) && (frac_a_raw == 23'd0);
        b_is_inf       = (exp_b_raw == 8'hFF) && (frac_b_raw == 23'd0);
        a_is_zero      = (exp_a_raw == 8'h00);
        b_is_zero      = (exp_b_raw == 8'h00);

        exp_a          = 8'd0;
        exp_b          = 8'd0;
        mant_a         = 24'd0;
        mant_b         = 24'd0;
        exp_big        = 8'd0;
        exp_small      = 8'd0;
        mant_big       = 24'd0;
        mant_small     = 24'd0;
        sign_big       = 1'b0;
        sign_small     = 1'b0;
        exp_diff       = 8'd0;
        ext_big        = 27'd0;
        ext_small_pre  = 27'd0;
        ext_small      = 27'd0;
        sum_ext        = 28'd0;
        diff_ext       = 27'd0;
        exp_res_pre    = 8'd0;
        sign_res_pre   = 1'b0;
        mag_pre        = 27'd0;
        lead_pos       = 5'd0;
        shift_left_amt = 8'd0;
        exp_norm       = 9'd0;
        mag_norm       = 27'd0;
        mant_main      = 24'd0;
        guard_bit      = 1'b0;
        round_bit      = 1'b0;
        sticky_bit     = 1'b0;
        increment      = 1'b0;
        mant_rounded   = 25'd0;
        exp_rounded    = 9'd0;
        frac_final     = 23'd0;
        y              = 32'h00000000;

        if (!a_is_zero) begin
            exp_a  = exp_a_raw;
            mant_a = {1'b1, frac_a_raw};
        end

        if (!b_is_zero) begin
            exp_b  = exp_b_raw;
            mant_b = {1'b1, frac_b_raw};
        end

        if (a_is_nan || b_is_nan) begin
            y = FP32_CANONICAL_NAN;
        end else if (a_is_inf && b_is_inf && (sign_a != sign_b_eff)) begin
            y = FP32_CANONICAL_NAN;
        end else if (a_is_inf) begin
            y = {sign_a, 8'hFF, 23'd0};
        end else if (b_is_inf) begin
            y = {sign_b_eff, 8'hFF, 23'd0};
        end else if (a_is_zero && b_is_zero) begin
            if (sign_a && sign_b_eff) begin
                y = 32'h80000000;
            end else begin
                y = 32'h00000000;
            end
        end else if (a_is_zero) begin
            y = {sign_b_eff, exp_b_raw, frac_b_raw};
            if ((exp_b_raw == 8'h00) && (frac_b_raw != 23'd0)) begin
                y = {sign_b_eff, 31'd0};
            end
        end else if (b_is_zero) begin
            y = {sign_a, exp_a_raw, frac_a_raw};
            if ((exp_a_raw == 8'h00) && (frac_a_raw != 23'd0)) begin
                y = {sign_a, 31'd0};
            end
        end else begin
            if ((exp_a > exp_b) || ((exp_a == exp_b) && (mant_a >= mant_b))) begin
                exp_big    = exp_a;
                mant_big   = mant_a;
                sign_big   = sign_a;
                exp_small  = exp_b;
                mant_small = mant_b;
                sign_small = sign_b_eff;
            end else begin
                exp_big    = exp_b;
                mant_big   = mant_b;
                sign_big   = sign_b_eff;
                exp_small  = exp_a;
                mant_small = mant_a;
                sign_small = sign_a;
            end

            exp_diff      = exp_big - exp_small;
            ext_big       = {mant_big, 3'b000};
            ext_small_pre = {mant_small, 3'b000};

            if (exp_diff >= 8'd27) begin
                ext_small = 27'd0;
                if (ext_small_pre != 27'd0) begin
                    ext_small[0] = 1'b1;
                end
            end else begin
                ext_small = ext_small_pre >> exp_diff;
                if (exp_diff != 8'd0) begin
                    for (idx = 0; idx < 27; idx = idx + 1) begin
                        if ((idx < exp_diff) && ext_small_pre[idx]) begin
                            ext_small[0] = 1'b1;
                        end
                    end
                end
            end

            if (sign_big == sign_small) begin
                sum_ext      = {1'b0, ext_big} + {1'b0, ext_small};
                sign_res_pre = sign_big;
                if (sum_ext[27]) begin
                    mag_pre = sum_ext[27:1];
                    if (sum_ext[0]) begin
                        mag_pre[0] = 1'b1;
                    end
                    exp_res_pre = exp_big + 8'd1;
                end else begin
                    mag_pre     = sum_ext[26:0];
                    exp_res_pre = exp_big;
                end
            end else begin
                diff_ext      = ext_big - ext_small;
                sign_res_pre  = sign_big;
                mag_pre       = diff_ext;
                exp_res_pre   = exp_big;
            end

            if (mag_pre == 27'd0) begin
                y = 32'h00000000;
            end else begin
                if      (mag_pre[26]) lead_pos = 5'd26;
                else if (mag_pre[25]) lead_pos = 5'd25;
                else if (mag_pre[24]) lead_pos = 5'd24;
                else if (mag_pre[23]) lead_pos = 5'd23;
                else if (mag_pre[22]) lead_pos = 5'd22;
                else if (mag_pre[21]) lead_pos = 5'd21;
                else if (mag_pre[20]) lead_pos = 5'd20;
                else if (mag_pre[19]) lead_pos = 5'd19;
                else if (mag_pre[18]) lead_pos = 5'd18;
                else if (mag_pre[17]) lead_pos = 5'd17;
                else if (mag_pre[16]) lead_pos = 5'd16;
                else if (mag_pre[15]) lead_pos = 5'd15;
                else if (mag_pre[14]) lead_pos = 5'd14;
                else if (mag_pre[13]) lead_pos = 5'd13;
                else if (mag_pre[12]) lead_pos = 5'd12;
                else if (mag_pre[11]) lead_pos = 5'd11;
                else if (mag_pre[10]) lead_pos = 5'd10;
                else if (mag_pre[9])  lead_pos = 5'd9;
                else if (mag_pre[8])  lead_pos = 5'd8;
                else if (mag_pre[7])  lead_pos = 5'd7;
                else if (mag_pre[6])  lead_pos = 5'd6;
                else if (mag_pre[5])  lead_pos = 5'd5;
                else if (mag_pre[4])  lead_pos = 5'd4;
                else if (mag_pre[3])  lead_pos = 5'd3;
                else if (mag_pre[2])  lead_pos = 5'd2;
                else if (mag_pre[1])  lead_pos = 5'd1;
                else                  lead_pos = 5'd0;

                shift_left_amt = 8'd26 - {3'd0, lead_pos};
                if (exp_res_pre > shift_left_amt) begin
                    mag_norm = mag_pre << shift_left_amt;
                    exp_norm = {1'b0, exp_res_pre - shift_left_amt};
                end else begin
                    mag_norm = mag_pre << (exp_res_pre - 8'd1);
                    exp_norm = 9'd1;
                end

                mant_main    = mag_norm[26:3];
                guard_bit    = mag_norm[2];
                round_bit    = mag_norm[1];
                sticky_bit   = mag_norm[0];
                increment    = guard_bit && (round_bit || sticky_bit || mant_main[0]);
                mant_rounded = {1'b0, mant_main} + {24'd0, increment};
                exp_rounded  = exp_norm;

                if (mant_rounded[24]) begin
                    mant_rounded = mant_rounded >> 1;
                    exp_rounded  = exp_norm + 9'd1;
                end

                if (exp_rounded >= 9'd255) begin
                    y = {sign_res_pre, 8'hFF, 23'd0};
                end else if ((exp_rounded == 9'd0) || ((exp_rounded == 9'd1) && (mant_rounded[23] == 1'b0))) begin
                    y = {sign_res_pre, 31'd0};
                end else begin
                    frac_final = mant_rounded[22:0];
                    y = {sign_res_pre, exp_rounded[7:0], frac_final};
                end
            end
        end
    end
endmodule
module fp32_mul (
    input  logic [31:0] a,
    input  logic [31:0] b,
    output logic [31:0] y
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
assign y = fp32_mul_bits(a, b);
endmodule
