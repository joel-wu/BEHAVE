module symmetric_matrix_multiply_2x2 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_side,
    input  logic        in_uplo,
    input  logic [63:0] in_alpha,
    input  logic [63:0] in_beta,

    input  logic [63:0] in_a00,
    input  logic [63:0] in_a01,
    input  logic [63:0] in_a10,
    input  logic [63:0] in_a11,

    input  logic [63:0] in_b00,
    input  logic [63:0] in_b10,
    input  logic [63:0] in_b01,
    input  logic [63:0] in_b11,

    input  logic [63:0] in_c00,
    input  logic [63:0] in_c10,
    input  logic [63:0] in_c01,
    input  logic [63:0] in_c11,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_c00,
    output logic [63:0] out_c10,
    output logic [63:0] out_c01,
    output logic [63:0] out_c11
);

logic busy;
wire input_accepted;
wire output_consumed;

logic [63:0] s_val;

logic [63:0] p00;
logic [63:0] p01;
logic [63:0] p10;
logic [63:0] p11;

logic [63:0] r00;
logic [63:0] r01;
logic [63:0] r10;
logic [63:0] r11;

assign output_consumed = out_valid & out_ready;
assign input_accepted  = in_valid & in_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    s_val = in_uplo ? in_a10 : in_a01;

    if (in_side == 1'b0) begin
        p00 = (in_a00 * in_b00) + (s_val * in_b10);
        p01 = (in_a00 * in_b01) + (s_val * in_b11);
        p10 = (s_val * in_b00) + (in_a11 * in_b10);
        p11 = (s_val * in_b01) + (in_a11 * in_b11);
    end else begin
        p00 = (in_b00 * in_a00) + (in_b01 * s_val);
        p01 = (in_b00 * s_val) + (in_b01 * in_a11);
        p10 = (in_b10 * in_a00) + (in_b11 * s_val);
        p11 = (in_b10 * s_val) + (in_b11 * in_a11);
    end

    r00 = (in_alpha * p00) + (in_beta * in_c00);
    r01 = (in_alpha * p01) + (in_beta * in_c01);
    r10 = (in_alpha * p10) + (in_beta * in_c10);
    r11 = (in_alpha * p11) + (in_beta * in_c11);
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy    <= 1'b0;
        out_c00 <= 64'd0;
        out_c10 <= 64'd0;
        out_c01 <= 64'd0;
        out_c11 <= 64'd0;
    end else if (input_accepted) begin
        out_c00 <= r00;
        out_c10 <= r10;
        out_c01 <= r01;
        out_c11 <= r11;
        busy    <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule