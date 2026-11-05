module fixed_sample_pid_q15 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic signed [15:0] in_error,
    input logic signed [15:0] in_p,
    input logic signed [15:0] in_i,
    input logic signed [15:0] in_d,
    input logic signed [15:0] in_output_ramp,
    input logic signed [15:0] in_limit,
    output logic out_valid,
    input logic out_ready,
    output logic signed [15:0] out_output
);

    logic signed [15:0] error_prev, integral_prev, output_prev;
    logic signed [63:0] error, pg, ig, dg, ramp, limit;
    logic signed [63:0] dt_num, dt_den, proportional, increment, integral;
    logic signed [63:0] derivative, result, step_size;
    logic busy;

    assign in_ready = !busy || out_ready;
    assign out_valid = busy;

    function automatic logic signed [63:0] bounded(
        input logic signed [63:0] value,
        input logic signed [63:0] bound
    );
        logic signed [63:0] low, high;
        begin
            low = bound >= 0 ? -bound : -64'sd32768;
            high = bound >= 0 ? bound : 64'sd32767;
            bounded = value < low ? low : (value > high ? high : value);
        end
    endfunction

    always_comb begin
        error = $signed(in_error);
        pg = $signed(in_p);
        ig = $signed(in_i);
        dg = $signed(in_d);
        ramp = $signed(in_output_ramp);
        limit = $signed(in_limit);
        dt_num = 64'sd1;
        dt_den = 64'sd1024;
        proportional = (pg * error) / 64'sd32768;
        increment = (ig * (error + $signed(error_prev)) * dt_num) / (64'sd65536 * dt_den);
        integral = bounded($signed(integral_prev) + increment, limit);
        derivative = (dg * (error - $signed(error_prev)) * dt_den) / (64'sd32768 * dt_num);
        result = bounded(proportional + integral + derivative, limit);
        step_size = 64'sd0;
        if (ramp > 0) begin
            step_size = (ramp * dt_num) / dt_den;
            if (result > $signed(output_prev) + step_size)
                result = $signed(output_prev) + step_size;
            else if (result < $signed(output_prev) - step_size)
                result = $signed(output_prev) - step_size;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            error_prev <= 16'sd0;
            integral_prev <= 16'sd0;
            output_prev <= 16'sd0;
            out_output <= 16'sd0;
        end else if (in_valid && in_ready) begin
            busy <= 1'b1;
            error_prev <= in_error;
            integral_prev <= integral[15:0];
            output_prev <= result[15:0];
            out_output <= result[15:0];
        end else if (out_ready) begin
            busy <= 1'b0;
        end
    end
endmodule
