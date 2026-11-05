module control_kalman_ekf_predict_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic signed [31:0] in_x0,
    input  logic signed [31:0] in_x1,

    input  logic signed [31:0] in_p00,
    input  logic signed [31:0] in_p01,
    input  logic signed [31:0] in_p10,
    input  logic signed [31:0] in_p11,

    input  logic signed [31:0] in_f00,
    input  logic signed [31:0] in_f01,
    input  logic signed [31:0] in_f10,
    input  logic signed [31:0] in_f11,

    input  logic signed [31:0] in_b0,
    input  logic signed [31:0] in_b1,
    input  logic signed [31:0] in_u,

    input  logic signed [31:0] in_q00,
    input  logic signed [31:0] in_q01,
    input  logic signed [31:0] in_q10,
    input  logic signed [31:0] in_q11,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [31:0] out_x0,
    output logic signed [31:0] out_x1,

    output logic signed [31:0] out_p00,
    output logic signed [31:0] out_p01,
    output logic signed [31:0] out_p10,
    output logic signed [31:0] out_p11
);

logic busy;
wire input_accepted;
wire output_consumed;

logic signed [31:0] next_out_x0;
logic signed [31:0] next_out_x1;
logic signed [31:0] next_out_p00;
logic signed [31:0] next_out_p01;
logic signed [31:0] next_out_p10;
logic signed [31:0] next_out_p11;

logic signed [63:0] prod64;
logic signed [31:0] m_f00_x0;
logic signed [31:0] m_f01_x1;
logic signed [31:0] m_b0_u;
logic signed [31:0] m_f10_x0;
logic signed [31:0] m_f11_x1;
logic signed [31:0] m_b1_u;

logic signed [31:0] m_f00_p00;
logic signed [31:0] m_f01_p10;
logic signed [31:0] m_f00_p01;
logic signed [31:0] m_f01_p11;
logic signed [31:0] m_f10_p00;
logic signed [31:0] m_f11_p10;
logic signed [31:0] m_f10_p01;
logic signed [31:0] m_f11_p11;

logic signed [31:0] a00;
logic signed [31:0] a01;
logic signed [31:0] a10;
logic signed [31:0] a11;

logic signed [31:0] m_a00_f00;
logic signed [31:0] m_a01_f01;
logic signed [31:0] m_a00_f10;
logic signed [31:0] m_a01_f11;
logic signed [31:0] m_a10_f00;
logic signed [31:0] m_a11_f01;
logic signed [31:0] m_a10_f10;
logic signed [31:0] m_a11_f11;

logic signed [31:0] ptmp00;
logic signed [31:0] ptmp01;
logic signed [31:0] ptmp10;
logic signed [31:0] ptmp11;

function automatic logic signed [31:0] mul_q31(
    input logic signed [31:0] a,
    input logic signed [31:0] b
);
    logic signed [63:0] mul_tmp;
    begin
        mul_tmp = a * b;
        mul_q31 = mul_tmp >>> 31;
    end
endfunction

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    m_f00_x0  = mul_q31(in_f00, in_x0);
    m_f01_x1  = mul_q31(in_f01, in_x1);
    m_b0_u    = mul_q31(in_b0,  in_u);
    next_out_x0 = m_f00_x0 + m_f01_x1 + m_b0_u;

    m_f10_x0  = mul_q31(in_f10, in_x0);
    m_f11_x1  = mul_q31(in_f11, in_x1);
    m_b1_u    = mul_q31(in_b1,  in_u);
    next_out_x1 = m_f10_x0 + m_f11_x1 + m_b1_u;

    m_f00_p00 = mul_q31(in_f00, in_p00);
    m_f01_p10 = mul_q31(in_f01, in_p10);
    a00 = m_f00_p00 + m_f01_p10;

    m_f00_p01 = mul_q31(in_f00, in_p01);
    m_f01_p11 = mul_q31(in_f01, in_p11);
    a01 = m_f00_p01 + m_f01_p11;

    m_f10_p00 = mul_q31(in_f10, in_p00);
    m_f11_p10 = mul_q31(in_f11, in_p10);
    a10 = m_f10_p00 + m_f11_p10;

    m_f10_p01 = mul_q31(in_f10, in_p01);
    m_f11_p11 = mul_q31(in_f11, in_p11);
    a11 = m_f10_p01 + m_f11_p11;

    m_a00_f00 = mul_q31(a00, in_f00);
    m_a01_f01 = mul_q31(a01, in_f01);
    ptmp00 = m_a00_f00 + m_a01_f01;

    m_a00_f10 = mul_q31(a00, in_f10);
    m_a01_f11 = mul_q31(a01, in_f11);
    ptmp01 = m_a00_f10 + m_a01_f11;

    m_a10_f00 = mul_q31(a10, in_f00);
    m_a11_f01 = mul_q31(a11, in_f01);
    ptmp10 = m_a10_f00 + m_a11_f01;

    m_a10_f10 = mul_q31(a10, in_f10);
    m_a11_f11 = mul_q31(a11, in_f11);
    ptmp11 = m_a10_f10 + m_a11_f11;

    next_out_p00 = ptmp00 + in_q00;
    next_out_p01 = ptmp01 + in_q01;
    next_out_p10 = ptmp10 + in_q10;
    next_out_p11 = ptmp11 + in_q11;
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy    <= 1'b0;
        out_x0  <= 32'sd0;
        out_x1  <= 32'sd0;
        out_p00 <= 32'sd0;
        out_p01 <= 32'sd0;
        out_p10 <= 32'sd0;
        out_p11 <= 32'sd0;
    end else if (input_accepted) begin
        busy    <= 1'b1;
        out_x0  <= next_out_x0;
        out_x1  <= next_out_x1;
        out_p00 <= next_out_p00;
        out_p01 <= next_out_p01;
        out_p10 <= next_out_p10;
        out_p11 <= next_out_p11;
    end else if (output_consumed) begin
        busy    <= 1'b0;
        out_x0  <= 32'sd0;
        out_x1  <= 32'sd0;
        out_p00 <= 32'sd0;
        out_p01 <= 32'sd0;
        out_p10 <= 32'sd0;
        out_p11 <= 32'sd0;
    end else if (!busy) begin
        out_x0  <= 32'sd0;
        out_x1  <= 32'sd0;
        out_p00 <= 32'sd0;
        out_p01 <= 32'sd0;
        out_p10 <= 32'sd0;
        out_p11 <= 32'sd0;
    end
end

endmodule