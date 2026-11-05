module lower_triangular_solve_f32_4x1 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [3:0]  in_lt_base,
    input  logic [3:0]  in_a_base,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_x0,
    output logic [31:0] out_x1,
    output logic [31:0] out_x2,
    output logic [31:0] out_x3,
    output logic        out_singular,

    output logic        lt_req_valid,
    output logic [3:0]  lt_req_addr,
    input  logic        lt_req_ready,
    input  logic        lt_resp_valid,
    input  logic [31:0] lt_resp_rdata,

    output logic        a_req_valid,
    output logic [3:0]  a_req_addr,
    input  logic        a_req_ready,
    input  logic        a_resp_valid,
    input  logic [31:0] a_resp_rdata
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

    // 27-bit aligned format: implicit-1 at bit 26, guard/round/sticky at [2:0].

    // 49-bit dividend / 24-bit divisor → 26-bit quotient.

    typedef enum logic [5:0] {
        S_IDLE     = 6'd0,
        S_LT_REQ0  = 6'd1,
        S_LT_RESP0 = 6'd2,
        S_LT_REQ1  = 6'd3,
        S_LT_RESP1 = 6'd4,
        S_LT_REQ2  = 6'd5,
        S_LT_RESP2 = 6'd6,
        S_LT_REQ3  = 6'd7,
        S_LT_RESP3 = 6'd8,
        S_LT_REQ4  = 6'd9,
        S_LT_RESP4 = 6'd10,
        S_LT_REQ5  = 6'd11,
        S_LT_RESP5 = 6'd12,
        S_LT_REQ6  = 6'd13,
        S_LT_RESP6 = 6'd14,
        S_LT_REQ7  = 6'd15,
        S_LT_RESP7 = 6'd16,
        S_LT_REQ8  = 6'd17,
        S_LT_RESP8 = 6'd18,
        S_LT_REQ9  = 6'd19,
        S_LT_RESP9 = 6'd20,
        S_A_REQ0   = 6'd21,
        S_A_RESP0  = 6'd22,
        S_A_REQ1   = 6'd23,
        S_A_RESP1  = 6'd24,
        S_A_REQ2   = 6'd25,
        S_A_RESP2  = 6'd26,
        S_A_REQ3   = 6'd27,
        S_A_RESP3  = 6'd28,
        S_COMPUTE  = 6'd29,
        S_OUT      = 6'd30
    } state_t;

    state_t state;

    logic busy;
    logic [3:0] lt_base_reg;
    logic [3:0] a_base_reg;

    logic [31:0] lt00, lt10, lt11, lt20, lt21, lt22, lt30, lt31, lt32, lt33;
    logic [31:0] a0, a1, a2, a3;

    logic [31:0] x0_w, x1_w, x2_w, x3_w;
    logic [31:0] t1_w, t2a_w, t2_w, t3a_w, t3b_w, t3_w;
    logic singular_w;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy;
    assign out_valid = busy & (state == S_OUT);

    always_comb begin
        singular_w = (((lt00 & 32'h7FFFFFFF) == 32'd0) ||
                      ((lt11 & 32'h7FFFFFFF) == 32'd0) ||
                      ((lt22 & 32'h7FFFFFFF) == 32'd0) ||
                      ((lt33 & 32'h7FFFFFFF) == 32'd0));

        x0_w  = fp32_div_bits(a0, lt00);
        t1_w  = fp32_sub_bits(a1, fp32_mul_bits(lt10, x0_w));
        x1_w  = fp32_div_bits(t1_w, lt11);
        t2a_w = fp32_sub_bits(a2, fp32_mul_bits(lt20, x0_w));
        t2_w  = fp32_sub_bits(t2a_w, fp32_mul_bits(lt21, x1_w));
        x2_w  = fp32_div_bits(t2_w, lt22);
        t3a_w = fp32_sub_bits(a3, fp32_mul_bits(lt30, x0_w));
        t3b_w = fp32_sub_bits(t3a_w, fp32_mul_bits(lt31, x1_w));
        t3_w  = fp32_sub_bits(t3b_w, fp32_mul_bits(lt32, x2_w));
        x3_w  = fp32_div_bits(t3_w, lt33);
    end

    always_comb begin
        lt_req_valid = 1'b0;
        lt_req_addr  = 4'h0;
        a_req_valid  = 1'b0;
        a_req_addr   = 4'h0;

        unique case (state)
            S_LT_REQ0: begin lt_req_valid = 1'b1; lt_req_addr = lt_base_reg + 4'd0;  end
            S_LT_REQ1: begin lt_req_valid = 1'b1; lt_req_addr = lt_base_reg + 4'd4;  end
            S_LT_REQ2: begin lt_req_valid = 1'b1; lt_req_addr = lt_base_reg + 4'd5;  end
            S_LT_REQ3: begin lt_req_valid = 1'b1; lt_req_addr = lt_base_reg + 4'd8;  end
            S_LT_REQ4: begin lt_req_valid = 1'b1; lt_req_addr = lt_base_reg + 4'd9;  end
            S_LT_REQ5: begin lt_req_valid = 1'b1; lt_req_addr = lt_base_reg + 4'd10; end
            S_LT_REQ6: begin lt_req_valid = 1'b1; lt_req_addr = lt_base_reg + 4'd12; end
            S_LT_REQ7: begin lt_req_valid = 1'b1; lt_req_addr = lt_base_reg + 4'd13; end
            S_LT_REQ8: begin lt_req_valid = 1'b1; lt_req_addr = lt_base_reg + 4'd14; end
            S_LT_REQ9: begin lt_req_valid = 1'b1; lt_req_addr = lt_base_reg + 4'd15; end
            S_A_REQ0:  begin a_req_valid  = 1'b1; a_req_addr  = a_base_reg + 4'd0;   end
            S_A_REQ1:  begin a_req_valid  = 1'b1; a_req_addr  = a_base_reg + 4'd1;   end
            S_A_REQ2:  begin a_req_valid  = 1'b1; a_req_addr  = a_base_reg + 4'd2;   end
            S_A_REQ3:  begin a_req_valid  = 1'b1; a_req_addr  = a_base_reg + 4'd3;   end
            default: begin end
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;
            busy <= 1'b0;
            lt_base_reg <= 4'h0;
            a_base_reg <= 4'h0;

            lt00 <= 32'h00000000;
            lt10 <= 32'h00000000;
            lt11 <= 32'h00000000;
            lt20 <= 32'h00000000;
            lt21 <= 32'h00000000;
            lt22 <= 32'h00000000;
            lt30 <= 32'h00000000;
            lt31 <= 32'h00000000;
            lt32 <= 32'h00000000;
            lt33 <= 32'h00000000;

            a0 <= 32'h00000000;
            a1 <= 32'h00000000;
            a2 <= 32'h00000000;
            a3 <= 32'h00000000;

            out_x0 <= 32'h00000000;
            out_x1 <= 32'h00000000;
            out_x2 <= 32'h00000000;
            out_x3 <= 32'h00000000;
            out_singular <= 1'b0;
        end else begin
            if (input_accepted) begin
                busy <= 1'b1;
                lt_base_reg <= in_lt_base;
                a_base_reg  <= in_a_base;
                state <= S_LT_REQ0;
            end else if (busy) begin
                unique case (state)
                    S_IDLE: begin
                        state <= S_IDLE;
                    end

                    S_LT_REQ0: begin
                        if (lt_req_ready) state <= S_LT_RESP0;
                    end
                    S_LT_RESP0: begin
                        if (lt_resp_valid) begin
                            lt00 <= lt_resp_rdata;
                            state <= S_LT_REQ1;
                        end
                    end

                    S_LT_REQ1: begin
                        if (lt_req_ready) state <= S_LT_RESP1;
                    end
                    S_LT_RESP1: begin
                        if (lt_resp_valid) begin
                            lt10 <= lt_resp_rdata;
                            state <= S_LT_REQ2;
                        end
                    end

                    S_LT_REQ2: begin
                        if (lt_req_ready) state <= S_LT_RESP2;
                    end
                    S_LT_RESP2: begin
                        if (lt_resp_valid) begin
                            lt11 <= lt_resp_rdata;
                            state <= S_LT_REQ3;
                        end
                    end

                    S_LT_REQ3: begin
                        if (lt_req_ready) state <= S_LT_RESP3;
                    end
                    S_LT_RESP3: begin
                        if (lt_resp_valid) begin
                            lt20 <= lt_resp_rdata;
                            state <= S_LT_REQ4;
                        end
                    end

                    S_LT_REQ4: begin
                        if (lt_req_ready) state <= S_LT_RESP4;
                    end
                    S_LT_RESP4: begin
                        if (lt_resp_valid) begin
                            lt21 <= lt_resp_rdata;
                            state <= S_LT_REQ5;
                        end
                    end

                    S_LT_REQ5: begin
                        if (lt_req_ready) state <= S_LT_RESP5;
                    end
                    S_LT_RESP5: begin
                        if (lt_resp_valid) begin
                            lt22 <= lt_resp_rdata;
                            state <= S_LT_REQ6;
                        end
                    end

                    S_LT_REQ6: begin
                        if (lt_req_ready) state <= S_LT_RESP6;
                    end
                    S_LT_RESP6: begin
                        if (lt_resp_valid) begin
                            lt30 <= lt_resp_rdata;
                            state <= S_LT_REQ7;
                        end
                    end

                    S_LT_REQ7: begin
                        if (lt_req_ready) state <= S_LT_RESP7;
                    end
                    S_LT_RESP7: begin
                        if (lt_resp_valid) begin
                            lt31 <= lt_resp_rdata;
                            state <= S_LT_REQ8;
                        end
                    end

                    S_LT_REQ8: begin
                        if (lt_req_ready) state <= S_LT_RESP8;
                    end
                    S_LT_RESP8: begin
                        if (lt_resp_valid) begin
                            lt32 <= lt_resp_rdata;
                            state <= S_LT_REQ9;
                        end
                    end

                    S_LT_REQ9: begin
                        if (lt_req_ready) state <= S_LT_RESP9;
                    end
                    S_LT_RESP9: begin
                        if (lt_resp_valid) begin
                            lt33 <= lt_resp_rdata;
                            state <= S_A_REQ0;
                        end
                    end

                    S_A_REQ0: begin
                        if (a_req_ready) state <= S_A_RESP0;
                    end
                    S_A_RESP0: begin
                        if (a_resp_valid) begin
                            a0 <= a_resp_rdata;
                            state <= S_A_REQ1;
                        end
                    end

                    S_A_REQ1: begin
                        if (a_req_ready) state <= S_A_RESP1;
                    end
                    S_A_RESP1: begin
                        if (a_resp_valid) begin
                            a1 <= a_resp_rdata;
                            state <= S_A_REQ2;
                        end
                    end

                    S_A_REQ2: begin
                        if (a_req_ready) state <= S_A_RESP2;
                    end
                    S_A_RESP2: begin
                        if (a_resp_valid) begin
                            a2 <= a_resp_rdata;
                            state <= S_A_REQ3;
                        end
                    end

                    S_A_REQ3: begin
                        if (a_req_ready) state <= S_A_RESP3;
                    end
                    S_A_RESP3: begin
                        if (a_resp_valid) begin
                            a3 <= a_resp_rdata;
                            state <= S_COMPUTE;
                        end
                    end

                    S_COMPUTE: begin
                        out_singular <= singular_w;
                        if (singular_w) begin
                            out_x0 <= 32'h00000000;
                            out_x1 <= 32'h00000000;
                            out_x2 <= 32'h00000000;
                            out_x3 <= 32'h00000000;
                        end else begin
                            out_x0 <= x0_w;
                            out_x1 <= x1_w;
                            out_x2 <= x2_w;
                            out_x3 <= x3_w;
                        end
                        state <= S_OUT;
                    end

                    S_OUT: begin
                        if (out_ready) begin
                            busy <= 1'b0;
                            state <= S_IDLE;
                        end
                    end

                    default: begin
                        state <= S_IDLE;
                    end
                endcase
            end
        end
    end

endmodule
