module keccak_chi_iota (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_b0,
    input  logic [63:0]  in_b1,
    input  logic [63:0]  in_b2,
    input  logic [63:0]  in_b3,
    input  logic [63:0]  in_b4,
    input  logic [63:0]  in_b5,
    input  logic [63:0]  in_b6,
    input  logic [63:0]  in_b7,
    input  logic [63:0]  in_b8,
    input  logic [63:0]  in_b9,
    input  logic [63:0]  in_b10,
    input  logic [63:0]  in_b11,
    input  logic [63:0]  in_b12,
    input  logic [63:0]  in_b13,
    input  logic [63:0]  in_b14,
    input  logic [63:0]  in_b15,
    input  logic [63:0]  in_b16,
    input  logic [63:0]  in_b17,
    input  logic [63:0]  in_b18,
    input  logic [63:0]  in_b19,
    input  logic [63:0]  in_b20,
    input  logic [63:0]  in_b21,
    input  logic [63:0]  in_b22,
    input  logic [63:0]  in_b23,
    input  logic [63:0]  in_b24,
    input  logic [4:0]   in_round,

    output logic         out_valid,
    output logic out_error,
    input  logic         out_ready,
    output logic [63:0]  out_a0,
    output logic [63:0]  out_a1,
    output logic [63:0]  out_a2,
    output logic [63:0]  out_a3,
    output logic [63:0]  out_a4,
    output logic [63:0]  out_a5,
    output logic [63:0]  out_a6,
    output logic [63:0]  out_a7,
    output logic [63:0]  out_a8,
    output logic [63:0]  out_a9,
    output logic [63:0]  out_a10,
    output logic [63:0]  out_a11,
    output logic [63:0]  out_a12,
    output logic [63:0]  out_a13,
    output logic [63:0]  out_a14,
    output logic [63:0]  out_a15,
    output logic [63:0]  out_a16,
    output logic [63:0]  out_a17,
    output logic [63:0]  out_a18,
    output logic [63:0]  out_a19,
    output logic [63:0]  out_a20,
    output logic [63:0]  out_a21,
    output logic [63:0]  out_a22,
    output logic [63:0]  out_a23,
    output logic [63:0]  out_a24
);

logic busy;
    wire invalid_input = in_round >= 5'd24;
logic [63:0] rc;

wire input_accepted;
wire output_consumed;

assign output_consumed = out_valid & out_ready;
assign in_ready        = rst ? 1'b1 : (~busy | output_consumed);
assign out_valid       = rst ? 1'b0 : busy;
assign input_accepted  = in_valid & in_ready;

