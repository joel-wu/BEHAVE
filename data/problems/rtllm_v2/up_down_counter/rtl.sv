module up_down_counter (
    input logic clk,
    input logic reset,
    input logic up_down,
    output logic [15:0] count
);
    always_ff @(posedge clk or posedge reset) begin
        if (reset) count <= 0;
        else if (up_down) count <= count + 1'b1;
        else count <= count - 1'b1;
    end
endmodule
