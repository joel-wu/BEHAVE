module counter_12 (
    input logic clk,
    input logic rst_n,
    input logic valid_count,
    output logic [3:0] out
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) out <= 0;
        else if (valid_count) out <= out == 11 ? 4'd0 : out + 1'b1;
    end
endmodule
