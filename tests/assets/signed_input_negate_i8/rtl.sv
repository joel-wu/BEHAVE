module signed_input_negate_i8 (
    input  logic              clk,
    input  logic              rst,

    input  logic              in_valid,
    output logic              in_ready,
    input  logic [6:0]        in_mag,

    output logic              out_valid,
    input  logic              out_ready,
    output logic signed [7:0] out_y
);

    logic busy;
    logic signed [7:0] neg_next;

    wire input_accepted = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready = ~busy | output_consumed;
    assign out_valid = busy;
    assign neg_next = -$signed({1'b0, in_mag});

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy <= 1'b0;
            out_y <= 8'sd0;
        end else if (input_accepted) begin
            busy <= 1'b1;
            out_y <= neg_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
