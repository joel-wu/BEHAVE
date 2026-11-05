module reference_clock_generator #(
    parameter int PERIOD = 10
) (
    input logic clk,
    input logic rst,
    output logic clk_out
);
    localparam integer HALF_PERIOD = PERIOD / 2;
    localparam integer COUNT_WIDTH = HALF_PERIOD > 1 ? $clog2(HALF_PERIOD) : 1;
    logic [COUNT_WIDTH-1:0] phase;
    always @(posedge clk) begin
        if (rst) begin
            phase <= 0;
            clk_out <= 0;
        end else if (phase == HALF_PERIOD-1) begin
            phase <= 0;
            clk_out <= ~clk_out;
        end else begin
            phase <= phase + 1'b1;
        end
    end
endmodule
