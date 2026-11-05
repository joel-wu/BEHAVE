module cryptography_sym_chacha_chacha20_quarterround (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a,
    input  logic [31:0] in_b,
    input  logic [31:0] in_c,
    input  logic [31:0] in_d,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_a,
    output logic [31:0] out_b,
    output logic [31:0] out_c,
    output logic [31:0] out_d
);

logic busy;

wire input_accepted;
wire output_consumed;

logic [31:0] a1;
logic [31:0] d1;
logic [31:0] d2;
logic [31:0] c1;
logic [31:0] b1;
logic [31:0] b2;
logic [31:0] a2;
logic [31:0] d3;
logic [31:0] d4;
logic [31:0] c2;
logic [31:0] b3;

assign in_ready        = ~busy | output_consumed;
assign out_valid       = busy;
assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

always_ff @(posedge clk) begin
    if (rst) begin
        busy  <= 1'b0;
        out_a <= 32'h00000000;
        out_b <= 32'h00000000;
        out_c <= 32'h00000000;
        out_d <= 32'h00000000;
    end else if (input_accepted) begin
        a1 = in_a + in_b;
        d1 = in_d ^ a1;
        d2 = {d1[15:0], d1[31:16]};
        c1 = in_c + d2;
        b1 = in_b ^ c1;
        b2 = {b1[19:0], b1[31:20]};
        a2 = a1 + b2;
        d3 = d2 ^ a2;
        d4 = {d3[23:0], d3[31:24]};
        c2 = c1 + d4;
        b3 = b2 ^ c2;

        out_a <= a2;
        out_b <= {b3[24:0], b3[31:25]};
        out_c <= c2;
        out_d <= d4;
        busy  <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule