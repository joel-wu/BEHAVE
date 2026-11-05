module binary_to_one_hot_decoder #(
    parameter int BINARY_WIDTH = 5, parameter int OUTPUT_WIDTH = 32
) (
    input logic [BINARY_WIDTH-1:0] binary_in,
    output logic [OUTPUT_WIDTH-1:0] one_hot_out
);
    always_comb begin
        one_hot_out='0;
        if (binary_in<OUTPUT_WIDTH) one_hot_out[binary_in]=1'b1;
    end
endmodule
