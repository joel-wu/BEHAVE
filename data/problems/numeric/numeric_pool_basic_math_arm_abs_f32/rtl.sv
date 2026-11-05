module vector_abs_f32x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a0,
    input  logic [31:0] in_a1,
    input  logic [31:0] in_a2,
    input  logic [31:0] in_a3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_r0,
    output logic [31:0] out_r1,
    output logic [31:0] out_r2,
    output logic [31:0] out_r3
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy   <= 1'b0;
        out_r0 <= 32'h00000000;
        out_r1 <= 32'h00000000;
        out_r2 <= 32'h00000000;
        out_r3 <= 32'h00000000;
    end else if (input_accepted) begin
        busy   <= 1'b1;
        out_r0 <= in_a0 & 32'h7FFFFFFF;
        out_r1 <= in_a1 & 32'h7FFFFFFF;
        out_r2 <= in_a2 & 32'h7FFFFFFF;
        out_r3 <= in_a3 & 32'h7FFFFFFF;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule