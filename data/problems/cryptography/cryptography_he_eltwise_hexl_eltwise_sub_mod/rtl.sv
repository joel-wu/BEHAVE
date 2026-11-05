module cryptography_he_eltwise_hexl_eltwise_sub_mod (
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
logic [63:0] result_next;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    if (in_operand1 >= in_operand2) begin
        result_next = in_operand1 - in_operand2;
    end else begin
        result_next = in_operand1 + in_modulus - in_operand2;
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        out_result <= 64'd0;
    end else if (input_accepted) begin
        busy       <= 1'b1;
        out_result <= result_next;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule