module vector_negate_f32x4 (
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
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3
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
        out_y0 <= 32'h00000000;
        out_y1 <= 32'h00000000;
        out_y2 <= 32'h00000000;
        out_y3 <= 32'h00000000;
    end else if (input_accepted) begin
        busy   <= 1'b1;
        out_y0 <= in_a0 ^ 32'h80000000;
        out_y1 <= in_a1 ^ 32'h80000000;
        out_y2 <= in_a2 ^ 32'h80000000;
        out_y3 <= in_a3 ^ 32'h80000000;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule