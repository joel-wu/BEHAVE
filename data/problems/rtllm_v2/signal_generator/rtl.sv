module signal_generator (
    input logic clk,
    input logic rst_n,
    output logic [4:0] wave
);
    logic direction;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wave <= 5'd0;
            direction <= 1'b0;
        end else if (!direction) begin
            if (wave == 5'd31) direction <= 1'b1;
            else wave <= wave + 5'd1;
        end else begin
            if (wave == 5'd0) direction <= 1'b0;
            else wave <= wave - 5'd1;
        end
    end
endmodule
