module signal_processing_cnn_fc_arm_fully_connected_s16 (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [1:0] in_batches,
    input  logic [3:0] in_depth,
    input  logic [2:0] in_outputs,
    input  logic [255:0] in_input,
    input  logic [255:0] in_kernel,
    input  logic [255:0] in_bias,
    input  logic [30:0] in_multiplier,
    input  logic signed [5:0] in_shift,
    input  logic signed [15:0] in_activation_min,
    input  logic signed [15:0] in_activation_max,
    output logic out_valid,
    input  logic out_ready,
    output logic [127:0] out_output,
    output logic out_error
);

logic [127:0] result;
logic invalid;
assign in_ready = !out_valid || out_ready;

always_comb begin : fully_connected
    logic signed [15:0] activation;
    logic signed [7:0] weight;
    logic signed [63:0] bias;
    logic signed [79:0] acc, scaled;
    logic [31:0] rounded_multiplier;
    logic signed [15:0] reduced;
    integer right_shift;
    result = '0;
    invalid = in_batches < 1 || in_batches > 2 ||
              in_depth < 1 || in_depth > 8 ||
              in_outputs < 1 || in_outputs > 4 ||
              in_shift < -6'sd31 || in_shift > 6'sd7 ||
              in_activation_min > in_activation_max;
    activation = '0;
    weight = '0;
    bias = '0;
    acc = '0;
    scaled = '0;
    rounded_multiplier = {1'b0, in_multiplier} + 32'd32768;
    reduced = in_multiplier >= 31'h7fff0000 ? 16'sd32767 :
              $signed(rounded_multiplier[31:16]);
    right_shift = 14 - int'($signed(in_shift));
    if (!invalid) begin
        for (integer batch = 0; batch < 2; batch = batch + 1) begin
            for (integer row = 0; row < 4; row = row + 1) begin
                if (batch < in_batches && row < in_outputs) begin
                    bias = $signed(in_bias[64*row +: 64]);
                    acc = {{16{bias[63]}}, bias};
                    for (integer col = 0; col < 8; col = col + 1) begin
                        if (col < in_depth) begin
                            activation = $signed(in_input[16*(batch*int'(in_depth)+col) +: 16]);
                            weight = $signed(in_kernel[8*(row*int'(in_depth)+col) +: 8]);
                            acc = acc + activation * weight;
                        end
                    end
                    scaled = acc * reduced;
                    scaled = ((scaled >>> right_shift) + 80'sd1) >>> 1;
                    if (scaled < in_activation_min)
                        scaled = {{64{in_activation_min[15]}}, in_activation_min};
                    if (scaled > in_activation_max)
                        scaled = {{64{in_activation_max[15]}}, in_activation_max};
                    result[16*(batch*int'(in_outputs)+row) +: 16] = scaled[15:0];
                end
            end
        end
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        out_valid <= 1'b0;
        out_output <= '0;
        out_error <= 1'b0;
    end else if (in_ready) begin
        out_valid <= in_valid;
        if (in_valid) begin
            out_output <= result;
            out_error <= invalid;
        end
    end
end
endmodule
