module signed_input_flag_bad (
    input  logic              clk,
    input  logic              rst,

    input  logic              in_valid,
    output logic              in_ready,
    input  logic signed [7:0] in_x,

    output logic              out_valid,
    input  logic              out_ready,
    output logic              out_neg
);

    logic busy;

    wire input_accepted = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy <= 1'b0;
            out_neg <= 1'b0;
        end else if (input_accepted) begin
            busy <= 1'b1;
            out_neg <= (in_x < 8'sd0);
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
