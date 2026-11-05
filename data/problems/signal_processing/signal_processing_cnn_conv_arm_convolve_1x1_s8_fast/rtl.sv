module signal_processing_cnn_conv_arm_convolve_1x1_s8_fast #(
    parameter int IN_CH  = 8,
    parameter int OUT_CH = 8
) (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic [31:0] in_input_ch,
    input  logic [31:0] in_output_ch,
    input  logic signed [8:0] in_input_offset,
    input  logic signed [7:0] in_output_offset,
    input  logic signed [7:0] in_activation_min,
    input  logic signed [7:0] in_activation_max,
    input  logic signed [8*IN_CH-1:0] in_input_data,
    input  logic                        in_bias_valid,
    input  logic signed [32*OUT_CH-1:0] in_bias_data,
    input  logic signed [8*IN_CH*OUT_CH-1:0] in_filter_data,
    input  logic signed [32*OUT_CH-1:0] in_multiplier,
    input  logic signed [32*OUT_CH-1:0] in_shift,

    output logic out_valid,
    input  logic out_ready,
    output logic [31:0] out_output_ch,
    output logic signed [8*OUT_CH-1:0] out_output_data,
    output logic out_error
);

    logic occupied, invalid;
    logic [8*OUT_CH-1:0] result;
    logic signed [63:0] value, scaled, output_value;
    logic signed [31:0] x, w, multiplier, shift;
    assign in_ready = !occupied || out_ready;
    assign out_valid = occupied;
    function automatic logic signed [63:0] requantize(
        input logic signed [63:0] value,
        input logic signed [31:0] multiplier,
        input logic signed [31:0] shift
    );
        logic signed [63:0] scaled, product, high, magnitude;
        integer right;
        begin
            scaled = value <<< ((shift > 0) ? shift : 0);
            product = scaled * multiplier;
            high = (product + 64'sd1073741824) >>> 31;
            right = (shift < 0) ? -shift : 0;
            if (right == 0) requantize = high;
            else begin
                magnitude = ((high < 0 ? -high : high) + (64'sd1 <<< (right-1))) >>> right;
                requantize = high < 0 ? -magnitude : magnitude;
            end
        end
    endfunction

    always_comb begin
        invalid = in_input_ch != IN_CH || in_output_ch != OUT_CH ||
                  $signed(in_input_offset) < -127 || $signed(in_input_offset) > 128 ||
                  $signed(in_activation_min) > $signed(in_activation_max);
        result = '0; value = 0; scaled = 0; output_value = 0;
        x = 0; w = 0; multiplier = 0; shift = 0;
        for (int oc=0; oc<OUT_CH; oc++) begin
            value = in_bias_valid ? 64'($signed(in_bias_data[32*oc +: 32])) : 64'sd0;
            for (int ic=0; ic<IN_CH; ic++) begin
                x = $signed(in_input_data[8*ic +: 8]);
                w = $signed(in_filter_data[8*(oc*IN_CH+ic) +: 8]);
                value += (64'(x) + 64'($signed(in_input_offset))) * 64'(w);
            end
            multiplier = $signed(in_multiplier[32*oc +: 32]);
            shift = $signed(in_shift[32*oc +: 32]);
            if (multiplier < 0 || shift < -31 || shift > 30) invalid = 1;
            else begin
                scaled = value <<< ((shift > 0) ? shift : 0);
                if (scaled < -64'sd2147483648 || scaled > 64'sd2147483647) invalid = 1;
                else begin
                    output_value = requantize(value, multiplier, shift) + $signed(in_output_offset);
                    if (output_value < $signed(in_activation_min)) output_value = $signed(in_activation_min);
                    if (output_value > $signed(in_activation_max)) output_value = $signed(in_activation_max);
                    result[8*oc +: 8] = output_value[7:0];
                end
            end
        end
        if (invalid) result = '0;
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            occupied <= 0; out_output_ch <= 0; out_output_data <= 0; out_error <= 0;
        end else if (in_valid && in_ready) begin
            occupied <= 1; out_output_ch <= invalid ? 32'd0 : OUT_CH;
            out_output_data <= result; out_error <= invalid;
        end else if (out_ready) occupied <= 0;
    end
endmodule
