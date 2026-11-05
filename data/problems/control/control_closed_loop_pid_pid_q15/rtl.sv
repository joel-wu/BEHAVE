module control_closed_loop_pid_pid_q15 (
    input  logic                clk,
    input  logic                rst,

    input  logic                in_valid,
    output logic                in_ready,
    input  logic signed [15:0]  in_error,
    input  logic signed [15:0]  in_p_gain,
    input  logic signed [15:0]  in_i_gain,
    input  logic signed [15:0]  in_d_gain,
    input  logic signed [15:0]  in_dt,
    input  logic signed [15:0]  in_output_ramp,
    input  logic signed [15:0]  in_limit,

    output logic                out_valid,
    input  logic                out_ready,
    output logic signed [15:0]  out_output
);

    logic signed [15:0] error_prev;
    logic signed [15:0] output_prev;
    logic signed [15:0] integral_prev;
    logic               busy;

    logic signed [15:0] next_output;
    logic signed [15:0] next_error_prev;
    logic signed [15:0] next_output_prev;
    logic signed [15:0] next_integral_prev;

    wire input_accepted;
    wire output_consumed;

    function automatic logic signed [15:0] sat_s16(input logic signed [31:0] value);
        begin
            if (value > 32'sd32767)
                sat_s16 = 16'sd32767;
            else if (value < -32'sd32768)
                sat_s16 = -16'sd32768;
            else
                sat_s16 = value[15:0];
        end
    endfunction

    function automatic logic signed [15:0] abs_limit_q15(input logic signed [15:0] limit);
        begin
            if (limit >= 16'sd0)
                abs_limit_q15 = limit;
            else if (limit == -16'sd32768)
                abs_limit_q15 = 16'sd32767;
            else
                abs_limit_q15 = -limit;
        end
    endfunction

    function automatic logic signed [31:0] trunc_div_toward_zero_32(
        input logic signed [31:0] n,
        input logic signed [15:0] d
    );
        logic [31:0] abs_n;
        logic [15:0] abs_d;
        logic [31:0] q_unsigned;
        begin
            if (d == 16'sd0) begin
                trunc_div_toward_zero_32 = 32'sd0;
            end else begin
                if (n < 32'sd0)
                    abs_n = $unsigned(-n);
                else
                    abs_n = $unsigned(n);

                if (d < 16'sd0)
                    abs_d = $unsigned(-d);
                else
                    abs_d = $unsigned(d);

                q_unsigned = abs_n / abs_d;

                if (n[31] ^ d[15])
                    trunc_div_toward_zero_32 = -$signed(q_unsigned);
                else
                    trunc_div_toward_zero_32 = $signed(q_unsigned);
            end
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        logic signed [31:0] proportional_q30;
        logic signed [31:0] proportional;
        logic signed [16:0] sum_error;
        logic signed [31:0] i_dt_q30;
        logic signed [31:0] i_dt_half_q15;
        logic signed [48:0] integral_delta_q30;
        logic signed [31:0] integral_delta;
        logic signed [31:0] integral_unsat;
        logic signed [15:0] limit_pos;
        logic signed [31:0] limit_pos_32;
        logic signed [31:0] neg_limit_pos_32;
        logic signed [16:0] error_delta;
        logic signed [31:0] d_num_q30;
        logic signed [31:0] derivative_calc;
        logic signed [15:0] derivative_sat;
        logic signed [31:0] output_unsat;
        logic signed [31:0] output_limited;
        logic signed [31:0] output_step;
        logic signed [31:0] output_rate_q30;
        logic signed [31:0] output_rate;
        logic signed [31:0] ramp_step_q30;
        logic signed [31:0] ramp_step;
        logic signed [31:0] output_ramp_32;
        logic signed [31:0] neg_output_ramp_32;
        logic signed [31:0] output_calc;
        logic signed [31:0] error_32;

        next_output        = 16'sd0;
        next_error_prev    = 16'sd0;
        next_output_prev   = 16'sd0;
        next_integral_prev = 16'sd0;

        proportional_q30   = 32'sd0;
        proportional       = 32'sd0;
        sum_error          = 17'sd0;
        i_dt_q30           = 32'sd0;
        i_dt_half_q15      = 32'sd0;
        integral_delta_q30 = 49'sd0;
        integral_delta     = 32'sd0;
        integral_unsat     = 32'sd0;
        limit_pos          = 16'sd0;
        limit_pos_32       = 32'sd0;
        neg_limit_pos_32   = 32'sd0;
        error_delta        = 17'sd0;
        d_num_q30          = 32'sd0;
        derivative_calc    = 32'sd0;
        derivative_sat     = 16'sd0;
        output_unsat       = 32'sd0;
        output_limited     = 32'sd0;
        output_step        = 32'sd0;
        output_rate_q30    = 32'sd0;
        output_rate        = 32'sd0;
        ramp_step_q30      = 32'sd0;
        ramp_step          = 32'sd0;
        output_ramp_32     = 32'sd0;
        neg_output_ramp_32 = 32'sd0;
        output_calc        = 32'sd0;
        error_32           = 32'sd0;

        proportional_q30 = $signed(in_p_gain) * $signed(in_error);
        proportional     = proportional_q30 >>> 15;

        sum_error          = $signed(in_error) + $signed(error_prev);
        i_dt_q30           = $signed(in_i_gain) * $signed(in_dt);
        i_dt_half_q15      = i_dt_q30 >>> 16;
        integral_delta_q30 = $signed(i_dt_half_q15) * $signed(sum_error);
        integral_delta     = integral_delta_q30 >>> 15;
        integral_unsat     = $signed(integral_prev) + integral_delta;

        limit_pos        = abs_limit_q15(in_limit);
        limit_pos_32     = {{16{limit_pos[15]}}, limit_pos};
        neg_limit_pos_32 = -limit_pos_32;

        if (integral_unsat > limit_pos_32)
            next_integral_prev = limit_pos;
        else if (integral_unsat < neg_limit_pos_32)
            next_integral_prev = -limit_pos;
        else
            next_integral_prev = sat_s16(integral_unsat);

        error_delta = $signed(in_error) - $signed(error_prev);
        d_num_q30   = $signed(in_d_gain) * $signed(error_delta);
        if ($signed(in_dt) == 16'sd0)
            derivative_calc = 32'sd0;
        else
            derivative_calc = trunc_div_toward_zero_32(d_num_q30, in_dt);
        derivative_sat = sat_s16(derivative_calc);

        output_unsat = proportional + {{16{next_integral_prev[15]}}, next_integral_prev}
                                     + {{16{derivative_sat[15]}}, derivative_sat};

        if (output_unsat > limit_pos_32)
            output_limited = limit_pos_32;
        else if (output_unsat < neg_limit_pos_32)
            output_limited = neg_limit_pos_32;
        else
            output_limited = output_unsat;

        output_calc        = output_limited;
        output_ramp_32     = {{16{in_output_ramp[15]}}, in_output_ramp};
        neg_output_ramp_32 = -output_ramp_32;

        if (($signed(in_output_ramp) > 16'sd0) && ($signed(in_dt) > 16'sd0)) begin
            output_step     = output_limited - {{16{output_prev[15]}}, output_prev};
            output_rate_q30 = output_step <<< 15;
            output_rate     = trunc_div_toward_zero_32(output_rate_q30, in_dt);
            ramp_step_q30   = $signed(in_output_ramp) * $signed(in_dt);
            ramp_step       = ramp_step_q30 >>> 15;

            if (output_rate > output_ramp_32)
                output_calc = {{16{output_prev[15]}}, output_prev} + ramp_step;
            else if (output_rate < neg_output_ramp_32)
                output_calc = {{16{output_prev[15]}}, output_prev} - ramp_step;
            else
                output_calc = output_limited;
        end

        next_output      = sat_s16(output_calc);
        next_output_prev = sat_s16(output_calc);
        error_32         = {{16{in_error[15]}}, in_error};
        next_error_prev  = sat_s16(error_32);
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            error_prev    <= 16'sd0;
            output_prev   <= 16'sd0;
            integral_prev <= 16'sd0;
            out_output    <= 16'sd0;
            busy          <= 1'b0;
        end else if (input_accepted) begin
            error_prev    <= next_error_prev;
            output_prev   <= next_output_prev;
            integral_prev <= next_integral_prev;
            out_output    <= next_output;
            busy          <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule