module LFSR (
    input logic clk,
    input logic rst,
    output logic [3:0] out
);
    always_ff @(posedge clk or posedge rst) begin
        if (rst) out <= 4'b0000;
        else out <= {out[2:0], ~(out[3] ^ out[2])};
    end
endmodule
