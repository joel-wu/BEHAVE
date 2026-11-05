module control_foc_transform_fast_atan2_approx (
    input  logic               clk,
    input  logic               rst,
    input  logic               in_valid,
    output logic               in_ready,
    input  logic signed [15:0] in_y,
    input  logic signed [15:0] in_x,
    output logic               out_valid,
    input  logic               out_ready,
    output logic signed [31:0] out_angle
);

    localparam logic signed [31:0] C3 = -32'sd99850786;
    localparam logic signed [31:0] C2 =  32'sd342124625;
    localparam logic signed [31:0] C1 = -32'sd703564562;

    localparam logic signed [63:0] PI_OVER_2_64 = 64'sd3373259426;
    localparam logic signed [63:0] PI_64        = 64'sd6746518852;

    logic busy;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic signed [63:0] q31_mul_tz_64(
        input logic signed [63:0] p,
        input logic signed [63:0] q
    );
        logic signed [127:0] mult;
        logic signed [127:0] neg_mult;
        begin
            mult = p * q;
            if (mult >= 0) begin
                q31_mul_tz_64 = $signed(mult >>> 31);
            end else begin
                neg_mult = -mult;
                q31_mul_tz_64 = -$signed(neg_mult >>> 31);
            end
        end
    endfunction

    logic signed [16:0] x_ext;
    logic signed [16:0] y_ext;
    logic        [16:0] abs_x;
    logic        [16:0] abs_y;
    logic        [16:0] min_abs;
    logic        [16:0] max_abs;
    logic        [31:0] a_q15_u;
    logic        [32:0] s_q15_u;
    logic signed [63:0] a_q31;
    logic signed [63:0] s_q31;
    logic signed [63:0] p0;
    logic signed [63:0] p1;
    logic signed [63:0] p2;
    logic signed [63:0] p3;
    logic signed [63:0] p4;
    logic signed [63:0] p5;
    logic signed [63:0] angle_calc;
    logic signed [31:0] next_out_angle;

    always_comb begin
        x_ext = {in_x[15], in_x};
        y_ext = {in_y[15], in_y};

        if (x_ext < 0) begin
            abs_x = -x_ext;
        end else begin
            abs_x = x_ext[16:0];
        end

        if (y_ext < 0) begin
            abs_y = -y_ext;
        end else begin
            abs_y = y_ext[16:0];
        end

        if (abs_x < abs_y) begin
            min_abs = abs_x;
            max_abs = abs_y;
        end else begin
            min_abs = abs_y;
            max_abs = abs_x;
        end

        a_q15_u      = 32'd0;
        s_q15_u      = 33'd0;
        a_q31        = 64'sd0;
        s_q31        = 64'sd0;
        p0           = 64'sd0;
        p1           = 64'sd0;
        p2           = 64'sd0;
        p3           = 64'sd0;
        p4           = 64'sd0;
        p5           = 64'sd0;
        angle_calc   = 64'sd0;
        next_out_angle = 32'sd0;

        if (max_abs == 0) begin
            angle_calc = 64'sd0;
        end else begin
            a_q15_u = ({{15'd0, min_abs}} << 15) / max_abs;
            s_q15_u = (a_q15_u * a_q15_u) >> 15;

            a_q31 = $signed({32'd0, a_q15_u}) <<< 16;
            s_q31 = $signed({31'd0, s_q15_u}) <<< 16;

            p0 = q31_mul_tz_64(C3, s_q31);
            p1 = p0 + C2;
            p2 = q31_mul_tz_64(p1, s_q31);
            p3 = p2 + C1;
            p4 = q31_mul_tz_64(p3, s_q31);
            p5 = q31_mul_tz_64(p4, a_q31);
            angle_calc = p5 + a_q31;

            if (abs_y > abs_x) begin
                angle_calc = PI_OVER_2_64 - angle_calc;
            end
            if (x_ext < 0) begin
                angle_calc = PI_64 - angle_calc;
            end
            if (y_ext < 0) begin
                angle_calc = -angle_calc;
            end
        end

        // Convert widened Q31 to Q29 radians, truncating toward zero.
        if (angle_calc >= 0)
            next_out_angle = $signed(angle_calc) >>> 2;
        else
            next_out_angle = -($signed(-angle_calc) >>> 2);
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy      <= 1'b0;
            out_angle <= 32'sd0;
        end else if (input_accepted) begin
            busy      <= 1'b1;
            out_angle <= next_out_angle;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
