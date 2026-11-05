module piso_8bit (
    input logic clk, rst,
    output logic serial_out
);
    logic [7:0] tmp;
    logic [2:0] bit_index;
    always_ff @(posedge clk or negedge rst) begin
        if (!rst) begin tmp<=1; bit_index<=0; serial_out<=0; end
        else begin
            serial_out<=tmp[7-bit_index];
            bit_index<=bit_index+1'b1;
            if (bit_index==7) tmp<=tmp+1'b1;
        end
    end
endmodule
