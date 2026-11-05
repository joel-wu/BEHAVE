module cryptography_pqc_dilithium_dilithium_makehint (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [23:0] in_a0,
    input  logic [5:0]  in_a1,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [0:0]  out_hint
);

logic busy;
logic hint_next;

wire input_accepted;
wire output_consumed;
wire signed [23:0] a0_signed;

assign a0_signed = in_a0;
assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    hint_next = 1'b0;
    if (a0_signed > 24'sd95232) begin
        hint_next = 1'b1;
    end else if (a0_signed < -24'sd95232) begin
        hint_next = 1'b1;
    end else if ((a0_signed == -24'sd95232) && (in_a1 != 6'd0)) begin
        hint_next = 1'b1;
    end
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy     <= 1'b0;
        out_hint <= 1'b0;
    end else if (input_accepted) begin
        busy     <= 1'b1;
        out_hint <= hint_next;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule