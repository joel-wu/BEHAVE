module ai_ml_quant_fp8_fp8_amax_history (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_h0,
    input  logic [31:0] in_h1,
    input  logic [31:0] in_h2,
    input  logic [31:0] in_h3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_h0,
    output logic [31:0] out_h1,
    output logic [31:0] out_h2,
    output logic [31:0] out_h3
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
        out_h0 <= 32'h00000000;
        out_h1 <= 32'h00000000;
        out_h2 <= 32'h00000000;
        out_h3 <= 32'h00000000;
    end else if (input_accepted) begin
        busy   <= 1'b1;
        out_h0 <= 32'h00000000;
        out_h1 <= in_h2;
        out_h2 <= in_h3;
        out_h3 <= in_h0;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
