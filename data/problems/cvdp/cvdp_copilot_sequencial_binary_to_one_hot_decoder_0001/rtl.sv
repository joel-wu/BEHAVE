module binary_to_one_hot_decoder_sequencial #(
    parameter int BINARY_WIDTH = 5, parameter int OUTPUT_WIDTH = 32
) (
    input logic i_clk, i_rstb,
    input logic [BINARY_WIDTH-1:0] i_binary_in,
    output logic [OUTPUT_WIDTH-1:0] o_one_hot_out
);
    logic [OUTPUT_WIDTH-1:0] decoded;
    always_comb begin
        decoded='0;
        if (i_binary_in<OUTPUT_WIDTH) decoded[i_binary_in]=1'b1;
    end
    always_ff @(posedge i_clk or negedge i_rstb) begin
        if (!i_rstb) o_one_hot_out<='0;
        else o_one_hot_out<=decoded;
    end
endmodule
