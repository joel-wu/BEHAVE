module keccak_theta (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_a0,
    input  logic [63:0]  in_a1,
    input  logic [63:0]  in_a2,
    input  logic [63:0]  in_a3,
    input  logic [63:0]  in_a4,
    input  logic [63:0]  in_a5,
    input  logic [63:0]  in_a6,
    input  logic [63:0]  in_a7,
    input  logic [63:0]  in_a8,
    input  logic [63:0]  in_a9,
    input  logic [63:0]  in_a10,
    input  logic [63:0]  in_a11,
    input  logic [63:0]  in_a12,
    input  logic [63:0]  in_a13,
    input  logic [63:0]  in_a14,
    input  logic [63:0]  in_a15,
    input  logic [63:0]  in_a16,
    input  logic [63:0]  in_a17,
    input  logic [63:0]  in_a18,
    input  logic [63:0]  in_a19,
    input  logic [63:0]  in_a20,
    input  logic [63:0]  in_a21,
    input  logic [63:0]  in_a22,
    input  logic [63:0]  in_a23,
    input  logic [63:0]  in_a24,

    output logic         out_valid,
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
wire  input_accepted;
wire  output_consumed;

logic [63:0] c0, c1, c2, c3, c4;
logic [63:0] d0, d1, d2, d3, d4;

function automatic logic [63:0] rotl64_1(input logic [63:0] x);
    begin
        rotl64_1 = {x[62:0], x[63]};
    end
endfunction

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    c0 = in_a0  ^ in_a5  ^ in_a10 ^ in_a15 ^ in_a20;
    c1 = in_a1  ^ in_a6  ^ in_a11 ^ in_a16 ^ in_a21;
    c2 = in_a2  ^ in_a7  ^ in_a12 ^ in_a17 ^ in_a22;
    c3 = in_a3  ^ in_a8  ^ in_a13 ^ in_a18 ^ in_a23;
    c4 = in_a4  ^ in_a9  ^ in_a14 ^ in_a19 ^ in_a24;

    d0 = c4 ^ rotl64_1(c1);
    d1 = c0 ^ rotl64_1(c2);
    d2 = c1 ^ rotl64_1(c3);
    d3 = c2 ^ rotl64_1(c4);
    d4 = c3 ^ rotl64_1(c0);
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy    <= 1'b0;
        out_a0  <= 64'b0;
        out_a1  <= 64'b0;
        out_a2  <= 64'b0;
        out_a3  <= 64'b0;
        out_a4  <= 64'b0;
        out_a5  <= 64'b0;
        out_a6  <= 64'b0;
        out_a7  <= 64'b0;
        out_a8  <= 64'b0;
        out_a9  <= 64'b0;
        out_a10 <= 64'b0;
        out_a11 <= 64'b0;
        out_a12 <= 64'b0;
        out_a13 <= 64'b0;
        out_a14 <= 64'b0;
        out_a15 <= 64'b0;
        out_a16 <= 64'b0;
        out_a17 <= 64'b0;
        out_a18 <= 64'b0;
        out_a19 <= 64'b0;
        out_a20 <= 64'b0;
        out_a21 <= 64'b0;
        out_a22 <= 64'b0;
        out_a23 <= 64'b0;
        out_a24 <= 64'b0;
    end else if (input_accepted) begin
        out_a0  <= in_a0  ^ d0;
        out_a1  <= in_a1  ^ d1;
        out_a2  <= in_a2  ^ d2;
        out_a3  <= in_a3  ^ d3;
        out_a4  <= in_a4  ^ d4;
        out_a5  <= in_a5  ^ d0;
        out_a6  <= in_a6  ^ d1;
        out_a7  <= in_a7  ^ d2;
        out_a8  <= in_a8  ^ d3;
        out_a9  <= in_a9  ^ d4;
        out_a10 <= in_a10 ^ d0;
        out_a11 <= in_a11 ^ d1;
        out_a12 <= in_a12 ^ d2;
        out_a13 <= in_a13 ^ d3;
        out_a14 <= in_a14 ^ d4;
        out_a15 <= in_a15 ^ d0;
        out_a16 <= in_a16 ^ d1;
        out_a17 <= in_a17 ^ d2;
        out_a18 <= in_a18 ^ d3;
        out_a19 <= in_a19 ^ d4;
        out_a20 <= in_a20 ^ d0;
        out_a21 <= in_a21 ^ d1;
        out_a22 <= in_a22 ^ d2;
        out_a23 <= in_a23 ^ d3;
        out_a24 <= in_a24 ^ d4;
        busy    <= 1'b1;
    end else if (output_consumed) begin
        busy    <= 1'b0;
        out_a0  <= 64'b0;
        out_a1  <= 64'b0;
        out_a2  <= 64'b0;
        out_a3  <= 64'b0;
        out_a4  <= 64'b0;
        out_a5  <= 64'b0;
        out_a6  <= 64'b0;
        out_a7  <= 64'b0;
        out_a8  <= 64'b0;
        out_a9  <= 64'b0;
        out_a10 <= 64'b0;
        out_a11 <= 64'b0;
        out_a12 <= 64'b0;
        out_a13 <= 64'b0;
        out_a14 <= 64'b0;
        out_a15 <= 64'b0;
        out_a16 <= 64'b0;
        out_a17 <= 64'b0;
        out_a18 <= 64'b0;
        out_a19 <= 64'b0;
        out_a20 <= 64'b0;
        out_a21 <= 64'b0;
        out_a22 <= 64'b0;
        out_a23 <= 64'b0;
        out_a24 <= 64'b0;
    end
end

endmodule