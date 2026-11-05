module EncoderObserverHysteresis (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic signed [31:0] in_obs_angle,
    input logic signed [31:0] in_enc_angle,
    input logic signed [31:0] in_speed_rad_q16,
    input logic [31:0] in_threshold_rpm_q16,
    output logic out_valid,
    input logic out_ready,
    output logic signed [31:0] out_angle,
    output logic out_using_encoder
);
    logic busy, next_using_encoder;
    logic signed [63:0] speed;
    logic [63:0] rpm_scaled, lower, upper;
    assign in_ready = !rst && (!busy || out_ready);
    assign out_valid = !rst && busy;
    always_comb begin
        speed = {{32{in_speed_rad_q16[31]}}, in_speed_rad_q16};
        if (speed < 0) speed = -speed;
        rpm_scaled = speed * 64'd200263260;
        lower = {32'd0, in_threshold_rpm_q16} * 64'd19922944;
        upper = {32'd0, in_threshold_rpm_q16} * 64'd22020096;
        next_using_encoder = out_using_encoder;
        if (out_using_encoder) begin
            if (rpm_scaled > upper) next_using_encoder = 1'b0;
        end else if (rpm_scaled < lower) next_using_encoder = 1'b1;
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            out_angle <= 32'd0;
            out_using_encoder <= 1'b0;
        end else if (in_valid && in_ready) begin
            busy <= 1'b1;
            out_using_encoder <= next_using_encoder;
            out_angle <= next_using_encoder ? in_enc_angle : in_obs_angle;
        end else if (out_ready) busy <= 1'b0;
    end
endmodule
