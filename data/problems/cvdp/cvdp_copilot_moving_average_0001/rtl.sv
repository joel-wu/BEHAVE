module moving_average (
    input logic clk, reset,
    input logic [11:0] data_in,
    output logic [11:0] data_out
);
    logic [11:0] history [0:7];
    logic [2:0] oldest;
    logic [14:0] total;
    assign data_out=total[14:3];
    always_ff @(posedge clk) begin
        if (reset) begin
            oldest<=0; total<=0;
            for (int i=0; i<8; i++) history[i]<=0;
        end else begin
            total<=total-15'(history[oldest])+15'(data_in);
            history[oldest]<=data_in;
            oldest<=oldest+1'b1;
        end
    end
endmodule
