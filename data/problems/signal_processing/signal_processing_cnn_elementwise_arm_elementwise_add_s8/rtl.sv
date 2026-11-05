module signal_processing_cnn_elementwise_arm_elementwise_add_s8 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_input_1,
    input  logic [7:0]   in_input_2,
    input  logic [31:0]  in_input_1_offset,
    input  logic [31:0]  in_input_1_mult,
    input  logic [31:0]  in_input_1_shift,
    input  logic [31:0]  in_input_2_offset,
    input  logic [31:0]  in_input_2_mult,
    input  logic [31:0]  in_input_2_shift,
    input  logic [31:0]  in_left_shift,
    input  logic [31:0]  in_out_offset,
    input  logic [31:0]  in_out_mult,
    input  logic [31:0]  in_out_shift,
    input  logic [31:0]  in_out_activation_min,
    input  logic [31:0]  in_out_activation_max,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_output,
    output logic         out_error
);

    logic occupied;
    logic signed [31:0] a, b, o1, o2, m1, m2, mo, s1, s2, so, left_count, offset, lo, hi;
    logic signed [63:0] r1, r2, value;
    logic invalid;
    logic [7:0] result;
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
        a = $signed(in_input_1); b = $signed(in_input_2);
        o1 = $signed(in_input_1_offset); o2 = $signed(in_input_2_offset);
        m1 = $signed(in_input_1_mult); m2 = $signed(in_input_2_mult); mo = $signed(in_out_mult);
        s1 = $signed(in_input_1_shift); s2 = $signed(in_input_2_shift); so = $signed(in_out_shift);
        left_count = $signed(in_left_shift); offset = $signed(in_out_offset);
        lo = $signed(in_out_activation_min); hi = $signed(in_out_activation_max);
        invalid = o1 < -127 || o1 > 128 || o2 < -127 || o2 > 128 ||
                  m1 < 0 || m2 < 0 || mo < 0 || left_count < 0 || left_count > 20 ||
                  s1 < -31 || s1 > 0 || s2 < -31 || s2 > 0 || so < -31 || so > 0 ||
                  offset < -128 || offset > 127 || lo < -128 || hi > 127 || lo > hi;
        r1 = 0; r2 = 0; value = 0; result = 0;
        if (!invalid) begin
            r1 = requantize((64'(a) + 64'(o1)) <<< left_count, m1, s1);
            r2 = requantize((64'(b) + 64'(o2)) <<< left_count, m2, s2);
            value = requantize(r1 + r2, mo, so) + offset;
            if (value < lo) value = lo;
            if (value > hi) value = hi;
            result = value[7:0];
        end
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            occupied <= 0; out_output <= 0; out_error <= 0;
        end else if (in_valid && in_ready) begin
            occupied <= 1; out_output <= result; out_error <= invalid;
        end else if (out_ready) occupied <= 0;
    end
endmodule