always_comb begin
    rc = 64'h0000000000000000;
    unique case (in_round)
        5'd0:  rc = 64'h0000000000000001;
        5'd1:  rc = 64'h0000000000008082;
        5'd2:  rc = 64'h800000000000808A;
        5'd3:  rc = 64'h8000000080008000;
        5'd4:  rc = 64'h000000000000808B;
        5'd5:  rc = 64'h0000000080000001;
        5'd6:  rc = 64'h8000000080008081;
        5'd7:  rc = 64'h8000000000008009;
        5'd8:  rc = 64'h000000000000008A;
        5'd9:  rc = 64'h0000000000000088;
        5'd10: rc = 64'h0000000080008009;
        5'd11: rc = 64'h000000008000000A;
        5'd12: rc = 64'h000000008000808B;
        5'd13: rc = 64'h800000000000008B;
        5'd14: rc = 64'h8000000000008089;
        5'd15: rc = 64'h8000000000008003;
        5'd16: rc = 64'h8000000000008002;
        5'd17: rc = 64'h8000000000000080;
        5'd18: rc = 64'h000000000000800A;
        5'd19: rc = 64'h800000008000000A;
        5'd20: rc = 64'h8000000080008081;
        5'd21: rc = 64'h8000000000008080;
        5'd22: rc = 64'h0000000080000001;
        5'd23: rc = 64'h8000000080008008;
        default: rc = 64'h0000000000000000;
    endcase
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy    <= 1'b0;
            out_error <= 1'b0;
        out_a0  <= 64'd0;
        out_a1  <= 64'd0;
        out_a2  <= 64'd0;
        out_a3  <= 64'd0;
        out_a4  <= 64'd0;
        out_a5  <= 64'd0;
        out_a6  <= 64'd0;
        out_a7  <= 64'd0;
        out_a8  <= 64'd0;
        out_a9  <= 64'd0;
        out_a10 <= 64'd0;
        out_a11 <= 64'd0;
        out_a12 <= 64'd0;
        out_a13 <= 64'd0;
        out_a14 <= 64'd0;
        out_a15 <= 64'd0;
        out_a16 <= 64'd0;
        out_a17 <= 64'd0;
        out_a18 <= 64'd0;
        out_a19 <= 64'd0;
        out_a20 <= 64'd0;
        out_a21 <= 64'd0;
        out_a22 <= 64'd0;
        out_a23 <= 64'd0;
        out_a24 <= 64'd0;
    end else if (input_accepted) begin
        out_a0  <= invalid_input ? '0 : (in_b0  ^ ((~in_b1)  & in_b2)  ^ rc);
        out_a1  <= invalid_input ? '0 : (in_b1  ^ ((~in_b2)  & in_b3));
        out_a2  <= invalid_input ? '0 : (in_b2  ^ ((~in_b3)  & in_b4));
        out_a3  <= invalid_input ? '0 : (in_b3  ^ ((~in_b4)  & in_b0));
        out_a4  <= invalid_input ? '0 : (in_b4  ^ ((~in_b0)  & in_b1));

        out_a5  <= invalid_input ? '0 : (in_b5  ^ ((~in_b6)  & in_b7));
        out_a6  <= invalid_input ? '0 : (in_b6  ^ ((~in_b7)  & in_b8));
        out_a7  <= invalid_input ? '0 : (in_b7  ^ ((~in_b8)  & in_b9));
        out_a8  <= invalid_input ? '0 : (in_b8  ^ ((~in_b9)  & in_b5));
        out_a9  <= invalid_input ? '0 : (in_b9  ^ ((~in_b5)  & in_b6));

        out_a10 <= invalid_input ? '0 : (in_b10 ^ ((~in_b11) & in_b12));
        out_a11 <= invalid_input ? '0 : (in_b11 ^ ((~in_b12) & in_b13));
        out_a12 <= invalid_input ? '0 : (in_b12 ^ ((~in_b13) & in_b14));
        out_a13 <= invalid_input ? '0 : (in_b13 ^ ((~in_b14) & in_b10));
        out_a14 <= invalid_input ? '0 : (in_b14 ^ ((~in_b10) & in_b11));

        out_a15 <= invalid_input ? '0 : (in_b15 ^ ((~in_b16) & in_b17));
        out_a16 <= invalid_input ? '0 : (in_b16 ^ ((~in_b17) & in_b18));
        out_a17 <= invalid_input ? '0 : (in_b17 ^ ((~in_b18) & in_b19));
        out_a18 <= invalid_input ? '0 : (in_b18 ^ ((~in_b19) & in_b15));
        out_a19 <= invalid_input ? '0 : (in_b19 ^ ((~in_b15) & in_b16));

        out_a20 <= invalid_input ? '0 : (in_b20 ^ ((~in_b21) & in_b22));
        out_a21 <= invalid_input ? '0 : (in_b21 ^ ((~in_b22) & in_b23));
        out_a22 <= invalid_input ? '0 : (in_b22 ^ ((~in_b23) & in_b24));
        out_a23 <= invalid_input ? '0 : (in_b23 ^ ((~in_b24) & in_b20));
        out_a24 <= invalid_input ? '0 : (in_b24 ^ ((~in_b20) & in_b21));

        busy <= 1'b1;
            out_error <= invalid_input;
    end else if (output_consumed) begin
        busy <= 1'b0;
            out_error <= 1'b0;
    end
end

endmodule
