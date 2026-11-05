module compression_flac_predict_flac_fixed_predictor_order1_4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [2:0]   in_order,
    input  logic [31:0]  in_sample,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_residual
);

logic busy;

logic signed [31:0] sample_z1;
logic signed [31:0] sample_z2;
logic signed [31:0] sample_z3;
logic signed [31:0] sample_z4;

logic [31:0] out_residual_reg;

logic signed [34:0] x_s;
logic signed [34:0] s1_s;
logic signed [34:0] s2_s;
logic signed [34:0] s3_s;
logic signed [34:0] s4_s;
logic signed [34:0] residual_calc;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready     = ~busy | output_consumed;
assign out_valid    = busy;
assign out_residual = busy ? out_residual_reg : 32'h00000000;

always_comb begin
    x_s = {{3{in_sample[31]}}, in_sample};
    s1_s = {{3{sample_z1[31]}}, sample_z1};
    s2_s = {{3{sample_z2[31]}}, sample_z2};
    s3_s = {{3{sample_z3[31]}}, sample_z3};
    s4_s = {{3{sample_z4[31]}}, sample_z4};
    residual_calc = 35'sd0;

    unique case (in_order)
        3'b001: residual_calc = x_s - s1_s;
        3'b010: residual_calc = x_s - (s1_s <<< 1) + s2_s;
        3'b011: residual_calc = x_s - (s1_s + (s1_s <<< 1)) + (s2_s + (s2_s <<< 1)) - s3_s;
        3'b100: residual_calc = x_s - (s1_s <<< 2) + ((s2_s <<< 2) + (s2_s <<< 1)) - (s3_s <<< 2) + s4_s;
        default: residual_calc = 35'sd0;
    endcase
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy             <= 1'b0;
        sample_z1        <= 32'sd0;
        sample_z2        <= 32'sd0;
        sample_z3        <= 32'sd0;
        sample_z4        <= 32'sd0;
        out_residual_reg <= 32'h00000000;
    end else if (input_accepted) begin
        out_residual_reg <= residual_calc[31:0];
        sample_z4        <= sample_z3;
        sample_z3        <= sample_z2;
        sample_z2        <= sample_z1;
        sample_z1        <= $signed(in_sample);
        busy             <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
