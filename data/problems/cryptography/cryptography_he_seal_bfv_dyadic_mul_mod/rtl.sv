module cryptography_he_seal_bfv_dyadic_mul_mod (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_operand1,
    input  logic [63:0]  in_operand2,
    input  logic [63:0]  in_modulus,
    input  logic [63:0]  in_const_ratio_0,
    input  logic [63:0]  in_const_ratio_1,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_result
);

logic busy;
wire  output_consumed;
wire  input_accepted;

logic [127:0] z;
logic [63:0]  z0;
logic [63:0]  z1;

logic [127:0] mul_z0_r0;
logic [63:0]  carry0;

logic [127:0] prod01;
logic [63:0]  prod01_lo;
logic [63:0]  prod01_hi;
logic [64:0]  sum1;
logic [63:0]  sum1_lo;
logic [63:0]  sum1_carry;
logic [63:0]  tmp3;

logic [127:0] prod10;
logic [63:0]  prod10_lo;
logic [63:0]  prod10_hi;
logic [64:0]  sum2;
logic [63:0]  sum2_lo;
logic [63:0]  sum2_carry;
logic [63:0]  carry1;

logic [127:0] mul_z1_r1;
logic [63:0]  tmp1;

logic [127:0] mul_tmp1_p;
logic [63:0]  t;
logic [63:0]  result_next;

assign output_consumed = out_valid & out_ready;
assign input_accepted  = in_valid & in_ready;

assign in_ready  = rst ? 1'b1 : (~busy | output_consumed);
assign out_valid = rst ? 1'b0 : busy;

always_comb begin
    z         = in_operand1 * in_operand2;
    z0        = z[63:0];
    z1        = z[127:64];

    mul_z0_r0 = z0 * in_const_ratio_0;
    carry0    = mul_z0_r0[127:64];

    prod01    = z0 * in_const_ratio_1;
    prod01_lo = prod01[63:0];
    prod01_hi = prod01[127:64];
    sum1      = {1'b0, prod01_lo} + {1'b0, carry0};
    sum1_lo   = sum1[63:0];
    sum1_carry= {63'd0, sum1[64]};
    tmp3      = prod01_hi + sum1_carry;

    prod10    = z1 * in_const_ratio_0;
    prod10_lo = prod10[63:0];
    prod10_hi = prod10[127:64];
    sum2      = {1'b0, sum1_lo} + {1'b0, prod10_lo};
    sum2_lo   = sum2[63:0];
    sum2_carry= {63'd0, sum2[64]};
    carry1    = prod10_hi + sum2_carry;

    mul_z1_r1 = z1 * in_const_ratio_1;
    tmp1      = mul_z1_r1[63:0] + tmp3 + carry1;

    mul_tmp1_p = tmp1 * in_modulus;
    t          = z0 - mul_tmp1_p[63:0];

    if (t >= in_modulus) begin
        result_next = t - in_modulus;
    end else begin
        result_next = t;
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        out_result <= 64'h0000000000000000;
    end else if (input_accepted) begin
        busy       <= 1'b1;
        out_result <= result_next;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule