module JC_counter (
    input logic clk,
    input logic rst_n,
    output logic [63:0] Q
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) Q <= 64'b0;
        else Q <= {~Q[0], Q[63:1]};
    end
endmodule
