module signal_processing_cnn_elementwise_arm_maximum_s8 (
    input  logic          clk,
    input  logic          rst,
    input  logic          in_valid,
    output logic          in_ready,
    input  logic [2:0] in_input_1_n,
    input  logic [2:0] in_input_1_h,
    input  logic [2:0] in_input_1_w,
    input  logic [2:0] in_input_1_c,
    input  logic [2:0] in_input_2_n,
    input  logic [2:0] in_input_2_h,
    input  logic [2:0] in_input_2_w,
    input  logic [2:0] in_input_2_c,
    input  logic [1:0] in_out_n,
    input  logic [1:0] in_out_h,
    input  logic [1:0] in_out_w,
    input  logic [1:0] in_out_c,
    input  logic [2047:0] in_input_1_data,
    input  logic [2047:0] in_input_2_data,
    output logic          out_valid,
    input  logic          out_ready,
    output logic [7:0]    out_value,
    output logic          out_error
);

    logic busy;
    logic invalid;
    logic [7:0] value;
    logic signed [7:0] a, b;
    integer shape1 [0:3];
    integer shape2 [0:3];
    integer coordinate [0:3];
    integer offset1, offset2, extent;

    always_comb begin
        shape1[0] = {29'd0,in_input_1_n};
        shape2[0] = {29'd0,in_input_2_n};
        coordinate[0] = {30'd0,in_out_n};
        shape1[1] = {29'd0,in_input_1_h};
        shape2[1] = {29'd0,in_input_2_h};
        coordinate[1] = {30'd0,in_out_h};
        shape1[2] = {29'd0,in_input_1_w};
        shape2[2] = {29'd0,in_input_2_w};
        coordinate[2] = {30'd0,in_out_w};
        shape1[3] = {29'd0,in_input_1_c};
        shape2[3] = {29'd0,in_input_2_c};
        coordinate[3] = {30'd0,in_out_c};
        invalid = 0; offset1 = 0; offset2 = 0; extent = 0;
        a = 0; b = 0; value = 0;
        for (integer j = 0; j < 4; j = j + 1) begin
            extent = shape1[j] > shape2[j] ? shape1[j] : shape2[j];
            if (shape1[j] < 1 || shape1[j] > 4 || shape2[j] < 1 || shape2[j] > 4 ||
                !(shape1[j] == shape2[j] || shape1[j] == 1 || shape2[j] == 1) ||
                coordinate[j] >= extent)
                invalid = 1;
            offset1 = offset1*shape1[j] + (shape1[j] == 1 ? 0 : coordinate[j]);
            offset2 = offset2*shape2[j] + (shape2[j] == 1 ? 0 : coordinate[j]);
        end
        if (!invalid) begin
            a = $signed(in_input_1_data[8*offset1 +: 8]);
            b = $signed(in_input_2_data[8*offset2 +: 8]);
            value = a >= b ? a : b;
        end
    end

    assign out_valid = busy;
    assign in_ready = !busy || out_ready;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0; out_value <= 0; out_error <= 0;
        end else if (in_valid && in_ready) begin
            busy <= 1; out_value <= value; out_error <= invalid;
        end else if (out_ready) begin
            busy <= 0; out_value <= 0; out_error <= 0;
        end
    end
endmodule
