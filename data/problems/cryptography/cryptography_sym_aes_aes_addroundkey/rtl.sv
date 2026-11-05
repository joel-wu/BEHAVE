module cryptography_sym_aes_aes_addroundkey (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_state,
    input  logic [127:0] in_round_key,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [127:0] out_state
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
        busy      <= 1'b0;
        out_state <= 128'h00000000000000000000000000000000;
    end else if (input_accepted) begin
        out_state <= in_state ^ in_round_key;
        busy      <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule