module cosine_distance_fp32 (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_base_a,
    input  logic [7:0]   in_base_b,
    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_distance,
    output logic         vec_a_req_valid,
    output logic [7:0]   vec_a_req_addr,
    input  logic         vec_a_req_ready,
    input  logic         vec_a_resp_valid,
    input  logic [31:0]  vec_a_resp_rdata,
    output logic         vec_b_req_valid,
    output logic [7:0]   vec_b_req_addr,
    input  logic         vec_b_req_ready,
    input  logic         vec_b_resp_valid,
    input  logic [31:0]  vec_b_resp_rdata
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

    localparam logic [2:0] ST_IDLE      = 3'd0;
    localparam logic [2:0] ST_REQ       = 3'd1;
    localparam logic [2:0] ST_WAIT_RESP = 3'd2;
    localparam logic [2:0] ST_COMPUTE   = 3'd3;
    localparam logic [2:0] ST_OUT       = 3'd4;

    logic [2:0] state;
    logic [7:0] base_a_reg, base_b_reg;
    logic [4:0] req_idx_a, req_idx_b;
    logic [4:0] resp_idx_a, resp_idx_b;
    logic [31:0] a_mem [0:15];
    logic [31:0] b_mem [0:15];
    logic [31:0] power_a_reg, power_b_reg, dot_reg;
    logic [31:0] power_product_reg, norm_reg, ratio_reg;
    logic [31:0] out_distance_reg;
    logic [4:0] compute_idx;
    logic [31:0] mul_aa_out, mul_bb_out, mul_ab_out;
    logic [31:0] add_pa_out, add_pb_out, add_dot_out;
    logic [31:0] mul_pp_out;
    logic [31:0] sqrt_out;
    logic [31:0] div_out;
    logic [31:0] sub_out;
    logic [31:0] a_cur, b_cur;

    assign in_ready = (state == ST_IDLE);
    assign out_valid = (state == ST_OUT);
    assign out_distance = (state == ST_OUT) ? out_distance_reg : 32'h00000000;

    assign vec_a_req_valid = (state == ST_REQ) && (req_idx_a < 5'd16);
    assign vec_b_req_valid = (state == ST_REQ) && (req_idx_b < 5'd16);
    assign vec_a_req_addr = base_a_reg + {3'd0, req_idx_a};
    assign vec_b_req_addr = base_b_reg + {3'd0, req_idx_b};

    assign a_cur = compute_idx < 16 ? a_mem[compute_idx] : 32'd0;
    assign b_cur = compute_idx < 16 ? b_mem[compute_idx] : 32'd0;

    assign mul_aa_out = fp32_mul_bits(a_cur,a_cur);
    assign mul_bb_out = fp32_mul_bits(b_cur,b_cur);
    assign mul_ab_out = fp32_mul_bits(a_cur,b_cur);
    assign add_pa_out = fp32_add_bits(power_a_reg,mul_aa_out);
    assign add_pb_out = fp32_add_bits(power_b_reg,mul_bb_out);
    assign add_dot_out = fp32_add_bits(dot_reg,mul_ab_out);
    assign mul_pp_out = fp32_mul_bits(power_a_reg,power_b_reg);
    assign sqrt_out = fp32_sqrt_bits(power_product_reg);
    assign div_out = fp32_div_bits(dot_reg,norm_reg);
    assign sub_out = fp32_sub_bits(32'h3f800000,ratio_reg);
    localparam logic [31:0] FP_QNAN=32'h7fc00000, FP_PINF=32'h7f800000;
    localparam logic [31:0] FP32_CANON_NAN=32'h7fc00000;

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= ST_IDLE;
            base_a_reg <= 8'h00;
            base_b_reg <= 8'h00;
            req_idx_a <= 5'd0;            req_idx_b <= 5'd0;
            resp_idx_a <= 5'd0;            resp_idx_b <= 5'd0;
            compute_idx <= 5'd0;
            power_a_reg <= 32'h00000000;
            power_b_reg <= 32'h00000000;
            dot_reg <= 32'h00000000;
            power_product_reg <= 32'h00000000;
            norm_reg <= 32'h00000000;
            ratio_reg <= 32'h00000000;
            out_distance_reg <= 32'h00000000;
            a_mem[0] <= 32'h00000000;
            a_mem[1] <= 32'h00000000;
            a_mem[2] <= 32'h00000000;
            a_mem[3] <= 32'h00000000;
            a_mem[4] <= 32'h00000000;
            a_mem[5] <= 32'h00000000;
            a_mem[6] <= 32'h00000000;
            a_mem[7] <= 32'h00000000;
            a_mem[8] <= 32'h00000000;
            a_mem[9] <= 32'h00000000;
            a_mem[10] <= 32'h00000000;
            a_mem[11] <= 32'h00000000;
            a_mem[12] <= 32'h00000000;
            a_mem[13] <= 32'h00000000;
            a_mem[14] <= 32'h00000000;
            a_mem[15] <= 32'h00000000;
            b_mem[0] <= 32'h00000000;
            b_mem[1] <= 32'h00000000;
            b_mem[2] <= 32'h00000000;
            b_mem[3] <= 32'h00000000;
            b_mem[4] <= 32'h00000000;
            b_mem[5] <= 32'h00000000;
            b_mem[6] <= 32'h00000000;
            b_mem[7] <= 32'h00000000;
            b_mem[8] <= 32'h00000000;
            b_mem[9] <= 32'h00000000;
            b_mem[10] <= 32'h00000000;
            b_mem[11] <= 32'h00000000;
            b_mem[12] <= 32'h00000000;
            b_mem[13] <= 32'h00000000;
            b_mem[14] <= 32'h00000000;
            b_mem[15] <= 32'h00000000;
        end else begin
            case (state)
                ST_IDLE: begin
                    req_idx_a <= 5'd0;                    req_idx_b <= 5'd0;
                    resp_idx_a <= 5'd0;                    resp_idx_b <= 5'd0;
                    compute_idx <= 5'd0;
                    power_a_reg <= 32'h00000000;
                    power_b_reg <= 32'h00000000;
                    dot_reg <= 32'h00000000;
                    power_product_reg <= 32'h00000000;
                    norm_reg <= 32'h00000000;
                    ratio_reg <= 32'h00000000;
                    out_distance_reg <= 32'h00000000;
                    if (in_valid && in_ready) begin
                        base_a_reg <= in_base_a;
                        base_b_reg <= in_base_b;
                        state <= ST_REQ;
                    end
                end

                ST_REQ, ST_WAIT_RESP: begin
                    if (vec_a_req_valid && vec_a_req_ready) req_idx_a <= req_idx_a + 1;
                    if (vec_b_req_valid && vec_b_req_ready) req_idx_b <= req_idx_b + 1;
                    if (vec_a_resp_valid && resp_idx_a < 16) begin
                        a_mem[resp_idx_a] <= vec_a_resp_rdata;
                        resp_idx_a <= resp_idx_a + 1;
                    end
                    if (vec_b_resp_valid && resp_idx_b < 16) begin
                        b_mem[resp_idx_b] <= vec_b_resp_rdata;
                        resp_idx_b <= resp_idx_b + 1;
                    end
                    if (req_idx_a == 16 && req_idx_b == 16) state <= ST_WAIT_RESP;
                    if (resp_idx_a == 16 && resp_idx_b == 16) begin
                        compute_idx <= 0;
                        state <= ST_COMPUTE;
                    end
                end

                ST_COMPUTE: begin
                    if (compute_idx == 5'd0) begin
                        power_a_reg <= mul_aa_out;
                        power_b_reg <= mul_bb_out;
                        dot_reg <= mul_ab_out;
                        compute_idx <= 5'd1;
                    end else if (compute_idx < 5'd16) begin
                        power_a_reg <= add_pa_out;
                        power_b_reg <= add_pb_out;
                        dot_reg <= add_dot_out;
                        if (compute_idx == 5'd15) begin
                            compute_idx <= 5'd16;
                        end else begin
                            compute_idx <= compute_idx + 5'd1;
                        end
                    end else if (compute_idx == 5'd16) begin
                        power_product_reg <= mul_pp_out;
                        compute_idx <= 5'd17;
                    end else if (compute_idx == 5'd17) begin
                        norm_reg <= sqrt_out;
                        compute_idx <= 5'd18;
                    end else if (compute_idx == 5'd18) begin
                        ratio_reg <= div_out;
                        compute_idx <= 5'd19;
                    end else begin
                        out_distance_reg <= sub_out;
                        state <= ST_OUT;
                    end
                end

                ST_OUT: begin
                    if (out_valid && out_ready) begin
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
