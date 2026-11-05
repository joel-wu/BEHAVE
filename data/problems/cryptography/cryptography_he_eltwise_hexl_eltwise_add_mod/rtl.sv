module cryptography_he_eltwise_hexl_eltwise_add_mod (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_operand1,
    input  logic [63:0]  in_operand2,
    input  logic [63:0]  in_modulus,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_result
);

logic        busy;
logic [63:0] result_reg;

wire input_accepted;
wire output_consumed;
wire [63:0] sum_wrapped;
wire [63:0] reduced_result;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = rst ? 1'b1 : (~busy | output_consumed);
assign out_valid = rst ? 1'b0 : busy;
assign out_result = rst ? 64'h0000000000000000 : result_reg;

assign sum_wrapped = in_operand1 + in_operand2;
assign reduced_result = ((in_modulus != 64'h0000000000000000) && (sum_wrapped >= in_modulus))
                      ? (sum_wrapped - in_modulus)
                      : sum_wrapped;

always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        result_reg <= 64'h0000000000000000;
    end else if (input_accepted) begin
        busy       <= 1'b1;
        result_reg <= reduced_result;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule