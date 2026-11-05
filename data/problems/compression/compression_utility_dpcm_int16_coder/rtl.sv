module compression_utility_dpcm_int16_coder (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_load_history,
    input  logic [15:0]  in_history,
    input  logic [15:0]  in_sample,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_residual
);

logic busy;
logic signed [15:0] history_sample;

wire input_accepted;
wire output_consumed;

logic signed [15:0] predictor_s;
logic signed [15:0] sample_s;
logic signed [15:0] residual_s;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    sample_s = $signed(in_sample);
    if (in_load_history) begin
        predictor_s = $signed(in_history);
    end else begin
        predictor_s = history_sample;
    end
    residual_s = sample_s - predictor_s;
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy           <= 1'b0;
        history_sample <= 16'sd0;
        out_residual   <= 16'h0000;
    end else if (input_accepted) begin
        busy           <= 1'b1;
        history_sample <= sample_s;
        out_residual   <= residual_s[15:0];
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule