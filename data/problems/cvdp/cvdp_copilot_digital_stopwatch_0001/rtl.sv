module dig_stopwatch #(
    parameter int CLK_FREQ = 50000000
) (
    input logic clk, input logic reset, input logic start_stop,
    output logic [5:0] seconds, minutes, output logic hour
);
    localparam int COUNT_WIDTH = $clog2(CLK_FREQ);
    logic [COUNT_WIDTH-1:0] cycle_count;
    logic one_sec_pulse;
    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            cycle_count <= 0; seconds <= 0; minutes <= 0; hour <= 0; one_sec_pulse <= 0;
        end else begin
            one_sec_pulse <= 0;
            if (start_stop && !hour) begin
                if (cycle_count == CLK_FREQ-1) begin
                    cycle_count <= 0;
                    one_sec_pulse <= 1;
                    if (seconds == 59) begin
                        seconds <= 0;
                        if (minutes == 59) begin minutes <= 0; hour <= 1; end
                        else minutes <= minutes + 1'b1;
                    end else seconds <= seconds + 1'b1;
                end else cycle_count <= cycle_count + 1'b1;
            end
        end
    end
endmodule
