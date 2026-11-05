module signed_output_i8 (
    input  logic              clk,
    input  logic              rst,

    input  logic              in_valid,
    output logic              in_ready,
    input  logic              in_sel,

    output logic              out_valid,
    input  logic              out_ready,
    output logic signed [7:0] out_y
);

    logic busy;

    wire input_accepted = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready = ~busy;
    assign out_valid = busy;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy <= 1'b0;
            out_y <= 8'sd0;
        end else begin
            if (input_accepted) begin
                busy <= 1'b1;
                out_y <= -8'sd1;
            end else if (output_consumed) begin
                busy <= 1'b0;
            end
        end
    end

endmodule
