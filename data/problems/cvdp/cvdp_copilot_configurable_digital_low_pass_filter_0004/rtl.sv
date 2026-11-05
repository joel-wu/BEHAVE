module advanced_decimator_with_adaptive_peak_detection #(
    parameter int N = 8,
    parameter int DATA_WIDTH = 16,
    parameter int DEC_FACTOR = 4
) (
    input logic clk,
    input logic reset,
    input logic valid_in,
    input logic [DATA_WIDTH*N-1:0] data_in,
    output logic valid_out,
    output logic [DATA_WIDTH*(N/DEC_FACTOR)-1:0] data_out,
    output logic [DATA_WIDTH-1:0] peak_value
);
    localparam int M = N / DEC_FACTOR;
    logic [DATA_WIDTH*N-1:0] frame;
    logic signed [DATA_WIDTH-1:0] sample_value, peak;
    always_comb begin
        data_out = '0;
        peak = $signed(frame[DATA_WIDTH*N-1 -: DATA_WIDTH]);
        sample_value = '0;
        for (int j=0; j<M; j++) begin
            sample_value = $signed(frame[DATA_WIDTH*(N-j*DEC_FACTOR)-1 -: DATA_WIDTH]);
            data_out[DATA_WIDTH*(M-j)-1 -: DATA_WIDTH] = sample_value;
            if (sample_value > peak) peak = sample_value;
        end
        peak_value = peak;
    end
    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            frame <= '0;
            valid_out <= 0;
        end else begin
            frame <= data_in;
            valid_out <= valid_in;
        end
    end
endmodule
