module signal_processing_dsp_math_trapezoidal_int (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [31:0] in_x,
    input  logic [31:0] in_y,
    output logic out_valid,
    input  logic out_ready,
    output logic [95:0] out_sum
);

logic signed [31:0] prev_x, prev_y;
logic [95:0] area_twice;
logic have_prev;
logic signed [32:0] dx, ys;
logic signed [65:0] contribution;
logic [95:0] next_area;
assign dx = $signed({in_x[31], in_x}) - $signed({prev_x[31], prev_x});
assign ys = $signed({in_y[31], in_y}) + $signed({prev_y[31], prev_y});
assign contribution = dx * ys;
assign next_area = area_twice + {{30{contribution[65]}}, contribution};
assign in_ready = !out_valid || out_ready;

always_ff @(posedge clk) begin
    if (rst) begin
        prev_x <= '0;
        prev_y <= '0;
        area_twice <= '0;
        have_prev <= 1'b0;
        out_valid <= 1'b0;
        out_sum <= '0;
    end else if (in_ready) begin
        out_valid <= 1'b0;
        if (in_valid) begin
            prev_x <= $signed(in_x);
            prev_y <= $signed(in_y);
            have_prev <= 1'b1;
            if (have_prev) begin
                area_twice <= next_area;
                out_sum <= next_area;
                out_valid <= 1'b1;
            end
        end
    end
end
endmodule
