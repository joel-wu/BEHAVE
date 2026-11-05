module convolutional_encoder (
    input logic clk,
    input logic rst,
    input logic data_in,
    output logic encoded_bit1,
    output logic encoded_bit2
);
    logic [1:0] shift_reg;
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            shift_reg <= '0;
            encoded_bit1 <= 0;
            encoded_bit2 <= 0;
        end else begin
            encoded_bit1 <= data_in ^ shift_reg[0] ^ shift_reg[1];
            encoded_bit2 <= data_in ^ shift_reg[1];
            shift_reg <= {shift_reg[0], data_in};
        end
    end
endmodule
