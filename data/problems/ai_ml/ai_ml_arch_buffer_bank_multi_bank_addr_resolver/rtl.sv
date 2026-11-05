module ai_ml_arch_buffer_bank_multi_bank_addr_resolver (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_laddr,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_bank,
    output logic [15:0] out_row
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
        busy     <= 1'b0;
        out_bank <= 8'h00;
        out_row  <= 16'h0000;
    end else if (input_accepted) begin
        out_bank <= {5'b00000, in_laddr[12:10]};
        out_row  <= {6'b000000, in_laddr[9:0]};
        busy     <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule