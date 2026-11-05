module q15_clip_element (
    input  logic               clk,
    input  logic               rst,

    input  logic               in_valid,
    output logic               in_ready,
    input  logic [15:0]        in_sample,
    input  logic [15:0]        in_low,
    input  logic [15:0]        in_high,

    output logic               out_valid,
    input  logic               out_ready,
    output logic [15:0]        out_sample
);

logic busy;

wire input_accepted;
wire output_consumed;

logic signed [15:0] sample_s;
logic signed [15:0] low_s;
logic signed [15:0] high_s;
logic signed [15:0] result_s;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    sample_s = $signed(in_sample);
    low_s    = $signed(in_low);
    high_s   = $signed(in_high);

    if (sample_s > high_s) begin
        result_s = high_s;
    end else if (sample_s < low_s) begin
        result_s = low_s;
    end else begin
        result_s = sample_s;
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        out_sample <= 16'h0000;
    end else if (input_accepted) begin
        out_sample <= result_s;
        busy       <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule