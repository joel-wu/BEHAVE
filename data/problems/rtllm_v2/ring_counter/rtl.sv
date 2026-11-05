module ring_counter (
    input logic clk,
    input logic reset,
    output logic [7:0] out
);
    always_ff @(posedge clk or posedge reset) begin
        if (reset) out <= 8'b00000001;
        else out <= {out[6:0], out[7]};
    end
endmodule
