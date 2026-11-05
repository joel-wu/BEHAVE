module quaternion_conjugate_fp32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_q0,
    input  logic [31:0] in_q1,
    input  logic [31:0] in_q2,
    input  logic [31:0] in_q3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_q0,
    output logic [31:0] out_q1,
    output logic [31:0] out_q2,
    output logic [31:0] out_q3
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_ff @(posedge clk) begin
    if (rst) begin
        busy   <= 1'b0;
        out_q0 <= 32'h00000000;
        out_q1 <= 32'h00000000;
        out_q2 <= 32'h00000000;
        out_q3 <= 32'h00000000;
    end else if (input_accepted) begin
        busy   <= 1'b1;
        out_q0 <= in_q0;
        out_q1 <= in_q1 ^ 32'h80000000;
        out_q2 <= in_q2 ^ 32'h80000000;
        out_q3 <= in_q3 ^ 32'h80000000;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule