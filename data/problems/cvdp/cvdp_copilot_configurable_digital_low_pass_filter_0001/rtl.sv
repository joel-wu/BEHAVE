module low_pass_filter #(
    parameter int DATA_WIDTH = 16,
    parameter int COEFF_WIDTH = 16,
    parameter int NUM_TAPS = 8
) (
    input logic clk, input logic reset,
    input logic valid_in,
    input logic [DATA_WIDTH*NUM_TAPS-1:0] data_in,
    input logic [COEFF_WIDTH*NUM_TAPS-1:0] coeffs,
    output logic valid_out,
    output logic [DATA_WIDTH+COEFF_WIDTH+$clog2(NUM_TAPS)-1:0] data_out
);
    localparam int NBW_MULT = DATA_WIDTH + COEFF_WIDTH;
    localparam int OUT_WIDTH = NBW_MULT + $clog2(NUM_TAPS);
    logic [DATA_WIDTH*NUM_TAPS-1:0] data_q;
    logic [COEFF_WIDTH*NUM_TAPS-1:0] coeff_q;
    logic signed [NBW_MULT-1:0] product;
    logic signed [OUT_WIDTH-1:0] total;
    always_comb begin
        total = '0;
        product = '0;
        for (int i=0; i<NUM_TAPS; i++) begin
            product = $signed(data_q[i*DATA_WIDTH +: DATA_WIDTH]) *
                      $signed(coeff_q[(NUM_TAPS-1-i)*COEFF_WIDTH +: COEFF_WIDTH]);
            total = total + OUT_WIDTH'(product);
        end
        data_out = total;
    end
    always_ff @(posedge clk) begin
        if (reset) begin
            data_q <= '0; coeff_q <= '0; valid_out <= 0;
        end else begin
            valid_out <= valid_in;
            if (valid_in) begin data_q <= data_in; coeff_q <= coeffs; end
        end
    end
endmodule
