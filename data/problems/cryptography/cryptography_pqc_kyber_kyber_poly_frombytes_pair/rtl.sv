module cryptography_pqc_kyber_kyber_poly_frombytes_pair (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [23:0] in_packed,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [11:0] out_coeff0,
    output logic [11:0] out_coeff1
);

logic busy;
logic [11:0] coeff0_next;
logic [11:0] coeff1_next;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = rst ? 1'b1 : (~busy | output_consumed);
assign out_valid = rst ? 1'b0 : busy;

always_comb begin
    coeff0_next = in_packed[11:0];
    coeff1_next = {in_packed[23:16], in_packed[15:12]};
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        out_coeff0 <= 12'h000;
        out_coeff1 <= 12'h000;
    end else if (input_accepted) begin
        busy       <= 1'b1;
        out_coeff0 <= coeff0_next;
        out_coeff1 <= coeff1_next;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule