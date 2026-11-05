module atan2_q31 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_y,
    input  logic [31:0]  in_x,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_result,
    output logic [1:0]   out_status
);

    localparam logic signed [31:0] ATAN2_COEF_0  = 32'sh00000000;
    localparam logic signed [31:0] ATAN2_COEF_1  = 32'sh7FFFFFFE;
    localparam logic signed [31:0] ATAN2_COEF_2  = 32'sh000001B6;
    localparam logic signed [31:0] ATAN2_COEF_3  = 32'shD555158E;
    localparam logic signed [31:0] ATAN2_COEF_4  = 32'sh00036463;
    localparam logic signed [31:0] ATAN2_COEF_5  = 32'sh1985F617;
    localparam logic signed [31:0] ATAN2_COEF_6  = 32'sh001992AE;
    localparam logic signed [31:0] ATAN2_COEF_7  = 32'shEED53A7F;
    localparam logic signed [31:0] ATAN2_COEF_8  = 32'shF8F15245;
    localparam logic signed [31:0] ATAN2_COEF_9  = 32'sh2215A3A4;
    localparam logic signed [31:0] ATAN2_COEF_10 = 32'shE0FAB004;
    localparam logic signed [31:0] ATAN2_COEF_11 = 32'sh0CDD4825;
    localparam logic signed [31:0] ATAN2_COEF_12 = 32'shFDDBC054;

    localparam logic signed [31:0] PIHALF_Q29 = 32'sh3243F6A9;
    localparam logic signed [31:0] PIQ29      = 32'sh6487ED51;

    logic busy;
    wire  input_accepted;
    wire  output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic signed [31:0] sat_neg_q31(input logic signed [31:0] v);
        begin
            if (v == 32'sh80000000) begin
                sat_neg_q31 = 32'sh7FFFFFFF;
            end else begin
                sat_neg_q31 = -v;
            end
        end
    endfunction

    function automatic logic signed [31:0] clip_q63_to_q31(input logic signed [63:0] v);
        begin
            if (v > 64'sd2147483647) begin
                clip_q63_to_q31 = 32'sh7FFFFFFF;
            end else begin
                clip_q63_to_q31 = v[31:0];
            end
        end
    endfunction

    function automatic logic signed [31:0] coef_by_rev_index(input logic [3:0] idx);
        begin
            coef_by_rev_index = 32'sd0;
            unique case (idx)
                4'd0:  coef_by_rev_index = ATAN2_COEF_12;
                4'd1:  coef_by_rev_index = ATAN2_COEF_11;
                4'd2:  coef_by_rev_index = ATAN2_COEF_10;
                4'd3:  coef_by_rev_index = ATAN2_COEF_9;
                4'd4:  coef_by_rev_index = ATAN2_COEF_8;
                4'd5:  coef_by_rev_index = ATAN2_COEF_7;
                4'd6:  coef_by_rev_index = ATAN2_COEF_6;
                4'd7:  coef_by_rev_index = ATAN2_COEF_5;
                4'd8:  coef_by_rev_index = ATAN2_COEF_4;
                4'd9:  coef_by_rev_index = ATAN2_COEF_3;
                4'd10: coef_by_rev_index = ATAN2_COEF_2;
                4'd11: coef_by_rev_index = ATAN2_COEF_1;
                4'd12: coef_by_rev_index = ATAN2_COEF_0;
            endcase
        end
    endfunction

    function automatic logic signed [31:0] reduced_divide_q31(
        input logic signed [31:0] num,
        input logic signed [31:0] den
    );
        logic [62:0] n_ext;
        logic [62:0] q_ext;
        begin
            n_ext = {num[31:0], 31'b0};
            q_ext = n_ext / den[31:0];
            if (q_ext > 63'd2147483647) begin
                reduced_divide_q31 = 32'sh7FFFFFFF;
            end else begin
                reduced_divide_q31 = $signed(q_ext[31:0]);
            end
        end
    endfunction

    function automatic logic signed [31:0] arm_atan_limited_q31(
        input logic signed [31:0] x
    );
        logic signed [63:0] res;
        logic signed [95:0] prod;
        logic signed [63:0] shifted_prod;
        logic signed [63:0] res_shr2;
        integer i;
        begin
            res = coef_by_rev_index(4'd0);
            for (i = 1; i <= 12; i = i + 1) begin
                prod = x * res;
                shifted_prod = prod >>> 31;
                res = shifted_prod + coef_by_rev_index(i[3:0]);
            end
            res_shr2 = res >>> 2;
            arm_atan_limited_q31 = clip_q63_to_q31(res_shr2);
        end
    endfunction

    function automatic logic signed [31:0] arm_atan_q31(
        input logic signed [31:0] y_in,
        input logic signed [31:0] x_in
    );
        logic signed [31:0] y;
        logic signed [31:0] x;
        logic               sign;
        logic signed [31:0] ratio;
        logic signed [31:0] limited;
        logic signed [31:0] res;
        begin
            y = y_in;
            x = x_in;
            sign = 1'b0;

            if (y < 0) begin
                y = sat_neg_q31(y);
                sign = 1'b1 - sign;
            end
            if (x < 0) begin
                x = sat_neg_q31(x);
                sign = 1'b1 - sign;
            end

            if (y > x) begin
                ratio = reduced_divide_q31(x, y);
                limited = arm_atan_limited_q31(ratio);
                res = PIHALF_Q29 - limited;
            end else begin
                ratio = reduced_divide_q31(y, x);
                res = arm_atan_limited_q31(ratio);
            end

            if (sign) begin
                res = sat_neg_q31(res);
            end
            arm_atan_q31 = res;
        end
    endfunction

    logic signed [31:0] x_s;
    logic signed [31:0] y_s;
    logic signed [31:0] result_calc;
    logic [1:0]         status_calc;
    logic signed [31:0] core_res;

    always_comb begin
        x_s = $signed(in_x);
        y_s = $signed(in_y);
        result_calc = 32'sd0;
        status_calc = 2'b00;
        core_res = 32'sd0;

        if (x_s > 0) begin
            result_calc = arm_atan_q31(y_s, x_s);
            status_calc = 2'b00;
        end else if (x_s < 0) begin
            if (y_s > 0) begin
                core_res = arm_atan_q31(y_s, x_s);
                result_calc = core_res + PIQ29;
            end else if (y_s < 0) begin
                core_res = arm_atan_q31(y_s, x_s);
                result_calc = core_res - PIQ29;
            end else begin
                result_calc = PIQ29;
            end
            status_calc = 2'b00;
        end else begin
            if (y_s > 0) begin
                result_calc = PIHALF_Q29;
                status_calc = 2'b00;
            end else if (y_s < 0) begin
                result_calc = -PIHALF_Q29;
                status_calc = 2'b00;
            end else begin
                result_calc = 32'sd0;
                status_calc = 2'b01;
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy       <= 1'b0;
            out_result <= 32'h00000000;
            out_status <= 2'b00;
        end else if (input_accepted) begin
            busy       <= 1'b1;
            out_result <= result_calc[31:0];
            out_status <= status_calc;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule