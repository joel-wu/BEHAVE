module DilithiumPower2Round (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [22:0]  in_a,

    output logic         out_valid,
    output logic out_error,
    input  logic         out_ready,
    output logic [13:0]  out_a0,
    output logic [9:0]   out_a1
);

    logic busy;
    wire invalid_input = in_a >= 23'd8380417;

    wire input_accepted;
    wire output_consumed;

    logic [23:0] a1_ext;
    logic [22:0] a1_shifted;
    logic [22:0] a0_bits;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy   <= 1'b0;
            out_error <= 1'b0;
            out_a0 <= 14'd0;
            out_a1 <= 10'b0000000000;
        end else if (input_accepted) begin
            a1_ext     = ({1'b0, in_a} + 24'd4095) >> 13;
            a1_shifted = a1_ext[22:0] << 13;
            a0_bits    = in_a - a1_shifted;

            out_a1 <= invalid_input ? '0 : (a1_ext[9:0]);
            out_a0 <= invalid_input ? '0 : (a0_bits[13:0]);
            busy   <= 1'b1;
            out_error <= invalid_input;
        end else if (output_consumed) begin
            busy <= 1'b0;
            out_error <= 1'b0;
        end
    end

endmodule
