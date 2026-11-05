module ai_ml_quant_dequant_fused_dequant_matmul_w4a16 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [255:0] in_act,
    input  logic [63:0]  in_w_packed,
    input  logic [63:0]  in_scale,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_bf16
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

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign in_ready        = ~busy | output_consumed;
    assign out_valid       = busy;

    function automatic logic [31:0] bf16_to_fp32(input logic [15:0] x);
        begin
            bf16_to_fp32 = {x, 16'h0000};
        end
    endfunction

    function automatic logic [15:0] fp32_to_bf16(input logic [31:0] x);
        logic sign;
        logic [7:0] exp_field;
        logic [22:0] frac_field;
        logic [15:0] top16;
        logic [15:0] low16;
        logic [16:0] result17;
        begin
            sign      = x[31];
            exp_field = x[30:23];
            frac_field = x[22:0];

            if (exp_field == 8'hFF) begin
                if (frac_field != 23'h0) fp32_to_bf16 = {sign, 15'h7FC0};
                else                     fp32_to_bf16 = {sign, 8'hFF, 7'h00};
            end else begin
                top16 = x[31:16];
                low16 = x[15:0];
                if (low16 < 16'h8000) begin
                    result17 = {1'b0, top16};
                end else if (low16 > 16'h8000) begin
                    result17 = {1'b0, top16} + 17'd1;
                end else begin
                    // exactly 0x8000 — round to even
                    result17 = {1'b0, top16} + {16'd0, top16[0]};
                end
                fp32_to_bf16 = result17[15:0];
            end
        end
    endfunction

    function automatic logic [31:0] int4_to_fp32(input logic [3:0] q);
        begin
            unique case (q)
                4'h0: int4_to_fp32 = 32'h00000000;
                4'h1: int4_to_fp32 = 32'h3F800000;
                4'h2: int4_to_fp32 = 32'h40000000;
                4'h3: int4_to_fp32 = 32'h40400000;
                4'h4: int4_to_fp32 = 32'h40800000;
                4'h5: int4_to_fp32 = 32'h40A00000;
                4'h6: int4_to_fp32 = 32'h40C00000;
                4'h7: int4_to_fp32 = 32'h40E00000;
                4'h8: int4_to_fp32 = 32'hC1000000;
                4'h9: int4_to_fp32 = 32'hC0E00000;
                4'hA: int4_to_fp32 = 32'hC0C00000;
                4'hB: int4_to_fp32 = 32'hC0A00000;
                4'hC: int4_to_fp32 = 32'hC0800000;
                4'hD: int4_to_fp32 = 32'hC0400000;
                4'hE: int4_to_fp32 = 32'hC0000000;
                4'hF: int4_to_fp32 = 32'hBF800000;
            endcase
        end
    endfunction

    logic [31:0] acts_fp32 [0:15];
    logic [31:0] scales_fp32 [0:3];
    logic [31:0] q_fp32 [0:15];
    logic [31:0] w_fp32 [0:15];
    logic [31:0] p_fp32 [0:15];
    logic [31:0] acc_fp32 [0:15];
    logic [15:0] out_bf16_next;

    integer i;
    always_comb begin
        for (i = 0; i < 16; i = i + 1) begin
            acts_fp32[i] = bf16_to_fp32(in_act[16*i +: 16]);
            q_fp32[i]    = int4_to_fp32(in_w_packed[4*i +: 4]);
        end

        for (i = 0; i < 4; i = i + 1) begin
            scales_fp32[i] = bf16_to_fp32(in_scale[16*i +: 16]);
        end

        for (i = 0; i < 16; i = i + 1) begin
            w_fp32[i] = fp32_mul_bits(q_fp32[i], scales_fp32[i >> 2]);
            p_fp32[i] = fp32_mul_bits(acts_fp32[i], w_fp32[i]);
        end

        acc_fp32[0] = p_fp32[0];
        for (i = 1; i < 16; i = i + 1) begin
            acc_fp32[i] = fp32_add_bits(acc_fp32[i-1], p_fp32[i]);
        end

        out_bf16_next = fp32_to_bf16(acc_fp32[15]);
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy     <= 1'b0;
            out_bf16 <= 16'h0000;
        end else if (input_accepted) begin
            busy     <= 1'b1;
            out_bf16 <= out_bf16_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
