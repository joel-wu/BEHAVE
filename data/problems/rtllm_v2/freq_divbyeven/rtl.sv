module freq_divbyeven (
    input logic clk,
    input logic rst_n,
    output logic clk_div
);
    logic [3:0] cnt;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cnt <= 4'd0;
            clk_div <= 1'b0;
        end else if (cnt < 4'd2) begin
            cnt <= cnt + 4'd1;
        end else begin
            cnt <= 4'd0;
            clk_div <= ~clk_div;
        end
    end
endmodule
