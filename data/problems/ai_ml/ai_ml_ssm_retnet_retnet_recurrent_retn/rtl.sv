module ai_ml_ssm_retnet_retnet_recurrent_retn (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [127:0] in_q, in_k, in_v,
    input logic in_has_initial_state,
    input logic [255:0] in_initial_state,
    input logic in_scale_valid,
    input logic [31:0] in_scale,
    input logic in_output_final_state,
    output logic out_valid,
    input logic out_ready,
    output logic [127:0] out_o,
    output logic out_final_state_valid,
    output logic [255:0] out_final_state
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

    logic [255:0] state, next_state;
    logic [127:0] next_o;
    logic [31:0] scale, decay, old, updated, term0, term1, query;
    assign in_ready = !out_valid || out_ready;
    always_comb begin
        scale = in_scale_valid ? in_scale : 32'h3F3504F3;
        next_o = 0;
        next_state = 0;
        decay = 0; old = 0; updated = 0; term0 = 0; term1 = 0; query = 0;
        for (int h=0; h<2; h++) begin
            decay = h==0 ? 32'h3F780000 : 32'h3F7C0000;
            for (int c=0; c<2; c++) begin
                for (int r=0; r<2; r++) begin
                    old = in_has_initial_state ? in_initial_state[(4*h+2*r+c)*32 +: 32] : state[(4*h+2*r+c)*32 +: 32];
                    updated = fp32_add_bits(fp32_mul_bits(old,decay),fp32_mul_bits(in_k[(2*h+r)*32 +: 32],in_v[(2*h+c)*32 +: 32]));
                    next_state[(4*h+2*r+c)*32 +: 32] = updated;
                    query = fp32_mul_bits(in_q[(2*h+r)*32 +: 32],scale);
                    if (r==0) term0=fp32_mul_bits(query,updated);
                    else term1=fp32_mul_bits(query,updated);
                end
                next_o[(2*h+c)*32 +: 32] = fp32_add_bits(term0,term1);
            end
        end
    end
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            state <= 0; out_valid <= 0; out_o <= 0;
            out_final_state_valid <= 0; out_final_state <= 0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin
                state <= next_state;
                out_o <= next_o;
                out_final_state_valid <= in_output_final_state;
                out_final_state <= in_output_final_state ? next_state : 256'd0;
            end else begin
                out_o <= 0; out_final_state_valid <= 0; out_final_state <= 0;
            end
        end
    end
endmodule
