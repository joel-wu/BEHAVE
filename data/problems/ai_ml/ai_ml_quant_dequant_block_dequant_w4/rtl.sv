module ai_ml_quant_dequant_block_dequant_w4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [3:0]  in_q,
    input  logic [15:0] in_scale,
    input  logic [7:0]  in_zero_point,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_data
);

logic busy;
wire  input_accepted;
wire  output_consumed;

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

function automatic logic [31:0] int_to_fp32_bits(input logic signed [8:0] v);
    logic sign;
    logic [8:0] mag;
    logic [2:0] msb;
    logic [7:0] exp_field;
    logic [22:0] frac_field;
    logic signed [8:0] neg_v;
    int_to_fp32_bits = 32'h00000000;
    sign = 1'b0;
    mag = 9'd0;
    msb = 3'd0;
    exp_field = 8'd0;
    frac_field = 23'd0;
    neg_v = 9'sd0;

    if (v == 0) begin
        int_to_fp32_bits = 32'h00000000;
    end else begin
        sign = v[8];
        if (v < 0) begin
            neg_v = -v;
            mag = neg_v[8:0];
        end else begin
            mag = v[8:0];
        end

        if (mag[7]) begin
            msb = 3'd7;
        end else if (mag[6]) begin
            msb = 3'd6;
        end else if (mag[5]) begin
            msb = 3'd5;
        end else if (mag[4]) begin
            msb = 3'd4;
        end else if (mag[3]) begin
            msb = 3'd3;
        end else if (mag[2]) begin
            msb = 3'd2;
        end else if (mag[1]) begin
            msb = 3'd1;
        end else begin
            msb = 3'd0;
        end

        exp_field = msb + 8'd127;
        frac_field = (mag << (23 - msb)) & 23'h7FFFFF;
        int_to_fp32_bits = {sign, exp_field, frac_field};
    end
endfunction

logic signed [4:0] q_s;
logic signed [8:0] zero_s;
logic signed [8:0] d_s;
logic [31:0] scale_fp32_c;
logic [31:0] d_fp32_c;
logic [31:0] prod_fp32_c;
logic [15:0] out_data_c;

always_comb begin
    q_s = {in_q[3], in_q};
    zero_s = {in_zero_point[7], in_zero_point};
    d_s = q_s - zero_s;
    scale_fp32_c = {in_scale, 16'b0};
    d_fp32_c = int_to_fp32_bits(d_s);
    prod_fp32_c = fp32_mul_bits(scale_fp32_c, d_fp32_c);
    out_data_c = fp32_to_bf16_bits(prod_fp32_c);
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy <= 1'b0;
        out_data <= 16'h0000;
    end else if (input_accepted) begin
        busy <= 1'b1;
        out_data <= out_data_c;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
