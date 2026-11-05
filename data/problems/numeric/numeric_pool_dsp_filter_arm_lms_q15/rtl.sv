module q15_lms_adaptive_filter_step (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_sample,
    input  logic [15:0] in_ref,
    input  logic [15:0] in_mu,
    input  logic [3:0]  in_post_shift,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_sample,
    output logic [15:0] out_error
);

    logic signed [15:0] coeff0, coeff1, coeff2, coeff3;
    logic signed [15:0] state0, state1, state2, state3;
    logic               busy;

    logic signed [15:0] x0, x1, x2, x3;
    logic signed [31:0] prod0, prod1, prod2, prod3;
    logic signed [63:0] acc;
    logic [4:0]         lshift;
    logic signed [63:0] y_shifted;
    logic signed [15:0] y_val;
    logic signed [16:0] e_full;
    logic signed [15:0] e_val;
    logic signed [31:0] alpha_prod;
    logic signed [31:0] alpha_shifted;
    logic signed [15:0] alpha_val;
    logic signed [31:0] delta0, delta1, delta2, delta3;
    logic signed [31:0] coef_sum0, coef_sum1, coef_sum2, coef_sum3;
    logic signed [15:0] coeff0_next, coeff1_next, coeff2_next, coeff3_next;
    logic signed [15:0] state0_next, state1_next, state2_next, state3_next;

    wire input_accepted;
    wire output_consumed;

    function automatic logic signed [15:0] sat16_64(input logic signed [63:0] value);
        begin
            if (value > 64'sd32767)
                sat16_64 = 16'sd32767;
            else if (value < -64'sd32768)
                sat16_64 = -16'sd32768;
            else
                sat16_64 = value[15:0];
        end
    endfunction

    function automatic logic signed [15:0] sat16_32(input logic signed [31:0] value);
        begin
            if (value > 32'sd32767)
                sat16_32 = 16'sd32767;
            else if (value < -32'sd32768)
                sat16_32 = -16'sd32768;
            else
                sat16_32 = value[15:0];
        end
    endfunction

    assign output_consumed = out_valid & out_ready;
    assign input_accepted  = in_valid & in_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        x0 = state0;
        x1 = state1;
        x2 = state2;
        x3 = $signed(in_sample);

        prod0 = x0 * coeff0;
        prod1 = x1 * coeff1;
        prod2 = x2 * coeff2;
        prod3 = x3 * coeff3;

        acc = $signed(prod0) + $signed(prod1) + $signed(prod2) + $signed(prod3);

        lshift = 5'd15 - {1'b0, in_post_shift};
        y_shifted = acc >>> lshift;
        y_val = sat16_64(y_shifted);

        e_full = $signed(in_ref) - y_val;
        e_val = e_full[15:0];

        alpha_prod = e_val * $signed(in_mu);
        alpha_shifted = alpha_prod >>> 15;
        alpha_val = alpha_shifted[15:0];

        delta0 = (alpha_val * x0) >>> 15;
        delta1 = (alpha_val * x1) >>> 15;
        delta2 = (alpha_val * x2) >>> 15;
        delta3 = (alpha_val * x3) >>> 15;

        coef_sum0 = coeff0 + delta0;
        coef_sum1 = coeff1 + delta1;
        coef_sum2 = coeff2 + delta2;
        coef_sum3 = coeff3 + delta3;

        coeff0_next = sat16_32(coef_sum0);
        coeff1_next = sat16_32(coef_sum1);
        coeff2_next = sat16_32(coef_sum2);
        coeff3_next = sat16_32(coef_sum3);

        state0_next = x1;
        state1_next = x2;
        state2_next = x3;
        state3_next = 16'sd0;
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            coeff0     <= 16'sd0;
            coeff1     <= 16'sd0;
            coeff2     <= 16'sd0;
            coeff3     <= 16'sd0;
            state0     <= 16'sd0;
            state1     <= 16'sd0;
            state2     <= 16'sd0;
            state3     <= 16'sd0;
            out_sample <= 16'h0000;
            out_error  <= 16'h0000;
            busy       <= 1'b0;
        end else if (input_accepted) begin
            out_sample <= y_val[15:0];
            out_error  <= e_val[15:0];

            coeff0 <= coeff0_next;
            coeff1 <= coeff1_next;
            coeff2 <= coeff2_next;
            coeff3 <= coeff3_next;

            state0 <= state0_next;
            state1 <= state1_next;
            state2 <= state2_next;
            state3 <= state3_next;

            busy <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule