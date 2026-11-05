module control_foc_transform_inv_clarke_transform_2p_to_3p (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_alpha,
    input  logic [15:0] in_beta,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_a,
    output logic [15:0] out_b,
    output logic [15:0] out_c
);

logic busy;

logic signed [15:0] alpha_s;
logic signed [15:0] beta_s;

logic signed [31:0] b_alpha_prod_q30;
logic signed [31:0] b_beta_prod_q30;
logic signed [31:0] c_alpha_prod_q30;
logic signed [31:0] c_beta_prod_q30;

logic signed [31:0] b_alpha_q15;
logic signed [31:0] b_beta_q15;
logic signed [31:0] c_alpha_q15;
logic signed [31:0] c_beta_q15;

logic signed [31:0] a_ext;
logic signed [31:0] b_sum;
logic signed [31:0] c_sum;

wire input_accepted;
wire output_consumed;

function automatic logic signed [15:0] sat16(input logic signed [31:0] value);
    begin
        if (value > 32'sd32767)
            sat16 = 16'sd32767;
        else if (value < -32'sd32768)
            sat16 = -16'sd32768;
        else
            sat16 = value[15:0];
    end
endfunction

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    alpha_s = $signed(in_alpha);
    beta_s  = $signed(in_beta);

    b_alpha_prod_q30 = alpha_s * (-16'sd16384);
    b_beta_prod_q30  = beta_s  *  16'sd28378;
    c_alpha_prod_q30 = alpha_s * (-16'sd16384);
    c_beta_prod_q30  = beta_s  *  16'sd28378;

    b_alpha_q15 = b_alpha_prod_q30 >>> 15;
    b_beta_q15  = b_beta_prod_q30  >>> 15;
    c_alpha_q15 = c_alpha_prod_q30 >>> 15;
    c_beta_q15  = c_beta_prod_q30  >>> 15;

    a_ext = alpha_s;
    b_sum = b_alpha_q15 + b_beta_q15;
    c_sum = c_alpha_q15 - c_beta_q15;
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy  <= 1'b0;
        out_a <= 16'h0000;
        out_b <= 16'h0000;
        out_c <= 16'h0000;
    end else if (input_accepted) begin
        out_a <= sat16(a_ext);
        out_b <= sat16(b_sum);
        out_c <= sat16(c_sum);
        busy  <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule