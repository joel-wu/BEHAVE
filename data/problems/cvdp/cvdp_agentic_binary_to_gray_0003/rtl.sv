module binary_to_gray #(parameter int WIDTH = 6) (
    input logic [WIDTH-1:0] binary_in,
    output logic [WIDTH-1:0] gray_out
);
    assign gray_out=binary_in^(binary_in>>1);
endmodule
