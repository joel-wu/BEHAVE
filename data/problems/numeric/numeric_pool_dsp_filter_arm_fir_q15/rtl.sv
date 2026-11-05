module fir_q15_sample (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_sample,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_sample
);

logic busy;
logic signed [15:0] state0;
logic signed [15:0] state1;
logic signed [15:0] state2;
logic signed [15:0] state3;
logic signed [15:0] state4;
logic signed [15:0] state5;
logic signed [15:0] state6;
logic signed [15:0] state7;

wire input_accepted;
wire output_consumed;

logic signed [15:0] sample_s;
logic signed [31:0] prod0;
logic signed [31:0] prod1;
logic signed [31:0] prod2;
logic signed [31:0] prod3;
logic signed [31:0] prod4;
logic signed [31:0] prod5;
logic signed [31:0] prod6;
logic signed [31:0] prod7;
logic signed [63:0] acc;
logic signed [63:0] shifted;
logic signed [15:0] fir_result;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    sample_s = $signed(in_sample);

    prod0 = sample_s * 16'sh1000;
    prod1 = state0    * 16'sh1000;
    prod2 = state1    * 16'sh1000;
    prod3 = state2    * 16'sh1000;
    prod4 = state3    * 16'sh1000;
    prod5 = state4    * 16'sh1000;
    prod6 = state5    * 16'sh1000;
    prod7 = state6    * 16'sh1000;

    acc = $signed(prod0) + $signed(prod1) + $signed(prod2) + $signed(prod3) +
          $signed(prod4) + $signed(prod5) + $signed(prod6) + $signed(prod7);

    shifted = acc >>> 15;
    fir_result = shifted[15:0];
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        out_sample <= 16'h0000;
        state0     <= 16'sh0000;
        state1     <= 16'sh0000;
        state2     <= 16'sh0000;
        state3     <= 16'sh0000;
        state4     <= 16'sh0000;
        state5     <= 16'sh0000;
        state6     <= 16'sh0000;
        state7     <= 16'sh0000;
    end else if (input_accepted) begin
        out_sample <= fir_result;
        busy       <= 1'b1;

        state0 <= sample_s;
        state1 <= state0;
        state2 <= state1;
        state3 <= state2;
        state4 <= state3;
        state5 <= state4;
        state6 <= state5;
        state7 <= state6;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule