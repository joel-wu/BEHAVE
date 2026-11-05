module edge_detect (
    input logic clk,
    input logic rst_n,
    input logic a,
    output logic rise,
    output logic down
);
    logic prev_a;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            prev_a <= 1'b0;
            rise <= 1'b0;
            down <= 1'b0;
        end else begin
            rise <= a & ~prev_a;
            down <= ~a & prev_a;
            prev_a <= a;
        end
    end
endmodule
