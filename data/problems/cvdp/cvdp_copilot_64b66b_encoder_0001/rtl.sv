module encoder_64b66b (
    input logic clk_in,
    input logic rst_in,
    input logic [63:0] encoder_data_in,
    input logic [7:0] encoder_control_in,
    output logic [65:0] encoder_data_out
);
    always_ff @(posedge clk_in or posedge rst_in) begin
        if (rst_in) encoder_data_out <= '0;
        else encoder_data_out <= encoder_control_in == 0
                              ? {2'b01, encoder_data_in} : {2'b10, 64'b0};
    end
endmodule
