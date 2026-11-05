module int8_per_tensor_quant (
    input logic clk, input logic rst,
    input logic in_valid, output logic in_ready,
    input logic [127:0] in_values, input logic [31:0] in_scale, input logic [7:0] in_zero_point,
    output logic out_valid, input logic out_ready,
    output logic [63:0] out_quantized, output logic out_error
);
    logic busy;
    assign in_ready = !busy || out_ready;
    assign out_valid = busy;
    logic [63:0] quantized;
    logic signed [63:0] value, q, zero;
    logic [63:0] numerator, remainder, denominator;
    always_comb begin
        quantized = 0;
        value = 0;
        q = 0;
        numerator = 0;
        remainder = 0;
        denominator = {32'b0, in_scale};
        zero = {{56{in_zero_point[7]}}, in_zero_point};
        if (in_scale != 0) begin
            for (integer i = 0; i < 8; i = i + 1) begin
                value = {{48{in_values[16*i+15]}}, in_values[16*i +: 16]};
                numerator = (value < 0 ? -value : value) * 64'd256;
                q = numerator / denominator;
                remainder = numerator % denominator;
                if (2*remainder > denominator || (2*remainder == denominator && q[0])) q = q + 1;
                if (value < 0) q = -q;
                q = q + zero;
                if (q > 127) q = 127;
                if (q < -128) q = -128;
                quantized[8*i +: 8] = q[7:0];
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            out_quantized <= 0; out_error <= 0;
        end else if (in_valid && in_ready) begin
            busy <= 1;
            out_quantized <= quantized; out_error <= in_scale == 0;
        end else if (out_ready) busy <= 0;
    end
endmodule
