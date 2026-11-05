module cryptography_sym_blake_blake2b_g_quarter (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_a,
    input  logic [63:0]  in_b,
    input  logic [63:0]  in_c,
    input  logic [63:0]  in_d,
    input  logic [63:0]  in_m0,
    input  logic [63:0]  in_m1,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_a,
    output logic [63:0]  out_b,
    output logic [63:0]  out_c,
    output logic [63:0]  out_d
);

    logic busy;

    wire input_accepted;
    wire output_consumed;

    logic [63:0] a1;
    logic [63:0] b1;
    logic [63:0] c1;
    logic [63:0] d1;
    logic [63:0] a2;
    logic [63:0] c2;
    logic [63:0] d2;

    function automatic logic [63:0] rotr64_32(input logic [63:0] x);
        rotr64_32 = {x[31:0], x[63:32]};
    endfunction

    function automatic logic [63:0] rotr64_24(input logic [63:0] x);
        rotr64_24 = {x[23:0], x[63:24]};
    endfunction

    function automatic logic [63:0] rotr64_16(input logic [63:0] x);
        rotr64_16 = {x[15:0], x[63:16]};
    endfunction

    function automatic logic [63:0] rotr64_63(input logic [63:0] x);
        rotr64_63 = {x[62:0], x[63]};
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy  <= 1'b0;
            out_a <= 64'd0;
            out_b <= 64'd0;
            out_c <= 64'd0;
            out_d <= 64'd0;
        end else if (input_accepted) begin
            a1 = in_a + in_b + in_m0;
            d1 = rotr64_32(in_d ^ a1);
            c1 = in_c + d1;
            b1 = rotr64_24(in_b ^ c1);
            a2 = a1 + b1 + in_m1;
            d2 = rotr64_16(d1 ^ a2);
            c2 = c1 + d2;

            out_a <= a2;
            out_b <= rotr64_63(b1 ^ c2);
            out_c <= c2;
            out_d <= d2;
            busy  <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule