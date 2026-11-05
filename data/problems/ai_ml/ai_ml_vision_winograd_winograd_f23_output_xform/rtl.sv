module ai_ml_vision_winograd_winograd_f23_output_xform (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic signed [15:0] in_m0,
    input  logic signed [15:0] in_m1,
    input  logic signed [15:0] in_m2,
    input  logic signed [15:0] in_m3,
    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [17:0] out_y0,
    output logic signed [17:0] out_y1
);

logic busy;
logic signed [17:0] y0_calc;
logic signed [17:0] y1_calc;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    y0_calc = $signed(in_m0) + $signed(in_m1) + $signed(in_m2);
    y1_calc = $signed(in_m1) - $signed(in_m2) - $signed(in_m3);
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy   <= 1'b0;
        out_y0 <= 18'sd0;
        out_y1 <= 18'sd0;
    end else if (input_accepted) begin
        out_y0 <= y0_calc;
        out_y1 <= y1_calc;
        busy   <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule