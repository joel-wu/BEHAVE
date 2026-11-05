module cryptography_sym_siphash_siphash_round (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_v0,
    input  logic [63:0]  in_v1,
    input  logic [63:0]  in_v2,
    input  logic [63:0]  in_v3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_v0,
    output logic [63:0]  out_v1,
    output logic [63:0]  out_v2,
    output logic [63:0]  out_v3
);

    logic busy;
    wire  input_accepted;
    wire  output_consumed;

    logic [63:0] calc_v0;
    logic [63:0] calc_v1;
    logic [63:0] calc_v2;
    logic [63:0] calc_v3;

    logic [63:0] t_v0;
    logic [63:0] t_v1;
    logic [63:0] t_v2;
    logic [63:0] t_v3;

    function automatic logic [63:0] rotl64(input logic [63:0] x, input integer n);
        logic [63:0] left_part;
        logic [63:0] right_part;
        begin
            left_part  = x << n;
            right_part = x >> (64 - n);
            rotl64     = left_part | right_part;
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        t_v0 = in_v0;
        t_v1 = in_v1;
        t_v2 = in_v2;
        t_v3 = in_v3;

        t_v0 = t_v0 + t_v1;
        t_v1 = rotl64(t_v1, 13);
        t_v1 = t_v1 ^ t_v0;
        t_v0 = rotl64(t_v0, 32);
        t_v2 = t_v2 + t_v3;
        t_v3 = rotl64(t_v3, 16);
        t_v3 = t_v3 ^ t_v2;
        t_v0 = t_v0 + t_v3;
        t_v3 = rotl64(t_v3, 21);
        t_v3 = t_v3 ^ t_v0;
        t_v2 = t_v2 + t_v1;
        t_v1 = rotl64(t_v1, 17);
        t_v1 = t_v1 ^ t_v2;
        t_v2 = rotl64(t_v2, 32);

        calc_v0 = t_v0;
        calc_v1 = t_v1;
        calc_v2 = t_v2;
        calc_v3 = t_v3;
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy   <= 1'b0;
            out_v0 <= 64'd0;
            out_v1 <= 64'd0;
            out_v2 <= 64'd0;
            out_v3 <= 64'd0;
        end else if (input_accepted) begin
            busy   <= 1'b1;
            out_v0 <= calc_v0;
            out_v1 <= calc_v1;
            out_v2 <= calc_v2;
            out_v3 <= calc_v3;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule