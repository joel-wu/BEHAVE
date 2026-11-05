module DilithiumDecompose44 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [22:0] in_a,

    output logic        out_valid,
    output logic out_error,
    input  logic        out_ready,
    output logic [23:0] out_a0,
    output logic [5:0]  out_a1
);

    logic busy;
    wire invalid_input = in_a >= 23'd8380417;

    wire input_accepted;
    wire output_consumed;

    logic [15:0] t_val;
    logic [29:0] prod_val;
    logic [5:0]  u_val;
    logic [5:0]  a1_val;
    logic signed [24:0] in_a_s;
    logic [23:0] a1_mul_const;
    logic signed [24:0] a1_mul_s;
    logic signed [24:0] a0_pre_val;
    logic signed [24:0] a0_val;
    logic [23:0] a0_bits;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        t_val       = (in_a + 23'd127) >> 7;
        prod_val    = (t_val * 16'd11275) + 30'd8388608;
        u_val       = prod_val >> 24;
        if (u_val == 6'd44)
            a1_val = 6'd0;
        else
            a1_val = u_val;

        in_a_s      = $signed({1'b0, in_a});
        a1_mul_const = a1_val * 18'd190464;
        a1_mul_s    = $signed({1'b0, a1_mul_const});
        a0_pre_val  = in_a_s - a1_mul_s;

        if (a0_pre_val > 25'sd4190208)
            a0_val = a0_pre_val - 25'sd8380417;
        else
            a0_val = a0_pre_val;

        a0_bits = a0_val[23:0];
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy   <= 1'b0;
            out_error <= 1'b0;
            out_a0 <= 24'h000000;
            out_a1 <= 6'h00;
        end else if (input_accepted) begin
            out_a0 <= invalid_input ? '0 : (a0_bits);
            out_a1 <= invalid_input ? '0 : (a1_val);
            busy   <= 1'b1;
            out_error <= invalid_input;
        end else if (output_consumed) begin
            busy <= 1'b0;
            out_error <= 1'b0;
        end
    end

endmodule
